"""Regression tests with isolated, small stores; scratch files stay under this checkout."""
from copy import deepcopy
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from store import ROOT, canonical_text, dump, entries, load, new_id, read_json, store_paths
from project_paths import APPLICATION_ROOT
from generate_ledger import render_entry
from check_store import check_card_statements
from add_entry import add_entry
from reconcile_statement import CARD, extract_rows, reconcile, text_report, validate_rows


def entry(identity='0000000000000001', day='2026-01-02', amount='10.00', **fields):
    return dict(id=identity, date=day, status='confirmed', description='Purchase',
                postings=[dict(account='Expenses:Test', amount=amount, commodity='INR'),
                          dict(account=CARD, amount='-' + amount, commodity='INR')], **fields)


class Fixture(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=Path(__file__).parent, prefix='.fixture-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.year = self.root / 'data/expenses/2026.json'
        dump(self.root / 'data/accounts.json', dict(commodities=['INR', 'USD'], accounts=[CARD, 'Expenses:Test', 'Assets:Bank:Savings Account', 'Equity:Opening Balances']))
        dump(self.year, [entry()])
        dump(self.root / 'data/forecast.json', [])
        dump(self.root / 'data/card-statements.json', [])

    def snapshot(self):
        return {str(p.relative_to(self.root)): p.read_bytes() for p in self.root.rglob('*') if p.is_file()}

    def cli(self, script, *args, text=None):
        return subprocess.run([sys.executable, str(APPLICATION_ROOT / 'scripts/ledger' / script), '--root', str(self.root), *args],
                              input=text, text=True, capture_output=True, check=True)


class ValidationTests(Fixture):
    def test_selected_store_smoke(self):
        documents = load()
        # Empty actuals and forecasts are valid in a separately selected ledger.
        self.assertIn(ROOT / 'data/accounts.json', documents)
        self.assertIn(ROOT / 'data/forecast.json', documents)

    def test_duplicate_id_across_namespaces(self):
        rule = entry(schedule=dict(every='month', **{'from': '2026-01-02'}))
        dump(self.root / 'data/forecast.json', [rule])
        with self.assertRaisesRegex(ValueError, 'Duplicate id'):
            load(self.root)

    def test_wrong_year(self):
        dump(self.year, [entry(day='2025-01-02')])
        with self.assertRaisesRegex(ValueError, 'does not belong'):
            load(self.root)

    def test_bad_filename_even_when_empty(self):
        self.year.rename(self.year.with_name('not-a-year.json'))
        with self.assertRaisesRegex(ValueError, 'calendar-year'):
            load(self.root)

    def test_dates_out_of_order(self):
        dump(self.year, [entry(day='2026-01-03'), entry(identity='0000000000000002')])
        with self.assertRaisesRegex(ValueError, 'out of order'):
            load(self.root)

    def test_unknown_and_removed_fields(self):
        for field in ['typo', 'printed-transaction-date']:
            with self.subTest(field=field):
                dump(self.year, [entry(**{field: '2026-01-02'})])
                with self.assertRaisesRegex(ValueError, 'Additional properties'):
                    load(self.root)
        value = entry()
        value['postings'][1]['date'] = '2026-01-03'
        dump(self.year, [value])
        with self.assertRaisesRegex(ValueError, 'Additional properties'):
            load(self.root)

    def test_non_decimal_amount(self):
        for invalid in ['1e3', 'NaN', '1,000.00', '1.2.3', 10.00]:
            with self.subTest(amount=invalid):
                value = entry()
                value['postings'][0]['amount'] = invalid
                dump(self.year, [value])
                with self.assertRaises(ValueError):
                    load(self.root)

    def test_duplicate_json_key(self):
        self.year.write_text('[{"id":"0000000000000001","id":"0000000000000002"}]')
        with self.assertRaisesRegex(ValueError, 'Duplicate JSON key'):
            load(self.root)

    def test_typed_fields_reject_invalid_values_on_actuals_and_forecasts(self):
        for field, bad in [('source-row', '1'), ('source-page', 0), ('statement-page', -1),
                           ('original-amount', '20 USD'), ('original-amount', {'amount': '1e3', 'commodity': 'USD'}),
                           ('variable-estimate', {'excluded': False, 'reason': 'reason'}),
                           ('variable-estimate', {'excluded': True, 'reason': 'reason\n'}),
                           ('original-amount', {'amount': '1.0\n', 'commodity': 'USD'}),
                           ('policy-term', {'years': 0}), ('trip-km', 'not supplied'), ('trip-km', '1.0\n'), ('fuel-filled-litres', 'NaN')]:
            for forecast in [False, True]:
                with self.subTest(field=field, forecast=forecast):
                    value = entry(**{field: bad})
                    if forecast:
                        value['schedule'] = {'every': 'month', 'from': value['date']}
                    dump(self.year, [] if forecast else [value])
                    dump(self.root / 'data/forecast.json', [value] if forecast else [])
                    with self.assertRaises(ValueError):
                        load(self.root)

    def test_canonical_round_trip(self):
        value = entry(**{'original-amount': {'commodity': 'USD', 'amount': '1.2300'}})
        text = canonical_text([value])
        self.assertEqual(json.loads(text), [value])
        self.assertEqual(canonical_text(json.loads(text)), text)
        self.assertIn('{"account": "Expenses:Test", "amount": "10.00", "commodity": "INR"}', text)

    def test_format_cli_sorts_stably_and_is_idempotent(self):
        self.year.write_text(json.dumps([entry(day='2026-01-03'), entry(identity='0000000000000002'), entry(identity='0000000000000003')]))
        self.cli('store.py', '--format')
        self.assertEqual([e['id'] for e in read_json(self.year)], ['0000000000000002', '0000000000000003', '0000000000000001'])
        before = self.snapshot()
        self.cli('store.py', '--format')
        self.assertEqual(self.snapshot(), before)


class GeneratorTests(unittest.TestCase):
    def test_card_member_and_typed_fields_golden(self):
        value = entry(**{'sources': ['Bills/card.pdf'], 'serial-number': '00123', 'source-page': 1,
                         'statement-page': 2, 'statement-end': '2026-01-28',
                         'original-amount': dict(amount='1.2300', commodity='USD'),
                         'fuel-filled-litres': '10.50', 'trip-km': '100.0',
                         'variable-estimate': dict(excluded=True, reason='all text, including commas'),
                         'policy-term': dict(years=1), 'notes': ['retained note']})
        self.assertEqual('\n'.join(render_entry(value)), '''2026-01-02 * Purchase
    ; id: 0000000000000001
    ; source: Bills/card.pdf
    ; serial-number: 00123
    ; source-page: 1
    ; statement-page: 2
    ; statement-end: 2026-01-28
    ; original amount: 1.2300 USD
    ; fuel-filled-litres: 10.50
    ; trip-km: 100.0
    ; variable-estimate: excluded, all text, including commas
    ; policy-term: 1 year
    ; retained note
    Expenses:Test                            10.00 INR
    Liabilities:Credit Cards:Rewards Card    -10.00 INR
''')

    def test_bank_row_golden(self):
        value = entry(**{'source-row': 42, 'reference': '000123', 'order-id': '001', 'policy-term': {'years': 3}})
        value['postings'][1]['account'] = 'Assets:Bank:Savings Account'
        self.assertEqual('\n'.join(render_entry(value)), '''2026-01-02 * Purchase
    ; id: 0000000000000001
    ; source-row: 42
    ; reference: 000123
    ; order-id: 001
    ; policy-term: 3 years
    Expenses:Test                  10.00 INR
    Assets:Bank:Savings Account    -10.00 INR
''')

    def test_forecast_interval_cost_and_assertion_golden(self):
        value = entry(schedule={'every': 'month', 'interval': 3, 'from': '2026-01-02'})
        value['postings'] = [dict(account='Assets:Fund', amount='1.000', commodity='FUND50',
                                  cost=dict(amount='100', commodity='INR', per='total'),
                                  **{'assert': dict(amount='2.000', commodity='FUND50')}, notes=['posting note']),
                             dict(account='Assets:Bank:Savings Account', amount='-100.00', commodity='INR')]
        self.assertEqual('\n'.join(render_entry(value)), '''~ every 3 months from 2026-01-02
    ; id: 0000000000000001
    ; forecast item: Purchase
    Assets:Fund                    1.000 "FUND50" @@ 100 INR = 2.000 "FUND50"
    ; posting note
    Assets:Bank:Savings Account    -100.00 INR
''')
        value['postings'][0]['cost']['per'] = 'unit'
        self.assertIn(' @ 100 INR', '\n'.join(render_entry(value)))


class MembershipTests(Fixture):
    def setUp(self):
        super().setUp()
        opening = entry(day='2026-01-01')
        opening['postings'][0]['account'] = 'Equity:Opening Balances'
        member = entry(identity='0000000000000002', **{'statement-end': '2026-01-28', 'serial-number': '001', 'statement-page': 1, 'sources': ['Bills/card.pdf']})
        self.actuals = [opening, member]
        self.summary = dict(id='0000000000000003', account=CARD, start='2026-01-01', end='2026-01-28', source='Bills/card.pdf',
                            **{'previous-balance': '10.00', 'purchases': '10.00', 'credits': '0.00', 'total-due': '20.00', 'rows': 1})

    def check(self):
        dump(self.year, self.actuals)
        dump(self.root / 'data/card-statements.json', [self.summary])
        return check_card_statements(load(self.root), self.root)

    def test_missing_row(self):
        self.actuals.pop()
        with self.assertRaisesRegex(ValueError, 'row count 0 != 1'):
            self.check()

    def test_extra_entry(self):
        extra = deepcopy(self.actuals[1]); extra.update(id='0000000000000004', **{'serial-number': '002'})
        self.actuals.append(extra)
        with self.assertRaisesRegex(ValueError, 'row count 2 != 1'):
            self.check()

    def test_wrong_amount(self):
        self.actuals[1]['postings'][1]['amount'] = '-11.00'
        with self.assertRaisesRegex(ValueError, 'purchases'):
            self.check()

    def test_duplicate_serial(self):
        extra = deepcopy(self.actuals[1]); extra['id'] = '0000000000000004'
        self.actuals.append(extra)
        with self.assertRaisesRegex(ValueError, 'Duplicate serial'):
            self.check()

    def test_broken_chain(self):
        self.summary['previous-balance'] = '11.00'
        with self.assertRaisesRegex(ValueError, 'broken statement chain'):
            self.check()

    def test_unknown_statement_end(self):
        self.actuals[1]['statement-end'] = '2026-02-28'
        with self.assertRaisesRegex(ValueError, 'unknown statement-end'):
            self.check()

    def test_missing_pdf(self):
        self.actuals[1]['sources'] = ['Bills/receipt.pdf']
        with self.assertRaisesRegex(ValueError, 'missing its statement PDF'):
            self.check()

    def test_missing_statement_page(self):
        del self.actuals[1]['statement-page']
        with self.assertRaisesRegex(ValueError, 'missing statement-page'):
            self.check()

    def test_unbilled_excludes_opening_assertions_and_future(self):
        unbilled = entry(identity='0000000000000004')
        self.actuals.append(unbilled)
        assertion = entry(identity='0000000000000005')
        assertion['postings'] = [dict(account=CARD, amount='0.00', commodity='INR', **{'assert': dict(amount='-30.00', commodity='INR')})]
        self.actuals.extend([assertion, entry(identity='0000000000000006', day='2026-02-01')])
        report = self.check()
        self.assertEqual([e['id'] for e in report['unbilled']], [unbilled['id']])
        self.assertEqual(report['statements'][0]['result'], 'PASS')


class AddEntryTests(Fixture):
    def new_entry(self, **fields):
        value = entry(**fields); del value['id']; return value

    def test_add_after_same_day_and_unique_id(self):
        dump(self.year, [entry(), entry(identity='0000000000000002'), entry(identity='0000000000000003', day='2026-01-03')])
        identity = add_entry(self.new_entry(), root=self.root)
        self.assertRegex(identity, r'^[0-9a-f]{16}$')
        values = read_json(self.year)
        self.assertEqual([v['id'] for v in values], ['0000000000000001', '0000000000000002', identity, '0000000000000003'])
        self.assertEqual({k:v for k,v in values[2].items() if k != 'id'}, self.new_entry())
        for path in store_paths(self.root):
            self.assertEqual(path.read_text(), canonical_text(read_json(path)))

    def test_new_year(self):
        identity = add_entry(self.new_entry(day='2027-01-01'), root=self.root)
        self.assertEqual(read_json(self.root / 'data/expenses/2027.json')[0]['id'], identity)
        load(self.root)

    def test_dry_run_and_invalid_input_never_write(self):
        before = self.snapshot()
        identity = add_entry(self.new_entry(), root=self.root, dry_run=True)
        self.assertRegex(identity, r'^[0-9a-f]{16}$')
        for invalid in [entry(), self.new_entry(day='invalid'), self.new_entry(typo=True), self.new_entry(schedule=dict(every='month', **{'from': '2026-01-02'}))]:
            with self.assertRaises(ValueError):
                add_entry(invalid, root=self.root)
        unknown = self.new_entry(); unknown['postings'][0]['account'] = 'Expenses:Unknown'
        with self.assertRaisesRegex(ValueError, 'Undeclared'):
            add_entry(unknown, root=self.root)
        with self.assertRaisesRegex(ValueError, 'evidence'):
            add_entry(self.new_entry(sources=['Bills/missing.pdf']), root=self.root)
        self.assertEqual(before, self.snapshot())

    def test_unbalanced_entry_never_writes(self):
        before = self.snapshot()
        value = self.new_entry()
        value['postings'][0]['amount'] = '11.00'
        with self.assertRaisesRegex(ValueError, 'strict hledger'):
            add_entry(value, root=self.root)
        self.assertEqual(self.snapshot(), before)

    def test_file_and_stdin_cli(self):
        file = self.root / 'entry.json'; file.write_text(json.dumps(self.new_entry()))
        identity = self.cli('add_entry.py', str(file), '--dry-run').stdout.strip()
        self.assertRegex(identity, r'^[0-9a-f]{16}$')
        identity = self.cli('add_entry.py', text=json.dumps(self.new_entry())).stdout.strip()
        self.assertEqual(read_json(self.year)[-1]['id'], identity)

    def test_id_collision_retries(self):
        used = {'0000000000000001'}
        with patch('store.secrets.token_hex', side_effect=['0000000000000001', '0000000000000002']):
            self.assertEqual(new_id(used), '0000000000000002')
        self.assertEqual(len(used), 2)


class ReconcileTests(Fixture):
    def test_serials_candidates_pending_and_unmatched(self):
        opening = entry(day='2026-01-01'); opening['postings'][0]['account'] = 'Equity:Opening Balances'
        one = entry(identity='0000000000000002', **{'serial-number': 'serial'})
        candidate = entry(identity='0000000000000003', day='2026-01-04')
        untouched = entry(identity='0000000000000004', day='2026-01-20')
        dump(self.year, [opening, one, candidate, untouched])
        row = dict(date='2026-01-02', serial='serial', description='Purchase', amount='10.00', direction='Debit', page=2)
        rows = [row, {**row, 'serial': 'candidate', 'date': '2026-01-04'}, {**row, 'serial': 'new', 'amount': '999.00'}]
        before = self.snapshot()
        report = reconcile(load(self.root), rows, end='2026-01-28', start='2026-01-01', root=self.root)
        self.assertEqual(report['serialMatches'][0]['entryId'], one['id'])
        self.assertEqual(report['serialMatches'][0]['fields']['statement-page'], 2)
        self.assertTrue(report['candidateRows'][0]['uniqueMatch'])
        self.assertEqual(report['newRows'][0]['proposedEntry']['status'], 'pending')
        self.assertEqual(report['newRows'][0]['proposedEntry']['description'], row['description'])
        self.assertEqual([r['entryId'] for r in report['unmatchedUnbilled']], [untouched['id']])
        self.assertIn('Already members: 0', text_report(report))
        self.assertEqual(self.snapshot(), before)
        file = self.root / 'rows.json'; file.write_text(json.dumps(rows))
        cli = self.cli('reconcile_statement.py', '--rows', str(file), '--start', '2026-01-01', '--end', '2026-01-28', '--json')
        self.assertEqual(json.loads(cli.stdout), report)

    def test_ambiguous_candidates_and_serial_conflict_are_not_silently_applied(self):
        opening = entry(day='2026-01-01'); opening['postings'][0]['account'] = 'Equity:Opening Balances'
        first = entry(identity='0000000000000002', **{'serial-number': 'old', 'statement-end': '2025-12-28'})
        second = entry(identity='0000000000000003')
        dump(self.year, [opening, first, second])
        row = dict(date='2026-01-02', serial='new1', description='Purchase', amount='10.00', direction='Debit', page=1)
        report = reconcile(load(self.root), [row, {**row, 'serial': 'new2'}, {**row, 'serial': 'old', 'amount': '11.00'}], end='2026-01-28', start='2026-01-01', root=self.root)
        self.assertTrue(all(not r['uniqueMatch'] for r in report['candidateRows']))
        self.assertEqual(len(report['warnings']), 2)
        self.assertEqual(report['serialMatches'][0]['existingStatementEnd'], '2025-12-28')

    def test_general_extract_and_invalid_rows(self):
        row = dict(date='2026-01-02', serial='001', description='Purchase', amount='10.00', direction='Debit', page=1)
        for bad in [{**row, 'amount': 10}, {**row, 'direction': 'Unknown'}, {**row, 'page': 0}]:
            with self.assertRaises(ValueError):
                validate_rows([bad])
        with self.assertRaises(ValueError):
            validate_rows([row, row])


if __name__ == '__main__':
    unittest.main()
