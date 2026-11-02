# Frameglass quality workspace

Open the settings gear → Quality tools. These tools read source files; they do not convert, rewrite, move or repair them.

## Compare two files

The loaded inspector file can fill A as the reference. Choose a candidate for B, or use the loaded file on either side. Rows cover file format, duration, track counts, source timecode, and every video and audio track's reported properties. Stills compare dimensions, color profile and descriptive/capture metadata. Tracks pair by type and order; this is not a content-based track association.

Use Differences only to hide matching rows. Missing fields remain Not reported, even when both files omit them. Comparison does not establish identical pixels, sound or file bytes. Source Out values may be estimated; the calculation is shown separately. Export comparison writes JSON containing the full comparison and active preset checks.

## Delivery presets

New creates a named set of requirements. Any excludes a field from checking. Video controls include resolution, frame rate, scan, primaries, transfer, matrix, pixel format and codec. Audio controls include codec, track count, channel count, sample rate and reported bit depth. Numeric frame rates can be rational, such as 30000/1001. Frame-rate comparison tolerates less than 0.001 fps rounding.

Every reported video/audio track is checked against its applicable rules. Pass means all configured reported properties match; Unknown means required metadata was not reported; Mismatch means a requirement conflicts with the file or a required track is absent. No delivery target is inferred from a filename. Example presets are editable starting points, not broadcaster or platform certification.

Preset checks are available for either loaded comparison file, without requiring both slots, and for batch results. Presets persist in local app settings. Duplicate makes a copy, Edit changes it, and Delete removes it after confirmation.

## Batch inspection

Choose a folder, optionally include subfolders, select a delivery preset, and click Inspect folder. Supported media extensions are discovered in a background worker. Metadata inspection is the default. Enable Full decode scan to read every audio/video payload or decode still-image frames. Large batches can take considerable time.

Headers appear while a full scan is running. Cancel retains completed rows and available headers, marks unfinished scans unverified, and never labels cancellation as a passed decode. Unreadable files remain visible with error details. Use the text filter to find HDR, missing audio, mismatches, unreadable files or particular codecs/frame rates. Multiple words must all match. Click column headers to sort; Audio details reveals channels and sample rates. Select a row for findings and requirement details. Double-click a row to inspect that file in the main app.

CSV export includes all inspected rows, including rows currently filtered out, paths, decode/error messages, and preset check details. Comparison export and CSV contain local filenames/paths and metadata; review before sharing them.

## Audio analysis and playback meters

Analyze audio measures the currently selected audio track. The full-track scan reports integrated loudness in LUFS, loudness range in LU, and true peak in dBFS from FFmpeg's EBU R128 filter. Silence detection uses −50 dB for at least 0.5 seconds. Silence regions describe that threshold; they do not establish editorial errors. No loudness delivery target or pass/fail judgment is assumed.

The waveform is a mono downmix envelope at 48 kHz, reduced to at most 1,500 min/max points. It is an overview, not a true-peak measurement or a per-channel phase display. Clicking seeks the inspector player. Copy analysis copies the measurements and silence regions as JSON. Full-track measurements are included in the inspector report after completion; cancellation leaves no complete measurement.

Audio-only playback shows decoded per-channel sample-peak meters before the playback volume control. Video meters are optional in Viewer tools. Sample peaks, true peaks and loudness are different measurements.

## Viewer tools, copy and workspace

Video Viewer tools adds previous/next-frame actions, fullscreen and optional 90%/80% picture guides. J/K/L remains available, including hold K and tap J/L to step. Fullscreen also provides frame-step/play controls; Escape exits it. Picture guides follow the display aspect ratio and rotation.

Still-image hovering reads the displayed preview's pixel coordinates and RGB/alpha. These are color-managed preview values, not untouched sensor or high-bit-depth source samples. Histogram shows sampled displayed sRGB values, excludes fully transparent pixels and uses a logarithmic vertical scale.

The gear → Copy and filename context menu copy filenames, paths, readable metadata summaries and available source timecode. Gear → Recent files keeps the latest 15 successfully inspected files. Missing files remain disabled in that menu. The next normal launch restores the selected file, tab, window geometry and sidebar width; an explicit command-line file takes precedence. Playback does not automatically start.

## Health states

Headers read means the metadata parser returned usable information. Payload not scanned is separate. A completed decode with no reported warnings means FFmpeg decoded the selected audio/video payload without reported decoder warnings; it does not certify perceptual quality, delivery compliance or attachments/subtitles. Warnings and failures preserve decoder messages and available explanations. Stills distinguish metadata reading from successful image decoding.

## Overview delivery highlighting

The upper-right toolbar contains a compact checkbox and **Highlight Specs** label. Click the label to open a dropdown containing the saved delivery preset selector and its checklist. Click the checkbox to evaluate the selected preset and color the displayed values: green matches, red conflicts, and amber is unknown. Properties without a configured rule keep their normal appearance. Hover a colored value for its expected and actual values. A combined value such as resolution reflects both width and height rules. Use **Edit delivery presets…** at the bottom of the dropdown to open the Delivery presets page with the current preset selected. Saved edits immediately refresh the dropdown and highlights. The dropdown shows every requirement, including specs not otherwise shown in the Overview, and contains the optional Measure audio action. Unchecking the toolbar checkbox stops evaluation and clears the colors; opening the label dropdown does not toggle the checkbox. Choosing another audio track displays that track's results. The selected preset and highlighting preference persist between sessions; changing presets does not change media.

Presets now support container, minimum/maximum total bitrate, per-video-track bitrate, per-audio-track bitrate, and duration. Bitrates are decimal Mb/s; duration is seconds; boundaries are inclusive. Bitrate checks use reported average metadata, not instantaneous or maximum encoder bitrate. Missing stream bitrate remains Unknown. QuickTime/MPEG-4 containers are distinguished with their reported major brand; an ambiguous demuxer family remains Unknown rather than relying on a filename extension.

Require **Embedded timecode: Present** to reject missing source timecode; optionally require Drop-frame or Non-drop. A generated relative playback clock does not satisfy embedded timecode. Audio channel layout is checked per audio track using the reported layout, separately from channel count and track count.

Integrated loudness minimum/maximum (LUFS) and maximum true peak (dBTP) use completed FFmpeg EBU R128 measurements of each original audio track. For a target of −23 LUFS ±1, set minimum −24 and maximum −22. **Measure audio** reads every full audio track, can be cancelled, and updates the checklist when all tracks complete. Unknown remains amber until measured. Measurements from Analyze audio also populate the corresponding track's checks. Solo/Mute, monitor mono, and playback volume never affect these measurements. Presets containing measured rules trigger full audio measurements during a new batch inspection. Selecting a measured preset after an existing metadata-only batch shows Unknown until the batch is inspected again. Comparison can use the current inspector's completed measurements; independently loaded comparison files remain Unknown until measurements are supplied by inspecting/measuring them in the main window and choosing Use loaded file.

JSON inspector reports include the active preset and all check rows; batch CSV and comparison exports also include the expanded rules and results.

## Channel auditioning and phase

Choose **Channels…** in the audio Overview card (Sound details for audio-only files), or **Viewer tools → Audio channels · Solo / Mute / Phase…**. Each channel has **S** (Solo) and **M** (Mute); several channels may be selected. Mute takes precedence over Solo. One solo channel is heard in both speakers. Stereo channels retain left/right placement for muting; multichannel selections use a stereo fold-down with headroom, not a mastering downmix. Monitor in mono lets you audition cancellation. Closing the panel, opening a different file, or choosing another audio track resets monitoring choices.

Source channel assignments come from reported layouts (Left, Right, Center, LFE, etc.); unreported layouts stay numbered. Embedded track titles are displayed but content roles such as dialogue/music are never guessed. The panel works on channels within the selected audio track; use the existing track arrows to audition separate tracks.

The phase readout measures the selected pair's decoded waveform correlation before Solo/Mute/volume. +1 indicates correlated signals, 0 uncorrelated signals, and −1 opposite polarity. Negative values flag possible mono cancellation; they do not by themselves establish a fault. Silence and identical channel selections have no correlation result. The readout is a smoothed live sample-window measurement, not a whole-file phase certification or timing-offset measurement.

Auditioning uses original decoded PCM from the player and a native stereo output device at 1× forward playback. While Solo/Mute/mono monitoring is active, native player audio is muted to avoid duplicate sound; shuttle speeds are silent in this mode. Seeking, pause, track changes and file changes clear the monitoring buffer. The output device must support stereo float PCM at the decoded sample rate; failures appear in the channel panel. Normal playback uses its existing native audio path when monitoring choices are reset.
