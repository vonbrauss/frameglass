# Standalone macOS app

The built application is `dist/Frameglass.app`. Open it in Finder or drag it to Applications. Move/copy the entire `.app`, not just its executable. It includes Python, PySide6/Qt (including the playback backend and native QML audio component), NumPy, Pillow, and HTTPS certificate roots. PyCharm and a separate Python installation are not required to run it.

This build targets Apple Silicon (arm64), macOS 13 or later. It has been tested on the build Mac; macOS 13 itself and Intel Macs have not been tested. It is locally ad-hoc signed, without an Apple Developer ID or Apple notarization. A release intended for other Macs needs an appropriate signing/notarization workflow and testing on those target systems.

FFmpeg and FFprobe command-line executables remain separately managed. The app detects existing Homebrew tools, supports the verified private installation route, and checks for updates at startup. The Qt FFmpeg playback libraries are included in the app and are separate from those command-line tools.

## Rebuild

Double-click `Build App.command`, or run it from Terminal in the project folder. It installs the project/build requirements into `.venv`, generates the icon and component notices, builds the bundle, and verifies its local signature. An existing `dist/Frameglass.app` is replaced. Keep the source project to make future changes and rebuilds.

On a fresh build machine, first create a compatible virtual environment:

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements-build.txt
./Build\ App.command
```

The checked-in `Frameglass.spec` defines the bundle and its document types. The entry point is `packaging/launcher.py`. QML collection is restricted to the modules needed by AudioOutput and its dependencies; unrelated Qt UI modules are excluded. `packaging/runtime.py` points Python HTTPS to the bundled certifi roots. `packaging/collect_licenses.py` collects installed component notices and the included upstream license texts.

## Verify a build

```sh
/usr/bin/codesign --verify --deep --strict dist/Frameglass.app
.venv/bin/python -m unittest discover -s tests
```

An explicit native packaged-runtime check is available as:

```sh
dist/Frameglass.app/Contents/MacOS/Frameglass --smoke-test /path/to/test-fixtures
```

The fixture folder must contain `clip.mp4` (video with audio, at least 2 seconds), `audio.wav` (stereo, at least 3 seconds), and `still.jpg`. The check uses isolated settings, mutes playback volume, writes `result.json` and a video screenshot into that folder, and tests video/audio playback, channel monitoring, image decoding, HDR math, full-track audio measurement, preset controls and the bundled certificate store. It does not change normal user settings or install media tools.

Finder Open With and files dropped on the app icon are handled with native Qt FileOpen events. The app registers as an alternate viewer for video, audio and images; it does not request to replace your default players.
