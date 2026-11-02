"""Still-image metadata and color-managed previews, isolated from video handling."""
from datetime import datetime
from io import BytesIO
from pathlib import Path
import math
import xml.etree.ElementTree as ET

from PIL import Image, ImageCms, ImageOps, ExifTags, IptcImagePlugin
from PIL.ImageQt import ImageQt
from PySide6.QtCore import QObject, QRunnable, Signal, Qt, QEvent
from PySide6.QtGui import QImage, QPixmap
from PySide6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QScrollArea, QLabel, QPushButton, QDialog, QSizePolicy

STILL_EXTENSIONS = {'.jpg', '.jpeg', '.jpe', '.png', '.tif', '.tiff', '.webp', '.bmp', '.gif', '.ico', '.psd', '.avif', '.heic', '.heif'}


def readable(value):
    if isinstance(value, bytes):
        try:
            return value.decode('utf-8').strip('\x00 ')
        except UnicodeDecodeError:
            return f'Binary data ({len(value):,} bytes)'
    if isinstance(value, dict):
        return {str(k): readable(v) for k, v in value.items()}
    if isinstance(value, (tuple, list)):
        return [readable(v) for v in value]
    return str(value)


def text(value):
    value = readable(value)
    return ', '.join(map(str, value)) if isinstance(value, list) else str(value)


def read_still(path, metadata_only=False):
    """Return None for animated images; those retain the existing media viewer."""
    with Image.open(path) as source:
        if getattr(source, 'is_animated', False):
            return None, QImage()
        info = source.info.copy()
        exif = source.getexif()
        fields = {ExifTags.TAGS.get(k, str(k)): v for k, v in exif.items() if k not in (34665, 34853)}
        for k, v in exif.get_ifd(34665).items():
            fields[ExifTags.TAGS.get(k, str(k))] = v
        gps_fields = exif.get_ifd(34853)
        gps = {ExifTags.GPSTAGS.get(k, str(k)): readable(v) for k, v in gps_fields.items()}
        try:
            def degrees(parts, ref):
                value = float(parts[0]) + float(parts[1])/60 + float(parts[2])/3600
                return -value if text(ref) in ('S','W') else value
            if 2 in gps_fields and 4 in gps_fields:
                gps = {'Coordinates': f'{degrees(gps_fields[2], gps_fields.get(1)):.6f}, {degrees(gps_fields[4], gps_fields.get(3)):.6f}', **gps}
        except (TypeError, ValueError, ZeroDivisionError, IndexError):
            pass
        iptc = IptcImagePlugin.getiptcinfo(source) or {}
        iptc = {f'{k[0]}:{k[1]}': readable(v) for k, v in iptc.items()}
        xmp = {}
        packet = info.get('xmp') or info.get('XML:com.adobe.xmp')
        if packet:
            try:
                root = ET.fromstring(packet)
                for element in root.iter():
                    for key, value in element.attrib.items():
                        if value.strip(): xmp[key] = value
                    if element.text and element.text.strip():
                        xmp[element.tag] = element.text.strip()
                    children = [e.text.strip() for e in element.iter() if e is not element and e.text and e.text.strip()]
                    if children and len(children) < 40:
                        xmp[element.tag] = list(dict.fromkeys(children))
            except ET.ParseError:
                xmp['Unreadable XMP'] = 'Embedded XMP could not be parsed'
        def xm(name):
            # Namespace-aware suffixes avoid confusing dc:creator with unrelated tags.
            return next((text(v) for k, v in xmp.items() if k.endswith('}' + name)), '')
        def first(*values):
            return next((text(v).strip() for v in values if v is not None and str(v).strip()), '')
        capture = []
        camera = ' '.join(str(fields.get(k, '')).strip() for k in ('Make', 'Model')).strip()
        if camera: capture.append(('Camera', camera))
        lens = first(fields.get('LensModel'), xm('Lens'))
        if lens: capture.append(('Lens', lens))
        exposure = []
        try:
            shutter = float(fields.get('ExposureTime', 0))
            if shutter: exposure.append((f'1/{round(1/shutter)}' if shutter < 1 else f'{shutter:g}') + ' s')
            aperture = float(fields.get('FNumber', 0))
            if aperture: exposure.append(f'ƒ/{aperture:g}')
        except (ValueError, TypeError, ZeroDivisionError): pass
        iso = first(fields.get('PhotographicSensitivity'), fields.get('ISOSpeedRatings'))
        if iso: exposure.append('ISO ' + iso)
        if exposure: capture.append(('Exposure', ' · '.join(exposure)))
        focal = fields.get('FocalLength')
        if focal:
            try: capture.append(('Focal length', f'{float(focal):g} mm'))
            except (TypeError, ValueError): pass
        date = first(fields.get('DateTimeOriginal'), xm('DateTimeOriginal'))
        if date:
            offset = first(fields.get('OffsetTimeOriginal'))
            capture.append(('Captured', date + (' ' + offset if offset else ' · timezone not recorded')))
        description = []
        for key, value in [
            ('Title', first(xm('title'), iptc.get('2:5'), info.get('Title'))),
            ('Caption', first(xm('description'), iptc.get('2:120'), fields.get('ImageDescription'), info.get('Description'))),
            ('Creator', first(xm('creator'), iptc.get('2:80'), fields.get('Artist'), info.get('Author'))),
            ('Copyright', first(xm('rights'), iptc.get('2:116'), fields.get('Copyright'))),
            ('Keywords', first(xm('subject'), iptc.get('2:25')))]:
            if value: description.append((key, value))
        original_size = source.size
        mode = source.mode
        bits = fields.get('BitsPerSample') or (16 if mode.startswith('I;16') else 32 if mode in ('I', 'F') else 8)
        if isinstance(bits, tuple): bits = bits[0]
        if source.format == 'PNG':
            with open(path, 'rb') as file:
                header = file.read(25)
            if len(header) == 25: bits = header[24]
        alpha = 'A' in source.getbands() or 'transparency' in info
        profile = info.get('icc_profile')
        profile_name = 'No embedded profile'
        display_note = 'Preview assumes sRGB for unprofiled RGB images' if mode in ('RGB','RGBA','P') else 'Unprofiled image · approximate RGB preview'
        oriented = source if metadata_only else ImageOps.exif_transpose(source)
        preview = None if metadata_only else oriented.convert('RGBA' if alpha else 'RGB')
        if profile:
            try:
                embedded = ImageCms.ImageCmsProfile(BytesIO(profile))
                profile_name = ImageCms.getProfileDescription(embedded).strip()
                if not metadata_only:
                    rgb = ImageCms.profileToProfile(oriented.convert('RGB') if mode in ('P', 'RGBA', 'LA') else oriented, embedded, ImageCms.createProfile('sRGB'), outputMode='RGB')
                    if alpha: rgb.putalpha(preview.getchannel('A'))
                    preview = rgb
                display_note = 'Embedded ICC profile → sRGB preview'
            except (ValueError, OSError, ImageCms.PyCMSError):
                profile_name = 'Embedded profile (unreadable or unsupported)'
                display_note = 'ICC conversion unavailable; approximate preview'
        width, height = oriented.size
        if metadata_only and exif.get(274) in (5,6,7,8):
            width,height=height,width
        divisor = math.gcd(width, height)
        dpi = info.get('dpi')
        image_rows = [('Color model', mode), ('Bit depth', f'{bits}-bit/channel'), ('Transparency', 'Yes' if alpha else 'No'), ('ICC profile', profile_name), ('Display', display_note)]
        if dpi and len(dpi) >= 2 and all(isinstance(n, (int, float)) and n > 0 for n in dpi[:2]):
            image_rows += [('Print density', f'{dpi[0]:g} × {dpi[1]:g} PPI'), ('Print size', f'{width/dpi[0]:.2f} × {height/dpi[1]:.2f} in')]
        software = first(fields.get('Software'), info.get('Software'), xm('CreatorTool'))
        if software: image_rows.append(('Software', software))
        stat = Path(path).stat()
        image_rows.append(('File modified', datetime.fromtimestamp(stat.st_mtime).astimezone().isoformat(timespec='seconds')))
        metadata = {'kind': 'still', 'format': {'format_name': source.format, 'size': stat.st_size}, 'image': {
            'width': width, 'height': height, 'stored_dimensions': list(original_size), 'megapixels': round(width*height/1e6, 2),
            'aspect': f'{width//divisor}:{height//divisor}', 'mode': mode, 'bits': text(bits), 'profile': profile_name,
            'capture': capture, 'description': description, 'properties': image_rows, 'gps': gps,
            'exif': readable(fields), 'iptc': iptc, 'xmp': xmp,
            'embedded_text': {k: readable(v) for k,v in info.items() if isinstance(v,str) and k != 'XML:com.adobe.xmp'}}}
        return metadata, QImage() if metadata_only else QImage(ImageQt(preview)).copy()


class StillSignals(QObject):
    ready = Signal(object, QImage, str, int)


class StillJob(QRunnable):
    def __init__(self, path, signals, cancelled, token):
        super().__init__()
        self.path, self.signals, self.cancelled, self.token = path, signals, cancelled, token

    def run(self):
        if self.cancelled.is_set(): return
        try:
            data, image = read_still(self.path)
            error = ''
        except Exception as e:
            data, image, error = None, QImage(), str(e)
        if not self.cancelled.is_set():
            try: self.signals.ready.emit(data, image, error, self.token)
            except RuntimeError: pass


class ImageViewer(QWidget):
    """Fit or inspect actual pixels; no playback controls on a still image."""
    def __init__(self, image, parent=None, expanded=False):
        super().__init__(parent)
        self.image = image
        self.zoom = None
        layout = QVBoxLayout(self); layout.setContentsMargins(0,0,0,0); layout.setSpacing(6)
        self.scroll = QScrollArea(); self.scroll.setObjectName('stillViewport')
        self.scroll.setAlignment(Qt.AlignCenter); self.scroll.setWidgetResizable(False)
        self.scroll.viewport().installEventFilter(self)
        from .inspection_tools import PixelLabel
        self.picture = PixelLabel(image); self.picture.setAlignment(Qt.AlignCenter)
        self.scroll.setWidget(self.picture); layout.addWidget(self.scroll,1)
        buttons = QHBoxLayout()
        from .design import ElidedLabel
        self.zoom_label = ElidedLabel('Fit');buttons.addWidget(self.zoom_label,1)
        self.picture.pixelChanged.connect(lambda value:self.zoom_label.setText(value or 'Fit'))
        for name, callback in [('Fit',lambda:self.set_zoom(None)),('100%',lambda:self.set_zoom(1)),('−',lambda:self.adjust_zoom(.8)),('+',lambda:self.adjust_zoom(1.25))]:
            button=QPushButton(name);button.setObjectName('quiet');button.clicked.connect(callback);buttons.addWidget(button)
        button=QPushButton('Histogram');button.setObjectName('quiet');button.clicked.connect(self.show_histogram);buttons.addWidget(button)
        if not expanded:
            button=QPushButton('Expand');button.setObjectName('quiet');button.clicked.connect(self.expand);buttons.addWidget(button)
        layout.addLayout(buttons)
        self.setMinimumSize(200,220)

    def set_zoom(self, zoom):
        self.zoom = zoom
        self.refresh()

    def adjust_zoom(self, factor):
        current = self.zoom or min(self.scroll.viewport().width()/self.image.width(),self.scroll.viewport().height()/self.image.height())
        self.set_zoom(max(.02,min(8,current*factor)))

    def refresh(self):
        if self.image.isNull():return
        ratio = self.zoom or min(self.scroll.viewport().width()/self.image.width(), self.scroll.viewport().height()/self.image.height())
        ratio=max(.001,ratio)
        target=self.image.size()*ratio*(self.devicePixelRatioF() if self.zoom is None else 1)
        pixmap=QPixmap.fromImage(self.image).scaled(target,Qt.KeepAspectRatio,Qt.SmoothTransformation)
        # Logical size accounts for Retina so 100% maps one image pixel to one display pixel.
        pixmap.setDevicePixelRatio(self.devicePixelRatioF())
        self.picture.setPixmap(pixmap);self.picture.resize(pixmap.deviceIndependentSize().toSize())
        self.zoom_label.setText('Fit' if self.zoom is None else f'{self.zoom*100:.0f}%')

    def eventFilter(self, watched, event):
        if watched is self.scroll.viewport() and event.type() == QEvent.Resize and self.zoom is None:
            self.refresh()
        return super().eventFilter(watched, event)

    def resizeEvent(self,event):
        super().resizeEvent(event)
        if self.zoom is None:self.refresh()

    def show_histogram(self):
        from .inspection_tools import Histogram
        dialog=QDialog(self);dialog.setWindowTitle('Display histogram · sRGB preview');dialog.resize(600,320)
        layout=QVBoxLayout(dialog)
        caption=QLabel('RGB histogram of the displayed preview · sampled at up to 1024 px · transparent pixels excluded')
        caption.setWordWrap(True);caption.setObjectName('muted');layout.addWidget(caption)
        layout.addWidget(Histogram(self.image));dialog.exec()

    def expand(self):
        dialog=QDialog(self);dialog.setWindowTitle('Frameglass · Image');dialog.resize(1100,800)
        layout=QVBoxLayout(dialog);layout.addWidget(ImageViewer(self.image,dialog,True));dialog.exec()
