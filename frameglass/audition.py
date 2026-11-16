"""Channel monitoring of decoded PCM. Source audio and measurements stay untouched."""
import math
import time
import numpy as np
from PySide6.QtCore import Qt,QTimer
from PySide6.QtMultimedia import QAudioFormat,QAudioSink,QMediaDevices,QtAudio
from PySide6.QtWidgets import QDialog,QVBoxLayout,QHBoxLayout,QLabel,QPushButton,QCheckBox,QComboBox,QScrollArea,QWidget,QGridLayout,QToolButton

LAYOUTS={
    'mono':['FC'],'stereo':['FL','FR'],'2.1':['FL','FR','LFE'],
    '3.0':['FL','FR','FC'],'3.0(back)':['FL','FR','BC'],
    'quad':['FL','FR','BL','BR'],'quad(side)':['FL','FR','SL','SR'],
    '4.0':['FL','FR','FC','BC'],'5.0':['FL','FR','FC','BL','BR'],
    '5.0(side)':['FL','FR','FC','SL','SR'],'5.1':['FL','FR','FC','LFE','BL','BR'],
    '5.1(side)':['FL','FR','FC','LFE','SL','SR'],'7.1':['FL','FR','FC','LFE','BL','BR','SL','SR'],
    '7.1(wide)':['FL','FR','FC','LFE','BL','BR','FLC','FRC']}
NAMES={'FL':'Left','FR':'Right','FC':'Center','LFE':'LFE','BL':'Back left','BR':'Back right','SL':'Side left','SR':'Side right','BC':'Back center','FLC':'Front left of center','FRC':'Front right of center'}


def channel_assignments(stream):
    count=int(stream.get('channels') or 0);layout=stream.get('channel_layout','')
    labels=LAYOUTS.get(layout)
    if not labels and '+' in layout:labels=layout.split('+')
    return labels if labels and len(labels)==count else [f'Ch {i+1}' for i in range(count)]


SHORT={'FL':'L','FR':'R','FC':'C','LFE':'LFE','BC':'Cs','FLC':'Lc','FRC':'Rc'}
PAIRS=(('FL','FR'),('FLC','FRC'),('SL','SR'),('BL','BR'))
MINUS_3_DB=math.sqrt(.5)
# Lo/Ro stereo mixdown: fronts at unity, center and surrounds at -3 dB, LFE left out.
MIXDOWN={'FL':(1,0),'FR':(0,1),'FC':(MINUS_3_DB,MINUS_3_DB),'LFE':(0,0),'BC':(.5,.5),
         'BL':(MINUS_3_DB,0),'SL':(MINUS_3_DB,0),'FLC':(MINUS_3_DB,0),'BR':(0,MINUS_3_DB),'SR':(0,MINUS_3_DB),'FRC':(0,MINUS_3_DB)}


def short_labels(labels):
    """Mixer-style channel names. Side and rear surrounds are told apart only when both exist."""
    both={'SL','BL'}<=set(labels) or {'SR','BR'}<=set(labels)
    surround={'SL':'Lss' if both else 'Ls','SR':'Rss' if both else 'Rs','BL':'Lrs' if both else 'Ls','BR':'Rrs' if both else 'Rs'}
    return [SHORT.get(label) or surround.get(label) or label for label in labels]


def monitor_options(labels):
    """Monitor choices for one audio track as (title, mode). The first one is the default.

    A mixdown is offered only when every channel has a reported assignment the
    fold-down knows; unassigned channels are never panned by guesswork.
    """
    count=len(labels);names=short_labels(labels)
    if count<=1:return [('Mono',('native',))]
    if count==2:options=[('Stereo',('native',))]
    elif all(label in MIXDOWN for label in labels):options=[('Stereo mixdown',('mixdown',)),('Player default',('native',))]
    else:options=[('Player default',('native',))]
    if count>2:
        pairs=[(labels.index(left),labels.index(right)) for left,right in PAIRS if left in labels and right in labels]
        if not pairs and all(label.startswith('Ch ') for label in labels):pairs=[(i,i+1) for i in range(0,count-1,2)]
        options+=[(f'{names[i]} / {names[j]}',('pair',i,j)) for i,j in pairs]
    return options+[(f'{names[i]} only',('channel',i)) for i in range(count)]


def monitor_route(labels,mode):
    """(stereo weights, meter weights, meter names) for a monitor mode; None leaves the player's own output."""
    count=len(labels);names=short_labels(labels);kind=mode[0]
    if kind=='native':return None
    stereo=np.zeros((count,2),dtype=np.float32)
    if kind=='mixdown':
        for index,label in enumerate(labels):stereo[index]=MIXDOWN[label]
        return stereo,stereo,['L','R']
    if kind=='pair':
        left,right=mode[1],mode[2];stereo[left]=(1,0);stereo[right]=(0,1)
        return stereo,stereo,[names[left],names[right]]
    if kind=='channel':
        # One channel plays in both speakers; the meter shows that single channel.
        index=mode[1];stereo[index]=(1,1);meter=np.zeros((count,1),dtype=np.float32);meter[index]=1
        return stereo,meter,[names[index]]
    raise ValueError('Unknown monitor mode')


def pcm_samples(buffer):
    if not buffer.isValid() or not buffer.frameCount():return None
    fmt=buffer.format();types={QAudioFormat.UInt8:('u1',128),QAudioFormat.Int16:('<i2',32768),QAudioFormat.Int32:('<i4',2147483648),QAudioFormat.Float:('<f4',1)}
    if fmt.sampleFormat() not in types:return None
    dtype,scale=types[fmt.sampleFormat()]
    samples=np.frombuffer(buffer.constData(),dtype=dtype).reshape(-1,fmt.channelCount()).astype(np.float32)
    if fmt.sampleFormat()==QAudioFormat.UInt8:samples-=128
    return samples/scale


def correlation(samples,left,right):
    if left==right or max(left,right)>=samples.shape[1]:return None
    pair=samples[:,[left,right]].astype(np.float64);pair-=pair.mean(axis=0)
    energy=np.sum(pair*pair,axis=0)
    if np.min(energy)<len(pair)*1e-10:return None
    return float(np.clip(np.sum(pair[:,0]*pair[:,1])/math.sqrt(energy[0]*energy[1]),-1,1))


def monitor_mix(samples,labels,solos,mutes,mono=False):
    """Stereo monitor fold-down. Normalize weights for headroom, never alter source."""
    count=samples.shape[1];active=[i for i in range(count) if i not in mutes and (not solos or i in solos)]
    weights=np.zeros((count,2),dtype=np.float32)
    if len(active)==1 and solos:weights[active[0]]=[1,1]
    else:
        for i in active:
            name=labels[i] if i<len(labels) else ''
            weights[i]=[1,0] if name in ('FL','FLC') else [0,1] if name in ('FR','FRC') else [.707,0] if name in ('BL','SL') else [0,.707] if name in ('BR','SR') else [.5,.5] if name=='LFE' else [.707,.707]
        weights/=np.maximum(1,np.abs(weights).sum(axis=0))
    result=samples@weights
    if mono:result[:]=result.mean(axis=1)[:,None]
    return np.ascontiguousarray(np.clip(result,-1,1),dtype='<f4')


class MonitorOutput:
    """Push PCM through a native Qt device, with a short bounded queue."""
    def __init__(self,parent):
        self.parent=parent;self.sink=None;self.device=None;self.rate=None;self.pending=bytearray();self.volume=.7;self.bytes_written=0
        self.timer=QTimer(parent);self.timer.setInterval(5);self.timer.timeout.connect(self.flush)
    def reset(self):
        self.timer.stop();self.pending.clear()
        if self.sink:self.sink.reset();self.sink.deleteLater()
        self.sink=None;self.device=None;self.rate=None
    def write(self,samples,rate,volume):
        if self.rate!=rate:
            self.reset();fmt=QAudioFormat();fmt.setSampleRate(rate);fmt.setChannelCount(2);fmt.setSampleFormat(QAudioFormat.Float)
            device=QMediaDevices.defaultAudioOutput()
            if device.isNull() or not device.isFormatSupported(fmt):raise ValueError('The audio device does not support stereo float monitoring at this sample rate.')
            self.sink=QAudioSink(device,fmt,self.parent);self.sink.setBufferSize(max(4096,int(rate*8*.08)));self.device=self.sink.start();self.rate=rate
            if self.device is None or self.sink.error()!=QtAudio.NoError:raise ValueError('Audio channel monitor could not open the output device.')
            self.timer.start()
        self.sink.setVolume(volume);self.pending.extend(samples.tobytes())
        # Never accumulate unbounded latency after GUI stalls.
        limit=int(rate*8*.15)
        if len(self.pending)>limit:del self.pending[:((len(self.pending)-limit+7)//8)*8]
        self.flush()
    def flush(self):
        if not self.sink or not self.device:return
        count=min(len(self.pending),self.sink.bytesFree());count-=count%8
        if count:
            written=self.device.write(bytes(self.pending[:count]))
            if written>0:del self.pending[:written];self.bytes_written+=written


class MonitorSelector(QComboBox):
    """Chooses what the selected audio track sends to the speakers and the meter."""
    HELP='What you hear and what the meter shows. Stereo mixdown: left/right at full level, center and surrounds at -3 dB, LFE left out.\nMeasurements always read the whole track. Monitoring runs at 1x forward playback.'
    def __init__(self,player,parent=None):
        super().__init__(parent);self.player=player;self.options=[];self.setAccessibleName('Audio monitor');self.setMinimumWidth(150)
        self.currentIndexChanged.connect(self.chosen);player.monitorChanged.connect(self.reload);self.reload()
    def reload(self):
        self.options=self.player.monitor_choices();self.blockSignals(True);self.clear()
        self.addItems([title for title,mode in self.options])
        self.setCurrentIndex(next((i for i,(title,mode) in enumerate(self.options) if mode==self.player.monitor_mode),0))
        self.blockSignals(False);self.setEnabled(len(self.options)>1)
        note=self.player.monitor_note
        self.setToolTip(self.HELP+('\n\nUsing the player default: '+note if note else ''))
    def chosen(self,index):
        if 0<=index<len(self.options):self.player.set_monitor(self.options[index][1])


class ChannelPanel(QDialog):
    def __init__(self,player):
        super().__init__(player);self.player=player;self.setWindowTitle('Frameglass · Audio channels');self.resize(560,360);self.last_ui=0;self.smoothed=None
        layout=QVBoxLayout(self);self.heading=QLabel();self.heading.setObjectName('sectionTitle');self.heading.setTextFormat(Qt.PlainText);heading_row=QHBoxLayout();heading_row.addWidget(self.heading,1);play=QPushButton('Play / Pause');play.setObjectName('quiet');play.clicked.connect(player.toggle);heading_row.addWidget(play);layout.addLayout(heading_row)
        note=QLabel('Solo / Mute affect monitoring of the selected track only. A solo channel plays in both speakers. Multichannel selections use a stereo fold-down with headroom.');note.setWordWrap(True);note.setObjectName('muted');layout.addWidget(note)
        scroll=QScrollArea();scroll.setWidgetResizable(True);self.rows=QWidget();self.grid=QGridLayout(self.rows);scroll.setWidget(self.rows);layout.addWidget(scroll,1)
        controls=QHBoxLayout();self.mono=QCheckBox('Monitor in mono');self.mono.setStyleSheet('QCheckBox::indicator { width: 12px; height: 12px; border: 1px solid #657587; border-radius: 3px; background: #1b2028; } QCheckBox::indicator:checked { background: #adddce; border-color: #adddce; }');self.mono.toggled.connect(self.changed);controls.addWidget(self.mono)
        reset=QPushButton('Reset Solo / Mute');reset.clicked.connect(self.reset);controls.addWidget(reset);layout.addLayout(controls)
        pair=QHBoxLayout();pair.addWidget(QLabel('Phase correlation'));self.left=QComboBox();self.right=QComboBox();pair.addWidget(self.left);pair.addWidget(self.right);layout.addLayout(pair)
        self.phase=QLabel('Play to measure');self.phase.setToolTip('Correlation of the selected decoded channel pair, before Solo/Mute/volume. +1 = correlated; 0 = uncorrelated; −1 = opposite polarity. Negative values flag possible mono cancellation, not necessarily an error. Silence has no result.');layout.addWidget(self.phase)
        self.status=QLabel('');self.status.setWordWrap(True);self.status.setObjectName('muted');layout.addWidget(self.status)
        self.left.currentIndexChanged.connect(self.clear_phase);self.right.currentIndexChanged.connect(self.clear_phase);self.load_track()
    def clear_phase(self,*args):self.smoothed=None;self.phase.setText('Play to measure')
    def load_track(self):
        while self.grid.count():
            item=self.grid.takeAt(0)
            if item.widget():item.widget().deleteLater()
        from .timecode import media_tracks
        tracks=media_tracks(self.player.data)[1];index=self.player.selected_audio_track;stream=tracks[index] if index<len(tracks) else {}
        title=stream.get('tags',{}).get('title','');self.heading.setText(f'Audio track {index+1}'+(' · '+title if title else ''))
        self.labels=channel_assignments(stream);self.solo=[];self.mute=[];self.peaks=[]
        for column,text in enumerate(('Channel / reported assignment','Solo','Mute','Sample peak')):self.grid.addWidget(QLabel(text),0,column)
        for i,assignment in enumerate(self.labels):
            name=QLabel(f'{i+1} · {NAMES.get(assignment,assignment)}');self.grid.addWidget(name,i+1,0)
            solo=QToolButton();solo.setText('S');solo.setCheckable(True);solo.setFixedSize(28,26);solo.setStyleSheet('QToolButton { padding: 0; } QToolButton:checked { color: #14251f; background: #adddce; border-color: #adddce; }');solo.setAccessibleName(f'Solo channel {i+1}');solo.toggled.connect(self.changed);self.grid.addWidget(solo,i+1,1);self.solo.append(solo)
            mute=QToolButton();mute.setText('M');mute.setCheckable(True);mute.setFixedSize(28,26);mute.setStyleSheet('QToolButton { padding: 0; } QToolButton:checked { color: #3a171b; background: #f2aaa9; border-color: #f2aaa9; }');mute.setAccessibleName(f'Mute channel {i+1}');mute.toggled.connect(self.changed);self.grid.addWidget(mute,i+1,2);self.mute.append(mute)
            peak=QLabel('—');self.grid.addWidget(peak,i+1,3);self.peaks.append(peak)
        self.grid.setRowStretch(len(self.labels)+1,1)
        for combo in (self.left,self.right):combo.blockSignals(True);combo.clear();combo.addItems([f'{i+1} · {v}' for i,v in enumerate(self.labels)]);combo.blockSignals(False)
        self.right.setCurrentIndex(min(1,len(self.labels)-1));self.clear_phase();self.mono.blockSignals(True);self.mono.setChecked(False);self.mono.blockSignals(False)
        self.status.setText('Assignments come from the reported channel layout. Unspecified layouts stay numbered. Monitoring is available at 1× forward playback.')
    def changed(self,*args):
        self.player.audition_solos={i for i,b in enumerate(self.solo) if b.isChecked()};self.player.audition_mutes={i for i,b in enumerate(self.mute) if b.isChecked()};self.player.audition_mono=self.mono.isChecked();self.player.monitor.reset();self.player.update_audio_routing()
    def reset(self):
        for button in self.solo+self.mute:button.blockSignals(True);button.setChecked(False);button.blockSignals(False)
        self.mono.blockSignals(True);self.mono.setChecked(False);self.mono.blockSignals(False);self.changed()
    def samples(self,samples):
        if not self.isVisible():return
        value=correlation(samples,self.left.currentIndex(),self.right.currentIndex()) if self.left.count()>1 else None
        self.smoothed=value if value is None or self.smoothed is None else .8*self.smoothed+.2*value
        if time.monotonic()-self.last_ui<.08:return
        self.last_ui=time.monotonic()
        peaks=np.max(np.abs(samples),axis=0)
        for widget,peak in zip(self.peaks,peaks):widget.setText(f'{20*math.log10(float(peak)):.1f} dBFS' if peak>1e-9 else '−∞')
        value=self.smoothed
        self.phase.setText('Phase · unavailable (mono, same channel, or silence)' if value is None else f'Phase correlation {value:+.2f}'+(' · possible mono cancellation' if value<-.2 else ''))
        self.phase.setStyleSheet('color: '+('#ecc78e' if value is not None and value<-.2 else '#a4dacf' if value is not None else '#9baabb'))
    def reject(self):self.reset();super().reject()
    def closeEvent(self,event):self.reset();super().closeEvent(event)
