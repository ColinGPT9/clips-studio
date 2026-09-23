"""WoopSocial HTTP routes."""

from pathlib import Path

import pytest

pytest.importorskip("httpx")

from fastapi import FastAPI
from fastapi.testclient import TestClient

from core.state import StateDB
from server import woopsocial_api
from server import woopsocial_service as service


@pytest.fixture
def client(tmp_path: Path):
    db_path = tmp_path / "state.db"
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    app = FastAPI()
    woopsocial_api.install(app, config={}, db=lambda: StateDB(db_path), data_dir=data_dir)
    c = TestClient(app, base_url="http://127.0.0.1")
    c.db_path = db_path
    c.data_dir = data_dir
    return c


def _enable(client):
    d = StateDB(client.db_path)
    service.save_settings(d, {"enabled": True, "project_id": "proj1"})
    d.close()


def _a_clip(client, tmp_path: Path) -> int:
    d = StateDB(client.db_path)
    d.upsert_video("v1", title="s")
    f = tmp_path / "c.mp4"
    f.write_bytes(b"v")
    clip_id = d.add_clip("v1", 0, 8, 80, "hook", path=str(f))
    d.close()
    return clip_id


def test_disabled_looks_absent(client):
    assert client.get("/woopsocial/status").json()["enabled"] is False
    assert client.get("/woopsocial/connections").status_code == 404
    assert client.post("/woopsocial/connect", json={"platform": "youtube"}).status_code == 404


def test_the_key_is_never_returned(client, monkeypatch):
    monkeypatch.setattr(service, "load_key", lambda _d: "woop-secret-key-123456")
    body = client.get("/woopsocial/status").text
    assert "woop-secret-key-123456" not in body
    assert "woop-secret" not in body


def test_a_key_that_fails_validation_is_discarded(client, monkeypatch):
    from publish.errors import AuthRequired

    class Rejecting:
        def validate_key(self):
            raise AuthRequired("WoopSocial did not accept that API key.")

    monkeypatch.setattr(service, "make_client", lambda _p: Rejecting())
    assert client.put("/woopsocial/key", json={"api_key": "bad"}).status_code == 400
    assert not service.has_key(client.data_dir)


def test_a_good_key_reports_only_its_tail(client, monkeypatch):
    monkeypatch.setattr(
        service,
        "make_client",
        lambda _p: type("C", (), {"validate_key": lambda self: {"projects": [{"id": "p"}]}})(),
    )
    got = client.put("/woopsocial/key", json={"api_key": "abcdefghij4321"})
    assert got.status_code == 200
    assert got.json()["key_tail"] == "4321"
    assert got.json()["projects"] == 1
    assert "abcdefghij4321" not in got.text


def test_a_project_is_created_rather_than_asked_for(client, monkeypatch):
    """A creator should never have to invent a project name."""
    d = StateDB(client.db_path)
    service.save_settings(d, {"enabled": True})
    d.close()
    made = []

    class FakeClient:
        def projects(self):
            return []

        def create_project(self, name):
            made.append(name)
            return {"id": "new-proj"}

        def connected_platforms(self, project_id):
            return ["youtube"]

    monkeypatch.setattr(service, "has_key", lambda _p: True)
    monkeypatch.setattr(service, "make_client", lambda _p: FakeClient())

    got = client.get("/woopsocial/connections")
    assert got.status_code == 200
    assert got.json()["connected"] == ["youtube"]
    assert made == [service.DEFAULT_PROJECT_NAME]

    # Remembered, so it is not created again on the next call.
    d = StateDB(client.db_path)
    assert service.load_settings(d)["project_id"] == "new-proj"
    d.close()


def test_connect_names_one_platform(client, monkeypatch):
    _enable(client)

    class FakeClient:
        def projects(self):
            return [{"id": "proj1"}]

        def connect_url(self, project_id, platform):
            return f"https://api.woopsocial.com/oauth/{platform}"

    monkeypatch.setattr(service, "has_key", lambda _p: True)
    monkeypatch.setattr(service, "make_client", lambda _p: FakeClient())

    assert client.post("/woopsocial/connect", json={}).status_code == 400
    got = client.post("/woopsocial/connect", json={"platform": "tiktok"})
    assert got.status_code == 200
    assert got.json()["url"].endswith("/tiktok")


def test_publishing_records_a_row_per_platform(client, monkeypatch, tmp_path):
    _enable(client)
    clip_id = _a_clip(client, tmp_path)
    monkeypatch.setattr(service, "has_key", lambda _p: True)
    monkeypatch.setattr(
        service, "make_client", lambda _p: type("C", (), {"projects": lambda self: [{"id": "proj1"}]})()
    )

    from publish import uploadpost as up
    from publish import woopsocial as ws

    monkeypatch.setattr(
        ws.WoopSocialPublisher,
        "start",
        lambda self, v, **k: up.FanOutResult(
            request_id="post-1",
            outcomes=[up.PlatformOutcome(platform=p) for p in k["platforms"]],
        ),
    )

    got = client.post(
        f"/woopsocial/clips/{clip_id}/publish",
        json={"platforms": ["youtube", "tiktok"], "title": "T"},
    )
    assert got.status_code == 200
    assert got.json()["request_id"] == "post-1"
    assert {p["platform"] for p in got.json()["platforms"]} == {"tiktok", "youtube"}
    # Rows record which provider delivered them, so the two cannot be confused.
    assert {p["provider"] for p in got.json()["platforms"]} == {"woopsocial"}


def test_hashtags_are_appended_to_the_caption(client, monkeypatch, tmp_path):
    """WoopSocial has one text field, so tags go into it rather than a
    separate parameter."""
    _enable(client)
    clip_id = _a_clip(client, tmp_path)
    monkeypatch.setattr(service, "has_key", lambda _p: True)
    monkeypatch.setattr(
        service, "make_client", lambda _p: type("C", (), {"projects": lambda self: [{"id": "proj1"}]})()
    )

    from publish import uploadpost as up
    from publish import woopsocial as ws

    seen = {}

    def fake_start(self, video, **kwargs):
        seen.update(kwargs)
        return up.FanOutResult(request_id="p", outcomes=[])

    monkeypatch.setattr(ws.WoopSocialPublisher, "start", fake_start)

    client.post(
        f"/woopsocial/clips/{clip_id}/publish",
        json={"platforms": ["youtube"], "title": "T", "description": "Body",
              "tags": ["gaming", "#clips"]},
    )
    assert "#gaming" in seen["text"] and "#clips" in seen["text"]
    assert "Body" in seen["text"]


def test_publishing_needs_a_title_and_a_platform(client, monkeypatch, tmp_path):
    _enable(client)
    clip_id = _a_clip(client, tmp_path)
    monkeypatch.setattr(service, "has_key", lambda _p: True)
    monkeypatch.setattr(service, "make_client", lambda _p: object())

    assert client.post(
        f"/woopsocial/clips/{clip_id}/publish", json={"platforms": ["youtube"], "title": " "}
    ).status_code == 400
    assert client.post(
        f"/woopsocial/clips/{clip_id}/publish", json={"platforms": [], "title": "T"}
    ).status_code == 400


def test_the_shipped_referral_url_reaches_the_app(client):
    """What every install sees, so the call to action points somewhere real."""
    got = client.get("/woopsocial/status").json()["affiliate_url"]
    assert got == service.AFFILIATE_URL
    assert got.startswith("https://"), "a bare domain would be refused by the allow-list"


def test_no_shipped_url_means_no_affiliate_claim(client, monkeypatch):
    """The rule the disclosure depends on: with nothing configured the app
    must claim no commission rather than link somewhere that earns none."""
    monkeypatch.setattr(service, "AFFILIATE_URL", "")
    assert client.get("/woopsocial/status").json()["affiliate_url"] == ""


def test_an_install_can_override_the_shipped_url(client):
    client.patch("/woopsocial/settings", json={"affiliate_url": "https://example.test/?via=me"})
    assert (
        client.get("/woopsocial/status").json()["affiliate_url"]
        == "https://example.test/?via=me"
    )


# ---- batch over days -------------------------------------------------------


def _clips(client, tmp_path: Path, n: int) -> list[int]:
    d = StateDB(client.db_path)
    d.upsert_video("vb", title="s")
    ids = []
    for i in range(n):
        f = tmp_path / f"c{i}.mp4"
        f.write_bytes(b"v")
        ids.append(d.add_clip("vb", i * 5.0, i * 5.0 + 4, 70, f"hook {i}", path=str(f)))
    d.close()
    return ids


def _ready(client, monkeypatch):
    _enable(client)
    monkeypatch.setattr(service, "has_key", lambda _p: True)
    monkeypatch.setattr(
        service,
        "make_client",
        lambda _p: type("C", (), {"projects": lambda self: [{"id": "proj1"}]})(),
    )


def test_a_batch_schedules_one_clip_per_day(client, monkeypatch, tmp_path):
    """The point of this: a video's clips become a posting calendar."""
    from datetime import datetime

    _ready(client, monkeypatch)
    ids = _clips(client, tmp_path, 4)

    from publish import uploadpost as up
    from publish import woopsocial as ws

    times = []

    def fake_start(self, video, **kwargs):
        times.append(kwargs.get("scheduled_for"))
        return up.FanOutResult(request_id="p", outcomes=[])

    monkeypatch.setattr(ws.WoopSocialPublisher, "start", fake_start)

    got = client.post(
        "/woopsocial/batch",
        json={"clip_ids": ids, "platforms": ["youtube"], "every_hours": 24},
    )
    assert got.status_code == 200
    assert len(got.json()["started"]) == 4
    assert all(times), "every clip in a spaced run needs a time"

    stamps = [datetime.fromisoformat(t) for t in times]
    assert stamps == sorted(stamps)
    gap = (stamps[1] - stamps[0]).total_seconds() / 3600
    assert 23.9 < gap < 24.1


def test_the_first_slot_is_never_in_the_past(client, monkeypatch, tmp_path):
    """Their scheduler refuses a past time, and 'starting now' drifts behind
    by the time the request lands."""
    from datetime import datetime
    from datetime import timezone as tz

    _ready(client, monkeypatch)
    ids = _clips(client, tmp_path, 1)

    from publish import uploadpost as up
    from publish import woopsocial as ws

    seen = {}

    def fake_start(self, video, **kwargs):
        seen["when"] = kwargs.get("scheduled_for")
        return up.FanOutResult(request_id="p", outcomes=[])

    monkeypatch.setattr(ws.WoopSocialPublisher, "start", fake_start)
    client.post(
        "/woopsocial/batch",
        json={"clip_ids": ids, "platforms": ["youtube"], "every_hours": 24},
    )
    assert datetime.fromisoformat(seen["when"]) > datetime.now(tz.utc)


def test_no_spacing_means_no_schedule_at_all(client, monkeypatch, tmp_path):
    _ready(client, monkeypatch)
    ids = _clips(client, tmp_path, 2)

    from publish import uploadpost as up
    from publish import woopsocial as ws

    times = []
    monkeypatch.setattr(
        ws.WoopSocialPublisher,
        "start",
        lambda self, v, **k: (times.append(k.get("scheduled_for")),
                              up.FanOutResult(request_id="p", outcomes=[]))[1],
    )
    client.post("/woopsocial/batch", json={"clip_ids": ids, "platforms": ["youtube"]})
    assert times == ["", ""]


def test_a_bad_clip_is_skipped_and_the_rest_continue(client, monkeypatch, tmp_path):
    _ready(client, monkeypatch)
    ids = _clips(client, tmp_path, 2)

    from publish import uploadpost as up
    from publish import woopsocial as ws

    monkeypatch.setattr(
        ws.WoopSocialPublisher,
        "start",
        lambda self, v, **k: up.FanOutResult(request_id="p", outcomes=[]),
    )
    got = client.post(
        "/woopsocial/batch", json={"clip_ids": [*ids, 987654], "platforms": ["youtube"]}
    )
    assert len(got.json()["started"]) == 2
    assert got.json()["skipped"] == [{"clip_id": 987654, "reason": "no such clip"}]


def test_a_batch_needs_clips_and_platforms(client, monkeypatch):
    _ready(client, monkeypatch)
    assert client.post(
        "/woopsocial/batch", json={"clip_ids": [], "platforms": ["youtube"]}
    ).status_code == 400
    assert client.post(
        "/woopsocial/batch", json={"clip_ids": [1], "platforms": []}
    ).status_code == 400


def test_a_daily_plan_shows_the_slots_that_will_be_used(client, monkeypatch, tmp_path):
    """The plan a person agrees to has to be the schedule that is sent, so
    it comes from the same slotting the publish uses."""
    from datetime import datetime

    _enable(client)
    monkeypatch.setattr(service, "has_key", lambda _p: True)

    class FakeClient:
        def projects(self):
            return [{"id": "proj1"}]

        def connected_platforms(self, project_id):
            return ["youtube"]

    monkeypatch.setattr(service, "make_client", lambda _p: FakeClient())
    ids = _clips(client, tmp_path, 4)
    got = client.post(
        "/woopsocial/batch/plan",
        json={"clip_ids": ids, "platforms": ["youtube"], "per_day": 2, "gap_hours": 1},
    )
    assert got.status_code == 200
    plan = got.json()
    assert plan["per_day"] == 2
    times = [datetime.fromisoformat(i["publish_at"]) for i in plan["items"]]
    assert (times[1] - times[0]).total_seconds() == 3600
    assert (times[2] - times[0]).total_seconds() == 24 * 3600
    assert (times[3] - times[2]).total_seconds() == 3600
