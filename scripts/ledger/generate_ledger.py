"""Pure JSON-to-hledger formatter. No statements, workbooks or Bills are read."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from store import ROOT, FIELD_LABELS, entries, load

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
    unit, interval = schedule["every"], schedule.get("interval", 1)
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
    if rule.get('bill'):
        bill = rule['bill']; day = bill.get('pay_by', bill['occurrence'])
        one = deepcopy(rule)
        for p in one['postings']:
            p['amount'] = ('-' if p['amount'].startswith('-') else '') + bill['amount']
        one['sources'] = bill['sources']
        one['schedule'] = {'every': 'day', 'from': day, 'to': (date.fromisoformat(day) + timedelta(days=1)).isoformat()}
        yield one
        current = advance(rule, bill['occurrence'])
    schedules=periodic_schedules(current)
    if rule.get('bill') and any(s['from'] < current['date'] for s in schedules):
        # A clamped multi-month cadence ordinarily uses an earlier reference
        # date. A saved bill must never re-emit that paid/replaced occurrence.
        # Use unbounded calendar phases, all starting at or after the next date.
        from math import gcd
        step=step_months(current);phases=12//gcd(step,12)
        schedules=[]
        for index in range(phases):
            phase={**current,'date':occurrence(current,index),'schedule':{'every':'year','interval':step*phases//12,
                'from':occurrence(current,index),'anchor-day':current['schedule'].get('anchor-day',int(current['date'][-2:]))}}
            schedules.extend(periodic_schedules(phase))
    for schedule in schedules:
        yield {**current, 'schedule': schedule}


def comments(entry: dict) -> list[str]:
    lines = [f"id: {entry['id']}"]
    if "schedule" in entry:
        lines.append(f"forecast item: {entry['description']}")
    lines.extend(f"source: {source}" for source in entry.get("sources", []))
    lines.extend(f"{label}: {field_text(field, entry[field])}" for field, label in FIELD_LABELS.items() if field in entry)
    lines.extend(entry.get("notes", []))
    return lines


def field_text(field: str, value) -> str:
    """Render structured facts with their original journal comment spelling."""
    if field == "original-amount":
        return f"{value['amount']} {value['commodity']}"
    if field == "variable-estimate":
        return f"excluded, {value['reason']}"
    if field == "policy-term":
        years = value["years"]
        return f"{years} {'year' if years == 1 else 'years'}"
    return str(value)


def render_entry(entry: dict) -> list[str]:
    header = "~ " + period(entry["schedule"]) if "schedule" in entry else f"{entry['date']} {'*' if entry['status'] == 'confirmed' else '!'} {entry['description']}"
    lines = [header, *(f"    ; {comment}" for comment in comments(entry))]
    width = max(len(p["account"]) for p in entry["postings"]) + 4
    for posting in entry["postings"]:
        text = f"    {posting['account']:<{width}}{amount(posting)}"
        if "cost" in posting:
            text += (" @@ " if posting["cost"]["per"] == "total" else " @ ") + amount(posting["cost"])
        if "assert" in posting:
            text += " = " + amount(posting["assert"])
        lines.append(text)
        lines.extend(f"    ; {note}" for note in posting.get("notes", []))
    return [*lines, ""]


def render(documents: dict, root: Path = ROOT) -> str:
    declarations = documents[root / "data/accounts.json"]
    lines = [HEADER.rstrip(), "", *("commodity " + x for x in declarations["commodities"]), "", *("account " + x for x in declarations["accounts"]), ""]
    for entry in entries(documents):
        lines.extend(render_entry(entry))
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
