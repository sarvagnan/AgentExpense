# Roadmap

AgentExpense is being generalised from a single-person expense dashboard into an app
anyone can set up with their own coding agent. This is the public plan; each phase ends
with its checks passing for every fixture ledger.

| # | Phase | Main content | Done when | Status |
|---|---|---|---|---|
| 0 | Mockups | New screens on fictional data: first run, navigation, Add with statements, Drafts with conversions, Review with its badge, empty states, agent notices | Approved | Done |
| 1 | New repository | A scrubbed copy of the app, MIT licence, README, personal-data scanner | Scan clean; builds and fixture tests pass | Done |
| 2 | Foundations | `ledger.json`, ledger format 1 and migrations, account metadata, account currencies with totals per currency, drafts dated by the browser's local time, fixture personas | All suites pass for every persona | Done |
| 3 | Optional modules | Fuel log, investments and price sources, old-ledger lookup, weekly review behind settings | Personas without modules run with every module off | Planned |
| 4 | Screens | The approved mockups: navigation, Add with statements, Drafts, conversions, empty states, agent notice, Settings | Graded in Light and Dark, three browser engines | Planned |
| 5 | Review on the server | Decisions applied by the server with audit lines, badge rules, migration from browser storage | Same Review count on every device; Export/Import still work | Planned |
| 6 | Agent adapters | Adapter contract; Codex, Claude Code, Pi, OpenCode, direct API; each pinned and self-checked | Each shipped adapter passes its live probe | Planned |
| 7 | Statements | Reader interface, generic CSV and spreadsheet readers, bank statement summaries, unknown formats read by the agent and checked by code, `startDate` | Fictional statements pass in every persona | Planned |
| 8 | Releases | `VERSION`, `CHANGELOG.md`, `make doctor`, `make update`, CI | An update between tagged releases, with a migration, works and rolls back | Planned |
| 9 | Setup | `SETUP.md`, ledger template, demo, first-run screens and both paths, deployment examples | A fresh agent session sets up a ledger from nothing using only the docs | Planned |

## Principles

- **Generic products only.** The app never names a real bank, card, card network,
  wallet, payment app or store. A person's own statement readers live in their ledger.
- **Currencies belong to accounts.** Totals are added up per currency and never
  converted. A conversion is asked only when an expense's currency differs from the
  paying account's.
- **No time zone setting.** Each draft carries the browser's local time.
- **The agent proposes; the server writes.** Agents run without shell or file tools, in
  their own home, checked by allow-lists before use.
- **Updates never break data.** Each ledger records its format; migrations are checked
  against the previous outputs and roll back on failure.
