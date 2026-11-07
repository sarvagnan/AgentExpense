# AgentExpense developer instructions

To set up AgentExpense for a person, follow [SETUP.md](SETUP.md); this file and that guide are the only agent instructions (no `CLAUDE.md`).

The application repository owns `scripts/`, `schema/`, `policy/`, `templates/`, `review-dashboard/`, tools and fixtures. A separate ledger repository at `ledger/` or `LEDGER_DIR` owns financial JSON, prices, evidence, archives, personal readers (`readers/`) and private checks (`checks/`). Read the general policy [policy/BOOKKEEPING.md](policy/BOOKKEEPING.md) and then the selected ledger's `AGENTS.md` (personal rules only) before adding, changing or reconciling expenses. Generated `expense_ledger.ledger` belongs inside that ledger. Never write a person's data into the application tree.

`make new-ledger DIR=… NAME=… CURRENCY=…` creates a ledger from `templates/ledger/` with its first verified commit; `make add-account` and `make opening-balances` (`scripts/ledger/setup_ledger.py`) declare accounts and opening balances under the lock; `make demo` runs a fixture copy in `/tmp` with the offline fake agent; `make serve` starts the server locally; `make private-checks` runs the selected ledger's `checks/`; `make bench-save` times saves on a large fictional ledger that `scripts/fixtures/generate_large.py` generates in `/tmp` (never commit its output). Deployment examples are in `docs/deploy/`.

Ledger format 2 is the typed store in `docs/STORE.md`: Python reads and writes every store document through the pydantic models in `scripts/ledger/store_models.py` (`store.py` loads, checks and serialises), and Node validates against the generated `schema/store.schema.json` (`ledger-manifest.mjs`). After changing the models regenerate the schemas with `store_models.py --write` (command in `docs/STORE.md`) and keep `docs/STORE.md` in step. Ledger CLIs run in the server's locked environment (`LEDGER_PYTHON`, `uv run --locked --project review-dashboard/server python`). Ledger-specific entry fields are extensions (`docs/EXTENSIONS.md`): built-in definitions in application `extensions/` (only `fuel`), a ledger's own in its `extensions/`, listed in `ledger.json`; after changing one run `make extension-update NAME=…`. For an older ledger, run `make upgrade-ledger ARGS="--dry-run"` to inspect the plan, then `make upgrade-ledger`; see `migrations/README.md` for options and rollback.

Python and Node share ledger-root helpers in `scripts/`: use `LEDGER_DIR`, otherwise application `ledger/`. Relative values resolve from caller cwd; pass an absolute value when changing directories. `--root` on Python ledger CLIs selects a ledger; schemas always come from application `schema/`.

`DASHBOARD_DATA_DIR` selects generated `app/data/` and `public/data/` under one output root (default `review-dashboard/`); resolve relative values before changing directories. The private source stamp prevents replacing another ledger's data. Use a separate output root per ledger; `DASHBOARD_DATA_REPLACE=1` explicitly replaces existing data and provenance. Tests and fixture runs use temporary output roots.

`VERSION` is the application version; `package.json`, `pyproject.toml` and their lock files follow it, and `make check-version` (part of `make test`) checks them and that `.node-version` lies within `engines.node`. Record changes in `CHANGELOG.md` with Upgrade notes; see `docs/VERSIONS.md`. `make doctor` checks prerequisites offline and exits non-zero only for blocking problems.

`make update` (`ARGS="--yes"`) moves an installation to the newest release tag and rolls back the checkout, the ledger checkpoint and the dashboard on any failure; `docs/RELEASING.md` describes it, how to cut a release and the CI workflows. Releases are detached checkouts of `vX.Y.Z` tags; customisations belong in the ledger or the ignored application `local.json` (`service`, `updates`, `agent`). `make test-update` exercises it between disposable local releases, with a test migration that never belongs in `migrations/`. A release that raises the ledger format changes `CURRENT_FORMAT` in both manifest modules and the schema, adds its migration and migrates `fixtures/ledgers/`; tests use the constant, never a literal format. After workflow changes run `make check-workflows`.

Node is pinned by `.node-version` through fnm. To update it, run `fnm install <v>`, change `.node-version`, then run `make personas` while nothing else is running. Without fnm, launchers warn and use Node on PATH. npm launchers disable the update notifier.

Keep experiments in ignored `tmp/`; maintained code belongs in `scripts/`, `review-dashboard/`, `tools/`, `migrations/` or `fixtures/`. After ledger-code changes run `make verify`. After dashboard changes run `npm --prefix review-dashboard run data`, `npm --prefix review-dashboard test` and `npm --prefix review-dashboard run build`; `make dashboard` verifies and builds both. Saves run `make verify-data` (journal, store checks with the JSON strict check `scripts/ledger/strict_check.py`, then the strict hledger check) under the lock; `make verify` adds ledger code tests for development. After server changes run `make test-server`. `make test` runs ledger, migration and offline server tests; development verification runs ledger code tests, avoiding server-test recursion. `make dashboard` also runs the disposable-repository migration tests; `make test-migrations` runs them separately. Run `make personas` for every fictional fixture under `fixtures/ledgers/`: it runs the full server suite once for the default fixture and only explicitly marked persona-sensitive tests for the others. With no ledger selected (no `ledger/`, no `LEDGER_DIR`), test targets use `fixtures/ledgers/asha` and print one line saying so. `SCANNER_TESTS=<pattern> make test-scanner` selects scanner tests by `unittest -k`. Plain `make test-server` keeps full discovery; `SERVER_TESTS=persona make test-server` selects the guarded subset. For refactors retain a baseline and require a byte-identical journal, identical hledger reports and dashboard JSON except `meta.generatedAt`. For documentation changes check referenced paths and commands.

Run the personal-data scan before committing: `make scan LIST=/path/to/private-identifiers.tsv`.

## Queue, locking and agent isolation

`review-dashboard/server/` owns the API and one FIFO worker per ledger. Queue items, uploads, run records and rollback journals live under `LEDGER_DIR/drafts/`; they are temporary and ignored, as is `.lock`. Completed draft records last 24 hours and uploads are removed on completion. Ordinary lists omit completed drafts. Pending drafts are not expenses.

Every writer must cooperate with `scripts/ledger/ledger_lock.py`; never unlink `.lock`. `make commit m="…"` holds the lock through `verify-data`, ledger commit and main bookmark update. The price job uses the same lock; keep `scripts/prices/update_fund_prices.sh` as its entry point and reserve `--ledger-locked` for the helper. The server waits for a clean ledger working copy before applying. Ledger commits use Jujutsu colocated with git; initialize personal ledger repositories separately from this application.

Dashboard additions use this reviewable commit format. Typed words and questions use JSON quoting so embedded newlines cannot forge metadata; omit Asked lines when there were no questions:

```text
Add <entry description> via dashboard

Typed: "<typed words>"
Saved: <YYYY-MM-DD HH:MM> <±HH:MM> <IANA zone>
Total: <payment amount and commodity>, <payment account>
Evidence: <filed paths, comma-separated>
Asked: "<question>" Answered: "<answer>"
Agent doubts: <doubts joined with "; ", or none>
New accounts: <list, or none>
Draft: <draft id>
Added-by: dashboard agent (<model>, <effort>)
```

Dashboard corrections keep the entry ID and use this reviewable format. Reasons use JSON quoting:

```text
Edit <entry description> via dashboard

Entry: <entry id>
Changed: <field: old → new; …>
Reason: "<optional reason, JSON-quoted>"
Rule: <what happened to a linked rule, or none>
Evidence: <added or moved paths, or none>
Draft: <operation id>
Added-by: user (correction)
```

Receipt readings use the same correction transaction and an `Added-by: <adapter/model> (receipt details for <entry id>)` trailer. Pending receipt readings and questions retain their draft beyond 24 hours.

The server reads `policy/BOOKKEEPING.md` and the ledger's `AGENTS.md` on every run (`review-dashboard/server/bookkeeping_policy.py`); test application copies include `policy/`. Commands remain disabled for the agent; only its read-only MCP lookup server reads the store and optional older ledger. Offline tests use fake agents and never models. After agent isolation changes run `make agent-check`; see `review-dashboard/server/README.md` for the automatic gate, allow-lists and maintainer-only Codex upgrades. The operator signs in once with `make agent-login`; the server never copies or links authentication into its dedicated home.

## Audit, statements and review

`LEDGER_DIR/data/additions/<year>.jsonl` is append-only. Draft events record added, failed, not-expense and discarded outcomes with typed words, questions, answers, runs and doubts. Statement events record reconciliation and decisions; review events record verdicts. `schema/additions.schema.json` defines the format and `make verify-data` checks it. An addition and its audit line share a commit; other outcomes have record-only commits. Read it with `uv run --locked --project review-dashboard/server python scripts/ledger/audit.py list --since 7d` or `--unreviewed`.

Statements reconciles card statement PDFs using the example layout in `scripts/statements/README.md`: passwords stay in memory, PDFium extracts text and printed totals must agree before rules run. `statement_rules.py` settles safe matches; users decide the remainder. Apply files the PDF, membership, summary and rows, appends an audit line and commits through `make commit`, including membership checks. Manual reconciliation follows the selected ledger's policy.

Follow `review-dashboard/server/WEEKLY-REVIEW.md` for periodic reviews. Record verdicts with `audit.py review` as one locked, verified commit, or change nothing. A verdict never authorizes an entry correction without user agreement.

## Varying recurring rules and bills (6f B)

Use `"amount": "varies"` only for a simple two-posting payment in one currency.
The postings hold the median of its last three payments, including matching older
history. Read [docs/RULES.md](docs/RULES.md) before changing varying rules or bills.
A saved bill is unpaid: save it on the rule and retain its evidence; record an
expense only after payment. An amount-check doubt needs the owner's explicit
acceptance and remains in the audit.

Dashboard bill commits use the draft's recorded local time, with Typed JSON-quoted:

```text
Record <rule description> bill via dashboard

Typed: "<typed words>"
Saved: <YYYY-MM-DD HH:MM> <±HH:MM> <IANA zone>
Bill: <amount and commodity>, pay by <YYYY-MM-DD or none>
Evidence: <filed paths, or none>
Agent doubts: <amount-check doubt, or none>
Draft: <draft id>
Added-by: user (bill, rule <rule id>)
```

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

Dashboard retention operations use these commit formats. JSON-quote reasons;
Saved uses the operation's recorded local time, offset and IANA zone.

```text
Delete <description> via dashboard

Saved: <YYYY-MM-DD HH:MM> <±HH:MM> <IANA zone>
Entry: <entry id>
Reason: <JSON-quoted reason, or "">
Rule: <next due or none>
Files to Trash: <original paths, or none>
Files kept: <shared paths, or none>
Draft: <operation id>
Added-by: user (retention)
```

Restore uses `Restore <description> via dashboard`, with Saved, Entry, Reason,
Rule, `Files restored: <original Bills/ paths, or none>`, Draft and Added-by.
Removal uses `Remove <count> entries for good via dashboard`; settings use
`Change time limits via dashboard`. Both retain Saved, Entry (selected IDs or
none), JSON-quoted Reason, Draft and Added-by. The audit retains selected items
or the complete limits before and after. Queue commits use `Discard Queue item
via dashboard` / `Restore Queue item via dashboard` with the same attribution.
