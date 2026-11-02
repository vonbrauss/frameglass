import os
os.environ['QT_QPA_PLATFORM']='offscreen'
import json
from pathlib import Path
import subprocess
import tempfile
import time
import unittest
from PIL import Image
from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication,QLabel
from PySide6.QtTest import QTest
from frameglass.app import Window,STYLE
from frameglass.audio import audio_overview
from frameglass.timecode import media_tracks

app=QApplication.instance() or QApplication([]);app.setStyle('Fusion');app.setStyleSheet(STYLE)
def until(predicate):
    for _ in range(500):
        if predicate():return
        QTest.qWait(20);time.sleep(.002)
    raise AssertionError('Audio operation timed out')

class AudioTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp=tempfile.TemporaryDirectory(dir='/private/tmp');cls.root=Path(cls.temp.name)
        cls.cover=cls.root/'cover.jpg';Image.new('RGB',(300,300),'#56867d').save(cls.cover)
        cls.album=cls.root/'music.mp3'
        subprocess.run(['/opt/homebrew/bin/ffmpeg','-v','error','-f','lavfi','-i','sine=frequency=440:duration=3','-i',str(cls.cover),'-map','0:a','-map','1:v','-c:a','libmp3lame','-b:a','192k','-c:v','copy','-id3v2_version','3','-disposition:v','attached_pic','-metadata','title=Evening Tide','-metadata','artist=Studio Sessions','-metadata','album=Coast','-metadata','genre=Ambient',str(cls.album)],check=True)
        cls.multi=cls.root/'dual.mka'
        subprocess.run(['/opt/homebrew/bin/ffmpeg','-v','error','-f','lavfi','-i','sine=frequency=440:sample_rate=48000:duration=3','-f','lavfi','-i','sine=frequency=880:sample_rate=44100:duration=3','-map','0:a','-map','1:a','-c:a','pcm_s24le',str(cls.multi)],check=True)
    @classmethod
    def tearDownClass(cls):cls.temp.cleanup()
    def setUp(self):
        self.w=Window(QSettings(str(self.root/'prefs.ini'),QSettings.IniFormat));self.w.settings.setValue('volume',0);self.w.show()
    def tearDown(self):self.w.close();QTest.qWait(60)
    def test_cover_art_tags_and_elapsed_playback(self):
        self.w.inspect(str(self.album));until(lambda:self.w.data is not None);until(lambda:self.w.preview.source is not None)
        self.assertEqual(len(media_tracks(self.w.data)[0]),0)
        self.assertTrue(self.w.playback.audio_only);self.assertEqual(self.w.tabs.tabText(1),'Audio tracks')
        QTest.qWait(100)
        texts=[v.text() for v in self.w.findChildren(QLabel) if v.isVisible()]
        self.assertIn('Evening Tide',texts);self.assertIn('Studio Sessions · Coast',texts)
        self.assertNotIn('CONTAINER',texts);self.assertNotIn('FRAME RATE',texts);self.assertNotIn('TRACK SUMMARY',texts)
        self.assertNotIn('timecode',self.w.report())
        self.assertEqual(len(self.w.report()['audio_tracks']),1)
        p=self.w.playback;p.toggle();until(lambda:p.player.position()>150)
        self.assertIsNone(p.video);self.assertFalse(p.position_label.text().startswith(('TC','Rel')))
        self.assertEqual(p.player.activeVideoTrack(),-1)
        p.pause();p.seek_to(1200);until(lambda:p.player.position()>=1200)
        self.w.grab().save('/private/tmp/frameglass-audio-overview.png')
        self.w.start_scan();until(lambda:self.w.scan is None);self.assertIn('no decoding warnings',self.w.scan_log)
    def test_track_arrows_change_details_and_playback(self):
        self.w.inspect(str(self.multi));until(lambda:self.w.data is not None)
        self.w.audio_selector.setCurrentIndex(1);self.w.playback.toggle()
        until(lambda:self.w.playback.player.position()>100)
        self.assertEqual(self.w.playback.player.activeAudioTrack(),1)
        self.assertEqual(self.w.playback.selected_audio_track,1)
        self.assertTrue(any(v.text()=='44.1 kHz' and v.isVisible() for v in self.w.findChildren(QLabel)))
        self.w.inspect('/private/tmp/frameglass-test.mp4');until(lambda:self.w.data is not None)
        self.assertFalse(self.w.playback.audio_only);self.assertEqual(self.w.tabs.tabText(1),'Tracks')
    def test_bwf_and_unknown_bitrate_are_not_inferred(self):
        data={'format':{'bit_rate':'9999999','tags':{'time_reference':'48000','originator':'Field recorder'}},'streams':[{'codec_type':'audio','sample_rate':'48000','channels':2,'bits_per_sample':24}]}
        overview=audio_overview(data,data['streams'][0])
        self.assertEqual(dict(overview['facts'])['BITRATE'],'Not reported')
        self.assertEqual(dict(overview['technical'])['Bit depth'],'24-bit')
        self.assertEqual(dict(overview['recording'])['BWF start'],'00:00:01.000 · sample-based')
        data['streams'][0]['bits_per_sample']=0
        self.assertEqual(dict(audio_overview(data,data['streams'][0])['technical'])['Bit depth'],'Not reported')
