"""Durable draft outcome snapshots; record-only commits use the same journal."""
from copy import deepcopy
from datetime import datetime, timezone
import os
import subprocess
from transactions import Transaction, jj
from audit import append, audit_path, event, records
from ledger_lock import LOCK_FD_ENV
from draft_time import ensure_context


def draft_event(draft, outcome, result=None):
    draft = ensure_context(dict(draft))
    result = result or draft.get('result') or {}
    sources = result.get('sources', [])
    return event('draft', draft_id=draft['id'], outcome=outcome, text=draft['text'], saved_at=draft['saved_at'],
                 client_time=draft['client_time'], client_zone=draft['client_zone'],
                 **{key:deepcopy(draft[key]) for key in ('finished_by','account_consent','usual_payment','refusal') if key in draft},
                 pieces=deepcopy(draft.get('pieces')), files=[dict(original_name=f['original_name'], **({'filed_path': sources[i]} if i < len(sources) else {})) for i, f in enumerate(draft['files'])],
                 questions=deepcopy(draft['questions']), runs=deepcopy(draft['runs']), proposal=deepcopy(draft.get('proposal')),
                 doubts=list(dict.fromkeys(x for r in draft['runs'] for x in r.get('doubts', []))),
                 check_errors=list(draft['check_errors']), error=draft.get('error'), entry_id=result.get('entry_id'))


def queue_outcome(draft, outcome):
    pending = draft.setdefault('audit_pending', [])
    pending.append(draft_event(draft, outcome))


class OutcomeTransaction(Transaction):
    def __init__(self, settings, drafts, draft, record):
        super().__init__(settings, drafts, draft)
        self.record = record
        self.marker = 'Audit: ' + record['id']
        self.journal = drafts.folder(draft['id']) / ('audit-' + record['id'] + '.json')

    def recover_record(self):
        if self.journal.exists():
            journal = __import__('json').loads(self.journal.read_text())
            committed = jj(self.root, 'log', '-r', 'description(' + __import__('json').dumps(self.marker) + ') & ancestors(@)', '--no-graph', '-T', 'commit_id')
            if committed:
                self.journal.unlink()
                return
            self.recover()

    def apply_record(self, lock_fd):
        self.recover_record()
        if any(r['id'] == self.record['id'] for r in records(self.root)):
            append(self.root, self.record)  # check equality without rewriting
            return
        journal = self.snapshot([audit_path(self.root, self.record), self.root / 'expense_ledger.ledger'])
        try:
            append(self.root, self.record)
            message = f"Record dashboard draft outcome: {self.record['outcome']}\n\n{self.marker}\nDraft: {self.draft['id']}\nAdded-by: dashboard audit recorder"
            env = {**os.environ, 'LEDGER_DIR': str(self.root), LOCK_FD_ENV: str(lock_fd), 'EXPENSES_COMMIT_MESSAGE': message}
            result = subprocess.run(['make', 'commit'], cwd=self.settings.application, env=env, pass_fds=(lock_fd,), capture_output=True, text=True)
            if result.returncode:
                raise RuntimeError('Could not commit the audit record: ' + (result.stdout + result.stderr)[-3000:])
            self.journal.unlink()
        except BaseException:
            self.rollback(journal)
            raise
