"""Check this machine's prerequisites offline and print a short table with fixes.

Standard library only, and runnable by an older Python, so a fresh clone can run it
before anything is installed. It never contacts the network, installs, signs in or
runs an agent CLI. Exit status 1 means a blocking problem; warnings exit 0.
"""
from __future__ import annotations
import ast
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys

APPLICATION = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APPLICATION / 'scripts'))
import check_version  # noqa: E402
import dependencies  # noqa: E402

# The oldest hledger whose behaviour the app relies on; docs/VERSIONS.md says why.
HLEDGER_MINIMUM = (1, 25)
OK, INFO, WARN, FAIL = 'ok', 'info', 'warn', 'FAIL'
OFFLINE = {'npm_config_update_notifier': 'false', 'npm_config_offline': 'true', 'UV_OFFLINE': '1'}


class Row:
    def __init__(self, check, status, detail, fix=''):
        self.check, self.status, self.detail, self.fix = check, status, detail, fix


def run(command, environ, cwd=None):
    """Output of a local command, or None when it is missing or fails."""
    try:
        result = subprocess.run(command, env={**environ, **OFFLINE}, cwd=cwd, capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return None
    return result.stdout.strip() if result.returncode == 0 else None


def version_in(text):
    match = re.search(r'(\d+)\.(\d+)(?:\.(\d+))?', text or '')
    return tuple(int(part or 0) for part in match.groups()) if match else None


def dotted(numbers):
    return '.'.join(map(str, numbers))


def check_python(application, python_version):
    text = (application / 'review-dashboard/server/pyproject.toml').read_text()
    match = re.search(r'(?m)^requires-python\s*=\s*">=\s*(\d+)\.(\d+)', text)
    minimum = (int(match.group(1)), int(match.group(2))) if match else (3, 11)
    current = dotted(python_version[:3])
    if tuple(python_version[:2]) < minimum:
        return Row('Python', FAIL, f'python3 is {current}; the app needs {dotted(minimum)} or newer', f'Install Python {dotted(minimum)}+ as python3.')
    return Row('Python', OK, f'{current} (needs {dotted(minimum)}+)')


def check_node(application, environ):
    """fnm with the pinned version, or Node on PATH; returns rows and Node's bin folder."""
    pin = (application / '.node-version').read_text().strip()
    rows = []
    fnm = shutil.which('fnm', path=environ.get('PATH'))
    folder = None
    if fnm:
        fnm_version = version_in(run([fnm, '--version'], environ))
        rows.append(Row('fnm', OK, dotted(fnm_version) if fnm_version else 'present'))
        installed = run([fnm, 'list'], environ) or ''
        if re.search(r'(?m)\bv' + re.escape(pin) + r'\b', installed):
            found = run([fnm, 'exec', '--using=' + str(application / '.node-version'), '--', 'node', '-p', 'process.execPath'], environ)
            folder = str(Path(found).parent) if found else None
        if folder:
            rows.append(Row('Node', OK, f'{pin} (.node-version, through fnm)'))
        else:
            rows.append(Row('Node', FAIL, f'{pin} from .node-version is not installed in fnm', f'fnm install {pin}'))
    else:
        rows.append(Row('fnm', WARN, 'not found; launchers use Node on PATH', 'Install fnm, then fnm install ' + pin))
        node = shutil.which('node', path=environ.get('PATH'))
        current = run([node, '--version'], environ) if node else None
        if not current:
            rows.append(Row('Node', FAIL, 'no Node on PATH', f'Install fnm, then fnm install {pin}'))
        else:
            folder = str(Path(node).parent)
            same = current.lstrip('v') == pin
            rows.append(Row('Node', OK if same else WARN, f'{current.lstrip("v")} on PATH' + ('' if same else f'; .node-version pins {pin}'),
                            '' if same else f'Install fnm, then fnm install {pin}'))
    pin_problems = check_version.node_pin_problems(application)
    rows.append(Row('Node pin', WARN, pin_problems[0], 'Make .node-version and engines agree; run make check-version.') if pin_problems
                else Row('Node pin', OK, f'{pin} is within package.json engines'))
    npm = shutil.which('npm', path=folder) if folder else None
    npm_version = run([npm, '--version'], {**environ, 'PATH': folder + os.pathsep + environ.get('PATH', '')}) if npm else None
    rows.append(Row('npm', OK, npm_version) if npm_version else
                Row('npm', FAIL, 'npm not found beside the selected Node' if folder else 'needs Node first', 'Reinstall that Node version with fnm.'))
    return rows


def check_tools(environ):
    rows = []
    uv = shutil.which('uv', path=environ.get('PATH'))
    uv_version = run([uv, '--version'], environ) if uv else None
    rows.append(Row('uv', OK, dotted(version_in(uv_version))) if version_in(uv_version) else
                Row('uv', FAIL, 'not found', 'Install uv (https://docs.astral.sh/uv/).'))
    hledger = environ.get('HLEDGER_BIN') or environ.get('HLEDGER') or shutil.which('hledger', path=environ.get('PATH'))
    found = run([hledger, '--version'], environ) if hledger else None
    number = version_in(found)
    minimum = dotted(HLEDGER_MINIMUM)
    if not number:
        rows.append(Row('hledger', FAIL, 'not found', f'Install hledger {minimum} or newer on PATH.'))
    elif number[:2] < HLEDGER_MINIMUM:
        rows.append(Row('hledger', FAIL, f'{dotted(number)} is older than {minimum}', f'Install hledger {minimum} or newer.'))
    else:
        rows.append(Row('hledger', OK, f'{dotted(number)} (needs {minimum}+)'))
    versions = {}
    for name in ('jj', 'git'):
        path = shutil.which(name, path=environ.get('PATH'))
        text = run([path, '--version'], environ) if path else None
        if text and version_in(text):
            versions[name] = dotted(version_in(text))
    detail = ', '.join(f'{name} {value}' for name, value in versions.items())
    rows.append(Row('jj / git', OK, detail) if versions else
                Row('jj / git', FAIL, 'neither found', 'Install Jujutsu (jj) or git; ledgers are version-controlled.'))
    return rows


def check_ledger(application, environ):
    default = application / 'ledger'
    configured = environ.get('LEDGER_DIR')
    root = Path(configured).expanduser().resolve() if configured else default.resolve()
    if root == default.resolve() and not default.exists():
        return Row('Ledger', INFO, 'no ledger selected (tests use fixtures/ledgers/asha)',
                   'Set LEDGER_DIR to your ledger, or keep one at ledger/.')
    if not root.is_dir():
        return Row('Ledger', FAIL, f'LEDGER_DIR {root} is not a folder', 'Point LEDGER_DIR at an existing ledger.')
    try:
        from ledger_manifest import CURRENT_FORMAT
        manifest = root / 'ledger.json'
        value = json.loads(manifest.read_text()).get('format') if manifest.exists() else 0
        if type(value) is not int or value < 0:
            raise ValueError('invalid format')
    except (OSError, ValueError, AttributeError) as exc:
        return Row('Ledger', FAIL, f'ledger.json is unreadable ({exc.__class__.__name__})', 'Repair ledger.json from the ledger history.')
    repository = 'jj' if (root / '.jj').is_dir() else 'git' if (root / '.git').exists() else None
    where = f'{root}' + (f', {repository} repository' if repository else '')
    if value < CURRENT_FORMAT:
        return Row('Ledger', WARN, f'format {value} at {where}; this app reads format {CURRENT_FORMAT}', 'make upgrade-ledger ARGS="--dry-run", then make upgrade-ledger')
    if value > CURRENT_FORMAT:
        return Row('Ledger', WARN, f'format {value} at {where}; this app reads format {CURRENT_FORMAT}', 'Update AgentExpense to a version that reads this format.')
    if not repository:
        return Row('Ledger', WARN, f'format {value} at {root}; not a jj or git repository', 'Saves commit to the ledger: initialise it with jj git init --colocate.')
    return Row('Ledger', OK, f'format {value} at {where}')


def check_npm_packages(application):
    different = dependencies.npm_differences(application)
    fix = 'npm --prefix review-dashboard ci'
    if different is None:
        return Row('npm packages', FAIL, 'not installed', fix)
    if different:
        return Row('npm packages', FAIL, f'{len(different)} differ from package-lock.json', fix)
    return Row('npm packages', OK, 'match package-lock.json')


def check_python_packages(application, environ):
    uv = shutil.which('uv', path=environ.get('PATH'))
    fix = 'uv sync --locked --directory review-dashboard/server'
    if not uv:
        return Row('Python packages', FAIL, 'needs uv first', 'Install uv, then ' + fix)
    if not (application / 'review-dashboard/server/.venv').is_dir():
        return Row('Python packages', FAIL, 'not installed', fix)
    if run([uv, 'sync', '--locked', '--check', '--offline', '--directory', str(application / 'review-dashboard/server')], environ) is None:
        return Row('Python packages', FAIL, 'differ from uv.lock', fix)
    return Row('Python packages', OK, 'match uv.lock')


def adapter_packages(application):
    """The pinned CLI adapters, read from the server source without importing it."""
    tree = ast.parse((application / 'review-dashboard/server/adapters/__init__.py').read_text())
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == 'PACKAGES' for t in node.targets):
            return ast.literal_eval(node.value)
    return {}


def check_adapter(application, environ):
    local = application / 'local.json'
    try:
        agent = json.loads(local.read_text()).get('agent', {}) if local.exists() else {}
        if not isinstance(agent, dict):
            raise ValueError('agent must be an object')
    except (ValueError, AttributeError) as exc:
        return Row('Agent adapter', WARN, f'local.json is invalid ({exc.__class__.__name__})', 'Fix local.json; see review-dashboard/server/README.md.')
    adapter = environ.get('EXPENSES_AGENT') or environ.get('ADAPTER') or agent.get('adapter') or 'codex'
    if agent.get('module'):
        raw = str(agent['module'])
        if raw.startswith('ledger:'):
            return Row('Agent adapter', INFO, f'{adapter}: custom module in the ledger', '')
        path = Path(raw).expanduser()
        path = path if path.is_absolute() else application / path
        return Row('Agent adapter', OK, f'{adapter}: custom module present') if path.is_file() else \
            Row('Agent adapter', WARN, f'{adapter}: custom module {raw} not found', 'Fix agent.module in local.json.')
    packages = adapter_packages(application)
    if adapter not in packages:
        if adapter == 'anthropic':
            return Row('Agent adapter', INFO, 'anthropic: direct API, no CLI to check')
        return Row('Agent adapter', WARN, f'{adapter}: unknown adapter', 'Choose an adapter listed in docs/ADAPTERS.md.')
    package, binary = packages[adapter]
    dashboard = application / 'review-dashboard'
    pinned = json.loads((dashboard / 'package.json').read_text()).get('dependencies', {}).get(package)
    try:
        installed = json.loads((dashboard / 'node_modules' / package / 'package.json').read_text()).get('version')
    except (OSError, ValueError):
        installed = None
    if installed != pinned or not (dashboard / 'node_modules/.bin' / binary).exists():
        found = f'{installed} installed' if installed else 'not installed'
        return Row('Agent adapter', WARN, f'{adapter}: pinned CLI {pinned}, {found}', 'npm --prefix review-dashboard ci')
    return Row('Agent adapter', OK, f'{adapter}: pinned CLI {pinned} present (sign-in not checked)')


def check_app(application):
    try:
        version = check_version.read_version(application)
    except (OSError, ValueError) as exc:
        return Row('AgentExpense', FAIL, str(exc), 'Restore VERSION from the release.')
    problems = [problem for problem in check_version.problems(application) if not problem.startswith('.node-version')]
    if problems:
        return Row('AgentExpense', WARN, f'{version}; {len(problems)} version files disagree', 'make check-version')
    return Row('AgentExpense', OK, version)


def diagnose(application=APPLICATION, environ=None, python_version=None):
    application = Path(application)
    environ = dict(os.environ if environ is None else environ)
    python_version = tuple(sys.version_info[:3] if python_version is None else python_version)
    return [check_app(application), check_python(application, python_version), *check_node(application, environ),
            *check_tools(environ), check_ledger(application, environ), check_npm_packages(application),
            check_python_packages(application, environ), check_adapter(application, environ)]


def render(rows):
    width = max(len(row.check) for row in rows)
    lines = [f'{"Check".ljust(width)}  Status  Details']
    for row in rows:
        lines.append(f'{row.check.ljust(width)}  {row.status.ljust(6)}  {row.detail}')
        if row.fix and row.status != OK:
            lines.append(f'{"".ljust(width)}          {"note" if row.status == INFO else "fix"}: {row.fix}')
    blocking = sum(row.status == FAIL for row in rows)
    warnings = sum(row.status == WARN for row in rows)
    lines.append(f'{blocking} blocking, {warnings} warnings.' if blocking or warnings else 'Ready.')
    return '\n'.join(lines)


def main():
    rows = diagnose()
    print(render(rows))
    return 1 if any(row.status == FAIL for row in rows) else 0


if __name__ == '__main__':
    sys.exit(main())
