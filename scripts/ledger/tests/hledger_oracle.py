"""Optional immutable hledger date fixtures shared by nested integration checks.

Only the external CLI result is reused. Callers still render every schedule,
recompute every expected date and run every assertion on every verification.
Without an explicit /tmp cache, standalone ledger tests always invoke hledger.
"""
import csv
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile


def calendar_dates(binary, journal, start, end):
    arguments = ['-f', '-', 'print', f'--forecast={start}..{end}', '-O', 'csv']
    cache = os.environ.get('EXPENSES_TEST_ORACLE_CACHE')
    target = None
    if cache:
        folder = Path(cache).resolve()
        if not folder.is_relative_to(Path('/tmp').resolve()):
            raise ValueError('Test oracle cache must stay under /tmp')
        folder.mkdir(parents=True, exist_ok=True)
        executable = Path(shutil.which(binary) or binary).resolve()
        key = hashlib.sha256(json.dumps([1, str(executable), arguments, journal]).encode())
        key.update(executable.read_bytes())
        target = folder / (key.hexdigest() + '.json')
        if target.is_file():
            return json.loads(target.read_text())
    run = subprocess.run([binary, *arguments], input=journal, text=True,
                         capture_output=True, check=True)
    actual = {}
    for row in csv.DictReader(io.StringIO(run.stdout)):
        if row['account'].startswith('Expenses:'):
            actual.setdefault(row['account'], []).append(row['date'])
    if target:
        # Publish complete immutable results; concurrent checks may compute the
        # same input, but cannot observe a partially written cache document.
        with tempfile.NamedTemporaryFile(mode='w', dir=target.parent, delete=False) as out:
            json.dump(actual, out)
        temporary = Path(out.name)
        try:
            try:
                os.link(temporary, target)
            except FileExistsError:
                pass
        finally:
            temporary.unlink()
    return actual
