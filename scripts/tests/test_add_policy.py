"""Pin each Add rule to the general sections that justify it.

Review both texts when a rule changes, then refresh the fingerprint for that row.
The fingerprint includes the headings and prose on both sides, with whitespace
collapsed so rewrapping prose needs no change to the mapping.
"""
import hashlib
from pathlib import Path
import re
import unittest


POLICY = Path(__file__).resolve().parents[2] / 'policy'
MAPPING = {
    'Before adding': (('Before adding or changing entries',), '366b290274796e5f'),  # pragma: allowlist secret
    'Duplicates': (('Duplicates',), 'd3bb0233bec070c7'),
    'Evidence and dates': (('Evidence and receipts',), '4bce867bb566d319'),  # pragma: allowlist secret
    'Double entry': (('Double entry',), '1f422c5bb2310ea6'),  # pragma: allowlist secret
    'Accounts': (('Accounts', 'Before adding or changing entries'), 'dc6129197723367d'),
    'Currencies and conversions': (('Currencies and conversions', 'Transfers'), 'bfb499e83f97c187'),  # pragma: allowlist secret
    'Transfers': (('Transfers',), 'b3c67bdeb5334d4b'),
    'Prepaid wallets': (('Prepaid wallets',), '864cb68731174f1b'),  # pragma: allowlist secret
    'People': (('People',), '93f3be68200e18d0'),  # pragma: allowlist secret
    'Investments': (('Investments',), 'e78af6a71a0aa15d'),  # pragma: allowlist secret
    'Rewards': (('Rewards',), '8dcabe523c4bd095'),  # pragma: allowlist secret
    'Pending entries': (('Pending entries',), 'e194a8736f3a47ad'),  # pragma: allowlist secret
    'Unpaid bills': (('Recurring rules, varying amounts and bills',), '23c7f2108cb8ee1a'),  # pragma: allowlist secret
    'Fuel log': (('Fuel log',), '0e6ad9b0e795036a'),  # pragma: allowlist secret
}


def sections(filename):
    parts = re.split(r'^## (.+)\n', (POLICY / filename).read_text(), flags=re.MULTILINE)
    headings = parts[1::2]
    if len(headings) != len(set(headings)):
        raise ValueError(f'Duplicate rule headings in {filename}')
    return {heading: ' '.join(body.split()) for heading, body in zip(headings, parts[2::2])}


def fingerprint(add, general, rule, sources):
    text = '\n'.join(f'{heading}\n{policy[heading]}' for heading, policy in
                     [(rule, add), *((source, general) for source in sources)])
    return hashlib.sha256(text.encode()).hexdigest()[:16]


class AddPolicyTests(unittest.TestCase):
    def test_every_add_rule_tracks_its_general_sources(self):
        preamble = (POLICY / 'ADD-AGENT.md').read_text().partition('\n## ')[0].strip()
        self.assertEqual(preamble, '# Add bookkeeping policy', 'Put every Add rule under a mapped heading.')
        add, general = sections('ADD-AGENT.md'), sections('BOOKKEEPING.md')
        self.assertEqual(set(add), set(MAPPING))
        for rule, (sources, approved) in MAPPING.items():
            with self.subTest(rule=rule, general_sections=sources):
                self.assertEqual(fingerprint(add, general, rule, sources), approved,
                                 'Review both policies before updating this fingerprint.')
