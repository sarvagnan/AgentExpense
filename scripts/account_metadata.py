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
