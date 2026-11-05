# Documentation

Start with [SETUP.md](../SETUP.md) to set up a ledger with a coding agent, and the
[README](../README.md) for an overview.

## Using AgentExpense

| Document | Covers |
| --- | --- |
| [SETUP.md](../SETUP.md) | The setup conversation for a coding agent: prerequisites, the ledger, accounts, starting balances or history, modules, the in-app agent, starting the dashboard |
| [policy/BOOKKEEPING.md](../policy/BOOKKEEPING.md) | The general bookkeeping policy; a ledger's `AGENTS.md` adds personal rules |
| [Ledger template](../templates/ledger/README.md) | What a new ledger holds; its [readers](../templates/ledger/readers/README.md) and [private checks](../templates/ledger/checks/README.md) |
| [CURRENCIES.md](CURRENCIES.md) | Account currencies, formatting, separate totals and currency-change commands |
| [MODULES.md](MODULES.md) | Optional fuel, investments, history lookup and weekly review |
| [RULES.md](RULES.md) | Recurring rules, varying amounts and saved bills |
| [READERS.md](READERS.md) | Statement readers (interface 1), generic CSV and spreadsheet options, unknown formats |
| [ADAPTERS.md](ADAPTERS.md) | The in-app agent: choosing an adapter, isolation, sign-in and checks |
| [deploy/](deploy/README.md) | Running the dashboard: local, systemd, launchd, reverse proxy, tailnet; access settings and security notes |
| [scheduling/](scheduling/README.md) | Scheduling the optional weekly review |
| [RELEASING.md](RELEASING.md) | Updating with `make update`, the update notice, cutting releases, CI |
| [VERSIONS.md](VERSIONS.md) | What versions mean: application, ledger format, interfaces, audit lines |

## Working on the application

| Document | Covers |
| --- | --- |
| [AGENTS.md](../AGENTS.md) | Instructions for agents changing the application |
| [ROADMAP.md](ROADMAP.md) | Phases and their status |
| [Ledger migrations](../migrations/README.md) | Ledger format upgrades, rehearsal and rollback |
| [Dashboard](../review-dashboard/README.md) and [server](../review-dashboard/server/README.md) | Data builds, routes, the API, queue and adapters |
| [Weekly review procedure](../review-dashboard/server/WEEKLY-REVIEW.md) | What a periodic review agent does |
| [Card statement layout](../scripts/statements/README.md) | The fictional example card statement and its reader |
| [Price sources](../scripts/prices/README.md) | Investment price source interface |
| [Browser tests](../review-dashboard/tests/browser/README.md) | Headless browser suites and captures |
| [Setup walkthrough](../scripts/tests/setup_walkthrough.md) | The acceptance test: a fresh agent sets up a fictional person from the docs alone |
