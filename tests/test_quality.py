import os
os.environ['QT_QPA_PLATFORM']='offscreen'
import copy
import csv
import json
import shutil
import tempfile
import time
import unittest
from pathlib import Path
from threading import Event
from unittest.mock import patch
from PIL import Image
from PySide6.QtCore import QSettings,Qt
from PySide6.QtWidgets import QApplication,QDialog
from PySide6.QtTest import QTest
from frameglass.app import Window,STYLE
from frameglass.quality import compare,check_preset,preset_status,properties,inspect_metadata,InspectionJob,InspectionSignals,DEFAULT_PRESETS
from frameglass.workspace import QualityWorkspace,PresetEditor

app=QApplication.instance() or QApplication([]);app.setStyle('Fusion');app.setStyleSheet(STYLE)
def until(predicate):
    for _ in range(1000):
        if predicate():return
        QTest.qWait(15);time.sleep(.002)
    raise AssertionError('Quality operation timed out')

VIDEO={'format':{'format_name':'mov,mp4','duration':'60','size':'1000000'},'streams':[
 {'index':0,'codec_type':'video','codec_name':'h264','width':1920,'height':1080,'avg_frame_rate':'60000/1001','r_frame_rate':'60000/1001','field_order':'progressive','color_primaries':'bt709'},
 {'index':1,'codec_type':'audio','codec_name':'pcm_s24le','channels':2,'sample_rate':'48000','bits_per_raw_sample':'24'}]}

class QualityLogicTests(unittest.TestCase):
 def test_comparison_reports_all_tracks_and_unreported(self):
    candidate=copy.deepcopy(VIDEO);candidate['streams'].append({'codec_type':'audio','codec_name':'aac','channels':1,'sample_rate':'44100'})
    candidate['streams'][0]['color_transfer']='smpte2084'
    rows={r[0]:r for r in compare(VIDEO,candidate)}
    self.assertEqual(rows['Audio tracks'][3],'Different')
    self.assertEqual(rows['Audio 2 · Sample rate'][2],'44100')
    self.assertEqual(rows['Video 1 · Transfer'][3],'Not reported')
    self.assertEqual(rows['Video 1 · Average frame rate'][3],'Same')
 def test_preset_checks_every_audio_track_and_unknown(self):
    preset=DEFAULT_PRESETS[0];self.assertEqual(preset_status(check_preset(VIDEO,preset)),'Pass')
    candidate=copy.deepcopy(VIDEO);candidate['streams'][1].pop('sample_rate')
    self.assertEqual(preset_status(check_preset(candidate,preset)),'Unknown')
    candidate['streams'].append({'codec_type':'audio','sample_rate':'44100','channels':1})
    checks=check_preset(candidate,preset)
    self.assertEqual(preset_status(checks),'Mismatch');self.assertTrue(any(r['track']=='Audio 2' and r['status']=='Mismatch' for r in checks))
    candidate['streams']=candidate['streams'][:1]
    self.assertEqual(preset_status(check_preset(candidate,{'rules':{'sample_rate':48000}})),'Mismatch')
 def test_still_requirements_and_metadata_only(self):
    with tempfile.TemporaryDirectory() as temp:
     path=Path(temp)/'photo.png';Image.new('RGB',(320,240),'blue').save(path)
     data=inspect_metadata(str(path),'/opt/homebrew/bin/ffprobe',Event())
     self.assertEqual(data['kind'],'still');self.assertEqual(properties(data)['Dimensions'],'320 × 240')
     self.assertEqual(preset_status(check_preset(data,{'rules':{'type':'Image','width':320,'height':240}})),'Pass')
 def test_preset_editor_rejects_invalid_rate(self):
    editor=PresetEditor();editor.name.setText('Custom');editor.fields['fps'].setCurrentText('0/0');editor.save()
    self.assertIsNone(editor.result_preset)
    editor.fields['fps'].setCurrentText('30000/1001');editor.save();self.assertEqual(editor.result(),QDialog.Accepted)
    self.assertEqual(editor.result_preset['rules'],{'fps':'30000/1001'});editor.close()

class QualityWorkspaceTests(unittest.TestCase):
 def setUp(self):
    self.temp=tempfile.TemporaryDirectory(dir='/private/tmp');self.root=Path(self.temp.name)
    shutil.copy('/private/tmp/frameglass-test.mp4',self.root/'clip.mp4');(self.root/'broken.mp4').write_bytes(b'bad file')
    Image.new('RGB',(320,240),'#518a87').save(self.root/'image.png');(self.root/'notes.txt').write_text('Skip me')
    (self.root/'nested').mkdir();shutil.copy('/private/tmp/frameglass-test.mp4',self.root/'nested'/'another.mp4')
    self.settings=QSettings(str(self.root/'settings.ini'),QSettings.IniFormat)
    self.w=QualityWorkspace(self.settings);self.w.show()
 def tearDown(self):self.w.close();QTest.qWait(100);self.temp.cleanup()
 def test_concurrent_compare_and_export(self):
    self.w.set_compare_file(0,str(self.root/'clip.mp4'));self.w.set_compare_file(1,str(self.root/'image.png'))
    until(lambda:not self.w.jobs);self.assertTrue(all(self.w.compare_data));self.assertTrue(self.w.compare_results)
    self.w.differences.setChecked(True)
    for row in range(self.w.compare_table.rowCount()):
     self.assertEqual(self.w.compare_table.isRowHidden(row),self.w.compare_table.item(row,3).text()=='Same')
    self.w.preset_compare.setCurrentIndex(1);self.assertIn('Mismatch',self.w.check_summary.text())
    target=self.root/'comparison.json'
    with patch('frameglass.workspace.QFileDialog.getSaveFileName',return_value=(str(target),'')):self.w.export_comparison()
    exported=json.loads(target.read_text());self.assertEqual(exported['files'][0],str(self.root/'clip.mp4'));self.assertTrue(exported['checks'])
    self.w.grab().save('/private/tmp/frameglass-quality-compare.png')
 def test_recursive_batch_decode_filter_sort_and_csv(self):
    self.w.tabs.setCurrentIndex(1);self.w.batch_folder.setText(str(self.root));self.w.recursive.setChecked(True);self.w.decode.setChecked(True)
    self.w.start_batch();until(lambda:not self.w.jobs)
    self.assertEqual(len(self.w.batch_rows),4)
    self.assertEqual(sum(bool(r['error']) for r in self.w.batch_rows),1)
    self.assertTrue(any(r['decode']=='Decode passed' for r in self.w.batch_rows));self.assertTrue(any(r['decode']=='Image decoded' for r in self.w.batch_rows))
    self.w.batch_preset.setCurrentIndex(1);self.w.batch_filter.setText('unreadable')
    self.assertEqual(sum(not self.w.batch_table.isRowHidden(r) for r in range(4)),1)
    self.w.batch_filter.clear();self.w.batch_table.sortItems(0,Qt.DescendingOrder)
    for row in range(4):self.assertEqual(self.w.selected_batch_result(row)['path'],self.w.batch_table.item(row,13).text())
    self.w.batch_table.selectRow(0);self.assertTrue(self.w.batch_detail.toPlainText())
    target=self.root/'batch.csv'
    with patch('frameglass.workspace.QFileDialog.getSaveFileName',return_value=(str(target),'')):self.w.export_batch()
    with target.open(encoding='utf-8-sig') as file:rows=list(csv.reader(file))
    self.assertEqual(len(rows),5);self.assertIn('Preset details',rows[0]);self.w.grab().save('/private/tmp/frameglass-quality-batch.png')
 def test_cancel_ignores_late_results_and_preset_persistence(self):
    self.w.set_compare_file(0,str(self.root/'clip.mp4'));token=self.w.jobs['compare0'][0];self.w.cancel('compare')
    self.w.row_ready({'metadata':VIDEO,'error':'','path':'stale'},token);self.assertIsNone(self.w.compare_data[0])
    self.w.presets=[];self.w.save_presets();other=QualityWorkspace(self.settings);self.assertEqual(other.presets,[]);other.close()
 def test_cancelling_batch_retains_headers_without_decode_pass(self):
    from types import SimpleNamespace
    cancelled=Event();self.w.jobs['batch']=(999,SimpleNamespace(cancelled=cancelled))
    row={'path':str(self.root/'clip.mp4'),'metadata':VIDEO,'decode':'Scanning','decode_log':'','error':''}
    self.w.row_ready(row,999);self.w.cancel('batch')
    self.assertTrue(cancelled.is_set());self.assertEqual(self.w.batch_rows[0]['decode'],'Cancelled / unverified')
    late=dict(row,decode='Decode passed');self.w.row_ready(late,999)
    self.assertEqual(self.w.batch_rows[0]['decode'],'Cancelled / unverified')
 def test_main_window_workspace_and_open_result(self):
    inspector=Window(self.settings);inspector.show();inspector.inspect(str(self.root/'clip.mp4'));until(lambda:inspector.data is not None)
    inspector.open_quality(0);quality=inspector.quality_workspace;self.assertEqual(quality.compare_paths[0],inspector.path)
    inspector.open_quality_file(str(self.root/'image.png'));until(lambda:inspector.data is not None)
    self.assertEqual(inspector.data['kind'],'still');self.assertFalse(quality.isVisible());inspector.close();QTest.qWait(50)
