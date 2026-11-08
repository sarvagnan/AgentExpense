"""Coalesced dashboard builds from locked snapshots; whole generations publish last.

Lock order: ledger lock -> this condition (a leaf). The builder never holds the
condition while taking a ledger/draft/statement lock. Publication reads no working
copy. Its durable record is written before committing, so a killed server repairs
an unfinished build on its next start.
"""
from __future__ import annotations

import json
import hashlib
import logging
import os
import re
from pathlib import Path
import secrets
import shutil
import subprocess
import threading
import time

from dashboard_data import check_source, public_data, write_source
from ledger_lock import ledger_lock
from node_toolchain import node_environment
from second_check import committed as current_commit

_active = {}
LOG = logging.getLogger(__name__)


def get(root):
    return _active.get(Path(root).resolve())


class DataRefresh:
    def __init__(self, settings):
        self.settings = settings
        self.root = settings.ledger.resolve()
        self.record = self.root / 'drafts/data-refresh.json'
        self.folder = settings.dashboard_data / '.live-data'
        self.snapshot_folder = self.root / 'drafts/dashboard-inputs'
        self.condition = threading.Condition()
        self.thread = None
        self.pending = self.prepared = None
        self.running = self.closed = False
        self.ready = self.wanted = None
        self.generation = None
        self.error = None
        self.runs = 0
        self.version = 0
        self.epoch = secrets.token_hex(16)
        self.committed_at = None
        self.fresh_seconds = None
        self.build_seconds = None
        self.stale = True
        self.runner = self.build
        self.inputs = {}
        self.provenance = {}
        self.force_version = self.satisfied_force = 0
        from daily_refresh import local_today
        self.today = local_today
        self.builder = None

    def builder_identity(self):
        application = self.settings.application
        digest = hashlib.sha256()
        files = list((application/'scripts').glob('*.mjs'))
        for folder in (application/'review-dashboard/scripts', application/'schema', application/'extensions'):
            files.extend(folder.glob('*'))
        for path in sorted(files):
            if path.is_file():
                digest.update(str(path.relative_to(application)).encode()+b'\0')
                digest.update(path.read_bytes())
        return digest.hexdigest()

    def write(self):
        self.record.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.record.with_suffix('.tmp')
        with temporary.open('w') as handle:
            json.dump(dict(wanted=self.wanted, ready=self.ready, generation=self.generation,
                           stale=self.stale, error=self.error, builder=self.provenance.get('builder'),
                           day=self.provenance.get('day'), force=self.force_version > self.satisfied_force), handle)
            handle.flush(); os.fsync(handle.fileno())
        temporary.replace(self.record)
        self.version += 1
        self.condition.notify_all()

    def status(self):
        with self.condition:
            return dict(version=self.version, epoch=self.epoch, commit=self.wanted, ready=self.ready, generation=self.generation,
                        data_stale=self.stale,
                        updating=bool(self.running or self.pending or self.prepared), refresh_error=self.error,
                        seconds=self.fresh_seconds)

    def start(self):
        _active[self.root] = self
        try:
            self.start_reading()
        except BlockingIOError:
            # Keep intake available during another writer without touching its
            # in-progress stale marker. A later save/maintenance can retry.
            self.stale = True
        except Exception as exc:
            with self.condition:
                self.error, self.stale = str(exc), True
                self.safe_write()
            LOG.warning('Dashboard startup refresh deferred', exc_info=True)

    def start_reading(self):
        check_source(self.settings.dashboard_data, self.root)
        self.folder.mkdir(parents=True, exist_ok=True)
        record = {}
        try:
            record = json.loads(self.record.read_text())
            if not isinstance(record, dict): record = {}
            generation = record.get('generation')
            if isinstance(generation, str) and len(generation) == 32 and all(c in '0123456789abcdef' for c in generation) and (self.folder / generation / 'public/data/dashboard.json.gz').is_file():
                self.generation, self.ready = generation, record.get('ready')
                try: self.inputs = json.loads((self.folder / generation / 'inputs.json').read_text())
                except (OSError, ValueError): pass
                if not isinstance(self.inputs, dict): self.inputs = {}
                try: self.provenance = json.loads((self.folder / generation / 'provenance.json').read_text())
                except (OSError, ValueError): pass
                if not isinstance(self.provenance, dict): self.provenance = {}
            if record.get('force'): self.force_version = 1
        except (OSError, ValueError, TypeError):
            pass
        self.builder = self.builder_identity()
        # An interrupted transaction keeps its old generation stale. A mismatch also
        # catches terminal commits made while the server was stopped.
        with ledger_lock(self.root, blocking=False):
            self.start_locked(record)

    def start_locked(self, record):
        identity = current_commit(self.root)
        if identity is None:
            # Read-only fixture hosts can expose health/static views without
            # repository history. A write still requires the normal jj gate.
            self.stale = False
            return
        with self.condition:
            self.wanted = identity
        self.stale = (self.ready != identity or record.get('stale', False) or
                      self.provenance != dict(builder=self.builder, day=self.today()) or
                      self.force_version > self.satisfied_force)
        dirty = subprocess.check_output(['jj', '--config', 'snapshot.max-new-file-size=0', '-R', str(self.root), 'diff', '--summary'], text=True).strip()
        if self.stale and not dirty:
            self.prepare()
            self.activate(self.committed(identity))

    def close(self):
        with self.condition:
            self.closed = True
            self.condition.notify_all()
        if self.thread and self.thread.ident is not None:
            self.thread.join(timeout=130)
        if _active.get(self.root) is self:
            _active.pop(self.root, None)

    def prepare(self, *, force=False, day=None):
        """Caller owns ledger lock. Snapshot only builder inputs, never evidence/auth."""
        check_source(self.settings.dashboard_data, self.root)
        if self.builder is None: self.builder = self.builder_identity()
        with self.condition:
            previous_generation, previous_inputs = self.generation, dict(self.inputs)
            provenance = dict(builder=self.builder, day=day or self.today())
            if force: self.force_version += 1
            force_version = self.force_version
            full = force_version > self.satisfied_force or provenance != self.provenance
        identity = secrets.token_hex(16)
        work = self.folder / identity
        # Raw financial documents belong to the ledger, even when the generated
        # dashboard output root uses its application-directory default.
        source = self.snapshot_folder / identity
        work.mkdir(parents=True)
        source.mkdir(parents=True)
        shutil.copyfile(self.root / 'ledger.json', source / 'ledger.json')
        (source / 'data').mkdir()
        for path in (self.root / 'data').glob('*.json'):
            shutil.copyfile(path, source / 'data' / path.name)
        for name in ('expenses', 'prices', 'additions'):
            if (self.root / 'data' / name).exists():
                shutil.copytree(self.root / 'data' / name, source / 'data' / name)
        if (self.root / 'extensions').exists():
            shutil.copytree(self.root / 'extensions', source / 'extensions')
        inputs = {str(path.relative_to(source)): hashlib.sha256(path.read_bytes()).hexdigest()
                  for path in source.rglob('*') if path.is_file()}
        changed = {name for name in inputs.keys() | previous_inputs.keys() if inputs.get(name) != previous_inputs.get(name)}
        builders = ['copy-fuel-log', 'build-dashboard-data', 'build-review-data', 'build-add-patterns']
        if previous_generation and previous_inputs and not full:
            previous = self.folder / previous_generation / 'app/data'
            shutil.copytree(previous, work / 'app/data', ignore=shutil.ignore_patterns('.*'))
            common = any(name == 'ledger.json' or name.startswith('extensions/') or name == 'data/accounts.json' or name.startswith('data/expenses/') for name in changed)
            builders = []
            if common or 'data/fuel-log.json' in changed: builders.append('copy-fuel-log')
            if common or any(name in ('data/forecast.json', 'data/card-statements.json') or name.startswith('data/prices/') for name in changed): builders.append('build-dashboard-data')
            if common: builders.append('build-review-data')
            if common or any(name in ('data/forecast.json', 'data/recurring-settings.json') or name.startswith('data/additions/') for name in changed): builders.append('build-add-patterns')
        (work / 'inputs.json').write_text(json.dumps(inputs))
        with self.condition:
            self.prepared = dict(id=identity, work=work, source=source, inputs=inputs, builders=builders,
                                 provenance=provenance, force_version=force_version, full=full)
            self.error = None
            self.stale = True
            self.write()  # durable before jj commit

    def committed(self, identity, *, at=None):
        with self.condition:
            self.wanted = identity
            self.committed_at = time.perf_counter() if at is None else at
            candidate = self.prepared
            self.prepared = None
            if candidate is None:
                self.latest_candidate = None
                self.stale = True
                self.write()
                return
            candidate.update(commit=identity, at=self.committed_at, eligible=False)
            self.latest_candidate = candidate['id']
            # At most one run and one waiting snapshot. Drop superseded inputs.
            if self.pending:
                shutil.rmtree(self.pending['source'], ignore_errors=True)
                shutil.rmtree(self.pending['work'])
            self.pending = candidate
            self.write()
            return candidate['id']

    def activate(self, candidate_id):
        """Acknowledge exactly this candidate after its durable receipt boundary."""
        with self.condition:
            if self.pending is None or self.pending['id'] != candidate_id:
                return
            self.pending['eligible'] = True
            self.start_builder()

    def safe_write(self):
        """Background status I/O cannot strand the worker or undo a saved receipt."""
        try: self.write()
        except Exception:
            LOG.exception('Dashboard refresh status could not be persisted')
            self.version += 1
            self.condition.notify_all()

    def start_builder(self):
        # Caller owns the condition. Eligibility is specific to the candidate;
        # even a running thread cannot consume an unacknowledged replacement.
        if self.running or self.closed or not self.pending or not self.pending['eligible']:
            return
        self.running = True
        self.thread = threading.Thread(target=self.loop, name='dashboard-data', daemon=True)
        try: self.thread.start()
        except Exception as exc:
            candidate, self.pending = self.pending, None
            self.running = False
            self.error, self.stale = str(exc), True
            self.safe_write()
            self.cleanup(candidate)

    def failed_prepare(self, error):
        with self.condition:
            self.prepared = None
            self.error = error
            self.stale = True
            self.write()

    def rolled_back(self):
        identity = current_commit(self.root)
        with self.condition:
            if self.prepared:
                shutil.rmtree(self.prepared['source'], ignore_errors=True)
                shutil.rmtree(self.prepared['work'])
                self.prepared = None
            if self.pending:
                shutil.rmtree(self.pending['source'], ignore_errors=True)
                shutil.rmtree(self.pending['work'])
                self.pending = None
            self.wanted = identity
            self.latest_candidate = None
            self.stale = self.wanted != self.ready
            self.write()
        if self.stale and identity is not None:
            self.prepare()
            self.activate(self.committed(identity))

    def result(self, result):
        # Read routes may ask about an older result while another save is still
        # recording its receipt. Only that save's completed transaction may
        # activate its pending candidate.
        status = self.status()
        result = {key: value for key, value in result.items() if key not in ('data_stale', 'data_pending', 'refresh_error')}
        return {**result, 'data_stale': status['data_stale'], 'data_pending': status['updating'],
                **({'refresh_error': status['refresh_error']} if status['refresh_error'] else {})}

    def wait_idle(self, timeout=130):
        with self.condition:
            return self.condition.wait_for(lambda: not self.running and not self.pending, timeout)

    def retry(self):
        # No draft/statement mutex is held by this endpoint.
        with ledger_lock(self.root):
            if subprocess.check_output(['jj', '--config', 'snapshot.max-new-file-size=0', '-R', str(self.root), 'diff', '--summary'], text=True).strip():
                raise ValueError('Finish the ledger changes before refreshing views')
            self.prepare(force=True)
            self.activate(self.committed(current_commit(self.root)))
        return self.status()

    def tick(self):
        identity = current_commit(self.root)
        with self.condition:
            if self.error or identity == self.wanted and (not self.stale or self.running or self.pending or self.prepared): return
        try:
            with ledger_lock(self.root, blocking=False):
                if subprocess.check_output(['jj', '--config', 'snapshot.max-new-file-size=0', '-R', str(self.root), 'diff', '--summary'], text=True).strip(): return
                self.prepare()
                self.activate(self.committed(current_commit(self.root)))
        except BlockingIOError:
            return

    def build(self, candidate):
        env = node_environment(self.settings.application)
        env.update(LEDGER_DIR=str(candidate['source']), DASHBOARD_DATA_DIR=str(candidate['work']),
                   EXPENSES_BUILD_SNAPSHOT='1', EXPENSES_DATA_BUILDERS=json.dumps(candidate['builders']))
        completed = subprocess.run(['node', 'review-dashboard/scripts/build-data.mjs'],
                                   cwd=self.settings.application, env=env, capture_output=True, text=True, timeout=120)
        if completed.returncode:
            raise RuntimeError((completed.stdout + completed.stderr)[-3000:])

    def loop(self):
        try:
            self.build_loop()
        except Exception as exc:
            with self.condition:
                self.error, self.stale = str(exc), True
                self.safe_write()
            LOG.exception('Dashboard builder stopped unexpectedly')
        finally:
            with self.condition:
                self.running = False
                self.condition.notify_all()
                # An activation racing the previous thread's exit must not be
                # lost. An unreceipted pending candidate remains paused.
                self.start_builder()

    def build_loop(self):
        while True:
            with self.condition:
                candidate = self.pending
                if candidate is None or not candidate['eligible'] or self.closed:
                    return
                self.pending = None
            try:
                started = time.perf_counter()
                self.runner(candidate)
                with self.condition:
                    self.runs += 1
                    if (candidate['eligible'] and not self.closed and candidate['id'] == self.latest_candidate
                            and not self.prepared and candidate['force_version'] == self.force_version):
                        (candidate['work'] / 'provenance.json').write_text(json.dumps(candidate['provenance']))
                        if self.generation and self.generation != candidate['id']:
                            # Give a reader the full retention window starting
                            # when its previously current generation is replaced.
                            superseded = time.time()
                            os.utime(self.folder/self.generation, (superseded, superseded))
                        self.ready = candidate['commit']
                        self.generation = candidate['id']
                        self.inputs = candidate['inputs']
                        self.provenance = candidate['provenance']
                        if candidate['full']: self.satisfied_force = candidate['force_version']
                        self.error = None
                        self.stale = False
                        self.build_seconds = time.perf_counter() - started
                        self.fresh_seconds = time.perf_counter() - candidate['at']
                        self.write()
                # Compatibility output for terminal readers. Each file is replaced
                # whole; browsers pin the immutable generation via data-status.
                if self.generation == candidate['id']:
                    for folder in ('app/data', 'public/data'):
                        target = self.settings.dashboard_data / folder
                        target.mkdir(parents=True, exist_ok=True)
                        for path in (candidate['work'] / folder).glob('*'):
                            if path.name.startswith('.'):
                                continue
                            temp = target / (path.name + '.refresh-tmp')
                            shutil.copyfile(path, temp); temp.replace(target / path.name)
                    write_source(self.settings.dashboard_data, self.root)
                LOG.info('data-timing %s', json.dumps(dict(commit=candidate['commit'],
                    build=round(time.perf_counter()-started, 4), fresh=self.fresh_seconds)))
            except Exception as exc:
                with self.condition:
                    if candidate['id'] == self.latest_candidate:
                        self.error = str(exc)
                        self.stale = True
                        self.safe_write()
            finally:
                self.cleanup(candidate)

    def cleanup(self, candidate):
        # Neither input cleanup nor pruning may kill the background thread.
        try:
            if candidate['source'].exists(): shutil.rmtree(candidate['source'])
        except Exception:
            LOG.warning('Dashboard input cleanup deferred', exc_info=True)
        try:
            with self.condition:
                retained = {self.generation, (self.pending or {}).get('id'), (self.prepared or {}).get('id')}
            for folder in (self.folder, self.snapshot_folder):
                if not folder.exists(): continue
                for path in folder.iterdir():
                    if re.fullmatch(r'[0-9a-f]{32}', path.name) and path.name not in retained and path.is_dir() and not path.is_symlink() and time.time()-path.stat().st_mtime > 600:
                        shutil.rmtree(path)
        except Exception:
            LOG.warning('Dashboard generation pruning deferred', exc_info=True)
