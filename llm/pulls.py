"""Which model downloads are running in this process right now.

The engine's API thread pulls models (server/api.py /models/pull) while its
worker thread runs jobs (server/jobs.py), and a job may start while a pull it
will need is still going: setup tells a new user to carry on while the
recommended model downloads. The job's model check asks here so it doesn't
fail a job for a model that is minutes from being installed.

Counted, not just remembered: a retry of the same tag can start in the moment
between one download's last event and its thread's end, and the first one
finishing must not take the second off the list.

Process-local and in memory on purpose: a download is a thread of this
process, so when the process goes, so does the download it would describe.
"""

from __future__ import annotations

import threading
from collections import Counter

_lock = threading.Lock()
_running: Counter[str] = Counter()


def started(tag: str) -> None:
    """Record that a download of `tag` began."""
    with _lock:
        _running[tag] += 1


def finished(tag: str) -> None:
    """Record that a download of `tag` ended, whether it worked or not."""
    with _lock:
        _running[tag] -= 1
        if _running[tag] <= 0:
            del _running[tag]


def running() -> set[str]:
    """The tags being downloaded now."""
    with _lock:
        return {tag for tag, n in _running.items() if n > 0}
