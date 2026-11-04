"""The optional update notice: a controlled clock, a fake git and no network."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
from types import SimpleNamespace
import unittest

from support import Fixture
from update_notice import DAY, UpdateNotice

TAGS = ('1111\trefs/tags/v0.1.0\n2222\trefs/tags/v0.2.0\n3333\trefs/tags/v0.3.0-rc.1\n'
        '4444\trefs/tags/not-a-release\n')


class UpdateNoticeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='update-notice-', dir=os.environ.get('EXPENSES_TEST_TMP', '/tmp'))
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / 'VERSION').write_text('0.1.0\n')
        self.time = 1_000_000.0
        self.calls, self.started = [], []
        self.reply = SimpleNamespace(returncode=0, stdout=TAGS)

    def notice(self, *, enabled=True, start=None):
        settings = SimpleNamespace(application=self.root, state_dir=self.root / 'state', update_notice=enabled, update_remote='upstream')
        return UpdateNotice(settings, clock=lambda: self.time, runner=self.fake_git, start=start or (lambda target: target()))

    def fake_git(self, command, **options):
        self.calls.append((command, options))
        if isinstance(self.reply, Exception):
            raise self.reply
        return self.reply

    def test_off_by_default_never_checks(self):
        notice = self.notice(enabled=False)
        self.assertFalse(notice.tick())
        self.assertEqual(self.calls, [])
        self.assertEqual(notice.status(), {'enabled': False, 'current': '0.1.0'})
        self.assertFalse((self.root / 'state/update-check.json').exists())

    def test_reading_the_status_never_contacts_the_remote(self):
        status = self.notice().status()
        self.assertEqual(self.calls, [])
        self.assertEqual(status, {'enabled': True, 'current': '0.1.0', 'latest': None, 'checkedAt': None,
                                  'failed': False, 'command': 'make update'})

    def test_checks_at_most_daily_with_a_controlled_clock(self):
        notice = self.notice()
        self.assertTrue(notice.tick())
        command, options = self.calls[0]
        self.assertEqual(command, ['git', 'ls-remote', '--tags', '--refs', 'upstream'])
        self.assertEqual(options['cwd'], self.root)
        self.assertEqual(options['timeout'], 30)
        self.assertEqual(options['env']['GIT_TERMINAL_PROMPT'], '0')
        status = notice.status()
        self.assertEqual(status['latest'], '0.2.0', 'pre-releases and other tags are ignored')
        self.assertEqual(status['checkedAt'], '1970-01-12T13:46:40+00:00')
        for elapsed in (1, 60 * 60, DAY - 1):
            self.time = 1_000_000.0 + elapsed
            self.assertFalse(notice.tick(), elapsed)
        self.assertEqual(len(self.calls), 1)
        self.time = 1_000_000.0 + DAY
        self.assertTrue(notice.tick())
        self.assertEqual(len(self.calls), 2)

    def test_the_last_check_survives_a_restart(self):
        self.notice().tick()
        self.time += DAY - 1
        restarted = self.notice()
        self.assertFalse(restarted.tick())
        self.assertEqual(restarted.status()['latest'], '0.2.0')
        self.assertEqual(len(self.calls), 1)

    def test_failures_wait_a_day_and_are_reported(self):
        for reply in (SimpleNamespace(returncode=128, stdout=''), subprocess.TimeoutExpired(['git'], 30), OSError('no git')):
            with self.subTest(reply=type(reply).__name__):
                self.reply = reply
                notice = self.notice()
                self.time += DAY
                with self.assertLogs('update_notice', 'WARNING'):
                    self.assertTrue(notice.tick())
                status = notice.status()
                self.assertTrue(status['failed'])
                self.assertIsNone(status['latest'])
                self.time += DAY - 1
                self.assertFalse(notice.tick())

    def test_current_or_older_releases_show_no_notice(self):
        (self.root / 'VERSION').write_text('0.2.0\n')
        notice = self.notice()
        notice.tick()
        self.assertIsNone(notice.status()['latest'])
        (self.root / 'VERSION').write_text('0.3.0\n')
        self.assertIsNone(notice.status()['latest'])

    def test_a_clock_set_back_counts_as_due(self):
        notice = self.notice()
        notice.tick()
        self.time -= 60
        self.assertTrue(notice.tick())

    def test_one_check_at_a_time(self):
        held = []
        notice = self.notice(start=held.append)
        self.assertTrue(notice.tick())
        self.assertFalse(notice.tick(), 'a check in flight is not started twice')
        held[0]()
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(json.loads((self.root / 'state/update-check.json').read_text()),
                         {'checked_at': 1_000_000.0, 'latest': '0.2.0'})


class UpdateEndpointTests(Fixture):
    def test_endpoint_reports_the_recorded_check_without_contacting_the_remote(self):
        with self.client(worker=False) as client:
            response = client.get('/api/update')
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json(), {'enabled': False, 'current': (self.application / 'VERSION').read_text().strip()})
        state = self.settings.state_dir
        state.mkdir(parents=True, exist_ok=True)
        (state / 'update-check.json').write_text(json.dumps({'checked_at': 0, 'latest': '99.0.0'}))
        with self.client(worker=False, update_notice=True) as client:
            body = client.get('/api/update').json()
        self.assertEqual(body['latest'], '99.0.0')
        self.assertEqual(body['command'], 'make update')


if __name__ == '__main__':
    unittest.main()
