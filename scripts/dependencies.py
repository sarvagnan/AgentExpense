"""Installed dependencies against the lockfiles; install only what differs.

`python3 scripts/dependencies.py npm` runs `npm ci` when the installed packages differ
from `review-dashboard/package-lock.json`; `uv` runs `uv sync --locked` when the server
environment differs from `uv.lock`; `install` does both. The comparison reads package
versions, so a release that only changes the application's own version reinstalls
nothing. Run it through make, which puts the project's Node first on PATH.
Standard library only.
"""
from __future__ import annotations
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

APPLICATION = Path(__file__).resolve().parents[1]


def npm_differences(application=APPLICATION):
    """Package names whose installed version differs from package-lock.json, or None when nothing is installed.

    Optional packages for other platforms may be absent.
    """
    dashboard = Path(application) / 'review-dashboard'
    hidden = dashboard / 'node_modules/.package-lock.json'
    if not hidden.is_file():
        return None
    locked = json.loads((dashboard / 'package-lock.json').read_text()).get('packages', {})
    installed = json.loads(hidden.read_text()).get('packages', {})
    different = [name for name, item in locked.items() if name and not (item.get('optional') and name not in installed)
                 and installed.get(name, {}).get('version') != item.get('version')]
    different += [name for name in installed if name and name not in locked]
    return different


def python_matches(application=APPLICATION, environ=None):
    """Whether the server environment matches uv.lock, checked offline."""
    environ = dict(os.environ if environ is None else environ)
    uv = shutil.which('uv', path=environ.get('PATH'))
    server = Path(application) / 'review-dashboard/server'
    if not uv or not (server / '.venv').is_dir():
        return False
    result = subprocess.run([uv, 'sync', '--locked', '--check', '--offline', '--directory', str(server)],
                            env=environ, capture_output=True, text=True)
    return result.returncode == 0


def install_npm(application=APPLICATION, run=subprocess.run):
    different = npm_differences(application)
    if different == []:
        return False
    print('npm packages are not installed; running npm ci.' if different is None else
          f'{len(different)} npm packages differ from package-lock.json; running npm ci.', flush=True)
    run(['npm', '--prefix', str(Path(application) / 'review-dashboard'), 'ci'], check=True)
    return True


def install_python(application=APPLICATION, run=subprocess.run, matches=python_matches):
    if matches(application):
        return False
    print('The server environment differs from uv.lock; running uv sync --locked.', flush=True)
    run(['uv', 'sync', '--locked', '--directory', str(Path(application) / 'review-dashboard/server')], check=True)
    return True


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if argv not in (['npm'], ['uv'], ['install']):
        sys.exit('usage: dependencies.py npm|uv|install')
    try:
        if argv[0] in ('npm', 'install'):
            install_npm()
        if argv[0] in ('uv', 'install'):
            install_python()
    except subprocess.CalledProcessError as exc:
        sys.exit(f'{exc.cmd[0]} failed with exit status {exc.returncode}.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
