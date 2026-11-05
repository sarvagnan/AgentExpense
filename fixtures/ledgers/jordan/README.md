# Jordan Lee — fictional fixture

All people, products, dates and values are invented. This USD fixture has opening balances and fifteen ordinary expenses. Fuel, investments, history and weekly review are disabled; no fuel or prices file is needed.

## Accounts and opening balances

Jordan's settings are name "Jordan Lee", main currency USD and locale `en-US`, with
every optional module off. The opening balances are dated September 1, 2026, the
ledger's `startDate`:

| Kind | Name | Currency | Opening balance | Aliases | Statements |
| --- | --- | --- | ---: | --- | --- |
| bank | Alder Checking | USD | 2,400.00 | alder, checking | |
| bank | Meadow Savings | USD | 3,500.00 | meadow, savings | |
| card | Lumen Card | USD | 125.00 owed | lumen, card | `example-card-pdf`, identifier 4321 |
| cash | Cash | USD | 80.00 | cash | |
| bank | Willow Travel | EUR | 650.00 | willow, travel account | |

Personal rules: opening balances are confirmed on the start date, and Willow Travel
holds euros for trips and stays untouched between trips.
`scripts/tests/setup_walkthrough.md` sets this ledger up from nothing.

From the application root run `LEDGER_DIR="$PWD/fixtures/ledgers/jordan" make dashboard`.

Willow Travel has an EUR 650 opening balance and no later activity. The fictional Juniper Table dinner keeps EUR 46.80 and USD 51.48 charged to Lumen Card. Spending remains in USD; account totals also retain EUR separately.

Recurring examples include Cedar Studio's September/October whole payments,
Maple Workshop's weaker irregular pattern, a paused Cedar Archive rule, an
already-paid rent rule and Lantern Notes' EUR expense with a planned USD charge.
The fixture's `never-automatic` preferences are ledger data. The four added
fictional history payments total USD 64; backfill makes one Cedar Studio rule.
The recurring-history example extends the opening date to September 1. All five
opening amounts, accounts, assertions and IDs are retained; only their date and
the manifest startDate move together so September payments follow the openings.
