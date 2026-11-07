"""Conventional account kind inference used by setup, migration and proposals."""

def is_balance_sheet(account):
    return account.split(':', 1)[0] not in ('Income', 'Expenses', 'Equity')


def infer_kind(account):
    if {'Accounts Payable', 'Accounts Receivable'} & set(account.split(':')):
        return 'person'
    for prefix, kind in (
        ('Assets:Bank:', 'bank'), ('Liabilities:Credit Cards:', 'card'),
        ('Assets:Cash', 'cash'), ('Assets:Wallets:', 'wallet'),
        ('Liabilities:Loans:', 'loan'), ('Assets:Investments:', 'investment'),
        ('Assets:Reimbursements:', 'person'), ('Liabilities:Payable:', 'person'),
    ):
        if account.startswith(prefix) and (prefix.endswith(':') or account == prefix or account.startswith(prefix + ':')):
            return kind
    return None


def empty(account, kind=None, currency=None):
    """Format 2 metadata for an account, written in full: what its root needs, with empty values."""
    root = account.split(':', 1)[0]
    if root in ('Income', 'Equity'):
        return {}
    if root == 'Expenses':
        return {'category': None}
    return {'kind': kind, 'currency': currency, 'label': None, 'aliases': [], 'statement': None}


def complete(account, metadata):
    """Metadata in full: an older or partial value with the empty values format 2 writes."""
    full = empty(account)
    for key in full:
        if key in metadata:
            full[key] = metadata[key]
    if full.get('statement'):
        full['statement'] = {'reader': full['statement']['reader'], 'identifier': full['statement'].get('identifier'),
                             'options': full['statement'].get('options')}
    return full
