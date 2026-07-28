"""Cooperative cancellation for in-flight video processing.

The worker runs one video at a time in a thread. Killing a thread mid-FFmpeg
is unsafe, so cancellation is cooperative: the API marks a video_id cancelled,
and the pipeline checks at every stage boundary (and inside the download
progress hook and render loop), raising CancelledError to unwind cleanly.
"""

import threading

_lock = threading.Lock()
_cancelled: set[str] = set()
_active: str | None = None  # the video the worker is processing right now


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
