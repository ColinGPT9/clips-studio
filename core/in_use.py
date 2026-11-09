"""Clips a person's request is working with at this moment: a publish sending
one, an export copying one, a thumbnail being chosen for one, the AI chat
working out an edit of one. Most of them record what they did only once the
slow part is done (the upload, the copy), so until then nothing in the
database says the clip is in use.

Make clips again (core/remake.py) keeps every clip held here, and deletes a
clip only inside settled(), where no request can start holding one. A
request holds its clips before it reads them, so it either holds a clip
before Make clips again decides about it (and the clip is kept), or finds it
already gone.

Files are held here too (hold_files): ones something in this process is
working with that no clip row points at yet, or not as it will. A Make
clips again run holds the renders it holds back from rows from before, and
the files of those clips while they are out of the run's way. Nothing may
delete a held file meanwhile: Storage's clean-up (core/housekeeping.py),
DELETE /clips/{clip_id} and Make clips again's own deleting all leave it.

In this process's memory only, as the editor lane's jobs are: a request the
app stopped in the middle of holds nothing once it's gone. Standard library
only.
"""

from __future__ import annotations

import os
import threading
from collections import Counter
from collections.abc import Callable, Iterable, Iterator
from contextlib import contextmanager
from functools import wraps
from pathlib import Path

# Re-entrant, so code inside settled() can still ask held() on its own thread.
_lock = threading.RLock()
_held: Counter[int] = Counter()
# The files held, by who holds them (id() of the holder): file_key()s.
_files: dict[int, set[str]] = {}


def _ids(clip_ids: Iterable) -> list[int]:
    out = []
    for clip_id in clip_ids:
        try:
            out.append(int(clip_id))
        except (TypeError, ValueError):
            continue
    return out


@contextmanager
def holding(clip_ids: Iterable) -> Iterator[None]:
    """Hold these clips as in use until the block ends, however it ends.
    Enter it before reading the clips. Two requests can hold the same clip;
    it stays held until both are done."""
    ids = _ids(clip_ids)
    with _lock:
        _held.update(ids)
    try:
        yield
    finally:
        with _lock:
            _held.subtract(ids)
            for clip_id in ids:
                if _held[clip_id] <= 0:
                    del _held[clip_id]


def holds_clips(fn: Callable) -> Callable:
    """A function that takes its clips as a `clip_ids` keyword argument
    holds them all while it runs, from before it reads the first one."""

    @wraps(fn)
    def run(*args, **kwargs):
        with holding(kwargs.get("clip_ids") or ()):
            return fn(*args, **kwargs)

    return run


def held() -> set[int]:
    """The clips held right now."""
    with _lock:
        return set(_held)


@contextmanager
def settled() -> Iterator[set[int]]:
    """The clips held right now, with no request able to start holding one
    until the block ends: for deciding about a clip and deleting it as one
    step. Keep the block short; a request starting meanwhile waits for it."""
    with _lock:
        yield set(_held)


# ---- files -------------------------------------------------------------------------


def file_key(path) -> str:
    """One key per file, however its path is written: resolved, and in one
    case on Windows."""
    try:
        return os.path.normcase(str(Path(path).resolve()))
    except (OSError, RuntimeError, ValueError):
        return os.path.normcase(os.path.abspath(str(path)))


def hold_files(holder: object, paths: Iterable) -> None:
    """Hold these files for `holder` until let_go_of_files(holder): nothing
    may delete one meanwhile. Holding a file twice holds it once."""
    keys = {file_key(p) for p in paths if p}
    if not keys:
        return
    with _lock:
        _files.setdefault(id(holder), set()).update(keys)


def let_go_of_files(holder: object) -> None:
    """Every file `holder` holds may be deleted again."""
    with _lock:
        _files.pop(id(holder), None)


def files_held(but: object | None = None) -> set[str]:
    """The file_key()s of every file held right now, less those `but` holds
    (a holder settling its own files)."""
    with _lock:
        return set().union(*(keys for who, keys in _files.items() if but is None or who != id(but)))
