"""Mutual-fund NAV history from the public AMFI data source.

Only the ledger's fund-sources.json chooses funds: commodity -> [house, code, ISIN].
"""
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation
import json
import time
import urllib.request

INTERFACE_VERSION = 1
HISTORY_URL = 'https://portal.amfiindia.com/DownloadNAVHistoryReport_Po.aspx?mf={house}&frmdt={start}&todt={end}'


def parse_response(text, wanted, start, end):
    """Parse a response independently of transport; tests use fictional responses."""
    if not text.startswith('Scheme Code;'):
        raise ValueError('AMFI returned something other than a NAV report')
    prices = {}
    for line in text.splitlines():
        fields = [value.strip() for value in line.split(';')]
        if len(fields) < 8 or fields[0] not in wanted:
            continue
        commodity, isin = wanted[fields[0]]
        if isin not in fields[4:6]:
            raise ValueError('Configured scheme no longer carries its configured ISIN')
        try:
            nav = Decimal(fields[6])
        except InvalidOperation:
            continue  # The source prints N.A. for days without a NAV.
        if not nav.is_finite() or nav <= 0:
            raise ValueError('Non-positive or non-finite NAV')
        day = datetime.strptime(fields[7], '%d-%b-%Y').date()
        if start <= day <= end:
            prices[(day.isoformat(), commodity)] = fields[6]
    return prices


class Source:
    def __init__(self, root):
        self.funds = json.loads((root / 'data/prices/fund-sources.json').read_text())
        if not isinstance(self.funds, dict) or any(not isinstance(v, list) or len(v) != 3 or
                not all(isinstance(x, str) and x for x in v) for v in self.funds.values()):
            raise ValueError('fund-sources.json must map commodities to [house, code, ISIN]')
        self.commodities = tuple(self.funds)

    def download(self, url):
        for attempt in range(4):
            try:
                request = urllib.request.Request(url, headers={'User-Agent': 'expense-ledger-nav-fetch/1'})
                with urllib.request.urlopen(request, timeout=120) as response:
                    text = response.read().decode('utf-8', errors='replace')
                if not text.startswith('Scheme Code;'):
                    raise ValueError('AMFI returned something other than a NAV report')
                return text
            except Exception:
                if attempt == 3:
                    raise
                time.sleep(10 * (attempt + 1))
        raise AssertionError('unreachable')

    def fetch(self, start, end):
        prices = {}
        for house in sorted({house for house, _, _ in self.funds.values()}):
            wanted = {code: (commodity, isin) for commodity, (fund_house, code, isin) in self.funds.items() if fund_house == house}
            first = start
            while first <= end:
                last = min(first + timedelta(days=80), end)
                url = HISTORY_URL.format(house=house, start=f'{first:%d-%b-%Y}', end=f'{last:%d-%b-%Y}')
                prices.update(parse_response(self.download(url), wanted, first, last))
                first = last + timedelta(days=1)
        return '\n'.join(f'P {day} "{commodity}" {nav} INR' for (day, commodity), nav in sorted(prices.items()))
