# Setting up AgentExpense

This guide is for a **coding agent** setting up AgentExpense with a person, in a
terminal, from a clone of this repository. Read [AGENTS.md](AGENTS.md) first. Then
go through the steps below in order. Explain each step to the person in a sentence
before you run it, and ask rather than guess: every name, currency, account and
amount comes from the person.

## Rules for the whole setup

- **Nothing personal goes in the application tree** (this repository). The person's
  data lives in their own ledger, a separate repository; machine settings live in the
  application's `local.json`, which git ignores. Never edit tracked application
  files during setup: `make update` refuses to run while the application has local
  changes. At the end, `git status` in the application must show nothing to commit.
- **Some steps need the person, not you:**
  - answering the questions below;
  - signing in to the in-app agent (`make agent-login`), in their own terminal or
    browser;
  - typing statement passwords, only into the dashboard when uploading a statement.
    The server keeps them in memory and never stores them. Never ask for a password
    in chat, and never put one in a file, a command or an environment variable;
  - agreeing before anything uses the network (`fnm install`, `make install`) or
    runs a model (`make agent-check LIVE=1`, and the first `make serve` after they
    sign in: see step 6);
  - choosing whether the dashboard is reachable from other devices.
- Run every command from the **application root**. After step 2, pass the ledger as
  an absolute path in `LEDGER_DIR` on every command (`export` it if your shell keeps
  variables between commands).
- Never push the ledger anywhere unless the person asks. If they want a backup
  remote, it must be private.
- When a command fails, read its message: it says what to do. Never change
  application code to get past a check.

## 1. Check the machine

```sh
make doctor
```

It checks Python, fnm and the pinned Node, npm, uv, hledger 1.25 or later, jj or
git, installed packages, the selected ledger and the agent adapter, offline, and
prints a fix for each problem. It exits non-zero only for blocking problems (FAIL).
Typical fixes, with the person's agreement since they download:

```sh
fnm install "$(cat .node-version)"   # the pinned Node
make install                         # dashboard and server packages from the lockfiles
```

`make install` runs both of the fixes `make doctor` prints for missing packages
(`npm --prefix review-dashboard ci` and `uv sync --locked --directory review-dashboard/server`).
Install missing tools (Python, uv, hledger, jj) with the person's usual package
manager. Jujutsu (jj) is required even when git is installed: `make commit` and the
dashboard save through it. Run `make doctor` again until it ends with `Ready.` Before
setup, the Ledger row says no ledger is selected; that is expected.

The ledger commands (`make new-ledger`, `make verify-data`, `make commit`) run in the
server's locked environment (`uv run --locked --project review-dashboard/server`, the
Makefile's `LEDGER_PYTHON`), which `make install` creates. Once it has run,
`export UV_OFFLINE=1` keeps them offline for the rest of the setup; without it,
include them when you ask about the network.

Ledger commits carry the jj user name and email. Check them with
`jj config get user.name` and `jj config get user.email`; if either prints nothing, ask
the person what to use and set it with `jj config set --user user.name "…"` (and
`user.email`). Otherwise every commit is saved with an empty author.

Optionally show the person the app first, on a fictional ledger:

```sh
make demo                 # or PERSONA=jordan (USD), PERSONA=sam (GBP and EUR), PERSONA=empty
```

It copies the application and a fictional ledger to a new folder in `/tmp`, starts
the server on 127.0.0.1 with an offline fake agent (no model, no network), and prints
the address. Ctrl-C stops it and removes the folder. It never reads or changes the
person's ledger or settings.

## 2. Create the ledger

Ask:

1. **The name** to show in the dashboard (their own name, or a household's).
2. **The main currency**, as an ISO 4217 code such as `USD`, `EUR` or `INR`. New
   accounts start in it. Each account keeps its own currency, so someone with
   accounts in two countries just has accounts in two currencies; totals are never
   converted.
3. Optionally a **locale** for number and date formats, such as `en-US` or `en-GB`.
   Without one, each browser uses its own.
4. **Where to keep the ledger.** A folder outside the application, for example
   `~/Documents/expense-ledger`, is best. The application's own `ledger/` folder
   also works: git ignores it and it is the default when `LEDGER_DIR` is unset.

```sh
make new-ledger DIR=/absolute/path/to/expense-ledger NAME="Their Name" CURRENCY=USD LOCALE=en-US
export LEDGER_DIR=/absolute/path/to/expense-ledger
```

This copies [`templates/ledger/`](templates/ledger/README.md), fills in `ledger.json`
(a new id, the name, the currency), checks it with `make verify-data`, and makes the
first commit in a new Jujutsu (jj) repository colocated with git, so git commands
work on it too. With `VCS=git` it makes a plain git repository instead; the dashboard
and `make commit` save through jj, so then run `jj git init --colocate "$LEDGER_DIR"`
before the first save (`make doctor` warns until you do). The ledger's `README.md`
explains its layout.

## 3. Accounts

For each account the person wants to track, ask:

| Question | Notes |
| --- | --- |
| Kind | `bank` (current, checking or savings), `card` (credit card), `cash`, `wallet` (a prepaid balance), `loan`, `investment`, or `person` (money owed between them and someone) |
| Name | What they call it, for example "Example Checking". It becomes the label |
| Currency | Defaults to the main currency; ask only if it might differ |
| Other words for it | Aliases, so "paid by card" or "from checking" finds it |
| Statements (bank and card) | Optional: which reader reads its statements, and what the statement prints to identify the account (usually the last four digits) |

Built-in statement readers are `example-card-pdf` (the documented example card
statement layout), `generic-csv` and `generic-sheet` (CSV and spreadsheet exports,
configured per account); see [docs/READERS.md](docs/READERS.md). A reader the
person writes for their own bank goes in the ledger's `readers/` folder. With no
reader, the in-app agent can read an unknown statement, and code checks every value
it reads against the file.

Declare each account:

```sh
make add-account ARGS='--kind bank --label "Example Checking" --alias checking'
make add-account ARGS='--kind card --label "Example Card" --alias card --reader example-card-pdf --identifier 1234'
make add-account ARGS='--kind cash --label Cash --alias cash'
make add-account ARGS='--kind bank --label "Travel Account" --currency EUR --alias travel'
```

Account names come from the kind and label (`Assets:Bank:Example Checking`,
`Liabilities:Credit Cards:Example Card`, `Assets:Cash`, `Assets:Wallets:…`,
`Liabilities:Loans:…`, `Assets:Investments:…`, `Assets:People:…`); `--account`
chooses another. Spending categories need no questions now: the in-app agent asks
before it adds a new one. To start with a few, declare them by name:

```sh
make add-account ARGS='--account Expenses:Groceries'
```

Commit the accounts:

```sh
make commit m="Add accounts"
```

**Or in the dashboard.** The person can do this step and the next in the dashboard
instead: a ledger with no accounts opens the first-run screens (About you, How to
start, then either their accounts and today's balances or their past statement
files), and those screens save through the same code and checks as these commands.
In that case:

1. Run step 7 now (`make verify-data`, `make build-dashboard`, `make serve`) and
   have the person open the address it prints and go through the screens.
2. The screens ask only each account's kind, name, currency and balance, and always
   use today's date. Afterwards, add what they leave out by editing the ledger's
   `data/accounts.json`: `aliases` (lower-case words) and, for a bank or card
   account, `"statement": {"reader": "…", "identifier": "…"}` (fields as in
   [docs/READERS.md](docs/READERS.md)). `make add-account` refuses an account that
   already exists. Commit with `make commit m="Add aliases and statement readers"`.
   If the person's records start before today, use `make opening-balances` instead
   of the screens (step 4).
3. Stop the server (Ctrl-C), continue with steps 5 and 6, then run step 7 again so
   the dashboard shows the changes and the server reads `local.json`.

## 4. Choose how to start

Ask which suits them. The two combine: start from today now, and bring in history
later.

**Start from today.** Ask each account's balance today: what is in a bank, cash,
wallet or investment account; what is owed on a card or loan, **including charges
not yet on a statement**; for a person, what they owe (a negative amount when the
person owes them).

```sh
make opening-balances ARGS='--date 2026-11-05 "Example Checking=2400.00" "Example Card=312.40" cash=80'
make commit m="Record opening balances"
```

Use today's date, or, if the person will type in expenses from an earlier day (for
example the first of the month), that day with the balances they had then. Each
balance becomes a balance assertion on that date against
`Equity:Opening Balances`, and the date is saved as `startDate` in `ledger.json`.
Statement rows dated before `startDate` count as covered by the opening balances.
History imported later ends with an assertion equal to the opening balance, rather
than changing it.

**Bring in past statements.** Ask how far back they want to go, then have them
upload the statements in the dashboard after step 7, typing any statement password
there themselves: on the Add page when the accounts are already declared, or in the
first-run screens' "Bring in past statements", which also creates accounts, when
there are none yet. Each statement's printed currency is checked
against its account before anything is saved. In a statement's checklist, one
answer can settle every row for the same payee, and old rows can stay
uncategorised: they go in as pending entries for Review. If the person has older
history they will not import, set `startDate` to the first day their records cover
(with `make opening-balances --date …` for that day, or in `ledger.json` followed by
`make commit`), because rows before it are treated as already covered.

## 5. Personal rules and optional modules

The general bookkeeping policy ships with the application in
[policy/BOOKKEEPING.md](policy/BOOKKEEPING.md); the dashboard's agent receives it
with the ledger's own `AGENTS.md` on every run. Ask whether the person has rules of
their own (who shares which costs, which account pays for what) and write them under
"Personal rules" in the ledger's `AGENTS.md`. Personal rules may add detail; they
never remove a check from the general policy.

Optional modules and extensions are set in the ledger's `ledger.json`
([docs/MODULES.md](docs/MODULES.md), [docs/EXTENSIONS.md](docs/EXTENSIONS.md)):

| Setting | Default | Ask |
| --- | --- | --- |
| `extensions.fuel` | not listed | Do they log fuel fills (litres, distance)? If so, list `"fuel": {"ask": true}` |
| `investments` | on, no price source | Do they hold funds? A price source fetches prices; without one, holdings show their cost |
| `history.oldLedger` | none | Do they have an older hledger journal the agent may search for precedent? |
| `weeklyReview` | off | Do they want a weekly review procedure for an agent to run? |

If they record something on every entry that the core doesn't hold (the client a work
expense was for, say), a ledger extension can hold it; [docs/EXTENSIONS.md](docs/EXTENSIONS.md)
describes the definition file. After listing or changing an extension, run
`make extension-update NAME=…`.

After changing `ledger.json` or `AGENTS.md`: `make commit m="Set modules and personal rules"`.

## 6. The in-app agent

The dashboard's Add page and statement reading use an agent the person already has.
Ask which one: `codex` (the default), `claude` (Claude Code), `pi`, `opencode`, or a
direct API (`openai` for an OpenAI-compatible endpoint, `anthropic`). Record the
choice in the application's `local.json`, which git ignores, for example:

```json
{"agent": {"adapter": "claude", "model": "claude-opus-5-5", "effort": "high"}}
```

Then:

```sh
make agent-login ADAPTER=claude    # the person signs in; skip for a direct API
make agent-check ADAPTER=claude    # offline: renders what the model would see, checks the allow-lists
```

The agent runs in its own home under the server's state folder; the application never
copies or links the person's own agent sign-in or settings. A direct API needs its
key in an environment variable or a key file, never in `local.json`. OpenCode cannot
remove its built-in shell tools and is refused unless the person explicitly opts in
with `"allowUnisolated": true`; explain that before offering it.
`make agent-check LIVE=1 ADAPTER=…` adds one small live probe, which runs a model
once: ask first. The server runs the same probe by itself the first time it starts
with a new adapter, model or effort (it answers one fictional account lookup), so the
first `make serve` after sign-in also runs a model once; ask before it. A passing
probe is remembered. Until the person signs in, Add shows a notice with the
`make agent-login` command and built-in statement readers still work.
The server reads `local.json` when it starts: restart it after changing the agent.
[docs/ADAPTERS.md](docs/ADAPTERS.md) has every option.

## 7. Verify, build and start

```sh
make verify-data        # the store, the JSON strict check, the generated journal and strict hledger
make build-dashboard    # dashboard data and the static build for this ledger
make serve              # http://127.0.0.1:8701/expenses/, Ctrl-C stops it
```

`make serve` serves only this computer and lets it save changes; see
[docs/deploy/local.md](docs/deploy/local.md). If port 8701 is taken, choose another
with `EXPENSES_PORT=8702 make serve`. After changing ledger files by hand (accounts,
modules, personal rules), commit them and run `make build-dashboard` again, then
reload the page. Show the person around:

- **Add**: type a few words, attach receipts or drop a statement. The agent proposes
  an entry and the server checks it; nothing is written until it passes.
- **Queue**: items waiting for the agent or for an answer. A pending item is not an
  expense. Discarded items go to the Queue's Trash.
- **Review**: a count of everything waiting, red when something is important, such
  as a possible duplicate.
- **Edit and Delete** on saved entries. Edit keeps the entry's identity; to reverse an
  edit, edit again (there is no Undo). Delete is offered only within the Delete time
  limit and while no filed statement covers the entry; deleted entries wait in Trash.
  The time limits are in Settings.
- **Explorer, Atlas, Journal, Forecast and Accounts** for looking at the money.

If the ledger keeps its own checks in `checks/`, run them with `make private-checks`
([templates/ledger/checks/README.md](templates/ledger/checks/README.md)).

## 8. Optional: start at login, or reach it from other devices

Ask whether the dashboard should start at login, and whether they want to open it
from a phone or another computer. [docs/deploy/README.md](docs/deploy/README.md) has
examples for a systemd user service (`make service`), a launchd agent, a reverse
proxy with a base path, and a tailnet or VPN, with the allowed hosts and sign-in
names each needs and their security notes. Never expose the dashboard to
the internet without a proxy that signs people in.

For `make update` to restart a service after an update, name it in `local.json`:
`{"service": {"systemd": "expense-dashboard.service"}}` or
`{"service": {"launchd": "<label>"}}`.

## 9. Updates

`make update` moves the application to the newest release, upgrades the ledger's
format if needed, rebuilds and tests, and rolls everything back if a step fails.
The dashboard can also show when a newer release exists, but that contacts GitHub
once a day, so it is off unless `local.json` turns it on
(`{"updates": {"notice": true}}`). See [docs/RELEASING.md](docs/RELEASING.md).

## Finish

- `git status` in the application shows nothing to commit (`local.json` is ignored).
- `jj st` (or `git status`) in the ledger shows no changes.
- `make doctor` ends with `Ready.` and its Ledger row shows the ledger.

Tell the person where their ledger is, the `LEDGER_DIR` value, how to start the
dashboard, and that `make update` keeps the application current.
