"""Draft-local progress. The run record remains the source of diagnostics."""
from datetime import datetime, timezone
import json
import re


MAX_EVENTS = 128
MAX_SNIPPET = 240
MAX_ARGUMENTS = 512


def codex_reconnect(event):
    return event.get('type') == 'error' and re.match(
        r'^Reconnecting\.\.\.\s+\d+/\d+\b', str(event.get('message', '')))


class RunEvents:
    def __init__(self, folder, adapter, model):
        self.path = folder / 'events.jsonl'
        self.adapter = adapter
        self.count = 0
        self.pending = {}
        self.allowed = set()
        self.write('start', adapter=adapter, model=model[:MAX_SNIPPET])

    def write(self, kind, *, terminal=False, **fields):
        if self.count >= (MAX_EVENTS if terminal else MAX_EVENTS - 2):
            return
        event = dict(type=kind, at=datetime.now(timezone.utc).isoformat(), **fields)
        try:
            line = json.dumps(event, separators=(',', ':'), ensure_ascii=False, allow_nan=False).encode(errors='replace') + b'\n'
            with self.path.open('a+b') as handle:
                if kind == 'start' and handle.tell():
                    handle.seek(-1, 2)
                    if handle.read(1) != b'\n':
                        handle.write(b'\n')
                handle.write(line)
            self.count += 1
        except (OSError, ValueError):
            pass   # A progress write must never change the run's outcome.

    def lookup(self, name, arguments, result):
        if name in self.allowed:
            try:
                args = json.dumps(arguments, separators=(',', ':'), ensure_ascii=False, allow_nan=False)
                size = None if result is None else len(json.dumps(result, ensure_ascii=False, allow_nan=False).encode(errors='replace'))
            except (ValueError, TypeError, RecursionError):
                return
            self.write('lookup', tool=name, arguments=args if len(args) <= MAX_ARGUMENTS
                       else args[:MAX_ARGUMENTS - 1] + '…', result_bytes=size)

    def progress(self, text):
        if isinstance(text, str) and text.strip():
            self.write('progress', text=text.strip()[:MAX_SNIPPET])

    def observe(self, line):
        if self.count >= MAX_EVENTS - 2:
            return
        try:
            self._observe(json.loads(line))
        except (AttributeError, KeyError, TypeError, ValueError, RecursionError):
            pass   # The existing protocol parser still validates the full output.

    def _observe(self, event):
        kind, item = event.get('type'), event.get('item', {})
        message, part = event.get('message'), event.get('part', {})
        claude_system = self.adapter == 'claude' and kind == 'system'
        if codex_reconnect(event) or kind == 'auto_retry_start' or (
            claude_system and event.get('subtype') == 'api_retry'
        ):
            self.write('reconnecting')
        elif claude_system and event.get('status') == 'requesting':
            self.progress('Requesting a provider response')
        elif kind in ('error', 'turn.failed') or (
            self.adapter == 'claude' and kind == 'result' and event.get('is_error')
        ) or (self.adapter == 'pi' and kind == 'message_end' and message.get('stopReason') == 'error'):
            self.write('error', text='The provider reported an error')
        elif self.adapter == 'codex' and kind == 'item.completed':
            if item.get('type') == 'reasoning':
                self.progress(item.get('text'))
            if item.get('type') == 'mcp_tool_call' and item.get('server') == 'ledger':
                self.lookup(item['tool'], item.get('arguments'), item.get('result'))
        elif self.adapter == 'claude' and kind in ('assistant', 'user'):
            for block in message.get('content', []):
                if block.get('type') == 'thinking' and kind == 'assistant':
                    self.progress(block.get('thinking'))
                if block.get('type') == 'tool_use' and block['name'].startswith('mcp__ledger__'):
                    self.pending[block['id']] = (block['name'].removeprefix('mcp__ledger__'), block.get('input'))
                if block.get('type') == 'tool_result' and block['tool_use_id'] in self.pending:
                    self.lookup(*self.pending.pop(block['tool_use_id']), block.get('content'))
        elif self.adapter == 'pi':
            if kind == 'tool_execution_start':
                self.pending[event['toolCallId']] = event.get('args')
            if kind == 'tool_execution_end':
                self.lookup(event['toolName'], self.pending.pop(event['toolCallId'], None), event.get('result'))
            if kind == 'message_update' and (update := event.get('assistantMessageEvent', {})).get('type') == 'thinking_end':
                self.progress(update.get('content'))
            if kind == 'auto_retry_end' and event.get('success'):
                self.progress('Provider connection resumed')
        elif self.adapter == 'opencode':
            if kind == 'tool_use' and part['tool'].startswith('ledger_'):
                state = part['state']
                self.lookup(part['tool'].removeprefix('ledger_'), state.get('input'), state.get('output'))
            if kind == 'step_start':
                self.progress('Agent started a step')


def read_events(folder, after):
    events = []
    try:
        with (folder / 'events.jsonl').open('rb') as handle:
            handle.seek(after)
            for _ in range(MAX_EVENTS):
                line = handle.readline()
                if not line.endswith(b'\n'):
                    break
                after = handle.tell()
                try:
                    event = json.loads(line)
                except (ValueError, RecursionError):
                    continue
                if isinstance(event, dict) and isinstance(event.get('type'), str) and all(
                    value is None or (type(value) is int if key == 'result_bytes' else isinstance(value, str))
                    for key, value in event.items()
                ):
                    events.append(event)
    except FileNotFoundError:
        pass
    return dict(after=after, events=events)
