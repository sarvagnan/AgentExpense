"""Compare ledger outputs without printing financial contents (only results/counts)."""
from __future__ import annotations
import argparse
from collections import Counter
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

from store import read_json, store_paths, load
from generate_ledger import render
from project_paths import APPLICATION_ROOT
from ledger_manifest import CURRENT_FORMAT
from node_toolchain import node_environment

BASELINE_FILE = 'outputs.json'
BUILDER_FAILURE = 'AGENTEXPENSE_BUILDER_FAILURE '


def builder_failure(stderr, builders):
    """One line naming the failed builder and its own error message."""
    for line in reversed(stderr.splitlines()):
        if line.startswith(BUILDER_FAILURE):
            try:
                failure = json.loads(line[len(BUILDER_FAILURE):])
            except ValueError:
                break
            name = failure.get('name') if failure.get('name') in builders else 'builders'
            message = ' '.join(str(failure.get('message') or '').split())
            return f'Unable to generate dashboard output ({name}): {message}' if message else f'Unable to generate dashboard output ({name}).'
    return 'Unable to generate dashboard output (builders): Node exited before a builder reported.'


def strip_header(journal):
    lines = journal.splitlines(keepends=True)
    while lines and (lines[0].startswith(';') or not lines[0].strip()):
        lines.pop(0)
    return ''.join(lines)


def snapshot(root, *, format_zero_settings=None):
    """Build in scratch space; neither the source ledger nor app outputs are written."""
    with tempfile.TemporaryDirectory(prefix='expense-outputs-', dir=os.environ.get('EXPENSES_TEST_TMP','/tmp')) as folder:
        scratch = Path(folder)
        ledger = scratch / 'ledger'
        # Only source data is needed. Never copy repository metadata, drafts or auth.
        shutil.copytree(root / 'data', ledger / 'data')
        manifest = root / 'ledger.json'
        if manifest.exists():
            # This app reads only the current format. An older manifest (a migration
            # baseline) is compared as if current; a migration whose settings change
            # the outputs declares that change instead.
            value = read_json(manifest)
            if isinstance(value, dict) and type(value.get('format')) is int and 0 < value['format'] < CURRENT_FORMAT:
                from store import dump
                dump(ledger / 'ledger.json', {**value, 'format': CURRENT_FORMAT})
            else:
                shutil.copyfile(manifest, ledger / 'ledger.json')
            documents = load(ledger)
        else:
            # Format-0 has no metadata. Migration comparisons supply the exact
            # proposed settings so both sides classify unchanged entries identically.
            from account_metadata import infer_kind, is_balance_sheet
            from store import dump
            documents = {p: read_json(p) for p in store_paths(ledger)}
            declarations = documents[ledger / 'data/accounts.json']
            if format_zero_settings is None:
                postings = [p for path, rows in documents.items() if path.parent.name == 'expenses' or path.name == 'forecast.json' for row in rows for p in row['postings']]
                counts = Counter(p.get('cost', p)['commodity'] for p in postings if is_balance_sheet(p['account']))
                default = counts.most_common(1)[0][0] if counts else 'INR'
                metadata = {}
                for account in declarations['accounts']:
                    units = {p['commodity'] for p in postings if p['account'] == account}
                    metadata[account] = {'kind': infer_kind(account) or 'person', 'currency': next(iter(units)) if len(units) == 1 else default} if is_balance_sheet(account) else {}
                settings = dict(format=CURRENT_FORMAT, id='00000000-0000-4000-8000-000000000000', name='Comparison', defaultCurrency=default)
                declarations = {**declarations, 'accounts': metadata}
            else:
                settings, declarations = format_zero_settings
                settings = {**settings, 'format': CURRENT_FORMAT}
            dump(ledger / 'ledger.json', settings)
            dump(ledger / 'data/accounts.json', declarations)
            documents[ledger / 'data/accounts.json'] = declarations
        journal = render(documents, ledger)
        journal_path = ledger / 'expense_ledger.ledger'
        journal_path.write_text(journal)
        result = {'journal': strip_header(journal)}
        hledger = os.environ.get('HLEDGER_BIN') or shutil.which('hledger') or '/opt/homebrew/bin/hledger'
        for name, args in [('print', ['print']), ('bal', ['bal', '--flat']), ('bal-cost', ['bal', '--flat', '-B']), ('reg', ['reg'])]:
            run = subprocess.run([hledger, '-f', str(journal_path), *args], capture_output=True, text=True)
            if run.returncode:
                raise ValueError(f'Unable to generate {name} output.')
            result[name] = run.stdout
        application = scratch / 'application'
        for relative in ['scripts/project-paths.mjs', 'scripts/dashboard-data.mjs', 'schema/currency-minor-units.json', 'schema/ledger.schema.json', 'schema/store.schema.json', 'schema/currencies.json', 'schema/recurring-locales.json']:
            target = application / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(APPLICATION_ROOT / relative, target)
        scripts = application / 'review-dashboard/scripts'
        shutil.copytree(APPLICATION_ROOT / 'review-dashboard/scripts', scripts)
        # These builders read one immutable snapshot. Share a Node process and
        # its imported modules instead of starting it four times per comparison.
        builders = ['copy-fuel-log', 'build-dashboard-data', 'build-review-data', 'build-add-patterns']
        urls = {name: (scripts / (name + '.mjs')).as_uri() for name in builders}
        # A failure ends stderr with one marked JSON line naming the builder and
        # the first line of its error message; builders' own output stays out.
        code = ("for (const [name, url] of Object.entries(JSON.parse(process.argv[1]))) { try { await import(url); } catch (error) { "
                "const message = String(error?.message ?? error).split('\\n')[0].trim(); "
                "process.stderr.write('\\n' + process.argv[2] + JSON.stringify({name, message}) + '\\n'); process.exit(1); } }")
        run = subprocess.run(['node', '--input-type=module', '-e', code, json.dumps(urls), BUILDER_FAILURE],
                             env={**node_environment(), 'LEDGER_DIR': str(ledger), 'DASHBOARD_DATA_DIR': str(application / 'review-dashboard')}, capture_output=True)
        if run.returncode:
            raise ValueError(builder_failure(run.stderr.decode(errors='replace'), builders))
        for file in sorted((application / 'review-dashboard/app/data').glob('*.json')):
            if file.name.startswith('.'):
                continue  # Private provenance is not a financial/browser output.
            result['dashboard/' + file.name] = json.loads(file.read_text())
        return result


def remove_path(value, parts):
    if not parts:
        return
    key, *rest = parts
    if isinstance(value, list):
        for i, item in enumerate(value):
            if key in ('*', str(i)):
                if rest:
                    remove_path(item, rest)
                else:
                    value[i] = None
    elif isinstance(value, dict):
        for candidate in list(value):
            if key in ('*', candidate):
                if rest:
                    remove_path(value[candidate], rest)
                else:
                    del value[candidate]


def normalize(value, ignores):
    value = json.loads(json.dumps(value))
    for key, item in value.items():
        if not key.startswith('dashboard/'):
            continue
        remove_path(item, ['meta', 'generatedAt'])
        for ignore in ignores:
            # Dotted paths or JSON Pointer; applied to each dashboard document.
            parts = ignore.lstrip('/').split('/') if ignore.startswith('/') else ignore.removeprefix('$.').split('.')
            remove_path(item, [p.replace('~1', '/').replace('~0', '~') for p in parts])
    return value


def compare(left, right, ignores=(), *, emit=True):
    left, right = normalize(left, ignores), normalize(right, ignores)
    passed = True
    for key in sorted(left.keys() | right.keys()):
        a, b = left.get(key), right.get(key)
        same = a == b
        passed &= same
        if isinstance(a, str) and isinstance(b, str):
            count = sum((Counter(a.splitlines()) - Counter(b.splitlines())).values()) + sum((Counter(b.splitlines()) - Counter(a.splitlines())).values())
            if not same and not count:  # Reordered lines still differ.
                count = sum(x != y for x, y in zip(a.splitlines(), b.splitlines()))
            unit = 'lines'
        else:
            def differences(x, y):
                if type(x) is not type(y): return 1
                if isinstance(x, dict): return sum(differences(x.get(k), y.get(k)) for k in x.keys() | y.keys())
                if isinstance(x, list): return sum(differences(u, v) for u, v in zip(x, y)) + abs(len(x) - len(y))
                return int(x != y)
            count, unit = differences(a, b), 'entries'
        if emit:
            print(f'{"PASS" if same else "FAIL"} {key}: {count} {unit} differing')
    return passed


def load_outputs(path):
    saved = path / BASELINE_FILE
    return read_json(saved) if saved.is_file() else snapshot(path)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('left', type=Path)
    parser.add_argument('right', type=Path, nargs='?')
    parser.add_argument('--save-baseline', type=Path)
    parser.add_argument('--ignore', action='append', default=[])
    args = parser.parse_args()
    try:
        outputs = load_outputs(args.left.resolve())
        if args.save_baseline:
            args.save_baseline.mkdir(parents=True, exist_ok=True)
            (args.save_baseline / BASELINE_FILE).write_text(json.dumps(outputs, ensure_ascii=False) + '\n')
            print(f'PASS baseline: {len(outputs)} outputs saved')
        elif args.right:
            if not compare(outputs, load_outputs(args.right.resolve()), args.ignore):
                raise SystemExit(1)
        else:
            parser.error('Supply a second root or --save-baseline DIR.')
    except Exception:
        parser.exit(1, 'FAIL comparison: output generation failed.\n')


if __name__ == '__main__':
    main()
