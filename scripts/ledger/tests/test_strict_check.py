"""The JSON strict check agrees with hledger's strict check.

Every fixture and generated large ledgers pass both checkers. A mutation suite starts
from a valid generated ledger, breaks it one way per check kind, and requires both
checkers to refuse it for the same reason: the JSON side is ``store.load`` (the models
and the checks across documents) then ``strict_check.check``; the hledger side is
``hledger check -s ordereddates`` on the journal rendered in the store's own order,
then one forecast run per rule template, as ``check_store`` ran it before the JSON
check replaced it. Further mutations break each other ``check_store`` check and require
its message. Without hledger, the agreement tests run the JSON side only and say so.

The hledger version the suite last passed with is HLEDGER_PASSED; update it when the
suite passes on a newer hledger.
"""
from contextlib import redirect_stdout
from copy import deepcopy
from datetime import date, timedelta
import csv
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

APPLICATION = Path(__file__).resolve().parents[3]
sys.path[:0] = [str(APPLICATION / 'scripts/ledger'), str(APPLICATION / 'scripts/fixtures')]
import generate_ledger
from generate_ledger import forecast_templates, render, render_entry
from store import canonical_text, load, read_json, store_paths
from strict_check import StrictCheckError, check as strict_check, journal_order
import check_store

HLEDGER_PASSED = 'hledger 1.52.4, mac-aarch64'
FIXTURES = APPLICATION / 'fixtures/ledgers'
REASONS = ('unbalanced', 'implicit-conversion', 'assertion', 'account', 'commodity', 'ordereddates', 'parseable')


def hledger_binary():
    return os.environ.get('HLEDGER_BIN') or shutil.which('hledger')


def json_reason(message):
    """The reason a JSON-side failure names."""
    for text, reason in (('no implicit conversion', 'implicit-conversion'), ('do not balance exactly', 'unbalanced'),
                         ('Balance assertion failed', 'assertion'), ('undeclared account', 'account'),
                         ('undeclared commodity', 'commodity'), ('dates out of order', 'ordereddates'),
                         ('posting date', 'parseable'), ('unrecognized price directive', 'parseable'),
                         ('Account name', 'parseable'), ('Commodity directive', 'parseable'), ('price date', 'parseable'),
                         ('quote the commodity', 'parseable'), ('a price is a decimal', 'parseable'),
                         ('a total cost needs a non-zero amount', 'cost'), ('cost.amount', 'cost')):
        if text in message:
            return reason
    return 'other: ' + message


def hledger_reason(stderr):
    """The reason an hledger failure names, from its own wording."""
    for text, reason in (('Automatic commodity conversion is not enabled', 'implicit-conversion'),
                         ('transaction is unbalanced', 'unbalanced'), ('Balance assertion failed', 'assertion'),
                         ('Strict account checking', 'account'), ('Strict commodity checking', 'commodity'),
                         ('Ordered dates checking', 'ordereddates'), ('expecting', 'parseable'), ('unexpected', 'parseable'),
                         ('Please include a decimal point', 'parseable'), ('not a valid date', 'parseable'),
                         ('hledger misread', 'parseable')):
        if text in stderr:
            return reason
    return 'other: ' + stderr


def json_side(root):
    """None when the JSON strict gate passes, otherwise the failure message."""
    try:
        strict_check(load(root), root)
    except ValueError as error:
        return str(error)
    return None


def raw_documents(root):
    """The store documents as written, without any check (a mutation may be invalid)."""
    return {path: read_json(path) for path in store_paths(root)}


def store_order_journal(root, documents):
    """The journal with entries in the store's own order, which ordereddates sees."""
    with patch.object(generate_ledger, 'entries', lambda docs: list(journal_order(docs))):
        return render(documents, root)


def forecast_failure(binary, root, documents):
    """One hledger run per rule template, each in its own forecast window (check_store's former check)."""
    declarations = documents[root / 'data/accounts.json']
    prefix = [*('commodity ' + c for c in declarations['commodities']), *('account ' + a for a in declarations['accounts']), '']
    for rule in documents[root / 'data/forecast.json']:
        for template in forecast_templates(rule):
            start = template['schedule']['from']
            end = (date.fromisoformat(start) + timedelta(days=1)).isoformat()
            journal = '\n'.join([*prefix, *render_entry(template)]) + '\n'
            run = subprocess.run([binary, '-f', '-', 'check', '-s', 'ordereddates', f'--forecast={start}..{end}'],
                                 input=journal, text=True, capture_output=True)
            if run.returncode:
                return run.stderr
    return None


def hledger_side(binary, root):
    """None when hledger's strict checks pass, otherwise its error."""
    documents = raw_documents(root)
    journal = root / 'expense_ledger.ledger'
    journal.write_text(store_order_journal(root, documents))
    run = subprocess.run([binary, '-f', str(journal), 'check', '-s', 'ordereddates'], text=True, capture_output=True)
    if run.returncode:
        return run.stderr
    return forecast_failure(binary, root, documents) or misread(binary, journal, root, documents)


def misread(binary, journal, root, documents):
    """hledger passed, but read something else than the store says: account names or balances.

    Our journal must declare and use exactly the store's account names, and hledger's balance of
    each account in each commodity must equal the exact sum of the store's postings."""
    declared = set(documents[root / 'data/accounts.json']['accounts'])
    for flag in ('--declared', '--used'):
        names = set(subprocess.run([binary, '-f', str(journal), 'accounts', flag], text=True, capture_output=True,
                                   check=True).stdout.splitlines())
        if not names <= declared or flag == '--declared' and names != declared:
            return f'hledger misread account names: {sorted(names ^ declared)[:3]}'
    from decimal import Decimal
    ours = {}
    for path in sorted(p for p in documents if p.parent.name == 'expenses'):
        for entry in documents[path]:
            for posting in entry['postings']:
                key = (posting['account'], posting['commodity'])
                ours[key] = ours.get(key, Decimal(0)) + Decimal(posting['amount'])
    ours = {key: value for key, value in ours.items() if value}
    rows = json.loads(subprocess.run([binary, '-f', str(journal), 'bal', '--flat', '-N', '-O', 'json'], text=True,
                                     capture_output=True, check=True).stdout)[0]
    theirs = {}
    for name, _, _, amounts in rows:
        # An amount with a cost is listed apart from the same commodity without one: add them up.
        for amount in amounts:
            key = (name, amount['acommodity'])
            theirs[key] = theirs.get(key, Decimal(0)) + Decimal(amount['aquantity']['decimalMantissa']).scaleb(-amount['aquantity']['decimalPlaces'])
    theirs = {key: value for key, value in theirs.items() if value}
    if ours != theirs:
        return f'hledger misread balances: {sorted(set(ours.items()) ^ set(theirs.items()))[:2]}'
    return None


def generate(folder, *args):
    import generate_large
    with redirect_stdout(io.StringIO()):
        return generate_large.main(['--dir', str(folder), *args, '--no-repository'])


class Ledger:
    """A copy of a valid ledger whose store documents a mutation edits as plain JSON."""

    def __init__(self, root):
        self.root = root
        self.documents = {str(path.relative_to(root)): read_json(path) for path in store_paths(root)}
        self.later = []  # changes made after the documents are written

    def year(self, number=None):
        years = sorted(name for name in self.documents if name.startswith('data/expenses/'))
        return self.documents[years[-1] if number is None else f'data/expenses/{number}.json']

    def entries(self):
        return [e for name in sorted(self.documents) if name.startswith('data/expenses/') for e in self.documents[name]]

    def accounts(self):
        return self.documents['data/accounts.json']['accounts']

    def plain(self, skip=0):
        """An expense entry with two postings in one commodity, no cost, assertion or statement."""
        found = [e for e in self.entries() if len(e['postings']) == 2 and not e['statement']
                 and all(not p['cost'] and not p['assert'] for p in e['postings']) and e['postings'][0]['account'].startswith('Expenses:')
                 and len({p['commodity'] for p in e['postings']}) == 1 and e['postings'][0]['commodity'] == 'INR']
        return found[skip]

    def assertion(self):
        """The first entry holding a balance assertion, its posting, and its file."""
        for name in sorted(self.documents):
            if name.startswith('data/expenses/'):
                for index, entry in enumerate(self.documents[name]):
                    for posting in entry['postings']:
                        if posting['assert']:
                            return name, index, entry, posting
        raise AssertionError('no assertion in the base ledger')

    def rule(self, **wanted):
        return next(r for r in self.documents['data/forecast.json'] if all(r[k] == v for k, v in wanted.items())
                    and len(r['postings']) == 2 and not r['bill'] and all(not p['cost'] for p in r['postings']))

    def expense_accounts(self):
        return [name for name in self.accounts() if name.startswith('Expenses:')][:2]

    def add_last(self, entry):
        """Append an entry on the last day of the last year, so the store stays in date order."""
        rows = self.year()
        entry['date'] = rows[-1]['date']
        rows.append(entry)

    def write(self):
        for name, value in self.documents.items():
            (self.root / name).write_text(canonical_text(value))
        for change in self.later:
            change()


def new_entry(identity, day, postings, **fields):
    value = {'id': identity, 'date': day, 'status': 'confirmed', 'description': 'Fictional mutation', 'evidence': [],
             'statement': None, 'references': None, 'classification': None, 'original-amount': None, 'reimbursement': None,
             'excluded-from-estimate': None, 'extensions': {'fuel': None}, 'rule': None, 'possible-duplicate-of': [],
             'notes': [], 'postings': postings}
    value.update(fields)
    return value


def posting(account, amount, commodity='INR', cost=None, assertion=None, notes=None):
    return {'account': account, 'amount': amount, 'commodity': commodity, 'cost': cost, 'assert': assertion, 'notes': notes or []}


def bump(text, delta='0.01'):
    from decimal import Decimal
    value = format(Decimal(text) + Decimal(delta), 'f')
    return value if '.' in value else value + '.00'


# One mutation per hledger check kind: (name, the reason both checkers must give, mutate(ledger)).
def _unbalanced(ledger):
    e = ledger.plain(); e['postings'][0]['amount'] = bump(e['postings'][0]['amount'])


def _implicit(ledger):
    e = ledger.plain(); e['postings'][0]['commodity'] = 'USD'


def _negative_cost(ledger):
    one, two = ledger.expense_accounts()
    ledger.add_last(new_entry('f' * 15 + '1', None, [posting(one, '1.00', 'USD', cost={'amount': '-10.00', 'commodity': 'INR', 'per': 'total'}),
                                                     posting(two, '-10.00')]))


def _zero_quantity_cost(ledger):
    one, two = ledger.expense_accounts()
    ledger.add_last(new_entry('f' * 15 + '1', None, [posting(one, '0.00', 'USD', cost={'amount': '10.00', 'commodity': 'INR', 'per': 'total'}),
                                                     posting(two, '0.00')]))


def _assertion(ledger):
    *_, p = ledger.assertion(); p['assert']['amount'] = bump(p['assert']['amount'], '1')


def _same_day(ledger, *, before=False):
    """Move 5.00 out and back on the assertion's day; the assertion expects the moment between."""
    name, index, entry, p = ledger.assertion()
    expense = ledger.expense_accounts()[0]
    out = new_entry('f' * 15 + '1', entry['date'], [posting(expense, '5.00'), posting(p['account'], '-5.00')])
    back = new_entry('f' * 15 + '2', entry['date'], [posting(expense, '-5.00'), posting(p['account'], '5.00')])
    p['assert']['amount'] = bump(p['assert']['amount'], '-5')
    rows = ledger.documents[name]
    rows[index:index + 1] = [out, entry, back] if before else [entry, out, back]


def _subaccount(ledger, *, counted=True):
    """A posting to a subaccount just before the assertion; hledger's '=' excludes subaccounts."""
    name, index, entry, p = ledger.assertion()
    sub = p['account'] + ':Mutation Pocket'
    ledger.accounts()[sub] = deepcopy(ledger.accounts()[p['account']])
    ledger.accounts()[sub]['label'] = None
    ledger.accounts()[sub]['aliases'] = []
    ledger.accounts()[sub]['statement'] = None
    expense = ledger.expense_accounts()[0]
    ledger.documents[name].insert(index, new_entry('f' * 15 + '1', entry['date'], [posting(expense, '5.00'), posting(sub, '-5.00')]))
    if counted:
        p['assert']['amount'] = bump(p['assert']['amount'], '-5')


def _account(ledger):
    e = ledger.plain(); e['postings'][0]['account'] = 'Expenses:Undeclared Mutation'


def _commodity(ledger):
    one, two = ledger.expense_accounts()
    ledger.add_last(new_entry('f' * 15 + '1', None, [posting(one, '1.00', 'EUR'), posting(two, '-1.00', 'EUR')]))


def _cost_commodity(ledger):
    one, two = ledger.expense_accounts()
    ledger.add_last(new_entry('f' * 15 + '1', None, [posting(one, '1.00', 'USD', cost={'amount': '10.00', 'commodity': 'EUR', 'per': 'total'}),
                                                     posting(two, '-10.00', 'EUR')]))


def _order(ledger):
    rows = ledger.year()
    index = next(i for i in range(len(rows) - 1) if rows[i]['date'] < rows[i + 1]['date'])
    rows[index], rows[index + 1] = rows[index + 1], rows[index]


def _posting_date(ledger):
    e = ledger.plain(); e['postings'][1]['notes'] = ['cleared later, date: soon']


def _rule_unbalanced(ledger):
    r = ledger.rule(amount='fixed'); r['postings'][0]['amount'] = bump(r['postings'][0]['amount'])
    ledger.documents['data/forecast.json'].insert(0, ledger.documents['data/forecast.json'].pop(ledger.documents['data/forecast.json'].index(r)))


def _rule_implicit(ledger):
    r = ledger.rule(amount='fixed'); r['postings'][0]['commodity'] = 'USD'


def _rule_account(ledger):
    r = ledger.rule(amount='fixed'); r['postings'][0]['account'] = 'Expenses:Undeclared Mutation'


def _paused_rule_commodity(ledger):
    one, two = ledger.expense_accounts()
    r = ledger.rule(amount='fixed'); r['lifecycle'] = 'paused'
    r['postings'] = [posting(one, '1.00', 'EUR'), posting(two, '-1.00', 'EUR')]


def _price_commodity(ledger):
    path = ledger.root / 'data/prices/mutual-funds.prices'
    path.write_text(path.read_text() + 'P 2026-10-31 "OTHERFUND" 10.00 INR\n')


def _price_line(ledger):
    path = ledger.root / 'data/prices/mutual-funds.prices'
    path.write_text(path.read_text() + 'P 2026-10-31 "BENCHFUND" ten INR\n')


def _declare(ledger, name):
    root = name.split(':')[0]
    ledger.accounts()[name] = {'category': None} if root == 'Expenses' else {} if root in ('Income', 'Equity') else {
        'kind': 'cash', 'currency': 'INR', 'label': None, 'aliases': [], 'statement': None}


def _account_named(*names):
    """Declare the accounts and move 5.00 from the last to the first (or between two expenses)."""
    def mutate(ledger):
        for name in names:
            _declare(ledger, name)
        other = names[1] if len(names) > 1 else ledger.expense_accounts()[0]
        ledger.add_last(new_entry('f' * 15 + '1', None, [posting(names[0], '5.00'), posting(other, '-5.00')]))
    return mutate


def _directive(old, new):
    def mutate(ledger):
        directives = ledger.documents['data/accounts.json']['commodities']
        if old:
            directives[directives.index(old)] = new
        else:
            directives.append(new)
    return mutate


def _price(line, *declare):
    def mutate(ledger):
        ledger.documents['data/accounts.json']['commodities'].extend(declare)
        path = ledger.root / 'data/prices/mutual-funds.prices'
        path.write_text(path.read_text() + line + '\n')
    return mutate


def _note(text):
    def mutate(ledger):
        ledger.plain()['postings'][1]['notes'] = [text]
    return mutate


# (name, the JSON reason, [the hledger reasons when they name the same fault differently,] mutate)
MUTATIONS = [
    ('an unbalanced entry', 'unbalanced', _unbalanced),
    ('an implicit conversion', 'implicit-conversion', _implicit),
    # The models refuse these shapes outright; hledger counts them as unbalanced.
    ('a negative total cost', 'cost', {'implicit-conversion'}, _negative_cost),
    ('a zero quantity at a total cost', 'cost', {'implicit-conversion'}, _zero_quantity_cost),
    ('a failing assertion', 'assertion', _assertion),
    ('an assertion that holds only before a same-day entry written after it', 'assertion', _same_day),
    ('an assertion that counts a subaccount posting', 'assertion', _subaccount),
    ('an undeclared account', 'account', _account),
    ('an undeclared commodity', 'commodity', _commodity),
    ('an undeclared cost commodity', 'commodity', _cost_commodity),
    ('dates out of order', 'ordereddates', _order),
    ('a posting note with an invalid posting date', 'parseable', _posting_date),
    ('an unbalanced recurring rule', 'unbalanced', _rule_unbalanced),
    ('a recurring rule with an implicit conversion', 'implicit-conversion', _rule_implicit),
    ('a recurring rule with an undeclared account', 'account', _rule_account),
    ('a paused rule with an undeclared commodity', 'commodity', _paused_rule_commodity),
    ('a price in an undeclared commodity', 'commodity', _price_commodity),
    ('an unreadable price line', 'parseable', _price_line),
    # Account names hledger refuses or reads as another name (review round).
    ('an account name with a leading (', 'parseable', _account_named('(Petty')),
    ('an account name with a leading [', 'parseable', _account_named('[Petty')),
    ('an account name with a leading * beside the same name', 'parseable', _account_named('*Petty', 'Petty')),
    ('an account name with a leading ! beside the same name', 'parseable', _account_named('!Petty', 'Petty')),
    ('an account name with a leading ; (the posting becomes a comment)', 'parseable', {'unbalanced'}, _account_named(';Petty')),
    ('two no-break spaces in an account name', 'parseable', _account_named('Expenses:Pe\u00a0\u00a0tty')),
    ('a no-break space and a space in an account name', 'parseable', _account_named('Expenses:Pe\u00a0 tty')),
    ('a thin space in an account name', 'parseable', _account_named('Expenses:Pe\u2009tty')),
    ('a tab in an account name', 'parseable', _account_named('Expenses:Pe\ttty')),
    ('a next-line character in an account name', 'parseable', {'parseable', 'account', 'unbalanced'}, _account_named('Expenses:Pe\u0085tty')),
    # Commodity directives hledger refuses or reads with "," as the decimal mark.
    ('a directive with "," as the decimal mark', 'parseable', _directive('INR', '1.000,00 INR')),
    ('a directive whose only mark is ","', 'parseable', _directive('INR', '1,000 INR')),
    ('a directive without a decimal mark', 'parseable', _directive('INR', '1000 INR')),
    ('an unquoted symbol with a digit', 'parseable', _directive(None, 'AB1')),
    # Price lines hledger refuses.
    ('a price with a basic-form date', 'parseable', _price('P 20261031 "BENCHFUND" 10.00 INR')),
    ('a NaN price', 'parseable', _price('P 2026-10-31 "BENCHFUND" NaN INR')),
    ('a price for an unquoted symbol with a digit', 'parseable', _price('P 2026-10-31 FUND1 10.00 INR', '"FUND1"')),
    ('a posting note with an impossible bracketed date', 'parseable', _note('paid [2/30]')),
]
# Changes both checkers accept: same-day order as written, and subaccounts left out.
CONTROLS = [
    ('the same-day entries written before the assertion', lambda ledger: _same_day(ledger, before=True)),
    ('a subaccount posting the assertion does not count', lambda ledger: _subaccount(ledger, counted=False)),
]


class Base(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix='expense-strict-')
        cls.folder = Path(cls.temp.name)
        cls.base = generate(cls.folder / 'base', '--entries', '600', '--seed', '7')

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def copy(self, name):
        target = self.folder / name
        # Evidence is shared read-only through a link (no mutation writes or removes a file in it).
        shutil.copytree(self.base, target, ignore=shutil.ignore_patterns('Bills'))
        (target / 'Bills').symlink_to(self.base / 'Bills', target_is_directory=True)
        self.addCleanup(shutil.rmtree, target, True)
        return Ledger(target)


class AgreementTests(Base):
    def setUp(self):
        self.binary = hledger_binary()

    def hledger(self, root):
        if not self.binary:
            return None
        return hledger_side(self.binary, root)

    def assert_both_pass(self, root):
        self.assertIsNone(json_side(root))
        with redirect_stdout(io.StringIO()):
            check_store.check(root)
        if self.binary:
            self.assertIsNone(self.hledger(root))

    def test_the_hledger_version_is_recorded(self):
        if not self.binary:
            self.skipTest('hledger is not installed: the agreement tests ran the JSON side only')
        version = subprocess.run([self.binary, '--version'], text=True, capture_output=True).stdout.strip()
        if version != HLEDGER_PASSED:
            print(f'\nNOTE: the strict-check agreement suite last passed with {HLEDGER_PASSED}; this is {version}. '
                  'Update HLEDGER_PASSED when it passes.', file=sys.stderr)

    def test_every_fixture_passes_both_and_prints_in_store_order(self):
        if not FIXTURES.is_dir():
            self.skipTest('No fixtures in this application copy')
        for fixture in sorted(FIXTURES.iterdir()):
            with self.subTest(fixture=fixture.name):
                root = self.folder / ('fixture-' + fixture.name)
                shutil.copytree(fixture, root, ignore=shutil.ignore_patterns('expense_ledger.ledger'))
                self.addCleanup(shutil.rmtree, root, True)
                documents = load(root)
                # ordereddates sees the store's order; the journal's date sort keeps it.
                self.assertEqual(store_order_journal(root, raw_documents(root)), render(documents, root))
                self.assert_both_pass(root)

    def test_generated_large_ledgers_pass_both(self):
        for seed, size in ((1, []), (2, []), (3, ['--entries', '600'])):
            with self.subTest(seed=seed):
                root = generate(self.folder / f'large-{seed}', '--seed', str(seed), *size)
                self.addCleanup(shutil.rmtree, root, True)
                self.assertEqual(store_order_journal(root, raw_documents(root)), render(load(root), root))
                self.assert_both_pass(root)

    def test_each_mutation_is_refused_by_both_for_the_same_reason(self):
        if not self.binary:
            print('\nNOTE: hledger is not installed; the mutation suite ran the JSON side only.', file=sys.stderr)
        for number, (name, reason, *rest) in enumerate(MUTATIONS):
            mutate, accepted = rest[-1], rest[0] if len(rest) == 2 else {reason}
            with self.subTest(mutation=name):
                ledger = self.copy(f'mutation-{number}')
                mutate(ledger)
                ledger.write()
                message = json_side(ledger.root)
                self.assertIsNotNone(message, 'the JSON check accepted it')
                self.assertEqual(json_reason(message), reason, message)
                if self.binary:
                    failure = self.hledger(ledger.root)
                    self.assertIsNotNone(failure, 'hledger accepted it')
                    self.assertIn(hledger_reason(failure), accepted, failure)

    def test_both_accept_same_day_order_as_written_and_ignore_subaccounts(self):
        for number, (name, change) in enumerate(CONTROLS):
            with self.subTest(control=name):
                ledger = self.copy(f'control-{number}')
                change(ledger)
                ledger.write()
                self.assertIsNone(json_side(ledger.root))
                if self.binary:
                    self.assertIsNone(self.hledger(ledger.root))

    def test_the_periods_the_journal_prints_each_forecast_on_their_start_day(self):
        """Every period form the strict check accepts generates exactly one transaction on its start day in hledger."""
        if not self.binary:
            self.skipTest('hledger is not installed')
        from recurring import new_rule
        schedules = [dict(every='month', interval=1, **{'from': '2026-01-31', 'anchor-day': None}),
                     dict(every='month', interval=1, **{'from': '2026-02-28', 'anchor-day': 31}),
                     dict(every='month', interval=1, **{'from': '2026-04-30', 'anchor-day': 31}),
                     dict(every='month', interval=1, **{'from': '2027-02-28', 'anchor-day': 29}),
                     dict(every='month', interval=2, **{'from': '2026-02-28', 'anchor-day': 31}),
                     dict(every='month', interval=3, **{'from': '2026-01-15', 'anchor-day': None}),
                     dict(every='month', interval=13, **{'from': '2026-01-31', 'anchor-day': None}),
                     dict(every='year', interval=1, **{'from': '2028-02-29', 'anchor-day': None}),
                     dict(every='year', interval=1, **{'from': '2027-02-28', 'anchor-day': 29}),
                     dict(every='year', interval=3, **{'from': '2027-02-28', 'anchor-day': 29}),
                     dict(every='year', interval=2, **{'from': '2026-03-31', 'anchor-day': None})]
        prefix = 'commodity INR\naccount Expenses:Test\naccount Assets:Bank\n\n'
        for schedule in schedules:
            rule = new_rule(id='0' * 16, date=schedule['from'], description='Fictional', schedule=schedule,
                            postings=[posting('Expenses:Test', '1.00'), posting('Assets:Bank', '-1.00')])
            for template in forecast_templates(rule):
                start = template['schedule']['from']
                end = (date.fromisoformat(start) + timedelta(days=1)).isoformat()
                with self.subTest(period=generate_ledger.period(template['schedule'])):
                    self.assertRegex(generate_ledger.period(template['schedule']), __import__('strict_check').PERIOD)
                    run = subprocess.run([self.binary, '-f', '-', 'print', f'--forecast={start}..{end}', '-O', 'csv'],
                                         input=prefix + '\n'.join(render_entry(template)) + '\n', text=True, capture_output=True, check=True)
                    days = {row['date'] for row in csv.DictReader(io.StringIO(run.stdout))}
                    self.assertEqual(days, {start})


def run_hledger(binary, journal, *args):
    return subprocess.run([binary, '-f', '-', *args], input=journal, text=True, capture_output=True)


class HledgerDecidesTests(unittest.TestCase):
    """hledger itself decides each case: the JSON check refuses what hledger refuses or reads differently."""

    def setUp(self):
        self.binary = hledger_binary()
        if not self.binary:
            self.skipTest('hledger is not installed')

    def test_account_names(self):
        from strict_check import account_problem
        names = ['(Petty', '[Petty', 'Petty)', '*Petty', '!Petty', '* Petty', ';Petty', '#Petty', '%Petty', '-Petty', '1Petty',
                 '~Petty', '=Petty', '@Petty', 'Assets:Pe;tty', 'Assets:Pe=tty', 'Assets:Pe@tty', 'Assets:Pe tty', 'Assets:Pe  tty',
                 'Assets:Pe\u00a0tty', 'Assets:Pe\u00a0 tty', 'Assets:Pe\u00a0\u00a0tty', 'Assets:Pe\u2009tty', 'Assets:Pe \u2009tty',
                 'Assets:Pe\u3000\u3000tty', 'Assets:Pe\ttty', 'Assets:P\u0085etty', 'Assets:P\ufeffetty', 'Assets:Petty ',
                 'Assets:Pétty', 'Assets:Petty (old)']
        for name in names:
            with self.subTest(name=name):
                journal = f'commodity INR\naccount Assets:Bank\naccount {name}\n\n2026-01-01 * a\n    Assets:Bank  -10.00 INR\n    {name}  10.00 INR\n'
                checked = run_hledger(self.binary, journal, 'check', '-s')
                used = run_hledger(self.binary, journal, 'accounts', '--used').stdout.splitlines()
                declared = run_hledger(self.binary, journal, 'accounts', '--declared').stdout.splitlines()
                faithful = checked.returncode == 0 and name in used and name in declared
                self.assertEqual(account_problem(name) is None, faithful, (account_problem(name), checked.stderr[-200:], used))

    def test_commodity_directives(self):
        from store import commodity_symbol
        # Refused by the JSON check although hledger reads them right: digit groups made of spaces.
        stricter = {'1 000.00 INR'}
        for directive, symbol in [('INR', 'INR'), ('"INR"', 'INR'), ('AB1', None), ('"AB1"', 'AB1'), ('1000 INR', None),
                                  ('1000.00 INR', 'INR'), ('1,000.00 INR', 'INR'), ('1.000,00 INR', None), ('1,000 INR', None),
                                  ('1.000 INR', 'INR'), ('INR 1,000.00', 'INR'), ('INR1,000.00', 'INR'), ('USD1,000.00', 'USD'),
                                  ('$1,000.00', '$'), ('$', '$'), ('1 000.00 INR', 'INR'), ('1000. INR', 'INR'), ('-1,000.00 INR', 'INR'),
                                  ('INR 1000', None), ('"FUND 1" 1.0000', 'FUND 1'), ('1.0000 "FUND 1"', 'FUND 1'), ('$ 1,000.00', '$'),
                                  ('1,000.00$', '$'), ('1,00,000.00 INR', 'INR'), ('INR 1.000,00', None), ('1.000.000 INR', None),
                                  ('1,000,000 INR', None), ('INR ; a note', None)]:
            with self.subTest(directive=directive):
                written = symbol if symbol and symbol.isalpha() else json.dumps(symbol or 'X')
                journal = f'commodity {directive}\naccount A\naccount B\n\n2026-01-01 * x\n    A  1234.56 {written}\n    B  -1234.56 {written}\n'
                run = run_hledger(self.binary, journal, 'bal', 'A', '-O', 'json', '-N')
                amount = json.loads(run.stdout)[0][0][3][0] if run.returncode == 0 else None
                faithful = bool(amount) and amount['aquantity']['decimalMantissa'] == 123456 and amount['aquantity']['decimalPlaces'] == 2 \
                    and amount['acommodity'] == symbol
                try:
                    ours = commodity_symbol(directive)
                except ValueError:
                    ours = None
                if ours is not None:
                    self.assertTrue(faithful, run.stderr[-200:])
                    self.assertEqual(ours, symbol)
                else:
                    self.assertTrue(not faithful or directive in stricter, f'hledger reads {directive!r} right')

    def test_price_lines(self):
        from strict_check import price_problem
        declared = {'INR', 'FUND1', 'FUNDX'}
        # Refused by the JSON check although hledger reads them: what the price job never writes.
        stricter = {'P 2026-09-28 "FUND1" 1,000.50 INR', 'P 2026-09-28 "FUND1" 1e3 INR', 'P 2026-09-28 12:00:00 "FUND1" 10.00 INR',
                    'P 2026-09-28 "FUND1" 10.00 INR ; note', 'P 2026-09-28 "FUND1" .5 INR'}
        lines = ['P 2026-09-28 "FUND1" 10.00 INR', 'P 20260928 "FUND1" 10.00 INR', 'P 2026/09/28 "FUND1" 10.00 INR',
                 'P 2026.09.28 "FUND1" 10.00 INR', 'P 2026-9-8 "FUND1" 10.00 INR', 'P 2026-09-28 "FUND1" NaN INR',
                 'P 2026-09-28 FUND1 10.00 INR', 'P 2026-09-28 FUNDX 10.00 INR', 'P 2026-09-28 "FUND1" 10 INR',
                 'P 2026-09-28 "FUND1" INR 10.00', 'P 2026-09-28 "FUND1" -10.00 INR', 'P 2026-09-28 "FUND1" 10.00 "INR"',
                 'P 2026-09-28 "FUND1" 10.00', 'P 2026-02-30 "FUND1" 10.00 INR', 'P 26-09-28 "FUND1" 10.00 INR',
                 'P  2026-09-28  "FUND1"  10.00  INR', 'P 2026-09-28 "FUNDY" 10.00 INR', *sorted(stricter)]
        for line in lines:
            with self.subTest(line=line):
                journal = f'commodity INR\ncommodity "FUND1"\ncommodity FUNDX\naccount A\n\n{line}\n'
                accepted = run_hledger(self.binary, journal, 'check', '-s').returncode == 0
                ours = price_problem(line, declared) is None
                if ours:
                    self.assertTrue(accepted)
                else:
                    self.assertTrue(not accepted or line in stricter, f'hledger accepts {line!r}')

    def test_posting_notes(self):
        from strict_check import POSTING_DATE
        notes = ['see [1]', 'inv [12]', '[2026]', '[-]', '[.]', '[/]', '[=]', '[1=2]', '[123456]', '[ 2/3 ]', '[2/3', 'update: 2024-02-01',
                 'xdate: 2024', '(date: soon)', 'Date: soon', 'a,date: 2024-02-01', 'DATE: x', '[2/3]', '[1.5]', '[1-2]', '[12/1]', '[02-01]',
                 '[2024-02-01]', '[=2024-02-01]', '[2024-02-01=2024-02-03]', '[13/45]', '[2/30]', '[1/2/3]', '[2024-02]', '[2..3]', '[2//3]',
                 '[0/0]', 'x[2/3]y', 'date: 2024-02-01', 'date:2024-02-01', 'date2: 2024-02-01', 'paid date: soon', 'x, date: 2024-02-01']
        for note in notes:
            with self.subTest(note=note):
                journal = f'commodity INR\naccount A\naccount B\n\n2026-01-10 * x\n    A  10.00 INR\n    ; {note}\n    B  -10.00 INR\n'
                run = run_hledger(self.binary, journal, 'print', '-O', 'json')
                posting = json.loads(run.stdout)[0]['tpostings'][0] if run.returncode == 0 else None
                dated = posting is None or posting['pdate'] is not None or posting['pdate2'] is not None
                self.assertEqual(bool(POSTING_DATE.search(note)), dated)


class StricterThanHledgerTests(Base):
    """Refusals the JSON check makes where hledger may accept: each keeps the store's meaning exact."""

    def test_posting_dates_and_journal_unsafe_names_are_refused(self):
        changes = [
            ('a posting date tag', lambda l: l.plain()['postings'][1]['notes'].append('cleared on date: 2026-12-01')),
            ('a bracketed posting date', lambda l: l.plain()['postings'][1]['notes'].append('cleared [2026-12-01]')),
            ('two spaces in an account name', lambda l: l.accounts().__setitem__('Expenses:Two  Spaces', {'category': None})),
            ('a virtual-posting account name', lambda l: l.accounts().__setitem__('(Assets:Virtual)', {
                'kind': 'bank', 'currency': 'INR', 'label': None, 'aliases': [], 'statement': None})),
            ('a balance off by less than a cent after a cost', lambda l: l.add_last(new_entry('f' * 15 + '1', None, [
                posting(l.expense_accounts()[0], '1.00', 'USD', cost={'amount': '10.004', 'commodity': 'INR', 'per': 'total'}),
                posting(l.expense_accounts()[1], '-10.00')]))),
        ]
        for number, (name, change) in enumerate(changes):
            with self.subTest(change=name):
                ledger = self.copy(f'stricter-{number}')
                change(ledger)
                ledger.write()
                self.assertIsNotNone(json_side(ledger.root))


# Every other check_store check: (name, mutate(ledger), the message it keeps).
def _first_statement(ledger):
    return next(s for s in ledger.documents['data/card-statements.json'] if s['kind'] == 'card')


def _members(ledger, statement):
    return [e for e in ledger.entries() if (e['statement'] or {}).get('end') == statement['end']]


def _bank_account(ledger):
    return next(name for name, meta in ledger.accounts().items() if meta.get('kind') == 'bank')


def _card_account(ledger):
    return next(name for name, meta in ledger.accounts().items() if meta.get('kind') == 'card')


def _with_trash(ledger, **changes):
    entry = new_entry('e' * 16, '2026-01-05', [posting(ledger.expense_accounts()[0], '1.00'), posting(ledger.expense_accounts()[1], '-1.00')])
    item = {'id': 'a' * 32, 'entry': entry, 'removed-at': '2026-01-06T10:00:00+05:30', 'added-at': None, 'reason': '', 'index': 0,
            'files': [], 'kept': [], 'rule-changes': [], 'duplicate-links': {}}
    item.update(changes)
    ledger.documents['data/trash.json'] = [item]
    return item


def _trash_file(ledger, stored):
    item = _with_trash(ledger, files=[{'original': 'Bills/2026-01-05/gone.md', 'stored': stored}])
    item['entry']['evidence'] = ['Bills/2026-01-05/gone.md']


def _rewrite(path, text):
    path.write_text(text)


STORE_CHECKS = [
    ('ledger.json layout', lambda l: _rewrite(l.root / 'ledger.json', json.dumps(read_json(l.root / 'ledger.json'))), 'ledger.json is not canonical'),
    ('store file layout', lambda l: l.later.append(lambda: _rewrite(l.root / 'data/forecast.json', json.dumps(l.documents['data/forecast.json']))),
     'is not canonical'),
    ('fuel log shape', lambda l: _rewrite(l.root / 'data/fuel-log.json', '[]\n'), 'data/fuel-log.json needs an entries array'),
    ('missing evidence file', lambda l: l.plain()['evidence'].append('Bills/2026-01-01/missing-receipt.md'), 'missing or invalid evidence path'),
    ('evidence path outside Bills', lambda l: l.plain()['evidence'].append('Bills/../ledger.json'), 'missing or invalid evidence path'),
    ('duplicate ID', lambda l: l.plain(1).__setitem__('id', l.plain()['id']), 'Duplicate id'),
    ('entry in another year file', lambda l: l.year().insert(0, {**deepcopy(l.year()[0]), 'id': 'f' * 16, 'date': '2019-12-31'}),
     'does not belong in'),
    ('posting outside its account currency', lambda l: l.plain().__setitem__('postings', [
        posting(l.plain()['postings'][0]['account'], '1.00', 'USD'), posting(_bank_account(l), '-1.00', 'USD')]),
     'must use its account currency'),
    ('assertion outside its account currency', lambda l: l.assertion()[3].__setitem__('assert', {'amount': '0.00', 'commodity': 'USD'}),
     'assertion must use its account currency'),
    ('account currency undeclared', lambda l: l.accounts()[_bank_account(l)].__setitem__('currency', 'EUR'),
     'account currency is not a declared commodity'),
    ('statement start after end', lambda l: _first_statement(l).__setitem__('start', '2099-01-01'), 'statement start is after end'),
    ('statements out of end-date order', lambda l: l.documents['data/card-statements.json'].reverse(), 'statements out of end-date order'),
    ('rule anchor', lambda l: l.rule(amount='fixed')['schedule'].__setitem__('anchor-day', 3 if l.rule(amount='fixed')['date'][-2:] != '03' else 4),
     'schedule.from must agree with anchor-day'),
    ('varying rule between two expenses', lambda l: l.rule(amount='varies').__setitem__('postings', [
        posting(l.expense_accounts()[0], '5.00'), posting(l.expense_accounts()[1], '-5.00')]), 'only simple two-posting payments in one currency can vary'),
    ('rule link to no rule', lambda l: l.plain().__setitem__('rule', {'id': 'f' * 16, 'occurrence': l.plain()['date']}), 'rule link must name an existing rule'),
    ('duplicate link to no entry', lambda l: l.plain().__setitem__('possible-duplicate-of', [{'id': 'f' * 16, 'reason': 'dissimilar-name'}]),
     'duplicate link must name an existing, greater actual entry ID'),
    ('rule evidence naming no entry', lambda l: l.rule(amount='fixed').__setitem__('origin', {'kind': 'owner', 'words': None, 'entry-ids': ['f' * 16], 'came-back': False}),
     'rule evidence must name existing actual entries'),
    ('extension groups out of step', lambda l: l.plain().__setitem__('extensions', {}), 'extension groups that differ from ledger.json'),
    ('Trash entry still in the ledger', lambda l: _with_trash(l, entry=deepcopy(l.plain())), 'Trash entries must be unique and absent from the ledger'),
    ('Trash operation IDs repeated', lambda l: l.documents.__setitem__('data/trash.json', [_with_trash(l), {**deepcopy(_with_trash(l)), 'entry': {**_with_trash(l)['entry'], 'id': 'd' * 16}}]),
     'Trash operation IDs must be unique'),
    ('Trash evidence unmapped', lambda l: _with_trash(l)['entry'].__setitem__('evidence', ['Bills/2026-01-05/gone.md']), 'Trash evidence must map every Bills source exactly once'),
    ('Trash file outside its folder', lambda l: _trash_file(l, 'data/trash-files/' + 'b' * 32 + '/gone.md'), 'Trash files must stay inside their operation folder'),
    ('Trash file missing', lambda l: _trash_file(l, 'data/trash-files/' + 'a' * 32 + '/gone.md'), 'Missing or unsafe Trash file'),
    ('Trash kept evidence missing', lambda l: _with_trash(l, kept=[{'path': 'Bills/2026-01-05/gone.md', 'citations': 1}])['entry'].__setitem__(
        'evidence', ['Bills/2026-01-05/gone.md']), 'Missing or unsafe shared Trash evidence'),
    ('card statement account undeclared', lambda l: _first_statement(l).__setitem__('account', 'Liabilities:Undeclared Card'), 'undeclared statement account'),
    ('card statement on a bank account', lambda l: _first_statement(l).__setitem__('account', _bank_account(l)), 'statement account must have kind card'),
    ('duplicate card statement', lambda l: l.documents['data/card-statements.json'].insert(0, {**deepcopy(_first_statement(l)), 'id': 'f' * 16}),
     'duplicate statement for'),
    ('duplicate statement serial', lambda l: _members(l, _first_statement(l))[1]['statement'].__setitem__('serial', _members(l, _first_statement(l))[0]['statement']['serial']),
     'Duplicate serial'),
    ('statement member without a card posting', lambda l: l.plain().__setitem__('statement', {'end': _first_statement(l)['end'], 'serial': 'S-X', 'page': 1, 'source-page': None, 'source-row': None}),
     'statement.end without a card posting'),
    ('statement member of no statement', lambda l: _members(l, _first_statement(l))[0]['statement'].__setitem__('end', '2000-01-01'), 'unknown statement.end'),
    ('statement member without its PDF', lambda l: _members(l, _first_statement(l))[0].__setitem__('evidence', []), 'statement member missing its statement PDF source'),
    ('statement row count', lambda l: _first_statement(l).__setitem__('rows', _first_statement(l)['rows'] + 1), 'row count'),
    ('statement purchases', lambda l: _first_statement(l).__setitem__('purchases', bump(_first_statement(l)['purchases'])), 'purchases'),
    ('statement chain', lambda l: _first_statement(l).__setitem__('previous-balance', bump(_first_statement(l)['previous-balance'])), 'broken statement chain'),
    ('statement total due', lambda l: _first_statement(l).__setitem__('total-due', bump(_first_statement(l)['total-due'])), 'summary total-due'),
    ('card without an opening', lambda l: [e['postings'].__setitem__(slice(None), [p if not p['account'].startswith('Equity:Opening') else {**p, 'account': l.expense_accounts()[0]} for p in e['postings']])
                                           for e in l.entries() if any(p['account'] == _card_account(l) for p in e['postings'])], 'expected one opening-equity entry'),
    ('fund price coverage', lambda l: _rewrite(l.root / 'data/prices/mutual-funds.prices', '; no prices\n'), 'no price on or before first unit holding'),
    ('price directive', lambda l: _rewrite(l.root / 'data/prices/mutual-funds.prices', 'P 2020-01-01\n'), 'Unrecognized price directive'),
]


class StoreCheckMutationTests(Base):
    def test_each_check_store_check_refuses_with_its_message(self):
        for number, (name, mutate, message) in enumerate(STORE_CHECKS):
            with self.subTest(check=name):
                ledger = self.copy(f'store-{number}')
                mutate(ledger)
                ledger.write()
                with redirect_stdout(io.StringIO()), self.assertRaises(ValueError) as caught:
                    check_store.check(ledger.root)
                self.assertIn(message, str(caught.exception))



# The balance-style (bank) statement checks, on a small statement of one row (test_bank_statements' layout).
BANK_CHECKS = [
    ('totals', lambda s, rows: s.__setitem__('debits', '11.00'), 'Statement debit/credit totals differ from membership'),
    ('running balance', lambda s, rows: s['members'][0].__setitem__('balance', '91.00'), 'Statement running balance differs at row-2'),
    ('closing balance', lambda s, rows: s.__setitem__('closing-balance', '91.00'), 'Statement opening − debits + credits differs from closing balance'),
    ('continuity', lambda s, rows: s.update({'opening-balance': '101.00', 'closing-balance': '91.00'}) or s['members'][0].__setitem__('balance', None),
     'Statement does not continue from ledger opening balance'),
    ('assertion', lambda s, rows: rows[2]['postings'][0]['assert'].__setitem__('amount', '89.00') or rows[2]['postings'][0].__setitem__('amount', '0.00')
     or s.__setitem__('assertion-id', '9' * 16), 'Statement closing balance assertion is missing or differs'),
    ('missing member', lambda s, rows: s['members'][0].__setitem__('entry-id', '9' * 16), 'Missing or repeated statement member entry'),
    ('member source', lambda s, rows: rows[1].__setitem__('evidence', []), 'Statement member is missing its source'),
    ('member amount', lambda s, rows: s['members'][0].__setitem__('direction', 'Credit'), 'Statement member amount/direction differs'),
    ('row count', lambda s, rows: s.__setitem__('rows', 2), 'Statement membership row count differs'),
    ('account kind', lambda s, rows: s.__setitem__('account', 'Expenses:Test'), 'Balance-style statement needs a declared bank or card account'),
    ('debt style', lambda s, rows: s.__setitem__('source-balance-style', 'debt'), 'Debt-style statement needs a card account'),
    ('duplicate serial', lambda s, rows: s['members'].append(deepcopy(s['members'][0])) or s.__setitem__('rows', 2), 'Duplicate statement row serial'),
]


class BankStatementCheckTests(unittest.TestCase):
    def test_each_balance_style_statement_check_refuses_with_its_message(self):
        from test_bank_statements import BankMembershipTests
        for name, change, message in BANK_CHECKS:
            with self.subTest(check=name):
                fixture = BankMembershipTests('test_membership_totals_and_assertion')
                fixture.setUp()
                try:
                    (fixture.root / 'Bills').mkdir()
                    (fixture.root / 'Bills/bank.csv').write_text('Fictional statement\n')
                    with redirect_stdout(io.StringIO()):
                        fixture.documents()
                        check_store.check(fixture.root)  # the valid statement passes
                    # As written, in the models' key order.
                    summary, rows = read_json(fixture.root / 'data/card-statements.json')[0], read_json(fixture.year)
                    change(summary, rows)
                    (fixture.root / 'data/card-statements.json').write_text(canonical_text([summary]))
                    fixture.year.write_text(canonical_text(rows))
                    with redirect_stdout(io.StringIO()), self.assertRaises(ValueError) as caught:
                        check_store.check(fixture.root)
                    self.assertIn(message, str(caught.exception))
                finally:
                    fixture.doCleanups()


if __name__ == '__main__':
    unittest.main()


class AssertionRefusalTests(unittest.TestCase):
    """A broken confirmed balance is always the structured refusal, with any other problem listed beside it."""

    def setUp(self):
        from test_ledger import Fixture
        self.fixture = Fixture('setUp')
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        from store import dump
        bank = 'Assets:Bank:Savings Account'
        self.bank = bank
        rows = [new_entry('1' * 16, '2026-01-01', [posting(bank, '100.00'), posting('Equity:Opening Balances', '-100.00')], extensions={}),
                new_entry('2' * 16, '2026-01-20', [posting(bank, '0.00', assertion={'amount': '100.00', 'commodity': 'INR'})], extensions={})]
        dump(self.fixture.year, rows)

    def candidate(self, **notes):
        return new_entry(None, '2026-01-10', [posting('Expenses:Test', '30.00'), posting(self.bank, '-30.00', **notes)], extensions={})

    def test_the_refusal_lists_the_other_problem(self):
        from add_entry import add_entry
        from confirmed_balances import BalanceRefusal
        with self.assertRaises(BalanceRefusal) as caught:
            add_entry(self.candidate(notes=['cleared, date: soon']), root=self.fixture.root, dry_run=True)
        refusal = caught.exception.refusal
        self.assertEqual((refusal['asserted'], refusal['calculated']), ('100.00', '70.00'))
        self.assertEqual(len(refusal['other_problems']), 1)
        self.assertIn('posting date', refusal['other_problems'][0])
        self.assertIn('also:', refusal['detail'])
        # The strict check itself: the assertion's error, with the other problem kept.
        documents = load(self.fixture.root)
        entry = {**self.candidate(notes=['cleared, date: soon']), 'id': '3' * 16}
        documents[self.fixture.year].insert(1, entry)
        with self.assertRaises(StrictCheckError) as error:
            strict_check(documents, self.fixture.root)
        self.assertEqual((error.exception.kind, len(error.exception.problems)), ('assertions', 1))
        self.assertIsNotNone(error.exception.refusal)

    def test_without_another_problem_the_refusal_lists_none(self):
        from add_entry import add_entry
        from confirmed_balances import BalanceRefusal
        with self.assertRaises(BalanceRefusal) as caught:
            add_entry(self.candidate(), root=self.fixture.root, dry_run=True)
        self.assertEqual(caught.exception.refusal['other_problems'], [])
