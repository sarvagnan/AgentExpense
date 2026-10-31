# Example card statement reader

This reader handles an invented card statement, with no bank or network branding.
Copy `statement_pdf.py` and adapt its recognition, headers and row parsing for a
new format. Account metadata and reader registration come in a later phase; the
current reconciler uses `Liabilities:Credit Cards:Rewards Card`.

## Layout

The first page starts with `Example Card Statement`, followed by the nine labeled
header lines below, each exactly once. Header labels and Debit/Credit are case
insensitive. Dates are `YYYY-MM-DD`; the period start must precede or equal its
end. The card identifier contains exactly four digits. Amounts are nonnegative
with two decimal places; optional thousands separators use groups of three.
Amounts charged to the card and all summary values are INR in this phase.

The table starts with the exact column names below, separated by `|`. Each row
has seven cells: date, unique serial (letters, digits or hyphens), description,
amount, Debit or Credit, foreign amount, currency. Descriptions cannot contain
`|` or wrap across lines. A domestic row leaves both final cells empty but keeps
the separators. Foreign amounts require an explicit uppercase three-letter
currency; the reader never supplies a currency by default. No reward-points
column is used. Blank lines and padding around cells are ignored.

Continuation pages may repeat the table heading or continue rows directly;
PDFium joins pages with form feed, and each row keeps its one-based page number.
The layout has no table footer. Malformed rows fail parsing rather than being
silently skipped.

```text
Example Card Statement
Statement period: 2026-01-01 to 2026-01-28
Card last four digits: 5678
Previous balance: 0.00
Purchases: 120.00
Cash advances: 0.00
Credits: 30.00
Total due: 90.00
Minimum due: 9.00
Due date: 2026-02-15
Date | Serial | Description | Amount | Direction | Foreign amount | Currency
2026-01-02 | EX-001 | Fable Shop | 120.00 | Debit | 2.00 | USD
2026-01-03 | EX-002 | Payment received | 30.00 | Credit | |
```

Every value above is invented. `CardStatement-2026-01-28.pdf` is the generic
filename convention recognized by evidence labels; parsing reads the contents.
The upload pipeline preserves the supplied basename when filing the PDF.

## Pipeline and checks

1. The server opens the PDF, using a locked PDF's password only in memory.
   `extract_text` uses PDFium character positions; `parse_text` reads the layout.
   Decimal strings are retained, including the printed minimum due and due date.
   `EXPENSES_STATEMENT_CARD_LAST4` optionally checks the identifier; unset skips
   only that comparison.
2. `checks` requires debit rows to equal purchases, credit rows to equal credits,
   and previous balance + purchases + cash advances − credits to equal total due.
   Cash advances retain the pipeline's existing separate summary-adjustment
   semantics. The previous balance must match the last filed statement when one
   exists. Minimum due and due date are extracted, without a payment rule.
3. Only after all checks pass does `statement_rules.py` propose matches and
   classifications. The user decides unresolved rows. A restricted second
   reading may recover extraction failures but must pass the same totals checks.
4. Apply files the PDF under `Bills/<period-end>/`, writes membership, summary and
   reconciliation rows, appends an audit event and commits under the ledger lock.
   Schema and membership checks still run; a failed apply rolls back.

`parse_pdf(path, expected_last4='')` combines PDFium extraction and parsing.
`parse_text(text, expected_last4='')` is useful for adapting and testing a reader
without a PDF. Run the offline tests with `make test-server`; the tests create
fictional text PDFs in memory, including encrypted and multipage examples.

For batch text extraction, the stable Node entry point delegates to the same
Python reader and validates totals before writing any export:

```sh
node scripts/statements/extract_statements.mjs /path/to/example-text /path/to/extracted.json
```

The optional arguments default to `LEDGER_DIR/data/statements` and its
`extracted.json`. Output keeps the reconciliation CLI's `statements` and
`transactions` shape, with decimal-string amounts and explicit foreign currency.
`organize_statement_pdfs.py` can file PDFs named by that export; read the selected
ledger's entry policy before moving evidence. These batch tools do not apply
transactions. They are separate from the server's locked Apply operation.
