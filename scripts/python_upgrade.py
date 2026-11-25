"""Select and rewrite the toolchain pins for the scheduled upgrade job."""
from __future__ import annotations

import argparse
import ast
import json
from pathlib import Path
import re
import sys
import tomllib

ROOT = Path(__file__).resolve().parents[1]
SERVER = Path('review-dashboard/server')
BOX = Path('tools/browser-box/browser_box.py')
VERSION = r'[0-9]+\.[0-9]+\.[0-9]+'


def latest_python(downloads):
    versions = [row['version'] for row in downloads
                if row['implementation'] == 'cpython' and row['variant'] == 'default'
                and re.fullmatch(VERSION, row['version'])]
    if not versions:
        raise ValueError('uv lists no stable CPython downloads for this runner')
    return max(versions, key=lambda value: tuple(map(int, value.split('.'))))


def box_pins(root):
    match = re.search(r'(?m)^PINS = (\{[^}]+\})', (root / BOX).read_text())
    if not match:
        raise ValueError(f'{BOX}: cannot read PINS')
    return ast.literal_eval(match[1])


def write_pins(root, uv, digest, python):
    if not re.fullmatch(VERSION, uv) or not re.fullmatch(VERSION, python):
        raise ValueError('uv and Python each need a stable major.minor.patch version')
    if not re.fullmatch(r'[0-9a-f]{64}', digest):
        raise ValueError('uv needs a published SHA-256 digest')
    minor = python.rsplit('.', 1)[0]
    changes = {
        SERVER / '.python-version': [(r'(\A)[0-9]+\.[0-9]+(?=\n?\Z)', minor)],
        SERVER / 'pyproject.toml': [(r'^(requires-python = ">=)[0-9]+\.[0-9]+(?=")', minor)],
        Path('.github/actions/setup/action.yml'): [
            (r'^(  uv-version:\n    description:[^\n]+\n    default: ")[^"\n]+', uv),
        ],
        BOX: [(rf'(^PINS = \{{[^}}]*?"{key}":\s*")[^"\n]+', value)
              for key, value in {'uv': uv, 'uv_sha256': digest, 'python': python}.items()],
    }
    rewritten = {}
    for path, replacements in changes.items():
        original = content = (root / path).read_text()
        for pattern, value in replacements:
            content, count = re.subn(pattern, lambda match: match[1] + value, content, flags=re.M)
            if count != 1:
                raise ValueError(f'{path}: expected one pin matching {pattern}, found {count}')
        if content != original:
            rewritten[path] = content
    for path, content in rewritten.items():
        (root / path).write_text(content)


def lock_changes(before, after):
    def versions(path):
        packages = tomllib.loads(path.read_text())['package']
        return {f"{package['name']}=={package['version']}" for package in packages}

    old, new = versions(before), versions(after)
    return ([f'- Removed `{package}`' for package in sorted(old - new)]
            + [f'- Added `{package}`' for package in sorted(new - old)])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    commands.add_parser('pins')
    commands.add_parser('latest-python')
    write = commands.add_parser('write-pins')
    for argument in ('uv', 'digest', 'python'):
        write.add_argument(argument)
    compare = commands.add_parser('lock-changes')
    compare.add_argument('before', type=Path)
    compare.add_argument('after', type=Path)
    args = parser.parse_args()
    if args.command == 'pins':
        pins = box_pins(ROOT)
        print(pins['uv'], pins['uv_sha256'], pins['python'])
    elif args.command == 'latest-python':
        print(latest_python(json.load(sys.stdin)))
    elif args.command == 'write-pins':
        write_pins(ROOT, args.uv, args.digest, args.python)
    elif args.command == 'lock-changes':
        print(*(lock_changes(args.before, args.after) or ['No locked package versions changed.']), sep='\n')


if __name__ == '__main__':
    main()
