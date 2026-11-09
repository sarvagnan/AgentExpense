"""The Add agent's answer and the hand form, validated by the ledger's models (scripts/ledger/store_models.py).

The agent answers with ``Answer``: the agent's fields of an entry, including only the
extensions ``ledger.json`` asks for and only their agent fields. The hand form and
Edit entry use ``Form``: the same envelope with every listed extension's fields and
optional new-account metadata. Both are kebab-case. A failure names the field.
"""
from __future__ import annotations

from copy import deepcopy
import re

from pydantic import ValidationError

from store import ledger_models


class AnswerError(ValueError):
    """An answer or form that doesn't fit its model; the agent can correct it."""


_WHOLE = re.compile(r'-?(0|[1-9][0-9]*)')


def person_amounts(form):
    """A person's form with whole-number amounts written as decimals ("450" becomes "450.00").

    People type whole amounts, and the composer and receipt readings carry them as typed;
    the agent's answers stay strict. Posting, cost and original amounts are normalised;
    anything else is left for the form check to report.
    """
    form = deepcopy(form)
    entry = form.get('entry') if isinstance(form, dict) else None
    if not isinstance(entry, dict):
        return form
    whole = lambda text: text + '.00' if isinstance(text, str) and _WHOLE.fullmatch(text) else text
    original = entry.get('original-amount')
    if isinstance(original, dict):
        original['amount'] = whole(original.get('amount'))
    for posting in entry.get('postings') or []:
        if isinstance(posting, dict):
            posting['amount'] = whole(posting.get('amount'))
            if isinstance(posting.get('cost'), dict):
                posting['cost']['amount'] = whole(posting['cost'].get('amount'))
    return form


def refusal_message(error: ValidationError, *, person: bool) -> str:
    """Useful recovery, without model types, regexes or validator internals."""
    first = error.errors()[0]
    parts = first['loc']
    where = '.'.join(str(part) for part in parts) or 'answer'
    if len(parts) >= 2 and parts[-2:] == ('cost', 'amount'):
        from decimal import Decimal, InvalidOperation
        try:
            if Decimal(str(first.get('input'))) <= 0:
                return 'A cost is always positive. For a refund, make the amount negative and keep the cost positive.'
        except InvalidOperation:
            pass
        reason = 'Enter a positive cost, such as 12.50.'
    elif first['type'] == 'missing':
        reason = 'Fill in this field.'
    elif first['type'] == 'extra_forbidden':
        reason = 'This field is not supported here. Remove it and try again.'
    elif parts and parts[-1] in ('amount', 'balance'):
        reason = 'Enter a number with a decimal point, such as 12.50. Use a minus sign for money paid out.'
    elif parts and parts[-1] in ('date', 'occurrence', 'next-session'):
        reason = 'Choose a real calendar date.'
    elif parts and parts[-1] in ('commodity', 'currency'):
        reason = 'Enter a currency or commodity on one line, without quotation marks.'
    elif parts and parts[-1] == 'account':
        reason = 'Choose an account, or enter its full path with one colon between each part.'
    elif first['type'] in ('literal_error', 'enum'):
        reason = 'Choose one of the available options.'
    elif first['type'] == 'string_pattern_mismatch':
        reason = 'Use a single line of text in the format shown by this field.'
    elif first['type'] == 'value_error':
        # Model cross-field checks already supply the bookkeeping reason. Drop
        # pydantic's prefix; never expose its input/type/URL rendering.
        reason = first['msg'].removeprefix('Value error, ')
        if 'cost must be in a different commodity' in reason:
            reason = 'A cost uses a different currency from its posting. For the same currency, remove the cost.'
        elif 'total cost needs a non-zero amount' in reason:
            reason = 'A posting with a total cost needs an amount other than zero.'
    else:
        reason = 'Check this field and try again.'
    if not person:
        return f'Please check {where}: {reason}'
    posting = next((part + 1 for part in parts if isinstance(part, int)), None)
    label = str(parts[-1]).replace('-', ' ').capitalize() if parts else 'Entry'
    return f'{"Posting " + str(posting) + " · " if posting else ""}{label}: {reason}'


def check(value, root, *, form: bool) -> None:
    models = ledger_models(root)
    try:
        (models.form if form else models.answer).model_validate(value)
    except ValidationError as error:
        raise AnswerError(refusal_message(error, person=form)) from None


def entry_extensions(root, given: dict | None) -> dict:
    """The stored extension groups from an answer's or form's ``extensions``: every listed
    extension, every field; a group with no values is null."""
    result = {}
    for item in ledger_models(root).extensions:
        group = (given or {}).get(item.name)
        values = {name: (group or {}).get(name) for name in item.definition.fields}
        result[item.name] = values if any(value is not None for value in values.values()) else None
    return result


def form_extensions(root, stored: dict | None) -> dict | None:
    """The form's ``extensions`` for these stored groups, or None when the ledger lists none."""
    models = ledger_models(root)
    if not models.extensions:
        return None
    return {item.name: deepcopy((stored or {}).get(item.name)) for item in models.extensions}


def answer_extensions(root) -> dict | None:
    """An answer's empty ``extensions`` (the asked extensions, each null), or None when none is asked."""
    asked = [item.name for item in ledger_models(root).extensions if item.ask and any(
        field.filled_by == 'agent' for field in item.definition.fields.values())]
    return {name: None for name in asked} if asked else None


def to_form(answer: dict, root) -> dict:
    """An agent's answer (or an older form) as a hand form: every listed extension with every field."""
    form = deepcopy(answer)
    entry = form.get('entry')
    if isinstance(entry, dict):
        groups = entry_extensions(root, entry.get('extensions')) if isinstance(entry.get('extensions'), (dict, type(None))) else entry.get('extensions')
        extensions = form_extensions(root, groups)
        rebuilt = {}
        for key, value in entry.items():
            if key == 'extensions':
                continue
            if key == 'notes' and extensions is not None:
                rebuilt['extensions'] = extensions
            rebuilt[key] = value
        if extensions is not None and 'extensions' not in rebuilt:
            rebuilt['extensions'] = extensions
        form['entry'] = rebuilt
    return form
