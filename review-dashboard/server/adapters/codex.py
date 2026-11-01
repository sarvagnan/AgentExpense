"""The established Codex flags, renderer and probe behind interface v1."""
from dataclasses import replace
import json
import os
from pathlib import Path
import signal
import subprocess
import time
from .base import AdapterError, Description, Result


class Codex:
    def __init__(self, settings):
        self.settings = settings

    def describe(self):
        from . import pinned_version
        return Description('codex', 'Codex', pinned_version(self.settings, '@openai/codex'), True,
                           'Dedicated CODEX_HOME, read-only sandbox, disabled execution/discovery, rendered tool allow-lists.')

    def environment(self):
        from agent import codex_environment
        return codex_environment(self.settings)

    def version(self, work, *, stop=None):
        from agent_check import invoke, require_codex
        require_codex(self.settings)
        self.settings.codex_home.mkdir(parents=True, exist_ok=True)
        return invoke([self.settings.codex, '--version'], work, self.settings.timeout,
                      environment=self.environment(), stop=stop).strip()

    def fingerprint(self):
        from agent import command_for, lookup_names
        import agent_check as check
        command = command_for(self.settings, check.OFFLINE_PROMPT, [])
        return {'isolation flags': command[2:command.index('-m')],
                'allow-lists': {'sections': sorted(check.DEVELOPER_SECTIONS), 'nested': sorted(check.NESTED_TOOLS),
                               'lookups': sorted(lookup_names(self.settings))}}

    def run(self, task):
        import agent
        started = time.monotonic()
        task.remaining()
        (task.work / 'schema.json').write_text(json.dumps(task.schema))
        process = subprocess.Popen(agent.command_for(self.settings, task.prompt, task.images), cwd=task.work,
                                   env=self.environment(), stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                   stderr=subprocess.PIPE, text=True, start_new_session=True)
        log = ''
        try:
            # Keep the injectable deadline boundary used by the offline retry tests.
            from .process import communicate
            out, err = communicate(process, task, legacy=agent.communicate)
            log = out + '\n' + err
            usage, calls = agent.parse_events(log)
            if process.returncode:
                raise AdapterError(f'Agent exited {process.returncode}; output withheld', usage=usage, lookups=calls)
            return Result(json.loads((task.work / 'answer.json').read_text()), usage, calls,
                          duration=time.monotonic() - started)
        except subprocess.TimeoutExpired:
            raise AdapterError(f'Agent timed out after {self.settings.timeout}s') from None
        finally:
            if process.poll() is None:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                process.communicate()

    def offline_check(self, work, *, stop=None):
        from agent_check import check_offline
        return check_offline(self.settings, work, stop=stop)

    def live_probe(self, work, account, *, stop=None):
        from agent_check import check_live
        return check_live(self.settings, work, account, stop=stop)

    def signed_in(self, work, *, stop=None):
        from agent_check import signed_in
        return signed_in(self.settings, work, stop=stop)

    def login_command(self):
        from agent_check import require_codex
        require_codex(self.settings)
        self.settings.codex_home.mkdir(parents=True, exist_ok=True)
        return [self.settings.codex, 'login', '--device-auth']
