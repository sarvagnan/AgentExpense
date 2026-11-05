"""Create a personal ledger from templates/ledger/ as its own repository.

Usage (from the application root):
    make new-ledger DIR=/path/to/ledger NAME="Your name" CURRENCY=USD [LOCALE=en-US] [VCS=jj|git]

The new folder must not exist yet (or be empty), and must lie outside the
application tree, except for the application's ignored ledger/ folder. The ledger
is verified with make verify-data before its first commit.
"""
import argparse
from datetime import date
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from project_paths import APPLICATION_ROOT
from ledger_manifest import CURRENT_FORMAT, app_version, manifest_schema, ordered_manifest, validate_manifest

TEMPLATE = APPLICATION_ROOT / 'templates/ledger'
# Make passes its own variables through these; nested make runs must not inherit them.
MAKE_VARIABLES = ('MAKEFLAGS', 'MFLAGS', 'MAKEOVERRIDES', 'MAKELEVEL', 'ARGS')


class SetupError(ValueError):
    pass


def check_target(target: Path, application: Path = APPLICATION_ROOT) -> Path:
    target = target.expanduser().resolve()
    application = application.resolve()
    if target.is_relative_to(application) and target != application / 'ledger':
        raise SetupError(f'{target} is inside the application. Choose a folder outside it '
                         f'(or {application / "ledger"}, which the application ignores).')
    if application.is_relative_to(target):
        raise SetupError(f'{target} contains the application. Choose a new, separate folder.')
    if target.exists() and (not target.is_dir() or any(target.iterdir())):
        raise SetupError(f'{target} already exists and is not empty. Choose a new folder.')
    if not target.parent.is_dir():
        raise SetupError(f'{target.parent} does not exist. Create it first, or choose another folder.')
    return target


def manifest(name: str, currency: str, locale: str | None) -> dict:
    template = json.loads((TEMPLATE / 'ledger.json').read_text())
    value = dict(template, format=CURRENT_FORMAT, id=str(uuid.uuid4()), name=name.strip(), defaultCurrency=currency)
    if locale:
        value['locale'] = locale
    value = ordered_manifest(value)
    for field, label in (('name', 'NAME'), ('defaultCurrency', 'CURRENCY'), ('locale', 'LOCALE')):
        if field in value:
            try:
                validate_manifest(value[field], manifest_schema()['properties'][field], field)
            except ValueError:
                raise SetupError(f'{label} is not valid: {value[field]!r}.') from None
    validate_manifest(value, manifest_schema())
    return value


def write_ledger(target: Path, name: str, currency: str, locale: str | None, today: date) -> dict:
    known = json.loads((APPLICATION_ROOT / 'schema/currencies.json').read_text())
    if currency not in known:
        raise SetupError(f'CURRENCY must be an ISO 4217 code such as USD, EUR or INR; {currency!r} is not one.')
    settings = manifest(name, currency, locale)
    shutil.copytree(TEMPLATE, target, dirs_exist_ok=True)
    (target / 'ledger.json').write_text(json.dumps(settings, indent=2, ensure_ascii=False) + '\n')
    accounts = json.loads((TEMPLATE / 'data/accounts.json').read_text())
    accounts['commodities'] = [currency]
    (target / 'data/accounts.json').write_text(json.dumps(accounts, indent=2, ensure_ascii=False) + '\n')
    (target / 'data/expenses').mkdir(exist_ok=True)
    (target / f'data/expenses/{today.year}.json').write_text('[]\n')
    return settings


def environment(target: Path) -> dict:
    env = {key: value for key, value in os.environ.items() if key not in MAKE_VARIABLES}
    env['LEDGER_DIR'] = str(target)
    env.setdefault('PYTHONDONTWRITEBYTECODE', '1')
    return env


def verify(target: Path):
    result = subprocess.run(['make', '--no-print-directory', 'verify-data', 'LEDGER_DIR=' + str(target)],
                            cwd=APPLICATION_ROOT, env=environment(target), text=True, capture_output=True)
    if result.returncode:
        raise SetupError('The new ledger did not pass make verify-data:\n' + (result.stdout + result.stderr)[-4000:])


def choose_vcs(requested: str | None) -> str:
    if requested:
        if requested not in ('jj', 'git'):
            raise SetupError('VCS must be jj or git.')
        if not shutil.which(requested):
            raise SetupError(f'{requested} is not installed; run make doctor.')
        return requested
    if shutil.which('jj'):
        return 'jj'
    if shutil.which('git'):
        return 'git'
    raise SetupError('Neither jj nor git is installed; run make doctor.')


def first_commit(target: Path, vcs: str, message: str):
    env = environment(target)
    def run(*command):
        result = subprocess.run(command, cwd=target, env=env, text=True, capture_output=True)
        if result.returncode:
            raise SetupError(f'{" ".join(command[:3])} failed:\n{(result.stdout + result.stderr)[-2000:]}')
    if vcs == 'jj':
        run('jj', 'git', 'init', '--colocate', str(target))
        run('jj', '-R', str(target), 'commit', '-m', message)
        run('jj', '-R', str(target), 'bookmark', 'set', 'main', '-r', '@-')
    else:
        run('git', 'init', '--quiet', str(target))
        run('git', '-C', str(target), 'checkout', '--quiet', '-b', 'main')
        run('git', '-C', str(target), 'add', '--all')
        run('git', '-C', str(target), 'commit', '--quiet', '-m', message)


def create(target: Path, name: str, currency: str, locale: str | None = None, vcs: str | None = None,
           today: date | None = None) -> dict:
    target = check_target(target)
    vcs = choose_vcs(vcs)
    created = not target.exists()
    try:
        settings = write_ledger(target, name, currency, locale, today or date.today())
        verify(target)
        first_commit(target, vcs, f'Create ledger from the AgentExpense template (app v{app_version()})')
    except BaseException:
        # Leave nothing half-made: remove what this run wrote.
        if created:
            shutil.rmtree(target, ignore_errors=True)
        else:
            for child in target.iterdir():
                shutil.rmtree(child) if child.is_dir() and not child.is_symlink() else child.unlink()
        raise
    return dict(settings, path=str(target), vcs=vcs)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--dir', required=True, type=Path, help='new ledger folder')
    parser.add_argument('--name', required=True, help="the ledger owner's name, shown in the dashboard")
    parser.add_argument('--currency', required=True, help='main currency, an ISO 4217 code such as USD')
    parser.add_argument('--locale', help='optional number and date locale such as en-US; default: the browser locale')
    parser.add_argument('--vcs', help='jj (default when installed) or git')
    args = parser.parse_args(argv)
    try:
        result = create(args.dir, args.name, args.currency.strip().upper(), args.locale or None, args.vcs or None)
    except SetupError as exc:
        raise SystemExit(str(exc))
    print(f'Created the ledger "{result["name"]}" ({result["defaultCurrency"]}) at {result["path"]}, '
          f'with a first {result["vcs"]} commit.')
    if result['vcs'] == 'git':
        print('Dashboard saves and make commit use Jujutsu: before the first save, run '
              f'jj git init --colocate "{result["path"]}" (it keeps the git history).')
    print(f'Next: export LEDGER_DIR="{result["path"]}" and continue with SETUP.md.')


if __name__ == '__main__':
    main()
