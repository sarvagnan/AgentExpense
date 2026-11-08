"""Ledger time limits and cached, read-only first-appearance history."""
from datetime import datetime, timezone
import json
import subprocess
from functools import lru_cache

DEFAULTS = dict(delete_days=7, trash_days=7, saved_hours=24)


def limits(manifest):
    return {**DEFAULTS, **manifest.get('retention', {})}


class AdditionTimes:
    """Loaded at startup/maintenance, never by an entry request.

    The first committed snapshot is setup/import, so its IDs stay protected.
    Other entries use audit timestamps before commit-time fallbacks.
    """
    def __init__(self, root):
        self.root = root
        self.head = None
        self.times = {}
        self.initial = set()
        self.first_commits = {}
        self.commit_count = 0
        self.refresh()

    def refresh(self):
        root = self.root
        # A fixture nested inside the application is not its own ledger repository.
        if not (root / '.jj').is_dir():
            return
        head = subprocess.check_output(['jj', '--ignore-working-copy', '-R', str(root), 'log', '-r', '@-', '--no-graph', '-T', 'commit_id'], text=True, stderr=subprocess.PIPE).strip()
        if head == self.head:
            return
        first = subprocess.check_output(['git','-C',str(root),'rev-list','--max-parents=0',head],text=True).splitlines()
        args = ['git', '-C', str(root), 'log', '--reverse', '--format=%H %cI', head]
        if self.head:
            ancestor=subprocess.run(['git','-C',str(root),'merge-base','--is-ancestor',self.head,head],capture_output=True)
            if ancestor.returncode == 0: args.append('^'+self.head)
            else: self.times.clear();self.initial.clear();self.first_commits.clear();self.commit_count=0
        commits = subprocess.check_output(args, text=True).splitlines()
        known = set(self.times) | self.initial
        # History is visited only once per process/head, before eligibility uses it.
        for index, line in enumerate(commits):
            commit, stamp = line.split(' ', 1)
            rank=self.commit_count;self.commit_count+=1
            paths = subprocess.check_output(['git', '-C', str(root), 'ls-tree', '-r', '--name-only', commit, '--', 'data/expenses'], text=True).splitlines()
            for path in paths:
                if not path.endswith('.json'):
                    continue
                rows = json.loads(subprocess.check_output(['git', '-C', str(root), 'show', commit + ':' + path], text=True))
                for entry in rows:
                    identity = entry['id']
                    if identity in known:
                        continue
                    known.add(identity)
                    self.first_commits[identity]=rank
                    if commit in first:
                        self.initial.add(identity)
                    else:
                        self.times[identity] = datetime.fromisoformat(stamp).astimezone(timezone.utc).isoformat()
        self.head = head

    def get(self, identity, lines):
        if identity in self.initial:return None
        added = next((r['time'] for r in lines if r['kind'] == 'draft' and r.get('outcome') == 'added' and r.get('entry_id') == identity), None)
        return added or self.times.get(identity)


@lru_cache(maxsize=32)
def history_for(root):
    return AdditionTimes(root)
