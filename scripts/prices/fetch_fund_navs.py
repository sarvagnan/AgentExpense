"""Fetch daily mutual fund NAVs from AMFI into data/prices/mutual-funds.prices.

The file holds one P directive per fund per NAV date; the ledger builder copies it
into the generated ledger so hledger -V and ledger -V value fund units at market.
Without --from, fetching starts a week before the latest stored NAV, so a missed
day fills in on the next run. AMFI serves at most 90 days per request.

    uv run python scripts/prices/fetch_fund_navs.py
    uv run python scripts/prices/fetch_fund_navs.py --from YYYY-MM-DD
"""
from __future__ import annotations

import argparse
import json
import re
import time
import urllib.request
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path

# Direct script execution puts this directory on sys.path; share the application helper.
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from project_paths import ledger_root

ROOT = ledger_root()
PRICES = ROOT / "data" / "prices" / "mutual-funds.prices"
HISTORY_URL = "https://portal.amfiindia.com/DownloadNAVHistoryReport_Po.aspx?mf={house}&frmdt={start}&todt={end}"
# Optional ledger-local mapping: commodity -> [AMFI house, scheme code, ISIN].
SOURCES = ROOT / "data/prices/fund-sources.json"
FUNDS = json.loads(SOURCES.read_text()) if SOURCES.exists() else {}
WINDOW = timedelta(days=80)
LOOKBACK = timedelta(days=7)
PRICE_LINE = re.compile(r'^P (\d{4}-\d{2}-\d{2}) "([A-Z0-9]+)" (\d+\.\d+) INR$')
HEADER = [
    "; Mutual fund NAVs in INR, one per fund per NAV date.",
    "; Source: AMFI NAV history, https://portal.amfiindia.com/DownloadNAVHistoryReport_Po.aspx",
    "; Written by scripts/prices/fetch_fund_navs.py and included by the generated expense_ledger.ledger.",
    "; Funds and scheme codes: " + ", ".join(f"{commodity} = AMFI {code} ({isin})" for commodity, (_, code, isin) in FUNDS.items()),
]


def read_prices() -> dict[tuple[str, str], str]:
    prices: dict[tuple[str, str], str] = {}
    if not PRICES.exists():
        return prices
    for number, line in enumerate(PRICES.read_text().splitlines(), 1):
        if not line or line.startswith(";"):
            continue
        match = PRICE_LINE.match(line)
        if not match or match[2] not in FUNDS:
            raise ValueError(f"{PRICES.name}:{number}: unexpected line {line!r}")
        prices[(match[1], match[2])] = match[3]
    return prices


def download(url: str) -> str:
    for attempt in range(4):
        try:
            request = urllib.request.Request(url, headers={"User-Agent": "expense-ledger-nav-fetch/1"})
            with urllib.request.urlopen(request, timeout=120) as response:
                text = response.read().decode("utf-8", errors="replace")
            # Errors come back as an HTML page with status 200.
            if not text.startswith("Scheme Code;"):
                raise ValueError("AMFI returned something other than a NAV report")
            return text
        except Exception as error:
            if attempt == 3:
                raise RuntimeError(f"AMFI request failed: {url}: {error}") from error
            time.sleep(10 * (attempt + 1))
    raise AssertionError("unreachable")


def fetch(start: date, end: date) -> dict[tuple[str, str], str]:
    fetched: dict[tuple[str, str], str] = {}
    for house in sorted({house for house, _, _ in FUNDS.values()}):
        wanted = {code: (commodity, isin) for commodity, (fund_house, code, isin) in FUNDS.items() if fund_house == house}
        window_start = start
        while window_start <= end:
            window_end = min(window_start + WINDOW, end)
            url = HISTORY_URL.format(house=house, start=f"{window_start:%d-%b-%Y}", end=f"{window_end:%d-%b-%Y}")
            for line in download(url).splitlines():
                fields = [value.strip() for value in line.split(";")]
                if len(fields) < 8 or fields[0] not in wanted:
                    continue
                commodity, isin = wanted[fields[0]]
                if isin not in fields[4:6]:
                    raise ValueError(f"Scheme {fields[0]} no longer carries ISIN {isin}: {line!r}")
                try:
                    nav = Decimal(fields[6])
                except InvalidOperation:
                    continue  # AMFI prints N.A. for days without a NAV
                if nav <= 0:
                    raise ValueError(f"Non-positive NAV: {line!r}")
                day = datetime.strptime(fields[7], "%d-%b-%Y").date()
                if window_start <= day <= window_end:
                    fetched[(day.isoformat(), commodity)] = fields[6]
            window_start = window_end + timedelta(days=1)
    return fetched


def write_prices(prices: dict[tuple[str, str], str]) -> None:
    lines = HEADER + [""] + [f'P {day} "{commodity}" {nav} INR' for (day, commodity), nav in sorted(prices.items())]
    PRICES.parent.mkdir(parents=True, exist_ok=True)
    temporary = PRICES.with_suffix(".tmp")
    temporary.write_text("\n".join(lines) + "\n")
    temporary.replace(PRICES)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--from", dest="start", type=date.fromisoformat, help="first NAV date to fetch (default: a week before the latest stored NAV)")
    parser.add_argument("--to", dest="end", type=date.fromisoformat, default=date.today(), help="last NAV date to fetch (default: today)")
    args = parser.parse_args()

    if not FUNDS:
        print('No fund sources configured in data/prices/fund-sources.json')
        return
    prices = read_prices()
    latest = {commodity: max((day for day, item in prices if item == commodity), default=None) for commodity in FUNDS}
    if args.start:
        start = args.start
    elif all(latest.values()):
        start = date.fromisoformat(min(latest.values())) - LOOKBACK
    else:
        parser.error('--from is required when configured funds have no stored NAVs')
    fetched = fetch(start, args.end)

    added = sorted(key for key in fetched if key not in prices)
    revised = sorted(key for key in fetched if key in prices and Decimal(prices[key]) != Decimal(fetched[key]))
    for day, commodity in revised:
        print(f"Revised {commodity} {day}: {prices[(day, commodity)]} -> {fetched[(day, commodity)]}")
    if added or revised or not PRICES.exists():
        prices.update(fetched)
        write_prices(prices)
    newest = {commodity: max((day for day, item in prices if item == commodity), default="none") for commodity in FUNDS}
    print(f"Fetched {start:%Y-%m-%d}..{args.end:%Y-%m-%d}: {len(added)} new and {len(revised)} revised NAVs. Latest: "
          + ", ".join(f"{commodity} {day}" for commodity, day in newest.items()))


if __name__ == "__main__":
    main()
