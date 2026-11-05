"""Setup plumbing: make new-ledger, the account and opening-balance commands, make
private-checks, make demo, the policy split and the setup documents' commands and links.

Every ledger here is fictional and lives under the test run folder.
"""
import io
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import unittest
import urllib.error

APPLICATION = Path(__file__).resolve().parents[2]
PAGE = (b'<div id="root"></div><script type="module" crossorigin src="/expenses/assets/index.js"></script>'
        b'<link rel="stylesheet" crossorigin href="/expenses/assets/index.css">')
INHERITED = ('LEDGER_DIR', 'LEDGER', 'DASHBOARD_DATA_DIR', 'MAKEFLAGS', 'MAKELEVEL', 'MFLAGS', 'MAKEOVERRIDES', 'ARGS',
             'PERSONA', 'BROWSER_PERSONA', 'JJ_CONFIG', 'GIT_CONFIG_GLOBAL')
SETUP_DOCUMENTS = ['SETUP.md', 'README.md', 'AGENTS.md', 'docs/README.md', *sorted(
    str(p.relative_to(APPLICATION)) for p in (APPLICATION / 'docs/deploy').glob('*.md')),
    'templates/ledger/README.md', 'templates/ledger/AGENTS.md', 'templates/ledger/readers/README.md',
    'templates/ledger/checks/README.md', 'policy/BOOKKEEPING.md']


class SetupCase(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(dir=os.environ.get('EXPENSES_TEST_TMP'), prefix='setup-')
        self.addCleanup(temporary.cleanup)
        self.work = Path(temporary.name).resolve()
        (self.work / 'jj.toml').write_text('[user]\nname = "Setup Fixture"\nemail = "setup@example.invalid"\n')
        (self.work / 'gitconfig').write_text('[user]\n\tname = Setup Fixture\n\temail = setup@example.invalid\n'
                                             '[init]\n\tdefaultBranch = main\n')
        self.environ = {key: value for key, value in os.environ.items() if key not in INHERITED}
        self.environ.update(JJ_CONFIG=str(self.work / 'jj.toml'), GIT_CONFIG_GLOBAL=str(self.work / 'gitconfig'),
                            GIT_CONFIG_NOSYSTEM='1', PYTHONDONTWRITEBYTECODE='1')

    def make(self, *arguments, ledger=None, **extra):
        env = dict(self.environ, **extra)
        if ledger:
            env['LEDGER_DIR'] = str(ledger)
        run = subprocess.run(['make', '--no-print-directory', *arguments], cwd=APPLICATION, env=env,
                             stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, timeout=600)
        return run.returncode, run.stdout

    def new_ledger(self, name='jj-ledger', *extra):
        target = self.work / name
        code, output = self.make('new-ledger', f'DIR={target}', 'NAME=Fictional Owner', 'CURRENCY=eur', 'LOCALE=en-GB', *extra)
        self.assertEqual(code, 0, output)
        return target


class NewLedgerTests(SetupCase):
    def jj(self, ledger, *arguments):
        return subprocess.run(['jj', '-R', str(ledger), *arguments], cwd=ledger, env=self.environ, capture_output=True,
                              text=True, check=True).stdout

    def test_jj_ledger_is_verified_committed_and_clean(self):
        ledger = self.new_ledger()
        manifest = json.loads((ledger / 'ledger.json').read_text())
        self.assertEqual((manifest['name'], manifest['defaultCurrency'], manifest['locale']), ('Fictional Owner', 'EUR', 'en-GB'))
        self.assertTrue((ledger / '.jj').is_dir() and (ledger / '.git').exists())
        log = self.jj(ledger, 'log', '--no-graph', '-r', '::@-', '-T', 'description.first_line() ++ "\\n"')
        self.assertRegex(log.strip(), r'^Create ledger from the AgentExpense template \(app v\d+\.\d+\.\d+\)$')
        self.assertIn('main', self.jj(ledger, 'log', '--no-graph', '-r', '@-', '-T', 'bookmarks'))
        self.assertIn('The working copy has no changes', self.jj(ledger, 'st'))
        committed = set(self.jj(ledger, 'file', 'list', '-r', '@-').split())
        self.assertIn('AGENTS.md', committed)
        self.assertIn('data/accounts.json', committed)
        self.assertNotIn('expense_ledger.ledger', committed)
        self.assertNotIn('.lock', committed)

    def test_setup_commands_add_accounts_and_openings_then_commit(self):
        ledger = self.new_ledger()
        steps = [
            ('add-account', 'ARGS=--kind bank --label "Example Checking" --alias checking'),
            ('add-account', 'ARGS=--kind card --label "Example Card" --reader example-card-pdf --identifier 4321'),
            ('add-account', 'ARGS=--kind bank --label "Travel Account" --currency USD'),
            ('add-account', 'ARGS=--account Expenses:Groceries'),
            ('opening-balances', 'ARGS=--date 2026-11-05 checking=1200 "Example Card=80.10" "Travel Account=50"'),
            ('commit', 'm=Set up fictional accounts'),
        ]
        for target, argument in steps:
            code, output = self.make(target, argument, ledger=ledger)
            self.assertEqual(code, 0, output)
        accounts = json.loads((ledger / 'data/accounts.json').read_text())
        self.assertEqual(accounts['commodities'], ['EUR', 'USD'])
        self.assertEqual(accounts['accounts']['Liabilities:Credit Cards:Example Card']['statement'],
                         {'reader': 'example-card-pdf', 'identifier': '4321'})
        self.assertEqual(json.loads((ledger / 'ledger.json').read_text())['startDate'], '2026-11-05')
        self.assertIn('The working copy has no changes', self.jj(ledger, 'st'))
        code, output = self.make('add-account', 'ARGS=--kind bank --label Other --reader missing-reader', ledger=ledger)
        self.assertNotEqual(code, 0)
        self.assertIn('Unknown statement reader', output)

    def test_git_ledger(self):
        ledger = self.new_ledger('git-ledger', 'VCS=git')
        self.assertFalse((ledger / '.jj').exists())
        status = subprocess.run(['git', '-C', str(ledger), 'status', '--porcelain'], env=self.environ, capture_output=True,
                                text=True, check=True).stdout
        self.assertEqual(status, '')
        log = subprocess.run(['git', '-C', str(ledger), 'log', '--format=%s%n%D'], env=self.environ, capture_output=True,
                             text=True, check=True).stdout
        self.assertIn('Create ledger from the AgentExpense template', log)
        self.assertIn('main', log)

    def test_refusals_create_nothing(self):
        code, output = self.make('new-ledger', f'DIR={self.work / "bad"}', 'NAME=Owner', 'CURRENCY=DOLLARS')
        self.assertNotEqual(code, 0)
        self.assertIn('ISO 4217', output)
        self.assertFalse((self.work / 'bad').exists())
        code, output = self.make('new-ledger', f'DIR={APPLICATION / "fixtures/new"}', 'NAME=Owner', 'CURRENCY=USD')
        self.assertNotEqual(code, 0)
        self.assertIn('inside the application', output)
        self.assertFalse((APPLICATION / 'fixtures/new').exists())
        (self.work / 'used').mkdir()
        (self.work / 'used/note.txt').write_text('fictional')
        code, output = self.make('new-ledger', f'DIR={self.work / "used"}', 'NAME=Owner', 'CURRENCY=USD')
        self.assertIn('not empty', output)
        self.assertEqual(sorted(p.name for p in (self.work / 'used').iterdir()), ['note.txt'])
        code, output = self.make('new-ledger', 'NAME=Owner')
        self.assertEqual(code, 2)
        self.assertIn('Usage: make new-ledger', output)


class PrivateCheckTests(SetupCase):
    def test_reports_each_check_and_fails_when_one_fails(self):
        ledger = self.new_ledger()
        code, output = self.make('private-checks', ledger=ledger)
        self.assertEqual(code, 0, output)
        self.assertIn('No private checks', output)
        checks = ledger / 'checks'
        (checks / 'test_fixture.py').write_text(
            'import os, unittest\nfrom pathlib import Path\n'
            'class T(unittest.TestCase):\n'
            '    def test_ledger(self):\n'
            "        self.assertTrue((Path(os.environ['LEDGER_DIR']) / 'ledger.json').is_file())\n"
            "        self.assertTrue((Path(os.environ['AGENTEXPENSE_APP']) / 'VERSION').is_file())\n")
        (checks / 'helper.py').write_text('raise SystemExit("helpers never run")\n')
        script = checks / 'closing_balance.sh'
        script.write_text('#!/bin/sh\necho "fictional mismatch"\nexit 3\n')
        script.chmod(0o755)
        code, output = self.make('private-checks', ledger=ledger)
        self.assertNotEqual(code, 0)
        self.assertIn('FAIL closing_balance.sh', output)
        self.assertIn('fictional mismatch', output)
        self.assertIn('PASS test_fixture.py', output)
        self.assertNotIn('helper', output)
        self.assertIn('1 of 2 private checks passed.', output)
        script.write_text('#!/bin/sh\nexit 0\n')
        code, output = self.make('private-checks', ledger=ledger)
        self.assertEqual(code, 0, output)
        self.assertIn('2 of 2 private checks passed.', output)

    def test_needs_a_ledger(self):
        code, output = self.make('private-checks', ledger=self.work / 'missing')
        self.assertNotEqual(code, 0)
        self.assertIn('Set LEDGER_DIR', output)


class DemoTests(SetupCase):
    def test_smoke_starts_serves_stops_and_removes_everything(self):
        generated = [APPLICATION / 'review-dashboard' / name for name in ('dist', 'app/data', 'public/data')]
        before = {path: path.stat().st_mtime_ns for path in generated if path.exists()}
        parent = self.work / 'demo-parent'
        parent.mkdir()
        code, output = self.make('demo', 'SMOKE=1', 'PERSONA=empty', AGENTEXPENSE_DEMO_PARENT=str(parent))
        self.assertEqual(code, 0, output)
        self.assertIn('Smoke check passed at http://127.0.0.1:', output)
        self.assertIn('agent ready', output)
        self.assertIn('Demo stopped; removed', output)
        self.assertEqual(list(parent.iterdir()), [])
        self.assertEqual({path: path.stat().st_mtime_ns for path in generated if path.exists()}, before)

    def test_unknown_persona_is_refused(self):
        code, output = self.make('demo', 'PERSONA=nobody')
        self.assertNotEqual(code, 0)
        self.assertIn('invalid choice', output)


class DemoWaitTests(unittest.TestCase):
    """The demo's two waits under a controlled clock: polling interval, deadline, early exit."""

    def setUp(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location('demo_under_test', APPLICATION / 'scripts/setup/demo.py')
        self.demo = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.demo)
        self.now = [0.0]
        self.sleeps = []
        def sleep(seconds):
            self.sleeps.append(seconds)
            self.now[0] += seconds
        from unittest.mock import patch
        for target, value in (('monotonic', lambda: self.now[0]), ('sleep', sleep)):
            patcher = patch.object(self.demo.time, target, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        temporary = tempfile.TemporaryDirectory(dir=os.environ.get('EXPENSES_TEST_TMP'), prefix='demo-wait-')
        self.addCleanup(temporary.cleanup)
        self.instance = self.demo.Demo.__new__(self.demo.Demo)
        self.instance.root = Path(temporary.name)
        self.instance.dashboard, self.instance.env, self.instance.port = self.instance.root, {}, 1
        self.instance.out = open(os.devnull, 'w')
        self.addCleanup(self.instance.out.close)

    def server(self, exits_after=None):
        from unittest.mock import MagicMock
        process = MagicMock()
        process.poll.side_effect = lambda: 1 if exits_after is not None and self.now[0] >= exits_after else None
        return process

    def health(self, *states):
        from unittest.mock import MagicMock
        replies = iter(states)
        self.assets = []
        def urlopen(url, timeout):
            response = MagicMock(status=200)
            response.__enter__.return_value = response
            if url.endswith(('.js', '.css')):
                self.assets.append(url)
                if 'missing' in url:
                    raise urllib.error.HTTPError(url, 404, 'Not Found', {}, io.BytesIO())
                return response
            state = next(replies)
            if isinstance(state, Exception):
                raise state
            response.read.return_value = (PAGE if state == 'page' else state.encode() if state.startswith('<') else
                                          json.dumps({'ok': True, 'agent': {'state': state}}).encode())
            return response
        return urlopen

    def test_start_polls_every_interval_until_the_deadline(self):
        from unittest.mock import patch
        with patch.object(self.demo.subprocess, 'Popen', return_value=self.server()), \
                patch.object(self.demo.urllib.request, 'urlopen', side_effect=OSError('refused')):
            with self.assertRaisesRegex(RuntimeError, 'did not start'):
                self.instance.start()
        self.assertEqual(set(self.sleeps), {self.demo.POLL})
        self.assertAlmostEqual(self.now[0], self.demo.START_SECONDS, delta=self.demo.POLL)

    def test_start_returns_on_the_first_healthy_answer_and_stops_waiting_when_the_server_exits(self):
        from unittest.mock import patch
        with patch.object(self.demo.subprocess, 'Popen', return_value=self.server()), \
                patch.object(self.demo.urllib.request, 'urlopen', side_effect=self.health(OSError(), OSError(), 'checking')):
            self.instance.start()
        self.assertEqual(self.sleeps, [self.demo.POLL] * 2)
        self.sleeps.clear()
        with patch.object(self.demo.subprocess, 'Popen', return_value=self.server(exits_after=1)), \
                patch.object(self.demo.urllib.request, 'urlopen', side_effect=OSError('refused')):
            with self.assertRaisesRegex(RuntimeError, 'did not start'):
                self.instance.start()
        self.assertLess(self.now[0], 1.5)

    def test_smoke_waits_for_the_agent_check_and_fails_when_it_never_settles(self):
        from unittest.mock import patch
        self.now[0] = 0
        with patch.object(self.demo.urllib.request, 'urlopen', side_effect=self.health('page', 'checking', 'checking', 'ready')):
            self.demo.smoke(self.instance)
        self.assertEqual(self.sleeps, [self.demo.POLL] * 2)
        self.sleeps.clear()
        forever = self.health('page', *(['checking'] * 1000))
        with patch.object(self.demo.urllib.request, 'urlopen', side_effect=forever):
            with self.assertRaisesRegex(RuntimeError, 'agent check did not pass'):
                self.demo.smoke(self.instance)
        self.assertEqual(set(self.sleeps), {self.demo.POLL})
        self.assertAlmostEqual(sum(self.sleeps), self.demo.AGENT_CHECK_SECONDS, delta=self.demo.POLL)
        with patch.object(self.demo.urllib.request, 'urlopen', side_effect=self.health('page', 'paused')):
            with self.assertRaisesRegex(RuntimeError, 'paused'):
                self.demo.smoke(self.instance)

    def test_smoke_loads_the_pages_scripts_and_styles_under_the_base_path(self):
        from unittest.mock import patch
        with patch.object(self.demo.urllib.request, 'urlopen', side_effect=self.health('page', 'ready')):
            self.demo.smoke(self.instance)
        self.assertEqual(self.assets, ['http://127.0.0.1:1/expenses/assets/index.js',
                                       'http://127.0.0.1:1/expenses/assets/index.css'])
        # A build served under the wrong base path leaves a blank page: its files answer 404.
        broken = '<div id="root"></div><script type="module" src="/expenses/assets/missing.js"></script>'
        with patch.object(self.demo.urllib.request, 'urlopen', side_effect=self.health(broken)):
            with self.assertRaisesRegex(RuntimeError, 'could not load /expenses/assets/missing.js'):
                self.demo.smoke(self.instance)
        with patch.object(self.demo.urllib.request, 'urlopen', side_effect=self.health('<div id="root"></div>')):
            with self.assertRaisesRegex(RuntimeError, 'names no scripts'):
                self.demo.smoke(self.instance)


class BasePathTests(unittest.TestCase):
    def test_every_way_of_starting_the_server_uses_the_builds_base_path(self):
        vite = (APPLICATION / 'review-dashboard/vite.config.ts').read_text()
        built = re.search(r"base: command === 'build' \|\| isPreview \? '([^']+)'", vite).group(1)
        self.assertEqual(built, '/expenses/')
        makefile = (APPLICATION / 'Makefile').read_text()
        serve = makefile.split('\nserve:\n', 1)[1].split('\n\n', 1)[0]
        self.assertIn(f'EXPENSES_BASE_PATH="$${{EXPENSES_BASE_PATH-{built}}}"', serve)
        demo = (APPLICATION / 'scripts/setup/demo.py').read_text()
        self.assertIn(f"BASE_PATH = '{built}'", demo)
        self.assertIn("'EXPENSES_BASE_PATH': BASE_PATH", demo)
        unit = (APPLICATION / 'review-dashboard/server/expense-dashboard.service').read_text()
        self.assertIn(f'Environment=EXPENSES_BASE_PATH={built}\n', unit)
        plist = (APPLICATION / 'docs/deploy/org.example.agentexpense.plist').read_text()
        self.assertRegex(plist, rf'<key>EXPENSES_BASE_PATH</key>\s*<string>{re.escape(built)}</string>')
        for name in ('README.md', 'SETUP.md', 'docs/deploy/local.md'):
            text = (APPLICATION / name).read_text()
            self.assertIn(f'http://127.0.0.1:8701{built}', text, name)
            self.assertNotRegex(text, r'http://127\.0\.0\.1:8701/(?!expenses/)', name)


class DocumentTests(unittest.TestCase):
    def test_policy_split(self):
        general = (APPLICATION / 'policy/BOOKKEEPING.md').read_text()
        for heading in ('Double entry', 'Evidence and receipts', 'Duplicates', 'Currencies and conversions', 'Transfers',
                        'Prepaid wallets', 'People', 'Investments', 'Rewards', 'Pending entries',
                        'Statements and reconciliation', 'Recurring rules, varying amounts and bills',
                        'Edit, Delete, Trash and retention', 'Before adding', 'Rebuild and verify'):
            self.assertRegex(general, rf'(?m)^## {re.escape(heading)}', heading)
        personal = [APPLICATION / 'templates/ledger/AGENTS.md', *sorted((APPLICATION / 'fixtures/ledgers').glob('*/AGENTS.md'))]
        self.assertEqual(len(personal), 5)
        for path in personal:
            text = path.read_text()
            self.assertIn('policy/BOOKKEEPING.md', text, path)
            for general_heading in ('## Delete, Trash and retention', '## Varying recurring rules', '## Source of truth'):
                self.assertNotIn(general_heading, text, path)

    def test_agents_md_is_the_only_instruction_file_and_links_setup(self):
        self.assertIn('SETUP.md', (APPLICATION / 'AGENTS.md').read_text())
        self.assertFalse((APPLICATION / 'CLAUDE.md').exists())

    def test_documented_make_targets_and_links_exist(self):
        makefile = (APPLICATION / 'Makefile').read_text()
        targets = set(re.findall(r'(?m)^([a-z][a-z0-9-]*):', makefile))
        for name in SETUP_DOCUMENTS:
            path = APPLICATION / name
            text = path.read_text()
            for target in re.findall(r'\bmake ([a-z][a-z0-9-]+)', text):
                self.assertIn(target, targets, f'{name}: make {target}')
            for link in re.findall(r'\]\(([^)#\s]+)(?:#[^)]*)?\)', text):
                if '://' in link or link.startswith('mailto:'):
                    continue
                self.assertTrue((path.parent / link).exists(), f'{name}: {link}')


if __name__ == '__main__':
    unittest.main()
