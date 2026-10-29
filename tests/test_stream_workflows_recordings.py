"""Own-stream archive discovery remains separate from human timing verification."""

import copy
import threading
import time

import pytest

from stream_workflows.models import WorkflowError
from stream_workflows.nominations import iso
from stream_workflows.runtime import WorkflowRuntime

ACCOUNT = "twitch:123"
START = 1_790_000_000.0


class Service:
    def __init__(self):
        self.rows = [{"id": ACCOUNT, "provider": "twitch", "user_id": "123",
                      "status": "connected", "scopes": ["user:read:chat"]}]
        self.videos = []
        self.lookups = 0

    def accounts(self):
        return copy.deepcopy(self.rows)

    def perform(self, account_id, operation, **params):
        if operation == "stream_context":
            return {"broadcaster": {"id": "123"}, "stream": None}
        assert operation == "videos"
        assert account_id == ACCOUNT and params["broadcaster_id"] == "123"
        self.lookups += 1
        return {"videos": copy.deepcopy(self.videos)}


class Events:
    def start(self, *args, **kwargs):
        return {"status": "connecting"}

    def release(self, *args):
        pass


def archive(**changes):
    return {"id": "789", "user_id": "123", "stream_id": "456", "type": "archive",
            "duration_seconds": 1200, **changes}


def finish_stream(value, clock):
    value.start_monitor(ACCOUNT)
    assert value.record_event(ACCOUNT, {
        "id": "online", "type": "stream.online", "timestamp": iso(clock[0]),
        "broadcaster_id": "123", "data": {"id": "456", "started_at": iso(START), "type": "live"},
    })
    clock[0] += 1200
    assert value.record_event(ACCOUNT, {
        "id": "offline", "type": "stream.offline", "timestamp": iso(clock[0]),
        "broadcaster_id": "123", "data": {},
    })


@pytest.fixture
def recording(tmp_path, monkeypatch):
    monkeypatch.setattr(WorkflowRuntime, "_recordings_later", lambda self: None, raising=False)
    service, clock = Service(), [START]
    value = WorkflowRuntime(tmp_path, service, Events(), clock=lambda: clock[0])
    finish_stream(value, clock)
    yield value, service, clock, tmp_path
    value.close()


def state(value):
    return value.streams(ACCOUNT)["streams"][0]["recording"]


def test_matching_archive_is_suggested_without_verifying_its_clock(recording):
    value, service, _, _ = recording
    assert state(value)["status"] == "pending"
    service.videos = [archive()]
    value.discover_recordings()
    assert state(value)["status"] == "ready"
    assert state(value)["vod_id"] == "789"
    assert value.streams(ACCOUNT)["streams"][0]["mapping"] is None
    assert value.nominations.mapping(ACCOUNT, "789") is None


@pytest.mark.parametrize("bad", [archive(user_id="999"), archive(stream_id="999"),
                                     archive(type="highlight"), archive(id="bad"),
                                     archive(duration_seconds=0), {}])
def test_discovery_never_guesses_an_owner_stream_or_playable_recording(recording, bad):
    value, service, _, _ = recording
    service.videos = [bad]
    value.discover_recordings()
    assert state(value)["status"] == "pending"
    assert state(value)["vod_id"] is None


def test_backoff_survives_restart_and_expiry_needs_explicit_retry(recording):
    value, service, clock, folder = recording
    value.discover_recordings()
    assert service.lookups == 1 and state(value)["attempts"] == 1
    value.discover_recordings()
    assert service.lookups == 1
    value.close()
    restarted = WorkflowRuntime(folder, service, Events(), clock=lambda: clock[0])
    try:
        restarted.discover_recordings()
        assert service.lookups == 1
        clock[0] += 6 * 3600 + 1
        restarted.discover_recordings()
        assert state(restarted)["status"] == "expired"
        assert service.lookups == 1
        restarted.retry_recording(ACCOUNT, "456")
        service.videos = [archive()]
        restarted.discover_recordings()
        assert state(restarted)["status"] == "ready"
    finally:
        restarted.close()


def test_disconnected_account_does_not_make_provider_calls(recording):
    value, service, _, _ = recording
    service.rows[0]["status"] = "reconnect_required"
    value.discover_recordings()
    assert service.lookups == 0
    service.rows[0]["status"] = "connected"
    with pytest.raises(WorkflowError):
        value.retry_recording(ACCOUNT, "999")


def test_restart_finds_recording_when_the_stream_ended_while_app_was_closed(tmp_path, monkeypatch):
    monkeypatch.setattr(WorkflowRuntime, "_recordings_later", lambda self: None)
    clock, service = [START], Service()
    value = WorkflowRuntime(tmp_path, service, Events(), clock=lambda: clock[0])
    value.start_monitor(ACCOUNT)
    assert value.record_event(ACCOUNT, {
        "id": "online", "type": "stream.online", "timestamp": iso(clock[0]),
        "broadcaster_id": "123", "data": {"id": "456", "started_at": iso(START)},
    })
    value.close()
    clock[0] += 1200
    restarted = WorkflowRuntime(tmp_path, service, Events(), clock=lambda: clock[0])
    try:
        restarted.start_monitor(ACCOUNT)  # Twitch confirms the channel is now offline.
        assert state(restarted)["status"] == "pending"
        service.videos = [archive()]
        restarted.discover_recordings()
        assert state(restarted)["vod_id"] == "789"
    finally:
        restarted.close()


def test_offline_event_starts_discovery_and_restored_pending_work_resumes(tmp_path, monkeypatch):
    monkeypatch.setattr("stream_workflows.runtime._RECORDING_POLL_SECONDS", 0.01, raising=False)
    clock, service = [START], Service()
    value = WorkflowRuntime(tmp_path, service, Events(), clock=lambda: clock[0])
    try:
        finish_stream(value, clock)
        until = time.monotonic() + 3
        while service.lookups == 0 and time.monotonic() < until:
            threading.Event().wait(0.01)
        assert service.lookups == 1
    finally:
        value.close()
    service.videos = [archive()]
    clock[0] += 60
    restarted = WorkflowRuntime(tmp_path, service, Events(), clock=lambda: clock[0])
    try:
        restarted.start()
        until = time.monotonic() + 3
        while state(restarted)["status"] != "ready" and time.monotonic() < until:
            threading.Event().wait(0.01)
        assert state(restarted)["status"] == "ready"
    finally:
        restarted.close()


def test_constructing_runtime_does_not_open_optional_storage_or_start_threads(tmp_path, monkeypatch):
    damaged = tmp_path / "stream-workflows.sqlite3"
    damaged.write_bytes(b"damaged optional storage")
    scheduled = []
    monkeypatch.setattr(WorkflowRuntime, "_recordings_later", lambda self: scheduled.append(True))
    value = WorkflowRuntime(tmp_path, Service(), Events())
    try:
        assert scheduled == []
        assert value._nominations is None
        assert damaged.read_bytes() == b"damaged optional storage"
    finally:
        value.close()
