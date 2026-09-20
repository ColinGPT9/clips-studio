"""Upload-Post HTTP routes.

TestClient with an explicit base_url because of TrustedHostMiddleware, the
same as tests/test_clip_marks.py.
"""

from pathlib import Path

import pytest

pytest.importorskip("httpx")

from fastapi import FastAPI
from fastapi.testclient import TestClient

from core.state import StateDB
from server import uploadpost_api
from server import uploadpost_service as service


@pytest.fixture
def client(tmp_path: Path, monkeypatch):
    db_path = tmp_path / "state.db"
    data_dir = tmp_path / "data"
    data_dir.mkdir()

    app = FastAPI()
    uploadpost_api.install(
        app, config={}, db=lambda: StateDB(db_path), data_dir=data_dir
    )
    c = TestClient(app, base_url="http://127.0.0.1")
    c.db_path = db_path
    c.data_dir = data_dir
    return c


def _enable(client):
    d = StateDB(client.db_path)
    service.save_settings(d, {"enabled": True, "profile": "me"})
    d.close()


# ---- the off state ---------------------------------------------------------


def test_disabled_feature_looks_absent(client):
    """Off should read as 'not here', not as 'here but refused'."""
    status = client.get("/uploadpost/status").json()
    assert status["enabled"] is False
    assert status["has_key"] is False

    for path in ("/uploadpost/profiles", "/uploadpost/clips/1"):
        assert client.get(path).status_code == 404
    assert client.post("/uploadpost/connect", json={}).status_code == 404


def test_status_never_carries_the_key(client, monkeypatch):
    monkeypatch.setattr(service, "load_key", lambda _d: "super-secret-key-1234")
    body = client.get("/uploadpost/status").text
    assert "super-secret-key-1234" not in body
    assert "super-secret" not in body


# ---- the key ---------------------------------------------------------------


def test_a_key_that_fails_validation_is_not_kept(client, monkeypatch):
    """A stored-but-broken key would make the settings card claim a
    connection that does not exist."""
    from publish.errors import AuthRequired

    class Rejecting:
        def validate_key(self):
            raise AuthRequired("Upload-Post did not accept that API key.")

    monkeypatch.setattr(service, "make_client", lambda _p: Rejecting())

    got = client.put("/uploadpost/key", json={"api_key": "bad"})
    assert got.status_code == 400
    assert not service.has_key(client.data_dir)


def test_a_good_key_is_stored_and_reported_only_by_its_tail(client, monkeypatch):
    monkeypatch.setattr(
        service,
        "make_client",
        lambda _p: type("C", (), {"validate_key": lambda self: {"plan": "Free"}})(),
    )

    got = client.put("/uploadpost/key", json={"api_key": "abcdefghij9876"})
    assert got.status_code == 200
    payload = got.json()

    assert payload["has_key"] is True
    assert payload["plan"] == "Free"
    assert payload["key_tail"] == "9876"
    assert "abcdefghij9876" not in got.text


def test_an_empty_key_is_refused(client):
    assert client.put("/uploadpost/key", json={"api_key": "   "}).status_code == 400


def test_the_key_can_be_removed(client, monkeypatch):
    service.save_key(client.data_dir, "abcdefghij9876")
    assert service.has_key(client.data_dir)

    got = client.request("DELETE", "/uploadpost/key")
    assert got.status_code == 200
    assert got.json()["has_key"] is False
    assert not service.has_key(client.data_dir)


# ---- settings --------------------------------------------------------------


def test_settings_round_trip_and_ignore_unknown_keys(client):
    got = client.patch(
        "/uploadpost/settings",
        json={"enabled": True, "profile": "me", "common_description": "Follow!"},
    )
    assert got.status_code == 200
    assert got.json()["profile"] == "me"
    assert got.json()["common_description"] == "Follow!"


def test_affiliate_url_is_empty_until_configured(client):
    """No affiliate claims anywhere until a real referral URL exists."""
    assert client.get("/uploadpost/status").json()["affiliate_url"] == ""


# ---- connecting ------------------------------------------------------------


def test_connect_creates_the_profile_and_returns_a_browser_link(client, monkeypatch):
    _enable(client)
    made = []

    class FakeClient:
        def list_profiles(self):
            return []

        def create_profile(self, username):
            made.append(username)

        def connect_url(self, username):
            return "https://app.upload-post.com/connect?token=jwt"

    monkeypatch.setattr(service, "has_key", lambda _p: True)
    monkeypatch.setattr(service, "make_client", lambda _p: FakeClient())

    got = client.post("/uploadpost/connect", json={"username": "colin"})
    assert got.status_code == 200
    assert got.json()["url"].startswith("https://app.upload-post.com/connect")
    assert got.json()["expires_hours"] == 48
    assert made == ["colin"]


def test_connect_with_no_profile_name_uses_the_default(client, monkeypatch):
    """A creator should never have to invent a name for something they will
    not look at again."""
    d = StateDB(client.db_path)
    service.save_settings(d, {"enabled": True})
    d.close()

    class FakeClient:
        def list_profiles(self):
            return []

        def create_profile(self, username):
            pass

        def connect_url(self, username):
            return "https://app.upload-post.com/connect?token=jwt"

    monkeypatch.setattr(service, "has_key", lambda _p: True)
    monkeypatch.setattr(service, "make_client", lambda _p: FakeClient())

    got = client.post("/uploadpost/connect", json={})
    assert got.status_code == 200
    assert got.json()["profile"] == service.DEFAULT_PROFILE


def test_status_always_names_a_profile(client):
    assert client.get("/uploadpost/status").json()["profile"] == service.DEFAULT_PROFILE


def test_connections_reports_what_is_linked(client, monkeypatch):
    _enable(client)
    monkeypatch.setattr(service, "has_key", lambda _p: True)
    monkeypatch.setattr(
        service,
        "make_client",
        lambda _p: type("C", (), {"connected_platforms": lambda self, u: ["youtube", "tiktok"]})(),
    )
    got = client.get("/uploadpost/connections").json()
    assert got["connected"] == ["youtube", "tiktok"]


# ---- publishing ------------------------------------------------------------


def _a_rendered_clip(client, tmp_path: Path) -> int:
    d = StateDB(client.db_path)
    d.upsert_video("vid1", title="A stream")
    clip_file = tmp_path / "clip.mp4"
    clip_file.write_bytes(b"video")
    clip_id = d.add_clip("vid1", 0.0, 10.0, 80, "hook", path=str(clip_file))
    d.close()
    return clip_id


def test_publishing_records_one_row_per_platform(client, monkeypatch, tmp_path):
    _enable(client)
    clip_id = _a_rendered_clip(client, tmp_path)

    monkeypatch.setattr(service, "has_key", lambda _p: True)
    monkeypatch.setattr(service, "make_client", lambda _p: object())

    from publish import uploadpost as up

    def fake_start(self, video, *, platforms, fields, thumbnail=None, idempotency_key=""):
        return up.FanOutResult(
            request_id="req-7",
            outcomes=[up.PlatformOutcome(platform=p) for p in platforms],
        )

    monkeypatch.setattr(up.UploadPostPublisher, "start", fake_start)

    got = client.post(
        f"/uploadpost/clips/{clip_id}/publish",
        json={"platforms": ["youtube", "tiktok"], "title": "My clip"},
    )
    assert got.status_code == 200
    payload = got.json()
    assert payload["request_id"] == "req-7"
    assert {p["platform"] for p in payload["platforms"]} == {"tiktok", "youtube"}
    assert payload["done"] is False


def test_publishing_needs_a_title_and_a_platform(client, monkeypatch, tmp_path):
    _enable(client)
    clip_id = _a_rendered_clip(client, tmp_path)
    monkeypatch.setattr(service, "has_key", lambda _p: True)
    monkeypatch.setattr(service, "make_client", lambda _p: object())

    no_title = client.post(
        f"/uploadpost/clips/{clip_id}/publish",
        json={"platforms": ["youtube"], "title": "  "},
    )
    assert no_title.status_code == 400

    no_platform = client.post(
        f"/uploadpost/clips/{clip_id}/publish",
        json={"platforms": [], "title": "T"},
    )
    assert no_platform.status_code == 400


def test_refresh_updates_each_platform_and_reports_done(client, monkeypatch, tmp_path):
    _enable(client)
    clip_id = _a_rendered_clip(client, tmp_path)
    monkeypatch.setattr(service, "has_key", lambda _p: True)
    monkeypatch.setattr(service, "make_client", lambda _p: object())

    d = StateDB(client.db_path)
    for platform in ("youtube", "tiktok"):
        d.record_clip_publish(
            clip_id, platform, {"state": "queued", "request_id": "req-7"}
        )
    d.close()

    from publish import uploadpost as up

    def fake_check(self, request_id):
        return up.FanOutResult(
            request_id=request_id,
            outcomes=[
                up.PlatformOutcome(
                    platform="youtube", state="published",
                    post_url="https://youtu.be/a", post_id="a",
                ),
                up.PlatformOutcome(
                    platform="tiktok", state="failed", error="Expired token"
                ),
            ],
        )

    monkeypatch.setattr(up.UploadPostPublisher, "check", fake_check)

    got = client.post("/uploadpost/refresh/req-7")
    assert got.status_code == 200
    payload = got.json()
    by_platform = {p["platform"]: p for p in payload["platforms"]}

    assert by_platform["youtube"]["state"] == "published"
    assert by_platform["youtube"]["post_url"] == "https://youtu.be/a"
    assert by_platform["tiktok"]["state"] == "failed"
    assert by_platform["tiktok"]["error"] == "Expired token"
    # Nothing still in flight, so the UI can stop polling.
    assert payload["done"] is True


def test_refreshing_an_unknown_request_is_a_404(client, monkeypatch):
    _enable(client)
    monkeypatch.setattr(service, "has_key", lambda _p: True)
    monkeypatch.setattr(service, "make_client", lambda _p: object())
    assert client.post("/uploadpost/refresh/nope").status_code == 404


# ---- Stage 2: overrides, scheduling, retry ---------------------------------


def _mock_start(monkeypatch):
    """Capture the fields a publish would actually send."""
    from publish import uploadpost as up

    sent = {}

    def fake_start(self, video, *, platforms, fields, thumbnail=None, idempotency_key=""):
        sent['fields'] = fields
        sent['platforms'] = platforms
        return up.FanOutResult(
            request_id="req-1",
            outcomes=[up.PlatformOutcome(platform=p) for p in platforms],
        )

    monkeypatch.setattr(up.UploadPostPublisher, "start", fake_start)
    return sent


def test_overrides_reach_the_api_under_their_platform_names(client, monkeypatch, tmp_path):
    _enable(client)
    clip_id = _a_rendered_clip(client, tmp_path)
    monkeypatch.setattr(service, "has_key", lambda _p: True)
    monkeypatch.setattr(service, "make_client", lambda _p: object())
    sent = _mock_start(monkeypatch)

    got = client.post(
        f"/uploadpost/clips/{clip_id}/publish",
        json={
            "platforms": ["youtube", "instagram"],
            "title": "Common title",
            "overrides": {"instagram": {"title": "IG only"}},
        },
    )
    assert got.status_code == 200
    fields = dict(sent["fields"])
    assert fields["title"] == "Common title"
    assert fields["instagram_title"] == "IG only"
    assert "youtube_title" not in fields


def test_facebook_without_a_page_id_is_refused_before_uploading(
    client, monkeypatch, tmp_path
):
    _enable(client)
    clip_id = _a_rendered_clip(client, tmp_path)
    monkeypatch.setattr(service, "has_key", lambda _p: True)
    monkeypatch.setattr(service, "make_client", lambda _p: object())
    _mock_start(monkeypatch)

    got = client.post(
        f"/uploadpost/clips/{clip_id}/publish",
        json={"platforms": ["facebook"], "title": "T"},
    )
    assert got.status_code == 400
    assert "Facebook page ID" in got.json()["detail"]


def test_scheduling_sends_the_time_and_the_timezone(client, monkeypatch, tmp_path):
    _enable(client)
    clip_id = _a_rendered_clip(client, tmp_path)
    monkeypatch.setattr(service, "has_key", lambda _p: True)
    monkeypatch.setattr(service, "make_client", lambda _p: object())
    sent = _mock_start(monkeypatch)

    got = client.post(
        f"/uploadpost/clips/{clip_id}/publish",
        json={
            "platforms": ["youtube"],
            "title": "T",
            "scheduled_date": "2099-01-01T12:00:00+00:00",
            "timezone": "America/Toronto",
        },
    )
    # 2099 is beyond their year horizon, so this must be refused, not sent.
    assert got.status_code == 400
    assert "365" in got.json()["detail"]
    assert "fields" not in sent


def test_a_time_and_the_queue_together_are_refused(client, monkeypatch, tmp_path):
    _enable(client)
    clip_id = _a_rendered_clip(client, tmp_path)
    monkeypatch.setattr(service, "has_key", lambda _p: True)
    monkeypatch.setattr(service, "make_client", lambda _p: object())
    _mock_start(monkeypatch)

    from datetime import datetime, timedelta
    from datetime import timezone as tz

    soon = (datetime.now(tz.utc) + timedelta(days=2)).isoformat()
    got = client.post(
        f"/uploadpost/clips/{clip_id}/publish",
        json={
            "platforms": ["youtube"],
            "title": "T",
            "scheduled_date": soon,
            "add_to_queue": True,
        },
    )
    assert got.status_code == 400
    assert "either" in got.json()["detail"].lower()


def test_the_queue_flag_is_sent_on_its_own(client, monkeypatch, tmp_path):
    _enable(client)
    clip_id = _a_rendered_clip(client, tmp_path)
    monkeypatch.setattr(service, "has_key", lambda _p: True)
    monkeypatch.setattr(service, "make_client", lambda _p: object())
    sent = _mock_start(monkeypatch)

    got = client.post(
        f"/uploadpost/clips/{clip_id}/publish",
        json={"platforms": ["youtube"], "title": "T", "add_to_queue": True},
    )
    assert got.status_code == 200
    fields = dict(sent["fields"])
    assert fields["add_to_queue"] == "true"
    assert "scheduled_date" not in fields


def test_retry_is_refused_when_nothing_failed(client, monkeypatch, tmp_path):
    _enable(client)
    clip_id = _a_rendered_clip(client, tmp_path)
    monkeypatch.setattr(service, "has_key", lambda _p: True)
    monkeypatch.setattr(service, "make_client", lambda _p: object())

    d = StateDB(client.db_path)
    d.record_clip_publish(clip_id, "youtube", {"state": "published", "request_id": "req-2"})
    d.close()

    assert client.post("/uploadpost/retry/req-2").status_code == 400


def test_retry_puts_only_the_failed_platform_back_in_flight(
    client, monkeypatch, tmp_path
):
    _enable(client)
    clip_id = _a_rendered_clip(client, tmp_path)
    monkeypatch.setattr(service, "has_key", lambda _p: True)
    monkeypatch.setattr(service, "make_client", lambda _p: object())

    d = StateDB(client.db_path)
    d.record_clip_publish(clip_id, "youtube", {"state": "published", "request_id": "req-2"})
    d.record_clip_publish(
        clip_id, "tiktok", {"state": "failed", "error": "boom", "request_id": "req-2"}
    )
    d.close()

    from publish import uploadpost as up

    # Their retry can acknowledge without fresh per-platform detail.
    monkeypatch.setattr(
        up.UploadPostPublisher, "retry", lambda self, rid: up.FanOutResult(request_id=rid)
    )

    got = client.post("/uploadpost/retry/req-2")
    assert got.status_code == 200
    states = {p["platform"]: p["state"] for p in got.json()["platforms"]}
    assert states["tiktok"] == "queued"
    # The one that worked is left completely alone.
    assert states["youtube"] == "published"


def test_capabilities_describe_what_each_platform_takes(client):
    got = client.get("/uploadpost/capabilities").json()["platforms"]
    assert got["youtube"]["thumbnail"] is True
    assert got["tiktok"]["thumbnail"] is False
    assert got["x"]["description"] is False
    assert got["facebook"]["requires"] == {"facebook_page_id": "Facebook page ID"}
    assert got["pinterest"]["first_comment"] is False


# ---- batch -----------------------------------------------------------------


def _clips(client, tmp_path: Path, n: int) -> list[int]:
    d = StateDB(client.db_path)
    d.upsert_video("vidb", title="A stream")
    ids = []
    for i in range(n):
        f = tmp_path / f"clip{i}.mp4"
        f.write_bytes(b"video")
        ids.append(d.add_clip("vidb", i * 10.0, i * 10.0 + 8, 70 + i, f"hook {i}", path=str(f)))
    d.close()
    return ids


def test_a_batch_starts_one_fan_out_per_clip(client, monkeypatch, tmp_path):
    _enable(client)
    ids = _clips(client, tmp_path, 3)
    monkeypatch.setattr(service, "has_key", lambda _p: True)
    monkeypatch.setattr(service, "make_client", lambda _p: object())

    from publish import uploadpost as up

    seen = []

    def fake_start(self, video, *, platforms, fields, thumbnail=None, idempotency_key=""):
        seen.append(dict(fields))
        return up.FanOutResult(
            request_id=f"req-{len(seen)}",
            outcomes=[up.PlatformOutcome(platform=p) for p in platforms],
        )

    monkeypatch.setattr(up.UploadPostPublisher, "start", fake_start)

    got = client.post(
        "/uploadpost/batch",
        json={"clip_ids": ids, "platforms": ["youtube", "tiktok"]},
    )
    assert got.status_code == 200
    assert len(got.json()["started"]) == 3
    assert got.json()["skipped"] == []
    # Each clip carries its own title, not one shared across the batch.
    assert {f["title"] for f in seen} == {"hook 0", "hook 1", "hook 2"}


def test_a_batch_spaces_posts_out_rather_than_firing_at_once(client, monkeypatch, tmp_path):
    """Twelve clips at the same instant reads as spam and burns the daily caps."""
    _enable(client)
    ids = _clips(client, tmp_path, 3)
    monkeypatch.setattr(service, "has_key", lambda _p: True)
    monkeypatch.setattr(service, "make_client", lambda _p: object())

    from publish import uploadpost as up

    times = []

    def fake_start(self, video, *, platforms, fields, thumbnail=None, idempotency_key=""):
        times.append(dict(fields).get("scheduled_date"))
        return up.FanOutResult(request_id="r", outcomes=[])

    monkeypatch.setattr(up.UploadPostPublisher, "start", fake_start)

    got = client.post(
        "/uploadpost/batch",
        json={"clip_ids": ids, "platforms": ["youtube"], "every_hours": 2},
    )
    assert got.status_code == 200
    assert all(times), "every clip in a spaced batch needs a time"
    assert times[0] < times[1] < times[2]


def test_a_batch_skips_a_bad_clip_and_keeps_going(client, monkeypatch, tmp_path):
    _enable(client)
    ids = _clips(client, tmp_path, 2)
    monkeypatch.setattr(service, "has_key", lambda _p: True)
    monkeypatch.setattr(service, "make_client", lambda _p: object())

    from publish import uploadpost as up

    monkeypatch.setattr(
        up.UploadPostPublisher,
        "start",
        lambda self, v, **k: up.FanOutResult(request_id="r", outcomes=[]),
    )

    got = client.post(
        "/uploadpost/batch",
        json={"clip_ids": [*ids, 999999], "platforms": ["youtube"]},
    )
    assert got.status_code == 200
    assert len(got.json()["started"]) == 2
    assert got.json()["skipped"] == [{"clip_id": 999999, "reason": "no such clip"}]


def test_a_batch_needs_clips_and_platforms(client, monkeypatch):
    _enable(client)
    monkeypatch.setattr(service, "has_key", lambda _p: True)
    monkeypatch.setattr(service, "make_client", lambda _p: object())

    assert client.post("/uploadpost/batch", json={"clip_ids": [], "platforms": ["youtube"]}).status_code == 400
    assert client.post("/uploadpost/batch", json={"clip_ids": [1], "platforms": []}).status_code == 400


def test_a_batch_carries_each_clips_own_hashtags(client, monkeypatch, tmp_path):
    """Hashtags are a JSON list in one column, not a space-separated string."""
    import json as _json

    _enable(client)
    monkeypatch.setattr(service, "has_key", lambda _p: True)
    monkeypatch.setattr(service, "make_client", lambda _p: object())

    d = StateDB(client.db_path)
    d.upsert_video("vidh", title="s")
    f = tmp_path / "h.mp4"
    f.write_bytes(b"v")
    clip_id = d.add_clip("vidh", 0, 5, 80, "hook", path=str(f))
    d.conn.execute(
        "UPDATE clips SET hashtags = ? WHERE id = ?",
        (_json.dumps(["#gaming", "clips"]), clip_id),
    )
    d.conn.commit()
    d.close()

    from publish import uploadpost as up

    sent = {}

    def fake_start(self, video, *, platforms, fields, thumbnail=None, idempotency_key=""):
        sent["fields"] = fields
        return up.FanOutResult(request_id="r", outcomes=[])

    monkeypatch.setattr(up.UploadPostPublisher, "start", fake_start)

    client.post("/uploadpost/batch", json={"clip_ids": [clip_id], "platforms": ["youtube"]})
    tags = [v for k, v in sent["fields"] if k == "tags[]"]
    # The leading # is stripped; the API takes bare tags.
    assert tags == ["gaming", "clips"]
