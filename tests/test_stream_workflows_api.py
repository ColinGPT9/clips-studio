"""Desktop routes and trusted account/VOD checks for shared stream workflows."""

import copy
from datetime import datetime, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from server.stream_workflows_api import install
from stream_workflows.models import WorkflowError
from stream_workflows.runtime import WorkflowRuntime

ACCOUNT = "twitch:123"
START = 1_790_000_000.0
HEADERS = {"X-Clips-Kitty-Session": "desktop-session-0123456789"}


def iso(value):
    return datetime.fromtimestamp(value, timezone.utc).isoformat().replace("+00:00", "Z")


class Service:
    def __init__(self):
        self.rows = [
            {
                "id": ACCOUNT,
                "provider": "twitch",
                "user_id": "123",
                "status": "connected",
                "scopes": ["user:read:chat"],
            }
        ]
        self.source = {
            "video_id": "789",
            "broadcaster_id": "123",
            "stream_id": "456",
            "type": "archive",
            "duration": "1h0m0s",
        }
        self.clip = {"id": "ActualClip", "broadcaster_id": "123", "title": "Verified clip"}
        self.calls = []

    def accounts(self):
        return copy.deepcopy(self.rows)

    def perform(self, account_id, operation, **params):
        self.calls.append((account_id, operation, params))
        return copy.deepcopy(self.clip if operation == "get_clip" else self.source)


class Events:
    def __init__(self):
        self.started = []
        self.stopped = []

    def start(self, *args):
        self.started.append(args)
        return {"status": "connecting"}

    def stop(self, account_id):
        self.stopped.append(account_id)


def event(kind, identifier, at, data=None, broadcaster="123"):
    return {
        "id": identifier,
        "type": kind,
        "timestamp": iso(at),
        "broadcaster_id": broadcaster,
        "data": data or {},
    }


@pytest.fixture
def runtime(tmp_path):
    clock = [START]
    service, events = Service(), Events()
    value = WorkflowRuntime(tmp_path, service, events, clock=lambda: clock[0])
    yield value, service, events, clock
    value.close()


def monitor_stream(runtime):
    value, _, _, clock = runtime
    value.start_monitor(ACCOUNT)
    assert value.record_event(
        ACCOUNT, event("stream.online", "online", clock[0], {"id": "456", "started_at": iso(START)})
    )
    clock[0] += 120
    assert value.record_event(
        ACCOUNT, event("channel.chat.message", "chat", clock[0], {"chatter_user_id": "777", "text": "!clip"})
    )


def map_vod(value, **changes):
    return value.bind_vod(
        ACCOUNT,
        **{
            "broadcaster_id": "123",
            "vod_id": "789",
            "stream_id": "456",
            "stream_start": iso(START),
            "offset_seconds": 30,
            "verified": True,
            "source_offset_seconds": 60,
            "source_id": "local-video-1",
            **changes,
        },
    )


def binding(**changes):
    return {
        "account_id": ACCOUNT,
        "broadcaster_id": "123",
        "video_id": "789",
        "duration": 1800,
        "source_id": "local-video-1",
        **changes,
    }


def test_monitor_requires_scope_and_own_channel_and_stops_sink(runtime):
    value, service, events, clock = runtime
    incoming = event("stream.online", "online", clock[0], {"id": "456", "started_at": iso(START)})
    assert not value.record_event(ACCOUNT, incoming)
    service.rows[0]["scopes"] = []
    with pytest.raises(WorkflowError, match="permission to read chat"):
        value.start_monitor(ACCOUNT)
    assert events.started == []
    service.rows[0]["scopes"] = ["user:read:chat"]
    value.start_monitor(ACCOUNT)
    assert events.started[0] == (ACCOUNT, "123", ["stream.online", "stream.offline", "channel.chat.message"])
    assert not value.record_event(ACCOUNT, {**incoming, "broadcaster_id": "999"})
    assert value.record_event(ACCOUNT, incoming)
    value.stop_monitor(ACCOUNT)
    assert not value.record_event(ACCOUNT, {**incoming, "id": "after-stop"})


def test_provider_stream_owner_and_recorded_start_verify_mapping(runtime):
    value, service, _, _ = runtime
    monitor_stream(runtime)
    for field, bad in (
        ("stream_id", "999"),
        ("broadcaster_id", "999"),
        ("video_id", "999"),
        ("type", "upload"),
    ):
        original = service.source[field]
        service.source[field] = bad
        with pytest.raises(WorkflowError):
            map_vod(value)
        service.source[field] = original
    with pytest.raises(WorkflowError):
        map_vod(value, broadcaster_id="999")
    with pytest.raises(WorkflowError):
        map_vod(value, stream_start=iso(START + 30))
    result = map_vod(value)
    assert result["source_offset_seconds"] == 60


def test_local_source_alignment_is_explicit_and_not_implicit_zero(runtime):
    value, _, _, _ = runtime
    monitor_stream(runtime)
    map_vod(value, source_offset_seconds=None)
    assert value.audience_for_binding(binding())["reason"] == "source_timeline_unknown"
    with pytest.raises(WorkflowError, match="local source timeline"):
        value.source_to_vod(binding(), 20)
    map_vod(value)
    evidence = value.audience_for_binding(binding())["nominations"][0]
    assert evidence["offset_seconds"] == 90  # stream120 + alignment30 - source60
    assert evidence["vod_offset_seconds"] == 150
    assert evidence["first_seen_seconds"] == 90
    assert value.source_to_vod(binding(), 20) == 80
    assert value.source_to_vod(binding(), 1800) == 1860
    assert value.audience_for_binding(binding(source_id="other-trimmed-file"))["nominations"] == []
    with pytest.raises(WorkflowError):
        value.source_to_vod(binding(source_id="other-trimmed-file"), 20)
    with pytest.raises(WorkflowError):
        value.source_to_vod(binding(broadcaster_id="999"), 20)
    for bad in (True, -1, float("nan"), 1800.1):
        with pytest.raises(WorkflowError):
            value.source_to_vod(binding(), bad)


def test_account_revocation_immediately_removes_evidence(runtime):
    value, service, _, _ = runtime
    monitor_stream(runtime)
    map_vod(value)
    assert value.audience_for_binding(binding())["nominations"]
    service.rows[0]["status"] = "reconnect_required"
    assert value.audience_for_binding(binding())["reason"] == "account_unavailable"
    assert not value.record_event(ACCOUNT, {})


def test_verified_digest_sources_and_no_transport_remains_unsent(runtime):
    value, service, _, _ = runtime
    monitor_stream(runtime)
    map_vod(value)
    prepared = value.digests.prepare(
        ACCOUNT,
        request_id="catchup-1",
        top_k=3,
        candidates=[
            {"title": "A factual moment", "vod_id": "789", "offset_seconds": 150, "score": 9},
            {"clip_id": "ActualClip", "score": 8},
        ],
    )
    assert prepared["status"] == "pending_approval"
    assert prepared["items"][0]["url"] == "https://www.twitch.tv/videos/789?t=0h2m30s"
    assert prepared["items"][1]["url"] == "https://clips.twitch.tv/ActualClip"
    approved = value.digests.approve(ACCOUNT, prepared["id"])
    assert approved["status"] == "unsent"
    assert value.digests.approve(ACCOUNT, prepared["id"])["status"] == "unsent"
    service.clip["broadcaster_id"] = "999"
    with pytest.raises(WorkflowError):
        value.digests.prepare(ACCOUNT, request_id="foreign-clip", candidates=[{"clip_id": "ActualClip"}])


def test_http_session_protects_every_action_and_rejects_arbitrary_urls(tmp_path):
    service, events = Service(), Events()
    app = FastAPI()
    runtime = install(
        app,
        data_dir=tmp_path,
        service=service,
        event_manager=events,
        session_secret=HEADERS["X-Clips-Kitty-Session"],
    )
    client = TestClient(app)
    paths = [
        ("POST", "monitor", None),
        ("DELETE", "monitor", None),
        ("GET", "streams", None),
        ("PUT", "vod-mapping", {}),
        ("GET", "digests", None),
        ("POST", "digests", {"request_id": "x", "candidates": [{"clip_id": "ActualClip"}]}),
        ("POST", "digests/unknown/approve", None),
        ("GET", "digests/unknown/export", None),
    ]
    try:
        for method, path, body in paths:
            assert client.request(method, f"/stream-workflows/{ACCOUNT}/{path}", json=body).status_code == 403
        assert events.started == []
        assert service.calls == []
        response = client.post(
            f"/stream-workflows/{ACCOUNT}/digests",
            headers=HEADERS,
            json={
                "request_id": "unsafe",
                "candidates": [{"url": "https://attacker.invalid/private-token", "clip_id": "ActualClip"}],
            },
        )
        assert response.status_code == 400
        assert "private-token" not in response.text
        response = client.post(
            f"/stream-workflows/{ACCOUNT}/digests",
            headers=HEADERS,
            json={"request_id": "safe", "candidates": [{"clip_id": "ActualClip"}], "top_k": 3},
        )
        assert response.status_code == 200, response.text
        prepared = response.json()
        approved = client.post(
            f"/stream-workflows/{ACCOUNT}/digests/{prepared['id']}/approve", headers=HEADERS
        )
        assert approved.json()["status"] == "unsent"
        exported = client.get(f"/stream-workflows/{ACCOUNT}/digests/{prepared['id']}/export", headers=HEADERS)
        assert exported.json()["schema_version"] == 1
        assert (
            client.get(f"/stream-workflows/{ACCOUNT}/digests", headers=HEADERS).json()["digests"][0]["id"]
            == prepared["id"]
        )
    finally:
        runtime.close()


def test_mid_live_monitor_bootstraps_only_actual_own_stream_context(runtime):
    value, service, _, clock = runtime
    original = service.perform

    def provider(account_id, operation, **params):
        if operation == "stream_context":
            return {"broadcaster": {"id": "123"}, "stream": {"id": "456", "started_at": iso(START)}}
        return original(account_id, operation, **params)

    service.perform = provider
    value.start_monitor(ACCOUNT)
    clock[0] += 20
    assert value.record_event(
        ACCOUNT,
        event("channel.chat.message", "mid-live-chat", clock[0], {"chatter_user_id": "777", "text": "!clip"}),
    )
    value.stop_monitor(ACCOUNT)
    service.perform = lambda *args, **kwargs: {
        "broadcaster": {"id": "999"},
        "stream": {"id": "456", "started_at": iso(START)},
    }
    value.start_monitor(ACCOUNT)
    assert not value.record_event(
        ACCOUNT,
        event(
            "channel.chat.message",
            "unverified-current-channel",
            clock[0],
            {"chatter_user_id": "888", "text": "!clip"},
        ),
    )


def test_stop_reenable_requires_fresh_verified_current_stream(runtime):
    value, _, _, clock = runtime
    monitor_stream(runtime)
    value.stop_monitor(ACCOUNT)
    value.start_monitor(ACCOUNT)  # Fake provider supplies no actual live stream.
    assert not value.record_event(
        ACCOUNT,
        event(
            "channel.chat.message", "stale-stream-chat", clock[0], {"chatter_user_id": "888", "text": "!clip"}
        ),
    )
    value.close()
    value.close()  # Host shutdown is safely idempotent.


def test_offline_during_live_lookup_prevents_stale_bootstrap(runtime):
    value, service, _, clock = runtime

    def provider(*args, **kwargs):
        value.record_event(ACCOUNT, event("stream.offline", "offline-during-lookup", clock[0]))
        return {"broadcaster": {"id": "123"}, "stream": {"id": "456", "started_at": iso(START)}}

    service.perform = provider
    value.start_monitor(ACCOUNT)
    clock[0] += 20
    assert not value.record_event(
        ACCOUNT,
        event("channel.chat.message", "after-offline", clock[0], {"chatter_user_id": "777", "text": "!clip"}),
    )
    assert value.streams(ACCOUNT)["streams"] == []


def test_disconnect_and_reconnect_requires_new_monitor_opt_in(runtime):
    value, service, _, clock = runtime
    monitor_stream(runtime)
    original = service.rows
    service.rows = []
    value.account_disconnected(ACCOUNT)  # Works after roster deletion.
    service.rows = original
    assert not value.record_event(
        ACCOUNT,
        event("stream.online", "reconnected-online", clock[0], {"id": "999", "started_at": iso(clock[0])}),
    )
    assert not value.streams(ACCOUNT)["monitoring"]
    value.start_monitor(ACCOUNT)
    assert value.record_event(
        ACCOUNT,
        event("stream.online", "fresh-opt-in-online", clock[0], {"id": "999", "started_at": iso(clock[0])}),
    )
