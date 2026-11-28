"""Single-flight snapshots of ledger reads, scoped to one app and endpoint."""
from threading import Lock

import ledger_lock
from store import file_signature, store_signature


def audit_signature(root):
    return file_signature(sorted((root/'data/additions').glob('*.jsonl')))


def ledger_signature(root):
    return store_signature(root), audit_signature(root)


class ReadCache:
    def __init__(self, root, *, signature=store_signature):
        self.root = root
        self.signature = signature
        self.latest = None
        self.rebuild = Lock()

    def hit(self, key, usable):
        latest = self.latest
        try:
            if latest is not None and latest[0] == key and latest[1] == self.signature(self.root) and usable(latest[2]):
                return latest
        except FileNotFoundError:
            # A writer may remove a file between glob and stat. Wait for it below.
            pass
        return None

    def get(self, build, *, key=None, usable=lambda value: True, blocking=True):
        if latest := self.hit(key, usable):
            return latest[2]
        if not self.rebuild.acquire(blocking=blocking):
            raise BlockingIOError('A ledger read is already rebuilding')
        try:
            if latest := self.hit(key, usable):
                return latest[2]
            with ledger_lock.ledger_lock(self.root, blocking=blocking):
                stamp = self.signature(self.root)
                result = build()
                self.latest = key, stamp, result
                return result
        finally:
            self.rebuild.release()
