import os
os.environ['QT_QPA_PLATFORM'] = 'offscreen'
import tempfile
import subprocess
import unittest
import time
import wave
from pathlib import Path
from unittest.mock import patch
from PySide6.QtCore import Qt, QSettings, QPoint
from PySide6.QtMultimedia import QMediaPlayer
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
from frameglass.app import Window, STYLE
from frameglass.design import Preview
from frameglass.player import ClipPlayer

app = QApplication.instance() or QApplication([])
app.setStyle('Fusion')
app.setStyleSheet(STYLE)


def until(predicate, timeout=8000):
    for _ in range(timeout // 20):
        if predicate():
            return
        QTest.qWait(20)
        time.sleep(.002)
    raise AssertionError('Playback operation timed out')


class PlaybackTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir='/private/tmp')
        self.settings = QSettings(self.temp.name + '/prefs.ini', QSettings.IniFormat)
        self.settings.setValue('volume', 0)
        self.w = Window(self.settings)
        self.w.set_folder(self.temp.name)
        self.w.show()
        self.w.inspect('/private/tmp/frameglass-test.mp4')
        until(lambda: self.w.data is not None)
        self.p = self.w.playback

    def tearDown(self):
        self.w.close()
        QTest.qWait(60)
        self.temp.cleanup()

    def test_switch_clips_during_playback(self):
        import shutil
        copies = [Path(self.temp.name) / f'clip-{i}.mp4' for i in range(2)]
        for path in copies:
            shutil.copy('/private/tmp/frameglass-test.mp4', path)
        for path in copies * 3:
            old = self.w.playback
            old.toggle()
            until(lambda: old.frame_seen)
            self.w.inspect(str(path))
            self.assertTrue(old.disposed)
            until(lambda: self.w.data is not None)
            self.assertEqual(self.w.path, str(path))
            self.assertIsNone(self.w.playback.player)
        self.w.playback.toggle()
        until(lambda: self.w.playback.frame_seen)

    def test_actual_play_pause_seek_and_decoded_video(self):
        self.assertIsNone(self.p.player)  # Inspection never autoplays.
        self.p.toggle()
        until(lambda: self.p.player.position() > 150 and self.p.frame_seen)
        self.assertEqual(self.p.player.playbackState(), QMediaPlayer.PlayingState)
        self.p.toggle()
        self.assertEqual(self.p.player.playbackState(), QMediaPlayer.PausedState)
        until(lambda: self.p.player.isSeekable())
        self.p.seek_to(1200)
        until(lambda: self.p.player.position() >= 1200)
        self.assertTrue(self.p.position_label.text().startswith('Rel '))
        QTest.mouseClick(self.p.seek, Qt.LeftButton, pos=QPoint(self.p.seek.width() // 2, self.p.seek.height() // 2))
        self.assertAlmostEqual(self.p.player.position(), 1000, delta=100)
        self.p.volume.setValue(25)
        self.assertAlmostEqual(self.p.audio.volume(), 0.25)
        self.assertEqual(self.settings.value('volume'), 25)

    def test_jkl_shuttle_reverse_and_frame_step(self):
        self.w.tree.setFocus()
        QTest.keyClick(self.w.tree, Qt.Key_L)
        until(lambda: self.p.player is not None and self.p.player.position() > 200)
        self.assertEqual(self.p.speed, 1)
        QTest.keyClick(self.w.tree, Qt.Key_L)
        self.assertEqual(self.p.speed, 2)
        QTest.keyClick(self.w.tree, Qt.Key_K)
        self.assertEqual(self.p.speed, 0)
        self.p.seek_to(1500)
        until(lambda: self.p.player.position() >= 1500)
        QTest.keyClick(self.w.tree, Qt.Key_J)
        self.assertEqual(self.p.speed, -1)
        until(lambda: self.p.player.position() < 1400)
        QTest.keyClick(self.w.tree, Qt.Key_K)
        self.assertFalse(self.p.reverse_timer.isActive())
        self.p.seek_to(1000)
        QTest.keyPress(self.w.tree, Qt.Key_K)
        QTest.keyClick(self.w.tree, Qt.Key_L)
        QTest.keyRelease(self.w.tree, Qt.Key_K)
        self.assertAlmostEqual(self.p.player.position(), 1042, delta=2)
        self.assertEqual(self.p.speed, 0)
        self.w.search.setFocus()
        QTest.keyClicks(self.w.search, 'jkl')
        self.assertEqual(self.w.search.text(), 'jkl')
        self.assertEqual(self.p.speed, 0)

    def test_switching_file_releases_player(self):
        self.p.toggle()
        until(lambda: self.p.player.position() > 100)
        old = self.p
        self.w.inspect('/private/tmp/frameglass-timecode.mov')
        self.assertTrue(old.disposed)
        self.assertTrue(old.player.source().isEmpty())
        until(lambda: self.w.data is not None)
        self.assertTrue(self.w.playback.position_label.text().startswith('TC 01:00:00:00'))
        self.assertIsNone(self.w.playback.player)

    def test_unsupported_media_and_external_fallback(self):
        path = Path(self.temp.name) / 'bad.mp4'
        path.write_bytes(b'not playable')
        panel = ClipPlayer(Preview(), str(path), self.settings, {})
        panel.resize(400, 240)
        panel.show()
        try:
            panel.toggle()
            until(lambda: panel.failed)
            self.assertFalse(panel.play_button.isEnabled())
            self.assertTrue(panel.error_label.isVisible())
            with patch('frameglass.player.QDesktopServices.openUrl') as opened:
                panel.external_button.click()
                self.assertEqual(opened.call_args.args[0].toLocalFile(), str(path))
        finally:
            panel.dispose()
            panel.close()

    def test_audio_only(self):
        path = Path(self.temp.name) / 'audio.wav'
        with wave.open(str(path), 'wb') as output:
            output.setnchannels(1)
            output.setsampwidth(2)
            output.setframerate(48000)
            output.writeframes(bytes(48000 * 2 * 2))
        self.w.inspect(str(path))
        until(lambda: self.w.data is not None)
        panel = self.w.playback
        panel.toggle()
        until(lambda: panel.player.position() > 100)
        self.assertTrue(panel.player.hasAudio())
        self.assertFalse(panel.player.hasVideo())
        self.assertTrue(panel.position_label.text().startswith('Time '))

    def test_replay_after_end_and_speed_ladder(self):
        self.p.toggle()
        until(lambda: self.p.player.isSeekable())
        self.p.seek_to(1850)
        until(lambda: self.p.player.mediaStatus() == QMediaPlayer.EndOfMedia)
        self.p.step(-1)
        self.assertAlmostEqual(self.p.player.position(), 1917, delta=2)
        self.p.seek_to(1950)
        self.p.shuttle(1, accelerate=False)
        until(lambda: self.p.player.mediaStatus() == QMediaPlayer.EndOfMedia)
        self.p.toggle()
        until(lambda: self.p.player.playbackState() == QMediaPlayer.PlayingState and self.p.player.position() < 500)
        self.p.pause()
        self.p.shuttle(1)
        for speed in (2, 4, 8, 8):
            self.p.shuttle(1)
            self.assertEqual(self.p.speed, speed)
        self.p.pause()

    def test_managed_hlg_and_audio_arrow_navigation(self):
        path = Path(self.temp.name) / 'hlg-two-tracks.mov'
        subprocess.run(['ffmpeg', '-nostdin', '-v', 'error', '-f', 'lavfi', '-i', 'testsrc2=size=320x180:rate=24',
                        '-f', 'lavfi', '-i', 'sine=frequency=440:sample_rate=48000',
                        '-f', 'lavfi', '-i', 'sine=frequency=880:sample_rate=48000',
                        '-map', '0:v', '-map', '1:a', '-map', '2:a', '-t', '2',
                        '-vf', 'setparams=color_primaries=bt2020:color_trc=arib-std-b67:colorspace=bt2020nc:range=limited',
                        '-c:v', 'libx264', '-pix_fmt', 'yuv420p10le', '-color_primaries', 'bt2020',
                        '-color_trc', 'arib-std-b67', '-colorspace', 'bt2020nc', '-c:a', 'aac', str(path)], check=True)
        self.w.inspect(str(path))
        until(lambda: self.w.data is not None and self.w.preview.source is not None)
        selector = self.w.audio_selector
        self.assertEqual(selector.count(), 2)
        self.assertFalse(selector.previous.isEnabled())
        selector.next.click()
        self.assertEqual(selector.currentIndex(), 1)
        self.assertFalse(selector.next.isEnabled())
        panel = self.w.playback
        self.assertIsNotNone(panel.transform)
        self.assertEqual(panel.selected_audio_track, 1)
        panel.toggle()
        until(lambda: panel.failed or (panel.player.activeAudioTrack() == 1 and panel.managed_view.source is not None))
        self.assertFalse(panel.failed, panel.error_label.toolTip())
        selector.previous.click()
        until(lambda: panel.player.activeAudioTrack() == 0)
        self.assertTrue(selector.next.isEnabled())
        panel.pause()
