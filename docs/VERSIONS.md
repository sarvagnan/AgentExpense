# Versions and compatibility

AgentExpense versions several things separately. This page says what each version
means and what a change to it asks of you.

| What | Where it is declared | Current |
| --- | --- | --- |
| Application | `VERSION` | see the file |
| Ledger format | `ledger.json` `format` in each ledger | 1 |
| Statement reader interface | `INTERFACE` in each reader module | 1 |
| Agent adapter interface | `Description.interface_version` | 1 |
| Audit lines | `version` on each line of `data/additions/<year>.jsonl` | 1 |
| Review export files | `reviewVersion` in an exported file | 2 |
| Node | `.node-version` (selected with fnm) | see the file |

## Application version

`VERSION` holds one [semantic version](https://semver.org/). The dashboard's
`review-dashboard/package.json` (and its lock file) and the server's
`review-dashboard/server/pyproject.toml` (and `uv.lock`) carry the same number.
`make check-version` fails when any of them differs, or when `.node-version` lies
outside `package.json` `engines.node`; `make test` and `make doctor` include it.
Releases are tagged `vX.Y.Z` and described in [CHANGELOG.md](../CHANGELOG.md),
whose **Upgrade notes** list the steps for each release.

What a release number promises:

- **Patch** (`0.1.0` → `0.1.1`): fixes only. Same ledger format, interfaces and
  settings. Updating needs no ledger step.
- **Minor** (`0.1.x` → `0.2.0`): new features. It may raise the ledger format (the
  update runs the migration for you) or add optional settings, audit fields and
  interface features. Anything that worked keeps working, except where the Upgrade
  notes say otherwise while the version is below 1.0.
- **Major** (`1.x` → `2.0`): may remove things: an old reader or adapter interface,
  a setting, or support for upgrading from a very old ledger format. The Upgrade
  notes say what to change first.

Before 1.0 a minor release may also contain a breaking change; its Upgrade notes
then say so and give the steps.

## Ledger format

Each application version reads exactly one ledger format and carries migrations
from every older one. A ledger without `ledger.json` is format 0. Every entry point
reads `ledger.json` first:

- an **older** format stops with "run `make upgrade-ledger`";
- a **newer** format stops and asks for a newer AgentExpense.

`make upgrade-ledger` takes the ledger lock, requires a clean working copy,
records a checkpoint commit, runs each migration in turn, verifies the result and
compares the journal, hledger reports and dashboard data with the checkpoint. It
commits "Upgrade ledger format k → k+1 (app vX.Y.Z)". Going back means the previous
application version together with the checkpoint commit. See
[migrations/README.md](../migrations/README.md).

A format can gain optional fields without a new number when older data stays
valid and unchanged; `migrations/README.md` lists those additions. A change that
existing data would fail, or that rewrites stored data, gets a new format number
and a migration.

## Statement readers

Reader modules declare `INTERFACE = 1` ([docs/READERS.md](READERS.md)). The server
discovers built-in readers and personal readers in `<ledger>/readers/`, and stops
discovery for an interface it does not support rather than guessing. New optional
reader features keep interface 1. A change existing readers cannot satisfy gets
interface 2, and a release that drops interface 1 is a major release that says how
to update personal readers.

## Agent adapters

Adapters declare `interface_version=1` ([docs/ADAPTERS.md](ADAPTERS.md)). The same
rules apply as for readers. Each shipped CLI adapter also pins its CLI version in
`review-dashboard/package.json`; changing a pin goes through `make agent-upgrade`
and the offline allow-list checks, and is a patch or minor change of its own.

## Audit lines

Each audit line carries its own `version` (currently 1, defined by
`schema/additions.schema.json`). Lines are append-only and never rewritten. New
kinds and optional fields are added within version 1. A new line version is only
added alongside the old one: the application keeps reading every older line
version, so history stays valid without a migration.

## Review export files

Exported Review files carry `reviewVersion` 2. Import accepts that version.

## Tools

`make doctor` checks these offline and prints a fix for each problem:

| Tool | Requirement | Why |
| --- | --- | --- |
| Node | the version in `.node-version`, through fnm | One tested runtime for builds, tests and the agent CLIs; without fnm, Node on `PATH` is used with a warning. `engines.node` in `package.json` must include it. |
| npm | ships with that Node | Installs the locked dashboard and agent CLI packages. |
| Python | `requires-python` in `review-dashboard/server/pyproject.toml` (3.11+) | Server, ledger tools and tests. |
| uv | any recent release | Runs the server environment from `uv.lock`. |
| hledger | 1.25 or newer | See below. |
| jj or git | either | Ledgers are version-controlled; dashboard saves commit with Jujutsu colocated with git. |

**hledger 1.25.** The app writes a journal with `account` and `commodity`
declarations, quoted commodity names, total costs (`@@`), balance assertions and
periodic `~` rules, then runs `hledger check -s ordereddates` (strict checks of
declared accounts and commodities, plus date order) and `--forecast=START..END`.
The migration comparison reads `print`, `register`, `balance --flat` and
`balance --flat -B`. hledger's manual gives 1.25 as the release from which signed
costs, which `-B` totals depend on, behave as they do now; every other feature above
is older. So 1.25 is the minimum. The test suites run only on the hledger installed
where they run, so versions between 1.25 and that one are expected to work but are
not exercised. Raise this minimum when the app starts to use a newer feature.
