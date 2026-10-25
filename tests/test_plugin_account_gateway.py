"""Account grants bind installed permissions to one run/channel, without OAuth tokens."""
import json
import uuid
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi import FastAPI
from fastapi.testclient import TestClient

from plugins import accounts, store


class Service:
    def __init__(self):
        self.calls = []
        self.fail = False

    def perform(self, account_id, operation, **args):
        self.calls.append((account_id, operation, args))
        if self.fail:
            raise RuntimeError("access_token=do-not-log-this-token")
        return {"kind": operation, "access_token": "never-handed-over", "safe": {"refresh_token": "hidden", "id": "7"}}


@pytest.fixture
def setup(tmp_path, plugin_source, install_plugin):
    manifest = plugin_source.manifest(permissions=["twitch.context.read", "twitch.clips.create", "twitch.chat.send",
                                                    "twitch.events.read"])
    folder = tmp_path / "plugin"
    plugin_source.write(folder, manifest)
    data_dir = tmp_path / "data"
    install_plugin(data_dir, folder)
    plugin = store.get(data_dir, manifest["id"])
    now = [100.0]
    service = Service()
    gateway = accounts.Gateway(data_dir, service, lambda video: {"account_id": "twitch:42", "broadcaster_id": "42",
                                                                "video_id": "456", "duration": 300},
                               timeline_mapper=lambda binding, seconds: seconds,
                               clock=lambda: now[0])
    yield SimpleNamespace(gateway=gateway, service=service, plugin=plugin, now=now, folder=folder, data_dir=data_dir)
    gateway.close()


VOD_CLIP = {"start_seconds": 10, "end_seconds": 30, "title": "A grounded highlight"}


def call(gateway, token, operation="stream_context", params=None, request_id=None):
    return gateway.perform(token, operation, params or {}, request_id or uuid.uuid4().hex)


def test_grant_binds_account_channel_source_and_never_returns_tokens(setup):
    with setup.gateway.run(setup.plugin, object(), 60) as env:
        token = env[accounts.CAPABILITY_ENV]
        result = call(setup.gateway, token)
        assert result == {"kind": "stream_context", "safe": {"id": "7"}}
        assert setup.service.calls == [("twitch:42", "stream_context", {"broadcaster_id": "42"})]
        assert "never-handed-over" not in json.dumps(env)
        call(setup.gateway, token, "vod_link", {"seconds": 299})
        assert setup.service.calls[-1] == ("twitch:42", "vod_link", {"video_id": "456", "seconds": 299})
        for override in ({"broadcaster_id": "999"}, {"account_id": "twitch:999"}, {"url": "https://example.com"}):
            with pytest.raises(accounts.AccessDenied):
                call(setup.gateway, token, params=override)
        with pytest.raises(accounts.AccessDenied):
            call(setup.gateway, token, "vod_link", {"seconds": 301})


def test_missing_permission_or_fake_capability_denied(setup):
    setup.plugin.manifest["permissions"] = ["twitch.context.read"]
    with setup.gateway.run(setup.plugin, object(), 60) as env:
        with pytest.raises(accounts.AccessDenied):
            call(setup.gateway, env[accounts.CAPABILITY_ENV], "create_vod_clip", VOD_CLIP)
        with pytest.raises(accounts.AccessDenied):
            call(setup.gateway, "different-token")
        with pytest.raises(accounts.AccessDenied):
            call(setup.gateway, env[accounts.CAPABILITY_ENV], "arbitrary_http")
    assert not setup.service.calls


def test_live_manifest_disable_and_permission_withdrawal_are_enforced(setup):
    with setup.gateway.run(setup.plugin, object(), 60) as env:
        token = env[accounts.CAPABILITY_ENV]
        manifest = setup.plugin.manifest.copy()
        manifest["permissions"] = ["twitch.context.read"]
        (setup.folder / "clipskitty.yaml").write_text(json.dumps(manifest), encoding="utf-8")
        with pytest.raises(accounts.AccessDenied):
            call(setup.gateway, token, "create_vod_clip", VOD_CLIP)
        call(setup.gateway, token)
        state = store.load(setup.data_dir)
        state["plugins"][setup.plugin.id]["enabled"] = False
        (store.root(setup.data_dir) / store.STATE_FILE).write_text(json.dumps(state), encoding="utf-8")
        with pytest.raises(accounts.AccessDenied):
            call(setup.gateway, token)
    assert len(setup.service.calls) == 1


def test_expiry_run_exit_exception_and_disconnect_revoke_grants(setup):
    with setup.gateway.run(setup.plugin, object(), 3) as env:
        token = env[accounts.CAPABILITY_ENV]
        setup.now[0] = 104
        with pytest.raises(accounts.AccessDenied):
            call(setup.gateway, token)
    tokens = []

    def failing_run():
        with setup.gateway.run(setup.plugin, object(), 30) as env:
            tokens.append(env[accounts.CAPABILITY_ENV])
            raise RuntimeError("process failed")

    with pytest.raises(RuntimeError):
        failing_run()
    with pytest.raises(accounts.AccessDenied):
        call(setup.gateway, tokens[0])
    with setup.gateway.run(setup.plugin, object(), 30) as env:
        token = env[accounts.CAPABILITY_ENV]
        setup.gateway.revoke_account("twitch:42")
        with pytest.raises(accounts.AccessDenied):
            call(setup.gateway, token)


def test_duplicate_writes_are_idempotent_and_conflicting_replay_is_denied(setup):
    with setup.gateway.run(setup.plugin, object(), 60) as env:
        token = env[accounts.CAPABILITY_ENV]
        request_id = uuid.uuid4().hex
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(call, setup.gateway, token, "create_vod_clip", VOD_CLIP, request_id)
                       for _ in range(2)]
            assert futures[0].result() == futures[1].result()
        assert len(setup.service.calls) == 1
        with pytest.raises(accounts.AccessDenied, match="already used"):
            call(setup.gateway, token, "send_chat", {"message": "hi"}, request_id)
        for _ in range(9):
            call(setup.gateway, token, "create_vod_clip", VOD_CLIP)
        with pytest.raises(accounts.AccessDenied) as limited:
            call(setup.gateway, token, "create_vod_clip", VOD_CLIP)
        assert limited.value.status == 429
        assert len(setup.service.calls) == 10


def test_ambiguous_write_failure_is_not_retried_and_error_has_no_credentials(setup):
    setup.service.fail = True
    with setup.gateway.run(setup.plugin, object(), 60) as env:
        token, request_id = env[accounts.CAPABILITY_ENV], uuid.uuid4().hex
        with pytest.raises(accounts.AccessDenied) as error:
            call(setup.gateway, token, "create_vod_clip", VOD_CLIP, request_id)
        assert "do-not-log" not in str(error.value)
        with pytest.raises(accounts.AccessDenied, match="already attempted"):
            call(setup.gateway, token, "create_vod_clip", VOD_CLIP, request_id)
    assert len(setup.service.calls) == 1


@pytest.mark.parametrize("code,status,expected", [
    ("missing_scope", 403, 403), ("reconnect_required", 401, 401), ("invalid_request", 400, 400),
    ("provider_unavailable", 503, 503), ("provider_not_configured", 409, 503), ("provider_response", 502, 502),
])
def test_host_account_errors_keep_their_status_and_fixed_message(setup, code, status, expected):
    from connected_accounts.models import AccountError

    def refuse(account_id, operation, **args):
        raise AccountError(code, "A fixed host message.", status=status)

    setup.service.perform = refuse
    with setup.gateway.run(setup.plugin, object(), 60) as env:
        with pytest.raises(accounts.AccessDenied) as error:
            call(setup.gateway, env[accounts.CAPABILITY_ENV])
    # 409 would read as "already attempted" to the SDK; nothing was.
    assert error.value.status == expected and str(error.value) == "A fixed host message."
    # The code goes along, so the SDK can tell a missing Twitch scope from a
    # run that isn't granted the operation (both 403).
    assert error.value.code == code


def test_the_route_sends_the_refusals_code_for_the_sdk(setup):
    from connected_accounts.models import AccountError

    def refuse(account_id, operation, **args):
        raise AccountError("missing_scope", "Reconnect this account with permission for this action.", status=403)

    app = FastAPI()
    gateway = accounts.install(app, data_dir=setup.data_dir, service=setup.service,
                               binding_for_video=lambda video: {"account_id": "twitch:42", "broadcaster_id": "42"})
    client = TestClient(app)
    with accounts.run_environment(setup.data_dir, setup.plugin, object(), 30) as env:
        headers = {accounts.HEADER: env[accounts.CAPABILITY_ENV]}

        def post(operation):
            return client.post(accounts.ROUTE, headers=headers, json={
                "operation": operation, "parameters": {}, "request_id": uuid.uuid4().hex})

        setup.service.perform = refuse
        scope = post("stream_context")
        assert scope.status_code == 403 and scope.headers[accounts.ERROR_HEADER] == "missing_scope"
        # An operation no run is granted: the gateway's own refusal.
        refused = post("not_an_operation")
        assert refused.status_code == 403 and refused.headers[accounts.ERROR_HEADER] == "not_permitted"
    gateway.close()


def test_a_refusal_code_is_only_ever_a_plain_word():
    assert accounts.AccessDenied("x", 412).code == "timeline_unverified"
    assert accounts.AccessDenied("x", 503, "provider_unavailable").code == "provider_unavailable"
    assert accounts.AccessDenied("x", 403, "Access_Token=secret").code == "not_permitted"
    assert accounts.AccessDenied("x", 418).code == "failed"


def test_no_binding_or_permissions_does_not_grant_account_access(setup):
    setup.gateway.binding_for_video = lambda video: None
    with setup.gateway.run(setup.plugin, object(), 60) as env:
        assert env == {}
    setup.plugin.manifest["permissions"] = []
    setup.gateway.binding_for_video = lambda video: (_ for _ in ()).throw(AssertionError("must not resolve"))
    with setup.gateway.run(setup.plugin, object(), 60) as env:
        assert env == {}


def test_unknown_source_timing_refuses_link_and_native_vod_creation(setup):
    setup.gateway.timeline_mapper = None
    with setup.gateway.run(setup.plugin, object(), 60) as env:
        token = env[accounts.CAPABILITY_ENV]
        for operation, params in (("vod_link", {"seconds": 12}),
                                  ("create_vod_clip", {"start_seconds": 10, "end_seconds": 30,
                                                       "title": "A grounded highlight"})):
            with pytest.raises(accounts.AccessDenied) as error:
                call(setup.gateway, token, operation, params)
            # Not 409, which the SDK reads as "already attempted".
            assert error.value.status == 412 and "verified" in str(error.value)
    assert setup.service.calls == []


def test_vod_clip_times_snap_to_twitch_whole_second_end(setup):
    setup.gateway.timeline_mapper = lambda binding, seconds: seconds + 90.5
    with setup.gateway.run(setup.plugin, object(), 60) as env:
        token = env[accounts.CAPABILITY_ENV]
        call(setup.gateway, token, "create_vod_clip", {"start_seconds": 100, "end_seconds": 130,
                                                    "title": "A grounded highlight"})
        call(setup.gateway, token, "create_vod_clip", {"start_seconds": 12.34, "end_seconds": 40.81,
                                                    "title": "Transcript times"})
        with pytest.raises(accounts.AccessDenied) as short:
            call(setup.gateway, token, "create_vod_clip", {"start_seconds": 10, "end_seconds": 14.4,
                                                        "title": "Too short"})
        assert short.value.status == 400
    first, second = (args for _, _, args in setup.service.calls)
    assert (first["start_seconds"], first["end_seconds"]) == (190.5, 221.0)
    assert (second["start_seconds"], second["end_seconds"]) == (102.8, 131.0)
    for args in (first, second):
        assert args["end_seconds"] == int(args["end_seconds"])
        assert 5 <= round(args["end_seconds"] - args["start_seconds"], 1) <= 60


def test_timestamps_outside_the_verified_vod_are_a_bad_request(setup):
    def mapper(binding, seconds):
        error = ValueError("outside")
        error.code = "invalid_timestamp"
        raise error

    setup.gateway.timeline_mapper = mapper
    with setup.gateway.run(setup.plugin, object(), 60) as env:
        with pytest.raises(accounts.AccessDenied) as error:
            call(setup.gateway, env[accounts.CAPABILITY_ENV], "vod_link", {"seconds": 12})
    assert error.value.status == 400 and setup.service.calls == []


def test_source_timing_is_applied_before_vod_requests(setup):
    setup.gateway.timeline_mapper = lambda binding, seconds: seconds + 100
    with setup.gateway.run(setup.plugin, object(), 60) as env:
        token = env[accounts.CAPABILITY_ENV]
        call(setup.gateway, token, "vod_link", {"seconds": 12})
        call(setup.gateway, token, "create_vod_clip", {"start_seconds": 10, "end_seconds": 30,
                                                    "title": "A grounded highlight"})
    assert setup.service.calls == [
        ("twitch:42", "vod_link", {"video_id": "456", "seconds": 112}),
        ("twitch:42", "create_vod_clip", {"broadcaster_id": "42", "video_id": "456",
                                        "start_seconds": 110, "end_seconds": 130,
                                        "title": "A grounded highlight"}),
    ]


def test_source_info_and_audience_are_narrow_host_callbacks(setup):
    setup.gateway.binding_for_video = lambda video: {
        "account_id": "twitch:42", "broadcaster_id": "42", "video_id": "456", "duration": 300,
        "source_id": "sha256:example", "path": "/private/path/never-hand-over.mp4",
    }
    seen = []

    def audience(binding):
        seen.append(binding)
        return {"status": "available", "nominations": [{"offset_seconds": 12, "count": 1,
                                                       "commands": {"chaos": 1}}]}

    setup.gateway.audience_for_binding = audience
    with setup.gateway.run(setup.plugin, object(), 60) as env:
        token = env[accounts.CAPABILITY_ENV]
        info = call(setup.gateway, token, "source_info")
        result = call(setup.gateway, token, "audience_signals")
        with pytest.raises(accounts.AccessDenied):
            call(setup.gateway, token, "audience_signals", {"account_id": "twitch:someone-else"})
    assert info == {"video_id": "456", "duration": 300, "source_id": "sha256:example"}
    assert result["nominations"][0]["commands"] == {"chaos": 1}
    assert seen[0]["account_id"] == "twitch:42" and "path" not in seen[0]
    assert setup.service.calls == []


def test_runs_cannot_clip_the_live_broadcast_and_vod_clips_are_limited(setup):
    with setup.gateway.run(setup.plugin, object(), 60) as env:
        token = env[accounts.CAPABILITY_ENV]
        # A run works on a finished VOD: a live clip of whatever is on air now
        # is not source-bound, so the gateway does not offer it.
        with pytest.raises(accounts.AccessDenied) as live:
            call(setup.gateway, token, "create_clip")
        assert live.value.status == 403
        for _ in range(10):
            call(setup.gateway, token, "create_vod_clip", VOD_CLIP)
        with pytest.raises(accounts.AccessDenied) as limited:
            call(setup.gateway, token, "create_vod_clip", VOD_CLIP)
        assert limited.value.status == 429
    assert [operation for _, operation, _ in setup.service.calls] == ["create_vod_clip"] * 10


class EventManager:
    def __init__(self):
        self.started, self.released = [], []

    def start(self, account_id, **kwargs):
        self.started.append((account_id, kwargs))
        return {"status": "connected"}

    def release(self, account_id, consumer):
        self.released.append((account_id, consumer))

    def events(self, account_id, **kwargs):
        return {"events": [{"type": "stream.online", "id": 1}, {"type": "channel.chat.message", "id": 2}], "cursor": 2}


def test_event_chat_reads_need_extra_permission_and_are_filtered(setup):
    setup.gateway.event_manager = EventManager()
    with setup.gateway.run(setup.plugin, object(), 60) as env:
        token = env[accounts.CAPABILITY_ENV]
        with pytest.raises(accounts.AccessDenied):
            call(setup.gateway, token, "events_start", {"event_types": ["channel.chat.message"]})
        result = call(setup.gateway, token, "events")
        assert result == {"events": [{"type": "stream.online", "id": 1}], "cursor": 2}


def test_a_runs_event_subscription_ends_with_the_run(setup):
    manager = setup.gateway.event_manager = EventManager()
    with setup.gateway.run(setup.plugin, object(), 60) as env:
        call(setup.gateway, env[accounts.CAPABILITY_ENV], "events_start", {"event_types": ["stream.online"]})
        assert manager.released == []
    (account_id, kwargs), = manager.started
    assert account_id == "twitch:42" and kwargs["event_types"] == ["stream.online"]
    assert manager.released == [("twitch:42", kwargs["consumer"])]
    # A run that never started events releases nothing.
    with setup.gateway.run(setup.plugin, object(), 60) as env:
        call(setup.gateway, env[accounts.CAPABILITY_ENV])
    assert len(manager.released) == 1


def test_http_route_rejects_desktop_session_as_a_plugin_capability(setup):
    app = FastAPI()
    gateway = accounts.install(app, data_dir=setup.data_dir, service=setup.service,
                               binding_for_video=lambda video: {"account_id": "twitch:42", "broadcaster_id": "42"})
    client = TestClient(app)
    body = {"operation": "stream_context", "parameters": {}, "request_id": uuid.uuid4().hex}
    assert client.post(accounts.ROUTE, json=body, headers={"X-Clips-Kitty-Session": "desktop-secret"}).status_code == 403
    with accounts.run_environment(setup.data_dir, setup.plugin, object(), 30) as env:
        response = client.post(accounts.ROUTE, json=body, headers={accounts.HEADER: env[accounts.CAPABILITY_ENV]})
        assert response.status_code == 200
        assert "access_token" not in response.text
    gateway.close()
    with accounts.run_environment(setup.data_dir, setup.plugin, object(), 30) as env:
        assert env == {}


def test_runner_injects_only_runtime_capability_and_revokes_on_process_failure(setup, monkeypatch, tmp_path):
    from plugins import runner
    from plugins._sdk import host

    app = FastAPI()
    gateway = accounts.install(app, data_dir=setup.data_dir, service=setup.service,
                               binding_for_video=lambda video: {"account_id": "twitch:42", "broadcaster_id": "42"})
    monkeypatch.setenv("CLIPS_KITTY_SESSION_SECRET", "desktop-session-not-for-plugin")
    monkeypatch.setenv("TWITCH_ACCESS_TOKEN", "provider-token-not-for-plugin")
    seen = []
    failure = [False]

    def run_plugin(*args, env, **kwargs):
        assert "CLIPS_KITTY_SESSION_SECRET" not in env and "TWITCH_ACCESS_TOKEN" not in env
        token = env[accounts.CAPABILITY_ENV]
        seen.append(token)
        call(gateway, token)
        assert not (tmp_path / "job.json").exists()
        if failure[0]:
            raise RuntimeError("child-process launcher failed")
        return SimpleNamespace(ok=True, cancelled=False)

    monkeypatch.setattr(host, "run_plugin", run_plugin)
    kwargs = {"video": SimpleNamespace(video_id="tw_456"), "data_dir": setup.data_dir, "stage": "analyze", "timeout": 30}
    runner._execute(setup.plugin, ["unused"], None, tmp_path, **kwargs)
    with pytest.raises(accounts.AccessDenied):
        call(gateway, seen[-1])
    failure[0] = True
    with pytest.raises(RuntimeError):
        runner._execute(setup.plugin, ["unused"], None, tmp_path, **kwargs)
    with pytest.raises(accounts.AccessDenied):
        call(gateway, seen[-1])
    gateway.close()
