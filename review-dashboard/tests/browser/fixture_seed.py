"""Audited, rollback-safe seeding for the disposable browser host only."""
import base64
import os
from pathlib import Path
import secrets

from audit import append, audit_path, stamp
from audit_changes import document_change
from currency_settings import SettingsRecords
from ledger_lock import ledger_lock, LOCK_FD_ENV
from save_timing import make_commit
from store import dump
from transactions import Transaction, clean, refresh


def save_fixture_settings(settings, get_changes, result, description, *, files=None, events=()):
    """Declare documents, evidence bytes and audit events before any fixture write."""
    root = settings.ledger
    assert root.resolve().is_relative_to(Path('/tmp').resolve())
    files = files or {}
    assert all(path.is_relative_to(root / 'Bills') for path in files)
    records = SettingsRecords(root)
    with ledger_lock(root) as fd:
        if not clean(root):
            raise ValueError('Finish the previous fixture changes before seeding')
        documents = get_changes()
        draft = dict(id=secrets.token_hex(16))
        records.put(draft)
        transaction = Transaction(settings, records, draft)
        audit_time = stamp()
        logs = [audit_path(root, row) for row in [*events, {'time': audit_time}]]
        journal = transaction.snapshot([*documents, *files, *logs, root / 'expense_ledger.ledger'])
        try:
            for path, value in documents.items():
                dump(path, value)
            for path, content in files.items():
                if content is None:
                    path.unlink(missing_ok=True)
                else:
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_bytes(content)
            journal['result'] = result
            transaction.save_journal(journal)
            message = f'{description}\n\n{transaction.marker}'
            line = document_change(root, documents, journal, message, audit_time)
            line.update(actor='agent', note='Offline browser fixture')
            for path, content in files.items():
                rel = str(path.relative_to(root))
                prior = journal['files'][rel]
                old = base64.b64decode(prior['bytes']) if prior else None
                if old != content:
                    line['paths'].append(dict(path=rel, status='removed' if content is None else 'added' if prior is None else 'modified'))
            for event in events:
                append(root, event)
            append(root, line)
            run = make_commit(settings.application, {**os.environ, 'LEDGER_DIR': str(root),
                              LOCK_FD_ENV: str(fd), 'EXPENSES_COMMIT_MESSAGE': message}, fd)
            if run.returncode:
                raise RuntimeError('Fixture seed failed verification: ' + (run.stdout + run.stderr)[-4000:])
            result['commit_id'] = run.stdout.strip()
            draft['committed_result'] = result
            records.put(draft)
            transaction.finish_commit(run)
        except BaseException:
            transaction.rollback(journal)
            raise
    return refresh(settings, result)
