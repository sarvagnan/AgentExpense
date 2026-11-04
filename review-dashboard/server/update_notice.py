"""The optional "update available" notice; off unless local.json sets updates.notice.

When on, the worker's loop asks `tick()` whether a check is due. At most one check runs
per day (also after a failure or a restart: the time is kept in the state directory).
A check lists the configured remote's tags with `git ls-remote` in its own thread and
sends nothing else; reading the status never contacts the remote.
"""
import json
import logging
import os
from pathlib import Path
import subprocess
import threading
import time
from datetime import datetime, timezone

import releases

logger = logging.getLogger(__name__)
DAY = 24 * 60 * 60
TIMEOUT = 30


def start_thread(target):
    threading.Thread(target=target, name='update-check', daemon=True).start()


class UpdateNotice:
    interval = DAY

    def __init__(self, settings, *, clock=time.time, runner=subprocess.run, start=start_thread):
        self.application = Path(settings.application)
        self.enabled, self.remote = settings.update_notice, settings.update_remote
        self.path = Path(settings.state_dir) / 'update-check.json'
        self.clock, self.runner, self.start = clock, runner, start
        self.mutex = threading.Lock()
        self.checking = False

    def current(self):
        return (self.application / 'VERSION').read_text().strip()

    def record(self):
        try:
            value = json.loads(self.path.read_text())
            return value if isinstance(value, dict) and isinstance(value.get('checked_at'), (int, float)) else None
        except (OSError, ValueError):
            return None

    def due(self):
        if not self.enabled:
            return False
        record = self.record()
        if record is None:
            return True
        elapsed = self.clock() - record['checked_at']
        # A clock set back past the last check counts as due rather than stalling for days.
        return elapsed >= self.interval or elapsed < 0

    def tick(self):
        if not self.due():
            return False
        with self.mutex:
            if self.checking:
                return False
            self.checking = True
        self.start(self.check)
        return True

    def check(self):
        started = self.clock()
        try:
            result = self.runner(['git', 'ls-remote', '--tags', '--refs', self.remote], cwd=self.application,
                                 env={**os.environ, 'GIT_TERMINAL_PROMPT': '0'}, capture_output=True, text=True,
                                 timeout=TIMEOUT)
            if result.returncode:
                raise RuntimeError(f'git ls-remote exited with status {result.returncode}')
            found = releases.releases(releases.ls_remote_tags(result.stdout))
            record = {'checked_at': started, 'latest': str(found[0]) if found else None}
        except Exception as exc:  # The notice is optional: log and try again tomorrow.
            logger.warning('Update check failed: %s', exc)
            record = {'checked_at': started, 'latest': None, 'error': True}
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.path.with_suffix('.next')
            temporary.write_text(json.dumps(record) + '\n')
            temporary.replace(self.path)
        except OSError:
            logger.exception('Could not record the update check')
        finally:
            with self.mutex:
                self.checking = False

    def status(self):
        current = self.current()
        if not self.enabled:
            return {'enabled': False, 'current': current}
        record = self.record() or {}
        latest, installed = releases.parse(record.get('latest') or ''), releases.parse(current)
        checked = record.get('checked_at')
        return {'enabled': True, 'current': current,
                'latest': str(latest) if latest and installed and latest > installed else None,
                'checkedAt': datetime.fromtimestamp(checked, timezone.utc).isoformat() if checked is not None else None,
                'failed': bool(record.get('error')), 'command': 'make update'}
