"""Locked, recoverable settings writes through the ledger's verified commit path."""
import json
import os
import secrets
import re
import subprocess
from pathlib import Path
import sys
from settings import APPLICATION
from transactions import Transaction, clean, jj, refresh
from ledger_lock import ledger_lock, LOCK_FD_ENV
sys.path.insert(0, str(APPLICATION / 'scripts/ledger'))
# The shared module uses a distinct name to avoid importing this API module again.
from account_currency import currency_changes
from store import dump, read_json


class SettingsRecords:
    def __init__(self, root):
        self.root = root / 'drafts/settings'
        if self.root.is_symlink() or self.root.parent.is_symlink():
            raise ValueError('Settings records must remain inside the ledger drafts directory')
        self.root.mkdir(parents=True, exist_ok=True)

    def folder(self, identity):
        if not re.fullmatch(r'[0-9a-f]{32}', identity):
            raise ValueError('Invalid settings record identity')
        folder = self.root / identity
        if folder.is_symlink():
            raise ValueError('Settings record must not be a symlink')
        return folder

    def put(self, draft):
        folder = self.folder(draft['id'])
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / 'record.json'
        temporary = path.with_suffix('.tmp')
        with temporary.open('w') as handle:
            json.dump(draft, handle, ensure_ascii=False, allow_nan=False)
            handle.flush()
            os.fsync(handle.fileno())
        temporary.replace(path)


def update_currency(settings, code, account=None):
    return update_settings(settings, lambda: currency_changes(settings.ledger, code, account),
                           dict(currency=code, account=account),
                           f"Change {'account ' + account if account else 'default'} currency to {code}")


def update_name(settings, name):
    name = name.strip()
    if not name or any(char in name for char in '\r\n'):
        raise ValueError('Enter a name on one line')

    def changes():
        manifest = read_json(settings.ledger / 'ledger.json')
        manifest['name'] = name
        return {settings.ledger / 'ledger.json': manifest}

    return update_settings(settings, changes, dict(name=name), 'Change ledger name')


def account_details(settings, account):
    from store import load, entries
    documents = load(settings.ledger)
    metadata = documents[settings.ledger / 'data/accounts.json']['accounts'].get(account)
    if not metadata or not metadata.get('currency'):
        raise ValueError('Choose a declared balance-sheet account')
    reason = None
    try:
        # Use the write path's exact opening/forecast/statement rules, without writing.
        currency_changes(settings.ledger, metadata['currency'], account)
    except ValueError as exc:
        reason = str(exc)
    from decimal import Decimal
    opening = sum((Decimal(p['amount']) for entry in entries(documents) for p in entry['postings']
                   if p['account'] == account and p['commodity'] == metadata['currency']), Decimal(0))
    return dict(account=account, **metadata, currencyEditable=reason is None, reason=reason,
                openingBalance=format(opening, 'f') if reason is None else None)


def update_settings(settings, get_changes, result, description):
    records = SettingsRecords(settings.ledger)
    with ledger_lock(settings.ledger) as fd:
        for folder in records.root.iterdir():
            if (folder / 'transaction.json').exists():
                draft = json.loads((folder / 'record.json').read_text())
                Transaction(settings, records, draft).recover()
        if not clean(settings.ledger):
            raise ValueError('The ledger has uncommitted changes; finish them before changing settings')
        changes = get_changes()
        draft = dict(id=secrets.token_hex(16))
        records.put(draft)
        transaction = Transaction(settings, records, draft)
        journal = transaction.snapshot([*changes, settings.ledger / 'expense_ledger.ledger'])
        try:
            for path, value in changes.items():
                dump(path, value)
            journal['result'] = result
            transaction.save_journal(journal)
            message = f"{description}\n\n{transaction.marker}"
            run = subprocess.run(['make', 'commit'], cwd=settings.application,
                                 env={**os.environ, 'LEDGER_DIR': str(settings.ledger), LOCK_FD_ENV: str(fd), 'EXPENSES_COMMIT_MESSAGE': message},
                                 pass_fds=(fd,), capture_output=True, text=True)
            if run.returncode:
                raise RuntimeError('Settings change failed verification: ' + (run.stdout + run.stderr)[-4000:])
            result['commit_id'] = jj(settings.ledger, 'log', '-r', '@-', '--no-graph', '-T', 'commit_id')
            draft['committed_result'] = result
            records.put(draft)
            transaction.journal.unlink()
        except BaseException:
            transaction.rollback(journal)
            raise
    return refresh(settings, result)


def create_account(settings, kind, name, currency):
    from transactions import reject_invisible
    reject_invisible(name)
    name = name.strip()
    if not name or ':' in name or len(name) > 100:
        raise ValueError('Enter an account name without a colon')
    if currency not in json.loads((APPLICATION / 'schema/currencies.json').read_text()):
        raise ValueError('Choose a supported currency')
    parents = dict(bank='Assets:Bank', card='Liabilities:Credit Cards', cash='Assets:Cash',
                   wallet='Assets:Wallet', loan='Liabilities:Loans', investment='Assets:Investments', person='Assets:People')
    if kind not in parents:
        raise ValueError('Choose an account kind')
    account = parents[kind] + ':' + name

    def changes():
        path = settings.ledger / 'data/accounts.json'
        declared = read_json(path)
        if account in declared['accounts']:
            raise ValueError('An account with this name already exists')
        declared['accounts'][account] = dict(kind=kind, label=name, currency=currency, aliases=[name.lower()])
        if currency not in declared['commodities']:
            declared['commodities'].append(currency)
        return {path: declared}

    return update_settings(settings, changes, dict(account=account, currency=currency), f'Add {name} account ({currency})')
