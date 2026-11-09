"""Cooperative cancellation for in-flight video processing.

The worker runs one video at a time in a thread. Killing a thread mid-FFmpeg
is unsafe, so cancellation is cooperative: the API marks a video_id cancelled,
and the pipeline checks at every stage boundary (and inside the download
progress hook and render loop), raising CancelledError to unwind cleanly.
"""

import subprocess
import threading
import time

_lock = threading.Lock()
_cancelled: set[str] = set()
_active: str | None = None  # the video the worker is processing right now
# How many times each video has been cancelled since the app started. clear()
# leaves it: core/pipeline.py clears the flag as a run starts, so a Cancel
# pressed before then would otherwise leave no trace (core/remake.py).
_requested: dict[str, int] = {}


def set_active(video_id: str | None) -> None:
    global _active
    with _lock:
        _active = video_id


def active_video() -> str | None:
    """The video currently being processed, or None. A video stuck in an
    in-progress DB status but NOT equal to this was orphaned by a crash/kill
    and is safe to delete."""
    with _lock:
        return _active


class CancelledError(Exception):
    """Raised inside the pipeline when its video has been cancelled."""


def request_cancel(video_id: str) -> None:
    with _lock:
        _cancelled.add(video_id)
        _requested[video_id] = _requested.get(video_id, 0) + 1


def times_requested(video_id: str | None) -> int:
    """How many times this video has been cancelled since the app started.
    Unlike is_cancelled(), clear() never resets it, so comparing it with an
    earlier reading tells whether a Cancel came in between."""
    with _lock:
        return _requested.get(video_id, 0) if video_id else 0


def is_cancelled(video_id: str) -> bool:
    with _lock:
        return video_id in _cancelled


def check(video_id: str) -> None:
    """Raise if this video was cancelled — call at stage boundaries."""
    if is_cancelled(video_id):
        raise CancelledError(video_id)


def check_active() -> None:
    """Raise if the video being processed right now has been cancelled.

    Stage boundaries are not enough. The two slowest stages — transcription
    and scoring — each run for many minutes in one call, and neither knows
    the video id: `transcribe()` is handed a path, `find_clips()` a list of
    segments. Cancel therefore did nothing until the whole stage finished,
    which on a long video with a big model is hours. The UI said
    "cancelling" and meant it; there was simply nobody listening.

    The worker runs one video at a time and already records which one, so
    deep code can ask "should I stop?" without a video id being threaded
    through every signature it sits behind.

    Cheap enough for a per-iteration call: one lock and a set lookup.
    """
    with _lock:
        current = _active
        stop = current is not None and current in _cancelled
    if stop:
        raise CancelledError(current)


def clear(video_id: str) -> None:
    with _lock:
        _cancelled.discard(video_id)


def wait(thread: threading.Thread, timeout: float | None = None, video_id: str | None = None) -> None:
    """thread.join(timeout), except that it stops waiting, raising
    CancelledError, as soon as the video is cancelled.

    The background passes over a whole video (its sound and picture, the chat
    replay) can run for many minutes on a long one, and a plain join() left
    Cancel unanswered for all of them: on a 3h46m Twitch VOD the person
    pressed Cancel six times and paused the queue while it carried on (#113)."""
    deadline = None if timeout is None else time.monotonic() + timeout
    while thread.is_alive():
        if video_id is not None:
            check(video_id)
        check_active()
        left = 0.5 if deadline is None else min(0.5, deadline - time.monotonic())
        if left <= 0:
            return
        thread.join(left)


def run(cmd: list[str]) -> subprocess.CompletedProcess:
    """subprocess.run(cmd, capture_output=True) for a long FFmpeg pass,
    except that the process is stopped and CancelledError raised as soon as
    the video being processed is cancelled, rather than decoding the rest of
    a long video for nobody."""
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    errors: list[bytes] = []
    reader = threading.Thread(target=lambda: errors.append(proc.stderr.read()), daemon=True)
    reader.start()
    chunks: list[bytes] = []
    try:
        while True:
            chunk = proc.stdout.read1(1 << 20)
            if not chunk:
                break
            chunks.append(chunk)
            check_active()
    except CancelledError:
        proc.kill()
        proc.wait()
        raise
    proc.wait()
    reader.join(5)
    return subprocess.CompletedProcess(cmd, proc.returncode, b"".join(chunks), errors[0] if errors else b"")
