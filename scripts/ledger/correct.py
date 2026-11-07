"""Inspect and correct a saved entry through the dashboard's checked service."""
import argparse
import json
from pathlib import Path
import secrets
import sys

sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'review-dashboard/server'))
from settings import Settings
from corrections import apply, wire_entry
from replacements import entry_hash
from store import load, entries
from ledger_lock import ledger_lock
from project_paths import ledger_root


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root',type=Path,default=ledger_root())
    parser.add_argument('entry_id')
    parser.add_argument('--file',type=Path,help='Correction JSON; omit to print a prefilled proposal and version')
    parser.add_argument('--op-id',default=None)
    args=parser.parse_args();settings=Settings.from_env();settings=__import__('dataclasses').replace(settings,ledger=args.root.resolve())
    if args.file:
        result=apply(settings,args.entry_id,args.op_id or secrets.token_hex(16),json.loads(args.file.read_text()))
    else:
        with ledger_lock(settings.ledger):
            entry=next(e for e in entries(load(settings.ledger)) if e['id']==args.entry_id)
            result=dict(version=entry_hash(entry),proposal=wire_entry(settings,entry),sources=entry['evidence'],reason='',approved_accounts=[])
    print(json.dumps(result,ensure_ascii=False,indent=2))


if __name__=='__main__': main()
