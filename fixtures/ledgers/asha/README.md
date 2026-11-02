# Asha Rao — fictional ledger

All people, products, dates, amounts and evidence here are invented. Four months demonstrate Nila Current, Saffron Reserve, Mosaic Card, cash, wallets, salary, transfers, fuel, one reimbursement, a pending classification, recurring commitments and an invented investment commodity. Confirmation notes are fictional evidence; prices are illustrative.

From the application root run `LEDGER_DIR="$PWD/fixtures/ledgers/asha" make dashboard`. Generated journal, lock and drafts are ignored. This fixture has no repository history.

See [the example statement reader](../../../scripts/statements/README.md) for the fictional statement layout. The ledger-local optional `data/prices/fund-sources.json` configures the price updater; this fixture has no live source mapping.

Recurring examples include Cedar Studio's September/October whole payments,
Maple Workshop's weaker irregular pattern, a paused Cedar Archive rule and an
already-paid rent rule. `data/recurring-settings.json` excludes this fixture's
variable food/fuel categories from automatic rules. These four added fictional
history payments total INR 64; backfill proposes one automatic Cedar Studio rule.
