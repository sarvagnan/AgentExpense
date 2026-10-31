"""Owner login and maintainer-only, offline Codex pin upgrades."""
from dataclasses import replace
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile

from agent import codex_environment
from agent_check import OfflineFailure, check_offline, fixture, require_codex
from settings import Settings


def login(settings):
    require_codex(settings)
    settings.codex_home.mkdir(parents=True, exist_ok=True)
    return subprocess.call([settings.codex, 'login', '--device-auth'], env=codex_environment(settings))


def upgrade(settings, version='', *, dry_run=False):
    if not version:
        version = subprocess.check_output(['npm', 'view', '@openai/codex', 'version'], text=True).strip()
    if not re.fullmatch(r'\d+\.\d+\.\d+(?:-[A-Za-z0-9.-]+)?', version):
        raise ValueError('VERSION must be an exact npm version')
    print('Checking Codex ' + version, flush=True)
    with tempfile.TemporaryDirectory(dir='/tmp', prefix='expense-agent-upgrade-') as folder:
        work = Path(folder)
        subprocess.run(['npm', 'install', '--prefix', str(work), '--save-exact', '@openai/codex@' + version], check=True)
        ledger, old, _ = fixture(work)
        candidate = replace(settings, codex=str(work / 'node_modules/.bin/codex'),
                            codex_home=work / 'codex-home', state_dir=work / 'state', ledger=ledger, old_ledger=old)
        try:
            check_offline(candidate, work)
        except OfflineFailure as exc:
            if exc.issues:
                raise
            print('Expected empty-home login failure; all isolation checks passed')
        if dry_run:
            print('PASS dry check; pin unchanged')
            return 0
        # Keep both manifests unchanged if npm cannot resolve/install the pin.
        dashboard = settings.application / 'review-dashboard'
        paths = [dashboard / name for name in ('package.json', 'package-lock.json')]
        before = [path.read_bytes() for path in paths]
        try:
            subprocess.run(['npm', '--prefix', str(dashboard), 'install', '--save-exact',
                            '@openai/codex@' + version], check=True)
        except Exception:
            for path, content in zip(paths, before):
                path.write_bytes(content)
            raise
        print('PASS pinned Codex ' + version)
        return 0


def main():
    try:
        settings = Settings.from_env()
        if sys.argv[1:] == ['login']:
            return login(settings)
        if sys.argv[1:] == ['upgrade']:
            return upgrade(settings, os.getenv('VERSION', ''), dry_run=os.getenv('DRY_RUN') == '1')
        raise ValueError('Use login or upgrade')
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        print('FAIL: ' + (str(exc) if isinstance(exc, ValueError) else type(exc).__name__))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
