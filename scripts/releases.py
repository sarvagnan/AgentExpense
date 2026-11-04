"""Release tags, version order and changelog sections, shared by make update and the server.

Standard library only. A release tag is `vMAJOR.MINOR.PATCH`, optionally with a
semantic-versioning pre-release suffix (`v0.3.0-rc.1`). Pre-releases sort before
their release and are ignored unless asked for.
"""
from __future__ import annotations
import re

_IDENTIFIER = r'(?:0|[1-9]\d*|\d*[A-Za-z-][0-9A-Za-z-]*)'
TAG = re.compile(r'^v?(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)(?:-(' + _IDENTIFIER + r'(?:\.' + _IDENTIFIER + r')*))?$')
HEADING = re.compile(r'^## \[([^\]]+)\]')
LINK = re.compile(r'^\[[^\]]+\]:\s')  # Link references at the end of the file


class Version:
    """A parsed semantic version; `str()` gives it without the leading v."""

    def __init__(self, text):
        match = TAG.match(text.strip())
        if not match:
            raise ValueError(f'Not a release version: {text}')
        self.major, self.minor, self.patch = (int(part) for part in match.groups()[:3])
        self.pre = tuple(match.group(4).split('.')) if match.group(4) else ()

    @property
    def prerelease(self):
        return bool(self.pre)

    def key(self):
        # A release sorts after its pre-releases; numeric identifiers sort before others.
        pre = (0, tuple((0, int(part), '') if part.isdigit() else (1, 0, part) for part in self.pre)) if self.pre else (1, ())
        return (self.major, self.minor, self.patch, pre)

    def __eq__(self, other):
        return isinstance(other, Version) and self.key() == other.key()

    def __lt__(self, other):
        return self.key() < other.key()

    def __le__(self, other):
        return self.key() <= other.key()

    def __gt__(self, other):
        return self.key() > other.key()

    def __ge__(self, other):
        return self.key() >= other.key()

    def __hash__(self):
        return hash(self.key())

    def __str__(self):
        return f'{self.major}.{self.minor}.{self.patch}' + ('-' + '.'.join(self.pre) if self.pre else '')

    @property
    def tag(self):
        return 'v' + str(self)


def parse(text):
    """The version for a release tag or version string, or None when it isn't one."""
    try:
        return Version(text)
    except ValueError:
        return None


def releases(tags, *, pre=False):
    """Release versions among tag names, newest first; tags must start with v."""
    found = {version for name in tags if name.startswith('v') and (version := parse(name))}
    return sorted((version for version in found if pre or not version.prerelease), reverse=True)


def newer(tags, current, *, pre=False):
    """Releases above `current`, newest first."""
    current = current if isinstance(current, Version) else Version(current)
    return [version for version in releases(tags, pre=pre) if version > current]


def ls_remote_tags(output):
    """Tag names from `git ls-remote --tags --refs` output."""
    names = []
    for line in output.splitlines():
        parts = line.split('\t')
        if len(parts) == 2 and parts[1].startswith('refs/tags/'):
            names.append(parts[1][len('refs/tags/'):])
    return names


def changelog_sections(text, current, target):
    """Changelog sections for versions above `current` up to `target`, as written."""
    current = current if isinstance(current, Version) else Version(current)
    target = target if isinstance(target, Version) else Version(target)
    sections, active = [], None
    for line in text.splitlines():
        heading = HEADING.match(line)
        if heading or line.startswith('## '):
            version = parse(heading.group(1)) if heading else None
            active = [] if version and current < version <= target else None
            if active is not None:
                sections.append(active)
        if active is not None and not LINK.match(line):
            active.append(line)
    return '\n\n'.join('\n'.join(section).rstrip() for section in sections)


def release_notes(text, version):
    """One version's changelog section without its heading, or None when it has none."""
    version = version if isinstance(version, Version) else Version(version)
    lines, active = [], False
    for line in text.splitlines():
        if line.startswith('## '):
            heading = HEADING.match(line)
            active = bool(heading and parse(heading.group(1)) == version)
            continue
        if active and not LINK.match(line):
            lines.append(line)
    body = '\n'.join(lines).strip()
    return body or None


def main(argv=None):
    """`releases.py notes VERSION` prints that version's notes from CHANGELOG.md (for release.yml)."""
    import sys
    from pathlib import Path
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) != 2 or argv[0] != 'notes' or not parse(argv[1]):
        sys.exit('usage: releases.py notes VERSION')
    notes = release_notes((Path(__file__).resolve().parents[1] / 'CHANGELOG.md').read_text(), argv[1])
    if not notes:
        sys.exit(f'CHANGELOG.md has no section for {argv[1]}.')
    print(notes)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
