"""Account currencies, opening-balance relabelling and exact native amounts."""
from test_ledger import Fixture, entry, CARD
from store import dump, load, read_json
from account_currency import currency_changes
from check_store import check_card_statements


class CurrencyTests(Fixture):
    def apply(self, code, account=None):
        changes = currency_changes(self.root, code, account)
        for path, value in changes.items():
            dump(path, value)
        return load(self.root)

    def test_wrong_currency_rejected_for_actuals_forecasts_and_assertions(self):
        for target in [self.year, self.root / 'data/forecast.json']:
            value = entry()
            value['postings'][1]['commodity'] = 'USD'
            if target.name == 'forecast.json':
                value['schedule'] = {'every': 'month', 'from': value['date']}
                dump(self.year, [])
            dump(target, [value])
            with self.assertRaisesRegex(ValueError, 'account currency INR, got USD'):
                load(self.root)
            dump(target, [])
        value = entry();value['postings'][1]['assert'] = dict(amount='-10.00', commodity='USD')
        dump(self.year, [value])
        with self.assertRaisesRegex(ValueError, 'assertion must use'):
            load(self.root)

    def test_opening_amounts_and_assertions_relabel_without_changing_numbers(self):
        value = entry();value['postings'][0]['account'] = 'Equity:Opening Balances'
        value['postings'][1]['assert'] = dict(amount='-10.00', commodity='INR')
        dump(self.year, [value])
        docs = self.apply('USD', CARD)
        actual = docs[self.year][0]
        self.assertEqual([p['commodity'] for p in actual['postings']], ['USD', 'USD'])
        self.assertEqual([p['amount'] for p in actual['postings']], ['10.00', '-10.00'])
        self.assertEqual(actual['postings'][1]['assert']['commodity'], 'USD')
        self.assertEqual(read_json(self.root / 'ledger.json')['defaultCurrency'], 'INR')

    def test_unused_account_and_start_date_zero_assertion_can_change(self):
        self.apply('EUR', 'Assets:Bank:Savings Account')
        path = self.root / 'ledger.json';manifest = read_json(path);manifest['startDate']='2026-01-02';dump(path, manifest)
        dump(self.year, [dict(id='1'*16,date='2026-01-02',status='confirmed',description='Opening assertion',postings=[dict(account=CARD,amount='0.00',commodity='INR',assert_=None)])])
        value = read_json(self.year);value[0]['postings'][0].pop('assert_');value[0]['postings'][0]['assert'] = dict(amount='0.00', commodity='INR');dump(self.year, value)
        self.apply('GBP', CARD)
        self.assertEqual(read_json(self.year)[0]['postings'][0]['assert']['commodity'], 'GBP')

    def test_activity_and_forecast_lock_currency_but_shared_openings_relabel_independently(self):
        before = self.snapshot()
        with self.assertRaisesRegex(ValueError, 'fixed by transaction'):
            currency_changes(self.root, 'USD', CARD)
        self.assertEqual(self.snapshot(), before)
        value = entry();value['schedule']=dict(every='month', **{'from':value['date']});dump(self.year, []);dump(self.root / 'data/forecast.json', [value])
        with self.assertRaisesRegex(ValueError, 'fixed by transaction'):
            currency_changes(self.root, 'USD', CARD)
        dump(self.root / 'data/forecast.json', [])
        value = entry();value['postings'][0]['account']='Equity:Opening Balances';value['postings'].append(dict(account='Assets:Bank:Savings Account',amount='0.00',commodity='INR'));dump(self.year,[value])
        result = self.apply('USD', CARD)
        bank = next(p for p in result[self.year][0]['postings'] if p['account'] == 'Assets:Bank:Savings Account')
        self.assertEqual(bank['commodity'], 'INR')

    def test_default_change_touches_only_manifest_and_cli_uses_selected_root(self):
        before = self.snapshot()
        self.cli('set_default_currency.py', 'EUR')
        after = self.snapshot()
        self.assertEqual([key for key in before if before[key] != after[key]], ['ledger.json'])
        self.assertEqual(read_json(self.root / 'ledger.json')['defaultCurrency'], 'EUR')
        self.cli('set_account_currency.py', 'Assets:Bank:Savings Account', 'USD')
        self.assertEqual(load(self.root)[self.root / 'data/accounts.json']['accounts']['Assets:Bank:Savings Account']['currency'], 'USD')
        for code in ['eur','EU','EURO','EUR\n']:
            with self.assertRaisesRegex(ValueError,'three-letter'):
                currency_changes(self.root,code)

    def test_investment_units_need_a_currency_cost(self):
        path=self.root / 'data/accounts.json';decl=read_json(path);decl['accounts']['Holdings:Units']=dict(kind='investment',currency='USD');decl['commodities'].append('LOT');dump(path,decl)
        value=entry();value['postings'][1]=dict(account='Holdings:Units',amount='-1.00',commodity='LOT');dump(self.year,[value])
        with self.assertRaisesRegex(ValueError,'units need a cost'):
            load(self.root)
        value['postings'][1]['cost']=dict(amount='10.00',commodity='INR',per='total');dump(self.year,[value]);load(self.root)

    def test_zero_unit_postings_need_no_cost_with_or_without_assertions(self):
        path = self.root / 'data/accounts.json'
        declarations = read_json(path)
        declarations['accounts']['Assets:Investments:Units'] = dict(kind='investment', currency='USD')
        declarations['commodities'].append('LOT')
        dump(path, declarations)
        for amount in ['0.00', '0.000', '-0.000']:
            for assertion in [None, dict(amount='0.000', commodity='LOT')]:
                with self.subTest(amount=amount, assertion=assertion):
                    posting = dict(account='Assets:Investments:Units', amount=amount, commodity='LOT')
                    if assertion:
                        posting['assert'] = assertion
                    value = entry()
                    value['postings'] = [posting]
                    dump(self.year, [value])
                    load(self.root)

    def test_investment_assertion_may_check_another_permitted_commodity(self):
        path = self.root / 'data/accounts.json'
        declarations = read_json(path)
        declarations['accounts']['Assets:Investments:Units'] = dict(kind='investment', currency='USD')
        declarations['commodities'].append('LOT')
        dump(path, declarations)
        for commodity, assertion_commodity in [('USD', 'LOT'), ('LOT', 'USD')]:
            value = entry()
            value['postings'] = [dict(account='Assets:Investments:Units', amount='0.00',
                                      commodity=commodity, **{'assert': dict(amount='0.00', commodity=assertion_commodity)})]
            dump(self.year, [value])
            load(self.root)

    def test_usd_card_membership_and_summary_use_its_currency(self):
        value=entry();opening=entry(identity='0'*16,day='2026-01-01',amount='0.00');opening['postings'][0]['account']='Equity:Opening Balances'
        for row in [opening,value]:
            for p in row['postings']:p['commodity']='USD'
        value.update({'statement-end':'2026-01-28','serial-number':'EX-1','statement-page':1,'sources':['Bills/statement.pdf']})
        path=self.root / 'data/accounts.json';decl=read_json(path);decl['accounts'][CARD]['currency']='USD';dump(path,decl)
        dump(self.year,[opening,value]);dump(self.root / 'data/card-statements.json',[dict(id='f'*16,account=CARD,start='2026-01-01',end='2026-01-28',source='Bills/statement.pdf',rows=1,**{'previous-balance':'0.00','purchases':'10.00','credits':'0.00','total-due':'10.00'})])
        report=check_card_statements(load(self.root),self.root)
        self.assertEqual(report['statements'][0]['totalDue'],'10.00')
