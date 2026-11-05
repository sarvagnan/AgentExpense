# Roadmap

AgentExpense is being generalised from a single-person expense dashboard into an app
anyone can set up with their own coding agent. This is the public plan; each phase ends
with its checks passing for every fixture ledger.

| # | Phase | Main content | Done when | Status |
|---|---|---|---|---|
| 0 | Mockups | New screens on fictional data: first run, navigation, Add with statements, Drafts with conversions, Review with its badge, empty states, agent notices | Approved | Done |
| 1 | New repository | A scrubbed copy of the app, MIT licence, README, personal-data scanner | Scan clean; builds and fixture tests pass | Done |
| 2 | Foundations | `ledger.json`, ledger format 1 and migrations, account metadata, account currencies with totals per currency, drafts dated by the browser's local time, fixture personas | All suites pass for every persona | Done |
| 3 | Optional modules | Fuel log, investments and price sources, old-ledger lookup, weekly review behind settings | Personas without modules run with every module off | Done |
| 4 | Screens | The approved mockups: navigation, Add with statements, Drafts, conversions, empty states, agent notice, Settings | Graded in Light and Dark, three browser engines | Built; graded with all screens at the end |
| 5 | Review on the server | Decisions applied by the server with audit lines, badge rules, migration from browser storage | Same Review count on every device; Export/Import still work | Done |
| 6 | Agent adapters | Adapter contract; Codex, Claude Code, Pi, OpenCode, direct API; each pinned and self-checked | Each shipped adapter passes its live probe | Built; live probes pending |
| 6a | Agent outages | Failure kinds from every adapter, one shared pause with backoff, finishing a draft by hand, asking before new accounts, Add defaults from history | Drafts survive a usage limit and can be finished without the agent | Done |
| 6b | Today's month | The current month always exists, a daily rebuild after midnight, one variable-spending row per currency in Forecast; recording runs through today, with due payments kept expected and same-day comparisons with the previous month | The page opens on today's month on the 1st, and Forecast and the month views agree | Done |
| 6c | Recurring rules | Rules guessed as you type and added as usual, rules made automatically from whole payments, a Rules screen, weekly-review proposals, a CLI | Usual payments and rules work in every persona and currency | Done; final browser pass runs with 8a |
| 6d | Corrections and receipts | Typed dates for usual payments, "Not this", correcting saved entries through one replacement transaction shared with Review's merge, reading receipts afterwards | A saved entry can be corrected safely from Drafts, Explorer and Journal | Done |
| 6e | Add stays put | One Add panel for expenses, receipts and statements; the save bar confirms the result, a short "Just added" list holds follow-ups, examples sit behind a link, and the input never moves while saving | The input keeps its position and focus from save to the next expense | Done |
| 6f | Confirmed balances, varying bills, Queue and Trash | Readable refusals when a save breaks a statement-confirmed balance, with locked amounts and a date window when correcting; recurring rules whose amount varies, with an estimate and saved bills; Drafts becomes the Queue, Undo gives way to Delete and a Trash with time limits in Settings; receipt dates that never fail a reading; the Accounts list in the Journal's style | A refused save says why, a varying bill is saved without paying it, and a deleted entry comes back exactly from Trash | Done |
| 7 | Statements | Reader interface, generic CSV and spreadsheet readers, bank statement summaries, unknown formats read by the agent and checked by code, `startDate` | Fictional statements pass in every persona | Done |
| 8 | Releases | `VERSION`, `CHANGELOG.md`, `make doctor`, `make update`, CI | An update between tagged releases, with a migration, works and rolls back | Done (publishing waits for the owner) |
| 8a | Checks and generated data | Saves run only the data checks (`make verify-data`); code tests stay in `make verify` and `make dashboard`; generated dashboard data records the ledger it came from, `DASHBOARD_DATA_DIR` chooses where it goes, and a build refuses to replace another ledger's data without `DASHBOARD_DATA_REPLACE=1` | Saves skip the code tests, and switching ledgers never overwrites another ledger's data | Done |
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
