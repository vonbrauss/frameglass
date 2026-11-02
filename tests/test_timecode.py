import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from frameglass.core import executable, probe_args
from frameglass.timecode import clip_timecode, frames_to_timecode, media_tracks, MISSING, duration_timecode, playhead_timecode


def sample(tc='01:00:00:00', fps='24/1', count='48', **extra):
    stream = dict(index=0, codec_type='video', r_frame_rate=fps,
                  avg_frame_rate=fps, nb_frames=count, tags={'timecode': tc})
    stream.update(extra)
    return {'streams': [stream]}


class TimecodeTests(unittest.TestCase):
    def test_non_drop_and_last_frame(self):
        result = clip_timecode(sample())
        self.assertEqual(result['in_'], '01:00:00:00')
        self.assertEqual(result['out'], '01:00:01:23')
        self.assertEqual(result['mode'], 'Non-drop')
        self.assertEqual(clip_timecode(sample(count='1'))['out'], '01:00:00:00')

    def test_drop_frame_minute_and_tenth_minute(self):
        for start, end in [('00:00:59;29', '00:01:00;02'),
                           ('00:09:59;29', '00:10:00;00'),
                           ('23:59:59;29', '00:00:00;00')]:
            with self.subTest(start=start):
                result = clip_timecode(sample(start, '30000/1001', '2'))
                self.assertEqual(result['out'], end)
                self.assertEqual(result['mode'], 'Drop-frame')

    def test_5994_drop_and_fractional_non_drop(self):
        self.assertEqual(clip_timecode(sample('00:00:59;59', '60000/1001', '2'))['out'], '00:01:00;04')
        self.assertEqual(clip_timecode(sample('00:00:59:29', '30000/1001', '2'))['out'], '00:01:00:00')
        self.assertEqual(clip_timecode(sample('23:59:59:23', '24000/1001', '2'))['out'], '00:00:00:00')
        self.assertEqual(frames_to_timecode(107892, 30, 2), '01:00:00;00')

    def test_no_timecode_and_invalid_labels(self):
        self.assertEqual(clip_timecode({'streams': []})['mode'], MISSING)
        for tc in ['00:01:00;00', '01:00:60;00', 'not a timecode', '01:00:00;30']:
            self.assertEqual(clip_timecode(sample(tc, '30000/1001'))['out'], MISSING)
        self.assertEqual(clip_timecode(sample('01:00:00;00', '24'))['out'], MISSING)

    def test_duration_estimation_and_no_container_guess(self):
        data = sample(count='N/A', duration_ts=48000, time_base='1/24000')
        result = clip_timecode(data)
        self.assertEqual(result['out'], '01:00:01:23')
        self.assertTrue(result['estimated'])
        data = sample(count='N/A')
        data['format'] = {'duration': '50'}
        self.assertEqual(clip_timecode(data)['out'], MISSING)

    def test_variable_rate_and_missing_rate(self):
        self.assertEqual(clip_timecode(sample(avg_frame_rate='239/10'))['out'], MISSING)
        self.assertEqual(clip_timecode(sample(fps='0/0'))['out'], MISSING)

    def test_sources_and_counts(self):
        data = sample(tags={})
        data['format'] = {'tags': {'timecode': '02:00:00:00'}}
        self.assertEqual(clip_timecode(data)['in_'], '02:00:00:00')
        del data['format']
        data['streams'] += [dict(index=1, codec_type='data', codec_tag_string='tmcd', tags={'timecode': '03:00:00:00'}, nb_frames='1'),
                            dict(codec_type='audio', channels=8), dict(codec_type='audio', channels=2),
                            dict(codec_type='video', disposition={'attached_pic': 1})]
        self.assertEqual(clip_timecode(data)['out'], '03:00:01:23')
        video, audio = media_tracks(data)
        self.assertEqual((len(video), len(audio)), (1, 2))

    def test_conflicting_embedded_sources(self):
        data = sample()
        data['format'] = {'tags': {'timecode': '02:00:00:00'}}
        result = clip_timecode(data)
        self.assertEqual(result['in_'], '01:00:00:00')
        self.assertIn('differ', result['note'])

    def test_real_mov_and_mxf(self):
        with tempfile.TemporaryDirectory() as directory:
            for container, codec in [('mov', 'libx264'), ('mxf', 'mpeg2video')]:
                path = Path(directory) / f'clip.{container}'
                subprocess.run([executable('ffmpeg'), '-nostdin', '-v', 'error',
                    '-f', 'lavfi', '-i', 'testsrc2=size=720x480:rate=30000/1001',
                    '-frames:v', '60', '-c:v', codec, '-timecode', '01:00:00;00', str(path)], check=True)
                data = json.loads(subprocess.check_output([executable('ffprobe'), *probe_args(path)]))
                result = clip_timecode(data)
                with self.subTest(container=container):
                    self.assertEqual(result['in_'], '01:00:00;00')
                    self.assertEqual(result['out'], '01:00:01;29')
                    self.assertEqual(result['mode'], 'Drop-frame')

    def test_duration_frame_count_not_last_frame(self):
        result = duration_timecode(sample(count='48'))
        self.assertEqual(result['value'], '00:00:02:00')
        self.assertEqual(result['mode'], 'NDF')
        self.assertEqual(duration_timecode(sample(count='1'))['value'], '00:00:00:01')

    def test_duration_drop_frame_independent_of_source(self):
        for fps, count in [('30000/1001', 107892), ('60000/1001', 215784)]:
            result = duration_timecode(sample('01:00:00:00', fps, str(count)))
            self.assertEqual(result['value'], '01:00:00;00')
            self.assertEqual(result['mode'], 'DF')
        self.assertEqual(duration_timecode(sample(fps='30000/1001', count='1800'))['value'], '00:01:00;02')
        self.assertEqual(duration_timecode(sample(fps='30000/1001', count='17982'))['value'], '00:10:00;00')

    def test_duration_does_not_wrap_after_24_hours(self):
        self.assertEqual(duration_timecode(sample(fps='30000/1001', count=str(107892 * 25)))['value'], '25:00:00;00')
        self.assertEqual(duration_timecode(sample(fps='24', count=str(24 * 3600 * 25)))['value'], '25:00:00:00')

    def test_duration_estimates_and_fallbacks(self):
        result = duration_timecode(sample(count='N/A', duration_ts=48000, time_base='1/24000'))
        self.assertEqual(result['value'], '00:00:02:00')
        self.assertTrue(result['estimated'])
        self.assertEqual(duration_timecode(sample(avg_frame_rate='20'))['value'], MISSING)
        self.assertEqual(duration_timecode(sample(fps='0/0'))['value'], MISSING)
        audio = duration_timecode({'format': {'duration': '12.5'}, 'streams': [{'codec_type': 'audio'}]})
        self.assertEqual(audio['mode'], 'TIME')
        self.assertEqual(audio['value'], '00:00:12.500')

    def test_playhead_uses_source_timecode_and_drop_boundaries(self):
        self.assertEqual(playhead_timecode(sample(), 1000), 'TC 01:00:01:00')
        self.assertEqual(playhead_timecode(sample('00:00:59;29', '30000/1001', '60'), 33.367), 'TC 00:01:00;02')
        self.assertEqual(playhead_timecode(sample('23:59:59:23', '24', '60'), 41.667), 'TC 00:00:00:00')
        self.assertEqual(playhead_timecode(sample(), 2000), 'TC 01:00:01:23')
        self.assertEqual(playhead_timecode(sample(tags={}), 1000), 'Rel 00:00:01:00')
        self.assertEqual(playhead_timecode({'streams': []}, 1000), 'Time 00:00:01.000')
