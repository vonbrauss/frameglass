import os
os.environ['QT_QPA_PLATFORM']='offscreen'
import hashlib
import json
import struct
import tempfile
import unittest
import zipfile
from pathlib import Path
from threading import Event
from unittest.mock import patch,Mock
from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication
from frameglass.app import STYLE
from frameglass.tool_manager import (ToolSignals,InstallJob,BrewJob,ToolController,ToolDialog,CheckJob,parse_manifest,parse_upstream,
    version_tuple,tool_status,unpack_tool,compatible_macho,trusted_url,detect_homebrew,verify_signature)
from frameglass.core import managed_executable

app=QApplication.instance() or QApplication([]);app.setStyleSheet(STYLE)
ROOT='https://ffmpeg.martin-riedl.de/download/macos/arm64/123_9.0.2/'
MANIFEST={'version':'9.0.2','build':'123_9.0.2','arch':'arm64','tools':{name:{'url':ROOT+name+'.zip','checksum_url':ROOT+name+'.zip.sha256'} for name in ('ffmpeg','ffprobe')}}

def info(name,version='9.0.2',error=''):
 return {'name':name,'path':'/opt/homebrew/bin/'+name,'version':version,'error':error,'source':'Homebrew','status':'Up to date'}

class ToolManagerTests(unittest.TestCase):
 def test_stable_manifest_requires_pair_and_checksums(self):
    links=''.join(f'<a href="{value}">download</a>' for tool in MANIFEST['tools'].values() for value in tool.values())
    links+='<a href="https://evil.example/download/macos/arm64/999_99.0/ffmpeg.zip">bad</a>'
    self.assertEqual(parse_manifest(links,'arm64'),MANIFEST)
    with self.assertRaises(ValueError):parse_manifest(links,'amd64')
    self.assertEqual(parse_upstream('ffmpeg-8.1.3.tar.xz ffmpeg-9.0.2.tar.xz'),'9.0.2')
 def test_versions_and_unknown_network_state(self):
    self.assertEqual(version_tuple('9.0.2_1'),(9,0,2));self.assertIsNone(version_tuple('N-12345-abcdef'))
    self.assertEqual(tool_status(info('ffmpeg','9.0.1'),'9.0.2','9.0.2'),'Update available')
    self.assertEqual(tool_status(info('ffmpeg'),'',''),'Installed · update check unavailable')
    self.assertEqual(tool_status(info('ffmpeg'),'9.1','9.0.2'),'New upstream release · installer build pending')
    self.assertEqual(tool_status(info('ffprobe',error='missing'),'9.0.2','9.0.2'),'Missing / unusable')
 def test_downloads_reject_unexpected_hosts_and_non_https(self):
    for url in ('http://ffmpeg.martin-riedl.de/test','https://evil.example/test','https://ffmpeg.martin-riedl.de:8443/test','https://name@ffmpeg.martin-riedl.de/test'):
     with self.assertRaises(ValueError):trusted_url(url,{'ffmpeg.martin-riedl.de'})
 def test_zip_writes_only_requested_executable_and_rejects_symlinks(self):
    with tempfile.TemporaryDirectory() as temp:
     root=Path(temp);archive=root/'tools.zip'
     with zipfile.ZipFile(archive,'w') as bundle:bundle.writestr('../../ffmpeg',b'safe payload')
     unpack_tool(archive,'ffmpeg',root/'target');self.assertEqual((root/'target').read_bytes(),b'safe payload')
     entry=zipfile.ZipInfo('ffmpeg');entry.create_system=3;entry.external_attr=(0o120777<<16)
     with zipfile.ZipFile(archive,'w') as bundle:bundle.writestr(entry,b'/bin/sh')
     with self.assertRaises(ValueError):unpack_tool(archive,'ffmpeg',root/'target')
 def test_architecture_is_checked(self):
    with tempfile.TemporaryDirectory() as temp:
     path=Path(temp)/'ffmpeg';path.write_bytes(b'\xcf\xfa\xed\xfe'+struct.pack('<I',0x0100000c)+bytes(64))
     self.assertTrue(compatible_macho(path,'arm64'));self.assertFalse(compatible_macho(path,'amd64'))
 def run_private_install(self,root,bad_checksum=False,cancel=False):
    archive_bytes={}
    for name in ('ffmpeg','ffprobe'):
     archive=root/(name+'-source.zip')
     with zipfile.ZipFile(archive,'w') as bundle:bundle.writestr(name,b'\xcf\xfa\xed\xfe'+struct.pack('<I',0x0100000c)+bytes(64))
     archive_bytes[name]=archive.read_bytes();archive.unlink()
    def download(url,cancelled,destination=None,progress=None,limit=0):
     name='ffprobe' if '/ffprobe.' in url else 'ffmpeg'
     if destination:Path(destination).write_bytes(archive_bytes[name]);return b''
     checksum='0'*64 if bad_checksum and name=='ffprobe' else hashlib.sha256(archive_bytes[name]).hexdigest()
     return (checksum+'  '+name+'.zip\n').encode()
    signals=ToolSignals();results=[];signals.installed.connect(lambda receipt,error:results.append((receipt,error)))
    job=InstallJob(MANIFEST,signals,root)
    if cancel:job.cancelled.set()
    with patch('frameglass.tool_manager.architecture',return_value='arm64'),patch('frameglass.tool_manager.fetch',side_effect=download),patch('frameglass.tool_manager.verify_signature',return_value='TESTTEAM12'),patch('frameglass.tool_manager.inspect_tool',side_effect=lambda name,path,cancelled:info(name)):
     job.run()
    return results[0]
 def test_private_install_commits_verified_pair_and_preserves_old_files(self):
    with tempfile.TemporaryDirectory() as temp:
     root=Path(temp);(root/'old-ffmpeg').write_bytes(b'old')
     receipt,error=self.run_private_install(root)
     self.assertFalse(error);self.assertEqual((root/'old-ffmpeg').read_bytes(),b'old')
     self.assertEqual(json.loads((root/'active.json').read_text())['tools'],receipt['tools'])
     with patch('frameglass.core.tool_directory',return_value=root):self.assertEqual(managed_executable('ffmpeg'),receipt['tools']['ffmpeg'])
 def test_failed_second_download_never_replaces_active_pair(self):
    with tempfile.TemporaryDirectory() as temp:
     root=Path(temp);old='{"tools":{"ffmpeg":"old","ffprobe":"old-probe"}}';(root/'active.json').write_text(old)
     receipt,error=self.run_private_install(root,bad_checksum=True)
     self.assertIsNone(receipt);self.assertIn('checksum mismatch',error)
     self.assertEqual((root/'active.json').read_text(),old);self.assertEqual(len(list(root.iterdir())),1)
 def test_cancelled_private_install_does_not_change_active_receipt(self):
    with tempfile.TemporaryDirectory() as temp:
     root=Path(temp);(root/'active.json').write_text('old')
     receipt,error=self.run_private_install(root,cancel=True)
     self.assertIsNone(receipt);self.assertIn('cancelled',error);self.assertEqual((root/'active.json').read_text(),'old')
 def test_offline_check_does_not_claim_up_to_date(self):
    signals=ToolSignals();received=[];signals.checked.connect(received.append)
    job=CheckJob({'ffmpeg':'test-ffmpeg','ffprobe':'test-ffprobe'},signals)
    with patch('frameglass.tool_manager.inspect_tool',side_effect=lambda name,path,c:info(name)),patch('frameglass.tool_manager.fetch',side_effect=OSError('offline')),patch('frameglass.tool_manager.find_brew',return_value=None):job.run()
    self.assertEqual(received[0]['tools'][0]['status'],'Installed · update check unavailable');self.assertTrue(received[0]['errors'])
 def test_private_signature_requires_apple_anchor_and_expected_team(self):
    metadata='Authority=Developer ID Application: Test\nTeamIdentifier=FAKETEAM12\n'
    with patch('frameglass.tool_manager.capture',side_effect=[(0,b'',''),(0,b'',metadata)]) as calls:
     with self.assertRaisesRegex(ValueError,'signing identity changed'):verify_signature(Path('/tmp/test-tool'),Event())
     self.assertIn('-R=anchor apple generic',calls.call_args_list[0].args[0])
 def test_homebrew_revision_detection_is_read_only(self):
    with tempfile.TemporaryDirectory() as temp:
     root=Path(temp);paths={name:str(root/'Cellar'/'ffmpeg'/'9.0.2_1'/'bin'/name) for name in ('ffmpeg','ffprobe')}
     for path in paths.values():Path(path).parent.mkdir(parents=True,exist_ok=True);Path(path).touch()
     local={'name':'ffmpeg','full_name':'ffmpeg','tap':'homebrew/core','installed':[{'version':'9.0.2_1'}]}
     def command(args,*unused,**kwargs):return (0,str(root).encode(),'') if '--prefix' in args else (0,json.dumps({'formulae':[local]}).encode(),'')
     with patch('frameglass.tool_manager.find_brew',return_value='/opt/homebrew/bin/brew'),patch('frameglass.tool_manager.capture',side_effect=command) as calls,patch('frameglass.tool_manager.fetch',return_value=b'{"versions":{"stable":"9.0.2"},"revision":2}'):
      result=detect_homebrew(paths,Event())
      self.assertTrue(result['outdated']);self.assertEqual(result['managed'],['ffmpeg','ffprobe'])
      self.assertTrue(all('update' not in call.args[0] and 'upgrade' not in call.args[0] for call in calls.call_args_list))
 def test_homebrew_source_is_preserved_without_changing_settings(self):
    with tempfile.TemporaryDirectory() as temp:
     settings=QSettings(str(Path(temp)/'settings.ini'),QSettings.IniFormat)
     controller=ToolController(settings)
     result={'tools':[info('ffmpeg'),info('ffprobe')],'latest':'9.0.2','manifest':MANIFEST,'errors':[],
             'brew':{'path':'/opt/homebrew/bin/brew','formula':'ffmpeg','installed':True,'managed':['ffmpeg','ffprobe'],'pinned':False}}
     controller.checked(result);dialog=ToolDialog(controller);controller.dialog=dialog
     self.assertEqual(dialog.method.currentData(),'homebrew');self.assertEqual(settings.value('ffmpeg',''),'')
     self.assertTrue(dialog.install.text().startswith('Update with Homebrew'));dialog.close();controller.close()
 def test_using_existing_homebrew_tools_never_runs_update(self):
    self.brew_job(use_existing=True)
 def test_homebrew_upgrade_is_scoped_to_current_formula(self):
    self.brew_job(use_existing=False)
 def brew_job(self,use_existing):
    record={'path':'/opt/homebrew/bin/brew','formula':'ffmpeg@7','paths':{},'pinned':False}
    signals=ToolSignals();received=[];signals.installed.connect(lambda data,error:received.append((data,error)))
    job=BrewJob(record,signals,use_existing)
    with patch.object(job,'command') as commands,patch('frameglass.tool_manager.brew_inventory',return_value={'installed':[{'version':'7.1'}]}),patch('frameglass.tool_manager.capture',return_value=(0,b'/opt/homebrew/opt/ffmpeg@7','')),patch('frameglass.tool_manager.inspect_tool',side_effect=lambda name,path,c:info(name,'7.1')):
     job.run()
     if use_existing:commands.assert_not_called()
     else:self.assertEqual([call.args[0] for call in commands.call_args_list],[['update'],['upgrade','--formula','ffmpeg@7']])
    self.assertFalse(received[0][1]);self.assertEqual(received[0][0]['manager'],'homebrew');self.assertIn('ffmpeg@7',received[0][0]['tools']['ffprobe'])
 def test_homebrew_pin_is_not_removed(self):
    signals=ToolSignals();received=[];signals.installed.connect(lambda data,error:received.append((data,error)))
    job=BrewJob({'path':'brew','formula':'ffmpeg','pinned':True},signals)
    with patch.object(job,'command') as command:job.run();command.assert_not_called()
    self.assertIn('pinned',received[0][1])
