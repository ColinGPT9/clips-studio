"""Thread-safe event broadcasting to WebSocket clients.

The pipeline worker runs in a plain thread; FastAPI's WebSockets live on the
asyncio loop. publish() may be called from any thread — events hop onto the
loop via call_soon_threadsafe and fan out to every subscribed client queue.
"""

import asyncio


class Broadcaster:
    def __init__(self):
        self._clients: set[asyncio.Queue] = set()
        self._loop: asyncio.AbstractEventLoop | None = None

    def attach_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop

    def publish(self, event: dict) -> None:
        """Callable from any thread."""
        if self._loop is None or self._loop.is_closed():
            return
        self._loop.call_soon_threadsafe(self._fanout, event)

    def _fanout(self, event: dict) -> None:
        for queue in list(self._clients):
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                pass  # slow client: drop events rather than block everyone

    def subscribe(self) -> asyncio.Queue:
        queue: asyncio.Queue = asyncio.Queue(maxsize=200)
        self._clients.add(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue) -> None:
        self._clients.discard(queue)


broadcaster = Broadcaster()
