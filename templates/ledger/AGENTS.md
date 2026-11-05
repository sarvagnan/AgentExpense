# Personal ledger rules

This ledger follows the general bookkeeping policy in the AgentExpense application,
`policy/BOOKKEEPING.md`. Read that first; it covers double entry, evidence,
duplicates, currencies, transfers, wallets, people, investments, rewards, pending
entries, statements, recurring rules and bills, and Delete, Trash and retention.
The dashboard's agent receives both documents on every run.

Keep only your own rules here, such as which account a regular payee belongs to,
who shares which costs, or how you name things. Personal rules may add detail;
they never remove a check from the general policy. Never put passwords, statement
passwords or sign-in details in this file.

## Personal rules

- (none yet)

## Rebuild and verify

Run every command from the application root with `LEDGER_DIR` set to this
directory. Commit store changes with `make commit m="…"`, which verifies them under
the ledger lock.
