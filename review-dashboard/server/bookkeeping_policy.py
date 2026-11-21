"""Application bookkeeping policy and the ledger's entry rules, read on every run."""
from __future__ import annotations

from pathlib import Path
import re

GENERAL = Path('policy/BOOKKEEPING.md')
ADD = Path('policy/ADD-AGENT.md')
ENTRY_RULES_HEADING = '## Rules for entries'
ENTRY_RULES_SECTION = re.compile(r'(?ms)^' + ENTRY_RULES_HEADING + r'[ \t]*$\n?(.*?)(?=^#{1,2} |\Z)')
FUEL_SECTION = re.compile(r'(?ms)^#{2,3} Fuel log[ \t]*$\n?.*?(?=^#{1,3} |\Z)')


def entry_rules(text: str) -> str | None:
    """Return the entry section, or None for a ledger using the legacy layout."""
    match = ENTRY_RULES_SECTION.search(text)
    return match[1].strip() if match else None


def read(settings):
    """Read both general policy files for server operations outside the Add agent."""
    return (settings.application / GENERAL).read_text(), (settings.ledger / 'AGENTS.md').read_text()


def add_policy(settings, *, fuel: bool) -> str:
    text = (settings.application / ADD).read_text()
    return (text if fuel else FUEL_SECTION.sub('', text)).strip()


def personal_rules(settings, *, fuel: bool) -> str:
    text = (settings.ledger / 'AGENTS.md').read_text()
    section = entry_rules(text)
    if section is None:
        return text.strip()
    return (section if fuel else FUEL_SECTION.sub('', section)).strip()
