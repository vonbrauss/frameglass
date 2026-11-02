# Frameglass

A minimalist Python desktop media inspector for macOS. Browse folders, click a file, or drop a local file/folder into the window. When multiple items are dropped, the first is opened.

## Run

For the standalone macOS app, open `dist/Frameglass.app` or copy it to Applications. To rebuild, double-click `Build App.command`. See [Building the app](docs/BUILDING.md).

Double-click `Launch.command` in Finder, or run:

```sh
cd /Users/editor/PycharmProjects/Frameglass
.venv/bin/python -m frameglass
```

The local virtual environment is installed. For a fresh installation, use Python 3.10+ and `python3 -m venv .venv`, then `.venv/bin/pip install -r requirements.txt`. Install FFmpeg separately (Homebrew: `brew install ffmpeg`). Both FFprobe and FFmpeg are already installed on this Mac.

## Interface

- **Library:** expand folders, double-click a folder to make it the root, or enter a path. Click a file or select it with the arrow keys to inspect it. Filter filenames with the search field; folders remain available for navigation.
- **Overview:** a large frame preview beside duration/size/resolution/rate, timecode In/Out with drop-frame status, separate video/audio track counts, and paired video/audio summaries. File tags expand on demand. The preview uses the first video frame; audio files show an audio symbol.
- **Tracks:** expandable sections with every reported stream property and chapter.
- **Diagnostics:** metadata checks and a cancellable full audio/video decode. Interpretations accompany recognized decoder messages.
- **Metadata:** complete JSON reported by FFprobe, including programs and chapters.
- **Gear menu:** tool paths, JSON report export, copy report, and reveal containing folder. Command-O opens media; Command-Shift-O opens a folder; Command-S exports.

Inspection runs asynchronously and never writes to source media. The full scan can be CPU intensive and can take as long as playback or longer. It decodes all audio/video tracks, excluding subtitles and data/attachments. A successful decode does not guarantee perceptual quality, perfect synchronization, or compatibility with every player. No automatic repairs are performed. Unknown properties remain unknown rather than being guessed. Reports include file paths and metadata; review before sharing.

Tool paths and last browsed folder persist in Qt's standard user settings location. Automatic executable discovery includes PATH and standard Homebrew locations.

## Audio overview and display color

All audio streams share one overview panel. Use the left/right arrow icons beside **Track 1 / N** to inspect another track and hear it in playback. A stereo/multichannel stream remains one track; its channel layout appears in the panel. The Tracks tab retains every stream’s complete metadata.

Video overview includes the tagged progressive/interlaced field order. Missing or unknown field order is shown as **Not reported**, without guessing from resolution or frame rate.

Automatic display rendering converts tagged BT.2020 HLG/PQ HDR to an SDR sRGB preview in both thumbnails and playback. It decodes the source transfer function, converts the gamut, compresses luminance with an extended Reinhard curve and tags the resulting image as sRGB. BT.2020 SDR uses gamut/transfer conversion without HDR tone mapping. Rec.709 SDR retains its native rendering path. Source metadata and files remain unchanged.

HLG uses a reference 1000-nit OOTF (gamma 1.2); PQ uses its absolute EOTF. Peak luminance uses available static metadata, otherwise 1000 nits; the preview target is SDR at a nominal 100 nits. This is a viewing transform, not a calibrated mastering monitor or native HDR output. Dynamic HDR/Dolby Vision trims and creative grading LUTs are not applied. Missing/unsupported tags use native rendering and are identified in the Display row. Settings → Display color allows native rendering for comparison.

Color-managed playback converts decoder planes in a background worker, bounded to 960×540 samples, with older pending frames replaced rather than queued indefinitely. Audio stays on the original playback clock. Smoothness depends on CPU and source codec; the preview is intended for inspection rather than full-resolution reference monitoring.

## Playback

Click the play icon on the thumbnail or press **L** to play the loaded clip. The controls under the picture provide play/pause, click or drag to seek, compact playhead timecode, volume behind the speaker icon, and **↗** to open the file in your default player.

- **J:** reverse shuttle. Repeated taps select 1×, 2×, 4× and 8× reverse.
- **K:** pause.
- **L:** forward playback. Repeated taps select 1×, 2×, 4× and 8× forward.
- **Hold K + tap J/L:** step backward/forward one frame. Holding the second key repeats frame steps.
- **Space:** play/pause while the player has focus.

J/K/L work in the main window, including the file browser. They leave text fields and dialogs free for typing. The compact readout shows **TC** for embedded source timecode, **Rel** for relative frame timecode when no source timecode is available, or **Time** for audio-only/unsupported frame rates. This readout is a calculated continuous timecode, not a scan for discontinuities in frame-level timecode.

Playback starts only when requested, and stops when another file is selected or the window closes. Reverse is silent and uses seeking, so its smoothness depends on the codec, storage and keyframe layout. Forward playback uses [Qt Multimedia](https://doc.qt.io/qtforpython-6/PySide6/QtMultimedia/QMediaPlayer.html); decoding support and shuttle audio quality vary by format. Unsupported media keeps its inspection details and offers the external-player action. Playback errors appear in the preview and their technical details are available on hover. Playback volume persists in app settings.

## Timecode and track counts

The overview reads embedded SMPTE timecode from the first video track, then container tags, then a MOV timecode track when it can be associated unambiguously. FFprobe exposes these sources as described in its [timecode documentation](https://ffmpeg.org/ffprobe.html#Timecode). Container format and bit rate sit immediately left of the compact timecode panel beneath the key facts. The timecode panel shows In, Out (last frame), and DF/NDF. Hover over it for calculation details and the source. Estimated Out values are labeled “Out (est.)”. Other embedded values remain in Tracks/Metadata.

**Duration** uses hours:minutes:seconds:frames from the total video frame count. It prefers drop-frame counting at 29.97, 59.94 and 119.88 fps even if embedded source timecode is non-drop; the duration label explicitly shows DF or NDF. Other supported rates use non-drop counting. Estimated duration is labeled EST. Durations longer than 24 hours do not wrap. Audio-only duration uses elapsed time because there is no video frame rate.

**Out is the last frame, inclusive**: In plus the reported video frame count minus one, assuming continuous timecode. When only video duration is available, Out is marked **Estimated**. Container duration is not used because audio can run longer than the video. Missing timecode, missing rates, conflicting frame rates, and unsupported values are shown as unavailable rather than inventing a timecode. Conflicting embedded timecodes are called out in the note. Frame-level timecode discontinuities are not scanned.

Drop-frame arithmetic supports 29.97, 59.94 and 119.88 rates, including ten-minute exceptions and 24-hour rollover. The timecode separator determines drop/non-drop, not the frame rate alone. Audio-only files without a video rate cannot have a calculated frame-based Out.

Video counts exclude cover art. Audio counts refer to streams/tracks: one stereo or multichannel stream counts as one track. Timecode, subtitle, data and cover-art streams are shown separately as “other.” Reports include the timecode calculation and video/audio counts.

## Development checks

Generate a small test fixture, then run the integration tests:

```sh
ffmpeg -hide_banner -loglevel error -f lavfi -i testsrc2=size=640x360:rate=24 -f lavfi -i sine=frequency=440:sample_rate=48000 -t 2 -c:v libx264 -c:a aac /private/tmp/frameglass-test.mp4 -y
.venv/bin/python -m unittest discover -s tests -v
```

The tests exercise actual FFprobe/FFmpeg processes through the Qt event loop, including metadata, thumbnails, decoding, cancellation, invalid media, filename filtering, keyboard selection, expandable tracks, and drops over the metadata viewer. Test settings are isolated from your saved app preferences. They use Qt's offscreen platform and save a preview to `/private/tmp/frameglass-preview.png`.

Built with [Qt for Python](https://doc.qt.io/qtforpython-6/) and [FFmpeg / FFprobe](https://ffmpeg.org/ffprobe.html). The installed Python dependencies retain their upstream licenses. A standalone Apple Silicon macOS build is available at `dist/Frameglass.app`. It bundles Python and the GUI/playback dependencies and is locally ad-hoc signed. Double-click `Build App.command` to rebuild it; see [Building the app](docs/BUILDING.md).

### Still images

JPEG, PNG, TIFF, WebP, BMP and other Pillow-supported stills have a separate Overview with an oriented, ICC-managed sRGB preview, fit/100%/zoom controls, pixel dimensions, file size, format, bit depth, transparency and print density when recorded. Capture settings, descriptions and rights appear when embedded. EXIF, IPTC, XMP and image text are available in collapsed metadata groups; GPS stays in a separate collapsed Location group. Image reports omit video timecode and track counts. Animated images retain the media viewer. Missing profiles are explicitly identified; unsupported profile conversions use a labeled approximate preview. Video inspection and playback retain their existing layout.

### Audio-only files

Audio-only Overview emphasizes embedded cover art, playback with J/K/L and elapsed milliseconds, sample rate, channel layout, stream bitrate and bit depth when reported. Music/rights tags and recording/BWF fields appear when available. Previous/next arrows select audio streams and update playback and metadata together. Attached artwork does not classify the file as video. Full decode scans remain available; audio reports omit frame-based timecode. Video and still-image layouts are unchanged.

### Quality workspace and inspection tools

Use the settings gear → **Quality tools** for **Compare files**, **Batch inspect folder**, **Delivery presets**, and **Analyze audio**. Comparison highlights differences across every media track and keeps missing metadata distinct from matches. Presets are editable local requirements; the bundled examples are starting points, not official delivery standards. Batch inspection supports subfolders, filtering, sorting, optional full decode scans, cancellation, CSV export, and double-clicking a result to inspect it. Comparison exports JSON with preset check details.

Audio analysis measures integrated loudness, loudness range, true peak and silence in the selected audio track, with a clickable mono waveform envelope. Playback meters show decoded sample peaks before volume; they are distinct from the full-track true-peak measurement. Diagnostics distinguishes header reads, unscanned payloads, cancellation, decode warnings and completed scans.

The gear's **Copy** submenu and filename context menu copy names, paths, source timecode and formatted summaries. Recent files are in the gear menu. Window size, sidebar width, selected file and tab restore on the next normal launch. The video's small viewer-tools menu includes frame stepping, fullscreen and optional 90%/80% picture guides. Still-image preview hovering displays pixel coordinates and preview RGB/alpha values; Histogram displays a sampled sRGB preview histogram.

See [Quality workspace guide](docs/QUALITY_WORKSPACE.md) for workflows and interpretation of results.

### Media-tool installation and updates

Frameglass checks FFmpeg/FFprobe versions in the background on every normal launch, compares them with the official stable release, and checks Homebrew package revisions when applicable. Missing/unusable tools and available updates appear in a dismissible Review tools banner. Open **Settings gear → Media tools & updates** at any time. Checks never install, upgrade or switch tools automatically. Offline checks report that update verification is unavailable.

Homebrew installations remain Homebrew-managed by default. Choose **Use existing Homebrew tools** without installing anything, or **Update with Homebrew** after confirmation. Versioned FFmpeg formulae and pins are respected. Homebrew may update required dependencies; Frameglass does not select unrelated packages for upgrade or unpin packages.

Without Homebrew, choose **Private tools for Frameglass**. The macOS installer downloads a matching FFmpeg/FFprobe pair from Martin Riedl’s build server, verifies published SHA-256 checksums, native architecture, Apple-anchored signatures and the expected signing team, then tests both versions before activating them together. Private tools live in `~/Library/Application Support/Frameglass/media-tools`; system executables are not overwritten, and previous private installs are retained. A cancelled or failed private install keeps the previous active pair. Existing binaries can also be selected manually. See [Media tools guide](docs/MEDIA_TOOLS.md).

Delivery presets can check container, bitrate/duration limits, embedded timecode and mode, channel layouts, and measured loudness/true peak. Click **Highlight Specs** in the upper-right toolbar to choose a preset and view its checklist; its separate checkbox evaluates and highlights green matches, red mismatches, and amber unknowns. **Measure audio** runs full-track measurements when needed. The audio card's **Channels…** control opens per-channel Solo/Mute, mono monitoring, channel assignments and live phase correlation. See [Quality workspace](docs/QUALITY_WORKSPACE.md) for units and behavior.
