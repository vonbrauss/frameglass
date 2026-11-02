"""Background media-tool checks and a transactional, app-local macOS installer."""
import hashlib
import html.parser
import json
import os
import platform
import re
import shutil
import signal
import stat
import struct
import subprocess
import tempfile
import time
import urllib.parse
import urllib.request
import uuid
import zipfile
from pathlib import Path
from threading import Event

from PySide6.QtCore import QObject, QRunnable, Signal, Qt, QThreadPool
from PySide6.QtWidgets import (QDialog,QVBoxLayout,QHBoxLayout,QLabel,QPushButton,QTableWidget,QTableWidgetItem,
                              QHeaderView,QFileDialog,QProgressBar,QMessageBox,QTextEdit,QComboBox)
from .core import executable, tool_directory
from .quality import capture, Cancelled

PROVIDER='https://ffmpeg.martin-riedl.de'
UPSTREAM='https://ffmpeg.org/download.html'
VENDOR_SIGNING_TEAM='KU3N25YGLU'  # Apple-anchored signature verified against the provider's stable build.
TOOLS=('ffmpeg','ffprobe')
MAX_DOWNLOAD=250*1024*1024
MAX_BINARY=400*1024*1024


def version_tuple(value):
    match=re.match(r'^n?(\d+)\.(\d+)(?:\.(\d+))?(?:$|[-+_])',str(value))
    return tuple(int(part or 0) for part in match.groups()) if match else None


def architecture():
    if platform.system()!='Darwin':raise ValueError('The built-in installer currently supports macOS. Choose existing executables on other systems.')
    machine=platform.machine().lower()
    if machine in ('arm64','aarch64'):return 'arm64'
    if machine in ('x86_64','amd64'):return 'amd64'
    raise ValueError('No verified download is available for this computer architecture.')


def trusted_url(url,allowed_hosts):
    parsed=urllib.parse.urlparse(url)
    if parsed.scheme!='https' or parsed.hostname not in allowed_hosts or parsed.username or parsed.password or parsed.port not in (None,443):
        raise ValueError('The download source returned an unexpected URL.')
    return url


class TrustedRedirects(urllib.request.HTTPRedirectHandler):
    def __init__(self,hosts):super().__init__();self.hosts=hosts
    def redirect_request(self,req,fp,code,msg,headers,newurl):
        trusted_url(newurl,self.hosts)
        return super().redirect_request(req,fp,code,msg,headers,newurl)


def fetch(url,cancelled,destination=None,progress=None,limit=2*1024*1024):
    if cancelled.is_set():raise Cancelled()
    host=urllib.parse.urlparse(url).hostname
    hosts={'ffmpeg.org','www.ffmpeg.org'} if host in ('ffmpeg.org','www.ffmpeg.org') else {'formulae.brew.sh'} if host=='formulae.brew.sh' else {'ffmpeg.martin-riedl.de'}
    trusted_url(url,hosts)
    opener=urllib.request.build_opener(TrustedRedirects(hosts))
    request=urllib.request.Request(url,headers={'User-Agent':'Frameglass/0.1 media-tool-manager'})
    collected=bytearray();received=0;deadline=time.monotonic()+180 if destination else time.monotonic()+20
    with opener.open(request,timeout=8) as response:
        trusted_url(response.geturl(),hosts)
        try:total=int(response.headers.get('Content-Length',0))
        except ValueError:total=0
        if total>limit:raise ValueError('Download is larger than the allowed size.')
        output=open(destination,'wb') if destination else None
        try:
            while True:
                if cancelled.is_set():raise Cancelled()
                if time.monotonic()>deadline:raise TimeoutError('Download timed out; retry when the connection is available.')
                chunk=response.read(128*1024)
                if not chunk:break
                received+=len(chunk)
                if received>limit:raise ValueError('Download exceeds the size limit.')
                if output:output.write(chunk)
                else:collected.extend(chunk)
                if progress:progress(received,total)
        finally:
            if output:output.close()
    return bytes(collected)


class Links(html.parser.HTMLParser):
    def __init__(self):super().__init__();self.urls=[]
    def handle_starttag(self,tag,attrs):
        if tag=='a':
            href=dict(attrs).get('href')
            if href:self.urls.append(urllib.parse.urljoin(PROVIDER,href))


def parse_manifest(page,arch):
    links=Links();links.feed(page);builds={}
    for url in links.urls:
        if urllib.parse.urlparse(url).hostname!='ffmpeg.martin-riedl.de':continue
        match=re.fullmatch(r'/download/macos/'+re.escape(arch)+r'/([0-9]+_([0-9]+\.[0-9]+(?:\.[0-9]+)?))/(ffmpeg|ffprobe)\.zip(?:\.sha256)?',urllib.parse.urlparse(url).path)
        if not match:continue
        build,version,name=match.groups();entry=builds.setdefault(build,{'version':version,'build':build,'arch':arch,'tools':{}})
        tool=entry['tools'].setdefault(name,{})
        tool['checksum_url' if url.endswith('.sha256') else 'url']=trusted_url(url,{'ffmpeg.martin-riedl.de'})
    usable=[entry for entry in builds.values() if all(set(entry['tools'].get(name,{}))=={'url','checksum_url'} for name in TOOLS)]
    if not usable:raise ValueError('No complete stable build with checksums is available for this Mac.')
    return max(usable,key=lambda entry:(version_tuple(entry['version']),int(entry['build'].split('_')[0])))


def parse_upstream(page):
    versions=re.findall(r'ffmpeg-(\d+\.\d+(?:\.\d+)?)\.tar\.xz',page)
    if not versions:raise ValueError('The latest stable FFmpeg release could not be verified.')
    return max(versions,key=version_tuple)


def inspect_tool(name,path,cancelled):
    result={'name':name,'path':path,'version':'','error':''}
    try:
        code,out,error=capture([path,'-version'],cancelled,timeout=8)
        first=out.decode(errors='replace').splitlines()[0] if out else ''
        match=re.match(re.escape(name)+r' version (\S+)',first)
        if code or not match:raise ValueError(error.strip() or f'This executable did not identify itself as {name}.')
        result['version']=match[1]
    except Cancelled:raise
    except FileNotFoundError:result['error']='Not installed or configured path is missing'
    except Exception as error:result['error']=str(error)
    return result


def tool_status(tool,latest,available):
    if tool['error']:return 'Missing / unusable'
    installed=version_tuple(tool['version'])
    if not installed:return 'Development or unrecognized version · stable comparison unavailable'
    target=version_tuple(latest) if latest else version_tuple(available) if available else None
    if not target:return 'Installed · update check unavailable'
    if installed<target:
        return 'Update available' if available and installed<version_tuple(available) else 'New upstream release · installer build pending'
    if installed>target:return 'Newer than latest verified stable release'
    return 'Up to date' if latest else 'Matches available build · upstream check unavailable'


def find_brew(paths=None):
    for path in (paths or {}).values():
        for parent in Path(path).resolve().parents:
            if parent.name=='Cellar' and (parent.parent/'bin'/'brew').is_file():
                return str(parent.parent/'bin'/'brew')
    return shutil.which('brew') or next((str(path) for path in (Path('/opt/homebrew/bin/brew'),Path('/usr/local/bin/brew')) if path.is_file()),None)


def brew_environment(brew):
    env=os.environ.copy()
    env.update({'HOMEBREW_NO_AUTO_UPDATE':'1','HOMEBREW_NO_ANALYTICS':'1','HOMEBREW_NO_INSTALL_CLEANUP':'1',
                'HOMEBREW_NO_INSTALLED_DEPENDENTS_CHECK':'1','HOMEBREW_NO_ASK':'1'})
    env['PATH']=str(Path(brew).parent)+os.pathsep+env.get('PATH','/usr/bin:/bin:/usr/sbin:/sbin')
    return env


def brew_inventory(brew,formula,cancelled):
    code,out,error=capture([brew,'info','--json=v2',formula],cancelled,timeout=20,env=brew_environment(brew))
    if code:raise ValueError(error.strip() or 'Homebrew could not read its FFmpeg package information.')
    formulas=json.loads(out).get('formulae',[])
    if len(formulas)!=1:raise ValueError('Homebrew did not identify a single FFmpeg formula.')
    return formulas[0]


def detect_homebrew(paths,cancelled):
    brew=find_brew(paths)
    if not brew:return None
    record={'path':brew,'formula':'ffmpeg','installed':False,'paths':{},'managed':[],'latest':'','outdated':None,'error':'','pinned':False}
    try:
        code,out,error=capture([brew,'--prefix'],cancelled,timeout=10,env=brew_environment(brew))
        if code:raise ValueError(error)
        root=Path(out.decode().strip()).resolve();record['root']=str(root)
        names=[]
        for name,path in paths.items():
            resolved=Path(path).resolve()
            try:
                parts=resolved.relative_to(root/'Cellar').parts
                if parts and re.fullmatch(r'ffmpeg(?:-full)?(?:@[0-9.]+)?',parts[0]):
                    names.append(parts[0]);record['managed'].append(name)
            except ValueError:pass
        if names and len(set(names))==1:record['formula']=names[0]
        local=brew_inventory(brew,record['formula'],cancelled)
        full_name=local.get('full_name',record['formula'])
        if not re.fullmatch(r'[A-Za-z0-9@_.+-]+(?:/[A-Za-z0-9@_.+-]+){0,2}',full_name):raise ValueError('Unsupported Homebrew formula name.')
        record['formula']=full_name;record['pinned']=bool(local.get('pinned'))
        installed=local.get('installed',[]);record['installed']=bool(installed)
        record['installed_version']=installed[-1].get('version','') if installed else ''
        # Use the stable opt symlink so future brew upgrades continue to work.
        prefix=root/'opt'/local.get('name',full_name.split('/')[-1])
        record['paths']={name:str(prefix/'bin'/name) for name in TOOLS}
        if local.get('tap') not in (None,'homebrew/core'):
            record['error']='Custom Homebrew tap: refresh through Homebrew to check for updates.'
            return record
        remote=json.loads(fetch('https://formulae.brew.sh/api/formula/'+urllib.parse.quote(local.get('name',record['formula']),safe='@')+'.json',cancelled).decode())
        record['latest']=remote.get('versions',{}).get('stable','')
        revision=int(remote.get('revision',0));record['latest_package']=record['latest']+(f'_{revision}' if revision else '')
        if installed:
            base=version_tuple(record['installed_version']);latest=version_tuple(record['latest'])
            old_revision=int(record['installed_version'].rsplit('_',1)[1]) if re.search(r'_\d+$',record['installed_version']) else 0
            record['outdated']=(base<latest or base==latest and old_revision<revision) if base and latest else None
    except Cancelled:raise
    except Exception as error:record['error']=str(error)
    return record


def tool_source(tool,brew):
    if brew and tool['name'] in brew.get('managed',[]):return 'Homebrew'
    try:
        if Path(tool['path']).resolve().is_relative_to(tool_directory().resolve()):return 'Frameglass private'
    except OSError:pass
    return 'System / custom'


class BrewJob(QRunnable):
    def __init__(self,record,signals,use_existing=False):
        super().__init__();self.record=record;self.signals=signals;self.use_existing=use_existing;self.cancelled=Event()
    def progress(self,message):
        try:self.signals.progress.emit(message,0,0)
        except RuntimeError:self.cancelled.set()
    def command(self,args):
        brew=self.record['path']
        with subprocess.Popen([brew,*args],stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,
                              env=brew_environment(brew),start_new_session=True) as process:
            while True:
                if self.cancelled.is_set():
                    try:os.killpg(process.pid,signal.SIGTERM)
                    except ProcessLookupError:pass
                    try:process.communicate(timeout=3)
                    except subprocess.TimeoutExpired:
                        try:os.killpg(process.pid,signal.SIGKILL)
                        except ProcessLookupError:pass
                        process.communicate()
                    raise Cancelled()
                try:
                    output,_=process.communicate(timeout=.2)
                    if process.returncode:raise ValueError(output.decode(errors='replace')[-12000:] or 'Homebrew command failed.')
                    return
                except subprocess.TimeoutExpired:pass
    def run(self):
        try:
            if self.cancelled.is_set():raise Cancelled()
            record=self.record;brew=record['path'];formula=record['formula']
            if not self.use_existing:
                if record.get('pinned'):raise ValueError('This Homebrew formula is pinned. Frameglass will not unpin it. Use the existing version or manage the pin in Homebrew.')
                self.progress('Homebrew: refreshing package metadata…');self.command(['update'])
                local=brew_inventory(brew,formula,self.cancelled)
                self.progress('Homebrew: updating FFmpeg and required dependencies…' if local.get('installed') else 'Homebrew: installing FFmpeg and required dependencies…')
                self.command(['upgrade' if local.get('installed') else 'install','--formula',formula])
            local=brew_inventory(brew,formula,self.cancelled)
            if not local.get('installed'):raise ValueError('Homebrew has not installed this FFmpeg formula.')
            code,out,error=capture([brew,'--prefix',formula],self.cancelled,timeout=15,env=brew_environment(brew))
            if code or not out.strip():raise ValueError(error or 'Homebrew could not locate its installed FFmpeg tools.')
            prefix=Path(out.decode().strip())
            paths={name:str(prefix/'bin'/name) for name in TOOLS}
            for name,path in paths.items():
                detected=inspect_tool(name,path,self.cancelled)
                if detected['error']:raise ValueError(detected['error'])
            if self.cancelled.is_set():raise Cancelled()
            self.signals.installed.emit({'manager':'homebrew','tools':paths,'formula':formula},'')
        except Exception as error:
            message='Homebrew command stopped. Package changes may be partial; check the tools again before using them.' if isinstance(error,Cancelled) else str(error)
            try:self.signals.installed.emit(None,message)
            except RuntimeError:pass


class ToolSignals(QObject):
    checked=Signal(object)
    progress=Signal(str,int,int)
    installed=Signal(object,str)


class CheckJob(QRunnable):
    def __init__(self,paths,signals):
        super().__init__();self.paths=paths;self.signals=signals;self.cancelled=Event()
    def run(self):
        result={'tools':[],'latest':'','manifest':None,'errors':[],'brew':None}
        try:
            for name in TOOLS:result['tools'].append(inspect_tool(name,self.paths[name],self.cancelled))
            try:result['latest']=parse_upstream(fetch(UPSTREAM,self.cancelled).decode('utf-8',errors='replace'))
            except Cancelled:raise
            except Exception as error:result['errors'].append('Official release check: '+str(error))
            try:result['manifest']=parse_manifest(fetch(PROVIDER+'/',self.cancelled).decode('utf-8',errors='replace'),architecture())
            except Cancelled:raise
            except Exception as error:result['errors'].append('Installer availability: '+str(error))
            result['brew']=detect_homebrew(self.paths,self.cancelled)
            if result['brew'] and result['brew']['error']:result['errors'].append('Homebrew check: '+result['brew']['error'])
            available=result['manifest']['version'] if result['manifest'] else ''
            for tool in result['tools']:
                tool['source']=tool_source(tool,result['brew'])
                tool['status']=tool_status(tool,result['latest'],available)
                if not tool['error'] and tool['source']=='Homebrew' and result['brew'].get('outdated'):
                    tool['status']='Homebrew update available'+(' · pinned' if result['brew'].get('pinned') else '')
                elif tool['source']=='Homebrew' and result['brew'].get('error'):
                    tool['status']+=' · Homebrew revision check unavailable'
                elif tool['source']=='Homebrew' and result['brew'].get('pinned'):
                    tool['status']+=' · pinned in Homebrew'
            if not self.cancelled.is_set():self.signals.checked.emit(result)
        except Cancelled:pass
        except RuntimeError:pass  # The app may have closed while checking the network.


def compatible_macho(path,arch):
    cpu=0x0100000c if arch=='arm64' else 0x01000007
    with open(path,'rb') as file:
        header=file.read(8)
        if len(header)!=8:return False
        if header[:4] in (b'\xcf\xfa\xed\xfe',b'\xfe\xed\xfa\xcf'):
            endian='<' if header[:4]==b'\xcf\xfa\xed\xfe' else '>'
            return struct.unpack(endian+'I',header[4:])[0]==cpu
        if header[:4] in (b'\xca\xfe\xba\xbe',b'\xca\xfe\xba\xbf'):
            count=struct.unpack('>I',header[4:])[0]
            if count>32:return False
            stride=32 if header[:4]==b'\xca\xfe\xba\xbf' else 20
            entries=file.read(count*stride)
            return any(struct.unpack('>I',entries[i:i+4])[0]==cpu for i in range(0,len(entries)-stride+1,stride))
    return False


def unpack_tool(archive,name,target):
    with zipfile.ZipFile(archive) as bundle:
        if len(bundle.infolist())>100:raise ValueError('Unexpected archive contents.')
        entries=[entry for entry in bundle.infolist() if Path(entry.filename).name==name and not entry.is_dir()]
        if len(entries)!=1:raise ValueError(f'The archive must contain exactly one {name} executable.')
        entry=entries[0]
        if stat.S_ISLNK(entry.external_attr>>16) or entry.file_size>MAX_BINARY:raise ValueError('Unsafe or oversized executable in archive.')
        # Never extract arbitrary paths from the archive.
        with bundle.open(entry) as source,open(target,'wb') as output:
            copied=0
            while chunk:=source.read(128*1024):
                copied+=len(chunk)
                if copied>MAX_BINARY:raise ValueError('Executable exceeds the size limit.')
                output.write(chunk)
    target.chmod(0o755)


def verify_signature(path,cancelled):
    code,_,error=capture(['/usr/bin/codesign','--verify','--strict','-R=anchor apple generic',str(path)],cancelled,timeout=15)
    if code:raise ValueError('The downloaded executable failed its macOS signature check: '+error)
    code,out,error=capture(['/usr/bin/codesign','-d','--verbose=4',str(path)],cancelled,timeout=15)
    details=out.decode(errors='replace')+error
    if code or 'Authority=Developer ID Application:' not in details or not re.search(r'TeamIdentifier=[A-Z0-9]{10}',details):
        raise ValueError('The downloaded executable has no identifiable Developer ID signature.')
    team=re.search(r'TeamIdentifier=([A-Z0-9]{10})',details)[1]
    if team!=VENDOR_SIGNING_TEAM:
        raise ValueError('The download signing identity changed. Private installation was stopped; use Homebrew or locate trusted existing tools.')
    return team


class InstallJob(QRunnable):
    def __init__(self,manifest,signals,directory=None):
        super().__init__();self.manifest=manifest;self.signals=signals;self.directory=Path(directory) if directory else tool_directory();self.cancelled=Event()
    def emit(self,message,current=0,total=0):
        try:self.signals.progress.emit(message,current,total)
        except RuntimeError:self.cancelled.set()
    def run(self):
        staged=None;published=None
        try:
            if self.cancelled.is_set():raise Cancelled()
            arch=architecture()
            if self.manifest['arch']!=arch:raise ValueError('The checked download architecture no longer matches this Mac. Check again.')
            self.directory.mkdir(parents=True,exist_ok=True)
            staged=Path(tempfile.mkdtemp(prefix='.install-',dir=self.directory))
            receipt={'manager':'private','version':self.manifest['version'],'source':PROVIDER,'arch':arch,'tools':{},'sha256':{},'signing_teams':{}}
            for index,name in enumerate(TOOLS):
                info=self.manifest['tools'][name]
                for url in info.values():trusted_url(url,{'ffmpeg.martin-riedl.de'})
                self.emit(f'Checking {name} download…')
                checksum=fetch(info['checksum_url'],self.cancelled,limit=4096).decode('ascii',errors='strict')
                match=re.match(r'^\s*([a-fA-F0-9]{64})(?:\s|$)',checksum)
                if not match:raise ValueError('The provider did not supply a valid SHA-256 checksum.')
                archive=staged/(name+'.zip')
                fetch(info['url'],self.cancelled,archive,lambda current,total,n=name:self.emit('Downloading '+n+'…',current,total),MAX_DOWNLOAD)
                self.emit('Verifying '+name+'…')
                hasher=hashlib.sha256()
                with archive.open('rb') as file:
                    while chunk:=file.read(1024*1024):
                        if self.cancelled.is_set():raise Cancelled()
                        hasher.update(chunk)
                digest=hasher.hexdigest()
                if digest!=match[1].lower():raise ValueError(name+' download checksum mismatch. Existing tools were not changed.')
                target=staged/name;unpack_tool(archive,name,target);archive.unlink()
                if not compatible_macho(target,arch):raise ValueError(name+' is not a compatible macOS executable.')
                receipt['signing_teams'][name]=verify_signature(target,self.cancelled)
                detected=inspect_tool(name,str(target),self.cancelled)
                if detected['error'] or version_tuple(detected['version'])!=version_tuple(self.manifest['version']):
                    raise ValueError(name+' did not pass its version test. '+detected['error'])
                receipt['sha256'][name]=digest
            if len(set(receipt['signing_teams'].values()))!=1:raise ValueError('The two executables have different signing identities.')
            if self.cancelled.is_set():raise Cancelled()
            self.emit('Activating verified tools…')
            folder=self.directory/(self.manifest['build']+'-'+uuid.uuid4().hex[:8])
            os.replace(staged,folder);staged=None;published=folder
            receipt['tools']={name:str(folder/name) for name in TOOLS}
            (folder/'receipt.json').write_text(json.dumps(receipt,indent=2),encoding='utf-8')
            pending=self.directory/('.active-'+uuid.uuid4().hex+'.json')
            try:
                pending.write_text(json.dumps(receipt,indent=2),encoding='utf-8')
                os.replace(pending,self.directory/'active.json')
            finally:pending.unlink(missing_ok=True)
            published=None  # The active receipt is the commit point; retain this install.
            try:self.signals.installed.emit(receipt,'')
            except RuntimeError:pass
        except Exception as error:
            message='Installation cancelled. Existing tools were kept.' if isinstance(error,Cancelled) else str(error)
            try:self.signals.installed.emit(None,message)
            except RuntimeError:pass
        finally:
            if staged:shutil.rmtree(staged,ignore_errors=True)
            if published:shutil.rmtree(published,ignore_errors=True)


class ToolDialog(QDialog):
    def __init__(self,controller,parent=None):
        super().__init__(parent);self.controller=controller;self.setWindowTitle('Frameglass · Media tools');self.resize(900,480)
        layout=QVBoxLayout(self)
        title=QLabel('Media tools');title.setObjectName('title');layout.addWidget(title)
        self.summary=QLabel('Checking installed tools and stable releases…');self.summary.setWordWrap(True);layout.addWidget(self.summary)
        self.table=QTableWidget(2,5);self.table.setHorizontalHeaderLabels(['Tool','Installed version','Managed by','Status','Location']);self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.setMaximumHeight(130);self.table.verticalHeader().hide();self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents);self.table.horizontalHeader().setStretchLastSection(True);layout.addWidget(self.table)
        self.method=QComboBox();self.method.addItem('Keep using Homebrew','homebrew');self.method.addItem('Private tools for Frameglass · no Homebrew required','private')
        self.method.currentIndexChanged.connect(self.method_changed);layout.addWidget(self.method)
        text=QLabel('Homebrew keeps ownership of its tools; updates run only after you choose them. Private installs keep system/Homebrew executables untouched.\nPrivate download source: Martin Riedl’s FFmpeg Build Server · SHA-256, architecture and Developer ID signatures are verified before activation.')
        text.setWordWrap(True);text.setObjectName('muted');layout.addWidget(text)
        self.destination=QLabel('Destination: '+str(tool_directory()));self.destination.setWordWrap(True);self.destination.setObjectName('muted');layout.addWidget(self.destination)
        self.details=QTextEdit();self.details.setReadOnly(True);self.details.setMaximumHeight(100);layout.addWidget(self.details)
        self.progress=QProgressBar();self.progress.hide();layout.addWidget(self.progress)
        row=QHBoxLayout()
        self.use_brew=QPushButton('Use existing Homebrew tools');self.use_brew.clicked.connect(controller.use_homebrew);row.addWidget(self.use_brew)
        self.check=QPushButton('Check again');self.check.clicked.connect(controller.check);row.addWidget(self.check)
        self.locate=QPushButton('Locate existing tools…');self.locate.clicked.connect(controller.locate);row.addWidget(self.locate)
        row.addStretch()
        self.install=QPushButton('Install / update private tools');self.install.setObjectName('primary');self.install.setEnabled(False);self.install.clicked.connect(controller.install);row.addWidget(self.install)
        self.cancel=QPushButton('Cancel download');self.cancel.hide();self.cancel.clicked.connect(controller.cancel_install);row.addWidget(self.cancel)
        close=QPushButton('Close');close.clicked.connect(self.close);row.addWidget(close);layout.addLayout(row)
        self.refresh()
    def refresh(self):
        c=self.controller;result=c.result
        if result:
            available=result['manifest']['version'] if result['manifest'] else 'Not available'
            latest=result['latest'] or 'Could not verify (offline or server unavailable)'
            brew=result.get('brew')
            package=brew.get('latest_package','Not verified') if brew else 'Not installed'
            self.summary.setText(f'Latest official stable release: {latest}\nHomebrew package available: {package} · Private build available: {available}')
            for row,tool in enumerate(result['tools']):
                for column,value in enumerate((tool['name'],tool['version'] or 'Not available',tool.get('source','System / custom'),tool['status'],tool['path'])):
                    item=QTableWidgetItem(value);item.setToolTip(tool.get('error') or value);self.table.setItem(row,column,item)
            self.details.setPlainText('\n'.join([*result['errors'],c.operation_error]).strip() or 'Check completed. No tools are installed or updated without your choice below.')
        busy=c.check_job is not None or c.install_job is not None
        self.check.setEnabled(not busy);self.locate.setEnabled(not busy)
        brew=result.get('brew') if result else None
        self.method.model().item(0).setEnabled(bool(brew))
        self.method.setEnabled(not busy)
        if not getattr(self,'method_initialized',False) and result:
            preference=c.settings.value('tool_install_method','')
            source='homebrew' if brew and len(brew.get('managed',[]))==2 else 'private'
            self.method.blockSignals(True);self.method.setCurrentIndex(0 if (preference or source)=='homebrew' and brew else 1);self.method.blockSignals(False)
            self.method_initialized=True
        using_brew=self.method.currentData()=='homebrew'
        self.use_brew.setVisible(using_brew);self.use_brew.setEnabled(bool(brew and brew.get('installed')) and not busy)
        if using_brew:
            self.install.setEnabled(bool(brew and not brew.get('pinned')) and not busy)
            self.install.setText('Update with Homebrew' if brew and brew.get('installed') else 'Install with Homebrew')
            self.destination.setText('Homebrew formula: '+(brew.get('formula','ffmpeg') if brew else 'Not installed')+' · Homebrew-managed tools remain selected unless you choose another source.')
        else:
            self.install.setEnabled(bool(result and result['manifest']) and not busy)
            self.install.setText('Install / update private tools '+(result['manifest']['version'] if result and result['manifest'] else ''))
            self.destination.setText('Private install destination: '+str(tool_directory()))
        self.cancel.setVisible(c.install_job is not None)
        self.cancel.setText('Stop Homebrew command' if isinstance(c.install_job,BrewJob) else 'Cancel download')
        if brew and brew.get('pinned') and using_brew:self.details.append('This Homebrew formula is pinned. Frameglass will keep that pin; use the existing tools or manage the pin in Homebrew.')
    def method_changed(self):self.refresh()
    def closeEvent(self,event):
        # Closing the review window cancels an unfinished download, not media inspection.
        self.controller.cancel_install();event.accept()
    def reject(self):self.controller.cancel_install();super().reject()


class ToolController(QObject):
    changed=Signal(object)
    activated=Signal()
    def __init__(self,settings,parent=None):
        super().__init__(parent);self.settings=settings;self.result=None;self.dialog=None;self.check_job=None;self.install_job=None;self.operation_error=''
        self.signals=ToolSignals(self);self.signals.checked.connect(self.checked,Qt.QueuedConnection)
        self.signals.progress.connect(self.progress,Qt.QueuedConnection);self.signals.installed.connect(self.installed,Qt.QueuedConnection)
    def check(self):
        if self.check_job or self.install_job:return
        paths={name:executable(name,self.settings.value(name,'')) for name in TOOLS}
        self.check_job=CheckJob(paths,self.signals)
        if self.dialog:
            self.dialog.refresh();self.dialog.summary.setText('Checking installed versions, Homebrew and stable releases…')
            self.dialog.progress.setRange(0,0);self.dialog.progress.show()
        QThreadPool.globalInstance().start(self.check_job)
    def checked(self,result):
        self.check_job=None;self.result=result
        if self.dialog:self.dialog.progress.hide();self.dialog.refresh()
        self.changed.emit(result)
    def show(self):
        if self.dialog is None:self.dialog=ToolDialog(self,self.parent())
        self.dialog.refresh();self.dialog.show();self.dialog.raise_();self.dialog.activateWindow()
        if self.result is None:self.check()
    def install(self):
        if self.dialog and self.dialog.method.currentData()=='homebrew':
            self.install_homebrew();return
        if not self.result or not self.result['manifest'] or self.install_job:return
        manifest=self.result['manifest']
        message=(f"Download FFmpeg and FFprobe {manifest['version']} for {manifest['arch']} from {PROVIDER}?\n\n"
                 f"Install location: {tool_directory()}\n\nFrameglass will switch to these private tools after both pass verification. Existing system tools will not be overwritten.")
        if any(version_tuple(t['version']) and version_tuple(t['version'])>version_tuple(manifest['version']) for t in self.result['tools']):
            message+='\n\nYour selected tools are newer than this private build. This choice would switch Frameglass to an older stable version.'
        if QMessageBox.question(self.dialog,'Install Frameglass media tools',message,QMessageBox.Yes|QMessageBox.Cancel,QMessageBox.Cancel)!=QMessageBox.Yes:return
        self.operation_error=''
        self.install_job=InstallJob(manifest,self.signals)
        self.dialog.progress.setRange(0,0);self.dialog.progress.show();self.dialog.refresh()
        QThreadPool.globalInstance().start(self.install_job)
    def start_brew(self,use_existing):
        if not self.result or not self.result.get('brew') or self.install_job:return
        self.operation_error=''
        self.install_job=BrewJob(self.result['brew'],self.signals,use_existing)
        self.dialog.progress.setRange(0,0);self.dialog.progress.show();self.dialog.refresh()
        QThreadPool.globalInstance().start(self.install_job)
    def use_homebrew(self):
        if not self.result or not self.result.get('brew'):return
        if QMessageBox.question(self.dialog,'Use Homebrew tools','Use the existing Homebrew FFmpeg and FFprobe for Frameglass? No install or update will run.',QMessageBox.Yes|QMessageBox.Cancel,QMessageBox.Cancel)==QMessageBox.Yes:
            self.start_brew(True)
    def install_homebrew(self):
        if not self.result or not self.result.get('brew'):return
        formula=self.result['brew']['formula']
        message=(f'Run Homebrew update, then install or upgrade {formula}?\n\nHomebrew may update required dependencies. This can affect other apps that use this Homebrew installation. Other installed packages are not selected for upgrade.\n\nFrameglass will keep using Homebrew after the tools pass their version checks.')
        if QMessageBox.question(self.dialog,'Update media tools with Homebrew',message,QMessageBox.Yes|QMessageBox.Cancel,QMessageBox.Cancel)==QMessageBox.Yes:
            self.start_brew(False)
    def cancel_install(self):
        if self.install_job:self.install_job.cancelled.set()
    def progress(self,message,current,total):
        if self.dialog:
            self.dialog.summary.setText(message);self.dialog.progress.setRange(0,total);self.dialog.progress.setValue(current)
    def installed(self,receipt,error):
        was_brew=isinstance(self.install_job,BrewJob)
        self.install_job=None
        if self.dialog:self.dialog.progress.hide();self.dialog.refresh()
        if error:
            self.operation_error=error
            if self.dialog:self.dialog.details.setPlainText(error)
            if was_brew:self.check()
            return
        for name,path in receipt['tools'].items():self.settings.setValue(name,path)
        self.settings.setValue('tool_install_method',receipt.get('manager','private'))
        self.settings.sync()
        if self.dialog:self.dialog.details.setPlainText('Media tools selected. Checking their versions…')
        self.activated.emit();self.check()
    def locate(self):
        selected={}
        for name in TOOLS:
            path,_=QFileDialog.getOpenFileName(self.dialog,'Locate '+name,self.settings.value(name,'/opt/homebrew/bin'))
            if not path:return
            selected[name]=path
        for name,path in selected.items():self.settings.setValue(name,path)
        self.settings.remove('tool_install_method')
        if self.dialog:self.dialog.method_initialized=False
        self.activated.emit();self.check()
    def close(self):
        if self.check_job:self.check_job.cancelled.set()
        self.cancel_install()
