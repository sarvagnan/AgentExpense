"""Statement selection and membership use metadata, including unconventional paths."""
from copy import deepcopy
from test_ledger import Fixture, CARD
from store import load
from reconcile_statement import select_account, reconciliation_path, extract_rows


class SelectionTests(Fixture):
    def test_reader_identifier_selection_and_ambiguity(self):
        documents = load(self.root)
        accounts = documents[self.root/'data/accounts.json']['accounts']
        accounts.pop(CARD)
        first, second = 'Liabilities:Plastic:Main', 'Borrowed:Travel'
        accounts[first] = dict(kind='card',currency='INR',statement=dict(reader='example-card-pdf'))
        self.assertEqual(select_account(documents,self.root,'5678'),first)
        accounts[second] = deepcopy(accounts[first])
        with self.assertRaisesRegex(ValueError,'Set statement.identifier on the card accounts'):
            select_account(documents,self.root,'5678')
        accounts[first]['statement']['identifier'] = '1234'
        accounts[second]['statement']['identifier'] = '5678'
        self.assertEqual(select_account(documents,self.root,'5678'),second)
        with self.assertRaises(ValueError): select_account(documents,self.root,'9999')
        with self.assertRaises(ValueError): select_account(documents,self.root,'5678',first)
        with self.assertRaises(ValueError): select_account(documents,self.root)
        accounts[first]['statement']['reader'] = 'another-reader'
        self.assertEqual(select_account(documents,self.root),second)
        accounts[second]['kind'] = 'bank'
        with self.assertRaises(ValueError): select_account(documents,self.root,'5678')

    def test_explicit_assignment_accepts_a_declared_account_without_guessing_an_identifier(self):
        documents=load(self.root)
        accounts=documents[self.root/'data/accounts.json']['accounts']
        accounts['Assets:Travel:Willow']=dict(kind='bank',currency='EUR')
        self.assertEqual(select_account(documents,self.root,account='Assets:Travel:Willow'),'Assets:Travel:Willow')
        with self.assertRaises(ValueError):
            select_account(documents,self.root,'5678','Assets:Travel:Willow')
        with self.assertRaises(ValueError):
            select_account(documents,self.root,account='Expenses:Food')

    def test_reconciliation_paths_use_label_and_disambiguate_same_label(self):
        first = reconciliation_path(self.root,'2026-01-28','Plastic:One',{'label':'Travel Card'})
        second = reconciliation_path(self.root,'2026-01-28','Plastic:Two',{'label':'Travel Card'})
        self.assertIn('Travel-Card',first.name)
        self.assertNotEqual(first,second)
        self.assertEqual(first.parent,self.root/'data/statements')
        first.parent.mkdir(parents=True,exist_ok=True)
        first.write_text('{}')
        self.assertEqual(reconciliation_path(self.root,'2026-01-28','Plastic:One',{'label':'Renamed'}),first)

    def test_labelled_reconciliation_is_read_without_an_account_argument(self):
        import json
        documents = load(self.root)
        path = reconciliation_path(self.root,'2026-01-28',CARD,documents[self.root/'data/accounts.json']['accounts'][CARD])
        path.parent.mkdir(parents=True,exist_ok=True)
        path.write_text(json.dumps(dict(account=CARD,periodStart='2026-01-01',source='Bills/example.pdf',rows=[])))
        rows, metadata = extract_rows(self.root,'2026-01-28')
        self.assertEqual(rows,[])
        self.assertEqual(metadata['account'],CARD)
