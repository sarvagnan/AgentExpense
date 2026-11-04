"""Owner login and maintainer-only, offline adapter pin upgrades."""
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
from node_toolchain import node_environment
from adapters import get_adapter, PACKAGES, login_environment


def login(settings):
    adapter = get_adapter(settings)
    command = adapter.login_command()
    if not command:
        print('Set the API key environment variable or dedicated keyFile configured in local.json; then run make agent-check LIVE=1 ADAPTER=' + settings.adapter)
        return 0
    return subprocess.call(command, env=login_environment(adapter, settings))


def upgrade(settings, version='', *, dry_run=False):
    if settings.adapter not in PACKAGES:
        raise ValueError('Only pinned CLI adapters have an npm upgrade')
    package, binary = PACKAGES[settings.adapter]
    base_env = node_environment(settings.application)
    if not version:
        version = subprocess.check_output(['npm', 'view', package, 'version'], env=base_env, text=True).strip()
    if not re.fullmatch(r'\d+\.\d+\.\d+(?:-[A-Za-z0-9.-]+)?', version):
        raise ValueError('VERSION must be an exact npm version')
    print('Checking ' + settings.adapter + ' ' + version, flush=True)
    with tempfile.TemporaryDirectory(dir=os.environ.get('EXPENSES_TEST_TMP','/tmp'), prefix='expense-agent-upgrade-') as folder:
        work = Path(folder)
        npm_env = dict(base_env, HOME=str(work / 'npm-home'), npm_config_cache=str(work / 'npm-cache'))
        (work / 'npm-home').mkdir()
        subprocess.run(['npm', 'install', '--prefix', str(work), '--save-exact', package + '@' + version], check=True, env=npm_env)
        ledger, old, _ = fixture(work)
        # Candidate executable/resource-loader stay outside the installed app;
        # application schemas and the reviewed extension remain shared.
        candidate = replace(settings, codex=str(work / 'node_modules/.bin' / binary),
                            agent_binary=str(work / 'node_modules/.bin' / binary),
                            codex_home=work / 'codex-home', state_dir=work / 'state', ledger=ledger, old_ledger=old)
        try:
            if settings.adapter == 'codex':
                check_offline(candidate, work)
            else:
                get_adapter(candidate).offline_check(work)
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
                            package + '@' + version], check=True, env=npm_env)
        except Exception:
            for path, content in zip(paths, before):
                path.write_bytes(content)
            raise
        print('PASS pinned ' + settings.adapter + ' ' + version)
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
