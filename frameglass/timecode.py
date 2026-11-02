"""Read embedded SMPTE timecode and derive a continuous, inclusive Out.

FFprobe exposes MOV timecode in stream tags and MXF timecode in format tags.
Timecode arithmetic uses exact rational rates, never elapsed-clock formatting.
"""
import re
from fractions import Fraction

MISSING = 'Not available'


def positive_fraction(value):
    try:
        value = Fraction(str(value))
        return value if value > 0 else None
    except (ValueError, ZeroDivisionError):
        return None


def media_tracks(data):
    streams = data.get('streams', [])
    video = [s for s in streams if s.get('codec_type') == 'video'
             and not s.get('disposition', {}).get('attached_pic')]
    audio = [s for s in streams if s.get('codec_type') == 'audio']
    return video, audio


def timecode_to_frames(text, nominal, drop):
    match = re.fullmatch(r'(\d{2}):(\d{2}):(\d{2})[:;.](\d{2,3})', text)
    if not match:
        raise ValueError('Unsupported timecode format')
    h, m, s, f = map(int, match.groups())
    if h > 23 or m > 59 or s > 59 or f >= nominal:
        raise ValueError('Timecode is outside its frame-rate range')
    if drop and m % 10 and s == 0 and f < drop:
        raise ValueError('Timecode uses a skipped drop-frame label')
    minutes = h * 60 + m
    return ((h * 3600 + m * 60 + s) * nominal + f
            - drop * (minutes - minutes // 10))


def frames_to_timecode(frames, nominal, drop, wrap_24h=True):
    day = nominal * 86400 - drop * (1440 - 144)
    if wrap_24h:
        frames %= day
    if drop:
        ten_minutes = nominal * 600 - drop * 9
        cycles, remainder = divmod(frames, ten_minutes)
        # Each ten-minute block starts with one minute without dropped labels.
        short_minutes = max(0, (remainder - drop) // (nominal * 60 - drop))
        frames += cycles * drop * 9 + short_minutes * drop
    seconds, frame = divmod(frames, nominal)
    minutes, second = divmod(seconds, 60)
    hour, minute = divmod(minutes, 60)
    sep = ';' if drop else ':'
    return f'{hour:02}:{minute:02}:{second:02}{sep}{frame:02}'


def clip_timecode(data):
    result = dict(in_=MISSING, out=MISSING, mode=MISSING, source='', estimated=False,
                  note='No embedded timecode reported by FFprobe.')
    video, _ = media_tracks(data)
    primary = video[0] if video else None
    streams = data.get('streams', [])
    tmcd = [s for s in streams if s.get('codec_tag_string') == 'tmcd']
    candidates = []
    if primary:
        candidates.append((primary, f"Video track {primary.get('index', 0)}"))
    candidates.append((data.get('format', {}), 'Container'))
    # A standalone timecode track cannot safely be associated with one of
    # several video tracks without explicit linkage from the container.
    if len(video) <= 1:
        candidates.extend((s, f"Timecode track {s.get('index', '?')}") for s in tmcd)
    found = []
    for owner, source in candidates:
        tags = {k.lower(): v for k, v in owner.get('tags', {}).items()}
        raw = str(tags.get('timecode', '')).strip()
        if raw:
            found.append((raw, source, owner))
    if not found:
        return result
    raw, source, owner = found[0]
    result['source'] = source
    result['in_'] = raw
    match = re.fullmatch(r'\d{2}:\d{2}:\d{2}([:;.])\d{2,3}', raw)
    if not match:
        result['note'] = 'Embedded timecode format is not supported for calculation.'
        return result
    is_drop = match[1] in ';.'
    result['mode'] = 'Drop-frame' if is_drop else 'Non-drop'
    reference = primary or owner
    fps = positive_fraction(reference.get('r_frame_rate')) or positive_fraction(reference.get('avg_frame_rate'))
    if not fps:
        result['note'] = 'Embedded In found; no reliable video frame rate for Out.'
        return result
    nominal = round(fps)
    supported = nominal in (24, 25, 30, 48, 50, 60, 100, 120) and fps in (Fraction(nominal), Fraction(nominal * 1000, 1001))
    if not supported or (is_drop and fps not in (Fraction(30000, 1001), Fraction(60000, 1001), Fraction(120000, 1001))):
        result['note'] = 'Frame rate and timecode mode are unsupported or inconsistent; Out unavailable.'
        return result
    drop = nominal // 15 if is_drop else 0
    try:
        start = timecode_to_frames(raw, nominal, drop)
    except ValueError as error:
        result['note'] = str(error) + '; Out unavailable.'
        return result
    result['in_'] = frames_to_timecode(start, nominal, drop)
    avg = positive_fraction(reference.get('avg_frame_rate'))
    if avg and avg != fps:
        result['note'] = 'Reported frame rates disagree; Out unavailable for potentially variable-rate media.'
        return result
    if not primary:
        result['note'] = 'No video track available to establish the clip’s last frame.'
        return result
    count = positive_fraction(primary.get('nb_frames'))
    if not count or count.denominator != 1:
        ticks = positive_fraction(primary.get('duration_ts'))
        base = positive_fraction(primary.get('time_base'))
        length = ticks * base if ticks and base else positive_fraction(primary.get('duration'))
        # Never use container duration: audio may extend beyond the video.
        if not length:
            result['note'] = 'Embedded In found; video frame count and duration are unavailable.'
            return result
        count = round(length * fps)
        result['estimated'] = True
    count = int(count)
    if count <= 0:
        result['note'] = 'No complete video frames reported; Out unavailable.'
        return result
    result['out'] = frames_to_timecode(start + count - 1, nominal, drop)
    result['note'] = ('Out is the last frame (inclusive), estimated from video duration.' if result['estimated']
                      else 'Out is the last frame (inclusive), calculated from the reported frame count.')
    result['note'] += ' Assumes continuous timecode.'
    if len(video) > 1:
        result['note'] += ' Uses the first video track.'
    if len({item[0].replace('.', ';') for item in found}) > 1:
        result['note'] += f' Embedded timecodes differ; using {source.lower()}.'
    return result


def duration_timecode(data):
    """Duration is the total frame count from zero, not the inclusive Out label.

    Prefer DF at compatible NTSC rates independently of embedded start TC mode.
    Duration can exceed 24 hours; unlike a source timecode, it must not wrap.
    """
    from .core import duration
    video, _ = media_tracks(data)
    result = dict(value=MISSING, mode='', estimated=False, frames=None,
                  note='No reliable video frame rate or frame count is available.')
    if not video:
        result.update(value=duration(data.get('format', {}).get('duration')), mode='TIME',
                      note='Elapsed time; audio-only media has no video frame rate.')
        return result
    stream = video[0]
    fps = positive_fraction(stream.get('r_frame_rate')) or positive_fraction(stream.get('avg_frame_rate'))
    if not fps:
        return result
    nominal = round(fps)
    if nominal not in (24, 25, 30, 48, 50, 60, 100, 120) or fps not in (Fraction(nominal), Fraction(nominal * 1000, 1001)):
        return result
    average = positive_fraction(stream.get('avg_frame_rate'))
    if average and average != fps:
        result['note'] = 'Reported video rates differ; a single frame-based duration is unavailable.'
        return result
    count = positive_fraction(stream.get('nb_frames'))
    if not count or count.denominator != 1:
        ticks = positive_fraction(stream.get('duration_ts'))
        base = positive_fraction(stream.get('time_base'))
        seconds = ticks * base if ticks and base else positive_fraction(stream.get('duration'))
        if not seconds:
            return result
        count = round(seconds * fps)
        result['estimated'] = True
    count = int(count)
    drop = nominal // 15 if fps in (Fraction(30000, 1001), Fraction(60000, 1001), Fraction(120000, 1001)) else 0
    result.update(value=frames_to_timecode(count, nominal, drop, wrap_24h=False),
                  mode='DF' if drop else 'NDF', frames=count)
    result['note'] = ('Hours:minutes:seconds:frames, using drop-frame counting.' if drop
                      else 'Hours:minutes:seconds:frames. This rate uses non-drop counting.')
    result['note'] += (' Estimated from video duration.' if result['estimated'] else ' From the reported video frame count.')
    result['note'] += ' Duration counts every frame; it is not the last-frame Out value.'
    return result


def playhead_timecode(data, position_ms):
    """Label the displayed frame using source TC, or relative TC if absent."""
    from .core import duration
    video, _ = media_tracks(data)
    if not video:
        return 'Time ' + duration(max(0, position_ms) / 1000)
    reference = video[0]
    fps = positive_fraction(reference.get('r_frame_rate')) or positive_fraction(reference.get('avg_frame_rate'))
    average = positive_fraction(reference.get('avg_frame_rate'))
    if not fps or (average and average != fps):
        return 'Time ' + duration(max(0, position_ms) / 1000)
    nominal = round(fps)
    if nominal not in (24, 25, 30, 48, 50, 60, 100, 120) or fps not in (Fraction(nominal), Fraction(nominal * 1000, 1001)):
        return 'Time ' + duration(max(0, position_ms) / 1000)
    index = max(0, round(Fraction(str(position_ms)) * fps / 1000))
    count = positive_fraction(reference.get('nb_frames'))
    if not count or count.denominator != 1:
        derived_count = duration_timecode(data)['frames']
        count = positive_fraction(derived_count)
    if count and count.denominator == 1:
        index = min(index, int(count) - 1)
    tc = clip_timecode(data)
    source_drop = nominal // 15 if tc['mode'] == 'Drop-frame' else 0
    if tc['in_'] != MISSING and tc['mode'] != MISSING:
        try:
            if source_drop and fps not in (Fraction(30000, 1001), Fraction(60000, 1001), Fraction(120000, 1001)):
                raise ValueError('Unsupported drop frame rate')
            start = timecode_to_frames(tc['in_'], nominal, source_drop)
            return 'TC ' + frames_to_timecode(start + index, nominal, source_drop)
        except ValueError:
            pass
    drop = nominal // 15 if fps in (Fraction(30000, 1001), Fraction(60000, 1001), Fraction(120000, 1001)) else 0
    return 'Rel ' + frames_to_timecode(index, nominal, drop, wrap_24h=False)
