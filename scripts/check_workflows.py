"""Check the GitHub workflows' structure offline; they only run once the repository is published.

`make check-workflows` runs this with the server environment's PyYAML. It checks what
actionlint would catch most often, without downloading anything:

- each workflow parses and has a name, triggers and jobs with runners, time limits and steps;
- each step has exactly one of `run` or `uses`, and every action is pinned to a commit SHA;
- `needs`, `matrix.*` and local actions refer to things that exist;
- every `${{ }}` expression is closed, and every `run` script passes `bash -n`;
- the project's rules: the personal-data scan uses an empty list made in the job,
  Playwright is pinned to the harness version, and nothing runs `npm audit`;
- the trigger split (owner, 2026-11-07): `ci.yml` runs the scans and the offline suites
  on every push and pull request, and nothing slower; `browsers.yml` runs the browser
  matrix in Light and `make test-update` only on `v*` tags, by hand and weekly.
"""
from __future__ import annotations
from pathlib import Path
import re
import subprocess
import sys

import yaml

APPLICATION = Path(__file__).resolve().parents[1]
EXPRESSION = re.compile(r'\$\{\{(.*?)\}\}', re.S)
PLAYWRIGHT = '1.64.1'


def load(path):
    value = yaml.safe_load(path.read_text())
    if isinstance(value, dict) and True in value and 'on' not in value:
        value['on'] = value.pop(True)  # YAML 1.1 reads a bare `on` key as true.
    return value


def strings(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from strings(item)


def check_script(script, where, problems):
    # Replace expressions with a word so bash sees the shell around them.
    text = EXPRESSION.sub('EXPR', script)
    result = subprocess.run(['bash', '-n'], input=text, capture_output=True, text=True)
    if result.returncode:
        problems.append(f'{where}: bash -n: {result.stderr.strip().splitlines()[-1] if result.stderr.strip() else "syntax error"}')


def check_steps(steps, where, problems, root, matrix=None):
    if not isinstance(steps, list) or not steps:
        problems.append(f'{where}: steps must be a non-empty list')
        return
    for index, step in enumerate(steps):
        label = f'{where} step {index + 1}'
        if not isinstance(step, dict) or ('run' in step) == ('uses' in step):
            problems.append(f'{label}: needs exactly one of run or uses')
            continue
        if 'uses' in step:
            uses = step['uses']
            if uses.startswith('./'):
                if not (root / uses / 'action.yml').is_file():
                    problems.append(f'{label}: local action {uses} has no action.yml')
            elif not re.fullmatch(r'[\w.-]+/[\w./-]+@[0-9a-f]{40}', uses):
                problems.append(f'{label}: action {uses} is not pinned to a commit SHA')
        else:
            check_script(step['run'], label, problems)
        for text in strings(step):
            if text.count('${{') != text.count('}}') and '${{' in text:
                problems.append(f'{label}: unbalanced expression')
            for expression in EXPRESSION.findall(text):
                for name in re.findall(r'\bmatrix\.([A-Za-z_][\w-]*)', expression):
                    if matrix is not None and name not in matrix:
                        problems.append(f'{label}: matrix.{name} is not in the job matrix')


def check_workflow(path, root=APPLICATION):
    problems = []
    where = path.relative_to(root)
    try:
        workflow = load(path)
    except yaml.YAMLError as exc:
        return [f'{where}: does not parse: {exc}']
    if not isinstance(workflow, dict):
        return [f'{where}: must be a mapping']
    for key in ('name', 'on', 'jobs'):
        if key not in workflow:
            problems.append(f'{where}: missing {key}')
    jobs = workflow.get('jobs') or {}
    if not isinstance(jobs, dict) or not jobs:
        return problems + [f'{where}: jobs must be a non-empty mapping']
    for name, job in jobs.items():
        label = f'{where} job {name}'
        if not isinstance(job, dict):
            problems.append(f'{label}: must be a mapping')
            continue
        for key in ('runs-on', 'timeout-minutes', 'steps'):
            if key not in job:
                problems.append(f'{label}: missing {key}')
        limit = job.get('timeout-minutes')
        if 'timeout-minutes' in job and (type(limit) is not int or not 1 <= limit <= 360):
            problems.append(f'{label}: timeout-minutes must be a whole number of minutes from 1 to 360')
        needs = job.get('needs', [])
        for need in [needs] if isinstance(needs, str) else needs:
            if need not in jobs:
                problems.append(f'{label}: needs unknown job {need}')
        matrix = (job.get('strategy') or {}).get('matrix')
        names = set(matrix) - {'include', 'exclude'} if isinstance(matrix, dict) else set()
        for text in strings({key: value for key, value in job.items() if key != 'steps'}):
            for expression in EXPRESSION.findall(text):
                for item in re.findall(r'\bmatrix\.([A-Za-z_][\w-]*)', expression):
                    if item not in names:
                        problems.append(f'{label}: matrix.{item} is not in the job matrix')
        check_steps(job.get('steps'), label, problems, root, names)
    return problems


def check_action(path, root=APPLICATION):
    where = path.relative_to(root)
    action = load(path)
    problems = [f'{where}: missing {key}' for key in ('name', 'description', 'runs') if key not in action]
    runs = action.get('runs', {})
    if runs.get('using') != 'composite':
        problems.append(f'{where}: only composite actions are expected')
    for index, step in enumerate(runs.get('steps', [])):
        if 'run' in step and 'shell' not in step:
            problems.append(f'{where} step {index + 1}: composite run steps need a shell')
    check_steps(runs.get('steps'), str(where), problems, root)
    return problems


def runs(job):
    """Every run script of a job, joined."""
    return '\n'.join(step.get('run', '') for step in (job.get('steps') or []) if isinstance(step, dict))


def triggers(workflow):
    value = workflow.get('on') if isinstance(workflow, dict) else None
    if isinstance(value, str):
        return {value: None}
    if isinstance(value, list):
        return {name: None for name in value}
    return value if isinstance(value, dict) else {}


def split_rules(root):
    """Pushes and pull requests get the scans and offline suites; the slow runs wait."""
    problems = []
    folder = root / '.github/workflows'
    try:
        ci, browsers = (load(folder / name) if (folder / name).is_file() else None for name in ('ci.yml', 'browsers.yml'))
    except yaml.YAMLError:
        return problems  # check_workflow already reports it.
    if not isinstance(ci, dict):
        return problems + ['ci.yml: missing; it runs the scans and offline suites on every push and pull request']
    if not isinstance(browsers, dict):
        return problems + ['browsers.yml: missing; it runs the browser matrix and make test-update']
    on = triggers(ci)
    if set(on) != {'push', 'pull_request'}:
        problems.append(f'ci.yml: must run on push and pull_request only, not {sorted(on)}')
    for event in ('push', 'pull_request'):
        if isinstance(on.get(event), dict) and set(on[event]) & {'branches', 'branches-ignore', 'tags', 'tags-ignore', 'paths', 'paths-ignore'}:
            problems.append(f'ci.yml: {event} must not be filtered; it runs on every {event.replace("_", " ")}')
    ci_jobs = ci.get('jobs') if isinstance(ci.get('jobs'), dict) else {}
    ci_text = '\n'.join(runs(job) for job in ci_jobs.values() if isinstance(job, dict))
    for needed, what in (('scripts/check_private.py', 'the personal-data scan'), ('gitleaks', 'the secret scan'),
                         ('make dashboard', 'the offline build'), ('make test-server', 'the server tests'),
                         ('npm --prefix review-dashboard test', 'the dashboard tests'), ('make test-scanner', 'the scanner and tooling tests')):
        if needed not in ci_text:
            problems.append(f'ci.yml: {what} ({needed}) must run on every push')
    offline = [job for job in ci_jobs.values() if isinstance(job, dict) and 'make test-server' in runs(job)]
    ledgers = root / 'fixtures/ledgers' if (root / 'fixtures/ledgers').is_dir() else APPLICATION / 'fixtures/ledgers'
    personas = sorted(path.name for path in ledgers.iterdir() if path.is_dir())
    for job in offline:
        matrix = ((job.get('strategy') or {}).get('matrix') or {}).get('persona')
        if personas and sorted(matrix or []) != personas:
            problems.append(f'ci.yml: the offline suites must run for every persona {personas}, not {matrix}')
    for slow, what in (('make browser-tests', 'the browser matrix'), ('make test-update', 'the update test')):
        if slow in ci_text:
            problems.append(f'ci.yml: {what} ({slow}) belongs in browsers.yml, not on every push')
    on = triggers(browsers)
    if set(on) != {'push', 'workflow_dispatch', 'schedule'}:
        problems.append(f'browsers.yml: must run on v* tags, workflow_dispatch and a schedule only, not {sorted(on)}')
    push = on.get('push')
    if 'push' in on and (not isinstance(push, dict) or set(push) != {'tags'} or push.get('tags') != ['v*']):
        problems.append('browsers.yml: push must be limited to v* tags')
    schedule = on.get('schedule')
    if 'schedule' in on and not (isinstance(schedule, list) and schedule and all(isinstance(item, dict) and isinstance(item.get('cron'), str) and len(item['cron'].split()) == 5 for item in schedule)):
        problems.append('browsers.yml: schedule needs a five-field cron')
    browser_jobs = browsers.get('jobs') if isinstance(browsers.get('jobs'), dict) else {}
    browser_text = '\n'.join(runs(job) for job in browser_jobs.values() if isinstance(job, dict))
    if 'make test-update' not in browser_text:
        problems.append('browsers.yml: make test-update is missing')
    suites = [job for job in browser_jobs.values() if isinstance(job, dict) and 'make browser-tests' in runs(job)]
    if not suites:
        problems.append('browsers.yml: make browser-tests is missing')
    for job in suites:
        engines = ((job.get('strategy') or {}).get('matrix') or {}).get('engine') or []
        for engine in ('chromium', 'firefox', 'webkit'):
            if engine not in engines:
                problems.append(f'browsers.yml: browser engine {engine} is missing')
        for line in runs(job).splitlines():
            if 'make browser-tests' in line and 'THEMES=light' not in line.split('make browser-tests')[0]:
                problems.append('browsers.yml: CI browser runs use THEMES=light; Dark is for the grading round')
    if 'BROWSER_PROBE_REQUIRED=1' not in browser_text:
        problems.append('browsers.yml: the browser cleanup probe must run (BROWSER_PROBE_REQUIRED=1) where Playwright is installed')
    return problems


def project_rules(root=APPLICATION):
    problems = []
    texts = {path.relative_to(root): path.read_text() for path in sorted((root / '.github').rglob('*.yml'))}
    for path, text in texts.items():
        if re.search(r'\bnpm\s+audit\b', text):
            problems.append(f'{path}: runs npm audit')
    ci = texts.get(Path('.github/workflows/ci.yml'), '')
    browsers = texts.get(Path('.github/workflows/browsers.yml'), '')
    if not re.search(r': > "\$list"\n\s*python3 scripts/check_private\.py --list "\$list"', ci):
        problems.append('ci.yml: the personal-data scan must use an empty list made in the job')
    if f'PLAYWRIGHT_VERSION: "{PLAYWRIGHT}"' not in browsers:
        problems.append(f'browsers.yml: Playwright must be pinned to {PLAYWRIGHT}')
    if not re.search(r'GITLEAKS_VERSION: "\d+\.\d+\.\d+"', ci):
        problems.append('ci.yml: the secret scanner must be pinned')
    return problems + split_rules(root)


def check(root=APPLICATION):
    root = Path(root)
    problems = []
    workflows = sorted((root / '.github/workflows').glob('*.yml'))
    if not workflows:
        return ['.github/workflows has no workflows']
    for path in workflows:
        problems += check_workflow(path, root)
    for path in sorted((root / '.github/actions').glob('*/action.yml')):
        problems += check_action(path, root)
    return problems + project_rules(root)


def main():
    problems = check()
    for problem in problems:
        print(problem)
    if problems:
        return 1
    print(f'Workflows look valid: {len(list((APPLICATION / ".github/workflows").glob("*.yml")))} workflows checked offline.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
