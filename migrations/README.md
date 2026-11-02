# Ledger migrations

The application reads format 1. A ledger without `ledger.json` uses format 0. Select an absolute `LEDGER_DIR` pointing to a separate, clean jj-colocated or plain git ledger repository, then run from the application root:

```sh
make upgrade-ledger ARGS="--dry-run"
make upgrade-ledger ARGS="--name 'My ledger' --locale en-IN"
```

The runner locks the ledger, records its checkpoint commit, upgrades, validates the store and hledger journal, compares journal/reports/dashboard data and commits the upgrade. A failure restores the checkpoint and the original store and generated-journal bytes; the lock file remains. A second run is a no-op. `make test-migrations` exercises upgrades in disposable repositories; `make dashboard`, `make test` and the persona matrix include it. Dry runs validate and print the proposed manifest without writing or committing.

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
uv run --with jsonschema python scripts/ledger/compare_outputs.py "$LEDGER_DIR" --save-baseline /tmp/expense-baseline
uv run --with jsonschema python scripts/ledger/compare_outputs.py /tmp/expense-baseline "$LEDGER_DIR"
```

Use repeatable `--ignore '$.path.to.field'` or JSON Pointer paths only for explicitly declared dashboard changes. Wildcards match array indexes or object keys. Saved baselines contain financial data: keep private baselines outside the application. To go back after a successful upgrade, use the previous application version with the checkpoint commit printed by the runner. This first migration creates only `ledger.json`, updates `data/accounts.json` and regenerates the ignored journal; audit lines are never rewritten.

Dashboard comparisons for format 0 use the proposed format-1 manifest and account
metadata on both sides. This checks the unchanged entries under the same current
classification rules; new manifest fields have no format-0 counterpart. Journal
and hledger report comparisons remain exact.

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
request IDs/hashes and changes; undo retains its original audit ID and reversing
entry ID. `rule-review` events record confirm/propose-removal plus reason/reviewer;
the owner's removal/keep answer is a separate line. Existing lines are unchanged.
See [Recurring rules](../docs/RULES.md) for exact fields, API and CLI contracts.

The 0 → 1 migration reports the number of already-paid stale rules and prints
`make rules-catch-up`. It does not advance or otherwise modify a rule. Journals
retain existing schedules unless the operator explicitly catches them up.
