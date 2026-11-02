"""Explicit SDR display rendering for tagged BT.2020 and PQ/HLG media.

Original video and metadata are never altered. PQ/HLG are decoded to linear
light, gamut-converted, luminance-tone-mapped, then encoded as tagged sRGB.
The same math is used for 16-bit PNG thumbnails and decoded video planes.
"""
from dataclasses import dataclass
from fractions import Fraction
import math
import numpy as np
from PySide6.QtCore import QObject, QRunnable, Signal
from PySide6.QtGui import QColorSpace, QImage
from PySide6.QtMultimedia import QVideoFrame


def scan_mode(stream):
    return {
        'progressive': 'Progressive',
        'tt': 'Interlaced · top field first',
        'bb': 'Interlaced · bottom field first',
        'tb': 'Interlaced · bottom field displayed first',
        'bt': 'Interlaced · top field displayed first',
    }.get(stream.get('field_order'), 'Not reported')


@dataclass(frozen=True)
class DisplayTransform:
    primaries: str
    transfer: str
    matrix: str
    full_range: bool
    peak_nits: float = 1000.0

    @property
    def hdr(self):
        return self.transfer in ('smpte2084', 'arib-std-b67')

    @property
    def description(self):
        if self.hdr:
            name = 'HLG' if self.transfer == 'arib-std-b67' else 'PQ'
            return f'{name} → SDR preview'
        return 'BT.2020 → sRGB preview'


def display_transform(stream):
    primaries = stream.get('color_primaries')
    transfer = stream.get('color_transfer')
    matrix = stream.get('color_space')
    hdr = transfer in ('smpte2084', 'arib-std-b67')
    if primaries not in ('bt2020', 'bt709') or (not hdr and primaries != 'bt2020'):
        return None
    if transfer not in ('smpte2084', 'arib-std-b67', 'bt2020-10', 'bt2020-12', 'bt709', 'iec61966-2-1'):
        return None  # Do not guess a transfer function from primaries alone.
    if matrix not in ('bt2020nc', 'bt709'):
        return None
    peak = 1000.0
    for side in stream.get('side_data_list', []):
        value = side.get('max_content') or side.get('max_luminance')
        try:
            candidate = float(Fraction(str(value)))
            if 100 <= candidate <= 10000:
                peak = candidate
        except (ValueError, ZeroDivisionError):
            pass
    return DisplayTransform(primaries, transfer, matrix, stream.get('color_range') == 'pc', peak)


def display_description(stream):
    transform = display_transform(stream)
    if transform:
        return transform.description
    if stream.get('color_primaries') == 'bt2020' or stream.get('color_transfer') in ('smpte2084', 'arib-std-b67'):
        return 'Native rendering · incomplete/unsupported color tags'
    return 'Native SDR rendering'


def display_rgb(encoded, transform):
    """Nonlinear source RGB in [0,1] -> SDR sRGB uint8; no exposure guessing."""
    rgb = np.clip(np.asarray(encoded, dtype=np.float32), 0, 1)
    if transform.transfer == 'smpte2084':
        # SMPTE ST 2084 absolute luminance, normalized to SDR reference white.
        m1, m2 = 2610 / 16384, 2523 / 32
        c1, c2, c3 = 3424 / 4096, 2413 / 128, 2392 / 128
        power = rgb ** (1 / m2)
        linear = (np.maximum(power - c1, 0) / np.maximum(c2 - c3 * power, 1e-6)) ** (1 / m1)
        linear *= 10000 / 100
    elif transform.transfer == 'arib-std-b67':
        # Inverse HLG OETF followed by a reference 1000-nit OOTF (gamma 1.2).
        a, b, c = 0.17883277, 0.28466892, 0.55991073
        scene = np.where(rgb <= 0.5, rgb * rgb / 3, (np.exp((rgb - c) / a) + b) / 12)
        weights = (0.2627, 0.6780, 0.0593) if transform.primaries == 'bt2020' else (0.2126, 0.7152, 0.0722)
        y = sum(scene[..., i] * weights[i] for i in range(3))
        linear = scene * np.maximum(y, 0)[..., None] ** 0.2 * (1000 / 100)
    elif transform.transfer == 'iec61966-2-1':
        linear = np.where(rgb <= 0.04045, rgb / 12.92, ((rgb + 0.055) / 1.055) ** 2.4)
    else:
        linear = np.where(rgb < 0.08145, rgb / 4.5, ((rgb + 0.0993) / 1.0993) ** (1 / 0.45))
    if transform.primaries == 'bt2020':
        r, g, b = (linear[..., i] for i in range(3))
        linear = np.stack((1.660491*r - 0.587641*g - 0.072850*b,
                           -0.124550*r + 1.132900*g - 0.008349*b,
                           -0.018151*r - 0.100579*g + 1.118730*b), axis=-1)
    linear = np.maximum(linear, 0)
    if transform.hdr:
        y = linear[..., 0]*0.2126 + linear[..., 1]*0.7152 + linear[..., 2]*0.0722
        peak = max(1, transform.peak_nits / 100)
        mapped = y * (1 + y / (peak * peak)) / (1 + y)
        linear *= (mapped / np.maximum(y, 1e-8))[..., None]
    srgb = np.where(linear <= 0.0031308, linear * 12.92, 1.055 * np.maximum(linear, 0) ** (1/2.4) - 0.055)
    return np.clip(np.round(srgb * 255), 0, 255).astype(np.uint8)


def rgb_image(encoded, transform):
    values = np.ascontiguousarray(display_rgb(encoded, transform))
    h, w, _ = values.shape
    image = QImage(values.data, w, h, values.strides[0], QImage.Format_RGB888).copy()
    image.setColorSpace(QColorSpace(QColorSpace.SRgb))
    return image


def convert_thumbnail(image, transform):
    image = image.convertToFormat(QImage.Format_RGBA64)
    samples = np.frombuffer(image.constBits(), dtype=np.uint16).reshape(image.height(), image.bytesPerLine() // 2)
    rgb = samples[:, :image.width()*4].reshape(image.height(), image.width(), 4)[..., :3]
    return rgb_image(rgb.astype(np.float32) / 65535, transform)


def video_image(frame, transform):
    """Read supported decoder YUV planes directly, before Qt's RGB/HDR path."""
    name = frame.pixelFormat().name
    if not frame.map(QVideoFrame.ReadOnly):
        raise ValueError('Decoder frame could not be mapped')
    try:
        h, w = frame.height(), frame.width()
        step = max(1, math.ceil(max(w / 960, h / 540)))
        ys, xs = np.arange(0, h, step), np.arange(0, w, step)
        def plane(number, dtype, height):
            stride = frame.bytesPerLine(number) // np.dtype(dtype).itemsize
            return np.frombuffer(frame.bits(number), dtype=dtype, count=stride*height).reshape(height, stride)
        bits = 8
        if name in ('Format_NV12', 'Format_NV21', 'Format_P010', 'Format_P016'):
            bits = 10 if name == 'Format_P010' else 16 if name == 'Format_P016' else 8
            dtype = np.uint16 if bits > 8 else np.uint8
            y = plane(0, dtype, h)[np.ix_(ys, xs)]
            uv = plane(1, dtype, (h+1)//2)
            first = uv[np.ix_(ys//2, (xs//2)*2)]
            second = uv[np.ix_(ys//2, (xs//2)*2+1)]
            u, v = (second, first) if name == 'Format_NV21' else (first, second)
            if bits == 10:
                y, u, v = (a >> 6 for a in (y, u, v))
        elif name in ('Format_YUV420P', 'Format_YUV420P10', 'Format_YV12', 'Format_YUV422P'):
            bits = 10 if name == 'Format_YUV420P10' else 8
            dtype = np.uint16 if bits > 8 else np.uint8
            sy = 1 if name == 'Format_YUV422P' else 2
            y = plane(0, dtype, h)[np.ix_(ys, xs)]
            u = plane(1, dtype, (h+sy-1)//sy)[np.ix_(ys//sy, xs//2)]
            v = plane(2, dtype, (h+sy-1)//sy)[np.ix_(ys//sy, xs//2)]
            if name == 'Format_YV12':
                u, v = v, u
        else:
            raise ValueError('Color conversion is not available for ' + name)
        y, u, v = (a.astype(np.float32) for a in (y, u, v))
        if transform.full_range:
            y /= (1 << bits) - 1
            u = (u - (1 << (bits-1))) / ((1 << bits)-1)
            v = (v - (1 << (bits-1))) / ((1 << bits)-1)
        else:
            scale = 1 << (bits - 8)
            y = (y - 16*scale) / (219*scale)
            u = (u - 128*scale) / (224*scale)
            v = (v - 128*scale) / (224*scale)
        kr, kb = (0.2627, 0.0593) if transform.matrix == 'bt2020nc' else (0.2126, 0.0722)
        r = y + 2*(1-kr)*v
        b = y + 2*(1-kb)*u
        g = (y - kr*r - kb*b) / (1-kr-kb)
        image = rgb_image(np.stack((r, g, b), axis=-1), transform)
        rotation = frame.rotation().value
        if rotation:
            from PySide6.QtGui import QTransform
            image = image.transformed(QTransform().rotate(rotation))
        return image
    finally:
        frame.unmap()


class ColorSignals(QObject):
    ready = Signal(QImage, str)


class ColorJob(QRunnable):
    def __init__(self, frame, transform, signals, cancelled):
        super().__init__()
        self.frame = QVideoFrame(frame)
        self.transform = transform
        self.signals = signals
        self.cancelled = cancelled

    def run(self):
        if self.cancelled.is_set():
            return
        try:
            image = video_image(self.frame, self.transform)
            error = ''
        except Exception as failure:
            image, error = QImage(), str(failure)
        if not self.cancelled.is_set():
            try:
                self.signals.ready.emit(image, error)
            except RuntimeError:
                pass  # The view may have closed while a decoder frame was mapped.


class ThumbnailSignals(QObject):
    ready = Signal(QImage, str, int)


class ThumbnailJob(QRunnable):
    """Drain high-bit-depth PNG output off the GUI thread, with a time limit."""
    def __init__(self, command, transform, signals, cancelled, token):
        super().__init__()
        self.command, self.transform = command, transform
        self.signals, self.cancelled, self.token = signals, cancelled, token

    def run(self):
        if self.cancelled.is_set():
            return
        import subprocess
        try:
            result = subprocess.run(self.command, capture_output=True, timeout=10)
            if result.returncode:
                raise ValueError(result.stderr.decode(errors='replace')[-2000:] or 'Thumbnail decoder failed')
            image = QImage.fromData(result.stdout)
            if image.isNull():
                raise ValueError('No preview image returned')
            if self.transform:
                image = convert_thumbnail(image, self.transform)
            error = ''
        except Exception as failure:
            image, error = QImage(), str(failure)
        if not self.cancelled.is_set():
            try:
                self.signals.ready.emit(image, error, self.token)
            except RuntimeError:
                pass
