"""The SDK account helper is loopback-only and never receives Twitch credentials."""
import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

SDK = Path(__file__).resolve().parent.parent / "sdk" / "python"
if str(SDK) not in sys.path:
    sys.path.insert(0, str(SDK))

from clipskitty_sdk import (  # noqa: E402
    AccountOperationError,
    ConnectedAccounts,
    Job,
    accounts,
    host,
    manifest,
)


@pytest.fixture
def service(monkeypatch):
    calls = []
    reply = {"status": 200, "body": {"result": {"kind": "timestamp", "url": "https://www.twitch.tv/videos/456?t=10s"}}}

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            calls.append((self.path, dict(self.headers), json.loads(self.rfile.read(int(self.headers["Content-Length"])))))
            self.send_response(reply["status"])
            self.send_header("Content-Type", "application/json")
            for name, value in reply.get("headers", {}).items():
                self.send_header(name, value)
            self.end_headers()
            self.wfile.write(json.dumps(reply["body"]).encode())

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    monkeypatch.setenv(accounts.ENDPOINT_ENV, f"http://127.0.0.1:{server.server_port}{accounts.ROUTE}")
    monkeypatch.setenv(accounts.CAPABILITY_ENV, "run-capability-" + "x" * 32)
    yield calls, reply
    server.shutdown()
    server.server_close()
    worker.join()


def test_job_accounts_use_fixed_operations_and_capability_without_proxy(service, monkeypatch, tmp_path):
    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:1")
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:1")
    monkeypatch.setenv("NO_PROXY", "")
    job = Job(tmp_path, {})
    assert job.accounts.available
    assert job.accounts.twitch.timestamp_link(10)["kind"] == "timestamp"
    calls, _ = service
    path, headers, body = calls[0]
    assert path == accounts.ROUTE
    assert headers[accounts.HEADER] == "run-capability-" + "x" * 32
    assert "Authorization" not in headers and "X-Clips-Kitty-Session" not in headers
    assert body["operation"] == "vod_link" and body["parameters"] == {"seconds": 10}
    assert len(body["request_id"]) == 32
    assert "capability" not in json.dumps(job.data)
    assert "run-capability" not in repr(job.accounts)


def test_source_nomination_and_vod_helpers_use_fixed_token_free_operations(service):
    api = ConnectedAccounts().twitch
    api.source_info()
    api.audience_signals()
    api.create_vod_clip(10, 30, "A grounded highlight")
    api.get_clip("VerifiedClipId")
    calls, _ = service
    assert [(body["operation"], body["parameters"]) for _, _, body in calls] == [
        ("source_info", {}), ("audience_signals", {}),
        ("create_vod_clip", {"start_seconds": 10, "end_seconds": 30, "title": "A grounded highlight"}),
        ("get_clip", {"clip_id": "VerifiedClipId"}),
    ]
    for path, headers, body in calls:
        assert path == accounts.ROUTE
        assert "Authorization" not in headers
        assert "access_token" not in json.dumps(body) and "account_id" not in body["parameters"]


@pytest.mark.parametrize("url", ["https://example.com/plugin-accounts/perform", "http://127.0.0.1:8765/other",
                                     "http://user:password@127.0.0.1:8765/plugin-accounts/perform",
                                     "http://127.0.0.1:8765/plugin-accounts/perform?secret=x"])
def test_untrusted_endpoint_never_receives_capability(service, monkeypatch, url):
    monkeypatch.setenv(accounts.ENDPOINT_ENV, url)
    api = ConnectedAccounts()
    assert not api.available
    with pytest.raises(AccountOperationError):
        api.twitch.context()
    assert service[0] == []


def test_standalone_plugin_has_no_grant_and_inherited_env_is_removed(monkeypatch, tmp_path):
    monkeypatch.delenv(accounts.ENDPOINT_ENV, raising=False)
    monkeypatch.delenv(accounts.CAPABILITY_ENV, raising=False)
    assert not ConnectedAccounts().available
    env = host.plugin_env({accounts.ENDPOINT_ENV: "endpoint", accounts.CAPABILITY_ENV: "capability",
                           "CLIPS_KITTY_SESSION_SECRET": "session", "TWITCH_ACCESS_TOKEN": "token"},
                          job_folder=tmp_path)
    assert accounts.ENDPOINT_ENV not in env and accounts.CAPABILITY_ENV not in env
    assert "CLIPS_KITTY_SESSION_SECRET" not in env and "TWITCH_ACCESS_TOKEN" not in env


def test_provider_errors_do_not_echo_body_credentials_or_retry_write(service):
    calls, reply = service
    reply.update(status=502, body={"detail": "Twitch access_token=never-echo-this"})
    with pytest.raises(AccountOperationError) as error:
        ConnectedAccounts().twitch.create_vod_clip(10, 30, "A grounded highlight")
    assert error.value.status == 502 and "never-echo" not in str(error.value)
    assert len(calls) == 1


def test_unverified_timeline_is_not_reported_as_already_attempted(service):
    calls, reply = service
    reply.update(status=412, body={"detail": "not verified"})
    for attempt in (lambda api: api.timestamp_link(20), lambda api: api.create_vod_clip(10, 30, "Highlight")):
        with pytest.raises(AccountOperationError) as error:
            attempt(ConnectedAccounts().twitch)
        assert error.value.status == 412
        assert "verified this video's Twitch timeline" in str(error.value)
        assert "already attempted" not in str(error.value)


def test_each_refusal_has_its_own_plain_message(service):
    calls, reply = service
    seen = set()
    for status in (400, 401, 403, 404, 412, 429, 502, 503):
        reply.update(status=status, body={"detail": "access_token=never-echo-this"})
        with pytest.raises(AccountOperationError) as error:
            ConnectedAccounts().twitch.context()
        assert error.value.status == status and "never-echo" not in str(error.value)
        seen.add(str(error.value))
    assert len(seen) == 8


def test_the_hosts_code_tells_refusals_with_one_status_apart(service):
    # Not granted to this run versus the Twitch connection lacking the scope
    # (both 403); a host part not running versus Twitch down (both 503); the
    # run's own limit versus Twitch's rate limit (both 429).
    calls, reply = service
    pairs = [(403, "not_permitted", "missing_scope"), (503, "unavailable", "provider_unavailable"),
             (429, "run_limit", "rate_limited")]
    for status, first, second in pairs:
        seen = []
        for code in (first, second):
            reply.update(status=status, headers={accounts.ERROR_HEADER: code},
                         body={"detail": "access_token=never-echo-this"})
            with pytest.raises(AccountOperationError) as error:
                ConnectedAccounts().twitch.context()
            assert error.value.status == status and error.value.code == code
            assert "never-echo" not in str(error.value)
            seen.append(str(error.value))
        assert seen[0] != seen[1], status


def test_a_code_that_is_not_a_plain_word_is_ignored(service):
    calls, reply = service
    reply.update(status=403, headers={accounts.ERROR_HEADER: "access_token=never-echo-this"}, body={})
    with pytest.raises(AccountOperationError) as error:
        ConnectedAccounts().twitch.context()
    assert error.value.code == "" and "never-echo" not in str(error.value)
    assert str(error.value) == "Clips Kitty has not granted that account operation for this run."


def test_chat_permission_without_events_is_refused_at_validation(plugin_source):
    report = manifest.validate(plugin_source.manifest(permissions=["video.read", "twitch.chat.read"]))
    assert any("twitch.chat.read needs twitch.events.read" in e for e in report.errors)
    report = manifest.validate(plugin_source.manifest(
        permissions=["video.read", "twitch.chat.read", "twitch.events.read"]))
    assert not any("twitch.chat.read" in e for e in report.errors)


def test_runs_have_no_live_clip_helper():
    # A plugin run works on a finished VOD; clipping whatever is live now is
    # the desktop's call, not a run's.
    assert not hasattr(ConnectedAccounts().twitch, "create_clip")


def test_manifest_vocabulary_and_schema_include_account_permissions():
    expected = {"twitch.context.read", "twitch.clips.create", "twitch.chat.read", "twitch.chat.send", "twitch.events.read"}
    assert expected <= set(manifest.PERMISSIONS)
    assert expected <= set(manifest.json_schema()["properties"]["permissions"]["items"]["enum"])
