"""The prompt sent to the provider is pinned independently of model responses."""
import json
from pathlib import Path
import re
import tempfile
import unittest

from jsonschema import validate

from agent import prompt_for, schema_for
from prompt_cases import GOLDENS, STATEMENT_ID, cases


class PromptTests(unittest.TestCase):
    def test_golden_prompts(self):
        with tempfile.TemporaryDirectory() as tmp:
            for name, settings, draft, folder, work in cases(Path(tmp)):
                with self.subTest(name=name):
                    prompt, _ = prompt_for(settings, draft, folder, work)
                    self.assertMultiLineEqual((GOLDENS / f'{name}.txt').read_text(), prompt + '\n')

    def test_no_internal_fields_or_empty_sections(self):
        with tempfile.TemporaryDirectory() as tmp:
            for name, settings, draft, folder, work in cases(Path(tmp)):
                with self.subTest(name=name):
                    prompt, _ = prompt_for(settings, draft, folder, work)
                    self.assertNotIn('hledger', prompt.casefold())
                    self.assertNotIn(STATEMENT_ID, prompt)
                    self.assertNotRegex(prompt, r'(?m)^(#+) .+\n\n*(?:\1 |\[\]$|\{\}$|\Z)')
                    if name in ('receipt', 'statement', 'cited-statement'):
                        continue
                    self.assertNotRegex(prompt, r'\b(?:entry IDs?|rule links?|reimbursement fields?|serial numbers?|statement fields?|assertions?)\b')
                    allowed = schema_for(settings)
                    fields = {field for model in (allowed, *allowed.get('$defs', {}).values())
                              for field in model.get('properties', {})}
                    self.assertLessEqual(set(re.findall(r'`([a-z]+(?:-[a-z]+)*)`', prompt)), fields)
                    self.assertNotIn('1234567890abcdef', prompt)  # pragma: allowlist secret
                    if name == 'ledger-fix':
                        saved_entry = prompt.split('# Saved entry\n', 1)[1].splitlines()[0]
                        validate(json.loads(saved_entry), {'$ref': '#/$defs/AnswerEntry', '$defs': allowed['$defs']})

    def test_conditional_context(self):
        with tempfile.TemporaryDirectory() as tmp:
            for name, settings, draft, folder, work in cases(Path(tmp)):
                if name in ('receipt', 'statement', 'cited-statement', 'ledger-fix'):
                    continue
                with self.subTest(name=name):
                    prompt, _ = prompt_for(settings, draft, folder, work)
                    expected = {
                        'search_old_ledger': name == 'older-ledger',
                        '<<<TEXT OF ATTACHMENT': name == 'pdf',
                        '## Fuel log': name != 'fuel-off',
                        '# Earlier questions and answers': name == 'answered',
                        'A confirmed balance cannot be overridden': name == 'balance-refusal',
                        'trust the words over this': name == 'ready',
                    }
                    for part, wanted in expected.items():
                        self.assertEqual(part in prompt, wanted, part)

    def test_payment_context_keeps_only_the_requested_account_fields(self):
        with tempfile.TemporaryDirectory() as tmp:
            _, settings, draft, folder, work = next(cases(Path(tmp)))
            prompt, _ = prompt_for(settings, draft, folder, work)
            self.assertLess(prompt.index('## Rewards'), prompt.index('# Payment accounts'))
            self.assertLess(prompt.index('# Payment accounts'), prompt.index('# Personal rules for this ledger'))
            self.assertLess(prompt.index('# Personal rules for this ledger'), prompt.index('# The draft'))
            serialized = prompt.split('# Payment accounts\n', 1)[1].splitlines()[0]
            payments = json.loads(serialized)
            self.assertIn('points', {payment['kind'] for payment in payments})
            for payment in payments:
                self.assertEqual(set(payment), {'account', 'label', 'kind', 'currency', 'aliases'})
