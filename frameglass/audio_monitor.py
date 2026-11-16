"""Audio monitoring parts: monitor chips and channel meters for the player, loudness for the audio card.

Monitoring and metering follow the monitor choice. Measurements never do: they
always read every channel of each complete track.
"""
import math
import time

import numpy as np
from PySide6.QtCore import Qt, QRectF, QSize, QTimer, QThreadPool
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import (QFormLayout, QHBoxLayout, QLabel, QMenu, QPushButton, QSizePolicy,
                              QToolButton, QVBoxLayout, QWidget)

from .core import executable
from .quality import MeasurementJob, MeasurementSignals
from .timecode import media_tracks

SCALE = 24  # Width of the dB scale left of the bars.
SLOT = 18   # Preferred width per bar, including its gap.
FLOOR = -60.0
CHIP_STYLE = ('QToolButton { padding: 4px 10px; border: 1px solid #3a4756; border-radius: 13px; background: transparent; color: #aab6c5; font-size: 12px; font-weight: 500; }'
              ' QToolButton:hover { color: #e4e8ec; border-color: #56657a; }'
              ' QToolButton:checked { color: #14251f; background: #adddce; border-color: #adddce; }'
              ' QToolButton:disabled { color: #aab6c5; }'
              ' QToolButton::menu-indicator { image: none; }')
CHIP_TIPS = {
    'native': 'The player sends this track to your output device unchanged.',
    'mixdown': 'Stereo mixdown: left and right at full level, center and surrounds at −3 dB to their side, LFE left out.',
    'pair': 'Hear only this channel pair, left and right.',
}
STATUS_COLORS = {'Pass': '#a4dacf', 'Mismatch': '#f2aaa9', 'Unknown': '#ecc78e'}


def peak_levels(samples):
    """Sample peak per channel in dBFS, floored for silence."""
    if samples is None or not len(samples):
        return []
    peaks = np.max(np.abs(np.asarray(samples, dtype=np.float64)), axis=0)
    return [20 * math.log10(max(1e-9, float(peak))) for peak in peaks]


class ChannelMeter(QWidget):
    """Vertical sample-peak bars for each channel of the selected track, then the monitor mix.

    Channels that do not feed the monitor are drawn dim. These are decoded sample
    peaks before the volume control, not true-peak or loudness values.
    """
    def __init__(self, parent=None, stretch=False):
        super().__init__(parent)
        self.stretch = stretch  # Audio-only media has no picture, so the meter may take the room.
        self.setMinimumHeight(100)
        self.setSizePolicy(QSizePolicy.Expanding if stretch else QSizePolicy.Preferred, QSizePolicy.Expanding)
        self.bars = []  # [name, level dB, held peak dB, feeds the monitor]
        self.mix_count = 0
        self.updated = 0.0
        self.timer = QTimer(self)
        self.timer.setInterval(60)
        self.timer.timeout.connect(self.decay)
        self.setAccessibleName('Audio channel meters')
        self.setToolTip('Sample peaks in dBFS for each channel of the selected track, before the volume control.\n'
                        'Dim bars are not part of the monitor. Lo and Ro show the stereo mixdown you hear.\n'
                        'This is not a true-peak or loudness measurement.')
        self.fit()

    def slots(self):
        return max(1, len(self.bars)) + (.7 if self.mix_count else 0)

    def sizeHint(self):
        return QSize(round(SCALE + self.slots() * (40 if self.stretch else SLOT) + 4), 150)

    def fit(self):
        """Keep the meter as narrow as its bars allow, so the picture keeps the width."""
        self.setMinimumWidth(round(SCALE + self.slots() * 10 + 4))
        self.setMaximumWidth(16777215 if self.stretch else self.sizeHint().width())
        self.updateGeometry()

    def set_channels(self, names, monitored, mix_names=()):
        """Show empty, named bars before any audio has played."""
        self.bars = [[name, FLOOR, FLOOR, bool(flag)] for name, flag in zip(names, monitored)]
        self.bars += [[name, FLOOR, FLOOR, True] for name in mix_names]
        self.mix_count = len(mix_names)
        self.fit()
        self.update()

    def show_levels(self, source, names, monitored, mix=None, mix_names=()):
        levels = peak_levels(source)
        names = list(names) if names and len(names) == len(levels) else [str(i + 1) for i in range(len(levels))]
        flags = list(monitored) if len(monitored) == len(levels) else [True] * len(levels)
        mix_levels = peak_levels(mix) if mix is not None else []
        mix_names = list(mix_names) if len(mix_names) == len(mix_levels) else [str(i + 1) for i in range(len(mix_levels))]
        labels, levels, flags = names + mix_names, levels + mix_levels, flags + [True] * len(mix_levels)
        same = [bar[0] for bar in self.bars] == labels
        self.bars = [[label, level, max(level, self.bars[i][2] if same else level), flag]
                     for i, (label, level, flag) in enumerate(zip(labels, levels, flags))]
        self.mix_count = len(mix_levels)
        if not same:
            self.fit()
        self.updated = time.monotonic()
        if not self.timer.isActive():
            self.timer.start()
        self.update()

    def decay(self):
        """Let held peaks fall slowly, and let the bars fall once audio stops arriving."""
        stalled = time.monotonic() - self.updated > .2
        for bar in self.bars:
            if stalled:
                bar[1] = max(FLOOR, bar[1] - 5)
            bar[2] = max(bar[1], bar[2] - (2 if stalled else .7))
        if stalled and all(bar[2] <= FLOOR for bar in self.bars):
            self.timer.stop()
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        left, top, bottom = SCALE, 6, 18
        height = max(10, self.height() - top - bottom)
        width = max(10, self.width() - left - 4)
        gap = .7 if self.mix_count else 0  # Separates the source channels from the mix.
        slot = min(40.0, width / self.slots())
        font = self.font()
        font.setPixelSize(10 if slot >= 15 else 8)
        painter.setFont(font)
        for mark in (0, -12, -24, -48):
            y = top + (mark / FLOOR) * height
            painter.setPen(QPen(QColor('#2e3641'), 1))
            painter.drawLine(left, round(y), left + width, round(y))
            painter.setPen(QColor('#768290'))
            painter.drawText(QRectF(0, y - 7, left - 5, 14), Qt.AlignRight | Qt.AlignVCenter, str(mark))
        if not self.bars:
            return
        bar_width = max(4.0, min(16.0, slot * .5))
        source_count = len(self.bars) - self.mix_count
        for index, (name, level, held, monitored) in enumerate(self.bars):
            x = left + (index + (gap if index >= source_count else 0)) * slot + (slot - bar_width) / 2
            painter.setPen(Qt.NoPen)
            painter.setBrush(QColor('#262e38'))
            painter.drawRoundedRect(QRectF(x, top, bar_width, height), 2, 2)
            color = QColor('#4f5b69' if not monitored else '#f2aaa9' if held >= -.1 else '#ecc78e' if level > -6 else '#a1d6c5')
            filled = max(0.0, min(1.0, (level - FLOOR) / -FLOOR)) * height
            if filled > 0:
                painter.setBrush(color)
                painter.drawRoundedRect(QRectF(x, top + height - filled, bar_width, filled), 2, 2)
            if held > FLOOR:
                y = top + height - max(0.0, min(1.0, (held - FLOOR) / -FLOOR)) * height
                painter.setBrush(QColor('#f2aaa9' if held >= -.1 and monitored else '#dfe6ee' if monitored else '#768290'))
                painter.drawRect(QRectF(x, y, bar_width, 1.5))
            painter.setPen(QColor('#aab6c5' if monitored else '#6b7684'))
            painter.drawText(QRectF(x + bar_width / 2 - slot / 2 - 2, top + height + 3, slot + 4, 14), Qt.AlignCenter, name)


class MonitorChips(QWidget):
    """The monitor choices of the selected track as one row of chips under the transport."""
    def __init__(self, player, parent=None):
        super().__init__(parent)
        self.player = player
        self.options = None
        self.buttons = []  # (button, mode); mode is None for the menu of remaining choices
        self.audition = None
        self.menu_modes = []
        # Never let a long chip row force the player wider than its picture needs.
        self.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        self.row = QHBoxLayout(self)
        self.row.setContentsMargins(4, 2, 4, 6)
        self.row.setSpacing(5)
        player.monitorChanged.connect(self.sync)
        self.sync()

    def build(self, options):
        while self.row.count():
            item = self.row.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self.options, self.buttons, self.audition = options, [], None
        # Chips: the default, the front pair and the center channel. Everything else shares one menu.
        labels = self.player.track_labels()
        shown = [options[0][1]] if options else []
        shown += [mode for title, mode in options if mode[0] == 'pair'][:1]
        if 'FC' in labels and len(labels) > 1:
            shown.append(('channel', labels.index('FC')))
        in_menu = [(title, mode) for title, mode in options if mode not in shown]
        self.menu_modes = [mode for title, mode in in_menu]
        for title, mode in options:
            if mode in self.menu_modes:
                continue
            button = QToolButton()
            button.setText(title.replace(' only', ''))
            button.setCheckable(True)
            button.setStyleSheet(CHIP_STYLE)
            button.setToolTip(CHIP_TIPS.get(mode[0], ''))
            button.setAccessibleName('Monitor ' + title)
            button.setEnabled(len(options) > 1)
            button.clicked.connect(lambda checked=False, chosen=mode: self.player.set_monitor(chosen))
            self.row.addWidget(button)
            self.buttons.append((button, mode))
        if in_menu:
            button = QToolButton()
            button.setCheckable(True)
            button.setStyleSheet(CHIP_STYLE)
            button.setToolTip('Other pairs, single channels in both speakers, or the track handed to the player unchanged.')
            button.setAccessibleName('More monitor choices')
            menu = QMenu(button)
            for title, mode in in_menu:
                menu.addAction(title, lambda checked=False, chosen=mode: self.player.set_monitor(chosen))
            # Dismissing the menu must not leave the chip looking selected.
            menu.aboutToHide.connect(lambda: QTimer.singleShot(0, self.sync))
            button.setMenu(menu)
            button.setPopupMode(QToolButton.InstantPopup)
            self.row.addWidget(button)
            self.buttons.append((button, None))
        if len(labels) > 0:
            audition = QToolButton()
            audition.setText('Solo / Mute / Phase…')
            audition.setStyleSheet(CHIP_STYLE)
            audition.setCheckable(True)
            audition.setToolTip('Per-channel solo and mute, reported channel assignments and phase correlation.')
            audition.clicked.connect(self.open_audition)
            self.row.addWidget(audition)
            self.audition = audition
        self.row.addStretch()

    def open_audition(self, *args):
        self.player.show_audio_channels()
        self.show_audition(self.player.audition_active())

    def show_audition(self, active):
        """Light the Solo / Mute chip while a solo, mute or mono check shapes what is heard."""
        if self.audition is not None:
            self.audition.setChecked(bool(active))

    def sync(self):
        options = self.player.monitor_choices()
        if options != self.options:
            self.build(options)
        current = self.player.monitor_mode
        titles = {mode: title for title, mode in options}
        for button, mode in self.buttons:
            if mode is None:
                chosen = current in self.menu_modes
                button.setChecked(chosen)
                idle = 'Single channel' if all(item[0] == 'channel' for item in self.menu_modes) else 'More'
                button.setText((titles.get(current, idle) if chosen else idle) + ' ▾')
            else:
                button.setChecked(mode == current)
        self.show_audition(self.player.audition_active())
        note = self.player.monitor_note
        self.setToolTip('Using the player default: ' + note if note else '')


class LoudnessBox(QWidget):
    """Whole-track loudness of the selected audio track. One request measures every track."""
    FIELDS = (('Integrated', 'integrated_lufs', 'LUFS', ('loudness_min', 'loudness_max')),
              ('True peak', 'true_peak_dbfs', 'dBTP', ('true_peak_max',)),
              ('Range', 'loudness_range_lu', 'LU', ()))

    def __init__(self, player, path, data, settings, delivery=None, horizontal=False):
        super().__init__()
        self.player, self.path, self.data, self.settings, self.delivery = player, path, data, settings, delivery
        self.tracks = media_tracks(data)[1]
        self.job = None
        self.token = 0
        self.values = {}
        self.measure = QPushButton('Measure audio')
        self.measure.setStyleSheet('QPushButton { padding: 4px 11px; }')
        self.measure.setToolTip('Reads every channel of each complete audio track (EBU R 128). The monitor choice does not change the result.')
        self.measure.clicked.connect(self.toggle_measurement)
        self.measure.setEnabled(bool(self.tracks))
        self.status = QLabel()
        self.status.setObjectName('muted')
        self.status.setWordWrap(True)
        self.status.setTextFormat(Qt.PlainText)
        self.status.hide()
        if horizontal:
            row = QHBoxLayout(self)
            row.setContentsMargins(0, 0, 0, 0)
            row.setSpacing(8)
            for title, key, unit, rules in self.FIELDS:
                row.addWidget(self.caption(title))
                row.addWidget(self.value(key))
                row.addSpacing(14)
            row.addWidget(self.status, 1)
            row.addWidget(self.measure)
        else:
            # The Measure button is placed by the caller, on the Loudness heading line.
            box = QVBoxLayout(self)
            box.setContentsMargins(0, 0, 0, 0)
            box.setSpacing(5)
            form = QFormLayout()
            form.setContentsMargins(0, 0, 0, 0)
            form.setVerticalSpacing(5)
            form.setHorizontalSpacing(14)
            for title, key, unit, rules in self.FIELDS:
                caption = self.caption(title)
                caption.setMinimumWidth(90)
                form.addRow(caption, self.value(key))
            box.addLayout(form)
            box.addWidget(self.status)
            self.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Minimum)
            self.setMinimumHeight(form.sizeHint().height())
        self.signals = MeasurementSignals(self)
        self.signals.ready.connect(self.measured, Qt.QueuedConnection)
        self.signals.progress.connect(self.progress, Qt.QueuedConnection)
        player.monitorChanged.connect(self.refresh_results)
        self.refresh_results()

    def caption(self, title):
        label = QLabel(title)
        label.setObjectName('muted')
        return label

    def value(self, key):
        label = QLabel('Not measured')
        label.setTextFormat(Qt.PlainText)
        label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.values[key] = label
        return label

    def say(self, text, detail=''):
        self.status.setText(text)
        self.status.setToolTip(detail)
        self.status.setVisible(bool(text))

    def toggle_measurement(self):
        if self.job:
            self.cancel()
            self.say('Measurement cancelled.')
            return
        if not self.tracks:
            return
        self.token += 1
        ffmpeg = executable('ffmpeg', self.settings.value('ffmpeg', ''))
        self.job = MeasurementJob(self.path, len(self.tracks), ffmpeg, self.signals, self.token)
        self.measure.setText('Cancel')
        self.say('Measuring…')
        QThreadPool.globalInstance().start(self.job)

    def progress(self, message, token):
        if token == self.token and self.job:
            self.say(message)

    def measured(self, result, error, token):
        if token != self.token or not self.job:
            return
        self.job = None
        self.measure.setText('Measure audio')
        if error or not result:
            self.say('Measurement failed. Hover for details.', error or 'No result was reported.')
            return
        self.data.setdefault('audio_measurements', {}).update(result)
        if self.delivery is not None:
            self.delivery.refresh()
        self.say('')
        self.refresh_results()

    def refresh_results(self, *args):
        """Show the selected track's numbers, colored by any matching delivery rules."""
        index = self.player.selected_audio_track + 1
        measured = (self.data.get('audio_measurements') or {}).get(str(index)) or {}
        checks = self.delivery.checks if self.delivery is not None else []
        for title, key, unit, rules in self.FIELDS:
            cell, value = self.values[key], measured.get(key)
            cell.setText(f'{value} {unit}' if value not in (None, '') else 'Not measured')
            states = [row['status'] for row in checks if row['track'] == f'Audio {index}' and row['key'] in rules]
            state = 'Mismatch' if 'Mismatch' in states else 'Unknown' if 'Unknown' in states else 'Pass' if states else ''
            cell.setStyleSheet('color: ' + STATUS_COLORS[state] + ';' if state else '' if value not in (None, '') else 'color: #949eac;')
            cell.setToolTip(('Delivery preset: ' + state + '\n' if state else '') + 'Measured over the whole track with every channel.')

    def cancel(self):
        if self.job:
            self.job.cancelled.set()
            self.job = None
            self.token += 1
            self.measure.setText('Measure audio')
