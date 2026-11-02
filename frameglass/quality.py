"""Comparison, explicit delivery requirements and cancellable folder inspection."""
import json
import re
import math
import subprocess
import time
from pathlib import Path
from threading import Event
from fractions import Fraction

from PySide6.QtCore import QObject, QRunnable, Signal
from PIL import Image, ImageSequence
from .core import executable, probe_args, scan_args, duration, findings
from .timecode import media_tracks, clip_timecode
from .color import scan_mode
from .stills import STILL_EXTENSIONS, read_still

MEDIA_EXTENSIONS = STILL_EXTENSIONS | {'.mov','.mp4','.m4v','.mxf','.mkv','.avi','.webm','.mts','.m2ts','.mpg','.mpeg','.ts','.vob','.wmv','.r3d','.braw','.wav','.wave','.aif','.aiff','.mp3','.m4a','.aac','.flac','.ogg','.opus','.wma','.caf','.mka','.ac3','.aiff','.mp2','.aifc','.m2v','.3gp'}
DEFAULT_PRESETS = [
    {'name':'Example · 1080p59.94 stereo', 'rules':{'type':'Video','width':1920,'height':1080,'fps':'60000/1001','scan':'Progressive','primaries':'bt709','audio_tracks':1,'channels':2,'sample_rate':48000}},
    {'name':'Example · 1080p29.97 stereo', 'rules':{'type':'Video','width':1920,'height':1080,'fps':'30000/1001','scan':'Progressive','primaries':'bt709','audio_tracks':1,'channels':2,'sample_rate':48000}},
    {'name':'Example · UHD23.976 stereo', 'rules':{'type':'Video','width':3840,'height':2160,'fps':'24000/1001','scan':'Progressive','audio_tracks':1,'channels':2,'sample_rate':48000}},
    {'name':'Example · Stereo PCM 48 kHz', 'rules':{'type':'Audio','audio_codec':'pcm_s24le','audio_tracks':1,'channels':2,'sample_rate':48000,'bits':24}},
]
RULES = {'type':'Media type','width':'Width','height':'Height','fps':'Frame rate','scan':'Scan','primaries':'Color primaries',
         'transfer':'Transfer function','matrix':'Color matrix','pixel_format':'Pixel format',
         'video_codec':'Video codec','audio_codec':'Audio codec','audio_tracks':'Audio tracks','channels':'Channels per audio track','sample_rate':'Audio sample rate','bits':'Audio bit depth'}

RULES.update({'container':'Container','bitrate_min':'Minimum total bitrate (Mb/s)','bitrate_max':'Maximum total bitrate (Mb/s)',
              'video_bitrate_min':'Minimum video bitrate (Mb/s)','video_bitrate_max':'Maximum video bitrate (Mb/s)',
              'audio_bitrate_min':'Minimum audio bitrate (Mb/s)','audio_bitrate_max':'Maximum audio bitrate (Mb/s)',
              'duration_min':'Minimum duration (seconds)','duration_max':'Maximum duration (seconds)',
              'timecode_required':'Embedded timecode','timecode_mode':'Timecode mode','channel_layout':'Audio channel layout',
              'loudness_min':'Minimum integrated loudness (LUFS)','loudness_max':'Maximum integrated loudness (LUFS)',
              'true_peak_max':'Maximum true peak (dBTP)'})
MEASURED_RULES={'loudness_min','loudness_max','true_peak_max'}
LIMIT_RULES={key for key in RULES if key.endswith(('_min','_max'))}


def load_presets(settings):
    from copy import deepcopy
    try:
        presets=json.loads(settings.value('delivery_presets','null'))
        if isinstance(presets,list) and all(isinstance(p,dict) and isinstance(p.get('name'),str) and isinstance(p.get('rules'),dict) for p in presets):return presets
    except (ValueError,TypeError):pass
    return deepcopy(DEFAULT_PRESETS)


def container_name(data):
    fmt=data.get('format',{});names=fmt.get('format_name','').split(',')
    brand=str(fmt.get('tags',{}).get('major_brand','')).strip().lower()
    if 'mov' in names:
        if brand=='qt':return 'mov'
        if brand:return 'mp4' if brand not in ('3gp4','3gp5','3g2a') else '3gp'
        return None  # FFprobe's mov,mp4,... demuxer name alone cannot distinguish these.
    if names==['matroska','webm']:return None
    return names[0] or None


def measure_loudness(path,track,ffmpeg,cancelled):
    code,_,log=capture([ffmpeg,'-nostdin','-hide_banner','-v','info','-i',path,'-map',f'0:a:{track}',
                       '-vn','-af','ebur128=peak=true','-f','null','-'],cancelled,timeout=None,stderr_limit=20000)
    if code:raise ValueError(log[-3000:] or 'Audio measurement failed')
    summary=log.rsplit('Summary:',1)[-1] if 'Summary:' in log else ''
    result={'track':track+1,'path':path}
    for key,pattern in [('integrated_lufs',r'I:\s+(-?[\d.]+|[-+]?inf)\s+LUFS'),('true_peak_dbfs',r'Peak:\s+(-?[\d.]+|[-+]?inf)\s+dBFS')]:
        match=re.search(pattern,summary)
        if match:result[key]=match.group(1)
    if 'integrated_lufs' not in result or 'true_peak_dbfs' not in result:raise ValueError('No complete loudness summary was reported')
    return result


def media_type(data):
    if data.get('kind') == 'still': return 'Image'
    video, audio = media_tracks(data)
    return 'Video' if video else 'Audio' if audio else 'Other'


def numeric(value):
    try: return Fraction(str(value))
    except (ValueError,ZeroDivisionError,TypeError): return None


def bit_depth(stream):
    for key in ('bits_per_raw_sample','bits_per_sample'):
        if numeric(stream.get(key)) and numeric(stream[key]) > 0: return int(stream[key])
    return None


def properties(data):
    """Stable labels include every media track, rather than only the first one."""
    fmt=data.get('format',{});video,audio=media_tracks(data)
    result={'Media type':media_type(data),'Format':fmt.get('format_name'),'File size (bytes)':fmt.get('size')}
    if result['Media type']=='Image':
        image=data['image']
        result.update({'Dimensions':f"{image['width']} × {image['height']}",'Color model':image['mode'],'Bit depth':image['bits'],'ICC profile':image['profile']})
        for group in ('capture','description','properties'):
            result.update({f'Image · {k}':v for k,v in image.get(group,[])})
        return result
    result.update({'Duration':duration(fmt.get('duration')) if fmt.get('duration') else None,'Video tracks':len(video),'Audio tracks':len(audio)})
    if video:
        tc=clip_timecode(data)
        for key,field in [('Timecode In','in_'),('Timecode Out (last)','out'),('Timecode mode','mode')]:
            result[key]=None if tc[field]=='Not available' else tc[field]
        result['Timecode Out calculation']='Estimated' if tc['estimated'] else 'Frame count' if tc['out']!='Not available' else None
    for index,s in enumerate(video,1):
        prefix=f'Video {index} · '
        result.update({prefix+k:v for k,v in {'Codec':s.get('codec_name'),'Profile':s.get('profile'),'Width':s.get('width'),'Height':s.get('height'),
                     'Average frame rate':str(numeric(s.get('avg_frame_rate'))) if numeric(s.get('avg_frame_rate')) else None,
                     'Nominal frame rate':str(numeric(s.get('r_frame_rate'))) if numeric(s.get('r_frame_rate')) else None,
                     'Scan':scan_mode(s),'Pixel format':s.get('pix_fmt'),'Primaries':s.get('color_primaries'),'Transfer':s.get('color_transfer'),
                     'Matrix':s.get('color_space'),'Range':s.get('color_range')}.items()})
    for index,s in enumerate(audio,1):
        prefix=f'Audio {index} · '
        result.update({prefix+k:v for k,v in {'Codec':s.get('codec_name'),'Channels':s.get('channels'),'Layout':s.get('channel_layout'),
                     'Sample rate':s.get('sample_rate'),'Bit depth':bit_depth(s),'Bitrate':s.get('bit_rate'),'Language':s.get('tags',{}).get('language')}.items()})
    return result


def unknown(value):
    return value is None or str(value).strip().lower() in ('','unknown','not reported','not available','unspecified','und')


def compare(left,right):
    a,b=properties(left),properties(right)
    rows=[]
    for key in dict.fromkeys([*a,*b]):
        va,vb=a.get(key),b.get(key)
        state='Not reported' if unknown(va) or unknown(vb) else 'Same' if str(va)==str(vb) else 'Different'
        rows.append((key,'Not reported' if unknown(va) else str(va),'Not reported' if unknown(vb) else str(vb),state))
    return rows


def check_preset(data,preset):
    video,audio=media_tracks(data);rows=[];fmt=data.get('format',{})
    for key,expected in preset.get('rules',{}).items():
        if key not in RULES: continue
        if key=='type': values=[('File',media_type(data))]
        elif key=='container':values=[('File',container_name(data))]
        elif key=='audio_tracks':values=[('File',len(audio))]
        elif key in ('duration_min','duration_max'):values=[('File',fmt.get('duration'))]
        elif key in ('bitrate_min','bitrate_max'):values=[('File',numeric(fmt.get('bit_rate'))/1000000 if numeric(fmt.get('bit_rate')) else None)]
        elif key in ('timecode_required','timecode_mode'):
            tc=clip_timecode(data)
            values=[('File',('Present' if tc['source'] and tc['in_']!='Not available' else 'Missing') if key=='timecode_required' else tc['mode'])]
        elif key in ('width','height') and data.get('kind')=='still':values=[('Image',data.get('image',{}).get(key))]
        elif key.startswith('video_bitrate_'):
            values=[(f'Video {i}',numeric(s.get('bit_rate'))/1000000 if numeric(s.get('bit_rate')) else None) for i,s in enumerate(video,1)] or [('File','No video tracks')]
        elif key in ('width','height','fps','scan','primaries','transfer','matrix','pixel_format','video_codec'):
            values=[]
            for i,s in enumerate(video,1):
                actual=(s.get('avg_frame_rate') or s.get('r_frame_rate')) if key=='fps' else scan_mode(s) if key=='scan' else s.get('color_primaries') if key=='primaries' else s.get('color_transfer') if key=='transfer' else s.get('color_space') if key=='matrix' else s.get('pix_fmt') if key=='pixel_format' else s.get('codec_name') if key=='video_codec' else s.get(key)
                values.append((f'Video {i}',actual))
            if not values: values=[('File','No video tracks')]
        else:
            values=[]
            for i,s in enumerate(audio,1):
                measurement=data.get('audio_measurements',{}).get(str(i),{})
                actual=measurement.get('integrated_lufs') if key.startswith('loudness_') else measurement.get('true_peak_dbfs') if key=='true_peak_max' else numeric(s.get('bit_rate'))/1000000 if key.startswith('audio_bitrate_') and numeric(s.get('bit_rate')) else None if key.startswith('audio_bitrate_') else bit_depth(s) if key=='bits' else s.get('codec_name') if key=='audio_codec' else s.get(key)
                values.append((f'Audio {i}',actual))
            if not values:values=[('File','No audio tracks')]
        for track,actual in values:
            if actual in ('No video tracks','No audio tracks'):state='Mismatch'
            elif unknown(actual) or (key=='fps' and not numeric(actual)):state='Unknown'
            elif key in LIMIT_RULES:
                try:
                    number,target=float(actual),float(expected)
                    state='Unknown' if math.isnan(number) or not math.isfinite(target) else 'Pass' if (number>=target if key.endswith('_min') else number<=target) else 'Mismatch'
                except (ValueError,TypeError,OverflowError):state='Unknown'
            elif key=='fps':state='Unknown' if not numeric(expected) else 'Pass' if abs(numeric(actual)-numeric(expected)) < Fraction(1,1000) else 'Mismatch'
            elif key in ('width','height','audio_tracks','channels','sample_rate','bits'):state='Pass' if numeric(actual)==numeric(expected) else 'Mismatch'
            else:state='Pass' if str(actual).casefold()==str(expected).casefold() else 'Mismatch'
            rows.append({'key':key,'field':RULES[key],'track':track,'expected':str(expected),
                         'actual':('Not measured' if key in MEASURED_RULES else 'Not reported') if unknown(actual) else f'{float(actual):g}' if isinstance(actual,Fraction) and key in LIMIT_RULES else str(actual),'status':state})
    return rows


def preset_status(rows):
    return 'Mismatch' if any(r['status']=='Mismatch' for r in rows) else 'Unknown' if any(r['status']=='Unknown' for r in rows) else 'Pass' if rows else 'Not checked'


class Cancelled(Exception): pass


def capture(command,cancelled,timeout=30,stderr_limit=200000,env=None):
    """Keep subprocess waits off the GUI thread and cancellation prompt."""
    with subprocess.Popen(command,stdout=subprocess.PIPE,stderr=subprocess.PIPE,env=env) as process:
        deadline=time.monotonic()+timeout if timeout else None
        while True:
            if cancelled.is_set():
                process.kill();process.communicate();raise Cancelled()
            if deadline and time.monotonic()>deadline:
                process.kill();process.communicate();raise TimeoutError('Inspection exceeded 30 seconds')
            try:
                stdout,stderr=process.communicate(timeout=.2)
                return process.returncode,stdout,stderr.decode(errors='replace')[-stderr_limit:] if stderr_limit else stderr.decode(errors='replace')
            except subprocess.TimeoutExpired:pass


def inspect_metadata(path,ffprobe,cancelled):
    if Path(path).suffix.lower() in STILL_EXTENSIONS:
        try:
            data,_=read_still(path,metadata_only=True)
            if data:return data
        except Exception:pass  # FFprobe remains a fallback for unsupported/animated formats.
    code,stdout,stderr=capture([ffprobe,*probe_args(path)],cancelled)
    if code:raise ValueError(stderr.strip() or 'FFprobe could not read the file')
    data=json.loads(stdout)
    if not data.get('streams'):raise ValueError('No readable media streams')
    return data


class InspectionSignals(QObject):
    row=Signal(object,int)
    progress=Signal(str,int,int,int)
    done=Signal(str,int)


class InspectionJob(QRunnable):
    def __init__(self,signals,token,paths=None,folder=None,recursive=False,decode=False,ffprobe=None,ffmpeg=None,measure=False):
        super().__init__()
        self.signals,self.token=signals,token
        self.paths,self.folder,self.recursive,self.decode=paths,folder,recursive,decode
        self.measure=measure
        self.ffprobe,self.ffmpeg=ffprobe or executable('ffprobe'),ffmpeg or executable('ffmpeg')
        self.cancelled=Event()

    def emit(self,name,*args):
        try:getattr(self.signals,name).emit(*args,self.token)
        except RuntimeError:self.cancelled.set()

    def run(self):
        try:
            paths=self.paths
            if paths is None:
                paths=[]
                self.emit('progress','Finding media files…',0,0)
                iterator=Path(self.folder).rglob('*') if self.recursive else Path(self.folder).iterdir()
                for p in iterator:
                    if self.cancelled.is_set():raise Cancelled()
                    if p.is_file() and p.suffix.lower() in MEDIA_EXTENSIONS:paths.append(str(p))
                paths.sort(key=str.casefold)
            for index,path in enumerate(paths):
                if self.cancelled.is_set():raise Cancelled()
                self.emit('progress',Path(path).name,index,len(paths))
                row={'path':str(path),'metadata':None,'error':'','decode':'Not scanned','decode_log':''}
                try:
                    row['metadata']=inspect_metadata(path,self.ffprobe,self.cancelled)
                    if self.measure:
                        row['metadata']['audio_measurements']={}
                        from copy import deepcopy
                        row['metadata']['measurement_status']='Measuring'
                        self.emit('row',deepcopy(row))
                        try:
                            for track,_ in enumerate(media_tracks(row['metadata'])[1]):
                                row['metadata']['audio_measurements'][str(track+1)]=measure_loudness(str(path),track,self.ffmpeg,self.cancelled)
                            row['metadata']['measurement_status']='Complete'
                        except Cancelled:raise
                        except Exception as error:
                            row['metadata']['measurement_status']='Failed'
                            row['metadata']['measurement_error']=str(error)
                    if self.decode:
                        row['decode']='Scanning'
                        self.emit('row',dict(row))
                        if row['metadata'].get('kind')=='still':
                            with Image.open(path) as image:
                                for frame in ImageSequence.Iterator(image):
                                    if self.cancelled.is_set():raise Cancelled()
                                    frame.load()
                            row['decode']='Image decoded'
                        else:
                            code,_,log=capture([self.ffmpeg,*scan_args(path)],self.cancelled,timeout=None)
                            row['decode_log']=log
                            row['decode']='Decode failed' if code else 'Decode warnings' if log.strip() else 'Decode passed'
                except Cancelled:raise
                except Exception as error:
                    if row['metadata']:
                        row['decode']='Decode failed';row['decode_log']=str(error)
                    else:row['error']=str(error)
                self.emit('row',row)
            self.emit('progress','Finished',len(paths),len(paths))
            self.emit('done','Complete' if paths else 'No supported media files found')
        except Cancelled:self.emit('done','Cancelled · partial results retained')
        except Exception as error:self.emit('done',str(error))


class MeasurementSignals(QObject):
    ready=Signal(object,str,int)
    progress=Signal(str,int)


class MeasurementJob(QRunnable):
    def __init__(self,path,count,ffmpeg,signals,token):
        super().__init__();self.path,self.count,self.ffmpeg,self.signals,self.token=path,count,ffmpeg,signals,token;self.cancelled=Event()
    def run(self):
        try:
            results={}
            for track in range(self.count):
                if self.cancelled.is_set():raise Cancelled()
                self.signals.progress.emit(f'Measuring audio {track+1}/{self.count}…',self.token)
                results[str(track+1)]=measure_loudness(self.path,track,self.ffmpeg,self.cancelled)
            if not self.cancelled.is_set():self.signals.ready.emit(results,'',self.token)
        except Cancelled:pass
        except Exception as error:
            if not self.cancelled.is_set():
                try:self.signals.ready.emit(None,str(error),self.token)
                except RuntimeError:pass
