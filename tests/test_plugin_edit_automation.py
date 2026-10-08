"""A plugin chosen to suggest edits that didn't run never holds a post.

Its suggestions only wait in the editor for the creator to use or hide, and
no clip changes without them, so a watched channel's automatic post and a
job's "then publish" go ahead without it. A rater that didn't run still holds
them (tests/test_automation.py, tests/test_chat_follow_through.py).
"""

import json

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi import FastAPI
from fastapi.testclient import TestClient

from core.state import StateDB
from server import automation
from sources.channel_feed import Channel, NewSourceVideo, Readiness

UC = "UC0123456789abcdefABCDEF"
START = 1_800_000_000.0
INTERVAL = 15 * 60
RATER = {"id": "example-dev/quarkbloom-rater"}
TRIMMER = {"id": "fixture-dev/trimmer"}


def yt(vid):
    return NewSourceVideo("youtube", UC, vid, f"https://www.youtube.com/watch?v={vid}", f"Video {vid}", 0.0)


class FakeFeed:
    def __init__(self):
        self.listings: dict[str, list] = {}

    def resolve(self, platform, text):
        return Channel(platform, text, f"Channel {text}")

    def latest(self, platform, channel_key):
        return list(self.listings.get(channel_key, []))

    def readiness(self, url, min_seconds=0):
        return Readiness("ready", duration=3600)


class FakeWorker:
    def notify(self):
        """The watcher wakes the worker after queueing; nothing runs here."""

    def progress_snapshot(self, job_id):
        return {"fraction": 0.5}


class FakeBroadcaster:
    def publish(self, event):
        """The app's live updates; nobody listens here."""


class Env:
    """A watcher on the clock of the test, with a publisher that records what it is asked to post."""

    def __init__(self, tmp_path):
        self.now = START
        self.feed = FakeFeed()
        self.db_path = tmp_path / "state.db"
        self.published: list[dict] = []
        app = FastAPI()
        self.watcher = automation.install(
            app, db=self.db, worker=FakeWorker(), broadcaster=FakeBroadcaster(),
            options_from=dict, feed=self.feed, clock=lambda: self.now,
            interval_minutes=15, publisher=self.publisher, data_dir=tmp_path,
        )
        self.client = TestClient(app, base_url="http://127.0.0.1")

    def publisher(self, d, **kwargs):
        self.published.append(kwargs)
        return {"started": [{"clip_id": c, "scheduled_for": ""} for c in kwargs["clip_ids"]], "skipped": []}

    def db(self):
        return StateDB(self.db_path)

    def run(self, fn):
        d = self.db()
        try:
            return fn(d)
        finally:
            d.close()

    def later(self, seconds=INTERVAL + 1):
        self.now += seconds
        self.watcher.tick()

    def item(self, video_id):
        return next(i for i in self.client.get("/automation/items").json() if i["video_id"] == video_id)


@pytest.fixture
def env(tmp_path):
    e = Env(tmp_path)
    assert e.client.patch("/automation", json={"enabled": True}).status_code == 200
    return e


def _rated(**changes) -> dict:
    """A rater's entry in a video outcome's `steps` (plugins/steps.after_finding)."""
    return {"plugin": RATER["id"], "version": "1.0.0", "name": "Quarkbloom Rater", "steps": ["rate"],
            "ok": True, "given": 3, "noted": 0, "rated": 3, "set_aside": 0, **changes}


def _edited(**changes) -> dict:
    """An edit plugin's entry in a video outcome's `steps` (plugins/steps.suggest_edits)."""
    return {"plugin": TRIMMER["id"], "version": "1.0.0", "name": "Trimmer", "steps": ["edit"], "ok": True,
            "given": 3, "suggested": 2, "noted": 0, "rated": 0, "set_aside": 0, **changes}


EDIT_FAILED = _edited(ok=False, suggested=0, error="It isn't installed any more.")
# What each job chose, and the outcome of its run, in which the edit plugin didn't run.
CHOSEN = [({"edit": [TRIMMER]}, [EDIT_FAILED]),
          ({"rate": [RATER], "edit": [TRIMMER]}, [_rated(), EDIT_FAILED])]


def _watched_and_made(env, options, steps):
    """A watch posting automatically with `options`, whose newnewnew01 was made into 3 clips."""
    env.feed.listings[UC] = []
    response = env.client.post("/automation/watches", json={"platform": "youtube", "channel": UC})
    assert response.status_code == 200, response.text
    watch = response.json()
    env.watcher.tick()
    env.client.patch(f"/automation/watches/{watch['id']}", json={
        "publish": {"mode": "auto", "platforms": ["youtube"]}, "options": options})
    env.feed.listings[UC] = [yt("newnewnew01")]
    env.later()

    def made(d):
        job = d.job_for_video("newnewnew01", ("queued", "running"))
        assert json.loads(job["payload"])["edit"] == [TRIMMER]
        d.finish_job(job["id"], "done")
        d.upsert_video("newnewnew01", title="t")
        d.set_video_status("newnewnew01", "done")
        for i in range(3):
            d.add_clip("newnewnew01", i * 10.0, i * 10.0 + 8, 90 - i, f"hook {i}")
        d.set_outcome("newnewnew01", {"clips": 3, "candidates": 3, "min_score": 55, "steps": steps})

    env.run(made)


@pytest.mark.parametrize(("options", "steps"), CHOSEN, ids=["edit", "rate-and-edit"])
def test_a_failed_edit_plugin_holds_no_automatic_post(env, options, steps):
    _watched_and_made(env, options, steps)
    env.later(5)
    env.later(5)
    assert len(env.published) == 1
    assert len(env.published[0]["clip_ids"]) == 3
    item = env.item("newnewnew01")
    assert (item["publish_state"], item["publish_error"]) == ("done", "")


def test_a_failed_rater_beside_it_still_holds_the_post_and_names_only_the_rater(env):
    _watched_and_made(env, {"rate": [RATER], "edit": [TRIMMER]},
                      [_rated(ok=False, rated=0, error="It isn't installed any more."), EDIT_FAILED])
    env.later(5)
    env.later(5)
    assert env.published == []
    item = env.item("newnewnew01")
    assert item["publish_state"] == "ask"
    assert item["publish_error"] == (
        "Clips Kitty made these clips without Quarkbloom Rater, so they weren't posted automatically. "
        "Check them and publish, or turn off Rate & understand in this channel's settings.")


# ---- a job's "then publish" (server/jobs.py) ----------------------------------------


class _OutcomeDB:
    """A video with these clips whose outcome (core/outcome.py) is `outcome`."""

    def __init__(self, clips, outcome):
        self._clips = clips
        self._outcome = outcome

    def clips_for_video(self, video_id):
        return self._clips

    def get_outcome(self, video_id):
        return self._outcome


def _capture_publish(monkeypatch, tmp_path):
    jobs = pytest.importorskip("server.jobs")
    woop = pytest.importorskip("server.woopsocial_service")
    called = {}
    monkeypatch.setattr(woop, "is_enabled", lambda db: True)
    monkeypatch.setattr(woop, "has_key", lambda d: True)
    monkeypatch.setattr(woop, "publish_clips",
                        lambda db, data_dir, **kw: called.update(kw) or {"started": [], "skipped": []})
    worker = jobs.Worker.__new__(jobs.Worker)
    worker.config = {"paths": {"data_dir": str(tmp_path)}}
    return worker, called


PUBLISH = {"action": "publish", "platforms": ["youtube"]}


@pytest.mark.parametrize(("options", "steps"), CHOSEN, ids=["edit", "rate-and-edit"])
def test_then_publish_goes_ahead_after_a_failed_edit_plugin(monkeypatch, tmp_path, capsys, options, steps):
    worker, called = _capture_publish(monkeypatch, tmp_path)
    db = _OutcomeDB([{"id": 1}, {"id": 2}], {"clips": 2, "steps": steps})
    worker._run_follow_up(db, {"video_id": "abc"}, {"then": PUBLISH, **options})
    assert called["clip_ids"] == [1, 2]
    out = capsys.readouterr().out
    assert "Publish skipped" not in out
    assert out.startswith("  Publishing 2 clip(s) to youtube...\n")


def test_then_publish_after_a_failed_rater_names_only_the_rater(monkeypatch, tmp_path, capsys):
    worker, called = _capture_publish(monkeypatch, tmp_path)
    steps = [_rated(ok=False, rated=0, error="It isn't installed any more."), EDIT_FAILED]
    db = _OutcomeDB([{"id": 1}, {"id": 2}], {"clips": 2, "steps": steps})
    worker._run_follow_up(db, {"video_id": "abc"}, {"then": PUBLISH, "rate": [RATER], "edit": [TRIMMER]})
    assert called == {}
    assert capsys.readouterr().out == ("  Publish skipped: Clips Kitty made these clips without Quarkbloom Rater, "
                                       "so they wait for you to publish them.\n")
