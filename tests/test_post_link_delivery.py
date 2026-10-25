"""Recovery of ordinary publish-to-Discord requests; no real network."""

import time

import pytest
from campaign_fake import FakePublisher, clip, link, webhook_destination
from discord_fake import CHANNEL_ID, FakeDiscordHTTP

from campaigns import post_links
from server import publisher


@pytest.mark.parametrize("provider", ["woopsocial", "upload_post"])
def test_a_new_publish_never_sends_the_old_link_and_recovers_after_a_crash(db, tmp_path, provider):
    clip_id = clip(db)
    dest = webhook_destination(db, tmp_path)
    writer = FakePublisher(db, provider)
    writer.publish(clip_id, ["youtube"], state="published", url=link("youtube", 1))
    post_links.prepare(db, destination_id=dest, clip_id=clip_id, platforms=["youtube"],
                       provider=provider, title="The new publish")
    http = FakeDiscordHTTP((200, {"id": "600000000000000001", "channel_id": CHANNEL_ID}))
    worker = publisher.PublishWorker({}, tmp_path / "state.db", tmp_path)
    worker.campaign_http = http
    worker._advance_campaigns(db, now=time.time())
    assert http.calls == []
    assert db.list_submissions() == []
    writer.refresh(clip_id, "youtube", "published", url=link("youtube", 1))
    post_links.recover(db)
    assert db.list_submissions() == [], "an older post's refresh is not a new publish"
    writer.publish(clip_id, ["youtube"], state="queued")
    # Crash after recording the accepted post: only the worker recovers it.
    restarted = publisher.PublishWorker({}, tmp_path / "state.db", tmp_path)
    restarted.campaign_http = http
    restarted._advance_campaigns(db, now=time.time())
    assert len(db.list_submissions()) == 1
    assert http.calls == []
    writer.refresh(clip_id, "youtube", "published", url=link("youtube", 2))
    restarted._campaigns_now = True
    restarted._advance_campaigns(db, now=time.time() + 120)
    restarted._campaigns_now = True
    restarted._advance_campaigns(db, now=time.time() + 240)
    assert [c["json"]["content"] for c in http.calls] == [f"The new publish\n{link('youtube', 2)}"]


def test_a_failed_new_publish_never_delivers_an_older_link(db, tmp_path):
    clip_id = clip(db)
    dest = webhook_destination(db, tmp_path)
    writer = FakePublisher(db)
    writer.publish(clip_id, ["youtube"], state="published", url=link("youtube"))
    post_links.prepare(db, destination_id=dest, clip_id=clip_id, platforms=["youtube"], provider="woopsocial")
    writer.publish(clip_id, ["youtube"], state="failed", error="Refused")
    post_links.recover(db)
    assert db.list_submissions() == []
    assert db.conn.execute("SELECT key FROM app_state WHERE key GLOB ?",
                           (post_links.PENDING_PREFIX + "*",)).fetchall() == []


def test_repeated_completed_requests_never_send_twice(db, tmp_path):
    clip_id = clip(db)
    dest = webhook_destination(db, tmp_path)
    http = FakeDiscordHTTP((200, {"id": "600000000000000001", "channel_id": CHANNEL_ID}))
    for _ in range(2):
        post_links.prepare(db, destination_id=dest, clip_id=clip_id, platforms=["youtube"], provider="woopsocial")
        FakePublisher(db).publish(clip_id, ["youtube"], state="published", url=link("youtube"))
        post_links.recover(db, completed_clip=clip_id, provider="woopsocial")
        worker = publisher.PublishWorker({}, tmp_path / "state.db", tmp_path)
        worker.campaign_http = http
        worker._advance_campaigns(db, now=time.time())
    assert len(http.calls) == 1
    assert len(db.list_submissions()) == 1
