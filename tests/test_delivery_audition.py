import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
import copy
import json
import tempfile
import unittest
from pathlib import Path
from threading import Event
import numpy as np
from PySide6.QtCore import QSettings,QByteArray,Qt
from PySide6.QtMultimedia import QAudioFormat,QAudioBuffer
from PySide6.QtWidgets import QApplication,QLabel
from PySide6.QtTest import QTest
from frameglass.quality import check_preset,preset_status,measure_loudness,InspectionJob,InspectionSignals
from frameglass.audition import channel_assignments,pcm_samples,monitor_mix,correlation
from frameglass.delivery import DeliveryBar
from frameglass.workspace import PresetEditor
from frameglass.app import Window

app=QApplication.instance() or QApplication([])
MEDIA={'format':{'format_name':'mov,mp4,m4a,3gp,3g2,mj2','tags':{'major_brand':'qt  ','timecode':'01:00:00:00'},'duration':'60','bit_rate':'50000000'},
       'streams':[{'codec_type':'video','width':1920,'height':1080,'avg_frame_rate':'25/1','r_frame_rate':'25/1','bit_rate':'48000000'},
                  {'codec_type':'audio','channels':2,'channel_layout':'stereo','sample_rate':'48000','bit_rate':'1536000'}]}

class DeliveryTests(unittest.TestCase):
 def test_limits_container_timecode_and_all_audio_tracks(self):
    rules={'container':'mov','duration_min':60,'duration_max':60,'bitrate_min':50,'bitrate_max':50,
           'video_bitrate_max':48,'audio_bitrate_max':1.536,'timecode_required':'Present','timecode_mode':'Non-drop','channel_layout':'stereo'}
    self.assertEqual(preset_status(check_preset(MEDIA,{'rules':rules})),'Pass')
    data=copy.deepcopy(MEDIA);data['streams'].append({'codec_type':'audio','channels':1,'channel_layout':'mono','bit_rate':'2000000'})
    checks=check_preset(data,{'rules':rules});self.assertTrue(any(r['track']=='Audio 2' and r['status']=='Mismatch' for r in checks))
    data['format']['tags']={};checks=check_preset(data,{'rules':{'container':'mov','timecode_required':'Present'}})
    self.assertEqual(checks[0]['status'],'Unknown');self.assertEqual(checks[1]['status'],'Mismatch')
 def test_measured_rules_never_pass_from_tags_or_unmeasured_audio(self):
    data=copy.deepcopy(MEDIA);data['streams'][1]['tags']={'integrated_lufs':'-23'}
    preset={'rules':{'loudness_min':-24,'loudness_max':-22,'true_peak_max':-1}}
    self.assertEqual(preset_status(check_preset(data,preset)),'Unknown')
    data['audio_measurements']={'1':{'integrated_lufs':'-23.0','true_peak_dbfs':'-1.0'}}
    self.assertEqual(preset_status(check_preset(data,preset)),'Pass')
    data['audio_measurements']['1']['true_peak_dbfs']='-0.9'
    self.assertEqual(preset_status(check_preset(data,preset)),'Mismatch')
    data['audio_measurements']['1']={'integrated_lufs':'nan','true_peak_dbfs':'-inf'}
    checks=check_preset(data,preset);self.assertEqual(checks[0]['status'],'Unknown');self.assertEqual(checks[-1]['status'],'Pass')
 def test_real_loudness_and_batch_measurements(self):
    measured=measure_loudness('/private/tmp/frameglass-test.mp4',0,'/opt/homebrew/bin/ffmpeg',Event())
    self.assertIn('integrated_lufs',measured);self.assertIn('true_peak_dbfs',measured)
    signals=InspectionSignals();rows=[];signals.row.connect(lambda row,token:rows.append(row))
    job=InspectionJob(signals,1,paths=['/private/tmp/frameglass-test.mp4'],measure=True);job.run()
    self.assertEqual(rows[-1]['error'],'');self.assertIn('1',rows[-1]['metadata']['audio_measurements'])
 def test_measurement_failure_is_separate_from_decode_failure(self):
    from unittest.mock import patch
    signals=InspectionSignals();rows=[];signals.row.connect(lambda row,token:rows.append(row))
    job=InspectionJob(signals,1,paths=['/private/tmp/frameglass-test.mp4'],measure=True)
    with patch('frameglass.quality.measure_loudness',side_effect=ValueError('Measurement test failure')):job.run()
    self.assertEqual(rows[-1]['decode'],'Not scanned');self.assertEqual(rows[-1]['metadata']['measurement_status'],'Failed')
    self.assertIn('Measurement test failure',rows[-1]['metadata']['measurement_error'])
 def test_editor_limits_validation_and_persistence(self):
    editor=PresetEditor();editor.name.setText('Delivery');editor.fields['duration_min'].setCurrentText('61');editor.fields['duration_max'].setCurrentText('60');editor.save()
    self.assertIsNone(editor.result_preset);editor.fields['duration_min'].setCurrentText('30');editor.fields['loudness_max'].setCurrentText('nan');editor.save();self.assertIsNone(editor.result_preset)
    editor.fields['loudness_max'].setCurrentText('-22');editor.save();self.assertEqual(editor.result_preset['rules']['duration_max'],60);editor.close()
 def test_overview_highlighting_state_unknown_and_reset(self):
    with tempfile.TemporaryDirectory() as temp:
     settings=QSettings(str(Path(temp)/'settings.ini'),QSettings.IniFormat)
     settings.setValue('delivery_presets',json.dumps([{'name':'Test','rules':{'width':1920,'height':720,'channel_layout':'stereo','loudness_max':-22}}]))
     bar=DeliveryBar(settings);width=QLabel('1920');height=QLabel('1080');loudness=QLabel('—');bar.bind(width,['width'],'Video 1');bar.bind(height,['height'],'Video 1');bar.bind(loudness,['loudness_max'],'Audio 1')
     bar.set_media('a.mov',copy.deepcopy(MEDIA));bar.presets.setCurrentIndex(1);bar.highlight.setChecked(True)
     self.assertEqual(width.property('deliveryStatus'),'Pass');self.assertEqual(height.property('deliveryStatus'),'Mismatch');self.assertEqual(loudness.property('deliveryStatus'),'Unknown')
     bar.highlight.setChecked(False);self.assertEqual(width.styleSheet(),'');self.assertEqual(height.property('deliveryStatus'),'')
     bar.close()

 def test_compact_control_opens_specs_without_toggling_evaluation(self):
    with tempfile.TemporaryDirectory() as temp:
     settings=QSettings(str(Path(temp)/'settings.ini'),QSettings.IniFormat)
     settings.setValue('delivery_presets',json.dumps([{'name':'Compact','rules':{'width':1920}}]))
     bar=DeliveryBar(settings);bar.set_media('a.mov',copy.deepcopy(MEDIA));bar.presets.setCurrentIndex(1);bar.show();QTest.qWait(10)
     self.assertEqual(bar.highlight.text(),'');self.assertEqual(bar.label.text(),'Highlight Specs');self.assertEqual(bar.checks,[])
     QTest.mouseClick(bar.label,Qt.LeftButton);QTest.qWait(10)
     self.assertTrue(bar.popup.isVisible());self.assertFalse(bar.highlight.isChecked());self.assertEqual(bar.check_table.item(0,4).text(),'Not checked')
     bar.popup.hide();QTest.mouseClick(bar.highlight,Qt.LeftButton);QTest.qWait(10)
     self.assertEqual(bar.checks[0]['status'],'Pass');self.assertFalse(bar.popup.isVisible())
     QTest.mouseClick(bar.label,Qt.LeftButton);QTest.qWait(10);self.assertEqual(bar.check_table.item(0,4).text(),'Pass')
     bar.close();self.assertFalse(bar.popup.isVisible());QTest.qWait(10)
 def test_main_overview_binds_specs_and_reports_current_checks(self):
    with tempfile.TemporaryDirectory() as temp:
     settings=QSettings(str(Path(temp)/'settings.ini'),QSettings.IniFormat)
     settings.setValue('delivery_presets',json.dumps([{'name':'Main','rules':{'width':640,'height':720,'fps':'24','sample_rate':48000,'channels':2,'timecode_required':'Present'}}]))
     w=Window(settings);w.show();w.inspect('/private/tmp/frameglass-test.mp4')
     for _ in range(500):
      if w.data and w.playback:break
      QTest.qWait(10)
     self.assertIsNotNone(w.data);w.delivery.presets.setCurrentIndex(1);w.delivery.highlight.setChecked(True);QTest.qWait(20)
     values=[(keys,track,widget.property('deliveryStatus')) for widget,keys,track,tip in w.delivery.bindings]
     self.assertTrue(any('fps' in keys and status=='Pass' for keys,track,status in values))
     self.assertTrue(any('height' in keys and status=='Mismatch' for keys,track,status in values))
     self.assertTrue(any('sample_rate' in keys and status=='Pass' and track=='Audio 1' for keys,track,status in values))
     self.assertTrue(any('timecode_required' in keys and status=='Mismatch' for keys,track,status in values))
     self.assertEqual(w.report()['delivery_preset']['name'],'Main');self.assertTrue(w.report()['delivery_checks'])
     import wave
     audio_path=str(Path(temp)/'audio.wav')
     with wave.open(audio_path,'wb') as sound:sound.setnchannels(2);sound.setsampwidth(2);sound.setframerate(48000);sound.writeframes(bytes(4096*4))
     w.inspect(audio_path)
     for _ in range(500):
      if w.data and w.playback:break
      QTest.qWait(10)
     self.assertNotIn('audio_measurements',w.data);self.assertTrue(all(track!='Video 1' for _,_,track,_ in w.delivery.bindings));w.close();QTest.qWait(30)

class AuditionTests(unittest.TestCase):
 def test_assignments_do_not_invent_unspecified_layouts(self):
    self.assertEqual(channel_assignments({'channels':2}),['Ch 1','Ch 2'])
    self.assertEqual(channel_assignments({'channels':6,'channel_layout':'5.1(side)'}),['FL','FR','FC','LFE','SL','SR'])
 def test_pcm_solo_mute_and_safe_multichannel_fold_down(self):
    fmt=QAudioFormat();fmt.setSampleRate(48000);fmt.setChannelCount(2);fmt.setSampleFormat(QAudioFormat.Int16)
    raw=np.array([[16384,8192],[-16384,-8192]],dtype='<i2');samples=pcm_samples(QAudioBuffer(QByteArray(raw.tobytes()),fmt))
    np.testing.assert_allclose(samples,[[.5,.25],[-.5,-.25]])
    solo=monitor_mix(samples,['FL','FR'],{1},set());np.testing.assert_allclose(solo,[[.25,.25],[-.25,-.25]])
    muted=monitor_mix(samples,['FL','FR'],set(),{0});np.testing.assert_allclose(muted,[[0,.25],[0,-.25]])
    np.testing.assert_allclose(monitor_mix(samples,['FL','FR'],{0},{0}),np.zeros((2,2)))
    surround=monitor_mix(np.ones((32,6),np.float32),['FL','FR','FC','LFE','SL','SR'],set(),set());self.assertLessEqual(float(surround.max()),1)
    mono=monitor_mix(samples,['FL','FR'],set(),set(),True);np.testing.assert_allclose(mono[:,0],mono[:,1])
 def test_phase_correlated_reversed_uncorrelated_and_silence(self):
    signal=np.sin(np.arange(4800)*.07);quad=np.cos(np.arange(4800)*.07)
    self.assertAlmostEqual(correlation(np.column_stack((signal,signal)),0,1),1)
    self.assertAlmostEqual(correlation(np.column_stack((signal,-signal)),0,1),-1)
    self.assertLess(abs(correlation(np.column_stack((signal,quad)),0,1)),.02)
    self.assertIsNone(correlation(np.zeros((64,2)),0,1))
 def test_channel_panel_reset_on_track_switch_and_close(self):
    from frameglass.player import ClipPlayer
    from frameglass.design import Preview
    with tempfile.TemporaryDirectory() as temp:
     settings=QSettings(str(Path(temp)/'settings.ini'),QSettings.IniFormat);data=copy.deepcopy(MEDIA);data['streams'].append({'codec_type':'audio','channels':1,'channel_layout':'mono'})
     player=ClipPlayer(Preview(),'/private/tmp/frameglass-test.mp4',settings,data);player.show_audio_channels();panel=player.channel_panel;panel.solo[0].setChecked(True)
     self.assertTrue(player.audition_active());player.set_audio_track(1);self.assertFalse(player.audition_active());self.assertEqual(len(panel.solo),1)
     panel.mute[0].setChecked(True);panel.close();self.assertFalse(player.audition_active());player.dispose();player.deleteLater();QTest.qWait(20)

if __name__=='__main__':unittest.main()
