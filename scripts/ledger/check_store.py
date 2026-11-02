"""Check schema, identities, chronology, canonical bytes, evidence paths,
card-statement membership and balance chains, forecasts and fund-price coverage.
"""
from datetime import date, timedelta
from collections import defaultdict
import os
from pathlib import Path
import shutil
import shlex
import subprocess
from decimal import Decimal

from store import ROOT, canonical_text, entries, load, read_json
from audit import records, missing_entries
from ledger_manifest import read_manifest, module_settings, price_source


def check_card_statements(documents: dict, root: Path = ROOT) -> dict:
    """Reconcile explicit billed membership, independently of transaction dates.

    Card liabilities are negative: a negative posting is a statement debit
    (purchase), and a positive posting is a statement credit (payment/refund).
    Opening equity and assertion-only entries are not unbilled activity.
    """
    actuals = entries(documents)
    statements = [s for s in documents[root / "data/card-statements.json"] if s.get("kind") != "bank"]
    declared = set(documents[root / "data/accounts.json"]["accounts"])
    by_key = {}
    by_account = defaultdict(list)
    members = defaultdict(list)
    serials = {}
    for statement in statements:
        account, end = statement["account"], statement["end"]
        key = (account, end)
        if account not in declared:
            raise ValueError(f"{end}: undeclared statement account {account}")
        if documents[root / "data/accounts.json"]["accounts"][account].get("kind") != "card":
            raise ValueError(f"{end}: statement account must have kind card: {account}")
        if key in by_key:
            raise ValueError(f"{end}: duplicate statement for {account}")
        by_key[key] = statement
        by_account[account].append(statement)
    for entry in actuals:
        serial = entry.get("serial-number")
        if serial is not None:
            if serial in serials:
                raise ValueError(f"Duplicate serial {serial}: {serials[serial]} and {entry['id']}")
            serials[serial] = entry["id"]
        if "statement-end" not in entry:
            continue
        card_accounts = {p["account"] for p in entry["postings"] if documents[root / "data/accounts.json"]["accounts"].get(p["account"], {}).get("kind") == "card"}
        if not card_accounts:
            raise ValueError(f"{entry['id']}: statement-end without a card posting")
        if len(card_accounts) != 1:
            raise ValueError(f"{entry['id']}: statement-end must identify exactly one card account")
        if sum(p["account"] in card_accounts for p in entry["postings"]) != 1:
            raise ValueError(f"{entry['id']}: statement member must have exactly one card posting")
        key = (next(iter(card_accounts)), entry["statement-end"])
        if key not in by_key:
            raise ValueError(f"{entry['id']}: unknown statement-end {key[1]} for {key[0]}")
        if not serial:
            raise ValueError(f"{entry['id']}: statement member missing serial-number")
        if "statement-page" not in entry:
            raise ValueError(f"{entry['id']}: statement member missing statement-page")
        if by_key[key]["source"] not in entry.get("sources", []):
            raise ValueError(f"{entry['id']}: statement member missing its statement PDF source")
        members[key].append(entry)
    results = []
    unbilled = []
    for account, filed in sorted(by_account.items()):
        currency = documents[root / 'data/accounts.json']['accounts'][account]['currency']
        filed.sort(key=lambda statement: statement["end"])
        openings = [entry for entry in actuals if entry["date"] <= filed[0]["start"]
                    and any(p["account"].startswith("Equity:Opening Balances") for p in entry["postings"])
                    and any(p["account"] == account for p in entry["postings"])]
        if len(openings) != 1:
            raise ValueError(f"{account}: expected one opening-equity entry, found {len(openings)}")
        opening_postings = [p for p in openings[0]["postings"] if p["account"] == account]
        if any(p["commodity"] != currency for p in opening_postings):
            raise ValueError(f"{account}: opening balance must be {currency}")
        previous = -sum((Decimal(p["amount"]) for p in opening_postings), Decimal(0))
        for statement in filed:
            end = statement["end"]
            rows = members[(account, end)]
            if len(rows) != statement["rows"]:
                raise ValueError(f"{end}: row count {len(rows)} != {statement['rows']} for {account}")
            postings = [p for entry in rows for p in entry["postings"] if p["account"] == account]
            if any(p["commodity"] != currency for p in postings):
                raise ValueError(f"{end}: statement card postings must be {currency}")
            amounts = [Decimal(p["amount"]) for p in postings]
            purchases = -sum((a for a in amounts if a < 0), Decimal(0))
            credits = sum((a for a in amounts if a > 0), Decimal(0))
            for label, actual in (("purchases", purchases), ("credits", credits)):
                if actual != Decimal(statement[label]):
                    raise ValueError(f"{end}: {label} {actual} != {statement[label]}")
            if Decimal(statement["previous-balance"]) != previous:
                raise ValueError(f"{end}: broken statement chain: previous-balance {statement['previous-balance']} != {previous}")
            due = previous + purchases + Decimal(statement.get("cash-advances", "0")) - credits
            if due != Decimal(statement["total-due"]):
                raise ValueError(f"{end}: summary total-due {statement['total-due']} != {due}")
            previous = Decimal(statement["total-due"])
            results.append(dict(account=account, start=statement["start"], end=end, rows=len(rows),
                                purchases=str(purchases), credits=str(credits), totalDue=str(previous), result="PASS"))
        latest = filed[-1]["end"]
        for entry in actuals:
            if "statement-end" in entry or entry["date"] > latest:
                continue
            if any(p["account"].startswith("Equity:Opening Balances") for p in entry["postings"]):
                continue
            if all(Decimal(p["amount"]) == 0 and "assert" in p for p in entry["postings"]):
                continue
            card = [p for p in entry["postings"] if p["account"] == account]
            if card:
                unbilled.append(dict(id=entry["id"], account=account, date=entry["date"],
                                     description=entry["description"], currency=documents[root / "data/accounts.json"]["accounts"][account]["currency"], amount=str(sum((Decimal(p["amount"]) for p in card), Decimal(0)))))
    from check_bank_statements import check_bank_statements
    results.extend(check_bank_statements(documents, root))
    return dict(statements=results, unbilled=unbilled)


def check_price_coverage(documents: dict, root: Path = ROOT) -> None:
    """Unit holdings opened at a different-currency cost need an initial NAV.

    This replaces the builder's initial fund-price coverage check without a
    hard-coded fund list. Manual prices are checked even without a fetch source;
    only configured sources require initial coverage. hledger also checks syntax
    and declarations during verification.
    """
    manifest = read_manifest(root)
    if module_settings(manifest)["investments"] is False:
        return
    first_held = {}
    for entry in entries(documents):
        for posting in entry["postings"]:
            if documents[root / "data/accounts.json"]["accounts"].get(posting["account"], {}).get("kind") == "investment" and "cost" in posting and posting["commodity"] != posting["cost"]["commodity"] and Decimal(posting["amount"]) != 0:
                first_held.setdefault(posting["commodity"], entry["date"])
    prices = {}
    price_file = root / "data/prices/mutual-funds.prices"
    for line in (price_file.read_text(encoding="utf-8") if price_file.exists() else "").splitlines():
        if line and not line.startswith(";"):
            parts = shlex.split(line)
            if len(parts) < 5 or parts[0] != "P":
                raise ValueError(f"Unrecognized price directive: {line!r}")
            prices.setdefault(parts[2], []).append(parts[1])
    if not price_source(manifest):
        return
    for commodity, first_day in first_held.items():
        if not any(day <= first_day for day in prices.get(commodity, [])):
            raise ValueError(f"{commodity}: no price on or before first unit holding {first_day}")


def check_forecasts(documents: dict, root: Path = ROOT) -> None:
    """hledger check alone does not expand or balance periodic templates.

    Check each first occurrence in isolation so historical forecasts cannot
    interfere with actual statement assertions. All input comes from the store.
    """
    from generate_ledger import render_entry
    hledger = os.environ.get("HLEDGER_BIN") or shutil.which("hledger") or "/opt/homebrew/bin/hledger"
    declarations = documents[root / "data/accounts.json"]
    prefix = [*("commodity " + c for c in declarations["commodities"]), *("account " + a for a in declarations["accounts"]), ""]
    for rule in documents[root / "data/forecast.json"]:
        start = rule["schedule"]["from"]
        end = (date.fromisoformat(start) + timedelta(days=1)).isoformat()
        journal = "\n".join([*prefix, *render_entry(rule)]) + "\n"
        result = subprocess.run([hledger, "-f", "-", "check", "-s", "ordereddates", f"--forecast={start}..{end}"], input=journal, text=True, capture_output=True)
        if result.returncode:
            raise ValueError(f"Forecast rule {rule['id']} ({rule['description']}) fails strict hledger validation:\n{result.stderr}")


def check(root: Path = ROOT) -> None:
    documents = load(root)
    if module_settings(read_manifest(root))['fuel']:
        fuel = read_json(root / 'data/fuel-log.json')
        if not isinstance(fuel, dict) or not isinstance(fuel.get('entries'), list):
            raise ValueError('data/fuel-log.json needs an entries array')
    elif (root / 'data/fuel-log.json').exists():
        print('WARNING: fuel is off; data/fuel-log.json is ignored')
    manifest_path = root / 'ledger.json'
    if manifest_path.read_bytes() != canonical_text(read_json(manifest_path)).encode('utf-8'):
        raise ValueError('ledger.json is not canonical; run make format')
    audit_records = records(root)
    print(f"Verified {len(audit_records)} audit lines")
    for line, identity in missing_entries(root, {e["id"] for e in entries(documents)}):
        print(f"WARNING: audit {line} references missing entry {identity}")
    evidence = set()
    for path, document in documents.items():
        if path.read_bytes() != canonical_text(document).encode("utf-8"):
            raise ValueError(f"{path.relative_to(root)} is not canonical; run make format")
        if path.name in ("accounts.json", "recurring-settings.json"):
            continue
        for entry in document:
            sources = [entry["source"]] if path.name == "card-statements.json" else entry.get("sources", [])
            for source in sources:
                if source.startswith("Bills/"):
                    if ".." in Path(source).parts or not (root / source).is_file():
                        raise ValueError(f"{entry['id']}: missing or invalid evidence path {source!r}")
                    evidence.add(source)
    membership = check_card_statements(documents, root)
    print(f"Verified {len(membership['statements'])} account statements by membership ({sum(s['rows'] for s in membership['statements'])} rows)")
    print(f"Unbilled card entries through latest statement end: {len(membership['unbilled'])}")
    for entry in membership["unbilled"]:
        print(f"  unbilled {entry['date']} {entry['id']} {entry['amount']} {entry['currency']} — {entry['description']}")
    check_forecasts(documents, root)
    check_price_coverage(documents, root)
    print(f"Verified {len(entries(documents))} actual entries, {len(documents[root / 'data/forecast.json'])} forecast rules, {len(evidence)} Bills paths and {len(documents) + 1} canonical files")


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT, help="Ledger repository root")
    check(parser.parse_args().root.resolve())
