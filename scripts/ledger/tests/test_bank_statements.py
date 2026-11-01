"""Balance-style membership checks remain active in every verified ledger write."""
from copy import deepcopy
from test_ledger import Fixture
from store import load, dump
from check_store import check_card_statements
from reconcile_bank import bank_plan

BANK = 'Assets:Bank:Savings Account'


class BankMembershipTests(Fixture):
    def setUp(self):
        super().setUp()
        self.rows = [dict(id='1'*16,date='2026-01-01',status='confirmed',description='Opening',postings=[dict(account=BANK,amount='100.00',commodity='INR'),dict(account='Equity:Opening Balances',amount='-100.00',commodity='INR')]),
                     dict(id='2'*16,date='2026-01-02',status='confirmed',description='Fable Shop',sources=['Bills/bank.csv'],reference='EX-001',postings=[dict(account='Expenses:Test',amount='10.00',commodity='INR'),dict(account=BANK,amount='-10.00',commodity='INR')]),
                     dict(id='3'*16,date='2026-01-28',status='confirmed',description='Closing',sources=['Bills/bank.csv'],postings=[dict(account=BANK,amount='0.00',commodity='INR',**{'assert':dict(amount='90.00',commodity='INR')})])]
        self.member = dict(date='2026-01-02',serial='row-2',description='Fable Shop',amount='10.00',direction='Debit',reference='EX-001',balance='90.00',citation=dict(sheet='CSV',row=2),disposition='recorded',**{'entry-id':'2'*16})
        self.summary = dict(id='4'*16,kind='bank',account=BANK,start='2026-01-01',end='2026-01-28',source='Bills/bank.csv',reader='generic-csv',rows=1,members=[self.member],**{'opening-balance':'100.00','closing-balance':'90.00','debits':'10.00','credits':'0.00','assertion-id':'3'*16})

    def documents(self):
        dump(self.year,self.rows);dump(self.root/'data/card-statements.json',[self.summary])
        return load(self.root)

    def test_membership_totals_and_assertion(self):
        result=check_card_statements(self.documents(),self.root)
        self.assertEqual(result['statements'][0]['closingBalance'],'90.00')

    def test_bad_totals_missing_member_assertion_and_running_balance(self):
        baseline=deepcopy(self.summary)
        for change,reason in [({'debits':'11.00'},'totals'),({'assertion-id':'9'*16},'assertion'),({'closing-balance':'91.00'},'closing balance')]:
            self.summary={**deepcopy(baseline),**change}
            with self.subTest(change=change),self.assertRaises(ValueError):check_card_statements(self.documents(),self.root)
        self.summary=deepcopy(baseline);self.summary['members'][0]['balance']='91.00'
        with self.assertRaisesRegex(ValueError,'running balance'):check_card_statements(self.documents(),self.root)
        self.summary=deepcopy(baseline);self.summary['members'][0]['entry-id']='9'*16
        with self.assertRaisesRegex(ValueError,'Missing'):check_card_statements(self.documents(),self.root)

    def test_same_period_and_forged_overlap_membership_are_rejected(self):
        documents=self.documents()
        second=deepcopy(self.summary);second.update(id='5'*16,end='2026-02-28')
        second['members'][0]['disposition']='already-in-ledger'
        second['members'][0]['reference']='OTHER'
        documents[self.root/'data/card-statements.json'].append(second)
        with self.assertRaisesRegex(ValueError,'earlier membership'):check_card_statements(documents,self.root)

    def test_bank_matching_reserves_reference_before_similar_amount(self):
        documents=self.documents();documents[self.root/'data/card-statements.json']=[]
        row=dict(date='2026-01-02',serial='row-2',description='Fable Shop',amount='10.00',direction='Debit',citation=dict(sheet='CSV',row=2))
        rows=[row,{**row,'serial':'row-3','reference':'EX-001'}]
        statement=dict(account=BANK,periodEnd='2026-01-28',rows=rows)
        planned=bank_plan(documents,statement,self.root)
        self.assertEqual(planned['items'][1]['rule'],'serial')
        self.assertEqual(planned['items'][1]['entry_id'],'2'*16)
        self.assertIsNone(planned['items'][0]['entry_id'])

    def test_later_history_uses_imported_after_start_hook(self):
        documents=self.documents();documents[self.root/'data/card-statements.json']=[]
        row=dict(date='2026-01-15',serial='row-2',description='Other printed name',amount='10.00',direction='Debit',citation=dict(sheet='CSV',row=2))
        planned=bank_plan(documents,dict(account=BANK,periodEnd='2026-01-28',rows=[row]),self.root,start_date='2026-01-01')
        self.assertEqual(planned['items'][0]['duplicate_reason'],'imported-after-start')
        self.assertTrue(planned['items'][0]['needs_user'])

    def test_read_only_bank_reconciliation_uses_general_matching(self):
        from reconcile_statement import reconcile,text_report
        documents=self.documents();documents[self.root/'data/card-statements.json']=[]
        row={**self.member,'page':1}
        documents[self.root/'data/accounts.json']['accounts'][BANK]['statement']=dict(reader='generic-csv',identifier='BANK-ONE')
        report=reconcile(documents,[row],start='2026-01-01',end='2026-01-28',account=BANK,identifier='BANK-ONE',root=self.root)
        self.assertEqual(report['kind'],'bank')
        self.assertEqual(report['items'][0]['entry_id'],'2'*16)
        self.assertIn('Read-only proposals',text_report(report))

    def test_different_unicode_merchants_do_not_share_an_empty_precedent_key(self):
        documents=self.documents();documents[self.root/'data/card-statements.json']=[]
        documents[self.year][1]['description']='旧商店'
        row=dict(date='2026-01-15',serial='row-2',description='新书店',amount='15.00',direction='Debit',citation=dict(sheet='CSV',row=2))
        report=bank_plan(documents,dict(account=BANK,periodEnd='2026-01-28',rows=[row]),self.root)
        self.assertEqual(report['items'][0]['rule'],'needs_you')
        self.assertIsNone(report['items'][0]['postings'])
