"""Ledger-wide durable outage episodes. Clock is injected by offline tests."""
from datetime import datetime, timedelta, timezone
import json
import os
import threading


class AgentPause:
    def __init__(self, root, clock=None):
        self.path = root / 'drafts/agent-pause.json'
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.mutex = threading.RLock()
        self.check_thread = None
        self.on_finished = lambda: None
        try: self.value = json.loads(self.path.read_text())
        except FileNotFoundError: self.value = {}

    def write(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp = self.path.with_suffix('.tmp')
        with temp.open('w') as handle:
            json.dump(self.value, handle);handle.flush();os.fsync(handle.fileno())
        temp.replace(self.path)

    def status(self):
        with self.mutex:
            if not self.value.get('kind'): return None
            return {key: self.value.get(key) for key in ('kind','reason','resumes_at','attempt')}

    def fail(self, kind, reset_at=None, reason=None):
        if kind == 'bad_answer': return
        with self.mutex:
            attempt = self.value.get('attempt', 0) + 1 if self.value.get('kind') == kind else 1
            delay = (900,1800,3600)[min(attempt-1,2)] if kind=='usage_limit' else (5,15,45,900)[(attempt-1)%4] if kind=='transient' else 30
            resume = self.clock() + timedelta(seconds=delay)
            if reset_at:
                try:
                    reset = datetime.fromisoformat(reset_at.replace('Z','+00:00'))
                    if reset.tzinfo and reset > self.clock(): resume = reset
                except ValueError: pass
            self.value = dict(kind=kind, attempt=attempt, resumes_at=resume.isoformat(), reason=reason or {'usage_limit':'usage limit','transient':'connection interrupted','auth':'sign-in or self-check needed'}[kind])
            self.write()

    def blocked(self, gate=None):
        with self.mutex:
            value=dict(self.value)
        if not value.get('kind'): return False
        if self.clock() < datetime.fromisoformat(value['resumes_at']): return True
        if value['kind'] != 'auth': return False
        if gate is None: return True
        # Gate checks can take time; never hold the pause mutex across diagnostics.
        try: gate.require_ready(force=True)
        except ValueError:
            self.fail('auth', reason=value['reason']);return True
        self.success();return False

    def tick(self, gate):
        with self.mutex:
            due=self.value.get('kind')=='auth' and self.clock()>=datetime.fromisoformat(self.value['resumes_at'])
            if due and (not self.check_thread or not self.check_thread.is_alive()):
                def recheck():
                    try: self.blocked(gate)
                    finally: self.on_finished()
                self.check_thread=threading.Thread(target=recheck,name='expense-pause-recheck',daemon=True)
                self.check_thread.start()

    def success(self):
        with self.mutex:
            if self.value:
                self.value = {};self.write()
