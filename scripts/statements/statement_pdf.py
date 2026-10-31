"""Reader for the invented card-statement layout in README.md.

PDFium rebuilds horizontal text lines; passwords are handled separately by the
server and never enter extraction metadata. Amounts remain decimal strings.
"""
from datetime import date
from decimal import Decimal
import re
import pypdfium2 as pdfium


TITLE = 'Example Card Statement'
ROW_HEADER = 'Date | Serial | Description | Amount | Direction | Foreign amount | Currency'
AMOUNT = r'(?:0|[1-9]\d*|[1-9]\d{0,2}(?:,\d{3})+)\.\d{2}'


def money(value):
    if not re.fullmatch(AMOUNT, value):
        raise ValueError('Expected a nonnegative amount with two decimal places')
    return format(Decimal(value.replace(',', '')), '.2f')


def iso_date(value):
    if not re.fullmatch(r'\d{4}-\d{2}-\d{2}', value):
        raise ValueError('Expected date YYYY-MM-DD')
    return date.fromisoformat(value).isoformat()


def header(text, label):
    values = re.findall(r'^\s*' + re.escape(label) + r':[ \t]*(.*?)[ \t]*$', text, re.I | re.M)
    if len(values) != 1:
        raise ValueError(label + ' must appear exactly once')
    return values[0]


def card_identifier(text):
    first = text.split('\f')[0]
    if not re.search(r'^\s*Card last four digits:', first, re.I | re.M):
        return None
    value = header(first, 'Card last four digits')
    if not re.fullmatch(r'\d{4}', value):
        raise ValueError('Card last four digits must contain four digits')
    return value


def recognised(text, expected_last4=''):
    # Recognition is deliberately lighter than parsing so incomplete example
    # statements can still reach the pipeline's checked second reading.
    first = text.split('\f')[0]
    try:
        last4 = card_identifier(first)
        return bool(re.search(r'^\s*' + TITLE + r'\s*$', first, re.I | re.M)
                    and (last4 is None or re.fullmatch(r'\d{4}', last4))
                    and (not expected_last4 or last4 is None or last4 == expected_last4)
                    and re.search(r'^\s*Statement period:', first, re.I | re.M))
    except ValueError:
        return False


def parse_text(text, *, expected_last4=''):
    if not recognised(text, expected_last4):
        raise ValueError('This is not a recognisable example card statement for the configured card.')
    pages = text.split('\f')
    first = pages[0]
    period = header(first, 'Statement period').split(' to ')
    if len(period) != 2:
        raise ValueError('Statement period must contain start to end')
    start, end = map(iso_date, period)
    if start > end:
        raise ValueError('Statement start is after end')
    result = dict(card_last4=card_identifier(first), periodStart=start, periodEnd=end,
                  dueDate=iso_date(header(first, 'Due date')), rows=[])
    for label, key in [('Previous balance', 'previousBalance'), ('Purchases', 'purchasesCharges'),
                       ('Cash advances', 'cashAdvances'), ('Credits', 'paymentsCredits'),
                       ('Total due', 'totalDue'), ('Minimum due', 'minimumDue')]:
        result[key] = money(header(first, label))
    serials = set()
    in_rows = False
    for page, page_text in enumerate(pages, 1):
        for line in page_text.splitlines():
            line = line.strip()
            if not line:
                continue
            cells = [cell.strip() for cell in line.split('|')]
            if [cell.casefold() for cell in cells] == [cell.strip().casefold() for cell in ROW_HEADER.split('|')]:
                in_rows = True
                continue
            if not in_rows:
                continue
            if len(cells) != 7:
                raise ValueError(f'Expected seven row columns on page {page}')
            day, serial, description, amount, direction, foreign, currency = cells
            if not re.fullmatch(r'[A-Za-z0-9-]+', serial) or not description:
                raise ValueError(f'Missing or invalid row serial/description on page {page}')
            if serial in serials:
                raise ValueError('Duplicate statement serial')
            serials.add(serial)
            if direction.casefold() not in ('debit', 'credit'):
                raise ValueError(f'Expected Debit or Credit on page {page}, serial {serial}')
            if bool(foreign) != bool(currency) or (currency and not re.fullmatch(r'[A-Z]{3}', currency)):
                raise ValueError(f'Foreign amount needs an explicit currency on page {page}, serial {serial}')
            result['rows'].append(dict(date=iso_date(day), serial=serial, description=description,
                                      amount=money(amount), direction=direction.capitalize(), page=page,
                                      foreignAmount=money(foreign) if foreign else None,
                                      foreignCurrency=currency or None))
    if not result['rows']:
        raise ValueError('No statement rows found')
    return result


def extract_text(path):
    pages = []
    with pdfium.PdfDocument(path) as pdf:
        for page in pdf:
            textpage = page.get_textpage()
            try:
                lines = []
                for index in range(textpage.count_chars()):
                    if abs(pdfium.raw.FPDFText_GetCharAngle(textpage, index)) > .01:
                        continue  # vertical printer marks are not table text
                    char = textpage.get_text_range(index, 1)
                    if not char or char in '\r\n' or ord(char[0]) < 32:
                        continue
                    left, bottom, right, top = textpage.get_charbox(index, loose=True)
                    if top - bottom < .1:  # PDFium's synthetic inter-box spaces
                        continue
                    line = next((line for line in lines if abs(line[0] - bottom) < 1.1), None)
                    if line is None:
                        line = [bottom, []]
                        lines.append(line)
                    line[1].append((left, right, char))
                rendered = []
                for _, chars in sorted(lines, key=lambda line: -line[0]):
                    chunks, chunk, origin, previous = [], '', 0, None
                    for left, right, char in sorted(chars):
                        if previous is None or left - previous > 3:
                            if chunk:
                                chunks.append((origin, chunk))
                            origin, chunk = left, ''
                        chunk += char
                        previous = right
                    if chunk:
                        chunks.append((origin, chunk))
                    line = ''
                    for x, chunk in chunks:
                        # Keep columns aligned; 1.5pt leaves room for narrow glyphs.
                        column = round(x / 1.5)
                        line += ' ' * max(2 if line else 0, column - len(line)) + chunk
                    rendered.append(line.rstrip())
                pages.append('\n'.join(rendered))
            finally:
                textpage.close()
                page.close()
    return '\f'.join(pages)


def parse_pdf(path, *, expected_last4=''):
    return parse_text(extract_text(path), expected_last4=expected_last4)


def checks(statement, previous=None):
    rows = statement['rows']
    debit = sum((Decimal(r['amount']) for r in rows if r['direction'] == 'Debit'), Decimal(0))
    credit = sum((Decimal(r['amount']) for r in rows if r['direction'] == 'Credit'), Decimal(0))
    return [dict(ok=debit == Decimal(statement['purchasesCharges']), text=f"Purchases: rows {debit:.2f} INR; printed {statement['purchasesCharges']} INR"),
            dict(ok=credit == Decimal(statement['paymentsCredits']), text=f"Credits: rows {credit:.2f} INR; printed {statement['paymentsCredits']} INR"),
            dict(ok=Decimal(statement['previousBalance']) + Decimal(statement['purchasesCharges']) + Decimal(statement.get('cashAdvances', '0')) - Decimal(statement['paymentsCredits']) == Decimal(statement['totalDue']), text='Previous balance + purchases + cash advances − credits equals total due'),
            dict(ok=previous is None or Decimal(statement['previousBalance']) == Decimal(previous), text=f"Previous balance {statement['previousBalance']} INR" + (f'; last filed total {previous} INR' if previous is not None else '; no earlier filed statement'))]
