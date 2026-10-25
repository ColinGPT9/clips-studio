"""The ordinary publish buttons deliver live links, without a campaign UI."""

import importlib
import json
import time
from types import SimpleNamespace

import pytest

fastapi = pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from campaign_fake import FakePublisher, link, webhook_destination  # noqa: E402
from discord_fake import CHANNEL_ID, FakeDiscordHTTP  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from core.state import StateDB  # noqa: E402
from publish.base import PublishResult  # noqa: E402
from publish.uploadpost import FanOutResult, PlatformOutcome  # noqa: E402
from server import publisher, youtube_api, youtube_service  # noqa: E402


@pytest.fixture(params=["woopsocial", "uploadpost"])
def fanout(request, monkeypatch, tmp_path):
    provider = request.param
    service = importlib.import_module(f"server.{provider}_service")
    routes = importlib.import_module(f"server.{provider}_api")
    db_path = tmp_path / "state.db"
    database = StateDB(db_path)
    service.save_settings(database, {"enabled": True, "project_id": "example-project", "profile": "example"})
    destination = webhook_destination(database, tmp_path)
    database.upsert_video("example-video")
    path = tmp_path / "clip.mp4"
    path.write_bytes(b"fake media")
    clip_id = database.add_clip("example-video", 0, 10, 90, "Saved title", path=str(path))
    database.close()
    sent = []

    def start(self, video, **kwargs):
        sent.append(kwargs)
        return FanOutResult(request_id="example-request", outcomes=[
            PlatformOutcome(platform=p, state="queued") for p in kwargs["platforms"]])

    monkeypatch.setattr(service, "has_key", lambda _: True)
    monkeypatch.setattr(service, "make_client", lambda _: object())
    target = "publish.woopsocial.WoopSocialPublisher.start" if provider == "woopsocial" else \
        "publish.uploadpost.UploadPostPublisher.start"
    monkeypatch.setattr(target, start)
    app = fastapi.FastAPI()
    routes.install(app, config={}, db=lambda: StateDB(db_path), data_dir=tmp_path)
    return SimpleNamespace(client=TestClient(app), db_path=db_path, data_dir=tmp_path, sent=sent,
                           provider=provider, destination=destination, clip_id=clip_id)


def _request(env, batch, **extra):
    path = f"/{env.provider}/batch" if batch else f"/{env.provider}/clips/{env.clip_id}/publish"
    body = {"clip_ids": [env.clip_id]} if batch else {"title": "Published title"}
    return env.client.post(path, json={**body, "platforms": ["youtube", "tiktok"], **extra})


def _worker(env, http):
    worker = publisher.PublishWorker({}, env.db_path, env.data_dir)
    worker.campaign_http = http
    return worker


@pytest.mark.parametrize("batch", [False, True])
def test_publishing_then_live_link_sends_once_even_after_restart(fanout, batch):
    env = fanout
    response = _request(env, batch, discord_destination_id=env.destination)
    assert response.status_code == 200, response.text
    db = StateDB(env.db_path)
    try:
        assert len(db.list_submissions()) == 2
        http = FakeDiscordHTTP((200, {"id": "600000000000000001", "channel_id": CHANNEL_ID}))
        worker = _worker(env, http)
        worker._advance_campaigns(db, now=time.time())
        assert http.calls == []
        writer = FakePublisher(db, "woopsocial" if env.provider == "woopsocial" else "upload_post")
        writer.refresh(env.clip_id, "youtube", "published", url=link("youtube"))
        writer.refresh(env.clip_id, "tiktok", "failed", error="Platform refused the post")
        # A new worker has no in-memory knowledge of the publish request.
        restarted = _worker(env, http)
        restarted._advance_campaigns(db, now=time.time() + 120)
        restarted._campaigns_now = True
        restarted._advance_campaigns(db, now=time.time() + 240)
        title = "Saved title" if batch else "Published title"
        assert [call["json"]["content"] for call in http.calls] == [f"{title}\n{link('youtube')}"]
    finally:
        db.close()


@pytest.mark.parametrize("batch", [False, True])
def test_a_publish_without_discord_or_with_an_unknown_destination(fanout, batch):
    env = fanout
    response = _request(env, batch, discord_destination_id=env.destination + 1)
    assert response.status_code == 400
    assert env.sent == []
    response = _request(env, batch)
    assert response.status_code == 200
    db = StateDB(env.db_path)
    try:
        assert db.list_submissions() == []
        assert db.list_campaigns() == []
    finally:
        db.close()


@pytest.mark.parametrize("opt_in", [False, True])
def test_direct_youtube_keeps_delivery_intent_in_the_queued_job(tmp_path, monkeypatch, opt_in):
    db_path = tmp_path / "state.db"
    db = StateDB(db_path)
    try:
        youtube_service.save_settings(db, {"enabled": True})
        destination = webhook_destination(db, tmp_path)
        db.upsert_video("example-video")
        path = tmp_path / "clip.mp4"
        path.write_bytes(b"fake media")
        clip_id = db.add_clip("example-video", 0, 10, 90, "Saved title", path=str(path))
        worker = publisher.PublishWorker({}, db_path, tmp_path)
        app = fastapi.FastAPI()
        youtube_api.install(app, config={}, db=lambda: StateDB(db_path), data_dir=tmp_path,
                            worker=SimpleNamespace(notify=lambda: None), publish_worker=worker)
        client = TestClient(app)
        body = {"title": "Published title", "privacy": "public"}
        if opt_in:
            invalid = client.post(f"/clips/{clip_id}/publish", json={**body, "discord_destination_id": destination + 1})
            assert invalid.status_code == 400
            assert db.conn.execute("SELECT COUNT(*) FROM publish_jobs").fetchone()[0] == 0
            body["discord_destination_id"] = destination
        response = client.post(f"/clips/{clip_id}/publish", json=body)
        assert response.status_code == 200, response.text
        job = db.claim_next_publish_job()
        assert json.loads(job["request"]).get("discord_destination_id") == (destination if opt_in else None)

        class FakeYouTube:
            def publish(self, request, **kwargs):
                return PublishResult(video_id="abcdefghi01", url=link("youtube"),
                                     requested_privacy="public", actual_privacy="public")

        monkeypatch.setattr(youtube_service, "make_publisher", lambda *a, **kw: FakeYouTube())
        monkeypatch.setattr(publisher.broadcaster, "publish", lambda event: None)
        # Run the queued job with a fresh worker, as after an app restart.
        restarted = publisher.PublishWorker({}, db_path, tmp_path)
        restarted._publish(db, job)
        http = FakeDiscordHTTP((200, {"id": "600000000000000001", "channel_id": CHANNEL_ID}))
        restarted.campaign_http = http
        restarted._advance_campaigns(db, now=time.time())
        restarted._campaigns_now = True
        restarted._advance_campaigns(db, now=time.time() + 120)
        assert len(http.calls) == int(opt_in)
        if opt_in:
            assert http.calls[0]["json"]["content"] == f"Published title\n{link('youtube')}"
    finally:
        db.close()
