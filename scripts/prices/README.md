# Price sources, interface version 1

`sources/<id>.py` is an application-owned plug-in. IDs contain lowercase letters,
digits and hyphens and cannot select a path outside this directory. Each module
exports `INTERFACE_VERSION = 1` and `Source(ledger_root)`. Each source instance
exposes `commodities`, the unit commodities it can price, and
`fetch(start, end)`, accepting inclusive Python dates and returning hledger `P`
directives as text. Transport and parsing belong to the source; persistence belongs
to the dispatcher. A source never updates the ledger directly.

The dispatcher validates directives, dates, positive finite values and declared
commodities, merges on `(date, commodity, quote currency)`, retains existing
comments and writes atomically. Without `--from`, it starts seven days before the
oldest latest stored price across the selected commodities. A source with no
stored history needs `--from YYYY-MM-DD` for its first fetch.

Configure `modules.investments.prices` with the automatic fetch source ID. Null or
investments off means the dispatcher does not fetch or read prices. With investments
enabled, journal generation, verification and the dashboard still use any existing
price file, including manually maintained `P` directives, regardless of the fetch
source setting. Without recorded prices, holdings show recorded cost. The shared
output path remains
`data/prices/mutual-funds.prices` for compatibility. `mutual-fund-nav` reads
`data/prices/fund-sources.json` from the ledger, mapping a commodity to the public
AMFI source's house, scheme code and ISIN strings. No fund list lives in source
code. Example shape, using fictional identifiers:

```json
{"DEMOFUND": ["999", "999001", "FICTIONAL0001"]}
```

Use `scripts/prices/update_fund_prices.sh` for scheduled updates. It holds the
shared ledger lock through fetching, dashboard verification/build and the price
commit, using the existing partial-commit behavior. For an initial dated fetch:

```sh
LEDGER_DIR=/absolute/path/to/ledger uv run python scripts/prices/fetch_fund_navs.py --from YYYY-MM-DD --to YYYY-MM-DD
```

The direct dispatcher also takes the shared lock. `--ledger-locked` is internal
and reserved for the lock helper. Tests inject a fake source and feed the NAV
parser [fictional response text](fixtures/nav-response.txt);
no test fetches prices from a network.
