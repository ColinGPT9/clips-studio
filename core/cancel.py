"""Cooperative cancellation for in-flight video processing.

The worker runs one video at a time in a thread. Killing a thread mid-FFmpeg
is unsafe, so cancellation is cooperative: the API marks a video_id cancelled,
and the pipeline checks at every stage boundary (and inside the download
progress hook and render loop), raising CancelledError to unwind cleanly.
"""

import threading

_lock = threading.Lock()
_cancelled: set[str] = set()


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


def clear(video_id: str) -> None:
    with _lock:
        _cancelled.discard(video_id)
