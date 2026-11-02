"""Compact overview delivery checks, shared with comparison and batch inspection."""
from PySide6.QtCore import Qt,QThreadPool,QPoint,Signal,QTimer
from PySide6.QtWidgets import QWidget,QHBoxLayout,QLabel,QComboBox,QCheckBox,QPushButton,QDialog,QVBoxLayout,QFrame,QToolButton,QSizePolicy
from shiboken6 import isValid
from .quality import load_presets,check_preset,preset_status,MEASURED_RULES,MeasurementJob,MeasurementSignals
from .core import executable

COLORS={'Pass':'#a4dacf','Mismatch':'#f2aaa9','Unknown':'#ecc78e'}


class DeliveryBar(QWidget):
    editRequested=Signal(str)
    def __init__(self,settings,parent=None):
        super().__init__(parent);self.settings=settings;self.data=None;self.path=None;self.bindings=[];self.checks=[];self.job=None;self.token=0
        self.setSizePolicy(QSizePolicy.Fixed,QSizePolicy.Fixed)
        row=QHBoxLayout(self);row.setContentsMargins(0,0,0,0);row.setSpacing(5)
        self.highlight=QCheckBox();self.highlight.setAccessibleName('Enable delivery spec highlighting')
        self.highlight.setStyleSheet('QCheckBox::indicator { width: 12px; height: 12px; border: 1px solid #657587; border-radius: 3px; background: #1b2028; } QCheckBox::indicator:checked { background: #adddce; border-color: #adddce; }')
        self.highlight.setToolTip('Evaluate the selected delivery preset and highlight matching, mismatched, and unknown specs.');row.addWidget(self.highlight)
        self.label=QToolButton();self.label.setText('Highlight Specs');self.label.setObjectName('deliveryToggle')
        self.label.setStyleSheet('QToolButton { background: transparent; border: none; padding: 4px 2px; font-size: 12px; color: #949eac; } QToolButton:hover { color: #e4e8ec; }')
        self.label.setToolTip('Choose a delivery preset and view the specs being evaluated');self.label.clicked.connect(self.show_details);row.addWidget(self.label)
        self.popup=QFrame(self,Qt.Popup);self.popup.setObjectName('deliveryPopup')
        self.popup.setStyleSheet('QFrame#deliveryPopup { background: #191e25; border: 1px solid #3a4756; border-radius: 9px; }')
        layout=QVBoxLayout(self.popup);layout.setContentsMargins(14,12,14,12);layout.setSpacing(10)
        header=QHBoxLayout();header.addWidget(QLabel('Delivery preset'))
        self.presets=QComboBox();self.presets.setMinimumWidth(180);self.presets.setAccessibleName('Overview delivery preset');header.addWidget(self.presets,1);layout.addLayout(header)
        self.summary=QLabel('Not checked');self.summary.setTextFormat(Qt.PlainText);layout.addWidget(self.summary)
        from .workspace import table
        self.check_table=table(['Track','Requirement','Expected','Actual','Result']);self.check_table.setMinimumHeight(190);self.check_table.setMaximumHeight(330)
        self.check_table.setColumnWidth(0,75);self.check_table.setColumnWidth(1,195);self.check_table.setColumnWidth(2,95);self.check_table.setColumnWidth(3,110);layout.addWidget(self.check_table)
        self.measure=QPushButton('Measure audio');self.measure.clicked.connect(self.toggle_measurement);layout.addWidget(self.measure);self.measure.hide()
        footer=QHBoxLayout()
        legend=QLabel('Green · match    Red · mismatch    Amber · unknown');legend.setObjectName('muted');footer.addWidget(legend,1)
        self.edit_button=QPushButton('Edit delivery presets…');self.edit_button.setObjectName('quiet');self.edit_button.clicked.connect(self.edit_presets);footer.addWidget(self.edit_button);layout.addLayout(footer)
        self.popup.setMinimumWidth(660);self.popup.resize(660,340)
        self.signals=MeasurementSignals(self);self.signals.ready.connect(self.measured,Qt.QueuedConnection);self.signals.progress.connect(self.progress,Qt.QueuedConnection)
        self.presets.currentIndexChanged.connect(self.selected);self.highlight.toggled.connect(self.toggled)
        enabled=settings.value('delivery_highlight',False,type=bool)
        self.reload();self.highlight.setChecked(enabled)

    def reload(self):
        name=self.presets.currentText() if self.presets.count() else self.settings.value('overview_delivery_preset','')
        self.presets.blockSignals(True);self.presets.clear();self.presets.addItem('No delivery preset',None)
        for preset in load_presets(self.settings):self.presets.addItem(preset['name'],preset)
        self.presets.setCurrentIndex(max(0,self.presets.findText(name)));self.presets.blockSignals(False);self.refresh()

    def toggled(self,enabled):
        self.refresh()
        if enabled and not self.presets.currentData():self.show_details()

    def selected(self,*args):
        self.settings.setValue('overview_delivery_preset',self.presets.currentText());self.refresh()

    def bind(self,widget,keys,track='File'):
        self.bindings.append((widget,set(keys),track,widget.toolTip()))

    def set_media(self,path,data):
        if path!=self.path:self.cancel()
        self.path,self.data=path,data;self.refresh()

    def refresh(self,*args):
        self.settings.setValue('delivery_highlight',self.highlight.isChecked())
        preset=self.presets.currentData();self.checks=check_preset(self.data,preset) if self.data and preset and self.highlight.isChecked() else []
        state=preset_status(self.checks);counts={s:sum(r['status']==s for r in self.checks) for s in COLORS}
        self.summary.setText((state+(f" · {counts['Mismatch']} off" if counts['Mismatch'] else '')) if self.highlight.isChecked() else 'Highlighting off · check the box to evaluate')
        self.summary.setStyleSheet('color: '+COLORS.get(state,'#9baabb')+';')
        self.summary.setToolTip(' · '.join(f'{n} {s.lower()}' for s,n in counts.items())+' · Click for all requirements')
        self.label.setToolTip((self.presets.currentText()+' · '+state+'\n' if preset else '')+'Choose a delivery preset and view the specs being evaluated')
        self.refresh_table()
        measured=bool(preset and MEASURED_RULES.intersection(preset['rules']))
        self.measure.setVisible(measured);self.measure.setEnabled(bool(self.data and any(s.get('codec_type')=='audio' for s in self.data.get('streams',[]))))
        if not self.job:self.measure.setText('Measure audio')
        for widget,keys,track,tip in self.bindings:
            if not isValid(widget):continue
            checks=[r for r in self.checks if r['key'] in keys and r['track']==track]
            active=self.highlight.isChecked() and checks
            status=preset_status(checks)
            widget.setProperty('deliveryStatus',status if active else '')
            widget.setStyleSheet(('color: '+COLORS[status]+'; background-color: '+{'Pass':'#203a33','Mismatch':'#42282d','Unknown':'#403728'}[status]+'; border-radius: 3px;') if active else '')
            widget.setToolTip(tip+('\n' if tip and active else '')+('\n'.join(f"{r['status']}: {r['field']} · expected {r['expected']} · actual {r['actual']}" for r in checks) if active else ''))

    def refresh_table(self):
        from .workspace import QualityWorkspace
        from .quality import RULES
        preset=self.presets.currentData()
        rows=[(r['track'],r['field'],r['expected'],r['actual'],r['status']) for r in self.checks]
        if preset and not rows:
            rows=[('All applicable',RULES.get(key,key),str(value),'—','Not checked') for key,value in preset['rules'].items()]
        QualityWorkspace.fill_table(self.check_table,rows)

    def edit_presets(self):
        name=self.presets.currentText() if self.presets.currentData() else ''
        self.popup.hide()
        QTimer.singleShot(0,lambda:self.editRequested.emit(name))

    def show_details(self):
        if self.popup.isVisible():self.popup.hide();return
        self.refresh_table();self.popup.adjustSize()
        point=self.mapToGlobal(QPoint(self.width()-self.popup.width(),self.height()+7))
        screen=self.screen().availableGeometry()
        point.setX(max(screen.left()+8,min(point.x(),screen.right()-self.popup.width()-8)))
        point.setY(max(screen.top()+8,min(point.y(),screen.bottom()-self.popup.height()-8)))
        self.popup.move(point);self.popup.show();self.popup.raise_()

    def hideEvent(self,event):
        self.popup.hide();super().hideEvent(event)

    def toggle_measurement(self):
        if self.job:self.cancel();return
        if not self.data:return
        from .timecode import media_tracks
        count=len(media_tracks(self.data)[1])
        if not count:return
        self.token+=1;self.job=MeasurementJob(self.path,count,executable('ffmpeg',self.settings.value('ffmpeg','')),self.signals,self.token)
        self.measure.setText('Cancel measurement');self.measure.setToolTip('Reads each complete audio track; audition Solo/Mute does not affect measurements.');QThreadPool.globalInstance().start(self.job)

    def progress(self,message,token):
        if token==self.token and self.job:self.summary.setText(message)

    def measured(self,result,error,token):
        if token!=self.token or not self.job:return
        self.job=None
        if result and self.data is not None:self.data['audio_measurements']=result
        self.refresh()
        if error:self.summary.setText('Measurement failed');self.summary.setToolTip(error)

    def cancel(self):
        self.popup.hide()
        if self.job:self.job.cancelled.set();self.job=None;self.token+=1
        self.refresh()
