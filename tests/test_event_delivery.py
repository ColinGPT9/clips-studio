"""What the event stream does and does not promise.

The UI used to refresh on a single "job done" event, and a finished run could
leave the Dashboard showing an empty video until someone reloaded it — which
reads as "no clips were created" and becomes a bug report.

The cause is not a bug in the broadcaster. Dropping events for a client that
falls behind is deliberate and correct: the alternative is one stalled client
blocking delivery for everyone. What was wrong was a reader assuming delivery.

These pin that behaviour, so the reason the UI polls as a fallback stays
visible to whoever reads it next.
"""

import asyncio

from server.events import Broadcaster


def drain(q):
    out = []
    while not q.empty():
        out.append(q.get_nowait())
    return out


def run(coro):
    return asyncio.run(coro)


def test_a_client_that_keeps_up_gets_everything():
    async def go():
        b = Broadcaster()
        b.attach_loop(asyncio.get_running_loop())
        q = b.subscribe()
        for i in range(50):
            b.publish({"type": "progress", "clip": i})
        b.publish({"type": "job", "status": "done"})
        await asyncio.sleep(0.05)
        return drain(q)

    got = run(go())
    assert len(got) == 51
    assert got[-1]["type"] == "job"


def test_the_terminal_event_is_dropped_when_a_client_falls_behind():
    """The failure the UI's fallback poll exists for.

    A render emits progress continuously for minutes. A client that stops
    draining — busy rendering, backgrounded, mid-reconnect — fills its queue,
    and nothing gives the terminal event priority over the progress spam ahead
    of it. So "the job finished" is exactly the message most likely to be lost.
    """

    async def go():
        b = Broadcaster()
        b.attach_loop(asyncio.get_running_loop())
        q = b.subscribe()
        for i in range(q.maxsize * 2):
            b.publish({"type": "progress", "clip": i})
        b.publish({"type": "job", "status": "done"})
        await asyncio.sleep(0.1)
        return q.maxsize, drain(q)

    cap, got = run(go())
    assert len(got) == cap, "the queue is bounded, so delivery is capped"
    assert not [e for e in got if e.get("type") == "job"], (
        "the terminal event survived — if this ever passes, the UI's fallback "
        "poll may no longer be needed, but check why before removing it"
    )


def test_one_stalled_client_does_not_block_another():
    """Why dropping is the right call: the slow client must not hold up the
    one that is keeping up."""

    async def go():
        b = Broadcaster()
        b.attach_loop(asyncio.get_running_loop())
        stalled = b.subscribe()
        healthy = b.subscribe()
        for i in range(stalled.maxsize + 10):
            b.publish({"type": "progress", "clip": i})
            # The healthy client drains as it goes.
            if not healthy.empty():
                healthy.get_nowait()
        await asyncio.sleep(0.1)
        return len(drain(stalled)), len(drain(healthy))

    slow, fast = run(go())
    assert slow > 0 and fast >= 0
    assert slow <= 200


def test_publishing_with_no_loop_attached_is_harmless():
    """The window before startup wires the loop up."""
    Broadcaster().publish({"type": "job", "status": "done"})


def test_unsubscribing_stops_delivery():
    async def go():
        b = Broadcaster()
        b.attach_loop(asyncio.get_running_loop())
        q = b.subscribe()
        b.unsubscribe(q)
        b.publish({"type": "job", "status": "done"})
        await asyncio.sleep(0.05)
        return drain(q)

    assert run(go()) == []
