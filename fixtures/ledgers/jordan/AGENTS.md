# Jordan Lee — fictional policy

Every value here is invented. Keep evidence with entries, search for duplicates, balance postings and use the shared ledger lock. Opening balances are confirmed on the start date. Run verification from the application with LEDGER_DIR set to this directory.

## Varying recurring rules and bills

A varying rule's postings hold the median of its last three payments. Save an
unpaid bill on the rule with its evidence; add an expense only when it is paid.
Retain amount-check doubts and require explicit acceptance of unusual amounts.

## Delete, Trash and retention

Delete is offered only before the ledger's Delete time limit ends and while no
filed card statement or statement-confirmed bank balance covers the entry.
The addition audit time starts the window; otherwise use the first ledger commit
containing the ID. Entries present in the ledger's first commit have no known
addition time and cannot be deleted, even when an import retained audit lines.
Ledger history reads are cached and read-only.

Delete moves the entry and exclusively cited evidence into ledger-local Trash.
Restore retains its ID, postings and original evidence paths, and marks a linked
rule paid again. Refuse Restore when a statement filed since removal covers the
payment date without the entry. The audit records deletions, restores, refusals,
permanent removals and changes to the shared time limits. Edit again to reverse
an edit. A saved bill remains unpaid until a payment is recorded.
