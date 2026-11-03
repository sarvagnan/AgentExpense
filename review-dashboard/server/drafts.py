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
    def __init__(self, root, clock=None):
        self.clock = clock or (lambda: datetime.now(timezone.utc))
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
            if draft.get('claim') and datetime.fromisoformat(draft['claim']['expires_at']) <= self.clock():
                self.release_claim(draft)
                self.put(draft)
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
            return records if include_discarded else [d for d in records if not d.get('discarded_until') and not d.get('intake_complete')]

    def purge(self, at=None):
        at = at or datetime.now(timezone.utc)
        with self.mutex:
            for draft in self.all(include_discarded=True):
                if draft.get('intake_complete'):
                    if datetime.fromisoformat(draft['updated_at'] if 'updated_at' in draft else draft['created_at']) <= at - timedelta(hours=24):
                        shutil.rmtree(self.folder(draft['id']))
                    continue
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
                    if draft.get('statement_file') or draft.get('audit_pending') or draft.get('receipt_reading',{}).get('state') in ('queued','reading','ready','question'):
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
            if draft.get('usual_payment') and result.get('entry_id') and result.get('sources') and not draft.get('receipt_reading'):
                draft['receipt_reading']=dict(state='queued',queued_at=now(),sources=result['sources'],entry_id=result['entry_id'])
            self.put(draft)  # durable result before removing uploads
            if not draft.get('statement_file'):
                shutil.rmtree(self.folder(draft['id']) / 'files', ignore_errors=True)

    def release_claim(self, draft):
        draft.pop('claim', None)
        draft.pop('account_consent', None)
        draft.pop('hand_checks', None)

    def claim(self, draft, token=None):
        with self.mutex:
            if draft['state'] not in ('queued','failed','question'):
                raise ValueError('Only queued, failed or question drafts can be finished by hand')
            previous = draft.get('claim')
            if previous and previous['token'] != token:
                raise ValueError('Another page is finishing this draft')
            draft['claim'] = dict(token=previous['token'] if previous else secrets.token_hex(32),
                                  expires_at=(self.clock()+timedelta(minutes=30)).isoformat())
            self.put(draft)
            return draft['claim']['token']

    def require_claim(self, draft, token):
        if not draft.get('claim') or not secrets.compare_digest(draft['claim']['token'], token):
            raise ValueError('This page no longer owns the draft; reopen Finish it yourself')
        self.claim(draft, token)
