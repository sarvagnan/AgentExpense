"""Exact calendar and conservative whole-payment tests, independent of source ledgers."""
from copy import deepcopy
from datetime import date, timedelta
import json
from calendar import monthrange
from math import gcd
import os
import shutil
import subprocess
import unittest
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from recurring import (active, advance, candidate, detect, identity, match_text,
                       occurrence, periodic_schedules, same_payment, text_amounts)
from generate_ledger import render_entry
from hledger_oracle import calendar_dates

ACCOUNTS={'Assets:Purse':dict(kind='cash',currency='EUR',label='Fern purse',aliases=['fern']),
          'Assets:Other':dict(kind='bank',currency='USD',label='River bank',aliases=['river'])}


def payment(identity='0000000000000001', day='2026-10-15', amount='20.00', currency='EUR', description='Willow club'):
    return dict(id=identity,date=day,status='confirmed',description=description,
                postings=[dict(account='Expenses:Leisure:Club',amount=amount,commodity=currency),
                          dict(account='Assets:Purse',amount='-'+amount,commodity=currency)])


def rule(day='2026-10-15', every='month', interval=1, anchor=None):
    r=payment();r['id']='f000000000000001';r['date']=day
    r['schedule']=dict(every=every,interval=interval,**{'from':day})
    if anchor: r['schedule']['anchor-day']=anchor
    return r


class RecurringTests(unittest.TestCase):
    def test_identity_uses_main_accounts_and_payer_not_fee_or_amount(self):
        a=payment();b=payment(amount='21.00');b['postings'].insert(1,dict(account='Expenses:Fees',amount='1.00',commodity='EUR'))
        self.assertEqual(identity(a,ACCOUNTS),identity(b,ACCOUNTS))
        fees=deepcopy(a);fees['postings'][0]['account']='Expenses:Fees';self.assertIsNone(identity(fees,ACCOUNTS))
        for name in ('Tax','Discount','Rounding','Fees:Service'):
            fees['postings'][0]['account']='Expenses:'+name;self.assertIsNone(identity(fees,ACCOUNTS))
        b['postings'][-1]['account']='Assets:Other';self.assertFalse(same_payment(a,b,ACCOUNTS))

    def test_name_required_dates_not_amounts_ranking_and_pick_list(self):
        r=rule();r['description']='Willow club';other=deepcopy(r);other.update(id='f000000000000002',date='2026-10-16')
        for text in ('20','20 EUR','fern 20','2026-10-15','15 oct','10/15/2026'):
            self.assertEqual(match_text([r],text,ACCOUNTS,'2026-10-15','EUR')['matches'],[])
        for text in ('Willow 15 oct','Willow 2026-10-15','Willow 15/10/2026','Willow October 15, 2026'):
            self.assertEqual(text_amounts(text),[])
        self.assertTrue(match_text([r,other],'Willow club',ACCOUNTS,'2026-10-15','EUR')['pick_list'])
        baseline=match_text([r],'Willow',ACCOUNTS,'2026-10-15','EUR')['suggestions'][0]['score']
        self.assertEqual(match_text([r],'Willow 20 USD',ACCOUNTS,'2026-10-15','EUR')['suggestions'][0]['score'],baseline)
        self.assertGreater(match_text([r],'Willow fern 21 EUR',ACCOUNTS,'2026-10-15','EUR')['matches'][0]['score'],baseline+4)
        other['lifecycle']='paused';self.assertEqual(len(match_text([r,other],'Willow club',ACCOUNTS,'2026-10-15','EUR')['matches']),1)
        shortcut_name={**r,'description':'Fern club'}
        self.assertEqual(match_text([shortcut_name],'fern 20 EUR',ACCOUNTS,'2026-10-15','EUR')['matches'],[])

    def test_one_shared_word_is_only_a_suggestion(self):
        """A lone shared word never proposes a rule; two distinctive words, or one with the amount, do."""
        def usual(identity, description, category, amount, currency, payer):
            return dict(id=identity, date='2026-11-15', status='confirmed', description=description, lifecycle='active',
                        schedule=dict(every='month', **{'from': '2026-11-15'}),
                        postings=[dict(account=category, amount=amount, commodity=currency),
                                  dict(account=payer, amount='-'+amount, commodity=currency)])
        # Asha's shape: a card subscription and plain cash.
        asha_accounts = {'Liabilities:Credit Cards:Mosaic Card': dict(kind='card', currency='INR', label='Mosaic Card', aliases=['mosaic card']),
                         'Assets:Cash': dict(kind='cash', currency='INR', label='Cash', aliases=['cash'])}
        lantern = usual('f0000000000000a1', 'Paper Lantern subscription', 'Expenses:Subscriptions:Paper Lantern', '90.00', 'INR', 'Liabilities:Credit Cards:Mosaic Card')
        weak = match_text([lantern], 'Paper Kite Market, 12, paid with cash', asha_accounts, '2026-11-06', 'INR', 'en-IN')
        self.assertEqual((weak['matches'], weak['rule_id'], weak['pick_list']), ([], None, False))
        self.assertEqual([(m['id'], m['matched_words']) for m in weak['suggestions']], [(lantern['id'], ['paper'])])
        for text, reason in [('Paper Lantern', 'two words'), ('Paper Lantern 90', 'two words and the amount'),
                             ('Lantern 90', 'one word and the amount')]:
            with self.subTest(text=text, reason=reason):
                result = match_text([lantern], text, asha_accounts, '2026-11-06', 'INR', 'en-IN')
                self.assertEqual((result['rule_id'], result['suggestions']), (lantern['id'], []))
        # The paying account alone is weak evidence: most payments share it, so it only ranks.
        payer_only = match_text([lantern], 'Lantern on Mosaic Card', asha_accounts, '2026-11-06', 'INR', 'en-IN')
        self.assertEqual((payer_only['rule_id'], [m['id'] for m in payer_only['suggestions']]), (None, [lantern['id']]))
        self.assertEqual(payer_only['suggestions'][0]['matched_alias'], 'Mosaic Card')
        # Jordan's shape: rent named by its expense account, paid from a checking account.
        jordan_accounts = {'Assets:Bank:Alder Checking': dict(kind='bank', currency='USD', label='Alder Checking', aliases=['alder', 'checking'])}
        rent = usual('f0000000000000b1', 'October rent', 'Expenses:Housing:Rent', '800.00', 'USD', 'Assets:Bank:Alder Checking')
        weak = match_text([rent], 'Birch rent 100 USD', jordan_accounts, '2026-11-06', 'USD', 'en-US')
        self.assertEqual((weak['matches'], weak['rule_id']), ([], None))
        self.assertEqual([m['id'] for m in weak['suggestions']], [rent['id']])
        for text in ('October rent', 'rent 800 USD', 'Rent from Alder Checking 800', 'rent 790'):
            with self.subTest(text=text):
                self.assertEqual(match_text([rent], text, jordan_accounts, '2026-11-06', 'USD', 'en-US')['rule_id'], rent['id'])
        self.assertEqual(match_text([rent], 'Rent from Alder Checking', jordan_accounts, '2026-11-06', 'USD', 'en-US')['rule_id'], None)
        # A pick list ranks proposals only; two one-word candidates are two suggestions.
        clover = [usual('f0000000000000c1', 'Clover music', 'Expenses:Music', '40.00', 'USD', 'Assets:Bank:Alder Checking'),
                  usual('f0000000000000c2', 'Clover lessons', 'Expenses:Lessons', '41.00', 'USD', 'Assets:Bank:Alder Checking')]
        both = match_text(clover, 'Clover 40', jordan_accounts, '2026-11-06', 'USD', 'en-US')
        self.assertTrue(both['pick_list']); self.assertEqual(len(both['matches']), 2); self.assertEqual(both['suggestions'], [])
        quiet = match_text(clover, 'Clover', jordan_accounts, '2026-11-06', 'USD', 'en-US')
        self.assertEqual((quiet['matches'], quiet['pick_list'], quiet['rule_id']), ([], False, None))
        self.assertEqual(len(quiet['suggestions']), 2)
        # A strong proposal and a weak one: only the proposal decides; the weak one stays a suggestion.
        mixed = match_text([rent, lantern], 'October rent paper', {**asha_accounts, **jordan_accounts}, '2026-11-06', 'USD', 'en-US')
        self.assertEqual((mixed['rule_id'], [m['id'] for m in mixed['suggestions']]), (rent['id'], [lantern['id']]))

    def test_only_distinctive_words_propose_a_usual_payment(self):
        """Category-group, account-kind and widely shared words never count; the payer only ranks."""
        accounts = {'Assets:Bank:Alder Checking': dict(kind='bank', currency='USD', label='Alder Checking', aliases=['alder', 'checking']),
                    'Liabilities:Credit Cards:Lumen Card': dict(kind='card', currency='USD', label='Lumen Card', aliases=['lumen']),
                    'Expenses:Insurance:Home': {}, 'Expenses:Insurance:Car': {}}
        def usual(identity, description, category, amount, payer='Assets:Bank:Alder Checking'):
            return dict(id=identity, date='2026-11-20', status='confirmed', description=description, lifecycle='active',
                        schedule=dict(every='month', **{'from': '2026-11-20'}),
                        postings=[dict(account=category, amount=amount, commodity='USD'),
                                  dict(account=payer, amount='-'+amount, commodity='USD')])
        # Two rules sharing a category word: "insurance" names the Insurance group, so it never counts.
        home = usual('f0000000000000d1', 'Quill home insurance', 'Expenses:Insurance:Home', '120.00')
        car = usual('f0000000000000d2', 'Thistle car insurance', 'Expenses:Insurance:Car', '64.00', 'Liabilities:Credit Cards:Lumen Card')
        def match(text, rules=(home, car)):
            return match_text(list(rules), text, accounts, '2026-11-06', 'USD', 'en-US')
        self.assertEqual(match('Quill home insurance')['rule_id'], home['id'])
        self.assertEqual(match('Quill insurance 120')['rule_id'], home['id'])
        one = match('Quill insurance')
        self.assertEqual((one['matches'], [(m['id'], m['matched_words']) for m in one['suggestions']]), ([], [(home['id'], ['quill'])]))
        # A payee typed with a shared word: nothing distinctive matches, even with the right amount.
        for text in ('Juniper Insurance Brokers 120', 'insurance 64', 'Juniper card 64'):
            with self.subTest(text=text):
                self.assertEqual({k: match(text)[k] for k in ('matches', 'suggestions', 'rule_id')}, dict(matches=[], suggestions=[], rule_id=None))
        # A word in three rules' names is shared, not distinctive; in two it still counts.
        dues = [usual('f0000000000000e%d' % n, 'Hawthorn ' + kind, 'Expenses:Clubs:' + kind.title(), amount)
                for n, (kind, amount) in enumerate([('dues', '30.00'), ('parking', '12.00'), ('pool', '18.00')], 1)]
        self.assertEqual({k: match('Hawthorn 30', dues)[k] for k in ('matches', 'suggestions')}, dict(matches=[], suggestions=[]))
        named = match('Hawthorn dues', dues)
        self.assertEqual((named['matches'], [m['id'] for m in named['suggestions']]), ([], [dues[0]['id']]))
        self.assertEqual(match('dues 30', dues)['rule_id'], dues[0]['id'])
        self.assertEqual(match('Hawthorn 30', dues[:2])['rule_id'], dues[0]['id'])
        # A rule with one distinctive word, typed with its amount, is still proposed; the payer breaks a tie.
        lone = usual('f0000000000000f1', 'Rent', 'Expenses:Housing:Rent', '800.00')
        self.assertEqual(match('Rent 800', [lone])['rule_id'], lone['id'])
        self.assertEqual(match('Rent', [lone])['rule_id'], None)
        twins = [usual('f0000000000000f2', 'Sorrel lessons', 'Expenses:Learning:Lessons', '40.00'),
                 usual('f0000000000000f3', 'Sorrel club', 'Expenses:Learning:Club', '40.00', 'Liabilities:Credit Cards:Lumen Card')]
        self.assertTrue(match('Sorrel 40', twins)['pick_list'])
        self.assertEqual(match('Sorrel 40 with Lumen', twins)['rule_id'], twins[1]['id'])
        # A rule named only by shared words is found by its whole name: with the amount it is proposed,
        # without it suggested; part of the name finds nothing, and a one-word shared name never counts.
        groups = dict(accounts, **{'Expenses:Car:Fuel': {}, 'Expenses:Car:Parking': {}})
        cover = usual('f0000000000000f5', 'Car insurance', 'Expenses:Insurance:Car', '90.00')
        alone = usual('f0000000000000f6', 'Insurance', 'Expenses:Insurance:Home', '120.00')
        def grouped(text, rules=(cover, home)):
            return match_text(list(rules), text, groups, '2026-11-06', 'USD', 'en-US')
        self.assertEqual(grouped('Car insurance 90')['rule_id'], cover['id'])
        whole = grouped('car insurance')
        self.assertEqual((whole['matches'], [(m['id'], m['matched_words']) for m in whole['suggestions']]), ([], [(cover['id'], ['car', 'insurance'])]))
        self.assertEqual({k: grouped('Juniper car 90')[k] for k in ('matches', 'suggestions')}, dict(matches=[], suggestions=[]))
        self.assertEqual({k: grouped('Juniper Insurance Brokers 120', [alone])[k] for k in ('matches', 'suggestions')}, dict(matches=[], suggestions=[]))

    def test_localized_calendar_forms_never_supply_amounts(self):
        for locale,text in [('en-GB','Willow 1st Nov 2026'),('en-US','Willow Nov 1st, 2026'),('en-IN','Willow 01/11'),('fr-FR','Willow 1 novembre 2026'),('de-DE','Willow 1. November 2026'),('es-ES','Willow noviembre 1 2026'),('ja-JP','Willow 2026年11月1日')]:
            with self.subTest(locale=locale):self.assertEqual(text_amounts(text,locale),[])
        self.assertEqual(text_amounts('Willow 20,50 EUR','fr-FR'),[__import__('decimal').Decimal('20.50')])
        self.assertEqual(text_amounts('Willow 1,20,000 INR','en-IN'),[__import__('decimal').Decimal('120000')])

    def test_advance_skips_missed_periods_retains_anchor(self):
        r=rule('2026-01-31');r=advance(r,'2026-02-12');self.assertEqual(r['date'],'2026-02-28')
        self.assertEqual(advance(r,'2026-02-28')['date'],'2026-03-31')
        self.assertEqual(advance(r,'2026-10-05')['date'],'2026-10-31')
        self.assertEqual(advance(rule('2026-01-31',interval=3),'2026-04-30')['date'],'2026-07-31')
        self.assertEqual(advance(rule('2024-02-29',every='year'),'2027-12-31')['date'],'2028-02-29')
        self.assertEqual(advance(rule('2024-02-29',every='year'),'2099-12-31')['date'],'2100-02-28')

    def test_legacy_periodic_blocks_remain_byte_identical(self):
        # New lifecycle/anchor support must not rewrite existing periodic blocks.
        for every, interval in [('month', n) for n in range(1, 14)] + [('year', n) for n in range(1, 9)]:
            for anchor in range(1, 32):
                r = rule(f'2026-01-{anchor:02}', every, interval)
                self.assertEqual(periodic_schedules(r), [r['schedule']])
                before = '\n'.join(render_entry(r))
                after = '\n'.join(line for schedule in periodic_schedules(r)
                                  for line in render_entry({**r, 'schedule': schedule}))
                self.assertEqual(after, before)

    def test_hledger_headers_and_ten_year_dates_for_every_anchor_and_interval(self):
        binary = os.getenv('HLEDGER_BIN') or shutil.which('hledger')
        intervals = [('week', 1), ('week', 2)]
        intervals += [('month', n) for n in (1, 2, 3, 5, 6, 7, 12, 13, 18, 24)]
        intervals += [('year', n) for n in (1, 2, 3, 4, 5, 6, 8)]
        # Batch the real formatter/CLI comparison: unique expense accounts identify
        # rules, including duplicate occurrences from an accidental phase split.
        for year in (2026, 2096):
            end = f'{year + 10}-01-01'
            journal, expected = [], {}
            for every, interval in intervals:
                for month in (1, 2, 4):
                    for anchor in range(1, 32):
                        start = f'{year}-{month:02}-{min(anchor, monthrange(year, month)[1]):02}'
                        r = rule(start, every, interval, anchor)
                        account = f'Expenses:Case{len(expected)}'
                        r['postings'][0]['account'] = account
                        schedules = periodic_schedules(r)
                        step = interval * (12 if every == 'year' else 1)
                        february_years = every != 'week' and step % 12 == 0 and month == 2 and anchor >= 29
                        count = 4 // gcd(step // 12, 4) if february_years else 1
                        with self.subTest(every=every, interval=interval, start=start, anchor=anchor):
                            self.assertEqual(len(schedules), count)
                            lines = [line for schedule in schedules
                                     for line in render_entry({**r, 'schedule': schedule})]
                            self.assertEqual(sum(line.startswith('~ ') for line in lines), count)
                            self.assertTrue(all(' to ' not in line for line in lines if line.startswith('~ ')))
                        journal.extend(lines)
                        dates, current = [], r
                        while current['date'] < end:
                            dates.append(current['date'])
                            if every == 'week':
                                current = {**current, 'date': (date.fromisoformat(current['date']) + timedelta(weeks=interval)).isoformat()}
                            else:
                                current = advance(current, current['date'])
                        expected[account] = (start, dates)
            observed = calendar_dates(binary, '\n'.join(journal), f'{year}-01-01', end)
            actual = {account: [day for day in observed.get(account, []) if day >= start]
                      for account, (start, dates) in expected.items()}
            for account, (start, dates) in expected.items():
                with self.subTest(year=year, account=account, start=start):
                    self.assertEqual(sorted(actual[account]), dates)

    def test_words_copy_whole_balanced_payment_and_annual(self):
        entry=payment();entry['postings'][0]['cost']=dict(amount='22.00',commodity='USD',per='total')
        for text,every in [('Willow subscription','month'),('Willow annual membership','year'),('yearly renewal Willow','year')]:
            result=candidate(entry,[entry],[],ACCOUNTS,{},'2026-11-01',text)
            self.assertTrue(result['automatic']);self.assertEqual(result['rule']['schedule']['every'],every)
            self.assertEqual(result['rule']['postings'],entry['postings'])
        self.assertIsNone(candidate(entry,[entry],[],ACCOUNTS,{},'2026-11-01','cancel subscription'))
        self.assertTrue(candidate(entry,[entry],[],ACCOUNTS,{},'2027-11-01','Willow annual membership')['automatic'])

    def test_history_consecutive_amount_currency_age_and_exclusions(self):
        old=payment(day='2026-09-15');latest=payment('0000000000000002',amount='22.00')
        found=candidate(latest,[old,latest],[],ACCOUNTS,{},'2026-11-01');self.assertTrue(found['automatic'])
        for change in ({'date':'2026-08-15'},{'description':'Other merchant'}):
            altered={**old,**change};result=candidate(latest,[altered,latest],[],ACCOUNTS,{},'2026-11-01')
            if change.get('date'): self.assertFalse(result['automatic'])
            else:self.assertIsNone(result)
        self.assertFalse(candidate(latest,[old,latest],[],ACCOUNTS,{},'2026-12-20')['automatic'])
        for r in (rule(),{**rule(),'lifecycle':'paused'},{**rule(),'lifecycle':'ended'}):
            self.assertIsNone(candidate(latest,[old,latest],[r],ACCOUNTS,{},'2026-11-01'))
        split = deepcopy(latest)
        split['postings'].insert(1,dict(account='Expenses:Other',amount='1.00',commodity='EUR'))
        self.assertIsNone(candidate(split,[old,split],[rule()],ACCOUNTS,{},'2026-11-01','subscription'))
        self.assertIsNone(candidate(latest,[old,latest],[],ACCOUNTS,{'never-automatic':['Expenses:Leisure']},'2026-11-01'))
        wrong=payment('0000000000000002',currency='USD');self.assertIsNone(candidate(wrong,[old,wrong],[],ACCOUNTS,{},'2026-11-01'))
        far=payment('0000000000000002',amount='22.01');self.assertIsNone(candidate(far,[old,far],[],ACCOUNTS,{},'2026-11-01'))

    def test_suppression_requires_two_new_payments_and_came_back(self):
        removed=rule();removed.update(lifecycle='removed',removal=dict(date='2026-10-20',evidence=['0000000000000001']))
        old=payment();one=payment('0000000000000002','2026-11-15');two=payment('0000000000000003','2026-12-15')
        self.assertIsNone(candidate(old,[old],[removed],ACCOUNTS,{},'2026-11-01','subscription'))
        self.assertIsNone(candidate(one,[old,one],[removed],ACCOUNTS,{},'2026-11-20','subscription'))
        already_recorded_future = {**old,'date':'2026-12-15'}
        self.assertIsNone(candidate(already_recorded_future,[one,already_recorded_future],[removed],ACCOUNTS,{},'2026-12-20','subscription'))
        found=candidate(two,[old,one,two],[removed],ACCOUNTS,{},'2026-12-20');self.assertTrue(found['automatic']);self.assertTrue(found['rule']['origin']['came_back'])
        made,suggestions=detect([old,one,two],[removed],ACCOUNTS,{},'2026-12-20');self.assertEqual(len(made),1);self.assertEqual(suggestions,[])


from test_ledger import Fixture, entry as fixture_entry
from store import dump, load, read_json


class RecurringStoreTests(Fixture):
    def test_generator_writes_only_active_rules(self):
        from generate_ledger import render
        rules=[]
        for index,life in enumerate(('active','paused','ended','removed')):
            r=fixture_entry(identity=f'f{index:015x}',schedule={'every':'month','from':'2026-01-02'},lifecycle=life)
            if life=='removed':r['removal']=dict(date='2026-01-02',evidence=[])
            rules.append(r)
        dump(self.root/'data/forecast.json',rules)
        text=render(load(self.root),self.root)
        self.assertEqual(text.count('~ monthly'),1)
        self.assertIn(rules[0]['id'],text)
        for r in rules[1:]:self.assertNotIn(r['id'],text)

    def test_lifecycle_origin_removal_links_and_preferences_are_checked(self):
        row=fixture_entry();r={**deepcopy(row),'id':'f000000000000001','schedule':dict(every='month',**{'from':row['date']}),
                              'lifecycle':'removed','origin':dict(kind='monthly-history',evidence=[row['id']]),
                              'created':row['date'],'removal':dict(date=row['date'],evidence=[row['id']])}
        row['rule']=dict(id=r['id'],occurrence=row['date'])
        dump(self.year,[row]);dump(self.root/'data/forecast.json',[r]);dump(self.root/'data/recurring-settings.json',{'never-automatic':['Expenses:Groceries']})
        self.assertEqual(load(self.root)[self.year][0]['rule'],row['rule'])
        for patch in ({'lifecycle':'unknown'},{'removal':None},{'origin':dict(kind='owner',evidence=['0'*16])},{'schedule':dict(every='month',**{'from':row['date'],'anchor-day':32})}):
            dump(self.root/'data/forecast.json',[{**r,**patch}])
            with self.assertRaises(ValueError):load(self.root)
        dump(self.root/'data/forecast.json',[r]);row['rule']['id']='a'*16;dump(self.year,[row])
        with self.assertRaisesRegex(ValueError,'rule link'):load(self.root)
        row['rule']['id']=r['id'];dump(self.year,[row]);dump(self.root/'data/recurring-settings.json',{'never-automatic':['Expenses:Groceries'],'machine':True})
        with self.assertRaises(ValueError):load(self.root)
