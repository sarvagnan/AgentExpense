"""First-run screens: name, accounts, opening balances and statement import.

Every ledger is a fictional disposable fixture; no model runs (the fake adapter
answers statement readings).
"""
from datetime import date, timedelta
import json
import threading
from unittest.mock import patch

from support import Fixture
from statement_fixtures import statement_pdf
from cited_statement_fixtures import answer, file_bytes
from store import dump, entries, load, read_json
from audit import records
from transactions import clean

CARD_TEXT = """Example Card Statement
Statement currency: {currency}
Statement period: {start} to {end}
Card last four digits: 4321
Previous balance: {previous}
Purchases: {purchases}
Cash advances: 0.00
Credits: 0.00
Total due: {due}
Minimum due: 1.00
Due date: 2026-03-15
Date | Serial | Description | Amount | Direction | Foreign amount | Currency
{rows}
"""


def card_pdf(currency, start, end, previous, rows, prefix):
    purchases = sum(float(amount) for _, _, amount in rows)
    text = CARD_TEXT.format(currency=currency, start=start, end=end, previous=f'{previous:.2f}', purchases=f'{purchases:.2f}',
                            due=f'{previous + purchases:.2f}',
                            rows='\n'.join(f'{day} | {serial} | {name} | {amount} | Debit | |' for day, serial, name, amount in
                                           [(d, f'{prefix}-{i:03d}', n, a) for i, (d, n, a) in enumerate(rows, 1)]))
    return statement_pdf(text)


class FirstRun(Fixture):
    def setUp(self):
        super().setUp()
        # A ledger fresh from the template: no accounts, no entries.
        dump(self.ledger / 'data/accounts.json', {'commodities': [], 'accounts': {}})
        self.jj('commit', '-m', 'Fresh fictional ledger')
        self.jj('bookmark', 'set', 'main', '-r', '@-')

    def description(self):
        return self.jj('log', '-r', '@-', '--no-graph', '-T', 'description')

    def test_about_and_opening_balances_are_two_verified_commits(self):
        with self.client(worker=False) as client:
            self.assertEqual(client.get('/api/setup').json(), dict(fresh=True, accounts=[]))
            response = client.post('/api/setup/about', json=dict(name='Jordan Lee', currency='USD'))
            self.assertEqual(response.status_code, 200, response.text)
            manifest = read_json(self.ledger / 'ledger.json')
            self.assertEqual((manifest['name'], manifest['defaultCurrency']), ('Jordan Lee', 'USD'))
            self.assertIn('Set name and main currency in first run', self.description())
            self.assertTrue(clean(self.ledger))
            unchanged = client.post('/api/setup/about', json=dict(name='Jordan Lee', currency='USD')).json()
            self.assertTrue(unchanged['unchanged'])
            today = date.today().isoformat()
            rows = [dict(kind='bank', label='Alder Checking', currency='USD', balance='1840.25'),
                    dict(kind='bank', label='Meadow Savings', currency='EUR', balance='4650'),
                    dict(kind='card', label='Lumen Card', currency='USD', balance='337.50'),
                    dict(kind='person', label='Casey Morgan', currency='USD', balance='-75'),
                    dict(kind='cash', label='Cash', currency='USD', balance='0')]
            response = client.post('/api/setup/opening', json=dict(start=today, accounts=rows))
            self.assertEqual(response.status_code, 200, response.text)
            self.assertFalse(response.json()['data_stale'], response.json().get('refresh_error'))
            self.assertIn('Record opening balances for 5 accounts in first run', self.description())
            self.assertTrue(clean(self.ledger))
            documents = load(self.ledger)
            declared = documents[self.ledger / 'data/accounts.json']
            self.assertEqual(declared['accounts']['Assets:Bank:Meadow Savings'], dict(kind='bank', currency='EUR', label='Meadow Savings', aliases=['meadow savings'], statement=None))
            self.assertEqual(declared['accounts']['Assets:People:Casey Morgan']['kind'], 'person')
            self.assertIn('Assets:Cash', declared['accounts'])
            self.assertIn('Equity:Opening Balances', declared['accounts'])
            self.assertEqual(sorted(declared['commodities']), ['EUR', 'USD'])
            opened = {e['postings'][0]['account']: e['postings'][0] for e in entries(documents)}
            self.assertEqual(opened['Liabilities:Credit Cards:Lumen Card']['assert'], dict(amount='-337.50', commodity='USD'))
            self.assertEqual(opened['Assets:Bank:Meadow Savings']['assert'], dict(amount='4650.00', commodity='EUR'))
            self.assertEqual(opened['Assets:People:Casey Morgan']['amount'], '-75.00')
            self.assertTrue(all(e['date'] == today for e in entries(documents)))
            self.assertEqual(read_json(self.ledger / 'ledger.json')['startDate'], today)
            # Setup is over once entries exist: every first-run write refuses.
            self.assertEqual(client.get('/api/setup').json()['fresh'], False)
            for path, body in [('about', dict(name='Other', currency='USD')), ('opening', dict(start=today, accounts=rows[:1])),
                               ('accounts', dict(kind='bank', label='Other', currency='USD'))]:
                refused = client.post('/api/setup/' + path, json=body)
                self.assertEqual(refused.status_code, 409, refused.text)
                self.assertIn('setup is finished', refused.json()['detail'])

    def test_refused_openings_leave_the_ledger_unchanged(self):
        today = date.today()
        good = dict(kind='bank', label='Alder Checking', currency='USD', balance='10.00')
        before = self.bytes()
        with self.client(worker=False) as client:
            for start, rows, message in [
                ((today + timedelta(days=3)).isoformat(), [good], 'future'),
                ('not-a-date', [good], 'calendar date'),
                (today.isoformat(), [], 'one and a hundred'),
                (today.isoformat(), [{**good, 'balance': '10.001'}], 'decimal places'),
                (today.isoformat(), [{**good, 'balance': 'ten'}], 'not an amount'),
                (today.isoformat(), [{**good, 'balance': ''}], 'Enter a balance'),
                (today.isoformat(), [good, {**good, 'kind': 'card'}], 'Two accounts'),
                (today.isoformat(), [{**good, 'label': ' '}], 'name for every account'),
                (today.isoformat(), [{**good, 'currency': 'ZZZ'}], 'supported currency'),
                (today.isoformat(), [{**good, 'kind': 'shop'}], 'kind'),
                (today.isoformat(), [{**good, 'extra': 1}], 'kind, name'),
            ]:
                response = client.post('/api/setup/opening', json=dict(start=start, accounts=rows))
                self.assertEqual(response.status_code, 409, (rows, response.text))
                self.assertIn(message, response.json()['detail'])
            for body in [dict(name='', currency='USD'), dict(name='Jordan\nLee', currency='USD'), dict(name='Jordan', currency='usd')]:
                self.assertEqual(client.post('/api/setup/about', json=body).status_code, 409)
        self.assertEqual(self.bytes(), before)
        self.assertTrue(clean(self.ledger))

    def test_verification_failure_rolls_back_every_file(self):
        before = self.bytes()
        import subprocess
        from first_run import save_opening
        with patch('currency_settings.subprocess.run', return_value=subprocess.CompletedProcess([], 1, '', 'injected failure')):
            with self.assertRaisesRegex(RuntimeError, 'failed verification'):
                save_opening(self.settings, date.today().isoformat(), [dict(kind='bank', label='Alder Checking', currency='USD', balance='1.00')])
        self.assertEqual(self.bytes(), before)
        self.assertTrue(clean(self.ledger))

    def test_accounts_declared_earlier_are_opened_without_declaring_them_again(self):
        import setup_ledger
        changes, account = setup_ledger.account_changes(self.ledger, kind='bank', label='Alder Checking', currency='USD', aliases=['checking'])
        for path, value in changes.items():
            dump(path, value)
        self.jj('commit', '-m', 'Account declared from the command line')
        with self.client(worker=False) as client:
            self.assertEqual(client.get('/api/setup').json(), dict(fresh=True, accounts=[account]))
            mismatch = client.post('/api/setup/opening', json=dict(start=date.today().isoformat(), accounts=[dict(kind='bank', label='Alder Checking', currency='EUR', balance='5')]))
            self.assertEqual(mismatch.status_code, 409)
            self.assertIn('already a bank account in USD', mismatch.json()['detail'])
            response = client.post('/api/setup/opening', json=dict(start=date.today().isoformat(), accounts=[dict(kind='bank', label='Alder Checking', currency='USD', balance='5')]))
            self.assertEqual(response.status_code, 200, response.text)
        declared = read_json(self.ledger / 'data/accounts.json')['accounts'][account]
        self.assertEqual(declared['aliases'], ['checking'])

    def work(self, client, rounds=20):
        sessions = client.app.state.statements
        for _ in range(rounds):
            if not sessions.work(threading.Event()):
                return

    def upload(self, client, data, name):
        response = client.post('/api/statements', files={'file': (name, data, 'application/octet-stream')})
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()['id']

    def test_import_creates_accounts_reads_files_and_applies_each_statement_in_order(self):
        january = card_pdf('USD', '2026-01-01', '2026-01-28', 0, [('2026-01-03', 'Paper Kite Market', '12.40'), ('2026-01-09', 'Copper Finch Cafe', '8.60')], 'LJ')
        february = card_pdf('USD', '2026-01-29', '2026-02-28', 21.00, [('2026-02-04', 'Lantern Printworks', '30.00')], 'LF')
        travel = card_pdf('EUR', '2026-01-01', '2026-01-28', 0, [('2026-01-05', 'Juniper Table', '46.80')], 'WT')
        with self.client(worker=False) as client:
            later, earlier, euro = (self.upload(client, february, 'lumen-february.pdf'), self.upload(client, january, 'lumen-january.pdf'),
                                    self.upload(client, travel, 'willow-travel.pdf'))
            self.work(client)
            states = {i: client.get(f'/api/statements/{i}').json() for i in (later, earlier, euro)}
            # No account exists yet, so each file waits with its printed currency.
            self.assertEqual({s['state'] for s in states.values()}, {'currency_mismatch'})
            self.assertEqual(states[euro]['file_currency'], 'EUR')
            refused = client.post('/api/setup/accounts', json=dict(kind='wallet', label='Lumen Card', currency='USD'))
            self.assertEqual(refused.status_code, 409)
            card = client.post('/api/setup/accounts', json=dict(kind='card', label='Lumen Card', currency='USD'))
            self.assertEqual(card.status_code, 201, card.text)
            card = card.json()['account']
            self.assertEqual(card, 'Liabilities:Credit Cards:Lumen Card')
            self.assertIn('Add Lumen Card account (USD) in first run', self.description())
            again = client.post('/api/setup/accounts', json=dict(kind='card', label='Lumen Card', currency='USD'))
            self.assertIn('already exists', again.json()['detail'])
            # A mismatching assignment never starts a reading.
            wrong = client.post(f'/api/setup/statements/{euro}/account', json=dict(account=card))
            self.assertEqual(wrong.status_code, 409)
            travel_account = client.post('/api/setup/accounts', json=dict(kind='card', label='Willow Travel', currency='EUR')).json()['account']
            for identity, account in ((later, card), (earlier, card), (euro, travel_account)):
                response = client.post(f'/api/setup/statements/{identity}/account', json=dict(account=account))
                self.assertEqual(response.status_code, 200, response.text)
            self.work(client)
            found = {i: client.get(f'/api/statements/{i}').json() for i in (later, earlier, euro)}
            self.assertEqual({s['state'] for s in found.values()}, {'found'}, [s.get('error') for s in found.values()])
            self.assertTrue(all(s['plan']['items'] and all(i['needs_user'] for i in s['plan']['items']) for s in found.values()))
            # An unready file cannot be imported.
            pending = self.upload(client, january, 'not-ready.pdf')
            self.assertEqual(client.post('/api/setup/import', json=dict(sessions=[earlier, pending])).status_code, 409)
            client.delete(f'/api/statements/{pending}')
            queued = client.post('/api/setup/import', json=dict(sessions=[later, earlier, euro]))
            self.assertEqual(queued.status_code, 202, queued.text)
            self.assertEqual([s['id'] for s in queued.json()['sessions']][0], earlier, 'Statements are applied in period order')
            self.work(client)
            applied = {i: client.get(f'/api/statements/{i}').json() for i in (later, earlier, euro)}
            self.assertEqual({s['state'] for s in applied.values()}, {'applied'}, [s.get('error') for s in applied.values()])
            self.assertEqual(client.get('/api/setup').json()['fresh'], False)
            review = client.get('/api/review').json()
            self.assertEqual(review['count'], 4)
        documents = load(self.ledger)
        rows = [e for e in entries(documents) if e['status'] == 'pending']
        self.assertEqual(len(rows), 4)
        self.assertTrue(all(any(p['account'] == 'Expenses:Uncategorized' for p in e['postings']) for e in rows))
        filed = documents[self.ledger / 'data/card-statements.json']
        self.assertEqual(sorted(s['end'] for s in filed if s['account'] == card), ['2026-01-28', '2026-02-28'])
        audit = [r for r in records(self.ledger) if r['kind'] == 'statement']
        self.assertEqual(len(audit), 3)
        self.assertTrue(all(d.get('by') == 'first run' and d['action'] == 'pending' for r in audit for d in r['decisions']))
        self.assertTrue(clean(self.ledger))

    def test_agent_reading_in_another_currency_waits_and_is_reused_for_the_right_account(self):
        self.set_answers(answer('EUR'))
        with self.client(worker=False) as client:
            identity = self.upload(client, file_bytes('EUR', 'csv'), 'travel-export.csv')
            self.work(client)
            session = client.get(f'/api/statements/{identity}').json()
            self.assertEqual(session['state'], 'account_needed')
            usd = client.post('/api/setup/accounts', json=dict(kind='bank', label='Alder Checking', currency='USD')).json()['account']
            self.assertEqual(client.post(f'/api/setup/statements/{identity}/account', json=dict(account=usd)).status_code, 200)
            self.work(client)
            session = client.get(f'/api/statements/{identity}').json()
            self.assertEqual((session['state'], session.get('file_currency')), ('currency_mismatch', 'EUR'), session.get('error'))
            self.assertEqual(len(self.calls()), 1)
            eur = client.post('/api/setup/accounts', json=dict(kind='bank', label='Willow Travel', currency='EUR')).json()['account']
            self.assertEqual(client.post(f'/api/setup/statements/{identity}/account', json=dict(account=eur)).status_code, 200)
            self.work(client)
            session = client.get(f'/api/statements/{identity}').json()
            self.assertEqual(session['state'], 'found', session.get('error'))
            self.assertEqual(session['statement']['account'], eur)
            self.assertTrue(session['agent_verified'])
            self.assertEqual(len(self.calls()), 1, 'The checked answer is reused rather than asking the agent again')
            # Moving a read file to another account reads it again.
            other = client.post('/api/setup/accounts', json=dict(kind='bank', label='Birch Travel', currency='EUR')).json()['account']
            self.assertEqual(client.post(f'/api/setup/statements/{identity}/account', json=dict(account=other)).json()['state'], 'reading')
            self.work(client)
            session = client.get(f'/api/statements/{identity}').json()
            self.assertEqual((session['state'], session['statement']['account']), ('found', other))
            self.assertEqual(len(self.calls()), 1)
            self.assertEqual(client.post('/api/setup/import', json=dict(sessions=[identity])).status_code, 202)
            self.work(client)
            self.assertEqual(client.get(f'/api/statements/{identity}').json()['state'], 'applied')
        self.assertTrue(clean(self.ledger))

    def test_replanning_never_asks_the_agent(self):
        self.set_answers(answer('USD'))
        with self.client(worker=False) as client:
            account = client.post('/api/setup/accounts', json=dict(kind='bank', label='Alder Checking', currency='USD')).json()['account']
            identity = self.upload(client, file_bytes('USD', 'csv'), 'alder-export.csv')
            self.work(client)
            client.post(f'/api/setup/statements/{identity}/account', json=dict(account=account))
            self.work(client)
            self.assertEqual(client.get(f'/api/statements/{identity}').json()['state'], 'found')
            # Another write changes the ledger before Finish: the checklist is rebuilt from the saved answer.
            client.post('/api/setup/accounts', json=dict(kind='card', label='Lumen Card', currency='USD'))
            self.assertEqual(client.post('/api/setup/import', json=dict(sessions=[identity])).status_code, 202)
            with patch('agent.run', side_effect=AssertionError('agent asked again')) as run:
                self.work(client)
                run.assert_not_called()
            self.assertEqual(client.get(f'/api/statements/{identity}').json()['state'], 'applied')
            self.assertEqual(len(self.calls()), 1)
