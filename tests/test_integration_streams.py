"""Streams handed over by an integration such as the OBS plugin.

These pin down the promise to the streamer: one job per stream, the VOD found
without lifting a finger when the platform allows it, a clear ask when it does
not, and never starting other videos along with theirs.
"""

import json

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")
pytest.importorskip("yt_dlp")  # sources.dispatch imports the platform modules

from fastapi import FastAPI
from fastapi.testclient import TestClient

from core import queue
from core.state import StateDB
from server import integrations
from sources.dispatch import identify
from sources.vod_finder import FoundVod

TWITCH_VOD = "https://www.twitch.tv/videos/2869889709"
KICK_VOD = "https://kick.com/somechannel/videos/12345678-1234-1234-1234-123456789abc"
STARTED, ENDED = 1_700_000_000.0, 1_700_007_200.0
SESSION = "obs-session-0001"


class FakeWorker:
    def __init__(self):
        self.snapshot = None

    def notify(self):
        pass

    def progress_snapshot(self, job_id):
        return self.snapshot


class FakeBroadcaster:
    def __init__(self):
        self.events = []

    def publish(self, event):
        self.events.append(event)


class Env:
    def __init__(self, tmp_path):
        self.now = ENDED + 60
        self.found = None
        self.lookups = 0
        self.db_path = tmp_path / "state.db"
        self.worker = FakeWorker()
        app = FastAPI()
        self.watcher = integrations.install(
            app,
            db=self.db,
            worker=self.worker,
            broadcaster=FakeBroadcaster(),
            finder=self.finder,
            clock=lambda: self.now,
        )
        self.client = TestClient(app)

    def db(self):
        return StateDB(self.db_path)

    def finder(self, platform, channel, started_at, ended_at):
        self.lookups += 1
        return self.found

    def post(self, **overrides):
        body = {
            "session_id": SESSION, "source": "obs", "platform": "twitch",
            "channel": "emjayplayss", "started_at": STARTED, "ended_at": ENDED,
            "preset": "standard", **overrides,
        }
        return self.client.post("/integrations/streams", json=body)

    def get(self):
        return self.client.get(f"/integrations/streams/{SESSION}").json()

    def publish_vod(self, url=TWITCH_VOD):
        self.found = FoundVod(url=url, started_at=STARTED, duration=ENDED - STARTED)
        self.watcher.tick()

    def run(self, fn):
        d = self.db()
        try:
            return fn(d)
        finally:
            d.close()


@pytest.fixture
def env(tmp_path):
    return Env(tmp_path)


def test_a_stream_waits_for_its_vod(env):
    response = env.post()
    assert response.status_code == 200
    assert response.json()["created"] is True
    assert response.json()["state"] == "waiting_for_vod"


def test_the_same_stream_posted_twice_is_one_stream(env):
    env.post()
    again = env.post(channel="someone-else").json()
    assert again["created"] is False
    assert again["channel"] == "emjayplayss"


@pytest.mark.parametrize(
    "overrides, message",
    [({"platform": "kick"}, "Kick"), ({"channel": ""}, "channel name")],
)
def test_it_asks_for_the_link_when_it_cannot_look(env, overrides, message):
    body = env.post(**overrides).json()
    assert body["state"] == "needs_link"
    assert message in body["error"]


@pytest.mark.parametrize(
    "overrides, status",
    [
        ({"platform": "facebook"}, 400),
        ({"preset": "gaming"}, 400),
        ({"ended_at": STARTED - 1}, 400),
        ({"session_id": "x"}, 422),
    ],
)
def test_bad_requests_are_refused(env, overrides, status):
    assert env.post(**overrides).status_code == status


def test_a_found_vod_is_queued_and_started_when_nothing_else_waits(env):
    env.run(lambda d: queue.set_paused(d, True))
    env.post()
    env.publish_vod()
    body = env.get()
    assert body["state"] == "queued"
    assert body["vod_url"] == TWITCH_VOD
    job = env.run(lambda d: d.get_job(body["job_id"]))
    assert json.loads(job["payload"])["url"] == TWITCH_VOD
    assert env.run(queue.is_paused) is False


def test_other_waiting_videos_are_never_started_with_it(env):
    def stage_a_video(d):
        queue.set_paused(d, True)
        d.add_job("process", json.dumps({"url": "https://www.youtube.com/watch?v=aaaaaaaaaaa"}),
                  video_id="aaaaaaaaaaa")

    env.run(stage_a_video)
    env.post()
    env.publish_vod()
    body = env.get()
    assert body["state"] == "queued"
    assert body["waiting_behind"] == 1
    assert body["queue_paused"] is True


def test_not_published_yet_looks_again_later_then_asks(env):
    env.post()
    env.watcher.tick()
    assert env.lookups == 1
    env.watcher.tick()  # not due again yet
    assert env.lookups == 1
    env.now += integrations.CHECK_EVERY_SECONDS
    env.watcher.tick()
    assert env.lookups == 2
    env.now = ENDED + integrations.GIVE_UP_AFTER_SECONDS + 1
    env.watcher.tick()
    body = env.get()
    assert body["state"] == "needs_link"
    assert "past broadcasts" in body["error"]


def test_a_vod_already_queued_is_not_queued_twice(env):
    _, vid = identify(TWITCH_VOD)
    existing = env.run(lambda d: d.add_job("process", json.dumps({"url": TWITCH_VOD}), video_id=vid))
    env.post()
    env.publish_vod()
    assert env.get()["job_id"] == existing
    assert len(env.run(lambda d: d.queued_jobs())) == 1


def test_a_vod_already_processed_is_complete_straight_away(env):
    _, vid = identify(TWITCH_VOD)

    def processed_earlier(d):
        d.upsert_video(vid, title="earlier run")
        d.set_video_status(vid, "done")

    env.run(processed_earlier)
    env.post()
    env.publish_vod()
    body = env.get()
    assert body["state"] == "complete"
    assert body["clips"] == 0


def test_a_pasted_link_queues_once(env):
    env.post(platform="kick", channel="")
    link = f"/integrations/streams/{SESSION}/link"
    first = env.client.post(link, json={"url": KICK_VOD}).json()
    second = env.client.post(link, json={"url": KICK_VOD}).json()
    assert first["state"] == "queued"
    assert second["job_id"] == first["job_id"]
    assert len(env.run(lambda d: d.queued_jobs())) == 1


def test_a_link_that_is_not_a_video_is_refused(env):
    env.post(platform="kick", channel="")
    body = env.client.post(f"/integrations/streams/{SESSION}/link", json={"url": "local:abc"}).json()
    assert body["state"] == "needs_link"
    assert len(env.run(lambda d: d.queued_jobs())) == 0


def test_cancelling_a_queued_stream_removes_its_job(env):
    env.post()
    env.publish_vod()
    body = env.client.delete(f"/integrations/streams/{SESSION}").json()
    assert body["state"] == "cancelled"
    assert env.run(lambda d: d.queued_jobs()) == []


def test_a_running_stream_shows_progress_and_time_left(env):
    env.post()
    env.publish_vod()
    env.run(lambda d: d.claim_next_job())
    env.worker.snapshot = {
        "stage": "render", "label": "Rendering clips", "percent": 80,
        "eta_seconds": 120, "elapsed_seconds": 480,
    }
    body = env.get()
    assert body["state"] == "processing"
    assert body["progress"]["percent"] == 80
    assert body["progress"]["eta_seconds"] == 120


def test_an_unknown_stream_is_404(env):
    assert env.client.get("/integrations/streams/does-not-exist").status_code == 404


def test_presets_are_only_existing_options(env):
    ids = [p["id"] for p in env.client.get("/integrations/presets").json()]
    assert ids == ["standard", "podcast", "long_clips", "highlights"]
