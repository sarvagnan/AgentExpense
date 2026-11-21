"""Opt-in old/new prompt comparison through the configured application's agent."""
import argparse
from copy import deepcopy
from dataclasses import replace
import json
from pathlib import Path
import subprocess
import sys
import tempfile
from types import ModuleType
from unittest.mock import patch

APPLICATION = Path(__file__).resolve().parents[2]
SERVER = APPLICATION / 'review-dashboard/server'
sys.path[:0] = [str(SERVER), str(SERVER / 'tests'), str(APPLICATION / 'scripts/ledger')]

import agent
from answers import AnswerError, check
from prompt_cases import cases
from settings import Settings


def baseline_prompt(ref, work):
    commit = subprocess.check_output(
        ['git', 'rev-parse', '--verify', '--end-of-options', ref + '^{commit}'],
        cwd=APPLICATION, text=True).strip()

    def read(path):
        return subprocess.check_output(['git', 'show', f'{commit}:{path}'], cwd=APPLICATION, text=True)

    policy = work / 'policy/BOOKKEEPING.md'
    policy.parent.mkdir(parents=True)
    policy.write_text(read('policy/BOOKKEEPING.md'))
    old_policy = ModuleType('bookkeeping_policy')
    exec(compile(read('review-dashboard/server/bookkeeping_policy.py'), f'{commit}:bookkeeping_policy.py', 'exec'),
         vars(old_policy))
    old_statement = ModuleType('statement_reading')
    old_statement.__file__ = str(SERVER / 'statement_reading.py')
    exec(compile(read('review-dashboard/server/statement_reading.py'), f'{commit}:statement_reading.py', 'exec'),
         vars(old_statement))
    old_agent = ModuleType('agent_baseline')
    with patch.dict(sys.modules, bookkeeping_policy=old_policy):
        exec(compile(read('review-dashboard/server/agent.py'), f'{commit}:agent.py', 'exec'), vars(old_agent))

    def build(prompt_settings, draft, folder, destination, **kwargs):
        with patch.dict(sys.modules, statement_reading=old_statement):
            return old_agent.prompt_for(replace(prompt_settings, application=work), draft, folder, destination, **kwargs)

    return commit, build


def compare(build, settings, draft, folder):
    def prompt(*args, **kwargs):
        nonlocal words
        text, images = build(*args, **kwargs)
        words = len(text.split())
        return text, images

    words = None
    with patch.object(agent, 'prompt_for', prompt):
        try:
            answer, record = agent.run(settings, deepcopy(draft), folder)
        except agent.AgentFailure as error:
            return {'words': words, 'error': str(error), 'kind': error.kind}
    result = {'words': words, 'answer': answer, 'usage': record['usage']}
    if not any(draft.get(key) for key in ('receipt_task', 'statement_task', 'statement_reading')):
        try:
            check(answer, settings.ledger, form=False)
        except AnswerError as error:
            result['validation_error'] = str(error)
    return result


def cell(result):
    return json.dumps(result, ensure_ascii=False).replace('|', '&#124;')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--live', action='store_true', required=True, help='Authorize calls to the configured agent.')
    parser.add_argument('--baseline', required=True, help='Local pre-6i commit with the original agent and policy.')
    parser.add_argument('--case', action='append', help='Compare only these case names (repeatable).')
    args = parser.parse_args()
    settings = Settings.from_env()
    with tempfile.TemporaryDirectory(prefix='expense-prompt-eval-') as temporary:
        work = Path(temporary)
        commit, old = baseline_prompt(args.baseline, work / 'old-policy')
        revised = agent.prompt_for
        selected = [case for case in cases(work, settings) if not args.case or case[0] in args.case]
        if args.case and set(args.case) != {case[0] for case in selected}:
            parser.error('unknown cases: ' + ', '.join(sorted(set(args.case) - {case[0] for case in selected})))
        print(f'# Prompt comparison for {settings.adapter} / {settings.model} / {settings.effort}\n')
        print(f'Baseline commit `{commit}`\n')
        print('| Case | Before | After |\n| --- | --- | --- |', flush=True)
        for name, case_settings, draft, folder, _ in selected:
            before = compare(old, case_settings, draft, folder)
            after = compare(revised, case_settings, draft, folder)
            print(f'| {name} | {cell(before)} | {cell(after)} |', flush=True)


if __name__ == '__main__':
    main()
