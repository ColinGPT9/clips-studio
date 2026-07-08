"""Process-wide progress event hook.

The pipeline emits structured events at stage boundaries; by default nothing
listens (CLI runs just print as before). The API server installs a handler
that broadcasts events to UI clients over WebSocket.

Deliberately tiny: no queues, no threads — just a settable callback that can
never break the pipeline.
"""

import threading
from typing import Callable

_handler: Callable[[dict], None] | None = None
_local = threading.local()


def set_handler(handler: Callable[[dict], None] | None) -> None:
    global _handler
    _handler = handler


def set_thread_tags(**tags) -> None:
    """Override fields on every event emitted from THIS thread. The download
    prefetcher uses this to restamp its events (stage='prefetch') so the UI
    never attributes them to the job that is currently running."""
    _local.tags = tags or None


def emit(**event) -> None:
    if _handler is None:
        return
    tags = getattr(_local, "tags", None)
    if tags:
        event = {**event, **tags}
    try:
        _handler(event)
    except Exception:
        pass  # a broken UI listener must never kill a render
