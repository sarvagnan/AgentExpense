"""Upgrade a clean ledger under its lock, preserving all observable outputs."""
from __future__ import annotations
import argparse
import importlib.util
import json
import os
import shutil
from pathlib import Path
import contextlib
import io
import subprocess
import sys
import tempfile

from store import canonical_text, dump, load, read_json, write_text
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
            # The working copy was clean before the upgrade: every change is the migration's.
            command(self.root, 'git', 'add', '-A')
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
    # The upgrade prints counts only: keep the store check's totals, not its per-entry lines.
    output = io.StringIO()
    with contextlib.redirect_stdout(output):
        check(root)
    print('\n'.join(line for line in output.getvalue().splitlines() if not line.startswith('  ')))
    command(root, os.environ.get('HLEDGER_BIN', 'hledger'), '-f', str(journal), 'check', '-s', 'ordereddates')


class CountsOnly(ValueError):
    """A failure whose message holds counts only, safe to print for a private ledger."""


AFTER_APPLY = ('Verification failed after migrating; the checkpoint is restored. Run make verify on a copy '
               'to see details, or set EXPENSES_UPGRADE_DEBUG=1 to print them.')


def validate_plan(step, result, root):
    """Validate the final step's plan in scratch space, even for --dry-run, without touching the ledger.

    A step may check its own result; otherwise the planned manifest and accounts are
    written over a scratch copy of the ledger's store, which loads with every current check.
    """
    if hasattr(step, 'check'):
        step.check(result)
        return
    with tempfile.TemporaryDirectory(prefix='expense-migration-plan-', dir=os.environ.get('EXPENSES_TEST_TMP','/tmp')) as folder:
        scratch = Path(folder)
        shutil.copytree(root / 'data', scratch / 'data')
        if (root / 'extensions').is_dir():
            shutil.copytree(root / 'extensions', scratch / 'extensions')
        write_text(scratch / 'ledger.json', canonical_text(ordered_manifest(result[0]), flat=False))
        dump(scratch / 'data/accounts.json', result[1], scratch)
        load(scratch)


def rehearse(root, options, steps, version):
    """Plan and apply every step on a scratch copy; validate the result at the current format.

    This app reads only the current format, so intermediate results can't be validated
    alone. Returns each step's plan by its source format; the ledger itself is never written.
    """
    with tempfile.TemporaryDirectory(prefix='expense-migration-chain-', dir=os.environ.get('EXPENSES_TEST_TMP','/tmp')) as folder:
        scratch = Path(folder)
        shutil.copytree(root / 'data', scratch / 'data')
        if (root / 'extensions').is_dir():
            shutil.copytree(root / 'extensions', scratch / 'extensions')
        if (root / 'ledger.json').exists():
            shutil.copyfile(root / 'ledger.json', scratch / 'ledger.json')
        plans = {}
        while version < CURRENT_FORMAT:
            step = steps[version]
            result = plans[version] = step.plan(scratch, options)
            if step.TARGET_FORMAT == CURRENT_FORMAT:
                validate_plan(step, result, scratch)
            step.apply(scratch, result)
            version = step.TARGET_FORMAT
        return plans


def report_stale_rules(root, declarations):
    from recurring import stale_rules
    import format1
    actual = [format1.entry(entry) for path in sorted((root / 'data/expenses').glob('*.json')) for entry in read_json(path)]
    rules = [format1.rule(rule) for rule in read_json(root / 'data/forecast.json')]
    count = len(stale_rules(actual, rules, declarations['accounts']))
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
        plans = rehearse(root, options, steps, version)
        result = plans[version]
        print(f'Plan: format {version} -> {step.TARGET_FORMAT} (app v{app_version()})')
        if hasattr(step, 'describe'):
            print('\n'.join(step.describe(result)))
        else:
            print(json.dumps(ordered_manifest(result[0]), indent=2, ensure_ascii=False))
        for later in range(step.TARGET_FORMAT, CURRENT_FORMAT):
            print(f'Then: format {later} -> {later + 1}')
            if hasattr(steps[later], 'describe'):
                print('\n'.join(steps[later].describe(plans[later])))
        if version == 0:
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
        targets = ['ledger.json', 'data/accounts.json', 'expense_ledger.ledger',
                   *(path for source in range(version, CURRENT_FORMAT) if hasattr(steps[source], 'targets')
                     for path in steps[source].targets(root))]
        targets = list(dict.fromkeys(targets))
        before = {name: (root / name).read_bytes() if (root / name).exists() else None for name in targets}
        applied = False
        try:
            rehearse(root, options, steps, version)
            initial_plan = steps[version].plan(root, options)
            if version == 0:
                report_stale_rules(root, initial_plan[1])
            baseline = snapshot(root, format_zero_settings=initial_plan if version == 0 else None)
            # Dashboard paths the applied migrations declare as intended changes.
            declared = [path for source in range(version, CURRENT_FORMAT) for path in getattr(steps[source], 'OUTPUT_CHANGES', ())]
            allowed = {}
            while version < CURRENT_FORMAT:
                step = steps[version]
                result = initial_plan if version == 0 else step.plan(root, options)
                final = step.TARGET_FORMAT == CURRENT_FORMAT
                # This app reads only the current format, so intermediate formats are
                # committed as they are; the final one is validated, verified and
                # compared, and any failure restores the checkpoint for every step.
                if hasattr(step, 'describe'):
                    print('\n'.join(step.describe(result)))
                # Entries whose journal lines a step declares as changing, from every step in the chain.
                for kind, ids in getattr(result, 'allowed', {}).items():
                    allowed.setdefault(kind, set()).update(ids)
                if final:
                    validate_plan(step, result, root)
                applied = True
                step.apply(root, result)
                if final:
                    verify(root)
                    if not compare(baseline, snapshot(root), declared, allowed=allowed):
                        raise CountsOnly('Generated outputs changed beyond the changes the migrations declare.' if declared
                                         else 'Generated outputs changed; migration declares no output changes.')
                repo.commit(f'Upgrade ledger format {version} → {step.TARGET_FORMAT} (app v{app_version()})')
                version = step.TARGET_FORMAT
            print(f'Upgraded ledger to format {version}.')
        except BaseException as error:
            repo.rollback(checkpoint)
            for name, data in before.items():
                target = root / name
                if data is None:
                    target.unlink(missing_ok=True)
                else:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(data)
            folder = root / 'extensions'
            if folder.is_dir() and not any(folder.iterdir()):
                folder.rmdir()
            # Once a step has written, a check's details can name entries, amounts or paths:
            # print counts only unless the operator asks for the details.
            if applied and isinstance(error, Exception) and not isinstance(error, CountsOnly) \
                    and not os.environ.get('EXPENSES_UPGRADE_DEBUG'):
                raise CountsOnly(AFTER_APPLY) from None
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
