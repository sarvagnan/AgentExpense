"""Scan public files against a private label<TAB>value list, without printing values.

Standard library only. This is an exact identifier scanner, not secret detection or
OCR. Review reported binaries manually. Ignored directories and symlinks are not
followed. Keep the private list outside the repository; it is excluded from scans.
"""
from __future__ import annotations

import argparse
from collections import deque
import fnmatch
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
SKIP = {'.git', '.jj', 'ledger', 'node_modules', 'dist', '__pycache__', '.venv',
        '.uv-cache', 'tmp', 'prototypes'}
TOKEN = re.compile(r'[\w-]+')


class Matcher:
    """Token hash lookups and Aho-Corasick substring search: one pass per line."""
    def __init__(self, entries):
        self.tokens = {}
        self.edges, self.fail, self.out = [{}], [0], [set()]
        for label, value in entries:
            folded = value.casefold()
            if all(c.isalnum() or c in '_-' for c in value):
                self.tokens.setdefault(folded, set()).add(label)
                continue
            state = 0
            for char in folded:
                if char not in self.edges[state]:
                    self.edges[state][char] = len(self.edges)
                    self.edges.append({}); self.fail.append(0); self.out.append(set())
                state = self.edges[state][char]
            self.out[state].add(label)
        queue = deque(self.edges[0].values())
        while queue:
            state = queue.popleft()
            for char, child in self.edges[state].items():
                queue.append(child)
                fallback = self.fail[state]
                while fallback and char not in self.edges[fallback]:
                    fallback = self.fail[fallback]
                self.fail[child] = self.edges[fallback].get(char, 0)
                self.out[child].update(self.out[self.fail[child]])

    def labels(self, line):
        line = line.casefold()
        labels = set()
        for token in TOKEN.findall(line):
            labels.update(self.tokens.get(token, ()))
        state = 0
        if len(self.edges) > 1:
            for char in line:
                while state and char not in self.edges[state]:
                    state = self.fail[state]
                state = self.edges[state].get(char, 0)
                labels.update(self.out[state])
        return sorted(labels)


def read_list(path):
    entries = []
    for number, line in enumerate(path.read_text(encoding='utf-8').splitlines(), 1):
        if not line.strip() or line.startswith('#'):
            continue
        fields = line.split('\t', 1)
        if len(fields) != 2 or not fields[0].strip() or not fields[1]:
            raise ValueError(f'Invalid list entry on line {number}; expected label<TAB>value')
        entries.append((fields[0].strip(), fields[1]))
    return entries


def ignored(path, root):
    """Honor ancestor .gitignore files without invoking version control.

    Supports directory patterns, wildcards, anchored paths and negation within
    visited directories. Like git, ignored parents are not traversed for negation.
    """
    try:
        rel = path.relative_to(root)
    except ValueError:
        root = path.parent
        rel = Path(path.name)
    if any(part in SKIP for part in rel.parts) or path.name.startswith('.env'):
        return True
    if rel.as_posix().startswith(('review-dashboard/app/data/', 'review-dashboard/public/data/')):
        return True
    result = False
    for base in [root, *[root.joinpath(*rel.parts[:i]) for i in range(1, len(rel.parts))]]:
        config = base / '.gitignore'
        if not config.is_file() or config.is_symlink():
            continue
        local = path.relative_to(base).as_posix()
        for raw in config.read_text(encoding='utf-8').splitlines():
            pattern = raw.strip()
            if not pattern or pattern.startswith('#'):
                continue
            negate = pattern.startswith('!')
            if negate:
                pattern = pattern[1:]
            directory = pattern.endswith('/')
            pattern = pattern.rstrip('/')
            anchored = pattern.startswith('/')
            pattern = pattern.lstrip('/')
            candidates = [local] if anchored or '/' in pattern else local.split('/')
            if directory and not path.is_dir():
                candidates = candidates[:-1] if not anchored and '/' not in pattern else []
            if any(fnmatch.fnmatchcase(candidate, pattern) for candidate in candidates):
                result = not negate
    return result


def files(paths, root, list_path):
    seen = set()
    def visit(path):
        if path.is_symlink() or path.resolve() == list_path.resolve() or ignored(path, root):
            return
        if path.is_dir():
            for child in sorted(path.iterdir()):
                yield from visit(child)
        elif path.is_file() and path.resolve() not in seen:
            seen.add(path.resolve())
            yield path
        elif not path.exists():
            raise ValueError('A scan path does not exist')
    for path in paths:
        if not path.is_symlink():
            yield from visit(path.resolve())


def scan(list_path, paths, *, root=ROOT, output=sys.stdout, errors=sys.stderr):
    root = root.resolve()
    matcher = Matcher(read_list(list_path))
    hits = 0
    for path in files(paths, root, list_path):
        try:
            name = path.relative_to(root).as_posix()
        except ValueError:
            name = str(path)
        data = path.read_bytes()
        try:
            if b'\0' in data or any(c < 9 or 13 < c < 32 for c in data):
                raise UnicodeError()
            text = data.decode('utf-8')
        except UnicodeError:
            print(f'{name}: binary (manual review required)', file=errors)
            continue
        for number, line in enumerate(text.splitlines(), 1):
            for label in matcher.labels(line):
                print(f'{name}:{number}: {label}', file=output)
                hits += 1
    return 1 if hits else 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--list', type=Path, required=True, dest='list_path')
    parser.add_argument('paths', type=Path, nargs='*')
    args = parser.parse_args()
    try:
        return scan(args.list_path, args.paths or [ROOT])
    except (OSError, UnicodeError, ValueError):
        # Exception strings may contain a private filename or list contents.
        print('Scan failed: check list format, readable files and scan paths.', file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
