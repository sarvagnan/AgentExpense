"""Validated commit baseline and incremental strict checks, under the ledger lock.

The baseline changes only after a successful commit. Model validation, identity and
reference checks, canonical bytes, audit, evidence and statement checks still use
their shared implementations. Strict posting checks reuse unchanged entries;
balance assertions reuse the identical prefix of each account/currency's history.
Any refusal is rerun through the full checker to retain its exact reason/priority.
"""
from __future__ import annotations

from collections import defaultdict
from decimal import Decimal, localcontext
from pathlib import Path
import os
import stat

from store import entries
from ledger_manifest import read_manifest
import strict_check


def fingerprint(root):
    """Every input to verification, including external atomic replacements/deletions.

    ctime detects a writer restoring an old mtime. Directory listings detect new
    files. No financial content, repository snapshot or lock mutation is involved.
    Callers hold the ledger lock; cooperating writers cannot interleave the scan.
    """
    result = []
    def add(relative, info):
        result.append((relative, info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns))
    def walk(folder, relative):
        # DirEntry supplies the same lstat metadata without allocating/sorting a
        # Path for every evidence file, then statting the whole tree again.
        with os.scandir(folder) as children:
            for child in children:
                name = relative + '/' + child.name
                info = child.stat(follow_symlinks=False)
                add(name, info)
                if stat.S_ISDIR(info.st_mode): walk(child.path, name)
    for relative in ('ledger.json', 'data', 'extensions', 'Bills', '.jj/repo/op_heads/heads', '.git/refs/heads/main', '.git/packed-refs'):
        path = root / relative
        try:
            info = path.lstat()
            add(relative, info)
            if relative in ('data', 'extensions', 'Bills', '.jj/repo/op_heads/heads') and path.is_dir(): walk(path, relative)
        except FileNotFoundError:
            result.append((relative, None))
    return tuple(sorted(result))


def histories(documents):
    result = defaultdict(list)
    for entry in entries(documents):
        for number, posting in enumerate(entry['postings']):
            key = (posting['account'], posting['commodity'])
            result[key].append((entry['id'], number, entry['date'], posting['amount'],
                                (posting['assert'] or {}).get('amount') if (posting['assert'] or {}).get('commodity') == key[1] else None))
            assertion = posting['assert']
            if assertion and assertion['commodity'] != key[1]:
                result[(posting['account'], assertion['commodity'])].append((entry['id'], number, entry['date'], '0', assertion['amount']))
    return dict(result)


def running(rows, prefix=(), previous=()):
    count = 0
    while count < min(len(rows), len(prefix)) and rows[count] == prefix[count]:
        count += 1
    totals = list(previous[:count])
    total = totals[-1] if totals else Decimal(0)
    with localcontext() as context:
        context.prec = 200
        for row in rows[count:]:
            total += Decimal(row[3])
            if row[4] is not None and total != Decimal(row[4]):
                raise ValueError('incremental balance assertion failed')
            totals.append(total)
    return totals


class LedgerIndex:
    def __init__(self, documents, root, *, previous=None):
        self.root = root
        self.documents = documents
        self.start_date = read_manifest(root).get('startDate')
        self.ids = {row['id']: row for values in documents.values() if isinstance(values, list) for row in values}
        self.membership = defaultdict(set)
        for statement in documents[root / 'data/card-statements.json']:
            for member in statement.get('members', []):
                if member['entry-id']:
                    self.membership[member['entry-id']].add(statement['id'])
        for row in entries(documents):
            if row['statement'] and row['statement']['end']:
                self.membership[row['id']].add(row['statement']['end'])
        self.histories = histories(documents)
        self.balances = {}
        for key, rows in self.histories.items():
            old = previous.histories.get(key, ()) if previous else ()
            if previous and rows == old:
                self.balances[key] = previous.balances[key]
            else:
                self.balances[key] = running(rows, old, previous.balances.get(key, ()) if previous else ())
        self.checked_entries = 0
        self.checked_postings = 0

    def check_membership(self, documents, root):
        from check_store import check_card_statements
        if (read_manifest(root).get('startDate') != self.start_date or
                documents[root / 'data/accounts.json'] != self.documents[root / 'data/accounts.json']):
            return check_card_statements(documents, root)
        changed = [row for row in entries(documents) if row != self.ids.get(row['id'])]
        changed += [self.ids[row['id']] for row in list(changed) if row['id'] in self.ids]
        present = {row['id'] for row in entries(documents)}
        changed += [row for row in entries(self.documents) if row['id'] not in present]
        metadata = documents[root / 'data/accounts.json']['accounts']
        accounts = {p['account'] for row in changed for p in row['postings'] if metadata.get(p['account'], {}).get('kind') in ('bank', 'card')}
        if any((row['statement'] or {}).get('end') and not any(metadata.get(p['account'], {}).get('kind') == 'card' for p in row['postings']) for row in changed):
            return check_card_statements(documents, root)
        old_statements = {s['id']: s for s in self.documents[root / 'data/card-statements.json']}
        for statement in documents[root / 'data/card-statements.json']:
            old = old_statements.pop(statement['id'], None)
            if statement != old:
                accounts.add(statement['account'])
                if old: accounts.add(old['account'])
        accounts.update(s['account'] for s in old_statements.values())
        # Serial uniqueness spans accounts; this global index check cannot be
        # narrowed to only the accounts receiving new statement members.
        serials = set()
        for row in entries(documents):
            serial = (row['statement'] or {}).get('serial')
            if serial is not None:
                if serial in serials:
                    return check_card_statements(documents, root)
                serials.add(serial)
        if not accounts:
            return dict(statements=[], unbilled=[])
        subset = {path: [row for row in rows if any(p['account'] in accounts for p in row['postings'])]
                  if path.parent.name == 'expenses' else rows for path, rows in documents.items()}
        subset[root / 'data/card-statements.json'] = [s for s in documents[root / 'data/card-statements.json'] if s['account'] in accounts]
        try:
            return check_card_statements(subset, root)
        except ValueError:
            # A transfer can pull another account's statement row into this
            # projection. Fall back conservatively to the complete membership
            # checker when the projection lacks that account's context.
            return check_card_statements(documents, root)

    def check(self, documents, root):
        declarations = documents[root / 'data/accounts.json']
        if declarations != self.documents[root / 'data/accounts.json']:
            return strict_check.check(documents, root)
        accounts = set(declarations['accounts'])
        declared = strict_check._declared(declarations['commodities'])
        try:
            self.checked_entries = 0
            for row in strict_check.journal_order(documents):
                if row == self.ids.get(row['id']):
                    continue
                self.checked_entries += 1
                for posting in row['postings']:
                    found = strict_check.posting_problem(posting, accounts, declared)
                    if found:
                        raise ValueError(found[1])
            if documents[root / 'data/forecast.json'] != self.documents[root / 'data/forecast.json']:
                strict_check.check_forecast(documents[root / 'data/forecast.json'], accounts, declared)
            strict_check.check_prices(root, declared)
            current = histories(documents)
            self.checked_postings = 0
            for key, rows in current.items():
                old = self.histories.get(key, ())
                if rows != old:
                    count = 0
                    while count < min(len(rows), len(old)) and rows[count] == old[count]:
                        count += 1
                    self.checked_postings += len(rows) - count
                    running(rows, old, self.balances.get(key, ()))
            # A removed history cannot leave an assertion behind: assertions in
            # other commodities have their own indexed zero-amount row above.
        except (ValueError, KeyError, TypeError):
            # Preserve the full checker's first error and structured refusal.
            # If the full checker passes, this is an implementation disagreement:
            # refuse the save, keep the old baseline, and require a restart/full check.
            strict_check.check(documents, root)
            raise RuntimeError('Incremental and full ledger checks disagree; save refused') from None
