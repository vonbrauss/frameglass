"""Display histograms, pixel readout, and measured audio analysis."""
import json
import math
import re
from pathlib import Path
from threading import Event
import numpy as np
from PySide6.QtCore import Qt, QObject, QRunnable, Signal, QPointF, QRectF, QThreadPool
from PySide6.QtGui import QPainter,QPen,QColor,QPainterPath,QImage
from PySide6.QtWidgets import QWidget,QDialog,QVBoxLayout,QHBoxLayout,QPushButton,QLabel,QTextEdit,QProgressBar
from .core import executable
from .quality import capture,Cancelled


class Histogram(QWidget):
    def __init__(self,image,parent=None):
        super().__init__(parent);self.setMinimumSize(400,200)
        reduced=image.scaled(1024,1024,Qt.KeepAspectRatio,Qt.SmoothTransformation).convertToFormat(QImage.Format_RGBA8888)
        data=np.frombuffer(reduced.constBits(),np.uint8).reshape(reduced.height(),reduced.bytesPerLine())[:,:reduced.width()*4].reshape(-1,4)
        data=data[data[:,3]>0]
        self.hist=[np.log1p(np.bincount(data[:,c],minlength=256)) for c in range(3)]
    def paintEvent(self,event):
        p=QPainter(self);p.fillRect(self.rect(),QColor('#10151c'));p.setRenderHint(QPainter.Antialiasing)
        peak=max(1,max(float(h.max()) for h in self.hist));w=self.width()-20;h=self.height()-20
        for hist,color in zip(self.hist,('#f0a5a5','#a4dacf','#8baeea')):
            path=QPainterPath(QPointF(10,self.height()-10))
            for x,value in enumerate(hist):path.lineTo(10+x*w/255,self.height()-10-float(value)/peak*h)
            p.setPen(QPen(QColor(color),1.5));p.drawPath(path)


class Waveform(QWidget):
    seekRequested=Signal(float)
    def __init__(self):
        super().__init__();self.setMinimumHeight(110);self.setFocusPolicy(Qt.StrongFocus);self.points=None;self.position=0
    def paintEvent(self,event):
        p=QPainter(self);p.fillRect(self.rect(),QColor('#10151c'));p.setPen(QPen(QColor('#a1d6c5'),1))
        if self.points is not None:
            center=self.height()/2
            for index,(low,high) in enumerate(self.points):
                x=index*max(1,self.width()-1)/len(self.points);p.drawLine(QPointF(x,center-high*center*.9),QPointF(x,center-low*center*.9))
        p.setPen(QPen(QColor('#f0c88e'),1));p.drawLine(round(self.position*self.width()),0,round(self.position*self.width()),self.height())
    def mousePressEvent(self,event):
        self.setFocus(Qt.MouseFocusReason)
        self.seekRequested.emit(max(0,min(1,event.position().x()/max(1,self.width()))))


class AnalysisSignals(QObject):
    ready=Signal(object,str,int)


def parse_audio_analysis(log):
    """Parse the final EBU R128 summary and FFmpeg silence events only."""
    summary=log.rsplit('Summary:',1)[-1] if 'Summary:' in log else ''
    result={}
    for key,pattern in [('integrated_lufs',r'I:\s+(-?[\d.]+|[-+]?inf)\s+LUFS'),('loudness_range_lu',r'LRA:\s+([\d.]+)\s+LU'),('true_peak_dbfs',r'Peak:\s+(-?[\d.]+|[-+]?inf)\s+dBFS')]:
        match=re.search(pattern,summary)
        if match:result[key]=match.group(1)
    starts=re.findall(r'silence_start:\s*([\d.]+)',log)
    ends=re.findall(r'silence_end:\s*([\d.]+)\s*\|\s*silence_duration:\s*([\d.]+)',log)
    result['silence_regions']=[{'start_seconds':float(start),'end_seconds':float(end),'duration_seconds':float(length)} for start,(end,length) in zip(starts,ends)]
    if len(starts)>len(ends):result['open_silence_start_seconds']=float(starts[-1])
    result['silence_threshold_db']=-50;result['minimum_silence_seconds']=.5
    return result


class AudioAnalysisJob(QRunnable):
    def __init__(self,path,track,ffmpeg,signals,token,seconds):
        super().__init__();self.path,self.track,self.ffmpeg,self.signals,self.token,self.seconds=path,track,ffmpeg,signals,token,seconds;self.cancelled=Event()
    def run(self):
        try:
            code,_,log=capture([self.ffmpeg,'-nostdin','-hide_banner','-v','info','-i',self.path,'-map',f'0:a:{self.track}',
                                '-vn','-af','ebur128=peak=true,silencedetect=noise=-50dB:d=0.5','-f','null','-'],self.cancelled,timeout=None,stderr_limit=None)
            if code:raise ValueError(log[-5000:])
            result=parse_audio_analysis(log)
            try:
                # Measure an envelope before reducing the point count, so high-frequency
                # audio cannot disappear as it would in a low-rate audio resample.
                bucket=max(1,round(48000*self.seconds/1500)) if self.seconds>0 else 48000
                filters=f'aformat=channel_layouts=mono:sample_fmts=flt,aresample=48000,asetnsamples=n={bucket}:p=0,astats=metadata=1:reset=1:measure_perchannel=none:measure_overall=Min_level+Max_level,ametadata=mode=print:file=-'
                code,raw,error=capture([self.ffmpeg,'-nostdin','-v','error','-i',self.path,'-map',f'0:a:{self.track}',
                                        '-vn','-af',filters,'-c:a','pcm_f32le','-f','null','-'],self.cancelled,timeout=None)
                if code:raise ValueError(error)
                metadata=raw.decode(errors='replace')
                low=[float(v) for v in re.findall(r'lavfi.astats.Overall.Min_level=([-+]?(?:[\d.]+(?:[eE][-+]?\d+)?|inf|nan))',metadata)]
                high=[float(v) for v in re.findall(r'lavfi.astats.Overall.Max_level=([-+]?(?:[\d.]+(?:[eE][-+]?\d+)?|inf|nan))',metadata)]
                if not low or len(low)!=len(high):raise ValueError('Waveform envelope was not reported by FFmpeg')
                envelope=np.array(list(zip(low,high)),dtype=float)
                envelope=envelope[np.isfinite(envelope).all(axis=1)]
                if not len(envelope):raise ValueError('No finite waveform samples were reported')
                points=[(float(chunk[:,0].min()),float(chunk[:,1].max())) for chunk in np.array_split(envelope,min(1500,len(envelope))) if len(chunk)]
                result['waveform']=points
                result['waveform_note']='Waveform envelope · mono downmix at 48 kHz · not a true-peak measurement'
            except Cancelled:
                raise
            except Exception as waveform_error:
                result['waveform']=[]
                result['waveform_note']='Waveform unavailable: '+str(waveform_error)
            result['track']=self.track+1;result['path']=self.path;result['log']=log
            if not self.cancelled.is_set():self.signals.ready.emit(result,'',self.token)
        except Cancelled:pass
        except Exception as error:
            if not self.cancelled.is_set():
                try:self.signals.ready.emit(None,str(error),self.token)
                except RuntimeError:pass


class AudioAnalysisDialog(QDialog):
    def __init__(self,path,track,settings,player,seconds,parent=None):
        super().__init__(parent);self.setWindowTitle('Frameglass · Audio analysis');self.resize(850,560)
        self.setAttribute(Qt.WA_DeleteOnClose)
        self.player=player;self.path=path;self.track=track;self.result_data=None
        layout=QVBoxLayout(self)
        label=QLabel(f'{Path(path).name} · Audio track {track+1}');label.setTextFormat(Qt.PlainText);label.setObjectName('sectionTitle');layout.addWidget(label)
        note=QLabel('Measures the entire selected track. Silence: below −50 dB for at least 0.5 s. No delivery target is assumed.');note.setWordWrap(True);note.setObjectName('muted');layout.addWidget(note)
        self.waveform=Waveform();self.waveform.setEnabled(False);self.waveform.seekRequested.connect(self.seek);layout.addWidget(self.waveform)
        self.details=QTextEdit();self.details.setReadOnly(True);self.details.setPlainText('Measuring loudness, true peak and silence…');layout.addWidget(self.details,1)
        self.progress=QProgressBar();self.progress.setRange(0,0);layout.addWidget(self.progress)
        row=QHBoxLayout();self.cancel=QPushButton('Cancel analysis');self.cancel.clicked.connect(self.cancel_job);row.addWidget(self.cancel)
        row.addStretch();self.copy=QPushButton('Copy analysis');self.copy.setEnabled(False);self.copy.clicked.connect(self.copy_result);row.addWidget(self.copy);layout.addLayout(row)
        self.signals=AnalysisSignals(self);self.signals.ready.connect(self.ready,Qt.QueuedConnection)
        self.job=AudioAnalysisJob(path,track,executable('ffmpeg',settings.value('ffmpeg','')),self.signals,1,seconds)
        QThreadPool.globalInstance().start(self.job)
    def ready(self,result,error,token):
        if self.job.cancelled.is_set():return
        self.progress.hide();self.cancel.setEnabled(False)
        if error:self.details.setPlainText('Analysis unavailable:\n'+error);return
        self.result_data=result;self.copy.setEnabled(True);self.waveform.setEnabled(True);self.waveform.points=result['waveform'];self.waveform.update()
        rows=[f"Integrated loudness: {result.get('integrated_lufs','Not reported')} LUFS",f"Loudness range: {result.get('loudness_range_lu','Not reported')} LU",f"True peak: {result.get('true_peak_dbfs','Not reported')} dBFS",'',result['waveform_note'],'','Detected silence regions:']
        rows.extend(f"{r['start_seconds']:.3f}–{r['end_seconds']:.3f} s ({r['duration_seconds']:.3f} s)" for r in result['silence_regions'])
        if not result['silence_regions']:rows.append('None reported at the configured threshold.')
        if 'open_silence_start_seconds' in result:rows.append(f"Silence begins at {result['open_silence_start_seconds']:.3f} s; no end was reported.")
        self.details.setPlainText('\n'.join(rows))
    def seek(self,fraction):
        if self.player.disposed:return
        self.player.ensure_player();self.player.pending_seek=round(fraction*float(self.player.data.get('format',{}).get('duration',0))*1000);self.player.apply_pending_seek()
    def copy_result(self):
        from PySide6.QtWidgets import QApplication
        result={k:v for k,v in self.result_data.items() if k not in ('waveform','log')};QApplication.clipboard().setText(json.dumps(result,indent=2))
    def cancel_job(self):
        self.job.cancelled.set();self.progress.hide();self.cancel.setEnabled(False);self.details.setPlainText('Analysis cancelled. No complete measurement is available.')
    def reject(self):self.job.cancelled.set();super().reject()
    def closeEvent(self,event):self.job.cancelled.set();super().closeEvent(event)

class PixelLabel(QLabel):
    pixelChanged=Signal(str)
    def __init__(self,image):
        super().__init__();self.image=image;self.setMouseTracking(True)
    def pixel_at(self,point):
        pixmap=self.pixmap()
        if not pixmap or pixmap.isNull():return ''
        size=pixmap.deviceIndependentSize();x=int(point.x()*self.image.width()/max(1,size.width()));y=int(point.y()*self.image.height()/max(1,size.height()))
        if not 0<=x<self.image.width() or not 0<=y<self.image.height():return ''
        color=self.image.pixelColor(x,y)
        return f'{x}, {y} · Preview RGB {color.red()}, {color.green()}, {color.blue()} · A {color.alpha()}'
    def mouseMoveEvent(self,event):self.pixelChanged.emit(self.pixel_at(event.position()));super().mouseMoveEvent(event)
