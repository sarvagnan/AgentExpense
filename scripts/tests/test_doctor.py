"""make doctor and make check-version against stub tools and fictional application roots."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

APPLICATION = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(APPLICATION / 'scripts'))
import check_version
from ledger_manifest import CURRENT_FORMAT
import doctor

PIN = '26.13.0'
PACKAGES = "PACKAGES = {'codex': ('@openai/codex', 'codex'), 'claude': ('@anthropic-ai/claude-code', 'claude')}\n"


def executable(path, body):
    path.write_text('#!/bin/sh\n' + body)
    path.chmod(0o755)


class DoctorFixture(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(dir=os.environ.get('EXPENSES_TEST_TMP'), prefix='doctor-')
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name)
        self.app = self.base / 'application'
        dashboard = self.app / 'review-dashboard'
        server = dashboard / 'server'
        (server / 'adapters').mkdir(parents=True)
        (self.app / 'VERSION').write_text('0.3.0\n')
        (self.app / '.node-version').write_text(PIN + '\n')
        self.package = dict(name='fictional', version='0.3.0', engines=dict(node='>=22.13.0'),
                            dependencies={'@openai/codex': '0.166.0', '@anthropic-ai/claude-code': '2.1.316', 'small-lib': '1.0.0'})
        self.write_json(dashboard / 'package.json', self.package)
        self.lock = dict(name='fictional', version='0.3.0', lockfileVersion=3, packages={
            '': dict(name='fictional', version='0.3.0'),
            'node_modules/@openai/codex': dict(version='0.166.0'),
            'node_modules/@anthropic-ai/claude-code': dict(version='2.1.316'),
            'node_modules/small-lib': dict(version='1.0.0'),
            'node_modules/small-lib-other-platform': dict(version='1.0.0', optional=True)})
        self.write_json(dashboard / 'package-lock.json', self.lock)
        installed = {name: item for name, item in self.lock['packages'].items() if name and not item.get('optional')}
        self.write_json(dashboard / 'node_modules/.package-lock.json', dict(name='fictional', packages=installed))
        for package, binary in [('@openai/codex', 'codex'), ('@anthropic-ai/claude-code', 'claude')]:
            self.write_json(dashboard / 'node_modules' / package / 'package.json', dict(version=self.package['dependencies'][package]))
            (dashboard / 'node_modules/.bin').mkdir(exist_ok=True)
            (dashboard / 'node_modules/.bin' / binary).write_text('')
        (server / 'pyproject.toml').write_text('[project]\nname = "expense-dashboard-server"\nversion = "0.3.0"\nrequires-python = ">=3.11"\n')
        (server / 'uv.lock').write_text('version = 1\n\n[[package]]\nname = "other"\nversion = "9.9.9"\n\n'
                                        '[[package]]\nname = "expense-dashboard-server"\nversion = "0.3.0"\nsource = { virtual = "." }\n')
        (server / '.venv').mkdir()
        (server / 'adapters/__init__.py').write_text(PACKAGES)
        self.tools = self.base / 'tools'
        self.node = self.base / 'node-bin'
        self.tools.mkdir()
        self.node.mkdir()
        executable(self.node / 'node', f'echo v{PIN}\n')
        executable(self.node / 'npm', 'echo 11.20.0\n')
        executable(self.tools / 'fnm', f'case "$1" in --version) echo "fnm 1.39.0";; list) echo "* v{PIN}";; '
                                       f'exec) echo "{self.node}/node";; esac\n')
        executable(self.tools / 'uv', 'case "$1" in --version) echo "uv 0.12.13 (fictional)";; sync) exit "${UV_SYNC_STATUS:-0}";; esac\n')
        executable(self.tools / 'hledger', 'echo "hledger ${HLEDGER_STUB_VERSION:-1.52.4}, fictional"\n')
        executable(self.tools / 'jj', 'echo "jj 0.46.0"\n')
        executable(self.tools / 'git', 'echo "git version 2.54.0"\n')
        self.environ = {'PATH': str(self.tools)}

    def write_json(self, path, value):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value))

    def rows(self, python=(3, 12, 1), **environ):
        return {row.check: row for row in doctor.diagnose(self.app, {**self.environ, **environ}, python)}

    def statuses(self, **environ):
        return {name: row.status for name, row in self.rows(**environ).items()}

    def ledger(self, manifest=None, repository='.jj'):
        root = self.base / 'ledger-folder'
        root.mkdir()
        if repository:
            (root / repository).mkdir()
        if manifest is not None:
            (root / 'ledger.json').write_text(manifest if isinstance(manifest, str) else json.dumps(manifest))
        return str(root)


class DoctorTests(DoctorFixture):
    def test_ready_machine_without_a_ledger_reports_information_only(self):
        rows = self.rows()
        self.assertEqual({name: row.status for name, row in rows.items()}, {
            'AgentExpense': 'ok', 'Python': 'ok', 'fnm': 'ok', 'Node': 'ok', 'Node pin': 'ok', 'npm': 'ok', 'uv': 'ok',
            'hledger': 'ok', 'jj / git': 'ok', 'Ledger': 'info', 'npm packages': 'ok', 'Python packages': 'ok', 'Agent adapter': 'ok'})
        self.assertEqual(rows['Ledger'].detail, 'no ledger selected (tests use fixtures/ledgers/asha)')
        self.assertEqual(rows['hledger'].detail, '1.52.4 (needs 1.25+)')
        self.assertEqual(rows['Agent adapter'].detail, 'codex: pinned CLI 0.166.0 present (sign-in not checked)')
        text = doctor.render(list(rows.values()))
        self.assertIn('note: Set LEDGER_DIR', text)
        self.assertTrue(text.endswith('Ready.'))
        # The make default names the absent application ledger/: still no selection.
        self.assertEqual(self.statuses(LEDGER_DIR=str(self.app / 'ledger'))['Ledger'], 'info')

    def test_fresh_clone_without_dependencies_blocks_with_fixes(self):
        shutil.rmtree(self.app / 'review-dashboard/node_modules')
        shutil.rmtree(self.app / 'review-dashboard/server/.venv')
        rows = self.rows()
        self.assertEqual((rows['npm packages'].status, rows['npm packages'].fix), ('FAIL', 'npm --prefix review-dashboard ci'))
        self.assertEqual((rows['Python packages'].status, rows['Python packages'].fix), ('FAIL', 'uv sync --locked --directory review-dashboard/server'))
        self.assertEqual(rows['Agent adapter'].status, 'warn')
        self.assertIn('2 blocking', doctor.render(list(rows.values())))

    def test_dependencies_must_match_lockfiles(self):
        installed = json.loads((self.app / 'review-dashboard/node_modules/.package-lock.json').read_text())
        installed['packages']['node_modules/small-lib']['version'] = '1.0.1'
        self.write_json(self.app / 'review-dashboard/node_modules/.package-lock.json', installed)
        rows = self.rows(UV_SYNC_STATUS='1')
        self.assertEqual((rows['npm packages'].status, rows['npm packages'].detail), ('FAIL', '1 differ from package-lock.json'))
        self.assertEqual((rows['Python packages'].status, rows['Python packages'].detail), ('FAIL', 'differ from uv.lock'))

    def test_blocking_tool_versions(self):
        self.assertEqual(self.rows(python=(3, 10, 14))['Python'].status, 'FAIL')
        old = self.rows(HLEDGER_STUB_VERSION='1.24.1')['hledger']
        self.assertEqual((old.status, old.detail), ('FAIL', '1.24.1 is older than 1.25'))
        self.assertEqual(self.statuses(HLEDGER_STUB_VERSION='1.25')['hledger'], 'ok')
        (self.tools / 'hledger').unlink()
        self.assertEqual(self.statuses()['hledger'], 'FAIL')
        (self.tools / 'jj').unlink()
        row = self.rows()['jj / git']
        self.assertEqual((row.status, row.detail, row.fix), ('warn', 'git 2.54.0; jj not found, and saves need it', 'Install Jujutsu (jj).'))
        (self.tools / 'git').unlink()
        self.assertEqual(self.statuses()['jj / git'], 'FAIL')
        (self.tools / 'uv').unlink()
        self.assertEqual(self.statuses()['uv'], 'FAIL')

    def test_node_selection(self):
        executable(self.tools / 'fnm', 'case "$1" in --version) echo "fnm 1.39.0";; list) echo "* v25.9.0";; esac\n')
        rows = self.rows()
        self.assertEqual((rows['Node'].status, rows['Node'].fix), ('FAIL', f'fnm install {PIN}'))
        self.assertEqual(rows['npm'].status, 'FAIL')
        (self.tools / 'fnm').unlink()
        self.assertEqual(self.statuses()['Node'], 'FAIL')
        path = f'{self.tools}{os.pathsep}{self.node}'
        rows = self.rows(PATH=path)
        self.assertEqual((rows['fnm'].status, rows['Node'].status, rows['npm'].status), ('warn', 'ok', 'ok'))
        executable(self.node / 'node', 'echo v25.9.0\n')
        node = self.rows(PATH=path)['Node']
        self.assertEqual((node.status, node.detail), ('warn', f'25.9.0 on PATH; .node-version pins {PIN}'))

    def test_engines_that_exclude_the_pin_are_a_finding(self):
        self.package['engines']['node'] = '>=27.0.0'
        self.write_json(self.app / 'review-dashboard/package.json', self.package)
        row = self.rows()['Node pin']
        self.assertEqual(row.status, 'warn')
        self.assertIn('outside review-dashboard/package.json engines.node', row.detail)

    def test_ledger_formats(self):
        for manifest, status, words in [
            (None, 'warn', 'format 0'), (dict(format=CURRENT_FORMAT), 'ok', f'format {CURRENT_FORMAT}'),
            (dict(format=CURRENT_FORMAT + 1), 'warn', f'format {CURRENT_FORMAT + 1}'),
            ('{"format": ', 'FAIL', 'unreadable'), ('{"format": "1"}', 'FAIL', 'unreadable')]:
            with self.subTest(manifest=manifest):
                root = self.ledger(manifest)
                row = self.rows(LEDGER_DIR=root)['Ledger']
                self.assertEqual(row.status, status)
                self.assertIn(words, row.detail)
                shutil.rmtree(root)
        row = self.rows(LEDGER_DIR=self.ledger(None))['Ledger']
        self.assertEqual(row.fix, 'make upgrade-ledger ARGS="--dry-run", then make upgrade-ledger')
        shutil.rmtree(self.base / 'ledger-folder')
        self.assertEqual(self.rows(LEDGER_DIR=self.ledger(dict(format=CURRENT_FORMAT), repository=None))['Ledger'].status, 'warn')
        shutil.rmtree(self.base / 'ledger-folder')

    def test_git_only_ledger_warns_because_saves_need_jj(self):
        root = self.ledger(dict(format=CURRENT_FORMAT), repository='.git')
        row = self.rows(LEDGER_DIR=root)['Ledger']
        self.assertEqual(row.status, 'warn')
        self.assertIn('git repository without jj; saves need jj', row.detail)
        self.assertEqual(row.fix, f'Colocate jj with it: jj git init --colocate {root}')
        (Path(root) / '.jj').mkdir()
        self.assertEqual(self.rows(LEDGER_DIR=root)['Ledger'].status, 'ok', 'A colocated ledger is ready')
        self.assertEqual(self.rows(LEDGER_DIR=str(self.base / 'absent'))['Ledger'].status, 'FAIL')

    def test_old_format_ledger_alone_does_not_block(self):
        rows = list(doctor.diagnose(self.app, {**self.environ, 'LEDGER_DIR': self.ledger(None)}, (3, 12, 0)))
        self.assertFalse([row.check for row in rows if row.status == 'FAIL'])

    def test_agent_adapters(self):
        self.assertEqual(self.rows(ADAPTER='anthropic')['Agent adapter'].status, 'info')
        self.assertEqual(self.rows(ADAPTER='unknown-one')['Agent adapter'].status, 'warn')
        self.write_json(self.app / 'review-dashboard/node_modules/@anthropic-ai/claude-code/package.json', dict(version='2.1.300'))
        row = self.rows(EXPENSES_AGENT='claude')['Agent adapter']
        self.assertEqual((row.status, row.detail), ('warn', 'claude: pinned CLI 2.1.316, 2.1.300 installed'))
        self.write_json(self.app / 'local.json', dict(agent=dict(adapter='claude', module='adapters/fictional.py')))
        self.assertEqual(self.rows()['Agent adapter'].status, 'warn')
        (self.app / 'adapters').mkdir()
        (self.app / 'adapters/fictional.py').write_text('')
        self.assertEqual(self.rows()['Agent adapter'].status, 'ok')
        (self.app / 'local.json').write_text('[')
        self.assertEqual(self.rows()['Agent adapter'].status, 'warn')

    def test_doctor_runs_without_network_installs_or_agent_clis(self):
        calls = []
        real = subprocess.run
        def record(command, **options):
            calls.append([Path(command[0]).name, *command[1:]])
            return real(command, **options)
        with mock.patch.object(doctor.subprocess, 'run', side_effect=record):
            self.rows()
        self.assertNotIn('codex', {call[0] for call in calls})
        self.assertNotIn('claude', {call[0] for call in calls})
        sync = next(call for call in calls if call[:2] == ['uv', 'sync'])
        self.assertIn('--check', sync)
        self.assertIn('--offline', sync)
        self.assertFalse([call for call in calls if call[0] in ('npm', 'fnm') and 'install' in call or call[0] == 'npm' and 'ci' in call])


class VersionCheckTests(DoctorFixture):
    def test_agreeing_versions(self):
        self.assertEqual(check_version.problems(self.app), [])
        self.assertEqual(self.rows()['AgentExpense'].detail, '0.3.0')

    def test_each_follower_must_match_version(self):
        (self.app / 'VERSION').write_text('0.4.0\n')
        found = check_version.problems(self.app)
        self.assertEqual(len(found), 5, found)
        for name in ['package.json', 'package-lock.json', 'package-lock.json (root package)', 'pyproject.toml', 'uv.lock']:
            self.assertTrue(any(name in problem for problem in found), name)
        self.assertEqual(self.rows()['AgentExpense'].status, 'warn')
        (self.app / 'VERSION').write_text('0.4\n')
        self.assertEqual(check_version.problems(self.app), ["VERSION: VERSION is not a semantic version: '0.4'"])
        self.assertEqual(self.rows()['AgentExpense'].status, 'FAIL')

    def test_versions_read_without_tomllib(self):
        with mock.patch.dict(sys.modules, {'tomllib': None}):
            self.assertEqual(check_version.problems(self.app), [])
            server = self.app / 'review-dashboard/server'
            (server / 'uv.lock').write_text((server / 'uv.lock').read_text().replace('version = "0.3.0"', 'version = "0.2.0"'))
            self.assertEqual(len(check_version.problems(self.app)), 1)

    def test_engines_ranges(self):
        for version, expression, expected in [
            ('26.13.0', '>=22.13.0', True), ('22.12.9', '>=22.13.0', False), ('26.13.0', '26.13.0', True),
            ('26.13.0', '^26.0.0', True), ('27.0.0', '^26.0.0', False), ('0.2.5', '^0.2.0', True), ('0.3.0', '^0.2.0', False),
            ('26.13.4', '~26.13.0', True), ('26.14.0', '~26.13.0', False), ('26.13.0', '>=22 <27', True),
            ('27.1.0', '>=22 <27', False), ('20.0.0', '^18 || ^20', True), ('26.13.0', '>=v22.13.0', True)]:
            with self.subTest(version=version, expression=expression):
                self.assertIs(check_version.satisfies(version, expression), expected)
        for expression in ['', '22.x - 26', 'latest']:
            with self.subTest(expression=expression), self.assertRaises(ValueError):
                check_version.satisfies('26.13.0', expression)

    def test_real_application_versions_agree(self):
        self.assertEqual(check_version.problems(), [])
        run = subprocess.run([sys.executable, str(APPLICATION / 'scripts/check_version.py')], capture_output=True, text=True)
        self.assertEqual(run.returncode, 0, run.stderr)
        import ledger_manifest
        self.assertEqual(ledger_manifest.app_version(), (APPLICATION / 'VERSION').read_text().strip())


if __name__ == '__main__':
    unittest.main()
