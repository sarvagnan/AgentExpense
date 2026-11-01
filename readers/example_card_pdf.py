"""Adapter for the documented fictional card layout; parsing remains unchanged."""
from statement_pdf import extract_text, recognised, parse_text
import pypdfium2 as pdfium
INTERFACE = 1
ID = 'example-card-pdf'
NAME = 'Example card PDF'
KINDS = ('card', 'bank')
EXTENSIONS = ('.pdf',)
CHECKS = ('printed_totals',)


def recognise(file):
    if file.suffix.lower() != '.pdf':
        return dict(confidence=0, reasons=['Expected PDF'])
    try:
        # Only inspect the first page. Full positioned extraction belongs to read.
        with pdfium.PdfDocument(file) as document:
            page = document[0]
            textpage = page.get_textpage()
            try:
                found = recognised(textpage.get_text_range().replace('\r\n','\n').replace('\r','\n'))
            finally:
                textpage.close()
                page.close()
        return dict(confidence=1 if found else 0, reasons=['Example card heading and period' if found else 'No example card heading and period'])
    except Exception:
        return dict(confidence=0, reasons=['PDF could not be recognised'])


def read(file, account, password=None):
    # The server decrypts password-protected PDFs in memory before dispatch.
    text = extract_text(file)
    result = parse_text(text)
    for row in result['rows']:
        page = text.split('\f')[row['page']-1]
        line = next(i for i, value in enumerate(page.splitlines(), 1) if len(cells := [c.strip() for c in value.split('|')]) == 7 and cells[0] == row['date'] and cells[1] == row['serial'])
        row['citation'] = dict(page=row['page'], line=line)
    return result
