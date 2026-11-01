# AgentExpense dashboard

A React/Vite dashboard for the JSON ledger, with Overview, Explorer, Journal, Accounts, Forecast, Review, Add, Drafts and Statements. Each account keeps its own currency; totals and charts keep currencies separate. Fuel links, fund valuations and the example card statement reader are built in.

From the application root set `LEDGER_DIR` to an absolute ledger directory, then run `make dashboard`. The fictional `fixtures/ledgers/asha/` is the public example. JSON under `app/data/` and compressed payloads under `public/data/` are generated locally; keep them out of version control. `npm --prefix review-dashboard test` runs Node unit and server-rendering checks with disposable application copies and no browser. See [tests/BROWSERS.md](tests/BROWSERS.md) for future browser coverage.

## Data and reporting

Builders read the JSON store directly through `scripts/expense-store.mjs`. Account declarations and schema validation belong to the ledger Python tools. The data checker verifies stable unique row IDs derived from store IDs and separately funded currencies, review membership and generated Add patterns for any selected ledger. Forecast rules are commitments kept separate from actuals. Funds hold units at recorded costs with their own currencies, valued in the selected price's quote currency. Fuel and price files are optional. See [Account currencies](../docs/CURRENCIES.md) for the generated data contract and currency-change commands.

Sources retain their original order; explicit statement membership takes priority in evidence labels. Receipt/image matching and fuel links need explicit confirmation. An amount/date coincidence is not proof. Reimbursements retain their group and party and do not become personal expenses. Typed variable-estimate exclusions and policy terms stay in the store.

## Routing and static builds

Vite development uses `/`; production builds use `/expenses/`. `scripts/build-static-views.mjs` writes a directory index for every route because plain file servers have no SPA fallback. The browser loads the gzip JSON payloads itself; serve them as bytes without an extra Content-Encoding header. Build outputs are local artifacts.

The optional Python server serves the built dashboard, evidence and API; see [server/README.md](server/README.md). For local development, `EXPENSES_DEV=1 EXPENSES_PORT=8701` enables Vite's `/api/` proxy to a local server. Evidence may contain private data: use trusted access controls on any real deployment.

## Review storage

Review decisions and counts come from the server and are shared across devices. Browser storage, scoped by ledger identity or its configured legacy key, keeps unsent edits and failed requests for explicit retry/discard. Export retains version 2; Import previews valid, unknown and changed entries, then applies valid decisions in one audited commit. Review offers to move pre-existing browser decisions into the ledger. See [server Review](server/README.md#server-review) for the write and recovery contract.

For card statement parsing or adaptation, read [the example layout and reader](../scripts/statements/README.md). Payment shortcuts are `card` (Rewards card), `savings` (Savings account), `salary` (Salary account), `wallet` (Shopping wallet), `appstore` (App store balance) and `cash`. Both wallet accounts appear as prepaid balances, and bank-funded top-ups appear as Balance top-up.

All data builders require a format-1 `ledger.json`. See [migrations](../migrations/README.md) for older ledgers. `make personas` builds and tests Asha, Jordan and the empty fixture. Missing optional fuel and price files produce empty data.
