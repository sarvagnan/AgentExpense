"""Strict mapped CSV/workbook rows. No format or account information is guessed."""
import csv
from datetime import date, datetime
from decimal import Decimal
import re


def checks(account):
    options = (account.get('statement') or {}).get('options') or {}
    result = []
    if options.get('summary'):
        result.append('printed_totals')
    if options.get('columns', {}).get('balance') is not None:
        result.append('running_balance')
    return tuple(result)


def integer(value, name, minimum=1):
    if type(value) is not int or value < minimum:
        raise ValueError(name + ' must be an integer >= ' + str(minimum))
    return value


def amount(value, options):
    if value is None or str(value).strip() == '':
        return None
    if isinstance(value, bool):
        raise ValueError('Invalid amount')
    raw = str(value).strip()
    decimal = options.get('decimal_separator', '.')
    thousands = options.get('thousands_separator', ',')
    if decimal not in ('.', ',') or (thousands and (len(thousands) != 1 or thousands == decimal)):
        raise ValueError('Invalid decimal/thousand separators')
    if isinstance(value, (int,float,Decimal)):
        result = Decimal(str(value))
        if not result.is_finite():
            raise ValueError('Invalid nonfinite amount')
        return format(result, '.2f') if result.as_tuple().exponent >= -2 else format(result, 'f')
    negative = raw.startswith('(') and raw.endswith(')')
    if negative:
        raw = '-' + raw[1:-1]
    sign = ''
    if raw[:1] in ('-', '+'):
        sign, raw = raw[0], raw[1:]
    parts = raw.split(decimal)
    if len(parts) > 2 or not parts[0] or len(parts) == 2 and not parts[1]:
        raise ValueError('Invalid amount: ' + str(value))
    whole = parts[0]
    if thousands and thousands in whole:
        groups = whole.split(thousands)
        if not (1 <= len(groups[0]) <= 3 and all(len(g) == 3 for g in groups[1:])):
            raise ValueError('Malformed thousands grouping')
        whole = ''.join(groups)
    if not whole.isascii() or not whole.isdigit() or len(parts) == 2 and (not parts[1].isascii() or not parts[1].isdigit()):
        raise ValueError('Invalid amount: ' + str(value))
    result = Decimal(sign + whole + ('.' + parts[1] if len(parts) == 2 else ''))
    return format(result, '.2f') if result.as_tuple().exponent >= -2 else format(result, 'f')


def mapped_rows(table, account, *, sheet):
    options = (account.get('statement') or {}).get('options') or {}
    columns = options.get('columns')
    if not isinstance(columns, dict) or not {'date', 'description'} <= columns.keys():
        raise ValueError('Set statement.options.columns with date, description and amount or debit/credit mappings')
    if ('amount' in columns) == bool('debit' in columns or 'credit' in columns):
        raise ValueError('Map amount or debit/credit columns, exclusively')
    header_row = integer(options.get('header_row', 1), 'header_row')
    skip = options.get('skip_rows', [])
    if not isinstance(skip, list):
        raise ValueError('skip_rows must be a list of one-based source row numbers')
    for row in skip:
        integer(row, 'skip_rows')
    if header_row > len(table):
        raise ValueError('Header row is outside the file')
    headers = [str(x).strip() if x is not None else '' for x in table[header_row - 1]]
    indexes = {}
    for key, value in columns.items():
        if isinstance(value, str):
            if headers.count(value) != 1:
                raise ValueError('Missing or duplicate mapped header: ' + value)
            indexes[key] = headers.index(value)
        else:
            indexes[key] = integer(value, 'column index') - 1
    result, currencies, identifiers = [], set(), set()
    for source_row, cells in enumerate(table[header_row:], header_row + 1):
        if source_row in skip or all(x is None or str(x).strip() == '' for x in cells):
            continue
        if sheet == 'CSV' and len(cells) != len(headers):
            raise ValueError(f'CSV column count differs from header at row {source_row}')
        def cell(key):
            if key not in indexes:
                return None
            index = indexes[key]
            if index >= len(cells):
                raise ValueError(f'Missing {key} at {sheet} row {source_row}')
            value = cells[index]
            if isinstance(value, str) and value.startswith('='):
                raise ValueError('Formula cells cannot supply statement values')
            return value
        raw_date = cell('date')
        try:
            if isinstance(raw_date, datetime):
                day = raw_date.date().isoformat()
            elif isinstance(raw_date, date):
                day = raw_date.isoformat()
            else:
                day = datetime.strptime(str(raw_date).strip(), options.get('date_format', '%Y-%m-%d')).date().isoformat()
        except (TypeError, ValueError):
            raise ValueError(f'Invalid date at {sheet} row {source_row}') from None
        description = str(cell('description') or '').strip()
        if not description or any(c in description for c in '\r\n'):
            raise ValueError(f'Missing or multiline description at {sheet} row {source_row}')
        if 'amount' in indexes:
            value = amount(cell('amount'), options)
            if value is None:
                raise ValueError(f'Missing amount at {sheet} row {source_row}')
            convention = options.get('sign', 'debit-negative')
            if convention not in ('debit-negative', 'debit-positive'):
                raise ValueError('sign must be debit-negative or debit-positive')
            negative = Decimal(value) < 0
            direction = 'Debit' if negative == (convention == 'debit-negative') else 'Credit'
            if 'direction' in indexes:
                direction = str(cell('direction')).strip().capitalize()
                if direction not in ('Debit', 'Credit') or Decimal(value) < 0:
                    raise ValueError('Explicit direction needs Debit/Credit and a nonnegative amount')
        else:
            debit, credit = amount(cell('debit'), options), amount(cell('credit'), options)
            if debit is None and credit is None or debit and Decimal(debit) < 0 or credit and Decimal(credit) < 0 or Decimal(debit or '0') and Decimal(credit or '0'):
                raise ValueError(f'Expected one nonnegative debit or credit at {sheet} row {source_row}')
            direction = 'Debit' if Decimal(debit or '0') or credit is None else 'Credit'
            value = debit if direction == 'Debit' else credit
        reference = str(cell('reference') or '').strip() or None
        # Row identities are local to this source. Cross-download matching uses
        # the optional reference, never this physical row number.
        serial = 'row-' + str(source_row)
        identifier = str(cell('identifier') or '').strip() or None
        if identifier:
            identifiers.add(identifier)
        currency = str(cell('currency') or '').strip() or None
        if currency:
            if not re.fullmatch('[A-Z]{3}', currency):
                raise ValueError('Invalid printed row currency')
            currencies.add(currency)
        foreign = amount(cell('foreign_amount'), options)
        foreign_currency = str(cell('foreign_currency') or '').strip() or None
        result.append(dict(date=day, description=description, serial=serial, amount=format(abs(Decimal(value)), 'f'), direction=direction,
                           reference=reference, balance=amount(cell('balance'), options), page=1,
                           foreignAmount=foreign, foreignCurrency=foreign_currency, citation=dict(sheet=sheet, row=source_row)))
    if not result:
        raise ValueError('No statement rows found')
    if len(identifiers) > 1:
        raise ValueError('Several printed account identifiers occur in this statement')
    if len(currencies) > 1:
        raise ValueError('A statement must have one account currency')
    summary = options.get('summary', {})
    if not isinstance(summary, dict):
        raise ValueError('summary must be an object of exact decimal strings')
    # Summary mapping is explicit operator configuration, not invented printed
    # totals. Values may be numeric cell references {row, column} in this sheet.
    values = {}
    for key in ('openingBalance', 'closingBalance', 'debits', 'credits'):
        if key not in summary:
            continue
        value = summary[key]
        if isinstance(value, dict):
            r, c = integer(value.get('row'), 'summary row'), integer(value.get('column'), 'summary column')
            try:
                value = table[r-1][c-1]
            except IndexError:
                raise ValueError('Summary cell is outside the sheet') from None
        values[key] = amount(value, options)
    def source_cell(value):
        if not isinstance(value, dict): return value
        r, c = integer(value.get('row'), 'source row'), integer(value.get('column'), 'source column')
        try: value = table[r-1][c-1]
        except IndexError: raise ValueError('Source cell is outside the sheet') from None
        if isinstance(value,str) and value.startswith('='): raise ValueError('Formula cells cannot supply statement values')
        return value
    def period_date(value):
        value = source_cell(value)
        if isinstance(value, datetime): return value.date().isoformat()
        if isinstance(value, date): return value.isoformat()
        if re.fullmatch(r'\d{4}-\d{2}-\d{2}', str(value).strip()):
            return date.fromisoformat(str(value).strip()).isoformat()
        return datetime.strptime(str(value).strip(), options.get('date_format', '%Y-%m-%d')).date().isoformat()
    period = options.get('period', {})
    currency = next(iter(currencies), source_cell(options.get('currency_cell', options.get('currency'))))
    return dict(identifier=next(iter(identifiers), None), kind='bank', periodStart=period_date(period.get('start', result[0]['date'])),
                periodEnd=period_date(period.get('end', result[-1]['date'])), currency=currency, rows=result, **values)


def read_csv(file, account):
    options = (account.get('statement') or {}).get('options') or {}
    delimiter = options.get('delimiter', ',')
    if not isinstance(delimiter, str) or len(delimiter) != 1:
        raise ValueError('CSV delimiter must be one character')
    with file.open(encoding=options.get('encoding', 'utf-8-sig'), newline='') as stream:
        try:
            table = list(csv.reader(stream, delimiter=delimiter, strict=True))
        except (csv.Error, UnicodeError) as exc:
            raise ValueError('CSV could not be decoded: ' + str(exc)) from None
    return mapped_rows(table, account, sheet='CSV')


def read_sheet(file, account):
    options = (account.get('statement') or {}).get('options') or {}
    sheet = options.get('sheet', 0)
    if type(sheet) is not int and not isinstance(sheet, str) or type(sheet) is int and sheet < 0:
        raise ValueError('sheet must be a name or zero-based index')
    if file.suffix.lower() == '.xlsx':
        import openpyxl
        book = openpyxl.load_workbook(file, read_only=True, data_only=False, keep_links=False)
        try:
            ws = book.worksheets[sheet] if type(sheet) is int else book[sheet]
            return mapped_rows(list(ws.values), account, sheet=ws.title)
        finally:
            book.close()
    import xlrd
    book = xlrd.open_workbook(str(file), on_demand=True)
    try:
        ws = book.sheet_by_index(sheet) if type(sheet) is int else book.sheet_by_name(sheet)
        table = [[datetime(*xlrd.xldate_as_tuple(cell.value, book.datemode)) if cell.ctype == xlrd.XL_CELL_DATE else cell.value for cell in ws.row(i)] for i in range(ws.nrows)]
        return mapped_rows(table, account, sheet=ws.name)
    finally:
        book.release_resources()
