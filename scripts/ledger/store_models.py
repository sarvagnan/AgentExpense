"""Ledger format 2: the one definition of the store's shape and the Add agent's answer.

Every store document and the agent's answer are pydantic v2 models in strict mode
with ``extra="forbid"``. Every field is required; a field that can be empty is
``T | None`` (``null``) or a list (``[]``), so a missing key and an extra key are
both errors. Names are the store's kebab-case aliases. Amounts are exact decimal
text, never JSON numbers.

Every field of an entry, a posting and a recurring rule (and of the groups inside
them) records who sets it in ``x-set-by``. The agent's answer model is derived from
the entry model by selecting the fields the agent sets, so the two cannot drift.

The JSON Schemas in ``schema/`` are generated from these models:

    uv run --locked --directory review-dashboard/server python ../../scripts/ledger/store_models.py --write

``--check`` exits non-zero when a checked-in schema is out of date. docs/STORE.md
describes every field. Nothing reads format 2 yet: the live app still reads
format 1 through ``schema/store.schema.json``.
"""
from __future__ import annotations

import argparse
from datetime import date, datetime
from enum import StrEnum
from functools import cache
import json
from pathlib import Path
import sys
import types
from typing import Annotated, Any, Literal, Union, get_args, get_origin

from annotated_types import Ge, Le
from pydantic import AfterValidator, BaseModel, ConfigDict, Field, RootModel, StringConstraints, create_model, model_validator
from pydantic.fields import FieldInfo
from pydantic.json_schema import GenerateJsonSchema, models_json_schema

FORMAT = 2
SCHEMA_DIR = Path(__file__).resolve().parents[2] / 'schema'
STORE_SCHEMA = SCHEMA_DIR / 'store-2.schema.json'
ANSWER_SCHEMA = SCHEMA_DIR / 'answer-2.schema.json'
ANSWER_SCHEMA_WITHOUT_FUEL = SCHEMA_DIR / 'answer-2-without-fuel.schema.json'


class Setter(StrEnum):
    """Who may write a field. A field lists every writer; readers never care."""
    AGENT = 'agent'          # the Add answer; the hand form and Edit entry fill the same answer
    SERVER = 'server'        # derived by the app on write, never taken from an answer
    STATEMENT = 'statement'  # statement Apply (card and balance-style) and the statement CLIs
    PERSON = 'person'        # a person's own action outside the answer: Review, rule actions, setup
    HISTORY = 'history'      # kept from older data; no current writer sets it, writers keep it


A, SV, ST, P, H = Setter.AGENT, Setter.SERVER, Setter.STATEMENT, Setter.PERSON, Setter.HISTORY


def field(alias: str, description: str, set_by: tuple[Setter, ...] | None = None, *,
          module: str | None = None, schema: dict | None = None, **constraints: Any) -> Any:
    """A required field with its kebab-case alias, meaning and, for ledger data, its setters."""
    extra = dict(schema or {})
    if set_by is not None:
        extra['x-set-by'] = [setter.value for setter in set_by]
    if module:
        extra['x-module'] = module
    return Field(alias=alias, description=description, json_schema_extra=extra or None, **constraints)


def setters(info: FieldInfo) -> tuple[str, ...]:
    extra = info.json_schema_extra if isinstance(info.json_schema_extra, dict) else {}
    return tuple(extra.get('x-set-by', ()))


def module_of(info: FieldInfo) -> str | None:
    extra = info.json_schema_extra if isinstance(info.json_schema_extra, dict) else {}
    return extra.get('x-module')


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
Code = Annotated[str, StringConstraints(pattern=r'^[^\s";=@]+$')]
# Exact decimal text. A posting amount always has a decimal point; a cost, an
# original amount or a reading may be a whole number, as it was written.
Amount = Annotated[str, StringConstraints(pattern=r'^-?(0|[1-9][0-9]*)\.[0-9]+$')]
Number = Annotated[str, StringConstraints(pattern=r'^-?(0|[1-9][0-9]*)(\.[0-9]+)?$')]
Unsigned = Annotated[str, StringConstraints(pattern=r'^(0|[1-9][0-9]*)\.[0-9]+$')]
Cents = Annotated[str, StringConstraints(pattern=r'^(0|[1-9][0-9]*)\.[0-9]{2}$')]
Positive = Annotated[int, Ge(1)]


class Strict(BaseModel):
    model_config = ConfigDict(strict=True, extra='forbid', validate_by_alias=True, validate_by_name=False,
                              serialize_by_alias=True)


class Group(Strict):
    """A group of related optional facts: ``null`` when none is known, never all ``null``."""

    @model_validator(mode='after')
    def _not_empty(self):
        if all(getattr(self, name) is None for name in type(self).model_fields):
            raise ValueError('a group with no values must be null')
        return self


# Postings ---------------------------------------------------------------------------

class Cost(Strict):
    """The posting's price in another commodity: the whole price (hledger @@) or the price of one unit (@)."""
    amount: Number = field('amount', 'Exact decimal text in the cost commodity, as written.', (A, ST))
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
    reference: Line | None = field('reference', 'A payment or bank reference printed on the evidence.', (A, ST))
    order_id: Line | None = field('order-id', "The payee's order or invoice number.", (A,))


class Money(Strict):
    """An amount in a commodity."""
    amount: Number = field('amount', 'Exact decimal text, as printed.', (A, ST))
    commodity: Code = field('commodity', 'The commodity code.', (A, ST))


class Fuel(Group):
    """Vehicle readings from a fuel receipt (fuel module only)."""
    litres: Number | None = field('litres', 'Fuel filled, in litres, exact decimal text.', (A,))
    trip_km: Number | None = field('trip-km', 'Trip meter reading in kilometres, exact decimal text.', (A,))


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
    fuel: Fuel | None = field('fuel', 'Fuel readings, or null.', (A,), module='fuel')
    reimbursement: Reimbursement | None = field('reimbursement', 'The reimbursement this entry belongs to, or null.', (H,))
    policy_term_years: Positive | None = field('policy-term-years', 'Years a policy payment covers, or null.', (H,))
    excluded_from_estimate: EstimateExclusion | None = field('excluded-from-estimate', 'Left out of the variable-spending estimate, or null.', (H,))
    rule: RuleLink | None = field('rule', 'The recurring rule occurrence it pays, or null.', (SV, P))
    possible_duplicate_of: list[DuplicateLink] = field('possible-duplicate-of', 'Unsettled possible duplicates, sorted by ID.', (ST, P, SV))
    notes: list[Note] = field('notes', 'Comment lines, in order.', (A, ST, P, SV))
    postings: list[Posting] = field('postings', 'At least one posting, in order.', (A, ST, P), min_length=1)


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
    policy_term_years: Positive | None = field('policy-term-years', 'Years a policy payment covers, or null.', (H,))
    excluded_from_estimate: EstimateExclusion | None = field('excluded-from-estimate', 'Left out of the variable-spending estimate, or null.', (H,))
    notes: list[Note] = field('notes', 'Comment lines, in order.', (P,))
    postings: list[Posting] = field('postings', 'The estimated postings of one occurrence.', (P, SV), min_length=1)


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
    category: Annotated[str, StringConstraints(pattern=r'^[^\r\n]*\S[^\r\n]*$')] | None = field(
        'category', 'Display category inherited by sub-accounts, or null.')


class PlainAccount(Strict):
    """Income and Equity accounts carry no metadata."""


class Accounts(Strict):
    """data/accounts.json: declared commodities and accounts."""
    commodities: unique(Annotated[str, StringConstraints(min_length=1)]) = field(
        'commodities', 'Commodity directive arguments verbatim, in order.')
    accounts: dict[Account, BalanceSheetAccount | ExpenseAccount | PlainAccount] = field(
        'accounts', 'Declared accounts in order, keyed by name.', schema={'additionalProperties': False})

    @model_validator(mode='after')
    def _metadata_matches_root(self):
        for name, metadata in self.accounts.items():
            root = name.split(':')[0]
            expected = PlainAccount if root in ('Income', 'Equity') else ExpenseAccount if root == 'Expenses' else BalanceSheetAccount
            if type(metadata) is not expected:
                raise ValueError(f'{name}: {root} accounts need {expected.__name__} metadata')
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


class Statements(RootModel[list[CardStatement | BankStatement]]):
    """data/card-statements.json: filed statements in end-date order."""
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
LEDGER_DATA = (Entry, Posting, Cost, Assertion, StatementRow, References, Money, Fuel, Reimbursement,
               EstimateExclusion, RuleLink, DuplicateLink, Rule, Schedule, Origin, Removal, Bill)


def missing_setters(model: type[BaseModel]) -> list[str]:
    """Fields with no setter, as model.field names."""
    return [f'{model.__name__}.{info.alias or name}' for name, info in model.model_fields.items() if not setters(info)]


# The agent's answer, derived from the entry model ---------------------------------------

def _nested(annotation: Any) -> list[type[BaseModel]]:
    if isinstance(annotation, type) and issubclass(annotation, BaseModel):
        return [annotation]
    return [model for arg in get_args(annotation) for model in _nested(arg)]


def _substitute(annotation: Any, modules: frozenset[str]) -> Any:
    if isinstance(annotation, type) and issubclass(annotation, Strict):
        return derive(annotation, modules)
    origin, args = get_origin(annotation), get_args(annotation)
    if origin is None:
        return annotation
    if origin is Annotated:
        return Annotated[(_substitute(args[0], modules), *annotation.__metadata__)]
    replaced = tuple(_substitute(arg, modules) for arg in args)
    if origin in (Union, types.UnionType):
        return Union[replaced]
    return origin[replaced if len(replaced) > 1 else replaced[0]]


@cache
def derive(model: type[Strict], modules: frozenset[str] = frozenset({'fuel'}), setter: Setter = Setter.AGENT) -> type[Strict]:
    """``model`` restricted to the fields ``setter`` sets, recursively.

    A field of a module that is off is left out. A nested model whose fields are
    all kept is reused unchanged, so its validators and schema name carry over.
    """
    kept = {name: info for name, info in model.model_fields.items()
            if setter.value in setters(info) and (module_of(info) is None or module_of(info) in modules)}
    if not kept:
        raise ValueError(f'{model.__name__} has no {setter.value} fields')
    unchanged = kept.keys() == model.model_fields.keys() and all(
        derive(nested, modules, setter) is nested for info in kept.values() for nested in _nested(info.annotation) if issubclass(nested, Strict))
    if unchanged:
        return model
    fields = {}
    for name, info in kept.items():
        copy = FieldInfo.merge_field_infos(info)
        fields[name] = (_substitute(info.annotation, modules), copy)
    dropped = sorted({module_of(info) for info in model.model_fields.values() if module_of(info) and module_of(info) not in modules})
    suffix = ''.join('Without' + module.title() for module in dropped)
    return create_model(f'Answer{model.__name__}{suffix}', __base__=Strict,
                        __doc__=f"{model.__doc__} Only the fields the {setter.value} sets.", **fields)


@cache
def answer_model(fuel: bool = True) -> type[Strict]:
    """The Add agent's answer: an envelope around the agent's fields of an entry."""
    entry = derive(Entry, frozenset({'fuel'}) if fuel else frozenset())
    return create_model(
        'Answer' if fuel else 'AnswerWithoutFuel', __base__=Strict,
        __doc__="The Add agent's answer. The server fills every other entry field when it stores the entry.",
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
    )


# Schema generation -------------------------------------------------------------------

class _Generator(GenerateJsonSchema):
    def field_title_should_be_set(self, schema) -> bool:
        return False


def _tidy(node: Any, *, wire: bool = False) -> Any:
    """Drop generated titles; for the wire schema also drop the x- annotations."""
    if isinstance(node, dict):
        return {key: _tidy(value, wire=wire) for key, value in node.items()
                if not (key == 'title' and isinstance(value, str)) and not (wire and key.startswith('x-'))}
    if isinstance(node, list):
        return [_tidy(item, wire=wire) for item in node]
    return node


def store_schema() -> dict:
    names = list(DOCUMENTS.values())
    refs, definitions = models_json_schema([(model, 'validation') for model in names], schema_generator=_Generator,
                                           ref_template='#/$defs/{model}')
    documents = {path: refs[(model, 'validation')]['$ref'] for path, model in DOCUMENTS.items()}
    return {
        '$schema': 'https://json-schema.org/draft/2020-12/schema',
        '$id': 'https://example.invalid/expense-ledger/store-2.schema.json',
        'title': 'AgentExpense ledger store, format 2',
        'description': 'Generated from scripts/ledger/store_models.py; do not edit. Validate each file with the '
                       '$defs entry x-documents names for it. Every key is present: an empty value is null or []. '
                       'x-set-by lists who writes each ledger field. docs/STORE.md describes every field.',
        'x-format': FORMAT,
        'x-documents': documents,
        **_tidy(definitions),
    }


def answer_schema(fuel: bool = True) -> dict:
    schema = answer_model(fuel).model_json_schema(schema_generator=_Generator, ref_template='#/$defs/{model}')
    return {'$schema': 'https://json-schema.org/draft/2020-12/schema', **_tidy(schema, wire=True)}


def generated() -> dict[Path, str]:
    text = lambda value: json.dumps(value, indent=2, ensure_ascii=False) + '\n'
    return {STORE_SCHEMA: text(store_schema()), ANSWER_SCHEMA: text(answer_schema(True)),
            ANSWER_SCHEMA_WITHOUT_FUEL: text(answer_schema(False))}


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
