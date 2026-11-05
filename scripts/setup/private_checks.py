"""Run the selected ledger's own checks in <ledger>/checks/ and report pass or fail.

- test*.py files run with unittest, one file at a time;
- any other executable file runs as a program;
- everything else (README.md, helper modules) is ignored.

Each check runs from the ledger root with LEDGER_DIR and AGENTEXPENSE_APP (the
application root) set. A check passes when it exits with status 0. Checks only
read the ledger; they never change it.
"""
import os
from pathlib import Path
import subprocess
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from project_paths import APPLICATION_ROOT, ledger_root

TIMEOUT = float(os.environ.get('PRIVATE_CHECKS_TIMEOUT', '600'))


def discover(folder: Path):
    if not folder.is_dir():
        return []
    checks = []
    for path in sorted(folder.iterdir()):
        if path.name.startswith(('.', '_')) or not path.is_file():
            continue
        if path.suffix == '.py' and path.name.startswith('test'):
            checks.append(('unittest', path))
        elif os.access(path, os.X_OK):
            checks.append(('program', path))
    return checks


def command(kind: str, path: Path):
    if kind == 'unittest':
        return [sys.executable, '-m', 'unittest', 'discover', '-s', str(path.parent), '-p', path.name, '-t', str(path.parent)]
    return [str(path)]


def run(root: Path, out=sys.stdout) -> int:
    folder = root / 'checks'
    checks = discover(folder)
    if not checks:
        print(f'No private checks in {folder}.', file=out)
        return 0
    env = {**os.environ, 'LEDGER_DIR': str(root), 'AGENTEXPENSE_APP': str(APPLICATION_ROOT),
           'PYTHONDONTWRITEBYTECODE': '1'}
    failed = 0
    for kind, path in checks:
        started = time.monotonic()
        try:
            result = subprocess.run(command(kind, path), cwd=root, env=env, text=True, capture_output=True,
                                    timeout=TIMEOUT, stdin=subprocess.DEVNULL)
            ok, output = result.returncode == 0, result.stdout + result.stderr
        except subprocess.TimeoutExpired as exc:
            ok, output = False, f'Timed out after {TIMEOUT:g} s.\n' + str(exc.output or '')
        except OSError as exc:
            ok, output = False, f'Could not run: {exc.strerror}.'
        seconds = time.monotonic() - started
        print(f'{"PASS" if ok else "FAIL"} {path.name} ({seconds:.1f} s)', file=out, flush=True)
        if not ok:
            failed += 1
            for line in output.strip().splitlines()[-40:]:
                print('    ' + line, file=out)
    print(f'{len(checks) - failed} of {len(checks)} private checks passed.', file=out)
    return 1 if failed else 0


if __name__ == '__main__':
    root = ledger_root()
    if not (root / 'ledger.json').is_file():
        raise SystemExit(f'No ledger at {root}. Set LEDGER_DIR to your ledger.')
    sys.exit(run(root))
