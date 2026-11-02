"""Frameglass visual language and small, reusable interface components."""
from PySide6.QtCore import Qt, QSize, QRectF, QPointF
from PySide6.QtGui import QColor, QIcon, QPainter, QPen, QPixmap, QPolygonF
from PySide6.QtWidgets import (QLabel, QToolButton, QFrame, QVBoxLayout, QHBoxLayout,
                              QWidget, QSizePolicy)

STYLE = '''
* { font-family: "Helvetica Neue"; font-size: 13px; color: #e4e8ec; }
QMainWindow, QDialog, QWidget#canvas { background: #14171c; }
QWidget { background: transparent; }
QDialog { background-color: #14171c; }
QWidget#sidebar { background: #101318; border-right: 1px solid #292e36; }
QLabel { background: transparent; }
QLabel#brand { font-size: 20px; font-weight: 700; letter-spacing: -0.5px; }
QLabel#eyebrow { color: #8c96a5; font-size: 10px; font-weight: 650; letter-spacing: 1.8px; }
QLabel#title { font-size: 22px; font-weight: 650; letter-spacing: -0.7px; }
QLabel#muted { color: #949eac; font-size: 12px; }
QLabel#metric { font-size: 20px; font-weight: 600; letter-spacing: -0.7px; }
QLabel#timecodeValue { font-family: Menlo; font-size: 12px; font-weight: 400; color: #c9d3de; }
QFrame#timecodeStrip, QFrame#containerSummary { background: #191e25; border: 1px solid #2a323d; border-radius: 7px; }
QScrollArea#stillViewport { background: #0d1117; border: 1px solid #2e3641; border-radius: 9px; }
QLabel#sectionTitle { font-size: 15px; font-weight: 600; }
QLabel#badge { color: #a4dacf; background: #203731; border-radius: 5px; padding: 5px 9px; font-size: 11px; }
QLabel#badge[tone="warning"] { color: #ecc78e; background: #3c3121; }
QLabel#badge[tone="neutral"] { color: #a7b3c3; background: #272e39; }
QLabel#badge[tone="error"] { color: #f2aaa9; background: #3d282c; }
QPushButton, QToolButton { background: #242a33; border: 1px solid #353e49; border-radius: 7px; padding: 8px 13px; font-weight: 500; }
QPushButton:hover, QToolButton:hover { background: #303944; border-color: #546476; }
QPushButton:pressed, QToolButton:pressed { background: #1b242a; }
QPushButton:focus, QToolButton:focus, QLineEdit:focus { border-color: #8bc6b9; }
QPushButton:disabled { color: #75808e; background: #20252c; border-color: #2b323b; }
QPushButton#primary { background: #adddce; color: #14251f; border-color: #adddce; font-weight: 600; }
QPushButton#primary:hover { background: #c5eadd; }
QToolButton#iconButton { background: transparent; border: 1px solid transparent; padding: 6px; }
QToolButton#iconButton:hover { background: #252d37; }
QToolButton#iconButton::menu-indicator { image: none; }
QPushButton#quiet { background: transparent; border-color: transparent; color: #aab6c5; padding: 7px 9px; }
QPushButton#quiet:hover { background: #252d37; color: #e4e8ec; }
QComboBox { background: #242d37; border: 1px solid #3a4756; border-radius: 6px; padding: 5px 9px; font-size: 11px; }
QComboBox::drop-down { border: none; width: 17px; }
QComboBox QAbstractItemView { background: #20262f; selection-background-color: #34473f; padding: 5px; }
QLineEdit { background: #1b2028; border: 1px solid #2d3540; border-radius: 7px; padding: 8px 10px; selection-background-color: #355b52; }
QLineEdit#folderPath { background: transparent; border-color: transparent; padding: 6px 0; color: #94a0af; font-size: 11px; }
QTableWidget { background: #171c23; alternate-background-color: #1c2129; border: 1px solid #2e3641; gridline-color: #2a323d; selection-background-color: #293e3b; selection-color: #c2e9de; }
QTableWidget::item { padding: 6px; }
QHeaderView::section { background: #20262f; color: #a7b3c3; border: none; border-bottom: 1px solid #353e49; padding: 7px; }
QCheckBox { spacing: 7px; }
QTreeView { background: transparent; border: none; outline: none; padding: 2px; }
QTreeView::item { padding: 6px 3px; border-radius: 5px; }
QTreeView::item:hover { background: #20262f; }
QTreeView::item:selected { background: #293e3b; color: #c2e9de; }
QTreeView::branch { background: transparent; }
QTabWidget::pane { border: none; }
QTabBar::tab { color: #8f9baa; padding: 9px 15px; border-bottom: 2px solid transparent; margin-right: 10px; }
QTabBar::tab:selected { color: #b6e2d4; border-bottom-color: #a1d6c5; }
QTabBar::tab:hover { color: #e4e8ec; }
QScrollArea { border: none; background: transparent; }
QScrollArea > QWidget > QWidget { background: transparent; }
QDialog { background-color: #14171c; }
QTextEdit { background: #191e25; border: 1px solid #2a323d; border-radius: 9px; padding: 16px; font-family: Menlo; font-size: 12px; selection-background-color: #355b52; }
QTextEdit#diagnosticText { font-family: "Helvetica Neue"; font-size: 13px; }
QFrame#card { background: #1c2129; border: 1px solid #2e3641; border-radius: 11px; }
QFrame#metricCard { background: #1c2129; border: 1px solid #2e3641; border-radius: 9px; }
QFrame#dropZone { background: #191f26; border: 1px dashed #43535e; border-radius: 15px; }
QFrame#dropOverlay { background: #1c302c; border: 2px dashed #a1d6c5; border-radius: 15px; }
QLabel#preview { background: #0d1117; border: 1px solid #2e3641; border-radius: 9px; color: #91a3af; }
QFrame#clipPlayer { background: #0d1117; border: 1px solid #2e3641; border-radius: 9px; }
QFrame#clipPlayer QToolButton#iconButton { padding: 0; }
QToolButton#playOverlay { background: rgba(15, 25, 29, 200); border: 1px solid #94b5a9; border-radius: 29px; padding: 0; }
QToolButton#playOverlay:hover { background: #29473f; }
QLabel#playbackClock { font-family: Menlo; font-size: 10px; color: #aab6c5; }
QLabel#playbackError { background: #252b34; color: #e4e8ec; padding: 8px; font-size: 11px; }
QSlider::groove:horizontal { background: #35414b; height: 3px; border-radius: 1px; }
QSlider::sub-page:horizontal { background: #a1d6c5; border-radius: 1px; }
QSlider::handle:horizontal { background: #c2e5d9; border: none; border-radius: 4px; width: 8px; margin: -3px 0; }
QSlider:disabled::sub-page:horizontal { background: #35414b; }
QSplitter::handle { background: #14171c; width: 1px; }
QProgressBar { border: none; background: #2d3742; border-radius: 3px; max-height: 6px; min-height: 6px; }
QProgressBar::chunk { background: #a1d6c5; border-radius: 3px; }
QScrollBar:vertical { background: transparent; width: 7px; margin: 0; }
QScrollBar::handle:vertical { background: #3e4957; min-height: 28px; border-radius: 3px; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background: transparent; }
QScrollBar:horizontal { height: 7px; background: transparent; }
QScrollBar::handle:horizontal { background: #3e4957; border-radius: 3px; min-width: 28px; }
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal { width: 0; }
QMenu { background: #20262f; border: 1px solid #3a4553; border-radius: 8px; padding: 6px; }
QMenu::item { padding: 9px 20px; border-radius: 4px; }
QMenu::item:selected { background: #34473f; }
QToolTip { background: #303b47; color: #edf3f5; padding: 6px; border: none; }
'''


def icon(name, color='#9baabb', size=20):
    """Consistent outlined icons, painted at high DPI without platform glyphs."""
    pix = QPixmap(size * 2, size * 2)
    pix.fill(Qt.transparent)
    p = QPainter(pix)
    p.setRenderHint(QPainter.Antialiasing)
    p.scale(size / 12, size / 12)
    p.setPen(QPen(QColor(color), 1.5, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
    if name == 'folder':
        p.drawLine(3, 7, 3, 19); p.drawLine(3, 19, 21, 19); p.drawLine(21, 19, 21, 7)
        p.drawLine(21, 7, 11, 7); p.drawLine(11, 7, 9, 4); p.drawLine(9, 4, 3, 4); p.drawLine(3, 4, 3, 7)
    elif name == 'up':
        p.drawLine(12, 20, 12, 4); p.drawLine(12, 4, 6, 10); p.drawLine(12, 4, 18, 10)
    elif name == 'search':
        p.drawEllipse(QRectF(4, 3, 12, 12)); p.drawLine(15, 14, 21, 20)
    elif name == 'settings':
        from math import cos, sin, pi
        p.drawEllipse(QRectF(5, 5, 14, 14)); p.drawEllipse(QRectF(9, 9, 6, 6))
        for i in range(8):
            a = i * pi / 4
            p.drawLine(int(12 + 7*cos(a)), int(12 + 7*sin(a)), int(12 + 10*cos(a)), int(12 + 10*sin(a)))
    elif name == 'video':
        p.drawRoundedRect(QRectF(3, 5, 14, 14), 2, 2)
        p.drawLine(17, 9, 22, 6); p.drawLine(22, 6, 22, 18); p.drawLine(22, 18, 17, 15)
    elif name == 'audio':
        for x, y in [(3, 9), (7, 5), (11, 2), (15, 7), (19, 4), (23, 9)]:
            p.drawLine(x, y, x, 24-y)
    elif name == 'check':
        p.drawEllipse(QRectF(3, 3, 18, 18)); p.drawLine(7, 12, 11, 16); p.drawLine(11, 16, 17, 9)
    elif name in ('previous', 'next'):
        x, other = (15, 8) if name == 'previous' else (9, 16)
        p.drawLine(x, 5, other, 12); p.drawLine(other, 12, x, 19)
    elif name == 'play':
        p.drawPolygon(QPolygonF([QPointF(8, 4), QPointF(20, 12), QPointF(8, 20)]))
    elif name == 'pause':
        p.drawLine(8, 5, 8, 19); p.drawLine(16, 5, 16, 19)
    elif name in ('volume', 'mute'):
        p.drawPolygon(QPolygonF([QPointF(3, 9), QPointF(7, 9), QPointF(12, 5), QPointF(12, 19), QPointF(7, 15), QPointF(3, 15)]))
        if name == 'volume':
            p.drawArc(QRectF(10, 5, 11, 14), -65 * 16, 130 * 16)
        else:
            p.drawLine(17, 9, 22, 15); p.drawLine(17, 15, 22, 9)
    elif name == 'external':
        p.drawLine(4, 4, 4, 20); p.drawLine(4, 20, 20, 20); p.drawLine(20, 20, 20, 14)
        p.drawLine(4, 4, 10, 4); p.drawLine(12, 12, 21, 3)
        p.drawLine(21, 3, 14, 3); p.drawLine(21, 3, 21, 10)
    elif name == 'arrow':
        p.drawLine(4, 12, 20, 12); p.drawLine(20, 12, 14, 6); p.drawLine(20, 12, 14, 18)
    else:
        p.drawRoundedRect(QRectF(3, 3, 18, 18), 3, 3)
        p.drawRoundedRect(QRectF(7, 7, 10, 10), 1, 1)
        p.drawLine(12, 1, 12, 5); p.drawLine(12, 19, 12, 23)
    p.end()
    pix.setDevicePixelRatio(2)
    return QIcon(pix)


def tool_button(name, tooltip, slot):
    button = QToolButton()
    button.setObjectName('iconButton')
    button.setIcon(icon(name))
    button.setIconSize(QSize(19, 19))
    button.setFixedSize(34, 34)
    button.setToolTip(tooltip)
    button.setAccessibleName(tooltip)
    button.clicked.connect(slot)
    return button


class ElidedLabel(QLabel):
    """Single-line file/path labels retain their full value for copy and tooltip."""
    def __init__(self, text='', name='muted', parent=None):
        super().__init__(parent)
        self.full_text = text
        self.setObjectName(name)
        self.setTextFormat(Qt.PlainText)
        self.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self.setMinimumWidth(0)
        self.setMinimumHeight(22 if name != 'title' else 34)
        self.setText(text)

    def setText(self, text):
        self.full_text = str(text)
        self.setToolTip(self.full_text)
        super().setText(self.fontMetrics().elidedText(self.full_text, Qt.ElideMiddle, max(40, self.width())))

    def resizeEvent(self, event):
        self.setText(self.full_text)
        super().resizeEvent(event)


class Preview(QLabel):
    def __init__(self):
        super().__init__('Preview')
        self.setObjectName('preview')
        self.setAlignment(Qt.AlignCenter)
        self.setMinimumSize(200, 220)
        self.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Expanding)
        self.source = None

    def set_image(self, pixmap):
        self.source = pixmap
        self.setPixmap(pixmap.scaled(self.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation))

    def resizeEvent(self, event):
        if self.source is not None:
            self.setPixmap(self.source.scaled(self.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation))
        super().resizeEvent(event)

    def placeholder(self, kind='video'):
        self.source = None
        self.setPixmap(icon(kind, '#728b96', 48).pixmap(48, 48))


class MediaHero(QWidget):
    """Large preview on the left, facts and compact source TC on the right."""
    def __init__(self, preview, details):
        super().__init__()
        self.setObjectName('mediaHero')
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(14)
        details.setMinimumWidth(370)
        row.addWidget(preview, 1)
        row.addWidget(details, 1)
        self.setFixedHeight(258)

    def resizeEvent(self, event):
        # Preserve a generous picture at wide sizes without making the hero tall.
        height = max(244, min(270, round((self.width() - 14) / 2 * 9 / 16)))
        if self.height() != height:
            self.setFixedHeight(height)
        super().resizeEvent(event)


class Section(QFrame):
    def __init__(self, title, subtitle='', symbol=None):
        super().__init__()
        self.setObjectName('card')
        self.content = QVBoxLayout(self)
        self.content.setContentsMargins(18, 17, 18, 17)
        self.content.setSpacing(12)
        heading = QHBoxLayout()
        if symbol:
            mark = QLabel()
            mark.setPixmap(icon(symbol, '#a1d6c5').pixmap(20, 20))
            heading.addWidget(mark)
        name = QLabel(title)
        name.setTextFormat(Qt.PlainText)
        name.setObjectName('sectionTitle')
        heading.addWidget(name)
        heading.addStretch()
        if subtitle:
            tag = QLabel(subtitle)
            tag.setTextFormat(Qt.PlainText)
            tag.setObjectName('muted')
            heading.addWidget(tag)
        self.content.addLayout(heading)


class Disclosure(QFrame):
    def __init__(self, title, content, expanded=False):
        super().__init__()
        self.setObjectName('card')
        layout = QVBoxLayout(self)
        layout.setContentsMargins(7, 7, 7, 7)
        self.toggle = QToolButton()
        self.toggle.setObjectName('iconButton')
        self.toggle.setText(title)
        self.toggle.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        self.toggle.setCheckable(True)
        self.toggle.setChecked(expanded)
        self.toggle.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.toggle.setArrowType(Qt.DownArrow if expanded else Qt.RightArrow)
        self.toggle.toggled.connect(lambda checked: self.change(checked))
        layout.addWidget(self.toggle)
        self.panel = content
        self.panel.setVisible(expanded)
        layout.addWidget(self.panel)

    def change(self, checked):
        self.panel.setVisible(checked)
        self.toggle.setArrowType(Qt.DownArrow if checked else Qt.RightArrow)


class AudioTrackBrowser(QWidget):
    """Compact previous/next navigation through audio streams."""
    from PySide6.QtCore import Signal
    currentIndexChanged = Signal(int)

    def __init__(self, count):
        super().__init__()
        self._count = count
        self._index = 0
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(2)
        self.previous = tool_button('previous', 'Previous audio track', lambda: self.setCurrentIndex(self._index - 1))
        self.next = tool_button('next', 'Next audio track', lambda: self.setCurrentIndex(self._index + 1))
        self.indicator = QLabel()
        self.indicator.setObjectName('muted')
        self.indicator.setAlignment(Qt.AlignCenter)
        self.indicator.setMinimumWidth(70)
        row.addWidget(self.previous)
        row.addWidget(self.indicator)
        row.addWidget(self.next)
        self.refresh()

    def count(self):
        return self._count

    def currentIndex(self):
        return self._index

    def setCurrentIndex(self, index):
        if 0 <= index < self._count and index != self._index:
            self._index = index
            self.refresh()
            self.currentIndexChanged.emit(index)

    def refresh(self):
        self.indicator.setText(f'Track {self._index + 1} / {self._count}')
        self.setAccessibleName(f'Audio track {self._index + 1} of {self._count}')
        self.previous.setEnabled(self._index > 0)
        self.next.setEnabled(self._index < self._count - 1)
