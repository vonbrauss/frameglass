"""A quiet workspace for comparison, delivery checks and batch inspection."""
import csv
import json
from copy import deepcopy
from pathlib import Path
from fractions import Fraction

from PySide6.QtCore import Qt, Signal, QThreadPool
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (QDialog,QWidget,QVBoxLayout,QHBoxLayout,QFormLayout,QTabWidget,QPushButton,QLabel,QLineEdit,
    QComboBox,QCheckBox,QTableWidget,QTableWidgetItem,QHeaderView,QFileDialog,QProgressBar,QMessageBox,QDialogButtonBox,QAbstractItemView,QTextEdit,QScrollArea)
from .quality import DEFAULT_PRESETS,RULES,LIMIT_RULES,MEASURED_RULES,load_presets,InspectionSignals,InspectionJob,compare,check_preset,preset_status,media_type
from .core import executable,duration,rate,findings
from .timecode import media_tracks


def note(text):
    label=QLabel(text);label.setObjectName('muted');label.setWordWrap(True);label.setTextFormat(Qt.PlainText)
    return label


def table(headers):
    widget=QTableWidget(0,len(headers));widget.setHorizontalHeaderLabels(headers)
    widget.setEditTriggers(QAbstractItemView.NoEditTriggers);widget.setSelectionBehavior(QAbstractItemView.SelectRows)
    widget.setAlternatingRowColors(True);widget.setWordWrap(False);widget.verticalHeader().hide()
    widget.horizontalHeader().setSectionResizeMode(QHeaderView.Interactive)
    widget.horizontalHeader().setStretchLastSection(True);widget.setSortingEnabled(True);widget.sortItems(0,Qt.AscendingOrder)
    return widget


class ValueItem(QTableWidgetItem):
    def __init__(self,value,sort_value=None):
        super().__init__(str(value));self.sort_value=sort_value
        self.setToolTip(str(value))
    def __lt__(self,other):
        if self.sort_value is not None and getattr(other,'sort_value',None) is not None:
            return self.sort_value<other.sort_value
        return self.text().casefold()<other.text().casefold()


def cell(value):
    item=ValueItem(value)
    if value in ('Different','Decode warnings','Unknown','Not reported','Warnings') or 'Missing audio' in str(value):item.setForeground(QColor('#ecc78e'))
    elif value in ('Mismatch','Unreadable','Decode failed'):item.setForeground(QColor('#f2aaa9'))
    elif value in ('Pass','Same','Decode passed','Image decoded'):item.setForeground(QColor('#a4dacf'))
    return item


class PresetEditor(QDialog):
    def __init__(self,preset=None,parent=None):
        super().__init__(parent);self.setWindowTitle('Delivery preset');self.resize(480,600)
        layout=QVBoxLayout(self);layout.addWidget(note('Only enabled requirements are checked. These are your delivery rules, not a certification or a decode scan.'))
        form=QFormLayout();self.name=QLineEdit((preset or {}).get('name',''));form.addRow('Name',self.name)
        options={'type':['Video','Audio','Image'],'fps':['24000/1001','24','25','30000/1001','30','50','60000/1001','60'],
                 'scan':['Progressive','Interlaced · top field first','Interlaced · bottom field first'],
                 'primaries':['bt709','bt2020'],'transfer':['bt709','arib-std-b67','smpte2084','iec61966-2-1'],
                 'matrix':['bt709','bt2020nc','rgb'],'pixel_format':['yuv420p','yuv422p10le','yuv420p10le','rgb24'],'video_codec':['h264','hevc','prores','dnxhd'],
                 'audio_codec':['pcm_s24le','pcm_s16le','aac','mp3','flac'],
                 'width':['1920','3840','1280'],'height':['1080','2160','720'],'audio_tracks':['0','1','2','8'],
                 'channels':['1','2','6','8'],'sample_rate':['48000','44100','96000'],'bits':['16','24','32'],
                 'container':['mov','mp4','mxf','matroska','wav','aiff','flac','mp3'],
                 'timecode_required':['Present'],'timecode_mode':['Drop-frame','Non-drop'],
                 'channel_layout':['mono','stereo','5.1','5.1(side)','7.1'],
                 'duration_min':['0','30','60'],'duration_max':['30','60','120'],
                 'bitrate_min':['10','50'],'bitrate_max':['50','100','250'],
                 'video_bitrate_min':['10','50'],'video_bitrate_max':['50','100','250'],
                 'audio_bitrate_min':['0.128','1.536'],'audio_bitrate_max':['0.320','2.304'],
                 'loudness_min':['-24','-23','-16'],'loudness_max':['-22','-23','-14'],'true_peak_max':['-1','-2','-3']}
        self.fields={}
        for key,label in RULES.items():
            field=QComboBox();field.setEditable(True);field.addItem('Any','');field.addItems(options[key])
            value=(preset or {}).get('rules',{}).get(key)
            if value is not None:field.setCurrentText(str(value))
            self.fields[key]=field;form.addRow(label,field)
        scroll=QScrollArea();scroll.setWidgetResizable(True);body=QWidget();body.setLayout(form);scroll.setWidget(body);layout.addWidget(scroll,1)
        self.error=note('');layout.addWidget(self.error)
        buttons=QDialogButtonBox(QDialogButtonBox.Save|QDialogButtonBox.Cancel);buttons.accepted.connect(self.save);buttons.rejected.connect(self.reject);layout.addWidget(buttons)
        self.result_preset=None
    def save(self):
        rules={}
        for key,field in self.fields.items():
            value=field.currentText().strip()
            if not value or value=='Any':continue
            if key in ('width','height','audio_tracks','channels','sample_rate','bits'):
                try:
                    value=int(value)
                    if value<0 or (value==0 and key!='audio_tracks'):raise ValueError()
                except ValueError:self.error.setText(f'{RULES[key]} must be a positive whole number (audio tracks may be zero).');return
            if key in LIMIT_RULES:
                import math
                try:
                    value=float(value)
                    if not math.isfinite(value) or (key.startswith(('duration_','bitrate_','video_bitrate_','audio_bitrate_')) and value<0):raise ValueError()
                except ValueError:self.error.setText(f'{RULES[key]} must be a finite number; duration and bitrate cannot be negative.');return
            if key=='fps':
                from .quality import numeric
                if numeric(value) is None or numeric(value)<=0:self.error.setText('Enter a positive frame rate, such as 30000/1001.');return
            if key=='type' and value not in ('Video','Audio','Image'):self.error.setText('Media type must be Video, Audio or Image.');return
            if key=='timecode_required' and value!='Present':self.error.setText('Embedded timecode must be Present, or Any to skip this rule.');return
            if key=='timecode_mode' and value not in ('Drop-frame','Non-drop'):self.error.setText('Choose Drop-frame or Non-drop.');return
            rules[key]=value
        for prefix in ('duration','bitrate','video_bitrate','audio_bitrate','loudness'):
            if prefix+'_min' in rules and prefix+'_max' in rules and rules[prefix+'_min']>rules[prefix+'_max']:
                self.error.setText(f'{prefix.replace("_"," ").capitalize()}: minimum cannot exceed maximum.');return
        if not self.name.text().strip() or not rules:self.error.setText('Give the preset a name and at least one requirement.');return
        self.result_preset={'name':self.name.text().strip(),'rules':rules};self.accept()


class QualityWorkspace(QDialog):
    openRequested=Signal(str)
    presetsChanged=Signal()
    def __init__(self,settings,parent=None):
        super().__init__(parent);self.settings=settings;self.setWindowTitle('Frameglass · Quality workspace');self.resize(1180,760)
        self.setWindowModality(Qt.WindowModal)
        self.current_path=None;self.current_data=None;self.compare_data=[None,None];self.compare_paths=['',''];self.compare_results=[]
        self.batch_rows=[];self.jobs={};self.next_token=0
        self.signals=InspectionSignals(self);self.signals.row.connect(self.row_ready,Qt.QueuedConnection)
        self.signals.progress.connect(self.progress_ready,Qt.QueuedConnection);self.signals.done.connect(self.job_done,Qt.QueuedConnection)
        self.presets=load_presets(settings)
        self.layout=QVBoxLayout(self);self.layout.setContentsMargins(20,16,20,16);self.layout.setSpacing(10)
        heading=QLabel('Quality workspace');heading.setObjectName('title');self.layout.addWidget(heading)
        self.layout.addWidget(note('Compare media, check delivery requirements, and inspect a folder. Source files are never modified.'))
        self.tabs=QTabWidget();self.layout.addWidget(self.tabs,1)
        self.build_compare();self.build_batch();self.build_presets();self.refresh_presets()

    def update_current(self,path,data):self.current_path,self.current_data=path,data

    def build_compare(self):
        page=QWidget();layout=QVBoxLayout(page);self.compare_inputs=[]
        for index,name in enumerate(('A · Reference','B · Candidate')):
            row=QHBoxLayout();row.addWidget(QLabel(name));field=QLineEdit();field.setReadOnly(True);field.setPlaceholderText('Choose a media file');row.addWidget(field,1)
            choose=QPushButton('Choose…');choose.clicked.connect(lambda checked=False,i=index:self.choose_compare(i));row.addWidget(choose)
            current=QPushButton('Use loaded file');current.clicked.connect(lambda checked=False,i=index:self.use_current(i));row.addWidget(current)
            layout.addLayout(row);self.compare_inputs.append(field)
        controls=QHBoxLayout();self.differences=QCheckBox('Differences only');self.differences.toggled.connect(self.filter_compare);controls.addWidget(self.differences)
        controls.addStretch();self.compare_cancel=QPushButton('Cancel');self.compare_cancel.clicked.connect(lambda:self.cancel('compare'));self.compare_cancel.setEnabled(False);controls.addWidget(self.compare_cancel)
        export=QPushButton('Export comparison…');export.clicked.connect(self.export_comparison);controls.addWidget(export);layout.addLayout(controls)
        self.compare_status=note('Choose two files to compare every reported media track.');layout.addWidget(self.compare_status)
        self.compare_table=table(['Property','A · Reference','B · Candidate','Result']);self.compare_table.setColumnWidth(0,250);self.compare_table.setColumnWidth(1,300);self.compare_table.setColumnWidth(2,300)
        layout.addWidget(self.compare_table,1)
        self.preset_compare=QComboBox();self.preset_compare.currentIndexChanged.connect(self.refresh_checks)
        row=QHBoxLayout();row.addWidget(QLabel('Delivery preset'));row.addWidget(self.preset_compare,1);layout.addLayout(row)
        self.check_summary=note('No preset selected.');layout.addWidget(self.check_summary)
        self.check_table=table(['File / track','Requirement','Expected','Actual','Result']);self.check_table.setMaximumHeight(190);layout.addWidget(self.check_table)
        self.tabs.addTab(page,'Compare')

    def choose_compare(self,index):
        path,_=QFileDialog.getOpenFileName(self,'Choose reference' if index==0 else 'Choose candidate',str(Path(self.current_path).parent) if self.current_path else '')
        if path:self.set_compare_file(index,path)

    def use_current(self,index):
        if not self.current_path or not self.current_data:self.compare_status.setText('Load a file in the inspector first.');return
        self.cancel(f'compare{index}');self.compare_paths[index]=self.current_path;self.compare_data[index]=self.current_data
        self.compare_inputs[index].setText(self.current_path);self.refresh_compare()

    def set_compare_file(self,index,path):
        self.cancel(f'compare{index}');self.compare_paths[index]=str(path);self.compare_data[index]=None;self.compare_inputs[index].setText(str(path))
        self.refresh_compare();self.start_job(f'compare{index}',paths=[str(path)])
        self.compare_status.setText('Reading '+Path(path).name+'…')

    def refresh_compare(self):
        self.compare_results=compare(*self.compare_data) if all(self.compare_data) else []
        self.fill_table(self.compare_table,self.compare_results)
        if self.compare_results:
            changed=sum(r[3]=='Different' for r in self.compare_results);missing=sum(r[3]=='Not reported' for r in self.compare_results)
            self.compare_status.setText(f'{changed} differences · {missing} fields with unreported values · Metadata comparison, not visual or audio equivalence')
        else:self.compare_status.setText('Choose both files to compare.')
        self.filter_compare();self.refresh_checks()

    def filter_compare(self):
        for row in range(self.compare_table.rowCount()):self.compare_table.setRowHidden(row,self.differences.isChecked() and self.compare_table.item(row,3).text()=='Same')

    def refresh_checks(self):
        preset=self.selected_preset(self.preset_compare);rows=[];summary=[]
        if preset:
            for index,data in enumerate(self.compare_data):
                if data:
                    checks=check_preset(data,preset);summary.append(f"{'A' if index==0 else 'B'}: {preset_status(checks)}")
                    rows.extend([(f"{'A' if index==0 else 'B'} · {r['track']}",r['field'],r['expected'],r['actual'],r['status']) for r in checks])
        self.fill_table(self.check_table,rows);self.check_table.setVisible(bool(preset))
        self.check_summary.setText(' · '.join(summary) if summary else 'Choose a preset to check reported metadata against your requirements.')

    def build_batch(self):
        page=QWidget();layout=QVBoxLayout(page)
        row=QHBoxLayout();self.batch_folder=QLineEdit();self.batch_folder.setPlaceholderText('Choose a folder');row.addWidget(self.batch_folder,1)
        choose=QPushButton('Choose folder…');choose.clicked.connect(self.choose_batch_folder);row.addWidget(choose);layout.addLayout(row)
        row=QHBoxLayout();self.recursive=QCheckBox('Include subfolders');row.addWidget(self.recursive)
        self.decode=QCheckBox('Full decode scan');self.decode.setToolTip('Optional. Reads the complete payload of every file and may take a long time.');row.addWidget(self.decode)
        self.batch_preset=QComboBox();self.batch_preset.currentIndexChanged.connect(self.refresh_batch);row.addWidget(self.batch_preset,1)
        self.batch_start=QPushButton('Inspect folder');self.batch_start.setObjectName('primary');self.batch_start.clicked.connect(self.start_batch);row.addWidget(self.batch_start)
        self.batch_cancel=QPushButton('Cancel');self.batch_cancel.setEnabled(False);self.batch_cancel.clicked.connect(lambda:self.cancel('batch'));row.addWidget(self.batch_cancel);layout.addLayout(row)
        self.batch_progress=QProgressBar();self.batch_progress.setRange(0,1);self.batch_progress.setValue(0);layout.addWidget(self.batch_progress)
        self.batch_status=note('Metadata inspection is quick. Loudness presets measure every audio track in full; full decode scanning is optional.');layout.addWidget(self.batch_status)
        row=QHBoxLayout();self.batch_filter=QLineEdit();self.batch_filter.setPlaceholderText('Filter name, type, codec, HDR, missing audio, warnings…');self.batch_filter.textChanged.connect(self.filter_batch);row.addWidget(self.batch_filter,1)
        export=QPushButton('Export CSV…');export.clicked.connect(self.export_batch);row.addWidget(export);layout.addLayout(row)
        self.batch_table=table(['File','Type','Duration','Dimensions','Frame rate','Video codec','Audio tracks','Channels','Sample rate','Color','Headers','Decode','Preset','Path'])
        self.batch_table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        for column,width in enumerate((210,70,108,110,85,88,90,75,95,100,95,100,85)):
            self.batch_table.setColumnWidth(column,width)
        self.batch_table.setColumnHidden(13,True)
        self.batch_table.setColumnHidden(7,True);self.batch_table.setColumnHidden(8,True)
        audio_columns=QCheckBox('Audio details');audio_columns.toggled.connect(lambda checked:[self.batch_table.setColumnHidden(i,not checked) for i in (7,8)])
        row.insertWidget(1,audio_columns)
        self.batch_table.cellDoubleClicked.connect(self.open_batch_row);self.batch_table.itemSelectionChanged.connect(self.show_batch_detail);layout.addWidget(self.batch_table,1)
        self.batch_detail=QTextEdit();self.batch_detail.setReadOnly(True);self.batch_detail.setMaximumHeight(125);self.batch_detail.setPlaceholderText('Select a file for warnings and preset details. Double-click to open it in the inspector.');layout.addWidget(self.batch_detail)
        self.tabs.addTab(page,'Batch')

    def choose_batch_folder(self):
        folder=QFileDialog.getExistingDirectory(self,'Inspect media folder',self.batch_folder.text())
        if folder:self.batch_folder.setText(folder)

    def start_batch(self):
        folder=Path(self.batch_folder.text()).expanduser()
        if not folder.is_dir():self.batch_status.setText('Choose an available folder.');return
        self.cancel('batch');self.batch_rows=[];self.batch_detail.clear();self.refresh_batch()
        self.start_job('batch',folder=str(folder),recursive=self.recursive.isChecked(),decode=self.decode.isChecked(),measure=bool(self.selected_preset(self.batch_preset) and MEASURED_RULES.intersection(self.selected_preset(self.batch_preset)['rules'])))

    def start_job(self,kind,**kwargs):
        self.next_token+=1;token=self.next_token
        job=InspectionJob(self.signals,token,ffprobe=executable('ffprobe',self.settings.value('ffprobe','')),ffmpeg=executable('ffmpeg',self.settings.value('ffmpeg','')),**kwargs)
        self.jobs[kind]=(token,job);self.set_busy(kind,True);QThreadPool.globalInstance().start(job)

    def cancel(self,kind):
        if kind=='compare':
            for side in ('compare0','compare1'):self.cancel(side)
            return
        active=self.jobs.pop(kind,None)
        if active:active[1].cancelled.set()
        self.set_busy(kind,False)
        if active:
            if kind=='batch':
                for result in self.batch_rows:
                    if result['decode']=='Scanning':result['decode']='Cancelled / unverified'
                    if (result.get('metadata') or {}).get('measurement_status')=='Measuring':result['metadata']['measurement_status']='Cancelled / unverified'
                self.refresh_batch()
            (self.batch_status if kind=='batch' else self.compare_status).setText('Cancelled · partial results retained' if kind=='batch' else 'Comparison loading cancelled')

    def job_kind(self,token):
        return next((kind for kind,(current,_) in self.jobs.items() if current==token),None)

    def row_ready(self,result,token):
        kind=self.job_kind(token)
        if kind and kind.startswith('compare'):
            self.compare_data[int(kind[-1])]=result['metadata'];self.refresh_compare()
            if result['error']:self.compare_status.setText(result['error'])
        elif kind=='batch':
            index=next((i for i,r in enumerate(self.batch_rows) if r['path']==result['path']),None)
            if index is None:self.batch_rows.append(result)
            else:self.batch_rows[index]=result
            self.append_batch(result)

    def progress_ready(self,message,current,total,token):
        if self.job_kind(token)=='batch':
            self.batch_progress.setRange(0,total);self.batch_progress.setValue(current)
            self.batch_status.setText(f'{current}/{total} · {message}' if total else message)

    def job_done(self,message,token):
        kind=self.job_kind(token)
        if not kind:return
        self.jobs.pop(kind);self.set_busy(kind,False)
        if kind=='batch':self.batch_status.setText(f'{message} · {len(self.batch_rows)} files · '+self.batch_counts())

    def set_busy(self,kind,busy):
        if kind=='batch':
            self.batch_start.setEnabled(not busy);self.batch_cancel.setEnabled(busy)
            self.recursive.setEnabled(not busy);self.decode.setEnabled(not busy)
        else:self.compare_cancel.setEnabled(busy or any(k.startswith('compare') for k in self.jobs))

    def batch_values(self,result):
        data=result['metadata'];path=result['path']
        if not data:return [Path(path).name,'Unreadable','','','','','','','','','Unreadable',result['decode'],'Not checked',path]
        video,audio=media_tracks(data);image=data.get('image',{});fmt=data.get('format',{});v=video[0] if video else {}
        colors=sorted({str(s.get('color_transfer','')) for s in video})
        color='HDR · '+', '.join(colors) if any(c in ('smpte2084','arib-std-b67') for c in colors) else ', '.join(sorted({s.get('color_primaries','Not reported') for s in video})) if video else image.get('profile','—')
        warnings=[title for level,title,_ in findings(data) if level in ('Warning','Issue')] if not image else []
        header='Warnings' if warnings else 'Headers read'
        if video and not audio:header+=' · Missing audio'
        if data.get('measurement_error'):header+=' · Measurement failed'
        preset=self.selected_preset(self.batch_preset);status=preset_status(check_preset(data,preset)) if preset else 'Not checked'
        return [Path(path).name,media_type(data),duration(fmt.get('duration')) if fmt.get('duration') else '—',
                f"{image.get('width',v.get('width'))} × {image.get('height',v.get('height'))}" if video or image else '—',
                rate(v.get('avg_frame_rate') or v.get('r_frame_rate')) if video else '—',v.get('codec_name','—'),str(len(audio)),
                ', '.join(str(s.get('channels','?')) for s in audio) or '—',', '.join(sorted({str(s.get('sample_rate','?')) for s in audio})) or '—',
                color,header,result['decode'],status,path]

    def append_batch(self,result):
        self.batch_table.setSortingEnabled(False)
        row=next((r for r in range(self.batch_table.rowCount()) if self.batch_table.item(r,0) and self.batch_table.item(r,0).data(Qt.UserRole)==result['path']),None)
        if row is None:
            row=self.batch_table.rowCount();self.batch_table.insertRow(row)
        values=self.batch_values(result)
        for column,value in enumerate(values):
            item=cell(value)
            if column==0:item.setData(Qt.UserRole,result['path'])
            if column in (2,4,6):
                data=result['metadata'] or {};video,_=media_tracks(data)
                try:item.sort_value=float(data.get('format',{}).get('duration',0)) if column==2 else float(Fraction(video[0].get('avg_frame_rate','0'))) if column==4 and video else int(value) if column==6 else -1
                except (ValueError,ZeroDivisionError,TypeError):item.sort_value=-1
            self.batch_table.setItem(row,column,item)
        self.batch_table.setSortingEnabled(True);self.filter_batch()

    def refresh_batch(self):
        self.batch_table.setRowCount(0)
        for result in self.batch_rows:self.append_batch(result)
        self.show_batch_detail()

    def batch_counts(self):
        values=[self.batch_values(r) for r in self.batch_rows]
        rates=sorted({v[4] for v in values if v[4] and v[4]!='—'})
        return ('Frame rates: '+', '.join(rates)+' · ' if rates else '')+f"{sum(v[1]=='Unreadable' for v in values)} unreadable · {sum(v[11] in ('Decode failed','Decode warnings') for v in values)} decode issues · {sum(v[12]=='Mismatch' for v in values)} preset mismatches"

    def filter_batch(self):
        query=self.batch_filter.text().casefold().strip()
        for row in range(self.batch_table.rowCount()):
            text=' '.join(self.batch_table.item(row,col).text() for col in range(self.batch_table.columnCount())).casefold()
            self.batch_table.setRowHidden(row,not all(word in text for word in query.split()))

    def selected_batch_result(self,row):
        item=self.batch_table.item(row,0)
        return next((r for r in self.batch_rows if item and r['path']==item.data(Qt.UserRole)),None)

    def show_batch_detail(self):
        result=self.selected_batch_result(self.batch_table.currentRow())
        if not result:self.batch_detail.clear();return
        lines=[result['path'],result['error'],result['decode']]
        if result['metadata']:
            if result['metadata'].get('measurement_status'):lines.append('Audio measurement: '+result['metadata']['measurement_status'])
            if result['metadata'].get('measurement_error'):lines.append(result['metadata']['measurement_error'])
            if result['metadata'].get('kind')!='still':lines.extend(f'{level} · {title}: {detail}' for level,title,detail in findings(result['metadata']))
            preset=self.selected_preset(self.batch_preset)
            if preset:
                lines.extend(f"{r['status']} · {r['track']} · {r['field']}: expected {r['expected']}, reported {r['actual']}" for r in check_preset(result['metadata'],preset))
        if result['decode_log']:lines.append(result['decode_log'])
        self.batch_detail.setPlainText('\n'.join(line for line in lines if line))

    def open_batch_row(self,row,column):
        result=self.selected_batch_result(row)
        if result:self.openRequested.emit(result['path'])

    def build_presets(self):
        page=QWidget();layout=QVBoxLayout(page);layout.addWidget(note('Example presets are starting points. Edit them to match your actual delivery specification. Every reported media track is checked; missing values remain Unknown.'))
        row=QHBoxLayout();self.preset_list=QComboBox();self.preset_list.currentIndexChanged.connect(self.show_preset);row.addWidget(self.preset_list,1)
        for title,slot in [('New…',lambda:self.edit_preset(False)),('Edit…',lambda:self.edit_preset(True)),('Duplicate',self.duplicate_preset),('Delete',self.delete_preset)]:
            button=QPushButton(title);button.clicked.connect(slot);row.addWidget(button)
        layout.addLayout(row);self.preset_table=table(['Requirement','Expected']);layout.addWidget(self.preset_table,1)
        layout.addWidget(note('Preset Pass means the reported metadata matches the configured rules. Loudness and true-peak rules require completed measurements. Presets do not verify visual quality or broadcaster acceptance.'))
        self.tabs.addTab(page,'Delivery presets')

    def refresh_presets(self):
        for combo,empty in [(self.preset_list,False),(self.preset_compare,True),(self.batch_preset,True)]:
            selected=combo.currentText();combo.blockSignals(True);combo.clear()
            if empty:combo.addItem('No delivery preset',None)
            for preset in self.presets:combo.addItem(preset['name'],preset)
            index=combo.findText(selected);combo.setCurrentIndex(index if index>=0 else 0);combo.blockSignals(False)
        self.show_preset();self.refresh_checks();self.refresh_batch()

    def selected_preset(self,combo):return combo.currentData()
    def show_preset(self):
        preset=self.selected_preset(self.preset_list)
        self.fill_table(self.preset_table,[(RULES.get(k,k),v) for k,v in preset.get('rules',{}).items()] if preset else [])

    def save_presets(self):
        self.settings.setValue('delivery_presets',json.dumps(self.presets));self.refresh_presets();self.presetsChanged.emit()

    def edit_preset(self,existing):
        index=self.preset_list.currentIndex();preset=self.presets[index] if existing and index>=0 else None
        editor=PresetEditor(preset,self)
        if editor.exec()!=QDialog.Accepted:return
        new=editor.result_preset
        if any(p['name']==new['name'] and (not existing or i!=index) for i,p in enumerate(self.presets)):
            QMessageBox.information(self,'Preset name','Choose a unique preset name.');return
        if preset:self.presets[index]=new
        else:self.presets.append(new)
        self.save_presets();self.preset_list.setCurrentText(new['name'])

    def duplicate_preset(self):
        preset=self.selected_preset(self.preset_list)
        if not preset:return
        copied=deepcopy(preset);name=copied['name']+' copy'
        while any(p['name']==name for p in self.presets):name+=' copy'
        copied['name']=name;self.presets.append(copied);self.save_presets();self.preset_list.setCurrentText(name)

    def delete_preset(self):
        index=self.preset_list.currentIndex()
        if index<0:return
        if QMessageBox.question(self,'Delete preset',f"Delete ‘{self.presets[index]['name']}’?")!=QMessageBox.Yes:return
        self.presets.pop(index);self.save_presets()

    @staticmethod
    def fill_table(widget,rows):
        widget.setSortingEnabled(False);widget.setRowCount(len(rows))
        for row,values in enumerate(rows):
            for column,value in enumerate(values):widget.setItem(row,column,cell(value))
        widget.setSortingEnabled(True)

    def export_comparison(self):
        if not self.compare_results:self.compare_status.setText('Choose two readable files before exporting.');return
        path,_=QFileDialog.getSaveFileName(self,'Export comparison','comparison.json','JSON (*.json)')
        if not path:return
        preset=self.selected_preset(self.preset_compare)
        data={'files':self.compare_paths,'comparison':[dict(zip(('property','reference','candidate','result'),r)) for r in self.compare_results],
              'preset':preset,'checks':[check_preset(d,preset) for d in self.compare_data] if preset else []}
        try:Path(path).write_text(json.dumps(data,indent=2,ensure_ascii=False));self.compare_status.setText('Comparison exported: '+path)
        except OSError as error:self.compare_status.setText(str(error))

    def export_batch(self):
        if not self.batch_rows:self.batch_status.setText('Inspect a folder before exporting.');return
        path,_=QFileDialog.getSaveFileName(self,'Export batch results','media-inspection.csv','CSV (*.csv)')
        if not path:return
        try:
            with open(path,'w',newline='',encoding='utf-8-sig') as file:
                writer=csv.writer(file);writer.writerow([self.batch_table.horizontalHeaderItem(i).text() for i in range(self.batch_table.columnCount())]+['Error / decode messages','Preset details'])
                for row in range(self.batch_table.rowCount()):
                    result=self.selected_batch_result(row)
                    # CSV cells beginning with spreadsheet formula characters are escaped.
                    preset=self.selected_preset(self.batch_preset)
                    checks=check_preset(result['metadata'],preset) if preset and result['metadata'] else []
                    values=[self.batch_table.item(row,col).text() for col in range(self.batch_table.columnCount())]+['\n'.join(v for v in (result['error'],result['decode_log'],(result.get('metadata') or {}).get('measurement_error','')) if v), '; '.join(f"{r['status']}: {r['track']} {r['field']} expected {r['expected']}, reported {r['actual']}" for r in checks)]
                    writer.writerow(["'"+v if v.startswith(('=','+','-','@','\t','\r')) else v for v in values])
            self.batch_status.setText('All results exported: '+path)
        except OSError as error:self.batch_status.setText(str(error))

    def reject(self):
        for kind in list(self.jobs):self.cancel(kind)
        super().reject()

    def closeEvent(self,event):
        for kind in list(self.jobs):self.cancel(kind)
        event.accept()
