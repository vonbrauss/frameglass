"""Media interpretation independent of the GUI."""
import shutil
import json
import sys
import os
from pathlib import Path


def tool_directory():
    if sys.platform=='darwin':
        return Path.home() / 'Library' / 'Application Support' / 'Frameglass' / 'media-tools'
    return Path.home() / '.local' / 'share' / 'Frameglass' / 'media-tools'


def managed_executable(name):
    root=tool_directory()
    try:
        receipt=json.loads((root/'active.json').read_text(encoding='utf-8'))
        path=Path(receipt['tools'][name])
        if path.resolve().is_relative_to(root.resolve()) and path.is_file() and os.access(path,os.X_OK):
            return str(path)
    except (OSError,ValueError,KeyError,TypeError):pass
    return None


def executable(name, configured=''):
    if configured:
        return str(Path(configured).expanduser())
    return managed_executable(name) or shutil.which(name) or next((str(p / name) for p in (Path('/opt/homebrew/bin'), Path('/usr/local/bin')) if (p / name).is_file()), name)


def probe_args(path):
    return ['-v', 'error', '-show_error', '-show_format', '-show_streams', '-show_chapters', '-show_programs', '-of', 'json', '-i', str(Path(path).absolute())]


def scan_args(path):
    return ['-nostdin', '-hide_banner', '-v', 'warning', '-progress', 'pipe:1', '-nostats', '-err_detect', 'careful', '-i', str(Path(path).absolute()), '-map', '0:v?', '-map', '0:a?', '-sn', '-dn', '-f', 'null', '-']


def duration(value):
    try:
        seconds = float(value)
        return f'{int(seconds // 3600):02}:{int(seconds % 3600 // 60):02}:{seconds % 60:06.3f}'
    except (ValueError, TypeError):
        return 'Unknown'


def size(value):
    try:
        n = float(value)
        for unit in ['B', 'KB', 'MB', 'GB', 'TB']:
            if n < 1000 or unit == 'TB':
                return f'{n:,.2f} {unit}'
            n /= 1000
    except (ValueError, TypeError):
        return 'Unknown'


def rate(value):
    try:
        a, _, b = str(value).partition('/')
        n = float(a) / (float(b) if b else 1)
        return f'{n:.3f}'.rstrip('0').rstrip('.') if n else 'Unknown'
    except (ValueError, ZeroDivisionError):
        return 'Unknown'


def findings(data):
    streams = data.get('streams', [])
    result = []
    if not streams:
        result.append(('Issue', 'No streams found', 'The file could not be identified as playable media.'))
    for s in streams:
        label = f"Stream {s.get('index', '?')}"
        if s.get('codec_name') in (None, 'unknown'):
            result.append(('Warning', f'{label}: unidentified codec', 'Playback or decoding support may be unavailable.'))
        if s.get('codec_type') == 'video' and not s.get('disposition', {}).get('attached_pic'):
            if not s.get('width') or not s.get('height'):
                result.append(('Issue', f'{label}: missing dimensions', 'The video header may be incomplete or unsupported.'))
            if s.get('color_transfer') in ('smpte2084', 'arib-std-b67'):
                result.append(('Info', f'{label}: HDR video', 'Appearance depends on display and player HDR support.'))
    if result:
        return result
    return [('Info', 'No obvious metadata issues', 'Run a full decode scan to check the audio and video payload. Metadata alone does not verify integrity.')]


def decoder_advice(log):
    """Conservative explanations; decoder messages are evidence, not repair guarantees."""
    lower = log.lower()
    notes = []
    rules = [
        (('moov atom not found', 'invalid data found', 'error reading header'), 'Container/header problem: the file may be incomplete, damaged, or mislabeled. Compare its size with the source and try copying or downloading it again.'),
        (('corrupt', 'error while decoding', 'invalid nal', 'concealing'), 'Damaged or undecodable payload: compare against the original source. A remux cannot restore missing or corrupted frames.'),
        (('non-monoton', 'invalid timestamp', 'dts', 'pts has no value'), 'Timestamp warning: seeking or audio/video timing may be affected. A remux may help some timestamp problems, but should be tested on a separate copy.'),
        (('unknown decoder', 'decoder not found', 'unsupported codec'), 'Decoder support issue: this FFmpeg build may lack the required decoder. This does not establish that the file itself is damaged.'),
        (('permission denied', 'operation not permitted'), 'Access problem: check file permissions and macOS access to the containing folder.'),
    ]
    for needles, explanation in rules:
        if any(needle in lower for needle in needles):
            notes.append(explanation)
    return notes
