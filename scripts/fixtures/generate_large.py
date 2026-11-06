"""Generate a large fictional ledger for timing runs; never commit its output.

Every person, account, merchant, amount and file here is invented. The same seed
and options always write the same store bytes. The default writes about five
years of one persona's entries (a journal of about 1.0–1.5 MB):

    uv run --with jsonschema python scripts/fixtures/generate_large.py --dir /tmp/agentexpense-large
    uv run --with jsonschema python scripts/fixtures/generate_large.py --entries 100000 --dir /tmp/agentexpense-stress

The ledger holds several accounts in two currencies, recurring rules (fixed and
varying) with linked payments, monthly card statements with explicit membership,
monthly confirmed balances, receipts as small placeholder files, audit lines,
fund prices and an investment. The last card statement periods stay unfiled, so
`make bench-save` can apply them. The folder becomes its own colocated jj
repository with one commit (``--no-repository`` skips it), and the store passes
`make verify-data`.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal, ROUND_HALF_UP
import json
import os
from pathlib import Path
import random
import shutil
import subprocess
import sys
import uuid

APPLICATION = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(APPLICATION / 'scripts'))
sys.path.insert(0, str(APPLICATION / 'scripts/ledger'))
from ledger_manifest import CURRENT_FORMAT  # noqa: E402
from store import dump  # noqa: E402

DEFAULT_ENTRIES = 4500
DEFAULT_SEED = 6
DEFAULT_END = date(2026, 10, 31)
YEARS = 5
UNFILED_STATEMENTS = 6
CENT = Decimal('0.01')

MAIN = 'Assets:Bank:Linden Current'
SAVINGS = 'Assets:Bank:Harbor Savings'
DOLLAR = 'Assets:Bank:Cobalt Dollar'
CARD = 'Liabilities:Credit Cards:Quartz Card'
CASH = 'Assets:Cash'
WALLET = 'Assets:Wallets:Transit Wallet'
FUND = 'Assets:Investments:Mutual Funds:Bench Index'
FRIEND = 'Assets:Reimbursements:Noor Halden'
CARD_IDENTIFIER = '4321'
FUND_UNITS = 'BENCHFUND'

BALANCE_ACCOUNTS = {
    MAIN: dict(kind='bank', currency='INR', label='Linden Current'),
    SAVINGS: dict(kind='bank', currency='INR', label='Harbor Savings'),
    DOLLAR: dict(kind='bank', currency='USD', label='Cobalt Dollar'),
    CARD: dict(kind='card', currency='INR', label='Quartz Card', statement=dict(reader='example-card-pdf', identifier=CARD_IDENTIFIER)),
    CASH: dict(kind='cash', currency='INR', label='Cash'),
    WALLET: dict(kind='wallet', currency='INR', label='Transit Wallet'),
    FUND: dict(kind='investment', currency=FUND_UNITS, label='Bench Index'),
    FRIEND: dict(kind='person', currency='INR', label='Noor Halden'),
}

# Everyday spending: (account, merchants, low, high, weight, payers).
EVERYDAY = [
    ('Expenses:Groceries', ['Birchwood Grocer', 'Tamarind Corner Store', 'Saltmarsh Fish Stall', 'Greenrow Vegetables'], 180, 3200, 22, 'card'),
    ('Expenses:Food:Dining & Delivery', ['Juniper Diner', 'Ember Noodle House', 'Fennel Kitchen', 'Pebble Bakery'], 90, 1800, 18, 'any'),
    ('Expenses:Food:Coffee', ['Larkspur Coffee Cart', 'Mossgate Tea Room'], 60, 420, 10, 'any'),
    ('Expenses:Transport:Taxi', ['Kestrel Cabs', 'Blue Heron Rides'], 80, 900, 8, 'wallet'),
    ('Expenses:Transport:Fuel', ['Driftwood Fuel Stop', 'Ridgeway Fuel'], 900, 3500, 5, 'card'),
    ('Expenses:Transport:Transit', ['Metro Pass Desk'], 40, 300, 4, 'wallet'),
    ('Expenses:Household', ['Hollow Oak Hardware', 'Wren Laundry', 'Clover Home Goods'], 150, 4800, 7, 'card'),
    ('Expenses:Health:Pharmacy', ['Lumen Pharmacy', 'Sagebrush Chemist'], 70, 1600, 4, 'any'),
    ('Expenses:Health:Clinic', ['Opal Dental Clinic', 'Riverbend Clinic'], 500, 6500, 1, 'card'),
    ('Expenses:Shopping:Books', ['Copperleaf Books', 'Quill and Ink Stationers'], 120, 2400, 4, 'card'),
    ('Expenses:Shopping:Clothing', ['Thistle Tailors', 'Bramble Outfitters'], 600, 7800, 3, 'card'),
    ('Expenses:Gifts', ['Marigold Florist', 'Cinder Gift House'], 250, 4200, 3, 'card'),
    ('Expenses:Entertainment', ['Nimbus Picture House', 'Foxglove Theatre'], 200, 1600, 4, 'card'),
    ('Expenses:Pets', ['Clover Pet Supplies', 'Whisker Vet Rooms'], 150, 3800, 2, 'card'),
    ('Expenses:Personal Care', ['Glasswing Salon', 'Seafoam Barbers'], 150, 1800, 3, 'any'),
]
FOREIGN = [('Expenses:Travel', ['Harbourlight Hotel', 'Skylark Rail Office', 'Cobblestone Museum Shop'], 12, 260)]
PAYERS = {'card': [(CARD, 1)], 'wallet': [(WALLET, 5), (CARD, 2)],
          'any': [(CARD, 5), (CASH, 3), (MAIN, 2), (WALLET, 1)]}

# Recurring payments: (description, account, payer, amount, every, interval, day, kind).
# kind: fixed, varies or yearly; amounts are INR unless the payer is the dollar account.
RULES = [
    ('Fernhill Court rent', 'Expenses:Housing:Rent', MAIN, '28500.00', 'month', 1, 3, 'fixed'),
    ('Aster Broadband', 'Expenses:Utilities:Internet', CARD, '999.00', 'month', 1, 6, 'fixed'),
    ('Tidewater Mobile', 'Expenses:Utilities:Phone', CARD, '549.00', 'month', 1, 9, 'fixed'),
    ('Paper Lantern Reader', 'Expenses:Subscriptions:Paper Lantern', CARD, '199.00', 'month', 1, 11, 'fixed'),
    ('Moonpetal Music', 'Expenses:Subscriptions:Moonpetal Music', CARD, '129.00', 'month', 1, 12, 'fixed'),
    ('Northwind Film Club', 'Expenses:Subscriptions:Northwind Film Club', CARD, '499.00', 'month', 1, 14, 'fixed'),
    ('Quarry Cloud Storage', 'Expenses:Subscriptions:Quarry Cloud', CARD, '75.00', 'month', 1, 15, 'fixed'),
    ('Sparrow News Digest', 'Expenses:Subscriptions:Sparrow News', CARD, '249.00', 'month', 1, 16, 'fixed'),
    ('Ironbark Gym', 'Expenses:Health:Gym', CARD, '1800.00', 'month', 1, 2, 'fixed'),
    ('Heron Lane Society dues', 'Expenses:Housing:Society Dues', MAIN, '3200.00', 'month', 1, 5, 'fixed'),
    ('Pinecrest School fees', 'Expenses:Education:School', MAIN, '42000.00', 'month', 3, 7, 'fixed'),
    ('Willowmere Language Classes', 'Expenses:Education:Classes', CARD, '2500.00', 'month', 1, 19, 'fixed'),
    ('Juniper Domestic Help', 'Expenses:Household:Help', CASH, '6000.00', 'month', 1, 1, 'fixed'),
    ('Old Mill Car Wash', 'Expenses:Transport:Car Care', CASH, '600.00', 'month', 1, 20, 'fixed'),
    ('Lighthouse Charity pledge', 'Expenses:Donations', MAIN, '1000.00', 'month', 1, 21, 'fixed'),
    ('Bluebell Water Supply', 'Expenses:Utilities:Water', MAIN, '420.00', 'month', 1, 22, 'varies'),
    ('Willow Energy', 'Expenses:Utilities:Electricity', MAIN, '2600.00', 'month', 1, 12, 'varies'),
    ('Cinderfield Gas', 'Expenses:Utilities:Gas', MAIN, '850.00', 'month', 1, 13, 'varies'),
    ('Rookery Waste Services', 'Expenses:Utilities:Waste', MAIN, '310.00', 'month', 1, 17, 'varies'),
    ('Maple Workshop tutoring', 'Expenses:Education:Tutoring', MAIN, '4000.00', 'month', 1, 18, 'varies'),
    ('Galewood Garden Care', 'Expenses:Household:Garden', CASH, '900.00', 'month', 1, 23, 'varies'),
    ('Thornbury Laundry Service', 'Expenses:Household:Laundry', MAIN, '1100.00', 'month', 1, 24, 'varies'),
    ('Kingfisher Parking', 'Expenses:Transport:Parking', CARD, '700.00', 'month', 1, 25, 'varies'),
    ('Seastone Home Insurance', 'Expenses:Insurance:Home', MAIN, '14800.00', 'year', 1, 8, 'yearly'),
    ('Seastone Car Insurance', 'Expenses:Insurance:Car', MAIN, '21500.00', 'year', 1, 9, 'yearly'),
    ('Evergreen Health Cover', 'Expenses:Insurance:Health', MAIN, '32000.00', 'year', 1, 10, 'yearly'),
    ('Arbor Vehicle Tax', 'Expenses:Taxes:Vehicle', MAIN, '4500.00', 'year', 1, 11, 'yearly'),
    ('Copperbeam Domain Renewal', 'Expenses:Subscriptions:Domain', DOLLAR, '18.00', 'year', 1, 4, 'yearly'),
    ('Slate Code Hosting', 'Expenses:Subscriptions:Code Hosting', DOLLAR, '7.00', 'month', 1, 8, 'fixed'),
    ('Mistral Design Tools', 'Expenses:Subscriptions:Design Tools', DOLLAR, '12.00', 'month', 1, 10, 'fixed'),
    ('Ledgerline Accountant', 'Expenses:Professional Fees', MAIN, '5500.00', 'month', 3, 26, 'fixed'),
    ('Cedar Archive membership', 'Expenses:Memberships:Cedar Archive', CARD, '350.00', 'month', 1, 27, 'fixed'),
]


def money(value) -> str:
    return format(Decimal(value).quantize(CENT, ROUND_HALF_UP), 'f')


class Writer:
    def __init__(self, seed: int, root: Path):
        self.rng = random.Random(seed)
        self.root = root
        self.used = set()
        self.files = {}
        self.events = []  # (date, order, entry); order keeps a stable same-day sequence

    def identity(self) -> str:
        while True:
            value = format(self.rng.getrandbits(64), '016x')
            if value not in self.used:
                self.used.add(value)
                return value

    def evidence(self, day: date, name: str, text: str) -> str:
        path = f'Bills/{day.isoformat()}/{name}'
        number = 2
        while path in self.files:
            stem, _, suffix = name.rpartition('.')
            path = f'Bills/{day.isoformat()}/{stem}-{number}.{suffix}'
            number += 1
        self.files[path] = text
        return path

    def add(self, day: date, description: str, postings: list, *, status='confirmed', order=1, **fields) -> dict:
        entry = dict(id=self.identity(), date=day.isoformat(), status=status, description=description, **fields, postings=postings)
        self.events.append((day, order, len(self.events), entry))
        return entry


def posting(account, value, commodity='INR', **extra):
    return dict(account=account, amount=value if isinstance(value, str) else money(value), commodity=commodity, **extra)


def month_day(year: int, month: int, day: int) -> date:
    from calendar import monthrange
    return date(year, month, min(day, monthrange(year, month)[1]))


def add_months(day: date, months: int, anchor: int) -> date:
    total = day.year * 12 + day.month - 1 + months
    return month_day(total // 12, total % 12 + 1, anchor)


def generate(root: Path, *, entries: int, seed: int, end: date) -> dict:
    w = Writer(seed, root)
    rng = w.rng
    start = date(end.year - YEARS, end.month, end.day) + timedelta(days=1)
    days = (end - start).days + 1

    # Opening balances, one per currency, on the first day.
    w.add(start, 'Fictional opening balances', [
        posting(MAIN, '185000.00'), posting(SAVINGS, '420000.00'), posting(CASH, '6000.00'),
        posting(WALLET, '800.00'), posting(CARD, '0.00'), posting(FRIEND, '0.00'),
        posting('Equity:Opening Balances', '-611800.00'),
        posting(DOLLAR, '2400.00', 'USD'), posting('Equity:Opening Balances', '-2400.00', 'USD')],
        order=0, sources=[w.evidence(start, 'opening-balances.md', 'Fictional opening balance confirmation.\n')])

    # Recurring rules and their linked payment history.
    rules = []
    for description, account, payer, base, every, interval, anchor, kind in RULES:
        currency = 'USD' if payer == DOLLAR else 'INR'
        rule = dict(id=w.identity(), description=description, account=account, payer=payer, currency=currency,
                    every=every, interval=interval, anchor=anchor, kind=kind, payments=[])
        first = month_day(start.year, start.month, anchor)
        if first < start:
            first = add_months(first, 1, anchor)
        if every == 'year':
            first = month_day(start.year + 1 if rng.random() < .5 else start.year, rng.randint(1, 12), anchor)
            while first < start:
                first = date(first.year + 1, first.month, first.day)
        step = interval * (12 if every == 'year' else 1)
        day, value = first, Decimal(base)
        while day <= end:
            if kind == 'varies':
                amount = (value * Decimal(rng.uniform(.75, 1.35))).quantize(CENT)
            else:
                if kind == 'yearly':
                    value = (value * Decimal('1.06')).quantize(CENT) if day != first else value
                amount = value
            entry = w.add(day, description, [posting(account, amount, currency), posting(payer, -amount, currency)],
                          rule=dict(id=rule['id'], occurrence=day.isoformat()))
            if kind != 'fixed' or rng.random() < .2:
                entry['sources'] = [w.evidence(day, f'{description.lower().replace(" ", "-")}.md', f'Fictional bill for {description}.\n')]
            rule['payments'].append(entry)
            day = add_months(day, step, anchor)
        rule['next'] = day
        rules.append(rule)

    # Monthly salary, savings, investment, interest; quarterly dollar income.
    salary = Decimal('165000.00')
    price = Decimal('42.5000')
    prices = []
    month = date(start.year, start.month, 1)
    while month <= end:
        if month.month == 4 and month > start:
            salary = (salary * Decimal('1.07')).quantize(Decimal('1.00'))
        for day, description, postings_, order in (
                (month_day(month.year, month.month, 1), 'Ashgrove Studio salary', [posting(MAIN, salary), posting('Income:Salary', -salary)], 0),
                (month_day(month.year, month.month, 4), 'Transfer to Harbor Savings', [posting(SAVINGS, '25000.00'), posting(MAIN, '-25000.00')], 1),
                (month_day(month.year, month.month, 28), 'Harbor Savings interest', [posting(SAVINGS, money(rng.uniform(900, 1900))), None], 1)):
            if start <= day <= end:
                if postings_[1] is None:
                    postings_[1] = posting('Income:Interest', '-' + postings_[0]['amount'])
                w.add(day, description, postings_, order=order)
        # Investment: units at total cost, with a monthly price.
        day = month_day(month.year, month.month, 15)
        price = (price * Decimal(rng.uniform(.97, 1.045))).quantize(Decimal('0.0001'))
        if start <= day <= end:
            prices.append(f'P {day.isoformat()} "{FUND_UNITS}" {price} INR')
            units = (Decimal('7500') / price).quantize(Decimal('0.001'))
            w.add(day, 'Bench Index monthly investment', [
                posting(FUND, format(units, 'f'), FUND_UNITS, cost=dict(amount='7500.00', commodity='INR', per='total')),
                posting(MAIN, '-7500.00')], sources=[w.evidence(day, 'bench-index-units.md', 'Fictional unit allotment.\n')])
        if month.month % 3 == 0:
            day = month_day(month.year, month.month, 20)
            if start <= day <= end:
                value = money(rng.uniform(800, 2600))
                w.add(day, 'Northgate Design freelance invoice', [posting(DOLLAR, value, 'USD'), posting('Income:Freelance', '-' + value, 'USD')],
                      reference=f'NG-{day:%Y%m}', sources=[w.evidence(day, 'northgate-invoice.md', 'Fictional invoice.\n')])
        month = add_months(month, 1, 1)

    # Everyday spending fills the remaining count, spread evenly over the days.
    fixed_count = len(w.events)
    target = max(entries - fixed_count, 0)
    weights = [item[4] for item in EVERYDAY]
    for index in range(target):
        day = start + timedelta(days=index * days // target if target else 0)
        if rng.random() < .03:
            account, merchants, low, high = FOREIGN[0]
            merchant = rng.choice(merchants)
            foreign = Decimal(rng.randint(low * 100, high * 100)) / 100
            charged = (foreign * Decimal(rng.uniform(81, 86))).quantize(CENT)
            w.add(day, f'{merchant} travel', [
                posting(account, foreign, 'USD', cost=dict(amount=money(charged), commodity='INR', per='total')),
                posting(CARD, -charged)], **{'original-amount': dict(amount=money(foreign), commodity='USD')})
            continue
        account, merchants, low, high, _, payers = rng.choices(EVERYDAY, weights)[0]
        merchant = rng.choice(merchants)
        payer = rng.choices(*zip(*PAYERS[payers]))[0]
        value = Decimal(rng.randint(low, high)) + (Decimal(rng.randint(0, 99)) / 100 if payer != CASH else 0)
        fields = {}
        if rng.random() < .3:
            fields['sources'] = [w.evidence(day, merchant.lower().replace(' ', '-') + '-receipt.md', f'Fictional receipt from {merchant}.\n')]
        if rng.random() < .1:
            fields['order-id'] = f'ORD-{rng.randint(100000, 999999)}'
        if rng.random() < .08:
            fields['notes'] = [rng.choice(['Shared with family', 'Paid at the counter', 'Weekend purchase', 'Price included delivery'])]
        if account == 'Expenses:Transport:Fuel':
            fields['fuel-filled-litres'] = money(value / Decimal('104.5'))
        status = 'pending' if day > end - timedelta(days=120) and rng.random() < .03 else 'confirmed'
        if status == 'pending':
            fields['classification'] = 'pending'
            account = 'Expenses:Uncategorized'
        if rng.random() < .04 and payer != CASH:
            share = (value / 2).quantize(CENT)
            postings_ = [posting(account, value - share), posting(FRIEND, share), posting(payer, -value)]
            fields['reimbursement-group'] = f'shared-{day:%Y%m}'
            fields['reimbursement-label'] = 'Noor Halden share'
        else:
            postings_ = [posting(account, value), posting(payer, -value)]
        w.add(day, f'{merchant}', postings_, status=status, order=1, **fields)

    events = sorted(w.events, key=lambda item: (item[0], item[1], item[2]))
    actual = [entry for _, _, _, entry in events]

    # Top up cash, the wallet and the friend account so balances stay plausible.
    actual = keep_positive(w, actual, start, end)

    # Monthly card statements on the 28th; the payment on the 10th clears the
    # previous total. The last UNFILED_STATEMENTS periods stay unfiled for Apply.
    statements = []
    ends = []
    day = month_day(start.year, start.month, 28)
    if day < start:
        day = add_months(day, 1, 28)
    while day <= end:
        ends.append(day)
        day = add_months(day, 1, 28)
    filed_ends = ends[:-UNFILED_STATEMENTS] if len(ends) > UNFILED_STATEMENTS else []
    previous_due = Decimal('0.00')
    period_start = start
    payments = []
    by_day = defaultdict(list)
    for entry in actual:
        by_day[entry['date']].append(entry)
    for statement_end in ends:
        # The previous statement's total is paid on the 10th, inside this period.
        for pay_day, value in [p for p in payments if period_start <= p[0] <= statement_end]:
            entry = dict(id=w.identity(), date=pay_day.isoformat(), status='confirmed', description='Quartz Card payment',
                         postings=[posting(CARD, value), posting(MAIN, -value)])
            by_day[entry['date']].append(entry)
        payments = [p for p in payments if p[0] > statement_end]
        members = [e for d in sorted(by_day) if period_start.isoformat() <= d <= statement_end.isoformat() for e in by_day[d]
                   if any(p['account'] == CARD for p in e['postings']) and not any(p['account'].startswith('Equity:') for p in e['postings'])]
        if statement_end in filed_ends and members:
            source = w.evidence(statement_end, f'quartz-statement-{statement_end.isoformat()}.pdf', None)
            purchases = credits = Decimal(0)
            for row, entry in enumerate(members, 1):
                value = next(Decimal(p['amount']) for p in entry['postings'] if p['account'] == CARD)
                if value < 0:
                    purchases -= value
                else:
                    credits += value
                entry['sources'] = [*entry.get('sources', []), source]
                entry['serial-number'] = f'QZ-{statement_end:%Y%m}-{row:03d}'
                entry['statement-page'] = 1 + (row - 1) // 40
                entry['statement-end'] = statement_end.isoformat()
            due = previous_due + purchases - credits
            statements.append(dict(id=w.identity(), account=CARD, start=period_start.isoformat(), end=statement_end.isoformat(), source=source,
                                   **{'previous-balance': money(previous_due), 'purchases': money(purchases), 'credits': money(credits),
                                      'total-due': money(due)}, rows=len(members)))
            previous_due = due
        elif statement_end in filed_ends:
            raise ValueError('An empty card statement period; use more entries')
        else:
            # Unfiled periods are paid the same way, from the card's own movement.
            previous_due += sum((-Decimal(p['amount']) for e in members for p in e['postings'] if p['account'] == CARD), Decimal(0))
        if previous_due > 0 and statement_end != ends[-1]:
            payments.append((add_months(statement_end, 1, 10), previous_due))
        period_start = statement_end + timedelta(days=1)
    actual = [e for d in sorted(by_day) for e in by_day[d]]

    # Confirmed balances: Harbor Savings at each month end, Cobalt Dollar each quarter.
    actual = confirmed_balances(w, actual, end)

    # Varying rules hold the median of the last three payments; every rule's
    # next occurrence follows the last payment.
    forecast = []
    for rule in rules:
        last = rule['payments'][-3:]
        value = sorted(Decimal(e['postings'][0]['amount']) for e in last)[len(last) // 2] if rule['kind'] == 'varies' else Decimal(rule['payments'][-1]['postings'][0]['amount'])
        schedule = dict(every=rule['every'], **({'interval': rule['interval']} if rule['interval'] != 1 else {}), **{'from': rule['next'].isoformat()})
        if rule['anchor'] != rule['next'].day:
            schedule['anchor-day'] = rule['anchor']
        item = dict(id=rule['id'], date=rule['next'].isoformat(), status='confirmed', description=rule['description'],
                    schedule=schedule, origin=dict(kind='owner'),
                    postings=[posting(rule['account'], value, rule['currency']), posting(rule['payer'], -value, rule['currency'])])
        if rule['kind'] == 'varies':
            item['amount'] = 'varies'
            item['notes'] = ['Amounts vary from month to month.']
        forecast.append(item)

    accounts = {**{name: dict(meta, aliases=[meta['label'].lower()]) for name, meta in BALANCE_ACCOUNTS.items()},
                'Equity:Opening Balances': {}, 'Income:Salary': {}, 'Income:Interest': {}, 'Income:Freelance': {},
                'Expenses:Uncategorized': {}}
    for account in sorted({p['account'] for e in actual for p in e['postings']} | {p['account'] for r in forecast for p in r['postings']}):
        accounts.setdefault(account, {})
    return dict(actual=actual, forecast=forecast, statements=statements, accounts=accounts, prices=prices,
                start=start, files=w.files, writer=w)


def keep_positive(w: Writer, actual: list, start: date, end: date) -> list:
    """Insert cash withdrawals, wallet top-ups and friend repayments when low."""
    result, balances = [], defaultdict(Decimal)
    floors = {CASH: (Decimal(1500), 'Linden Current cash withdrawal', Decimal(8000)),
              WALLET: (Decimal(400), 'Transit Wallet top-up', Decimal(2000))}
    for entry in actual:
        for account, (floor, description, amount) in floors.items():
            need = sum((-Decimal(p['amount']) for p in entry['postings'] if p['account'] == account), Decimal(0))
            if need > 0 and balances[account] - need < floor:
                top = amount + need
                top = top.quantize(Decimal(1)) + (0 if top == top.quantize(Decimal(1)) else 1)
                result.append(dict(id=w.identity(), date=entry['date'], status='confirmed', description=description,
                                   postings=[posting(account, top), posting(MAIN, -top)]))
                balances[account] += top
        if balances[FRIEND] > 5000:
            value = balances[FRIEND]
            result.append(dict(id=w.identity(), date=entry['date'], status='confirmed', description='Noor Halden repayment',
                               postings=[posting(MAIN, value), posting(FRIEND, -value)]))
            balances[FRIEND] = Decimal(0)
        result.append(entry)
        for p in entry['postings']:
            balances[p['account']] += Decimal(p['amount'])
    return result


def confirmed_balances(w: Writer, actual: list, end: date) -> list:
    running = defaultdict(Decimal)
    by_day = defaultdict(list)
    for entry in actual:
        by_day[entry['date']].append(entry)
    result = []
    first = date.fromisoformat(actual[0]['date'])
    day = first
    while day <= end:
        for entry in by_day.get(day.isoformat(), []):
            result.append(entry)
            for p in entry['postings']:
                if p['account'] in (SAVINGS, DOLLAR):
                    running[p['account']] += Decimal(p['amount'])
        next_day = day + timedelta(days=1)
        if next_day.day == 1:
            for account, currency, months in ((SAVINGS, 'INR', 1), (DOLLAR, 'USD', 3)):
                if day.month % months == 0:
                    label = BALANCE_ACCOUNTS[account]['label']
                    result.append(dict(id=w.identity(), date=day.isoformat(), status='confirmed', description=f'{label} confirmed balance',
                                       sources=[w.evidence(day, f'{label.lower().replace(" ", "-")}-balance.md', f'Fictional {label} balance confirmation.\n')],
                                       postings=[posting(account, '0.00', currency, **{'assert': dict(amount=money(running[account]), commodity=currency)})]))
        day = next_day
    return result


def audit_lines(actual: list, end: date) -> dict:
    """Dashboard addition records for the last six months' everyday entries."""
    lines = defaultdict(list)
    cutoff = (end - timedelta(days=183)).isoformat()
    number = 0
    for entry in actual:
        if entry['date'] < cutoff or entry.get('rule') or len(entry['postings']) < 2 or 'statement-end' in entry:
            continue
        number += 1
        identity = format(number, '032x')
        when = entry['date'] + 'T12:00:00+00:00'
        record = dict(version=1, id=identity, kind='draft', time=when, draft_id=identity, outcome='added', text=entry['description'],
                      saved_at=when, pieces=None, files=[], questions=[], runs=[], proposal=None, doubts=[], check_errors=[],
                      error=None, entry_id=entry['id'])
        lines[entry['date'][:4]].append(json.dumps(record, ensure_ascii=False, separators=(',', ':')))
    return lines


PDF_PLACEHOLDER = b'%PDF-1.4\n% Fictional placeholder statement for timing runs.\n%%EOF\n'


def write(root: Path, data: dict, *, seed: int, entries: int, end: date) -> None:
    rng = random.Random(seed + 1)
    (root / 'data/expenses').mkdir(parents=True)
    (root / 'data/prices').mkdir(parents=True)
    (root / 'data/additions').mkdir(parents=True)
    manifest = {'format': CURRENT_FORMAT, 'id': str(uuid.UUID(int=rng.getrandbits(128), version=4)),
                'name': 'Ira Castellan (large fictional)', 'defaultCurrency': 'INR', 'locale': 'en-IN',
                'modules': {'fuel': True, 'investments': {'prices': 'mutual-fund-nav'}, 'history': {'oldLedger': None}, 'weeklyReview': False},
                'review': {'storageKey': None}}
    dump(root / 'ledger.json', manifest)
    dump(root / 'data/accounts.json', dict(commodities=['INR', 'USD', FUND_UNITS], accounts=data['accounts']))
    years = defaultdict(list)
    for entry in data['actual']:
        years[entry['date'][:4]].append(entry)
    for year, rows in years.items():
        dump(root / f'data/expenses/{year}.json', rows)
    dump(root / 'data/forecast.json', data['forecast'])
    dump(root / 'data/card-statements.json', data['statements'])
    dump(root / 'data/recurring-settings.json', {'never-automatic': ['Expenses:Groceries', 'Expenses:Food:Dining & Delivery', 'Expenses:Transport:Fuel']})
    (root / 'data/fuel-log.json').write_text('{\n  "meta": {\n    "fictional": true\n  },\n  "entries": []\n}\n')
    (root / 'data/prices/mutual-funds.prices').write_text('; Invented NAVs, not an actual financial product\n' + '\n'.join(data['prices']) + '\n')
    (root / 'data/prices/fund-sources.json').write_text(json.dumps({FUND_UNITS: ['999', '999002', 'FICTIONAL0002']}, indent=2) + '\n')
    for year, lines in audit_lines(data['actual'], end).items():
        (root / f'data/additions/{year}.jsonl').write_text('\n'.join(lines) + '\n')
    for path, text in data['files'].items():
        target = root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(PDF_PLACEHOLDER if text is None else text.encode())
    # The personal-rules template; application copies in the offline tests carry templates/.
    shutil.copyfile(APPLICATION / 'templates/ledger/AGENTS.md', root / 'AGENTS.md')
    shutil.copyfile(APPLICATION / 'templates/ledger/.gitignore', root / '.gitignore')
    (root / 'README.md').write_text(
        '# Ira Castellan — large fictional ledger\n\n'
        f'Generated by `scripts/fixtures/generate_large.py --seed {seed} --entries {entries} --end {end.isoformat()}` '
        'for timing runs. Every person, account, merchant, amount and file is invented. Never commit it.\n')


def init_repository(root: Path) -> None:
    def jj(*args, cwd=root):
        subprocess.run(['jj', '--config', 'snapshot.max-new-file-size=0', '--config', 'user.name="Large Fixture"',
                        '--config', 'user.email="fixture@example.invalid"', *args], cwd=cwd, check=True,
                       stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    jj('git', 'init', '--colocate', str(root), cwd=root.parent)
    jj('-R', str(root), 'commit', '-m', 'Generated large fictional ledger')
    jj('-R', str(root), 'bookmark', 'set', 'main', '-r', '@-')


def main(argv=None) -> Path:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--dir', type=Path, help='new, empty output folder (default: /tmp/agentexpense-large-<seed>-<entries>)')
    parser.add_argument('--entries', type=int, default=DEFAULT_ENTRIES, help=f'approximate actual entries (default {DEFAULT_ENTRIES})')
    parser.add_argument('--seed', type=int, default=DEFAULT_SEED, help=f'random seed (default {DEFAULT_SEED})')
    parser.add_argument('--end', type=date.fromisoformat, default=DEFAULT_END, help=f'last ledger day (default {DEFAULT_END})')
    parser.add_argument('--no-repository', action='store_true', help='write the files only, without a jj repository')
    args = parser.parse_args(argv)
    if args.entries < 600:
        parser.error('--entries must be at least 600 for five years of statements')
    root = (args.dir or Path(f'/tmp/agentexpense-large-{args.seed}-{args.entries}')).expanduser().resolve()
    if root.is_relative_to(APPLICATION.resolve()):
        parser.error('Write generated ledgers outside the application, for example under /tmp')
    if root.exists() and any(root.iterdir()):
        parser.error(f'{root} exists and is not empty')
    root.mkdir(parents=True, exist_ok=True)
    data = generate(root, entries=args.entries, seed=args.seed, end=args.end)
    write(root, data, seed=args.seed, entries=args.entries, end=args.end)
    if not args.no_repository:
        init_repository(root)
    print(f'Wrote {len(data["actual"])} entries, {len(data["forecast"])} rules, {len(data["statements"])} filed card statements '
          f'and {len(data["files"])} evidence files to {root}')
    return root


if __name__ == '__main__':
    os.environ.setdefault('PYTHONDONTWRITEBYTECODE', '1')
    main()
