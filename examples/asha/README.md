# Asha Rao — fictional ledger

All values are invented; none come from a personal store. Four calendar months demonstrate bank, Rewards card, cash and Shopping wallet spending, salary, transfers, fuel, one advance and reimbursement from Dev Sen, one pending classification, monthly commitments and an invented investment commodity. Markdown confirmations explicitly identify themselves as fictional. The required price and fuel files are present. The example has no statement PDFs or repository history.

Run commands from the application root with `LEDGER_DIR="$PWD/examples/asha"`. Generated journal, lock and drafts are ignored. Fund prices are illustrative; no AMFI product is selected. To configure real AMFI updates, supply your private ledger's `data/prices/fund-sources.json` mapping commodity names to `[house number, "scheme code", "ISIN"]` and use `--from` on its first fetch.

The generic products are Savings Account, Salary Account, Rewards Card, Shopping Wallet and App Store Balance, with card rewards recorded under Income:Card Rewards. The salary account and app store balance are declared for the payment shortcuts and have no invented activity yet. See [the example statement reader](../../scripts/statements/README.md) for a fictional card statement layout.
