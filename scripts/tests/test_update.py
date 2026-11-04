"""make update between tagged releases of a local bare repository; no network.

`make test-update` runs this module. It exports the files a commit of this working copy
would contain into a disposable source repository under the test root and tags:

- v0.1.0: the export as it is (main stays here, as if cloned when it was the latest);
- v0.2.0: a real test migration, ledger format 1 -> 2 adding `testField` to
  ledger.json, with the format, schema and fixture changes a release makes, plus its
  changelog entry;
- v0.3.0: an injected failure, a ledger test that fails after the ledger migration
  has been committed;
- v0.4.0-rc.1: a pre-release, which make update ignores without --pre.

It pushes them to a bare repository, clones it (plain git and jj-colocated), links the
installed dependencies read-only, makes a fictional ledger repository from the asha
fixture and runs the real `make update` in each clone. A fake systemctl or launchctl
stands in for the service manager; no real service is touched.
The test migration lives only in the disposable repository, never in migrations/.
"""
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import unittest

from test_fresh_clone import INHERITED, committed_files

APPLICATION = Path(__file__).resolve().parents[2]
ENABLED = os.environ.get('AGENTEXPENSE_UPDATE_TEST') == '1'
REASON = 'slow: builds and updates disposable clones; run make test-update'

MIGRATION = '''"""Format 1 -> 2, only in make update's test releases: adds testField to ledger.json.

The dashboard copies the manifest into meta.ledger; that is the one declared output change.
"""
from store import dump, read_json

SOURCE_FORMAT = 1
TARGET_FORMAT = 2
OUTPUT_CHANGES = ('$.meta.ledger.testField',)


def plan(root, options):
    manifest = read_json(root / 'ledger.json')
    return {**manifest, 'format': 2, 'testField': True}, read_json(root / 'data/accounts.json')


def apply(root, result):
    dump(root / 'ledger.json', result[0])
'''

FAILING_TEST = '''import unittest


class InjectedFailure(unittest.TestCase):
    def test_release_fails_its_own_checks(self):
        self.fail('Injected failure: this test release must roll back.')
'''

FAKE_SERVICE = '''#!/bin/sh
# Records the call; never contacts a real service manager.
printf '%s %s\\n' "$(basename "$0")" "$*" >> "$FAKE_SERVICE_LOG"
case "$(basename "$0") $1 $2" in
  "systemctl --user is-active") echo active ;;
  "launchctl print "*) echo "state = running" ;;
esac
exit 0
'''

state = {}


def run(command, cwd, env, check=True, **options):
    result = subprocess.run(command, cwd=cwd, env=env, capture_output=True, text=True, **options)
    if check and result.returncode:
        raise AssertionError(f'{command} failed:\n{result.stdout[-3000:]}\n{result.stderr[-3000:]}')
    return result.stdout.strip()


def replace_once(path, old, new):
    text = path.read_text()
    if old not in text:
        raise AssertionError(f'{path}: {old!r} not found')
    path.write_text(text.replace(old, new, 1))


def bump_version(root, old, new, changes):
    """The version files a release changes, then its changelog entry."""
    (root / 'VERSION').write_text(new + '\n')
    replace_once(root / 'review-dashboard/package.json', f'"version": "{old}"', f'"version": "{new}"')
    lock = root / 'review-dashboard/package-lock.json'
    lines = lock.read_text().split('\n')
    hits = [i for i, line in enumerate(lines[:12]) if line.strip() == f'"version": "{old}",']
    assert len(hits) == 2, hits
    for i in hits:
        lines[i] = lines[i].replace(old, new)
    lock.write_text('\n'.join(lines))
    replace_once(root / 'review-dashboard/server/pyproject.toml', f'version = "{old}"', f'version = "{new}"')
    replace_once(root / 'review-dashboard/server/uv.lock', f'name = "expense-dashboard-server"\nversion = "{old}"',
                 f'name = "expense-dashboard-server"\nversion = "{new}"')
    replace_once(root / 'CHANGELOG.md', '## [Unreleased]\n', f'## [Unreleased]\n\n## [{new}] - 2026-11-05\n\n{changes}\n')


def release_020(root):
    """A release that bumps the ledger format, as a real one would."""
    replace_once(root / 'scripts/ledger_manifest.py', 'CURRENT_FORMAT = 1\n', 'CURRENT_FORMAT = 2\n')
    replace_once(root / 'review-dashboard/scripts/ledger-manifest.mjs', 'export const CURRENT_FORMAT = 1;', 'export const CURRENT_FORMAT = 2;')
    schema = root / 'schema/ledger.schema.json'
    value = json.loads(schema.read_text())
    assert value['properties']['format']['const'] == 1
    value['properties']['format']['const'] = 2
    value['properties']['testField'] = {'type': 'boolean'}
    schema.write_text(json.dumps(value, indent=2, ensure_ascii=False) + '\n')
    (root / 'migrations/0002_test_field.py').write_text(MIGRATION)
    # The upgrade test lists the manifest keys a full upgrade produces.
    replace_once(root / 'scripts/ledger/migration_tests/test_upgrade.py',
                 "['format', 'id', 'name', 'defaultCurrency', 'locale', 'startDate', 'modules', 'review'])",
                 "['format', 'id', 'name', 'defaultCurrency', 'locale', 'startDate', 'modules', 'review', 'testField'])")
    for manifest in sorted((root / 'fixtures/ledgers').glob('*/ledger.json')):
        value = json.loads(manifest.read_text())
        manifest.write_text(json.dumps({**value, 'format': 2, 'testField': True}, indent=2, ensure_ascii=False) + '\n')
    bump_version(root, '0.1.0', '0.2.0', '### Added\n\n- Ledger format 2: `ledger.json` gains `testField` (test release).\n\n'
                 '### Upgrade notes\n\n1. Run `make update`; it upgrades the ledger to format 2.')


def release_030(root):
    (root / 'scripts/ledger/tests/test_injected_failure.py').write_text(FAILING_TEST)
    bump_version(root, '0.2.0', '0.3.0', '### Changed\n\n- A release whose own checks fail (test release).\n\n'
                 '### Upgrade notes\n\n1. Run `make update`.')


def setUpModule():
    if not ENABLED:
        return
    base = Path(tempfile.mkdtemp(prefix='update-', dir=os.environ.get('EXPENSES_TEST_TMP')))
    environ = {key: value for key, value in os.environ.items() if key not in INHERITED}
    (base / 'gitconfig').write_text('[user]\n\tname = Update Fixture\n\temail = update@example.invalid\n'
                                    '[init]\n\tdefaultBranch = main\n[advice]\n\tdetachedHead = false\n')
    (base / 'jj.toml').write_text('[user]\nname = "Update Fixture"\nemail = "update@example.invalid"\n')
    fake = base / 'fake-bin'
    fake.mkdir()
    for name in ('systemctl', 'launchctl'):
        (fake / name).write_text(FAKE_SERVICE)
        (fake / name).chmod(0o755)
    environ.update(GIT_CONFIG_GLOBAL=str(base / 'gitconfig'), GIT_CONFIG_NOSYSTEM='1', JJ_CONFIG=str(base / 'jj.toml'),
                   PATH=str(fake) + os.pathsep + environ['PATH'], GIT_TERMINAL_PROMPT='0')
    source, remote = base / 'source', base / 'remote.git'
    for name in committed_files():
        target = source / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(APPLICATION / name, target, follow_symlinks=False)
    run(['git', 'init', '--quiet'], source, environ)
    run(['git', 'init', '--quiet', '--bare', str(remote)], base, environ)
    commits = {}

    def release(version, message):
        run(['python3', 'scripts/check_version.py'], source, environ)
        run(['git', 'add', '--all'], source, environ)
        run(['git', 'commit', '--quiet', '-m', message], source, environ)
        run(['git', 'tag', '-a', 'v' + version, '-m', 'AgentExpense ' + version], source, environ)
        commits[version] = run(['git', 'rev-parse', 'HEAD'], source, environ)

    release('0.1.0', 'AgentExpense 0.1.0')
    run(['git', 'push', '--quiet', str(remote), 'main', 'v0.1.0'], source, environ)
    release_020(source)
    release('0.2.0', 'AgentExpense 0.2.0: test ledger format 2')
    release_030(source)
    release('0.3.0', 'AgentExpense 0.3.0: injected failure')
    run(['git', 'tag', '-a', 'v0.4.0-rc.1', '-m', 'Pre-release'], source, environ)
    run(['git', 'push', '--quiet', str(remote), '--tags'], source, environ)
    state.update(base=base, environ=environ, remote=remote, commits=commits, fake_log=base / 'service.log')


def tearDownModule():
    if state.get('base'):
        shutil.rmtree(state['base'], ignore_errors=True)


def install(clone):
    """Read-only links to installed dependencies, as the fresh-clone test does."""
    modules = clone / 'review-dashboard/node_modules'
    modules.mkdir()
    for entry in (APPLICATION / 'review-dashboard/node_modules').iterdir():
        if not entry.name.startswith('.vite'):
            (modules / entry.name).symlink_to(entry)
    (clone / 'review-dashboard/server/.venv').symlink_to(APPLICATION / 'review-dashboard/server/.venv')


def make_ledger(clone, target, kind, environ):
    shutil.copytree(clone / 'fixtures/ledgers/asha', target,
                    ignore=shutil.ignore_patterns('expense_ledger.ledger', 'drafts', '.lock'))
    (target / '.gitignore').write_text('/drafts/\n/.lock\n/expense_ledger.ledger\n')
    if kind == 'jj':
        run(['jj', 'git', 'init', '--colocate', str(target)], target.parent, environ)
        run(['jj', 'commit', '-m', 'Fictional ledger baseline'], target, environ)
        run(['jj', 'bookmark', 'set', 'main', '-r', '@-'], target, environ)
    else:
        run(['git', 'init', '--quiet'], target, environ)
        run(['git', 'add', '--all'], target, environ)
        run(['git', 'commit', '--quiet', '-m', 'Fictional ledger baseline'], target, environ)


@unittest.skipUnless(ENABLED, REASON)
class UpdateScenario(unittest.TestCase):
    kind = None
    service = None

    @classmethod
    def setUpClass(cls):
        if cls.kind is None:
            raise unittest.SkipTest('base class')
        base, environ = state['base'], dict(state['environ'])
        cls.clone, cls.ledger = base / f'{cls.kind}-application', base / f'{cls.kind}-ledger'
        if cls.kind == 'jj':
            run(['jj', 'git', 'clone', '--colocate', str(state['remote']), str(cls.clone)], base, environ)
        else:
            run(['git', 'clone', '--quiet', str(state['remote']), str(cls.clone)], base, environ)
        # The linked environment is a symlink, which .gitignore's directory pattern doesn't
        # match. Exclude it before any jj snapshot can see it.
        with (cls.clone / '.git/info/exclude').open('a') as exclude:
            exclude.write('/review-dashboard/server/.venv\n')
        install(cls.clone)
        (cls.clone / 'local.json').write_text(json.dumps({'service': dict([cls.service])}))
        make_ledger(cls.clone, cls.ledger, cls.kind, environ)
        environ.update(LEDGER_DIR=str(cls.ledger), FAKE_SERVICE_LOG=str(state['fake_log']))
        cls.environ = environ
        cls.initial = cls.app_commit()
        cls.checkpoint = cls.ledger_commit()

    @classmethod
    def app_commit(cls):
        if cls.kind == 'jj':
            return run(['jj', 'log', '-r', '@-', '--no-graph', '-T', 'commit_id'], cls.clone, cls.environ)
        return run(['git', 'rev-parse', 'HEAD'], cls.clone, cls.environ)

    @classmethod
    def ledger_commit(cls):
        if cls.kind == 'jj':
            return run(['jj', 'log', '-r', '@-', '--no-graph', '-T', 'commit_id'], cls.ledger, cls.environ)
        return run(['git', 'rev-parse', 'HEAD'], cls.ledger, cls.environ)

    def update(self, *arguments, answer=None):
        # make reports any failing recipe as status 2; the output says which outcome it was.
        state['fake_log'].unlink(missing_ok=True)
        command = ['make', '--no-print-directory', 'update'] + (['ARGS=' + ' '.join(arguments)] if arguments else [])
        result = subprocess.run(command, cwd=self.clone, env=self.environ, input=answer, text=True,
                                stdin=None if answer is not None else subprocess.DEVNULL,
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=2400)
        return result.returncode, result.stdout

    def make(self, *targets):
        return subprocess.run(['make', '--no-print-directory', *targets], cwd=self.clone, env=self.environ,
                              stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, timeout=600)

    def check_refuses_local_changes(self):
        readme = self.clone / 'README.md'
        original = readme.read_bytes()
        readme.write_bytes(original + b'\nA local edit.\n')
        try:
            code, output = self.update('--yes')
        finally:
            readme.write_bytes(original)
        self.assertEqual(code, 2, output[-4000:])
        self.assertIn('The application checkout has local changes (README.md)', output)
        self.assertIn('local.json', output)
        self.assertNotIn('Checking out', output)
        self.assert_previous_release_intact(output)
        self.assertEqual(self.service_calls(), [])

    def service_calls(self):
        return state['fake_log'].read_text().splitlines() if state['fake_log'].exists() else []

    def clean(self, root):
        if (root / '.jj').is_dir():
            return run(['jj', 'diff', '-r', '@', '--name-only'], root, self.environ) == ''
        return run(['git', 'status', '--porcelain'], root, self.environ) == ''

    def manifest(self):
        return json.loads((self.ledger / 'ledger.json').read_text())

    def assert_previous_release_intact(self, output):
        self.assertEqual((self.clone / 'VERSION').read_text().strip(), '0.1.0', output[-4000:])
        self.assertEqual(self.app_commit(), self.initial)
        self.assertTrue(self.clean(self.clone), output[-4000:])
        if self.kind == 'git':
            self.assertEqual(run(['git', 'symbolic-ref', '--short', 'HEAD'], self.clone, self.environ), 'main')
        self.assertEqual(self.ledger_commit(), self.checkpoint, output[-4000:])
        self.assertTrue(self.clean(self.ledger))
        self.assertEqual(self.manifest()['format'], 1)
        self.assertNotIn('testField', self.manifest())

    def check_failure_rolls_back(self):
        code, output = self.update('--yes')
        self.assertEqual(code, 2, output[-6000:])
        self.assertNotIn('[y/N]', output)
        # The failure came after the ledger migration had been committed.
        self.assertIn('Upgrading the ledger: make upgrade-ledger', output)
        self.assertIn('Upgraded ledger to format 2.', output)
        self.assertIn('Injected failure: this test release must roll back.', output)
        self.assertIn('Update to 0.3.0 failed during "Verifying, rebuilding and testing"', output)
        self.assertIn('Nothing from 0.3.0 remains installed.', output)
        self.assertIn(f'restored: ledger restored to its checkpoint {self.checkpoint[:12]}', output)
        self.assertIn('restored: journal and dashboard rebuilt', output)
        self.assertNotIn('NOT restored', output)
        self.assert_previous_release_intact(output)
        # The previous release still works against the restored ledger and its rebuilt outputs.
        self.assertTrue((self.ledger / 'expense_ledger.ledger').is_file())
        self.assertTrue((self.clone / 'review-dashboard/public/data/dashboard.json.gz').is_file())
        verify = self.make('verify-data')
        self.assertEqual(verify.returncode, 0, verify.stdout[-4000:])
        # Only the rollback restarts the service: the release failed before its restart.
        calls = self.service_calls()
        self.assertEqual(sum(' restart ' in f' {call} ' or ' kickstart ' in f' {call} ' for call in calls), 1, calls)

    def check_update_to_020(self, *, prompt):
        arguments = ['--to', 'v0.2.0'] if prompt else ['--yes', '--to', 'v0.2.0']
        code, output = self.update(*arguments, answer='y\n' if prompt else None)
        self.assertEqual(code, 0, output[-6000:])
        self.assertEqual('Update AgentExpense 0.1.0 to 0.2.0? [y/N]' in output, prompt)
        self.assertIn('AgentExpense updated from 0.1.0 to 0.2.0. Ledger format 2. The server was restarted.', output)
        self.assertIn('## [0.2.0] - 2026-11-05', output)
        self.assertNotIn('## [0.3.0]', output)
        self.assertEqual((self.clone / 'VERSION').read_text().strip(), '0.2.0')
        self.assertEqual(self.app_commit(), state['commits']['0.2.0'])
        self.assertTrue(self.clean(self.clone))
        self.assertEqual(self.manifest()['format'], 2)
        self.assertIs(self.manifest()['testField'], True)
        self.assertTrue(self.clean(self.ledger))
        template = 'description' if self.kind == 'jj' else None
        message = (run(['jj', 'log', '-r', '@-', '--no-graph', '-T', template], self.ledger, self.environ) if template else
                   run(['git', 'log', '-1', '--format=%s'], self.ledger, self.environ))
        self.assertEqual(message.strip(), 'Upgrade ledger format 1 → 2 (app v0.2.0)')
        self.assertTrue(self.service_calls(), 'the configured service is restarted')
        doctor = self.make('doctor')
        self.assertEqual(doctor.returncode, 0, doctor.stdout)
        self.assertRegex(doctor.stdout, r'(?m)^Ledger +ok +format 2 at ')


class GitCloneUpdate(UpdateScenario):
    """A plain git clone on main, a git ledger and a systemd unit."""
    kind = 'git'
    service = ('systemd', 'agentexpense-test.service')

    def test_1_refuses_local_changes(self):
        self.check_refuses_local_changes()

    def test_2_prompt_shows_changes_and_no_cancels(self):
        code, output = self.update(answer='n\n')
        self.assertEqual(code, 2, output[-4000:])
        self.assertIn('Changes from 0.1.0 to 0.3.0:', output)
        self.assertLess(output.index('## [0.3.0] - 2026-11-05'), output.index('## [0.2.0] - 2026-11-05'))
        self.assertNotIn('## [0.1.0]', output)
        self.assertNotIn('0.4.0', output.split('Changes from')[1])
        self.assertIn('Update AgentExpense 0.1.0 to 0.3.0? [y/N]', output)
        self.assertIn('Update cancelled; nothing changed.', output)
        self.assert_previous_release_intact(output)

    def test_3_failure_after_migration_rolls_back(self):
        self.check_failure_rolls_back()
        self.assertIn('systemctl --user restart agentexpense-test.service', self.service_calls())

    def test_4_update_with_prompt(self):
        self.check_update_to_020(prompt=True)
        self.assertIn('systemctl --user restart agentexpense-test.service', self.service_calls())
        self.assertGreaterEqual(self.service_calls().count('systemctl --user is-active agentexpense-test.service'), 3)

    def test_5_refuses_to_downgrade(self):
        code, output = self.update('--yes', '--to', 'v0.1.0')
        self.assertEqual(code, 2, output[-4000:])
        self.assertIn('v0.1.0 is older than the installed 0.2.0; make update never downgrades.', output)
        self.assertEqual((self.clone / 'VERSION').read_text().strip(), '0.2.0')
        self.assertEqual(self.app_commit(), state['commits']['0.2.0'])
        self.assertEqual(self.service_calls(), [])


class JjCloneUpdate(UpdateScenario):
    """A jj clone colocated with git, a jj ledger and a launchd agent."""
    kind = 'jj'
    service = ('launchd', 'org.example.agentexpense-test')

    def test_1_refuses_local_changes(self):
        self.check_refuses_local_changes()

    def test_2_failure_after_migration_rolls_back(self):
        self.check_failure_rolls_back()
        self.assertTrue(any(re.fullmatch(r'launchctl kickstart -k gui/\d+/org\.example\.agentexpense-test', call)
                            for call in self.service_calls()), self.service_calls())

    def test_3_yes_skips_the_prompt(self):
        self.check_update_to_020(prompt=False)


if __name__ == '__main__':
    unittest.main()
