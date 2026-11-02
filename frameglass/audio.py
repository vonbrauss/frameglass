"""Audio-specific presentation of FFprobe metadata; never infer measured quality."""
from .core import duration, size
from .timecode import media_tracks


def audio_tags(data, stream):
    return {str(k).lower().replace(' ', '_'): str(v) for source in (data.get('format', {}), stream)
            for k, v in source.get('tags', {}).items()}


def audio_overview(data, stream):
    fmt = data.get('format', {})
    tags = audio_tags(data, stream)
    def tag(*names):
        return next((tags[n] for n in names if tags.get(n)), '')
    def present(rows):
        return [(name, value) for name, value in rows if value]
    hz = stream.get('sample_rate')
    try: sample_rate = f'{int(hz)/1000:g} kHz'
    except (TypeError, ValueError): sample_rate = 'Not reported'
    channels = stream.get('channels')
    layout = stream.get('channel_layout')
    channel_summary = f"{channels} · {layout}" if channels and layout else str(channels or 'Not reported')
    bits = stream.get('bits_per_raw_sample') or stream.get('bits_per_sample')
    bits = f'{bits}-bit' if str(bits).isdigit() and int(bits) > 0 else 'Not reported'
    # Container bitrate can include artwork/tags. Use only the selected stream's rate.
    try: bitrate = f"{int(stream['bit_rate'])/1000:g} kb/s"
    except (KeyError, TypeError, ValueError): bitrate = 'Not reported'
    identity = present([('Title',tag('title')),('Artist',tag('artist','author')),('Album',tag('album'))])
    music = present([('Album artist',tag('album_artist','albumartist')),('Track',tag('track','tracknumber')),
                     ('Disc',tag('disc','discnumber')),('Genre',tag('genre')),('Date',tag('date','year')),
                     ('Composer',tag('composer')),('Copyright',tag('copyright')),('Comment',tag('comment','description'))])
    recording = present([('Description',tag('description') if any(tag(k) for k in ('originator','origination_date','time_reference')) else ''),('Originator',tag('originator')),('Reference',tag('originator_reference')),
                         ('Recorded date',tag('origination_date')),('Recorded time',tag('origination_time')),
                         ('Time reference',tag('time_reference')),('Coding history',tag('coding_history'))])
    # BWF time_reference is a count of samples since midnight, not frame timecode.
    reference = tag('time_reference')
    if reference and hz:
        try:
            samples, rate = int(reference), int(hz)
            if samples >= 0 and rate > 0:
                recording.append(('BWF start',duration(samples/rate) + ' · sample-based'))
        except (ValueError,TypeError): pass
    technical = [('Codec',stream.get('codec_long_name') or stream.get('codec_name','Unknown')),
                 ('Bit depth',bits),('Sample format',stream.get('sample_fmt','Not reported')),
                 ('Channel layout',layout or 'Not reported'),('Stream bitrate',bitrate)]
    if stream.get('profile'):technical.append(('Profile',stream['profile']))
    if tag('language') not in ('','und'):technical.append(('Language',tag('language')))
    if tag('encoder','encoded_by'):technical.append(('Encoder',tag('encoder','encoded_by')))
    file_rows = [('Format',fmt.get('format_long_name') or fmt.get('format_name','Unknown')),
                 ('File size',size(fmt.get('size'))),('Audio tracks',str(len(media_tracks(data)[1])))]
    return dict(identity=identity,music=music,recording=recording,technical=technical,file=file_rows,
                facts=[('DURATION',duration(stream.get('duration') or fmt.get('duration'))),
                       ('SAMPLE RATE',sample_rate),('CHANNELS',channel_summary),('BITRATE',bitrate)])
