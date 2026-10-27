"""Exercise the desktop HTTP boundary with the real account lifecycle offline."""

import copy
import json
from typing import ClassVar

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi import FastAPI
from fastapi.testclient import TestClient

from connected_accounts.models import AccountError
from connected_accounts.service import ConnectedAccounts
from core.state import StateDB
from server import connected_accounts_api

HEADERS = {"X-Clips-Kitty-Session": "desktop-session-0123456789"}
ACCOUNT = "twitch:123"


class MemoryStore:
    available = True
    backend = "test-memory"

    def __init__(self):
        self.values = {}

    def save(self, key, value):
        self.values[key] = copy.deepcopy(value)

    def load(self, key):
        return copy.deepcopy(self.values.get(key))

    def delete(self, key):
        self.values.pop(key, None)


class FakeTwitch:
    id = "twitch"
    client_id = "publicclientid"
    default_scopes = ("clips:edit",)
    capabilities: ClassVar = {"device_code": True, "pkce": False}
    operation_scopes: ClassVar = {"create_clip": ("clips:edit",)}

    def __init__(self):
        self.revoked = []
        self.operations = []

    def begin(self, scopes):
        return {
            "device_code": "private-device-code",
            "user_code": "ABCD-EFGH",
            "verification_uri": "https://www.twitch.tv/activate",
            "expires_in": 600,
            "interval": 1,
        }

    def poll(self, device_code):
        assert device_code == "private-device-code"
        return {"access_token": "private-access", "refresh_token": "private-refresh", "expires_in": 3600}

    def validate(self, access_token):
        return {
            "user_id": "123",
            "client_id": self.client_id,
            "login": "kitty",
            "scopes": ["clips:edit"],
            "expires_in": 3600,
        }

    def identity(self, access_token):
        return {"id": "123", "login": "kitty", "display_name": "Kitty"}

    def revoke(self, access_token):
        self.revoked.append(access_token)

    def perform(self, access_token, account, operation, **params):
        self.operations.append((operation, params))
        return {"kind": "native_clip", "id": "example-clip", "url": "https://clips.twitch.tv/example-clip"}


class FakeEvents:
    def __init__(self):
        self.started = []
        self.stopped = []
        self.released = []

    def start(self, account_id, broadcaster_id, event_types):
        self.started.append((account_id, broadcaster_id, event_types))
        return {"status": "connecting"}

    def events(self, account_id, **params):
        return {"status": "connected", "events": [], "cursor": 0}

    def stop(self, account_id):
        self.stopped.append(account_id)

    def release(self, account_id, consumer):
        self.released.append((account_id, consumer))

    def close(self):
        pass


@pytest.fixture
def api(tmp_path):
    clock = [100.0]
    provider, store, events = FakeTwitch(), MemoryStore(), FakeEvents()
    service = ConnectedAccounts(tmp_path, {"twitch": provider}, store=store, clock=lambda: clock[0])
    app = FastAPI()
    runtime = connected_accounts_api.install(
        app,
        data_dir=tmp_path,
        db=lambda: StateDB(tmp_path / "state.db"),
        session_secret=HEADERS["X-Clips-Kitty-Session"],
        service=service,
        event_manager=events,
    )
    client = TestClient(app, base_url="http://127.0.0.1")
    return client, service, provider, store, events, runtime, clock, tmp_path


def connect(api):
    client, _, _, _, _, _, clock, _ = api
    response = client.post(
        "/connected-accounts/twitch/connect", headers=HEADERS, json={"scopes": ["clips:edit"]}
    )
    assert response.status_code == 200, response.text
    flow = response.json()
    assert "private-device-code" not in response.text
    clock[0] += flow["interval"]
    response = client.post(f"/connected-accounts/flows/{flow['flow_id']}/poll", headers=HEADERS)
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "connected"
    return flow, response


def test_all_account_routes_require_desktop_session_before_side_effects(api):
    client = api[0]
    for method, path, body in [
        ("GET", "", None),
        ("PUT", "/twitch/config", {"client_id": "abc"}),
        ("POST", "/twitch/connect", {}),
        ("POST", "/flows/unknown/poll", None),
        ("DELETE", "/flows/unknown", None),
        ("POST", f"/{ACCOUNT}/test", None),
        ("DELETE", f"/{ACCOUNT}", None),
        ("POST", f"/{ACCOUNT}/watch", None),
        ("POST", f"/{ACCOUNT}/twitch", {"operation": "create_clip"}),
        ("POST", f"/{ACCOUNT}/events", {}),
        ("GET", f"/{ACCOUNT}/events", None),
        ("DELETE", f"/{ACCOUNT}/events", None),
    ]:
        for headers in ({}, {"X-Clips-Kitty-Session": "incorrect"}):
            response = client.request(method, "/connected-accounts" + path, json=body, headers=headers)
            assert response.status_code == 403, (method, path, response.text)
    assert api[1].accounts() == []


def test_connect_test_and_status_never_return_credentials(api):
    _, result = connect(api)
    client = api[0]
    status = client.get("/connected-accounts", headers=HEADERS)
    tested = client.post(f"/connected-accounts/{ACCOUNT}/test", headers=HEADERS)
    assert tested.json()["ok"] is True
    assert status.json()["providers"][0]["storage_available"] is True
    assert status.json()["accounts"][0]["id"] == ACCOUNT
    for response in (result, status, tested):
        assert "private-access" not in response.text
        assert "private-refresh" not in response.text
        assert "access_token" not in response.text
    roster = (api[-1] / "connected-accounts.json").read_text()
    assert "private-" not in roster


def test_cancelled_flow_cannot_be_exchanged(api):
    client = api[0]
    flow = client.post("/connected-accounts/twitch/connect", headers=HEADERS, json={}).json()
    cancelled = client.delete(f"/connected-accounts/flows/{flow['flow_id']}", headers=HEADERS)
    assert cancelled.status_code == 200
    response = client.post(f"/connected-accounts/flows/{flow['flow_id']}/poll", headers=HEADERS)
    assert response.status_code == 404
    assert api[1].accounts() == []


def test_new_consent_resets_old_grants_and_collection_opt_in(api):
    runtime, events = api[5], api[4]
    revoked, reset = [], []

    class Gateway:
        def revoke_account(self, account_id):
            revoked.append(account_id)

    class Workflow:
        def account_disconnected(self, account_id):
            reset.append(account_id)

    runtime.gateway, runtime.workflow = Gateway(), Workflow()
    connect(api)
    assert revoked == [ACCOUNT] and reset == [ACCOUNT] and events.stopped == [ACCOUNT]


def test_connected_account_adds_existing_watched_channel_idempotently(api):
    connect(api)
    client = api[0]
    first = client.post(f"/connected-accounts/{ACCOUNT}/watch", headers=HEADERS)
    assert first.status_code == 200, first.text
    assert first.json()["created"] is True
    second = client.post(f"/connected-accounts/{ACCOUNT}/watch", headers=HEADERS)
    assert second.status_code == 200, second.text
    assert second.json()["created"] is False
    db = StateDB(api[-1] / "state.db")
    try:
        assert len(db.list_watches()) == 1
        assert db.list_watches()[0]["channel_key"] == "kitty"
    finally:
        db.close()


def test_disconnect_revokes_gateway_events_and_local_credentials(api):
    connect(api)
    client, service, provider, store, events, runtime, _, _ = api
    events.stopped.clear()  # Fresh consent already cleared any obsolete consumers.

    class Gateway:
        def __init__(self):
            self.revoked = []

        def revoke_account(self, account_id):
            self.revoked.append(account_id)

    runtime.gateway = Gateway()
    response = client.delete(f"/connected-accounts/{ACCOUNT}", headers=HEADERS)
    assert response.json() == {"disconnected": True, "revoked": True}
    assert provider.revoked == ["private-access"]
    assert events.stopped == [ACCOUNT]
    assert runtime.gateway.revoked == [ACCOUNT]
    assert store.values == {} and service.accounts() == []


def test_config_is_public_only_and_preserves_future_providers(api):
    client, _, _, _, _, _, _, data_dir = api
    path = data_dir / "connected-account-providers.json"
    path.write_text(json.dumps({"future": {"client_id": "keep"}}))
    response = client.put(
        "/connected-accounts/twitch/config", headers=HEADERS, json={"client_id": "newpublicid"}
    )
    assert response.status_code == 200, response.text
    assert json.loads(path.read_text()) == {
        "future": {"client_id": "keep"},
        "twitch": {"client_id": "newpublicid"},
    }
    for body in ({"client_id": "valid", "client_secret": "never"}, {"client_id": "https://evil.example"}):
        assert client.put("/connected-accounts/twitch/config", headers=HEADERS, json=body).status_code == 422


def test_desktop_operations_bind_broadcaster_and_reject_unrestricted_actions(api):
    connect(api)
    client, _, provider, *_ = api
    path = f"/connected-accounts/{ACCOUNT}/twitch"
    for body in (
        {"operation": "http", "params": {"url": "https://evil.example"}},
        {"operation": "subscribe_events", "params": {"session_id": "untrusted"}},
        {"operation": "send_chat", "params": {"broadcaster_id": "999", "message": "No write"}},
    ):
        assert client.post(path, headers=HEADERS, json=body).status_code in (400, 403)
    assert provider.operations == []
    response = client.post(path, headers=HEADERS, json={"operation": "create_clip"})
    assert response.status_code == 200
    assert provider.operations == [("create_clip", {"broadcaster_id": "123"})]


def test_viewer_can_read_selected_public_channel_and_subscribe(api):
    connect(api)
    client, _, provider, _, events, *_ = api
    path = f"/connected-accounts/{ACCOUNT}"
    response = client.post(path + "/twitch", headers=HEADERS,
                           json={"operation": "stream_context", "params": {"broadcaster_login": "zackrawrr"}})
    assert response.status_code == 200, response.text
    assert provider.operations[-1] == ("stream_context", {"broadcaster_login": "zackrawrr"})
    response = client.post(path + "/events", headers=HEADERS,
                           json={"broadcaster_id": "999", "event_types": ["stream.online"]})
    assert response.status_code == 200, response.text
    assert events.started[-1] == (ACCOUNT, "999", ["stream.online"])
    response = client.post(path + "/twitch", headers=HEADERS,
                           json={"operation": "create_clip", "params": {"broadcaster_id": "999"}})
    assert response.status_code == 200, response.text
    assert provider.operations[-1] == ("create_clip", {"broadcaster_id": "999"})
    response = client.post(path + "/twitch", headers=HEADERS, json={
        "operation": "vod_link", "params": {"broadcaster_id": "999", "video_id": "789", "seconds": 60}})
    assert response.status_code == 200, response.text
    assert provider.operations[-1] == ("vod_link", {"broadcaster_id": "999", "video_id": "789", "seconds": 60})


@pytest.mark.parametrize("key", ["account_id", "operation", "access_token", "url", "session_id"])
def test_reserved_and_unknown_operation_parameters_are_rejected(api, key):
    connect(api)
    response = api[0].post(
        f"/connected-accounts/{ACCOUNT}/twitch",
        headers=HEADERS,
        json={"operation": "create_clip", "params": {key: "untrusted"}},
    )
    assert response.status_code == 400
    assert api[2].operations == []


def test_failed_provider_registration_restores_saved_configuration(api, monkeypatch):
    client, service, _, _, _, _, _, data_dir = api
    path = data_dir / "connected-account-providers.json"
    original = b'{"twitch":{"client_id":"publicclientid"},"future":{"client_id":"keep"}}'
    path.write_bytes(original)

    def failed(provider):
        raise AccountError("account_store_unavailable", "The account list could not be saved.", status=503)

    monkeypatch.setattr(service, "register_provider", failed)
    response = client.put(
        "/connected-accounts/twitch/config", headers=HEADERS, json={"client_id": "newpublicid"}
    )
    assert response.status_code == 503
    assert path.read_bytes() == original
    assert service.providers["twitch"].client_id == "publicclientid"


def test_eventsub_session_is_owned_by_host(api):
    connect(api)
    client, _, _, _, events, *_ = api
    response = client.post(
        f"/connected-accounts/{ACCOUNT}/events", headers=HEADERS, json={"event_types": ["stream.online"]}
    )
    assert response.status_code == 200
    assert events.started == [(ACCOUNT, "123", ["stream.online"])]
    response = client.post(
        f"/connected-accounts/{ACCOUNT}/events",
        headers=HEADERS,
        json={"session_id": "plugin-socket", "event_types": ["stream.online"]},
    )
    assert response.status_code == 422


def test_stopping_desktop_events_leaves_nomination_consumer_running(api):
    connect(api)
    client, _, _, _, events, *_ = api
    events.stopped.clear()
    response = client.delete(f"/connected-accounts/{ACCOUNT}/events", headers=HEADERS)
    assert response.status_code == 200
    assert events.released == [(ACCOUNT, "desktop")]
    assert events.stopped == []


def test_unavailable_keychain_prevents_authorization(api):
    api[3].available = False
    response = api[0].post("/connected-accounts/twitch/connect", headers=HEADERS, json={})
    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "credential_store_unavailable"


def test_fresh_install_has_unconfigured_provider_without_crashing(tmp_path):
    service = connected_accounts_api.make_service(tmp_path)
    assert service.status()["providers"][0]["configured"] is False
    assert service.accounts() == []


def test_provider_error_is_sanitized_at_http_boundary(api):
    connect(api)

    def failed(*args, **kwargs):
        raise RuntimeError("upstream echoed private-access and private-refresh")

    api[2].perform = failed
    response = api[0].post(
        f"/connected-accounts/{ACCOUNT}/twitch", headers=HEADERS, json={"operation": "create_clip"}
    )
    assert response.status_code == 502
    assert "private-" not in response.text
    assert response.json()["detail"]["code"] == "provider_unavailable"


def test_offline_revocation_still_disconnects_locally(api):
    connect(api)

    def offline(*args):
        raise AccountError("provider_unavailable", "Twitch could not be reached.", status=503)

    api[2].revoke = offline
    response = api[0].delete(f"/connected-accounts/{ACCOUNT}", headers=HEADERS)
    assert response.json() == {"disconnected": True, "revoked": False}
    assert api[1].accounts() == [] and api[3].values == {}
