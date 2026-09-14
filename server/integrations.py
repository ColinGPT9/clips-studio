"""Integrations: a streamer tool hands a finished livestream to Clips Kitty.

The OBS plugin is the first client. It decides that a stream has really ended
(Clips Kitty is not even running while someone is live), then posts the stream
here. From that point the work is Clips Kitty's: find the VOD that Twitch or
YouTube publishes after the stream, queue it once, and report progress in terms
any dock can show. It lives here, once, so every integration behaves the same
and nobody building on the API has to rebuild it.

Supported API, documented in docs/API.md. Shapes change only with a note in
CHANGELOG.md, and an incompatible change bumps API_VERSION in server/api.py.
"""

import json
import threading
import time
import traceback
from collections.abc import Callable

from fastapi import HTTPException
from pydantic import BaseModel, Field

from core import cancel, queue
from core.state import StateDB

PLATFORMS = ("twitch", "youtube", "kick")

# How long to keep looking for a VOD before asking for the link instead. Twitch
# publishes within minutes and YouTube can take longer on a long stream, but past
# two hours something else is wrong (past broadcasts off, the VOD private) and
# the streamer is better told than left waiting.
GIVE_UP_AFTER_SECONDS = 2 * 60 * 60
CHECK_EVERY_SECONDS = 5 * 60
WATCH_TICK_SECONDS = 30

# Named bundles of options the pipeline already has. None of these is a new
# processing mode: a dock shows these names instead of raw option flags.
PRESETS = {
    "standard": {
        "name": "Standard",
        "description": "Vertical clips with captions.",
        "options": {},
    },
    "podcast": {
        "name": "Podcast",
        "description": "Letterboxed framing for multi-camera podcasts, without subject tracking.",
        "options": {"podcast": True},
    },
    "long_clips": {
        "name": "Long clips",
        "description": "Clips between 61 and 180 seconds long.",
        "options": {"long_clips": True},
    },
    "highlights": {
        "name": "Stream highlights",
        "description": "A horizontal highlights video of the stream.",
        "options": {"longform": {"mode": "highlights"}},
    },
}

_NEEDS_LINK_KICK = "Kick doesn't let Clips Kitty find past broadcasts yet. Paste the VOD link."
_NEEDS_LINK_CHANNEL = "Add your channel name to find the VOD automatically, or paste the VOD link."
_NOT_FOUND = (
    "Couldn't find this stream's VOD. Check that past broadcasts are turned on and "
    "public, or paste the VOD link."
)

_JOB_STATES = {
    "queued": "queued",
    "running": "processing",
    "done": "complete",
    "failed": "error",
    "cancelled": "cancelled",
}


class StreamIn(BaseModel):
    session_id: str = Field(pattern=r"^[A-Za-z0-9-]{8,64}$")
    source: str = Field(default="", max_length=32)
    platform: str
    channel: str = Field(default="", max_length=64)
    started_at: float
    ended_at: float
    preset: str = "standard"


class LinkIn(BaseModel):
    url: str = Field(min_length=1, max_length=500)


def _clip_count(d: StateDB, video_id: str) -> int:
    return d.conn.execute(
        "SELECT COUNT(*) FROM clips WHERE video_id = ?", (video_id,)
    ).fetchone()[0]


def view(d: StateDB, row, worker) -> dict:
    """A stream as an integration should show it.

    Once a job exists its status is the truth, read live from the queue, so the
    stream never needs a second copy of state that could drift from it."""
    out = {
        key: row[key]
        for key in (
            "session_id", "source", "platform", "channel", "started_at", "ended_at",
            "preset", "state", "vod_url", "video_id", "job_id", "waiting_behind", "error",
        )
    }
    if row["job_id"]:
        job = d.get_job(row["job_id"])
        if job is None:
            out["state"] = "cancelled"
            out["error"] = "Removed from the Clips Kitty queue."
        elif row["state"] != "cancelled" or job["status"] in ("running", "done"):
            out["state"] = _JOB_STATES.get(job["status"], row["state"])
            if job["status"] == "failed":
                out["error"] = "Clips Kitty couldn't finish this stream."
                out["details"] = (job["error"] or "")[:500]
    if out["state"] == "queued":
        out["waiting_behind"] = queue.waiting_ahead(d, row["job_id"])
        out["queue_paused"] = queue.is_paused(d)
    elif out["state"] == "processing":
        out["progress"] = worker.progress_snapshot(row["job_id"])
    elif out["state"] == "complete" and row["video_id"]:
        out["clips"] = _clip_count(d, row["video_id"])
    return out


def queue_vod(d: StateDB, row, url: str, worker, broadcaster) -> None:
    """Queue the VOD for a stream, exactly once, and record where it went."""
    from sources.dispatch import identify

    source, vid = identify(url)
    if source == "local" or not vid:
        d.set_stream(row["session_id"], state="needs_link",
                     error="That link isn't a video Clips Kitty can open.")
        return
    if d.video_status(vid) == "done":
        d.set_stream(row["session_id"], state="complete", vod_url=url, video_id=vid,
                     job_id=0, error="")
        return
    job_id = queue.duplicate_of(d, vid)
    if job_id is None:
        if queue.capacity(d) <= 0:
            d.set_stream(row["session_id"], state="error", vod_url=url, video_id=vid,
                         error=f"The Clips Kitty queue is full ({queue.MAX_ACTIVE} videos).")
            return
        preset = PRESETS.get(row["preset"]) or PRESETS["standard"]
        payload = {"url": url, **preset["options"]}
        title = f"{row['channel'] or row['platform'].title()} stream"
        job_id = d.add_job("process", json.dumps(payload), video_id=vid, title=title)
    queue.start_if_alone(d, job_id)
    d.set_stream(row["session_id"], state="queued", vod_url=url, video_id=vid, job_id=job_id,
                 waiting_behind=queue.waiting_ahead(d, job_id), error="")
    worker.notify()
    broadcaster.publish({"type": "queue"})


class StreamWatcher(threading.Thread):
    """Looks for the VODs of ended streams a few times an hour, and queues them.

    Deliberately slow: a VOD appears minutes to an hour after a stream, and each
    look is a couple of requests to the platform."""

    def __init__(self, db: Callable[[], StateDB], finder, on_found, clock=time.time):
        super().__init__(daemon=True, name="stream-watcher")
        self._db = db
        self._finder = finder
        self._on_found = on_found
        self._clock = clock
        self._wake = threading.Event()
        self._stop = threading.Event()

    def wake(self) -> None:
        self._wake.set()

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()

    def run(self) -> None:
        while not self._stop.is_set():
            try:
                self.tick()
            except Exception:
                traceback.print_exc()  # the watcher must outlive any one failure
            self._wake.wait(timeout=WATCH_TICK_SECONDS)
            self._wake.clear()

    def tick(self) -> None:
        now = self._clock()
        d = self._db()
        try:
            for row in d.streams_due(now):
                if now - row["ended_at"] > GIVE_UP_AFTER_SECONDS:
                    d.set_stream(row["session_id"], state="needs_link", error=_NOT_FOUND)
                    continue
                try:
                    found = self._finder(
                        row["platform"], row["channel"], row["started_at"], row["ended_at"]
                    )
                except Exception as e:
                    print(f"VOD lookup failed for stream {row['session_id']}: {e}")
                    found = None
                if found is None:
                    d.set_stream(row["session_id"], next_check_at=now + CHECK_EVERY_SECONDS)
                    continue
                self._on_found(d, row, found.url)
        finally:
            d.close()


def install(app, *, db: Callable[[], StateDB], worker, broadcaster,
            finder=None, clock=time.time) -> StreamWatcher:
    """Add the integration routes to `app`. Returns the watcher for the caller
    to start and stop with the rest of the server."""
    from sources.vod_finder import clean_handle, find_stream_vod

    watcher = StreamWatcher(
        db,
        finder or find_stream_vod,
        lambda d, row, url: queue_vod(d, row, url, worker, broadcaster),
        clock,
    )

    def load(d: StateDB, session_id: str):
        row = d.get_stream(session_id)
        if row is None:
            raise HTTPException(404, "no such stream")
        return row

    @app.get("/integrations/presets")
    def list_presets():
        return [{"id": key, **value} for key, value in PRESETS.items()]

    @app.post("/integrations/streams")
    def create_stream(body: StreamIn):
        if body.platform not in PLATFORMS:
            raise HTTPException(400, f"unsupported platform '{body.platform}'")
        if body.preset not in PRESETS:
            raise HTTPException(400, f"unknown preset '{body.preset}'")
        if body.ended_at < body.started_at:
            raise HTTPException(400, "ended_at is before started_at")
        if body.platform == "kick":
            state, error = "needs_link", _NEEDS_LINK_KICK
        elif clean_handle(body.channel) is None:
            state, error = "needs_link", _NEEDS_LINK_CHANNEL
        else:
            state, error = "waiting_for_vod", ""
        d = db()
        try:
            created = d.insert_stream(
                body.session_id, source=body.source, platform=body.platform,
                channel=body.channel.strip(), started_at=body.started_at,
                ended_at=body.ended_at, preset=body.preset, state=state, error=error,
                next_check_at=clock(),
            )
            result = view(d, load(d, body.session_id), worker)
        finally:
            d.close()
        if created and state == "waiting_for_vod":
            watcher.wake()
        return {"created": created, **result}

    @app.get("/integrations/streams/{session_id}")
    def get_stream(session_id: str):
        d = db()
        try:
            return view(d, load(d, session_id), worker)
        finally:
            d.close()

    @app.post("/integrations/streams/{session_id}/link")
    def set_link(session_id: str, body: LinkIn):
        d = db()
        try:
            current = view(d, load(d, session_id), worker)
            if current["state"] in ("queued", "processing", "complete"):
                return current  # already handed over; a second click changes nothing
            queue_vod(d, load(d, session_id), body.url.strip(), worker, broadcaster)
            return view(d, load(d, session_id), worker)
        finally:
            d.close()

    @app.delete("/integrations/streams/{session_id}")
    def cancel_stream(session_id: str):
        d = db()
        try:
            row = load(d, session_id)
            current = view(d, row, worker)
            if current["state"] in ("complete", "cancelled"):
                return current
            job = d.get_job(row["job_id"]) if row["job_id"] else None
            if job is not None and job["status"] == "queued":
                queue.remove(d, job["id"])
            elif job is not None and job["status"] == "running" and row["video_id"]:
                cancel.request_cancel(row["video_id"])
            d.set_stream(session_id, state="cancelled")
            result = view(d, load(d, session_id), worker)
        finally:
            d.close()
        worker.notify()
        broadcaster.publish({"type": "queue"})
        return result

    return watcher
