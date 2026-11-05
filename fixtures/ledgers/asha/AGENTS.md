# Fictional example ledger

Every person, date, amount, reference and event in this fixture is invented for
tests. This is Asha Rao's demonstration INR ledger. It follows the application's
general bookkeeping policy, `policy/BOOKKEEPING.md`; only Asha's own rules are kept
here.

## Personal rules

- Sources here are invented confirmation notes; never treat them as real receipts.
- Advances for Dev Sen debit `Assets:Reimbursements:Dev Sen`; their repayment clears it.
- The fuel log is on: record each fill's litres and trip distance with its expense.

## Rebuild and verify

Run make verify and make dashboard from the application with LEDGER_DIR set to this directory. This example has no repository history; initialize a separate personal ledger only when intentionally adopting it.
