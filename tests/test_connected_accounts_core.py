"""Offline account lifecycle/security checks; no real credentials or OAuth calls."""

import base64
import copy
import hashlib
import json
import sys
import threading
import time
import types
from concurrent.futures import ThreadPoolExecutor
from typing import ClassVar

import pytest

from connected_accounts.models import AccountError
from connected_accounts.service import ConnectedAccounts
from connected_accounts.store import SecureTokenStore


class Clock:
    def __init__(self):
        self.now = 1000

    def __call__(self):
        return self.now


class MemoryStore:
    available = True
    backend = "memory-test-only"

    def __init__(self):
        self.values = {}
        self.saves = 0
        self.fail_delete = False

    def save(self, key, value):
        self.values[key] = copy.deepcopy(value)
        self.saves += 1

    def load(self, key):
        return copy.deepcopy(self.values.get(key))

    def delete(self, key):
        if self.fail_delete:
            raise AccountError("credential_store_unavailable", "Could not delete stored credentials.", status=503)
        self.values.pop(key, None)


class FakeProvider:
    id = "twitch"
    client_id = "public-client-id"
    default_scopes = ("clips:edit",)
    operation_scopes: ClassVar = {"create_clip": ("clips:edit",)}
    capabilities: ClassVar = {"flow": "device_code", "pkce": False}

    def __init__(self):
        self.polls = self.refreshes = self.validations = self.operations = self.revocations = 0
        self.poll_error = None
        self.refresh_error = None
        self.validation_error = None
        self.revoke_error = None
        self.validation_user = "42"
        self.validation_client = self.client_id
        self.scopes = ["clips:edit"]
        self.closed = False
        self.revoked_tokens = []

    def begin(self, scopes):
        return {"device_code": "private-device-value", "user_code": "ABCD",
                "verification_uri": "https://www.twitch.tv/activate", "expires_in": 600, "interval": 5}

    def poll(self, code):
        assert code == "private-device-value"
        self.polls += 1
        if self.poll_error:
            raise self.poll_error
        return {"access_token": "private-access-value", "refresh_token": "private-refresh-value",
                "expires_in": 3600, "scope": self.scopes}

    def validate(self, token):
        self.validations += 1
        if self.validation_error:
            raise self.validation_error
        return {"client_id": self.validation_client, "user_id": self.validation_user,
                "login": "kitty", "scopes": self.scopes, "expires_in": 3600}

    def identity(self, token):
        return {"id": "42", "login": "kitty", "display_name": "Kitty"}

    def refresh(self, refresh_token):
        assert refresh_token == "private-refresh-value"
        self.refreshes += 1
        if self.refresh_error:
            raise self.refresh_error
        return {"access_token": "rotated-private-access-value", "refresh_token": "rotated-private-refresh-value",
                "expires_in": 3600, "scope": self.scopes}

    def perform(self, access_token, account, operation, **params):
        assert access_token in {"private-access-value", "rotated-private-access-value"}
        assert "access_token" not in account and "refresh_token" not in account
        self.operations += 1
        return {"id": "created-clip"}

    def revoke(self, token):
        self.revocations += 1
        self.revoked_tokens.append(token)
        if self.revoke_error:
            raise self.revoke_error

    def close(self):
        self.closed = True


@pytest.fixture
def setup(tmp_path):
    clock, store, provider = Clock(), MemoryStore(), FakeProvider()
    service = ConnectedAccounts(tmp_path, {"twitch": provider}, store=store, clock=clock)
    return service, provider, store, clock


def connect(setup):
    service, provider, store, clock = setup
    flow = service.begin("twitch")
    clock.now += 5
    result = service.poll(flow["flow_id"])
    assert result["status"] == "connected"
    return result["account"]["id"]


def test_flow_and_roster_never_expose_credentials(setup, tmp_path):
    service, provider, store, clock = setup
    flow = service.begin("twitch")
    assert "private-device-value" not in json.dumps(flow)
    assert "device_code" not in flow
    clock.now += 5
    result = service.poll(flow["flow_id"])
    assert result["account"]["id"] == "twitch:42"
    public = json.dumps([service.status(), result, json.loads((tmp_path / "connected-accounts.json").read_text())])
    assert "private-access-value" not in public
    assert "private-refresh-value" not in public
    assert "access_token" not in public and "refresh_token" not in public
    with pytest.raises(AccountError) as error:
        service.poll(flow["flow_id"])
    assert error.value.code == "invalid_flow"
    assert provider.polls == 1


def test_device_flow_poll_interval_slowdown_expiry_and_cancel(setup):
    service, provider, store, clock = setup
    flow = service.begin("twitch")
    assert service.poll(flow["flow_id"])["status"] == "pending"
    assert provider.polls == 0
    clock.now += 5
    provider.poll_error = AccountError("slow_down", "Wait before polling again.")
    assert service.poll(flow["flow_id"])["interval"] == 10
    clock.now += 5
    service.poll(flow["flow_id"])
    assert provider.polls == 1
    clock.now += 601
    with pytest.raises(AccountError) as error:
        service.poll(flow["flow_id"])
    assert error.value.code == "expired_token"
    flow = service.begin("twitch")
    assert service.cancel(flow["flow_id"])["status"] == "cancelled"
    with pytest.raises(AccountError):
        service.poll(flow["flow_id"])


def test_denied_device_flow_cannot_be_reused(setup):
    service, provider, store, clock = setup
    flow = service.begin("twitch")
    clock.now += 5
    provider.poll_error = AccountError("access_denied", "Sign-in was cancelled.")
    with pytest.raises(AccountError):
        service.poll(flow["flow_id"])
    provider.poll_error = None
    with pytest.raises(AccountError) as error:
        service.poll(flow["flow_id"])
    assert error.value.code == "invalid_flow"


@pytest.mark.parametrize("mismatch", ["user", "client"])
def test_issued_token_must_match_identity_and_public_client(setup, mismatch):
    service, provider, store, clock = setup
    flow = service.begin("twitch")
    if mismatch == "user":
        provider.validation_user = "other-user"
    else:
        provider.validation_client = "other-client"
    clock.now += 5
    with pytest.raises(AccountError) as error:
        service.poll(flow["flow_id"])
    assert error.value.code == "invalid_token"
    assert service.accounts() == [] and not store.values
    assert provider.revocations == 1


def test_parallel_operations_refresh_and_rotate_only_once(setup):
    service, provider, store, clock = setup
    account_id = connect(setup)
    clock.now += 3601
    with ThreadPoolExecutor(max_workers=8) as executor:
        results = list(executor.map(lambda _: service.perform(account_id, "create_clip"), range(16)))
    assert len(results) == 16
    assert provider.refreshes == 1
    assert store.values[account_id]["refresh_token"] == "rotated-private-refresh-value"
    assert store.saves == 2


def test_refused_refresh_marks_reconnect_without_deleting_other_accounts(setup):
    service, provider, store, clock = setup
    account_id = connect(setup)
    clock.now += 3601
    provider.refresh_error = AccountError("invalid_grant", "Reconnect Twitch.", status=401)
    with pytest.raises(AccountError):
        service.perform(account_id, "create_clip")
    assert service.accounts()[0]["status"] == "reconnect_required"
    assert provider.operations == 0
    with pytest.raises(AccountError) as error:
        service.perform(account_id, "create_clip")
    assert error.value.code == "reconnect_required"
    assert provider.refreshes == 1


def test_transient_refresh_failure_does_not_erase_authorization(setup):
    service, provider, store, clock = setup
    account_id = connect(setup)
    clock.now += 3601
    provider.refresh_error = AccountError("provider_unavailable", "Try again later.", status=502)
    with pytest.raises(AccountError):
        service.perform(account_id, "create_clip")
    assert service.accounts()[0]["status"] == "connected"
    assert account_id in store.values


def test_restored_account_validates_before_first_operation(setup, tmp_path):
    service, provider, store, clock = setup
    account_id = connect(setup)
    restored = ConnectedAccounts(tmp_path, {"twitch": provider}, store=store, clock=clock)
    provider.validation_user = "another-user"
    with pytest.raises(AccountError) as error:
        restored.perform(account_id, "create_clip")
    assert error.value.code == "invalid_token"
    assert provider.operations == 0
    assert restored.accounts()[0]["status"] == "reconnect_required"


def test_hourly_validation_updates_scopes_and_blocks_disallowed_action(setup):
    service, provider, store, clock = setup
    account_id = connect(setup)
    # Keep the access token unexpired while making validation due.
    store.values[account_id]["expires_at"] += 10000
    clock.now += 3600
    provider.scopes = []
    with pytest.raises(AccountError) as error:
        service.perform(account_id, "create_clip")
    assert error.value.code == "missing_scope"
    assert provider.operations == 0
    assert service.accounts()[0]["scopes"] == []


def test_validation_transport_errors_never_expose_raw_provider_exception(setup):
    service, provider, store, clock = setup
    account_id = connect(setup)
    provider.validation_error = RuntimeError("request failed token=private-access-value")
    with pytest.raises(AccountError) as error:
        service.test(account_id)
    assert "private-access-value" not in str(error.value)
    assert error.value.code == "provider_unavailable"


def test_disconnect_removes_credentials_even_if_provider_is_offline(setup, tmp_path):
    service, provider, store, clock = setup
    account_id = connect(setup)
    provider.revoke_error = AccountError("provider_unavailable", "Unavailable.", status=502)
    result = service.disconnect(account_id)
    assert result == {"disconnected": True, "revoked": False}
    assert not store.values and service.accounts() == []
    assert json.loads((tmp_path / "connected-accounts.json").read_text()) == []


def test_disconnect_after_the_access_token_expired_refreshes_then_revokes(setup):
    service, provider, store, clock = setup
    account_id = connect(setup)
    clock.now += 3601  # the app was closed for a while
    assert service.disconnect(account_id) == {"disconnected": True, "revoked": True}
    assert provider.refreshes == 1
    assert provider.revoked_tokens == ["rotated-private-access-value"]


def test_disconnect_reports_revoked_false_when_the_refresh_is_refused(setup):
    service, provider, store, clock = setup
    account_id = connect(setup)
    clock.now += 3601
    provider.refresh_error = AccountError("invalid_grant", "Reconnect Twitch.", status=401)
    provider.revoke_error = AccountError("invalid_token", "Invalid token.", status=400)
    assert service.disconnect(account_id) == {"disconnected": True, "revoked": False}
    assert provider.revoked_tokens == ["private-access-value"] and not store.values


def test_disconnect_does_not_report_success_if_local_credentials_cannot_be_deleted(setup):
    service, provider, store, clock = setup
    account_id = connect(setup)
    store.fail_delete = True
    with pytest.raises(AccountError) as error:
        service.disconnect(account_id)
    assert error.value.code == "credential_store_unavailable"
    assert account_id in store.values and service.accounts()


def test_changed_client_cancels_pending_flow_and_requires_reconnect(setup):
    service, provider, store, clock = setup
    account_id = connect(setup)
    flow = service.begin("twitch")
    replacement = FakeProvider()
    replacement.client_id = "different-client"
    service.register_provider(replacement)
    assert service.accounts()[0]["status"] == "reconnect_required"
    assert provider.closed
    with pytest.raises(AccountError) as error:
        service.poll(flow["flow_id"])
    assert error.value.code == "invalid_flow"
    with pytest.raises(AccountError):
        service.perform(account_id, "create_clip")


def test_changed_client_after_restart_updates_visible_status(setup, tmp_path):
    service, provider, store, clock = setup
    account_id = connect(setup)
    replacement = FakeProvider()
    replacement.client_id = "different-client"
    restored = ConnectedAccounts(tmp_path, {"twitch": replacement}, store=store, clock=clock)
    with pytest.raises(AccountError) as error:
        restored.test(account_id)
    assert error.value.code == "reconnect_required"
    assert restored.accounts()[0]["status"] == "reconnect_required"
    assert replacement.validations == 0


def test_reconnect_metadata_failure_restores_prior_credentials(setup, monkeypatch):
    service, provider, store, clock = setup
    account_id = connect(setup)
    previous_account = service.accounts()[0]
    previous_token = copy.deepcopy(store.values[account_id])
    provider.poll = lambda code: {"access_token": "replacement-access-value",
                                 "refresh_token": "replacement-refresh-value", "expires_in": 3600}
    flow = service.begin("twitch")
    clock.now += 5

    def fail_persist():
        raise AccountError("account_store_unavailable", "The account list could not be saved.", status=503)

    monkeypatch.setattr(service, "_persist", fail_persist)
    with pytest.raises(AccountError) as error:
        service.poll(flow["flow_id"])
    assert error.value.code == "account_store_unavailable"
    assert service.accounts() == [previous_account]
    assert store.values[account_id] == previous_token
    assert provider.revoked_tokens == ["replacement-access-value"]
    assert account_id not in service._validated
    validations = provider.validations
    service.perform(account_id, "create_clip")
    assert provider.validations == validations + 1


def test_failed_credential_rollback_clears_replacement_and_requires_reconnect(setup, monkeypatch):
    service, provider, store, clock = setup
    account_id = connect(setup)
    provider.poll = lambda code: {"access_token": "replacement-access-value",
                                 "refresh_token": "replacement-refresh-value", "expires_in": 3600}
    flow = service.begin("twitch")
    clock.now += 5
    save = store.save

    def fail_rollback(key, value):
        if value["access_token"] == "private-access-value":
            raise AccountError("credential_store_unavailable", "Could not restore the previous credentials.", status=503)
        save(key, value)

    def fail_persist():
        raise AccountError("account_store_unavailable", "The account list could not be saved.", status=503)

    monkeypatch.setattr(store, "save", fail_rollback)
    monkeypatch.setattr(service, "_persist", fail_persist)
    with pytest.raises(AccountError) as error:
        service.poll(flow["flow_id"])
    assert error.value.code == "credential_store_unavailable"
    assert account_id not in store.values
    assert service.accounts()[0]["status"] == "reconnect_required"
    assert provider.revoked_tokens == ["replacement-access-value"]
    with pytest.raises(AccountError) as error:
        service.perform(account_id, "create_clip")
    assert error.value.code == "reconnect_required"


def test_configuration_metadata_failure_preserves_provider_accounts_and_flow(setup, monkeypatch):
    service, provider, store, clock = setup
    account_id = connect(setup)
    original_accounts = service.accounts()
    original_validations = dict(service._validated)
    flow = service.begin("twitch")
    replacement = FakeProvider()
    replacement.client_id = "different-client"

    def fail_persist():
        raise AccountError("account_store_unavailable", "The account list could not be saved.", status=503)

    monkeypatch.setattr(service, "_persist", fail_persist)
    with pytest.raises(AccountError):
        service.register_provider(replacement)
    assert service.providers["twitch"] is provider
    assert service.accounts() == original_accounts
    assert service._validated == original_validations
    assert flow["flow_id"] in service._flows
    assert not provider.closed
    assert service.perform(account_id, "create_clip") == {"id": "created-clip"}


def test_same_client_configuration_closes_superseded_provider_without_cancelling_flow(setup):
    service, provider, store, clock = setup
    flow = service.begin("twitch")
    replacement = FakeProvider()
    service.register_provider(replacement)
    assert provider.closed
    assert flow["flow_id"] in service._flows
    clock.now += 5
    assert service.poll(flow["flow_id"])["status"] == "connected"


def test_disconnect_metadata_failure_keeps_truthful_retryable_status(setup, monkeypatch):
    service, provider, store, clock = setup
    account_id = connect(setup)
    persist = service._persist

    def fail_persist():
        raise AccountError("account_store_unavailable", "The account list could not be saved.", status=503)

    monkeypatch.setattr(service, "_persist", fail_persist)
    with pytest.raises(AccountError):
        service.disconnect(account_id)
    assert account_id not in store.values
    assert service.accounts()[0]["status"] == "reconnect_required"
    monkeypatch.setattr(service, "_persist", persist)
    assert service.disconnect(account_id) == {"disconnected": True, "revoked": False}
    assert service.accounts() == []


def test_no_connection_without_secure_store(setup):
    service, provider, store, clock = setup
    store.available = False
    with pytest.raises(AccountError) as error:
        service.begin("twitch")
    assert error.value.code == "credential_store_unavailable"
    assert not store.values


def test_linux_plaintext_or_chained_keyring_is_rejected(monkeypatch, tmp_path):
    monkeypatch.setattr("connected_accounts.store.sys.platform", "linux")
    plaintext = type("PlaintextKeyring", (), {"__module__": "keyrings.alt.file"})()
    monkeypatch.setitem(sys.modules, "keyring", types.SimpleNamespace(get_keyring=lambda: plaintext))
    store = SecureTokenStore(tmp_path)
    assert store.backend == "unavailable" and not store.available
    with pytest.raises(AccountError):
        store.save("twitch:42", {"access_token": "private-access-value"})
    assert not list(tmp_path.rglob("*"))


def test_windows_store_encrypts_before_atomic_file_write(monkeypatch, tmp_path):
    monkeypatch.setattr("connected_accounts.store.sys.platform", "win32")

    def fake_dpapi(encrypt, raw):
        return b"encrypted:" + base64.b64encode(raw) if encrypt else base64.b64decode(raw.removeprefix(b"encrypted:"))

    monkeypatch.setattr("core.secrets._dpapi", fake_dpapi)
    store = SecureTokenStore(tmp_path)
    payload = {"access_token": "private-access-value", "refresh_token": "private-refresh-value"}
    store.save("twitch:42", payload)
    files = list(tmp_path.rglob("*.bin"))
    assert len(files) == 1
    assert b"private-access-value" not in files[0].read_bytes()
    assert store.load("twitch:42") == payload
    assert not list(tmp_path.rglob(".connected-*"))
    store.delete("twitch:42")
    assert store.load("twitch:42") is None


def _fake_windows_store(monkeypatch, tmp_path):
    """The real store on its Windows path, with DPAPI faked and breakable."""
    monkeypatch.setattr("connected_accounts.store.sys.platform", "win32")
    # Bumping the key stands in for another Windows account's master key:
    # blobs written before it no longer decrypt.
    key = {"generation": 1}

    def fake_dpapi(encrypt, raw):
        if encrypt:
            return b"%d:" % key["generation"] + base64.b64encode(raw)
        generation, _, body = raw.partition(b":")
        if int(generation) != key["generation"]:
            raise OSError("DPAPI refused the data")
        return base64.b64decode(body)

    monkeypatch.setattr("core.secrets._dpapi", fake_dpapi)
    return SecureTokenStore(tmp_path), key


def test_windows_tokens_go_through_core_secrets_one_entry_per_account(monkeypatch, tmp_path):
    store, _ = _fake_windows_store(monkeypatch, tmp_path)
    store.save("twitch:42", {"access_token": "private-access-value"})
    digest = hashlib.sha256(b"twitch:42").hexdigest()
    assert [p.name for p in (tmp_path / "credentials").iterdir()] == [f"connected-account-{digest}.bin"]


def test_undecryptable_credential_asks_to_reconnect_and_reconnect_keeps_the_new_grant(monkeypatch, tmp_path):
    store, key = _fake_windows_store(monkeypatch, tmp_path)
    clock, provider = Clock(), FakeProvider()
    service = ConnectedAccounts(tmp_path, {"twitch": provider}, store=store, clock=clock)
    account_id = connect((service, provider, store, clock))
    # A data folder restored onto another Windows account: the blob is there
    # but DPAPI can't open it. Not "store unavailable": the grant is gone.
    key["generation"] = 2
    assert store.load(account_id) is None
    with pytest.raises(AccountError) as error:
        service.test(account_id)
    assert error.value.code == "reconnect_required"
    assert service.accounts()[0]["status"] == "reconnect_required"
    # Reconnecting reads the old, unreadable blob first; that must not make
    # it revoke the token the person just approved.
    connect((service, provider, store, clock))
    assert provider.revoked_tokens == []
    assert service.accounts()[0]["status"] == "connected"
    assert service.test(account_id)["ok"] is True


def test_unreadable_old_credential_never_revokes_a_fresh_approval(setup):
    service, provider, store, clock = setup
    connect(setup)

    def locked_keychain(key):
        raise AccountError("credential_store_unavailable", "The OS credential store could not read this account.",
                           status=503)

    store.load = locked_keychain
    connect(setup)
    assert provider.revoked_tokens == []


class IssuerProvider(FakeProvider):
    """Records which client ID each revoke names, as Twitch checks it."""

    def __init__(self, client_id="public-client-id"):
        self.client_id = client_id
        super().__init__()
        self.revoked_with = []

    def revoke(self, token, *, client_id=None):
        self.revoked_with.append((token, client_id or self.client_id))
        super().revoke(token)


def test_disconnect_after_a_client_id_change_revokes_with_the_issuing_client(tmp_path):
    clock, store, old = Clock(), MemoryStore(), IssuerProvider()
    service = ConnectedAccounts(tmp_path, {"twitch": old}, store=store, clock=clock)
    account_id = connect((service, old, store, clock))
    new = IssuerProvider("different-client")
    service.register_provider(new)
    assert service.disconnect(account_id) == {"disconnected": True, "revoked": True}
    assert new.revoked_with == [("private-access-value", "public-client-id")]


def test_reconnecting_under_a_new_client_id_revokes_the_old_grant(tmp_path):
    clock, store, old = Clock(), MemoryStore(), IssuerProvider()
    service = ConnectedAccounts(tmp_path, {"twitch": old}, store=store, clock=clock)
    connect((service, old, store, clock))
    new = IssuerProvider("different-client")
    new.poll = lambda code: {"access_token": "replacement-access-value",
                             "refresh_token": "replacement-refresh-value", "expires_in": 3600}
    service.register_provider(new)
    connect((service, new, store, clock))
    assert new.revoked_with == [("private-access-value", "public-client-id")]
    assert service.accounts()[0]["client_id"] == "different-client"


def test_reconnecting_with_the_same_client_leaves_the_new_grant_alone(setup):
    service, provider, store, clock = setup
    connect(setup)
    connect(setup)
    assert provider.revoked_tokens == []


def test_a_damaged_account_list_is_set_aside_instead_of_stopping_startup(tmp_path, capsys):
    roster = tmp_path / "connected-accounts.json"
    roster.write_text("{not json", encoding="utf-8")
    clock, store, provider = Clock(), MemoryStore(), FakeProvider()
    service = ConnectedAccounts(tmp_path, {"twitch": provider}, store=store, clock=clock)
    assert service.accounts() == []
    assert (tmp_path / "connected-accounts.json.corrupt").read_text(encoding="utf-8") == "{not json"
    assert "set aside" in capsys.readouterr().out
    connect((service, provider, store, clock))
    assert len(json.loads(roster.read_text(encoding="utf-8"))) == 1


@pytest.mark.parametrize("content", ["{}", "[1, 2"])
def test_an_account_list_that_cant_be_moved_is_never_overwritten(tmp_path, monkeypatch, content):
    roster = tmp_path / "connected-accounts.json"
    roster.write_text(content, encoding="utf-8")
    monkeypatch.setattr("connected_accounts.service.set_aside", lambda path: False)
    clock, store, provider = Clock(), MemoryStore(), FakeProvider()
    service = ConnectedAccounts(tmp_path, {"twitch": provider}, store=store, clock=clock)
    assert service.accounts() == []
    flow = service.begin("twitch")
    clock.now += 5
    with pytest.raises(AccountError) as error:
        service.poll(flow["flow_id"])
    assert error.value.code == "account_store_unavailable"
    assert roster.read_text(encoding="utf-8") == content
    assert provider.revoked_tokens == ["private-access-value"]


def test_a_slow_twitch_request_never_blocks_the_account_list(setup):
    service, provider, store, clock = setup
    account_id = connect(setup)
    entered, release = threading.Event(), threading.Event()

    def slow(access_token, account, operation, **params):
        entered.set()
        release.wait(10)
        return {"id": "created-clip"}

    provider.perform = slow
    worker = threading.Thread(target=service.perform, args=(account_id, "create_clip"))
    worker.start()
    try:
        assert entered.wait(5)
        # The EventSub receive thread and the Settings page read these while
        # the request is still waiting on Twitch.
        started = time.monotonic()
        assert service.accounts()[0]["id"] == account_id
        assert service.status()["accounts"]
        assert time.monotonic() - started < 1
    finally:
        release.set()
        worker.join(5)
    assert not worker.is_alive()


def test_chained_keyring_is_accepted_only_when_every_backend_is_an_os_keychain(monkeypatch, tmp_path):
    monkeypatch.setattr("connected_accounts.store.sys.platform", "linux")

    def backend(module):
        return type("Backend", (), {"__module__": module})()

    def chained(*modules):
        chain = type("ChainerBackend", (), {"__module__": "keyring.backends.chainer"})()
        chain.backends = [backend(m) for m in modules]
        return chain

    for value, secure in ((chained("keyring.backends.SecretService", "keyring.backends.kwallet"), True),
                          (chained("keyring.backends.SecretService", "keyrings.alt.file"), False),
                          (chained(), False), (backend("keyring.backends.macOS"), True)):
        monkeypatch.setitem(sys.modules, "keyring", types.SimpleNamespace(get_keyring=lambda value=value: value))
        assert SecureTokenStore(tmp_path).available is secure
