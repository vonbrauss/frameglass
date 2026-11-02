import os
os.environ['QT_QPA_PLATFORM'] = 'offscreen'
import json
import tempfile
import time
import unittest
from pathlib import Path
from PIL import Image, ImageCms, PngImagePlugin
from PySide6.QtWidgets import QApplication, QLabel
from PySide6.QtCore import QSettings, Qt
from PySide6.QtTest import QTest
from frameglass.app import Window, STYLE
from frameglass.stills import read_still
from frameglass.design import Disclosure

app=QApplication.instance() or QApplication([])
app.setStyle('Fusion');app.setStyleSheet(STYLE)

def until(predicate):
    for _ in range(500):
        if predicate():return
        QTest.qWait(20);time.sleep(.002)
    raise AssertionError('Image inspection timed out')

class StillTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(dir='/private/tmp')
        self.path=Path(self.temp.name)/'photo.jpg'
        image=Image.new('RGB',(1200,800),'#619bb8')
        exif=Image.Exif();exif[271]='Canon';exif[272]='EOS R5';exif[274]=6
        exif[315]='Taylor Editor';exif[270]='Afternoon on the coast'
        exif[34665]={33434:1/250,33437:2.8,34855:400,36867:'2026:10:20 12:30:00',36881:'-07:00',42036:'RF 50mm',37386:50}
        image.save(self.path,exif=exif,icc_profile=ImageCms.ImageCmsProfile(ImageCms.createProfile('sRGB')).tobytes(),dpi=(300,300))
        self.w=None
    def tearDown(self):
        if self.w:self.w.close();QTest.qWait(60)
        self.temp.cleanup()
    def test_orientation_capture_profile_and_rights(self):
        data,image=read_still(self.path);info=data['image']
        self.assertEqual((info['width'],info['height']),(800,1200))
        self.assertEqual((image.width(),image.height()),(800,1200))
        capture=dict(info['capture'])
        self.assertEqual(capture['Exposure'],'1/250 s · ƒ/2.8 · ISO 400')
        self.assertIn('-07:00',capture['Captured'])
        self.assertEqual(dict(info['description'])['Creator'],'Taylor Editor')
        self.assertIn('sRGB',info['profile'])
        self.assertEqual(dict(info['properties'])['Print size'],'2.67 × 4.00 in')
        json.dumps(data)
    def test_png_transparency_xmp_and_missing_profile(self):
        path=Path(self.temp.name)/'graphic.png'
        png=PngImagePlugin.PngInfo()
        png.add_text('XML:com.adobe.xmp','<x:xmpmeta xmlns:x="adobe:ns:meta/" xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#"><dc:title><rdf:Alt><rdf:li>Coastal study</rdf:li></rdf:Alt></dc:title><dc:subject><rdf:Bag><rdf:li>Ocean</rdf:li><rdf:li>Summer</rdf:li></rdf:Bag></dc:subject></x:xmpmeta>')
        Image.new('RGBA',(100,50),(0,100,255,120)).save(path,pnginfo=png)
        data,image=read_still(path);info=data['image']
        self.assertEqual(info['profile'],'No embedded profile')
        self.assertEqual(dict(info['properties'])['Transparency'],'Yes')
        self.assertEqual(dict(info['description'])['Title'],'Coastal study')
        self.assertEqual(dict(info['description'])['Keywords'],'Ocean, Summer')
        self.assertTrue(image.hasAlphaChannel());self.assertFalse(info['capture'])
    def test_animation_keeps_media_path(self):
        path=Path(self.temp.name)/'animated.gif'
        Image.new('RGB',(30,30),'red').save(path,save_all=True,append_images=[Image.new('RGB',(30,30),'blue')],duration=100,loop=0)
        self.assertIsNone(read_still(path)[0])
    def test_overview_and_switch_back_to_video(self):
        self.w=Window(QSettings(self.temp.name+'/prefs.ini',QSettings.IniFormat));self.w.show();self.w.inspect(str(self.path))
        until(lambda:self.w.data is not None)
        self.assertEqual(self.w.data['kind'],'still');self.assertIsNone(self.w.playback)
        self.assertEqual(self.w.tabs.tabText(1),'Image metadata');self.assertFalse(self.w.scan_button.isEnabled())
        texts=[label.text() for label in self.w.findChildren(QLabel) if label.isVisible()]
        self.assertFalse(any('DURATION' in t or t=='CONTAINER' or t=='TRACK SUMMARY' for t in texts))
        self.assertTrue(any(d.toggle.text()=='All image metadata' and not d.toggle.isChecked() for d in self.w.findChildren(Disclosure)))
        self.w.image_viewer.set_zoom(1);self.assertEqual(self.w.image_viewer.zoom_label.text(),'100%')
        self.w.image_viewer.set_zoom(None)
        self.assertNotIn('timecode',self.w.report())
        QTest.qWait(100)
        self.w.grab().save('/private/tmp/frameglass-still-overview.png')
        self.w.inspect('/private/tmp/frameglass-test.mp4');until(lambda:self.w.data is not None)
        self.assertIsNotNone(self.w.playback);self.assertEqual(self.w.tabs.tabText(1),'Tracks')
        self.assertTrue(self.w.scan_button.isEnabled());self.assertIn('timecode',self.w.report())
        self.w.inspect(str(self.path));self.w.inspect('/private/tmp/frameglass-test.mp4')
        until(lambda:self.w.data is not None);QTest.qWait(250)
        self.assertNotEqual(self.w.data.get('kind'),'still')
