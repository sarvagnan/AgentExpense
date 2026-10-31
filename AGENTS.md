# AgentExpense developer instructions

The application repository owns `scripts/`, `schema/`, `review-dashboard/`, tools and examples. A separate ledger repository at `ledger/` or `LEDGER_DIR` owns financial JSON, prices, evidence and archives. Read the selected ledger's `AGENTS.md` before adding, changing or reconciling expenses. Generated `expense_ledger.ledger` belongs inside that ledger.

Ledger format 1 requires `ledger.json` and account metadata in `data/accounts.json`. For an older ledger, run `make upgrade-ledger ARGS="--dry-run"` to inspect the plan, then `make upgrade-ledger`; see `migrations/README.md` for options and rollback.

Python and Node share ledger-root helpers in `scripts/`: use `LEDGER_DIR`, otherwise application `ledger/`. Relative values resolve from caller cwd; pass an absolute value when changing directories. `--root` on Python ledger CLIs selects a ledger; schemas always come from application `schema/`.

Keep experiments in ignored `tmp/`; maintained code belongs in `scripts/`, `review-dashboard/`, `tools/`, `migrations/` or `fixtures/`. After ledger-code changes run `make verify`. After dashboard changes run `npm --prefix review-dashboard run data`, `npm --prefix review-dashboard test` and `npm --prefix review-dashboard run build`; `make dashboard` verifies and builds both. After server changes run `make test-server`. `make test` runs ledger, migration and offline server tests; verification runs only ledger tests to avoid recursion. `make dashboard` also runs the disposable-repository migration tests; `make test-migrations` runs them separately. Run `make personas` for every fictional fixture under `fixtures/ledgers/`. For refactors retain a baseline and require a byte-identical journal, identical hledger reports and dashboard JSON except `meta.generatedAt`. For documentation changes check referenced paths and commands.

Run the personal-data scan before committing: `make scan LIST=/path/to/private-identifiers.tsv`.

## Live drafts, locking and agent isolation

`review-dashboard/server/` owns the API and one FIFO worker per ledger. Drafts, uploads, run records and rollback journals live under `LEDGER_DIR/drafts/`; they are temporary and ignored, as is `.lock`. Completed draft records last 24 hours and uploads are removed on completion. Ordinary lists omit completed drafts. Pending drafts are not expenses.

Every writer must cooperate with `scripts/ledger/ledger_lock.py`; never unlink `.lock`. `make commit m="…"` holds the lock through verification, ledger commit and main bookmark update. The price job uses the same lock; keep `scripts/prices/update_fund_prices.sh` as its entry point and reserve `--ledger-locked` for the helper. The server waits for a clean ledger working copy before applying. Ledger commits use Jujutsu colocated with git; initialize personal ledger repositories separately from this application.

Dashboard additions use this reviewable commit format. Typed words and questions use JSON quoting so embedded newlines cannot forge metadata; omit Asked lines when there were no questions:

```text
Add <entry description> via dashboard

Typed: "<typed words>"
Saved: <YYYY-MM-DD HH:MM> Asia/Kolkata
Total: <payment amount and commodity>, <payment account>
Evidence: <filed paths, comma-separated>
Asked: "<question>" Answered: "<answer>"
Agent doubts: <doubts joined with "; ", or none>
New accounts: <list, or none>
Draft: <draft id>
Added-by: dashboard agent (<model>, <effort>)
```

Read the ledger policy at run time. Commands remain disabled for the agent; only its read-only MCP lookup server reads the store and optional older ledger. Offline tests use fake agents and never models. After agent isolation changes run `make agent-check`; see `review-dashboard/server/README.md` for the automatic gate, allow-lists and maintainer-only Codex upgrades. The operator signs in once with `make agent-login`; the server never copies or links authentication into its dedicated home.

## Audit, statements and review

`LEDGER_DIR/data/additions/<year>.jsonl` is append-only. Draft events record added, failed, not-expense and discarded outcomes with typed words, questions, answers, runs and doubts. Statement events record reconciliation and decisions; review events record verdicts. `schema/additions.schema.json` defines the format and `make verify` checks it. An addition and its audit line share a commit; other outcomes have record-only commits. Read it with `uv run --with jsonschema python scripts/ledger/audit.py list --since 7d` or `--unreviewed`.

Statements reconciles card statement PDFs using the example layout in `scripts/statements/README.md`: passwords stay in memory, PDFium extracts text and printed totals must agree before rules run. `statement_rules.py` settles safe matches; users decide the remainder. Apply files the PDF, membership, summary and rows, appends an audit line and commits through `make commit`, including membership checks. Manual reconciliation follows the selected ledger's policy.

Follow `review-dashboard/server/WEEKLY-REVIEW.md` for periodic reviews. Record verdicts with `audit.py review` as one locked, verified commit, or change nothing. A verdict never authorizes an entry correction without user agreement.
