"""Operator Queue/Trash interface; the same checked services as the dashboard."""
import argparse
import json
from pathlib import Path
import secrets
import sys

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from project_paths import APPLICATION_ROOT,ledger_root
sys.path.insert(0,str(APPLICATION_ROOT/'review-dashboard/server'))
from settings import Settings
from drafts import Drafts
from statements import Statements
from retention_service import Retention
from ledger_lock import ledger_lock


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root',type=Path,default=ledger_root())
    sub=parser.add_subparsers(dest='command',required=True)
    sub.add_parser('list')
    status=sub.add_parser('entry');status.add_argument('id')
    preview=sub.add_parser('preview');preview.add_argument('file',type=Path)
    operation=sub.add_parser('operations');operation.add_argument('file',type=Path);operation.add_argument('--op-id',default=None)
    sub.add_parser('sweep')
    args=parser.parse_args();settings=Settings(ledger=args.root.resolve());service=Retention(settings,Drafts(settings.ledger));service.statements=Statements(settings)
    try:
        if args.command=='operations':result=service.operate(args.op_id or secrets.token_hex(16),json.loads(args.file.read_text()))
        elif args.command=='entry':result=service.eligibility(args.id)
        elif args.command=='sweep':service.sweep(force=True);result=dict(swept=True)
        else:
            with ledger_lock(settings.ledger):result=service.listing() if args.command=='list' else service.preview(json.loads(args.file.read_text()))
        print(json.dumps(result,ensure_ascii=False,indent=2))
    except (ValueError,OSError) as exc:parser.exit(1,str(exc)+'\n')


if __name__=='__main__':main()
