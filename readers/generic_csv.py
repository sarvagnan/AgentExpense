"""Explicitly mapped CSV exports."""
from tabular import read_csv, checks
INTERFACE = 1
ID = 'generic-csv'
NAME = 'Generic CSV'
KINDS = ('bank', 'card')
EXTENSIONS = ('.csv',)
CHECKS = checks


def recognise(file):
    return dict(confidence=.5 if file.suffix.lower() == '.csv' else 0, reasons=['CSV extension; account column mapping is required'])


def read(file, account, password=None):
    return read_csv(file, account)
