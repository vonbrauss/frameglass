import os
os.environ['QT_QPA_PLATFORM'] = 'offscreen'
import unittest
import time
import tempfile
import shutil
from PySide6.QtCore import QSettings, QMimeData, QUrl, QPointF, Qt
from PySide6.QtGui import QDragEnterEvent, QDropEvent
from frameglass.design import Disclosure
from pathlib import Path
from PySide6.QtWidgets import QApplication
from PySide6.QtTest import QTest
from frameglass.app import Window, STYLE
from frameglass.core import duration, rate, findings
app = QApplication.instance() or QApplication([])
app.setStyle('Fusion')
app.setStyleSheet(STYLE)
def until(predicate):
    for _ in range(750):
        if predicate(): return
        QTest.qWait(20)
        time.sleep(.002)
    raise AssertionError('Operation timed out')
class InspectionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir='/private/tmp')
        shutil.copy('/private/tmp/frameglass-test.mp4', Path(self.temp.name) / 'sample.mp4')
        (Path(self.temp.name) / 'unrelated.txt').write_text('test')
        self.w = Window(QSettings(str(Path(self.temp.name) / 'settings.ini'), QSettings.IniFormat))
        self.w.set_folder(self.temp.name)
        self.w.show()
    def tearDown(self):
        self.w.close()
        QTest.qWait(30)
        self.temp.cleanup()
    def test_probe_and_decode(self):
        self.w.inspect('/private/tmp/frameglass-test.mp4')
        until(lambda: self.w.data is not None)
        self.assertEqual(len(self.w.data['streams']), 2)
        self.assertEqual(self.w.data['streams'][0]['width'], 640)
        until(lambda: self.w.preview.source is not None)
        self.w.grab().save('/private/tmp/frameglass-preview.png')
        self.w.start_scan()
        until(lambda: self.w.scan is None)
        self.assertIn('no decoding warnings', self.w.scan_log)
        self.assertEqual(self.w.report()['metadata']['streams'][1]['codec_type'], 'audio')
    def test_unreadable_file(self):
        path = Path('/private/tmp/frameglass-broken.mp4')
        path.write_bytes(b'not a video')
        self.w.inspect(str(path))
        until(lambda: 'Unable to inspect' in self.w.status.text())
        self.assertIsNone(self.w.data)
        self.assertFalse(self.w.scan_button.isEnabled())
    def test_cancel(self):
        self.w.inspect('/private/tmp/frameglass-test.mp4')
        until(lambda: self.w.data is not None)
        self.w.start_scan()
        self.w.cancel_scan()
        until(lambda: self.w.scan is None)
        self.assertIn('cancelled', self.w.scan_log)
    def test_helpers(self):
        self.assertEqual(duration('3661.5'), '01:01:01.500')
        self.assertEqual(rate('24000/1001'), '23.976')
        self.assertEqual(rate('0/0'), 'Unknown')
        self.assertEqual(findings({})[0][0], 'Issue')

    def test_filter_keyboard_and_disclosure(self):
        target = Path(self.temp.name) / 'sample.mp4'
        until(lambda: self.w.model.rowCount(self.w.tree.rootIndex()) >= 2)
        self.w.search.setText('sample')
        until(lambda: self.w.model.rowCount(self.w.tree.rootIndex()) == 1)
        index = self.w.model.index(str(target))
        self.w.tree.setCurrentIndex(index)
        until(lambda: self.w.data is not None)
        self.assertEqual(self.w.path, str(target))
        details = self.w.findChildren(Disclosure)
        audio = next(d for d in details if d.toggle.text().startswith('Audio'))
        self.assertFalse(audio.toggle.isChecked())
        audio.toggle.click()
        self.assertTrue(audio.toggle.isChecked())
        self.assertFalse(audio.panel.isHidden())

    def test_drop_over_metadata_view(self):
        self.w.tabs.setCurrentIndex(3)
        mime = QMimeData()
        mime.setUrls([QUrl.fromLocalFile('/private/tmp/frameglass-test.mp4')])
        enter = QDragEnterEvent(self.w.raw.rect().center(), Qt.CopyAction, mime, Qt.LeftButton, Qt.NoModifier)
        QApplication.sendEvent(self.w.raw.viewport(), enter)
        self.assertTrue(enter.isAccepted())
        self.assertTrue(self.w.drop_overlay.isVisible())
        drop = QDropEvent(QPointF(30, 30), Qt.CopyAction, mime, Qt.LeftButton, Qt.NoModifier)
        QApplication.sendEvent(self.w.raw.viewport(), drop)
        until(lambda: self.w.data is not None)
        self.assertFalse(self.w.drop_overlay.isVisible())
        self.assertEqual(self.w.path, '/private/tmp/frameglass-test.mp4')

    def test_folder_navigation_replaces_accessible_view_and_preserves_filter(self):
        from PySide6.QtGui import QAccessible
        second = Path(self.temp.name) / 'other'
        second.mkdir()
        shutil.copy('/private/tmp/frameglass-test.mp4', second / 'sample-other.mp4')
        self.w.search.setText('sample')
        for folder in [second, Path(self.temp.name)] * 8:
            old_tree = self.w.tree
            accessible = QAccessible.queryAccessibleInterface(old_tree)
            if accessible:
                for index in range(accessible.childCount()):
                    accessible.child(index)
            self.w.set_folder(str(folder))
            self.assertIsNot(self.w.tree, old_tree)
            self.assertEqual(self.w.model.nameFilters(), ['*sample*'])
            until(lambda: self.w.model.rowCount(self.w.tree.rootIndex()) > 0)
            self.w.tree.setCurrentIndex(self.w.model.index(str(folder / ('sample-other.mp4' if folder == second else 'sample.mp4'))))
            until(lambda: self.w.data is not None)
            self.assertEqual(Path(self.w.path).parent, folder)
            current_tree = self.w.tree
            self.w.set_folder(str(folder))
            self.assertIs(self.w.tree, current_tree)
