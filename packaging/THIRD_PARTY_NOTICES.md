# Frameglass bundled components

Frameglass includes Python (PSF License), PySide6 / Shiboken6 and Qt (LGPLv3 and other applicable Qt component licenses), NumPy (BSD-3-Clause and bundled numerical library licenses), Pillow (MIT-CMU and bundled image-library licenses), certifi (MPL-2.0 certificate bundle), and the PyInstaller bootloader (GPL with its distribution exception).

Qt's multimedia backend includes FFmpeg libraries and other codec libraries. Their applicable licenses and copyright notices ship in the app's third-party licenses directory alongside the Python-package notices. Standalone ffmpeg/ffprobe executables are not bundled; their Homebrew or private installation is separately managed.

Qt and other libraries are shipped as separate shared libraries within the app bundle. Source and license resources:
- Python: https://www.python.org/downloads/source/
- Qt / PySide: https://code.qt.io/ and https://www.qt.io/qt-for-python
- NumPy: https://github.com/numpy/numpy
- Pillow: https://github.com/python-pillow/Pillow
- FFmpeg: https://ffmpeg.org/download.html
- certifi: https://github.com/certifi/python-certifi
- PyInstaller: https://github.com/pyinstaller/pyinstaller

This is a local development build, ad-hoc signed rather than signed with an Apple Developer ID or notarized by Apple.
