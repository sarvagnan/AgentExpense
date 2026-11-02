"""The optional integration-test oracle reuses only exact successful CLI inputs."""
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from hledger_oracle import calendar_dates


class OracleTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='expense-oracle-test-', dir='/tmp')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.binary = self.root / 'hledger'
        self.binary.write_bytes(b'fictional executable version one')
        self.result = __import__('subprocess').CompletedProcess([], 0, 'account,date\nExpenses:Case0,2026-01-01\nAssets:Purse,2026-01-01\n', '')

    def test_only_exact_journal_arguments_and_binary_share_an_oracle(self):
        with patch.dict(os.environ, {'EXPENSES_TEST_ORACLE_CACHE': str(self.root / 'cache')}), patch('hledger_oracle.subprocess.run', return_value=self.result) as run:
            actual = calendar_dates(str(self.binary), 'first journal', '2026-01-01', '2036-01-01')
            self.assertEqual(actual, {'Expenses:Case0': ['2026-01-01']})
            self.assertEqual(calendar_dates(str(self.binary), 'first journal', '2026-01-01', '2036-01-01'), actual)
            self.binary.chmod(0o700)
            with patch.dict(os.environ, {'PATH': str(self.root)}):
                self.assertEqual(calendar_dates('hledger', 'first journal', '2026-01-01', '2036-01-01'), actual)
            self.assertEqual(run.call_count, 1)
            calendar_dates(str(self.binary), 'changed journal', '2026-01-01', '2036-01-01')
            calendar_dates(str(self.binary), 'first journal', '2026-01-01', '2037-01-01')
            self.binary.write_bytes(b'fictional executable version two')
            calendar_dates(str(self.binary), 'first journal', '2026-01-01', '2036-01-01')
            self.assertEqual(run.call_count, 4)

    def test_standalone_tests_always_run_cli_and_failed_results_are_not_cached(self):
        with patch.dict(os.environ, {'EXPENSES_TEST_ORACLE_CACHE': ''}), patch('hledger_oracle.subprocess.run', return_value=self.result) as run:
            for _ in range(2):
                calendar_dates(str(self.binary), 'journal', '2026-01-01', '2036-01-01')
            self.assertEqual(run.call_count, 2)
        with patch.dict(os.environ, {'EXPENSES_TEST_ORACLE_CACHE': str(self.root / 'cache')}), patch('hledger_oracle.subprocess.run', side_effect=__import__('subprocess').CalledProcessError(1, ['hledger'])):
            with self.assertRaises(__import__('subprocess').CalledProcessError):
                calendar_dates(str(self.binary), 'journal', '2026-01-01', '2036-01-01')
            self.assertEqual(list((self.root / 'cache').iterdir()), [])

    def test_cache_cannot_escape_tmp_and_invalid_content_fails_closed(self):
        with patch.dict(os.environ, {'EXPENSES_TEST_ORACLE_CACHE': '/outside-expense-test-cache'}):
            with self.assertRaisesRegex(ValueError, 'under /tmp'):
                calendar_dates(str(self.binary), 'journal', '2026-01-01', '2036-01-01')
        with patch.dict(os.environ, {'EXPENSES_TEST_ORACLE_CACHE': str(self.root / 'cache')}), patch('hledger_oracle.subprocess.run', return_value=self.result):
            calendar_dates(str(self.binary), 'journal', '2026-01-01', '2036-01-01')
            target, = (self.root / 'cache').iterdir()
            target.write_text('incomplete')
            with self.assertRaises(json.JSONDecodeError):
                calendar_dates(str(self.binary), 'journal', '2026-01-01', '2036-01-01')
