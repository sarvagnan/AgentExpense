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
from store import commodities, commodity_directive, dump, read_json


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


def current_settings(settings, account=None):
    """Committed metadata for an uncertain-response retry; never expose a candidate."""
    from ledger_manifest import read_manifest
    with ledger_lock(settings.ledger):
        if not clean(settings.ledger):
            raise ValueError('The ledger has uncommitted changes; finish them before checking the saved settings')
        manifest = read_manifest(settings.ledger)
        result = dict(name=manifest['name'], currency=manifest['defaultCurrency'],
                      commit_id=jj(settings.ledger, 'log', '-r', '@-', '--no-graph', '-T', 'commit_id'))
        if account:
            details = account_details(settings, account)
            result.update(account=account, account_currency=details['currency'])
        return result


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


def update_settings(settings, get_changes, result, description, operation_id=None, request_hash=None):
    from save_timing import operation
    with operation('settings'):
        return _update_settings(settings, get_changes, result, description, operation_id, request_hash)


def _update_settings(settings, get_changes, result, description, operation_id=None, request_hash=None):
    records = SettingsRecords(settings.ledger)
    with ledger_lock(settings.ledger) as fd:
        for folder in records.root.iterdir():
            if (folder / 'transaction.json').exists():
                draft = json.loads((folder / 'record.json').read_text())
                Transaction(settings, records, draft).recover()
        if operation_id:
            path=records.folder(operation_id)/'record.json'
            if path.exists():
                previous=json.loads(path.read_text())
                if previous.get('request_hash')!=request_hash:
                    raise ValueError('This setup request changed. Check the saved accounts before starting again.')
                if previous.get('committed_result'):
                    return refresh(settings,previous['committed_result'])
        if not clean(settings.ledger):
            raise ValueError('The ledger has uncommitted changes; finish them before changing settings')
        changes = get_changes()
        draft = dict(id=operation_id or secrets.token_hex(16))
        if operation_id: draft['request_hash']=request_hash
        records.put(draft)
        transaction = Transaction(settings, records, draft)
        journal = transaction.snapshot([*changes, settings.ledger / 'expense_ledger.ledger'])
        try:
            for path, value in changes.items():
                dump(path, value)
            journal['result'] = result
            transaction.save_journal(journal)
            message = f"{description}\n\n{transaction.marker}"
            from save_timing import make_commit
            run = make_commit(settings.application, {**os.environ, 'LEDGER_DIR': str(settings.ledger), LOCK_FD_ENV: str(fd), 'EXPENSES_COMMIT_MESSAGE': message}, fd)
            if run.returncode:
                raise RuntimeError('Settings change failed verification: ' + (run.stdout + run.stderr)[-4000:])
            result['commit_id'] = run.stdout.strip()
            draft['committed_result'] = result
            records.put(draft)
            transaction.finish_commit(run)
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
        declared['accounts'][account] = dict(kind=kind, currency=currency, label=name, aliases=[name.lower()], statement=None)
        if currency not in commodities(declared):
            declared['commodities'].append(commodity_directive(currency))
        return {path: declared}

    return update_settings(settings, changes, dict(account=account, currency=currency), f'Add {name} account ({currency})')


def setup_operation(settings, identity):
    """Read or recover an identified opening transaction without resubmitting it."""
    records=SettingsRecords(settings.ledger)
    with ledger_lock(settings.ledger):
        path=records.folder(identity)/'record.json'
        if not path.exists(): return None
        draft=json.loads(path.read_text())
        if 'request_hash' not in draft: return None
        transaction=Transaction(settings,records,draft)
        if transaction.journal.exists(): transaction.recover()
        return draft.get('committed_result')
