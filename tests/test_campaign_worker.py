"""The campaign step inside the publish worker (DESIGN §6.1, §4.3).

It runs right after the provider checks, every 30 seconds or at once after
notify_campaigns(), and is contained like them: nothing it does can end the
thread that runs every YouTube upload. At startup, sends a restart cut off
are settled before the loop starts, and an error there doesn't end run().
No network: Discord is a scripted transport.
"""

import threading
from datetime import datetime, timezone

import pytest
from campaign_fake import FakePublisher, clip, link, webhook_destination
from discord_fake import CHANNEL_ID, FakeDiscordHTTP

from campaigns import queue, statemachine
from core.state import StateDB
from server import publisher

NOW = datetime(2026, 10, 1, 12, 0, tzinfo=timezone.utc).timestamp()


def _bare(tmp_path):
    """A worker as the existing worker tests make one: no thread, no init."""
    w = publisher.PublishWorker.__new__(publisher.PublishWorker)
    w.config, w.data_dir = {}, tmp_path
    return w


@pytest.fixture
def emitted(monkeypatch):
    seen: list[dict] = []
    monkeypatch.setattr(publisher.broadcaster, "publish", seen.append)
    return seen


def _ready(db, data_dir) -> int:
    clip_id = clip(db)
    FakePublisher(db).publish(clip_id, ["tiktok"], state="published", url=link("tiktok"))
    dest = webhook_destination(db, data_dir)
    cid = db.insert_campaign("Example Campaign", method="discord_webhook", destination_id=dest)
    row = queue.attach(db, items=[{"clip_id": clip_id, "platforms": ["tiktok"]}], campaign_ids=[cid],
                       now=NOW)["created"][0]
    assert row["status"] == "ready"
    return row["id"]


def test_a_pass_sends_and_announces_through_the_broadcaster(db, tmp_path, emitted):
    sub_id = _ready(db, tmp_path)
    w = _bare(tmp_path)
    w.campaign_http = FakeDiscordHTTP((200, {"id": "600000000000000001", "channel_id": CHANNEL_ID}))
    w._advance_campaigns(db, now=NOW)
    assert db.get_submission(sub_id)["status"] == "awaiting_confirmation"
    assert [(e["type"], e["submission_id"], e["previous"], e["status"]) for e in emitted] == [
        ("campaign", sub_id, "sending", "awaiting_confirmation")]


@pytest.mark.parametrize("failure_text", [
    "https://discord.com/api/webhooks/400000000000000001/fake-webhook-token-Abc123_xyz",
    "private diagnostic that isn't a recognisable token",
])
def test_the_step_never_raises_into_the_loop(db, tmp_path, monkeypatch, capsys, failure_text):
    def broken(*args, **kw):
        raise RuntimeError(failure_text)

    monkeypatch.setattr(queue, "advance", broken)
    w = _bare(tmp_path)
    w._advance_campaigns(db, now=NOW)
    out = capsys.readouterr().out
    assert "Could not move campaign submissions on" in out
    assert "fake-webhook-token" not in out, "a webhook link in an error never reaches the log"
    assert failure_text not in out, "exception text can contain secrets a pattern doesn't recognise"


def test_it_runs_every_thirty_seconds_or_at_once_when_asked(db, tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(queue, "advance", lambda *a, **kw: calls.append(kw) or {
        "changed": 0, "sent": 0, "paused_until": 0.0, "more": False})
    w = _bare(tmp_path)
    w._wake = threading.Event()
    w._advance_campaigns(db, now=NOW)
    w._advance_campaigns(db, now=NOW)
    assert len(calls) == 1, "throttled to CAMPAIGN_CHECK_SECONDS"
    w.notify_campaigns()
    assert w._wake.is_set(), "the loop wakes up for it"
    w._advance_campaigns(db, now=NOW)
    assert len(calls) == 2
    w._advance_campaigns(db, now=NOW)
    assert len(calls) == 2


def test_sends_left_over_bring_the_next_pass_closer_and_the_global_pause_is_kept(db, tmp_path, monkeypatch):
    answers = [{"changed": 0, "sent": 5, "paused_until": NOW + 30, "more": True}]
    seen = []

    def advance(*args, **kw):
        seen.append(kw["paused_until"])
        return answers[0]

    monkeypatch.setattr(queue, "advance", advance)
    w = _bare(tmp_path)
    w._advance_campaigns(db, now=NOW)
    assert w._campaign_pause == NOW + 30
    left = w._next_campaign_check - publisher.time.monotonic()
    assert 0 < left <= 2, "back in a couple of seconds, not thirty"
    w._next_campaign_check = 0
    w._advance_campaigns(db, now=NOW + 1)
    assert seen == [0.0, NOW + 30]


def _run_once(tmp_path, monkeypatch, order):
    """Run the real run() for one turn of the loop: the first step stops it."""
    w = publisher.PublishWorker({}, tmp_path / "state.db", tmp_path)
    real_jobs = StateDB.recover_running_publish_jobs

    def jobs(self):
        order.append("publish_jobs")
        return real_jobs(self)

    monkeypatch.setattr(StateDB, "recover_running_publish_jobs", jobs)
    monkeypatch.setattr(w, "_run_due_verifications", lambda db: order.append("loop") or w.stop())
    for name in ("_refresh_provider_posts", "_refresh_missing_urls"):
        monkeypatch.setattr(w, name, lambda db: None)
    monkeypatch.setattr(w, "_check_scheduled_youtube", lambda db: order.append("youtube"))
    monkeypatch.setattr(w, "_advance_campaigns", lambda db: order.append("campaigns"))
    w.run()
    return w


def test_startup_settles_cut_off_sends_after_the_uploads_and_before_the_loop(tmp_path, monkeypatch):
    db = StateDB(tmp_path / "state.db")
    sub_id = _ready(db, tmp_path)
    statemachine.apply(db, sub_id, "send_start", now=NOW)
    db.close()
    order = []
    real = StateDB.recover_sending_submissions

    def sending(self, reason, **kw):
        order.append("campaign_sends")
        return real(self, reason, **kw)

    monkeypatch.setattr(StateDB, "recover_sending_submissions", sending)
    _run_once(tmp_path, monkeypatch, order)
    assert order == ["publish_jobs", "campaign_sends", "loop", "youtube", "campaigns"], \
        "the campaign step comes right after the YouTube check that learns links"
    db = StateDB(tmp_path / "state.db")
    row = db.get_submission(sub_id)
    assert (row["status"], row["sent_by"], row["status_reason"]) == (
        "awaiting_confirmation", "", "Clips Kitty closed while sending. Check the channel.")
    db.close()


def test_a_startup_recovery_that_raises_doesnt_end_the_worker(tmp_path, monkeypatch, capsys):
    import sqlite3

    def locked(self, reason, **kw):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(StateDB, "recover_sending_submissions", locked)
    order = []
    _run_once(tmp_path, monkeypatch, order)
    assert order == ["publish_jobs", "loop", "youtube", "campaigns"], "the loop still ran"
    assert "Could not settle campaign sends cut off by a restart" in capsys.readouterr().out
