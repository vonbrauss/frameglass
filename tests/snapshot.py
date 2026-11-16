"""Save pictures of the Overview and the player without opening a window.

    .venv/bin/python tests/snapshot.py [media file]

Writes test-logs/overview.png and test-logs/player.png. The meter is filled
with fixed sample levels so the bars are visible in a still picture.
"""
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import sys
import tempfile
import time
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication
from frameglass.app import Window, STYLE
from frameglass.audition import short_labels


def main():
    media = sys.argv[1] if len(sys.argv) > 1 else '/private/tmp/frameglass-51.mov'
    output = ROOT / 'test-logs'
    output.mkdir(exist_ok=True)
    app = QApplication.instance() or QApplication([])
    app.setStyle('Fusion')
    app.setStyleSheet(STYLE)
    with tempfile.TemporaryDirectory() as folder:
        window = Window(QSettings(str(Path(folder) / 'settings.ini'), QSettings.IniFormat))
        window.resize(1280, 1040)
        window.show()
        window.inspect(media)
        deadline = time.monotonic() + 12
        while time.monotonic() < deadline and (window.data is None or window.preview is None or window.preview.source is None):
            app.processEvents()
            time.sleep(.02)
        if window.data is None:
            raise SystemExit('The media could not be read: ' + media)
        player = window.playback
        labels = player.track_labels()
        if not labels:
            raise SystemExit('This media has no audio track.')
        levels = np.array([[.5, .42, .7, .06, .22, .18, .3, .28][:len(labels)]], dtype=np.float32)
        mix = levels @ player.monitor_route[0] if player.monitor_mode == ('mixdown',) else None
        player.meter.show_levels(levels, short_labels(labels), player.monitored_channels(len(labels)), mix, ('Lo', 'Ro') if mix is not None else ())
        player.meter.timer.stop()
        for _ in range(20):
            app.processEvents()
            time.sleep(.01)
        window.grab().save(str(output / 'overview.png'))
        player.grab().save(str(output / 'player.png'))
        print('Saved', output / 'overview.png')
        print('Saved', output / 'player.png')
        window.close()
        app.processEvents()


if __name__ == '__main__':
    main()
