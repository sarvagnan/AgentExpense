# The ledger store, format 2

A ledger keeps its financial data as JSON under `data/`. Format 2 gives every
entry, posting and recurring rule one typed shape. Every key is always present,
and an empty value is `null` or `[]`.

The shape is defined once, as pydantic v2 models in
[`scripts/ledger/store_models.py`](../scripts/ledger/store_models.py). Python reads
and writes every store document through them (`scripts/ledger/store.py`): `load`
validates each document and then the checks that span documents, and `dump`
validates a whole document before writing it. The dashboard build validates the
same documents in Node against the generated schema (`ledger-manifest.mjs`).

Facts that only some people record, such as fuel readings, are **extensions**: a
group of typed fields that a ledger adds to every entry by listing it in
`ledger.json`. [EXTENSIONS.md](EXTENSIONS.md) describes them. A ledger's models
and schema are the core composed with the extensions it lists.

These files are generated from the models and checked in; a test fails when they
are out of date:

- `schema/store.schema.json`: every store document of a ledger without extensions
  (the core);
- `schema/answer.schema.json`: the Add agent's answer for a ledger that asks for no
  extension (the core);
- `schema/extension-definition.schema.json`: an extension definition;
- `schema/extension-types.json`: each extension field type's JSON Schema, which
  Python and Node both use to compose a ledger's schema.

To regenerate them:

```sh
uv run --locked --project review-dashboard/server python scripts/ledger/store_models.py --write
```

`--check` exits non-zero when a checked-in schema is out of date.

## Files

| File | Holds |
|---|---|
| `data/accounts.json` | Declared commodities and accounts |
| `data/expenses/<year>.json` | That calendar year's entries, in date order |
| `data/forecast.json` | Recurring rules, in order |
| `data/card-statements.json` | Filed card and balance-style statements, by end date |
| `data/recurring-settings.json` | Optional rule-matching settings |
| `data/trash.json` | Optional: deleted entries that can still be restored |

`ledger.json` (the manifest) keeps its own schema, `schema/ledger.schema.json`.
The schema's `x-documents` names the definition that validates each file.

## Conventions

- **Every key is present.** A missing key is an error, and so is an unknown key.
- **Empty values.** A value that can be empty is `null`, or `[]` for a list.
  A boolean's empty value is `false`.
- **Groups.** A few related facts form a group, such as `statement`, `references`
  or an extension's fields. A group is `null` when none of its facts is known. It
  is never an object whose values are all `null` or `[]`.
- **Names** are kebab-case.
- **Amounts** are exact decimal text, never JSON numbers. Trailing zeros are kept
  as written.
- **Text that appears in the journal is a single line.** This covers descriptions,
  notes, evidence and labels.
- **Key order** is the order in the tables below. Writers serialise through the
  models, so files stay byte-stable and diffs stay small.
- **Layout.** UTF-8 without escapes, two-space indentation and a final newline.
  Each posting, and each object that holds only plain values (text, numbers,
  `true`, `false`, `null`, `[]` or `{}`), sits on one line. A list of strings
  has one item per line.

### Types

| Type | Meaning |
|---|---|
| ID | 16 lowercase hex characters, random and permanent |
| date | `YYYY-MM-DD`, a real calendar date |
| moment | ISO date and time with an offset or `Z` |
| line | Non-empty text without line breaks |
| note | Text without line breaks; may be empty |
| account | A declared account name: segments joined by `:` |
| commodity | A commodity symbol without quotes or line breaks |
| code | A commodity code without spaces, quotes, `;`, `=` or `@` |
| amount | Signed decimal text with a decimal point: `-12.50`, `0.00` |
| number | Signed decimal text; the decimal point is optional: `100`, `8.5` |
| unsigned | Decimal text with a decimal point, not negative |
| cents | Unsigned decimal text with exactly two decimals |
| positive | A whole number of at least 1 (a JSON integer) |

## Who sets each field

Every field of an entry, a posting and a rule (and of the groups inside them)
records its writers in `x-set-by`. The agent's answer is built from the entry
fields the agent sets, so the prompt and the answer schema can't drift from the store.

| Setter | Who |
|---|---|
| `agent` | The Add agent's answer. The hand form and Edit entry fill the same answer, so a person sets these fields there. |
| `server` | The app, on write, from its own state. Never taken from an answer. |
| `statement` | Statement Apply (card and balance-style) and the statement command-line tools. |
| `person` | A person's own action outside the answer: Review decisions, rule actions, Delete and Restore, setup commands. |
| `history` | Kept from older data. No current writer sets it, and every writer keeps it unchanged. |

An extension field's setters follow its definition: `filled-by: agent` gives
`agent` and `person`, `filled-by: person` gives `person` only.

## Entry

One actual entry in `data/expenses/<year>.json`.

| Field | Meaning | Type | Set by | Empty |
|---|---|---|---|---|
| `id` | Permanent random ID. It is unique across entries, rules, statements and Trash. | ID | server | never |
| `date` | The calendar date printed on the evidence or the payment. Its year names the file. | date | agent, statement | never |
| `status` | `confirmed` or `pending`. Pending means the classification still needs review. | enum | agent, statement, person | never |
| `description` | The payee, or what was paid for, as the evidence names it. | line | agent, statement | never |
| `evidence` | Filed evidence paths under `Bills/` (or older free-text sources), in order. | list of line | server, statement, person | `[]` |
| `statement` | Where a statement or import recorded the entry (see below). | group | statement | `null` |
| `references` | Identifiers on the evidence (see below). | group | agent, statement | `null` |
| `classification` | A short note on how the expense was classified. | line | agent | `null` |
| `original-amount` | The amount in another currency, as printed, when the payment was converted. | money | agent, statement | `null` |
| `reimbursement` | The reimbursement this entry belongs to. | object | history | `null` |
| `excluded-from-estimate` | Leaves the entry out of the variable-spending estimate. | object | history | `null` |
| `extensions` | One group per extension `ledger.json` lists, in that order (see [EXTENSIONS.md](EXTENSIONS.md)). | object | agent, person | `{}` |
| `rule` | The recurring rule occurrence this entry pays. | object | server, person | `null` |
| `possible-duplicate-of` | Unsettled possible duplicates, sorted by ID. | list | statement, person, server | `[]` |
| `notes` | Comment lines, in order. | list of note | agent, statement, person, server | `[]` |
| `postings` | At least one posting, in order. | list of posting | agent, statement, person | never |

### Groups and objects of an entry

**`statement`** (set by statements and imports). It is `null` unless one of these is known:

| Field | Meaning | Type |
|---|---|---|
| `end` | End date of the filed card statement that bills this entry. | date or `null` |
| `serial` | The row's serial number printed on the statement. | line or `null` |
| `page` | Page of the row in the filed card statement PDF. | positive or `null` |
| `source-page` | Page of the row in the entry's first evidence file (a balance-style import). | positive or `null` |
| `source-row` | Row number in an imported bank workbook. | positive or `null` |

**`references`**:

| Field | Meaning | Type | Set by |
|---|---|---|---|
| `reference` | A payment or bank reference printed on the evidence. | line or `null` | agent, statement |
| `order-id` | The payee's order or invoice numbers, in order. | unique list of line, `[]` when none | agent |

The group is `null` when `reference` is `null` and `order-id` is `[]`.

**`original-amount`** (money): `amount` (number, as printed) and `commodity`
(code). Both are set by the agent or a statement.

**`extensions`**: an object with one key per extension `ledger.json` lists, in
that order; `{}` when it lists none. Each value is that extension's group (its
fields in definition order, each `null` when unknown) or `null`.

**`reimbursement`** (history): `group` is the identifier shared by the entries
of one reimbursement (line). `label` is its display label (line or `null`).

**`excluded-from-estimate`** (history): `reason` (line), for example "forecast separately".

**`rule`** (server, person): `id` (the rule's ID) and `occurrence` (the
occurrence date it pays).

**`possible-duplicate-of`** items (statement, person, server): `id` is the other
entry, whose ID is greater than this one. `reason` is one of `amount-date-gap`,
`dissimilar-name`, `several-candidates`, `overlapping-statements` or `imported-after-start`.

## Posting

One line of an entry or a rule. It is written on one line in the file.

| Field | Meaning | Type | Set by | Empty |
|---|---|---|---|---|
| `account` | A declared account. | account | agent, statement, person | never |
| `amount` | Signed: positive into the account, negative out of it. | amount | agent, statement, person | never |
| `commodity` | The account currency, or units for an investment. | commodity | agent, statement, person | never |
| `cost` | Its price in another commodity. | object | agent, statement | `null` |
| `assert` | A confirmed balance after this posting. | object | statement, person | `null` |
| `notes` | Comment lines under the posting, in order. | list of note | history | `[]` |

**`cost`**:

| Field | Meaning | Type | Set by |
|---|---|---|---|
| `amount` | The price in the cost commodity, as written; greater than zero. | number | agent, statement |
| `commodity` | The commodity the posting was paid in. | commodity | agent, statement |
| `per` | `total`: the amount is the whole price (hledger `@@`). `unit`: the price of one unit (`@`), allowed only where it balances exactly. | enum | server, statement, history |

The agent never writes `per`. The server stores every answer's cost as `total`.

**`assert`** (statement, person): `amount` is the expected running balance (amount)
and `commodity` is the account currency or investment units (commodity). It is a
partial balance assertion (hledger `=`), never an assignment.

## Recurring rule

One rule in `data/forecast.json`. Its postings hold the estimate for one occurrence.

| Field | Meaning | Type | Set by | Empty |
|---|---|---|---|---|
| `id` | Permanent random ID. | ID | server | never |
| `date` | The next occurrence. It equals `schedule.from`. | date | person, server | never |
| `status` | `confirmed` or `pending`: whether the owner confirmed it. | enum | person | never |
| `description` | What the payment is for. | line | person | never |
| `schedule` | When it recurs (see below). | object | person, server | never |
| `amount` | `fixed`, or `varies`: the postings hold the median of the last three payments. | enum | person, server | never |
| `lifecycle` | `active`, `paused`, `ended` or `removed`. Only active rules forecast and match. | enum | person, server | never |
| `origin` | How the rule was proposed. | object | person, server | `null` (older rules) |
| `created` | When it was made. | date | server | `null` (older rules) |
| `removal` | Removal evidence. It is required when the rule is removed. | object | person, server | `null` |
| `bill` | A received, unpaid bill for the current occurrence. | object | person, server | `null` |
| `evidence` | Evidence carried by older rules. | list of line | history | `[]` |
| `references` | Identifiers carried by older rules. | group | history | `null` |
| `classification` | Carried by older rules. | line | history | `null` |
| `original-amount` | Carried by older rules. | money | history | `null` |
| `reimbursement` | Carried by older rules. | object | history | `null` |
| `excluded-from-estimate` | Left out of the variable-spending estimate. | object | history | `null` |
| `notes` | Comment lines, in order. | list of note | person | `[]` |
| `postings` | The estimated postings of one occurrence. | list of posting | person, server | never |

A rule has no `statement`, `extensions`, `rule` or `possible-duplicate-of`.
Those describe a payment that happened.

**`schedule`**:

| Field | Meaning | Type | Set by |
|---|---|---|---|
| `every` | `month` or `year`. | enum | person |
| `from` | The current occurrence; it equals the rule date. | date | person, server |
| `interval` | Units between occurrences; 1 for monthly or yearly. | positive | person |
| `anchor-day` | The day of the month it falls on when a short month moved it. | 1–31 or `null` | person, server |

**`origin`**:

| Field | Meaning | Type | Set by |
|---|---|---|---|
| `kind` | `owner`, `subscription-words`, `monthly-history` or `history-pass`. | enum | person, server |
| `words` | The typed words that proposed it. | text or `null` | person, server |
| `entry-ids` | The entries it was proposed from. | unique list of ID | person, server |
| `came-back` | `true` when a removed rule was proposed again. | boolean | server |

**`removal`** (person, server): `date` (when it was removed) and `entry-ids`
(the entries that showed it had stopped; a unique list of ID).

**`bill`** (person, except `evidence`, which the server files):

| Field | Meaning | Type |
|---|---|---|
| `amount` | The billed amount. | unsigned |
| `received` | When the bill arrived. | date |
| `pay-by` | The due date. | date or `null` |
| `occurrence` | The occurrence it bills; it equals the rule date. | date |
| `evidence` | Filed bill paths. | unique list of line |

## Other documents

These aren't entries, so their fields carry no setters. The same conventions
apply: every key is present, with `null` or `[]` when empty.

**`data/accounts.json`**: `commodities` holds the commodity directive arguments
verbatim, as a unique list. `accounts` maps each declared name, in order, to its metadata:

- **Income and Equity accounts:** `{}`.
- **Expenses accounts:** `{"category": …}`, a display category or `null`.
- **Every other root:** `kind` (`bank`, `card`, `cash`, `wallet`, `loan`,
  `investment` or `person`), `currency` (a declared commodity code), `label`
  (line or `null`), `aliases` (unique lowercase words, `[]` when none) and
  `statement`. `statement` is `null` or `{reader, identifier, options}`, where
  `identifier` and `options` are each `null` when absent.

**`data/card-statements.json`** holds two kinds of statement, in end-date order:

- **`kind: "card"`**, a card statement summary: `id`, `account`, `start`, `end`,
  `source` (the filed PDF), `previous-balance`, `purchases`, `cash-advances`
  (`null` when none), `credits`, `total-due` (all cents) and `rows`. Its rows are
  the entries whose `statement.end` is its `end`.
- **`kind: "bank"`**, a balance-style statement: `id`, `account`, `start`, `end`,
  `source`, `source-balance-style` (`"debt"` or `null`), `opening-balance`,
  `closing-balance`, `debits`, `credits`, `rows`, `members`, `assertion-id`
  (`null` only for debt-style) and `reader`.
  - Each member has `date`, `serial`, `description`, `amount`, `direction`,
    `citation` (`{page, line}` or `{sheet, row}`), `reference`, `balance` and
    `entry-id` (each `null` when absent), `disposition` and `review-candidates` (`[]`).

**`data/recurring-settings.json`**: `never-automatic`, a unique list of account prefixes.

**`data/trash.json`**: a list of deleted entries. Each item has:

- `id` (32 hex);
- `entry` (a format 2 entry);
- `removed-at` and `added-at` (moments; `added-at` is `null` when unknown);
- `reason` (`""` when none) and `index`;
- `files` (`{original, stored}`) and `kept` (`{path, citations}`);
- `rule-changes` (`{id, before, after}`, with format 2 rules or `null`);
- `duplicate-links` (the links removed from other entries, keyed by entry ID).

## The agent's answer

The answer is an envelope around the entry fields whose setters include `agent`.
The model is derived from the entry model (`store_models.answer_model`), and so
is the posting inside it. Every name is kebab-case; the status value
`not_expense` keeps its spelling.

| Field | Meaning | Empty |
|---|---|---|
| `status` | `ready`, `question` or `not_expense`. | never |
| `question` | One short question when the status is `question`. | `null` |
| `entry` | `date`, `status`, `description`, `references`, `classification`, `original-amount`, `extensions` (only when the ledger asks for an extension), `notes` and `postings`. Each posting has `account`, `amount`, `commodity` and `cost` (`amount`, `commodity`). | `null` |
| `new-accounts` | Accounts the entry uses that aren't declared yet. | `[]` |
| `doubts` | Anything the person should check. | `[]` |
| `attachment-names` | One descriptive base name per attachment, without folder or extension. | `[]` |
| `explanation` | One short reason when the status is `not_expense`. | `null` |

`entry.extensions` holds only the extensions `ledger.json` lists with
`"ask": true`, each with only its `agent` fields, each group or `null`. The
answer leaves `extensions` out when the ledger asks for none.

Amounts use the store's pattern: a posting amount always has a decimal point
(`120.00`, never `120`). Any other field, such as `statement`, `id`, `evidence`
or `rule`, a posting's `assert`, a cost's `per`, an extension the ledger doesn't
ask for or a `person` field fails validation. A failed answer goes back to the
agent under "Server check failures to correct", like any other check failure.
When the server stores the entry it fills in the rest:

- `id`;
- `evidence`, from the files it filed for `attachment-names`;
- `rule`;
- a cost's `per` (always `total`);
- every listed extension's group, with `person` fields `null`;
- `null` or `[]` everywhere else.

The answer schema has no `x-` annotations. Every object in it has
`additionalProperties: false` and lists all its properties as required, as
strict structured output expects.

**The hand form and Edit entry** use the same envelope (`store_models.form_model`)
with every listed extension (asked or not) and every one of its fields, so a
person can edit `person` fields there, plus optional `account-metadata` for new
balance-sheet accounts.

## Checks on one entry (model validators)

These are model validators. They need nothing outside the entry, so every read
and every write runs them:

- **Exact balance per commodity.** The postings sum to exactly zero in each
  commodity, with no rounding tolerance.
  - A posting with a cost counts in its cost commodity, as hledger counts it: the
    `unit` price times the quantity, or the `total` amount, negated when the
    quantity is negative (a zero quantity counts the whole amount). Costs are
    normally positive, so either is signed like the posting.
  - When the residue spans commodities, the message says so: amounts in different
    commodities need a cost (no implicit conversion).
  - Postings in different commodities without costs pass only when each
    commodity sums to zero on its own, as in an opening-balance entry with two
    currencies.
- **Costs.** A cost is greater than zero and in a different commodity from its
  posting, and a `total` cost needs a non-zero quantity. So the cost's sign is the
  posting's, and every reader that shows a cost as its absolute value signed like
  the quantity shows what hledger counts.
- **Statement row.** `statement.end` requires `serial` and `page`.
- **Duplicate links.** Targets are unique, sorted, and greater than the entry's own ID.
- **Rules.**
  - `date` equals `schedule.from`, and every rule balances exactly.
  - A removed rule has `removal`.
  - `amount: "varies"` and `bill` keep their format 1 conditions: a simple
    two-posting payment in one currency, a positive bill, and the current occurrence.
  - A rule's postings have no `assert`.

Node (`ledger-manifest.mjs`) makes the same checks with the same words: the
schema marks each group `x-group`, statements are a union tagged by `kind`, and
the validator runs the checks above for `Entry`, `Posting`, `Rule` and
`BankStatement`. Store documents are parsed so that a number written with a
decimal point or an exponent (`5.0`) is never an integer, and a repeated key is
refused, as in Python. Patterns spell whitespace out instead of `\s`, which differs
between Python, Rust and JavaScript, and a date in year 0000 is refused in both.

Checks across entries stay in `store.validate` and `check_store.py`. These
include ID uniqueness, account currencies, statement membership, rule
references, evidence files and extension groups that match `ledger.json`.

## The JSON strict check

`scripts/ledger/strict_check.py` checks, on the validated store and without
starting a process, what `hledger check -s ordereddates` checks on the journal
the store renders (hledger's manual, "check"):

| hledger check | The JSON strict check |
|---|---|
| parseable | Account names: no whitespace but the plain space (hledger reads a no-break or thin space as a space and U+0085 as a line break), no two spaces in a row or space at either end, and no leading `(`, `[`, `*`, `!` or `;`. Commodity directives: a symbol (quoted unless it is letters or currency signs), or an amount with `.` as its decimal mark, such as `1,000.00 INR` or `$1,000.00` (`1.000,00 INR` makes hledger read every amount ×100). No posting note sets a posting date: a `date:` or `date2:` tag after a space, or brackets holding only `0-9/.=-` with a digit and a separator (`[2/3]`; `[1]` stays text). The price file holds only comments and `P DATE COMMODITY AMOUNT` lines with a real year-month-day date, a decimal number, quoted symbols unless letters or currency signs, and declared commodities. |
| autobalanced, balanced | The model validators above: every entry and rule balances exactly, with costs counted as hledger counts them and no implicit conversion. |
| assertions | In date order, same-day entries as written, each posting's assertion holds for its own account and commodity only (no subaccounts), ignoring costs, compared exactly: the running balances `confirmed_balances.py` keeps. |
| commodities | Every posting, cost, assertion and price commodity is declared. |
| accounts | Every posting account is declared, exactly. |
| ordereddates | The entries, in the order the year files hold them, are in date order. |
| forecasts (`--forecast`) | Every rule, active or not, and every forecast transaction it renders (a saved bill's included): it balances exactly, its accounts and commodities are declared, and its period is one hledger generates on its start date. |

`check_store.py` (so `make verify-data`, `make verify` and `make commit`) and the
server's previews and saves run it as the gate. hledger, when installed, is the second
check: `make verify-data` and `make verify` run by hand run its strict check of the whole
journal at once, while `make commit` (so every save) sets `EXPENSES_HLEDGER_CHECK=background`
and the server checks each commit afterwards (see "The second check" below). The
agreement tests (`scripts/ledger/tests/test_strict_check.py`) run both on every
fixture and on generated large ledgers, and break a valid ledger one way per check
kind to require both to refuse it for the same reason. hledger counts as refusing
also when it reads something else than the store says: other account names, or
other balances. When a balance assertion fails, the check always reports it as a
confirmed-balance refusal (with its possible duplicates) and lists any other
problem beside it. Where the JSON check is stricter than hledger, it is so on
purpose: exact balance (hledger rounds to the entry's precision), a posting date
in a posting note, negative costs and zero quantities at a total cost, digit groups
made of spaces in a commodity directive, and price lines with group marks,
exponents, times or comments.

## The second check (hledger, in the background)

hledger is optional. When it is installed, the server checks every commit with it
after the save has answered (`review-dashboard/server/ledger_check.py`, with the shared
helpers in `scripts/ledger/second_check.py`):

- **When:** after each commit through `make commit`, at server start-up, on "Check
  again", and within 30 seconds of a commit made outside the server (the worker sees the
  `main` bookmark move).
- **Coalesced:** at most one check runs and one waits; the waiting check reads the newest
  commit when it starts, so the last check always covers the latest commit.
- **From a snapshot, without the lock:** the store files at the commit are read with
  read-only jj commands (`--ignore-working-copy`) into a temporary folder, the journal is
  rendered there and `hledger check -s ordereddates` runs on it. A save in progress is
  never read, and no save waits for hledger.
- **Recorded** in `drafts/ledger-check.json` (ignored and temporary, never in the
  ledger's history): the commit checked, the time, pass or fail, hledger's finding, its
  own text, and the entries changed since the last good check.

When a check fails, every screen shows a banner, Queue › Needs you shows a Ledger check
item, and statement Apply (which also confirms a balance) waits for the accounts the
finding and the changed entries involve, or for every account when it can't tell. The
item puts hledger's finding in plain words for the common kinds (unbalanced, implicit
conversion, assertion, undeclared account or commodity, date order, parse error), with
hledger's own text under Details. When the JSON strict check passes the same snapshot,
the app and hledger disagree: the item says it is a problem in AgentExpense and offers
Copy details. Otherwise it offers Ask the agent to fix it (the agent's proposal opens in
Edit entry and changes nothing until the person saves it), Edit the entry and Check
again. The item closes when a check passes. `make verify-data` run by hand reports a
failure in the same words.

## Server saves

The server and terminal `make commit` share `scripts/ledger/commit.py`. The server
keeps validated models and account/currency histories, checks changes under the
ledger lock, commits, then rebuilds dashboard data in the background. Full checks
run at startup and in `make verify`. [SAVES.md](SAVES.md) describes source-stamp
invalidation, rollback, agreement tests and whole-generation browser publication.

## The journal

Format 2 renders the same hledger journal as format 1. The comment lines come
out in this order:

1. `id:`;
2. one `source:` line per `evidence` item;
3. then `serial-number` (`statement.serial`), `source-row`, `source-page`,
   `statement-page` (`statement.page`), `statement-end` (`statement.end`),
   `reference`, one `order-id` per value, `classification`, `original amount`;
4. each listed extension's values, in `ledger.json` order and then field order,
   under their `journal-tag` (fuel's `fuel-filled-litres` and `trip-km` keep
   their format 1 place);
5. then `reimbursement-group`, `reimbursement-label`, `variable-estimate`
   (rendered `excluded, <reason>`);
6. then `notes`.

A `null` field renders nothing, as an absent field did in format 1.

## Examples (fictional)

An Add in a ledger that lists the fuel extension:

<!-- example: Entry fuel -->
```json
{
  "id": "000000000000a001",
  "date": "2026-06-18",
  "status": "confirmed",
  "description": "Fable Fuels petrol",
  "evidence": [
    "Bills/2026-06-18/Fable-Fuels-Receipt.jpg"
  ],
  "statement": null,
  "references": {"reference": "TXN-55120", "order-id": []},
  "classification": null,
  "original-amount": null,
  "reimbursement": null,
  "excluded-from-estimate": null,
  "extensions": {
    "fuel": {"litres": "8.00", "trip-km": "240.0"}
  },
  "rule": null,
  "possible-duplicate-of": [],
  "notes": [],
  "postings": [
    {"account": "Expenses:Transport:Fuel", "amount": "840.00", "commodity": "INR", "cost": null, "assert": null, "notes": []},
    {"account": "Assets:Bank:Saffron Reserve", "amount": "-840.00", "commodity": "INR", "cost": null, "assert": null, "notes": []}
  ]
}
```

A foreign purchase billed on a card statement, which pays a rule occurrence and
has an unsettled possible duplicate (a ledger without extensions):

<!-- example: Entry -->
```json
{
  "id": "000000000000a002",
  "date": "2026-10-02",
  "status": "confirmed",
  "description": "Cedar Studio annual plan",
  "evidence": [
    "Bills/2026-10-02/Cedar-Studio-Invoice.pdf",
    "Bills/2026-10-28/mosaic-statement.pdf"
  ],
  "statement": {"end": "2026-10-28", "serial": "MC-014", "page": 2, "source-page": null, "source-row": null},
  "references": {
    "reference": null,
    "order-id": [
      "CS-88213",
      "CS-88214"
    ]
  },
  "classification": null,
  "original-amount": {"amount": "48.00", "commodity": "USD"},
  "reimbursement": null,
  "excluded-from-estimate": {"reason": "forecast separately"},
  "extensions": {},
  "rule": {"id": "000000000000b001", "occurrence": "2026-10-02"},
  "possible-duplicate-of": [
    {"id": "000000000000a003", "reason": "dissimilar-name"}
  ],
  "notes": [
    "statement merchant: CEDAR STUDIO",
    "statement charge: 4123.20 INR (Debit)"
  ],
  "postings": [
    {"account": "Expenses:Subscriptions:Cedar Studio", "amount": "4123.20", "commodity": "INR", "cost": null, "assert": null, "notes": []},
    {"account": "Liabilities:Credit Cards:Mosaic Card", "amount": "-4123.20", "commodity": "INR", "cost": null, "assert": null, "notes": []}
  ]
}
```

A confirmed closing balance from a filed statement:

<!-- example: Entry -->
```json
{
  "id": "000000000000c001",
  "date": "2026-10-31",
  "status": "confirmed",
  "description": "Statement closing balance",
  "evidence": [
    "Bills/2026-10-31/nila-current-statement.pdf"
  ],
  "statement": null,
  "references": null,
  "classification": null,
  "original-amount": null,
  "reimbursement": null,
  "excluded-from-estimate": null,
  "extensions": {},
  "rule": null,
  "possible-duplicate-of": [],
  "notes": [],
  "postings": [
    {"account": "Assets:Bank:Nila Current", "amount": "0.00", "commodity": "INR", "cost": null, "assert": {"amount": "18450.75", "commodity": "INR"}, "notes": []}
  ]
}
```

A varying rule with a saved bill:

<!-- example: Rule -->
```json
{
  "id": "000000000000b001",
  "date": "2026-11-12",
  "status": "confirmed",
  "description": "Willow Energy electricity",
  "schedule": {"every": "month", "from": "2026-11-12", "interval": 1, "anchor-day": null},
  "amount": "varies",
  "lifecycle": "active",
  "origin": {
    "kind": "monthly-history",
    "words": null,
    "entry-ids": [
      "00000000000000a1",
      "00000000000000a2",
      "00000000000000a3"
    ],
    "came-back": false
  },
  "created": "2026-08-14",
  "removal": null,
  "bill": {
    "amount": "1312.40",
    "received": "2026-11-03",
    "pay-by": "2026-11-12",
    "occurrence": "2026-11-12",
    "evidence": [
      "Bills/2026-11-03/Willow-Energy-Bill.pdf"
    ]
  },
  "evidence": [],
  "references": null,
  "classification": null,
  "original-amount": null,
  "reimbursement": null,
  "excluded-from-estimate": null,
  "notes": [
    "Amounts vary with household use."
  ],
  "postings": [
    {"account": "Expenses:Utilities:Willow Energy", "amount": "1240.00", "commodity": "INR", "cost": null, "assert": null, "notes": []},
    {"account": "Assets:Bank:Nila Current", "amount": "-1240.00", "commodity": "INR", "cost": null, "assert": null, "notes": []}
  ]
}
```

The agent's answer for a USD purchase on an INR card, in a ledger that asks for
the fuel extension:

<!-- example: Answer fuel -->
```json
{
  "status": "ready",
  "question": null,
  "entry": {
    "date": "2026-10-02",
    "status": "confirmed",
    "description": "Cedar Studio annual plan",
    "references": {"reference": null, "order-id": ["CS-88213"]},
    "classification": null,
    "original-amount": null,
    "extensions": {"fuel": null},
    "notes": [],
    "postings": [
      {"account": "Expenses:Subscriptions:Cedar Studio", "amount": "48.00", "commodity": "USD", "cost": {"amount": "4123.20", "commodity": "INR"}},
      {"account": "Liabilities:Credit Cards:Mosaic Card", "amount": "-4123.20", "commodity": "INR", "cost": null}
    ]
  },
  "new-accounts": [],
  "doubts": [],
  "attachment-names": [
    "Cedar-Studio-Invoice"
  ],
  "explanation": null
}
```

## Dashboard data

The dashboard build reads format 2 and keeps its own JSON shapes, with these
additions and changes:

- `dashboard.json` `meta.ledger` is the normalised manifest: `format` 2 and
  `extensions` instead of `modules.fuel`;
- `meta.accounts` carries account metadata in full (`null` labels, `[]` aliases);
- `meta.extensions` lists the definitions of the listed extensions (name, `ask`,
  description and fields), and each transaction has `extensions`, its groups;
- `add-patterns.json` `rules` are format 2 rules (with the dashboard's extra
  fields) and `payments` carries account metadata in full;
- Forecast's "Left out of the estimate" no longer shows an "N-year policy" note:
  a policy term is now an extension value.

The fuel screens (the Review Fuel Log, the Fuel Add pattern and its litres
suggestions, the Help paragraph and `fuel-log.json`) switch on when the ledger
lists the `fuel` extension.

## Format 1 to format 2

`make upgrade-ledger` runs `migrations/0002_typed_store.py`. Every format 1
field and where it goes ("same" means the name and value are unchanged; the key
is always present):

### Entries and rules

| Format 1 | Format 2 | Change |
|---|---|---|
| `id`, `date`, `status`, `description` | same | none |
| `sources` (absent when none) | `evidence` | renamed; `[]` when none |
| `serial-number` | `statement.serial` | grouped |
| `statement-end` | `statement.end` | grouped |
| `statement-page` | `statement.page` | grouped |
| `source-page` | `statement.source-page` | grouped |
| `source-row` | `statement.source-row` | grouped |
| `reference` | `references.reference` | grouped |
| `order-id`, and repeats kept in `notes` as `order-id: …` | `references.order-id` | a list, in order; the repeat notes go |
| `classification` | same | `null` when absent |
| `original-amount` | same | `null` when absent |
| `fuel-filled-litres` | `extensions.fuel.litres` | the fuel extension |
| `trip-km` | `extensions.fuel.trip-km` | the fuel extension |
| `reimbursement-group` | `reimbursement.group` | grouped |
| `reimbursement-label` | `reimbursement.label` | grouped |
| `variable-estimate: {excluded: true, reason}` | `excluded-from-estimate: {reason}` | renamed; the constant `excluded` is dropped |
| `policy-term: {years}` | `extensions.policy.term-years` | a ledger extension, `policy`, made only when an entry has one |
| `rule` | same | `null` when absent |
| `possible-duplicate-of` (absent when none) | same | `[]` when none |
| `notes` (absent when none) | same | `[]` when none |
| `postings` | same | each posting written in full |
| rule `schedule.every`, `schedule.from` | same | none |
| rule `schedule.interval` (absent when 1) | same | always present; 1 by default |
| rule `schedule.anchor-day` | same | `null` when absent |
| rule `amount: "varies"` (absent when fixed) | `amount` | `"fixed"` or `"varies"` |
| rule `lifecycle` (absent when active) | same | always present; `"active"` by default |
| rule `origin.kind`, `origin.words` | same | `words` is `null` when absent |
| rule `origin.evidence` | `origin.entry-ids` | renamed (they are entry IDs, not files) |
| rule `origin.came_back` | `origin.came-back` | kebab-case; `false` when absent |
| rule `created` | same | `null` when absent |
| rule `removal.date` | same | none |
| rule `removal.evidence` | `removal.entry-ids` | renamed |
| rule `bill.amount`, `received`, `occurrence` | same | none |
| rule `bill.pay_by` | `bill.pay-by` | kebab-case; `null` when absent |
| rule `bill.sources` | `bill.evidence` | renamed |

### Postings

| Format 1 | Format 2 | Change |
|---|---|---|
| `account`, `amount`, `commodity` | same | none |
| `cost: {amount, commodity, per}` | same | `null` when absent; an inexact `unit` cost becomes `total` (see below) |
| `assert: {amount, commodity}` | same | `null` when absent |
| `notes` | same | `[]` when none |

### `ledger.json` and other documents

| Format 1 | Format 2 | Change |
|---|---|---|
| `format: 1` | `format: 2` | raised |
| `modules.fuel: true` (or entries with fuel readings) | `extensions: {"fuel": {"ask": true}}` | the fuel extension; `ask` is `false` when the module was off but entries held readings |
| (none) | `extensions: {}` | always present |
| accounts `commodities`, `accounts` | same | none |
| Income or Equity metadata `{}` | same | none |
| Expenses metadata `{}` or `{category}` | `{category}` | `category` is `null` when absent |
| account `kind`, `currency` | same | none |
| account `label` | same | `null` when absent |
| account `aliases` | same | `[]` when absent |
| account `statement.reader` | same | none |
| account `statement.identifier`, `statement.options` | same | `null` when absent |
| card statement (no `kind`) | `kind: "card"` | added |
| card statement `cash-advances` (absent when zero) | same | `null` when absent |
| bank statement `source-balance-style` | same | `null` when absent |
| bank statement `assertion-id` | same | `null` when absent (debt-style only) |
| statement member `reference`, `balance`, `entry-id` | same | `null` when absent |
| statement member `review-candidates` | same | `[]` when absent |
| recurring settings `never-automatic` | same | none |
| trash `removed_at`, `added_at` | `removed-at`, `added-at` | kebab-case; `added-at` is `null` when absent |
| trash `rule_changes`, `duplicate_links` | `rule-changes`, `duplicate-links` | kebab-case; typed |
| trash `entry`, and the rules in `rule-changes` | same | converted to format 2 |
| trash `id`, `reason`, `index`, `files`, `kept` | same | none |

Audit lines in `data/additions/` aren't store documents and are never rewritten.
Snapshots in older lines keep the format 1 shape; code that replays one (a
rule's `before` and `after`, a paid bill) converts it on read
(`scripts/ledger/format1.py`). New lines carry format 2 snapshots, and the
answers they record use the kebab-case envelope.

### What the migration converts, refuses and declares

The migration converts every other shape mechanically and prints counts only
(no names, amounts, dates, descriptions, account names or paths):

- **Unit costs that don't balance exactly** become the `total` cost that makes the
  entry balance exactly (`@` becomes `@@` in the journal; native-commodity reports
  are unchanged and at-cost reports may move by the old rounding residue). When
  several unit-cost postings in one commodity share the residue, each becomes its
  value rounded to the places of the entry's other postings in that commodity, and
  the leftover steps go to the largest remainders, so the totals add up exactly and
  none moves by more than one step. A unit cost that already balances exactly stays.
  The migration counts both.
- **An origin kind spelled with spaces** (`subscription words`, from older ledgers)
  is respelled with hyphens. Counted.
- **A repeated `order-id`** becomes a list. The journal prints every value in the
  `order-id` slot, which is where format 1 printed them unless other comment lines
  sat between; the migration counts entries whose comment lines move.
- **A policy term** moves into the `policy` extension; its journal line changes from
  `policy-term: 5 years` to `policy-term: 5`, in the extension slot. Counted.
- **Empty strings** in `classification`, `reference`, `order-id`, `serial-number`,
  `reimbursement-group` and `reimbursement-label` become `null`, and empty paths leave
  `evidence`; format 1 printed an empty comment line for them, format 2 prints none.
  Counted as "empty values cleared".
- **Fuel** is listed only when `modules.fuel` was on.

It stops with a count per kind, and writes nothing, for:

1. a repeat of any other label in `notes`;
2. a repeated `order-id` with the same value;
3. an entry or rule that doesn't balance exactly in each commodity and isn't an
   inexact unit cost, or several unit costs in one commodity whose rounded totals
   can't make up the rest of the entry (mixed signs, or off by more than one step each);
4. a rule that carries a statement field, a fuel reading, a policy term, a `rule`
   link or `possible-duplicate-of`, or a rule posting with an assertion;
5. `reimbursement-label` without `reimbursement-group` (an empty group with a label included);
6. entries with fuel readings in a ledger without the fuel module;
7. anything else the format 2 models refuse (for example, a line break in text the
   journal prints), named by the kind of document only (entries, rules, statements,
   accounts, trash, …), never a file, year or value.

The upgrade runner compares the journal and hledger reports with format 1's own
rendering: identical, except the entries the migration declares (converted unit
costs, policy terms, moved order-ids, cleared empty values), whose blocks are compared by count, and
the at-cost balance when unit costs were converted.
