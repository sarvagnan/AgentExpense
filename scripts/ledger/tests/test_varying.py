"""Varying-payment arithmetic and real hledger forecast contracts."""
from copy import deepcopy
from datetime import date,timedelta
from decimal import Decimal
import os
from pathlib import Path
import shutil
import subprocess
import sys
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from recurring import recompute_estimate, payment_history, varying_details, amount_check, advance
from generate_ledger import forecast_templates,render_entry

ACCOUNTS={'Assets:Purse':{'kind':'cash','currency':'EUR'}}
def payment(number,day,value,**extra):
    return dict(id=f'{number:016x}',date=day,status='confirmed',description='Willow Energy',postings=[dict(account='Expenses:Utilities',amount=value,commodity='EUR'),dict(account='Assets:Purse',amount='-'+value,commodity='EUR')],**extra)
def rule():
    return dict(payment(0xf000000000000001,'2026-11-12','50.00'),amount='varies',schedule={'every':'month','from':'2026-11-12'},notes=['Keep these notes.'])

class VaryingTests(unittest.TestCase):
    def test_median_outlier_fewer_none_links_and_history_identity(self):
        r=rule();rows=[payment(1,'2026-08-12','40.00'),payment(2,'2026-09-12','180.00'),payment(3,'2026-10-12','48.00')]
        recompute_estimate(r,rows,ACCOUNTS);self.assertEqual(r['postings'][0]['amount'],'48.00');self.assertEqual(r['notes'],['Keep these notes.'])
        recompute_estimate(r,rows[:2],ACCOUNTS);self.assertEqual(Decimal(r['postings'][0]['amount']),Decimal('110'))
        recompute_estimate(r,rows[:1],ACCOUNTS);self.assertEqual(r['postings'][0]['amount'],'40.00')
        recompute_estimate(r,[],ACCOUNTS);self.assertEqual(r['postings'][0]['amount'],'40.00')
        unrelated=payment(4,'2026-10-20','5000.00');unrelated['description']='Other payee'
        linked=deepcopy(unrelated);linked['rule']={'id':r['id'],'occurrence':'2026-10-12'}
        self.assertEqual(len(payment_history(r,[*rows,unrelated],ACCOUNTS)),3)
        self.assertEqual(payment_history(r,[*rows,linked],ACCOUNTS)[0],linked)
        linked['rule']['id']='f000000000000002';self.assertEqual(len(payment_history(r,[*rows,linked],ACCOUNTS)),3)
        wrong=payment(5,'2026-11-01','50.00');wrong['postings'][0]['commodity']='GBP'
        self.assertEqual(len(payment_history(r,[*rows,wrong],ACCOUNTS)),3)
        stats=varying_details(r,rows,ACCOUNTS)['varying'];self.assertEqual((stats['min'],stats['max']),('40.00','180.00'))

    def test_last_six_strict_amount_boundaries_and_fixed_noop(self):
        r=rule();rows=[payment(i,f'2026-{i:02d}-12',f'{i*10}.00') for i in range(1,8)]
        # The old 10 is outside the most recent six (20..70).
        for value in ('10.00','140.00'):self.assertIsNone(amount_check(r,value,rows,ACCOUNTS))
        for value in ('9.99','140.01'):self.assertIsNotNone(amount_check(r,value,rows,ACCOUNTS))
        fixed=deepcopy(r);fixed.pop('amount');before=deepcopy(fixed)
        recompute_estimate(fixed,rows,ACCOUNTS);self.assertEqual(fixed,before);self.assertIsNone(amount_check(fixed,'1000.00',rows,ACCOUNTS))
        broken=deepcopy(r);broken['postings'].append(dict(account='Expenses:Fees',amount='1.00',commodity='EUR'))
        with self.assertRaisesRegex(ValueError,'two-posting'):recompute_estimate(broken,rows,ACCOUNTS)

    def forecast(self,r,start,end):
        text='\n'.join(['commodity EUR','account Expenses:Utilities','account Assets:Purse','',*(line for template in forecast_templates(r) for line in render_entry(template))])
        run=subprocess.run([os.environ.get('HLEDGER_BIN') or shutil.which('hledger'),'-f','-','print',f'--forecast={start}..{end}'],input=text,text=True,capture_output=True)
        self.assertEqual(run.returncode,0,run.stderr)
        return run.stdout

    def test_bill_replaces_one_occurrence_and_keeps_estimates_unbounded(self):
        r=rule();plain=self.forecast(r,'2026-11-01','2027-02-01');self.assertIn('2026-11-12',plain);self.assertEqual(plain.count('50.00 EUR'),6)
        for pay_by in (None,'2026-11-20','2026-12-16'):
            with self.subTest(pay_by=pay_by):
                r['bill']=dict(amount='77.00',received='2026-11-01',occurrence='2026-11-12',sources=[],**({'pay_by':pay_by} if pay_by else {}))
                output=self.forecast(r,'2026-11-01','2027-02-01')
                self.assertIn(pay_by or '2026-11-12',output);self.assertEqual(output.count('77.00 EUR'),2);self.assertEqual(output.count('50.00 EUR'),4)
                if pay_by:self.assertNotIn('2026-11-12',output)
        # Clamped month-end advances must not emit the paid occurrence again.
        r=advance(dict(rule(),date='2026-01-31',schedule={'every':'month','from':'2026-01-31'}),'2026-01-31')
        r['bill']=dict(amount='77.00',received='2026-02-01',occurrence=r['date'],sources=[])
        output=self.forecast(r,'2026-02-01','2026-05-01');self.assertIn('2026-02-28',output);self.assertIn('2026-03-31',output);self.assertEqual(output.count('77.00 EUR'),2)

    def test_same_day_order_and_linked_pending_payment_are_retained(self):
        r=rule();rows=[payment(i,'2026-10-12',f'{i}.00') for i in range(1,5)]
        self.assertEqual([e['id'] for e in payment_history(r,rows,ACCOUNTS)], [e['id'] for e in reversed(rows)])
        rows[-1]['status']='pending';rows[-1]['rule']={'id':r['id'],'occurrence':'2026-10-12'}
        recompute_estimate(r,rows,ACCOUNTS);self.assertEqual(r['postings'][0]['amount'],'3.00')

    def test_bill_bounds_clamped_quarterly_main_rule_after_its_occurrence(self):
        r=rule();r.update(date='2026-11-30',schedule={'every':'month','interval':3,'from':'2026-11-30','anchor-day':31})
        r['bill']=dict(amount='77.00',received='2026-11-01',occurrence=r['date'],sources=[])
        output=self.forecast(r,'2026-11-01','2027-08-01')
        self.assertEqual(output.count('2026-11-30'),1);self.assertIn('2027-02-28',output);self.assertIn('2027-05-31',output);self.assertEqual(output.count('50.00 EUR'),4)
        # The same phases remain unbounded well beyond the first forecast year.
        future=self.forecast(r,'2099-11-01','2101-08-01');self.assertIn('2100-02-28',future);self.assertIn('2100-05-31',future);self.assertIn('2101-02-28',future);self.assertNotIn('77.00 EUR',future)
