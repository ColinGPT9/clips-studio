"""A scheduled direct-YouTube upload is live only once YouTube says so.

It goes up private with a time on it, and YouTube makes it public at that
time. The only read-back was in memory a minute after the upload, so here it
stayed private for good and a restart lost even that. The worker now asks a
few times after the time (+2 min, +10 min, +1 h, +6 h, +24 h), one quota unit
each, and server/posts.py youtube_view reads the answers back.

Synthetic rows, frozen clocks and a fake publisher; nothing talks to YouTube.
"""

import json
from datetime import datetime, timedelta, timezone

import pytest

from server import posts
from server import youtube_service as service

T = datetime(2026, 10, 1, 12, 0, tzinfo=timezone.utc)  # the scheduled time


def _utc(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _clip(db, start=0.0, end=30.0, render_opts=""):
    db.upsert_video("vid", title="t")
    return db.add_clip("vid", start, end, 90, "hook", path="clip.mp4", render_opts=render_opts)


def _upload(db, clip_id, *, publish_at=T, actual="private", privacy="public", state="uploaded",
            checked=None, video="abcdefghijk"):
    clip = db.get_clip(clip_id)
    db.record_publish(clip_id, {
        "youtube_id": video, "video_id": clip["video_id"], "start_s": clip["start_s"],
        "end_s": clip["end_s"], "privacy": privacy, "actual_privacy": actual,
        "publish_at": _utc(publish_at) if publish_at else "", "state": state,
        "checked_at": _utc(checked) if checked else "",
    })
    return db.get_upload(clip_id)


def _view(db, clip_id, at):
    return posts.youtube_view(db.get_upload(clip_id), db.get_clip(clip_id), at)


# ---- what the upload is ---------------------------------------------------------


def test_not_live_before_its_time(db):
    clip_id = _clip(db)
    _upload(db, clip_id)

    assert _view(db, clip_id, T - timedelta(minutes=5)) == {
        "state": "scheduled", "live": False, "post_url": ""}
    assert service.due_live_checks(db, T - timedelta(minutes=5)) == []


def test_checking_until_it_is_read_back(db):
    clip_id = _clip(db)
    # The read-back a minute after the upload, long before its time, is not
    # a check that it went live.
    _upload(db, clip_id, checked=T - timedelta(days=1))

    assert _view(db, clip_id, T + timedelta(minutes=30))["state"] == "checking"
    assert _view(db, clip_id, T + timedelta(hours=30))["state"] == "checking"
    # And while it reads checking, a check is on its way, however late.
    for later in (timedelta(minutes=30), timedelta(hours=30), timedelta(days=60)):
        assert [r["clip_id"] for r in service.due_live_checks(db, T + later)] == [clip_id]


def test_a_scheduled_upload_is_not_live_on_what_was_asked_for(db):
    """Scheduling is a private upload whatever privacy was asked for, so a
    request for public says nothing until YouTube is read back."""
    clip_id = _clip(db)
    _upload(db, clip_id, actual="", privacy="public")
    assert _view(db, clip_id, T + timedelta(minutes=30)) == {
        "state": "checking", "live": False, "post_url": ""}


@pytest.fixture
def youtube(monkeypatch):
    """YouTube publishing on, with a channel that answers from a script."""
    from server import publisher

    answers: list = []
    asked: list = []
    emitted: list = []

    class FakeYouTube:
        def __init__(self, channel_id):
            self.channel_id = channel_id

        def video_status(self, video_id):
            asked.append((self.channel_id, video_id))
            answer = answers.pop(0)
            if isinstance(answer, Exception):
                raise answer
            return answer

    monkeypatch.setattr(service, "is_enabled", lambda _db: True)
    monkeypatch.setattr(service, "make_publisher",
                        lambda _config, _data, channel_id=None: FakeYouTube(channel_id))
    monkeypatch.setattr(publisher.broadcaster, "publish", emitted.append)
    return {"answers": answers, "asked": asked, "emitted": emitted}


def _worker(tmp_path):
    from server.publisher import PublishWorker

    w = PublishWorker.__new__(PublishWorker)
    w.config, w.data_dir = {}, tmp_path
    return w


def _check(worker, db, at):
    worker._next_youtube_check = 0.0  # the ten-minute timer, out of the way
    worker._check_scheduled_youtube(db, now=at)


def test_private_at_the_first_check_and_public_at_the_second_is_live(db, tmp_path, youtube):
    """YouTube can publish a minute or two late, so a first answer of private
    is not the last word."""
    clip_id = _clip(db)
    db.record_publish(clip_id, {"youtube_id": "abcdefghijk", "channel_id": "UC-mine",
                                "publish_at": _utc(T), "actual_privacy": "private"})
    youtube["answers"] += [{"privacy": "private", "upload_status": "processed"},
                           {"privacy": "public", "upload_status": "processed"}]
    w = _worker(tmp_path)

    _check(w, db, T + timedelta(minutes=3))
    assert _view(db, clip_id, T + timedelta(minutes=3))["state"] == "checking"

    _check(w, db, T + timedelta(minutes=13))
    seen = _view(db, clip_id, T + timedelta(minutes=13))

    assert seen == {"state": "published", "live": True,
                    "post_url": "https://www.youtube.com/shorts/abcdefghijk"}
    assert youtube["asked"] == [("UC-mine", "abcdefghijk")] * 2, "through its own channel"
    assert service.due_live_checks(db, T + timedelta(days=2)) == [], "live: never asked again"
    left = service.due_live_checks(db, T + timedelta(days=2), last_tried=w._youtube_tried)
    assert (left, w._youtube_tried) == ([], {}), "its try is forgotten once it is not due"


@pytest.mark.parametrize("checked, expected", [
    (None, timedelta(minutes=2)),
    (timedelta(days=-1), timedelta(minutes=2)),       # the read-back after the upload
    (timedelta(minutes=2), timedelta(minutes=10)),
    (timedelta(minutes=10), timedelta(hours=1)),
    (timedelta(hours=1), timedelta(hours=6)),
    (timedelta(hours=3), timedelta(hours=6)),         # a late check counts as the one it covered
    (timedelta(hours=6), timedelta(hours=24)),
    (timedelta(hours=24), None),
    (timedelta(days=3), None),
])
def test_the_step_schedule(checked, expected):
    row = {"publish_at": _utc(T), "checked_at": _utc(T + checked) if checked is not None else ""}
    got = posts.next_live_check(row)
    assert got == (T + expected if expected is not None else None)


def test_due_checks_follow_the_steps_and_stop_after_the_last(db):
    clip_id = _clip(db)
    _upload(db, clip_id)

    def due(at):
        return [r["clip_id"] for r in service.due_live_checks(db, at)]

    assert due(T + timedelta(minutes=1)) == []
    assert due(T + timedelta(minutes=2)) == [clip_id]
    db.set_upload_check(clip_id, checked_at=_utc(T + timedelta(minutes=2, seconds=30)))
    assert due(T + timedelta(minutes=9)) == []
    assert due(T + timedelta(minutes=10)) == [clip_id]
    db.set_upload_check(clip_id, checked_at=_utc(T + timedelta(hours=24, minutes=5)))
    assert due(T + timedelta(days=3)) == [], "after the +24 h check, never again"


@pytest.mark.parametrize("answer, state, live", [
    ({"privacy": "public", "upload_status": "processed"}, "published", True),
    ({"privacy": "private", "upload_status": "processed"}, "private", False),
])
def test_an_upload_a_month_past_its_time_is_asked_once_and_settles(db, tmp_path, youtube,
                                                                    answer, state, live):
    """Uploaded before these checks existed (its only read-back the one a
    minute after the upload), or the app closed all week: it is asked once,
    and the answer ends its schedule instead of reading "checking" for good."""
    clip_id = _clip(db)
    _upload(db, clip_id, checked=T - timedelta(days=2))
    late = T + timedelta(days=30)
    assert [r["clip_id"] for r in service.due_live_checks(db, late)] == [clip_id]
    youtube["answers"].append(answer)

    _check(_worker(tmp_path), db, late)

    assert youtube["asked"] == [(None, "abcdefghijk")]
    assert _view(db, clip_id, late + timedelta(minutes=1))["state"] == state
    assert _view(db, clip_id, late + timedelta(days=365))["live"] is live
    assert service.due_live_checks(db, late + timedelta(days=1)) == [], "asked once"


def test_the_app_closed_between_two_checks_for_over_a_week(db, tmp_path, youtube):
    clip_id = _clip(db)
    _upload(db, clip_id, checked=T + timedelta(minutes=2, seconds=30))  # +2 min: private
    youtube["answers"].append({"privacy": "private", "upload_status": "processed"})

    _check(_worker(tmp_path), db, T + timedelta(days=8))

    assert _view(db, clip_id, T + timedelta(days=8, minutes=1))["state"] == "private"


def test_a_late_check_that_fails_waits_for_the_next_start(db, tmp_path, youtube):
    """Past its week a failing check is not tried on every pass, only once
    each time the app starts (a new worker, so a new memory of tries)."""
    clip_id = _clip(db)
    _upload(db, clip_id)
    youtube["answers"].append(RuntimeError("token revoked"))
    w = _worker(tmp_path)
    late = T + timedelta(days=9)

    _check(w, db, late)
    _check(w, db, late + timedelta(minutes=10))
    _check(w, db, late + timedelta(minutes=20))

    assert youtube["asked"] == [(None, "abcdefghijk")]
    assert w._youtube_tried == {clip_id: late.timestamp()}, "remembered, not pruned"
    assert _view(db, clip_id, late)["state"] == "checking"
    assert [r["clip_id"] for r in service.due_live_checks(db, late, last_tried={})] == [clip_id]


def test_a_backlog_of_late_uploads_does_not_hold_up_a_new_one(db):
    ids = [_clip(db, start=i * 40.0, end=i * 40.0 + 30) for i in range(11)]
    for clip_id in ids[:10]:
        _upload(db, clip_id, publish_at=T - timedelta(days=20))
    _upload(db, ids[10])
    due = service.due_live_checks(db, T + timedelta(minutes=3))
    assert len(due) == 10 and due[0]["clip_id"] == ids[10]


def test_only_scheduled_uploads_not_yet_public_are_asked_about(db):
    ids = [_clip(db, start=i * 40.0, end=i * 40.0 + 30) for i in range(5)]
    _upload(db, ids[0])
    _upload(db, ids[4], video="")                               # never got an id
    _upload(db, ids[1], publish_at=None, actual="public")       # not scheduled
    _upload(db, ids[2], actual="public")                        # already read back
    _upload(db, ids[3], state="rejected")
    assert [r["clip_id"] for r in service.due_live_checks(db, T + timedelta(hours=2))] == [ids[0]]


def test_still_private_after_the_last_check_is_private_and_stays_uploaded(db, tmp_path, youtube):
    clip_id = _clip(db)
    _upload(db, clip_id, checked=T + timedelta(hours=6, minutes=4))
    youtube["answers"].append({"privacy": "private", "upload_status": "processed"})

    _check(_worker(tmp_path), db, T + timedelta(hours=24, minutes=3))

    assert _view(db, clip_id, T + timedelta(hours=25))["state"] == "private"
    row = db.get_upload(clip_id)
    # Never an audit lock: the owner may have made it private himself.
    assert (row["state"], row["actual_privacy"]) == ("uploaded", "private")
    assert service.due_live_checks(db, T + timedelta(days=2)) == []


def test_nothing_returned_writes_only_when_it_was_checked(db, tmp_path, youtube):
    clip_id = _clip(db)
    _upload(db, clip_id)
    youtube["answers"].append({})

    _check(_worker(tmp_path), db, T + timedelta(minutes=3))

    row = db.get_upload(clip_id)
    assert (row["actual_privacy"], row["state"]) == ("private", "uploaded")
    assert row["checked_at"] == _utc(T + timedelta(minutes=3))


def test_a_rejection_after_its_time_is_recorded_and_announced(db, tmp_path, youtube):
    clip_id = _clip(db)
    _upload(db, clip_id)
    youtube["answers"].append({"privacy": "private", "upload_status": "rejected",
                               "rejection_reason": "duplicate"})

    _check(_worker(tmp_path), db, T + timedelta(minutes=3))

    row = db.get_upload(clip_id)
    assert (row["state"], row["error"]) == ("rejected", "YouTube rejected this video: duplicate.")
    assert _view(db, clip_id, T + timedelta(minutes=4))["state"] == "failed"
    assert [(e["terminal"], e["state"]) for e in youtube["emitted"]] == [("checked", "rejected")]


def test_a_check_that_fails_is_not_counted_and_takes_its_turn(db, tmp_path, youtube, capsys):
    """An unreachable upload is asked again next pass, but behind one that was
    never asked, so a dozen that keep failing cannot hold up a new one."""
    ids = [_clip(db, start=i * 40.0, end=i * 40.0 + 30) for i in range(11)]
    for clip_id in ids[:10]:
        _upload(db, clip_id)
    youtube["answers"] += [RuntimeError("token revoked")] * 10
    w = _worker(tmp_path)

    _check(w, db, T + timedelta(minutes=3))
    assert all(db.get_upload(c)["checked_at"] == "" for c in ids[:10]), "not a check"
    assert "Could not check 10 scheduled YouTube upload(s)." in capsys.readouterr().out

    _upload(db, ids[10], publish_at=T + timedelta(minutes=5))
    first = service.due_live_checks(db, T + timedelta(minutes=13), last_tried=w._youtube_tried)
    assert len(first) == 10 and first[0]["clip_id"] == ids[10]


def test_set_upload_check_leaves_uploaded_at_and_the_rest_alone(db):
    clip_id = _clip(db)
    _upload(db, clip_id)
    db.conn.execute("UPDATE uploads SET uploaded_at = '2026-09-30T08:00:00', title = 'As sent'")
    db.conn.commit()

    db.set_upload_check(clip_id, checked_at=_utc(T), actual_privacy="public")

    row = db.get_upload(clip_id)
    assert (row["uploaded_at"], row["title"], row["state"]) == (
        "2026-09-30T08:00:00", "As sent", "uploaded")
    assert (row["actual_privacy"], row["checked_at"]) == ("public", _utc(T))


def test_the_check_is_off_with_youtube_off_and_never_raises(db, tmp_path, monkeypatch, capsys):
    asked = []
    monkeypatch.setattr(service, "is_enabled", lambda _db: False)
    monkeypatch.setattr(service, "due_live_checks", lambda *_a, **_k: asked.append(1) or [])
    w = _worker(tmp_path)
    _check(w, db, T)
    assert asked == []

    def down(*_a, **_k):
        raise RuntimeError("database is locked")

    monkeypatch.setattr(service, "is_enabled", lambda _db: True)
    monkeypatch.setattr(service, "due_live_checks", down)
    _check(w, db, T)  # must not raise
    assert "database is locked" in capsys.readouterr().out


def test_the_check_runs_on_its_own_ten_minute_timer(db, tmp_path, monkeypatch):
    asked = []
    monkeypatch.setattr(service, "is_enabled", lambda _db: True)
    monkeypatch.setattr(service, "due_live_checks", lambda *_a, **_k: asked.append(1) or [])
    w = _worker(tmp_path)
    w._check_scheduled_youtube(db, now=T)
    w._check_scheduled_youtube(db, now=T)
    assert asked == [1]


# ---- private and locked ---------------------------------------------------------


def test_a_locked_upload_and_one_asked_to_be_private_are_private(db):
    locked, asked_private = _clip(db, 0, 30), _clip(db, 40, 70)
    _upload(db, locked, publish_at=None, actual="private", state="locked_private")
    _upload(db, asked_private, publish_at=None, privacy="private", actual="private")
    for clip_id in (locked, asked_private):
        assert _view(db, clip_id, T) == {"state": "private", "live": False, "post_url": ""}


def test_an_unlisted_upload_is_live(db):
    """Live, with its link; a campaign's public check is what refuses it."""
    clip_id = _clip(db)
    _upload(db, clip_id, publish_at=None, privacy="unlisted", actual="unlisted")
    assert _view(db, clip_id, T)["live"] is True


# ---- the link ---------------------------------------------------------------------


@pytest.mark.parametrize("start, end, render_opts, url", [
    (0.0, 30.0, "", "https://www.youtube.com/shorts/abcdefghijk"),
    (0.0, 180.0, "", "https://www.youtube.com/shorts/abcdefghijk"),
    (0.0, 200.0, "", "https://www.youtube.com/watch?v=abcdefghijk"),
    # A Longform render is a 16:9 video, never a Short, however short.
    (0.0, 60.0, json.dumps({"profile": "short_clips"}), "https://www.youtube.com/watch?v=abcdefghijk"),
    (0.0, 60.0, json.dumps({"gaming": True}), "https://www.youtube.com/shorts/abcdefghijk"),
])
def test_a_short_links_to_its_shorts_page_and_the_rest_to_watch(db, start, end, render_opts, url):
    clip_id = _clip(db, start, end, render_opts=render_opts)
    _upload(db, clip_id, publish_at=None, actual="public")
    assert _view(db, clip_id, T)["post_url"] == url
