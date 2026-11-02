"""Adapter interface v1. Adapters propose JSON; the server owns validation/writes."""
from __future__ import annotations
from dataclasses import dataclass, field
import base64
import json
import mimetypes
from pathlib import Path
import threading
import time
from typing import Protocol

INTERFACE_VERSION = 1


@dataclass(frozen=True)
class Description:
    id: str
    display_name: str
    pinned_version: str
    isolation: bool
    enforcement: str
    interface_version: int = INTERFACE_VERSION


@dataclass(frozen=True)
class LookupTools:
    command: tuple[str, ...]
    definitions: tuple[dict, ...]
    call: object

    @classmethod
    def for_settings(cls, settings):
        from lookups import LookupStore
        from ledger_manifest import old_ledger_path
        old = old_ledger_path(settings.ledger, fallback=settings.old_ledger)
        store = LookupStore(settings.ledger, old)
        # Preserve the approved Codex MCP command. Other adapters use this too.
        command = ('python3', str(Path(__file__).resolve().parents[1] / 'lookup_server.py'),
                   '--ledger', str(settings.ledger), *(['--old-ledger', str(old)] if old else []))
        return cls(command, tuple(store.definitions()), store.call)


@dataclass(frozen=True)
class Task:
    prompt: str
    images: tuple[Path, ...]
    lookups: LookupTools
    schema: dict
    deadline: float  # monotonic, shared across tools, retries and transport
    cancel: threading.Event = field(default_factory=threading.Event)
    work: Path = Path('.')

    def remaining(self):
        if self.cancel.is_set():
            raise AdapterError('Agent cancelled')
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            raise AdapterError('Agent deadline exceeded', kind='transient')
        return remaining


@dataclass
class Result:
    answer: dict
    usage: dict = field(default_factory=dict)
    lookups: list = field(default_factory=list)
    errors: list = field(default_factory=list)
    duration: float = 0
    stderr_tail: str = ''

    def metadata(self):
        return dict(usage=self.usage, lookups=self.lookups, errors=self.errors, duration=self.duration, stderr_tail=self.stderr_tail)


class AdapterError(ValueError):
    def __init__(self, message, *, usage=None, lookups=None, kind="bad_answer", reset_at=None, errors=None, stderr_tail=""):
        from .failures import KINDS, diagnostic
        if kind not in KINDS: raise ValueError("Unknown adapter failure kind")
        self.kind, self.reset_at = kind, reset_at
        self.errors = errors or []
        self.stderr_tail = diagnostic(stderr_tail)
        super().__init__(message)
        self.usage, self.lookups = usage or {}, lookups or []


class AdapterPolicyError(AdapterError):
    """Verified model-visible or executed capability outside the allow-list."""


class Adapter(Protocol):
    def describe(self) -> Description: ...
    def run(self, task: Task) -> Result: ...
    def offline_check(self, work: Path, *, stop=None): ...
    def live_probe(self, work: Path, account: str, *, stop=None): ...
    def login_command(self) -> list[str]: ...
    def signed_in(self, work: Path, *, stop=None) -> bool: ...


def image_content(path):
    mime = mimetypes.guess_type(path.name)[0]
    if mime not in ('image/png', 'image/jpeg', 'image/webp'):
        raise AdapterError('Unsupported agent image format')
    return {'type': 'image', 'data': base64.b64encode(path.read_bytes()).decode(), 'mimeType': mime}


def strict_json(text):
    # No fenced/explanatory fallback: final text must be one complete JSON value.
    return json.loads(text)
