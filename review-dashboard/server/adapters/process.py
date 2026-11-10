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
    try:
        if legacy:
            output = legacy(process, task.remaining(), data)
        else:
            output = process.communicate(input=data, timeout=task.remaining())
        task.remaining()
        return output
    finally:
        done.set()
        watcher.join()
        if process.poll() is None:
            kill(process)
            process.communicate()
