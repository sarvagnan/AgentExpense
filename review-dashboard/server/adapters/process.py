"""Deadline/cancellation-aware process groups; no child diagnostics escape."""
import os
import signal
import subprocess
import threading
from .base import AdapterError


def kill(process):
    process.poll()  # Reap an exited leader before signalling its remaining group.
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    except PermissionError:
        # A concurrent deadline watcher can race group teardown on the host.
        # The exact Popen child is still ours; terminate it and let the caller reap.
        process.kill()


def communicate(process, task, *, data=None, legacy=None):
    # The watchdog makes even a blocked communicate interruptible. It never changes
    # the established runner's injectable timeout boundary.
    done = threading.Event()
    def watch():
        while not done.wait(.05):
            if task.cancel.is_set() or __import__('time').monotonic() >= task.deadline:
                kill(process)
                return
    watcher = threading.Thread(target=watch, daemon=True)
    watcher.start()
    reader, lines, errors = None, [], []
    if task.events:
        # Give stdout its own reader while communicate drains stderr and writes
        # stdin under the established deadline boundary.
        stdout, process.stdout = process.stdout, None
        def read():
            try:
                with stdout:
                    for line in stdout:
                        lines.append(line)
                        task.events.observe(line)
            except (OSError, UnicodeError) as exc:
                errors.append(exc)
        reader = threading.Thread(target=read, daemon=True)
        reader.start()
    try:
        if legacy:
            output = legacy(process, task.remaining(), data)
        else:
            output = process.communicate(input=data, timeout=task.remaining())
        if reader:
            reader.join(timeout=task.remaining())
            if errors:
                raise errors[0]
            output = (''.join(lines), output[1])
        task.remaining()
        return output
    finally:
        done.set()
        watcher.join()
        if process.poll() is None:
            kill(process)
            process.communicate()
        if reader and reader.is_alive():
            kill(process)
            reader.join(timeout=1)
