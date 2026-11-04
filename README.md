# AgentExpense

AgentExpense is an expense ledger application with a dashboard, evidence-backed Queue items, statement reconciliation and an audit trail. A restricted Codex agent proposes entries; the server validates and applies them. JSON is the source of truth and hledger checks the generated journal. Accounts retain their own currencies; totals and charts keep currencies separate. Fuel, investments and an example card statement reader are built in. Saved dates follow the browser's local save time; timestamps display in the viewer's zone.

The built-in [card statement reader](scripts/statements/README.md) uses a documented fictional layout.

This is early development. Setup by a coding agent comes in a later release; see the [roadmap](docs/ROADMAP.md).

## Application and ledger

Keep the application and personal ledger in two separate repositories. The default ledger directory is `ledger/`, which the application ignores. Set `LEDGER_DIR` to choose another directory; relative paths resolve from the caller's working directory. Use an absolute path across commands that change directories. Ledger Python CLIs also accept `--root`. Schemas belong to the application in `schema/`. Ledgers use format 1: `ledger.json` holds settings and `data/accounts.json` holds ordered account metadata. An older ledger stops with an upgrade instruction; see [ledger migrations](migrations/README.md).

## Fictional example and checks

Install Python 3.11+, uv, fnm, hledger 1.25+ and jj or git on PATH, then run `make doctor`: it checks these offline, including installed dependencies and the selected ledger's format, and prints a fix for each problem. The project selects the installed Node version in `.node-version` (supported versions remain in `review-dashboard/package.json`). Run `fnm install $(cat .node-version)` once. Without fnm, launchers warn and use Node on PATH. To update Node, run `fnm install <v>`, change `.node-version`, and run `make personas` while nothing else is running. Every value in `fixtures/ledgers/asha/` is invented. From the application root:

```sh
export PYTHONDONTWRITEBYTECODE=1
export LEDGER_DIR="$PWD/fixtures/ledgers/asha"
export DASHBOARD_DATA_DIR="$(mktemp -d /tmp/expense-example-data-XXXXXX)"
make verify
make dashboard
make test-server
npm --prefix review-dashboard test
npm --prefix review-dashboard run build
python3 -m unittest discover -s scripts/tests -v
```

With no ledger selected (no `ledger/` and no `LEDGER_DIR`), the test targets and `npm --prefix review-dashboard test` use `fixtures/ledgers/asha/` and say so in one line, so a fresh clone passes its own tests.

`fixtures/ledgers/jordan/` demonstrates USD spending, an opening-only EUR travel account and an EUR dinner charged in USD. `fixtures/ledgers/sam/` demonstrates a move from GBP to EUR in July; `fixtures/ledgers/empty/` is a fresh ledger. Run `make personas` to verify, build and test every fixture. See [Account currencies](docs/CURRENCIES.md) for formatting, separate totals and currency-change commands.

`make commit m="…"` holds the ledger lock while running `make verify-data`: journal generation, schema/store/audit/evidence/statement checks and strict hledger validation. `make verify` also runs ledger code tests; `make dashboard` retains them.

`DASHBOARD_DATA_DIR` selects a generated-output root containing `app/data/` and `public/data/` (default `review-dashboard/`). Relative paths resolve from the invoking directory. A private source stamp follows the ledger’s real path; builds refuse another ledger before changing any output. Choose another root or set `DASHBOARD_DATA_REPLACE=1` to replace it. The stamp is neither published nor served. Tests and persona runs always use temporary output roots; npm update notifications are disabled.

`make dashboard` installs the pinned npm dependencies when the installed versions differ from the lockfile, generates dashboard data and builds static views. Server tests use disposable repositories and a fake agent; they run no model. Browser suites are separate and are not part of the commands above. See [dashboard documentation](review-dashboard/README.md) and [server documentation](review-dashboard/server/README.md).

Before committing, run `make scan LIST=/path/to/private-identifiers.tsv`. Keep that list outside the repository. Each line is `label<TAB>value`; output contains only labels and locations. Binary files are reported for manual inspection. Review the scanner's limits in `scripts/check_private.py`.

## Versions

The application version is in `VERSION`; `make check-version` confirms the package files follow it. [CHANGELOG.md](CHANGELOG.md) describes each release with upgrade steps, and [docs/VERSIONS.md](docs/VERSIONS.md) explains the ledger format, interface and audit versions.

`make update` updates an installation to the newest release tag, upgrades the ledger and runs the offline checks, and rolls everything back if a step fails; `make update ARGS="--yes"` skips the question. [docs/RELEASING.md](docs/RELEASING.md) covers updating, the optional update notice, how releases are cut and the CI workflows.

## Licence

MIT; see [LICENSE](LICENSE).

Optional fuel, investments, history lookup and weekly review are configured in each ledger’s `ledger.json`; see [module settings](docs/MODULES.md).
