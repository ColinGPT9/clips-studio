"""Campaigns end to end, in one process: the real routes (Discord and
campaigns), the real publish worker's campaign step and the real HTTP
transport, against a fake Discord on 127.0.0.1 and a fake publisher that
writes rows the way the providers do.

1. Editor: a post scheduled ahead is attached, followed until it is
   published and has its link, and sent through the webhook exactly once.
2. Assisted: a post already live is the user's to submit in Discord; they
   record that they submitted it, then that the campaign accepted it.
"""

import time
from datetime import datetime, timedelta, timezone

import pytest

fastapi = pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from campaign_fake import FakePublisher, clip, link  # noqa: E402
from discord_fake import WEBHOOK_ID, WEBHOOK_TOKEN, FakeDiscordServer  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from campaigns import discord_http  # noqa: E402
from core.state import StateDB  # noqa: E402
from server import campaigns_api, discord_api, publisher  # noqa: E402

SECRET = "test-session-secret-0123456789"
HEADERS = {"X-Clips-Kitty-Session": SECRET}


@pytest.fixture
def fake(monkeypatch):
    with FakeDiscordServer() as server:
        monkeypatch.setenv(discord_http.API_ENV, server.api)
        monkeypatch.setenv(discord_http.AUTHORIZE_ENV, server.authorize)
        yield server


@pytest.fixture
def app(tmp_path, fake, monkeypatch):
    events: list[dict] = []
    monkeypatch.setattr(publisher.broadcaster, "publish", events.append)
    db_path = tmp_path / "state.db"
    StateDB(db_path).close()
    worker = publisher.PublishWorker({}, db_path, tmp_path)  # never started: its step is run by hand here
    api = fastapi.FastAPI()
    discord_api.install(api, db=lambda: StateDB(db_path), data_dir=tmp_path, session_secret=SECRET)
    campaigns_api.install(api, db=lambda: StateDB(db_path), data_dir=tmp_path, publish_worker=worker,
                          session_secret=SECRET)
    client = TestClient(api, base_url="http://127.0.0.1")
    client.worker, client.events, client.db_path = worker, events, db_path
    return client


def _pass(app, at: float) -> None:
    """One turn of the worker's campaign step, as its loop would run it."""
    db = StateDB(app.db_path)
    try:
        app.worker._campaigns_now = True
        app.worker._advance_campaigns(db, now=at)
    finally:
        db.close()


def _utc(moment) -> str:
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ")


def test_an_editor_post_scheduled_ahead_is_sent_through_the_webhook_once(app, fake):
    added = app.post("/discord/destinations", headers=HEADERS, json={
        "webhook_url": f"https://discord.com/api/webhooks/{WEBHOOK_ID}/{WEBHOOK_TOKEN}", "label": "Example hook"})
    assert added.status_code == 200, added.text
    dest = added.json()["destination"]["id"]
    campaign = app.post("/campaigns", headers=HEADERS, json={
        "name": "Example Campaign", "method": "discord_webhook", "destination_id": dest,
        "message_template": "{title}: {url}", "approval_required": False}).json()["campaign"]

    db = StateDB(app.db_path)
    clip_id = clip(db)
    publisher_ = FakePublisher(db)
    start = datetime.now(timezone.utc)
    publisher_.publish(clip_id, ["tiktok"], scheduled_for=_utc(start + timedelta(hours=2)))
    db.close()

    attached = app.post("/campaigns/submissions", headers=HEADERS, json={
        "items": [{"clip_id": clip_id, "platforms": ["tiktok"]}], "campaign_ids": [campaign["id"]],
        "provider": "woopsocial", "mode": "after_publish"}).json()
    (row,) = attached["created"]
    assert (row["status"], row["due_at"]) == ("waiting_publication", _utc(start + timedelta(hours=2)))
    assert app.worker._campaigns_now, "the attach asks the worker to look at once"

    now = time.time()
    _pass(app, now)
    db = StateDB(app.db_path)
    publisher_ = FakePublisher(db)
    publisher_.refresh(clip_id, "tiktok", "published")
    db.close()
    _pass(app, now + 2 * 3600 + 600)
    assert app.get(f"/campaigns/submissions/{row['id']}").json()["status"] == "waiting_url"

    db = StateDB(app.db_path)
    FakePublisher(db).refresh(clip_id, "tiktok", "published", url=link("tiktok"))
    db.close()
    _pass(app, now + 2 * 3600 + 700)
    sent = app.get(f"/campaigns/submissions/{row['id']}").json()
    assert (sent["status"], sent["status_label"], sent["sent_by"]) == ("sent", "Message sent", "webhook")
    assert sent["message_url"].startswith("https://discord.com/channels/300000000000000001/")

    (message,) = fake.messages
    assert message["query"] == {"wait": "true"}
    assert message["body"] == {"content": f"A great moment: {link('tiktok')}", "allowed_mentions": {"parse": []}}
    for later in (60, 3600, 86400):
        _pass(app, now + 2 * 3600 + 700 + later)
    assert len(fake.messages) == 1, "sent once"
    assert [e["status"] for e in app.events if e.get("submission_id") == row["id"]] == [
        "waiting_url", "ready", "sent"]
    assert WEBHOOK_TOKEN not in app.get("/campaigns/submissions").text


def test_an_assisted_post_is_submitted_by_hand_then_confirmed(app, fake):
    campaign = app.post("/campaigns", headers=HEADERS, json={
        "name": "Example Campaign", "channel_link": "https://discord.com/channels/300000000000000001/"
                                                    "200000000000000002",
        "command_name": "/submit", "command_args": [{"name": "link", "value": ""}], "url_argument": "link",
    }).json()["campaign"]
    db = StateDB(app.db_path)
    clip_id = clip(db)
    FakePublisher(db).publish(clip_id, ["tiktok"], state="published", url=link("tiktok"))
    db.close()

    (row,) = app.post("/campaigns/submissions", headers=HEADERS, json={
        "items": [{"clip_id": clip_id, "platforms": ["tiktok"]}], "campaign_ids": [campaign["id"]],
        "mode": "now"}).json()["created"]
    assert (row["status"], row["status_reason"], row["to_submit"]) == ("manual", "Submit it in Discord", True)
    assert row["command"]["args"] == [{"name": "link", "value": link("tiktok")}]
    assert row["discord_url"] == "https://discord.com/channels/300000000000000001/200000000000000002"

    path = f"/campaigns/submissions/{row['id']}"
    assert app.post(f"{path}/opened", headers=HEADERS).status_code == 200
    submitted = app.post(f"{path}/record", headers=HEADERS, json={"outcome": "submitted"}).json()
    assert (submitted["status"], submitted["outcome_to_record"]) == ("awaiting_confirmation", True)
    _pass(app, time.time())
    confirmed = app.post(f"{path}/record", headers=HEADERS, json={"outcome": "confirmed"}).json()
    assert confirmed["status"] == "confirmed"
    assert fake.messages == [], "Clips Kitty posted nothing for an assisted campaign"
