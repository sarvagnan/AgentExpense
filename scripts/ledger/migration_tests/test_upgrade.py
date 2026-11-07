"""Format gates, metadata, migration safety and private-safe comparisons."""
from argparse import Namespace
from contextlib import redirect_stdout
from copy import deepcopy
import io
import json
import os
from pathlib import Path
import subprocess
import sys
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tests'))
from test_ledger import Fixture, CARD
import format1
from store import dump, load, read_json
from ledger_manifest import CURRENT_FORMAT, app_version
from ledger_manifest import read_manifest
from compare_outputs import compare, snapshot
from upgrade_ledger import upgrade, Repository


def entry(identity='0000000000000001', day='2026-01-02', amount='10.00', **fields):
    """A format 1 entry: the shape these migrations start from."""
    return dict(id=identity, date=day, status='confirmed', description='Purchase',
                postings=[dict(account='Expenses:Test', amount=amount, commodity='INR'),
                          dict(account=CARD, amount='-' + amount, commodity='INR')], **fields)


def old_dump(path, value):
    """Write a format 0 or 1 document in format 1's layout."""
    format1.dump(path, value)


def converted(value):
    """What the 1 → 2 migration makes of a format 1 entry in a ledger without extensions."""
    return format1.entry(value, ())


def options(**changes):
    return Namespace(dry_run=False, name='Fictional owner', default_currency=None, locale=None,
                     start_date=None, review_storage_key=None, accounts=None, **changes)


class Repository0(Fixture):
    """A format 0 ledger in its own jj repository."""
    def setUp(self):
        super().setUp()
        (self.root / 'ledger.json').unlink()
        path = self.root / 'data/accounts.json'
        data = read_json(path);data['accounts'] = list(data['accounts']);path.write_text(json.dumps(data) + '\n')
        # Format 0 holds format 1 entries.
        old_dump(self.year, [entry()])
        old_dump(self.root / 'data/forecast.json', [])
        old_dump(self.root / 'data/card-statements.json', [])
        (self.root / '.gitignore').write_text('/.lock\n/expense_ledger.ledger\n')
        self.config = self.root.parent / (self.root.name + '-jj.toml')
        self.config.write_text('[user]\nname="Offline migration test"\nemail="test@example.invalid"\n')
        self.addCleanup(self.config.unlink)
        self.environment = patch.dict(os.environ, {'JJ_CONFIG': str(self.config), 'GIT_CONFIG_GLOBAL': '/dev/null', 'GIT_CONFIG_NOSYSTEM': '1', 'GIT_AUTHOR_NAME':'Offline test','GIT_AUTHOR_EMAIL':'test@example.invalid','GIT_COMMITTER_NAME':'Offline test','GIT_COMMITTER_EMAIL':'test@example.invalid'})
        self.environment.start();self.addCleanup(self.environment.stop)
        self.run_command('jj', 'git', 'init', '--colocate', str(self.root))
        self.run_command('jj', 'commit', '-m', 'Format zero fixture')

    def run_command(self, *args):
        return subprocess.check_output(args, cwd=self.root, text=True, stderr=subprocess.PIPE).strip()

    def source_bytes(self):
        return {str(p.relative_to(self.root)):p.read_bytes() for p in self.root.rglob('*') if p.is_file() and not set(p.relative_to(self.root).parts)&{'.git','.jj'} and p.name!='.lock'}

    def run_upgrade(self, **changes):
        args = options();vars(args).update(changes)
        with redirect_stdout(io.StringIO()): upgrade(self.root, args)



class MigrationTests(Repository0):
    """Format 0 → current: the whole chain."""

    def test_success_preserves_every_output_and_second_run_is_noop(self):
        before = snapshot(self.root)
        self.run_upgrade(locale='en-IN', start_date='2026-01-01', review_storage_key='legacy-key')
        self.assertTrue(compare(before, snapshot(self.root), ignores=('meta.ledger',), emit=False))
        manifest = read_manifest(self.root)
        self.assertEqual(list(read_json(self.root / 'ledger.json')),
                         ['format', 'id', 'name', 'defaultCurrency', 'locale', 'startDate', 'modules', 'extensions', 'review'])
        self.assertEqual(list(read_json(self.root / 'ledger.json')['modules']),
                         ['investments', 'history', 'weeklyReview'])
        self.assertEqual(manifest['defaultCurrency'], 'INR')
        self.assertEqual(manifest['review']['storageKey'], 'legacy-key')
        repo = Repository(self.root);repo.require_clean()
        checkpoint = repo.checkpoint()
        self.run_upgrade();self.assertEqual(repo.checkpoint()[0], checkpoint[0])
        log = self.run_command('jj','log','-r','::@-','--no-graph','-T','description')
        for step in range(CURRENT_FORMAT):
            self.assertIn(f'Upgrade ledger format {step} → {step + 1} (app v{app_version()})', log)

    def test_legacy_card_summary_membership_and_all_outputs_survive_unchanged(self):
        from test_ledger import CARD
        from generate_ledger import render
        from check_store import check_card_statements
        opening = entry(day='2026-01-01')
        opening['postings'][0]['account'] = 'Equity:Opening Balances'
        member = entry(identity='0000000000000002', **{'statement-end':'2026-01-28','serial-number':'EX-001','statement-page':1,'sources':['Bills/example.pdf']})
        summary = dict(id='0000000000000003',account=CARD,start='2026-01-01',end='2026-01-28',source='Bills/example.pdf',rows=1,**{'previous-balance':'10.00','purchases':'10.00','credits':'0.00','total-due':'20.00'})
        old_dump(self.year,[opening,member]);old_dump(self.root/'data/card-statements.json',[summary])
        (self.root/'Bills').mkdir();(self.root/'Bills/example.pdf').write_bytes(b'%PDF-1.4 Fictional evidence')
        self.run_command('jj','commit','-m','Fictional legacy card membership')
        before = snapshot(self.root)
        self.run_upgrade()
        self.assertEqual(read_json(self.root/'data/card-statements.json'),[format1.statement(summary)])
        self.assertEqual(load(self.root)[self.year],[converted(opening),converted(member)])
        self.assertTrue(compare(before,snapshot(self.root),ignores=('meta.ledger',),emit=False))
        self.assertEqual(check_card_statements(load(self.root),self.root)['statements'][0]['rows'],1)

    def test_dry_run_writes_nothing(self):
        before = self.source_bytes()
        self.run_upgrade(dry_run=True)
        self.assertEqual(before, self.source_bytes())
        self.assertFalse((self.root / '.lock').exists())

    def test_assertion_only_investment_units_preserve_every_output(self):
        path = self.root / 'data/accounts.json'
        declarations = read_json(path)
        account = 'Assets:Investments:Demo Units'
        declarations['accounts'].append(account)
        declarations['commodities'].append('LOT')
        path.write_text(json.dumps(declarations) + '\n')
        purchase = entry(identity='0000000000000002', day='2026-01-03')
        purchase['postings'] = [
            dict(account=account, amount='2.000', commodity='LOT',
                 cost=dict(amount='10.00', commodity='INR', per='total')),
            dict(account='Assets:Bank:Savings Account', amount='-10.00', commodity='INR'),
        ]
        assertion = entry(identity='0000000000000003', day='2026-01-04')
        assertion['postings'] = [dict(account=account, amount='0.000', commodity='LOT',
                                     **{'assert': dict(amount='2.000', commodity='LOT')})]
        old_dump(self.year, [entry(), purchase, assertion])
        prices = self.root / 'data/prices/mutual-funds.prices'
        prices.parent.mkdir()
        prices.write_text('P 2026-01-03 LOT 5 INR\n')
        self.run_command('jj', 'commit', '-m', 'Fictional holdings check')
        before = snapshot(self.root)
        self.run_upgrade()
        after = snapshot(self.root)
        self.assertTrue(compare(before, after, ignores=('meta.ledger',), emit=False))
        self.assertEqual(load(self.root)[self.year][-1], converted(assertion))
        self.assertNotIn(assertion['id'], [row['id'] for row in after['dashboard/dashboard.json']['transactions']])

    def test_person_at_root_payable_and_receivable_preserve_every_output(self):
        from check_store import check
        from generate_ledger import render
        payable = 'Dev Sen:Accounts Payable:Asha'
        receivable = 'Dev Sen:Balances:Accounts Receivable:Asha'
        path = self.root / 'data/accounts.json'
        declarations = read_json(path)
        declarations['accounts'].extend([payable, receivable])
        path.write_text(json.dumps(declarations) + '\n')
        row = entry()
        row['postings'] = [dict(account=receivable, amount='10.00', commodity='USD'),
                           dict(account=payable, amount='-10.00', commodity='USD')]
        old_dump(self.root / 'data/expenses/2026.json', [row])
        self.run_command('jj', 'commit', '-m', 'Fictional person accounts')
        before = snapshot(self.root)
        self.run_upgrade()
        self.assertTrue(compare(before, snapshot(self.root), ignores=('meta.ledger',), emit=False))
        documents = load(self.root)
        self.assertEqual(list(documents[path]['accounts']), declarations['accounts'])
        for name in [payable, receivable]:
            self.assertEqual(documents[path]['accounts'][name], dict(kind='person', currency='USD', label=None, aliases=[], statement=None))
        self.assertEqual(read_manifest(self.root)['defaultCurrency'], 'USD')
        legacy = {**documents, path: declarations}
        self.assertEqual(render(legacy, self.root), render(documents, self.root))
        with redirect_stdout(io.StringIO()):
            check(self.root)

    def test_manual_prices_without_mapping_survive_format_zero_migration(self):
        path = self.root / 'data/prices/mutual-funds.prices'
        path.parent.mkdir()
        path.write_text('P 2026-01-02 USD 80 INR\n')
        self.assertFalse((path.parent / 'fund-sources.json').exists())
        self.run_command('jj', 'commit', '-m', 'Fictional manual prices')
        before = snapshot(self.root)
        self.assertIn('include data/prices/mutual-funds.prices', before['journal'])
        expected_prices = [dict(commodity='USD', currency='INR', dates=['2026-01-02'], navs=[80])]
        self.assertEqual(before['dashboard/dashboard.json']['prices'], expected_prices)
        self.run_upgrade()
        self.assertIsNone(read_manifest(self.root)['modules']['investments']['prices'])
        after = snapshot(self.root)
        self.assertTrue(compare(before, after, ignores=('meta.ledger',), emit=False))
        self.assertEqual(after['dashboard/dashboard.json']['prices'], expected_prices)
        self.assertIn('include data/prices/mutual-funds.prices', after['journal'])
        self.assertEqual(path.read_text(), 'P 2026-01-02 USD 80 INR\n')

    def test_uninferable_kind_and_override(self):
        path = self.root / 'data/accounts.json';data=read_json(path);data['accounts'].append('Assets:Other:Test');path.write_text(json.dumps(data)+'\n')
        self.run_command('jj','commit','-m','Unknown kind fixture')
        with self.assertRaisesRegex(ValueError, 'Supply --accounts PATH'): self.run_upgrade()
        override = self.root.parent/(self.root.name+'-overrides.json')
        override.write_text(json.dumps({'Assets:Other:Test':dict(kind='wallet',currency='USD',label='Test wallet',aliases=['test wallet'])}))
        self.addCleanup(override.unlink)
        self.run_upgrade(accounts=override)
        self.assertEqual(load(self.root)[path]['accounts']['Assets:Other:Test']['kind'],'wallet')

    def test_dirty_working_copy_and_newer_format_refused(self):
        path=self.root/'data/forecast.json';path.write_text('[ ]\n')
        with self.assertRaisesRegex(ValueError,'clean working copy'): self.run_upgrade()
        dump(self.root/'ledger.json',dict(format=CURRENT_FORMAT+1))
        with self.assertRaisesRegex(ValueError,'newer AgentExpense'): self.run_upgrade()

    def test_expense_category_overrides_preserve_journal_and_reports(self):
        path = self.root / 'data/accounts.json'
        declarations = read_json(path)
        declarations['accounts'].extend(['Expenses', 'Income:Salary'])
        path.write_text(json.dumps(declarations) + '\n')
        self.run_command('jj', 'commit', '-m', 'Fictional parent and income declarations')
        override = self.root.parent / (self.root.name + '-categories.json')
        self.addCleanup(override.unlink)
        for account, metadata in [('Income:Salary', dict(category='Income')),
                                  ('Equity:Opening Balances', dict(category='Opening')),
                                  ('Expenses:Test', dict(category='')),
                                  ('Expenses:Test', 'Groceries')]:
            override.write_text(json.dumps({account: metadata}))
            with self.subTest(account=account, metadata=metadata), self.assertRaises(ValueError):
                self.run_upgrade(accounts=override, dry_run=True)
        before = snapshot(self.root)
        override.write_text(json.dumps({'Expenses': dict(category='Everyday'),
                                        'Expenses:Test': dict(category='Groceries')}))
        self.run_upgrade(accounts=override)
        self.assertEqual(load(self.root)[self.root / 'data/accounts.json']['accounts']['Expenses:Test'],
                         dict(category='Groceries'))
        after = snapshot(self.root)
        for key in ['journal', 'print', 'bal', 'bal-cost', 'reg']:
            self.assertEqual(before[key], after[key], key)
        self.assertEqual(after['dashboard/dashboard.json']['transactions'][0]['category'], 'Groceries')

    def test_failure_restores_checkpoint_and_ignored_journal(self):
        (self.root/'expense_ledger.ledger').write_text('original ignored journal\n')
        before=self.source_bytes();repo=Repository(self.root);checkpoint=repo.checkpoint()
        with patch('upgrade_ledger.verify', side_effect=ValueError('Injected verification failure for Fictional Merchant')):
            with self.assertRaises(ValueError) as caught: self.run_upgrade()
        # After apply, the message is counts-only: never the check's details.
        self.assertIn('Verification failed after migrating; the checkpoint is restored', str(caught.exception))
        self.assertNotIn('Fictional Merchant', str(caught.exception))
        self.assertEqual(before,self.source_bytes());self.assertEqual(repo.checkpoint()[0],checkpoint[0]);repo.require_clean()
        with patch.dict('os.environ',{'EXPENSES_UPGRADE_DEBUG':'1'}),patch('upgrade_ledger.verify', side_effect=ValueError('Injected verification failure')):
            with self.assertRaisesRegex(ValueError,'Injected'): self.run_upgrade()
        self.assertEqual(before,self.source_bytes())
        self.assertTrue((self.root/'.lock').exists())

    def test_comparison_failure_rolls_back(self):
        before=self.source_bytes()
        with patch('upgrade_ledger.compare',return_value=False):
            with self.assertRaisesRegex(ValueError,'outputs changed'): self.run_upgrade()
        self.assertEqual(before,self.source_bytes())

    def test_plain_git_repository_supported(self):
        import shutil
        shutil.rmtree(self.root/'.jj')
        # Colocated git HEAD still points to the imported checkpoint.
        self.run_upgrade()
        self.assertEqual(read_manifest(self.root)['format'],CURRENT_FORMAT)
        self.assertEqual(self.run_command('git','status','--porcelain'),'')

    def test_multiple_account_commodities_refused(self):
        path=self.root/'data/expenses/2026.json';rows=read_json(path)
        extra=entry(identity='0000000000000002');extra['postings'][1]['commodity']='USD';rows.append(extra);old_dump(path,rows)
        self.run_command('jj','commit','-m','Multiple commodities fixture')
        with self.assertRaisesRegex(ValueError,'Multiple posting commodities'): self.run_upgrade()


class FormatOneTests(Repository0):
    """Format 1 → 2: the typed store, extensions, unit costs and repeated labels."""

    def setUp(self):
        super().setUp()
        accounts = {CARD: dict(kind='card', currency='INR', label='Test Card', statement=dict(reader='example-card-pdf')),
                    'Expenses:Test': {}, 'Assets:Bank:Savings Account': dict(kind='bank', currency='INR'), 'Equity:Opening Balances': {}}
        old_dump(self.root / 'ledger.json', dict(format=1, id='00000000-0000-4000-8000-000000000001', name='Offline fixture',
                                                defaultCurrency='INR', modules=dict(fuel=False)))
        old_dump(self.root / 'data/accounts.json', dict(commodities=['INR', 'USD'], accounts=accounts))
        self.run_command('jj', 'commit', '-m', 'Format one fixture')

    def with_entries(self, *rows, **manifest):
        if manifest:
            old_dump(self.root / 'ledger.json', {**read_json(self.root / 'ledger.json'), **manifest})
        old_dump(self.year, list(rows))
        self.run_command('jj', 'commit', '-m', 'Fictional format one entries')

    def upgraded(self, **changes):
        output = io.StringIO()
        args = options();vars(args).update(changes)
        with redirect_stdout(output):
            upgrade(self.root, args)
        return output.getvalue()

    def journal(self):
        from generate_ledger import render
        return render(load(self.root), self.root)

    def test_inexact_unit_cost_becomes_the_exact_total_and_is_counted(self):
        row = entry(amount='100.00')
        row['postings'][0] = dict(account='Expenses:Test', amount='3.000', commodity='USD', cost=dict(amount='33.333', commodity='INR', per='unit'))
        self.with_entries(row)
        output = self.upgraded()
        stored = load(self.root)[self.year][0]
        self.assertEqual(stored['postings'][0]['cost'], {'amount': '100.00', 'commodity': 'INR', 'per': 'total'})
        self.assertIn('unit costs converted to total costs: 1', output)
        self.assertIn('journal entries that change as declared (unit-cost): 1', output)
        self.assertIn('DECLARED bal-cost' if 'DECLARED bal-cost' in output else 'PASS bal-cost', output)
        self.assertIn('3.000 USD @@ 100.00 INR', self.journal())

    def test_unit_cost_that_balances_exactly_stays(self):
        row = entry(amount='100.00')
        row['postings'][0] = dict(account='Expenses:Test', amount='2.000', commodity='USD', cost=dict(amount='50.00', commodity='INR', per='unit'))
        self.with_entries(row)
        output = self.upgraded()
        self.assertEqual(load(self.root)[self.year][0]['postings'][0]['cost'], {'amount': '50.00', 'commodity': 'INR', 'per': 'unit'})
        self.assertIn('unit costs kept (they balance exactly): 1', output)
        self.assertIn('2.000 USD @ 50.00 INR', self.journal())

    def test_repeated_order_id_becomes_a_list_with_the_same_journal(self):
        self.with_entries(entry(**{'order-id': 'ORD-1', 'notes': ['order-id: ORD-2', 'Kept note']}))
        before = snapshot(self.root)
        output = self.upgraded()
        stored = load(self.root)[self.year][0]
        self.assertEqual(stored['references'], {'reference': None, 'order-id': ['ORD-1', 'ORD-2']})
        self.assertEqual(stored['notes'], ['Kept note'])
        self.assertIn('order-id repeats made into lists: 1', output)
        self.assertEqual(before['journal'], snapshot(self.root)['journal'])

    def several_unit_costs(self, cash):
        row = entry(amount=cash)
        rate = dict(amount='83.3333333333', commodity='INR', per='unit')
        row['postings'] = [dict(account='Expenses:Test', amount=units, commodity='USD', cost=dict(rate))
                           for units in ('1.00', '2.00', '3.00')] + [dict(account=CARD, amount='-' + cash, commodity='INR')]
        self.with_entries(row)

    def test_several_inexact_unit_costs_share_the_exact_total(self):
        self.several_unit_costs('500.00')
        output = self.upgraded()
        costs = [p['cost'] for p in load(self.root)[self.year][0]['postings'][:3]]
        self.assertEqual([c['amount'] for c in costs], ['83.33', '166.67', '250.00'])
        self.assertEqual({(c['commodity'], c['per']) for c in costs}, {('INR', 'total')})
        self.assertIn('unit costs converted to total costs: 3', output)
        self.assertIn('journal entries that change as declared (unit-cost): 1', output)

    def test_several_unit_costs_far_from_the_total_stop(self):
        self.several_unit_costs('510.00')
        before = self.source_bytes()
        with self.assertRaisesRegex(ValueError, r'1 unbalanced entries with several unit costs in one commodity'):
            self.upgraded()
        self.assertEqual(before, self.source_bytes())

    def test_another_repeated_label_stops_and_changes_nothing(self):
        self.with_entries(entry(reference='R-1', notes=['reference: R-2']))
        before = self.source_bytes()
        with self.assertRaisesRegex(ValueError, r'1 entries or rules repeating a label other than order-id'):
            self.upgraded()
        self.assertEqual(before, self.source_bytes())
        with self.assertRaisesRegex(ValueError, 'repeating a label'):
            self.upgraded(dry_run=True)

    def test_unbalanced_entry_stops_with_a_count_and_changes_nothing(self):
        row = entry()
        row['postings'][0]['amount'] = '10.001'
        self.with_entries(row)
        before = self.source_bytes()
        with self.assertRaisesRegex(ValueError, r'1 entries or rules that do not balance exactly'):
            self.upgraded()
        self.assertEqual(before, self.source_bytes())

    def test_policy_term_moves_into_a_ledger_extension(self):
        self.with_entries(entry(**{'policy-term': {'years': 5}}), entry(identity='0000000000000002'))
        output = self.upgraded()
        self.assertEqual(read_manifest(self.root)['extensions'], {'policy': {'ask': False}})
        self.assertEqual(read_json(self.root / 'extensions/policy.json'), format1.POLICY)
        rows = load(self.root)[self.year]
        self.assertEqual([row['extensions'] for row in rows], [{'policy': {'term-years': 5}}, {'policy': None}])
        self.assertIn('journal entries that change as declared (policy-term): 1', output)
        self.assertIn('    ; policy-term: 5\n', self.journal())
        self.assertNotIn('5 years', self.journal())

    def test_fuel_module_becomes_the_fuel_extension(self):
        (self.root / 'data/fuel-log.json').write_text('{"meta":{},"entries":[]}\n')
        self.with_entries(entry(**{'fuel-filled-litres': '8.00'}), modules=dict(fuel=True))
        before = snapshot(self.root)
        self.upgraded()
        manifest = read_manifest(self.root)
        self.assertEqual(manifest['extensions'], {'fuel': {'ask': True}})
        self.assertNotIn('fuel', manifest['modules'])
        self.assertEqual(load(self.root)[self.year][0]['extensions'], {'fuel': {'litres': '8.00', 'trip-km': None}})
        self.assertFalse((self.root / 'extensions').exists())
        after = snapshot(self.root)
        for key in ['journal', 'print', 'bal', 'bal-cost', 'reg']:
            self.assertEqual(before[key], after[key], key)

    def test_fuel_readings_without_the_fuel_module_stop_with_a_count(self):
        # The owner never turned fuel on: the migration doesn't list it for them, it stops and says why.
        self.with_entries(entry(**{'trip-km': '120'}), entry(identity='0000000000000002'))
        before = self.source_bytes()
        for dry_run in (True, False):
            with self.assertRaisesRegex(ValueError, r'1 entries with fuel readings in a ledger without the fuel module'):
                self.upgraded(dry_run=dry_run)
        self.assertEqual(before, self.source_bytes())
        self.assertFalse((self.root / 'extensions').exists())

    def test_empty_optional_strings_become_null_and_are_counted(self):
        row = entry(classification='', reference='', **{'order-id': '', 'serial-number': '', 'sources': ['', 'Bills/2026-01-02/r.pdf']})
        (self.root / 'Bills/2026-01-02').mkdir(parents=True); (self.root / 'Bills/2026-01-02/r.pdf').write_bytes(b'%PDF-1.4 Fictional')
        self.with_entries(row)
        output = self.upgraded()
        self.assertIn('empty values cleared: 5', output)
        stored = load(self.root)[self.year][0]
        self.assertEqual((stored['classification'], stored['references'], stored['statement'], stored['evidence']),
                         (None, None, None, ['Bills/2026-01-02/r.pdf']))

    def test_a_check_failure_names_the_document_kind_not_the_file(self):
        row = entry(); row['date'] = '2026-02-30'
        self.with_entries(row)
        before = self.source_bytes()
        with self.assertRaises(ValueError) as caught:
            self.upgraded(dry_run=True)
        self.assertIn('(entries)', str(caught.exception))
        self.assertNotIn('2026', str(caught.exception))
        self.assertEqual(before, self.source_bytes())

    def test_dry_run_prints_counts_only(self):
        row = entry(**{'order-id': 'ORD-77', 'sources': ['Bills/2026-01-02/receipt.pdf']});row['description'] = 'Fictional Merchant'
        self.with_entries(row)
        before = self.source_bytes()
        output = self.upgraded(dry_run=True)
        self.assertEqual(before, self.source_bytes())
        self.assertIn('entries: 1', output)
        for private in ('Fictional Merchant', 'ORD-77', 'Bills/', CARD, '2026-01-02', '10.00', 'Offline fixture'):
            self.assertNotIn(private, output)

    def test_rollback_restores_every_store_file(self):
        self.with_entries(entry(**{'policy-term': {'years': 2}}))
        (self.root / 'expense_ledger.ledger').write_text('original ignored journal\n')
        before = self.source_bytes()
        repo = Repository(self.root);checkpoint = repo.checkpoint()
        with patch('upgrade_ledger.verify', side_effect=ValueError('Injected verification failure')):
            with self.assertRaisesRegex(ValueError, 'Verification failed after migrating'):
                self.upgraded()
        self.assertEqual(before, self.source_bytes())
        self.assertEqual(repo.checkpoint()[0], checkpoint[0])
        self.assertFalse((self.root / 'extensions').exists())
        repo.require_clean()
