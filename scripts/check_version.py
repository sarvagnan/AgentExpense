"""One application version, followed by the package files, and a Node pin within engines.

Standard library only, so `make doctor` can use it before dependencies exist.
"""
from __future__ import annotations
import json
from pathlib import Path
import re
import sys

APPLICATION = Path(__file__).resolve().parents[1]
SEMVER = re.compile(r'(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)(?:-([0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?')
SERVER_PACKAGE = 'expense-dashboard-server'


def read_version(application=APPLICATION):
    value = (Path(application) / 'VERSION').read_text().strip()
    if not SEMVER.fullmatch(value):
        raise ValueError(f'VERSION is not a semantic version: {value!r}')
    return value


def parse_version(text):
    """Release numbers of a full or partial version (missing parts are zero)."""
    match = re.fullmatch(r'v?(\d+)(?:\.(\d+))?(?:\.(\d+))?(?:-[0-9A-Za-z.-]+)?', text.strip())
    if not match:
        raise ValueError(f'Not a version: {text!r}')
    return tuple(int(part or 0) for part in match.groups())


def satisfies(version, expression):
    """The subset of npm ranges that engines fields use: comparators, ^, ~, || and exact versions."""
    target = parse_version(version)
    alternatives = [part.split() for part in expression.split('||')]
    if not any(alternatives):
        raise ValueError('Empty version range')
    def matches(comparator):
        match = re.fullmatch(r'(>=|<=|>|<|=|\^|~)?(v?\d+(?:\.\d+){0,2}(?:-[0-9A-Za-z.-]+)?)', comparator)
        if not match:
            raise ValueError(f'Unsupported version range: {expression!r}')
        operator, value = match.group(1) or '=', match.group(2)
        bound = parse_version(value)
        if operator == '^':
            index = next((i for i, part in enumerate(bound) if part), 2)
            upper = tuple(part + 1 if i == index else (part if i < index else 0) for i, part in enumerate(bound))
            return bound <= target < upper
        if operator == '~':
            upper = (bound[0], bound[1] + 1, 0) if '.' in value else (bound[0] + 1, 0, 0)
            return bound <= target < upper
        return {'>=': target >= bound, '<=': target <= bound, '>': target > bound,
                '<': target < bound, '=': target == bound}[operator]
    return any(alternative and all(matches(item) for item in alternative) for alternative in alternatives)


def _toml_version(text, *, table=None, package=None):
    """Read one version without tomllib, which Python 3.10 and older lack."""
    try:
        import tomllib
    except ImportError:
        tomllib = None
    if tomllib:
        document = tomllib.loads(text)
        if table:
            return document.get(table, {}).get('version')
        return next((item.get('version') for item in document.get('package', []) if item.get('name') == package), None)
    blocks = re.split(r'(?m)^\[', text)
    for block in blocks:
        header, _, body = block.partition('\n')
        if table and header.strip() == table + ']' or package and header.strip() == '[package]]' and re.search(r'(?m)^name = "' + re.escape(package) + '"$', body):
            found = re.search(r'(?m)^version = "([^"]+)"$', body)
            return found.group(1) if found else None
    return None


def problems(application=APPLICATION):
    """Every disagreement, as short sentences naming the file to change."""
    application = Path(application)
    found = []
    try:
        version = read_version(application)
    except (OSError, ValueError) as exc:
        return [f'VERSION: {exc}']
    def expect(label, value):
        if value != version:
            found.append(f'{label} has version {value!r}; VERSION has {version!r}.')
    dashboard = application / 'review-dashboard'
    package = json.loads((dashboard / 'package.json').read_text())
    expect('review-dashboard/package.json', package.get('version'))
    lock = json.loads((dashboard / 'package-lock.json').read_text())
    expect('review-dashboard/package-lock.json', lock.get('version'))
    expect('review-dashboard/package-lock.json (root package)', lock.get('packages', {}).get('', {}).get('version'))
    server = application / 'review-dashboard/server'
    expect('review-dashboard/server/pyproject.toml', _toml_version((server / 'pyproject.toml').read_text(), table='project'))
    expect('review-dashboard/server/uv.lock', _toml_version((server / 'uv.lock').read_text(), package=SERVER_PACKAGE))
    found.extend(node_pin_problems(application, package))
    return found


def node_pin_problems(application=APPLICATION, package=None):
    application = Path(application)
    if package is None:
        package = json.loads((application / 'review-dashboard/package.json').read_text())
    try:
        pin = (application / '.node-version').read_text().strip()
        parse_version(pin)
    except (OSError, ValueError) as exc:
        return [f'.node-version: {exc}']
    engines = package.get('engines', {}).get('node')
    if not engines:
        return ['review-dashboard/package.json has no engines.node range.']
    try:
        if not satisfies(pin, engines):
            return [f'.node-version {pin} is outside review-dashboard/package.json engines.node {engines!r}.']
    except ValueError as exc:
        return [f'review-dashboard/package.json engines.node: {exc}']
    return []


def main():
    found = problems()
    if found:
        print('Version check failed:', *found, sep='\n  ', file=sys.stderr)
        return 1
    print(f'Versions agree: {read_version()}; Node pin {(APPLICATION / ".node-version").read_text().strip()} is within engines.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
