"""Desktop routes and trusted account/VOD checks for shared stream workflows."""

import copy
import threading
import time
from datetime import datetime, timezone

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

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
        self.released = []

    def start(self, *args, consumer="desktop"):
        self.started.append(args)
        return {"status": "connecting"}

    def release(self, account_id, consumer):
        self.released.append((account_id, consumer))

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


def eventually(check, timeout=5):
    until = time.monotonic() + timeout
    while time.monotonic() < until:
        if check():
            return True
        threading.Event().wait(0.01)
    return check()


def test_storage_opens_on_first_use_and_damaged_files_are_set_aside(tmp_path, capsys):
    value = WorkflowRuntime(tmp_path, Service(), Events())
    try:
        # Reads never create the files: a person who never opts in gets none.
        assert value.streams(ACCOUNT) == {"monitoring": False, "streams": []}
        assert value.audience_for_binding(binding())["status"] == "unavailable"
        with pytest.raises(WorkflowError):
            value.source_to_vod(binding(), 20)
        assert not list(tmp_path.iterdir())
        for name in ("stream-workflows.sqlite3", "stream-digests.sqlite3"):
            (tmp_path / name).write_bytes(b"damaged by a sync tool" * 100)
        value.start_monitor(ACCOUNT)
        assert value.digests.list(ACCOUNT) == []
        for name in ("stream-workflows.sqlite3", "stream-digests.sqlite3"):
            assert (tmp_path / (name + ".corrupt")).read_bytes().startswith(b"damaged")
        assert capsys.readouterr().out.count("set aside") == 2
        assert value.streams(ACCOUNT)["monitoring"] is True
    finally:
        value.close()


def test_stop_releases_the_chat_subscription(runtime):
    value, _, events, _ = runtime
    value.start_monitor(ACCOUNT)
    value.stop_monitor(ACCOUNT)
    assert events.released == [(ACCOUNT, "stream-workflow")]


def test_a_fresh_subscription_looks_for_a_stream_that_started_in_the_gap(runtime):
    value, service, _, clock = runtime
    answers = []

    def provider(account_id, operation, **params):
        if operation != "stream_context":
            return {}
        answers.append(operation)
        return {"broadcaster": {"id": "123"},
                "stream": {"id": "456", "started_at": iso(START)} if len(answers) > 1 else None}

    service.perform = provider
    value.start_monitor(ACCOUNT)  # offline when collection starts
    clock[0] += 60
    chat = event("channel.chat.message", "gap-chat", clock[0], {"chatter_user_id": "777", "text": "!clip"})
    assert not value.record_event(ACCOUNT, chat)
    # The socket dropped and resubscribed while the stream went live: Twitch
    # sends no stream.online for it, only the host's own session notice.
    assert not value.record_event(ACCOUNT, {"type": "session.subscribed", "broadcaster_id": "123"})
    assert eventually(lambda: value.streams(ACCOUNT)["streams"])
    assert value.record_event(ACCOUNT, dict(chat, id="after-gap-chat"))


def test_a_stream_that_ended_in_the_gap_stops_being_counted(runtime):
    value, service, _, clock = runtime
    monitor_stream(runtime)
    service.perform = lambda account_id, operation, **params: {"broadcaster": {"id": "123"}, "stream": None}
    value.record_event(ACCOUNT, {"type": "session.subscribed", "broadcaster_id": "123"})
    clock[0] += 60
    late = event("channel.chat.message", "late-chat", clock[0], {"chatter_user_id": "778", "text": "!clip"})
    assert eventually(lambda: not value.record_event(ACCOUNT, dict(late, id=f"late-{time.monotonic()}")))


def test_bootstrap_retries_while_twitch_cannot_answer(runtime, monkeypatch):
    from connected_accounts.models import AccountError

    monkeypatch.setattr("stream_workflows.runtime._BOOTSTRAP_DELAYS", (0, 0, 0))
    value, service, _, clock = runtime
    answers = []

    def provider(account_id, operation, **params):
        answers.append(operation)
        if len(answers) < 3:
            raise AccountError("provider_unavailable", "Twitch is temporarily unavailable.", status=503)
        return {"broadcaster": {"id": "123"}, "stream": {"id": "456", "started_at": iso(START)}}

    service.perform = provider
    value.start_monitor(ACCOUNT)
    assert eventually(lambda: value.streams(ACCOUNT)["streams"])
    assert len(answers) == 3


def test_whole_archive_runs_map_source_time_without_a_calibration(runtime):
    value, _, _, _ = runtime
    whole = binding(whole_vod=True, vod_duration=3600.0, duration=3600.4)
    assert value.source_to_vod(whole, 20) == 20.0
    assert value.source_to_vod(whole, 3600) == 3600.0
    with pytest.raises(WorkflowError) as error:
        value.source_to_vod(whole, 3600.2)
    assert error.value.code == "invalid_timestamp"
    for unverified in (binding(), binding(whole_vod="yes", vod_duration=3600.0), binding(whole_vod=True),
                       binding(whole_vod=True, vod_duration=3600.0, broadcaster_id="999")):
        with pytest.raises(WorkflowError) as error:
            value.source_to_vod(unverified, 20)
        assert error.value.status == 412
    # Chat time still needs a stream-to-VOD alignment: say so plainly.
    audience = value.audience_for_binding(whole)
    assert audience["status"] == "unavailable" and "can't do that by itself yet" in audience["message"]


def test_http_host_adapter_uses_saved_destination_and_rechecks_account(tmp_path, db, monkeypatch):
    from campaigns import service as discord_service
    from core.state import StateDB

    destination = db.insert_destination(label="Catch-up")
    monkeypatch.setattr(discord_service, "load_webhook", lambda *args: {
        "webhook_id": "12345678901234567", "token": "private-test-hook",
    })

    class Http:
        def __init__(self):
            self.calls = []

        def request(self, method, url, **kwargs):
            self.calls.append((method, url, kwargs))

            class Response:
                status_code = 200

                def json(self):
                    return {"id": "private-receipt"}

            return Response()

    service, events, http = Service(), Events(), Http()
    app = FastAPI()
    runtime = install(
        app, data_dir=tmp_path, service=service, event_manager=events,
        session_secret=HEADERS["X-Clips-Kitty-Session"],
        db=lambda: StateDB(tmp_path / "state.db"), http=http,
    )
    try:
        with TestClient(app) as client:
            body = {"request_id": "selected", "destination_id": destination,
                    "candidates": [{"clip_id": "ActualClip"}]}
            prepared = client.post(f"/stream-workflows/{ACCOUNT}/digests", headers=HEADERS, json=body)
            assert prepared.status_code == 200
            assert prepared.json()["destination_id"] == destination
            assert prepared.json()["status"] == "pending_approval" and not http.calls
            approve = f"/stream-workflows/{ACCOUNT}/digests/{prepared.json()['id']}/approve"
            assert client.post(approve).status_code == 403
            service.rows[0]["status"] = "reconnect_required"
            assert client.post(approve, headers=HEADERS).status_code == 401
            assert not http.calls
            service.rows[0]["status"] = "connected"
            result = client.post(approve, headers=HEADERS)
            assert result.json()["status"] == "sent"
            assert client.post(approve, headers=HEADERS).json() == result.json()
            assert len(http.calls) == 1
            assert "private-test-hook" not in result.text and "private-receipt" not in result.text
            assert client.post(
                f"/stream-workflows/{ACCOUNT}/digests", headers=HEADERS,
                json={**body, "request_id": "invalid", "destination_id": True},
            ).status_code == 422
    finally:
        runtime.close()
