import json
import sys
from pathlib import Path
from threading import Event

from PySide6.QtCore import Qt, QDir, QProcess, QSettings, QTimer, QUrl, QSize, QEvent, QThreadPool, Signal
from PySide6.QtGui import QAction, QDesktopServices, QFont, QKeySequence, QPixmap, QImage
from PySide6.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QPushButton, QToolButton, QMenu, QFileDialog, QFileSystemModel, QTreeView,
    QSplitter, QTabWidget, QTextEdit, QScrollArea, QFormLayout, QFrame, QLineEdit,
    QProgressBar, QMessageBox, QDialog, QDialogButtonBox, QGridLayout, QSizePolicy, QComboBox, QStackedWidget)
from .core import executable, probe_args, scan_args, duration, size, rate, findings, decoder_advice

from .stills import STILL_EXTENSIONS, StillSignals, StillJob, ImageViewer
from .tool_manager import ToolController
from .workspace import QualityWorkspace
from .inspection_tools import AudioAnalysisDialog
from .audio import audio_overview
from .delivery import DeliveryBar
from .player import ClipPlayer
from .color import scan_mode, display_transform, display_description, ThumbnailJob, ThumbnailSignals
from .timecode import clip_timecode, media_tracks, duration_timecode
from .design import STYLE, icon, tool_button, ElidedLabel, Preview, Section, Disclosure, MediaHero, AudioTrackBrowser


def label(text, name=None):
    widget = QLabel(str(text))
    if name:
        widget.setObjectName(name)
    widget.setWordWrap(name != 'eyebrow')
    widget.setTextInteractionFlags(Qt.TextSelectableByMouse)
    widget.setTextFormat(Qt.PlainText)
    return widget


def clear(layout):
    while layout.count():
        item = layout.takeAt(0)
        if item.widget():
            item.widget().deleteLater()
        elif item.layout():
            clear(item.layout())


class Window(QMainWindow):
    def __init__(self, settings=None):
        super().__init__()
        self.settings = settings if settings is not None else QSettings('Frameglass', 'Frameglass')
        self.path = None
        self.data = None
        self.probe = None
        self.scan = None
        self.scan_log = ''
        self.thumbnail = None
        self.thumbnail_token = 0
        self.thumbnail_cancelled = Event()
        self.still_signals = StillSignals(self)
        self.still_signals.ready.connect(self.still_ready, Qt.QueuedConnection)
        self.thumbnail_signals = ThumbnailSignals(self)
        self.thumbnail_signals.ready.connect(self.thumbnail_ready, Qt.QueuedConnection)
        self.preview = None
        self.playback = None
        self.quality_workspace = None
        self.tool_controller = None
        self.analysis_dialog = None
        self.analysis_summary = None
        self.setAttribute(Qt.WA_DeleteOnClose, True)
        self.setWindowTitle('Frameglass — Media Inspector')
        self.resize(1280, 860)
        self.setMinimumSize(960, 660)
        self.setAcceptDrops(True)
        self.setWindowIcon(icon('brand', '#a1d6c5', 128))
        root = QWidget()
        root.setObjectName('canvas')
        self.setCentralWidget(root)
        layout = QVBoxLayout(root)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        self.splitter = QSplitter()
        self.splitter.setChildrenCollapsible(False)
        layout.addWidget(self.splitter, 1)
        browser = QWidget()
        browser.setObjectName('sidebar')
        browser.setMinimumWidth(228)
        browser.setMaximumWidth(400)
        side = QVBoxLayout(browser)
        side.setContentsMargins(20, 26, 18, 18)
        side.setSpacing(12)
        brand = QHBoxLayout()
        mark = QLabel()
        mark.setPixmap(icon('brand', '#adddce', 28).pixmap(28, 28))
        brand.addWidget(mark)
        brand.addWidget(label('Frameglass', 'brand'))
        brand.addStretch()
        side.addLayout(brand)
        side.addSpacing(22)
        nav = QHBoxLayout()
        nav.addWidget(label('FILES', 'eyebrow'))
        nav.addStretch()
        nav.addWidget(tool_button('up', 'Parent folder', lambda: self.set_folder(str(Path(self.folder.text()).parent))))
        nav.addWidget(tool_button('folder', 'Choose folder', self.open_folder))
        side.addLayout(nav)
        self.folder_name = ElidedLabel('Home', 'sectionTitle')
        side.addWidget(self.folder_name)
        self.folder = QLineEdit()
        self.folder.setObjectName('folderPath')
        self.folder.setToolTip('Edit folder path and press Return')
        self.folder.returnPressed.connect(lambda: self.set_folder(self.folder.text()))
        side.addWidget(self.folder)
        self.search = QLineEdit()
        self.search.setPlaceholderText('Filter files…')
        self.search.setClearButtonEnabled(True)
        self.search.addAction(icon('search'), QLineEdit.LeadingPosition)
        self.search.setAccessibleName('Filter filenames in the folder browser')
        self.search.textChanged.connect(self.filter_files)
        side.addWidget(self.search)
        self.model, self.tree = self.create_browser()
        side.addWidget(self.tree, 1)
        shortcuts = QHBoxLayout()
        for name, directory in [('Home', Path.home()), ('Movies', Path.home() / 'Movies')]:
            button = QPushButton(name)
            button.setObjectName('quiet')
            button.clicked.connect(lambda checked=False, path=directory: self.set_folder(str(path)))
            shortcuts.addWidget(button)
        shortcuts.addStretch()
        side.addLayout(shortcuts)
        side.addWidget(label('Drop a file or folder to inspect', 'muted'))
        self.splitter.addWidget(browser)
        detail = QWidget()
        body = QVBoxLayout(detail)
        body.setContentsMargins(30, 14, 30, 10)
        body.setSpacing(6)
        toolbar = QHBoxLayout()
        self.title = ElidedLabel('Every detail, in focus.', 'title')
        self.title.setContextMenuPolicy(Qt.CustomContextMenu)
        self.title.customContextMenuRequested.connect(self.title_copy_menu)
        toolbar.addWidget(self.title, 1)
        open_button = QPushButton('Open media')
        open_button.setIcon(icon('folder'))
        open_button.clicked.connect(self.open_file)
        self.delivery=DeliveryBar(self.settings,self)
        self.delivery.editRequested.connect(self.open_delivery_presets)
        toolbar.addWidget(self.delivery)
        toolbar.addSpacing(12)
        toolbar.addWidget(open_button)
        gear = tool_button('settings', 'Settings and actions', lambda: None)
        menu = QMenu(gear)
        quality_menu=QMenu('Quality tools',menu)
        copy_menu=QMenu('Copy',menu)
        for title, slot, shortcut in [
            ('Open media…', self.open_file, 'Ctrl+O'),
            ('Open folder…', self.open_folder, 'Ctrl+Shift+O'),
            ('Compare files…', lambda: self.open_quality(0), 'Ctrl+Shift+C'),
            ('Batch inspect folder…', lambda: self.open_quality(1), 'Ctrl+Shift+B'),
            ('Delivery presets…', lambda: self.open_quality(2), ''),
            ('Analyze audio…', self.open_audio_analysis, ''),
            ('Media tools & updates…', self.show_media_tools, ''),
            ('Copy media summary', self.copy_summary, ''),
            ('Copy file path', lambda: self.copy_value(self.path or ''), ''),
            ('Copy filename', lambda: self.copy_value(Path(self.path).name if self.path else ''), ''),
            ('Copy source timecode', self.copy_timecode, ''),
            ('Export report…', self.export_report, 'Ctrl+S'),
            ('Copy report', self.copy_report, ''),
            ('Reveal in Finder', self.reveal, ''),
            ('Open in default player', self.open_external, ''),
            ('Settings…', self.preferences, ''),
            ('About Frameglass', self.about, '')]:
            action = QAction(title, self)
            action.triggered.connect(slot)
            if shortcut:
                action.setShortcut(QKeySequence(shortcut))
                self.addAction(action)
            destination=quality_menu if title in ('Compare files…','Batch inspect folder…','Delivery presets…','Analyze audio…') else copy_menu if title.startswith('Copy') else menu
            destination.addAction(action)
        menu.insertMenu(menu.actions()[2],quality_menu)
        menu.insertMenu(menu.actions()[3],copy_menu)
        self.recent_menu=menu.addMenu('Recent files')
        self.recent_menu.aboutToShow.connect(self.populate_recents)
        gear.setMenu(menu)
        gear.setPopupMode(QToolButton.InstantPopup)
        toolbar.addWidget(gear)
        body.addLayout(toolbar)
        self.subtitle = ElidedLabel('A clearer view of your video, audio and images.')
        body.addWidget(self.subtitle)
        self.tabs = QTabWidget()
        self.tabs.currentChanged.connect(lambda index:self.delivery.setVisible(index==0))
        self.tabs.setDocumentMode(True)
        self.tabs.tabBar().setDrawBase(False)
        self.overview, self.overview_layout = self.scroll_tab('Overview')
        self.streams, self.stream_layout = self.scroll_tab('Tracks')
        diagnostic = QWidget()
        diag = QVBoxLayout(diagnostic)
        diag.setContentsMargins(0, 18, 0, 0)
        diag.setSpacing(16)
        summary = Section('Media health', 'AUDIO + VIDEO', 'check')
        self.health_title = label('Ready when you are', 'sectionTitle')
        self.health_detail = label('Open media to check its headers and decode its tracks.', 'muted')
        summary.content.addWidget(self.health_title)
        summary.content.addWidget(self.health_detail)
        states=QHBoxLayout()
        self.header_state=label('Headers · Not read','muted');states.addWidget(self.header_state)
        self.decode_state_label=label('Decode · Not scanned','muted');states.addWidget(self.decode_state_label);states.addStretch()
        summary.content.addLayout(states)
        actions = QHBoxLayout()
        self.scan_button = QPushButton('Scan entire file')
        self.scan_button.setObjectName('primary')
        self.scan_button.setEnabled(False)
        self.scan_button.clicked.connect(self.start_scan)
        actions.addWidget(self.scan_button)
        self.cancel_button = QPushButton('Cancel scan')
        self.cancel_button.hide()
        self.cancel_button.clicked.connect(self.cancel_scan)
        actions.addWidget(self.cancel_button)
        actions.addStretch()
        summary.content.addLayout(actions)
        self.progress = QProgressBar()
        self.progress.setTextVisible(False)
        self.progress.hide()
        summary.content.addWidget(self.progress)
        diag.addWidget(summary)
        diag.addWidget(label('FINDINGS & DECODER OUTPUT', 'eyebrow'))
        self.diag_text = QTextEdit()
        self.diag_text.setObjectName('diagnosticText')
        self.diag_text.setReadOnly(True)
        self.diag_text.setPlainText('No file selected.')
        diag.addWidget(self.diag_text, 1)
        self.tabs.addTab(diagnostic, 'Diagnostics')
        self.raw = QTextEdit()
        self.raw.setReadOnly(True)
        self.tabs.addTab(self.raw, 'Metadata')
        body.addWidget(self.tabs, 1)
        self.tools_notice=QFrame();self.tools_notice.setObjectName('card')
        notice=QHBoxLayout(self.tools_notice);notice.setContentsMargins(10,6,10,6)
        self.tools_notice_text=label('Media tools need attention','muted');notice.addWidget(self.tools_notice_text,1)
        review=QPushButton('Review tools…');review.setObjectName('quiet');review.clicked.connect(self.show_media_tools);notice.addWidget(review)
        later=QPushButton('Later');later.setObjectName('quiet');later.clicked.connect(self.tools_notice.hide);notice.addWidget(later)
        self.tools_notice.hide();body.addWidget(self.tools_notice)
        footer = QHBoxLayout()
        self.status = ElidedLabel('Choose a file to begin')
        footer.addWidget(self.status, 1)
        footer.addWidget(label('LOCAL • READ ONLY', 'eyebrow'))
        body.addLayout(footer)
        self.splitter.addWidget(detail)
        self.splitter.setSizes([272, 1008])
        self.splitter.setStretchFactor(1, 1)
        self.drop_overlay = QFrame(self)
        self.drop_overlay.setObjectName('dropOverlay')
        overlay_layout = QVBoxLayout(self.drop_overlay)
        overlay_layout.addStretch()
        for text, name in [('Drop to inspect', 'title'), ('Media files or folders', 'muted')]:
            item = label(text, name)
            item.setAlignment(Qt.AlignCenter)
            overlay_layout.addWidget(item)
        overlay_layout.addStretch()
        self.drop_overlay.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.drop_overlay.hide()
        QApplication.instance().installEventFilter(self)
        self.set_folder(self.settings.value('folder', str(Path.home())))
        self.empty_state()
        geometry=self.settings.value('window_geometry')
        if geometry:self.restoreGeometry(geometry)
        splitter=self.settings.value('splitter_state')
        if splitter:self.splitter.restoreState(splitter)

    def ensure_tool_controller(self):
        if self.tool_controller is None:
            self.tool_controller=ToolController(self.settings,self)
            self.tool_controller.changed.connect(self.tools_checked)
            self.tool_controller.activated.connect(self.tools_activated)
        return self.tool_controller

    def check_tools_on_startup(self):
        self.ensure_tool_controller().check()

    def show_media_tools(self):
        self.ensure_tool_controller().show()

    def tools_checked(self,result):
        missing=[t['name'] for t in result['tools'] if t['error']]
        updates=[t['name'] for t in result['tools'] if t['status'] in ('Update available','New upstream release · installer build pending') or t['status'].startswith('Homebrew update available')]
        self.tools_notice.setVisible(bool(missing or updates))
        if missing:self.tools_notice_text.setText('Media tools missing or unusable: '+', '.join(missing))
        elif updates:self.tools_notice_text.setText('Media tool updates available: '+', '.join(updates))

    def tools_activated(self):
        if self.path and not self.scan:
            self.inspect(self.path)

    def copy_value(self,text):
        if text:
            QApplication.clipboard().setText(str(text))
            self.status.setText('Copied to clipboard')

    def copy_summary(self):
        if not self.data:return
        from .quality import properties
        rows=properties(self.data)
        self.copy_value(Path(self.path).name+'\n'+self.path+'\n'+'\n'.join(f'{k}: {v}' for k,v in rows.items() if v is not None))

    def copy_timecode(self):
        if not self.data or self.data.get('kind')=='still':return
        tc=clip_timecode(self.data)
        if tc['in_']=='Not available':self.status.setText('No embedded source timecode to copy.');return
        self.copy_value(f"In {tc['in_']} · Out (last) {tc['out']} · {tc['mode']}"+(' · Out estimated' if tc['estimated'] else ''))

    def title_copy_menu(self,point):
        menu=QMenu(self)
        menu.addAction('Copy filename',lambda:self.copy_value(Path(self.path).name if self.path else ''))
        menu.addAction('Copy file path',lambda:self.copy_value(self.path or ''))
        menu.addAction('Copy media summary',self.copy_summary)
        menu.addAction('Copy source timecode',self.copy_timecode)
        menu.exec(self.title.mapToGlobal(point))

    def remember_file(self):
        try:recent=json.loads(self.settings.value('recent_files','[]'))
        except (ValueError,TypeError):recent=[]
        if not isinstance(recent,list):recent=[]
        self.settings.setValue('recent_files',json.dumps([self.path]+[p for p in recent if p!=self.path and isinstance(p,str)][:14]))

    def populate_recents(self):
        self.recent_menu.clear()
        try:recent=json.loads(self.settings.value('recent_files','[]'))
        except (ValueError,TypeError):recent=[]
        for path in recent if isinstance(recent,list) else []:
            if not isinstance(path,str):continue
            action=self.recent_menu.addAction(Path(path).name);action.setToolTip(path);action.setEnabled(Path(path).is_file())
            action.triggered.connect(lambda checked=False,p=path:self.inspect(p))
        if self.recent_menu.isEmpty():self.recent_menu.addAction('No recent media').setEnabled(False)

    def open_audio_analysis(self):
        if not self.data or not media_tracks(self.data)[1]:
            QMessageBox.information(self,'Audio analysis','Load a file with an audio track first.');return
        if self.analysis_dialog:self.analysis_dialog.close()
        track=self.playback.selected_audio_track
        seconds=float(self.data.get('format',{}).get('duration') or 0)
        self.analysis_dialog=AudioAnalysisDialog(self.path,track,self.settings,self.playback,seconds,self)
        self.analysis_dialog.signals.ready.connect(self.keep_analysis,Qt.QueuedConnection)
        dialog=self.analysis_dialog
        dialog.finished.connect(lambda code,d=dialog:self.analysis_closed(d))
        self.analysis_dialog.show()

    def analysis_closed(self,dialog):
        if self.analysis_dialog is dialog:self.analysis_dialog=None

    def keep_analysis(self,result,error,token):
        if result and result['path']==self.path and self.analysis_dialog and not self.analysis_dialog.job.cancelled.is_set():
            self.analysis_summary={k:v for k,v in result.items() if k not in ('waveform','log')}
            self.data.setdefault('audio_measurements',{})[str(result['track'])]=self.analysis_summary
            self.delivery.refresh()

    def open_quality(self, tab=0):
        if self.playback:
            self.playback.pause()
        if self.quality_workspace is None:
            self.quality_workspace = QualityWorkspace(self.settings,self)
            self.quality_workspace.openRequested.connect(self.open_quality_file)
            self.quality_workspace.presetsChanged.connect(self.delivery.reload)
        workspace = self.quality_workspace
        workspace.update_current(self.path,self.data)
        if not workspace.batch_folder.text():workspace.batch_folder.setText(self.folder.text())
        workspace.tabs.setCurrentIndex(tab)
        if tab == 0 and self.data and not any(workspace.compare_data):workspace.use_current(0)
        workspace.show();workspace.raise_();workspace.activateWindow()

    def open_delivery_presets(self,name=''):
        self.open_quality(2)
        if name:self.quality_workspace.preset_list.setCurrentText(name)

    def open_quality_file(self,path):
        self.quality_workspace.close()
        self.set_folder(str(Path(path).parent))
        self.inspect(path)

    def scroll_tab(self, name):
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        widget = QWidget()
        content = QVBoxLayout(widget)
        content.setContentsMargins(0, 10, 6, 6)
        content.setSpacing(10)
        content.setAlignment(Qt.AlignTop)
        scroll.setWidget(widget)
        self.tabs.addTab(scroll, name)
        return scroll, content

    def rows(self, rows, compact=False, delivery_track=None):
        widget = QWidget()
        form = QFormLayout(widget)
        form.setContentsMargins(0, 0, 0, 0)
        form.setVerticalSpacing(5 if compact else 10)
        form.setHorizontalSpacing(14 if compact else 20)
        form.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)
        form.setRowWrapPolicy(QFormLayout.WrapLongRows)
        for key, value in rows:
            key_label = label(key, 'muted')
            key_label.setMinimumWidth(90)
            value_label = label(value)
            value_label.setMinimumWidth(0)
            value_label.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
            if delivery_track:
                rule_keys={'Codec':['video_codec' if delivery_track.startswith('Video') else 'audio_codec'],
                           'Scan':['scan'],'Primaries':['primaries'],'Pixel format':['pixel_format'],'Color space':['matrix'],
                           'Transfer':['transfer'],'Channels':['channels','channel_layout'],'Channel layout':['channel_layout'],
                           'Sample rate':['sample_rate'],'Bit depth':['bits'],'Stream bitrate':['audio_bitrate_min','audio_bitrate_max'],
                           'Format':['container'],'Audio tracks':['audio_tracks']}.get(key,[])
                self.delivery.bind(value_label,rule_keys,delivery_track if key not in ('Format','Audio tracks') else 'File')
            form.addRow(key_label, value_label)
        return widget

    def card(self, layout, title, rows):
        frame = Section(title)
        frame.content.addWidget(self.rows(rows))
        layout.addWidget(frame)
        return frame

    def empty_state(self):
        zone = QFrame()
        zone.setObjectName('dropZone')
        zone.setMinimumHeight(330)
        content = QVBoxLayout(zone)
        content.setContentsMargins(36, 38, 36, 38)
        content.setSpacing(18)
        content.addStretch()
        mark = QLabel()
        mark.setPixmap(icon('brand', '#adddce', 64).pixmap(64, 64))
        mark.setAlignment(Qt.AlignCenter)
        content.addWidget(mark)
        for text, name in [('Your media. A little clearer.', 'title'),
                           ('Drop a file here, or choose one from your folders.', 'muted')]:
            item = label(text, name)
            item.setAlignment(Qt.AlignCenter)
            content.addWidget(item)
        button = QPushButton('Choose media')
        button.setObjectName('primary')
        button.clicked.connect(self.open_file)
        content.addWidget(button, 0, Qt.AlignCenter)
        content.addStretch()
        self.overview_layout.addWidget(zone)
        self.card(self.overview_layout, 'Built for a closer look', [
            ('Understand', 'Picture, sound, color and encoding details at a glance.'),
            ('Inspect', 'Explore every track and its original metadata.'),
            ('Diagnose', 'Scan the complete file and review decoder findings.')])

    def filter_files(self, text):
        self.model.setNameFilters(['*' + text + '*'] if text else [])

    def set_health(self, title, detail):
        self.health_title.setText(title)
        self.health_detail.setText(detail)

    def create_browser(self, folder=None):
        model = QFileSystemModel(self)
        model.setReadOnly(True)
        model.setFilter(QDir.AllDirs | QDir.Files | QDir.NoDotAndDotDot)
        model.setNameFilterDisables(False)
        text = self.search.text()
        model.setNameFilters(['*' + text + '*'] if text else [])
        tree = QTreeView()
        if folder is not None:
            root = model.setRootPath(folder)
        tree.setModel(model)
        if folder is not None:
            tree.setRootIndex(root)
        tree.setSortingEnabled(True)
        tree.sortByColumn(0, Qt.AscendingOrder)
        tree.setIndentation(15)
        tree.setUniformRowHeights(True)
        tree.setIconSize(QSize(16, 16))
        for col in (1, 2, 3):
            tree.hideColumn(col)
        tree.setHeaderHidden(True)
        tree.selectionModel().currentChanged.connect(lambda current, previous: self.selected(current))
        tree.doubleClicked.connect(self.navigate)
        return model, tree

    def set_folder(self, folder):
        path = Path(folder).expanduser()
        if not path.is_dir():
            self.status.setText('Folder unavailable. Choose another folder.')
            return
        self.folder.setText(str(path.absolute()))
        self.folder.setCursorPosition(0)
        self.folder_name.setText('Home' if path == Path.home() else path.name or '/')
        # Qt's macOS accessibility cache can retain stale table cells when a
        # populated QTreeView changes root. Initialize a fresh model/view before
        # exposing it, rather than resetting the accessible live table.
        folder_path = str(path.absolute())
        if self.model.rootPath() != folder_path:
            model, tree = self.create_browser(folder_path)
            previous_tree, previous_model = self.tree, self.model
            layout = previous_tree.parentWidget().layout()
            layout.replaceWidget(previous_tree, tree)
            self.model, self.tree = model, tree
            previous_tree.hide()
            previous_tree.blockSignals(True)
            previous_tree.selectionModel().blockSignals(True)
            previous_tree.deleteLater()
            previous_model.deleteLater()
        self.settings.setValue('folder', str(path))

    def open_folder(self):
        path = QFileDialog.getExistingDirectory(self, 'Choose media folder', self.folder.text())
        if path:
            self.set_folder(path)

    def open_file(self):
        path, _ = QFileDialog.getOpenFileName(self, 'Inspect media', self.folder.text())
        if path:
            self.inspect(path)

    def selected(self, index):
        path = self.model.filePath(index)
        if Path(path).is_file() and str(Path(path).absolute()) != self.path:
            self.inspect(path)

    def navigate(self, index):
        path = self.model.filePath(index)
        if Path(path).is_dir():
            self.set_folder(path)

    def eventFilter(self, watched, event):
        # Intercept file drops over children too, including read-only text views.
        if isinstance(watched, QWidget) and (watched is self or self.isAncestorOf(watched)):
            if event.type() in (QEvent.KeyPress, QEvent.KeyRelease) and self.playback and not QApplication.activeModalWidget():
                focus = QApplication.focusWidget()
                editing = isinstance(focus, (QLineEdit, QTextEdit))
                modified = bool(event.modifiers() & (Qt.ControlModifier | Qt.MetaModifier | Qt.AltModifier))
                if not editing and not modified and self.playback.handle_key(event):
                    event.accept()
                    return True
                if event.type() == QEvent.KeyRelease and event.key() == Qt.Key_K:
                    self.playback.held_k = False
            if event.type() == QEvent.WindowDeactivate and self.playback:
                self.playback.held_k = False
            if event.type() in (QEvent.DragEnter, QEvent.DragMove):
                if any(u.isLocalFile() for u in event.mimeData().urls()):
                    self.drop_overlay.setGeometry(self.rect().adjusted(14, 14, -14, -14))
                    self.drop_overlay.show()
                    self.drop_overlay.raise_()
                    event.acceptProposedAction()
                    return True
            elif event.type() == QEvent.DragLeave:
                self.drop_overlay.hide()
            elif event.type() == QEvent.Drop:
                self.drop_overlay.hide()
                paths = [u.toLocalFile() for u in event.mimeData().urls() if u.isLocalFile()]
                if paths:
                    path = Path(paths[0])
                    self.set_folder(str(path if path.is_dir() else path.parent))
                    if path.is_file():
                        self.inspect(str(path))
                    event.acceptProposedAction()
                    return True
        return super().eventFilter(watched, event)

    def stop_process(self, process):
        if process:
            process.blockSignals(True)
            process.kill()
            process.waitForFinished(1000)
            process.deleteLater()

    def inspect(self, path):
        if self.analysis_dialog:
            self.analysis_dialog.close()
            self.analysis_dialog=None
        self.analysis_summary=None
        self.delivery.set_media(None,None)
        self.header_state.setText('Headers · Reading…')
        self.decode_state_label.setText('Decode · Not scanned')
        self.thumbnail_cancelled.set()
        self.thumbnail_cancelled = Event()
        self.thumbnail_token += 1
        self.dispose_playback()
        self.stop_process(self.probe)
        self.probe = None
        self.stop_process(self.scan)
        self.stop_process(self.thumbnail)
        self.thumbnail = None
        self.scan = None
        self.cancel_button.hide()
        self.progress.hide()
        self.path = str(Path(path).absolute())
        self.data = None
        self.still_image = None
        self.image_viewer = None
        self.scan_log = ''
        self.preview = None
        self.title.setText(Path(path).name)
        self.subtitle.setText(self.path)
        self.subtitle.setToolTip(self.path)
        self.tabs.setTabText(1, 'Tracks')
        self.status.setText('Reading media headers…')
        self.scan_button.setEnabled(False)
        self.raw.clear()
        self.diag_text.setPlainText('Reading metadata…')
        self.set_health('Reading headers…', 'Checking the container and media tracks.')
        clear(self.overview_layout)
        clear(self.stream_layout)
        self.card(self.overview_layout, 'Reading media…', [('Status', 'Inspecting container and track headers')])
        if Path(path).suffix.lower() in STILL_EXTENSIONS:
            self.still_job = StillJob(self.path, self.still_signals, self.thumbnail_cancelled, self.thumbnail_token)
            QThreadPool.globalInstance().start(self.still_job)
        else:
            self.start_probe()

    def still_ready(self, data, image, error, token):
        if token != self.thumbnail_token or self.thumbnail_cancelled.is_set():
            return
        if data is None:
            self.start_probe()  # Animated or unsupported images retain FFmpeg inspection.
            return
        self.data = data
        self.remember_file()
        self.header_state.setText('Image metadata · Read')
        self.decode_state_label.setText('Image · Decoded')
        self.still_image = image
        self.render()

    def start_probe(self):
        self.probe = QProcess(self)
        process = self.probe
        process.finished.connect(lambda code, status: self.probe_finished(process, code))
        process.errorOccurred.connect(lambda error: self.process_error(process, 'FFprobe'))
        process.start(executable('ffprobe', self.settings.value('ffprobe', '')), probe_args(self.path))
        QTimer.singleShot(30000, lambda: self.probe_timeout(process))

    def process_error(self, process, name):
        if process.error() == QProcess.FailedToStart:
            self.status.setText(f'{name} could not start. Check its executable path in Settings.')
            self.diag_text.setPlainText(process.errorString())
            self.set_health('Tool unavailable', f'Check the {name} location in Settings.')
            if name == 'FFmpeg':
                self.scan = None
                self.scan_button.setEnabled(True)
                self.cancel_button.hide()
                self.progress.hide()

    def probe_timeout(self, process):
        if self.probe is process and process.state() != QProcess.NotRunning:
            self.stop_process(process)
            self.probe = None
            self.status.setText('Inspection timed out after 30 seconds.')
            self.set_health('Inspection timed out', 'Try again or check the file’s storage location.')
            self.diag_text.setPlainText('FFprobe could not finish reading this file. The file may be damaged, inaccessible or on slow storage.')

    def probe_finished(self, process, code):
        if process is not self.probe:return
        stderr = bytes(process.readAllStandardError()).decode(errors='replace')
        try:
            data = json.loads(bytes(process.readAllStandardOutput()))
        except (ValueError, UnicodeDecodeError):
            data = {}
        if code or not data.get('streams'):
            clear(self.overview_layout)
            self.card(self.overview_layout, 'Could not read this file', [('Next step', 'See Diagnostics for the error and possible causes.')])
            self.status.setText('Unable to inspect this file.')
            self.header_state.setText('Headers · Unreadable')
            self.set_health('Unable to read media', 'The file may be unsupported, inaccessible or incomplete.')
            self.diag_text.setPlainText((stderr or json.dumps(data, indent=2) or 'No readable media streams.') + '\n\n' + '\n\n'.join(decoder_advice(stderr)))
            self.raw.setPlainText(json.dumps(data, indent=2, ensure_ascii=False))
            self.tabs.setCurrentIndex(2)
            return
        self.data = data
        self.remember_file()
        self.header_state.setText('Headers · Read successfully')
        self.render()
        if stderr:
            self.diag_text.append('\nFFprobe messages:\n' + stderr)
        self.status.setText('Headers read · Payload not scanned · Full scan in Diagnostics')
        self.scan_button.setEnabled(True)

    def dispose_playback(self):
        if self.playback:
            self.playback.dispose()
            self.playback = None

    def render(self):
        self.dispose_playback()
        self.delivery.bindings=[]
        self.delivery.set_media(self.path,self.data)
        QTimer.singleShot(0,self.delivery.refresh)
        clear(self.overview_layout)
        clear(self.stream_layout)
        data = self.data
        if data.get('kind') == 'still':
            self.render_still()
            return
        fmt = data.get('format', {})
        streams = data.get('streams', [])
        videos, audios = media_tracks(data)
        if audios and not videos:
            self.render_audio(audios)
            return
        self.raw.setPlainText(json.dumps(data, indent=2, ensure_ascii=False))
        details = QWidget()
        detail_layout = QVBoxLayout(details)
        detail_layout.setContentsMargins(0, 0, 0, 0)
        detail_layout.setSpacing(10)
        self.preview = Preview()
        self.preview.placeholder('video' if videos else 'audio' if audios else 'brand')
        self.preview.setToolTip('Representative frame preview')
        metrics = QGridLayout()
        metrics.setSpacing(10)
        resolution = f"{videos[0].get('width', '?')} × {videos[0].get('height', '?')}" if videos else 'Audio only' if audios else 'Other media'
        clip_duration = duration_timecode(data)
        duration_title = 'DURATION' + (' · ' + clip_duration['mode'] if clip_duration['mode'] else '')
        if clip_duration['estimated']:
            duration_title += ' · EST.'
        facts = [(duration_title, clip_duration['value']), ('FILE SIZE', size(fmt.get('size'))),
                 ('RESOLUTION' if videos else 'MEDIA TYPE', resolution),
                 ('FRAME RATE' if videos else 'SAMPLE RATE', rate(videos[0].get('avg_frame_rate')) + ' fps' if videos else str(audios[0].get('sample_rate', '?')) + ' Hz' if audios else '—')]
        for i, (name, value) in enumerate(facts):
            metric = QFrame()
            metric.setObjectName('metricCard')
            box = QVBoxLayout(metric)
            box.setContentsMargins(14, 10, 14, 10)
            box.setSpacing(4)
            box.addWidget(label(name, 'eyebrow'))
            text = ElidedLabel(value, 'metric')
            text.setMinimumHeight(29)
            if i == 0:
                metric.setToolTip(clip_duration['note'])
                text.setToolTip(value + '\n' + clip_duration['note'])
                text.setAccessibleName('Duration: ' + value + '. ' + clip_duration['note'])
            box.addWidget(text)
            self.delivery.bind(text,[['duration_min','duration_max'],[],['width','height'],['fps']][i],'Video 1' if i in (2,3) else 'File')
            metrics.addWidget(metric, i // 2, i % 2)
        detail_layout.addLayout(metrics, 1)
        tc = clip_timecode(data)
        timecode = QFrame()
        timecode.setObjectName('timecodeStrip')
        timecode.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Expanding)
        explanation = tc['note'] + '\nSource: ' + (tc['source'] or 'No embedded timecode')
        timecode.setToolTip(explanation)
        values = QGridLayout(timecode)
        values.setContentsMargins(12, 9, 12, 9)
        values.setHorizontalSpacing(6)
        values.setVerticalSpacing(5)
        for row, (name, value) in enumerate([('In', tc['in_']), ('Out (est.)' if tc['estimated'] else 'Out (last)', tc['out'])]):
            caption = label(name + (' · ' + {'Drop-frame': 'DF', 'Non-drop': 'NDF'}.get(tc['mode'], '—') if row == 0 else ''), 'muted')
            caption.setWordWrap(False)
            caption.setToolTip(explanation)
            values.addWidget(caption, row, 0)
            number = label(value, 'timecodeValue')
            number.setWordWrap(False)
            number.setToolTip(explanation)
            number.setAccessibleName(f'{name} timecode: {value}. {explanation}')
            self.delivery.bind(number,['timecode_required','timecode_mode'])
            values.addWidget(number, row, 1)
        values.setColumnStretch(1, 1)
        container = QFrame()
        container.setObjectName('containerSummary')
        container.setMinimumWidth(100)
        container_box = QVBoxLayout(container)
        container_box.setContentsMargins(10, 9, 10, 9)
        container_box.setSpacing(3)
        container_box.addWidget(label('CONTAINER', 'eyebrow'))
        format_name = fmt.get('format_long_name', fmt.get('format_name', 'Unknown'))
        format_label = ElidedLabel(format_name, 'containerValue')
        format_label.setAccessibleName('Container format: ' + format_name)
        self.delivery.bind(format_label,['container'])
        container_box.addWidget(format_label)
        bit_rate = f"{float(fmt['bit_rate']) / 1000000:,.2f} Mb/s" if str(fmt.get('bit_rate', '')).isdigit() else 'Unknown bit rate'
        bitrate_label = ElidedLabel(bit_rate, 'muted')
        bitrate_label.setToolTip('Container bit rate: ' + bit_rate)
        self.delivery.bind(bitrate_label,['bitrate_min','bitrate_max'])
        container_box.addWidget(bitrate_label)
        technical_summary = QHBoxLayout()
        technical_summary.setSpacing(10)
        technical_summary.addWidget(container, 1)
        technical_summary.addWidget(timecode, 1)
        detail_layout.addLayout(technical_summary)
        self.playback = ClipPlayer(self.preview, self.path, self.settings, data, playable=bool(videos or audios))
        self.overview_layout.addWidget(MediaHero(self.playback, details))
        summary = QHBoxLayout()
        summary.addWidget(label('TRACK SUMMARY', 'eyebrow'))
        for name, count in [('Video', len(videos)), ('Audio', len(audios))]:
            badge = label(f'{name} · {count}', 'badge')
            badge.setWordWrap(False)
            badge.setToolTip('Video tracks exclude cover art.' if name == 'Video' else 'Audio tracks, not individual channels. A stereo track counts as one.')
            if name=='Audio':self.delivery.bind(badge,['audio_tracks'])
            summary.addWidget(badge)
        summary.addStretch()
        other_count = len(streams) - len(videos) - len(audios)
        extras = []
        if other_count:
            extras.append(f'{other_count} other')
        if data.get('chapters'):
            extras.append(f'{len(data["chapters"])} chapters')
        track_count = label(' · '.join(extras), 'muted')
        track_count.setWordWrap(False)
        summary.addWidget(track_count)
        self.overview_layout.addLayout(summary)
        track_grid = QGridLayout()
        track_grid.setSpacing(14)
        overview_cards = []
        for i, stream in enumerate(streams):
            kind = stream.get('codec_type', 'Other')
            name = 'Cover art' if stream.get('disposition', {}).get('attached_pic') else kind.capitalize()
            title = f"{name}"
            rows = [('Codec', stream.get('codec_long_name', stream.get('codec_name', 'Unknown')))]
            if kind == 'video':
                rows += [('Scan', scan_mode(stream)), ('Primaries', stream.get('color_primaries', 'Not reported')),
                         ('Display', display_description(stream) if self.settings.value('color_mode', 'auto') == 'auto' else 'Native rendering'),
                         ('Profile', stream.get('profile', 'Not reported')),
                         ('Pixel format', stream.get('pix_fmt', 'Not reported')),
                         ('Color space', stream.get('color_space', 'Not reported')),
                         ('Transfer', stream.get('color_transfer', 'Not reported'))]
            elif kind == 'audio':
                rows += [('Channels', stream.get('channel_layout', str(stream.get('channels', '?')))),
                         ('Sample rate', f"{stream.get('sample_rate', '?')} Hz"),
                         ('Bit depth', stream.get('bits_per_raw_sample') or stream.get('bits_per_sample') or 'Not reported')]
            language = stream.get('tags', {}).get('language', 'Not specified')
            rows.append(('Language', 'Not specified' if language == 'und' else language))
            if kind != 'audio':
                card = Section(title, f"TRACK {stream.get('index', '?')}", kind if kind in ('video', 'audio') else 'brand')
                card.content.addWidget(self.rows(rows, compact=True,delivery_track=f'Video {videos.index(stream)+1}' if stream in videos else None))
                card.content.addStretch()
                overview_cards.append((kind, card))
            technical_rows = [(k.replace('_', ' ').capitalize(), json.dumps(v, ensure_ascii=False) if isinstance(v, (dict, list)) else str(v)) for k, v in stream.items()]
            panel = self.rows(technical_rows)
            panel.layout().setContentsMargins(12, 8, 12, 12)
            disclosure = Disclosure(f"{name} · Track {stream.get('index', '?')} · {stream.get('codec_name', 'Unknown').upper()}", panel, i == 0)
            self.stream_layout.addWidget(disclosure)
        cards = [card for kind, card in overview_cards if kind == 'video']
        self.audio_selector = None
        if audios:
            audio_card = Section('Audio', symbol='audio')
            self.audio_selector = AudioTrackBrowser(len(audios))
            self.audio_selector.setToolTip('Select the audio track to inspect and hear during playback')
            audio_pages = QStackedWidget()
            audio_pages.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
            for audio_index, stream in enumerate(audios):
                channels = stream.get('channels', '?')
                language = stream.get('tags', {}).get('language', 'Not specified')
                audio_pages.addWidget(self.rows([
                    ('Stream', str(stream.get('index', '?'))),
                    ('Codec', stream.get('codec_long_name', stream.get('codec_name', 'Unknown'))),
                    ('Channels', stream.get('channel_layout') or str(channels)),
                    ('Sample rate', f"{stream.get('sample_rate', '?')} Hz"),
                    ('Bit depth', stream.get('bits_per_raw_sample') or stream.get('bits_per_sample') or 'Not reported'),
                    ('Language', 'Not specified' if language == 'und' else language)], compact=True,delivery_track=f'Audio {audio_index+1}'))
            audio_card.content.itemAt(0).layout().addWidget(self.audio_selector)
            self.audio_selector.currentIndexChanged.connect(audio_pages.setCurrentIndex)
            self.audio_selector.currentIndexChanged.connect(self.playback.set_audio_track)
            audio_card.content.addWidget(audio_pages)
            audition=QPushButton('Channels · Solo / Mute / Phase…');audition.clicked.connect(self.playback.show_audio_channels);audio_card.content.addWidget(audition)
            audio_card.content.addStretch()
            cards.append(audio_card)
        cards.extend(card for kind, card in overview_cards if kind != 'video')
        for index, card in enumerate(cards):
            card.content.setContentsMargins(16, 11, 16, 11)
            card.content.setSpacing(7)
            track_grid.addWidget(card, index // 2, index % 2)
        track_grid.setColumnStretch(0, 1)
        track_grid.setColumnStretch(1, 1)
        self.overview_layout.addLayout(track_grid)
        if fmt.get('tags'):
            tags = self.rows(list(fmt['tags'].items()))
            tags.layout().setContentsMargins(12, 8, 12, 12)
            self.overview_layout.addWidget(Disclosure('File tags', tags))
        for chapter in data.get('chapters', []):
            self.card(self.stream_layout, f"Chapter {chapter.get('id', '?')}", [(k, json.dumps(v, ensure_ascii=False) if isinstance(v, dict) else str(v)) for k, v in chapter.items()])
        checks = findings(data)
        has_issues = any(f[0] in ('Warning', 'Issue') for f in checks)
        self.set_health('Metadata needs attention' if has_issues else 'Headers read successfully',
                        'Review the findings below. A full scan checks the audio and video payload.' if has_issues else 'No obvious header issues. Run a full scan to check the audio and video payload.')
        self.diag_text.setPlainText('\n\n'.join(f'{level.upper()} · {title}\n{detail}' for level, title, detail in checks))
        if any(s.get('codec_type') == 'video' for s in streams):
            self.load_thumbnail()

    def render_audio(self, audios):
        self.tabs.setTabText(1, 'Audio tracks')
        self.raw.setPlainText(json.dumps(self.data, indent=2, ensure_ascii=False))
        self.preview = Preview(); self.preview.placeholder('audio')
        self.preview.setToolTip('Embedded artwork' if any(s.get('disposition',{}).get('attached_pic') for s in self.data.get('streams',[])) else 'Audio file · No embedded artwork')
        self.playback = ClipPlayer(self.preview,self.path,self.settings,self.data,audio_only=True)
        self.audio_selector = AudioTrackBrowser(len(audios))
        self.audio_selector.setToolTip('Select the audio track to inspect and hear')
        hero = QWidget(); hero.setFixedHeight(260)
        layout = QHBoxLayout(hero);layout.setContentsMargins(0,0,0,0);layout.setSpacing(14)
        layout.addWidget(self.playback, 2)
        details = QWidget();right = QVBoxLayout(details);right.setContentsMargins(0,0,0,0);right.setSpacing(8)
        heading = QHBoxLayout();heading.addWidget(label('AUDIO', 'eyebrow'));heading.addStretch();heading.addWidget(self.audio_selector);right.addLayout(heading)
        identity = QStackedWidget();identity.setSizePolicy(QSizePolicy.Ignored,QSizePolicy.Preferred)
        facts = QStackedWidget();facts.setSizePolicy(QSizePolicy.Ignored,QSizePolicy.Expanding)
        panels = QStackedWidget();panels.setSizePolicy(QSizePolicy.Ignored,QSizePolicy.Maximum)
        all_tags = QWidget();tag_layout = QVBoxLayout(all_tags);tag_layout.setContentsMargins(12,8,12,12)
        for index,stream in enumerate(audios):
            overview = audio_overview(self.data,stream)
            identity_panel = QWidget();titles = QVBoxLayout(identity_panel);titles.setContentsMargins(0,0,0,0);titles.setSpacing(2)
            values = dict(overview['identity'])
            titles.addWidget(ElidedLabel(values.get('Title',Path(self.path).stem),'sectionTitle'))
            byline = ' · '.join(values[k] for k in ('Artist','Album') if values.get(k))
            titles.addWidget(ElidedLabel(byline or 'No artist or album tags embedded','muted'))
            identity.addWidget(identity_panel)
            metric_panel = QWidget();grid=QGridLayout(metric_panel);grid.setContentsMargins(0,0,0,0);grid.setSpacing(10)
            for position,(name,value) in enumerate(overview['facts']):
                metric=QFrame();metric.setObjectName('metricCard');box=QVBoxLayout(metric);box.setContentsMargins(12,8,12,8)
                box.addWidget(label(name,'eyebrow'));text=ElidedLabel(value,'metric');box.addWidget(text)
                self.delivery.bind(text,[['duration_min','duration_max'],['sample_rate'],['channels','channel_layout'],['audio_bitrate_min','audio_bitrate_max']][position], 'File' if position==0 else f'Audio {index+1}')
                grid.addWidget(metric,position//2,position%2)
            facts.addWidget(metric_panel)
            panel=QWidget();cards=QGridLayout(panel);cards.setContentsMargins(0,0,0,0);cards.setSpacing(14)
            sections=[('Sound details',overview['technical']),('File details',overview['file'])]
            inline_music = bool(overview['music'] and not overview['recording'])
            if overview['music'] and not inline_music:sections.append(('Music & rights',overview['music']))
            if overview['recording']:sections.append(('Recording',overview['recording']))
            for position,(title,rows) in enumerate(sections):
                card=Section(title);card.content.setContentsMargins(16,12,16,12);card.content.setSpacing(7)
                if title=='Sound details':
                    audition=QPushButton('Channels…');audition.setObjectName('quiet');audition.setToolTip('Solo, mute, reported channel assignments and stereo phase');audition.clicked.connect(self.playback.show_audio_channels);card.content.itemAt(0).layout().addWidget(audition)
                card.content.addWidget(self.rows(rows,compact=True,delivery_track=f'Audio {index+1}'))
                if title == 'File details' and inline_music:
                    card.content.addWidget(label('MUSIC & RIGHTS','eyebrow'))
                    card.content.addWidget(self.rows(overview['music'],compact=True))
                card.content.addStretch();cards.addWidget(card,position//2,position%2)
            cards.setColumnStretch(0,1);cards.setColumnStretch(1,1);panels.addWidget(panel)
            technical=[(k.replace('_',' ').capitalize(),json.dumps(v,ensure_ascii=False) if isinstance(v,(dict,list)) else str(v)) for k,v in stream.items()]
            self.stream_layout.addWidget(Disclosure(f"Audio · Track {index+1} · {stream.get('codec_name','Unknown').upper()}",self.rows(technical),index==0))
            if stream.get('tags'):tag_layout.addWidget(Disclosure(f'Track {index+1} tags',self.rows(list(stream['tags'].items()),compact=True)))
        right.addWidget(identity);right.addWidget(facts,1);layout.addWidget(details,3)
        self.overview_layout.addWidget(hero);self.overview_layout.addWidget(panels)
        format_tags=self.data.get('format',{}).get('tags',{})
        if format_tags:tag_layout.insertWidget(0,self.rows(list(format_tags.items()),compact=True))
        if not tag_layout.count():tag_layout.addWidget(label('No descriptive tags embedded in this file.','muted'))
        metadata_disclosure = Disclosure('All audio metadata',all_tags)
        metadata_disclosure.setSizePolicy(QSizePolicy.Expanding,QSizePolicy.Maximum)
        self.overview_layout.addWidget(metadata_disclosure)
        self.audio_selector.currentIndexChanged.connect(identity.setCurrentIndex)
        self.audio_selector.currentIndexChanged.connect(facts.setCurrentIndex)
        self.audio_selector.currentIndexChanged.connect(panels.setCurrentIndex)
        self.audio_selector.currentIndexChanged.connect(self.playback.set_audio_track)
        checks=findings(self.data)
        self.set_health('Audio headers read successfully','Run a full decode scan to check the audio payload. Metadata does not measure audible quality.')
        self.diag_text.setPlainText('\n\n'.join(f'{level.upper()} · {title}\n{detail}' for level,title,detail in checks))
        if any(s.get('disposition',{}).get('attached_pic') for s in self.data.get('streams',[])):
            self.load_thumbnail()

    def render_still(self):
        info = self.data['image']
        self.tabs.setTabText(1, 'Image metadata')
        self.raw.setPlainText(json.dumps(self.data, indent=2, ensure_ascii=False))
        hero = QWidget()
        hero.setFixedHeight(320)
        row = QHBoxLayout(hero); row.setContentsMargins(0, 0, 0, 0); row.setSpacing(14)
        picture = QWidget(); picture_layout = QVBoxLayout(picture); picture_layout.setContentsMargins(0,0,0,0)
        self.image_viewer = ImageViewer(self.still_image)
        picture_layout.addWidget(self.image_viewer, 1)
        caption = label(f"{info['width']:,} × {info['height']:,} px  ·  {info['megapixels']:g} MP  ·  {info['aspect']}", 'muted')
        caption.setAlignment(Qt.AlignCenter); picture_layout.addWidget(caption)
        row.addWidget(picture, 3)
        essentials = QWidget(); facts = QVBoxLayout(essentials); facts.setContentsMargins(0,0,0,0); facts.setSpacing(10)
        grid = QGridLayout(); grid.setSpacing(10)
        for index, (name,value) in enumerate([
            ('DIMENSIONS', f"{info['width']:,} × {info['height']:,}"),
            ('FILE SIZE', size(self.data['format']['size'])),
            ('FORMAT', self.data['format']['format_name']),
            ('COLOR', f"{info['mode']} · {info['bits']}-bit")]):
            card = QFrame();card.setObjectName('metricCard')
            box = QVBoxLayout(card);box.setContentsMargins(12,10,12,10)
            box.addWidget(label(name,'eyebrow'));text=ElidedLabel(value,'metric');box.addWidget(text)
            if index==0:self.delivery.bind(text,['width','height'],'Image')
            if index==2:self.delivery.bind(text,['container'])
            grid.addWidget(card,index//2,index%2)
        facts.addLayout(grid)
        profile = Section('Color & reproduction')
        profile.content.setContentsMargins(12,10,12,10)
        profile.content.addWidget(self.rows([('ICC profile', info['profile']), ('Transparency', 'Yes' if any(k == 'Transparency' and v == 'Yes' for k,v in info['properties']) else 'No')], compact=True))
        facts.addWidget(profile)
        row.addWidget(essentials,2)
        self.overview_layout.addWidget(hero)
        cards = QGridLayout();cards.setSpacing(14)
        sections = []
        if info['capture']:sections.append(('Capture', info['capture']))
        if info['description']:sections.append(('Description & rights', info['description']))
        image_details = [(k,v) for k,v in info['properties'] if k not in ('ICC profile','Transparency','Color model','Bit depth')]
        combine_details = len(sections) == 2
        if not combine_details:
            sections.append(('Image details', image_details))
        for index,(title,rows) in enumerate(sections):
            card = Section(title);card.content.setContentsMargins(16,12,16,12)
            card.content.addWidget(self.rows(rows, compact=True))
            if combine_details and title == 'Description & rights':
                card.content.addWidget(label('IMAGE DETAILS', 'eyebrow'))
                card.content.addWidget(self.rows(image_details, compact=True))
            card.content.addStretch()
            cards.addWidget(card,index//2,index%2)
        cards.setColumnStretch(0,1);cards.setColumnStretch(1,1)
        self.overview_layout.addLayout(cards)
        if info['gps']:
            self.overview_layout.addWidget(Disclosure('Location',self.rows(list(info['gps'].items()),compact=True)))
        all_metadata = QWidget(); groups = QVBoxLayout(all_metadata);groups.setContentsMargins(10,4,10,10)
        for title,key in [('EXIF','exif'),('IPTC','iptc'),('XMP','xmp'),('Embedded text','embedded_text')]:
            entries = info[key]
            if entries:
                rows = [(k, json.dumps(v,ensure_ascii=False) if isinstance(v,(list,dict)) else str(v)) for k,v in entries.items()]
                groups.addWidget(Disclosure(title,self.rows(rows,compact=True)))
                self.stream_layout.addWidget(Disclosure(title,self.rows(rows,compact=True)))
        if not groups.count():groups.addWidget(label('No EXIF, IPTC, or XMP metadata embedded in this image.','muted'))
        self.overview_layout.addWidget(Disclosure('All image metadata',all_metadata))
        self.status.setText('Image ready · Orientation applied · Source file unchanged')
        self.set_health('Image decoded successfully', 'Image metadata and embedded color profile have been read. Video decode scans do not apply to still images.')
        self.diag_text.setPlainText('Image decoded successfully.\n\n' + '\n'.join(f'{k}: {v}' for k,v in info['properties']))
        self.scan_button.setEnabled(False)

    def load_thumbnail(self):
        video, _ = media_tracks(self.data)
        source = video[0] if video else next((s for s in self.data.get('streams', []) if s.get('codec_type') == 'video'), {})
        self.thumbnail_transform = display_transform(source) if self.settings.value('color_mode', 'auto') == 'auto' else None
        scale = 'scale=1280:720:force_original_aspect_ratio=decrease'
        if self.thumbnail_transform:
            scale += ':in_color_matrix=' + ('bt2020' if self.thumbnail_transform.matrix == 'bt2020nc' else 'bt709')
            scale += ':in_range=' + ('full' if self.thumbnail_transform.full_range else 'limited') + ':out_range=full'
        arguments = ['-nostdin', '-v', 'error', '-i', self.path, '-map', f"0:{source.get('index', 0)}", '-frames:v', '1', '-vf', scale]
        if self.thumbnail_transform:
            arguments += ['-pix_fmt', 'rgb48be']
        arguments += ['-f', 'image2pipe', '-vcodec', 'png', 'pipe:1']
        command = [executable('ffmpeg', self.settings.value('ffmpeg', '')), *arguments]
        self.thumbnail_job = ThumbnailJob(command, self.thumbnail_transform, self.thumbnail_signals,
                                          self.thumbnail_cancelled, self.thumbnail_token)
        QThreadPool.globalInstance().start(self.thumbnail_job)

    def thumbnail_ready(self, image, error, token):
        if token != self.thumbnail_token or self.thumbnail_cancelled.is_set() or self.preview is None:
            return
        if error:
            self.preview.setToolTip('Preview unavailable: ' + error)
        else:
            self.preview.set_image(QPixmap.fromImage(image))

    def start_scan(self):
        if not self.data or self.data.get('kind') == 'still' or self.scan:
            return
        self.decode_state_label.setText('Decode · Scanning…')
        self.set_health('Scanning media…', 'Decoding every audio and video track. You can cancel at any time.')
        self.scan_log = ''
        self.progress_buffer = ''
        self.cancelled = False
        self.scan_button.setEnabled(False)
        self.cancel_button.show()
        self.progress.setRange(0, 0)
        self.progress.show()
        self.diag_text.setPlainText('Scanning all audio and video tracks…\nThis may take as long as playback or longer. The original file is unchanged.\nSubtitles, attachments and visual/audio quality are not validated by this scan.')
        self.scan = QProcess(self)
        process = self.scan
        process.readyReadStandardError.connect(self.scan_errors)
        process.readyReadStandardOutput.connect(self.scan_progress)
        process.finished.connect(self.scan_finished)
        process.errorOccurred.connect(lambda error: self.process_error(process, 'FFmpeg'))
        process.start(executable('ffmpeg', self.settings.value('ffmpeg', '')), scan_args(self.path))
        self.status.setText('Full decode scan running…')

    def scan_errors(self):
        if self.scan:
            chunk = bytes(self.scan.readAllStandardError()).decode(errors='replace')
            self.scan_log = (self.scan_log + chunk)[-200000:]

    def scan_progress(self):
        if not self.scan:
            return
        self.progress_buffer += bytes(self.scan.readAllStandardOutput()).decode(errors='replace')
        lines = self.progress_buffer.split('\n')
        self.progress_buffer = lines.pop()
        for line in lines:
            if line.startswith('out_time_us='):
                try:
                    total = float(self.data.get('format', {}).get('duration', 0))
                    if total > 0:
                        self.progress.setRange(0, 100)
                        self.progress.setValue(min(99, int(float(line.split('=')[1]) / 1000000 / total * 100)))
                except ValueError:
                    pass

    def cancel_scan(self):
        if self.scan:
            self.cancelled = True
            self.scan.kill()

    def scan_finished(self, code, exit_status):
        self.scan_errors()
        if self.cancelled:
            result = 'Scan cancelled. Integrity was not verified.'
        elif code != 0:
            result = f'Decode scan failed (exit {code}). See decoder output below.'
        elif self.scan_log.strip():
            result = 'Scan completed with decoder warnings or errors. Review the output below.'
        else:
            result = 'Scan completed. FFmpeg reported no decoding warnings or errors in the audio/video streams.\nThis does not guarantee perceptual quality or validate subtitles and attachments.'
        self.decode_state_label.setText('Decode · '+('Cancelled / unverified' if self.cancelled else 'Failed' if code else 'Warnings' if self.scan_log.strip() else 'Passed'))
        self.set_health('Scan cancelled' if self.cancelled else 'Scan failed' if code != 0 else 'Review decoder findings' if self.scan_log.strip() else 'Decode scan complete', result.split('\n')[0])
        advice = '\n\n'.join(decoder_advice(self.scan_log))
        self.diag_text.setPlainText(result + ('\n\nInterpretation:\n' + advice if advice else '') + ('\n\nDecoder output (last 200,000 characters):\n' + self.scan_log if self.scan_log else ''))
        self.scan_log = self.diag_text.toPlainText()
        self.status.setText(result.split('\n')[0])
        self.progress.hide()
        self.cancel_button.hide()
        self.scan_button.setEnabled(True)
        self.scan.deleteLater()
        self.scan = None

    def report(self):
        if self.data and self.data.get('kind') == 'still':
            return {'delivery_preset': self.delivery.presets.currentData(), 'delivery_checks': self.delivery.checks, 'application': 'Frameglass 0.1.0', 'file': self.path, 'metadata': self.data, 'image_decode': 'Successful'}
        videos, audios = media_tracks(self.data) if self.data else ([], [])
        if audios and not videos:
            return {'delivery_preset': self.delivery.presets.currentData(), 'delivery_checks': self.delivery.checks, 'application': 'Frameglass 0.1.0', 'file': self.path, 'metadata': self.data,
                    'audio_tracks': [audio_overview(self.data, stream) for stream in audios],
                    'metadata_findings': findings(self.data), 'audio_analysis': self.analysis_summary,
                    'decode_scan': ('In progress; not verified.\n' + self.scan_log) if self.scan else (self.scan_log or 'Not run')}
        return {'delivery_preset': self.delivery.presets.currentData(), 'delivery_checks': self.delivery.checks, 'application': 'Frameglass 0.1.0', 'file': self.path, 'metadata': self.data,
                'timecode': clip_timecode(self.data) if self.data else None,
                'duration_timecode': duration_timecode(self.data) if self.data else None,
                'track_counts': dict(zip(('video', 'audio'), map(len, media_tracks(self.data)))) if self.data else None,
                'metadata_findings': findings(self.data) if self.data else [], 'audio_analysis': self.analysis_summary, 'decode_scan': ('In progress; not verified.\n' + self.scan_log) if self.scan else (self.scan_log or 'Not run')}

    def export_report(self):
        if not self.data:
            QMessageBox.information(self, 'Export report', 'Inspect a media file first.')
            return
        path, _ = QFileDialog.getSaveFileName(self, 'Export report', str(Path.home() / (Path(self.path).name + '.json')), 'JSON (*.json)')
        if path:
            try:
                Path(path).write_text(json.dumps(self.report(), indent=2, ensure_ascii=False), encoding='utf-8')
                self.status.setText('Report exported: ' + path)
            except OSError as error:
                QMessageBox.warning(self, 'Export failed', str(error))

    def copy_report(self):
        if self.data:
            QApplication.clipboard().setText(json.dumps(self.report(), indent=2, ensure_ascii=False))
            self.status.setText('Report copied to clipboard')

    def open_external(self):
        if self.path:
            if self.playback:
                self.playback.open_external()
            else:
                QDesktopServices.openUrl(QUrl.fromLocalFile(self.path))

    def reveal(self):
        if self.path:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(Path(self.path).parent)))

    def preferences(self):
        dialog = QDialog(self)
        dialog.setWindowTitle('Frameglass settings')
        dialog.setMinimumWidth(560)
        form = QFormLayout(dialog)
        form.addRow(label('Tool locations', 'eyebrow'))
        form.addRow(label('Leave blank to detect installed tools automatically.', 'muted'))
        manage=QPushButton('Check, install or update media tools…')
        manage.clicked.connect(lambda:(dialog.accept(),QTimer.singleShot(0,self.show_media_tools)))
        form.addRow(manage)
        fields = {}
        for name in ('ffprobe', 'ffmpeg'):
            row = QHBoxLayout()
            field = QLineEdit(self.settings.value(name, ''))
            field.setPlaceholderText(executable(name))
            fields[name] = field
            row.addWidget(field)
            button = QPushButton('Browse')
            button.clicked.connect(lambda checked=False, f=field: self.choose_tool(f))
            row.addWidget(button)
            form.addRow(name.upper(), row)
        color_mode = QComboBox()
        color_mode.addItem('Automatic SDR preview (PQ/HLG and BT.2020)', 'auto')
        color_mode.addItem('Native player rendering', 'native')
        color_mode.setCurrentIndex(1 if self.settings.value('color_mode', 'auto') == 'native' else 0)
        form.addRow('Display color', color_mode)
        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        form.addRow(buttons)
        if dialog.exec():
            self.settings.setValue('color_mode', color_mode.currentData())
            paths_changed=any(self.settings.value(name,'')!=field.text().strip() for name,field in fields.items())
            for name, field in fields.items():
                self.settings.setValue(name, field.text().strip())
            if paths_changed:
                self.settings.remove('tool_install_method')
                if self.tool_controller:
                    if self.tool_controller.dialog:self.tool_controller.dialog.method_initialized=False
                    self.tool_controller.check()
            if self.path:
                self.inspect(self.path)

    def choose_tool(self, field):
        path, _ = QFileDialog.getOpenFileName(self, 'Choose executable', '/opt/homebrew/bin')
        if path:
            field.setText(path)

    def about(self):
        QMessageBox.information(self, 'About Frameglass', 'Frameglass 0.1.0\nA quiet, detailed media inspector.\n\nBuilt with Python, Qt for Python, FFprobe and FFmpeg.\nInspection is local. Source media is never modified.')

    def closeEvent(self, event):
        self.delivery.cancel()
        if self.tool_controller:self.tool_controller.close()
        self.settings.setValue('window_geometry',self.saveGeometry())
        self.settings.setValue('splitter_state',self.splitter.saveState())
        self.settings.setValue('last_file',self.path or '')
        self.settings.setValue('last_tab',self.tabs.currentIndex())
        if self.analysis_dialog:self.analysis_dialog.close()
        if self.quality_workspace:
            self.quality_workspace.close()
        self.thumbnail_cancelled.set()
        self.dispose_playback()
        QApplication.instance().removeEventFilter(self)
        self.stop_process(self.probe)
        self.probe = None
        self.stop_process(self.scan)
        self.stop_process(self.thumbnail)
        self.thumbnail = None
        self.probe = None
        self.scan = None
        event.accept()


class MediaApplication(QApplication):
    """Handle Finder / Dock Open With events without argv emulation."""
    mediaOpened=Signal(str)
    def __init__(self,args):
        self.pending_media=[];self.ready_for_media=False
        super().__init__(args)
    def event(self,event):
        if event.type()==QEvent.FileOpen:
            path=event.file() or event.url().toLocalFile()
            if path:
                if self.ready_for_media:self.mediaOpened.emit(path)
                else:self.pending_media.append(path)
                event.accept();return True
        return super().event(event)


def main():
    app = MediaApplication(sys.argv)
    app.setApplicationName('Frameglass')
    app.setStyle('Fusion')
    app.setStyleSheet(STYLE)
    window = Window()
    def open_requested(path):
        source=Path(path).expanduser()
        if source.is_dir():window.set_folder(str(source))
        elif source.is_file():window.set_folder(str(source.parent));window.inspect(str(source))
        window.show();window.raise_();window.activateWindow()
    app.mediaOpened.connect(open_requested)
    app.ready_for_media=True
    window.show()
    if len(sys.argv) > 1:
        path = Path(sys.argv[1]).expanduser()
        if path.is_dir():
            window.set_folder(str(path))
        elif path.is_file():
            window.inspect(str(path))
    else:
        path=Path(window.settings.value('last_file',''))
        if path.is_file():window.inspect(str(path))
        try:window.tabs.setCurrentIndex(max(0,min(3,int(window.settings.value('last_tab',0)))))
        except (ValueError,TypeError):pass
    for path in app.pending_media:QTimer.singleShot(0,lambda p=path:open_requested(p))
    app.pending_media.clear()
    QTimer.singleShot(0,window.check_tools_on_startup)
    sys.exit(app.exec())
