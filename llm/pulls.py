"""Which model downloads are running in this process right now.

The engine's API thread pulls models (server/api.py /models/pull) while its
worker thread runs jobs (server/jobs.py), and a job may start while a pull it
will need is still going: setup tells a new user to carry on while the
recommended model downloads. The job's model check asks here so it doesn't
fail a job for a model that is minutes from being installed.

Process-local and in memory on purpose: a download is a thread of this
process, so when the process goes, so does the download it would describe.
"""

from __future__ import annotations

import threading

_lock = threading.Lock()
_running: set[str] = set()


def started(tag: str) -> None:
    """Record that a download of `tag` began."""
    with _lock:
        _running.add(tag)


def finished(tag: str) -> None:
    """Record that the download of `tag` ended, whether it worked or not."""
    with _lock:
        _running.discard(tag)


def running() -> set[str]:
    """The tags being downloaded now (a copy)."""
    with _lock:
        return set(_running)
