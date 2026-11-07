# The ledger store, format 2

> **Status: design for approval (step 6h, part 2).** The app still reads and
> writes format 1 (`schema/store.schema.json`). Nothing reads format 2 yet. The
> migration, the readers and writers and `CURRENT_FORMAT` change only after the
> owner approves this design.

A ledger keeps its financial data as JSON under `data/`. Format 2 gives every
entry, posting and recurring rule one typed shape. Every key is always present,
and an empty value is `null` or `[]`.

The shape is defined once, as pydantic v2 models in
[`scripts/ledger/store_models.py`](../scripts/ledger/store_models.py). These files
are generated from the models and checked in; a test fails when they are out of date:

- `schema/store-2.schema.json`: every store document;
- `schema/answer-2.schema.json`: the Add agent's answer, with the fuel module on;
- `schema/answer-2-without-fuel.schema.json`: the same answer, with the fuel module off.

To regenerate them:

```sh
uv run --locked --directory review-dashboard/server python ../../scripts/ledger/store_models.py --write
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
  or `fuel`. A group is `null` when none of its facts is known. It is never an
  object whose values are all `null`.
- **Names** are kebab-case.
- **Amounts** are exact decimal text, never JSON numbers. Trailing zeros are kept
  as written.
- **Text that appears in the journal is a single line.** This covers descriptions,
  notes, evidence and labels.
- **Key order** is the order in the tables below. Writers serialise through the
  models, so files stay byte-stable and diffs stay small.
- **Layout.** UTF-8 without escapes, two-space indentation and a final newline.
  Each posting, and each object that holds only plain values, sits on one line.
  A list of strings has one item per line.

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

A field of a module, marked `x-module`, is part of the answer only while that
module is on. Today that is only `fuel`.

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
| `fuel` | Fuel readings (fuel module). | group | agent | `null` |
| `reimbursement` | The reimbursement this entry belongs to. | object | history | `null` |
| `policy-term-years` | The number of years a policy payment covers. | positive | history | `null` |
| `excluded-from-estimate` | Leaves the entry out of the variable-spending estimate. | object | history | `null` |
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
| `order-id` | The payee's order or invoice number. | line or `null` | agent |

**`original-amount`** (money): `amount` (number, as printed) and `commodity`
(code). Both are set by the agent or a statement.

**`fuel`** (fuel module, set by the agent): `litres` and `trip-km`. Each is a
number or `null`.

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
| `amount` | The price in the cost commodity, as written. | number | agent, statement |
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
| `policy-term-years` | Years a policy payment covers. | positive | history | `null` |
| `excluded-from-estimate` | Left out of the variable-spending estimate. | object | history | `null` |
| `notes` | Comment lines, in order. | list of note | person | `[]` |
| `postings` | The estimated postings of one occurrence. | list of posting | person, server | never |

A rule has no `statement`, `fuel`, `rule` or `possible-duplicate-of`. Those
describe a payment that happened.

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
is the posting inside it.

| Field | Meaning | Empty |
|---|---|---|
| `status` | `ready`, `question` or `not_expense`. | never |
| `question` | One short question when the status is `question`. | `null` |
| `entry` | `date`, `status`, `description`, `references`, `classification`, `original-amount`, `fuel` (fuel module only), `notes` and `postings`. Each posting has `account`, `amount`, `commodity` and `cost` (`amount`, `commodity`). | `null` |
| `new-accounts` | Accounts the entry uses that aren't declared yet. | `[]` |
| `doubts` | Anything the person should check. | `[]` |
| `attachment-names` | One descriptive base name per attachment, without folder or extension. | `[]` |
| `explanation` | One short reason when the status is `not_expense`. | `null` |

Any other field, such as `statement`, `id`, `evidence` or `rule`, or a posting's
`assert`, fails validation. A failed answer is retried like any other error. When
the server stores the entry it fills in the rest:

- `id`;
- `evidence`, from the files it filed for `attachment-names`;
- `rule`;
- a cost's `per` (always `total`);
- `null` or `[]` everywhere else.

The answer schema has no `x-` annotations. Every object in it has
`additionalProperties: false` and lists all its properties as required, as
strict structured output expects.

## Checks on one entry (planned model validators)

Once the design is approved, part 2 adds these as model validators. They need
nothing outside the entry:

- **Exact balance per commodity.** The postings sum to exactly zero in each
  commodity, with no rounding tolerance.
  - A posting with a cost counts in its cost commodity: the `total` amount, or
    the `unit` price times the quantity. It is signed like the posting.
  - Postings in different commodities without costs pass only when each
    commodity sums to zero on its own, as in an opening-balance entry with two
    currencies.
- **Costs.** A cost is in a different commodity from its posting.
- **Statement row.** `statement.end` requires `serial` and `page`.
- **Duplicate links.** Targets are unique, sorted, and greater than the entry's own ID.
- **Rules.**
  - `date` equals `schedule.from`.
  - A removed rule has `removal`.
  - `amount: "varies"` and `bill` keep their format 1 conditions: a simple
    two-posting payment in one currency, a positive bill, and the current occurrence.
  - A rule's postings have no `assert`.

Checks across entries stay in part 3's checker. These include ID uniqueness,
declared accounts and commodities, account currencies, balance assertions,
statement membership, rule references and evidence files.

## The journal is unchanged

Format 2 renders exactly the same hledger journal as format 1. The comment lines
come out in the same order:

1. `id:`;
2. one `source:` line per `evidence` item;
3. then `serial-number` (`statement.serial`), `source-row`, `source-page`,
   `statement-page` (`statement.page`), `statement-end` (`statement.end`),
   `reference`, `order-id`, `classification`, `original amount`,
   `fuel-filled-litres` (`fuel.litres`), `trip-km`, `reimbursement-group`,
   `reimbursement-label`, `variable-estimate` (rendered `excluded, <reason>`) and
   `policy-term` (rendered `N year(s)`);
4. then `notes`.

A `null` field renders nothing, as an absent field did in format 1.

## Examples (fictional)

An Add with the fuel module on:

<!-- example: Entry -->
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
  "references": {"reference": "TXN-55120", "order-id": null},
  "classification": null,
  "original-amount": null,
  "fuel": {"litres": "8.00", "trip-km": "240.0"},
  "reimbursement": null,
  "policy-term-years": null,
  "excluded-from-estimate": null,
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
has an unsettled possible duplicate:

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
  "references": {"reference": null, "order-id": "CS-88213"},
  "classification": null,
  "original-amount": {"amount": "48.00", "commodity": "USD"},
  "fuel": null,
  "reimbursement": null,
  "policy-term-years": 1,
  "excluded-from-estimate": {"reason": "forecast separately"},
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
  "fuel": null,
  "reimbursement": null,
  "policy-term-years": null,
  "excluded-from-estimate": null,
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
  "policy-term-years": null,
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

The agent's answer for a USD purchase on an INR card:

<!-- example: Answer -->
```json
{
  "status": "ready",
  "question": null,
  "entry": {
    "date": "2026-10-02",
    "status": "confirmed",
    "description": "Cedar Studio annual plan",
    "references": {"reference": null, "order-id": "CS-88213"},
    "classification": null,
    "original-amount": null,
    "fuel": null,
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

## Format 1 to format 2

Every format 1 field and where it goes. "Same" means the name and value are
unchanged. The only difference is that the key is always present.

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
| `order-id` | `references.order-id` | grouped |
| `classification` | same | `null` when absent |
| `original-amount` | same | `null` when absent |
| `fuel-filled-litres` | `fuel.litres` | grouped |
| `trip-km` | `fuel.trip-km` | grouped |
| `reimbursement-group` | `reimbursement.group` | grouped |
| `reimbursement-label` | `reimbursement.label` | grouped |
| `variable-estimate: {excluded: true, reason}` | `excluded-from-estimate: {reason}` | renamed; the constant `excluded` is dropped |
| `policy-term: {years}` | `policy-term-years` | flattened |
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

### Other documents

| Format 1 | Format 2 | Change |
|---|---|---|
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
| other card statement fields | same | none |
| bank statement `source-balance-style` | same | `null` when absent |
| bank statement `assertion-id` | same | `null` when absent (debt-style only) |
| other bank statement fields | same | none |
| statement member `reference`, `balance`, `entry-id` | same | `null` when absent |
| statement member `review-candidates` | same | `[]` when absent |
| other statement member fields | same | none |
| recurring settings `never-automatic` | same | none |
| trash `removed_at`, `added_at` | `removed-at`, `added-at` | kebab-case; `added-at` is `null` when absent |
| trash `rule_changes`, `duplicate_links` | `rule-changes`, `duplicate-links` | kebab-case; typed |
| trash `entry`, and the rules in `rule-changes` | same | converted to format 2 |
| trash `id`, `reason`, `index`, `files`, `kept` | same | none |

Audit lines in `data/additions/` aren't store documents and are never rewritten.
Snapshots in older lines keep the format 1 shape. Any code that replays one, for
example a rule's `before` and `after`, converts it on read.

## Format 1 shapes that need a decision at migration

The migration converts every other shape mechanically. For each of these it
reports counts, and stops unless an option or the owner's decision covers the case:

1. **"First occurrence only" labels.** Format 1 kept the first `serial-number`,
   `reference`, `order-id`, `classification`, `reimbursement-*`, `statement-end`
   or similar label as a field. Any repeat stayed in `notes` as `label: value`.
   - Format 2 has one value per field, so a repeated label is an error at
     migration, unless the owner says that label really repeats.
   - Where it does, the field becomes a list.
   - The journal changes only in comment order.
2. **Unit costs that don't balance exactly.** A per-unit cost (`@`) that doesn't
   make the entry sum to exactly zero becomes a `total` cost (`@@`).
   - The total is the exact amount that balances the entry, which is what hledger inferred.
   - The journal line changes from `@` to `@@`. Native-commodity reports are
     unchanged, and cost reports may move by the old rounding residue.
   - The migration reports how many it changed. New purchases always save a total cost.
3. **Other entries that don't balance exactly per commodity.** These stop the
   migration with their IDs. No conversion is guessed.
4. **Rules that carry entry-only fields.** These are statement fields, fuel
   readings, a `rule` link or `possible-duplicate-of`, which format 2 rules don't
   have. Each is reported, and it stops the migration.
5. **Rule postings with an assertion.** Reported; stops the migration.
6. **`reimbursement-label` without `reimbursement-group`.** Reported; stops the migration.
7. **Line breaks in text that renders into the journal.** These can't exist in a
   ledger whose journal passes hledger. Reported; stops the migration.

These are kept as they are, and counted only for the record:

- entries that cite the same evidence twice;
- app-written notes with a fixed prefix (`statement merchant:`, `statement charge:`,
  `statement printed date:`, `Currency estimate:`). Moving them into fields would
  reorder the journal's comment lines.
