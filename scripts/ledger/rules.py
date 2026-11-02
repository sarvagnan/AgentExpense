"""Operator interface for recurring rules, using the HTTP service's checked writes.

Run with the installed server environment (or the make targets). No adapter,
agent gate, credential or model is used by these commands.
"""
import argparse
import json
from pathlib import Path
import secrets
import sys

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from project_paths import APPLICATION_ROOT, ledger_root
sys.path.insert(0,str(APPLICATION_ROOT/'review-dashboard/server'))
from settings import Settings
from ledger_lock import ledger_lock
import rules_service


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root',type=Path,default=ledger_root())
    sub=parser.add_subparsers(dest='command',required=True)
    sub.add_parser('list')
    matching=sub.add_parser('match');matching.add_argument('text');matching.add_argument('--today');matching.add_argument('--currency')
    operation=sub.add_parser('operations');operation.add_argument('file',type=Path,help='JSON operation body');operation.add_argument('--op-id',default=None)
    for name in ('backfill','catch-up'):
        p=sub.add_parser(name);p.add_argument('--op-id',default=None)
    undo=sub.add_parser('undo');undo.add_argument('audit_id');undo.add_argument('--op-id',default=None)
    args=parser.parse_args(argv)
    settings=Settings(ledger=args.root.resolve(),state_dir=args.root.resolve()/'drafts/unused-agent-state')
    try:
        if args.command in ('list','match'):
            with ledger_lock(settings.ledger):
                result=rules_service.snapshot(settings) if args.command=='list' else rules_service.match(settings,args.text,args.today,args.currency)
        else:
            body=json.loads(args.file.read_text()) if args.command=='operations' else dict(action='undone',undoes=args.audit_id) if args.command=='undo' else dict(action=args.command)
            result=rules_service.operate(settings,args.op_id or secrets.token_hex(16),body)
        print(json.dumps(result,ensure_ascii=False,indent=2))
    except (ValueError,KeyError,TypeError,OSError) as exc:
        parser.exit(1,str(exc)+'\n')


if __name__=='__main__': main()
