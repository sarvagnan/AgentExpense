"""Daily rebuild decisions use an injected clock, never real delays or agents."""
from pathlib import Path
from types import SimpleNamespace
import gzip
import json
import os
import tempfile
import unittest
from unittest.mock import patch

from daily_refresh import DailyRefresh, local_today
from ledger_lock import ledger_lock


class DailyRefreshTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='daily-refresh-', dir='/tmp')
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.settings = SimpleNamespace(application=root, ledger=root)
        self.path = root / 'review-dashboard/public/data/dashboard.json.gz'
        self.path.parent.mkdir(parents=True)
        self.write({'meta': {'today': '2026-10-31'}})
        self.time = 0
        self.today = '2026-11-01'
        self.job = DailyRefresh(self.settings, clock=lambda: self.time, today=lambda: self.today)
        self.clean = self.enterContext(patch('daily_refresh.clean', return_value=True))
        self.refresh = self.enterContext(patch('daily_refresh.refresh', side_effect=self.rebuild))

    def write(self, value):
        self.path.write_bytes(gzip.compress(json.dumps(value).encode()))

    def rebuild(self, settings, result):
        self.write({'meta': {'today': self.today}})
        return dict(data_stale=False)

    def test_startup_then_thirty_second_checks_and_local_day_rollover(self):
        self.job.tick()
        self.refresh.assert_called_once_with(self.settings, {})
        self.time = 29; self.today = '2026-11-02'; self.job.tick()
        self.assertEqual(self.refresh.call_count, 1)
        self.time = 30; self.job.tick()
        self.assertEqual(self.refresh.call_count, 2)
        self.time = 60; self.job.tick()
        self.assertEqual(self.refresh.call_count, 2)

    def test_busy_lock_skips_without_waiting_then_retries(self):
        with ledger_lock(self.settings.ledger):
            self.job.tick()
        self.refresh.assert_not_called()
        self.clean.assert_not_called()
        self.time = 30; self.job.tick()
        self.refresh.assert_called_once()

    def test_dirty_ledger_skips_and_clean_rebuild_holds_shared_lock(self):
        self.clean.return_value = False
        self.job.tick(); self.refresh.assert_not_called()
        def rebuild(settings, result):
            with self.assertRaises(BlockingIOError), ledger_lock(settings.ledger, blocking=False):
                pass
            return self.rebuild(settings, result)
        self.clean.return_value = True
        self.refresh.side_effect = rebuild
        self.time = 30; self.job.tick(); self.refresh.assert_called_once()

    def test_refresh_failure_backoff_is_ten_minutes_and_never_escapes(self):
        for error in [RuntimeError('build failed'), {'data_stale': True, 'refresh_error': 'build failed'}]:
            with self.subTest(error=type(error).__name__):
                self.write({'meta': {'today': '2026-10-31'}})
                self.time = 0; self.job.next_check = 0; self.refresh.reset_mock()
                if isinstance(error, Exception): self.refresh.side_effect = error
                else: self.refresh.side_effect = None; self.refresh.return_value = error
                with self.assertLogs('daily_refresh', level='ERROR') as log: self.job.tick()
                self.assertIn('10 minutes', log.output[0])
                for value in [30, 599]: self.time = value; self.job.tick()
                self.assertEqual(self.refresh.call_count, 1)
                self.refresh.side_effect = self.rebuild
                self.time = 600; self.job.tick()
                self.assertEqual(self.refresh.call_count, 2)

    def test_missing_or_old_metadata_rebuilds_and_bad_json_backs_off(self):
        self.path.unlink(); self.job.tick(); self.refresh.assert_called_once()
        self.write({'meta': {}}); self.time = 30; self.job.tick()
        self.assertEqual(self.refresh.call_count, 2)
        self.path.write_bytes(gzip.compress(b'{')); self.time = 60
        with self.assertLogs('daily_refresh', level='ERROR'): self.job.tick()
        self.assertEqual(self.job.next_check, 660)
        self.refresh.assert_called_with(self.settings, {})

    def test_partially_published_refresh_still_retries_after_backoff(self):
        def partial(settings, result):
            self.rebuild(settings, result)
            return dict(data_stale=True, refresh_error='later document failed')
        self.refresh.side_effect = partial
        with self.assertLogs('daily_refresh', level='ERROR'): self.job.tick()
        self.time = 600; self.refresh.side_effect = self.rebuild; self.job.tick()
        self.assertEqual(self.refresh.call_count, 2)
        self.assertFalse(self.job.retry_failed)

    def test_server_local_date_and_test_pin(self):
        from datetime import date
        with patch.dict(os.environ, {'DASHBOARD_TODAY': ''}): self.assertEqual(local_today(), date.today().isoformat())
        with patch.dict(os.environ, {'DASHBOARD_TODAY': '2026-11-01'}): self.assertEqual(local_today(), '2026-11-01')
        with patch.dict(os.environ, {'DASHBOARD_TODAY': '2026-02-30'}):
            with self.assertRaises(ValueError): local_today()
