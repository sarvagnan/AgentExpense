"""Idle-worker daily data refresh; never wait for a ledger writer."""
from datetime import date
import gzip
import json
import logging
import os
import subprocess
import time

from transactions import clean, command_failure, ledger_lock, refresh
from dashboard_data import check_source, public_data

logger = logging.getLogger(__name__)


def local_today():
    # Same test pin as the Node/page calendar helper; production uses server local time.
    pinned = os.environ.get('DASHBOARD_TODAY')
    return date.fromisoformat(pinned).isoformat() if pinned else date.today().isoformat()


class DailyRefresh:
    interval = 30
    failure_backoff = 10 * 60

    def __init__(self, settings, *, clock=time.monotonic, today=local_today):
        self.settings, self.clock, self.today = settings, clock, today
        self.next_check = 0
        self.retry_failed = False
        self.failed_version = None

    def tick(self):
        now = self.clock()
        if now < self.next_check:
            return
        self.next_check = now + self.interval
        try:
            today = self.today()
            from data_refresh import get
            job = get(self.settings.ledger)
            if job:
                status = job.status()
                # A running or acknowledged queued build belongs to its caller;
                # maintenance must not replace a person's Retry.
                if status['updating']: return
                if status['refresh_error'] and status['version'] != self.failed_version:
                    self.failed_version = status['version']
                    self.retry_failed = True
                    self.next_check = now + self.failure_backoff
                    logger.error('Daily dashboard refresh failed; retrying in 10 minutes: %s', status['refresh_error'])
                    return
                if not status['data_stale']:
                    self.retry_failed = False
                    self.failed_version = None
            source_matches = check_source(self.settings.dashboard_data, self.settings.ledger)
            path = public_data(self.settings.dashboard_data) / 'dashboard.json.gz'
            built_today = json.loads(gzip.decompress(path.read_bytes()))['meta'].get('today') if path.exists() else None
            if source_matches and built_today == today and not self.retry_failed:
                return
            try:
                with ledger_lock(self.settings.ledger, blocking=False):
                    if not clean(self.settings.ledger):
                        return
                    if job:
                        # A local-day change affects every date-sensitive builder.
                        from second_check import committed
                        job.prepare(force=True, day=today)
                        job.activate(job.committed(committed(self.settings.ledger)))
                        result = job.result({})
                    else:
                        result = refresh(self.settings, {})
                    if result.get('data_stale') and not result.get('data_pending'):
                        raise RuntimeError(result.get('refresh_error', 'Dashboard refresh failed'))
                    self.retry_failed = False
            except BlockingIOError:
                return
        except subprocess.CalledProcessError as exc:
            # jj could not read the ledger: one line, not a traceback, and the same retry.
            self.retry_failed = True
            self.next_check = self.clock() + self.failure_backoff
            logger.warning('Daily dashboard refresh deferred; retrying in 10 minutes: %s', command_failure(exc))
        except Exception:
            self.retry_failed = True
            self.next_check = self.clock() + self.failure_backoff
            logger.exception('Daily dashboard refresh failed; retrying in 10 minutes')
