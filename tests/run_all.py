"""Run each test file in its own process and keep a log per file.

One crashing file cannot hide the results of the others, and Python's fault
handler records where a native crash happened.

    .venv/bin/python tests/run_all.py

Logs go to test-logs/ in the project folder.
"""
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LOGS = ROOT / 'test-logs'


def main():
    LOGS.mkdir(exist_ok=True)
    environment = dict(os.environ, QT_QPA_PLATFORM='offscreen', PYTHONFAULTHANDLER='1')
    summary = []
    for test in sorted((ROOT / 'tests').glob('test_*.py')):
        command = [sys.executable, '-X', 'faulthandler', '-m', 'unittest', 'discover', '-s', 'tests', '-p', test.name, '-v']
        try:
            result = subprocess.run(command, cwd=ROOT, env=environment, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=900)
            output, code = result.stdout.decode(errors='replace'), result.returncode
        except subprocess.TimeoutExpired as expired:
            output, code = (expired.stdout or b'').decode(errors='replace'), 'timeout'
        (LOGS / (test.stem + '.log')).write_text(output, encoding='utf-8')
        state = 'PASS' if code == 0 else 'TIMEOUT' if code == 'timeout' else f'CRASH (signal {-code})' if code < 0 else 'FAIL'
        summary.append(f'{state:18} {test.name}')
        print(summary[-1], flush=True)
    (LOGS / 'summary.txt').write_text('\n'.join(summary) + '\n', encoding='utf-8')
    print('Logs:', LOGS)
    sys.exit(0 if all(line.startswith('PASS') for line in summary) else 1)


if __name__ == '__main__':
    main()
