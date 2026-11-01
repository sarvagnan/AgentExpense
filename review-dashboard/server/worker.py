"""One durable FIFO worker per ledger, independent of HTTP request lifetimes."""
import fcntl
from pathlib import Path
import threading

import agent
from outcomes import queue_outcome, OutcomeTransaction
from agent import now
from transactions import Transaction, ProposalError, clean, ledger_lock, refresh


class Worker:
    def __init__(self, settings, drafts):
        self.settings, self.drafts = settings, drafts
        self.stop_event, self.wake = threading.Event(), threading.Event()
        self.thread = None
        self.retry_interval = 30

    def start(self):
        self.lease = (self.drafts.root / '.worker.lock').open('a+b')
        try:
            fcntl.flock(self.lease.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self.lease.close()
            raise RuntimeError('Another expense worker already owns this ledger')
        self.drafts.purge()
        if getattr(self, "statements", None):
            self.statements.recover_work()
        for draft in self.drafts.all():
            if draft['state'] == 'working':
                draft['state'] = 'queued'
                for run in draft['runs']:
                    if run['end'] is None:
                        run.update(end=now(), outcome='interrupted', error='Server restarted during agent run')
                self.drafts.put(draft)
        self.thread = threading.Thread(target=self.loop, name='expense-drafts', daemon=True)
        self.thread.start()

    def close(self):
        self.stop_event.set()
        self.wake.set()
        if self.thread:
            # Keep the worker lease until the thread exits. No detached second
            # worker may start while an agent or ledger transaction is still running.
            self.thread.join()
        self.lease.close()

    def loop(self):
        while not self.stop_event.is_set():
            self.drafts.purge()
            self.flush_audits()
            with self.drafts.mutex:
                queued = sorted((d for d in self.drafts.all() if d['state'] == 'queued'),
                                key=lambda d: (d['queued_at'], d['id']))
                draft = queued[0] if queued else None
            # Statement extraction/apply can take time. Keep draft reads and
            # handover Undo available while this single worker performs it.
            if getattr(self, 'statements', None) and self.statements.work(self.stop_event, before=draft['queued_at'] if draft else None):
                continue
            if draft:
                with self.drafts.mutex:
                    try:
                        draft = self.drafts.get(draft['id'])
                    except FileNotFoundError:
                        continue
                    if draft['state'] != 'queued':
                        continue
                    draft.update(state='working', waiting=None)
                    self.drafts.put(draft)
            if not draft:
                self.wake.wait(0.5)
                self.wake.clear()
                continue
            try:
                self.process(draft)
            except Exception as exc:
                draft.update(state='failed', error=str(exc), waiting=None)
                queue_outcome(draft, 'failed')
                self.drafts.put(draft)

    def flush_audits(self):
        for draft in self.drafts.all(include_discarded=True):
            if not draft.get('audit_pending'):
                continue
            try:
                with ledger_lock(self.settings.ledger) as fd:
                    for record in draft['audit_pending']:
                        OutcomeTransaction(self.settings, self.drafts, draft, record).recover_record()
                    if not clean(self.settings.ledger):
                        continue
                    while True:
                        with self.drafts.mutex:
                            current = self.drafts.get(draft['id'], include_discarded=True)
                            if not current.get('audit_pending'):
                                break
                            record = current['audit_pending'][0]
                        # Ledger verification/commit holds the ledger lock, but
                        # must not block autosave, answers or handover requests.
                        OutcomeTransaction(self.settings, self.drafts, current, record).apply_record(fd)
                        with self.drafts.mutex:
                            latest = self.drafts.get(draft['id'], include_discarded=True)
                            latest['audit_pending'] = [r for r in latest.get('audit_pending', []) if r['id'] != record['id']]
                            latest.pop('audit_error', None)
                            self.drafts.put(latest)
            except FileNotFoundError:
                continue
            except Exception as exc:
                with self.drafts.mutex:
                    try:
                        latest = self.drafts.get(draft['id'], include_discarded=True)
                    except FileNotFoundError:
                        continue
                    latest['audit_error'] = str(exc)
                    self.drafts.put(latest)

    def process(self, draft):
        transaction = Transaction(self.settings, self.drafts, draft)
        with ledger_lock(self.settings.ledger):
            recovered = transaction.recover()
        result = draft.get('committed_result') or recovered
        if result:
            self.drafts.completed(draft, refresh(self.settings, result))
            return
        if not draft.get('proposal'):
            from statements import recognised
            from statement_pdf import extract_text
            from pypdf import PdfReader
            for file in draft['files']:
                if file.get('extension') != '.pdf':
                    continue
                path = self.drafts.folder(draft['id']) / 'files' / file['name']
                try:
                    reader = PdfReader(path)
                    locked = reader.is_encrypted and not reader.decrypt('')
                    detected = locked or recognised(extract_text(path))
                except Exception:
                    detected = False
                if detected:
                    draft['statement_file'] = file['id']
                    self.drafts.completed(draft, dict(outcome='not_expense', explanation='This is a card statement. Continue in Drafts to reconcile its rows.'))
                    return
        answer = draft.get('proposal')
        while not self.stop_event.is_set():
            if answer is None:
                for attempt in range(2):
                    pending = dict(start=now(), end=None, model=self.settings.model, effort=self.settings.effort,
                                   outcome=None, question=None, doubts=[], error=None, duration=None, usage={}, lookups=[])
                    draft['runs'].append(pending)
                    self.drafts.put(draft)
                    try:
                        answer, record = agent.run(self.settings, draft, self.drafts.folder(draft['id']))
                    except agent.AgentFailure as exc:
                        draft['runs'][-1] = exc.record
                        self.drafts.put(draft)
                        if attempt or not exc.retryable:
                            raise
                    else:
                        draft['runs'][-1] = record
                        draft['proposal'] = answer
                        self.drafts.put(draft)
                        break
            if answer.get('entry'):
                draft['description'] = answer['entry']['description']
            if answer['status'] == 'question':
                draft['questions'].append(dict(question=answer['question'], answer=None, asked_at=now()))
                from currency_drafts import currency_request
                request = currency_request(answer, self.settings.ledger)
                if request:
                    draft.update(currency_request=request, currency_proposal=answer)
                draft.update(state='question', proposal=None)
                self.drafts.put(draft)
                return
            if answer['status'] == 'not_expense':
                if 'statement' in (answer.get('explanation') or '').casefold():
                    pdf = next((f for f in draft['files'] if f.get('extension')=='.pdf'),None)
                    if pdf:
                        draft['statement_file']=pdf['id']
                self.drafts.completed(draft, dict(outcome='not_expense', explanation=answer['explanation']))
                return
            try:
                with ledger_lock(self.settings.ledger) as fd:
                    if clean(self.settings.ledger):
                        draft['waiting'] = None
                        self.drafts.put(draft)
                        result = transaction.apply(answer, fd)
                    else:
                        result = None
                        draft['waiting'] = 'Waiting for a clean ledger; checking again in 30 seconds'
                        self.drafts.put(draft)
                if draft['waiting']:
                    self.stop_event.wait(self.retry_interval)
                    continue
            except ProposalError as exc:
                draft['check_errors'].append(str(exc))
                draft['runs'][-1].update(outcome='check_failed', error=str(exc))
                draft['proposal'] = None
                self.drafts.put(draft)
                if len(draft['check_errors']) > 1:
                    raise
                answer = None
                continue
            self.drafts.completed(draft, refresh(self.settings, result))
            return
