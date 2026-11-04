# Fictional example ledger

Every person, date, amount, reference and event in this fixture is invented for tests. This is Asha Rao's demonstration INR ledger.

## Before adding or changing transactions

## Source of truth

JSON in data/expenses is authoritative; data/forecast.json holds commitments. Declare accounts in data/accounts.json. Search for duplicates, retain evidence, use exact decimal amounts and balance every entry. Expenses debit Expenses and credit their actual bank, cash, wallet or card funding source. Advances debit a reimbursement asset; repayment clears it rather than creating income. Record original currency and exact billed cost when applicable. Sources here are invented confirmation notes; never treat them as real receipts. Keep same-day order stable and use the shared ledger lock for concurrent writes.

## Fuel entries

Record physical readings separately from monetary cost. Match a fuel log by explicit confirmation.

## Rebuild and verify

Run make verify and make dashboard from the application with LEDGER_DIR set to this directory. This example has no repository history; initialize a separate personal ledger only when intentionally adopting it.

## Varying recurring rules and bills

A varying rule's postings hold the median of its last three payments. Save an
unpaid bill on the rule with its evidence; add an expense only when it is paid.
Retain amount-check doubts and require explicit acceptance of unusual amounts.
