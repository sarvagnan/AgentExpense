"""Long-lived verification state. Only the ledger lock protects this state.

It precedes drafts.mutex and statements.mutex in the existing lock order. It
never acquires either. A preview uses ordinary full checks, with model reuse;
only a locked commit can advance the validated baseline.
"""
from pathlib import Path

from ledger_lock import ledger_lock
from incremental_check import LedgerIndex, fingerprint
import store
from check_store import _check

_active = {}


def get(root):
    return _active.get(Path(root).resolve())


class SaveService:
    def __init__(self, settings):
        self.settings = settings
        self.root = settings.ledger.resolve()
        self.index = None
        self.stamp = None
        self.canonical = {}
        self.full_checks = self.incremental_checks = 0
        self.rollback_operation = None
        self.checkpoint = None

    def start(self):
        try:
            with ledger_lock(self.root, blocking=False):
                self.reload()
        except BlockingIOError:
            # Startup must keep reads available while a terminal writer owns the
            # lock. The first save performs the full check after acquiring it.
            self.index = self.stamp = None
        store._model_memos[self.root] = store.ModelMemo(self.canonical)
        _active[self.root] = self

    def close(self):
        if _active.get(self.root) is self:
            _active.pop(self.root, None)
            store._model_memos.pop(self.root, None)

    def full_check(self):
        loaded = store.load_parsed(self.root)
        _check(self.root, loaded=loaded, canonical_cache=self.canonical, emit=lambda *args: None)
        self.index = LedgerIndex(loaded[0], self.root)
        self.stamp = fingerprint(self.root)
        self.full_checks += 1

    def before(self):
        # Called after jj's clean-working-copy snapshot, before the first write.
        if self.stamp != fingerprint(self.root):
            self.reload()
        self.rollback_operation = None

    def reload(self):
        try:
            self.full_check()
        except (ValueError, OSError):
            # Recover interrupted writes and allow an explicit correction of an
            # invalid external commit. Until a full check passes, every proposed
            # commit takes the full gate; no stale index is usable.
            self.index = None
            self.canonical.clear()
            self.stamp = fingerprint(self.root)
            import logging
            logging.getLogger(__name__).warning('Full startup/external ledger check failed; saves require a full check')

    def accepted(self, identity, documents):
        self.index = LedgerIndex(documents, self.root, previous=self.index)
        self.stamp = fingerprint(self.root)
        self.incremental_checks += 1

    def rolled_back(self):
        # Keep the last committed index. A restored file has new ctime, forcing a
        # full reload before any next save; never bless a failed candidate.
        self.stamp = None
        self.index = self.checkpoint
        self.rollback_operation = None
