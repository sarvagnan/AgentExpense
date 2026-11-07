"""Ledger format 2: the one definition of the store's shape and the Add agent's answer.

Every store document and the agent's answer are pydantic v2 models in strict mode
with ``extra="forbid"``. Every field is required; a field that can be empty is
``T | None`` (``null``) or a list (``[]``), so a missing key and an extra key are
both errors. Names are the store's kebab-case aliases. Amounts are exact decimal
text, never JSON numbers.

Every field of an entry, a posting and a recurring rule (and of the groups inside
them) records who sets it in ``x-set-by``. The agent's answer model is derived from
the entry model by selecting the fields the agent sets, so the two cannot drift.

**Extensions.** The core entry knows nothing about ledger-specific facts such as
fuel readings. An extension definition (``extensions/<name>.json``, built in or in
the ledger) adds a group of typed fields to every entry of a ledger that lists it
in ``ledger.json``. ``ledger_models`` composes each ledger's entry, Trash and
answer models from the core and those definitions. ``schema/extension-types.json``
maps each extension type to its JSON Schema; Python and Node both compose a
ledger's store schema from it (``compose_store_schema`` and
``review-dashboard/scripts/ledger-manifest.mjs``), and a test checks the
composition equals what the models produce.

The model validators check one entry or rule on its own: exact balance per
commodity, costs, the statement row and duplicate links. Checks across documents
(IDs, declared accounts, statement membership, rule links) stay in
``store.validate`` and ``check_store.py``.

The JSON Schemas in ``schema/`` are generated from these models:

    uv run --locked --project review-dashboard/server python scripts/ledger/store_models.py --write

``--check`` exits non-zero when a checked-in schema is out of date. docs/STORE.md
describes every field and docs/EXTENSIONS.md the extensions.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, localcontext
from enum import StrEnum
from functools import cache
import json
from pathlib import Path
import re
import sys
import types
from typing import Annotated, Any, Literal, Union, get_args, get_origin

from annotated_types import Ge, Le
from pydantic import AfterValidator, BaseModel, ConfigDict, Field, RootModel, StringConstraints, TypeAdapter, create_model, model_validator
from pydantic.fields import FieldInfo
from pydantic.json_schema import GenerateJsonSchema, models_json_schema

FORMAT = 2
APPLICATION_ROOT = Path(__file__).resolve().parents[2]
SCHEMA_DIR = APPLICATION_ROOT / 'schema'
STORE_SCHEMA = SCHEMA_DIR / 'store.schema.json'
ANSWER_SCHEMA = SCHEMA_DIR / 'answer.schema.json'
EXTENSION_SCHEMA = SCHEMA_DIR / 'extension-definition.schema.json'
EXTENSION_TYPES = SCHEMA_DIR / 'extension-types.json'
BUILT_IN_EXTENSIONS = APPLICATION_ROOT / 'extensions'


class Setter(StrEnum):
    """Who may write a field. A field lists every writer; readers never care."""
    AGENT = 'agent'          # the Add answer; the hand form and Edit entry fill the same answer
    SERVER = 'server'        # derived by the app on write, never taken from an answer
    STATEMENT = 'statement'  # statement Apply (card and balance-style) and the statement CLIs
    PERSON = 'person'        # a person's own action outside the answer: Review, rule actions, setup, Edit entry
    HISTORY = 'history'      # kept from older data; no current writer sets it, writers keep it


A, SV, ST, P, H = Setter.AGENT, Setter.SERVER, Setter.STATEMENT, Setter.PERSON, Setter.HISTORY


def field(alias: str, description: str, set_by: tuple[Setter, ...] | None = None, *,
          schema: dict | None = None, **constraints: Any) -> Any:
    """A required field with its kebab-case alias, meaning and, for ledger data, its setters."""
    extra = dict(schema or {})
    if set_by is not None:
        extra['x-set-by'] = [setter.value for setter in set_by]
    return Field(alias=alias, description=description, json_schema_extra=extra or None, **constraints)


def setters(info: FieldInfo) -> tuple[str, ...]:
    extra = info.json_schema_extra if isinstance(info.json_schema_extra, dict) else {}
    return tuple(extra.get('x-set-by', ()))


class JsonExtra:
    """Annotation metadata that adds keywords (for example ``format``) to a type's JSON Schema."""

    def __init__(self, **keywords: Any):
        self.keywords = keywords

    def __get_pydantic_json_schema__(self, core_schema, handler):
        return {**handler(core_schema), **self.keywords}


def _calendar_day(text: str) -> str:
    date.fromisoformat(text)
    return text


def _moment(text: str) -> str:
    datetime.fromisoformat(text)
    return text


def _unique(values: list) -> list:
    if len({json.dumps(value, sort_keys=True) for value in values}) != len(values):
        raise ValueError('items must be unique')
    return values


def unique(item: Any) -> Any:
    """A list whose items are all different."""
    return Annotated[list[item], AfterValidator(_unique), JsonExtra(uniqueItems=True)]


# Scalar types. Text that renders into the journal stays on one line.
Id = Annotated[str, StringConstraints(pattern=r'^[0-9a-f]{16}$')]
OperationId = Annotated[str, StringConstraints(pattern=r'^[0-9a-f]{32}$')]
Day = Annotated[str, StringConstraints(pattern=r'^[0-9]{4}-[0-9]{2}-[0-9]{2}$'), AfterValidator(_calendar_day), JsonExtra(format='date')]
Moment = Annotated[str, StringConstraints(pattern=r'^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(\.[0-9]+)?([+-][0-9]{2}:[0-9]{2}|Z)$'),
                   AfterValidator(_moment), JsonExtra(format='date-time')]
Line = Annotated[str, StringConstraints(pattern=r'^[^\r\n]+$')]
Note = Annotated[str, StringConstraints(pattern=r'^[^\r\n]*$')]
Account = Annotated[str, StringConstraints(pattern=r'^[^\r\n:]+(?::[^\r\n:]+)*$')]
Commodity = Annotated[str, StringConstraints(pattern=r'^[^\r\n"]+$')]
# Whitespace spelled out, so pydantic's regex and JavaScript's agree: the union of what Python, Rust
# and JavaScript call \s (JavaScript adds U+FEFF; Python and Rust add U+0085; Python adds U+001C–U+001F).
WHITESPACE = r'\t\n\x0b\x0c\r\x1c-\x1f \x85\xa0\u1680\u2000-\u200a\u2028\u2029\u202f\u205f\u3000\ufeff'
Code = Annotated[str, StringConstraints(pattern=rf'^[^{WHITESPACE}";=@]+$')]
# Exact decimal text. A posting amount always has a decimal point; a cost, an
# original amount or a reading may be a whole number, as it was written.
Amount = Annotated[str, StringConstraints(pattern=r'^-?(0|[1-9][0-9]*)\.[0-9]+$')]
Number = Annotated[str, StringConstraints(pattern=r'^-?(0|[1-9][0-9]*)(\.[0-9]+)?$')]
Unsigned = Annotated[str, StringConstraints(pattern=r'^(0|[1-9][0-9]*)\.[0-9]+$')]
# Greater than zero; the decimal point is optional, as in Number.
PositiveNumber = Annotated[str, StringConstraints(pattern=r'^([1-9][0-9]*(\.[0-9]+)?|0\.[0-9]*[1-9][0-9]*)$')]
Cents = Annotated[str, StringConstraints(pattern=r'^(0|[1-9][0-9]*)\.[0-9]{2}$')]
Positive = Annotated[int, Ge(1)]
WholeNumber = Annotated[int, Ge(0)]
Kebab = Annotated[str, StringConstraints(pattern=r'^[a-z][a-z0-9]*(-[a-z0-9]+)*$')]


class Strict(BaseModel):
    model_config = ConfigDict(strict=True, extra='forbid', validate_by_alias=True, validate_by_name=False,
                              serialize_by_alias=True)


class Group(Strict):
    """A group of related optional facts: ``null`` when none is known, never all ``null`` or ``[]``.

    Its schema carries ``x-group``, so a JSON Schema reader (Node) makes the same check."""
    model_config = ConfigDict(json_schema_extra={'x-group': True})

    @model_validator(mode='after')
    def _not_empty(self):
        if all(getattr(self, name) in (None, []) for name in type(self).model_fields):
            raise ValueError('a group with no values must be null')
        return self


# Postings ---------------------------------------------------------------------------

class Cost(Strict):
    """The posting's price in another commodity: the whole price (hledger @@) or the price of one unit (@)."""
    amount: PositiveNumber = field('amount', 'Exact decimal text in the cost commodity, as written; greater than zero.', (A, ST))
    commodity: Commodity = field('commodity', 'The commodity the posting was paid in.', (A, ST))
    per: Literal['total', 'unit'] = field('per', '"total": the amount is the whole price. "unit": the price of one unit; '
                                          'only where it balances exactly.', (SV, ST, H))


class Assertion(Strict):
    """A confirmed balance: the account's running balance in this commodity after this posting."""
    amount: Amount = field('amount', 'The expected balance, exact decimal text.', (ST, P))
    commodity: Commodity = field('commodity', 'The asserted commodity: the account currency or investment units.', (ST, P))


class Posting(Strict):
    """One line of an entry or rule. Written on one line in the store file."""
    account: Account = field('account', 'A declared account name.', (A, ST, P))
    amount: Amount = field('amount', 'Signed exact decimal text: positive into the account, negative out of it.', (A, ST, P))
    commodity: Commodity = field('commodity', "The posting's commodity: the account currency, or units for an investment.", (A, ST, P))
    cost: Cost | None = field('cost', 'Its price in another commodity, or null.', (A, ST))
    assertion: Assertion | None = field('assert', 'A confirmed balance after this posting, or null.', (ST, P))
    notes: list[Note] = field('notes', 'Comment lines under the posting, in order.', (H,))

    @model_validator(mode='after')
    def _cost_in_another_commodity(self):
        if self.cost is not None and self.cost.commodity == self.commodity:
            raise ValueError('a cost must be in a different commodity from its posting')
        if self.cost is not None and self.cost.per == 'total' and Decimal(self.amount) == 0:
            raise ValueError('a total cost needs a non-zero amount')
        return self


def balance(postings: list) -> dict[str, Decimal]:
    """Each commodity's exact sum, with a costed posting counted in its cost commodity.

    As hledger counts it: a unit cost counts its price times the quantity; a total cost
    counts its amount as written, negated when the quantity is negative (so a zero
    quantity counts the whole cost). Costs are normally positive, and then either is
    signed like the posting. Accepts models or store dictionaries. Only the
    commodities that don't sum to zero are returned.
    """
    totals: dict[str, Decimal] = defaultdict(Decimal)
    with localcontext() as context:
        context.prec = 200  # exact for any amount the store can hold
        for posting in postings:
            raw = posting if isinstance(posting, dict) else posting.model_dump(by_alias=True)
            amount = Decimal(raw['amount'])
            cost = raw.get('cost')
            if cost is None:
                totals[raw['commodity']] += amount
                continue
            price = Decimal(cost['amount'])
            totals[cost['commodity']] += (-price if amount < 0 else price) if cost['per'] == 'total' else price * amount
    return {commodity: total for commodity, total in totals.items() if total != 0}


def unbalanced(postings: list) -> str | None:
    """Why postings don't balance exactly, in hledger's two kinds (unbalanced, or an
    implicit conversion between commodities), or None when they balance."""
    residue = balance(postings)
    if not residue:
        return None
    raw = [posting if isinstance(posting, dict) else posting.model_dump(by_alias=True) for posting in postings]
    text = 'the postings do not balance exactly in ' + ', '.join(sorted(residue))
    if len({p['commodity'] for p in raw} | {p['cost']['commodity'] for p in raw if p.get('cost')}) > 1:
        text += '; amounts in different commodities need a cost (no implicit conversion)'
    return text


def _balanced(postings: list) -> None:
    problem = unbalanced(postings)
    if problem:
        raise ValueError(problem)


# Groups of an entry --------------------------------------------------------------------

class StatementRow(Group):
    """Where the entry was read from a statement or an imported file."""
    end: Day | None = field('end', 'End date of the filed card statement that bills this entry.', (ST,))
    serial: Line | None = field('serial', "The row's serial number printed on the statement.", (ST,))
    page: Positive | None = field('page', 'Page of the row in the filed card statement PDF.', (ST,))
    source_page: Positive | None = field('source-page', "Page of the row in the entry's first evidence file.", (ST,))
    source_row: Positive | None = field('source-row', 'Row number in an imported bank workbook.', (ST,))


class References(Group):
    """Identifiers printed by the payee or the bank."""
    reference: Line | None = field('reference', 'A payment or bank reference printed on the evidence, or null.', (A, ST))
    order_id: unique(Line) = field('order-id', "The payee's order or invoice numbers, in order; [] when none.", (A,))


class Money(Strict):
    """An amount in a commodity."""
    amount: Number = field('amount', 'Exact decimal text, as printed.', (A, ST))
    commodity: Code = field('commodity', 'The commodity code.', (A, ST))


class Reimbursement(Strict):
    """A shared cost that someone repays, grouped with its other parts."""
    group: Line = field('group', 'Identifier shared by the entries of one reimbursement.', (H,))
    label: Line | None = field('label', 'Display label for the group, or null.', (H,))


class EstimateExclusion(Strict):
    """The entry is left out of the variable-spending estimate."""
    reason: Line = field('reason', 'Why, in a few words (for example, forecast separately).', (H,))


class RuleLink(Strict):
    """The recurring rule occurrence this entry pays."""
    id: Id = field('id', 'The rule ID.', (SV, P))
    occurrence: Day = field('occurrence', 'The occurrence date it pays.', (SV, P))


class DuplicateLink(Strict):
    """An unsettled possible duplicate."""
    id: Id = field('id', 'The other entry, whose ID is greater than this one.', (ST, P, SV))
    reason: Literal['amount-date-gap', 'dissimilar-name', 'several-candidates', 'overlapping-statements', 'imported-after-start'] = field(
        'reason', 'Why the pair needs a look.', (ST, P, SV))


class Extensions(Strict):
    """One group per extension the ledger lists in ledger.json, in that order; each is null when empty.
    The core lists none, so an entry of a ledger without extensions has {}."""


# Entry --------------------------------------------------------------------------------

class Entry(Strict):
    """One actual entry in data/expenses/<year>.json."""
    id: Id = field('id', 'Permanent random ID, unique across entries, rules, statements and Trash.', (SV,))
    date: Day = field('date', 'The calendar date printed on the evidence or the payment. Its year names the file.', (A, ST))
    status: Literal['confirmed', 'pending'] = field('status', '"pending" when the classification still needs review.', (A, ST, P))
    description: Line = field('description', 'The payee or what was paid for, as the evidence names it.', (A, ST))
    evidence: list[Line] = field('evidence', 'Filed evidence paths under Bills/, or older free-text sources, in order.', (SV, ST, P))
    statement: StatementRow | None = field('statement', 'Where a statement or import recorded it, or null.', (ST,))
    references: References | None = field('references', 'Identifiers on the evidence, or null.', (A, ST))
    classification: Line | None = field('classification', 'A short note on how the expense was classified, or null.', (A,))
    original_amount: Money | None = field('original-amount', 'The amount in another currency, as printed, when the payment was converted.', (A, ST))
    reimbursement: Reimbursement | None = field('reimbursement', 'The reimbursement this entry belongs to, or null.', (H,))
    excluded_from_estimate: EstimateExclusion | None = field('excluded-from-estimate', 'Left out of the variable-spending estimate, or null.', (H,))
    extensions: Extensions = field('extensions', "The ledger's extension groups, in ledger.json order.", (A, P))
    rule: RuleLink | None = field('rule', 'The recurring rule occurrence it pays, or null.', (SV, P))
    possible_duplicate_of: list[DuplicateLink] = field('possible-duplicate-of', 'Unsettled possible duplicates, sorted by ID.', (ST, P, SV))
    notes: list[Note] = field('notes', 'Comment lines, in order.', (A, ST, P, SV))
    postings: list[Posting] = field('postings', 'At least one posting, in order.', (A, ST, P), min_length=1)

    @model_validator(mode='after')
    def _one_entry(self):
        _balanced(self.postings)
        row = self.statement
        if row is not None and row.end is not None and (row.serial is None or row.page is None):
            raise ValueError('a card statement row needs its serial and page')
        targets = [link.id for link in self.possible_duplicate_of]
        if targets != sorted(set(targets)) or any(target <= self.id for target in targets):
            raise ValueError('possible-duplicate-of needs unique, sorted IDs greater than the entry ID')
        return self


# Recurring rules ------------------------------------------------------------------------

class Schedule(Strict):
    """When a rule recurs."""
    every: Literal['month', 'year'] = field('every', 'The calendar unit.', (P,))
    start: Day = field('from', 'The current occurrence; equals the rule date.', (P, SV))
    interval: Positive = field('interval', 'Units between occurrences; 1 for monthly or yearly.', (P,))
    anchor_day: Annotated[int, Ge(1), Le(31)] | None = field('anchor-day', 'Day of month it falls on when a short month moved it, or null.', (P, SV))


class Origin(Strict):
    """How the rule came to exist."""
    kind: Literal['owner', 'subscription-words', 'monthly-history', 'history-pass'] = field('kind', 'What proposed it.', (P, SV))
    words: str | None = field('words', 'The typed words that proposed it, or null.', (P, SV))
    entry_ids: unique(Id) = field('entry-ids', 'Entries it was proposed from.', (P, SV))
    came_back: bool = field('came-back', 'True when a removed rule was proposed again.', (SV,))


class Removal(Strict):
    """Why a removed rule was removed."""
    date: Day = field('date', 'When it was removed.', (P, SV))
    entry_ids: unique(Id) = field('entry-ids', 'Entries that showed it had stopped.', (P, SV))


class Bill(Strict):
    """A received, unpaid bill for the current occurrence of a varying rule."""
    amount: Unsigned = field('amount', 'The billed amount, exact decimal text.', (P,))
    received: Day = field('received', 'When the bill arrived.', (P,))
    pay_by: Day | None = field('pay-by', 'Due date, or null.', (P,))
    occurrence: Day = field('occurrence', 'The occurrence it bills; equals the rule date.', (P,))
    evidence: unique(Line) = field('evidence', 'Filed bill paths.', (SV,))


class Rule(Strict):
    """One recurring rule in data/forecast.json. Its postings are the estimate."""
    id: Id = field('id', 'Permanent random ID.', (SV,))
    date: Day = field('date', 'The next occurrence; equals schedule.from.', (P, SV))
    status: Literal['confirmed', 'pending'] = field('status', 'Whether the owner confirmed it.', (P,))
    description: Line = field('description', 'What the payment is for.', (P,))
    schedule: Schedule = field('schedule', 'When it recurs.', (P, SV))
    amount: Literal['fixed', 'varies'] = field('amount', '"varies": the postings hold the median of the last three payments.', (P, SV))
    lifecycle: Literal['active', 'paused', 'ended', 'removed'] = field('lifecycle', 'Only active rules forecast and match.', (P, SV))
    origin: Origin | None = field('origin', 'How it was proposed, or null for older rules.', (P, SV))
    created: Day | None = field('created', 'When it was made, or null for older rules.', (SV,))
    removal: Removal | None = field('removal', 'Removal evidence; required when removed.', (P, SV))
    bill: Bill | None = field('bill', 'A received unpaid bill, or null.', (P, SV))
    evidence: list[Line] = field('evidence', 'Evidence carried by older rules.', (H,))
    references: References | None = field('references', 'Identifiers carried by older rules, or null.', (H,))
    classification: Line | None = field('classification', 'Carried by older rules, or null.', (H,))
    original_amount: Money | None = field('original-amount', 'Carried by older rules, or null.', (H,))
    reimbursement: Reimbursement | None = field('reimbursement', 'Carried by older rules, or null.', (H,))
    excluded_from_estimate: EstimateExclusion | None = field('excluded-from-estimate', 'Left out of the variable-spending estimate, or null.', (H,))
    notes: list[Note] = field('notes', 'Comment lines, in order.', (P,))
    postings: list[Posting] = field('postings', 'The estimated postings of one occurrence.', (P, SV), min_length=1)

    @model_validator(mode='after')
    def _one_rule(self):
        _balanced(self.postings)
        if self.date != self.schedule.start:
            raise ValueError('a rule date must equal schedule.from')
        if self.lifecycle == 'removed' and self.removal is None:
            raise ValueError('a removed rule needs removal')
        if any(posting.assertion is not None for posting in self.postings):
            raise ValueError('rule postings have no assert')
        if self.amount == 'varies' and (len(self.postings) != 2 or len({p.commodity for p in self.postings}) != 1
                                        or any(p.cost is not None for p in self.postings)):
            raise ValueError('only simple two-posting payments in one currency can vary')
        if self.bill is not None and (self.amount != 'varies' or Decimal(self.bill.amount) <= 0
                                      or self.bill.occurrence != self.date):
            raise ValueError('a bill needs a varying rule, a positive amount and the current occurrence')
        return self


# Other store documents (no setters: they are not entries) --------------------------------

class StatementReader(Strict):
    """How to read this account's statements."""
    reader: Line = field('reader', 'The reader name.')
    identifier: Line | None = field('identifier', 'Text that identifies the account on its statements, or null.')
    options: dict[str, Any] | None = field('options', 'Trusted reader configuration, or null.')


class BalanceSheetAccount(Strict):
    """Metadata of an Assets or Liabilities account (or another balance-sheet root)."""
    kind: Literal['bank', 'card', 'cash', 'wallet', 'loan', 'investment', 'person'] = field('kind', 'What the account is.')
    currency: Code = field('currency', 'Its currency; a declared commodity.')
    label: Line | None = field('label', 'Display name, or null.')
    aliases: unique(Annotated[str, StringConstraints(pattern=r'^[^A-Z\r\n]+$')]) = field(
        'aliases', 'Lowercase words people use for it.')
    statement: StatementReader | None = field('statement', 'Statement reader settings, or null.')


class ExpenseAccount(Strict):
    """Metadata of an Expenses account."""
    category: Annotated[str, StringConstraints(pattern=rf'^[^\r\n]*[^{WHITESPACE}][^\r\n]*$')] | None = field(
        'category', 'Display category inherited by sub-accounts, or null.')


class PlainAccount(Strict):
    """Income and Equity accounts carry no metadata."""


def account_metadata_model(name: str) -> type[Strict]:
    """The metadata model an account's root needs."""
    root = name.split(':')[0]
    return PlainAccount if root in ('Income', 'Equity') else ExpenseAccount if root == 'Expenses' else BalanceSheetAccount


class Accounts(Strict):
    """data/accounts.json: declared commodities and accounts."""
    commodities: unique(Annotated[str, StringConstraints(min_length=1)]) = field(
        'commodities', 'Commodity directive arguments verbatim, in order.')
    accounts: dict[Account, BalanceSheetAccount | ExpenseAccount | PlainAccount] = field(
        'accounts', 'Declared accounts in order, keyed by name.', schema={
            # Each root's metadata, as the model validator below checks it, for JSON Schema readers (Node).
            'additionalProperties': False, 'patternProperties': {
                r'^(?:Income|Equity)(?::[^\r\n:]+)*$': {'$ref': '#/$defs/PlainAccount'},
                r'^Expenses(?::[^\r\n:]+)*$': {'$ref': '#/$defs/ExpenseAccount'},
                r'^(?!(?:Income|Equity|Expenses)(?::|$))[^\r\n:]+(?::[^\r\n:]+)*$': {'$ref': '#/$defs/BalanceSheetAccount'}}})

    @model_validator(mode='after')
    def _metadata_matches_root(self):
        for name, metadata in self.accounts.items():
            expected = account_metadata_model(name)
            if type(metadata) is not expected:
                raise ValueError(f'{name}: {name.split(":")[0]} accounts need {expected.__name__} metadata')
        return self


class CardStatement(Strict):
    """A filed card statement summary; its rows are the entries whose statement.end is its end."""
    kind: Literal['card'] = field('kind', 'Always "card".')
    id: Id = field('id', 'Permanent random ID.')
    account: Account = field('account', 'The card account.')
    start: Day = field('start', 'First day of the period.')
    end: Day = field('end', 'Last day of the period.')
    source: Annotated[str, StringConstraints(pattern=r'^Bills/[^\r\n]+\.pdf$')] = field('source', 'The filed statement PDF.')
    previous_balance: Cents = field('previous-balance', 'Printed previous balance.')
    purchases: Cents = field('purchases', 'Printed purchases.')
    cash_advances: Cents | None = field('cash-advances', 'Printed cash advances, or null when none.')
    credits: Cents = field('credits', 'Printed payments and credits.')
    total_due: Cents = field('total-due', 'Printed total due.')
    rows: Positive = field('rows', 'Number of rows billed.')


class PageCitation(Strict):
    page: Positive = field('page', 'Page in the statement.')
    line: Positive = field('line', 'Line on the page.')


class SheetCitation(Strict):
    sheet: Annotated[str, StringConstraints(min_length=1)] = field('sheet', 'Worksheet name.')
    row: Positive = field('row', 'Row in the sheet.')


class StatementMember(Strict):
    """One row of a balance-style statement."""
    date: Day = field('date', 'Printed date.')
    serial: Annotated[str, StringConstraints(min_length=1)] = field('serial', 'Row serial.')
    description: Annotated[str, StringConstraints(min_length=1)] = field('description', 'Printed description.')
    amount: Unsigned = field('amount', 'Printed amount.')
    direction: Literal['Debit', 'Credit'] = field('direction', 'Debit or Credit.')
    citation: PageCitation | SheetCitation = field('citation', 'Where the row is printed.')
    reference: str | None = field('reference', 'Printed reference, or null.')
    balance: Amount | None = field('balance', 'Printed running balance, or null.')
    entry_id: Id | None = field('entry-id', 'The entry recorded for it, or null.')
    disposition: Literal['recorded', 'already-in-ledger', 'pre-start', 'overlap-pending'] = field('disposition', 'What Apply did with it.')
    review_candidates: unique(Id) = field('review-candidates', 'Entries to compare in Review.')


class BankStatement(Strict):
    """A filed balance-style statement with its rows."""
    kind: Literal['bank'] = field('kind', 'Always "bank".')
    id: Id = field('id', 'Permanent random ID.')
    account: Account = field('account', 'The bank or card account.')
    start: Day = field('start', 'First day of the period.')
    end: Day = field('end', 'Last day of the period.')
    source: Annotated[str, StringConstraints(pattern=r'^Bills/[^\r\n]+\.[A-Za-z0-9]+$')] = field('source', 'The filed statement.')
    source_balance_style: Literal['debt'] | None = field('source-balance-style', '"debt" for card debt printed as positive balances, or null.')
    opening_balance: Amount = field('opening-balance', 'Printed opening balance.')
    closing_balance: Amount = field('closing-balance', 'Printed closing balance.')
    debits: Unsigned = field('debits', 'Printed debits.')
    credits: Unsigned = field('credits', 'Printed credits.')
    rows: Positive = field('rows', 'Number of rows.')
    members: list[StatementMember] = field('members', 'The rows, in order.', min_length=1)
    assertion_id: Id | None = field('assertion-id', 'The closing-balance entry; null only for debt-style statements.')
    reader: Line = field('reader', 'The reader that read it.')

    @model_validator(mode='after')
    def _assertion(self):
        if self.source_balance_style is None and self.assertion_id is None:
            raise ValueError('a balance-style statement needs assertion-id')
        return self


class RecurringSettings(Strict):
    """data/recurring-settings.json."""
    never_automatic: unique(Account) = field('never-automatic', 'Account prefixes never matched automatically.')


class TrashFile(Strict):
    original: Annotated[str, StringConstraints(pattern=r'^Bills/[^.][^\r\n]*$')] = field('original', 'Where the file was filed.')
    stored: Annotated[str, StringConstraints(pattern=r'^data/trash-files/[a-f0-9]{32}/[^/\r\n]+$')] = field('stored', 'Where Trash keeps it.')


class KeptFile(Strict):
    path: Annotated[str, StringConstraints(pattern=r'^Bills/[^.][^\r\n]*$')] = field('path', 'A file other entries still cite.')
    citations: Positive = field('citations', 'How many other entries cite it.')


class RuleChange(Strict):
    """A rule before and after an operation; null when it did not exist."""
    id: Id = field('id', 'The rule ID.')
    before: Rule | None = field('before', 'The rule before, or null.')
    after: Rule | None = field('after', 'The rule after, or null.')


class TrashItem(Strict):
    """One deleted entry, restorable until its time limit."""
    id: OperationId = field('id', 'The Delete operation ID.')
    entry: Entry = field('entry', 'The entry as it was.')
    removed_at: Moment = field('removed-at', 'When it was deleted.')
    added_at: Moment | None = field('added-at', 'When it was added, or null when unknown.')
    reason: str = field('reason', 'The reason given, or "".')
    index: Annotated[int, Ge(0)] = field('index', 'Its position among same-day entries.')
    files: list[TrashFile] = field('files', 'Evidence only it cited, moved to Trash.')
    kept: list[KeptFile] = field('kept', 'Evidence other entries still cite.')
    rule_changes: list[RuleChange] = field('rule-changes', 'Rules the Delete changed.')
    duplicate_links: dict[Id, list[DuplicateLink]] = field('duplicate-links', 'Duplicate links removed from other entries, by entry ID.')


class ExpenseYear(RootModel[list[Entry]]):
    """data/expenses/<year>.json: one calendar year of entries in date order."""
    model_config = ConfigDict(strict=True)


class Forecast(RootModel[list[Rule]]):
    """data/forecast.json: the recurring rules in order."""
    model_config = ConfigDict(strict=True)


class Statements(RootModel[list[Annotated[CardStatement | BankStatement, Field(discriminator='kind')]]]):
    """data/card-statements.json: filed statements in end-date order; each kind has its own shape."""
    model_config = ConfigDict(strict=True)


class Trash(RootModel[list[TrashItem]]):
    """data/trash.json: deleted entries."""
    model_config = ConfigDict(strict=True)


DOCUMENTS = {
    'data/accounts.json': Accounts,
    'data/expenses/<year>.json': ExpenseYear,
    'data/forecast.json': Forecast,
    'data/card-statements.json': Statements,
    'data/recurring-settings.json': RecurringSettings,
    'data/trash.json': Trash,
}
# Every model whose fields describe ledger data, so every field has a setter.
LEDGER_DATA = (Entry, Posting, Cost, Assertion, StatementRow, References, Money, Reimbursement,
               EstimateExclusion, Extensions, RuleLink, DuplicateLink, Rule, Schedule, Origin, Removal, Bill)


def missing_setters(model: type[BaseModel]) -> list[str]:
    """Fields with no setter, as model.field names."""
    return [f'{model.__name__}.{info.alias or name}' for name, info in model.model_fields.items() if not setters(info)]


# Extensions --------------------------------------------------------------------------

# Labels the core journal already uses; an extension's journal tag must not clash with them.
CORE_JOURNAL_LABELS = frozenset({
    'id', 'forecast item', 'source', 'serial-number', 'source-row', 'source-page', 'statement-page', 'statement-end',
    'reference', 'order-id', 'classification', 'original amount', 'reimbursement-group', 'reimbursement-label',
    'variable-estimate'})
# hledger reads a comment's tag as the word before the colon: "original amount:" is the tag
# "amount" and "forecast item:" is "item". An extension tag may clash with neither form.
CORE_JOURNAL_TAGS = CORE_JOURNAL_LABELS | frozenset(label.split()[-1] for label in CORE_JOURNAL_LABELS)
RESERVED_NAMES = frozenset({'fuel'})  # built-in extensions; a ledger can't define its own with these names
JournalTag = Annotated[str, StringConstraints(pattern=r'^[a-z][a-z0-9]*(-[a-z0-9]+)*$')]


class PlainField(Strict):
    """An extension field of a type without options."""
    type: Literal['decimal', 'text', 'whole-number', 'date', 'yes-no'] = field(
        'type', 'decimal: exact decimal text. text: one line. whole-number: 0 or more. date: YYYY-MM-DD. yes-no: true or false.')
    filled_by: Literal['agent', 'person'] = field(
        'filled-by', 'agent: the Add agent may fill it and the person can edit it. person: only Edit entry sets it.')
    description: Line = field('description', 'What the field holds; the agent reads it for agent fields.')
    journal_tag: JournalTag = field('journal-tag', 'The comment tag the journal prints the value under.')


class ChoiceField(Strict):
    """An extension field whose value is one of a fixed list."""
    type: Literal['choice'] = field('type', 'One of values.')
    filled_by: Literal['agent', 'person'] = field(
        'filled-by', 'agent: the Add agent may fill it and the person can edit it. person: only Edit entry sets it.')
    description: Line = field('description', 'What the field holds; the agent reads it for agent fields.')
    journal_tag: JournalTag = field('journal-tag', 'The comment tag the journal prints the value under.')
    values: Annotated[list[Line], AfterValidator(_unique), JsonExtra(uniqueItems=True), Field(min_length=1)] = field(
        'values', 'The allowed values, in display order.')


class ExtensionDefinition(Strict):
    """extensions/<name>.json: a group of typed fields added to every entry of a ledger that lists it."""
    name: Kebab = field('name', 'The extension name; it matches the file name.')
    description: Line = field('description', 'What the extension is for.')
    fields: Annotated[dict[Kebab, ChoiceField | PlainField], Field(min_length=1)] = field(
        'fields', 'The fields, in display and journal order, keyed by kebab-case name.')

    @model_validator(mode='after')
    def _tags(self):
        tags = [definition.journal_tag for definition in self.fields.values()]
        if len(set(tags)) != len(tags):
            raise ValueError('journal tags must differ')
        clash = sorted(set(tags) & CORE_JOURNAL_TAGS)
        if clash:
            raise ValueError('journal tags clash with core labels: ' + ', '.join(clash))
        return self


def _choice_value(values: tuple[str, ...]):
    def check(value: str) -> str:
        if value not in values:
            raise ValueError('must be one of: ' + ', '.join(values))
        return value
    return check


_TYPES = {'decimal': Number, 'text': Line, 'whole-number': WholeNumber, 'date': Day, 'yes-no': bool, 'choice': str}


def extension_type(definition: PlainField | ChoiceField) -> Any:
    """The Python type of one extension field's value (without null)."""
    if definition.type == 'choice':
        values = tuple(definition.values)
        return Annotated[str, AfterValidator(_choice_value(values)), JsonExtra(enum=list(values))]
    return _TYPES[definition.type]


def extension_types() -> dict[str, dict]:
    """Each extension type's JSON Schema, generated from the Python types (schema/extension-types.json).

    A choice field adds ``"enum": <values>`` to its type's schema.
    """
    return {kind: TypeAdapter(value).json_schema() for kind, value in _TYPES.items()}


def pascal(name: str) -> str:
    return ''.join(part.title() for part in name.split('-'))


def snake(name: str) -> str:
    return 'x_' + name.replace('-', '_')


@cache
def _definition(text: str) -> ExtensionDefinition:
    return ExtensionDefinition.model_validate_json(text)


@dataclass(frozen=True)
class LedgerExtension:
    """An extension a ledger lists, with its definition (as JSON text) and whether the agent is asked to fill it."""
    name: str
    ask: bool
    text: str

    @property
    def definition(self) -> ExtensionDefinition:
        return _definition(self.text)

    @classmethod
    def of(cls, name: str, ask: bool, definition: ExtensionDefinition | dict) -> 'LedgerExtension':
        value = definition if isinstance(definition, dict) else definition.model_dump(mode='json', by_alias=True)
        return cls(name, bool(ask), json.dumps(value, ensure_ascii=False))


def read_definition(path: Path) -> ExtensionDefinition:
    definition = ExtensionDefinition.model_validate_json(path.read_text(encoding='utf-8'))
    if definition.name != path.stem:
        raise ValueError(f'{path.name}: the name must match the file name')
    return definition


def built_in_extension(name: str) -> Path:
    return BUILT_IN_EXTENSIONS / f'{name}.json'


def ledger_extensions(root: Path, listed: dict) -> tuple[LedgerExtension, ...]:
    """The definitions of the extensions ``ledger.json`` lists, in order, checked for clashes."""
    result, owners = [], {}
    for name, settings in listed.items():
        built_in = built_in_extension(name)
        own = Path(root) / 'extensions' / f'{name}.json'
        if name in RESERVED_NAMES:
            if own.exists():
                raise ValueError(f'extensions/{name}.json: "{name}" is a built-in extension; choose another name')
            path = built_in
        else:
            path = own
        if not path.is_file():
            raise ValueError(f'ledger.json lists the extension "{name}", but extensions/{name}.json is missing')
        try:
            definition = read_definition(path)
        except ValueError as error:
            raise ValueError(f'extensions/{name}.json is not a valid extension definition: {error}') from None
        for tag in (item.journal_tag for item in definition.fields.values()):
            if tag in owners:
                raise ValueError(f'The journal tag "{tag}" of extension "{name}" clashes with extension "{owners[tag]}"')
            owners[tag] = name
        result.append(LedgerExtension.of(name, settings['ask'], definition))
    return tuple(result)


def extension_group(definition: ExtensionDefinition) -> type[Group]:
    """The group model of one extension: every field nullable; null when none is known."""
    fields = {snake(name): (extension_type(item) | None, field(
        name, item.description, (A, P) if item.filled_by == 'agent' else (P,),
        schema={'x-journal-tag': item.journal_tag})) for name, item in definition.fields.items()}
    return create_model(pascal(definition.name) + 'Extension', __base__=Group, __doc__=definition.description, **fields)


def _extensions_model(extensions: tuple[LedgerExtension, ...], groups: dict[str, type]) -> type[Strict]:
    if not extensions:
        return Extensions
    fields = {snake(item.name): (groups[item.name] | None, field(item.name, item.definition.description, tuple(
        Setter(setter) for setter in dict.fromkeys(s for f in groups[item.name].model_fields.values() for s in setters(f)))))
        for item in extensions}
    return create_model('Extensions', __base__=Strict, __doc__=Extensions.__doc__, **fields)


@dataclass(frozen=True)
class LedgerModels:
    """One ledger's models: the core with its extensions composed in."""
    extensions: tuple[LedgerExtension, ...]
    entry: type[Strict]
    expense_year: type[RootModel]
    trash: type[RootModel]
    answer: type[Strict]
    form: type[Strict]

    def document(self, kind: str) -> type[BaseModel]:
        """The model of one store document: accounts, expenses, forecast, card-statements, recurring-settings, trash."""
        return {'accounts': Accounts, 'expenses': self.expense_year, 'forecast': Forecast,
                'card-statements': Statements, 'recurring-settings': RecurringSettings, 'trash': self.trash}[kind]

    def empty_extensions(self) -> dict:
        return {item.name: None for item in self.extensions}

    def store_schema(self) -> dict:
        return _store_schema((Accounts, self.expense_year, Forecast, Statements, RecurringSettings, self.trash))

    def answer_schema(self) -> dict:
        return wire_schema(self.answer)


@cache
def _compose(extensions: tuple[LedgerExtension, ...]) -> LedgerModels:
    if not extensions:
        return LedgerModels((), Entry, ExpenseYear, Trash, answer_model(()), form_model(()))
    groups = {item.name: extension_group(item.definition) for item in extensions}
    ledger_extensions_model = _extensions_model(extensions, groups)
    info = FieldInfo.merge_field_infos(Entry.model_fields['extensions'])
    entry = create_model('Entry', __base__=Entry, __doc__=Entry.__doc__, extensions=(ledger_extensions_model, info))
    trash_item = create_model('TrashItem', __base__=TrashItem, __doc__=TrashItem.__doc__,
                              entry=(entry, FieldInfo.merge_field_infos(TrashItem.model_fields['entry'])))
    year = type('ExpenseYear', (RootModel[list[entry]],), {'__doc__': ExpenseYear.__doc__, 'model_config': ConfigDict(strict=True)})
    trash = type('Trash', (RootModel[list[trash_item]],), {'__doc__': Trash.__doc__, 'model_config': ConfigDict(strict=True)})
    return LedgerModels(extensions, entry, year, trash, answer_model(extensions), form_model(extensions))


def models_for(extensions: tuple[LedgerExtension, ...] = ()) -> LedgerModels:
    """The composed models for a ledger's extensions (cached per definition set)."""
    return _compose(tuple(extensions))


# The agent's answer, derived from the entry model ---------------------------------------

def _nested(annotation: Any) -> list[type[BaseModel]]:
    if isinstance(annotation, type) and issubclass(annotation, BaseModel):
        return [annotation]
    return [model for arg in get_args(annotation) for model in _nested(arg)]


def _substitute(annotation: Any, setter: Setter) -> Any:
    if isinstance(annotation, type) and issubclass(annotation, Strict):
        return derive(annotation, setter)
    origin, args = get_origin(annotation), get_args(annotation)
    if origin is None:
        return annotation
    if origin is Annotated:
        return Annotated[(_substitute(args[0], setter), *annotation.__metadata__)]
    replaced = tuple(_substitute(arg, setter) for arg in args)
    if origin in (Union, types.UnionType):
        return Union[replaced]
    return origin[replaced if len(replaced) > 1 else replaced[0]]


@cache
def derive(model: type[Strict], setter: Setter = Setter.AGENT) -> type[Strict]:
    """``model`` restricted to the fields ``setter`` sets, recursively.

    The ``extensions`` field is left out: the answer adds the ledger's asked
    extensions itself. A nested model whose fields are all kept is reused
    unchanged, so its validators and schema name carry over.
    """
    kept = {name: info for name, info in model.model_fields.items()
            if setter.value in setters(info) and not (issubclass(model, Entry) and name == 'extensions')}
    if not kept:
        raise ValueError(f'{model.__name__} has no {setter.value} fields')
    unchanged = kept.keys() == model.model_fields.keys() and all(
        derive(nested, setter) is nested for info in kept.values() for nested in _nested(info.annotation) if issubclass(nested, Strict))
    if unchanged:
        return model
    fields = {name: (_substitute(info.annotation, setter), FieldInfo.merge_field_infos(info)) for name, info in kept.items()}
    return create_model(f'Answer{model.__name__}', __base__=Strict,
                        __doc__=f"{model.__doc__} Only the fields the {setter.value} sets.", **fields)


def _answer_extensions(extensions: tuple[LedgerExtension, ...], *, agent_only: bool) -> type[Strict] | None:
    chosen = [item for item in extensions if item.ask or not agent_only]
    groups = {}
    for item in chosen:
        fields = {name: value for name, value in item.definition.fields.items() if value.filled_by == 'agent' or not agent_only}
        if fields:
            groups[item.name] = extension_group(item.definition.model_copy(update={'fields': fields}))
    if not groups:
        return None
    prefix = 'Answer' if agent_only else 'Form'
    for name, group in groups.items():
        groups[name] = create_model(prefix + group.__name__, __base__=group, __doc__=group.__doc__)
    fields = {snake(name): (group | None, field(name, next(i.definition.description for i in extensions if i.name == name)))
              for name, group in groups.items()}
    return create_model(prefix + 'Extensions', __base__=Strict, __doc__='The extension groups of the entry; each is null when empty.', **fields)


def _with_extensions(entry: type[Strict], extensions_model: type[Strict] | None, name: str) -> type[Strict]:
    fields = {key: (info.annotation, FieldInfo.merge_field_infos(info)) for key, info in entry.model_fields.items()}
    if extensions_model is not None:
        ordered = {}
        for key, value in fields.items():
            if key == 'notes':
                ordered['extensions'] = (extensions_model, field('extensions', "The ledger's extension groups to fill; each is null when unknown."))
            ordered[key] = value
        fields = ordered
    return create_model(name, __base__=Strict, __doc__=entry.__doc__, **fields)


def _envelope(name: str, entry: type[Strict], doc: str, **more: Any) -> type[Strict]:
    return create_model(
        name, __base__=Strict, __doc__=doc,
        status=(Literal['ready', 'question', 'not_expense'], field(
            'status', '"ready": the evidence and words settle the entry. "question": one thing is unsettled. '
            '"not_expense": the draft is not an expense.')),
        question=(str | None, field('question', 'One short question when status is "question", otherwise null.')),
        entry=(entry | None, field('entry', 'The entry, or null when it cannot be given.')),
        new_accounts=(list[Account], field('new-accounts', 'Accounts the entry uses that are not declared yet.')),
        doubts=(list[str], field('doubts', 'Anything the person should check.')),
        attachment_names=(list[Annotated[str, StringConstraints(pattern=r'^[^\r\n/\\]+$')]], field(
            'attachment-names', 'One descriptive base name per attachment, without folder or extension.')),
        explanation=(str | None, field('explanation', 'One short reason when status is "not_expense", otherwise null.')),
        **more,
    )


@cache
def answer_model(extensions: tuple[LedgerExtension, ...] = ()) -> type[Strict]:
    """The Add agent's answer: an envelope around the agent's fields of an entry.

    ``entry.extensions`` holds only the listed extensions with ``ask: true``, each
    with only its agent fields; it is left out when there are none.
    """
    entry = _with_extensions(derive(Entry), _answer_extensions(extensions, agent_only=True), 'AnswerEntry')
    return _envelope('Answer', entry, "The Add agent's answer. The server fills every other entry field when it stores the entry.")


class NewAccount(Strict):
    """Metadata the person chose for a new balance-sheet account in the hand form."""
    kind: Literal['bank', 'card', 'cash', 'wallet', 'loan', 'investment', 'person'] = field('kind', 'What the account is.')
    currency: Code = field('currency', 'Its currency.')


@cache
def form_model(extensions: tuple[LedgerExtension, ...] = ()) -> type[Strict]:
    """The hand form and Edit entry: the answer, with every listed extension's fields and new-account metadata."""
    entry = _with_extensions(derive(Entry), _answer_extensions(extensions, agent_only=False), 'FormEntry')
    return _envelope('Form', entry, 'The hand form and Edit entry: the answer plus every extension field.',
                     account_metadata=(dict[Account, NewAccount], Field(default_factory=dict, alias='account-metadata',
                                       description='Kind and currency for new balance-sheet accounts.')))


# Schema generation -------------------------------------------------------------------

class _Generator(GenerateJsonSchema):
    def field_title_should_be_set(self, schema) -> bool:
        return False


# Keywords strict structured output refuses (OpenAI Structured Outputs, used by Codex --output-schema
# and response_format; Anthropic output_config.format): the wire schema leaves them out, and the
# answer model still checks every one of them when the answer arrives.
WIRE_DROPPED = frozenset({'uniqueItems', 'minimum', 'maximum', 'exclusiveMinimum', 'exclusiveMaximum',
                          'multipleOf', 'minLength', 'maxLength'})


def _tidy(node: Any, *, wire: bool = False) -> Any:
    """Drop generated titles; for the wire schema also drop the x- annotations and WIRE_DROPPED keywords."""
    if isinstance(node, dict):
        return {key: _tidy(value, wire=wire) for key, value in node.items()
                if not (key == 'title' and isinstance(value, str))
                and not (wire and (key.startswith('x-') or key in WIRE_DROPPED))}
    if isinstance(node, list):
        return [_tidy(item, wire=wire) for item in node]
    return node


DOCUMENT_PATHS = tuple(DOCUMENTS)


def _store_schema(models: tuple) -> dict:
    refs, definitions = models_json_schema([(model, 'validation') for model in models], schema_generator=_Generator,
                                           ref_template='#/$defs/{model}')
    documents = {path: refs[(model, 'validation')]['$ref'] for path, model in zip(DOCUMENT_PATHS, models)}
    return {
        '$schema': 'https://json-schema.org/draft/2020-12/schema',
        '$id': 'https://example.invalid/expense-ledger/store.schema.json',
        'title': 'AgentExpense ledger store, format 2',
        'description': 'Generated from scripts/ledger/store_models.py; do not edit. Validate each file with the '
                       '$defs entry x-documents names for it. Every key is present: an empty value is null or []. '
                       'x-set-by lists who writes each ledger field. This is the core schema: a ledger that lists '
                       'extensions composes their groups into $defs.Extensions with schema/extension-types.json. '
                       'docs/STORE.md describes every field.',
        'x-format': FORMAT,
        'x-documents': documents,
        **_tidy(definitions),
    }


def store_schema() -> dict:
    """The core store schema: a ledger without extensions."""
    return _store_schema(tuple(DOCUMENTS.values()))


def wire_schema(model: type[BaseModel]) -> dict:
    schema = model.model_json_schema(schema_generator=_Generator, ref_template='#/$defs/{model}')
    return {'$schema': 'https://json-schema.org/draft/2020-12/schema', **_tidy(schema, wire=True)}


def answer_schema() -> dict:
    """The core answer schema: a ledger that asks the agent for no extension."""
    return wire_schema(answer_model(()))


def extension_schema() -> dict:
    schema = ExtensionDefinition.model_json_schema(schema_generator=_Generator, ref_template='#/$defs/{model}')
    return {'$schema': 'https://json-schema.org/draft/2020-12/schema',
            '$id': 'https://example.invalid/expense-ledger/extension-definition.schema.json',
            # The journal tags an extension field may not use (ExtensionDefinition's check), for Node.
            'x-reserved-journal-tags': sorted(CORE_JOURNAL_TAGS),
            **_tidy(schema)}


def compose_store_schema(core: dict, extensions: list[tuple[str, dict]], types_map: dict) -> dict:
    """A ledger's store schema from the core schema, its extension definitions (as JSON) and the type map.

    Node composes the same way (``composeStoreSchema`` in ledger-manifest.mjs);
    tests check both against the models.
    """
    if not extensions:
        return core
    schema = json.loads(json.dumps(core))
    defs = schema['$defs']
    properties, setters_of = {}, {}
    for name, definition in extensions:
        group = pascal(name) + 'Extension'
        fields = {}
        for key, item in definition['fields'].items():
            value = dict(types_map[item['type']])
            if item['type'] == 'choice':
                value['enum'] = list(item['values'])
            set_by = ['agent', 'person'] if item['filled-by'] == 'agent' else ['person']
            fields[key] = {'anyOf': [value, {'type': 'null'}], 'description': item['description'],
                           'x-journal-tag': item['journal-tag'], 'x-set-by': set_by}
        defs[group] = {'additionalProperties': False, 'description': definition['description'], 'properties': fields,
                       'required': list(fields), 'type': 'object', 'x-group': True}
        setters_of[name] = list(dict.fromkeys(s for value in fields.values() for s in value['x-set-by']))
        properties[name] = {'anyOf': [{'$ref': f'#/$defs/{group}'}, {'type': 'null'}],
                            'description': definition['description'], 'x-set-by': setters_of[name]}
    defs['Extensions'] = {**defs['Extensions'], 'properties': properties, 'required': list(properties)}
    return schema


def generated() -> dict[Path, str]:
    text = lambda value: json.dumps(value, indent=2, ensure_ascii=False) + '\n'
    return {STORE_SCHEMA: text(store_schema()), ANSWER_SCHEMA: text(answer_schema()),
            EXTENSION_SCHEMA: text(extension_schema()), EXTENSION_TYPES: text(extension_types())}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument('--write', action='store_true', help='Write the generated schemas')
    group.add_argument('--check', action='store_true', help='Exit 1 when a checked-in schema is out of date')
    args = parser.parse_args()
    missing = [name for model in LEDGER_DATA for name in missing_setters(model)]
    if missing:
        print('Fields without a setter: ' + ', '.join(missing), file=sys.stderr)
        return 1
    stale = [path for path, content in generated().items() if not path.exists() or path.read_text(encoding='utf-8') != content]
    if args.write:
        for path, content in generated().items():
            path.write_text(content, encoding='utf-8')
        print(f'Wrote {len(generated())} schemas')
        return 0
    for path in stale:
        print(f'{path.relative_to(SCHEMA_DIR.parent)} is out of date; run store_models.py --write', file=sys.stderr)
    return 1 if stale else 0


if __name__ == '__main__':
    sys.exit(main())
