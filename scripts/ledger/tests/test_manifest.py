"""Format gates, metadata, migration safety and private-safe comparisons."""
from contextlib import redirect_stdout
from copy import deepcopy
import io
import json
from pathlib import Path
import subprocess
import sys

from test_ledger import Fixture, entry, rule, write
from account_metadata import complete
from store import canonical_text, dump, load, read_json
from ledger_manifest import CURRENT_FORMAT, read_manifest
from compare_outputs import compare, snapshot


class ManifestTests(Fixture):
    def test_manifest_format_canonicalizes_top_level_and_module_order(self):
        from check_store import check
        path = self.root / 'ledger.json'
        manifest = {**read_json(path), 'locale': 'en-IN', 'startDate': '2026-01-01',
                    'modules': dict(weeklyReview=False, history=dict(oldLedger=None),
                                    investments=dict(prices=None)),
                    'review': dict(storageKey=None)}
        path.write_text(json.dumps(dict(reversed(list(manifest.items())))))
        with self.assertRaisesRegex(ValueError, 'ledger.json is not canonical'):
            check(self.root)
        self.cli('store.py', '--format')
        result = read_json(path)
        self.assertEqual(list(result), ['format', 'id', 'name', 'defaultCurrency', 'locale', 'startDate', 'modules', 'extensions', 'review'])
        self.assertEqual(list(result['modules']), ['investments', 'history', 'weeklyReview'])
        from ledger_manifest import ordered_manifest
        self.assertEqual(path.read_text(), canonical_text(ordered_manifest(manifest), flat=False))
        with redirect_stdout(io.StringIO()):
            check(self.root)

    def test_nonstandard_roots_require_balance_sheet_metadata(self):
        from account_metadata import infer_kind, is_balance_sheet
        from check_store import check
        path = self.root / 'data/accounts.json'
        accounts = read_json(path)
        names = ['Dev Sen:Accounts Payable:Asha', 'Dev Sen:Balances:Accounts Receivable:Asha',
                 'Accounts Receivable:Asha', 'Custom', 'IncomeExtra:Test']
        for name in names:
            self.assertTrue(is_balance_sheet(name))
            accounts['accounts'][name] = complete(name, dict(kind='person', currency='USD'))
        for name in names[:3]:
            self.assertEqual(infer_kind(name), 'person')
        self.assertIsNone(infer_kind('Dev Sen:Accounts Payable Extra:Asha'))
        for name in ['Income', 'Expenses:Test', 'Equity:Opening Balances']:
            self.assertFalse(is_balance_sheet(name))
            accounts['accounts'][name] = complete(name, {})
        dump(path, accounts)
        with redirect_stdout(io.StringIO()):
            check(self.root)
        self.assertEqual(list(load(self.root)[path]['accounts']), list(accounts['accounts']))
        for name in names:
            changed = deepcopy(accounts)
            changed['accounts'][name] = {}
            write(path, changed)
            with self.subTest(name=name), self.assertRaises(ValueError):
                load(self.root)
        for name in ['', ':Custom', 'Custom:', 'Custom::Test', 'Custom\nTest', 'Custom:Test\n', 'Custom\rTest']:
            changed = deepcopy(accounts)
            changed['accounts'][name] = complete('Assets', dict(kind='person', currency='USD'))
            write(path, changed)
            with self.subTest(name=name), self.assertRaises(ValueError):
                load(self.root)

    def test_defaults_and_closed_nested_objects(self):
        result = read_manifest(self.root)
        self.assertEqual(result['modules'], dict(investments=dict(prices=None), history=dict(oldLedger=None), weeklyReview=False))
        self.assertEqual(result['extensions'], {})
        self.assertEqual(result['review'], dict(storageKey=None))
        original = read_json(self.root / 'ledger.json')
        for change in [dict(unexpected=True), dict(id='bad'), dict(defaultCurrency='bad unit'), dict(locale='invalid_tag'), dict(startDate='2026-02-30'),
                       dict(modules=dict(investments=dict(prices=None, surprise=True))), dict(modules=dict(fuel=True)), dict(review=dict(storageKey=1)),
                       dict(extensions={'fuel': {'ask': 'yes'}}), dict(extensions={'Fuel Log': {'ask': True}}), dict(extensions={'fuel': {}}),
                       dict(extensions={'fuel': {'ask': True, 'extra': 1}}), dict(extensions=None)]:
            with self.subTest(change=change):
                dump(self.root / 'ledger.json', {**original, **change})
                with self.assertRaises(ValueError): read_manifest(self.root)
        dump(self.root / 'ledger.json', {key: value for key, value in original.items() if key != 'extensions'})
        with self.assertRaisesRegex(ValueError, 'missing extensions'): read_manifest(self.root)

    def test_python_and_node_format_diagnostics(self):
        from project_paths import APPLICATION_ROOT
        path = self.root / 'ledger.json'
        module = (APPLICATION_ROOT / 'review-dashboard/scripts/ledger-manifest.mjs').as_uri()
        for version in [0, CURRENT_FORMAT - 1, CURRENT_FORMAT + 1]:
            if version == 0: path.unlink()
            else: dump(path, dict(format=version))
            with self.assertRaises(ValueError) as caught: load(self.root)
            node = subprocess.run(['node', '--input-type=module', '-e', f'import {{readManifest}} from {json.dumps(module)}; try {{await readManifest({json.dumps(str(self.root))})}} catch(e) {{console.log(e.message);process.exitCode=1}}'], text=True, capture_output=True)
            self.assertEqual(node.returncode, 1)
            self.assertEqual(node.stdout.strip(), str(caught.exception))

    def test_account_metadata_and_order(self):
        path = self.root / 'data/accounts.json'
        accounts = read_json(path)
        names = list(accounts['accounts'])
        dump(path, accounts)
        self.assertEqual(list(read_json(path)['accounts']), names)
        for metadata in [dict(kind='bank',currency='MISSING'), dict(kind='unknown',currency='INR'), dict(kind='card',currency='INR',aliases=['Mixed']),
                         dict(kind='cash',currency='INR',statement=dict(identifier='1234', reader=None, options=None)),
                         {key: value for key, value in complete(names[0], dict(kind='cash',currency='INR')).items() if key != 'label'}]:
            changed = deepcopy(accounts)
            changed['accounts'][names[0]] = complete(names[0], metadata) if 'label' in metadata or len(metadata) < 4 else metadata
            write(path, changed)
            with self.subTest(metadata=metadata), self.assertRaises(ValueError): load(self.root)
        changed = deepcopy(accounts);changed['accounts']['Expenses:Test'] = dict(label='No metadata')
        write(path, changed)
        with self.assertRaises(ValueError): load(self.root)

    def test_comparison_never_emits_contents_and_supports_ignores(self):
        one = {'journal':'private text\n','dashboard/dashboard.json':dict(meta=dict(generatedAt='a'), rows=[dict(secret='private value')])}
        two = {'journal':'different private text\n','dashboard/dashboard.json':dict(meta=dict(generatedAt='b'), rows=[dict(secret='other private value')])}
        output = io.StringIO()
        with redirect_stdout(output): self.assertFalse(compare(one, two))
        self.assertNotIn('private', output.getvalue())
        two['journal'] = one['journal']
        self.assertTrue(compare(one, two, ['rows.*.secret'], emit=False))

    def with_fuel(self):
        """This fixture with the fuel extension listed and a rule.

        Self-contained: server tests run this suite in staged copies without fixtures/.
        """
        dump(self.root / 'ledger.json', {**read_json(self.root / 'ledger.json'), 'extensions': {'fuel': {'ask': True}}})
        dump(self.year, [{**item, 'extensions': {'fuel': None}} for item in read_json(self.year)])
        dump(self.root / 'data/forecast.json', [rule(identity='0000000000000002', origin={'kind': 'owner', 'words': None, 'entry-ids': [], 'came-back': False})])
        (self.root / 'data/fuel-log.json').write_text('{"entries": []}\n')

    def test_builder_failures_name_the_underlying_error(self):
        self.with_fuel()
        self.assertIn('dashboard/dashboard.json', snapshot(self.root))
        (self.root / 'data/fuel-log.json').write_text('{"entries": [1]}\n')
        with self.assertRaises(ValueError) as caught:
            snapshot(self.root)
        self.assertEqual(str(caught.exception), 'Unable to generate dashboard output (copy-fuel-log): The fuel extension is listed; supply a valid data/fuel-log.json: entries must be a list of objects.')

    def test_fuel_log_failures_name_the_problem_without_contents(self):
        self.with_fuel()
        log = self.root / 'data/fuel-log.json'
        log.write_text('{"entries": [{"note": "fictional private words"}')
        with self.assertRaises(ValueError) as caught:
            snapshot(self.root)
        self.assertEqual(str(caught.exception), 'Unable to generate dashboard output (copy-fuel-log): The fuel extension is listed; supply a valid data/fuel-log.json: it is not valid JSON.')
        log.unlink()
        with self.assertRaises(ValueError) as caught:
            snapshot(self.root)
        self.assertEqual(str(caught.exception), 'Unable to generate dashboard output (copy-fuel-log): The fuel extension is listed; supply a valid data/fuel-log.json: the file is missing.')

    def test_builder_failure_report_is_one_line(self):
        from compare_outputs import BUILDER_FAILURE, builder_failure
        builders = ['copy-fuel-log', 'build-dashboard-data']
        line = BUILDER_FAILURE + json.dumps(dict(name='build-dashboard-data', message='Invalid  rule.origin.kind.'))
        self.assertEqual(builder_failure('warning from a builder\n' + line + '\n', builders),
                         'Unable to generate dashboard output (build-dashboard-data): Invalid rule.origin.kind.')
        self.assertEqual(builder_failure(BUILDER_FAILURE + json.dumps(dict(name='other', message='')), builders),
                         'Unable to generate dashboard output (builders).')
        self.assertEqual(builder_failure('Segmentation fault\n', builders),
                         'Unable to generate dashboard output (builders): Node exited before a builder reported.')

    def test_expense_display_category_validation(self):
        from check_store import check
        path = self.root / 'data/accounts.json'
        original = read_json(path)
        original['accounts']['Expenses'] = dict(category='Everyday')
        original['accounts']['Expenses:Test'] = dict(category='Groceries')
        dump(path, original)
        with redirect_stdout(io.StringIO()):
            check(self.root)
        for account, metadata in [
            *[('Expenses:Test', dict(category=value)) for value in ['', ' ', '\t', 'Groceries\n', 'Groceries\rOther', 42]],
            ('Income:Salary', dict(category='Income')),
            ('Equity:Opening Balances', dict(category='Opening')),
            ('Assets:Checking:Alder', dict(kind='bank', currency='INR', category='Groceries')),
            ('Expenses:Test', dict(category='Groceries', label='Store')),
        ]:
            changed = deepcopy(original)
            changed['accounts'][account] = metadata
            write(path, changed)
            with self.subTest(account=account, metadata=metadata), self.assertRaises(ValueError):
                with redirect_stdout(io.StringIO()):
                    check(self.root)

    def test_display_categories_flow_through_dashboard_forecast_and_patterns_without_financial_changes(self):
        from generate_ledger import render
        path = self.root / 'data/accounts.json'
        declarations = read_json(path)
        names = ['Expenses:Market:Produce', 'Expenses:Market:Dining:Corner Store',
                 'Expenses:Food:Other', 'Expenses:Transport:Other']
        for account in ['Expenses:Market', 'Expenses:Market:Dining', *names]:
            declarations['accounts'][account] = {'category': None}
        dump(path, declarations)
        rows = []
        for index, account in enumerate(names, 1):
            row = entry(identity=f'{index:016x}', amount=f'{index * 10}.00')
            row['postings'][0]['account'] = account
            rows.append(row)
        dump(self.year, rows)
        rules = [rule(identity=f'{index + 10:016x}', day=row['date'], amount=row['postings'][0]['amount'], postings=row['postings'])
                 for index, row in enumerate(rows)]
        dump(self.root / 'data/forecast.json', rules)
        journal = render(load(self.root), self.root)
        before = snapshot(self.root)
        declarations['accounts']['Expenses:Market'] = dict(category='Groceries')
        declarations['accounts']['Expenses:Market:Dining'] = dict(category='Meals')
        dump(path, declarations)
        after = snapshot(self.root)
        self.assertEqual(render(load(self.root), self.root), journal)
        for key in ['journal', 'print', 'bal', 'bal-cost', 'reg']:
            self.assertEqual(before[key], after[key], key)
        dashboard = after['dashboard/dashboard.json']
        expected = dict(zip(names, ['Groceries', 'Meals', 'Food & dining', 'Transport']))
        self.assertEqual({row['ledgerAccount']: row['category'] for row in dashboard['transactions']}, expected)
        self.assertEqual({row['category']: row['amount'] for row in dashboard['months'][0]['categories']},
                         {'Groceries': 10, 'Meals': 20, 'Food & dining': 30, 'Transport': 40})
        self.assertTrue(dashboard['forecast']['items'])
        for item in dashboard['forecast']['items']:
            self.assertEqual(item['category'], expected[item['account']])
        pattern = after['dashboard/add-patterns.json']['patterns']['dining']
        self.assertEqual(pattern['usualExpense'], names[1])
        self.assertEqual(pattern['usualCategory'], 'Meals')
