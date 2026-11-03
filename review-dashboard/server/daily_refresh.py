"""Idle-worker daily data refresh; never wait for a ledger writer."""
from datetime import date
import gzip
import json
import logging
import os
import time

from transactions import clean, ledger_lock, refresh
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

    def tick(self):
        now = self.clock()
        if now < self.next_check:
            return
        self.next_check = now + self.interval
        try:
            today = self.today()
            source_matches = check_source(self.settings.dashboard_data, self.settings.ledger)
            path = public_data(self.settings.dashboard_data) / 'dashboard.json.gz'
            built_today = json.loads(gzip.decompress(path.read_bytes()))['meta'].get('today') if path.exists() else None
            if source_matches and built_today == today and not self.retry_failed:
                return
            try:
                with ledger_lock(self.settings.ledger, blocking=False):
                    if not clean(self.settings.ledger):
                        return
                    result = refresh(self.settings, {})
                    if result.get('data_stale'):
                        raise RuntimeError(result.get('refresh_error', 'Dashboard refresh failed'))
                    self.retry_failed = False
            except BlockingIOError:
                return
        except Exception:
            self.retry_failed = True
            self.next_check = self.clock() + self.failure_backoff
            logger.exception('Daily dashboard refresh failed; retrying in 10 minutes')
