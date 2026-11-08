"""make verify-data's hledger step: at once for a person, in the background for saves, skipped without hledger."""
from contextlib import redirect_stderr, redirect_stdout
import io
import os
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch

APPLICATION = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(APPLICATION / 'scripts/ledger'))
import second_check

HLEDGER = second_check.find_hledger()
HEAD = 'commodity INR\naccount Assets:Cash\naccount Expenses:Food\n\n'
GOOD = HEAD + '2026-01-02 * Lunch\n    ; id: aaaaaaaaaaaaaaaa\n    Expenses:Food    100.00 INR\n    Assets:Cash    -100.00 INR\n'
BAD = GOOD.replace('Expenses:Food    100.00', 'Expenses:Drinks    100.00')


class VerifyTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory(prefix='expense-second-check-', dir=os.environ.get('EXPENSES_TEST_TMP'))
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)

    def verify(self, text, hledger, **environ):
        (self.root / second_check.JOURNAL).write_text(text)
        out, err = io.StringIO(), io.StringIO()
        with patch.dict(os.environ, environ), redirect_stdout(out), redirect_stderr(err):
            code = second_check.verify(self.root, hledger)
        return code, out.getvalue(), err.getvalue()

    @unittest.skipUnless(HLEDGER, 'hledger is not installed')
    def test_a_person_gets_hledgers_failure_in_the_plain_words_of_the_queue_item(self):
        self.assertEqual(self.verify(GOOD, HLEDGER, EXPENSES_HLEDGER_CHECK='')[:2], (0, 'hledger second check: passed\n'))
        code, out, err = self.verify(BAD, HLEDGER, EXPENSES_HLEDGER_CHECK='')
        self.assertEqual(code, 1)
        found = second_check.finding(second_check.run_hledger(HLEDGER, self.root / second_check.JOURNAL)[1], BAD)
        self.assertTrue(err.startswith(f'hledger second check failed: {found["title"]}.\n{found["summary"]}\nEntry: aaaaaaaaaaaaaaaa\n'), err)
        self.assertIn('An account isn’t declared', err)
        self.assertIn('\nhledger said:\n', err)

    def test_saves_leave_it_to_the_background_and_without_hledger_it_is_skipped(self):
        missing = str(self.root / 'no-hledger-here')
        code, out, _ = self.verify(BAD, missing, EXPENSES_HLEDGER_CHECK='background')
        self.assertEqual((code, out), (0, 'hledger second check: in the background (the server checks each commit; make verify-data runs it now)\n'))
        code, out, _ = self.verify(BAD, None, EXPENSES_HLEDGER_CHECK='')
        self.assertEqual((code, out), (0, 'hledger is not installed: the second check is skipped (it is optional; see make doctor)\n'))
        self.assertIsNone(second_check.find_hledger({'PATH': str(self.root), 'HLEDGER_BIN': missing}))


if __name__ == '__main__':
    unittest.main()
