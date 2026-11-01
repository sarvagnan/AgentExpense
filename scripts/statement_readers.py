"""Version-1 trusted statement readers and the mandatory shared validation gate."""
from copy import deepcopy
from datetime import date
from decimal import Decimal
import importlib.util
from pathlib import Path
import re
import sys

APPLICATION = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APPLICATION / 'scripts/statements'))
CHECKS = {'printed_totals', 'running_balance'}


def decimal(value):
    if not isinstance(value, str) or not re.fullmatch(r'-?(0|[1-9]\d*)(\.\d+)?', value):
        raise ValueError('Amounts must be finite decimal strings')
    return Decimal(value)


def money_text(value):
    text = format(value, 'f')
    return text if '.' in text else text + '.00'


def signed(row):
    return decimal(row['amount']) * (-1 if row['direction'] == 'Debit' else 1)


class Reader:
    def __init__(self, module, *, personal=False):
        if getattr(module, 'INTERFACE', None) != 1:
            raise ValueError('Unsupported statement reader interface; expected INTERFACE = 1')
        for key in ('ID', 'NAME', 'KINDS', 'EXTENSIONS', 'CHECKS', 'recognise', 'read'):
            if not hasattr(module, key):
                raise ValueError('Statement reader is missing ' + key)
        if not re.fullmatch(r'[a-z0-9][a-z0-9-]*', module.ID):
            raise ValueError('Invalid statement reader ID')
        if not isinstance(module.NAME, str) or not module.NAME.strip() or not callable(module.recognise) or not callable(module.read):
            raise ValueError('Invalid statement reader name or callbacks')
        if not isinstance(module.EXTENSIONS, (tuple, list)) or not module.EXTENSIONS or any(not isinstance(x,str) or not re.fullmatch(r'\.[a-z0-9]+',x) for x in module.EXTENSIONS):
            raise ValueError('Reader extensions must be lowercase dotted suffixes')
        if not set(module.KINDS) <= {'bank', 'card'} or not module.KINDS:
            raise ValueError('Reader kinds must be bank and/or card')
        if not callable(module.CHECKS) and not set(module.CHECKS) <= CHECKS:
            raise ValueError('Unknown statement reader check')
        self.module, self.personal = module, personal
        self.id, self.name = module.ID, module.NAME
        self.kinds, self.extensions, self.checks = tuple(module.KINDS), tuple(module.EXTENSIONS), module.CHECKS if callable(module.CHECKS) else tuple(module.CHECKS)

    def recognise(self, file):
        """Contain buggy recognisers, including malformed confidence/reasons."""
        try:
            result = self.module.recognise(Path(file))
            confidence, reasons = result['confidence'], result['reasons']
            if type(confidence) not in (int, float) or not 0 <= confidence <= 1 or not isinstance(reasons, list) or not all(isinstance(x, str) for x in reasons):
                raise ValueError()
            return dict(confidence=confidence, reasons=reasons)
        except Exception:
            return dict(confidence=0, reasons=['Reader could not recognise this file'])

    def read(self, file, account, password=None):
        if account['kind'] not in self.kinds:
            raise ValueError('This reader does not handle the selected account kind')
        result = self.module.read(Path(file), deepcopy(account), password=password)
        for row in result.get('rows',[]):
            row.setdefault('page',row.get('citation',{}).get('page',1))
            row.setdefault('foreignAmount',None)
            row.setdefault('foreignCurrency',None)
        identifier = result.get('identifier', result.get('card_last4'))
        expected = account.get('statement',{}).get('identifier')
        if identifier and expected and identifier != expected:
            raise ValueError('The printed identifier does not match the selected account')
        result['reader'] = self.id
        result['readerName'] = self.name
        result['readerPersonal'] = self.personal
        result['declaredChecks'] = list(self.checks(account) if callable(self.checks) else self.checks)
        return result


class Registry:
    def __init__(self, ledger=None):
        self.readers = {}
        folders = [(APPLICATION / 'readers', False)]
        if ledger is not None:
            folders.append((Path(ledger) / 'readers', True))
        for folder, personal in folders:
            for path in sorted(folder.glob('*.py')):
                if path.name.startswith('_'):
                    continue
                name = '_expense_reader_' + str(path.resolve()).encode().hex()
                spec = importlib.util.spec_from_file_location(name, path)
                module = importlib.util.module_from_spec(spec)
                sys.modules[name] = module
                spec.loader.exec_module(module)
                reader = Reader(module, personal=personal)
                if reader.id in self.readers:
                    raise ValueError('Duplicate statement reader ID: ' + reader.id)
                self.readers[reader.id] = reader

    def get(self, identity):
        if identity not in self.readers:
            raise ValueError('Unknown statement reader: ' + str(identity))
        return self.readers[identity]

    def describe(self):
        return [dict(id=r.id, name=r.name, kinds=r.kinds, extensions=r.extensions, personal=r.personal) for r in self.readers.values()]


def check_statement(statement, *, account, previous=None, start_date=None, opening=None):
    """Validate structure/currency, then all declared checks before any matching.

    previous is {end, balance} in account units. opening is the startDate/opening
    anchor {date, balance}. Card summary arithmetic retains positive debt units.
    """
    s = statement
    currency = s.get('currency')
    if currency is not None and (not isinstance(currency, str) or not re.fullmatch('[A-Z]{3}', currency)):
        raise ValueError('Printed currency must be an explicit three-letter code')
    if currency and currency != account['currency']:
        raise ValueError(f"Statement currency {currency} differs from account currency {account['currency']}")
    s['currency'] = account['currency']
    start, end = s['periodStart'], s['periodEnd']
    for value in (start, end):
        if not isinstance(value, str) or date.fromisoformat(value).isoformat() != value:
            raise ValueError('Statement dates must be YYYY-MM-DD')
    if start > end:
        raise ValueError('Statement start is after end')
    rows, serials = s['rows'], set()
    if not isinstance(rows, list) or not rows:
        raise ValueError('No statement rows found')
    last = start
    for row in rows:
        if not start <= row['date'] <= end or date.fromisoformat(row['date']).isoformat() != row['date']:
            raise ValueError('Row date is outside the statement period')
        if s.get('kind') == 'bank' and row['date'] < last:
            raise ValueError('Statement rows must be in ascending date order')
        last = row['date']
        if not isinstance(row.get('description'), str) or not row['description'].strip() or any(c in row['description'] for c in '\r\n'):
            raise ValueError('Missing or invalid row description')
        if row.get('direction') not in ('Debit', 'Credit') or decimal(row['amount']) < 0:
            raise ValueError('Rows require a nonnegative amount and Debit or Credit')
        if not isinstance(row.get('serial'), str) or not row['serial'] or row['serial'] in serials:
            raise ValueError('Missing or duplicate row serial')
        serials.add(row['serial'])
        cite = row.get('citation', {})
        if not ((type(cite.get('page')) is int and cite['page'] > 0 and type(cite.get('line')) is int and cite['line'] > 0) or
                (isinstance(cite.get('sheet'), str) and cite['sheet'] and type(cite.get('row')) is int and cite['row'] > 0)):
            raise ValueError('Every row needs a page/line or sheet/row source citation')
        if row.get('foreignAmount') is not None:
            if decimal(row['foreignAmount']) < 0 or not re.fullmatch('[A-Z]{3}', row.get('foreignCurrency') or ''):
                raise ValueError('Foreign amount needs an explicit currency')
        elif row.get('foreignCurrency'):
            raise ValueError('Foreign currency needs an amount')
        if row.get('balance') is not None:
            decimal(row['balance'])
    declared = s.get('declaredChecks', [])
    if not set(declared) <= CHECKS:
        raise ValueError('Unknown statement reader check')
    tested = []
    def test(ok, text):
        tested.append(dict(ok=bool(ok), text=text))
    if s.get('kind') != 'bank':
        from statement_pdf import checks
        if 'printed_totals' not in declared:
            raise ValueError('Card readers must declare printed_totals')
        tested.extend(checks(s, previous['balance'] if previous else None))
    else:
        debits = sum((decimal(r['amount']) for r in rows if r['direction'] == 'Debit'), Decimal(0))
        credits = sum((decimal(r['amount']) for r in rows if r['direction'] == 'Credit'), Decimal(0))
        if 'printed_totals' in declared and s.get('openingBalance') is None:
            raise ValueError('Printed totals check requires openingBalance')
        anchor = previous or opening
        if anchor is None and s.get('openingBalance') is None and 'running_balance' not in declared:
            raise ValueError('The first bank statement needs an opening balance or a running balance column')
        # Infer the source opening from its first printed running balance, or the
        # retained ledger anchor. Overlapping movement before the anchor is reversed.
        if s.get('openingBalance') is None:
            if 'running_balance' in declared and rows[0].get('balance') is not None:
                inferred = decimal(rows[0]['balance']) - signed(rows[0])
            elif previous:
                inferred = decimal(previous['balance']) - sum((signed(r) for r in rows if r['date'] <= previous['end']), Decimal(0))
            else:
                inferred = decimal(opening['balance']) - sum((signed(r) for r in rows if start_date and r['date'] < start_date), Decimal(0))
            s['openingBalance'] = money_text(inferred)
        balance = decimal(s['openingBalance'])
        if 'printed_totals' in declared:
            for field, actual in (('debits', debits), ('credits', credits)):
                if s.get(field) is None:
                    raise ValueError('Printed totals check requires ' + field)
                test(decimal(s[field]) == actual, 'Printed ' + field + ' equal row totals')
            if s.get('closingBalance') is None:
                raise ValueError('Printed totals check requires closingBalance')
            test(balance - debits + credits == decimal(s['closingBalance']), 'Opening − debits + credits equals printed closing balance')
        if 'running_balance' in declared:
            for row in rows:
                balance += signed(row)
                if row.get('balance') is None:
                    raise ValueError('Running balance is missing at ' + row['serial'])
                test(balance == decimal(row['balance']), 'Running balance at ' + row['serial'])
        else:
            balance += credits - debits
        if s.get('closingBalance') is not None:
            test(decimal(s['closingBalance']) == balance, 'Closing balance agrees with rows')
        else:
            s['closingBalance'] = money_text(balance)
        if anchor:
            anchor_date = anchor.get('end', anchor.get('date'))
            overlap = sum((signed(r) for r in rows if r['date'] <= anchor_date), Decimal(0)) if previous else sum((signed(r) for r in rows if start_date and r['date'] < start_date), Decimal(0))
            test(decimal(s['openingBalance']) + overlap == decimal(anchor['balance']), 'Continuity with previous closing balance' if previous else 'Continuity with opening balance')
            if previous and start > previous['end']:
                test(decimal(s['openingBalance']) == decimal(previous['balance']), 'Statement starts from previous closing balance')
        s['debits'], s['credits'] = money_text(debits), money_text(credits)
        s['effectiveStart'] = max(start, start_date or start)
    if not all(c['ok'] for c in tested):
        raise ValueError('; '.join(c['text'] for c in tested if not c['ok']))
    return tested
