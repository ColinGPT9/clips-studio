"""Watched channels: a creator posts and Clips Kitty clips it, once.

The promises pinned here: adding a channel never queues its back catalogue, a
video becomes at most one job however it is seen (twice in one feed, by two
watches, after a restart, after a title change, or after being clipped by
hand), nothing live is handed to the downloader, content missed while the app
was closed is never silently lost, and nothing is published unless asked.
"""

import json

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi import FastAPI
from fastapi.testclient import TestClient

from core import queue
from core.state import StateDB
from server import automation
from sources.channel_feed import Channel, NewSourceVideo, Readiness

UC = "UCXuqSBlHAE6Xw-yeJA0Tunw"
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
    def __init__(self, tmp_path, feed=None):
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
            options_from=lambda raw: dict(raw), feed=self.feed, clock=lambda: self.now,
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
    d.add_channel(UC, "Linus Tech Tips")
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


def test_shorts_listed_before_this_are_cleared(tmp_path):
    path = tmp_path / "state.db"
    d = StateDB(path)
    d.insert_watch("youtube", UC, name="x")
    d.insert_watch_item("s1", watch_id=1, url="https://www.youtube.com/shorts/s1", state="baseline")
    d.insert_watch_item("v1", watch_id=1, url="https://www.youtube.com/watch?v=v1", state="baseline")
    d.close()
    d = StateDB(path)
    assert d.watch_item_ids() == {"v1"}
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

