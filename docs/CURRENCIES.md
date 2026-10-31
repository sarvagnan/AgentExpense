# Account currencies

The manifest's `defaultCurrency` is a default for new accounts. Each balance-sheet
account declares its own `currency` in `data/accounts.json`. Account metadata, rather
than a ledger-wide conversion, determines the unit of every payment and balance.
`schema/currencies.json` lists the ISO currency codes recognized by the application;
other commodities are units. Non-zero investment unit postings carry their cost's
quote currency. Zero-unit postings, including periodic holdings assertions, need no
cost and leave the cost basis unchanged.

An expense in another currency keeps the original expense amount and a cost in the
paying account's currency. For example, EUR 46.80 on a USD card with USD 51.48 charged
uses an expense posting of `46.80 EUR @@ 51.48 USD` and a card posting of `-51.48 USD`.
The Add agent asks for the charged amount or a rate if neither is supplied. Transfers
keep both sent and received amounts, with a cost to balance different currencies.
Statements replace estimated costs with the actual amount charged in the card's unit.

Amounts use `Intl.NumberFormat` with the optional manifest `locale`, falling back to
the viewer's locale. Totals keep currencies separate. The default currency comes first,
then other codes alphabetically. Each chart contains one currency. No conversion is
implied by a total such as `€6,558.80 + £3,284.65`.

## Change a currency

Select the ledger using an absolute `LEDGER_DIR`, then run from the application:

```sh
make set-default-currency ARGS="EUR"
make set-account-currency ARGS="'Assets:Bank:Willow Travel' EUR"
make verify
```

The first command changes only `ledger.json`. The second changes account metadata and
relabels its opening amounts and assertions, preserving the numbers. Shared opening
entries split only the corresponding equity amount. Ordinary transactions, forecast
rules and filed statements fix an account's currency; refusal identifies the cause.
Opening equity entries and zero-amount balance assertions on `startDate` do not fix it.
CLI changes hold the shared ledger lock and remain in the working copy for review.

The server provides `POST /api/settings/default-currency` with `{"currency":"EUR"}`
and `POST /api/accounts/currency` with `{"account":"Assets:Bank:Willow Travel","currency":"EUR"}`.
Both use the existing login, host and Origin rules, require a clean ledger, take its
lock, and commit through `make commit` with verification. Durable rollback journals
live under `drafts/settings/`. A failed verification restores the previous state.
A successful response includes the commit id and whether dashboard data refreshed.
The Settings screen itself belongs to a later phase.

## Generated data

- `transactions[].currency` is the funding account's currency; `amount` and
  `signedExpense` are in that unit. Postings retain their original `currency`, with
  signed `cost` and `costCurrency` when priced. Foreign expense totals use the net
  amount moved by the funding postings, after other allocations, rather than the sum
  of rounded posting costs. Foreign lines share a rate derived from that charge;
  recurring shares and Review use the same allocation.
- Independently funded currencies in one expense or income entry produce one row
  per currency. Those rows have unique `<store-id>-<currency>` IDs and retain the
  original ID in `storeId`. Their posting subsets partition the original entry,
  so account balances count every posting exactly once. Single-currency rows keep
  their existing IDs and data shape.
- `months[]` has one row per `(month, currency)`. Its totals, categories and days
  belong to that row's currency. Counts count entries in that row only.
- `forecasts[]` contains one explicitly labelled forecast per currency, including
  commitments and variable profiles. `forecast` is the same single-currency projection
  for the manifest default, retained for existing consumers; it is never a mixed total.
- Reimbursement groups are keyed by `(id, currency)`. Price series carry their quote
  `currency`. Fuel rows and Add history examples carry their own currency too.
- Review's `pendingNetAmount` and `pendingAbsoluteAmount` are currency-to-number maps.

The shared formatting and arithmetic modules are
`review-dashboard/scripts/formatting.mjs` and `review-dashboard/scripts/currency.mjs`.
The Indian compact-label fallback preserves the existing whole-thousand and tenth-lakh
rounding and uses one unit across every axis. Other locales use Intl compact notation.
