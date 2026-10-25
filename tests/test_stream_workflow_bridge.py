"""Real EventSub -> durable workflow -> run-scoped SDK bridge, all offline."""

import copy
import io
import json
import queue
import sys
import threading
import time
import urllib.error
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi import FastAPI
from fastapi.testclient import TestClient

from connected_accounts.eventsub import EventSubManager
from plugins import accounts, store
from server import connected_accounts_api, stream_workflows_api

SDK = Path(__file__).resolve().parent.parent / "sdk" / "python"
if str(SDK) not in sys.path:
    sys.path.insert(0, str(SDK))

from clipskitty_sdk import AccountOperationError, ConnectedAccounts, _loopback  # noqa: E402

ACCOUNT = "twitch:123"
START = 1_790_000_000.0
SESSION = {"X-Clips-Kitty-Session": "desktop-session-0123456789"}
SOURCE = "sha256:" + "a" * 64


def iso(value):
    return datetime.fromtimestamp(value, timezone.utc).isoformat().replace("+00:00", "Z")


def packet(kind, at, payload=None, identifier="notification"):
    return json.dumps(
        {
            "metadata": {"message_type": kind, "message_id": identifier, "message_timestamp": iso(at)},
            "payload": payload or {},
        }
    )


class Socket:
    def __init__(self):
        self.messages = queue.Queue()
        self.messages.put(
            packet(
                "session_welcome",
                START,
                {"session": {"id": "host-only-session", "keepalive_timeout_seconds": 10}},
            )
        )

    def recv(self, timeout):
        value = self.messages.get(timeout=timeout)
        if isinstance(value, Exception):
            raise value
        return value

    def close(self):
        self.messages.put(ConnectionError("closed"))


class Service:
    """Provider boundary deliberately includes credential-shaped test fields."""

    def __init__(self):
        self.rows = [
            {
                "id": ACCOUNT,
                "provider": "twitch",
                "user_id": "123",
                "status": "connected",
                "scopes": ["user:read:chat", "channel:manage:clips"],
            }
        ]
        self.calls = []

    def accounts(self, *args):
        return copy.deepcopy(self.rows)

    def perform(self, account_id, operation, **params):
        self.calls.append((account_id, operation, params))
        if operation == "subscribe_events":
            return {
                "subscriptions": [
                    {"id": "host-subscription", "type": kind, "status": "enabled"}
                    for kind in params["event_types"]
                ]
            }
        if operation == "stream_context":
            return {"broadcaster": {"id": "123"}, "stream": None}
        if operation == "vod_link":
            seconds = int(params["seconds"])
            return {
                "video_id": "789",
                "broadcaster_id": "123",
                "stream_id": "456",
                "type": "archive",
                "duration_seconds": 3600,
                "url": f"https://www.twitch.tv/videos/789?t={seconds // 3600}h{seconds % 3600 // 60}m{seconds % 60}s",
                "access_token": "private-provider-access",
            }
        if operation == "create_vod_clip":
            return {"id": "ActualClip", "status": "processing", "access_token": "private-provider-access"}
        if operation == "get_clip":
            return {
                "id": "ActualClip",
                "video_id": "789",
                "broadcaster_id": "123",
                "url": "https://clips.twitch.tv/ActualClip",
                "refresh_token": "private-provider-refresh",
            }
        raise AssertionError(operation)

    def disconnect(self, account_id):
        self.rows = []
        return {"disconnected": True, "revoked": True}

    def close(self):
        pass


def eventually(function):
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        if function():
            return
        threading.Event().wait(0.005)
    assert function()


@pytest.fixture
def bridge(tmp_path, plugin_source, install_plugin, monkeypatch):
    manifest = plugin_source.manifest(permissions=["twitch.context.read", "twitch.clips.create"])
    folder = plugin_source.folder(manifest=manifest)
    data_dir = tmp_path / "data"
    install_plugin(data_dir, folder)
    plugin = store.get(data_dir, manifest["id"])
    service, socket = Service(), Socket()
    events = EventSubManager(service, connector=lambda url: socket)
    app = FastAPI()
    account_runtime = connected_accounts_api.install(
        app,
        data_dir=data_dir,
        db=lambda: None,
        session_secret=SESSION["X-Clips-Kitty-Session"],
        service=service,
        event_manager=events,
    )
    workflow = stream_workflows_api.install(
        app,
        data_dir=data_dir,
        service=service,
        event_manager=events,
        session_secret=SESSION["X-Clips-Kitty-Session"],
    )
    clock = [START]
    workflow.nominations.clock = lambda: clock[0]
    events.add_sink(workflow.record_event)
    account_runtime.workflow = workflow
    binding = {
        "account_id": ACCOUNT,
        "broadcaster_id": "123",
        "video_id": "789",
        "duration": 1800,
        "source_id": SOURCE,
        "path": "/private/local/path.mp4",
    }
    gateway = accounts.install(
        app,
        data_dir=data_dir,
        service=service,
        binding_for_video=lambda video: copy.deepcopy(binding),
        event_manager=events,
        audience_for_binding=workflow.audience_for_binding,
        timeline_mapper=workflow.source_to_vod,
    )
    account_runtime.gateway = gateway
    client = TestClient(app)
    replies = []

    def sdk_http(request, **kwargs):
        response = client.post(accounts.ROUTE, content=request.data, headers=dict(request.header_items()))
        replies.append(response.text)
        if response.status_code >= 400:
            raise urllib.error.HTTPError(
                request.full_url, response.status_code, "denied", {}, io.BytesIO(response.content)
            )
        return io.BytesIO(response.content)

    monkeypatch.setattr(_loopback, "urlopen", sdk_http)
    state = SimpleNamespace(
        service=service,
        socket=socket,
        events=events,
        workflow=workflow,
        clock=clock,
        binding=binding,
        gateway=gateway,
        plugin=plugin,
        client=client,
        data_dir=data_dir,
        replies=replies,
    )
    yield state
    account_runtime.close()
    workflow.close()


def notify(bridge, kind, identifier, **extra):
    data = {
        "broadcaster_user_id": "123",
        "broadcaster_user_login": "owner",
        "broadcaster_user_name": "Owner",
        **extra,
    }
    payload = {"subscription": {"type": kind, "condition": {"broadcaster_user_id": "123"}}, "event": data}
    bridge.socket.messages.put(packet("notification", bridge.clock[0], payload, identifier))


def collect_and_map(bridge):
    result = bridge.client.post(f"/stream-workflows/{ACCOUNT}/monitor", headers=SESSION)
    assert result.status_code == 200, result.text
    eventually(lambda: bridge.events.events(ACCOUNT)["status"] == "connected")
    notify(bridge, "stream.online", "online", id="456", started_at=iso(START))
    bridge.clock[0] += 120
    notify(
        bridge,
        "channel.chat.message",
        "nomination",
        chatter_user_id="987654321",
        chatter_user_login="private-viewer-login",
        chatter_user_name="Private Viewer Name",
        message={"text": "!chaos"},
        access_token="private-event-token",
    )
    notify(
        bridge,
        "channel.chat.message",
        "irrelevant-chat",
        chatter_user_id="987654322",
        message={"text": "PRIVATE RAW CHAT MUST NOT BE SAVED"},
    )
    eventually(
        lambda: (
            bool(bridge.workflow.streams(ACCOUNT)["streams"])
            and bridge.workflow.streams(ACCOUNT)["streams"][0]["nomination_count"] == 1
        )
    )
    result = bridge.client.put(
        f"/stream-workflows/{ACCOUNT}/vod-mapping",
        headers=SESSION,
        json={
            "broadcaster_id": "123",
            "vod_id": "789",
            "stream_id": "456",
            "stream_start": iso(START),
            "offset_seconds": 30,
            "source_offset_seconds": 60,
            "source_id": SOURCE,
            "verified": True,
        },
    )
    assert result.status_code == 200, result.text


def sdk_for_run(bridge, monkeypatch):
    return bridge.gateway.run(bridge.plugin, SimpleNamespace(duration=1800), 60)


def enable_sdk(env, monkeypatch):
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    return ConnectedAccounts().twitch


def test_real_eventsub_to_persisted_mapping_to_sdk_is_relative_and_private(bridge, monkeypatch):
    collect_and_map(bridge)
    with sdk_for_run(bridge, monkeypatch) as env:
        sdk = enable_sdk(env, monkeypatch)
        evidence = sdk.audience_signals()
        assert evidence["nominations"] == [
            {
                "offset_seconds": 90,
                "vod_offset_seconds": 150,
                "count": 1,
                "commands": {"chaos": 1},
                "first_seen_seconds": 90,
                "last_seen_seconds": 90,
            }
        ]
        assert sdk.source_info() == {"video_id": "789", "duration": 1800, "source_id": SOURCE}
        assert sdk.timestamp_link(20)["url"] == "https://www.twitch.tv/videos/789?t=0h1m20s"
        assert sdk.create_vod_clip(10, 20, "Verified moment")["status"] == "processing"
        creation = [call for call in bridge.service.calls if call[1] == "create_vod_clip"][-1]
        assert creation[2] == {
            "broadcaster_id": "123",
            "video_id": "789",
            "start_seconds": 70,
            "end_seconds": 80,
            "title": "Verified moment",
        }
        assert sdk.get_clip("ActualClip")["url"] == "https://clips.twitch.tv/ActualClip"
    public = "".join(bridge.replies)
    persisted = (bridge.data_dir / "stream-workflows.sqlite3").read_bytes()
    for private in (
        "987654321",
        "private-viewer-login",
        "Private Viewer Name",
        "PRIVATE RAW CHAT",
        "private-provider-access",
        "private-provider-refresh",
        "private-event-token",
        "/private/local/path.mp4",
    ):
        assert private not in public
        assert private.encode() not in persisted


def test_bad_local_source_identity_blocks_sdk_timeline_and_audience(bridge, monkeypatch):
    collect_and_map(bridge)
    bridge.binding["source_id"] = "sha256:" + "b" * 64
    with sdk_for_run(bridge, monkeypatch) as env:
        sdk = enable_sdk(env, monkeypatch)
        assert sdk.audience_signals()["nominations"] == []
        with pytest.raises(AccountOperationError) as error:
            sdk.timestamp_link(20)
        assert error.value.status == 412 and "verified" in str(error.value)
        with pytest.raises(AccountOperationError):
            sdk.create_vod_clip(10, 20, "Mismatched source")
    assert not any(call[1] == "create_vod_clip" for call in bridge.service.calls)


def test_monitor_stop_ignores_new_chat_and_account_delete_revokes_sdk(bridge, monkeypatch):
    collect_and_map(bridge)
    assert bridge.events.events(ACCOUNT)["events"]
    stopped = bridge.client.delete(f"/stream-workflows/{ACCOUNT}/monitor", headers=SESSION)
    assert stopped.status_code == 200
    # Stopping collection ends the chat subscription and drops buffered chat:
    # monitoring was the listener's only consumer.
    assert bridge.events.events(ACCOUNT) == {"status": "stopped", "events": [], "cursor": 0}
    bridge.clock[0] += 120
    notify(
        bridge,
        "channel.chat.message",
        "disabled-nomination",
        chatter_user_id="987654323",
        message={"text": "!funny"},
    )
    assert bridge.events.events(ACCOUNT)["events"] == []
    assert bridge.workflow.streams(ACCOUNT)["streams"][0]["nomination_count"] == 1
    with sdk_for_run(bridge, monkeypatch) as env:
        sdk = enable_sdk(env, monkeypatch)
        assert sdk.audience_signals()["nominations"][0]["count"] == 1
        deleted = bridge.client.delete(f"/connected-accounts/{ACCOUNT}", headers=SESSION)
        assert deleted.status_code == 200, deleted.text
        with pytest.raises(AccountOperationError) as error:
            sdk.audience_signals()
        assert error.value.status == 403
        assert not bridge.workflow.record_event(ACCOUNT, {})
