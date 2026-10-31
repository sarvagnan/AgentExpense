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
from test_ledger import Fixture, entry
from store import dump, load, read_json
from ledger_manifest import read_manifest
from compare_outputs import compare, snapshot
from upgrade_ledger import upgrade, Repository


def options(**changes):
    return Namespace(dry_run=False, name='Fictional owner', default_currency=None, locale=None,
                     start_date=None, review_storage_key=None, accounts=None, **changes)


class MigrationTests(Fixture):
    def setUp(self):
        super().setUp()
        (self.root / 'ledger.json').unlink()
        path = self.root / 'data/accounts.json'
        data = read_json(path);data['accounts'] = list(data['accounts']);path.write_text(json.dumps(data) + '\n')
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

    def test_success_preserves_every_output_and_second_run_is_noop(self):
        before = snapshot(self.root)
        self.run_upgrade(locale='en-IN', start_date='2026-01-01', review_storage_key='legacy-key')
        self.assertTrue(compare(before, snapshot(self.root), ignores=('meta.ledger',), emit=False))
        manifest = read_manifest(self.root)
        self.assertEqual(list(read_json(self.root / 'ledger.json')),
                         ['format', 'id', 'name', 'defaultCurrency', 'locale', 'startDate', 'modules', 'review'])
        self.assertEqual(list(read_json(self.root / 'ledger.json')['modules']),
                         ['fuel', 'investments', 'history', 'weeklyReview'])
        self.assertEqual(manifest['defaultCurrency'], 'INR')
        self.assertEqual(manifest['review']['storageKey'], 'legacy-key')
        repo = Repository(self.root);repo.require_clean()
        checkpoint = repo.checkpoint()
        self.run_upgrade();self.assertEqual(repo.checkpoint()[0], checkpoint[0])
        self.assertIn('Upgrade ledger format 0 → 1 (app v0.1.0)', self.run_command('jj','log','-r','@-','--no-graph','-T','description'))

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
        dump(self.year, [entry(), purchase, assertion])
        prices = self.root / 'data/prices/mutual-funds.prices'
        prices.parent.mkdir()
        prices.write_text('P 2026-01-03 LOT 5 INR\n')
        self.run_command('jj', 'commit', '-m', 'Fictional holdings check')
        before = snapshot(self.root)
        self.run_upgrade()
        after = snapshot(self.root)
        self.assertTrue(compare(before, after, ignores=('meta.ledger',), emit=False))
        self.assertEqual(load(self.root)[self.year][-1], assertion)
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
        dump(self.root / 'data/expenses/2026.json', [row])
        self.run_command('jj', 'commit', '-m', 'Fictional person accounts')
        before = snapshot(self.root)
        self.run_upgrade()
        self.assertTrue(compare(before, snapshot(self.root), ignores=('meta.ledger',), emit=False))
        documents = load(self.root)
        self.assertEqual(list(documents[path]['accounts']), declarations['accounts'])
        for name in [payable, receivable]:
            self.assertEqual(documents[path]['accounts'][name], dict(kind='person', currency='USD'))
        self.assertEqual(read_manifest(self.root)['defaultCurrency'], 'USD')
        legacy = {**documents, path: declarations}
        self.assertEqual(render(legacy, self.root), render(documents, self.root))
        with redirect_stdout(io.StringIO()):
            check(self.root)

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
        dump(self.root/'ledger.json',dict(format=2))
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
        with patch('upgrade_ledger.verify', side_effect=ValueError('Injected verification failure')):
            with self.assertRaisesRegex(ValueError,'Injected'): self.run_upgrade()
        self.assertEqual(before,self.source_bytes());self.assertEqual(repo.checkpoint()[0],checkpoint[0]);repo.require_clean()
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
        self.assertEqual(read_manifest(self.root)['format'],1)
        self.assertEqual(self.run_command('git','status','--porcelain'),'')

    def test_multiple_account_commodities_refused(self):
        path=self.root/'data/expenses/2026.json';rows=read_json(path)
        extra=entry(identity='0000000000000002');extra['postings'][1]['commodity']='USD';rows.append(extra);dump(path,rows)
        self.run_command('jj','commit','-m','Multiple commodities fixture')
        with self.assertRaisesRegex(ValueError,'Multiple posting commodities'): self.run_upgrade()
