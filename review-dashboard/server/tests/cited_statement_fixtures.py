"""Invented addressed statements, generated in memory; no saved binary fixtures."""
import csv
import io
from statement_reading import cell_address
from statement_fixtures import statement_pdf


def table(currency):
    return [['Period start','2026-01-01','Period end','2026-01-28'],
            ['Currency',currency],['Opening','100.00'],['Closing','92.00'],
            ['Debits','10.00'],['Credits','2.00'],
            ['Date','Description','Amount','Balance','Reference'],
            ['2026-01-02','Fable Shop','-10.00','90.00','CITED-001'],
            ['2026-01-03','Fable refund','2.00','92.00','CITED-002']]


def answer(currency, sheet='CSV', pdf=False):
    rows = table(currency)
    def fact(value, row, column):
        return dict(value=value,source=f'p1:l{row}' if pdf else cell_address(sheet,row,column))
    result = dict(read_ok=True,balanceStyle='account',uncertain=[],periodStart=fact(rows[0][1],1,2),periodEnd=fact(rows[0][3],1,4),
                  currency=fact(currency,2,2),**{key:fact(rows[r-1][1],r,2) for key,r in [('openingBalance',3),('closingBalance',4),('debits',5),('credits',6)]})
    result['rows']=[]
    for i,row in enumerate(rows[7:],8):
        result['rows'].append(dict(date=fact(row[0],i,1),description=fact(row[1],i,2),amount=fact(row[2].lstrip('-'),i,3),
                                   direction=fact('Debit' if row[2].startswith('-') else 'Credit',i,3),balance=fact(row[3],i,4),reference=fact(row[4],i,5)))
    return result


def file_bytes(currency, extension):
    rows=table(currency)
    if extension=='csv':
        output=io.StringIO();csv.writer(output).writerows(rows);return output.getvalue().encode()
    if extension=='pdf':
        # Each PDF line has complete row text. Directions are printed explicitly.
        lines=[' | '.join(row) for row in rows]
        for i,row in enumerate(rows[7:],7): lines[i]+=' | '+('Debit' if row[2].startswith('-') else 'Credit')
        return statement_pdf('\n'.join(lines))
    output=io.BytesIO()
    if extension=='xlsx':
        import openpyxl
        book=openpyxl.Workbook();sheet=book.active;sheet.title='Activity'
        for row in rows:sheet.append(row)
        book.save(output)
    else:
        import xlwt
        book=xlwt.Workbook();sheet=book.add_sheet('Activity')
        for r,row in enumerate(rows):
            for c,value in enumerate(row): sheet.write(r,c,value)
        book.save(output)
    return output.getvalue()
