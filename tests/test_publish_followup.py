"""Following a post until it is live with a link.

Three gaps this closes, each found by reading what the background refresh
actually asked about:

* A post a provider called published without a link was never asked about
  again, because only queued and processing posts were followed.
* WoopSocial's refresh asked about the newest 60 requests. A watch spreads its
  clips over days, so the newest were the ones due last, and a day-one post
  went unchecked for a week. It also asked WoopSocial about Upload-Post ids.
* Upload-Post had no background check at all, so a scheduled post never got
  its link unless its panel happened to be open.

Every waiting window counts from when the post is due, never from when its
row was written, so a post scheduled weeks ahead is not treated as late.
Synthetic rows and frozen clocks only; nothing here talks to a provider.
"""

from datetime import datetime, timedelta, timezone

import pytest

from core.state import StateDB
from publish.uploadpost import FanOutResult, PlatformOutcome
from server import posts
from server import uploadpost_service as up
from server import woopsocial_service as woop

NOW = datetime(2026, 10, 1, 12, 0, tzinfo=timezone.utc)


def _local(moment: datetime) -> str:
    """How core/state.py writes created_at: local time, no zone."""
    return moment.astimezone().replace(tzinfo=None).isoformat(timespec="seconds")


def _utc(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _post(db, clip_id, platform="youtube", *, provider="woopsocial", state="queued",
          request_id="", created=NOW, scheduled="", post_url="", caption=""):
    db.record_clip_publish(clip_id, platform, {
        "provider": provider, "state": state, "request_id": request_id,
        "scheduled_for": scheduled, "post_url": post_url, "caption": caption,
    })
    db.conn.execute(
        "UPDATE clip_publishes SET created_at = ? WHERE clip_id = ? AND platform = ?",
        (_local(created), clip_id, platform),
    )
    db.conn.commit()


def _up(db, clip_id, request_id, **kwargs):
    _post(db, clip_id, provider="upload_post", request_id=request_id, **kwargs)


def _row(db, clip_id, platform="youtube"):
    return db.conn.execute(
        "SELECT * FROM clip_publishes WHERE clip_id = ? AND platform = ?", (clip_id, platform)
    ).fetchone()


@pytest.fixture
def woop_checks(monkeypatch):
    """A WoopSocial that records what it was asked about and knows nothing."""
    asked: list[str] = []

    class Publisher:
        def __init__(self, client, project):
            pass

        def check(self, request_id):
            asked.append(request_id)
            return FanOutResult(request_id=request_id, outcomes=[])

    monkeypatch.setattr("publish.woopsocial.WoopSocialPublisher", Publisher)
    monkeypatch.setattr(woop, "make_client", lambda _d: object())
    monkeypatch.setattr(woop, "resolve_project", lambda _db, _c: "proj")
    return asked


# ---- when a post is due ------------------------------------------------------


def test_due_time_reads_local_created_at_and_utc_scheduled_for():
    created = datetime(2026, 9, 30, 8, 0)  # as written: local, no zone
    plain = {"created_at": created.isoformat(), "scheduled_for": ""}
    assert posts.due_time(plain) == created.astimezone()

    later = {"created_at": created.isoformat(), "scheduled_for": "2026-10-03T09:00:00Z"}
    assert posts.due_time(later) == datetime(2026, 10, 3, 9, 0, tzinfo=timezone.utc)

    # The later of the two: a time already past when the row was written
    # does not make the post due before it was sent.
    earlier = {"created_at": created.isoformat(), "scheduled_for": "2026-09-01T09:00:00+00:00"}
    assert posts.due_time(earlier) == created.astimezone()


def test_a_missing_or_unreadable_time_counts_as_due_now():
    # The rows tests/test_publish_selection.py stands in with have neither column.
    assert posts.due_time({"request_id": "r", "state": "queued"}, now=NOW) == NOW
    assert posts.due_time({"created_at": "not a time", "scheduled_for": ""}, now=NOW) == NOW
    assert posts.due_time(
        {"created_at": "2026-09-30T08:00:00", "scheduled_for": "soon"}, now=NOW
    ) == NOW


def test_an_upload_is_due_at_its_publish_time_or_when_it_was_uploaded():
    scheduled = {"publish_at": "2026-10-05T18:00:00Z", "uploaded_at": "2026-09-30T08:00:00"}
    assert posts.due_time(scheduled) == datetime(2026, 10, 5, 18, 0, tzinfo=timezone.utc)
    now_ = {"publish_at": "", "uploaded_at": "2026-09-30T08:00:00"}
    assert posts.due_time(now_) == datetime(2026, 9, 30, 8, 0).astimezone()


# ---- links that came late -----------------------------------------------------


def test_missing_links_are_asked_about_for_a_day_after_the_post_was_due(db):
    _post(db, 1, state="published", request_id="fresh", created=NOW - timedelta(hours=2))
    _post(db, 2, state="published", request_id="stale", created=NOW - timedelta(hours=30))
    _post(db, 3, state="published", request_id="linked", created=NOW - timedelta(hours=2),
          post_url="https://www.youtube.com/shorts/abcdefghijk")
    _post(db, 4, state="processing", request_id="moving", created=NOW - timedelta(hours=2))
    _up(db, 5, "other", state="published", created=NOW - timedelta(hours=2))
    # Made 20 days ago for a time three hours ago: the day counts from then.
    _post(db, 6, state="published", request_id="planned", created=NOW - timedelta(days=20),
          scheduled=_utc(NOW - timedelta(hours=3)))

    assert posts.missing_url_requests(db, "woopsocial", now=NOW) == ["planned", "fresh"]
    assert posts.missing_url_requests(db, "upload_post", now=NOW) == ["other"]


def _learns(monkeypatch, provider, outcomes):
    """A provider whose status check reports `outcomes` for any request."""
    asked: list[str] = []

    class Publisher:
        def __init__(self, *_args):
            pass

        def check(self, request_id):
            asked.append(request_id)
            return FanOutResult(request_id=request_id, outcomes=list(outcomes))

    if provider is woop:
        monkeypatch.setattr("publish.woopsocial.WoopSocialPublisher", Publisher)
        monkeypatch.setattr(woop, "resolve_project", lambda _db, _c: "proj")
    else:
        monkeypatch.setattr("publish.uploadpost.UploadPostPublisher", Publisher)
    monkeypatch.setattr(provider, "make_client", lambda _d: object())
    return asked


@pytest.mark.parametrize("service, provider", [(woop, "woopsocial"), (up, "upload_post")])
def test_a_link_that_arrives_after_publishing_is_filled_in(db, tmp_path, monkeypatch,
                                                          service, provider):
    url = "https://www.tiktok.com/@someone/video/7300000000000000001"
    asked = _learns(monkeypatch, service, [
        PlatformOutcome(platform="tiktok", state="published", post_id="73", post_url=url),
    ])
    _post(db, 1, "tiktok", provider=provider, state="published", request_id="r1",
          created=NOW - timedelta(hours=1), caption="As sent")

    service.refresh_missing_urls(db, tmp_path, now=NOW)

    assert asked == ["r1"]
    row = _row(db, 1, "tiktok")
    assert row["post_url"] == url
    assert row["caption"] == "As sent", "a refresh never rewrites what the post said"


# ---- WoopSocial: only its own requests, due ones first ------------------------


def test_in_flight_is_filtered_by_provider_in_sql(db):
    _post(db, 1, request_id="w1")
    _up(db, 2, "u1")
    assert woop.in_flight(db) == ["w1"]
    assert woop.in_flight(db, provider="upload_post") == ["u1"]
    assert up.in_flight(db) == ["u1"]


def test_woopsocial_is_never_asked_about_an_upload_post_request(db, tmp_path, woop_checks):
    _up(db, 1, "u1")
    _post(db, 2, request_id="w1")
    out = woop.refresh_in_flight(db, tmp_path, now=NOW)
    assert woop_checks == ["w1"]
    assert out["failed"] == 0


def test_refresh_checks_exactly_the_ids_it_is_given(db, tmp_path, woop_checks):
    _post(db, 1, request_id="w1")
    _post(db, 2, request_id="w2", scheduled=_utc(NOW + timedelta(days=3)))
    out = woop.refresh_in_flight(db, tmp_path, ids=["w2"], now=NOW)
    assert woop_checks == ["w2"]
    assert out["checked"] == 1


def test_a_due_post_is_checked_even_behind_61_scheduled_ones(db, tmp_path, woop_checks):
    """The newest 60 used to be checked, and with a long schedule the newest
    are the posts due last."""
    _post(db, 1, request_id="due", created=NOW - timedelta(days=1),
          scheduled=_utc(NOW - timedelta(hours=1)))
    for i in range(61):
        _post(db, 100 + i, request_id=f"later-{i:02d}", created=NOW - timedelta(minutes=30),
              scheduled=_utc(NOW + timedelta(days=2 + i)))

    out = woop.refresh_in_flight(db, tmp_path, now=NOW)

    assert woop_checks == ["due"]
    assert out["checked"] == 1
    assert out["still_waiting"] == 62


def test_due_requests_come_oldest_due_first(db):
    _post(db, 1, request_id="c", created=NOW - timedelta(minutes=10))
    _post(db, 2, request_id="a", created=NOW - timedelta(hours=3))
    _post(db, 3, request_id="b", created=NOW - timedelta(days=2),
          scheduled=_utc(NOW - timedelta(hours=1)))
    _post(db, 4, request_id="soon", created=NOW - timedelta(hours=1),
          scheduled=_utc(NOW + timedelta(minutes=1)))
    _post(db, 5, request_id="later", created=NOW - timedelta(hours=1),
          scheduled=_utc(NOW + timedelta(minutes=10)))
    # A request is due when its earliest destination is.
    _post(db, 6, "youtube", request_id="m", created=NOW - timedelta(hours=5),
          scheduled=_utc(NOW + timedelta(days=3)))
    _post(db, 6, "tiktok", request_id="m", created=NOW - timedelta(hours=5))

    assert woop.due_in_flight(db, now=NOW) == ["m", "a", "b", "c", "soon"]


def test_the_refresh_button_still_checks_the_newest_60(tmp_path, monkeypatch, woop_checks):
    """POST /woopsocial/refresh is a person asking: it checks what it always did."""
    pytest.importorskip("fastapi")
    pytest.importorskip("httpx")
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from server import woopsocial_api

    db_path = tmp_path / "state.db"
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    app = FastAPI()
    woopsocial_api.install(app, config={}, db=lambda: StateDB(db_path), data_dir=data_dir)
    client = TestClient(app, base_url="http://127.0.0.1")

    d = StateDB(db_path)
    woop.save_settings(d, {"enabled": True, "project_id": "proj1"})
    ahead = datetime.now(timezone.utc) + timedelta(days=1)
    for i in range(62):
        _post(d, i + 1, request_id=f"r{i:02d}", created=NOW - timedelta(minutes=62 - i),
              scheduled=_utc(ahead + timedelta(days=i)))
    d.close()
    monkeypatch.setattr(woop, "has_key", lambda _p: True)

    got = client.post("/woopsocial/refresh")

    assert got.status_code == 200
    assert got.json()["checked"] == 60
    assert sorted(woop_checks) == [f"r{i:02d}" for i in range(2, 62)]


def test_committed_times_ignore_upload_post(db):
    _post(db, 1, request_id="w", scheduled="2099-01-01T09:00:00+00:00")
    _up(db, 2, "u", scheduled="2099-01-02T09:00:00+00:00")
    assert woop.committed_times(db) == ["2099-01-01T09:00:00+00:00"]


# ---- Upload-Post in the background ---------------------------------------------


def test_upload_post_follows_due_posts_for_14_days_after_they_were_due(db):
    _up(db, 1, "future", created=NOW - timedelta(hours=1), scheduled=_utc(NOW + timedelta(hours=1)))
    _up(db, 2, "old", created=NOW - timedelta(days=16), scheduled=_utc(NOW - timedelta(days=15)))
    _up(db, 3, "due", created=NOW - timedelta(days=1), scheduled=_utc(NOW - timedelta(minutes=5)))
    # Scheduled 20 days after it was sent: followed once it comes due.
    _up(db, 4, "planned", created=NOW - timedelta(days=20), scheduled=_utc(NOW - timedelta(minutes=1)))
    _up(db, 5, "done", state="published", created=NOW - timedelta(minutes=5))
    _post(db, 6, request_id="woop", created=NOW - timedelta(minutes=5))

    assert up.due_requests(db, now=NOW, last_checked={}) == ["due", "planned"]


def test_upload_post_slows_down_as_a_post_ages(db):
    due = NOW - timedelta(minutes=1)
    _up(db, 1, "r", created=due - timedelta(days=1), scheduled=_utc(due))
    checked: dict = {}

    def asks(at):
        return up.due_requests(db, now=at, last_checked=checked) == ["r"]

    def check(at):
        checked["r"] = at.timestamp()

    # The first hour after it is due: every 3 minutes.
    t = due + timedelta(minutes=1)
    assert asks(t)
    check(t)
    assert not asks(t + timedelta(minutes=2))
    assert asks(t + timedelta(minutes=3))
    # Then every 30 minutes until a day has passed.
    t = due + timedelta(hours=2)
    check(t)
    assert not asks(t + timedelta(minutes=20))
    assert asks(t + timedelta(minutes=30))
    # Then every 3 hours.
    t = due + timedelta(hours=25)
    check(t)
    assert not asks(t + timedelta(hours=2))
    assert asks(t + timedelta(hours=3))


def test_an_add_to_queue_request_starts_at_the_30_minute_step(db):
    """Upload-Post picks the time, so the row's time says nothing about when
    it goes out: no point asking every three minutes."""
    _up(db, 1, "queued", created=NOW - timedelta(minutes=10))
    checked = {"queued": (NOW - timedelta(minutes=5)).timestamp()}
    assert up.due_requests(db, now=NOW, last_checked=checked) == []
    assert up.due_requests(db, now=NOW + timedelta(minutes=25), last_checked=checked) == ["queued"]


def test_check_times_of_finished_requests_are_forgotten(db):
    _up(db, 1, "r", created=NOW - timedelta(minutes=5))
    checked = {"gone": 1.0}
    up.due_requests(db, now=NOW, last_checked=checked)
    assert "gone" not in checked


def test_the_upload_post_refresh_records_what_it_learns(db, tmp_path, monkeypatch):
    up.save_settings(db, {"enabled": True, "profile": "me"})
    url = "https://www.youtube.com/shorts/abcdefghijk"
    asked = _learns(monkeypatch, up, [
        PlatformOutcome(platform="youtube", state="published", post_id="abcdefghijk", post_url=url),
    ])
    _up(db, 7, "r", created=NOW - timedelta(minutes=5), caption="As sent")
    db.conn.execute(
        "UPDATE clip_publishes SET video_id = 'v1', start_s = 1, end_s = 9 WHERE clip_id = 7"
    )
    db.conn.commit()
    checked: dict = {}

    out = up.refresh_in_flight(db, tmp_path, now=NOW, last_checked=checked)

    assert asked == ["r"]
    assert (out["checked"], out["updated"], out["still_waiting"]) == (1, 1, 0)
    assert checked == {"r": NOW.timestamp()}
    row = _row(db, 7)
    assert (row["state"], row["post_url"], row["request_id"]) == ("published", url, "r")
    assert (row["video_id"], row["start_s"], row["end_s"], row["caption"]) == ("v1", 1, 9, "As sent")


def test_an_unreachable_upload_post_request_is_counted_not_raised(db, tmp_path, monkeypatch):
    class Publisher:
        def __init__(self, *_args):
            pass

        def check(self, request_id):
            raise RuntimeError("upload-post is down")

    monkeypatch.setattr("publish.uploadpost.UploadPostPublisher", Publisher)
    monkeypatch.setattr(up, "make_client", lambda _d: object())
    _up(db, 1, "r", created=NOW - timedelta(minutes=5))
    out = up.refresh_in_flight(db, tmp_path, now=NOW, last_checked={})
    assert (out["checked"], out["failed"]) == (1, 1)


# ---- the worker -------------------------------------------------------------------


def _worker(tmp_path):
    """The publish worker's background checks, without starting its thread."""
    from server.publisher import PublishWorker

    w = PublishWorker.__new__(PublishWorker)
    w.data_dir = tmp_path
    w._next_provider_check = 0.0
    return w


def _woop_on(monkeypatch, *, in_flight):
    monkeypatch.setattr(woop, "is_enabled", lambda _db: True)
    monkeypatch.setattr(woop, "has_key", lambda _d: True)
    monkeypatch.setattr(woop, "reconcile_sending", lambda _db, _d: {"adopted": 0, "failed": 0})
    monkeypatch.setattr(woop, "in_flight", lambda _db: list(in_flight))


def _up_on(monkeypatch, calls):
    monkeypatch.setattr(up, "is_enabled", lambda _db: True)
    monkeypatch.setattr(up, "has_key", lambda _d: True)
    monkeypatch.setattr(up, "due_requests", lambda _db, **kw: calls.append(("due", kw)) or ["u1"])

    def refresh(_db, _d, **kw):
        calls.append(("refresh", kw))
        return {"checked": 1, "updated": 0, "still_waiting": 1, "failed": 0}

    monkeypatch.setattr(up, "refresh_in_flight", refresh)


def test_woopsocial_having_nothing_in_flight_does_not_skip_upload_post(monkeypatch, tmp_path):
    calls: list = []
    _woop_on(monkeypatch, in_flight=[])
    _up_on(monkeypatch, calls)
    w = _worker(tmp_path)

    w._refresh_provider_posts(object())

    assert [c[0] for c in calls] == ["due", "refresh"]
    assert calls[1][1]["ids"] == ["u1"]
    # The check times live on the worker, created on first use.
    assert calls[1][1]["last_checked"] is w._uploadpost_checked


def test_woopsocial_failing_does_not_stop_upload_post(monkeypatch, tmp_path, capsys):
    calls: list = []

    def down(_db):
        raise RuntimeError("woopsocial is down")

    monkeypatch.setattr(woop, "is_enabled", down)
    _up_on(monkeypatch, calls)

    _worker(tmp_path)._refresh_provider_posts(object())

    assert [c[0] for c in calls] == ["due", "refresh"]
    assert "woopsocial is down" in capsys.readouterr().out


def test_upload_post_failing_does_not_stop_woopsocial_or_the_worker(monkeypatch, tmp_path, capsys):
    ran = []
    _woop_on(monkeypatch, in_flight=["w1"])
    monkeypatch.setattr(
        woop, "refresh_in_flight",
        lambda _db, _d: ran.append(1) or {"checked": 1, "updated": 0, "still_waiting": 1, "failed": 0},
    )
    monkeypatch.setattr(up, "is_enabled", lambda _db: True)
    monkeypatch.setattr(up, "has_key", lambda _d: True)

    def down(_db, **_kw):
        raise RuntimeError("upload-post is down")

    monkeypatch.setattr(up, "due_requests", down)

    _worker(tmp_path)._refresh_provider_posts(object())  # must not raise

    assert ran == [1]
    assert "upload-post is down" in capsys.readouterr().out


def test_missing_links_are_checked_on_their_own_slower_timer(monkeypatch, tmp_path, capsys):
    asked = []
    for service, name in ((woop, "woop"), (up, "up")):
        monkeypatch.setattr(service, "is_enabled", lambda _db: True)
        monkeypatch.setattr(service, "has_key", lambda _d: True)

    def woop_links(_db, _d, *, now):
        asked.append("woop")
        raise RuntimeError("woopsocial is down")

    def up_links(_db, _d, *, now):
        asked.append("up")
        return {"checked": 1, "updated": 1, "still_waiting": 0, "failed": 0}

    monkeypatch.setattr(woop, "refresh_missing_urls", woop_links)
    monkeypatch.setattr(up, "refresh_missing_urls", up_links)
    w = _worker(tmp_path)

    w._refresh_missing_urls(object())
    w._refresh_missing_urls(object())

    assert asked == ["woop", "up"], "one failing does not stop the other; not again within 15 min"
    assert "woopsocial is down" in capsys.readouterr().out
