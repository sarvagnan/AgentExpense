# Setup walkthrough: a fresh agent sets up Jordan from the docs

This is an acceptance test that a coding agent performs by hand, not a script. It
shows that [README.md](../../README.md), [SETUP.md](../../SETUP.md) and the documents
they link are enough to set AgentExpense up for a person, with no knowledge of the
code. Run it after changing setup, the first-run screens, `make serve`, `make demo`,
the template or those documents, and before a release.

The person is Jordan Lee from the fictional fixture
[`fixtures/ledgers/jordan`](../../fixtures/ledgers/jordan/README.md). The walkthrough
succeeds when the new ledger has Jordan's settings, accounts and opening balances,
passes `make verify-data`, builds, and opens in a browser.

## Rules

- **Read only the docs.** Act as an agent that has never seen this repository: follow
  README.md, then SETUP.md, and open only the documents they link. Don't read source
  code, tests or reports to fill a gap. When the docs are unclear, wrong or missing
  something, write it down first, then work out the right step (by trying it, or
  from a command's own message) and carry on.
- **Start from nothing, twice.** Each run starts from a fresh export of the
  application's tracked files under `/tmp`, and creates a new ledger there, as its
  own repository. Never clone over the network and never push.
- **Offline, fictional, no model.** Nothing downloads and no model runs. The in-app
  agent is the offline fake adapter. Every browser is headless (the pinned
  Playwright in `~/.cache/expense-playwright`), with profiles under `/tmp`.
- **Never touch the application checkout**: no edits, no generated files, no commits.

## Preparing each run (harness, not part of SETUP)

These steps stand in for things only the person can do. Record each one in the log
as "needs the person" rather than as a doc problem.

1. Export the tracked files: `jj file list > files.txt` in the checkout, then
   `rsync -a --files-from=files.txt <checkout>/ <run>/app/`. Optionally `git init`
   and commit the export so SETUP's final `git status` check means something.
2. `make install` downloads, so link the installed packages read-only instead:
   one symlink per entry of the checkout's `review-dashboard/node_modules` (except
   `.vite*`) inside a new `review-dashboard/node_modules`, and a symlink
   `review-dashboard/server/.venv` to the checkout's server environment. (The link
   shows as untracked in `git status`; that is the harness, not a finding.)
3. Give jj a fictional identity: a file with `[user] name = "Jordan Lee"` and
   `email = "jordan@example.invalid"`, selected with `JJ_CONFIG`.
4. Export `PYTHONDONTWRITEBYTECODE=1`, `npm_config_update_notifier=false` and
   `UV_OFFLINE=1` (SETUP step 1 suggests the last one once packages are installed).
5. The fake agent replaces `make agent-login` and the server's first live probe.
   Copy `review-dashboard/server/tests/fake_agent.py` to `<run>/fake-agent` with a
   first line `#!<server environment>/bin/python`, make it executable, and write a
   `fake.json` with one step that answers with a question. Start the server with
   `EXPENSES_STATE_DIR=<run>/state`, `EXPENSES_CODEX_HOME=<run>/state/codex-home`,
   `EXPENSES_CODEX=<run>/fake-agent`, `EXPENSES_FAKE_CONFIG=<run>/fake.json` and a
   free `EXPENSES_PORT`, then `make serve` exactly as SETUP says. Don't run
   `make agent-check` against the real CLI; the server's own gate runs against the
   fake agent and must report `ready` at `/expenses/api/health`.

## Jordan's answers

Answer SETUP's questions with these, and nothing else:

| Question | Answer |
| --- | --- |
| Name, main currency, locale | Jordan Lee, USD, `en-US` |
| Where to keep the ledger | `<run>/jordan-ledger` |
| Accounts | the table in Jordan's README: Alder Checking, Meadow Savings, Lumen Card (reader `example-card-pdf`, identifier 4321), Cash, Willow Travel in EUR, each with its aliases |
| How to start | Start from today, with the balances in the README. In the CLI run, the records start on September 1, 2026; in the screens run, today |
| Personal rules | the two in the README |
| Modules | all off (`investments` is on in the template) |
| In-app agent | `codex`, the default; signing in needs the person (the fake agent stands in) |
| Start at login, other devices | no |

## Run A: the CLI path

Follow SETUP from step 1: `make doctor`, `make new-ledger`, `make add-account` for
each account, `make commit`, `make opening-balances --date 2026-09-01 …`,
`make commit`, the modules and personal rules by hand with `make commit`,
`local.json`, then `make verify-data`, `make build-dashboard` and `make serve`.

## Run B: the first-run screens

On a second fresh export, follow SETUP to `make new-ledger`, then take the
dashboard alternative in step 3: run step 7, and in a headless browser at the
address `make serve` prints, go through About you, How to start and Start from
today, filling one row per account by its visible labels (Kind, Account name,
Balance or Amount owed, Currency) and pressing Finish setup. Add opens with the
confirmation. Then follow SETUP's next steps for what the screens leave out: the
aliases and the card's statement reader in `data/accounts.json`, `make commit`,
stop the server, steps 5 and 6, and step 7 again.

## Checks at the end of each run

- `make verify-data` and `make build-dashboard` pass; `make private-checks` says
  there are none; `make doctor` ends with `Ready.` and shows the ledger.
- The page at the printed address loads with no failed request, and Accounts shows
  USD 2,400.00, 3,500.00, 80.00, 125.00 owed and EUR 650.00, dated on the start date.
- `jj st` in the ledger is clean, and `git status` in the export shows only the
  harness's `.venv` link (and nothing at all on a real installation).
- The ledger matches the fixture. `ledger.json` has the same `name`,
  `defaultCurrency`, `locale` and `modules`, and `startDate` 2026-09-01 (run A) or
  today (run B). The balance-sheet accounts in `data/accounts.json` (those with a
  `kind`) are the same, in the same order, with the same `kind`, `currency`,
  `label`, `aliases` and `statement`; `Equity:Opening Balances` is declared; the
  commodities are USD and EUR. Each opening entry (the one posting against
  `Equity:Opening Balances`) has the fixture's account, amount, commodity, balance
  assertion and status, dated on `startDate`. For run A, the opening balances agree
  in hledger too:

  ```sh
  diff <(hledger -f fixtures/ledgers/jordan/expense_ledger.ledger bal desc:Opening -N) \
       <(hledger -f <run>/jordan-ledger/expense_ledger.ledger bal desc:Opening -N)
  ```

## The log

Write one line per step: the command or screen, what happened, and any finding:

```text
A3 make add-account … (5 accounts)   ok; each prints its own commit hint, SETUP commits once (fine)
B2 make serve → blank page           FINDING: assets requested under /expenses/ answer 404 …
```

Number each finding, say whether the docs were unclear, wrong or missing something,
and what you did instead. Then fix the docs (or the code, when the docs describe the
right thing and the code disagrees) and name, for each finding, the fix that answers
it. Repeat both runs on a fresh export of the fixed copy before reporting.

## Clean up

Stop every server you started, and delete the run folders.
