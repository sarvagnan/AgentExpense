# Private checks

`make private-checks`, run from the application root with `LEDGER_DIR` set to this
ledger, runs each check here and reports PASS or FAIL:

- `test*.py` files run with Python's `unittest`, one file at a time;
- any other executable file runs as a program and passes when it exits with 0;
- other files, like this README or helper modules, are ignored.

Each check starts in the ledger root with `LEDGER_DIR` (this ledger) and
`AGENTEXPENSE_APP` (the application root) set. Checks read the ledger; they never
change it. They are yours: they stay in this repository and never in the
application.

Example, `checks/test_accounts.py`:

```python
import json
import os
from pathlib import Path
import unittest

LEDGER = Path(os.environ['LEDGER_DIR'])


class Accounts(unittest.TestCase):
    def test_every_account_has_a_currency(self):
        accounts = json.loads((LEDGER / 'data/accounts.json').read_text())['accounts']
        for name, meta in accounts.items():
            if name.startswith(('Assets:', 'Liabilities:')):
                self.assertIn('currency', meta, name)


if __name__ == '__main__':
    unittest.main()
```

A check can also compare hledger reports, for example a closing balance you know
from a statement: `hledger -f expense_ledger.ledger bal <account> -e <date>`.
