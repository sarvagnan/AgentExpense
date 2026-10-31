"""Entirely invented statement layout and PDF text for offline tests."""
import io
from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

TEXT = """Example Card Statement
Statement period: 2026-01-01 to 2026-01-28
Card last four digits: 5678
Previous balance: 0.00
Purchases: 120.00
Cash advances: 0.00
Credits: 30.00
Total due: 90.00
Minimum due: 9.00
Due date: 2026-02-15
Date | Serial | Description | Amount | Direction | Foreign amount | Currency
2026-01-02 | EX-001 | Fable Shop | 120.00 | Debit | 2.00 | USD
2026-01-03 | EX-002 | Payment received | 30.00 | Credit | |
"""


def statement_pdf(text=TEXT, password=None):
    """Make a small text PDF in memory, using no browser or external renderer."""
    writer = PdfWriter()
    for page_text in text.split('\f'):
        page = writer.add_blank_page(width=1000, height=800)
        font = DictionaryObject({NameObject('/Type'): NameObject('/Font'),
                                 NameObject('/Subtype'): NameObject('/Type1'),
                                 NameObject('/BaseFont'): NameObject('/Courier')})
        page[NameObject('/Resources')] = DictionaryObject({NameObject('/Font'): DictionaryObject({NameObject('/F1'): writer._add_object(font)})})
        commands = ['BT /F1 9 Tf 20 770 Td 14 TL']
        for line in page_text.splitlines():
            escaped = line.replace('\\', '\\\\').replace('(', '\\(').replace(')', '\\)')
            commands.append(f'({escaped}) Tj T*')
        commands.append('ET')
        stream = DecodedStreamObject()
        stream.set_data('\n'.join(commands).encode('ascii'))
        page[NameObject('/Contents')] = writer._add_object(stream)
    if password:
        writer.encrypt(password)
    output = io.BytesIO()
    writer.write(output)
    return output.getvalue()


def statement_answer():
    return dict(read_ok=True, card_last4='5678', uncertain=[], period_start='2026-01-01', period_end='2026-01-28',
                previous_balance='0.00', purchases='120.00', cash_advances='0.00', credits='30.00', total_due='90.00',
                minimum_due='9.00', due_date='2026-02-15', transactions=[
                    dict(date='2026-01-02', serial='EX-001', description='Fable Shop', amount='120.00', is_credit=False, page=1, foreign_amount='2.00', foreign_currency='USD'),
                    dict(date='2026-01-03', serial='EX-002', description='Payment received', amount='30.00', is_credit=True, page=1, foreign_amount=None, foreign_currency=None)])
