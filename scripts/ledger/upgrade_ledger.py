"""Upgrade a clean ledger under its lock, preserving all observable outputs."""
from __future__ import annotations
import argparse
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

from store import dump, read_json, validate
from project_paths import APPLICATION_ROOT, ledger_root
from ledger_manifest import CURRENT_FORMAT, app_version, ordered_manifest, read_manifest
from ledger_lock import ledger_lock
from compare_outputs import snapshot, compare


def command(root, *args):
    result = subprocess.run(args, cwd=root, text=True, capture_output=True)
    if result.returncode:
        raise RuntimeError(f'{args[0]} operation failed.')
    return result.stdout.strip()


class Repository:
    def __init__(self, root):
        root = root.resolve()
        self.root = root
        self.jj = (root / '.jj').is_dir()
        if self.jj:
            actual = command(root, 'jj', 'root')
        else:
            actual = command(root, 'git', 'rev-parse', '--show-toplevel')
        if Path(actual).resolve() != root:
            raise ValueError('Initialize the ledger as its own repository before upgrading.')

    def jj_command(self, *args):
        return command(self.root, 'jj', '--config', 'snapshot.max-new-file-size=0', *args)

    def require_clean(self):
        changed = (self.jj_command('diff', '--name-only').splitlines() if self.jj else
                   command(self.root, 'git', 'status', '--porcelain', '--untracked-files=all').splitlines())
        changed = [line for line in changed if (line if self.jj else line[3:]) != '.lock']
        if changed:
            raise ValueError('The ledger has uncommitted changes; upgrade requires a clean working copy.')

    def checkpoint(self):
        if self.jj:
            return (self.jj_command('log', '-r', '@-', '--no-graph', '-T', 'commit_id'),
                    self.jj_command('op', 'log', '--limit', '1', '--no-graph', '-T', 'id'))
        return command(self.root, 'git', 'rev-parse', 'HEAD'), None

    def commit(self, message):
        if self.jj:
            self.jj_command('commit', '-m', message)
            self.jj_command('bookmark', 'set', 'main', '-r', '@-')
        else:
            command(self.root, 'git', 'add', '--', 'ledger.json', 'data/accounts.json')
            command(self.root, 'git', 'commit', '-m', message)

    def rollback(self, checkpoint):
        if self.jj:
            self.jj_command('op', 'restore', checkpoint[1])
        else:
            command(self.root, 'git', 'reset', '--mixed', checkpoint[0])


def migrations():
    steps = {}
    for path in sorted((APPLICATION_ROOT / 'migrations').glob('[0-9][0-9][0-9][0-9]_*.py')):
        spec = importlib.util.spec_from_file_location(path.stem, path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        if module.SOURCE_FORMAT in steps or module.TARGET_FORMAT != module.SOURCE_FORMAT + 1:
            raise ValueError('Invalid migration sequence.')
        steps[module.SOURCE_FORMAT] = module
    return steps


def verify(root):
    from check_store import check
    from generate_ledger import render
    from store import load
    journal = root / 'expense_ledger.ledger'
    journal.write_text(render(load(root), root))
    check(root)
    command(root, os.environ.get('HLEDGER_BIN', 'hledger'), '-f', str(journal), 'check', '-s', 'ordereddates')


def validate_plan(result):
    # Validate in scratch space even for --dry-run, without touching the ledger.
    with tempfile.TemporaryDirectory(prefix='expense-migration-plan-', dir=os.environ.get('EXPENSES_TEST_TMP','/tmp')) as folder:
        root = Path(folder)
        dump(root / 'ledger.json', result[0])
        validate({root / 'data/accounts.json': result[1]}, root)


def report_stale_rules(root, declarations):
    from recurring import stale_rules
    actual = [entry for path in sorted((root / 'data/expenses').glob('*.json')) for entry in read_json(path)]
    count = len(stale_rules(actual, read_json(root / 'data/forecast.json'), declarations['accounts']))
    print(f'Recurring rules already paid: {count}. Run make rules-catch-up with this LEDGER_DIR to move past last payment.')


def upgrade(root, options):
    root = root.resolve()
    manifest = read_manifest(root, check_format=False)
    version = manifest['format']
    if version > CURRENT_FORMAT:
        read_manifest(root)  # Shared, identical newer-format diagnostic.
    if version == CURRENT_FORMAT:
        print(f'Ledger already uses format {CURRENT_FORMAT}; no changes.')
        return
    repo = Repository(root)
    steps = migrations()
    if options.dry_run:
        repo.require_clean()
        step = steps[version]
        result = step.plan(root, options)
        validate_plan(result)
        print(f'Plan: format {version} -> {step.TARGET_FORMAT} (app v{app_version()})')
        print(json.dumps(ordered_manifest(result[0]), indent=2, ensure_ascii=False))
        report_stale_rules(root, result[1])
        return
    with ledger_lock(root):
        # Re-read after waiting for another cooperating writer.
        version = read_manifest(root, check_format=False)['format']
        if version >= CURRENT_FORMAT:
            read_manifest(root)
            print(f'Ledger already uses format {CURRENT_FORMAT}; no changes.')
            return
        repo.require_clean()
        checkpoint = repo.checkpoint()
        print('Checkpoint: ' + checkpoint[0])
        # Migrations and verification only write these paths. Preserve ignored journal bytes too.
        targets = ['ledger.json', 'data/accounts.json', 'expense_ledger.ledger']
        before = {name: (root / name).read_bytes() if (root / name).exists() else None for name in targets}
        try:
            initial_plan = steps[version].plan(root, options)
            validate_plan(initial_plan)
            if version == 0:
                report_stale_rules(root, initial_plan[1])
            baseline = snapshot(root, format_zero_settings=initial_plan if version == 0 else None)
            while version < CURRENT_FORMAT:
                step = steps[version]
                result = initial_plan if version == 0 else step.plan(root, options)
                validate_plan(result)
                step.apply(root, result)
                verify(root)
                if not compare(baseline, snapshot(root)):
                    raise ValueError('Generated outputs changed; migration declares no output changes.')
                repo.commit(f'Upgrade ledger format {version} → {step.TARGET_FORMAT} (app v{app_version()})')
                version = step.TARGET_FORMAT
            print(f'Upgraded ledger to format {version}.')
        except BaseException:
            repo.rollback(checkpoint)
            for name, data in before.items():
                target = root / name
                if data is None:
                    target.unlink(missing_ok=True)
                else:
                    target.write_bytes(data)
            raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=ledger_root())
    parser.add_argument('--dry-run', action='store_true')
    parser.add_argument('--name', default='My ledger')
    parser.add_argument('--default-currency')
    parser.add_argument('--locale')
    parser.add_argument('--start-date')
    parser.add_argument('--review-storage-key')
    parser.add_argument('--accounts', type=Path)
    args = parser.parse_args()
    try:
        upgrade(args.root.resolve(), args)
    except Exception as exc:
        parser.exit(1, f'Upgrade failed; checkpoint restored if migration began: {exc}\n')


if __name__ == '__main__':
    main()
