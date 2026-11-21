"""Fixed fictional inputs shared by the offline goldens and the opt-in prompt comparison."""
from copy import deepcopy
from dataclasses import replace
import json
from pathlib import Path
import shutil

from corrections import wire_entry
from ledger_manifest import CURRENT_FORMAT
from settings import Settings
from store import dump
from support import entry, metadata, posting
from test_agent import pdf_bytes


GOLDENS = Path(__file__).with_name('prompts')
STATEMENT_ID = 'Prompt-case-private-identifier'


def cases(work, settings=None):
    ledger = work / 'ledger'
    settings = settings or Settings(ledger=ledger)
    (ledger / 'data/expenses').mkdir(parents=True)
    (ledger / 'Bills').mkdir()
    dump(ledger / 'ledger.json', dict(format=CURRENT_FORMAT, id='00000000-0000-4000-8000-000000000006',
                                     name='Prompt cases', defaultCurrency='INR', extensions={'fuel': {'ask': True}}))
    (ledger / 'AGENTS.md').write_text(
        '# Fictional ledger\n\n## Rules for entries\n\n'
        '- Advances for Dev Sen debit Assets:Reimbursements:Dev Sen.\n\n'
        '## Terminal notes\n\nTerminal-only marker.\n'
    )
    accounts = {}
    for name, label, kind, currency in (
        ('Assets:Bank:Nila Current', 'Nila Current', 'bank', 'INR'),
        ('Assets:Bank:Willow Travel', 'Willow Travel', 'bank', 'EUR'),
        ('Liabilities:Credit Cards:Mosaic Card', 'Mosaic Card', 'card', 'INR'),
        ('Assets:Cash', 'Cash', 'cash', 'INR'),
        ('Assets:Wallets:Shopping Wallet', 'Shopping Wallet', 'wallet', 'INR'),
        ('Assets:Reimbursements:Dev Sen', 'Dev Sen', 'person', 'INR'),
        ('Assets:Investments:Valley Fund', 'Valley Fund', 'investment', 'VALLEY'),
        ('Assets:Points:Fern', 'Fern Points', 'points', 'FERNPTS'),
    ):
        accounts[name] = metadata(name, kind=kind, label=label, currency=currency, aliases=[label.lower()])
    card = accounts['Liabilities:Credit Cards:Mosaic Card']
    card['statement'] = {'reader': 'example-card-pdf', 'identifier': STATEMENT_ID, 'options': None}
    accounts.update({'Expenses:Food': {'category': None}, 'Income:Rewards': {}})
    dump(ledger / 'data/accounts.json', {'accounts': accounts, 'commodities': ['INR', 'EUR', 'FERNPTS', 'VALLEY']})
    duplicate = entry('0123456789abcdef', '2026-11-06', 'Copper Finch Café', [
        posting('Expenses:Food', '59.00'), posting('Assets:Cash', '-59.00')],
        references={'reference': 'CF-001', 'order-id': []})
    dump(ledger / 'data/expenses/2026.json', [duplicate])
    dump(ledger / 'data/forecast.json', [])
    dump(ledger / 'data/card-statements.json', [])

    saved = '2026-11-06T13:05:00+05:30'
    base = dict(saved_at=saved, client_time=saved, client_zone='Asia/Kolkata', files=[], questions=[], check_errors=[])
    new = replace(settings, ledger=ledger, old_ledger=work / 'absent-history')
    off = work / 'ledger-without-fuel'
    off.mkdir()
    shutil.copytree(ledger / 'data', off / 'data')
    (off / 'data/expenses/2026.json').unlink()
    shutil.copyfile(ledger / 'AGENTS.md', off / 'AGENTS.md')
    manifest = json.loads((ledger / 'ledger.json').read_text())
    (off / 'ledger.json').write_text(json.dumps({**manifest, 'extensions': {}}))
    folder = work / 'draft'
    (folder / 'files').mkdir(parents=True)
    pdf = pdf_bytes(2)
    (folder / 'files/upload.pdf').write_bytes(pdf)
    receipt = ledger / 'Bills/prompt-case.pdf'
    receipt.write_bytes(pdf)
    old = work / 'older.ledger'
    old.write_text('2020-01-01 * Lark Café\n    Expenses:Food  100 INR\n    Assets:Cash  -100 INR\n')

    lunch = 'Copper Finch Café lunch 18.60 cash'
    drafts = {
        'ready': dict(text=lunch, pieces={'description': 'Copper Finch Café', 'amount': '18.60'}),
        'question': dict(text='Copper Finch Café lunch 18.60'),
        'currency-expense': dict(text='Copper Finch Café lunch 18.60 EUR paid by Mosaic Card'),
        'currency-transfer': dict(text='Sent 2000 INR from Nila Current to Willow Travel'),
        'not-expense': dict(text='Here is the Mosaic Card statement'),
        'duplicate': dict(text='Copper Finch Café breakfast 59.00 cash, receipt CF-001'),
        'wallet': dict(text='Loaded 500 to Shopping Wallet from Nila Current'),
        'person': dict(text='Paid 400 cash for Dev Sen'),
        'investment': dict(text='Bought 10 Valley Fund units for 500 from Nila Current'),
        'cashback': dict(text='25 cash reward credited to Nila Current'),
        'points-earned': dict(text='Paid 500 by Mosaic Card and earned 25 Fern Points'),
        'points-spent': dict(text='Paid 500 Fern Points worth 125 INR for lunch'),
        'points-expired': dict(text='25 Fern Points expired'),
        'fuel-on': dict(text='30 litres fuel 3070 cash, trip meter 420 km'),
        'fuel-off': dict(text='30 litres fuel 3070 cash, trip meter 420 km'),
        'balance-refusal': dict(
            text='Copper Finch Café lunch 500 by Mosaic Card yesterday',
            check_errors=['Not saved: a confirmed balance would change'],
            refusal={'kind': 'confirmed_balance', 'account': 'Liabilities:Credit Cards:Mosaic Card',
                     'date': '2026-11-05', 'entry_id': '1234567890abcdef', 'asserted': '-500.00'}),
        'answered': dict(text='Copper Finch Café lunch 18.60',
                         questions=[{'question': 'Which account paid?', 'answer': 'Cash'}]),
        'older-ledger': dict(text='Lark Café lunch 18.60 cash like last time'),
        'pdf': dict(text=lunch, files=[{'name': 'upload.pdf', 'original_name': 'Lunch.pdf'}]),
        'ledger-fix': dict(text='', ledger_fix_task={
            'kind': 'assertion', 'title': 'A balance does not match',
            'summary': 'The confirmed cash balance differs by 12.00 INR.',
            'details': 'hledger: The amount differs by 12.00 INR.\n    ; id: 1234567890abcdef', 'guess': True,
            'form': wire_entry(new, entry('0123456789abcdef', '2026-11-05', 'Copper Finch Café', [
                posting('Expenses:Food', '18.60'), posting('Assets:Cash', '-18.60')]))}),
        'receipt': dict(text='', receipt_task={'sources': ['Bills/prompt-case.pdf']}),
        'statement': dict(text='', statement_task=True, statement_accounts={'Mosaic Card': card},
                          files=[{'name': 'upload.pdf', 'original_name': 'Statement.pdf'}]),
    }
    (ledger / 'file.csv').write_text('Period start,2026-11-01\nPeriod end,2026-11-30\nCurrency,INR\n'
                                      'Opening balance,0.00\nClosing balance,-18.60\nDebits,18.60\nCredits,0.00\n'
                                      'Date,Description,Debit,Credit\n2026-11-04,Copper Finch Café,18.60,\n')
    drafts['cited-statement'] = dict(text='', statement_reading={
        'path': str(ledger / 'file.csv'), 'account': card, 'locale': 'en-IN'})
    for name, change in drafts.items():
        actual = replace(new, ledger=off) if name == 'fuel-off' else replace(new, old_ledger=old) if name == 'older-ledger' else new
        case = work / name
        case.mkdir()
        yield name, actual, deepcopy({**base, **change}), folder, case
