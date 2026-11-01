"""Explicitly mapped workbook exports."""
from tabular import read_sheet, checks
INTERFACE = 1
ID = 'generic-sheet'
NAME = 'Generic spreadsheet'
KINDS = ('bank', 'card')
EXTENSIONS = ('.xlsx', '.xls')
CHECKS = checks


def recognise(file):
    return dict(confidence=.5 if file.suffix.lower() in EXTENSIONS else 0, reasons=['Workbook extension; account column mapping is required'])


def read(file, account, password=None):
    return read_sheet(file, account)
