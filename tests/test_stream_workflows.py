"""Viewer nominations contain only bounded relative evidence and own VODs."""

from datetime import datetime, timezone

import pytest

from stream_workflows.models import WorkflowError
from stream_workflows.nominations import Nominations

ACCOUNT = "twitch:123"
START = 1_790_000_000.0


def iso(value):
    return datetime.fromtimestamp(value, timezone.utc).isoformat().replace("+00:00", "Z")


def event(kind, identifier, at, data=None, broadcaster="123"):
    return {
        "id": identifier,
        "sequence": 1,
        "type": kind,
        "timestamp": iso(at),
        "broadcaster_id": broadcaster,
        "data": data or {},
    }


def online(store, clock, **data):
    return store.record_event(
        ACCOUNT, event("stream.online", "online-1", clock[0], {"id": "456", "started_at": iso(START), **data})
    )


def chat(store, clock, identifier, viewer="777", text="!clip", at=None):
    return store.record_event(
        ACCOUNT,
        event(
            "channel.chat.message",
            identifier,
            clock[0] if at is None else at,
            {
                "chatter_user_id": viewer,
                "chatter_user_login": "private-viewer-login",
                "chatter_user_name": "Private Viewer Name",
                "text": text,
            },
        ),
    )


def bind(store, **changes):
    return store.bind_vod(
        **{
            "account_id": ACCOUNT,
            "broadcaster_id": "123",
            "vod_id": "789",
            "stream_id": "456",
            "stream_start": iso(START),
            "offset_seconds": 0,
            "verified": True,
            "duration": 3600,
            "source_offset_seconds": 0,
            "source_id": "local-video-1",
            **changes,
        }
    )


def binding(**changes):
    return {
        "account_id": ACCOUNT,
        "broadcaster_id": "123",
        "video_id": "789",
        "duration": 3600,
        "source_offset_seconds": 0,
        "source_id": "local-video-1",
        **changes,
    }


def test_exact_commands_cooldown_dedupe_and_no_viewer_persistence(tmp_path):
    clock = [START]
    store = Nominations(tmp_path, clock=lambda: clock[0])
    assert online(store, clock)
    clock[0] += 10
    assert chat(store, clock, "first-command")
    assert not chat(store, clock, "duplicate-viewer", text="!chaos")
    assert not chat(store, clock, "first-command", viewer="778")
    for index, text in enumerate(("!CLIP", "!clip now", " !clip", "hello !clip", "!funny\n")):
        assert not chat(store, clock, f"not-exact-{index}", viewer="779", text=text)
    assert chat(store, clock, "other-viewer", viewer="780", text="!funny")
    clock[0] += 60
    assert chat(store, clock, "after-cooldown", text="!chaos")
    bind(store)
    result = store.audience_for_binding(binding())
    assert result["status"] == "available"
    assert sum(item["count"] for item in result["nominations"]) == 3
    assert result["nominations"][0]["commands"] == {"clip": 1, "funny": 1}
    store.close()
    raw = (tmp_path / "stream-workflows.sqlite3").read_bytes()
    for private in (
        b"private-viewer-login",
        b"Private Viewer Name",
        b"first-command",
        b"!clip",
        b"!chaos",
        b"!funny",
        b"777",
    ):
        assert private not in raw


def test_persistent_event_dedupe_and_aggregates_survive_restart(tmp_path):
    clock = [START]
    store = Nominations(tmp_path, clock=lambda: clock[0])
    online(store, clock)
    clock[0] += 20
    assert chat(store, clock, "same-event")
    bind(store)
    store.close()
    restarted = Nominations(tmp_path, clock=lambda: clock[0])
    assert not chat(restarted, clock, "same-event")
    assert sum(x["count"] for x in restarted.audience_for_binding(binding())["nominations"]) == 1
    # Active association is cleared: a stream may end while host is stopped.
    assert not chat(restarted, clock, "new-event")
    assert restarted.record_event(
        ACCOUNT, event("stream.online", "fresh-presence", clock[0], {"id": "456", "started_at": iso(START)})
    )
    # Viewer cooldown hashes intentionally do not persist across sessions.
    assert chat(restarted, clock, "after-verified-presence")
    restarted.close()


def test_no_timeline_or_unverified_binding_yields_no_evidence(tmp_path):
    clock = [START]
    store = Nominations(tmp_path, clock=lambda: clock[0])
    assert online(store, clock, started_at="")
    clock[0] += 10
    assert not chat(store, clock, "unknown-timeline")
    with pytest.raises(WorkflowError, match="recorded stream start"):
        bind(store)
    assert store.audience_for_binding(binding())["nominations"] == []
    assert store.audience_for_binding(None)["reason"] == "unverified_binding"
    store.close()


def test_explicit_alignment_never_uses_archive_created_at(tmp_path):
    clock = [START]
    store = Nominations(tmp_path, clock=lambda: clock[0])
    online(store, clock)
    clock[0] += 120
    assert chat(store, clock, "nomination")
    with pytest.raises(WorkflowError):
        bind(store, verified=False)
    with pytest.raises(WorkflowError):
        bind(store, stream_start=iso(START + 20))
    bind(store, offset_seconds=-30)
    evidence = store.audience_for_binding(binding())["nominations"]
    assert evidence[0]["offset_seconds"] == 90
    assert evidence[0]["first_seen_seconds"] == 90
    assert store.audience_for_binding(binding(account_id="twitch:999"))["nominations"] == []
    assert store.audience_for_binding(binding(broadcaster_id="999"))["nominations"] == []
    assert store.audience_for_binding(binding(video_id="999"))["nominations"] == []
    assert store.audience_for_binding(binding(duration=60))["nominations"] == []
    store.close()


def test_retention_clears_old_pending_data_and_bounded_rows(tmp_path):
    clock = [START]
    store = Nominations(
        tmp_path,
        clock=lambda: clock[0],
        retention_days=1,
        max_events=10,
        max_streams=2,
        max_buckets=10,
        cooldown_seconds=1,
        bucket_seconds=1,
    )
    online(store, clock)
    for index in range(30):
        clock[0] += 2
        chat(store, clock, f"message-{index}", viewer=str(10000 + index))
    assert store._db.execute("SELECT COUNT(*) FROM nominations").fetchone()[0] <= 10
    assert store._db.execute("SELECT COUNT(*) FROM events").fetchone()[0] <= 10
    clock[0] += 86401
    assert store.streams(ACCOUNT) == []
    assert store._db.execute("SELECT COUNT(*) FROM nominations").fetchone()[0] == 0
    store.close()


def test_a_burst_of_commands_never_prunes_per_event(tmp_path, monkeypatch):
    # record_event runs on the EventSub receive thread; pruning sorts whole
    # tables, so a hype moment must not pay for it on every !clip.
    clock = [START]
    store = Nominations(tmp_path, clock=lambda: clock[0], cooldown_seconds=1)
    online(store, clock)
    passes = []
    prune = store._prune
    monkeypatch.setattr(store, "_prune", lambda: passes.append(clock[0]) or prune())
    for index in range(200):
        clock[0] += 0.1
        assert chat(store, clock, f"burst-{index}", viewer=str(20000 + index))
    assert passes == []
    clock[0] += 61
    assert chat(store, clock, "a-minute-later", viewer="30000")
    assert len(passes) == 1
    indexes = {row[1] for table in ("events", "nominations", "streams")
               for row in store._db.execute(f"PRAGMA index_list({table})")}
    assert {"events_received", "nominations_last_seen", "streams_touched"} <= indexes
    store.close()


def test_offline_finishes_pending_stream_and_ignores_later_chat(tmp_path):
    clock = [START]
    store = Nominations(tmp_path, clock=lambda: clock[0])
    online(store, clock)
    clock[0] += 10
    assert chat(store, clock, "before-offline")
    assert store.record_event(ACCOUNT, event("stream.offline", "offline", clock[0]))
    assert not chat(store, clock, "after-offline", viewer="888")
    assert store.streams(ACCOUNT)[0]["ended_at"] == iso(clock[0])
    bind(store)
    assert store.audience_for_binding(binding())["nominations"][0]["count"] == 1
    store.close()


def test_delayed_stream_lifecycle_does_not_replace_or_close_newer_stream(tmp_path):
    clock = [START]
    store = Nominations(tmp_path, clock=lambda: clock[0])
    online(store, clock)
    clock[0] += 100
    assert store.record_event(
        ACCOUNT, event("stream.online", "new-stream", clock[0], {"id": "999", "started_at": iso(clock[0])})
    )
    assert not store.record_event(
        ACCOUNT, event("stream.online", "late-old-online", START, {"id": "456", "started_at": iso(START)})
    )
    assert not store.record_event(ACCOUNT, event("stream.offline", "late-old-offline", START + 50))
    clock[0] += 10
    assert chat(store, clock, "current-chat")
    assert store._db.execute("SELECT stream_id FROM nominations").fetchone()[0] == "999"
    store.close()


def test_aggregate_events_outside_source_bounds_are_not_returned(tmp_path):
    clock = [START]
    store = Nominations(tmp_path, clock=lambda: clock[0])
    online(store, clock)
    clock[0] += 20
    assert chat(store, clock, "only-outside-short-source")
    bind(store)
    assert store.audience_for_binding(binding(duration=15))["nominations"] == []
    with pytest.raises(WorkflowError, match="begin within"):
        bind(store, source_offset_seconds=3600)
    store.close()
