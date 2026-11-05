"""The first-run screens: a new ledger's name, accounts and opening balances.

Every write is one locked, verified commit through the settings transaction
(`currency_settings.update_settings`). Account names and opening balances come from
`scripts/ledger/setup_ledger.py`, the same code as `make add-account` and
`make opening-balances`. Statements use the ordinary statement pipeline.
"""
from datetime import date, timedelta
import json
import sys

from settings import APPLICATION
from store import entries, load, read_json
from currency_settings import update_settings
from transactions import reject_invisible

sys.path.insert(0, str(APPLICATION / 'scripts/ledger'))
import setup_ledger  # noqa: E402

FIRST_RUN_KINDS = setup_ledger.KINDS
STATEMENT_KINDS = ('bank', 'card')


def is_fresh(root, documents=None):
    """Setup is open until the ledger holds an entry or a filed statement."""
    documents = load(root) if documents is None else documents
    return not entries(documents) and not documents[root / 'data/card-statements.json']


def require_fresh(root):
    if not is_fresh(root):
        raise ValueError('This ledger already has entries, so setup is finished. Change settings in Settings.')


def status(settings):
    documents = load(settings.ledger)
    accounts = documents[settings.ledger / 'data/accounts.json']['accounts']
    return dict(fresh=is_fresh(settings.ledger, documents),
                accounts=[name for name, meta in accounts.items() if meta.get('kind')])


def _currency(code):
    if code not in json.loads((APPLICATION / 'schema/currencies.json').read_text()):
        raise ValueError('Choose a supported currency.')
    return code


def _name(value, label='Enter your name on one line.'):
    if not isinstance(value, str):
        raise ValueError(label)
    reject_invisible(value, 'name')
    value = value.strip()
    if not value or len(value) > 200:
        raise ValueError(label)
    return value


def save_about(settings, name, currency):
    name, currency = _name(name), _currency(currency)

    def changes():
        require_fresh(settings.ledger)
        manifest = read_json(settings.ledger / 'ledger.json')
        if manifest.get('name') == name and manifest.get('defaultCurrency') == currency:
            return {}
        manifest.update(name=name, defaultCurrency=currency)
        return {settings.ledger / 'ledger.json': manifest}

    manifest = read_json(settings.ledger / 'ledger.json')
    if manifest.get('name') == name and manifest.get('defaultCurrency') == currency:
        require_fresh(settings.ledger)
        return dict(name=name, currency=currency, unchanged=True)
    return update_settings(settings, changes, dict(name=name, currency=currency), 'Set name and main currency in first run')


def add_account(settings, kind, label, currency):
    """One bank or card account for a statement file, in the file's currency."""
    if kind not in STATEMENT_KINDS:
        raise ValueError('Choose Bank or Card for a statement account.')
    label, currency = _name(label, 'Enter an account name on one line.'), _currency(currency)
    result = {}

    def changes():
        require_fresh(settings.ledger)
        if setup_ledger.account_name(kind, label) in read_json(settings.ledger / 'data/accounts.json')['accounts']:
            raise ValueError(f'An account called {label} already exists. Choose it, or use another name.')
        changed, account = setup_ledger.account_changes(settings.ledger, kind=kind, label=label, currency=currency, aliases=[label])
        result.update(account=account, currency=currency, label=label)
        return changed

    return update_settings(settings, changes, result, f'Add {label} account ({currency}) in first run')


def save_opening(settings, start, accounts, today=None):
    """Declare every account and its opening balance on `start` (the browser's date)."""
    try:
        day = date.fromisoformat(start)
    except (TypeError, ValueError):
        raise ValueError('The start date must be a calendar date.') from None
    if day > (today or date.today()) + timedelta(days=1):
        raise ValueError('The start date is in the future. Check the date on this device.')
    if not isinstance(accounts, list) or not 1 <= len(accounts) <= 100:
        raise ValueError('Add between one and a hundred accounts.')
    rows, labels = [], set()
    for row in accounts:
        if not isinstance(row, dict) or set(row) - {'kind', 'label', 'currency', 'balance'}:
            raise ValueError('Each account needs a kind, name, currency and balance.')
        if row.get('kind') not in FIRST_RUN_KINDS:
            raise ValueError('Choose a kind for every account.')
        label = _name(row.get('label'), 'Enter a name for every account.')
        if label.casefold() in labels:
            raise ValueError(f'Two accounts are called {label}. Give each a different name.')
        labels.add(label.casefold())
        balance = row.get('balance')
        if not isinstance(balance, str) or not balance.strip():
            raise ValueError(f'Enter a balance for {label}.')
        rows.append(dict(kind=row['kind'], label=label, currency=_currency(row.get('currency')), balance=balance.strip()))
    result = dict(start=start, accounts=[])

    def changes():
        require_fresh(settings.ledger)
        changed = setup_ledger.first_run_changes(settings.ledger, start, rows)
        declared = changed[settings.ledger / 'data/accounts.json']['accounts']
        result['accounts'] = [name for name, meta in declared.items() if meta.get('kind')]
        return changed

    return update_settings(settings, changes, result, f'Record opening balances for {len(rows)} account{"s" if len(rows) != 1 else ""} in first run')
