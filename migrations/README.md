# Ledger migrations

The application reads format 2. A ledger without `ledger.json` uses format 0. Select an absolute `LEDGER_DIR` pointing to a separate, clean jj-colocated or plain git ledger repository, then run from the application root:

```sh
make upgrade-ledger ARGS="--dry-run"
make upgrade-ledger ARGS="--name 'My ledger' --locale en-IN"
```

The runner locks the ledger, records its checkpoint commit, upgrades, validates the store and hledger journal, compares journal/reports/dashboard data and commits the upgrade. hledger is optional: without it the runner prints one line saying so and skips only hledger's strict check and the hledger report comparison; the models, the JSON strict check and the journal and dashboard comparisons still run.

A ledger several formats behind runs every migration in turn. This app reads only the current format, so the runner first rehearses the whole chain on a scratch copy (planning and applying each step, then validating the result), and `--dry-run` does the same. It then applies and commits each step ("Upgrade ledger format k → k+1"), and verifies and compares only the final format; a failure at any step restores the checkpoint. The comparison reads an older `ledger.json` as if it were the current format. A migration module defines `SOURCE_FORMAT`, `TARGET_FORMAT`, `plan(root, options)` returning `(manifest, accounts)`, and `apply(root, result)`. It may also define `check(result)` (run on the rehearsal's result), `describe(result)` (the dry run's lines, instead of the manifest), `targets(root)` (every path it may write, for rollback) and an `allowed` attribute on its result (entry IDs per declared journal change, passed to the comparison). A migration that intends a dashboard change lists those paths in `OUTPUT_CHANGES` (for example `('$.meta.ledger.newField',)`, because the dashboard copies the manifest into `meta.ledger`). Journal and hledger reports must stay identical. `make update` runs this runner after checking out a release; see [docs/RELEASING.md](../docs/RELEASING.md). A failure restores the checkpoint and the original store and generated-journal bytes; the lock file remains. A second run is a no-op. `make test-migrations` exercises upgrades in disposable repositories; `make dashboard`, `make test` and the persona matrix include it. Dry runs validate and print the proposed manifest without writing or committing.

Optional arguments: `--default-currency SYMBOL`, `--start-date YYYY-MM-DD`, `--review-storage-key KEY`, and `--accounts PATH`. Supply metadata overrides as an object keyed by account name, for example:

```json
{
  "Assets:Other:Example": {"kind": "wallet", "currency": "USD", "label": "Example balance", "aliases": ["example"]},
  "Expenses:Corner Store": {"category": "Groceries"}
}
```

Conventional account paths infer kinds. An `Accounts Payable` or `Accounts Receivable` segment at any depth infers `person`. Income and Equity accounts have empty metadata. Expenses accounts may have an optional `category`: a nonblank, single-line display name. The nearest declared category on the account or an ancestor wins; without one the dashboard keeps its top-level category rule (including Food → Food & dining). This applies to transactions, month categories, Explorer, Forecast and Add suggestions, without changing journal accounts or hledger reports. The `--accounts` overrides accept this field for Expenses accounts, including declared parents; Income and Equity still require `{}`. Accounts under every other root require kind and currency, including person-at-root conventions. An unknown kind stops and lists the accounts requiring overrides. Each account uses the single commodity of its postings; multiple posting commodities stop the migration. Without an explicit default, the most frequent balance-sheet posting currency (the cost currency for priced postings) wins, with first occurrence breaking ties. A ledger without such postings requires `--default-currency`. The optional existing fund-price file plus its mapping selects the `mutual-fund-nav` source id. History remains disabled. Missing optional settings receive shared defaults.

Format 0 → 1 declares **no changes** to the generated journal, hledger reports or dashboard JSON. The comparator ignores only the leading journal header comment block and `meta.generatedAt`. It generates outputs in temporary space and prints only pass/fail and differing counts:

```sh
uv run --locked --project review-dashboard/server python scripts/ledger/compare_outputs.py "$LEDGER_DIR" --save-baseline /tmp/expense-baseline
uv run --locked --project review-dashboard/server python scripts/ledger/compare_outputs.py /tmp/expense-baseline "$LEDGER_DIR"
```

Use repeatable `--ignore '$.path.to.field'` or JSON Pointer paths only for explicitly declared dashboard changes. Wildcards match array indexes or object keys. Saved baselines contain financial data: keep private baselines outside the application. To go back after a successful upgrade, use the previous application version with the checkpoint commit printed by the runner. This first migration creates only `ledger.json`, updates `data/accounts.json` and regenerates the ignored journal; audit lines are never rewritten.

Dashboard comparisons for format 0 use the proposed format-1 manifest and account
metadata on both sides. This checks the unchanged entries under the same current
classification rules; new manifest fields have no format-0 counterpart. Journal
and hledger report comparisons remain exact.

## Format 1 → 2: typed store and extensions

Format 2 is the typed store in [docs/STORE.md](../docs/STORE.md): every entry, posting
and rule is written in full in kebab-case, with related facts grouped, and every
document is read and written through the pydantic models in
`scripts/ledger/store_models.py` (Python) or validated against the generated
`schema/store.schema.json` (Node). Fuel and the policy term leave the core and become
[extensions](../docs/EXTENSIONS.md). `migrations/0002_typed_store.py`:

- writes every store document in its format 2 shape: `sources` becomes `evidence`;
  the statement labels become `statement`; `reference` and `order-id` become
  `references`, with `order-id` a list (a repeated order-id label becomes one list);
  reimbursement, variable-estimate and original amount become groups; trash items
  become kebab-case; rules gain `lifecycle`, `amount`, `origin`, `bill` and the rest
  in full; account metadata is written in full;
- turns `modules.fuel: true` into `"extensions": {"fuel": {"ask": true}}` and moves
  each entry's fuel readings into `extensions.fuel`; it never lists an extension the
  owner didn't turn on;
- moves `policy-term` into a ledger extension, `policy` (`extensions/policy.json`,
  `term-years`, `whole-number`, `filled-by: person`, listed with `"ask": false`), made
  only when an entry has a policy term;
- turns a per-unit cost that doesn't balance exactly into the total cost that makes
  the entry balance exactly; several in one commodity share the residue, each rounded
  to the entry's places with the leftover steps on the largest remainders; a unit
  cost that already balances stays;
- respells an origin kind written with spaces (`subscription words`) with hyphens;
- clears empty strings, which format 1 allowed and format 2 refuses, in
  `classification`, `reference`, `order-id`, `serial-number`, `reimbursement-group`
  and `reimbursement-label`, and drops empty paths from `sources`; each counts as one
  of the "empty values cleared", and the entry's empty comment line leaves the journal;
- stops, writing nothing, when the models can't hold something: another repeated
  label, an entry that doesn't balance exactly in a commodity, several unit costs in
  one commodity whose rounded totals can't make up the rest, a rule carrying an entry-only field, a rule assertion, a
  reimbursement label without a group (an empty group with a label included), entries
  with fuel readings in a ledger without the fuel module, or a ledger that already has
  `extensions/`. The message gives a count per kind. A converted ledger that still fails
  the format 2 checks stops with the kind of document only (entries, rules,
  statements, accounts, trash, …), never a file name, year or value.

`--dry-run` and the real run print counts only (accounts, entries, rules, statements,
trash items, unit costs converted and kept, order-id repeats made into lists,
entries with fuel or policy values, extensions listed, and the journal entries that
change as declared): no names, amounts, dates, descriptions, account names or paths. The runner rehearses the result on a scratch
copy, loads it with every format 2 check, then compares outputs. The journal stays
byte-identical except the declared entries: converted unit costs (`@` becomes `@@`,
so native-commodity reports stay identical and only at-cost reports may differ in
rounding), policy terms (`policy-term: 5` in the extension slot instead of `5 years`)
repeated order-ids and entries whose empty values were cleared. The dashboard data
differs only in `OUTPUT_CHANGES`. A failure once the migration has written (in
verification, validation or a later step) rolls back and prints counts only: "Verification
failed after migrating; the checkpoint is restored". To see the details, run
`make verify` on a copy, or set `EXPENSES_UPGRADE_DEBUG=1` for the run.

Audit lines are never rewritten. Older audit snapshots (bills, rule and entry
snapshots in format 1) are converted when read; new lines are written in format 2.
Drafts still waiting in the Queue keep an answer in the format 1 shape, which the
format 2 answer check refuses: finish or discard them before upgrading.

The format 1 sections below describe format 1's shapes and are kept as history.

### Unreleased format 1: server Review

Format 1 is not released yet; phase 5 extends it without a format bump. Actual
entries may contain `possible-duplicate-of: [{"id":"<other entry ID>","reason":"amount-date-gap"}]`.
Each pair is stored only on its lexicographically smaller ID, with unique targets
sorted by ID. References must name existing actual entries; forecasts cannot link.
The other reasons are `dissimilar-name`, `several-candidates`,
`overlapping-statements` and `imported-after-start`. The last two are hooks for
future reader/history imports. This metadata does not change financial postings or
journal output. Settled links are removed only by explicit review actions.

Version 1 audit logs accept the new `review-action` kind with `action`,
`request_id`, `request_hash` and `changes`. Actions are resolve, followup,
acknowledge, separate, undo-separate, merge and import. Changes record entry IDs,
before/after hashes, decisions with their original export identity, pair reasons, undo targets and moved evidence/notes
as appropriate. Existing lines are never rewritten. This log supplies the server's
decision history and idempotency receipts. Browser exports retain reviewVersion 2.

Format 1 also accepts balance-style statements in the existing
`data/card-statements.json` file and `statement.options` account configuration.
Legacy card summaries and membership survive the 0 → 1 migration unchanged; no
file rename is necessary. See [the reader/store contract](../docs/READERS.md).

Step 6a extends unreleased format 1 without changing its number. Existing financial
store files are unchanged. Version-1 draft audit records optionally carry
`finished_by: {actor:"user", reason}` and `account_consent: {proposal_hash, accounts,
method}` (`create-and-add` or `hand-ticks`). The canonical SHA-256 covers the exact
retained `proposal`; audit validation recomputes it. Run records additionally accept
`failure_kind`, optional `reset_at`, bounded `stderr_tail` and `normalisations`.
Historical lines remain valid and are never rewritten. Ignored claim/form/pause
state belongs under `drafts/`, outside the financial store.

Step 6b changes generated dashboard data only; it adds no store or audit fields
and needs no migration. `meta.today` records the build's local calendar date,
`meta.defaultMonth` selects its month, and the month bounds include it. An empty
current month has one calendar placeholder with `currency: ""`, zero counters and
empty daily/category arrays. It is navigation data, excluded from monetary
figures and recorded-month averages. Each currency's forecast carries
`currentMonth` and `daysPassed` (zero when its latest entry is in another month),
retains its own latest actual date as `asOf` (empty without entries), and starts
at the current month's first unrecorded day. Its horizon ends after twelve
following months. Profiles include only completed recorded months in that
currency. The Forecast page sums the remaining weekly portions into one final
variable-spending row; weekly source profiles remain available for explanation.
`DASHBOARD_TODAY=YYYY-MM-DD` pins the shared Node/browser helper for tests. The
worker checks at startup and every 30 seconds, rebuilding stale daily data only
under a nonblocking ledger lock with a clean working copy; failures are logged
and delayed ten minutes before another attempt.

Step 6b2 also changes generated data only. `meta.today` is the shared recording
cut-off for the current month's summaries and charts, independently of the latest
actual entry. Current-month aggregates include entries through that date. Each
month/currency adds `comparison`: the previous calendar month's signed expense
summary (`month`, `total`, `count`, `pending`, `categories`, `daily`), or `null`
when that currency was absent. In the current month the comparison includes only
days up to today's day number; older months compare complete months. The same
expense/refund aggregation builds both summaries. An empty calendar placeholder
has `comparison: null`.

Each forecast adds `variableAfterDay`: the last day excluded from the remaining
variable estimate. It is yesterday's day number until an actual in that currency
is dated today, then today's day number. `asOf` and the legacy `daysPassed` retain
their latest-entry meanings; `start` is the later of the day after that entry and
the first of the current month. Scheduled payments between `start` and today
remain expected, appearing at today on the forecast line. No store, audit,
financial posting or ledger format changes are required.

### Unreleased format 1: recurring rules (6c1)

Forecast rules optionally add `lifecycle` (active/paused/ended/removed; omitted
means active), `origin` (`kind`: owner/subscription-words/monthly-history/history-pass,
optional words/evidence/came_back), `created` date, `removal` (date/evidence), and
`schedule.anchor-day` (1–31). Actual entries optionally add `rule: {id, occurrence}`.
Rule references and origin/removal evidence must exist. Only active rules forecast
or match. `data/recurring-settings.json` optionally stores the schema-checked
`never-automatic` account prefixes. Preferences remain ledger data.

Version-1 draft audit lines optionally add `usual_payment` and `rule_changes`
(before/after rule snapshots). New `rule` events record made, edited, paused,
resumed, ended, restored, removed, kept, undone, catch-up and backfill, with durable
request IDs/hashes and changes. Historical Undo events retain their original audit ID and reversing
entry ID. `rule-review` events record confirm/propose-removal plus reason/reviewer;
the owner's removal/keep answer is a separate line. Existing lines are unchanged.
See [Recurring rules](../docs/RULES.md) for exact fields, API and CLI contracts.

The 0 → 1 migration reports the number of already-paid stale rules and prints
`make rules-catch-up`. It does not advance or otherwise modify a rule. Journals
retain existing schedules unless the operator explicitly catches them up.

Step 6c part 2 adds no financial-store or audit-schema fields. Editing drafts may
retain ignored `declined_rules` (unique rule IDs) for guess dismissal. Built
`add-patterns.json` now includes the host's rule snapshot, full planned postings,
account aliases (existing payments metadata), next due and payment history. Live
rule reads include compact `payments` for checking a chosen payment period.
Suggestion dismissal uses the existing removed lifecycle and suppression evidence;
weekly rule verdicts use the existing version-1 `rule-review` schema.

### Format 1: corrections and receipt readings

Version-1 append-only audit events additionally accept `kind: correction`, with
`entry_id`, `operation_id` (32 hex), `request_hash` (64 hex), complete `before` and
`after` snapshots, `changed_fields`, optional-text `reason`, `actor`, `result` and
`rule_changes`. Receipt follow-ups also retain `receipt` (extracted answer, choice,
run and the compared entry totals). Existing lines are never rewritten. The audit
retains operation replay after temporary correction drafts expire. No store fields,
ledger format bump or migration are needed. Correction sources and receipt queue
state are ignored runtime records in `drafts/`.

### Unreleased format 1: confirmed-balance refusals (6f A)

Version-1 draft audit events optionally retain `refusal`: a closed structured
confirmed-balance reason, with account/label, assertion entry/date, exact
asserted/calculated/difference amounts and commodity, structured source,
possible duplicate entry summaries and hledger detail. Correction restrictions
add an optional `restriction` code. Old events remain valid and unchanged.
No financial store field, new card assertion, format bump or migration is added.

### Format 1: varying recurring rules and bills (6f B)

Unreleased format 1 adds optional rule `amount: "varies"` and
`bill: {amount, received, pay_by?, occurrence, sources}`. Absence of `amount` stays
fixed. The Python store and Node reader enforce simple two-posting, balanced,
one-currency payments; bills require a positive amount and the current occurrence.
The stored postings hold the median estimate, so journal generation needs only
the store. Bill evidence is checked like entry evidence. No migration is added.

Version-1 additions audit extends rule actions with `bill-saved`, `bill-removed`
and `bill-paid`, optional bill snapshots and structured `amount_check` with
amount/min/max/currency. Existing text/files/doubts retain typed words and
accepted checks. Existing draft audit doubts now also retain checked usual-payment
proposal doubts. Link/correction/switching share the estimate recompute; Delete
and Restore callers are implemented by Part C. Historical reversal records remain valid.

### Step 6f C: Queue, Trash and retention (format 1)

Format 1 remains unreleased. The optional manifest `retention` object has
`delete_days`, `trash_days` and `saved_hours`; absent means 7 days, 7 days and
24 hours. `data/trash.json` stores removed entry snapshots, original same-day
index, addition/removal times, reason, original/stored evidence paths, shared
citations, duplicate links and rule before/after changes. Exclusive files live in
`data/trash-files/<operation id>/`. Trash IDs are disjoint from actual IDs; rule
origin/removal evidence may refer to retained Trash IDs. Permanent removal drops
those evidence IDs from live rules. Python and Node validate this optional store;
Trash never renders as actual entries or forecasts. No migration is required.

Append-only version-1 `retention` audit events add `retention_action`,
`operation_id`, `request_hash`, selected IDs, details/result and local save context.
Actions are deleted, restored, restore-refused, removed-for-good, queue-trashed,
queue-restored and settings-changed. Previous payment/separation reversal lines
remain valid. Current writes use Delete, Restore, Edit and the shared retention
service instead. Ignored operation journals/receipts live in `drafts/retention/`.
