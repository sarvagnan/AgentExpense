from contextlib import contextmanager
import atexit
from dataclasses import replace
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
from PIL import Image
from isolation import snapshot, assert_unchanged

SERVER = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SERVER))
from app import create_app
from settings import Settings, APPLICATION
from store import dump


# Every test retains its own writable repository. Only exact hledger oracle
# results are shared across the many nested make verify calls in this process.
_oracle_cache = None


def oracle_cache():
    global _oracle_cache
    if _oracle_cache is None:
        _oracle_cache = tempfile.TemporaryDirectory(prefix='expense-hledger-oracles-', dir=os.environ.get('AGENTEXPENSE_TEST_ROOT', os.environ.get('EXPENSES_TEST_TMP', '/tmp')))
        atexit.register(_oracle_cache.cleanup)
    if os.environ.get('AGENTEXPENSE_TEST_ROOT') and not Path(_oracle_cache.name).resolve().is_relative_to(Path(os.environ['AGENTEXPENSE_TEST_ROOT']).resolve()):
        raise AssertionError('Worker oracle cache escaped the owned test root')
    return _oracle_cache.name


def ready(*, new=True):
    return dict(status='ready', question=None, explanation=None,
                attachment_names=['Cafe-Receipt'], doubts=['No bank reference supplied'],
                new_accounts=['Expenses:Food:Cafe'] if new else [],
                entry=dict(date='2026-10-29', status='confirmed', description='Cafe — lunch — cash',
                           order_id=None, reference=None, classification=None, original_amount=None,
                           fuel_filled_litres=None, trip_km=None, notes=['User confirmed payment'],
                           postings=[dict(account='Expenses:Food:Cafe' if new else 'Expenses:Food',
                                          amount='100', commodity='INR', cost=None),
                                     dict(account='Assets:Cash', amount='-100', commodity='INR', cost=None)]))


def png():
    image = Image.new('RGB', (64, 64), 'orange')
    out = io.BytesIO()
    image.save(out, format='PNG', compress_level=0)
    return out.getvalue()


class Fixture(unittest.TestCase):
    def setUp(self):
        before = snapshot(APPLICATION)
        self.addCleanup(assert_unchanged, before, APPLICATION)
        artifacts = Path(os.environ.get('EXPENSES_TEST_TMP', '/tmp/expense-dashboard-tests')).resolve()
        if not str(artifacts).startswith(('/tmp/', '/private/tmp/')):
            raise ValueError('Test artifacts must stay under /tmp')
        artifacts.mkdir(parents=True, exist_ok=True)
        self.temporary = tempfile.TemporaryDirectory(prefix='expense-server-test-', dir=artifacts)
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name)
        self.application = self.base / 'app'
        self.application.mkdir()
        shutil.copytree(APPLICATION / 'scripts', self.application / 'scripts', ignore=shutil.ignore_patterns('__pycache__', '.fixture-*', '.app-fixture-*'))
        shutil.copytree(APPLICATION / 'schema', self.application / 'schema')
        shutil.copytree(APPLICATION / 'migrations', self.application / 'migrations')
        shutil.copyfile(APPLICATION / 'Makefile', self.application / 'Makefile')
        shutil.copyfile(APPLICATION / '.node-version', self.application / '.node-version')
        shutil.copyfile(APPLICATION / 'VERSION', self.application / 'VERSION')
        dash = self.application / 'review-dashboard'
        shutil.copytree(APPLICATION / 'review-dashboard/scripts', dash / 'scripts')
        package = json.loads((APPLICATION / 'review-dashboard/package.json').read_text())
        (dash / 'package.json').write_text(json.dumps(package))
        self.ledger = self.application / 'ledger'
        (self.ledger / 'data/expenses').mkdir(parents=True)
        dump(self.ledger / 'ledger.json', dict(format=1, id='00000000-0000-4000-8000-000000000001', name='Offline fixture', defaultCurrency='INR', modules={'fuel': True}))
        dump(self.ledger / 'data/accounts.json', {'commodities': ['INR', 'USD'], 'accounts': {'Assets:Cash': dict(kind='cash', currency='INR'), 'Expenses:Food': {}}})
        dump(self.ledger / 'data/expenses/2026.json', [])
        dump(self.ledger / 'data/forecast.json', [])
        dump(self.ledger / 'data/card-statements.json', [])
        (self.ledger / 'data/prices').mkdir()
        (self.ledger / 'data/prices/mutual-funds.prices').write_text('; test prices\n')
        (self.ledger / 'data/fuel-log.json').write_text('{"meta":{},"entries":[]}\n')
        (self.ledger / 'Bills').mkdir()
        (self.ledger / 'AGENTS.md').write_text((APPLICATION / 'fixtures/ledgers/asha/AGENTS.md').read_text())
        (self.ledger / '.gitignore').write_text('/drafts/\n/.lock\n/expense_ledger.ledger\n')
        self.jj('git', 'init', '--colocate', str(self.ledger), at=self.base)
        self.jj('commit', '-m', 'Fixture baseline')
        self.jj('bookmark', 'set', 'main', '-r', '@-')
        self.old = self.base / 'old.ledger'
        self.old.write_text('2020-01-01 * Cafe\n    Expenses:Food  100 INR\n    Assets:Cash  -100 INR\n')
        self.config = self.base / 'fake.json'
        self.fake = self.base / 'fake-codex'
        self.fake.write_text(f'#!{sys.executable}\n' + (SERVER / 'tests/fake_agent.py').read_text().split('\n', 1)[1])
        self.fake.chmod(0o755)
        self.settings = Settings(application=self.application, ledger=self.ledger, codex=str(self.fake),
                                 dashboard_data=dash, state_dir=self.base / 'state', dev=True, timeout=2, old_ledger=self.old)
        self.environment = patch.dict(os.environ, {'EXPENSES_FAKE_CONFIG': str(self.config), 'npm_config_cache': str(self.base / '.npm-cache'), 'npm_config_update_notifier': 'false', 'DASHBOARD_DATA_DIR': str(dash), 'TMPDIR': str(artifacts), 'PYTHONDONTWRITEBYTECODE': '1', 'EXPENSES_TEST_ORACLE_CACHE': oracle_cache()})
        self.environment.start()
        self.addCleanup(self.environment.stop)
        self.set_answers(ready())

    def jj(self, *args, at=None):
        return subprocess.check_output(['jj', '--config', 'snapshot.max-new-file-size=0', '--config', 'user.name="Offline Test"', '--config', 'user.email="test@example.invalid"', *(['-R', str(self.ledger)] if at is None else []), *args],
                                       cwd=at or self.ledger, stderr=subprocess.PIPE, text=True).strip()

    def set_answers(self, *answers):
        self.config.write_text(json.dumps({'steps': [a if 'kind' in a else {'answer': a} for a in answers]}))
        self.config.with_suffix('.count').unlink(missing_ok=True)
        self.config.with_suffix('.calls').unlink(missing_ok=True)

    def calls(self):
        path = self.config.with_suffix('.calls')
        return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []

    @contextmanager
    def client(self, *, worker=True, **overrides):
        app = create_app(replace(self.settings, **overrides), start_worker=worker)
        app.state.worker.retry_interval = .05
        with TestClient(app) as client:
            yield client

    def create(self, client, text='lunch', *, upload=True):
        response = client.post('/api/drafts', json={'text': text, 'pieces': {'category': 'food'}})
        self.assertEqual(response.status_code, 201, response.text)
        identity = response.json()['id']
        if upload:
            response = client.post(f'/api/drafts/{identity}/files',
                                   files=[('files', ('../../outside.exe', png(), 'text/plain'))])
            self.assertEqual(response.status_code, 201, response.text)
        return identity

    def wait_condition(self, client, identity, predicate):
        # Notifications come after the durable write, so slow verification and
        # refresh cannot outrun a short polling window. Timeout is a deadlock guard.
        drafts = client.app.state.drafts
        with drafts.changed:
            reached = drafts.changed.wait_for(lambda: predicate(drafts.get(identity)), timeout=180)
            last = drafts.get(identity)
        self.assertTrue(reached, f'Draft condition was never reached: {last}')
        return last

    def wait(self, client, identity, state):
        last = self.wait_condition(client, identity,
                                   lambda d: d['state'] == state or d['state'] == 'failed' and state != 'failed')
        self.assertEqual(last['state'], state, last)
        # Exercise the HTTP representation too, after synchronizing with the writer.
        return client.get(f'/api/drafts/{identity}').json()

    def save(self, client, identity):
        response = client.post(f'/api/drafts/{identity}/save')
        self.assertEqual(response.status_code, 200, response.text)

    def bytes(self):
        return {str(p.relative_to(self.ledger)): p.read_bytes() for p in self.ledger.rglob('*')
                if p.is_file() and p.relative_to(self.ledger).parts[0] not in ('.jj', '.git', 'drafts') and p.name != '.lock'}
