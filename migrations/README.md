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
