"""Watched channels: a creator posts and Clips Kitty clips it, once.

The promises pinned here: adding a channel never queues its back catalogue, a
video becomes at most one job however it is seen (twice in one feed, by two
watches, after a restart, after a title change, or after being clipped by
hand), nothing live is handed to the downloader, content missed while the app
was closed is never silently lost, and nothing is published unless asked.
"""

import json
from pathlib import Path

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi import FastAPI
from fastapi.testclient import TestClient

from core import queue
from core.state import StateDB
from server import automation
from sources.channel_feed import Channel, NewSourceVideo, Readiness

UC = "UC0123456789abcdefABCDEF"
START = 1_800_000_000.0
INTERVAL = 15 * 60


def yt(vid, title=None, published_at=0.0, channel=UC):
    return NewSourceVideo("youtube", channel, vid, f"https://www.youtube.com/watch?v={vid}",
                          title or f"Video {vid}", published_at)


class FakeFeed:
    def __init__(self):
        self.listings: dict[str, list] = {}
        self.ready: dict[str, Readiness] = {}
        self.fail: Exception | None = None
        self.readiness_calls: list[str] = []

    def resolve(self, platform, text):
        return Channel(platform, text, f"Channel {text}")

    def latest(self, platform, channel_key):
        if self.fail:
            raise self.fail
        return list(self.listings.get(channel_key, []))

    def readiness(self, url, min_seconds=0):
        self.readiness_calls.append(url)
        found = self.ready.get(url, Readiness("ready", duration=3600))
        if found.state == "ready" and min_seconds and found.duration < min_seconds:
            return Readiness("skip", "Shorter than the minimum.", duration=found.duration)
        return found


class FakeWorker:
    def __init__(self):
        self.notified = 0

    def notify(self):
        self.notified += 1

    def progress_snapshot(self, job_id):
        return {"fraction": 0.5}


class FakeBroadcaster:
    def __init__(self):
        self.events = []

    def publish(self, event):
        self.events.append(event)


class Env:
    def __init__(self, tmp_path, feed=None, options_from=dict):
        self.now = START
        self.feed = feed or FakeFeed()
        self.db_path = tmp_path / "state.db"
        self.worker = FakeWorker()
        self.published: list[dict] = []
        self.publish_calls = 0
        self.publish_error: Exception | None = None
        self.data_dir = tmp_path
        app = FastAPI()
        self.watcher = automation.install(
            app, db=self.db, worker=self.worker, broadcaster=FakeBroadcaster(),
            options_from=options_from, feed=self.feed, clock=lambda: self.now,
            interval_minutes=15, publisher=self.publisher, data_dir=tmp_path,
        )
        self.client = TestClient(app, base_url="http://127.0.0.1")

    def publisher(self, d, **kwargs):
        self.publish_calls += 1
        if self.publish_error:
            raise self.publish_error
        self.published.append(kwargs)
        # Like publish_clips: a time for each clip when spaced, none when sent now.
        when = "2026-09-24T13:00:00+00:00" if kwargs.get("per_day") else ""
        return {"started": [{"clip_id": c, "scheduled_for": when} for c in kwargs["clip_ids"]],
                "skipped": []}

    def db(self):
        return StateDB(self.db_path)

    def run(self, fn):
        d = self.db()
        try:
            return fn(d)
        finally:
            d.close()

    def enable(self):
        assert self.client.patch("/automation", json={"enabled": True}).status_code == 200

    def add(self, key=UC, platform="youtube"):
        response = self.client.post("/automation/watches", json={"platform": platform, "channel": key})
        assert response.status_code == 200, response.text
        return response.json()

    def later(self, seconds=INTERVAL + 1):
        self.now += seconds
        self.watcher.tick()

    def jobs(self):
        return self.run(lambda d: [dict(r) for r in d.list_jobs(limit=100)])

    def items(self, **params):
        return self.client.get("/automation/items", params=params).json()

    def item(self, video_id):
        return next(i for i in self.items() if i["video_id"] == video_id)

    def finish(self, video_id, status="done"):
        def go(d):
            job = d.job_for_video(video_id, ("queued", "running"))
            d.finish_job(job["id"], status)
            d.upsert_video(video_id, title="t")
            d.set_video_status(video_id, "done" if status == "done" else "failed")
        self.run(go)


@pytest.fixture
def env(tmp_path):
    e = Env(tmp_path)
    e.enable()
    return e


def watched(env, *videos, key=UC):
    """A watch whose first look saw `videos` (the back catalogue)."""
    env.feed.listings[key] = list(videos)
    watch = env.add(key)
    env.watcher.tick()
    return watch


def test_nothing_happens_until_automation_is_switched_on(tmp_path):
    e = Env(tmp_path)
    e.feed.listings[UC] = [yt("aaaaaaaaaaa")]
    e.add()
    e.watcher.tick()
    assert e.items() == []
    assert e.client.get("/automation").json()["enabled"] is False


def test_adding_a_channel_never_queues_its_back_catalogue(env):
    watched(env, yt("aaaaaaaaaaa"), yt("bbbbbbbbbbb"))
    assert env.jobs() == []
    assert {i["status"] for i in env.items()} == {"earlier"}


def test_a_new_upload_is_queued_once(env):
    watched(env, yt("aaaaaaaaaaa"))
    env.feed.listings[UC].insert(0, yt("newnewnew01"))
    env.later()
    jobs = env.jobs()
    assert len(jobs) == 1
    assert jobs[0]["video_id"] == "newnewnew01"
    assert json.loads(jobs[0]["payload"])["url"] == "https://www.youtube.com/watch?v=newnewnew01"
    assert env.item("newnewnew01")["status"] == "queued"
    # Nothing else was waiting, so the watch's go-ahead started the queue.
    assert env.run(queue.is_paused) is False
    assert env.worker.notified >= 1


def test_the_same_video_seen_again_or_retitled_is_not_a_second_job(env):
    watched(env)
    env.feed.listings[UC] = [yt("newnewnew01")]
    env.later()
    env.feed.listings[UC] = [yt("newnewnew01", title="Retitled (and a new thumbnail)")]
    env.later()
    env.later()
    assert len(env.jobs()) == 1
    assert len(env.items()) == 1


def test_a_feed_that_repeats_an_entry_gives_one_job(env):
    watched(env)
    env.feed.listings[UC] = [yt("newnewnew01"), yt("newnewnew01")]
    env.later()
    assert len(env.jobs()) == 1


def test_two_watches_that_find_the_same_video_give_one_job(env):
    other = "UC" + "x" * 22
    watched(env)
    watched(env, key=other)
    env.feed.listings[UC] = [yt("newnewnew01")]
    env.feed.listings[other] = [yt("newnewnew01", channel=other)]
    env.later()
    assert len(env.jobs()) == 1
    assert len(env.items()) == 1


def test_a_restart_does_not_queue_anything_twice(env, tmp_path):
    watched(env)
    env.feed.listings[UC] = [yt("newnewnew01")]
    env.later()
    restarted = Env(tmp_path, feed=env.feed)
    restarted.now = env.now + INTERVAL + 1
    restarted.watcher.tick()
    restarted.later()
    assert len(restarted.jobs()) == 1


def test_a_video_already_clipped_by_hand_is_not_clipped_again(env):
    watched(env)
    env.run(lambda d: (d.upsert_video("newnewnew01"), d.set_video_status("newnewnew01", "done")))
    env.feed.listings[UC] = [yt("newnewnew01")]
    env.later()
    assert env.jobs() == []
    item = env.item("newnewnew01")
    assert item["status"] == "skipped"
    assert "Already clipped" in item["reason"]


def test_a_video_already_in_the_queue_is_joined_not_duplicated(env):
    watched(env)
    job_id = env.run(lambda d: queue.enqueue(
        d, "process", {"url": "https://www.youtube.com/watch?v=newnewnew01"},
        video_id="newnewnew01"))
    env.feed.listings[UC] = [yt("newnewnew01")]
    env.later()
    assert len(env.jobs()) == 1
    assert env.item("newnewnew01")["job_id"] == job_id


def test_a_full_queue_waits_and_then_queues(env):
    watched(env)
    for n in range(queue.MAX_ACTIVE):
        env.run(lambda d, n=n: queue.enqueue(d, "process", {"url": f"u{n}"}, video_id=f"other{n}"))
    env.feed.listings[UC] = [yt("newnewnew01")]
    env.later()
    item = env.item("newnewnew01")
    assert item["status"] == "waiting_for_queue"
    assert "room in the queue" in item["reason"]
    env.run(lambda d: d.finish_job(d.job_for_video("other0", ("queued",))["id"], "done"))
    env.later(5)
    assert env.item("newnewnew01")["status"] == "queued"


def test_staged_videos_are_not_started_by_a_watch(env):
    watched(env)
    env.run(lambda d: queue.enqueue(d, "process", {"url": "staged"}, video_id="staged01"))
    env.feed.listings[UC] = [yt("newnewnew01")]
    env.later()
    assert env.item("newnewnew01")["status"] == "queued"
    assert env.run(queue.is_paused) is True  # the user has not pressed Start


def test_a_live_video_waits_until_it_has_finished(env):
    watched(env)
    url = "https://www.youtube.com/watch?v=livelive001"
    env.feed.ready[url] = Readiness("not_yet", "Still live.")
    env.feed.listings[UC] = [yt("livelive001")]
    env.later()
    assert env.jobs() == []
    assert env.item("livelive001")["status"] == "waiting_for_video"
    env.later(60)  # not due yet: no second look within the recheck window
    assert env.feed.readiness_calls.count(url) == 1
    env.feed.ready[url] = Readiness("ready", duration=7200)
    env.later(automation.RECHECK_SECONDS)
    assert env.item("livelive001")["status"] == "queued"


def test_a_video_that_never_finishes_is_eventually_set_aside(env):
    watched(env)
    url = "https://www.youtube.com/watch?v=premiere001"
    env.feed.ready[url] = Readiness("not_yet", "Premieres next month.")
    env.feed.listings[UC] = [yt("premiere001")]
    env.later()
    env.later(automation.GIVE_UP_SECONDS + 1)
    assert env.item("premiere001")["status"] == "skipped"


def test_shorts_are_not_clipped(env):
    watched(env)
    env.feed.ready["https://www.youtube.com/watch?v=shortshort1"] = Readiness("ready", duration=41)
    env.feed.listings[UC] = [yt("shortshort1")]
    env.later()
    assert env.jobs() == []
    assert env.item("shortshort1")["status"] == "skipped"


def test_a_platform_that_cannot_be_read_says_so_and_loses_nothing(env):
    watched(env)
    env.feed.fail = OSError("503 Service Unavailable")
    env.later()
    watch = env.client.get("/automation/watches").json()[0]
    assert "503" in watch["last_error"]
    env.feed.fail = None
    env.feed.listings[UC] = [yt("newnewnew01")]
    env.later()
    watch = env.client.get("/automation/watches").json()[0]
    assert watch["last_error"] == ""
    assert len(env.jobs()) == 1


# ---- catching up after being closed ------------------------------------------


def closed_for_a_while(env, backlog, *videos):
    watch = watched(env)
    env.client.patch(f"/automation/watches/{watch['id']}", json={"backlog": backlog})
    env.feed.listings[UC] = list(videos)
    env.later(10 * INTERVAL)


def test_catch_up_newest_only_by_default(env):
    closed_for_a_while(env, "newest", yt("cccccccccc3"), yt("bbbbbbbbbb2"), yt("aaaaaaaaaa1"))
    assert [j["video_id"] for j in env.jobs()] == ["cccccccccc3"]
    skipped = [i for i in env.items() if i["status"] == "skipped"]
    assert len(skipped) == 2  # listed, one click from being clipped
    assert all("wasn't watching" in i["reason"] for i in skipped)


def test_catch_up_all(env):
    closed_for_a_while(env, "all", yt("cccccccccc3"), yt("bbbbbbbbbb2"))
    assert len(env.jobs()) == 2


def test_catch_up_none_keeps_everything_for_you_to_choose(env):
    closed_for_a_while(env, "none", yt("cccccccccc3"), yt("bbbbbbbbbb2"))
    assert env.jobs() == []
    assert len([i for i in env.items() if i["status"] == "skipped"]) == 2


def test_catch_up_last_day(env):
    now = START + 10 * INTERVAL  # when the catch-up look happens
    closed_for_a_while(
        env, "day",
        yt("cccccccccc3", published_at=now - 3600),
        yt("bbbbbbbbbb2", published_at=now - 2 * 3600),
        yt("aaaaaaaaaa1", published_at=now - 3 * 86400),
    )
    assert sorted(j["video_id"] for j in env.jobs()) == ["bbbbbbbbbb2", "cccccccccc3"]


def test_two_uploads_between_normal_looks_are_both_clipped(env):
    watched(env)
    env.feed.listings[UC] = [yt("cccccccccc3"), yt("bbbbbbbbbb2")]
    env.later()
    assert len(env.jobs()) == 2


def test_clip_this_takes_a_set_aside_video(env):
    watched(env, yt("aaaaaaaaaaa"))
    item = env.item("aaaaaaaaaaa")
    response = env.client.post(f"/automation/items/{item['id']}/queue")
    assert response.status_code == 200
    env.later(5)
    assert env.item("aaaaaaaaaaa")["status"] == "queued"


# ---- after the clips exist ---------------------------------------------------


def with_publish_mode(env, mode):
    watch = watched(env)
    env.client.patch(f"/automation/watches/{watch['id']}",
                     json={"publish": {"mode": mode, "platforms": ["youtube"]}})
    env.feed.listings[UC] = [yt("newnewnew01")]
    env.later()
    return watch


@pytest.mark.parametrize("mode, expected", [("off", "off"), ("ask", "ask")])
def test_finished_clips_wait_to_be_asked_or_stay_put(env, mode, expected):
    with_publish_mode(env, mode)
    env.later(5)
    assert env.item("newnewnew01")["publish_state"] == ""  # not done yet
    env.finish("newnewnew01")
    env.later(5)
    item = env.item("newnewnew01")
    assert item["status"] == "complete"
    assert item["publish_state"] == expected


def test_a_failed_run_is_not_published(env):
    with_publish_mode(env, "ask")
    env.finish("newnewnew01", status="failed")
    env.later(5)
    item = env.item("newnewnew01")
    assert item["status"] == "failed"
    assert item["publish_state"] == ""


def test_watch_options_reach_the_job(env):
    watch = watched(env)
    env.client.patch(f"/automation/watches/{watch['id']}",
                     json={"preset": "podcast", "options": {"max_clips": 4}})
    env.feed.listings[UC] = [yt("newnewnew01")]
    env.later()
    payload = json.loads(env.jobs()[0]["payload"])
    assert payload["podcast"] is True and payload["max_clips"] == 4
    assert "preset" not in payload


def test_legacy_cli_channels_come_across_switched_off(tmp_path):
    path = tmp_path / "old.db"
    d = StateDB(path)
    d.conn.execute("DELETE FROM app_state WHERE key = 'watches_imported'")
    d.add_channel(UC, "Some Channel")
    d.close()
    d = StateDB(path)
    rows = d.list_watches()
    d.close()
    assert [(r["channel_key"], r["enabled"]) for r in rows] == [(UC, 0)]


# ---- publishing --------------------------------------------------------------


def finished_with_clips(env, video_id="newnewnew01", n=3):
    env.finish(video_id)
    env.run(lambda d: [d.add_clip(video_id, i * 10.0, i * 10.0 + 8, 90 - i, f"hook {i}")
                       for i in range(n)])


def publishing_watch(env, mode="auto", **publish):
    watch = watched(env)
    settings = {"mode": mode, "platforms": ["youtube", "tiktok"], "per_day": 3,
                "footer": "Full video: {source_url}", **publish}
    env.client.patch(f"/automation/watches/{watch['id']}", json={"publish": settings})
    env.feed.listings[UC] = [yt("newnewnew01")]
    env.later()
    return watch


def test_automatic_publishes_once_with_the_watchs_settings(env):
    publishing_watch(env)
    finished_with_clips(env)
    env.later(5)
    env.later(5)
    assert len(env.published) == 1
    sent = env.published[0]
    assert len(sent["clip_ids"]) == 3
    assert sent["platforms"] == ["youtube", "tiktok"]
    assert sent["per_day"] == 3
    assert sent["footer"] == "Full video: https://www.youtube.com/watch?v=newnewnew01"
    assert env.item("newnewnew01")["publish_state"] == "done"


def test_a_publish_the_app_stopped_in_the_middle_of_runs_again(env, tmp_path):
    publishing_watch(env, mode="ask")
    finished_with_clips(env)
    env.later(5)
    item = env.item("newnewnew01")
    env.run(lambda d: d.set_watch_item(item["id"], publish_state="publishing"))
    restarted = Env(tmp_path, feed=env.feed)
    restarted.now = env.now + 5
    restarted.watcher.tick()
    # Runs again; publish_clips(once=True) is what keeps that from double posting.
    assert len(restarted.published) == 1
    assert restarted.item("newnewnew01")["publish_state"] == "done"


def test_ask_first_publishes_when_told_to(env):
    publishing_watch(env, mode="ask")
    finished_with_clips(env)
    env.later(5)
    item = env.item("newnewnew01")
    assert item["publish_state"] == "ask" and env.published == []
    response = env.client.post(f"/automation/items/{item['id']}/publish")
    assert response.status_code == 200
    env.later(5)
    assert len(env.published) == 1
    assert env.item("newnewnew01")["publish_state"] == "done"


def test_publish_is_refused_before_the_clips_exist(env):
    publishing_watch(env, mode="ask")
    item = env.item("newnewnew01")
    assert env.client.post(f"/automation/items/{item['id']}/publish").status_code == 409


def test_an_approved_publish_goes_out_even_with_watching_switched_off(env):
    publishing_watch(env, mode="ask")
    finished_with_clips(env)
    env.later(5)
    env.client.patch("/automation", json={"enabled": False})
    env.client.post(f"/automation/items/{env.item('newnewnew01')['id']}/publish")
    env.later(5)
    assert len(env.published) == 1


def test_a_publish_that_cannot_start_falls_back_to_asking(env):
    publishing_watch(env)
    finished_with_clips(env)
    env.publish_error = RuntimeError("WoopSocial isn't set up. Add your key in Settings.")
    env.later(5)
    item = env.item("newnewnew01")
    assert item["publish_state"] == "ask"
    assert "isn't set up" in item["publish_error"]


def test_no_platforms_chosen_means_asking_not_guessing(env):
    publishing_watch(env, platforms=[])
    finished_with_clips(env)
    env.later(5)
    item = env.item("newnewnew01")
    assert env.published == []
    assert item["publish_state"] == "ask"


def test_a_run_with_no_clips_has_nothing_to_publish(env):
    publishing_watch(env)
    env.finish("newnewnew01")
    env.later(5)
    assert env.published == []
    assert env.item("newnewnew01")["publish_state"] == "done"


def test_the_footer_placeholders_are_filled_and_other_braces_kept():
    watch = {"name": "LTT", "channel_key": UC, "platform": "youtube"}
    item = {"url": "https://youtu.be/x", "title": "Title"}
    got = automation.render_footer(
        "{source_title} on {source_platform} by {source_channel}: {source_url} {not_a_field}",
        item, watch,
    )
    assert got == "Title on YouTube by LTT: https://youtu.be/x {not_a_field}"


# ---- hands-off: an always-on PC with nobody watching --------------------------


class Unreachable(Exception):
    """What a network blip, a 5xx or a 429 looks like to the watcher."""

    retryable = True


def test_a_channel_can_be_added_hands_off_in_one_step(env):
    env.feed.listings[UC] = []
    response = env.client.post("/automation/watches", json={
        "platform": "youtube", "channel": UC,
        "publish": {"mode": "auto", "platforms": ["youtube", "tiktok"]},
    })
    assert response.status_code == 200
    publish = response.json()["publish"]
    assert publish["mode"] == "auto" and publish["platforms"] == ["youtube", "tiktok"]
    assert publish["per_day"] == 5  # the rest keeps its defaults


def test_a_failed_run_is_tried_again_twice_then_left(env):
    publishing_watch(env)
    for delay in automation.PROCESS_RETRY_DELAYS:
        env.finish("newnewnew01", status="failed")
        env.later(5)  # schedules the retry
        assert env.item("newnewnew01")["status"] == "failed"
        env.later(delay)
        assert env.item("newnewnew01")["status"] == "queued"
    env.finish("newnewnew01", status="failed")
    env.later(5)
    env.later(max(automation.PROCESS_RETRY_DELAYS) * 2)
    item = env.item("newnewnew01")
    assert item["status"] == "failed" and item["retries"] == 2
    assert len(env.jobs()) == 1  # the same job, retried, never a second one


def test_only_hands_off_channels_retry(env):
    publishing_watch(env, mode="ask")
    env.finish("newnewnew01", status="failed")
    env.later(5)
    env.later(max(automation.PROCESS_RETRY_DELAYS) * 2)
    assert env.item("newnewnew01")["status"] == "failed"
    assert env.item("newnewnew01")["retries"] == 0


def test_an_unreachable_woopsocial_is_tried_again_then_asked_about(env):
    publishing_watch(env)
    finished_with_clips(env)
    env.publish_error = Unreachable("Could not reach WoopSocial.")
    env.later(5)
    item = env.item("newnewnew01")
    assert item["publish_state"] == "publishing" and item["publish_attempts"] == 1
    env.later(60)
    assert env.publish_calls == 1  # not before the wait is up
    for _ in range(automation.PUBLISH_START_ATTEMPTS):
        env.later(automation.PUBLISH_RETRY_SECONDS)
    assert env.publish_calls == automation.PUBLISH_START_ATTEMPTS + 1
    assert env.item("newnewnew01")["publish_state"] == "ask"


def test_it_publishes_once_woopsocial_is_back(env):
    publishing_watch(env)
    finished_with_clips(env)
    env.publish_error = Unreachable("503")
    env.later(5)
    env.publish_error = None
    env.later(automation.PUBLISH_RETRY_SECONDS)
    assert len(env.published) == 1
    assert env.item("newnewnew01")["publish_state"] == "done"


def reject_a_post(env, video_id="newnewnew01"):
    def go(d):
        clip = d.clips_for_video(video_id)[0]
        d.record_clip_publish(clip["id"], "youtube", {
            "provider": "woopsocial", "video_id": video_id, "state": "failed",
            "error": "WoopSocial allows 5 YouTube posts a day on your plan.",
        })
    env.run(go)


def test_rejected_posts_are_sent_again_later(env):
    publishing_watch(env)
    finished_with_clips(env)
    env.later(5)
    assert len(env.published) == 1
    reject_a_post(env)
    for delay in automation.DELIVERY_RETRY_DELAYS:
        env.later(5)  # notices the rejection, schedules the re-send
        before = len(env.published)
        env.later(delay)
        assert len(env.published) == before + 1
    env.later(5)
    env.later(max(automation.DELIVERY_RETRY_DELAYS) * 2)
    assert len(env.published) == 1 + len(automation.DELIVERY_RETRY_DELAYS)


def test_nothing_is_sent_again_when_nothing_was_rejected(env):
    publishing_watch(env)
    finished_with_clips(env)
    env.later(5)
    env.later(max(automation.DELIVERY_RETRY_DELAYS) * 2)
    assert len(env.published) == 1


def download(env, video_id="newnewnew01"):
    folder = env.data_dir / "downloads"
    folder.mkdir(exist_ok=True)
    source = folder / f"{video_id}.mp4"
    source.write_bytes(b"several gigabytes")
    return source


def test_the_download_is_deleted_once_its_clips_are_published(env):
    env.client.patch("/automation", json={"delete_sources": True})
    publishing_watch(env)
    source = download(env)
    finished_with_clips(env)
    env.later(5)
    assert not source.exists()
    assert env.item("newnewnew01")["source_freed"] == 1


def test_the_download_is_kept_unless_asked(env):
    publishing_watch(env)
    source = download(env)
    finished_with_clips(env)
    env.later(5)
    assert source.exists()


def test_the_download_is_kept_while_the_clips_wait_to_be_published(env):
    env.client.patch("/automation", json={"delete_sources": True})
    publishing_watch(env, mode="ask")
    source = download(env)
    finished_with_clips(env)
    env.later(5)
    assert source.exists()  # a person may still edit before publishing


def test_only_a_watched_videos_download_is_ever_deleted(env):
    env.client.patch("/automation", json={"delete_sources": True})
    publishing_watch(env, mode="off")
    other = download(env, "somethingelse")
    finished_with_clips(env)
    env.later(5)
    assert other.exists()


def test_a_video_with_no_download_is_not_reported_as_deleted(env):
    env.client.patch("/automation", json={"delete_sources": True})
    publishing_watch(env)
    finished_with_clips(env)
    env.later(5)
    assert env.item("newnewnew01")["source_freed"] == 2


# ---- set up once: schedule, hashtags, per-platform settings -------------------


def test_the_channels_hashtags_time_and_platform_settings_reach_the_publish(env):
    publishing_watch(
        env, hashtags=["#creatorname", "#twitch"], ai_hashtags=False, day_start="09:00",
        overrides={"tiktok": {"privacyLevel": "SELF_ONLY", "allowDuet": False}},
    )
    finished_with_clips(env)
    env.later(5)
    sent = env.published[0]
    assert sent["lead_hashtags"] == ["#creatorname", "#twitch"]
    assert sent["ai_hashtags"] is False
    assert sent["day_start"] == "09:00"
    assert sent["overrides"]["tiktok"] == {"privacyLevel": "SELF_ONLY", "allowDuet": False}


def test_a_first_post_time_that_is_not_a_time_is_refused(env):
    watch = watched(env)
    response = env.client.patch(f"/automation/watches/{watch['id']}",
                                json={"publish": {"mode": "auto", "day_start": "25:00"}})
    assert response.status_code == 422


def test_the_preview_shows_the_slots_that_would_be_used(env):
    from datetime import datetime

    got = env.client.get("/automation/slots",
                         params={"per_day": 2, "gap_hours": 1, "day_start": "09:00", "count": 3})
    assert got.status_code == 200
    times = [datetime.fromisoformat(t).astimezone() for t in got.json()["times"]]
    assert [(t.hour, t.minute) for t in times] == [(9, 0), (10, 0), (9, 0)]
    assert got.json()["already_scheduled"] == 0


def test_a_schedule_that_cannot_fit_in_a_day_is_explained(env):
    got = env.client.get("/automation/slots", params={"per_day": 10, "gap_hours": 3})
    assert got.status_code == 400


def test_only_the_best_clips_are_posted_when_asked(env):
    publishing_watch(env, max_posts=2)
    finished_with_clips(env, n=4)  # scores 90, 89, 88, 87
    env.later(5)
    best = env.run(lambda d: [int(c["id"]) for c in d.clips_for_video("newnewnew01")][:2])
    assert env.published[0]["clip_ids"] == best


def test_every_clip_is_posted_by_default(env):
    publishing_watch(env)
    finished_with_clips(env, n=4)
    env.later(5)
    assert len(env.published[0]["clip_ids"]) == 4


def test_a_retry_of_rejected_posts_stays_within_the_best_clips(env):
    publishing_watch(env, max_posts=1)
    finished_with_clips(env, n=3)
    env.later(5)
    reject_a_post(env)
    env.later(5)
    env.later(automation.DELIVERY_RETRY_DELAYS[0])
    assert all(len(p["clip_ids"]) == 1 for p in env.published)


def test_the_whole_schedule_is_saved_when_the_channel_is_added(env):
    env.feed.listings[UC] = []
    response = env.client.post("/automation/watches", json={
        "platform": "youtube", "channel": UC,
        "publish": {"mode": "auto", "platforms": ["youtube"], "max_posts": 3, "per_day": 2,
                    "gap_hours": 3, "day_start": "18:00", "hashtags": ["creatorname", "twitch"],
                    "ai_hashtags": False},
    })
    publish = response.json()["publish"]
    assert (publish["max_posts"], publish["per_day"], publish["gap_hours"], publish["day_start"])         == (3, 2, 3, "18:00")
    assert publish["hashtags"] == ["creatorname", "twitch"] and publish["ai_hashtags"] is False



# ---- the live panel -------------------------------------------------------------


def activity(env):
    return env.client.get("/automation/activity").json()


def test_the_panel_says_it_is_watching(env):
    watched(env)
    got = activity(env)
    assert got["now"]["state"] == "watching"
    assert got["now"]["text"] == "Watching 1 channel"
    assert got["watching"] == 1 and got["next_check_at"] > 0


def test_the_panel_says_when_it_is_off(tmp_path):
    e = Env(tmp_path)
    assert activity(e)["now"]["state"] == "off"


def test_each_step_is_told_as_it_happens(env):
    publishing_watch(env)  # adds, baselines, then finds newnewnew01 and queues it
    finished_with_clips(env)
    env.later(5)
    texts = [e["text"] for e in reversed(activity(env)["events"])]
    assert any(t.startswith("Now watching") for t in texts)
    assert any("New video on" in t and "Video newnewnew01" in t for t in texts)
    assert any(t.startswith("Queued") for t in texts)
    assert any(t.startswith("Scheduled 3 clip(s)") for t in texts)


def test_a_video_being_clipped_shows_as_the_current_work(env):
    publishing_watch(env)
    env.run(lambda d: d.claim_next_job())  # the worker picks it up
    now = activity(env)["now"]
    assert now["state"] == "busy" and now["text"].startswith("Making clips of")
    assert now["progress"] == {"fraction": 0.5}


def test_a_post_going_live_is_announced(env):
    publishing_watch(env)
    finished_with_clips(env)
    env.later(5)

    def land(d):
        clip = d.clips_for_video("newnewnew01")[0]
        d.record_clip_publish(clip["id"], "youtube", {
            "provider": "woopsocial", "video_id": "newnewnew01", "state": "published"})
    env.run(land)
    env.later(5)
    assert activity(env)["events"][0]["text"].endswith("to YouTube")


def test_a_check_that_finds_nothing_still_says_so(env):
    watched(env)
    env.later()
    assert activity(env)["events"][0]["text"] == f"Checked Channel {UC}: nothing new"


def test_post_right_away_sends_without_a_schedule(env):
    publishing_watch(env, spread=False, max_posts=1, day_start="09:00")
    finished_with_clips(env)
    env.later(5)
    sent = env.published[0]
    assert sent["per_day"] == 0 and sent["day_start"] == ""
    assert len(sent["clip_ids"]) == 1


def test_spacing_is_the_default(env):
    publishing_watch(env)
    finished_with_clips(env)
    env.later(5)
    assert env.published[0]["per_day"] == 3


def test_clips_sent_right_away_are_told_as_sent_now(env):
    publishing_watch(env, spread=False, max_posts=1)
    finished_with_clips(env)
    env.later(5)
    texts = [e["text"] for e in activity(env)["events"]]
    assert any(t.startswith("Sent 1 clip(s)") and t.endswith("now.") for t in texts)


def test_a_found_video_links_to_itself(env):
    publishing_watch(env)
    found = next(e for e in activity(env)["events"] if e["kind"] == "found")
    assert found["url"] == "https://www.youtube.com/watch?v=newnewnew01"


def test_a_post_going_live_links_to_the_post(env):
    publishing_watch(env)
    finished_with_clips(env)
    env.later(5)

    def land(d):
        clip = d.clips_for_video("newnewnew01")[0]
        d.record_clip_publish(clip["id"], "youtube", {
            "provider": "woopsocial", "video_id": "newnewnew01", "state": "published",
            "post_url": "https://www.youtube.com/watch?v=posted0001"})
    env.run(land)
    env.later(5)
    assert activity(env)["events"][0]["url"] == "https://www.youtube.com/watch?v=posted0001"


# ---- only full videos and finished streams -----------------------------------


def short(vid):
    video = yt(vid)
    video.url = f"https://www.youtube.com/shorts/{vid}"
    video.short = True
    return video


def test_shorts_are_never_listed_or_clipped(env):
    watched(env, short("shortshort1"), yt("aaaaaaaaaaa"))
    env.feed.listings[UC].insert(0, short("shortshort2"))
    env.later()
    ids = {i["video_id"] for i in env.items()}
    assert ids == {"aaaaaaaaaaa"}
    assert env.jobs() == []


def test_shorts_listed_before_this_are_cleared_once(tmp_path):
    path = tmp_path / "state.db"
    d = StateDB(path)
    d.conn.execute("DELETE FROM app_state WHERE key = 'watch_shorts_cleared'")
    d.insert_watch("youtube", UC, name="x")
    d.insert_watch_item("s1", watch_id=1, url="https://www.youtube.com/shorts/s1", state="baseline")
    d.insert_watch_item("s2", watch_id=1, url="https://www.youtube.com/watch?v=s2",
                        state="skipped", reason="Shorter than 3 minutes.")
    d.insert_watch_item("v1", watch_id=1, url="https://www.youtube.com/watch?v=v1", state="baseline")
    d.close()
    d = StateDB(path)
    assert d.watch_item_ids() == {"v1"}
    # Once: a video skipped for length after this stays recorded.
    d.insert_watch_item("s3", watch_id=1, url="https://www.youtube.com/watch?v=s3",
                        state="skipped", reason="Shorter than 5 minutes.")
    d.close()
    d = StateDB(path)
    assert d.watch_item_ids() == {"v1", "s3"}
    d.close()


def test_the_clip_settings_chosen_at_add_reach_every_job(env):
    env.feed.listings[UC] = []
    response = env.client.post("/automation/watches", json={
        "platform": "youtube", "channel": UC,
        "options": {"captions": False, "podcast": True}, "preset": "standard",
    })
    assert response.json()["options"] == {"captions": False, "podcast": True}
    env.watcher.tick()
    env.feed.listings[UC] = [yt("newnewnew01")]
    env.later()
    payload = json.loads(env.jobs()[0]["payload"])
    assert payload["captions"] is False and payload["podcast"] is True


# ---- the creator it learns about --------------------------------------------


NAME = f"Channel {UC}"  # what FakeFeed calls the channel


def creator_rows(env):
    return env.run(lambda d: [dict(r) for r in d.conn.execute(
        "SELECT c.creator_id, c.display_name, a.platform FROM creators c"
        " JOIN platform_accounts a ON a.creator_id = c.creator_id")])


def learned(env, video_id, n, creator_id):
    """What the pipeline's learning pass stores: facts from this video."""
    def go(d):
        for i in range(n):
            d.conn.execute(
                "INSERT INTO creator_knowledge (creator_id, knowledge_type, information,"
                " source_video, created_at) VALUES (?, 'topic', ?, ?, '')",
                (creator_id, f"fact {i}", video_id))
        d.conn.commit()
    env.run(go)


def learned_lines(env):
    events = env.client.get("/automation/activity").json()["events"]
    return [e["text"] for e in events if e["kind"] == "learned"]


def test_adding_a_channel_makes_its_creator_profile_at_once(env):
    watch = env.add()
    rows = creator_rows(env)
    assert [(r["display_name"], r["platform"]) for r in rows] == [(NAME, "youtube")]
    assert watch["creator"] == {"id": rows[0]["creator_id"], "name": NAME,
                                "learning": True, "videos": 0, "facts": 0}


def test_a_channel_known_only_by_its_id_gets_no_profile(env):
    env.feed.resolve = lambda platform, text: Channel(platform, text, text)
    assert env.add()["creator"] is None
    assert creator_rows(env) == []


def test_a_watched_video_learns_into_the_watchs_creator(env):
    from creator import identity

    creator_id = watched(env)["creator"]["id"]
    env.feed.listings[UC] = [yt("newnewnew01")]
    env.later()
    tagged = env.run(lambda d: d.conn.execute(
        "SELECT creator_id FROM videos WHERE video_id = 'newnewnew01'").fetchone()[0])
    assert tagged == creator_id
    # The pipeline tags by what the download calls the channel, and keeps this.
    assert env.run(lambda d: identity.tag_video(d, "newnewnew01", "Something Else")) == creator_id
    # A video of the channel clipped by hand, its name in another case, joins it too.
    assert env.run(lambda d: identity.resolve(d, "byhand00001", NAME.upper())) == creator_id
    assert len(creator_rows(env)) == 1


def test_the_live_panel_says_what_a_video_taught_it_once(env):
    watch = publishing_watch(env, mode="ask")
    learned(env, "newnewnew01", 3, watch["creator"]["id"])
    finished_with_clips(env)
    env.later(5)
    env.later(5)
    assert learned_lines(env) == [
        f"Learned 3 new things about {NAME} from “Video newnewnew01”"]
    card = env.client.get("/automation/watches").json()[0]["creator"]
    assert (card["videos"], card["facts"]) == (1, 3)


def test_a_video_that_taught_nothing_says_nothing(env):
    publishing_watch(env, mode="ask")
    finished_with_clips(env)
    env.later(5)
    assert learned_lines(env) == []



# ---- Vertical Live watches ---------------------------------------------------------


def test_a_vertical_live_watch_skips_videos_with_no_vertical_version(env):
    watch = watched(env)
    env.client.patch(f"/automation/watches/{watch['id']}", json={"options": {"vertical_live": True}})
    env.feed.listings[UC] = [yt("landscape01"), yt("portrait001")]
    env.feed.ready["https://www.youtube.com/watch?v=landscape01"] = Readiness(
        "ready", duration=3600, orientation="horizontal")
    env.feed.ready["https://www.youtube.com/watch?v=portrait001"] = Readiness(
        "ready", duration=3600, orientation="vertical")
    env.later()
    jobs = env.jobs()
    assert [j["video_id"] for j in jobs] == ["portrait001"]
    assert json.loads(jobs[0]["payload"])["vertical_live"] is True
    skipped = env.item("landscape01")
    assert skipped["status"] == "skipped" and "No vertical version" in skipped["reason"]


def test_without_the_toggle_a_landscape_video_is_clipped_as_before(env):
    watched(env)
    env.feed.listings[UC] = [yt("landscape01")]
    env.feed.ready["https://www.youtube.com/watch?v=landscape01"] = Readiness(
        "ready", duration=3600, orientation="horizontal")
    env.later()
    payload = json.loads(env.jobs()[0]["payload"])
    assert "vertical_live" not in payload


def test_clip_this_on_a_skipped_landscape_video_runs_it_the_standard_way(env):
    watch = watched(env)
    env.client.patch(f"/automation/watches/{watch['id']}", json={"options": {"vertical_live": True}})
    env.feed.listings[UC] = [yt("landscape01")]
    env.feed.ready["https://www.youtube.com/watch?v=landscape01"] = Readiness(
        "ready", duration=3600, orientation="horizontal")
    env.later()
    item = env.item("landscape01")
    assert item["status"] == "skipped"
    assert env.client.post(f"/automation/items/{item['id']}/queue").status_code == 200
    env.watcher.tick()
    payload = json.loads(env.jobs()[0]["payload"])
    assert payload["origin"] == "manual" and "vertical_live" not in payload


def test_a_gaming_watch_queues_its_videos_as_gaming(env):
    watch = watched(env)
    env.client.patch(f"/automation/watches/{watch['id']}", json={"options": {"gaming": True}})
    env.feed.listings[UC] = [yt("landscape01")]
    env.feed.ready["https://www.youtube.com/watch?v=landscape01"] = Readiness(
        "ready", duration=3600, orientation="horizontal")
    env.later()
    assert json.loads(env.jobs()[0]["payload"])["gaming"] is True


def test_a_watch_with_gaming_and_another_layout_mode_keeps_the_other():
    watch = {"options": json.dumps({"gaming": True, "podcast": True})}
    payload = automation.job_payload(watch, "https://www.youtube.com/watch?v=x")
    assert payload["podcast"] is True and "gaming" not in payload


def test_a_videos_shape_is_read_from_its_formats():
    from sources.channel_feed import _orientation

    assert _orientation({"formats": [{"width": 1920, "height": 1080}, {"width": 1080, "height": 1920}]}) == "vertical"
    assert _orientation({"formats": [{"width": 1920, "height": 1080}, {"vcodec": "none"}]}) == "horizontal"
    assert _orientation({"width": 720, "height": 1280}) == "vertical"
    assert _orientation({}) == ""


# ---- Rate & understand -----------------------------------------------------------
# A watch can choose Marketplace plugins to rate or understand its videos' moments
# (plugins/steps.py). When one this job chose didn't run, the clips wait for a person.

STEPPER = Path(__file__).resolve().parent / "fixtures" / "plugins" / "stepper"
RATER = {"id": "example-dev/quarkbloom-rater"}


def _step(**changes) -> dict:
    """One entry of a video outcome's `steps`, as plugins/steps.after_finding reports it."""
    entry = {"plugin": "example-dev/quarkbloom-rater", "version": "1.0.0", "name": "Quarkbloom Rater",
             "steps": ["rate"], "ok": True, "given": 3, "noted": 0, "rated": 3, "set_aside": 0}
    entry.update(changes)
    return entry


FAILED = {"clips": 3, "candidates": 3, "min_score": 55,
          "steps": [_step(ok=False, rated=0, error="It isn't installed any more.")]}


def _real_options(data_dir):
    """A watch's options checked as the app checks them (server/api.py), with
    plugins installed in `data_dir`."""
    api = pytest.importorskip("server.api")
    return lambda raw: api._process_options(api.JobPatch(**raw), data_dir=data_dir)


def stepped_watch(env, mode="auto", options=None, **publish):
    """A watch publishing with `mode` whose options choose a rater (unless
    `options` says otherwise), once it has queued newnewnew01."""
    watch = watched(env)
    env.client.patch(f"/automation/watches/{watch['id']}", json={
        "publish": {"mode": mode, "platforms": ["youtube"], **publish},
        "options": {"rate": [RATER]} if options is None else options})
    env.feed.listings[UC] = [yt("newnewnew01")]
    env.later()
    return watch


def set_outcome(env, outcome, video_id="newnewnew01"):
    env.run(lambda d: d.set_outcome(video_id, outcome))


def feed_lines(env) -> list[str]:
    return [e["text"] for e in activity(env)["events"]]


def test_watch_options_accept_steps_and_drop_them_beside_longform(tmp_path, install_plugin):
    data_dir = tmp_path / "data"
    install_plugin(data_dir, STEPPER)
    e = Env(tmp_path, options_from=_real_options(data_dir))
    e.enable()
    e.feed.listings[UC] = []
    steps = {"rate": [{"id": "fixture-dev/stepper", "settings": {"scores": "*=+5"}}],
             "understand": [{"id": "fixture-dev/stepper"}]}
    response = e.client.post("/automation/watches", json={
        "platform": "youtube", "channel": UC, "preset": "highlights",
        "options": {"rate": {"id": "fixture-dev/stepper", "settings": {"scores": "*=+5"}},
                    "understand": "fixture-dev/stepper"}})
    assert response.status_code == 200 and response.json()["options"] == steps  # checked and kept
    watch_id = response.json()["id"]
    e.watcher.tick()
    e.feed.listings[UC] = [yt("newnewnew01")]
    e.later()
    payload = json.loads(e.jobs()[0]["payload"])
    assert payload["longform"] == {"mode": "highlights"} and not {"rate", "understand"} & set(payload)
    # Kept in the watch, so a preset that makes Shorts hands them over again.
    assert e.client.patch(f"/automation/watches/{watch_id}", json={"preset": "standard"}).status_code == 200
    e.feed.listings[UC] = [yt("newnewnew02"), yt("newnewnew01")]
    e.later()
    payload = json.loads(next(j for j in e.jobs() if j["video_id"] == "newnewnew02")["payload"])
    assert payload["rate"] == steps["rate"] and payload["understand"] == steps["understand"]
    # Longform written into the options themselves drops them too; the modes that
    # pick moments their own way, and a pipeline, keep them.
    for options, kept in (({"longform": {"mode": "short_clips", "shorts": True}}, False),
                          ({"sport": {"name": "soccer"}}, True), ({"gaming_scoring": True}, True),
                          ({"pipeline": {"id": "fixture-dev/echo"}}, True)):
        payload = automation.job_payload({"options": json.dumps({**steps, **options})}, yt("x").url)
        assert ("rate" in payload and "understand" in payload) is kept, options
    # One that isn't installed is refused when the watch is saved, in the job's words.
    response = e.client.patch(f"/automation/watches/{watch_id}", json={"options": {"rate": "fixture-dev/missing"}})
    assert response.status_code == 400
    assert response.json()["detail"] == "rate[0]: the pipeline fixture-dev/missing isn't installed"


def test_auto_mode_holds_clips_when_a_chosen_step_failed(env):
    stepped_watch(env)
    finished_with_clips(env)
    set_outcome(env, FAILED)
    env.later(5)
    env.later(5)
    assert env.publish_calls == 0 and env.published == []
    item = env.item("newnewnew01")
    assert item["publish_state"] == "ask"
    assert item["publish_error"] == (
        "Clips Kitty made these clips without Quarkbloom Rater, so they weren't posted automatically. "
        "Check them and publish, or turn off Rate & understand in this channel's settings.")
    assert ("Clips of “Video newnewnew01” are ready, but Clips Kitty made them without Quarkbloom "
            "Rater, so they weren't posted automatically.") in feed_lines(env)
    # A person's go-ahead sends them.
    assert env.client.post(f"/automation/items/{item['id']}/publish").status_code == 200
    env.later(5)
    assert len(env.published) == 1 and env.item("newnewnew01")["publish_state"] == "done"


def test_ask_mode_says_a_chosen_step_failed(env):
    stepped_watch(env, mode="ask", options={"understand": [RATER], "rate": [RATER]})
    finished_with_clips(env)
    set_outcome(env, {**FAILED, "steps": [_step(name="Quarkbloom Notes", steps=["understand"], ok=False),
                                          _step(ok=False)]})
    env.later(5)
    item = env.item("newnewnew01")
    assert item["publish_state"] == "ask" and env.published == []
    assert item["publish_error"] == ("Clips Kitty made these clips without Quarkbloom Notes, Quarkbloom Rater. "
                                     "Check them before you publish.")
    assert "Clips of “Video newnewnew01” are ready. Waiting for you to publish them." in feed_lines(env)
    env.client.post(f"/automation/items/{item['id']}/publish")
    env.later(5)
    item = env.item("newnewnew01")
    assert len(env.published) == 1 and (item["publish_state"], item["publish_error"]) == ("done", "")


def test_a_watch_without_steps_is_never_held_by_an_old_outcome(env):
    stepped_watch(env, options={"captions": False})
    finished_with_clips(env)
    set_outcome(env, FAILED)  # left by an earlier run of the video that had a rater
    env.later(5)
    assert len(env.published) == 1
    item = env.item("newnewnew01")
    assert (item["publish_state"], item["publish_error"]) == ("done", "")


def test_auto_mode_posts_the_best_rated_first(env):
    stepped_watch(env, max_posts=2)
    env.finish("newnewnew01")

    def clips(d):
        # (start, the score it was found with, the rater's score or None)
        for start, found, rated in ((0.0, 90, 40), (10.0, 70, 95), (20.0, 80, None), (30.0, 75, 85)):
            scores = {"text": found}
            if rated is not None:
                scores.update(found_score=found, plugin_ratings=[
                    {"plugin": RATER["id"], "version": "1.0.0", "name": "Quarkbloom Rater",
                     "score": rated, "reason": "the caster called a big play"}])
            d.add_clip("newnewnew01", start, start + 8, found if rated is None else rated, f"hook {start:g}",
                       scores=json.dumps(scores))
        return {c["start_s"]: int(c["id"]) for c in d.clips_for_video("newnewnew01")}

    ids = env.run(clips)
    set_outcome(env, {"clips": 4, "steps": [_step(rated=3)]})
    env.later(5)
    assert env.published[0]["clip_ids"] == [ids[10.0], ids[30.0]]


def test_no_clips_after_a_rater_says_so(env):
    stepped_watch(env)
    env.finish("newnewnew01")
    set_outcome(env, {"clips": 0, "candidates": 3, "best_score": 30, "min_score": 55, "rejected": {"below_min_score": 3},
                      "rated_out": 3, "cause": "rated_out", "steps": [_step(rated=3, set_aside=3)]})
    env.later(5)
    item = env.item("newnewnew01")
    assert env.published == [] and item["publish_state"] == "done"
    assert item["publish_error"] == ("Quarkbloom Rater rated every moment under the minimum score (55), "
                                     "so there were no clips to publish.")


def _windows(env) -> dict:
    return env.run(lambda d: {c["start_s"]: int(c["id"]) for c in d.clips_for_video("newnewnew01")})


def _rescore(env, scores: dict):
    """What a forced re-run with a rater does to the score column (core/pipeline._register_clip)."""
    def go(d):
        for start, score in scores.items():
            d.conn.execute("UPDATE clips SET score = ? WHERE video_id = 'newnewnew01' AND start_s = ?",
                           (score, start))
        d.conn.commit()
    env.run(go)


def _reject(env, clip_id):
    env.run(lambda d: d.record_clip_publish(clip_id, "youtube", {
        "provider": "woopsocial", "video_id": "newnewnew01", "state": "failed",
        "error": "WoopSocial allows 5 YouTube posts a day on your plan."}))


def test_a_resend_sends_only_the_clips_chosen_the_first_time(env):
    stepped_watch(env, max_posts=2)
    finished_with_clips(env, n=3)  # scores 90, 89, 88 at 0 s, 10 s and 20 s
    set_outcome(env, {"clips": 3, "steps": [_step()]})
    ids = _windows(env)
    # The first publish can't start, and is tried again after a forced re-run changed the scores.
    env.publish_error = Unreachable("Could not reach WoopSocial.")
    env.later(5)
    _rescore(env, {20.0: 99, 0.0: 10})
    env.publish_error = None
    env.later(automation.PUBLISH_RETRY_SECONDS)
    chosen = [ids[10.0], ids[0.0]]  # the best two when it was first published, in today's order
    assert [p["clip_ids"] for p in env.published] == [chosen]
    # The re-send of a rejected post, and Retry failed, keep that choice.
    _reject(env, ids[10.0])
    env.later(5)
    env.later(automation.DELIVERY_RETRY_DELAYS[0])
    assert len(env.published) == 2 and env.published[1]["clip_ids"] == chosen
    env.client.post(f"/automation/items/{env.item('newnewnew01')['id']}/publish")
    env.later(5)
    assert len(env.published) == 3 and env.published[2]["clip_ids"] == chosen


def test_an_item_published_before_this_change_keeps_todays_choice(env):
    stepped_watch(env, max_posts=2)
    finished_with_clips(env, n=3)
    set_outcome(env, {"clips": 3, "steps": [_step()]})
    env.later(5)
    ids = _windows(env)
    assert env.published[0]["clip_ids"] == [ids[0.0], ids[10.0]]
    item = env.item("newnewnew01")
    env.run(lambda d: d.set_watch_item(item["id"], chosen_clips=""))  # as an item published before it was kept
    _rescore(env, {20.0: 99})
    env.client.post(f"/automation/items/{item['id']}/publish")
    env.later(5)
    assert env.published[1]["clip_ids"] == [ids[20.0], ids[0.0]]  # the best two now, as before this change


def _trim(env, monkeypatch, clip_id, start) -> int:
    """Trim a clip in the editor: the real re-render job (server/jobs.py
    _rerender_clip), with the render itself stood in for. The clip comes back
    with a new id and a new window. Returns the new id."""
    pytest.importorskip("numpy")
    pytest.importorskip("cv2")
    from types import SimpleNamespace

    from core import pipeline
    from server.jobs import Worker

    for folder, name, data in (("downloads", "newnewnew01.mp4", b"source"),
                               ("transcripts", "newnewnew01.json", b'{"segments": []}')):
        (env.data_dir / folder).mkdir(exist_ok=True)
        (env.data_dir / folder / name).write_bytes(data)

    def render(_source, _candidate, _segments, clip_dir, _config, render_opts, _lang):
        clip_dir.mkdir(parents=True, exist_ok=True)
        (clip_dir / "clip.mp4").write_bytes(b"clip")
        return clip_dir / "clip.mp4", json.dumps(render_opts)

    monkeypatch.setattr(pipeline, "_render_files", render)
    job = SimpleNamespace(config={"paths": {"data_dir": str(env.data_dir)}, "clips": {"outro": False}})
    env.run(lambda d: Worker._rerender_clip(job, d, {"clip_id": clip_id, "start": start}))
    return _windows(env)[start]


@pytest.mark.parametrize("max_posts", [0, 1])
def test_a_clip_trimmed_after_its_post_was_rejected_is_sent_again(env, monkeypatch, max_posts):
    """No rate or understand here: a re-send keeps the first publish's clips,
    and a clip the creator trimmed since is still one of them."""
    publishing_watch(env, max_posts=max_posts)
    finished_with_clips(env, n=3)  # scores 90, 89, 88 at 0 s, 10 s and 20 s
    env.later(5)
    ids = _windows(env)
    first = [ids[0.0], ids[10.0], ids[20.0]][: max_posts or 3]
    assert [p["clip_ids"] for p in env.published] == [first]
    reject_a_post(env)  # the best clip's YouTube post
    trimmed = _trim(env, monkeypatch, ids[0.0], 1.0)
    env.later(5)
    env.later(automation.DELIVERY_RETRY_DELAYS[0])
    # The trimmed clip goes out again; the others are passed over by
    # publish_clips(once=True) as already sent, as before the choice was kept.
    assert env.published[1]["clip_ids"] == [trimmed, ids[10.0], ids[20.0]][: max_posts or 3]
    item = env.item("newnewnew01")
    assert (item["publish_state"], item["publish_error"]) == ("done", "")
    assert json.loads(env.run(lambda d: d.get_watch_item(item["id"])["chosen_clips"]))[0] == [1.0, 8.0]


def test_a_resend_whose_chosen_clips_were_deleted_says_so(env):
    publishing_watch(env, max_posts=1)
    finished_with_clips(env, n=3)
    env.later(5)
    ids = _windows(env)
    assert env.published[0]["clip_ids"] == [ids[0.0]]
    reject_a_post(env)
    env.run(lambda d: d.delete_clip(ids[0.0]))
    env.later(5)
    env.later(automation.DELIVERY_RETRY_DELAYS[0])
    assert len(env.published) == 1  # never one the first publish didn't choose
    item = env.item("newnewnew01")
    assert item["publish_state"] == "done"
    assert item["publish_error"] == ("The clips chosen when this video was first published aren't there "
                                     "any more, so there was nothing to send.")


def test_a_watch_keeps_its_rater_through_an_autosave(tmp_path, install_plugin):
    data_dir = tmp_path / "data"
    install_plugin(data_dir, STEPPER)
    e = Env(tmp_path, options_from=_real_options(data_dir))
    e.enable()
    watch = watched(e)
    kept = {"rate": [{"id": "fixture-dev/stepper", "settings": {"scores": "*=+5"}}],
            "understand": [{"id": "fixture-dev/stepper"}]}
    response = e.client.patch(f"/automation/watches/{watch['id']}", json={"options": {**kept, "captions": False}})
    assert response.status_code == 200 and response.json()["options"] == {**kept, "captions": False}
    # The watch editor saves every option it shows, sending the kept ones back as they were.
    response = e.client.patch(f"/automation/watches/{watch['id']}",
                              json={"options": {**kept, "captions": True}, "publish": {"mode": "ask"}})
    assert response.status_code == 200 and response.json()["options"] == {**kept, "captions": True}
    e.feed.listings[UC] = [yt("newnewnew01")]
    e.later()
    payload = json.loads(e.jobs()[0]["payload"])
    assert payload["rate"] == kept["rate"] and payload["understand"] == kept["understand"]
    # Options sent without them replace the watch's: that is how they are turned off.
    response = e.client.patch(f"/automation/watches/{watch['id']}", json={"options": {"captions": True}})
    assert response.json()["options"] == {"captions": True}
