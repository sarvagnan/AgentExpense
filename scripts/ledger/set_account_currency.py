"""Relabel an unused account and its opening balances under the ledger lock."""
import argparse
from pathlib import Path
from account_currency import currency_changes
from ledger_lock import ledger_lock
from store import ROOT, dump, load


def main(default_only=False):
    parser = argparse.ArgumentParser(description=__doc__)
    if not default_only:
        parser.add_argument('account')
    parser.add_argument('code')
    parser.add_argument('--root', type=Path, default=ROOT)
    args = parser.parse_args()
    root = args.root.resolve()
    with ledger_lock(root):
        changes = currency_changes(root, args.code, None if default_only else args.account)
        before = {path: path.read_bytes() for path in changes}
        try:
            for path, value in changes.items():
                dump(path, value)
            load(root)
        except BaseException:
            for path, value in before.items():
                path.write_bytes(value)
            raise
    print('Updated default currency' if default_only else 'Relabelled account and opening balances')


if __name__ == '__main__':
    main()
