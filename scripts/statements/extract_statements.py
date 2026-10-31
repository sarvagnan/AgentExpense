"""Export example card statements for the read-only reconciliation CLI."""
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from project_paths import ledger_root
from statement_pdf import parse_text, checks


def extract(source_dir):
    statements, transactions = [], []
    for path in sorted(Path(source_dir).glob('*.txt')):
        statement = parse_text(path.read_text())
        failures = [check['text'] for check in checks(statement) if not check['ok']]
        if failures:
            raise ValueError(f"{path.name}: " + '; '.join(failures))
        source = path.with_suffix('.pdf').name
        statements.append({key: value for key, value in statement.items() if key != 'rows'} |
                          dict(sourceFile=source, transactionCount=len(statement['rows'])))
        for row in statement['rows']:
            transactions.append(dict(date=row['date'], serialNumber=row['serial'], description=row['description'],
                                     amount=row['amount'], currency=statement.get('currency'), direction=row['direction'], pageNumber=row['page'],
                                     internationalAmount=row['foreignAmount'], internationalCurrency=row['foreignCurrency'],
                                     statementEnd=statement['periodEnd'], sourceFile=source))
    return dict(statements=statements, transactions=transactions, errors=[])


def main():
    source = Path(sys.argv[1]) if len(sys.argv) > 1 else ledger_root() / 'data/statements'
    output = Path(sys.argv[2]) if len(sys.argv) > 2 else source / 'extracted.json'
    data = extract(source)
    output.write_text(json.dumps(data, indent=2) + '\n')
    print(json.dumps(dict(statementCount=len(data['statements']), transactionCount=len(data['transactions']), errorCount=0)))


if __name__ == '__main__':
    main()
