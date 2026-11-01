"""Synthetic checked statement applications; no model or source writes."""
from collections import Counter
from copy import deepcopy
from dataclasses import replace
from decimal import Decimal
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import tarfile
import threading
import time
from unittest.mock import patch
from pypdf import PdfReader, PdfWriter
from support import Fixture, APPLICATION, ready
from statements import Statements, StatementTransaction
from statement_pdf import parse_text
from statement_fixtures import TEXT, statement_pdf, statement_answer
CARD = 'Liabilities:Plastic:Main'
from store import load, entries
from audit import records
from transactions import clean

class StatementTests(Fixture):
    def wait_statement(self, client, identity, states):
        sessions = client.app.state.statements
        with sessions.changed:
            reached = sessions.changed.wait_for(lambda: sessions.get(identity).get('state') in states, timeout=180)
            last = sessions.get(identity)
        self.assertTrue(reached, last)
        return client.get('/api/statements/' + identity).json()

    def test_two_cards_without_identifiers_stop_before_any_agent(self):
        from store import dump
        declarations=load(self.ledger)[self.ledger/'data/accounts.json']
        declarations['accounts'].update({name:dict(kind='card',currency='INR',statement=dict(reader='example-card-pdf')) for name in (CARD,'Borrowed:Travel')})
        dump(self.ledger/'data/accounts.json',declarations)
        sessions=Statements(self.settings)
        session=sessions.create(statement_pdf(),'Statement.pdf')
        with patch('agent.run') as run:
            with self.assertRaisesRegex(ValueError,'Set statement.identifier on the card accounts'):
                sessions.read(session)
            run.assert_not_called()

    def test_printed_identifier_selects_one_of_two_unconventional_cards(self):
        from store import dump
        declarations=load(self.ledger)[self.ledger/'data/accounts.json']
        declarations['accounts'].update({CARD:dict(kind='card',currency='INR',label='Travel',statement=dict(reader='example-card-pdf',identifier='5678')),
                                         'Borrowed:Other':dict(kind='card',currency='INR',label='Other',statement=dict(reader='example-card-pdf',identifier='1234')),
                                         'Equity:Opening Balances':{}})
        dump(self.ledger/'data/accounts.json',declarations)
        dump(self.ledger/'data/expenses/2026.json',[dict(id='a'*16,date='2026-01-01',status='confirmed',description='Opening',postings=[dict(account=CARD,amount='0.00',commodity='INR'),dict(account='Equity:Opening Balances',amount='0.00',commodity='INR')])])
        sessions=Statements(self.settings)
        session=sessions.create(statement_pdf(),'Statement.pdf')
        with patch('agent.run') as run:
            sessions.read(session)
            run.assert_not_called()
        result=sessions.get(session['id'])
        self.assertEqual(result['statement']['account'],CARD)
        self.assertEqual(result['statement']['accountLabel'],'Travel')
        self.assertEqual(result['state'],'found')

    def test_new_pending_foreign_row_preserves_currency_and_billed_cost(self):
        from store import dump
        from statements import fingerprint
        declarations=load(self.ledger)[self.ledger/'data/accounts.json']
        declarations['accounts'].update({CARD: dict(kind='card', currency='INR', label='Test Card', statement=dict(reader='example-card-pdf', identifier='5678')), 'Equity:Opening Balances': {}})
        dump(self.ledger/'data/accounts.json',declarations)
        dump(self.ledger/'data/expenses/2026.json',[dict(id='a'*16,date='2026-09-01',status='confirmed',description='Fixture card opening',postings=[dict(account=CARD,amount='0.00',commodity='INR'),dict(account='Equity:Opening Balances',amount='0.00',commodity='INR')])])
        self.jj('commit','-m','Foreign row fixture baseline')
        sessions=Statements(self.settings)
        writer=PdfWriter();writer.add_blank_page(width=100,height=100);output=io.BytesIO();writer.write(output)
        s=sessions.create(output.getvalue(),'synthetic-statement.pdf')
        text=TEXT.replace('2026-01-01 to 2026-01-28', '2026-09-29 to 2026-10-28').replace('2026-01-02', '2026-10-01').replace('120.00', '900.00').replace('Credits: 30.00', 'Credits: 0.00').replace('Total due: 90.00', 'Total due: 900.00').replace('2.00 | USD', '10.00 | USD').split('2026-01-03 |')[0]
        statement=parse_text(text)
        statement.update(account=CARD, accountLabel='Test Card')
        row=statement['rows'][0]
        s.update(state='review',fingerprint=fingerprint(self.ledger),extraction='parser',statement=statement,plan=dict(items=[dict(row=row,rule='needs_you',entry_id=None,postings=None,needs_user=True,decision=dict(action='pending',account='Expenses:Uncategorized'))],counts=dict(needs_you=1)))
        sessions.put(s);sessions.queue_apply(s['id']);sessions.work(threading.Event())
        self.assertEqual(sessions.get(s['id'])['state'],'applied',sessions.get(s['id']).get('error'))
        added=next(e for e in entries(load(self.ledger)) if e.get('serial-number')==row['serial'])
        self.assertEqual(added['status'],'pending');self.assertEqual(added['original-amount'],dict(amount='10.00',commodity='USD'))
        self.assertEqual(added['postings'][0],dict(account='Expenses:Uncategorized',amount='10.00',commodity='USD',cost=dict(amount='900.00',commodity='INR',per='total')))
        self.assertEqual(added['postings'][1]['amount'],'-900.00')
        self.assertTrue(clean(self.ledger))

    def test_agent_statement_answer_preserves_file_for_handover(self):
        writer=PdfWriter();writer.add_blank_page(width=100,height=100);output=io.BytesIO();writer.write(output)
        answer=ready();answer.update(status='not_expense',entry=None,explanation='This is a card statement, not one expense.')
        self.set_answers(answer)
        with self.client() as client:
            identity=self.create(client,'Unclear scanned document',upload=False)
            r=client.post(f'/api/drafts/{identity}/files',files={'files':('scan.pdf',output.getvalue(),'application/pdf')})
            self.assertEqual(r.status_code,201)
            self.save(client,identity);draft=self.wait(client,identity,'added')
            self.assertEqual(len(self.calls()),1);self.assertTrue(draft['statement_file'])
            self.assertTrue((client.app.state.drafts.folder(identity)/'files').is_dir())
            response=client.post(f'/api/drafts/{identity}/statements')
            self.assertEqual(response.status_code,201,response.text)
            self.assertEqual(response.json()['handover_draft'],identity)
            self.assertEqual(client.get(f'/api/drafts/{identity}').status_code,404)
            self.assertEqual(json.loads((self.ledger/'data/expenses/2026.json').read_text()),[])


    def test_locked_example_pdf_is_checked_decided_filed_and_audited(self):
        from store import dump
        declarations=load(self.ledger)[self.ledger/'data/accounts.json']
        declarations['accounts'].update({CARD: dict(kind='card', currency='INR', label='Test Card', statement=dict(reader='example-card-pdf', identifier='5678')), 'Assets:Bank:Savings Account': dict(kind='bank', currency='INR'), 'Equity:Opening Balances': {}})
        dump(self.ledger/'data/accounts.json', declarations)
        dump(self.ledger/'data/expenses/2026.json', [dict(id='a'*16, date='2026-01-01', status='confirmed', description='Example opening', postings=[dict(account=CARD,amount='0.00',commodity='INR'),dict(account='Equity:Opening Balances',amount='0.00',commodity='INR')])])
        self.jj('commit','-m','Example card baseline')
        sessions=Statements(self.settings)
        pdf=statement_pdf(password='invented-test-password')
        s=sessions.create(pdf, 'CardStatement-2026-01-28.pdf')
        self.assertEqual(s['state'], 'locked')
        with self.assertRaisesRegex(ValueError, 'password'):
            sessions.unlock(s['id'], 'incorrect')
        s=sessions.unlock(s['id'], 'invented-test-password')
        with patch('agent.run') as run:
            sessions.read(s)
            run.assert_not_called()
        s=sessions.get(s['id'])
        self.assertEqual(s['state'], 'found', s.get('error'))
        self.assertTrue(all(c['ok'] for c in s['checks']))
        self.assertEqual(s['statement'], {**parse_text(TEXT), 'currency':'INR', 'account':CARD, 'accountLabel':'Test Card'})
        sessions.decide(s['id'], 'EX-001', dict(action='account', account='Expenses:Food'))
        sessions.decide(s['id'], 'EX-002', dict(action='account', account='Assets:Bank:Savings Account'))
        sessions.queue_apply(s['id']);sessions.work(threading.Event())
        s=sessions.get(s['id'])
        self.assertEqual(s['state'], 'applied', s.get('error'))
        self.assertTrue((self.ledger/s['result']['source']).exists())
        docs=load(self.ledger)
        self.assertEqual(docs[self.ledger/'data/card-statements.json'][0]['account'], CARD)
        self.assertEqual(len([e for e in entries(docs) if e.get('statement-end')=='2026-01-28']), 2)
        from reconcile_statement import reconciliation_path
        self.assertTrue(reconciliation_path(self.ledger,'2026-01-28',CARD,{'label':'Test Card'}).exists())
        self.assertEqual(records(self.ledger)[0]['kind'], 'statement')
        self.assertIn('Reconcile Test Card statement', self.jj('log', '-r', '@-', '--no-graph', '-T', 'description'))
        self.assertNotIn('invented-test-password', json.dumps(s))
        self.assertTrue(clean(self.ledger))

    def test_totals_failure_cannot_reach_reconciliation_rules(self):
        from store import dump
        declarations=load(self.ledger)[self.ledger/'data/accounts.json']
        declarations['accounts'][CARD] = dict(kind='card',currency='INR',statement=dict(reader='example-card-pdf',identifier='5678'))
        dump(self.ledger/'data/accounts.json',declarations)
        sessions=Statements(self.settings)
        s=sessions.create(statement_pdf(TEXT.replace('Purchases: 120.00','Purchases: 121.00')), 'CardStatement-2026-01-28.pdf')
        answer=statement_answer();answer['purchases']='121.00'
        with patch('agent.run', return_value=(answer, dict(model='fake',effort='offline'))), patch('statements.plan') as rules:
            with self.assertRaisesRegex(ValueError, 'could not validate'):
                sessions.read(s)
            rules.assert_not_called()
        self.assertEqual(load(self.ledger)[self.ledger/'data/expenses/2026.json'], [])
