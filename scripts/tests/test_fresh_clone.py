"""A fresh clone with no ledger/ and no LEDGER_DIR passes its own persona-sensitive tests.

The export holds the files a commit would contain (tracked plus unignored new files),
so ignored ledgers, generated data and dependencies are absent. Installed
dependencies are linked read-only from this checkout; nothing is downloaded.
"""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

APPLICATION = Path(__file__).resolve().parents[2]
NOTICE = 'No ledger selected: tests use the fictional fixture fixtures/ledgers/asha (set LEDGER_DIR to choose another).'
# Selection, nesting and run-root state belong to the outer run, not the clone.
INHERITED = ('LEDGER_DIR', 'LEDGER', 'AGENTEXPENSE_TEST_ROOT', 'AGENTEXPENSE_TEST_SUPERVISED', 'EXPENSES_TEST_TMP',
             'EXPENSES_TEST_ORACLE_CACHE', 'DASHBOARD_DATA_DIR', 'SERVER_TESTS', 'SCANNER_TESTS', 'KEEP_ARTIFACTS',
             'MAKEFLAGS', 'MAKELEVEL', 'MFLAGS', 'PERSONA', 'BROWSER_PERSONA')


def committed_files():
    listed = subprocess.run(['git', 'ls-files', '-z', '--cached', '--others', '--exclude-standard'], cwd=APPLICATION,
                            capture_output=True, check=True).stdout.decode().split('\0')
    return sorted({name for name in listed if name and (APPLICATION / name).is_file()})


class FreshCloneTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory(dir=os.environ.get('EXPENSES_TEST_TMP'), prefix='fresh-clone-')
        cls.clone = Path(cls.temporary.name) / 'application'
        for name in committed_files():
            target = cls.clone / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(APPLICATION / name, target, follow_symlinks=False)
        # Read-only links to installed dependencies; Vite caches stay out.
        modules = cls.clone / 'review-dashboard/node_modules'
        modules.mkdir()
        for entry in (APPLICATION / 'review-dashboard/node_modules').iterdir():
            if not entry.name.startswith('.vite'):
                (modules / entry.name).symlink_to(entry)
        (cls.clone / 'review-dashboard/server/.venv').symlink_to(APPLICATION / 'review-dashboard/server/.venv')
        cls.environ = {key: value for key, value in os.environ.items() if key not in INHERITED}

    @classmethod
    def tearDownClass(cls):
        cls.temporary.cleanup()

    def make(self, *arguments):
        run = subprocess.run(['make', '--no-print-directory', *arguments], cwd=self.clone, env=self.environ,
                             stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, timeout=900)
        return run.returncode, run.stdout

    def test_export_has_no_ledger_or_generated_data(self):
        self.assertFalse((self.clone / 'ledger').exists())
        self.assertFalse((self.clone / 'review-dashboard/app/data').exists())
        self.assertFalse((self.clone / 'fixtures/ledgers/asha/expense_ledger.ledger').exists())
        self.assertTrue((self.clone / 'VERSION').is_file())

    def test_persona_sensitive_server_tests_use_the_default_fixture(self):
        code, output = self.make('test-server', 'SERVER_TESTS=persona')
        self.assertEqual(code, 0, output[-6000:])
        self.assertEqual(output.count(NOTICE), 1, output[-6000:])
        self.assertRegex(output, r'Ran [1-9]\d* tests')

    def test_scanner_cleanup_probe_uses_the_default_fixture(self):
        code, output = self.make('test-scanner', 'SCANNER_TESTS=test_server_worker_suite_removes_owned_root')
        self.assertEqual(code, 0, output[-6000:])
        self.assertIn(NOTICE, output)
        self.assertIn('Ran 1 test', output)

    def test_doctor_reports_no_ledger_as_information(self):
        code, output = self.make('doctor')
        self.assertEqual(code, 0, output)
        self.assertRegex(output, r'(?m)^Ledger +info +no ledger selected \(tests use fixtures/ledgers/asha\)$')
        self.assertNotIn(NOTICE, output)


if __name__ == '__main__':
    unittest.main()
