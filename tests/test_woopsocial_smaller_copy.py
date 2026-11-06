"""WoopSocial's smaller copy (publish/woopsocial.py, editor suite step 1, #203).

WoopSocial takes clips up to 100 MB in one piece. With the setting on (it is
off by default), a clip over that is sent as a copy under about 95 MB, made
with video/export_presets.py, and the copy is deleted afterwards. Off, a clip
goes exactly as it always did, and the publisher is made exactly as before.

Light tier: the copy is faked here. A real one, made by FFmpeg and checked
with ffprobe, is in tests/test_export_presets_ffmpeg.py.
"""

import threading
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from core.state import StateDB
from publish import woopsocial as ws
from publish.uploadpost import FanOutResult, PlatformOutcome
from server import woopsocial_service as service
from video import export_presets


class FakeClient:
    def __init__(self):
        self.uploaded: list[Path] = []
        self.sizes: list[int] = []

    def social_accounts(self, project_id=""):
        return [{"platform": "YOUTUBE", "id": "a1"}]

    def upload_media(self, video, project_id):
        self.uploaded.append(Path(video))
        self.sizes.append(Path(video).stat().st_size)
        return "media-1"

    def create_post(self, body):
        return {"id": "post-1", "socialAccountPosts": [{"platform": "YOUTUBE", "status": "SCHEDULED"}]}


def publish(client, clip, **kwargs):
    return ws.WoopSocialPublisher(client, "proj", **kwargs).start(clip, platforms=["youtube"], title="T", text="t")


@pytest.fixture
def big_clip(tmp_path, monkeypatch) -> Path:
    """A clip over the limit (the limit lowered to 1 kB for the test)."""
    monkeypatch.setattr(ws, "SINGLE_UPLOAD_MAX_BYTES", 1000)
    clip = tmp_path / "clip.mp4"
    clip.write_bytes(b"v" * 5000)
    return clip


def test_off_by_default_a_clip_goes_as_it_always_did(big_clip, monkeypatch):
    monkeypatch.setattr(export_presets, "smaller_copy", lambda *a, **k: pytest.fail("no copy when it is off"))
    client = FakeClient()
    assert ws.WoopSocialPublisher(client, "proj").smaller_copy is False
    publish(client, big_clip)
    assert client.uploaded == [big_clip]


def test_on_a_clip_under_the_limit_goes_as_it_is(tmp_path, monkeypatch):
    monkeypatch.setattr(export_presets, "smaller_copy", lambda *a, **k: pytest.fail("no copy for a small clip"))
    clip = tmp_path / "clip.mp4"
    clip.write_bytes(b"v" * 50)
    client = FakeClient()
    publish(client, clip, smaller_copy=True)
    assert client.uploaded == [clip]


def test_on_a_clip_over_the_limit_goes_as_a_smaller_copy_that_is_then_deleted(big_clip, monkeypatch):
    made = []

    def shrink(src, dst, max_bytes, *, workdir, run=None):
        assert Path(src) == big_clip and max_bytes == ws.SMALLER_COPY_BYTES == 95_000_000
        Path(dst).write_bytes(b"s" * 100)
        made.append(Path(dst))
        return {"bytes": 100, "over_target": False}

    monkeypatch.setattr(export_presets, "smaller_copy", shrink)
    client = FakeClient()
    got = publish(client, big_clip, smaller_copy=True)
    assert client.uploaded == made and client.sizes == [100]
    assert not made[0].exists() and not made[0].parent.exists(), "the copy and its folder are gone"
    assert big_clip.read_bytes() == b"v" * 5000, "the clip itself is untouched"
    assert [o.platform for o in got.outcomes] == ["youtube"]


def test_a_copy_that_cannot_be_made_is_said_plainly_and_nothing_is_sent(big_clip, monkeypatch):
    def shrink(*_a, **_k):
        raise export_presets.ExportError("Error while opening encoder")

    monkeypatch.setattr(export_presets, "smaller_copy", shrink)
    client = FakeClient()
    with pytest.raises(ws.WoopSocialError, match="smaller copy couldn't be made: Error while opening encoder"):
        publish(client, big_clip, smaller_copy=True)
    assert client.uploaded == []


def test_the_setting_is_off_until_saved(tmp_path):
    db = StateDB(tmp_path / "state.db")
    try:
        assert service.load_settings(db)["smaller_copy"] is False
        assert service.status_payload(db, tmp_path)["smaller_copy"] is False
        service.save_settings(db, {"smaller_copy": True})
        assert service.status_payload(db, tmp_path)["smaller_copy"] is True
    finally:
        db.conn.close()


def _a_clip(db: StateDB, tmp_path: Path) -> int:
    db.upsert_video("v1", title="s")
    f = tmp_path / "c.mp4"
    f.write_bytes(b"v")
    return db.add_clip("v1", 0, 8, 80, "hook", path=str(f))


def test_background_publishing_asks_for_the_copy_only_when_it_is_on(tmp_path, monkeypatch):
    made: list[dict] = []

    class Publisher:
        def __init__(self, client, project, **kwargs):
            made.append(kwargs)

        def start(self, path, *, platforms, title, text, scheduled_for, overrides):
            return FanOutResult(request_id="r1", outcomes=[PlatformOutcome(platform="youtube", state="queued")])

    monkeypatch.setattr("publish.woopsocial.WoopSocialPublisher", Publisher)
    monkeypatch.setattr(service, "make_client", lambda _d: object())
    monkeypatch.setattr(service, "resolve_project", lambda _db, _c: "proj")
    db = StateDB(tmp_path / "state.db")
    try:
        clip_id = _a_clip(db, tmp_path)
        service.publish_clips(db, tmp_path, clip_ids=[clip_id], platforms=["youtube"])
        assert made == [{}], "made exactly as before"
        service.save_settings(db, {"smaller_copy": True})
        service.publish_clips(db, tmp_path, clip_ids=[clip_id], platforms=["youtube"])
        assert made[-1] == {"smaller_copy": True}
    finally:
        db.conn.close()


def test_the_settings_route_and_the_publish_route(tmp_path, monkeypatch):
    pytest.importorskip("fastapi")
    pytest.importorskip("httpx")
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from server import woopsocial_api

    db_path, data_dir = tmp_path / "state.db", tmp_path / "data"
    data_dir.mkdir()
    app = FastAPI()
    woopsocial_api.install(app, config={}, db=lambda: StateDB(db_path), data_dir=data_dir)
    client = TestClient(app, base_url="http://127.0.0.1")
    made: list[dict] = []

    class Publisher:
        def __init__(self, client, project, **kwargs):
            made.append(kwargs)

        def start(self, path, *, platforms, title, text, scheduled_for, overrides):
            return FanOutResult(request_id="r1", outcomes=[PlatformOutcome(platform="youtube", state="queued")])

    monkeypatch.setattr("publish.woopsocial.WoopSocialPublisher", Publisher)
    monkeypatch.setattr(service, "has_key", lambda _d: True)
    monkeypatch.setattr(service, "make_client", lambda _d: object())
    d = StateDB(db_path)
    service.save_settings(d, {"enabled": True, "project_id": "proj1"})
    clip_id = _a_clip(d, tmp_path)
    d.conn.close()

    body = {"platforms": ["youtube"], "title": "A title"}
    assert client.post(f"/woopsocial/clips/{clip_id}/publish", json=body).status_code == 200
    assert made == [{}]
    status = client.patch("/woopsocial/settings", json={"smaller_copy": True}).json()
    assert status["smaller_copy"] is True
    assert client.post(f"/woopsocial/clips/{clip_id}/publish", json=body).status_code == 200
    assert made[-1] == {"smaller_copy": True}
    assert client.patch("/woopsocial/settings", json={"smaller_copy": False}).json()["smaller_copy"] is False


# ---- a copy that takes longer than the sweep's window -------------------------------
#
# The publish worker settles a background send still at "sending" after
# STALE_SENDING_SECONDS as interrupted (reconcile_sending): with no media id
# yet, it is marked failed and safe to send again. The copy is made inside the
# send, before the upload, and on a slow PC it can take longer than that.


class SweptClient(FakeClient):
    """FakeClient, plus the lookup the sweep makes for an uploaded clip (no
    post carries it), a post that can time out, and a hook into the upload."""

    def __init__(self):
        super().__init__()
        self.on_upload = None
        self.post_error: Exception | None = None

    def upload_media(self, video, project_id):
        if self.on_upload is not None:
            self.on_upload()
        return super().upload_media(video, project_id)

    def create_post(self, body):
        if self.post_error is not None:
            raise self.post_error
        return super().create_post(body)

    def social_account_posts(self, account_ids, *, limit=100):
        return []


def _aged(db: StateDB, clip_id: int, seconds: float) -> None:
    """A clip's rows as they look `seconds` after they were last written."""
    stamp = (datetime.now() - timedelta(seconds=seconds)).isoformat(timespec="seconds")
    db.conn.execute("UPDATE clip_publishes SET updated_at = ? WHERE clip_id = ?", (stamp, clip_id))
    db.conn.commit()


def _state(db: StateDB, clip_id: int) -> str:
    return db.conn.execute("SELECT state FROM clip_publishes WHERE clip_id = ?", (clip_id,)).fetchone()["state"]


PAST_THE_WINDOW = service.STALE_SENDING_SECONDS + 60


@pytest.fixture
def swept(tmp_path, monkeypatch):
    """A clip over the limit, ready to go out in the background; the publish
    worker's sweep; and a send the app really did stop: another clip's row,
    left at "sending" with no media from before the window."""
    monkeypatch.setattr(ws, "SINGLE_UPLOAD_MAX_BYTES", 1000)
    client = SweptClient()
    monkeypatch.setattr(service, "make_client", lambda _d: client)
    monkeypatch.setattr(service, "resolve_project", lambda _db, _c: "proj")
    db_path = tmp_path / "state.db"
    db = StateDB(db_path)
    db.upsert_video("v1", title="s")
    ids = []
    for start in (0.0, 20.0):
        f = tmp_path / f"clip{int(start)}.mp4"
        f.write_bytes(b"v" * 5000)
        ids.append(db.add_clip("v1", start, start + 8, 80, "hook", path=str(f)))
    clip_id, stopped = ids
    db.record_clip_publish(stopped, "youtube", {"provider": "woopsocial", "state": "sending", "media_id": ""})
    _aged(db, stopped, PAST_THE_WINDOW)

    def sweep() -> dict:
        """reconcile_sending as PublishWorker runs it: on its own thread, with
        its own connection and the real window."""
        got: dict = {}

        def run():
            d = StateDB(db_path)
            try:
                got.update(service.reconcile_sending(d, tmp_path))
            except Exception as e:   # reported here rather than lost on the thread
                got["error"] = repr(e)
            finally:
                d.close()

        worker = threading.Thread(target=run)
        worker.start()
        worker.join(timeout=60)
        return got

    def send(*, once=True) -> dict:
        return service.publish_clips(db, tmp_path, clip_ids=[clip_id], platforms=["youtube"], once=once)

    yield SimpleNamespace(db=db, client=client, clip_id=clip_id, stopped=stopped, sweep=sweep, send=send)
    db.close()


def test_a_copy_that_outlasts_the_window_is_not_settled_as_interrupted(swept, monkeypatch):
    """Partway through this copy, the clip's rows are older than
    STALE_SENDING_SECONDS and the publish worker's sweep runs. It settles the
    send the app really stopped and leaves this one at "sending", so the clip
    is never shown as failed and safe to send again while it is on its way.
    The send then finishes as usual."""
    service.save_settings(swept.db, {"smaller_copy": True})
    during: dict = {}

    def slow_copy(src, dst, max_bytes, *, workdir, run=None):
        _aged(swept.db, swept.clip_id, PAST_THE_WINDOW)   # the copy has taken this long so far
        during["sweep"] = swept.sweep()
        during["state"] = _state(swept.db, swept.clip_id)
        Path(dst).write_bytes(b"s" * 100)
        return {"bytes": 100, "over_target": False}

    monkeypatch.setattr(export_presets, "smaller_copy", slow_copy)
    out = swept.send()

    assert during["sweep"] == {"adopted": 0, "failed": 1}, "only the send that really stopped"
    assert during["state"] == "sending"
    assert _state(swept.db, swept.stopped) == "failed"
    assert [s["clip_id"] for s in out["started"]] == [swept.clip_id]
    assert _state(swept.db, swept.clip_id) == "queued"
    assert swept.client.sizes == [100], "the copy went, not the clip"
    assert service._held == {}, "nothing is held once the send has ended"


def test_a_send_that_ended_is_settled_as_before(swept, monkeypatch):
    """A post that timed out after its upload leaves the rows at "sending"
    for the sweep to settle (it may or may not exist). The send has ended, so
    past the window the sweep settles it like any other: no post carries its
    media, so it is failed and safe to send again."""
    service.save_settings(swept.db, {"smaller_copy": True})

    def copy(src, dst, max_bytes, *, workdir, run=None):
        Path(dst).write_bytes(b"s" * 100)
        return {"bytes": 100, "over_target": False}

    monkeypatch.setattr(export_presets, "smaller_copy", copy)
    timeout = ws.WoopSocialError("WoopSocial did not respond in time.")
    timeout.retryable = True
    swept.client.post_error = timeout

    assert swept.send()["started"] == []
    assert _state(swept.db, swept.clip_id) == "sending"
    _aged(swept.db, swept.clip_id, PAST_THE_WINDOW)
    assert swept.sweep() == {"adopted": 0, "failed": 2}
    assert _state(swept.db, swept.clip_id) == "failed"


@pytest.mark.parametrize("copy_on, once", [(False, True), (True, False)])
def test_nothing_is_held_with_the_copy_off_or_for_a_persons_publish(swept, copy_on, once):
    """Off (the default), the sweep is exactly as it was: a background send
    is held only with the copy on. A person's publish writes no "sending"
    rows, so it has nothing to hold."""
    if copy_on:
        service.save_settings(swept.db, {"smaller_copy": True})
    held: list = []
    swept.client.on_upload = lambda: held.append(dict(service._held))
    if copy_on:
        # A clip under the limit, so the upload is reached without a copy.
        Path(swept.db.get_clip(swept.clip_id)["path"]).write_bytes(b"v" * 10)

    swept.send(once=once)

    assert held == [{}]
