# Statement readers, interface 1

Readers turn a file into exact decimal rows and a statement summary. The server
validates the result, checks currency, all declared arithmetic and continuity,
then builds a checklist. Only the server writes the ledger. Unknown formats use
the addressed agent-reading task below; recognised-reader errors stop the file.

Built-ins are standalone Python modules in [`readers/`](../readers/). Trusted personal
modules go in `<ledger>/readers/*.py`. **Personal readers execute in the server
process with its permissions**, just like built-ins; install only code you trust.
Discovery imports non-underscore `.py` files in filename order, built-ins first.
Unsupported interfaces, invalid declarations and duplicate IDs stop discovery;
personal modules cannot silently replace built-ins. Imported dependencies must be
installed in the server environment. The registry is
[`scripts/statement_readers.py`](../scripts/statement_readers.py).

Each module declares:

```python
INTERFACE = 1
ID = 'example-export'                 # lowercase letters, digits and hyphens
NAME = 'Example account export'
KINDS = ('bank', 'card')              # supported account metadata kinds
EXTENSIONS = ('.csv',)                # lowercase suffixes, with dots
CHECKS = ('running_balance',)         # or a callable: CHECKS(account) -> tuple

def recognise(file):                 # pathlib.Path; cheap, read-only
    return {'confidence': .5, 'reasons': ['CSV extension']}

def read(file, account, password=None):
    # account is a copy of the complete account metadata, including statement.options
    ...                              # return a Statement dict; never write anything
```

`recognise` returns a finite confidence in `[0,1]` and a list of strings. The
registry catches exceptions and malformed results and returns zero confidence.
Recognition must not call agents, start processes or change files. Extension and
account-kind compatibility are checked before dispatch. An exact printed identifier selects its configured account when unambiguous.
Otherwise, if several configured accounts can read a generic file, the person
selects the account before reconciliation.
An explicit printed identifier must agree with the selected account's configured
`statement.identifier`. No currency is inferred from a symbol or file name.

The server decrypts PDFs with the password in memory and passes a temporary,
decrypted PDF to the reader; the password is never retained. Reader modules may
accept `password` for independent use. Failed CSV/workbook decoding is an error,
not a partly imported statement. File size is capped at 25 MB at intake.

## Statement dictionary

Every Statement has `periodStart`, `periodEnd` (`YYYY-MM-DD`, inclusive), `currency`
(a printed uppercase three-letter code, or `None` if absent), and a nonempty `rows`
array. The account currency is supplied only after checking any printed currency.
The registry adds `reader`, `readerName`, `readerPersonal`, and `declaredChecks`.

Each row has:

| Field | Meaning |
| --- | --- |
| `date` | Printed calendar date, never after `periodEnd`. A card statement (positive debt: `previousBalance`, `totalDue`, or a `debt` reading) lists each purchase posted in its period under the day it was made, so it accepts dates up to 31 days before `periodStart`. Account statements (`kind: "bank"` with signed balances) stay within the period in ascending date order; same-day order is significant. |
| `description` | Nonblank, single-line printed description. |
| `amount` | Nonnegative exact decimal string, never a JSON number. |
| `direction` | `Debit` decreases the account balance; `Credit` increases it. Card debt therefore increases on Debit. |
| `serial` | Unique, nonblank identity **within this file**. Generic readers use the physical source row; this is never an overlap identity. |
| `reference` | Optional stable reference, or `None`; used before amount/date matching. |
| `foreignAmount`, `foreignCurrency` | Optional paired exact nonnegative amount and explicit three-letter currency. |
| `balance` | Optional signed running account balance after the row, as a decimal string. |
| `citation` | Either `{"page":1,"line":12}` or `{"sheet":"Activity","row":3}`; all positions are one-based. CSV uses sheet `CSV`. |
| `page` | Legacy compatibility field; PDF page, or 1 for tabular files. Matching uses citations for tabular source identity. |

Balance-style statements set `kind: "bank"` (the historical subtype name also
covers generic card exports using signed account balances), and may provide
`openingBalance`, `closingBalance`, `debits`, `credits`. Amounts are account units;
negative card balances are liabilities. The example PDF keeps its existing
positive-debt fields: `previousBalance`, `purchasesCharges`, `cashAdvances`,
`paymentsCredits`, `totalDue`, `minimumDue`, `dueDate`, and optional `card_last4`.
See the [example PDF layout](../scripts/statements/README.md).

Checks are mandatory before matching:

- Row dates obey the bounds above. A date after `periodEnd`, before an account
  statement's `periodStart`, or more than 31 days before a card statement's
  `periodStart` (likely a misread year) stops with the row's page/line or
  sheet/row citation. A card row dated before `periodStart` belongs to this
  statement; it never overlaps the previous one.
- `printed_totals`: every provided printed debit/credit total agrees with rows;
  opening minus debits plus credits agrees with printed closing. The example card
  retains its existing purchases/credits/cash-advance/total-due equation.
- `running_balance`: every row's printed balance equals the previous running
  balance plus its signed movement, including the first row against opening.
- Balance-style continuity **always** applies when a prior statement or ledger
  opening exists. For account statements, overlap movement through the previous
  end is reversed to compare the same balance boundary; a card's opening equals
  the previous closing. Pre-start movement is checked but covered
  by opening balances; the effective import starts at `ledger.json.startDate`.
- Without a printed opening, a running balance supplies it. Without either, the
  existing ledger opening or prior closing supplies it. Without any anchor the
  statement stops. Computed totals are identified as computed; they are not
  described as printed checks. A no-summary/no-balance export cannot independently
  prove its download is complete; the final hledger closing assertion still runs.

All amounts use `Decimal`. A failed declared check stops before matching or any
write. Personal readers must supply every declared field; they cannot ask the
server to bypass a failing check.

## Generic CSV and spreadsheet options

Configure the paying account in `data/accounts.json`:

```json
"Assets:Bank:Example Current": {
  "kind": "bank",
  "currency": "EUR",
  "label": "Example Current",
  "statement": {
    "reader": "generic-csv",
    "options": {
      "columns": {
        "date": "Date",
        "description": "Description",
        "debit": "Debit",
        "credit": "Credit",
        "balance": "Balance",
        "reference": "Reference",
        "currency": "Currency"
      },
      "date_format": "%d/%m/%Y",
      "header_row": 2,
      "skip_rows": [1],
      "decimal_separator": ",",
      "thousands_separator": ".",
      "delimiter": ";",
      "encoding": "utf-8-sig",
      "period": {"start": "2026-11-01", "end": "2026-11-28"}
    }
  }
}
```

`columns` values are exact unique header strings or one-based integer column
positions. Required: `date`, `description`, and either `amount` or one/both of
`debit`, `credit`. Optional: `balance`, `reference`, `currency`, `direction`,
`foreign_amount`, `foreign_currency`, `identifier`. Blank debit/credit cells mean no movement;
two nonzero columns, negative debit/credit values, invalid grouping, nonfinite
numbers and missing mapped cells stop the file. Wholly blank rows are ignored.
CSV data rows must have the same column count as the header. `skip_rows` lists
one-based physical source row numbers; it does not renumber citations.

With `amount`, `sign` defaults to `debit-negative`; `debit-positive` reverses it.
An explicit `direction` column requires nonnegative amounts and Debit/Credit words.
`date_format` uses Python `strptime`, default `%Y-%m-%d`. Spreadsheet date cells
are accepted directly. Defaults: header row 1, UTF-8 with optional BOM, comma
CSV delimiter, decimal `.`, thousands `,`, and no skipped rows. Native spreadsheet numeric cells keep their stored numeric value independently
of text separators. Use `""` to turn off thousands separators. Grouped amounts must use groups of three.

Set reader `generic-sheet` for `.xlsx` (openpyxl) or `.xls` (xlrd). All mappings
above apply, plus `sheet`: an exact name or zero-based index, default 0. `.xlsx`
formula cells in mapped fields are refused, avoiding stale cached arithmetic.
Legacy `.xls` supplies the workbook's stored cell values. Workbook links are
never followed. CSV-only encoding and delimiter options do not affect workbooks.

`period` is optional; without it the first/last data-row dates are the period.
An optional `currency` option states a printed file-level currency when there is
no row-currency column. Mixed printed currencies are refused. Optional `summary`
contains `openingBalance`, `closingBalance`, `debits`, `credits`. Each value is
an exact numeric string in the configured number format or a one-based source
cell, e.g. `{"row":1,"column":2}`. Summary cells must be outside the data table
or explicitly skipped. Supplying summary declares `printed_totals`, requiring
all four fields; mapping balance declares `running_balance`. Do not configure
computed values as printed values.

## Worked personal reader

A personal layout can reuse the strict mapped parser with reviewed defaults:

```python
# <ledger>/readers/example_current.py
from tabular import read_csv
INTERFACE = 1
ID = 'example-current-csv'
NAME = 'Example Current export'
KINDS = ('bank',)
EXTENSIONS = ('.csv',)
CHECKS = ('running_balance',)

def recognise(file):
    if file.suffix.lower() != '.csv':
        return dict(confidence=0, reasons=['Expected CSV'])
    with file.open(encoding='utf-8-sig') as stream:
        found = stream.readline().strip() == 'Date,Description,Amount,Balance,Reference'
    return dict(confidence=1 if found else 0, reasons=['Reviewed export header'])

def read(file, account, password=None):
    account['statement']['options'] = {
        'columns': {'date':'Date', 'description':'Description', 'amount':'Amount',
                    'balance':'Balance', 'reference':'Reference'}
    }
    return read_csv(file, account)
```

Select `statement.reader: "example-current-csv"`. Given a 100.00 opening and a
`2026-11-02,Fable Shop,-10.00,90.00,EX-001` row, the reader returns Debit 10.00,
balance 90.00, reference EX-001 and citation `{"sheet":"CSV","row":2}`. The
shared checker proves the opening/row/closing arithmetic before a checklist exists.

## Store, overlap and Apply

`data/card-statements.json` remains the shared file for card and bank summaries.
An entry's statement membership is its `statement` group (`end`, `serial`, `page`,
`source-page`, `source-row`); [STORE.md](STORE.md) lists every key.

New balance-style summaries contain `id`, `kind:"bank"`, `account`, `start`, `end`,
`source`, `reader`, `opening-balance`, `closing-balance`, `debits`, `credits`, `rows`,
`members`, and `assertion-id`. A member has date, serial, description, amount,
direction, citation, optional reference/balance, and `disposition`:

- `recorded`: `entry-id` identifies a new or explicitly matched actual entry.
- `already-in-ledger`: `entry-id` reuses identical earlier account membership.
- `pre-start`: no entry; covered by the ledger's opening balances.
- `overlap-pending`: no second movement; `review-candidates` retains existing entry
  IDs for the important `overlapping-statements` Review item. Review confirms an
  existing membership through the normal locked, verified, audited writer.

Overlap compares the same account, date, numeric amount, direction and exact
printed description, plus reference/running balance whenever present. Physical
CSV/workbook row numbers never prove cross-file identity. Ambiguous overlap is
retained for the person, not silently skipped or booked twice. Matching reserves
references first; uncertain amount/date matches can be applied pending with
`imported-after-start` (when applicable) or the existing duplicate reasons.

Apply files the original source, adds approved entries, writes the summary and
explicit membership, adds a zero posting with a closing balance assertion at
period end (account statements only; see cards below), appends an audit event, verifies and commits under the ledger lock.
Existing bank entries retain their descriptions and dates; ordinary matches add
source links. A foreign-currency estimate requires the explicit conversion choice
from the existing checklist before the server can replace its billed amount; the
original foreign amount and entry identity remain intact. A failed assertion or any other verification rolls everything
back. A statement entirely before startDate is displayed as covered and cannot
apply financial changes. The store checker independently verifies totals,
membership, ordering, continuity, overlap candidates and the assertion entry.

Fictional inputs and their exact account configurations are in
[`fixtures/statements/`](../fixtures/statements/); regenerate with
[`fixtures/generate_statements.py`](../fixtures/generate_statements.py). Configuration
is applied only to disposable test ledgers, keeping the source personas unchanged.
The server lockfile includes openpyxl/xlrd, with xlwt in the development group for
fixture generation. Test with `make personas` and `make browser-tests`.

Card PDFs spanning startDate use the same signed-balance membership for the
clipped import and later statements on that account. Pre-start charges stay
covered by opening balances. Ordinary legacy card imports retain their original
summary and membership shape.

A card statement read by a personal reader or the agent, or a card PDF spanning
startDate, is normalized to signed units. The reading keeps
`sourceBalanceStyle: "debt"` and the stored summary `source-balance-style: "debt"`.
A billed total is not the account balance on the closing date: a purchase made
before the close can post on the next statement. These summaries therefore omit
`assertion-id` and add no closing assertion; membership and the printed arithmetic
confirm them, as for ordinary card summaries. Continuity compares the previous
closing with the printed opening. A first statement on an account with activity
continues from its opening balance alone, as a filed card chain does. A first
opening for an unused account is dated on the earliest row (clipped at
`startDate`). Each purchase matches an unbilled entry or is added, and is a member
of one statement only; `startDate` still classifies each row by its own date.
Account-style signed exports of bank and card accounts keep their closing
assertion. Existing summaries and assertions are never rewritten.

Card coverage follows membership. A member is locked against amount and paying
account changes and cannot be deleted, whatever its date. For Restore, a filed card
statement covers the dates from its earliest member, or its period start if
earlier, through its end.

Completed statement uploads are removed after Apply or an already-filed result.
Completed session records expire after 24 hours; source files filed in Bills remain.

## Unknown formats and cited readings

Add interpretation never starts an agent. A PDF explicitly chosen as Statement,
or a CSV/workbook with no applicable configured column mapping, shows that the
agent will read it and code will check source values and printed totals. Continue
queues that file's own statement session on the existing FIFO worker. Choose its
account before reading. A recognised reader's parse or mandatory-check failure
stops; it cannot request an agent repair. Built-in readers keep working during
the shared agent pause. Unknown readings retain their session through outages
and resume automatically with the same backoff/sign-in notice as Add.

[`statement_reading.py`](../review-dashboard/server/statement_reading.py) owns the
strict task schema, extraction and verifier. PDFium extracts every page with
one-based `p3:l17` text addresses and page images. CSV cells use `CSV!B12`;
workbooks use their exact sheet name, e.g. `Activity!B12`. Every symlink component
and ledger escape is refused. Extraction fails rather than truncating beyond
100 PDF pages, 20,000 rows per sheet, 100,000 cells or 4 MB of addressed text.
Images accompany the source but cannot substitute for citable text. XLSX formula
cells cannot supply facts; legacy XLS supplies stored cell values without
executing formulas. Workbook external links are never followed.

The dedicated prompt includes source evidence, selected account and locale,
without Add policy or write instructions. Known facts are `{value, source}`;
unknown optional facts are null, and unreadable/uncertain answers stop. Required
cited facts are period, explicit currency, printed opening/closing, debit/credit
totals, and every row's date, description, amount and direction. References are
optional. Bank accounts require each running balance. A direction cites its
row's printed word or amount cell/line. Code verifies a debit/credit header above
an unsigned amount cell as column context; signed amounts use the account's
sign convention. Reusing any field's source address across rows is refused,
including direction. Tabular row values must share one
physical source row. PDF descriptions and references match a complete line or
column segment (pipes, tabs or repeated spaces) after whitespace normalization;
tabular descriptions and references match their complete cells.

Amounts use exact decimals, native workbook numbers, explicit account number
settings and evident separators; locale resolves ambiguous grouping and dates.
Printed positive card debt uses `balanceStyle: "debt"`: code first checks facts
in their printed units, then converts balances to signed account units for the
shared arithmetic/membership gate. Other statements use `"account"`. All row
totals, running balances, account currency, continuity, overlap and startDate
checks remain mandatory. Failure reports name fields and source addresses,
without echoing source amounts into logs. Apply rechecks the source hash and
citations under the ledger lock before the ordinary verified transaction.
After an agent returns, matching reloads the current ledger and builds the
checklist/fingerprint under one ledger lock. A concurrent account or locale
change stops the reading; another ledger addition is included in matching.

Successful sessions show **Read by the agent · source values and totals checked**.
Their reconciliation sidecar retains per-field `valueSources`; the audit uses
the existing `agent` extraction kind and adapter run metadata. Source ledgers
and existing audit lines need no migration.

For a fixed one-sheet signed-amount table, code may offer **Save this column
mapping for future statements** only after a generic-reader replay matches the
verified rows and summary. The choice stays in the draft until Apply, which saves
`statement.options` in the same checked transaction. Discard writes no mapping.
Period and currency use source cells, avoiding a previous statement's fixed date
or currency. `period.start` / `period.end` accept `{row, column}` date cells;
`currency_cell` accepts a `{row, column}` explicit currency cell. Future files
use the generic reader and its mandatory checks. No reader code is generated.

Personal card readers may return the example card summary fields. The server
checks that equation first, then normalizes them to signed balances and explicit
membership for Apply. Cash advances must be represented by rows for this normalized
path. Installing an unconfigured personal reader does not displace a configured
built-in. The first statement for an unused account proposes its opening balance
for review before Apply.
