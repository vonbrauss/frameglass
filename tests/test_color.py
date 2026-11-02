import unittest
import numpy as np
from PySide6.QtCore import QSize
from PySide6.QtGui import QImage, QColorSpace
from PySide6.QtMultimedia import QVideoFrame, QVideoFrameFormat
from frameglass.color import (DisplayTransform, display_transform, display_rgb,
                              convert_thumbnail, video_image, scan_mode)

HLG = DisplayTransform('bt2020', 'arib-std-b67', 'bt2020nc', False)
PQ = DisplayTransform('bt2020', 'smpte2084', 'bt2020nc', False)


class ColorTests(unittest.TestCase):
    def test_only_tagged_supported_color_is_transformed(self):
        self.assertIsNone(display_transform({'color_primaries': 'bt2020'}))
        self.assertIsNone(display_transform({'color_primaries': 'bt709', 'color_transfer': 'bt709', 'color_space': 'bt709'}))
        stream = dict(color_primaries='bt2020', color_transfer='arib-std-b67', color_space='bt2020nc', color_range='tv')
        self.assertEqual(display_transform(stream).description, 'HLG → SDR preview')
        stream['color_transfer'] = 'smpte2084'
        self.assertEqual(display_transform(stream).description, 'PQ → SDR preview')
        stream['color_transfer'] = 'bt2020-10'
        self.assertFalse(display_transform(stream).hdr)

    def test_hdr_black_white_and_monotonic_grays(self):
        grays = np.repeat(np.array([0, .1, .25, .5, .75, 1], dtype=np.float32)[:, None], 3, axis=1)
        for transform in (HLG, PQ):
            values = display_rgb(grays, transform)
            self.assertEqual(values[0].tolist(), [0, 0, 0])
            self.assertEqual(values[-1].tolist(), [255, 255, 255])
            self.assertTrue(np.all(np.diff(values[:, 0].astype(int)) >= 0))
            self.assertTrue(np.all(np.max(values,axis=1)-np.min(values,axis=1) <= 1))
        # PQ code for 100 nits is ~0.508. The shoulder must produce a visible midtone.
        pq_100 = display_rgb(np.array([[[.508078, .508078, .508078]]]), PQ)[0,0,0]
        self.assertTrue(180 <= pq_100 <= 195)

    def test_thumbnail_and_decoder_match_on_limited_10_bit_gray(self):
        surface = QVideoFrameFormat(QSize(8, 4), QVideoFrameFormat.Format_YUV420P10)
        frame = QVideoFrame(surface)
        self.assertTrue(frame.map(QVideoFrame.WriteOnly))
        try:
            for plane, height, value in [(0,4,512), (1,2,512), (2,2,512)]:
                array = np.frombuffer(frame.bits(plane),dtype=np.uint16)
                array[:] = value
        finally:
            frame.unmap()
        decoded = video_image(frame, HLG)
        encoded = round((512-64)/876*65535)
        image = QImage(8,4,QImage.Format_RGBA64)
        data = np.frombuffer(image.bits(),dtype=np.uint16).reshape(4,-1)
        data[:,:32].reshape(4,8,4)[:,:,:3] = encoded
        data[:,:32].reshape(4,8,4)[:,:,3] = 65535
        thumb = convert_thumbnail(image,HLG)
        self.assertEqual(decoded.colorSpace(), QColorSpace(QColorSpace.SRgb))
        for component in ('red','green','blue'):
            self.assertLessEqual(abs(getattr(decoded.pixelColor(0,0),component)()-getattr(thumb.pixelColor(0,0),component)()),1)

    def test_scan_metadata(self):
        self.assertEqual(scan_mode({'field_order':'progressive'}),'Progressive')
        self.assertIn('top field first',scan_mode({'field_order':'tt'}))
        self.assertIn('bottom field first',scan_mode({'field_order':'bb'}))
        self.assertEqual(scan_mode({}),'Not reported')
