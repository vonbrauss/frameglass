"""Create the sample clips the tests read from /private/tmp.

Run once before the test suite: .venv/bin/python tests/make_fixtures.py
Existing clips are kept; pass --force to recreate them.
"""
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from frameglass.core import executable

FOLDER = Path('/private/tmp')
CLIPS = {
    # The tests expect exactly this: 640-wide 24 fps H.264 first, 48 kHz stereo AAC second, 2 seconds.
    'frameglass-test.mp4': ['-f', 'lavfi', '-i', 'testsrc2=size=640x360:rate=24', '-f', 'lavfi', '-i', 'sine=frequency=440:sample_rate=48000',
                            '-t', '2', '-c:v', 'libx264', '-pix_fmt', 'yuv420p', '-c:a', 'aac', '-ac', '2'],
    # Non-drop source timecode starting at 01:00:00:00.
    'frameglass-timecode.mov': ['-f', 'lavfi', '-i', 'testsrc2=size=640x360:rate=24', '-t', '3', '-c:v', 'libx264', '-pix_fmt', 'yuv420p',
                                '-timecode', '01:00:00:00'],
}


def main():
    ffmpeg = executable('ffmpeg')
    force = '--force' in sys.argv
    for name, arguments in CLIPS.items():
        target = FOLDER / name
        if target.exists() and not force:
            print('Kept', target)
            continue
        subprocess.run([ffmpeg, '-nostdin', '-v', 'error', '-y', *arguments, str(target)], check=True)
        print('Created', target)


if __name__ == '__main__':
    main()
