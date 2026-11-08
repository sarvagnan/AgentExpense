"""One durable FIFO worker per ledger, independent of HTTP request lifetimes."""
import fcntl
from pathlib import Path
import subprocess
import threading

from starlette.exceptions import HTTPException

import agent
from daily_refresh import DailyRefresh
from outcomes import queue_outcome, OutcomeTransaction
from agent import now
from transactions import BalanceRefusal, Transaction, AccountError, ProposalError, LedgerBusy, clean, command_failure, ledger_lock, refresh
import save_timing


# FileNotFoundError passes through too: endpoints answer a missing item with their own 404 or 409.
# So does HTTPException, which an operation raises for its own answer (intake's state checks).
REFUSALS = (ValueError, TypeError, KeyError, ArithmeticError, FileNotFoundError, HTTPException)


class SaveFailed(ValueError):
    """A checked operation failed outside its own refusals (a failed verification or jj
    command, a file error). Its transaction has rolled back; the request answers 409 with
    this one-line reason instead of a bare 500, and the log keeps the traceback."""


def refusal(exc, kind):
    """The exception a worker.call endpoint answers with: refusals as they are, anything else
    as SaveFailed with a one-line reason."""
    if isinstance(exc, REFUSALS): return exc
    import logging
    logging.getLogger(__name__).error('%s failed and was not saved', kind, exc_info=exc)
    if isinstance(exc, subprocess.CalledProcessError): reason = command_failure(exc)
    else: reason = (str(exc).strip().splitlines() or [type(exc).__name__])[0][:300]
    return SaveFailed(f'The ledger could not save this change ({reason}). Try again; if it fails again, run make doctor.')


class Worker:
    def __init__(self, settings, drafts):
        self.settings, self.drafts = settings, drafts
        self.stop_event, self.wake = threading.Event(), threading.Event()
        self.thread = None
        self.calls = []
        self.receipt_cancellations = {}
        self.retry_interval = 30
        self.daily_refresh = DailyRefresh(settings)
        from agent_pause import AgentPause
        self.pause = AgentPause(settings.ledger)

    def call(self, operation, kind='call'):
        """Run a checked human operation on this ledger's FIFO worker.

        The service owns locking/recovery/audit. An unsent or interrupted HTTP
        request can retry its durable idempotency key. Offline hosts without a
        worker execute synchronously through the same service. `kind` names the
        operation in its save timing line.
        """
        if not self.thread:
            try:
                with save_timing.operation(kind): return operation()
            except Exception as exc: raise refusal(exc, kind) from exc
        job=dict(queued_at=now(), operation=operation, done=threading.Event(), kind=kind)
        with self.drafts.mutex:
            if self.stop_event.is_set(): raise ValueError('The server is stopping; retry this action')
            self.calls.append(job)
        self.wake.set();job['done'].wait()
        if 'error' in job: raise refusal(job['error'], kind) from job['error']
        return job['result']

    def cancel_receipt(self, entry_id):
        with self.drafts.mutex:
            signal = self.receipt_cancellations.get(entry_id)
            if signal: signal.set()

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
            if draft.pop('discard_requested',None):self.drafts.put(draft)
            if draft.get('receipt_reading',{}).get('state')=='reading':
                draft['receipt_reading']['state']='queued';self.drafts.put(draft)
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
        for signal in self.receipt_cancellations.values(): signal.set()
        self.wake.set()
        with self.drafts.mutex:
            for job in self.calls:
                job['error']=ValueError('The server stopped before this action; retry it')
                job['done'].set()
            self.calls.clear()
        if self.thread:
            # Keep the worker lease until the thread exits. No detached second
            # worker may start while an agent or ledger transaction is still running.
            self.thread.join()
        if self.pause.check_thread: self.pause.check_thread.join()
        self.lease.close()

    def sweep_retention(self):
        if not getattr(self, 'retention', None): return
        import logging
        try:
            with save_timing.operation('retention-sweep'): self.retention.sweep()
        except subprocess.CalledProcessError as exc:
            # jj could not read the ledger (for example a test fixture that is not a
            # repository): the sweep retries in 30 s, and says why in one line.
            logging.warning('Retention sweep deferred: %s', command_failure(exc))
        except Exception:
            logging.exception('Retention sweep deferred')

    def loop(self):
        while not self.stop_event.is_set():
            self.sweep_retention()
            self.daily_refresh.tick()
            if getattr(self, 'ledger_check', None):
                try: self.ledger_check.tick()
                except Exception:
                    import logging
                    logging.exception('Ledger check deferred')
            if getattr(self, 'update_notice', None):
                try: self.update_notice.tick()
                except Exception:
                    import logging
                    logging.exception('Update check deferred')
            self.drafts.purge()
            from agent_gate import get_gate
            self.pause.tick(get_gate(self.settings))
            self.flush_audits()
            with self.drafts.mutex:
                queued = sorted((d for d in self.drafts.all() if d['state'] == 'queued' and not d.get('claim') and not d.get('discard_requested') and (d.get('bill_request') or d.get('finished_by') or d.get('proposal') or not self.pause.blocked())),
                                key=lambda d: (d['queued_at'], d['id']))
                draft = queued[0] if queued else None
                job = self.calls[0] if self.calls else None
                if job and draft and draft['queued_at'] <= job['queued_at']: job=None
                before=job['queued_at'] if job else draft['queued_at'] if draft else None
                readings=sorted((d for d in self.drafts.all() if d.get('receipt_reading',{}).get('state') in ('queued','ready') and not self.pause.blocked()),key=lambda d:(d['receipt_reading']['queued_at'],d['id']))
                reading=readings[0] if readings and (not before or readings[0]['receipt_reading']['queued_at']<before) else None
                if reading: before=reading['receipt_reading']['queued_at']
            # Statement extraction/apply can take time. Keep draft reads and
            # cancellation available while this single worker performs it.
            if getattr(self, 'statements', None) and self.statements.work(self.stop_event, before=before):
                continue
            if reading:
                try:
                    from receipts import read
                    with save_timing.operation('receipt-details',queued_at=reading['receipt_reading'].get('queued_at')):
                        read(self,reading)
                except LedgerBusy:
                    try: reading=self.drafts.get(reading['id'],include_discarded=True)
                    except FileNotFoundError: continue
                    reading['receipt_reading']['state']='ready'
                    reading['waiting']='Waiting for a clean ledger before saving receipt details'
                    self.drafts.put(reading)
                    self.stop_event.wait(self.retry_interval)
                except Exception as exc:
                    try: reading=self.drafts.get(reading['id'],include_discarded=True)
                    except FileNotFoundError: continue
                    reading['waiting']=None
                    reading['receipt_reading'].update(state='unreadable',message='The receipt details could not be saved. Use Edit entry.',error=str(exc))
                    self.drafts.put(reading)
                continue
            if job:
                with self.drafts.mutex:
                    if job not in self.calls: continue
                    self.calls.remove(job)
                try:
                    while not self.stop_event.is_set():
                        try:
                            with save_timing.operation(job['kind'],queued_at=job['queued_at']):
                                job['result']=job['operation']()
                            break
                        except LedgerBusy:
                            self.stop_event.wait(self.retry_interval)
                    if 'result' not in job: raise ValueError('The server stopped before this correction; retry it')
                except Exception as exc: job['error']=exc
                finally: job['done'].set()
                continue
            if draft:
                with self.drafts.mutex:
                    try:
                        draft = self.drafts.get(draft['id'])
                    except FileNotFoundError:
                        continue
                    if draft['state'] != 'queued' or draft.get('claim') or draft.get('discard_requested'):
                        continue
                    draft.update(state='working', waiting=None)
                    self.drafts.put(draft)
            if not draft:
                self.wake.wait(0.5)
                self.wake.clear()
                continue
            try:
                kind='ledger-fix' if draft.get('ledger_fix') else 'bill' if draft.get('bill_request') else 'usual' if draft.get('usual_payment') else 'hand-add' if draft.get('finished_by') else 'add'
                with save_timing.operation(kind,queued_at=draft.get('queued_at')):
                    self.process(draft)
            except Exception as exc:
                if self.stop_event.is_set() and isinstance(exc, agent.AgentFailure):
                    # Cancellation during shutdown is interrupted work, not an
                    # expense failure. Resume this FIFO position after restart.
                    draft.update(state='queued', error=None, waiting=None)
                    if draft['runs']:
                        draft['runs'][-1].update(outcome='interrupted', error='Server stopped during agent run')
                    self.drafts.put(draft)
                    continue
                if isinstance(exc, agent.AgentFailure) and exc.kind != 'bad_answer':
                    self.pause.fail(exc.kind, exc.reset_at)
                    draft.update(state='queued', error=None, waiting=None)
                    self.drafts.put(draft)
                    continue
                if isinstance(exc, BalanceRefusal):
                    draft['refusal'] = exc.refusal
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
                        with save_timing.operation('audit-record'):
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
        if draft.get('ledger_fix'):
            # Ask the agent to fix the ledger check: a proposal for Edit entry, never a save.
            from ledger_check import propose
            return propose(self, draft)
        transaction = Transaction(self.settings, self.drafts, draft)
        with ledger_lock(self.settings.ledger):
            recovered = transaction.recover()
        result = draft.get('committed_result') or recovered
        if result:
            self.drafts.completed(draft, refresh(self.settings, result))
            return
        if draft.get('bill_request'):
            from rules_service import operate
            while not self.stop_event.is_set():
                try:
                    result=operate(self.settings,draft['id'],draft['bill_request']['operation'])
                except LedgerBusy:
                    draft['waiting']='Waiting for a clean ledger before saving the bill'
                    self.drafts.put(draft);self.stop_event.wait(self.retry_interval)
                    continue
                self.drafts.completed(draft,refresh(self.settings,result) if result.get('replayed') else result)
                return
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
        # A queued proposal is the person's (a hand form or usual payment) only when they finished the draft.
        from_agent = not draft.get('finished_by')
        while not self.stop_event.is_set():
            if answer is None:
                from_agent = True
                for attempt in range(2):
                    pending = dict(start=now(), end=None, model=self.settings.model, effort=self.settings.effort,
                                   outcome=None, question=None, doubts=[], error=None, duration=None, usage={}, lookups=[])
                    draft['runs'].append(pending)
                    self.drafts.put(draft)
                    try:
                        with save_timing.step('agent'):
                            answer, record = agent.run(self.settings, draft, self.drafts.folder(draft['id']), cancel=self.stop_event)
                    except agent.AgentFailure as exc:
                        draft['runs'][-1] = exc.record
                        if exc.kind=='bad_answer' and not self.stop_event.is_set(): draft['answer_failures']=draft.get('answer_failures',0)+1
                        self.drafts.put(draft)
                        if exc.kind != 'bad_answer' or self.stop_event.is_set() or draft.get('answer_failures',0)>=2 or not exc.retryable:
                            raise
                    else:
                        self.pause.success()
                        draft['answer_failures']=0
                        draft['runs'][-1] = record
                        draft['proposal'] = answer
                        self.drafts.put(draft)
                        break
            from answers import AnswerError, check as check_answer, person_amounts
            try:
                # Agent output always meets the answer model; only the person's form uses the form model.
                check_answer(answer if from_agent else person_amounts(answer), self.settings.ledger, form=not from_agent)
            except AnswerError as exc:
                # The existing correction path: the agent sees this under "Server check failures to correct".
                draft['check_errors'].append(str(exc))
                if draft['runs']: draft['runs'][-1].update(outcome='check_failed', error=str(exc))
                draft['last_proposal'] = answer
                draft['proposal'] = None
                self.drafts.put(draft)
                if draft.get('finished_by') or draft.get('usual_payment') or len(draft['check_errors']) > 1:
                    raise ProposalError(str(exc))
                answer = None
                continue
            if answer.get('entry'):
                draft['description'] = answer['entry']['description']
            if answer['status'] == 'question':
                draft['questions'].append(dict(question=answer['question'], answer=None, asked_at=now()))
                from currency_drafts import currency_request
                request = currency_request(answer, self.settings.ledger)
                if request:
                    draft.update(currency_request=request, currency_proposal=answer)
                draft.update(state='question', last_proposal=answer, proposal=None)
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
                        prepared = transaction.prepare(answer, person=not from_agent)
                        if prepared['new_roots'] and not draft.get('account_consent'):
                            draft.update(state='question', account_proposal=answer,
                                         account_question=dict(proposal_hash=prepared['proposal_hash'], accounts=prepared['new_roots'], postings=answer['entry']['postings']), proposal=None)
                            draft['questions'].append(dict(question='Create these new top-level accounts and add this entry?', answer=None, asked_at=now()))
                            self.drafts.put(draft);return
                        draft['waiting'] = None
                        self.drafts.put(draft)
                        result = transaction.apply(answer, fd, person=not from_agent)
                    else:
                        result = None
                        draft['waiting'] = 'Waiting for a clean ledger; checking again in 30 seconds'
                        self.drafts.put(draft)
                if draft['waiting']:
                    self.stop_event.wait(self.retry_interval)
                    continue
            except BalanceRefusal as exc:
                draft['refusal'] = exc.refusal
                draft['check_errors'].append(str(exc))
                if draft['runs']: draft['runs'][-1].update(outcome='check_failed', error=str(exc))
                raise
            except AccountError as exc:
                draft.update(state='question', last_proposal=answer, proposal=None)
                from store import load
                from proposal_consent import proposal_hash
                roots={a.split(':')[0] for a in load(self.settings.ledger)[self.settings.ledger/'data/accounts.json']['accounts']}
                new_roots=[a for a in answer['new-accounts'] if a.split(':')[0] not in roots]
                if new_roots:
                    draft.update(account_proposal=answer,account_question=dict(proposal_hash=proposal_hash(answer),accounts=new_roots,postings=answer['entry']['postings']))
                draft['questions'].append(dict(question=str(exc), answer=None, asked_at=now()))
                self.drafts.put(draft);return
            except ProposalError as exc:
                draft['check_errors'].append(str(exc))
                if draft['runs']: draft['runs'][-1].update(outcome='check_failed', error=str(exc))
                if draft.get('finished_by') or draft.get('usual_payment'): raise
                draft['last_proposal'] = answer
                draft['proposal'] = None
                self.drafts.put(draft)
                if len(draft['check_errors']) > 1:
                    raise
                answer = None
                continue
            self.drafts.completed(draft, refresh(self.settings, result))
            return
