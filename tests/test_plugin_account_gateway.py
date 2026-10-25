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
            call(setup.gateway, env[accounts.CAPABILITY_ENV], "create_clip")
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
            call(setup.gateway, token, "create_clip")
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
    with pytest.raises(RuntimeError), setup.gateway.run(setup.plugin, object(), 30) as env:
        token = env[accounts.CAPABILITY_ENV]
        raise RuntimeError("process failed")
    with pytest.raises(accounts.AccessDenied):
        call(setup.gateway, token)
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
            futures = [pool.submit(call, setup.gateway, token, "create_clip", {}, request_id) for _ in range(2)]
            assert futures[0].result() == futures[1].result()
        assert len(setup.service.calls) == 1
        with pytest.raises(accounts.AccessDenied, match="already used"):
            call(setup.gateway, token, "send_chat", {"message": "hi"}, request_id)
        for _ in range(9):
            call(setup.gateway, token, "create_clip")
        with pytest.raises(accounts.AccessDenied) as limited:
            call(setup.gateway, token, "create_clip")
        assert limited.value.status == 429
        assert len(setup.service.calls) == 10


def test_ambiguous_write_failure_is_not_retried_and_error_has_no_credentials(setup):
    setup.service.fail = True
    with setup.gateway.run(setup.plugin, object(), 60) as env:
        token, request_id = env[accounts.CAPABILITY_ENV], uuid.uuid4().hex
        with pytest.raises(accounts.AccessDenied) as error:
            call(setup.gateway, token, "create_clip", {}, request_id)
        assert "do-not-log" not in str(error.value)
        with pytest.raises(accounts.AccessDenied, match="already attempted"):
            call(setup.gateway, token, "create_clip", {}, request_id)
    assert len(setup.service.calls) == 1


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
            assert error.value.status == 409
    assert setup.service.calls == []


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


def test_native_clip_write_limit_is_shared_by_live_and_vod_actions(setup):
    with setup.gateway.run(setup.plugin, object(), 60) as env:
        token = env[accounts.CAPABILITY_ENV]
        for _ in range(5):
            call(setup.gateway, token, "create_clip")
            call(setup.gateway, token, "create_vod_clip", {"start_seconds": 10, "end_seconds": 30,
                                                        "title": "A grounded highlight"})
        with pytest.raises(accounts.AccessDenied) as limited:
            call(setup.gateway, token, "create_clip")
        assert limited.value.status == 429
    assert len(setup.service.calls) == 10


def test_event_chat_reads_need_extra_permission_and_are_filtered(setup):
    class EventManager:
        def start(self, account_id, **kwargs):
            return {"status": "connected"}

        def events(self, account_id, **kwargs):
            return {"events": [{"type": "stream.online", "id": 1}, {"type": "channel.chat.message", "id": 2}], "cursor": 2}

    setup.gateway.event_manager = EventManager()
    with setup.gateway.run(setup.plugin, object(), 60) as env:
        token = env[accounts.CAPABILITY_ENV]
        with pytest.raises(accounts.AccessDenied):
            call(setup.gateway, token, "events_start", {"event_types": ["channel.chat.message"]})
        result = call(setup.gateway, token, "events")
        assert result == {"events": [{"type": "stream.online", "id": 1}], "cursor": 2}


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
