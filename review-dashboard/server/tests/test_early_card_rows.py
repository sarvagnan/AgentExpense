"""Card rows dated before their statement period (step 7A2); every statement is invented.

A card statement lists each purchase posted in its billing period under the day
the purchase was made, so a purchase made a few days before the period starts
appears on this statement, and on no other, with a date before periodStart.
"""
from copy import deepcopy
import csv
from decimal import Decimal
import io
import json
import secrets
import threading

from support import Fixture, meta, entry as store_entry, posting
from store import load, dump, entries
from statements import Statements
from statement_fixtures import statement_pdf
from statement_readers import check_statement
from statement_reading import extract, verify
from cited_statement_fixtures import answer, table
from check_store import check_card_statements
from confirmed_balances import failures, correction_locks
from reconcile_statement import reconcile
from retention_service import coverage, Retention
from replacements import statement_locked, enforce_statement, entry_hash
from drafts import Drafts
from add_entry import add_entry

CARD = 'Liabilities:Cards:Example'
META = meta(kind='card', currency='INR', label='Example Card', statement=dict(reader='example-card-pdf', identifier='5678'))
PERSONAL_READER = '''from statement_readers import Registry
INTERFACE = 1
ID = 'personal-card'
NAME = 'Personal card'
KINDS = ('card',)
EXTENSIONS = ('.pdf',)
CHECKS = ('printed_totals',)
def recognise(file): return dict(confidence=1, reasons=['Fictional personal layout'])
def read(file, account, password=None): return Registry().get('example-card-pdf').read(file, account)
'''


def card_text(start, end, rows, previous='0.00'):
    """Example card PDF text; rows are (date, serial, description, amount, direction)."""
    purchases = sum((Decimal(r[3]) for r in rows if r[4] == 'Debit'), Decimal(0))
    credits = sum((Decimal(r[3]) for r in rows if r[4] == 'Credit'), Decimal(0))
    total = Decimal(previous) + purchases - credits
    lines = ['Example Card Statement', f'Statement period: {start} to {end}', 'Card last four digits: 5678',
             f'Previous balance: {previous}', f'Purchases: {purchases:.2f}', 'Cash advances: 0.00',
             f'Credits: {credits:.2f}', f'Total due: {total:.2f}', 'Minimum due: 1.00', f'Due date: {end}',
             'Date | Serial | Description | Amount | Direction | Foreign amount | Currency']
    return '\n'.join(lines + [f'{d} | {serial} | {text} | {amount} | {direction} |  | ' for d, serial, text, amount, direction in rows]) + '\n'


def purchase(identity, day, description, amount):
    return store_entry(identity, day, description, [posting('Expenses:Food', amount), posting(CARD, '-' + amount)])


JANUARY = ('2026-01-01', '2026-01-28')
FEBRUARY = ('2026-01-29', '2026-02-28')


class EarlyCardRows(Fixture):
    def card(self, *, personal=False, opening=True, start_date=None):
        docs = load(self.ledger)
        accounts = docs[self.ledger/'data/accounts.json']
        accounts['accounts'].update({CARD: deepcopy(META), 'Equity:Opening Balances': {}})
        if personal:
            (self.ledger/'readers').mkdir()
            (self.ledger/'readers/personal.py').write_text(PERSONAL_READER)
            accounts['accounts'][CARD]['statement']['reader'] = 'personal-card'
        dump(self.ledger/'data/accounts.json', accounts)
        if opening:
            day = start_date or '2025-11-01'
            dump(self.ledger/f'data/expenses/{day[:4]}.json', [store_entry('1000000000000001', day, 'Opening card balance',
                 [posting(CARD, '0.00'), posting('Equity:Opening Balances', '0.00')])])
        if start_date:
            manifest = json.loads((self.ledger/'ledger.json').read_text())
            manifest['startDate'] = start_date
            dump(self.ledger/'ledger.json', manifest)
        self.jj('commit', '-m', 'Fictional card account')
        return Statements(self.settings)

    def record(self, *rows):
        """Entries typed before their statement arrived, in a later commit than the baseline."""
        docs = load(self.ledger)
        for row in rows:
            docs.setdefault(self.ledger/f"data/expenses/{row['date'][:4]}.json", []).append(row)
        for path, document in docs.items():
            if path.parent.name == 'expenses':
                dump(path, sorted(document, key=lambda e: e['date']))
        self.jj('commit', '-m', 'Fictional purchases awaiting their statement')

    def read(self, sessions, text, name='Statement.pdf', state='found'):
        session = sessions.create(statement_pdf(text), name)
        sessions.work(threading.Event())
        session = sessions.get(session['id'])
        self.assertEqual(session['state'], state, session.get('error'))
        return session

    def apply(self, sessions, session):
        for item in session['plan']['items']:
            if item['needs_user']:
                sessions.decide(session['id'], item['row']['serial'], dict(action='pending'))
        sessions.queue_apply(session['id'])
        sessions.work(threading.Event())
        session = sessions.get(session['id'])
        self.assertEqual(session['state'], 'applied', session.get('error'))
        return load(self.ledger)

    def item(self, session, day):
        return next(i for i in session['plan']['items'] if i['row']['date'] == day)

    def test_built_in_card_rows_one_and_three_days_early_validate_match_and_reconcile(self):
        sessions = self.card()
        typed = purchase('2000000000000001', '2025-12-31', 'Fable Shop', '120.00')
        self.record(typed)
        session = self.read(sessions, card_text(*JANUARY, [('2025-12-31', 'EX-001', 'Fable Shop', '120.00', 'Debit'),
                                                         ('2025-12-29', 'EX-002', 'Fable Books', '30.00', 'Debit'),
                                                         ('2026-01-05', 'EX-003', 'Payment received', '50.00', 'Credit')]))
        self.assertTrue(all(c['ok'] for c in session['checks']))
        self.assertEqual(self.item(session, '2025-12-31')['rule'], 'exact')
        self.assertEqual(self.item(session, '2025-12-31')['entry_id'], typed['id'])
        docs = self.apply(sessions, session)
        members = [e for e in entries(docs) if (e['statement'] or {}).get('end') == '2026-01-28']
        self.assertEqual(sorted(e['date'] for e in members), ['2025-12-29', '2025-12-31', '2026-01-05'])
        self.assertEqual([r['result'] for r in check_card_statements(docs, self.ledger)['statements']], ['PASS'])
        self.assertEqual(check_card_statements(docs, self.ledger)['unbilled'], [])
        report = reconcile(docs, self.reconciliation(JANUARY[1]), start=JANUARY[0], end=JANUARY[1], account=CARD, root=self.ledger)
        self.assertEqual(sorted(i['row']['date'] for i in report['alreadyMembers']), ['2025-12-29', '2025-12-31', '2026-01-05'])
        self.assertEqual(report['warnings'], [])

    def test_dates_more_than_31_days_early_or_after_the_period_are_refused_with_citation(self):
        from statement_pdf import parse_text
        def statement(day):
            s = parse_text(card_text(*JANUARY, [(day, 'EX-001', 'Fable Shop', '10.00', 'Debit')]))
            s['declaredChecks'] = ['printed_totals']
            s['rows'][0]['citation'] = dict(page=1, line=12)
            return s
        self.assertTrue(all(c['ok'] for c in check_statement(statement('2025-12-01'), account=META)))
        for day, problem in [('2025-11-30', 'before the statement period starts by more than 31 days'), ('2026-01-29', 'after the statement period ends')]:
            with self.subTest(day=day), self.assertRaisesRegex(ValueError, f'{day} is {problem}.*: page 1, line 12$'):
                check_statement(statement(day), account=META)
        sessions = self.card()
        refused = self.read(sessions, card_text(*JANUARY, [('2025-11-30', 'EX-001', 'Fable Shop', '10.00', 'Debit')]), state='couldnt_read')
        self.assertRegex(refused['error'], r"^A row's date doesn't fit this statement.*2025-11-30.*more than 31 days.*page 1, line \d+$")
        self.assertEqual(load(self.ledger)[self.ledger/'data/card-statements.json'], [])

    def test_account_statements_keep_rows_inside_their_period(self):
        row = dict(date='2025-12-31', description='Fable Shop', amount='1.00', direction='Debit', serial='1', balance='99.00', citation=dict(sheet='CSV', row=8))
        statement = dict(kind='bank', currency='INR', periodStart=JANUARY[0], periodEnd=JANUARY[1], openingBalance='100.00', declaredChecks=['running_balance'], rows=[row])
        # A signed export of a card account keeps running balances in date order too.
        for kind in ('bank', 'card'):
            with self.subTest(kind=kind), self.assertRaisesRegex(ValueError, r'2025-12-31 is before the statement period starts: CSV, row 8$'):
                check_statement(deepcopy(statement), account=dict(kind=kind, currency='INR'))
        with self.assertRaisesRegex(ValueError, 'after the statement period ends: CSV, row 8$'):
            check_statement({**deepcopy(statement), 'rows': [{**row, 'date': '2026-01-29'}]}, account=dict(kind='bank', currency='INR'))

    def reconciliation(self, end):
        return next(json.loads(p.read_text()) for p in (self.ledger/'data/statements').glob('*.json') if json.loads(p.read_text())['periodEnd'] == end)['rows']

    def consecutive(self, *, personal):
        sessions = self.card(personal=personal)
        self.apply(sessions, self.read(sessions, card_text(*JANUARY, [('2026-01-05', 'JA-001', 'Fable Shop', '90.00', 'Debit')]), 'January.pdf'))
        january = {e['id'] for e in entries(load(self.ledger)) if e['description'] == 'Fable Shop'}
        # Made inside January's period, but posted after it closed.
        late = purchase('3000000000000001', '2026-01-27', 'Fable Cafe', '40.00')
        self.record(late)
        self.assertEqual([u['id'] for u in check_card_statements(load(self.ledger), self.ledger)['unbilled']], [late['id']])
        february = self.read(sessions, card_text(*FEBRUARY, [('2026-01-27', 'FE-001', 'Fable Cafe', '40.00', 'Debit'),
                                                           ('2026-02-03', 'FE-002', 'Fable Books', '15.00', 'Debit')], previous='90.00'), 'February.pdf')
        self.assertTrue(all(c['ok'] for c in february['checks']))
        item = self.item(february, '2026-01-27')
        self.assertEqual((item['rule'], item['entry_id']), ('exact', late['id']))
        self.assertFalse(january & {u['entry_id'] for u in february['plan']['unmatched']})
        docs = self.apply(sessions, february)
        self.assertEqual(len(check_card_statements(docs, self.ledger)['statements']), 2)
        self.assertEqual(check_card_statements(docs, self.ledger)['unbilled'], [])
        report = reconcile(docs, self.reconciliation(FEBRUARY[1]), start=FEBRUARY[0], end=FEBRUARY[1], account=CARD, root=self.ledger)
        return docs, late, report

    def test_consecutive_built_in_card_statements_bill_an_early_row_on_the_second_only(self):
        docs, late, report = self.consecutive(personal=False)
        self.assertEqual(next(e for e in entries(docs) if e['id'] == late['id'])['statement']['end'], FEBRUARY[1])
        self.assertIn(late['id'], [i['entryId'] for i in report['alreadyMembers']])

    def test_consecutive_normalized_card_statements_bill_an_early_row_on_the_second_only(self):
        docs, late, report = self.consecutive(personal=True)
        summaries = docs[self.ledger/'data/card-statements.json']
        self.assertEqual([s['end'] for s in summaries if any(m.get('entry-id') == late['id'] for m in s['members'])], [FEBRUARY[1]])
        self.assertTrue(all(s['source-balance-style'] == 'debt' and s['assertion-id'] is None for s in summaries))
        self.assertIn(('serial', late['id']), [(i['rule'], i['entry_id']) for i in report['items']])

    def test_early_member_is_covered_locked_and_cannot_be_deleted(self):
        sessions = self.card(personal=True)
        early = purchase('4000000000000001', '2025-12-30', 'Fable Shop', '60.00')
        self.record(early)
        docs = self.apply(sessions, self.read(sessions, card_text(*JANUARY, [('2025-12-30', 'EX-001', 'Fable Shop', '60.00', 'Debit')])))
        early = next(e for e in entries(docs) if e['id'] == early['id'])
        retention = Retention(self.settings, Drafts(self.ledger))
        self.assertTrue(statement_locked(docs, self.ledger, early))
        status = retention.eligibility(early['id'])
        self.assertEqual((status['offered'], status['reason'], status['lock']), (False, 'This entry is included in a filed statement.', 'statement'))
        with self.assertRaisesRegex(ValueError, 'filed statement'):
            retention.operate(secrets.token_hex(16), dict(action='delete', entry_id=early['id'], version=entry_hash(early)))
        changed = deepcopy(early)
        changed['postings'][1]['amount'] = '-61.00'
        with self.assertRaisesRegex(ValueError, 'filed statement'):
            enforce_statement(early, changed, docs, self.ledger)
        # Membership, not a date window, locks the card posting.
        self.assertEqual(correction_locks(docs, self.ledger, early), [])
        # A billed total is no confirmed balance on its closing date: a purchase
        # made inside the filed period can still be recorded before it is billed.
        unbilled = purchase(None, '2026-01-27', 'Fable Cafe', '20.00')
        add_entry(unbilled, root=self.ledger)
        self.assertEqual(next(failures(load(self.ledger), self.ledger), None), None)

    def test_confirmed_balance_duplicates_include_an_early_member_of_its_statement(self):
        self.card()
        docs = load(self.ledger)
        member = purchase('5000000000000001', '2025-12-31', 'Fable Shop', '10.00')
        close = store_entry('5000000000000002', '2026-01-28', 'Statement closing balance',
                            [posting(CARD, '0.00', **{'assert': dict(amount='-10.00', commodity='INR')})])
        docs[self.ledger/'data/expenses/2025.json'].append(member)
        docs[self.ledger/'data/expenses/2026.json'].append(close)
        docs[self.ledger/'data/card-statements.json'] = [dict(account=CARD, start=JANUARY[0], end=JANUARY[1], source='Bills/Example.pdf', kind='bank',
                                                              members=[{'entry-id': member['id']}], **{'assertion-id': close['id']})]
        repeat = {**deepcopy(member), 'id': '5000000000000003'}
        docs[self.ledger/'data/expenses/2025.json'].append(repeat)
        refusal = next(failures(docs, self.ledger, candidate=repeat))
        self.assertEqual([d['entry_id'] for d in refusal['possible_duplicates']], [member['id']])

    def test_agent_read_card_statement_accepts_an_early_row(self):
        self.card()
        docs = load(self.ledger)
        docs[self.ledger/'data/expenses/2025.json'][0]['postings'][0]['amount'] = '-100.00'
        docs[self.ledger/'data/expenses/2025.json'][0]['postings'][1]['amount'] = '100.00'
        dump(self.ledger/'data/expenses/2025.json', docs[self.ledger/'data/expenses/2025.json'])
        self.jj('commit', '-m', 'Fictional card debt')
        # Printed debt: 100 owed, a 10.00 purchase two days before the period, a 2.00 refund.
        rows = table('INR')
        rows[2][1], rows[3][1] = '100.00', '108.00'
        rows[7][0], rows[7][3], rows[8][3] = '2025-12-30', '110.00', '108.00'
        source = io.StringIO()
        csv.writer(source).writerows(rows)
        reading = answer('INR')
        reading['balanceStyle'] = 'debt'
        for key, value in [('openingBalance', '100.00'), ('closingBalance', '108.00')]:
            reading[key]['value'] = value
        reading['rows'][0]['date']['value'], reading['rows'][0]['balance']['value'], reading['rows'][1]['balance']['value'] = '2025-12-30', '110.00', '108.00'
        path = self.base/'early.csv'
        path.write_bytes(source.getvalue().encode())
        work = self.base/'source'
        work.mkdir()
        self.assertEqual(verify(deepcopy(reading), extract(path, work), META)['rows'][0]['date'], '2025-12-30')
        sessions = Statements(self.settings)
        session = sessions.create(source.getvalue().encode(), 'Unknown.csv')
        session.update(agent_requested=True, assigned_account=CARD)
        sessions.put(session)
        self.set_answers(reading)
        sessions.work(threading.Event())
        session = sessions.get(session['id'])
        self.assertEqual(session['state'], 'found', session.get('error'))
        self.assertEqual([i['row']['date'] for i in session['plan']['items']], ['2025-12-30', '2026-01-03'])
        docs = self.apply(sessions, session)
        summary = docs[self.ledger/'data/card-statements.json'][0]
        self.assertEqual((summary['source-balance-style'], summary['members'][0]['date']), ('debt', '2025-12-30'))
        check_card_statements(docs, self.ledger)
        # A misread year in the agent's reading is refused with its source.
        misread = deepcopy(reading)
        misread['rows'][0]['date']['value'] = '2024-12-30'
        (self.base/'misread').mkdir()
        units = extract(path, self.base/'misread')
        units['units'][misread['rows'][0]['date']['source']] = '2024-12-30'
        with self.assertRaisesRegex(ValueError, 'more than 31 days.*CSV, row 8'):
            verify(misread, units, META)

    def test_restore_is_refused_while_a_card_statement_covers_the_date_from_its_earliest_member(self):
        sessions = self.card(personal=True)
        removed = purchase('6000000000000001', '2025-12-30', 'Fable Shop', '25.00')
        self.record(removed)
        retention = Retention(self.settings, Drafts(self.ledger))
        trashed = retention.operate(secrets.token_hex(16), dict(action='delete', entry_id=removed['id'], version=entry_hash(removed)))
        # The statement then bills that purchase again, as a new entry.
        self.apply(sessions, self.read(sessions, card_text(*JANUARY, [('2025-12-29', 'EX-001', 'Fable Shop', '25.00', 'Debit')])))
        result = retention.operate(secrets.token_hex(16), dict(action='restore', item_id=trashed['item_id']))
        self.assertEqual((result['refused'], result['reason']), (True, 'A statement filed since removal covers this date without this entry.'))
        docs = load(self.ledger)
        self.assertEqual([e['date'] for e in entries(docs) if e['description'] == 'Fable Shop'], ['2025-12-29'])
        # A date the statement does not reach stays restorable.
        self.assertIsNone(coverage(docs, self.ledger, {**removed, 'date': '2025-12-28'}, restoring=True))

    def test_start_date_still_classifies_each_early_purchase_by_its_own_date(self):
        sessions = self.card(start_date='2025-12-30')
        docs = load(self.ledger)
        docs[self.ledger/'data/expenses/2025.json'][0]['postings'][0]['amount'] = '-30.00'
        docs[self.ledger/'data/expenses/2025.json'][0]['postings'][1]['amount'] = '30.00'
        dump(self.ledger/'data/expenses/2025.json', docs[self.ledger/'data/expenses/2025.json'])
        self.jj('commit', '-m', 'Fictional balance owed on the start date')
        session = self.read(sessions, card_text(*JANUARY, [('2025-12-31', 'EX-001', 'Fable Shop', '120.00', 'Debit'), ('2025-12-29', 'EX-002', 'Fable Books', '30.00', 'Debit')]))
        self.assertEqual([i['rule'] for i in session['plan']['items']], ['needs_you', 'pre_start'])
        docs = self.apply(sessions, session)
        check_card_statements(docs, self.ledger)
        self.assertEqual([m['disposition'] for m in docs[self.ledger/'data/card-statements.json'][0]['members']], ['recorded', 'pre-start'])

    def test_first_statement_opens_an_unused_card_before_its_earliest_purchase(self):
        sessions = self.card(opening=False)
        session = self.read(sessions, card_text(*JANUARY, [('2026-01-04', 'EX-001', 'Fable Shop', '20.00', 'Debit'), ('2025-12-29', 'EX-002', 'Fable Books', '30.00', 'Debit')], previous='70.00'))
        self.assertEqual((session['opening']['date'], session['opening']['postings'][0]['amount']), ('2025-12-29', '-70.00'))
        docs = self.apply(sessions, session)
        check_card_statements(docs, self.ledger)

    def test_first_normalized_statement_continues_from_the_opening_balance_alone(self):
        sessions = self.card(personal=True)
        # Typed two days before the date the card prints, and before the period.
        typed = purchase('7000000000000001', '2025-12-27', 'Fable Shop', '120.00')
        self.record(typed)
        session = self.read(sessions, card_text(*JANUARY, [('2025-12-29', 'EX-001', 'Fable Shop', '120.00', 'Debit'), ('2025-12-31', 'EX-002', 'Fable Books', '5.00', 'Debit')]))
        self.assertTrue(all(c['ok'] for c in session['checks']))
        self.assertEqual(self.item(session, '2025-12-29')['entry_id'], typed['id'])
        check_card_statements(self.apply(sessions, session), self.ledger)
