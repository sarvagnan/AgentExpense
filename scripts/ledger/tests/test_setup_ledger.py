"""Setup: the ledger template, accounts and opening balances, all in fictional ledgers."""
from datetime import date
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path[:0] = [str(Path(__file__).resolve().parents[1]), str(Path(__file__).resolve().parents[2])]
from project_paths import APPLICATION_ROOT
from ledger_manifest import CURRENT_FORMAT, read_manifest
from store import load, read_json
import setup_ledger
from setup_ledger import account_changes, apply, opening_changes

sys.path.insert(0, str(APPLICATION_ROOT / 'scripts/setup'))
from new_ledger import SetupError, check_target, write_ledger

READERS = {'example-card-pdf', 'generic-csv', 'generic-sheet'}
HLEDGER = os.environ.get('HLEDGER_BIN') or 'hledger'


class SetupFixture(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(dir=os.environ.get('EXPENSES_TEST_TMP', '/tmp'), prefix='expense-setup-')
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name) / 'ledger'
        write_ledger(self.root, 'Fictional Owner', 'USD', None, date(2026, 11, 5))

    def add(self, **values):
        return apply(self.root, lambda: account_changes(self.root, readers=READERS, **values))

    def opening(self, start, *balances):
        return apply(self.root, lambda: opening_changes(self.root, start, list(balances)))

    def entries(self):
        return read_json(self.root / 'data/expenses/2026.json')


class TemplateTests(SetupFixture):
    def test_template_makes_an_empty_valid_ledger(self):
        manifest = read_manifest(self.root)
        self.assertEqual(manifest['format'], CURRENT_FORMAT)
        self.assertEqual((manifest['name'], manifest['defaultCurrency']), ('Fictional Owner', 'USD'))
        self.assertNotIn('locale', manifest)
        self.assertNotIn('startDate', manifest)
        self.assertEqual(manifest['modules'], {'fuel': False, 'investments': {'prices': None},
                                               'history': {'oldLedger': None}, 'weeklyReview': False})
        self.assertEqual(manifest['review'], {'storageKey': None})
        accounts = read_json(self.root / 'data/accounts.json')
        self.assertEqual(accounts, {'commodities': ['USD'], 'accounts': {}})
        self.assertEqual(self.entries(), [])
        load(self.root)
        for name in ('AGENTS.md', 'README.md', '.gitignore', 'readers/README.md', 'checks/README.md'):
            self.assertTrue((self.root / name).is_file(), name)
        self.assertIn('policy/BOOKKEEPING.md', (self.root / 'AGENTS.md').read_text())
        self.assertNotIn('{{', (self.root / 'ledger.json').read_text() + (self.root / 'data/accounts.json').read_text())

    def test_each_new_ledger_gets_its_own_id_and_optional_locale(self):
        other = self.root.parent / 'other'
        write_ledger(other, 'Second Owner', 'EUR', 'en-GB', date(2026, 11, 5))
        self.assertNotEqual(read_manifest(other)['id'], read_manifest(self.root)['id'])
        self.assertEqual(read_manifest(other)['locale'], 'en-GB')

    def test_invalid_settings_and_targets_are_refused_before_writing(self):
        for name, currency, locale in (('Owner', 'DOLLARS', None), (' ', 'USD', None), ('Owner', 'USD', 'not a locale!')):
            target = self.root.parent / 'refused'
            with self.assertRaises(SetupError):
                write_ledger(target, name, currency, locale, date(2026, 11, 5))
            self.assertFalse(target.exists())
        application = self.root.parent / 'application'
        application.mkdir()
        with self.assertRaisesRegex(SetupError, 'inside the application'):
            check_target(application / 'fixtures/new-ledger', application)
        self.assertEqual(check_target(application / 'ledger', application), (application / 'ledger').resolve())
        with self.assertRaisesRegex(SetupError, 'contains the application'):
            check_target(self.root.parent, application)
        with self.assertRaisesRegex(SetupError, 'not empty'):
            check_target(self.root)


class AccountTests(SetupFixture):
    def test_balance_sheet_accounts_take_names_currency_and_metadata(self):
        self.assertEqual(self.add(kind='bank', label='Example Checking', aliases=['Checking', 'checking ', 'main']),
                         'Assets:Bank:Example Checking')
        self.add(kind='card', label='Example Card', aliases=['card'], reader='example-card-pdf', identifier='1234')
        self.add(kind='cash', label='Cash')
        self.add(kind='wallet', label='Transit Pass')
        self.add(kind='loan', label='Study Loan')
        self.add(kind='person', label='Fictional Friend')
        self.add(kind='bank', label='Travel Account', currency='eur')
        self.add(kind='investment', label='Index Units', currency='DEMOUNITS')
        self.add(account='Expenses:Groceries')
        accounts = read_json(self.root / 'data/accounts.json')
        self.assertEqual(list(accounts['accounts']), [
            'Assets:Bank:Example Checking', 'Liabilities:Credit Cards:Example Card', 'Assets:Cash',
            'Assets:Wallets:Transit Pass', 'Liabilities:Loans:Study Loan', 'Assets:People:Fictional Friend',
            'Assets:Bank:Travel Account', 'Assets:Investments:Index Units', 'Expenses:Groceries'])
        self.assertEqual(accounts['accounts']['Assets:Bank:Example Checking'],
                         {'kind': 'bank', 'currency': 'USD', 'label': 'Example Checking', 'aliases': ['checking', 'main']})
        self.assertEqual(accounts['accounts']['Liabilities:Credit Cards:Example Card']['statement'],
                         {'reader': 'example-card-pdf', 'identifier': '1234'})
        self.assertEqual(accounts['accounts']['Expenses:Groceries'], {})
        self.assertEqual(accounts['commodities'], ['USD', 'EUR', 'DEMOUNITS'])
        load(self.root)

    def test_refusals_leave_the_ledger_unchanged(self):
        self.add(kind='bank', label='Example Checking')
        before = (self.root / 'data/accounts.json').read_bytes()
        refusals = [
            (dict(kind='bank', label='Example Checking'), 'already declared'),
            (dict(kind='bank', label='Other', reader='unknown-reader'), 'Unknown statement reader'),
            (dict(kind='card', label='Other', identifier='1234'), 'needs --reader'),
            (dict(kind='cash', label='Other', reader='generic-csv'), 'bank and card'),
            (dict(kind='bank', label='Other', currency='NOPE'), 'ISO 4217'),
            (dict(kind='bank', label='Example Checking:Sub'), 'nest'),
            (dict(kind='savings', label='Other'), '--kind must be'),
            (dict(kind='bank', label=' '), '--label'),
            (dict(account='Assets:Unlabelled'), 'need --kind'),
            (dict(account='Groceries'), 'Account names look like'),
            (dict(kind='bank', label='Other', account='Expenses:Other'), 'start with Assets'),
        ]
        for values, message in refusals:
            with self.subTest(values=values), self.assertRaisesRegex(ValueError, message):
                self.add(**values)
        self.assertEqual((self.root / 'data/accounts.json').read_bytes(), before)


class OpeningTests(SetupFixture):
    def setUp(self):
        super().setUp()
        self.add(kind='bank', label='Example Checking', aliases=['checking'])
        self.add(kind='card', label='Example Card', aliases=['card'])
        self.add(kind='person', label='Fictional Friend')
        self.add(kind='bank', label='Yen Account', currency='JPY')

    def test_openings_are_assertions_on_the_start_date(self):
        self.opening('2026-11-05', 'checking=2,400', 'Example Card=312.40', 'Fictional Friend=-25.5', 'Yen Account=1000')
        entries = self.entries()
        self.assertEqual([e['description'] for e in entries], ['Opening balance'] * 4)
        self.assertEqual({e['date'] for e in entries}, {'2026-11-05'})
        held = {e['postings'][0]['account']: e['postings'][0] for e in entries}
        self.assertEqual(held['Assets:Bank:Example Checking']['amount'], '2400.00')
        # A card's amount owed is a liability: typed as owed, held as negative.
        self.assertEqual(held['Liabilities:Credit Cards:Example Card']['amount'], '-312.40')
        self.assertEqual(held['Liabilities:Credit Cards:Example Card']['assert'], {'amount': '-312.40', 'commodity': 'USD'})
        self.assertEqual(held['Assets:People:Fictional Friend']['amount'], '-25.50')
        self.assertEqual(held['Assets:Bank:Yen Account']['assert'], {'amount': '1000.00', 'commodity': 'JPY'})
        for entry in entries:
            self.assertEqual(entry['postings'][1]['account'], 'Equity:Opening Balances')
            self.assertEqual(entry['postings'][1]['commodity'], entry['postings'][0]['commodity'])
        self.assertEqual(len({e['id'] for e in entries}), 4)
        self.assertEqual(read_manifest(self.root)['startDate'], '2026-11-05')
        self.assertIn('Equity:Opening Balances', read_json(self.root / 'data/accounts.json')['accounts'])
        subprocess.run([sys.executable, str(APPLICATION_ROOT / 'scripts/ledger/generate_ledger.py'), '--root', str(self.root)],
                       check=True, capture_output=True)
        journal = self.root / 'expense_ledger.ledger'
        subprocess.run([HLEDGER, '-f', str(journal), 'check', '-s', 'ordereddates'], check=True, capture_output=True)
        report = subprocess.run([HLEDGER, '-f', str(journal), 'bal', 'Liabilities', '-N', '--flat'], check=True,
                                capture_output=True, text=True).stdout
        self.assertIn('-312.40 USD', report)

    def test_refusals_leave_every_file_unchanged(self):
        self.opening('2026-11-05', 'checking=100')
        snapshot = {p: p.read_bytes() for p in self.root.rglob('*.json')}
        refusals = [
            (('2026-11-06', 'card=1'), 'already starts on 2026-11-05'),
            (('2026-11-05', 'checking=5'), 'already has an opening balance'),
            (('2026-11-05', 'card=1', 'card=2'), 'already has an opening balance'),
            (('2026-11-05', 'Yen Account=1.5'), 'at most 0 decimal places'),
            (('2026-11-05', 'Example Card=lots'), 'not an amount'),
            (('2026-11-05', 'nobody=1'), 'matches no declared account'),
            (('2026-11-05', 'card'), 'ACCOUNT=AMOUNT'),
        ]
        for arguments, message in refusals:
            with self.subTest(arguments=arguments), self.assertRaisesRegex(ValueError, message):
                self.opening(*arguments)
        self.assertEqual({p: p.read_bytes() for p in self.root.rglob('*.json')}, snapshot)

    def test_a_failed_write_restores_files_already_written(self):
        before = {p: p.read_bytes() for p in self.root.rglob('*.json')}
        real = setup_ledger.dump
        written = []
        def failing(path, value):
            if written:
                raise OSError('fictional disk failure')
            written.append(path)
            real(path, value)
        with patch.object(setup_ledger, 'dump', failing), self.assertRaisesRegex(OSError, 'fictional'):
            self.opening('2026-11-05', 'checking=100')
        self.assertEqual({p: p.read_bytes() for p in self.root.rglob('*.json')}, before)

    def test_cli_reports_refusals_without_a_traceback(self):
        result = subprocess.run([sys.executable, str(APPLICATION_ROOT / 'scripts/ledger/setup_ledger.py'), '--root',
                                 str(self.root), 'opening', '--date', '2026-11-05', 'nobody=1'],
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 1)
        self.assertIn('matches no declared account', result.stderr)
        self.assertNotIn('Traceback', result.stderr)


if __name__ == '__main__':
    unittest.main()
