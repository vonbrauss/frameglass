import os
os.environ['QT_QPA_PLATFORM']='offscreen'
import json
import tempfile
import time
import unittest
from pathlib import Path
import numpy as np
from PySide6.QtCore import QByteArray,QSettings,QPointF
from PySide6.QtGui import QImage,QColor,QPixmap
from PySide6.QtWidgets import QApplication
from PySide6.QtMultimedia import QAudioFormat,QAudioBuffer
from PySide6.QtTest import QTest
from frameglass.app import Window,STYLE
from frameglass.inspection_tools import LevelMeter,PixelLabel,Histogram,AudioAnalysisJob,AnalysisSignals,parse_audio_analysis

app=QApplication.instance() or QApplication([]);app.setStyle('Fusion');app.setStyleSheet(STYLE)
def until(predicate):
 for _ in range(750):
  if predicate():return
  QTest.qWait(20);time.sleep(.002)
 raise AssertionError('Viewer operation timed out')

class InspectionToolTests(unittest.TestCase):
 def test_meter_uses_pcm_samples_and_pixel_readout(self):
    fmt=QAudioFormat();fmt.setSampleRate(48000);fmt.setChannelCount(2);fmt.setSampleFormat(QAudioFormat.Int16)
    samples=np.array([[16384,8192],[-16384,-8192]],dtype='<i2')
    meter=LevelMeter();meter.set_buffer(QAudioBuffer(QByteArray(samples.tobytes()),fmt))
    self.assertAlmostEqual(meter.levels[0],-6.0206,places=3);self.assertAlmostEqual(meter.levels[1],-12.0412,places=3)
    image=QImage(20,20,QImage.Format_RGBA8888);image.fill(QColor(32,64,128,255))
    picture=PixelLabel(image);picture.setPixmap(QPixmap.fromImage(image))
    self.assertIn('Preview RGB 32, 64, 128',picture.pixel_at(QPointF(5,5)))
    histogram=Histogram(image);self.assertEqual(int(np.argmax(histogram.hist[0])),32)
    picture.close();meter.close();histogram.close()
 def test_actual_audio_measurement_and_full_band_waveform(self):
    signals=AnalysisSignals();received=[];signals.ready.connect(lambda data,error,token:received.append((data,error)))
    job=AudioAnalysisJob('/private/tmp/frameglass-test.mp4',0,'/opt/homebrew/bin/ffmpeg',signals,1,2)
    job.run();self.assertTrue(received);data,error=received[0]
    self.assertEqual(error,'');self.assertIn('integrated_lufs',data);self.assertIn('true_peak_dbfs',data)
    self.assertGreater(max(abs(p) for pair in data['waveform'] for p in pair),.01)
    self.assertLessEqual(len(data['waveform']),1500)
    self.assertLess(max(abs(p) for pair in data['waveform'] for p in pair),1.1)
 def test_silence_and_loudness_summary_parser(self):
    result=parse_audio_analysis('silence_start: 0\nsilence_end: 1.5 | silence_duration: 1.5\nSummary:\n I: -18.0 LUFS\n LRA: 2.0 LU\n Peak: -1.2 dBFS\n')
    self.assertEqual(result['integrated_lufs'],'-18.0');self.assertEqual(result['silence_regions'][0]['duration_seconds'],1.5)
 def test_analysis_dialog_report_and_close_cleanup(self):
    with tempfile.TemporaryDirectory(dir='/private/tmp') as temp:
     settings=QSettings(str(Path(temp)/'prefs.ini'),QSettings.IniFormat);settings.setValue('volume',0)
     w=Window(settings);w.show();w.inspect('/private/tmp/frameglass-test.mp4');until(lambda:w.data is not None)
     w.open_audio_analysis();until(lambda:w.analysis_dialog and w.analysis_dialog.result_data is not None)
     until(lambda:w.analysis_summary is not None)
     self.assertIn('integrated_lufs',w.report()['audio_analysis'])
     self.assertTrue(w.analysis_dialog.waveform.points)
     w.analysis_dialog.close();QTest.qWait(50);self.assertIsNone(w.analysis_dialog)
     self.assertIn('integrated_lufs',w.report()['audio_analysis']);w.close();QTest.qWait(50)
 def test_restore_recents_copy_health_and_fullscreen(self):
    with tempfile.TemporaryDirectory(dir='/private/tmp') as temp:
     settings=QSettings(str(Path(temp)/'prefs.ini'),QSettings.IniFormat);settings.setValue('volume',0)
     w=Window(settings);w.show();w.inspect('/private/tmp/frameglass-test.mp4');until(lambda:w.data is not None)
     recent=json.loads(settings.value('recent_files'));self.assertEqual(recent[0],w.path)
     w.copy_summary();self.assertIn('Audio 1',app.clipboard().text());self.assertIn('Decode · Not scanned',w.decode_state_label.text())
     QTest.qWait(50)
     w.playback.set_guides(True);self.assertTrue(w.playback.guides.isVisible())
     w.playback.show_fullscreen();QTest.qWait(50);self.assertIsNotNone(w.playback.fullscreen_dialog)
     w.playback.fullscreen_dialog.close();QTest.qWait(50);self.assertIsNone(w.playback.fullscreen_dialog)
     w.playback.toggle();until(lambda:w.playback.frame_seen);w.start_scan();until(lambda:w.scan is None)
     self.assertEqual(w.decode_state_label.text(),'Decode · Passed')
     w.resize(1150,800);w.tabs.setCurrentIndex(2);w.close();QTest.qWait(100)
     self.assertEqual(settings.value('last_tab'),2);self.assertEqual(settings.value('last_file'),'/private/tmp/frameglass-test.mp4')
     restored=Window(settings);self.assertTrue(settings.value('window_geometry'));self.assertGreaterEqual(restored.width(),restored.minimumWidth());restored.close();QTest.qWait(50)
