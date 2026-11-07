"""The JSON strict check: what `hledger check -s ordereddates` checks on the generated journal,
checked on the validated store without starting a process.

It runs on documents that ``store.validate`` has already checked against the models
(each entry and rule balances exactly there, with costs counted as hledger counts
them: ``store_models.balance``). What it adds, kind by kind, in hledger's own terms
(hledger's manual, "check", "Balance assertions", "Transaction balancing",
"Periodic transactions"):

- ``parseable``: the journal text this store renders parses as written. Account names
  carry no run of two spaces, tab, surrounding space, status mark or virtual-posting
  brackets; commodity directives are one line; a posting note sets no posting date
  (a ``date:``/``date2:`` tag or a bracketed date would move the posting); the
  included price file holds only ``P`` directives and comments.
- ``balanced``: every recurring rule's forecast transactions (the bill that replaces
  one occurrence included) balance exactly, with no implicit conversion.
- ``assertions``: in date order, same-day entries as written, each posting's balance
  assertion holds for its own account and commodity only (no subaccounts), ignoring
  costs, compared exactly.
- ``commodities``: every posting, cost, assertion and price commodity is declared.
- ``accounts``: every posting account is declared, exactly.
- ``ordereddates``: the entries the journal prints are in date order.
- ``forecast``: each rule's period renders as one of the period expressions hledger
  is known to generate on its start date (tests check them against hledger), for
  every rule, active or not, as ``check_store`` did with one hledger run per rule.

The strict hledger check of the whole journal remains a second check.
"""
from __future__ import annotations

from collections.abc import Iterable
from datetime import date
from decimal import Decimal, localcontext
from pathlib import Path
import re
from typing import Any

from store import bare_symbol, commodities
from store_models import unbalanced

KINDS = ('parseable', 'balanced', 'assertions', 'commodities', 'accounts', 'ordereddates', 'forecast')

# A posting comment that hledger reads as a posting date (or refuses as one), measured on 1.52.4:
# a date: or date2: tag after a space or at the start, or brackets holding only 0-9 / . = -
# with a digit and a date separator. "[1]", "[2026]", "[-]" and "a,date:" stay plain text.
POSTING_DATE = re.compile(r'(?:^|\s)date2?:|\[(?=[0-9/.=-]*[0-9])(?=[0-9=]*[/.-])[0-9/.=-]+\]')
# hledger's simple dates: year, month and day with one separator (-, / or .).
PRICE_DATE = re.compile(r'(\d{4})([-/.])(\d{1,2})\2(\d{1,2})')
PRICE_NUMBER = re.compile(r'-?\d+(?:\.\d+)?')
TOKEN = re.compile(r'"([^"]*)"|(\S+)')
PERIOD = re.compile(r'^(?:monthly|yearly|daily|every [1-9][0-9]* (?:months|years)|every (?:29|30|31)th day of month)'
                    r' from (\d{4}-\d{2}-\d{2})(?: to (\d{4}-\d{2}-\d{2}))?$')


class StrictCheckError(ValueError):
    """A store the strict check refuses: ``kind`` is one of KINDS."""

    def __init__(self, kind: str, message: str, *, refusal: dict | None = None):
        super().__init__(message)
        self.kind = kind
        self.refusal = refusal
        self.problems: list[str] = []  # other problems found alongside a failing assertion


# What a leading character makes of a posting line in hledger (measured on 1.52.4).
LEADING = {'(': 'a virtual posting', '[': 'a balanced virtual posting', '*': 'a cleared status mark',
           '!': 'a pending status mark', ';': 'a comment'}


def odd_space(ch: str) -> bool:
    """Whitespace or a line or control character other than the plain space U+0020.

    hledger reads a no-break or thin space as a plain space (so the name changes), two of
    them as the end of the account name, and U+0085 as a line break."""
    import unicodedata
    return ch != ' ' and (ch.isspace() or unicodedata.category(ch) in ('Zs', 'Zl', 'Zp', 'Cc'))


def account_problem(name: str) -> str | None:
    """Why hledger would refuse or misread an account name in our journal, or None."""
    odd = next((ch for ch in name if odd_space(ch)), None)
    if odd:
        return f'a whitespace character other than a plain space (U+{ord(odd):04X})'
    if name != name.strip(' '):
        return 'a space at either end'
    if '  ' in name:
        return 'two spaces in a row, which end an account name in the journal'
    if name[:1] in LEADING:
        return f'a leading "{name[0]}", which hledger reads as {LEADING[name[0]]}'
    return None


def _declared(directives: list[str]) -> set[str]:
    try:
        return commodities({'commodities': directives})
    except ValueError as error:
        raise StrictCheckError('parseable', str(error)) from None


def posting_problem(posting: dict, accounts: set[str], declared: set[str]) -> tuple[str, str] | None:
    """The kind and text of the first strict-check problem in one posting, or None."""
    for note in posting['notes']:
        if POSTING_DATE.search(note):
            return 'parseable', f'a posting note must not set a posting date: {note!r}'
    if posting['account'] not in accounts:
        return 'accounts', f"undeclared account {posting['account']}"
    for amount in (posting, posting['cost'], posting['assert']):
        if amount and amount['commodity'] not in declared:
            return 'commodities', f"undeclared commodity {amount['commodity']}"
    return None


def check_forecast(rules: list[dict], accounts: set[str], declared: set[str]) -> None:
    """Every rule's forecast transactions, as one hledger run per rule template checked them."""
    from generate_ledger import forecast_templates, period
    for rule in rules:
        owner = f"Forecast rule {rule['id']} ({rule['description']})"
        for template in forecast_templates(rule):
            text = period(template['schedule'])
            found = PERIOD.match(text)
            if not found or found[2] and found[2] <= found[1]:
                raise StrictCheckError('forecast', f'{owner} fails strict validation: unsupported period "{text}"')
            problem = unbalanced(template['postings'])
            if problem:
                raise StrictCheckError('balanced', f'{owner} fails strict validation: {problem}')
            for posting in template['postings']:
                found = posting_problem(posting, accounts, declared)
                if found:
                    raise StrictCheckError(found[0], f'{owner} fails strict validation: {found[1]}')


def price_problem(line: str, declared: set[str]) -> tuple[str, str] | None:
    """Why hledger would refuse a P directive, or None: P, a simple date, a commodity and an amount.

    Stricter than hledger only in what the price job never writes: group marks, exponents,
    a time of day and same-line comments are refused too."""
    tokens = TOKEN.findall(line)  # (quoted text, bare text) pairs
    if len(tokens) != 5 or tokens[0] != ('', 'P'):
        return 'parseable', 'unrecognized price directive'
    _, day_text, commodity, first, second = tokens
    day = PRICE_DATE.fullmatch(day_text[1])
    try:
        if not day:
            raise ValueError
        date(int(day[1]), int(day[3]), int(day[4]))
    except ValueError:
        return 'parseable', 'a price date is a year, month and day, such as 2026-09-28'
    if not first[0] and PRICE_NUMBER.fullmatch(first[1]):
        unit = second
    elif not second[0] and PRICE_NUMBER.fullmatch(second[1]):
        unit = first
    else:
        return 'parseable', 'a price is a decimal number and its commodity'
    for quoted, bare in (commodity, unit):
        if bare and not bare_symbol(bare):
            return 'parseable', f'quote the commodity {bare}: only letters or currency signs go unquoted'
    missing = next((symbol for symbol in (commodity[0] or commodity[1], unit[0] or unit[1]) if symbol not in declared), None)
    return ('commodities', f'undeclared commodity {missing}') if missing else None


def check_prices(root: Path, declared: set[str]) -> None:
    """The included price file: P directives hledger reads as written, with declared commodities, and comments."""
    from ledger_manifest import module_settings, read_manifest
    path = root / 'data/prices/mutual-funds.prices'
    if module_settings(read_manifest(root))['investments'] is False or not path.exists():
        return
    for number, line in enumerate(path.read_text(encoding='utf-8').splitlines(), 1):
        if not line.strip() or line.lstrip()[:1] in (';', '#', '*'):
            continue
        found = price_problem(line, declared)
        if found:
            raise StrictCheckError(found[0], f'data/prices/mutual-funds.prices:{number}: {found[1]}')


def check_assertions(documents: dict[Path, Any], root: Path) -> None:
    """hledger's balance assertions, from the same running balances as confirmed balances."""
    from confirmed_balances import failures
    with localcontext() as context:
        context.prec = 200
        failure = next(failures(documents, root), None)
    if failure:
        raise StrictCheckError('assertions', (
            f"Balance assertion failed in {failure['account']} on {failure['date']} ({failure['entry_id']}): "
            f"asserted {failure['asserted']} {failure['commodity']}, calculated {failure['calculated']} {failure['commodity']}"),
            refusal=failure)


def journal_order(documents: dict[Path, Any]) -> Iterable[dict]:
    """The entries in the order the store holds them: year files by name, each as written."""
    for path in sorted(path for path in documents if path.parent.name == 'expenses'):
        yield from documents[path]


def check(documents: dict[Path, Any], root: Path, *, prices: bool = True) -> None:
    """Refuse the first problem hledger's strict check would find, as a StrictCheckError.

    ``documents`` have passed ``store.validate``. ``prices`` also checks the ledger's
    included price file (a scratch copy without it has nothing to include). The balance
    assertions always run: when one fails, the error is the assertion's, with its structured
    ``refusal``, and the first other problem is in ``problems`` and in the message."""
    try:
        _check_journal(documents, root, prices=prices)
        other = None
    except StrictCheckError as error:
        other = error
    try:
        check_assertions(documents, root)
    except StrictCheckError as error:
        if other is not None:
            error.problems.append(str(other))
            error.args = (f'{error}; also: {other}',)
        raise error from None
    if other is not None:
        raise other


def _check_journal(documents: dict[Path, Any], root: Path, *, prices: bool) -> None:
    """Everything but the balance assertions."""
    declarations = documents[root / 'data/accounts.json']
    accounts = set(declarations['accounts'])
    declared = _declared(declarations['commodities'])
    for name in accounts:
        problem = account_problem(name)
        if problem:
            raise StrictCheckError('parseable', f'Account name {name!r} has {problem}')
    previous = ''
    with localcontext() as context:
        context.prec = 200
        for entry in journal_order(documents):
            if entry['date'] < previous:
                raise StrictCheckError('ordereddates', f"{entry['id']}: dates out of order ({entry['date']} after {previous})")
            previous = entry['date']
            for posting in entry['postings']:
                found = posting_problem(posting, accounts, declared)
                if found:
                    raise StrictCheckError(found[0], f"{entry['id']}: {found[1]}")
        check_forecast(documents[root / 'data/forecast.json'], accounts, declared)
    if prices:
        check_prices(root, declared)

