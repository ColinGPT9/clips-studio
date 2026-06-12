"""Process-wide progress event hook.

The pipeline emits structured events at stage boundaries; by default nothing
listens (CLI runs just print as before). The API server installs a handler
that broadcasts events to UI clients over WebSocket.

Deliberately tiny: no queues, no threads — just a settable callback that can
never break the pipeline.
"""

from typing import Callable

_handler: Callable[[dict], None] | None = None


def set_handler(handler: Callable[[dict], None] | None) -> None:
    global _handler
    _handler = handler


def emit(**event) -> None:
    if _handler is None:
        return
    try:
        _handler(event)
    except Exception:
        pass  # a broken UI listener must never kill a render
