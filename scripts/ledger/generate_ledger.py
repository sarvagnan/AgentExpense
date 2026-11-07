"""Pure JSON-to-hledger formatter. No statements, workbooks or Bills are read."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from store import ROOT, entries, extensions_of, load

HEADER = """; AgentExpense ledger
; Generated from the JSON store in data/expenses/ and data/forecast.json.
; Edit the JSON data, then run make verify. Declarations are hand-kept in data/accounts.json.
; Transactions marked ! need classification review; original descriptions and evidence are retained.
; Forecast rules are commitments and affect reports only when --forecast is used.
; Mutual fund accounts hold units at cost; included AMFI prices provide market valuations (-V).
; Other investment balances represent contributions, not full account valuations.
; Comments are plain text; IDs are persistent store identities.
;
; Useful commands:
;   hledger -f expense_ledger.ledger check -s ordereddates
;   hledger -f expense_ledger.ledger bal Expenses -M --tree
;   hledger -f expense_ledger.ledger is -M
;   hledger -f expense_ledger.ledger register 'status:!'
;   hledger -f expense_ledger.ledger bal 'Mutual Funds' -V
"""


def commodity(symbol: str) -> str:
    # hledger requires digit-bearing commodity names to be quoted.
    return symbol if symbol.isalpha() else json.dumps(symbol, ensure_ascii=False)


def amount(value: dict) -> str:
    return f"{value['amount']} {commodity(value['commodity'])}"


def period(schedule: dict) -> str:
    unit, interval = schedule["every"], schedule.get("interval") or 1
    cadence = {"day": "daily", "week": "weekly", "month": "monthly", "year": "yearly"}[unit] if interval == 1 else f"every {interval} {unit}s"
    anchor = schedule.get('anchor-day')
    if unit == 'month' and interval == 1 and anchor and anchor != int(schedule['from'][-2:]):
        # Explicit day-of-month syntax keeps an advanced February occurrence's
        # boundary and restores its 29th–31st anchor in following months.
        cadence = f'every {anchor}th day of month'
    return f"{cadence} from {schedule['from']}" + (f" to {schedule['to']}" if 'to' in schedule else '')


def forecast_templates(rule):
    """The bill replaces one occurrence; later occurrences keep the estimate."""
    from recurring import advance, occurrence, periodic_schedules, step_months
    from copy import deepcopy
    from datetime import date, timedelta
    current = rule
    if rule['bill']:
        bill = rule['bill']; day = bill['pay-by'] or bill['occurrence']
        one = deepcopy(rule)
        for p in one['postings']:
            p['amount'] = ('-' if p['amount'].startswith('-') else '') + bill['amount']
        one['evidence'] = bill['evidence']
        one['schedule'] = {'every': 'day', 'from': day, 'to': (date.fromisoformat(day) + timedelta(days=1)).isoformat()}
        yield one
        current = advance(rule, bill['occurrence'])
    schedules=periodic_schedules(current)
    if rule['bill'] and any(s['from'] < current['date'] for s in schedules):
        # A clamped multi-month cadence ordinarily uses an earlier reference
        # date. A saved bill must never re-emit that paid/replaced occurrence.
        # Use unbounded calendar phases, all starting at or after the next date.
        from math import gcd
        step=step_months(current);phases=12//gcd(step,12)
        schedules=[]
        for index in range(phases):
            phase={**current,'date':occurrence(current,index),'schedule':{'every':'year','interval':step*phases//12,
                'from':occurrence(current,index),'anchor-day':current['schedule'].get('anchor-day') or int(current['date'][-2:])}}
            schedules.extend(periodic_schedules(phase))
    for schedule in schedules:
        yield {**current, 'schedule': schedule}


def tags(entry: dict, extensions: tuple = ()) -> list[tuple[str, str]]:
    """The labelled comment lines of an entry or rule, in journal order: (label, text)."""
    result = []
    row = entry.get('statement')
    if row:
        for key, label in (('serial', 'serial-number'), ('source-row', 'source-row'), ('source-page', 'source-page'),
                           ('page', 'statement-page'), ('end', 'statement-end')):
            if row[key] is not None:
                result.append((label, str(row[key])))
    references = entry['references']
    if references:
        if references['reference'] is not None:
            result.append(('reference', references['reference']))
        result.extend(('order-id', value) for value in references['order-id'])
    if entry['classification'] is not None:
        result.append(('classification', entry['classification']))
    if entry['original-amount']:
        result.append(('original amount', f"{entry['original-amount']['amount']} {entry['original-amount']['commodity']}"))
    groups = entry.get('extensions') or {}
    for extension in extensions:
        values = groups.get(extension.name)
        if not values:
            continue
        for name, field in extension.definition.fields.items():
            if values.get(name) is not None:
                result.append((field.journal_tag, extension_text(values[name])))
    if entry['reimbursement']:
        result.append(('reimbursement-group', entry['reimbursement']['group']))
        if entry['reimbursement']['label'] is not None:
            result.append(('reimbursement-label', entry['reimbursement']['label']))
    if entry['excluded-from-estimate']:
        result.append(('variable-estimate', f"excluded, {entry['excluded-from-estimate']['reason']}"))
    return result


def extension_text(value) -> str:
    """How the journal prints an extension value: yes-no as yes or no, everything else as written."""
    return ('yes' if value else 'no') if isinstance(value, bool) else str(value)


def comments(entry: dict, extensions: tuple = ()) -> list[str]:
    lines = [f"id: {entry['id']}"]
    if "schedule" in entry:
        lines.append(f"forecast item: {entry['description']}")
    lines.extend(f"source: {source}" for source in entry["evidence"])
    lines.extend(f"{label}: {text}" for label, text in tags(entry, extensions))
    lines.extend(entry["notes"])
    return lines


def render_entry(entry: dict, extensions: tuple = ()) -> list[str]:
    header = "~ " + period(entry["schedule"]) if "schedule" in entry else f"{entry['date']} {'*' if entry['status'] == 'confirmed' else '!'} {entry['description']}"
    lines = [header, *(f"    ; {comment}" for comment in comments(entry, extensions))]
    width = max(len(p["account"]) for p in entry["postings"]) + 4
    for posting in entry["postings"]:
        text = f"    {posting['account']:<{width}}{amount(posting)}"
        if posting["cost"]:
            text += (" @@ " if posting["cost"]["per"] == "total" else " @ ") + amount(posting["cost"])
        if posting["assert"]:
            text += " = " + amount(posting["assert"])
        lines.append(text)
        lines.extend(f"    ; {note}" for note in posting["notes"])
    return [*lines, ""]


def render(documents: dict, root: Path = ROOT, *, actual_lines=None) -> str:
    """The whole journal. ``actual_lines(entry)`` may replace how an actual entry renders
    (the 1 → 2 migration compares with format 1's own rendering)."""
    declarations = documents[root / "data/accounts.json"]
    lines = [HEADER.rstrip(), "", *("commodity " + x for x in declarations["commodities"]), "", *("account " + x for x in declarations["accounts"]), ""]
    extensions = extensions_of(root)
    for entry in entries(documents):
        lines.extend(actual_lines(entry) if actual_lines else render_entry(entry, extensions))
    from ledger_manifest import read_manifest, module_settings
    if module_settings(read_manifest(root))["investments"] is not False and (root / "data/prices/mutual-funds.prices").exists():
        lines.extend([
        "; Mutual fund NAVs in INR from AMFI.",
        "; Valuation uses the latest NAV on or before each date.",
        "include data/prices/mutual-funds.prices", "",
        ])
    lines.extend([
        "; User-confirmed recurring commitments used by hledger --forecast.",
        "; These are forecast-only commitments, not historical transactions.", "",
    ])
    for rule in documents[root / "data/forecast.json"]:
        from recurring import active
        if active(rule):
            for template in forecast_templates(rule):
                lines.extend(render_entry(template))
    return "\n".join(lines).rstrip() + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT, help="Ledger repository root")
    root = parser.parse_args().root.resolve()
    from ledger_manifest import read_manifest
    from ledger_lock import ledger_lock
    read_manifest(root)
    from step_timings import timed
    with ledger_lock(root), timed("journal"):
        documents = load(root)
        (root / "expense_ledger.ledger").write_text(render(documents, root), encoding="utf-8")
    print(f"Generated {len(entries(documents))} entries and {len(documents[root / 'data/forecast.json'])} forecast rules")


if __name__ == "__main__":
    main()
