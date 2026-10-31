"""Locked, recoverable settings writes through the ledger's verified commit path."""
import json
import os
import secrets
import re
import subprocess
from pathlib import Path
import sys
from settings import APPLICATION
from transactions import Transaction, clean, jj, refresh
from ledger_lock import ledger_lock, LOCK_FD_ENV
sys.path.insert(0, str(APPLICATION / 'scripts/ledger'))
# The shared module uses a distinct name to avoid importing this API module again.
from account_currency import currency_changes
from store import dump


class SettingsRecords:
    def __init__(self, root):
        self.root = root / 'drafts/settings'
        if self.root.is_symlink() or self.root.parent.is_symlink():
            raise ValueError('Settings records must remain inside the ledger drafts directory')
        self.root.mkdir(parents=True, exist_ok=True)

    def folder(self, identity):
        if not re.fullmatch(r'[0-9a-f]{32}', identity):
            raise ValueError('Invalid settings record identity')
        folder = self.root / identity
        if folder.is_symlink():
            raise ValueError('Settings record must not be a symlink')
        return folder

    def put(self, draft):
        folder = self.folder(draft['id'])
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / 'record.json'
        temporary = path.with_suffix('.tmp')
        with temporary.open('w') as handle:
            json.dump(draft, handle, ensure_ascii=False, allow_nan=False)
            handle.flush()
            os.fsync(handle.fileno())
        temporary.replace(path)


def update_currency(settings, code, account=None):
    records = SettingsRecords(settings.ledger)
    with ledger_lock(settings.ledger) as fd:
        for folder in records.root.iterdir():
            if (folder / 'transaction.json').exists():
                draft = json.loads((folder / 'record.json').read_text())
                Transaction(settings, records, draft).recover()
        if not clean(settings.ledger):
            raise ValueError('The ledger has uncommitted changes; finish them before changing currencies')
        changes = currency_changes(settings.ledger, code, account)
        draft = dict(id=secrets.token_hex(16))
        records.put(draft)
        transaction = Transaction(settings, records, draft)
        journal = transaction.snapshot([*changes, settings.ledger / 'expense_ledger.ledger'])
        try:
            for path, value in changes.items():
                dump(path, value)
            result = dict(currency=code, account=account)
            journal['result'] = result
            transaction.save_journal(journal)
            message = f"Change {'account ' + account if account else 'default'} currency to {code}\n\n{transaction.marker}"
            run = subprocess.run(['make', 'commit'], cwd=settings.application,
                                 env={**os.environ, 'LEDGER_DIR': str(settings.ledger), LOCK_FD_ENV: str(fd), 'EXPENSES_COMMIT_MESSAGE': message},
                                 pass_fds=(fd,), capture_output=True, text=True)
            if run.returncode:
                raise RuntimeError('Currency change failed verification: ' + (run.stdout + run.stderr)[-4000:])
            result['commit_id'] = jj(settings.ledger, 'log', '-r', '@-', '--no-graph', '-T', 'commit_id')
            draft['committed_result'] = result
            records.put(draft)
            transaction.journal.unlink()
        except BaseException:
            transaction.rollback(journal)
            raise
    return refresh(settings, result)
