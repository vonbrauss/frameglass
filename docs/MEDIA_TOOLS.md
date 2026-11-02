# Media tools and updates

Frameglass uses FFprobe for video/audio metadata and FFmpeg for thumbnails, decode scans and audio analysis. Still-image inspection and Qt playback have separate dependencies.

## On launch

A background check runs each time the app normally opens. It executes each selected tool with `-version`, checks the official stable FFmpeg release, and checks available private builds. If Homebrew owns the selected tools, the matching formula's available package version and revision are also checked through Homebrew's formula API. Read-only Homebrew inventory commands run with automatic updates disabled. No install, upgrade or tool-source change happens during this check.

Missing/unusable tools or updates show a Review tools banner. Later dismisses the banner for the current session. The complete status remains accessible through Settings gear → Media tools & updates. When a network check fails, the app reports that updates could not be verified; it does not claim the tools are current. An upstream release can precede a matching Homebrew package or private build; the available versions are displayed separately.

## Keep Homebrew

When both selected tools belong to Homebrew, Keep using Homebrew is the default option. Use existing Homebrew tools validates and selects the existing pair without running install/update commands. Update with Homebrew first asks for confirmation, refreshes Homebrew metadata, then installs or upgrades only the selected FFmpeg formula and required dependencies. Homebrew can affect other applications sharing those tools. Frameglass disables automatic cleanup and unrelated dependent-package upgrade checks for these commands.

Versioned formulae remain versioned: selecting ffmpeg@7 does not silently migrate to another major formula. Pins are not removed. A pinned formula can still be selected for use, or managed directly through Homebrew. Homebrew itself is never installed automatically. If stopped during an update, Homebrew can have partially changed its packages; recheck the tools and use Homebrew's normal repair workflow if necessary.

## Private tools without Homebrew

Choose Private tools for Frameglass to download a release pair matching the Mac's architecture. The confirmation names the source, release and destination. Files are staged in the app's own media-tools directory. Each archive is checked against its published SHA-256 checksum, only the requested executable is extracted, and architecture and Apple-anchored Developer ID signatures are verified. The provider's expected signing team is pinned to KU3N25YGLU. A changed identity stops installation. Gatekeeper/security protections are not disabled.

Both executables must identify themselves and report the checked release before the pair is activated atomically. A failed or cancelled private install does not change the active pair. Previous private versions remain on disk. Homebrew and other system tools are not overwritten. Switching tool sources requires an explicit user choice.

## Existing or custom tools

Locate existing tools chooses both files before applying the paths. Advanced paths can also be edited in Settings. Tool checks reveal invalid paths, launch errors and unrecognized versions. Development/nightly versions are identified separately instead of automatically treating them as older than a stable release.

## Sources

- Official release metadata: https://ffmpeg.org/download.html
- Private macOS builds, checksums and signing information: https://ffmpeg.martin-riedl.de/
- Homebrew package metadata: https://formulae.brew.sh/
- Homebrew command reference: https://docs.brew.sh/Manpage
