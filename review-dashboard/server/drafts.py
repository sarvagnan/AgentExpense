"""Atomic, ignored draft records, protected from concurrent API/worker updates."""
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import threading

from agent import now
from draft_time import client_context, ensure_context


class Drafts:
    def __init__(self, root):
        self.root = root / 'drafts'
        self.root.mkdir(exist_ok=True, parents=True)
        self.mutex = threading.RLock()
        self.changed = threading.Condition(self.mutex)

    def folder(self, identity):
        if not re.fullmatch('[0-9a-f]{32}', identity):
            raise FileNotFoundError('Unknown draft')
        folder = self.root / identity
        if folder.is_symlink():
            raise FileNotFoundError('Invalid draft')
        return folder

    def get(self, identity, *, include_discarded=False):
        with self.mutex:
            draft = json.loads((self.folder(identity) / 'draft.json').read_text())
            if draft.get('discarded_until') and not include_discarded:
                raise FileNotFoundError('Discarded draft')
            return ensure_context(draft)

    def put(self, draft):
        with self.mutex:
            folder = self.folder(draft['id'])
            folder.mkdir(exist_ok=True)
            path = folder / 'draft.json'
            temporary = path.with_suffix('.tmp')
            with temporary.open('w') as handle:
                json.dump(draft, handle, indent=2, ensure_ascii=False, allow_nan=False)
                handle.write('\n')
                handle.flush()
                os.fsync(handle.fileno())
            temporary.replace(path)
            self.changed.notify_all()

    def create(self, text='', pieces=None, client_time=None, client_zone=None):
        draft = dict(id=secrets.token_hex(16), state='editing', text=text, pieces=pieces,
                     created_at=now(), saved_at=None, queued_at=None, files=[], runs=[],
                     questions=[], check_errors=[], result=None, error=None, waiting=None,
                     **client_context(client_time, client_zone))
        with self.mutex:
            self.put(draft)
            (self.folder(draft['id']) / 'files').mkdir()
        return draft

    def save_time(self, draft, client_time=None, client_zone=None):
        context = client_context(client_time, client_zone)
        draft.update(saved_at=now(), **context)

    def all(self, *, include_discarded=False):
        with self.mutex:
            records = [self.get(path.parent.name, include_discarded=True) for path in sorted(self.root.glob('*/draft.json'))]
            return records if include_discarded else [d for d in records if not d.get('discarded_until')]

    def purge(self, at=None):
        at = at or datetime.now(timezone.utc)
        with self.mutex:
            for draft in self.all(include_discarded=True):
                if draft.get('discarded_until'):
                    if datetime.fromisoformat(draft['discarded_until']) <= at:
                        if not draft.get('discard_audit_queued'):
                            from outcomes import queue_outcome
                            queue_outcome(draft, 'discarded')
                            draft['discard_audit_queued'] = True
                            self.put(draft)
                        if not draft.get('audit_pending'):
                            shutil.rmtree(self.folder(draft['id']))
                    continue
                if draft['state'] == 'added':
                    if draft.get('statement_file') or draft.get('audit_pending'):
                        continue
                    if datetime.fromisoformat(draft['completed_at']) <= at - timedelta(hours=24):
                        shutil.rmtree(self.folder(draft['id']))
                    else:
                        shutil.rmtree(self.folder(draft['id']) / 'files', ignore_errors=True)

    def completed(self, draft, result):
        with self.mutex:
            draft.update(state='added', result=result, completed_at=now(), waiting=None, error=None)
            if result.get('outcome') == 'not_expense':
                from outcomes import queue_outcome
                queue_outcome(draft, 'not_expense')
            self.put(draft)  # durable result before removing uploads
            if not draft.get('statement_file'):
                shutil.rmtree(self.folder(draft['id']) / 'files', ignore_errors=True)
