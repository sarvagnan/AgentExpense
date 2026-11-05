"""The policy the in-app agent follows: the application's general bookkeeping policy
plus the selected ledger's personal rules. Both are read on every run."""
from pathlib import Path
import re

GENERAL = Path('policy/BOOKKEEPING.md')
FUEL_SECTION = re.compile(r'(?ms)^## Fuel[^\n]*\n.*?(?=^#{1,2} |\Z)')


def policy_text(path: Path) -> str:
    """The entry rules in one policy file, without its terminal instructions."""
    lines = path.read_text().splitlines()
    start = next((i for i, l in enumerate(lines) if l.startswith('## Before adding')), 0)
    end = next((i for i, l in enumerate(lines) if l.startswith('## Rebuild and verify')), len(lines))
    return '\n'.join(lines[start:end]).strip()


def read(settings):
    """Read both policy files; a missing file stops the run before anything is written."""
    return (settings.application / GENERAL).read_text(), (settings.ledger / 'AGENTS.md').read_text()


def prompt_policy(settings, *, fuel: bool) -> str:
    general = policy_text(settings.application / GENERAL)
    personal = policy_text(settings.ledger / 'AGENTS.md')
    if not fuel:
        general, personal = FUEL_SECTION.sub('', general), FUEL_SECTION.sub('', personal)
    return (general.strip() + '\n\n# Personal rules for this ledger\n\nThese add to the general policy above '
            'and never remove one of its checks.\n\n' + personal.strip())
