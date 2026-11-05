# AgentExpense

AgentExpense is a personal expense ledger with a dashboard, set up and kept by the
coding agent you already use. You type a few words or drop a receipt or a statement
on the Add page; an agent proposes the entry, and the server checks it and records it
with its evidence and an audit line. JSON in your own ledger repository is the source
of truth, and hledger checks the journal generated from it. Accounts keep their own
currencies, and totals are never converted. Statements are reconciled against your
entries with their printed totals checked first.

The application and your ledger are two separate repositories: the application holds
no personal data, so updates never touch your records except through checked ledger
migrations.

## Quick start

You need Python 3.11+, uv, fnm, hledger 1.25+ and jj (or git). Then:

```sh
git clone <repository URL> AgentExpense && cd AgentExpense
make doctor        # checks the machine offline and prints a fix for each problem
make install       # dashboard and server packages from the lockfiles (downloads)
make demo          # optional: the app on a fictional ledger, at a local address; Ctrl-C ends it
```

Then start your coding agent in this folder and ask it: **"Set up AgentExpense for me,
following SETUP.md."** It creates your ledger with `make new-ledger`, asks for your
name, main currency and accounts, records your starting balances or brings in past
statements, connects the dashboard's agent, and starts the dashboard at
<http://127.0.0.1:8701/>.

## Documentation

- [SETUP.md](SETUP.md): the setup conversation, step by step, for a coding agent.
- [policy/BOOKKEEPING.md](policy/BOOKKEEPING.md): the general bookkeeping policy every
  ledger follows; a ledger's own `AGENTS.md` holds only personal rules.
- [docs/](docs/README.md): accounts and currencies, modules, statement readers, agent
  adapters, recurring rules, running the dashboard, versions and releases.
- [AGENTS.md](AGENTS.md): instructions for agents working on the application itself.
- [CHANGELOG.md](CHANGELOG.md) and the [roadmap](docs/ROADMAP.md).

## Development and checks

Every value in `fixtures/ledgers/` is invented: `asha` (INR, every module),
`jordan` (USD with a EUR travel account), `sam` (a move from GBP to EUR) and `empty`.
From the application root:

```sh
export PYTHONDONTWRITEBYTECODE=1
export LEDGER_DIR="$PWD/fixtures/ledgers/asha"
export DASHBOARD_DATA_DIR="$(mktemp -d /tmp/expense-example-data-XXXXXX)"
make verify
make dashboard
make test-server
npm --prefix review-dashboard test
make test-scanner
```

With no ledger selected (no `ledger/` and no `LEDGER_DIR`), the test targets and
`npm --prefix review-dashboard test` use `fixtures/ledgers/asha/` and say so in one
line, so a fresh clone passes its own tests. `make personas` verifies, builds and tests
every fixture. Server tests use disposable repositories and a fake agent and run no
model; browser suites are separate (`make browser-tests`).

The project selects the Node version in `.node-version` through fnm (supported versions
are in `review-dashboard/package.json`); without fnm, launchers warn and use Node on
PATH. To change Node, run `fnm install <v>`, change `.node-version`, and run
`make personas` while nothing else is running.

`LEDGER_DIR` selects the ledger (default: the application's ignored `ledger/`);
relative paths resolve from the caller's working directory, so pass an absolute path
across commands that change directories. Ledger Python CLIs also accept `--root`.
Schemas stay in the application's `schema/`. Ledgers use format 1: `ledger.json` holds
settings and `data/accounts.json` ordered account metadata; an older ledger stops with
an upgrade instruction ([ledger migrations](migrations/README.md)).

`make commit m="…"` holds the ledger lock while running `make verify-data` (journal
generation, schema, store, audit, evidence and statement checks, and strict hledger
validation) and commits. `make verify` also runs the ledger code tests.

`DASHBOARD_DATA_DIR` selects a generated-output root containing `app/data/` and
`public/data/` (default `review-dashboard/`). A private source stamp follows the
ledger's real path, and builds refuse another ledger's output unless
`DASHBOARD_DATA_REPLACE=1`. Tests and persona runs always use temporary output roots.
See the [dashboard](review-dashboard/README.md) and [server](review-dashboard/server/README.md)
documentation.

Before committing, run `make scan LIST=/path/to/private-identifiers.tsv` with a list
kept outside the repository (`label<TAB>value` per line); output contains only labels
and locations. Review the scanner's limits in `scripts/check_private.py`.

## Versions

The application version is in `VERSION`; `make check-version` confirms the package
files follow it. [CHANGELOG.md](CHANGELOG.md) describes each release with upgrade steps,
and [docs/VERSIONS.md](docs/VERSIONS.md) explains the ledger format, interface and audit
versions. `make update` moves an installation to the newest release and rolls everything
back if a step fails; see [docs/RELEASING.md](docs/RELEASING.md).

## Licence

MIT; see [LICENSE](LICENSE).
