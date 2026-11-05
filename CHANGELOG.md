# Changelog

All notable changes to AgentExpense are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow
[Semantic Versioning](https://semver.org/) as described in
[docs/VERSIONS.md](docs/VERSIONS.md).

Every release has an **Upgrade notes** section. Its numbered steps are written so a
person or a coding agent can follow them in order from the application root, with
the ledger selected by `LEDGER_DIR` (or kept at `ledger/`). Stop at the first step
that fails and keep its output.

## [Unreleased]

## [0.1.0] - Unreleased

The first release. The date is set when the release is tagged.

### Added

- **Ledger and checks.** JSON in a separate ledger repository is the source of
  truth; a generated hledger journal is checked strictly on every save
  (`make verify-data`). Ledger format 1: `ledger.json` holds settings and
  `data/accounts.json` holds account metadata. Format 0 ledgers upgrade with
  `make upgrade-ledger`, which compares outputs and rolls back on failure.
- **Accounts and currencies.** Each account keeps its own currency; totals and
  charts stay separate per currency, and conversions are asked for only when an
  expense's currency differs from the paying account's.
- **Dashboard.** Add, Queue, Review, Explorer, Atlas, Journal, Forecast and
  Accounts, with Rules, Trash and Settings, in Light and Dark, built from generated
  data (`DASHBOARD_DATA_DIR` chooses where it goes).
- **Add with an agent.** Typed words, receipts and statements become drafts in a
  per-ledger Queue. A restricted agent proposes entries; the server validates and
  writes them with an audit line and a reviewable commit. Adapters for Codex,
  Claude Code, Pi, OpenCode and a direct API, each pinned and checked offline.
  Drafts survive agent outages and can be finished by hand.
- **Recurring rules and bills.** Rules guessed while typing or made from repeated
  payments; rules whose amount varies, with saved bills that stay unpaid until a
  payment is recorded.
- **Corrections, Delete and Trash.** Saved entries can be corrected, deleted within
  the ledger's time limits and restored from Trash with their evidence.
- **Statements.** A reader interface (version 1) with generic CSV and spreadsheet
  readers, an example card statement reader for a fictional layout, bank statement
  summaries and confirmed balances.
- **Review on the server.** Possible duplicates and other decisions are applied by
  the server with audit lines; periodic reviews follow
  `review-dashboard/server/WEEKLY-REVIEW.md`.
- **Optional modules.** Fuel, investments with price sources, an older-ledger
  lookup and the weekly review, each off unless `ledger.json` turns it on.
- **Tooling.** `VERSION` as the single application version (`make check-version`),
  `make doctor` for offline prerequisite checks, fictional fixture ledgers
  (`make personas`), offline test suites with fake agents, headless browser
  suites, and a personal-data scanner (`make scan`). `npm ci` runs only when the
  installed packages differ from `package-lock.json` by version (`make install`
  covers both dependency sets).
- **Updates and releases.** `make update` moves an installation to the newest
  release tag: doctor, a clean checkout, the changelog since the installed version,
  confirmation (or `--yes`), a detached checkout of the tag, the ledger upgrade,
  verification, the dashboard build and the offline tests, and a restart of a
  service named in `local.json`; any failure after the checkout restores the previous
  checkout, the ledger checkpoint and the dashboard. `make upgrade-ledger` runs a
  chain of migrations, rehearsing it on a scratch copy first. An optional update
  notice in Settings (off unless `local.json` turns it on) reads the remote's tags at
  most daily. GitHub workflows run CI, propose agent CLI pins weekly and publish
  releases from tags; see [docs/RELEASING.md](docs/RELEASING.md).
- **Setup.** [SETUP.md](SETUP.md) walks a coding agent through setting up a ledger.
  `make new-ledger` creates one from `templates/ledger/` as its own repository with a
  verified first commit; `make add-account` and `make opening-balances` declare
  accounts and opening balances under the ledger lock; `make demo` runs a fictional
  ledger from `/tmp` with an offline fake agent; `make serve` starts the dashboard
  locally at `http://127.0.0.1:8701/expenses/`; `make private-checks` runs a
  ledger's own `checks/`. A ledger with no
  accounts opens the first-run screens in the dashboard: About you, then either
  Start from today (accounts and opening balances) or Bring in past statements
  (files read by built-in readers or the agent, each with its account and currency),
  then Add. Deployment examples
  for local use, systemd, launchd, a reverse proxy and a tailnet are in
  [docs/deploy/](docs/deploy/README.md).
- **General policy.** The general bookkeeping policy is
  [policy/BOOKKEEPING.md](policy/BOOKKEEPING.md); a ledger's `AGENTS.md` keeps only
  personal rules. The server gives the dashboard's agent both, read on every run.

### Upgrade notes

There is no earlier release. For a ledger made during development before this
release:

1. Run `make doctor`. Fix every row marked `FAIL`; a `warn` on the Ledger row is
   expected for an older format.
2. Make sure the ledger's working copy is clean (`jj st` or `git status` in the
   ledger) and that nothing else is writing to it (stop the server).
3. Run `make upgrade-ledger ARGS="--dry-run"` and read the proposed manifest. If it
   stops and lists accounts, supply metadata with `--accounts`, as
   [migrations/README.md](migrations/README.md) describes.
4. Run `make upgrade-ledger` with the same arguments. Note the checkpoint commit it
   prints.
5. Run `make verify`, then `make dashboard`.
6. Run `make doctor` again; the Ledger row should now be `ok`.

7. The general policy now ships with the application. If the ledger's `AGENTS.md`
   repeats general rules, keep only personal rules there (see
   `templates/ledger/AGENTS.md`) and commit with `make commit`. A full older policy
   still works; the agent then reads some rules twice.
8. If you ran the dashboard from an edited copy of
   `review-dashboard/server/expense-dashboard.service`, move your access settings into
   a drop-in as [docs/deploy/systemd.md](docs/deploy/systemd.md) describes, restore
   the tracked file, and run `make service` with `LEDGER_DIR` set.

To go back, use the previous application checkout with the checkpoint commit from
step 4.
