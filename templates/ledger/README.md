# Personal expense ledger

This folder is a personal ledger for AgentExpense, made from
the application's `templates/ledger/` by `make new-ledger`. It is its own private
repository: the application never stores anything personal, and an application
update never changes this folder except through a ledger format migration that
checks itself and commits separately.

Select it from the application root with `export LEDGER_DIR=/absolute/path/to/this/folder`.

| Path | What it holds |
| --- | --- |
| `ledger.json` | Settings: name, main currency, optional locale and start date, optional modules |
| `AGENTS.md` | Your own bookkeeping rules; the general policy is the application's `policy/BOOKKEEPING.md` |
| `data/accounts.json` | Accounts with their kind, currency, label, aliases and statement reader |
| `data/expenses/<year>.json` | Entries, the source of truth |
| `data/forecast.json` | Recurring rules and saved bills |
| `data/card-statements.json` | Filed card statements |
| `data/additions/` | The append-only audit log (created by the first save) |
| `Bills/` | Evidence: receipts, invoices and statements (created by the first save) |
| `readers/` | Your own statement readers; see `readers/README.md` |
| `checks/` | Your own checks, run by `make private-checks`; see `checks/README.md` |

Generated and temporary files (`expense_ledger.ledger`, `drafts/`, `.lock`) are
ignored by `.gitignore`.

## Private checks

From the application root, `make private-checks` runs whatever this ledger keeps in
`checks/` and prints PASS or FAIL for each, with the output of any failure:

- `test*.py` files run with Python's `unittest`, one file at a time;
- any other executable file runs as a program and passes when it exits with 0;
- other files (this README, helper modules) are ignored.

Use checks for facts about your own history that the application cannot know, such
as "the savings account closed last year at this balance" or "rent is always paid
from checking". See `checks/README.md` for an example.
