"""make update's decisions and its service restart, with a controlled clock and fake commands.

The end-to-end update between tagged releases is test_update.py (make test-update).
"""
from argparse import Namespace
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest

APPLICATION = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(APPLICATION / 'scripts'))
import releases  # noqa: E402
import update  # noqa: E402


class Clock:
    def __init__(self):
        self.now, self.sleeps = 0.0, []

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += seconds


class ServiceTests(unittest.TestCase):
    def service(self, kind, states, *, restart_code=0):
        self.clock, self.commands = Clock(), []
        states = iter(states)

        def run(command, **options):
            self.commands.append(command)
            if command[1:3] in (['--user', 'restart'], ['kickstart', '-k']):
                return SimpleNamespace(returncode=restart_code, stdout='', stderr='Unit not found.\n' if restart_code else '')
            state = next(states)
            if kind == 'systemd':
                return SimpleNamespace(returncode=0 if state == 'active' else 3, stdout=state + '\n', stderr='')
            if state is None:
                return SimpleNamespace(returncode=113, stdout='', stderr='Could not find service')
            return SimpleNamespace(returncode=0, stdout=f'gui/501/x = {{\n\tstate = {state}\n}}\n', stderr='')

        name = 'expense-dashboard.service' if kind == 'systemd' else 'org.example.agentexpense'
        return update.Service(kind, name, run=run, clock=self.clock, sleep=self.clock.sleep, uid=501)

    def test_systemd_waits_until_the_unit_stays_active(self):
        self.service('systemd', ['activating', 'activating', 'active', 'activating', 'active', 'active', 'active']).restart()
        self.assertEqual(self.commands[0], ['systemctl', '--user', 'restart', 'expense-dashboard.service'])
        self.assertEqual(self.commands[1:], [['systemctl', '--user', 'is-active', 'expense-dashboard.service']] * 7)
        # Three consecutive active readings, one second apart; a dip starts the count again.
        self.assertEqual(self.clock.sleeps, [1.0] * 6)

    def test_launchd_kickstarts_the_agent_and_reads_its_state(self):
        self.service('launchd', ['waiting', 'running', 'running', 'running']).restart()
        self.assertEqual(self.commands[0], ['launchctl', 'kickstart', '-k', 'gui/501/org.example.agentexpense'])
        self.assertEqual(self.commands[1], ['launchctl', 'print', 'gui/501/org.example.agentexpense'])
        self.assertEqual(self.clock.sleeps, [1.0] * 3)

    def test_gives_up_after_thirty_seconds_without_a_steady_service(self):
        service = self.service('systemd', ['activating'] * 100)
        with self.assertRaisesRegex(update.UpdateError, 'not running steadily 30 seconds after its restart'):
            service.restart()
        self.assertEqual(self.clock.now, 30.0)
        self.assertEqual(self.clock.sleeps, [1.0] * 30)
        self.assertEqual(len(self.commands), 1 + 31)

    def test_a_failed_or_missing_service_stops_at_once(self):
        for kind, states in (('systemd', ['failed']), ('systemd', ['inactive']), ('launchd', [None])):
            with self.subTest(kind=kind, states=states):
                service = self.service(kind, states)
                with self.assertRaisesRegex(update.UpdateError, 'stopped after its restart'):
                    service.restart()
                self.assertEqual(self.clock.sleeps, [])

    def test_a_refused_restart_is_reported(self):
        with self.assertRaisesRegex(update.UpdateError, 'did not restart: Unit not found.'):
            self.service('systemd', [], restart_code=5).restart()
        self.assertEqual(self.clock.sleeps, [])


class EnvironmentTests(unittest.TestCase):
    def test_each_make_run_is_its_own(self):
        # make passes command-line variables on in MAKEFLAGS; ARGS=--yes must not reach upgrade-ledger.
        environ = {'MAKEFLAGS': ' -- ARGS=--yes', 'MFLAGS': '', 'MAKEOVERRIDES': '${-*-command-variables-*-}', 'MAKELEVEL': '1',
                   'ARGS': '--yes', 'PATH': '/bin', 'LEDGER_DIR': '/ledger'}
        updater = update.Updater(Namespace(yes=True), application=APPLICATION, environ=environ)
        self.assertEqual(updater.environ, {'PATH': '/bin', 'LEDGER_DIR': '/ledger'})


class ChooseTests(unittest.TestCase):
    TAGS = ['v0.1.0', 'v0.2.0', 'v0.3.0-rc.1', 'nightly']

    def choose(self, current='0.1.0', **options):
        arguments = Namespace(**{'to': None, 'pre': False, 'yes': True, 'remote': None, **options})
        app = SimpleNamespace(tags=lambda: self.TAGS)
        return update.Updater(arguments, application=APPLICATION, environ={}).choose(app, releases.Version(current))

    def test_newest_release_above_the_installed_one(self):
        self.assertEqual(str(self.choose()), '0.2.0')
        self.assertIsNone(self.choose('0.2.0'))
        self.assertEqual(str(self.choose('0.2.0', pre=True)), '0.3.0-rc.1')

    def test_explicit_target(self):
        self.assertEqual(str(self.choose(to='v0.2.0')), '0.2.0')
        self.assertEqual(str(self.choose(to='0.2.0')), '0.2.0')
        for target, message in (('v0.1.0', 'already installed'), ('v0.9.0', 'no release tag v0.9.0'),
                                ('latest', 'not a release version')):
            with self.subTest(target=target), self.assertRaisesRegex(update.Refusal, message):
                self.choose(to=target)
        with self.assertRaisesRegex(update.Refusal, 'v0.1.0 is older than the installed 0.2.0; make update never downgrades'):
            self.choose('0.2.0', to='v0.1.0')


if __name__ == '__main__':
    unittest.main()
