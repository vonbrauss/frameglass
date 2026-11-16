"""Local playback in the overview; media is opened only on user request."""
from pathlib import Path
from threading import Event

import numpy as np
from shiboken6 import isValid

from PySide6.QtCore import QEvent, QSize, Qt, QUrl, QTimer, QElapsedTimer, QThreadPool, Signal
from PySide6.QtGui import QDesktopServices, QKeySequence, QShortcut, QPainter, QColor
from PySide6.QtMultimedia import QMediaPlayer, QVideoSink, QVideoFrame, QAudioBufferOutput
from PySide6.QtQml import QQmlEngine, QQmlComponent
from PySide6.QtMultimediaWidgets import QVideoWidget
from PySide6.QtWidgets import (QFrame, QGridLayout, QHBoxLayout, QLabel, QMenu,
                              QSlider, QStackedWidget, QToolButton, QVBoxLayout,
                              QWidget, QWidgetAction, QSizePolicy, QDialog)
from .audition import MonitorOutput,ChannelPanel,pcm_samples,monitor_mix,channel_assignments,monitor_options,monitor_route,short_labels
from .core import duration
from .design import icon, tool_button, Preview
from .color import display_transform, ColorJob, ColorSignals
from .audio_monitor import ChannelMeter, MonitorChips
from .timecode import playhead_timecode, media_tracks, positive_fraction, duration_timecode


class ScrubSlider(QSlider):
    """Slim timeline with direct click-to-seek and native keyboard support."""
    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        center = self.height() / 2
        span = max(1, self.width() - 8)
        fraction = (self.value() - self.minimum()) / max(1, self.maximum() - self.minimum())
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor('#35414b'))
        painter.drawRoundedRect(4, int(center - 1), span, 3, 1, 1)
        if self.isEnabled():
            painter.setBrush(QColor('#a1d6c5'))
            painter.drawRoundedRect(4, int(center - 1), max(0, round(span * fraction)), 3, 1, 1)
            painter.drawEllipse(round(4 + span * fraction) - 3, round(center) - 3, 6, 6)

    def move_to(self, event):
        fraction = max(0, min(1, (event.position().x() - 4) / max(1, self.width() - 8)))
        self.setValue(round(self.minimum() + fraction * (self.maximum() - self.minimum())))
        self.sliderMoved.emit(self.value())

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton and self.isEnabled():
            self.setFocus(Qt.MouseFocusReason)
            self.setSliderDown(True)
            self.move_to(event)
            event.accept()
        else:
            super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self.isSliderDown():
            self.move_to(event)
            event.accept()
        else:
            super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton and self.isSliderDown():
            self.move_to(event)
            self.setSliderDown(False)
            event.accept()
        else:
            super().mouseReleaseEvent(event)


class ClipPlayer(QFrame):
    monitorChanged = Signal()

    def __init__(self, preview, path, settings, data, playable=True, audio_only=False):
        super().__init__()
        self.setObjectName('clipPlayer')
        self.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Expanding)
        self.setMinimumWidth(220)
        self.audio_only = audio_only
        self.preview = preview
        self.path = str(Path(path).absolute())
        self.settings = settings
        self.data = data
        videos, _ = media_tracks(data)
        self.transform = display_transform(videos[0]) if videos and settings.value('color_mode', 'auto') == 'auto' else None
        self.color_busy = False
        self.color_signals = ColorSignals(self)
        self.color_signals.ready.connect(self.color_frame_ready, Qt.QueuedConnection)
        self.color_cancelled = Event()
        self.color_job = None
        self.pending_color_frame = None
        self.selected_audio_track = 0
        self.audition_solos=set();self.audition_mutes=set();self.audition_mono=False
        self.channel_panel=None;self.monitor=MonitorOutput(self);self.channel_meters=[]
        self.reset_monitor()
        self.speed = 0
        self.held_k = False
        self.pending_step = 0
        self.pending_seek = None
        self.expects_video = bool(media_tracks(data)[0])
        self.reverse_timer = QTimer(self)
        self.reverse_timer.setInterval(50)
        self.reverse_timer.timeout.connect(self.reverse_tick)
        self.reverse_clock = QElapsedTimer()
        self.reverse_origin = 0
        self.reverse_ready = False
        self.player = None
        self.audio = None
        self.video = None
        self.disposed = False
        self.failed = False
        self.frame_seen = False
        self.audio_frame_seen = False
        self.setFocusPolicy(Qt.StrongFocus)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        self.stack = QStackedWidget()
        self.stack.setMinimumSize(0, 100)
        self.stack.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Expanding)
        self.fullscreen_dialog=None
        poster = QWidget()
        poster_layout = QGridLayout(poster)
        poster_layout.setContentsMargins(0, 0, 0, 0)
        preview.setMinimumSize(0, 100)
        preview.setCursor(Qt.PointingHandCursor if playable else Qt.ArrowCursor)
        preview.installEventFilter(self)
        poster_layout.addWidget(preview, 0, 0)
        self.overlay = QToolButton()
        self.overlay.setObjectName('playOverlay')
        self.overlay.setIcon(icon('play', '#e5f5ef', 34))
        self.overlay.setIconSize(QSize(34, 34))
        self.overlay.setFixedSize(58, 58)
        self.overlay.setToolTip('Play clip')
        self.overlay.setAccessibleName('Play clip')
        self.overlay.clicked.connect(self.toggle)
        poster_layout.addWidget(self.overlay, 0, 0, Qt.AlignCenter)
        self.error_label = QLabel()
        self.error_label.setObjectName('playbackError')
        self.error_label.setTextFormat(Qt.PlainText)
        self.error_label.setWordWrap(True)
        self.error_label.setAlignment(Qt.AlignCenter)
        self.error_label.hide()
        poster_layout.addWidget(self.error_label, 0, 0, Qt.AlignBottom)
        self.stack.addWidget(poster)
        # Picture and channel meters share one row, so levels are read without looking away.
        has_audio = bool(media_tracks(data)[1])
        self.monitor_row = has_audio
        self.meter = ChannelMeter(stretch=audio_only)
        self.meter.setVisible(has_audio)
        if has_audio and not audio_only:
            self.stack.setMinimumSize(160, 100)
        self.screen_area = QWidget()
        picture_row = QHBoxLayout(self.screen_area)
        picture_row.setContentsMargins(0, 0, 4 if has_audio else 0, 0)
        picture_row.setSpacing(6)
        picture_row.addWidget(self.stack, 1)
        picture_row.addWidget(self.meter, 1 if audio_only else 0)
        layout.addWidget(self.screen_area, 1)
        controls = QHBoxLayout()
        controls.setContentsMargins(3, 2, 3, 2)
        controls.setSpacing(2)
        self.play_button = tool_button('play', 'Play clip', self.toggle)
        controls.addWidget(self.play_button)
        self.seek = ScrubSlider(Qt.Horizontal)
        self.seek.setObjectName('playbackSeek')
        self.seek.setRange(0, 0)
        self.seek.setMinimumWidth(20)
        self.seek.setEnabled(False)
        self.seek.setAccessibleName('Playback position')
        self.seek.setToolTip('Seek through clip')
        self.seek.sliderMoved.connect(self.seek_to)
        self.seek.sliderReleased.connect(lambda: self.seek_to(self.seek.value()))
        self.seek.valueChanged.connect(self.seek_changed)
        controls.addWidget(self.seek, 1)
        self.position_label = QLabel('Time ' + duration(0) if audio_only else playhead_timecode(data, 0))
        self.position_label.setObjectName('playbackClock')
        self.position_label.setMinimumWidth(0)
        self.position_label.setToolTip('Playhead: source timecode when embedded; Rel = relative frame timecode.\nJ reverse · K pause · L forward · repeated J/L: 1×, 2×, 4×, 8×.\nHold K and tap J/L to step one frame.')
        if audio_only:
            self.position_label.setToolTip('Elapsed playback time · J reverse (silent), K pause, L play · repeat J/L for shuttle speed')
        controls.addWidget(self.position_label)
        self.volume_button = tool_button('volume', 'Volume', lambda: None)
        menu = QMenu(self.volume_button)
        volume_box = QWidget()
        volume_layout = QVBoxLayout(volume_box)
        volume_layout.setContentsMargins(12, 8, 12, 8)
        volume_layout.addWidget(QLabel('Volume'))
        self.volume = QSlider(Qt.Horizontal)
        self.volume.setRange(0, 100)
        try:
            level = max(0, min(100, int(settings.value('volume', 70))))
        except (TypeError, ValueError):
            level = 70
        self.volume.setValue(level)
        self.volume.setMinimumWidth(130)
        self.volume.setAccessibleName('Playback volume')
        self.volume.valueChanged.connect(self.change_volume)
        volume_layout.addWidget(self.volume)
        action = QWidgetAction(menu)
        action.setDefaultWidget(volume_box)
        menu.addAction(action)
        self.volume_button.setIcon(icon('mute' if level == 0 else 'volume'))
        self.volume_button.setMenu(menu)
        self.volume_button.setPopupMode(QToolButton.InstantPopup)
        controls.addWidget(self.volume_button)
        self.external_button = tool_button('external', 'Open clip in default player', self.open_external)
        controls.addWidget(self.external_button)
        self.fullscreen_button=tool_button('fullscreen','Fullscreen viewer',self.show_fullscreen)
        self.fullscreen_button.setVisible(not audio_only)
        controls.addWidget(self.fullscreen_button)
        for button in (self.play_button, self.volume_button, self.external_button, self.fullscreen_button):
            button.setFixedSize(26, 28)
            button.setIconSize(QSize(16, 16))
        layout.addLayout(controls)
        self.chips = MonitorChips(self)
        self.chips.setVisible(has_audio)
        layout.addWidget(self.chips)
        self.attach_meter(self.meter)
        self.monitorChanged.connect(self.refresh_meter)
        self.refresh_meter()
        self.overlay.setVisible(playable)
        self.play_button.setEnabled(playable)
        self.shortcut = QShortcut(QKeySequence(Qt.Key_Space), self)
        self.shortcut.setContext(Qt.WidgetWithChildrenShortcut)
        self.shortcut.activated.connect(self.toggle)
        self.shortcut.setEnabled(playable)

    def ensure_player(self):
        if self.player is not None:
            return
        self.player = QMediaPlayer(self)
        # A native Qt audio object avoids a PySide virtual-disconnect callback
        # acquiring Python's GIL on the audio thread while the GUI waits for it
        # to stop at EOF/seek. The UI itself remains entirely Qt Widgets.
        self.audio_engine = QQmlEngine(self)
        component = QQmlComponent(self.audio_engine)
        component.setData(b'import QtMultimedia\nAudioOutput {}', QUrl())
        self.audio = component.create()
        if self.audio is None:
            self.playback_error(QMediaPlayer.ResourceError, 'Audio initialization failed: ' + '\n'.join(e.toString() for e in component.errors()))
            return
        QQmlEngine.setObjectOwnership(self.audio, QQmlEngine.CppOwnership)
        self.audio.setParent(self)
        self.audio.setVolume(self.volume.value() / 100 if self.selected_audio_track == 0 else 0)
        self.player.setAudioOutput(self.audio)
        self.buffer_output=QAudioBufferOutput(self)
        self.buffer_output.audioBufferReceived.connect(self.audio_buffer,Qt.QueuedConnection)
        self.player.setAudioBufferOutput(self.buffer_output)
        if not self.audio_only:
            self.video = QVideoWidget()
            self.video.setAspectRatioMode(Qt.KeepAspectRatio)
            self.video.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Expanding)
            self.video.installEventFilter(self)
            self.video.setCursor(Qt.PointingHandCursor)
            self.stack.addWidget(self.video)
            if self.transform:
                self.managed_view = Preview()
                self.managed_view.setMinimumSize(0, 100)
                self.managed_view.installEventFilter(self)
                self.managed_view.setCursor(Qt.PointingHandCursor)
                self.managed_view.setToolTip(self.transform.description)
                self.stack.addWidget(self.managed_view)
                self.sink = QVideoSink(self)
                self.player.setVideoSink(self.sink)
            else:
                self.sink = self.video.videoSink()
                self.player.setVideoOutput(self.video)
            self.sink.videoFrameChanged.connect(self.video_frame, Qt.QueuedConnection)
        self.player.tracksChanged.connect(self.apply_audio_track, Qt.QueuedConnection)
        self.player.playbackStateChanged.connect(self.state_changed, Qt.QueuedConnection)
        self.player.positionChanged.connect(self.position_changed, Qt.QueuedConnection)
        self.player.durationChanged.connect(self.duration_changed, Qt.QueuedConnection)
        self.player.seekableChanged.connect(self.seekability_changed, Qt.QueuedConnection)
        self.player.errorOccurred.connect(self.playback_error, Qt.QueuedConnection)
        self.player.mediaStatusChanged.connect(self.media_status, Qt.QueuedConnection)
        self.update_audio_routing()
        self.player.setSource(QUrl.fromLocalFile(self.path))

    def audio_buffer(self,buffer):
        if self.disposed:return
        if buffer.isValid() and buffer.frameCount():
            first=not self.audio_frame_seen
            self.audio_frame_seen=True
            if first:QTimer.singleShot(0,self.apply_audio_track)
        panel_meters=[meter for meter in self.channel_meters if isValid(meter) and meter.isVisible()]
        if not (panel_meters or self.monitor_routed() or (self.channel_panel and self.channel_panel.isVisible())):return
        samples=pcm_samples(buffer)
        if samples is None:return
        if self.channel_panel:self.channel_panel.samples(samples)
        labels=self.track_labels();matched=len(labels)==samples.shape[1]
        source_names=short_labels(labels) if matched else None
        mix_out,mix_names=None,()
        if self.monitor_routed():
            try:
                if not matched:raise ValueError('Decoder channel count differs from the source layout. Channel monitoring is unavailable for this track.')
                if self.audition_active():
                    mix=monitor_mix(samples,labels,self.audition_solos,self.audition_mutes,self.audition_mono)
                    mix_out,mix_names=mix,('L','R')
                else:
                    mix=samples@self.monitor_route[0]  # Metered before clipping, so overs stay visible.
                    if self.monitor_mode==('mixdown',):mix_out,mix_names=mix,('Lo','Ro')
                if self.player and self.player.playbackState()==QMediaPlayer.PlayingState and self.speed==1:
                    self.monitor.write(np.ascontiguousarray(np.clip(mix,-1,1),dtype='<f4'),buffer.format().sampleRate(),self.volume.value()/100)
            except Exception as error:
                self.monitor_unavailable(str(error))
                mix_out,mix_names=None,()
        for panel_meter in panel_meters:panel_meter.show_levels(samples,source_names,self.monitored_channels(samples.shape[1]),mix_out,mix_names)

    def audition_active(self):return bool(self.audition_solos or self.audition_mutes or self.audition_mono)

    def track_labels(self):
        tracks=media_tracks(self.data)[1]
        return channel_assignments(tracks[self.selected_audio_track]) if self.selected_audio_track<len(tracks) else []

    def monitor_choices(self):return monitor_options(self.track_labels())

    def reset_monitor(self):
        """Select the default monitor for the current track: a stereo mixdown for assigned surround layouts."""
        self.monitor_mode=self.monitor_choices()[0][1];self.monitor_route=monitor_route(self.track_labels(),self.monitor_mode);self.monitor_note=''

    def set_monitor(self,mode):
        self.monitor.reset();self.monitor_mode=tuple(mode);self.monitor_route=monitor_route(self.track_labels(),self.monitor_mode);self.monitor_note=''
        self.update_audio_routing();self.monitorChanged.emit()

    def monitor_routed(self):return self.audition_active() or self.monitor_route is not None

    def refresh_meter(self):
        """Name the meter's bars for the selected track and monitor before any audio plays."""
        labels=self.track_labels()
        self.meter.set_channels(short_labels(labels),self.monitored_channels(len(labels)),('Lo','Ro') if self.monitor_mode==('mixdown',) else ())

    def attach_meter(self,meter):
        """Send decoded levels to another meter, such as the audio panel's."""
        self.channel_meters.append(meter)

    def monitored_channels(self,count):
        """Which channels of the selected track currently reach the speakers."""
        if self.audition_active():return [i not in self.audition_mutes and (not self.audition_solos or i in self.audition_solos) for i in range(count)]
        if self.monitor_route is None or len(self.monitor_route[0])!=count:return [True]*count
        return [bool(self.monitor_route[0][i].any()) for i in range(count)]

    def monitor_unavailable(self,message):
        self.monitor.reset()
        if self.channel_panel:self.channel_panel.status.setText('Monitoring unavailable: '+message)
        if self.monitor_route is not None and not self.audition_active():
            # Fall back to the player's own output rather than leaving the track silent.
            self.monitor_mode=('native',);self.monitor_route=None;self.monitor_note=message
            self.update_audio_routing();self.monitorChanged.emit()

    def update_audio_routing(self):
        # Routed monitoring runs at 1x forward only. A mixdown hands faster shuttle back to the
        # player's own output; a soloed, paired or single-channel choice stays silent there
        # instead of leaking the other channels.
        routed=self.audition_active() or (self.monitor_route is not None and (self.monitor_mode!=('mixdown',) or self.speed in (0,1)))
        if self.audio:self.audio.setMuted(routed)
        chips=getattr(self,'chips',None)
        if chips is not None:chips.show_audition(self.audition_active())

    def show_audio_channels(self):
        if self.disposed or not media_tracks(self.data)[1]:return
        if self.channel_panel is None:self.channel_panel=ChannelPanel(self)
        self.channel_panel.show();self.channel_panel.raise_();self.channel_panel.activateWindow()

    def show_fullscreen(self):
        if self.fullscreen_dialog or self.disposed:return
        self.shortcut.setEnabled(False)
        dialog=QDialog(self);dialog.setWindowTitle('Frameglass · Viewer');self.fullscreen_dialog=dialog
        layout=QVBoxLayout(dialog);layout.setContentsMargins(0,0,0,0);layout.addWidget(self.screen_area,1)
        row=QHBoxLayout()
        for title,callback in [('Previous frame',lambda:self.step(-1)),('Play / pause',self.toggle),('Next frame',lambda:self.step(1)),('Exit fullscreen',dialog.close)]:
            from PySide6.QtWidgets import QPushButton
            button=QPushButton(title);button.clicked.connect(callback);row.addWidget(button)
        layout.addLayout(row)
        shortcut=QShortcut(QKeySequence(Qt.Key_Space),dialog);shortcut.activated.connect(self.toggle)
        dialog.finished.connect(self.restore_fullscreen);dialog.showFullScreen()

    def restore_fullscreen(self,*args):
        dialog=self.fullscreen_dialog
        if not dialog:return
        self.layout().insertWidget(0,self.screen_area,1);self.screen_area.show();self.fullscreen_dialog=None;dialog.deleteLater()
        self.shortcut.setEnabled(not self.disposed and self.play_button.isEnabled())

    def set_audio_track(self, index):
        self.monitor.reset();self.audition_solos.clear();self.audition_mutes.clear();self.audition_mono=False
        self.selected_audio_track = index
        self.reset_monitor()
        if self.channel_panel:self.channel_panel.load_track()
        self.update_audio_routing()
        self.apply_audio_track()
        self.monitorChanged.emit()

    def apply_audio_track(self):
        if self.disposed or not self.player:
            return
        if self.expects_video and not self.frame_seen:
            return  # Avoid flushing the video decoder before its first frame.
        if self.audio_only and not self.audio_frame_seen:
            return  # Let the audio decoder start before switching its output track.
        if self.audio_only and self.player.activeVideoTrack() != -1:
            self.player.setActiveVideoTrack(-1)
        if self.selected_audio_track < len(self.player.audioTracks()):
            if self.player.activeAudioTrack() != self.selected_audio_track:
                self.player.setActiveAudioTrack(self.selected_audio_track)
            if self.audio:
                self.audio.setVolume(self.volume.value() / 100)

    def toggle(self):
        if self.disposed or self.failed or not self.play_button.isEnabled():
            return
        self.setFocus(Qt.MouseFocusReason)
        self.ensure_player()
        if self.failed:
            return
        if self.speed < 0 or self.player.playbackState() == QMediaPlayer.PlayingState:
            self.pause()
        else:
            self.shuttle(1, accelerate=False)

    def pause(self):
        self.monitor.reset()
        self.pending_step = 0
        self.reverse_timer.stop()
        self.speed = 0
        if self.player:
            self.player.pause()
            self.player.setPlaybackRate(1)
        self.update_transport()

    def shuttle(self, direction, accelerate=True):
        if self.disposed or self.failed or not self.play_button.isEnabled():
            return
        self.ensure_player()
        if self.failed:
            return
        same_direction = self.speed * direction > 0
        speed = min(8, abs(self.speed) * 2) if accelerate and same_direction else 1
        self.reverse_timer.stop()
        self.monitor.reset()
        self.speed = direction * speed
        if direction > 0:
            if self.player.mediaStatus() == QMediaPlayer.EndOfMedia:
                self.player.setPosition(0)
            self.player.setPlaybackRate(speed)
            self.player.play()
        else:
            self.reverse_origin = self.player.position()
            self.player.pause()
            self.player.setPlaybackRate(1)
            self.reverse_clock.start()
            self.reverse_ready = self.player.isSeekable()
            self.reverse_timer.start()
        self.update_transport()

    def reverse_tick(self):
        if self.disposed or not self.player or self.failed:
            self.reverse_timer.stop()
            return
        if not self.player.isSeekable() or (self.expects_video and not self.frame_seen):
            return
        if not self.reverse_ready:
            self.reverse_origin = self.player.position()
            self.reverse_clock.start()
            self.reverse_ready = True
        target = max(0, round(self.reverse_origin + self.speed * self.reverse_clock.elapsed()))
        self.player.setPosition(target)
        if target == 0:
            self.pause()

    def step(self, direction):
        if self.disposed or self.failed or not self.play_button.isEnabled():
            return
        self.ensure_player()
        if self.failed:
            return
        position = self.player.position()
        self.pause()
        if not self.player.isSeekable() or (self.expects_video and not self.frame_seen):
            self.pending_step = direction
            return
        video, _ = media_tracks(self.data)
        fps = (positive_fraction(video[0].get('r_frame_rate')) or positive_fraction(video[0].get('avg_frame_rate'))) if video else None
        if fps:
            current_frame = round(position * fps / 1000)
            count = duration_timecode(self.data)['frames']
            if count:
                current_frame = min(current_frame, count - 1)
            target_frame = max(0, current_frame + direction)
            if count:
                target_frame = min(target_frame, count - 1)
            target = round(target_frame * 1000 / fps)
        else:
            target = position + direction * 40
        self.monitor.reset()
        self.player.setPosition(max(0, min(target, max(0, self.player.duration() - 1))))

    def handle_key(self, event):
        key = event.key()
        if key not in (Qt.Key_J, Qt.Key_K, Qt.Key_L):
            return False
        if event.type() == QEvent.KeyRelease:
            if key == Qt.Key_K and not event.isAutoRepeat():
                self.held_k = False
            return True
        if key == Qt.Key_K:
            self.held_k = True
            self.pause()
        elif self.held_k:
            self.step(-1 if key == Qt.Key_J else 1)
        elif not event.isAutoRepeat():
            self.shuttle(-1 if key == Qt.Key_J else 1)
        return True

    def update_transport(self):
        playing = self.speed != 0 and not self.failed
        name = 'pause' if playing else 'play'
        title = 'Pause clip (K)' if playing else 'Play clip (L)'
        if playing:
            title += f' · {abs(self.speed)}×' + (' reverse (silent)' if self.speed < 0 else '')
        self.play_button.setIcon(icon(name))
        self.play_button.setToolTip(title)
        self.play_button.setAccessibleName(title)
        self.overlay.setIcon(icon(name, '#e5f5ef', 34))
        self.overlay.setToolTip(title)
        self.overlay.setAccessibleName(title)
        self.update_audio_routing()

    def state_changed(self, state):
        if self.disposed:
            return
        if state != QMediaPlayer.PlayingState:self.monitor.reset()
        if state == QMediaPlayer.StoppedState and self.speed >= 0:
            self.speed = 0
        self.update_transport()

    def video_frame(self, frame):
        if not self.disposed and frame.isValid():
            first = not self.frame_seen
            self.frame_seen = True
            if self.transform:
                if self.color_busy:
                    self.pending_color_frame = QVideoFrame(frame)
                else:
                    self.start_color_job(frame)
            else:
                self.stack.setCurrentWidget(self.video)
            if first:
                QTimer.singleShot(0, self.apply_pending_seek)
                QTimer.singleShot(0, self.apply_audio_track)

    def start_color_job(self, frame):
        self.color_busy = True
        job = ColorJob(frame, self.transform, self.color_signals, self.color_cancelled)
        self.color_job = job
        QThreadPool.globalInstance().start(job)

    def color_frame_ready(self, image, error):
        self.color_busy = False
        self.color_job = None
        if self.disposed:
            return
        if error:
            self.playback_error(QMediaPlayer.ResourceError, 'SDR display conversion failed: ' + error + '. You can select Native player rendering in Settings.')
        else:
            from PySide6.QtGui import QPixmap
            self.managed_view.set_image(QPixmap.fromImage(image))
            self.stack.setCurrentWidget(self.managed_view)
            if self.pending_color_frame is not None:
                frame, self.pending_color_frame = self.pending_color_frame, None
                self.start_color_job(frame)

    def seekability_changed(self, seekable):
        if self.disposed:
            return
        ready = seekable and (not self.expects_video or self.frame_seen)
        self.seek.setEnabled(ready and not self.failed)
        if ready:
            QTimer.singleShot(0, self.apply_pending_seek)

    def apply_pending_seek(self):
        if self.disposed or self.failed or not self.player:
            return
        if not self.player.isSeekable() or (self.expects_video and not self.frame_seen):
            return
        self.seek.setEnabled(True)
        if self.pending_seek is not None:
            target, self.pending_seek = self.pending_seek, None
            self.monitor.reset()
            self.player.setPosition(target)
        if self.pending_step:
            direction, self.pending_step = self.pending_step, 0
            self.step(direction)

    def duration_changed(self, ms):
        if self.disposed:
            return
        self.seek.setRange(0, max(0, ms))
        self.position_changed(self.player.position())

    def position_changed(self, ms):
        if self.disposed:
            return
        if not self.seek.isSliderDown():
            self.seek.blockSignals(True)
            self.seek.setValue(ms)
            self.seek.blockSignals(False)
        self.position_label.setText('Time ' + duration(ms / 1000) if self.audio_only else playhead_timecode(self.data, ms))

    def seek_to(self, ms):
        if self.reverse_timer.isActive():
            self.pause()
        if self.player and self.player.isSeekable() and not self.disposed:
            if self.expects_video and not self.frame_seen:
                self.pending_seek = ms
            else:
                self.monitor.reset()
                self.player.setPosition(ms)

    def seek_changed(self, ms):
        # Support keyboard changes as well as scrubbing, without feedback loops.
        if not self.seek.isSliderDown():
            self.seek_to(ms)

    def change_volume(self, level):
        if self.audio:
            self.audio.setVolume(level / 100)
        if self.monitor.sink:self.monitor.sink.setVolume(level/100)
        self.settings.setValue('volume', level)
        self.volume_button.setIcon(icon('mute' if level == 0 else 'volume'))

    def media_status(self, status):
        if self.disposed:
            return
        if self.pending_step and self.player.isSeekable():
            QTimer.singleShot(0, self.apply_pending_seek)
        if status == QMediaPlayer.LoadingMedia:
            self.play_button.setToolTip('Loading clip…')
        elif status == QMediaPlayer.EndOfMedia:
            self.speed = 0
            self.update_transport()
            self.play_button.setToolTip('Replay clip (L)')

    def playback_error(self, error, message):
        if self.disposed or error == QMediaPlayer.NoError:
            return
        self.failed = True
        self.reverse_timer.stop()
        self.speed = 0
        self.player.stop()
        self.stack.setCurrentIndex(0)
        self.overlay.hide()
        self.error_label.setText('Playback unavailable here.\nUse ↗ to open in your default player.')
        self.error_label.setToolTip(message)
        self.error_label.show()
        self.play_button.setEnabled(False)
        self.seek.setEnabled(False)
        self.play_button.setToolTip(message)
        self.external_button.setToolTip('Open clip in default player — ' + message)

    def open_external(self):
        self.pause()
        QDesktopServices.openUrl(QUrl.fromLocalFile(self.path))

    def eventFilter(self, watched, event):
        if watched in (self.preview, self.video, getattr(self, 'managed_view', None)) and event.type() == QEvent.MouseButtonRelease and event.button() == Qt.LeftButton:
            self.toggle()
            return True
        return super().eventFilter(watched, event)

    def dispose(self):
        if self.disposed:
            return
        self.disposed = True
        self.monitor.reset()
        if self.channel_panel:self.channel_panel.close()
        if self.fullscreen_dialog:
            self.fullscreen_dialog.close()
        self.color_cancelled.set()
        self.pending_color_frame = None
        self.reverse_timer.stop()
        self.speed = 0
        if self.player:
            self.player.blockSignals(True)
            if hasattr(self, 'sink'):
                self.sink.blockSignals(True)
            self.player.stop()
            self.player.setSource(QUrl())
            self.player.setVideoOutput(None)
            self.player.setAudioBufferOutput(None)
            self.buffer_output.blockSignals(True)
            self.player.setAudioOutput(None)
            # The buffer output retains a native back-reference to its player.
            # Destroy it first, while QMediaPlayer is still alive.
            self.buffer_output.deleteLater()
            # Dispose in a defined order before the QML context or Python exits.
            self.player.deleteLater()
            if self.audio:
                self.audio.deleteLater()
            if hasattr(self, 'audio_engine'):
                self.audio_engine.deleteLater()
        self.shortcut.setEnabled(False)
