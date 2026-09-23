"""Publishing that nobody is watching must never post a clip twice.

WoopSocial has no idempotency key, so a watched channel's clips go out through
publish_clips(once=True): anything already sent or on its way is skipped, each
send is written down as "sending" before any request, tagged with its media as
soon as the upload lands, and reconcile_sending settles a send the app stopped
in the middle of. A person pressing Publish (once=False) is unaffected.
"""

from pathlib import Path

import pytest

from core.state import StateDB
from publish import woopsocial as ws
from publish.errors import PublishError
from publish.uploadpost import FanOutResult, PlatformOutcome
from server import woopsocial_service as service


class Crash(Exception):
    """The app stopping, as far as publish_clips can tell."""


class FakeWoop:
    """Stands in for WoopSocialPublisher and remembers every post it made."""

    def __init__(self):
        self.posts: list[dict] = []
        self.outcome: dict[str, str] = {}   # platform -> state to report
        self.fail_before_upload: PublishError | None = None
        self.fail_after_upload: Exception | None = None
        self.crash_after_create = False

    def install(self, monkeypatch):
        fake = self

        class Publisher:
            def __init__(self, client, project_id):
                pass

            def start(self, video, *, platforms, title, text, scheduled_for="",
                      overrides=None, on_media=None):
                if fake.fail_before_upload:
                    raise fake.fail_before_upload
                media = f"m{len(fake.posts) + 1}"
                if on_media:
                    on_media(media)
                if fake.fail_after_upload:
                    raise fake.fail_after_upload
                post = {"id": f"p{len(fake.posts) + 1}", "media": media,
                        "platforms": list(platforms), "text": text}
                fake.posts.append(post)
                if fake.crash_after_create:
                    raise Crash()
                return fake.result(post)

            def find_by_media(self, media_id, platforms):
                for post in fake.posts:
                    if post["media"] == media_id:
                        return fake.result(post)
                return None

        monkeypatch.setattr(ws, "WoopSocialPublisher", Publisher)
        monkeypatch.setattr(service, "make_client", lambda _d: object())
        monkeypatch.setattr(service, "resolve_project", lambda _db, _c: "proj1")

    def result(self, post):
        return FanOutResult(
            request_id=post["id"],
            outcomes=[PlatformOutcome(platform=p, state=self.outcome.get(p, "queued"))
                      for p in post["platforms"]],
        )


@pytest.fixture
def woop(monkeypatch):
    fake = FakeWoop()
    fake.install(monkeypatch)
    return fake


@pytest.fixture
def db(tmp_path):
    d = StateDB(tmp_path / "state.db")
    yield d
    d.close()


def clips(db, tmp_path: Path, n: int, video="vid") -> list[int]:
    db.upsert_video(video, title="t")
    ids = []
    for i in range(n):
        f = tmp_path / f"{video}{i}.mp4"
        f.write_bytes(b"v")
        ids.append(db.add_clip(video, i * 10.0, i * 10.0 + 8, 90 - i, f"hook {i}", path=str(f)))
    return ids


def publish(db, tmp_path, ids, platforms=("youtube",), **kwargs):
    return service.publish_clips(db, tmp_path, clip_ids=ids, platforms=list(platforms), **kwargs)


def states(db):
    return {(r["clip_id"], r["platform"]): r["state"]
            for r in db.conn.execute("SELECT clip_id, platform, state FROM clip_publishes")}


def test_twenty_clips_are_twenty_posts_and_a_rerun_sends_nothing(db, tmp_path, woop):
    ids = clips(db, tmp_path, 20)
    first = publish(db, tmp_path, ids, once=True, per_day=5)
    assert len(first["started"]) == 20
    again = publish(db, tmp_path, ids, once=True, per_day=5)
    assert again["started"] == []
    assert {s["reason"] for s in again["skipped"]} == {"already sent"}
    assert len(woop.posts) == 20


def test_only_the_platform_that_failed_is_sent_again(db, tmp_path, woop):
    ids = clips(db, tmp_path, 1)
    woop.outcome = {"youtube": "published", "tiktok": "failed", "instagram": "published"}
    publish(db, tmp_path, ids, platforms=("youtube", "tiktok", "instagram"), once=True)
    woop.outcome = {}
    publish(db, tmp_path, ids, platforms=("youtube", "tiktok", "instagram"), once=True)
    assert [p["platforms"] for p in woop.posts] == [
        ["youtube", "tiktok", "instagram"],
        ["tiktok"],
    ]


def test_a_person_publishing_again_still_means_it(db, tmp_path, woop):
    ids = clips(db, tmp_path, 1)
    publish(db, tmp_path, ids)
    publish(db, tmp_path, ids)
    assert len(woop.posts) == 2


def test_a_re_rendered_clip_counts_as_already_sent(db, tmp_path, woop):
    ids = clips(db, tmp_path, 1)
    publish(db, tmp_path, ids, once=True)
    clip = db.get_clip(ids[0])
    # Applying edits gives the same moment a new clip id.
    db.conn.execute("DELETE FROM clips WHERE id = ?", (ids[0],))
    new_id = db.add_clip("vid", clip["start_s"], clip["end_s"], 90, "hook", path=clip["path"])
    publish(db, tmp_path, [new_id], once=True)
    assert len(woop.posts) == 1


def test_a_send_is_written_down_before_it_goes(db, tmp_path, woop):
    ids = clips(db, tmp_path, 1)
    woop.crash_after_create = True
    with pytest.raises(Crash):
        publish(db, tmp_path, ids, once=True)
    row = db.conn.execute("SELECT state, media_id FROM clip_publishes").fetchone()
    assert (row["state"], row["media_id"]) == ("sending", "m1")
    # Running again before it is settled does not send it a second time.
    woop.crash_after_create = False
    publish(db, tmp_path, ids, once=True)
    assert len(woop.posts) == 1


def test_an_interrupted_send_that_reached_woopsocial_is_adopted(db, tmp_path, woop):
    ids = clips(db, tmp_path, 1)
    woop.crash_after_create = True
    with pytest.raises(Crash):
        publish(db, tmp_path, ids, once=True)
    settled = service.reconcile_sending(db, tmp_path, older_than=0)
    assert settled == {"adopted": 1, "failed": 0}
    row = db.conn.execute("SELECT state, request_id FROM clip_publishes").fetchone()
    assert (row["state"], row["request_id"]) == ("queued", "p1")
    woop.crash_after_create = False
    publish(db, tmp_path, ids, once=True)
    assert len(woop.posts) == 1


def test_an_interrupted_send_that_never_arrived_is_safe_to_send_again(db, tmp_path, woop):
    ids = clips(db, tmp_path, 1)
    woop.fail_after_upload = Crash()
    with pytest.raises(Crash):
        publish(db, tmp_path, ids, once=True)
    assert woop.posts == []
    assert service.reconcile_sending(db, tmp_path, older_than=0) == {"adopted": 0, "failed": 1}
    woop.fail_after_upload = None
    publish(db, tmp_path, ids, once=True)
    assert len(woop.posts) == 1


def test_a_send_stopped_before_its_upload_needs_no_lookup(db, tmp_path, woop):
    ids = clips(db, tmp_path, 1)
    db.record_clip_publish(ids[0], "youtube", {
        "provider": "woopsocial", "video_id": "vid", "state": "sending", "media_id": "",
    })
    assert service.reconcile_sending(db, tmp_path, older_than=0) == {"adopted": 0, "failed": 1}


def test_a_recent_send_is_left_alone(db, tmp_path, woop):
    ids = clips(db, tmp_path, 1)
    db.record_clip_publish(ids[0], "youtube", {"provider": "woopsocial", "state": "sending"})
    assert service.reconcile_sending(db, tmp_path) == {"adopted": 0, "failed": 0}


def test_a_refusal_is_a_failure_that_can_be_retried(db, tmp_path, woop):
    ids = clips(db, tmp_path, 1)
    woop.fail_before_upload = PublishError("Not connected")
    publish(db, tmp_path, ids, once=True)
    assert states(db) == {(ids[0], "youtube"): "failed"}
    woop.fail_before_upload = None
    publish(db, tmp_path, ids, once=True)
    assert len(woop.posts) == 1


def test_a_timeout_after_the_upload_waits_to_be_settled(db, tmp_path, woop):
    ids = clips(db, tmp_path, 1)
    timeout = PublishError("WoopSocial did not respond in time.")
    timeout.retryable = True
    woop.fail_after_upload = timeout
    publish(db, tmp_path, ids, once=True)
    assert states(db) == {(ids[0], "youtube"): "sending"}


def test_the_footer_goes_under_the_caption(db, tmp_path, woop):
    ids = clips(db, tmp_path, 1)
    publish(db, tmp_path, ids, once=True, footer="Full video: https://youtu.be/abc")
    assert woop.posts[0]["text"].endswith("Full video: https://youtu.be/abc")


def test_a_background_run_does_not_change_the_dialogs_platforms(db, tmp_path, woop):
    ids = clips(db, tmp_path, 1)
    service.save_settings(db, {"platforms": ["tiktok"]})
    publish(db, tmp_path, ids, platforms=("youtube",), once=True, remember=False)
    assert service.load_settings(db)["platforms"] == ["tiktok"]


def test_a_send_in_progress_holds_its_slot_in_the_schedule(db, tmp_path, woop):
    ids = clips(db, tmp_path, 1)
    db.record_clip_publish(ids[0], "youtube", {
        "provider": "woopsocial", "state": "sending", "scheduled_for": "2099-01-01T09:00:00+00:00",
    })
    assert service.committed_times(db) == ["2099-01-01T09:00:00+00:00"]
