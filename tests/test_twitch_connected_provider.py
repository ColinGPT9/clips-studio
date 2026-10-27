"""Offline contract tests: no live Twitch account or credentials are used."""

from __future__ import annotations

import json

import pytest
import requests

from connected_accounts.models import AccountError
from connected_accounts.twitch import TwitchProvider


class Reply:
    def __init__(self, status=200, data=None):
        self.status_code = status
        self.data = data if data is not None else {}

    def json(self):
        if isinstance(self.data, Exception):
            raise self.data
        return self.data


class Session:
    def __init__(self, *replies):
        self.replies = list(replies)
        self.calls = []
        self.close_count = 0

    def close(self):
        self.close_count += 1

    def request(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        if not self.replies:
            raise AssertionError("Unexpected network request")
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply


def account(*scopes):
    return {
        "id": "twitch:42",
        "user_id": "42",
        "provider": "twitch",
        "client_id": "public-client",
        "scopes": list(scopes),
    }


def test_public_channel_login_resolves_to_provider_verified_id():
    session = Session(
        Reply(data={"data": [{"id": "99", "login": "zackrawrr", "display_name": "Zack"}]}),
        Reply(data={"data": []}), Reply(data={"data": []}),
    )
    provider = TwitchProvider("public-client", session=session)
    result = provider.perform("TOKEN", account(), "stream_context", broadcaster_login="zackrawrr")
    assert result["broadcaster"]["id"] == "99"
    assert session.calls[0][2]["params"] == {"login": "zackrawrr"}
    assert session.calls[1][2]["params"]["user_id"] == "99"


@pytest.mark.parametrize("login", ["https://evil.example/", "bad/name", "", "x?access_token=y"])
def test_public_channel_lookup_rejects_non_login_input_without_network(login):
    session = Session()
    with pytest.raises(AccountError):
        TwitchProvider("public-client", session=session).perform(
            "TOKEN", account(), "stream_context", broadcaster_login=login)
    assert not session.calls


def token():
    return {
        "access_token": "ACCESS-MUST-NOT-LEAK",
        "refresh_token": "REFRESH-MUST-NOT-LEAK",
        "scope": ["clips:edit"],
        "expires_in": 3600,
        "token_type": "bearer",
    }


def test_unconfigured_provider_can_be_listed_but_never_uses_network():
    session = Session()
    provider = TwitchProvider("", session=session)
    assert provider.client_id == ""
    for operation in [
        lambda: provider.begin([]),
        lambda: provider.poll("DEVICE"),
        lambda: provider.refresh("REFRESH"),
    ]:
        with pytest.raises(AccountError) as exc:
            operation()
        assert exc.value.code == "provider_not_configured"
    assert session.calls == []


def test_provider_close_releases_session_once_and_prevents_new_requests():
    session = Session()
    provider = TwitchProvider("public-client", session=session)
    provider.close()
    provider.close()
    assert session.close_count == 1
    with pytest.raises(AccountError) as exc:
        provider.poll("DEVICE")
    assert exc.value.code == "provider_unavailable"
    assert session.calls == []


def test_provider_close_does_not_expose_transport_error():
    class BrokenClose(Session):
        def close(self):
            raise RuntimeError("ACCESS-MUST-NOT-LEAK")

    provider = TwitchProvider("public-client", session=BrokenClose())
    provider.close()  # must not raise
    with pytest.raises(AccountError) as error:
        provider.validate("TOKEN")
    assert error.value.code == "provider_unavailable" and "ACCESS-MUST-NOT-LEAK" not in str(error.value)


def test_public_device_flow_has_no_secret_and_fixed_trusted_browser_url():
    session = Session(
        Reply(
            data={
                "device_code": "DEVICE",
                "user_code": "ABCD",
                "verification_uri": "https://www.twitch.tv/activate",
                "expires_in": 1800,
                "interval": 5,
            }
        ),
        Reply(data=token()),
    )
    provider = TwitchProvider("public-client", session=session)
    grant = provider.begin(["clips:edit", "user:read:chat"])
    assert grant["user_code"] == "ABCD"
    assert grant["interval"] == 5
    assert provider.poll(grant["device_code"])["access_token"] == "ACCESS-MUST-NOT-LEAK"
    request = session.calls[0]
    assert request[:2] == ("POST", "https://id.twitch.tv/oauth2/device")
    assert request[2]["data"] == {"client_id": "public-client", "scopes": "clips:edit user:read:chat"}
    assert session.calls[1][2]["data"]["grant_type"] == "urn:ietf:params:oauth:grant-type:device_code"
    assert all(call[2]["allow_redirects"] is False for call in session.calls)
    assert all(call[2]["timeout"] == (5, 20) for call in session.calls)
    assert "client_secret" not in json.dumps(session.calls)
    assert provider.capabilities["pkce"] is False


@pytest.mark.parametrize(
    "uri",
    [
        "https://attacker.test/activate",
        "http://www.twitch.tv/activate",
        "https://www.twitch.tv@attacker.test/activate",
        "https://www.twitch.tv:8443/activate",
        "https://[broken",
    ],
)
def test_device_browser_url_cannot_be_arbitrary_redirect(uri):
    session = Session(
        Reply(
            data={
                "device_code": "DEVICE",
                "user_code": "ABCD",
                "verification_uri": uri,
                "expires_in": 1800,
                "interval": 5,
            }
        )
    )
    with pytest.raises(AccountError, match="verification page"):
        TwitchProvider("public-client", session=session).begin([])


@pytest.mark.parametrize(
    ("payload", "code"),
    [
        ({"message": "authorization_pending"}, "authorization_pending"),
        ({"error": "slow_down"}, "slow_down"),
        ({"message": "access_denied"}, "access_denied"),
        ({"message": "expired_token"}, "expired_token"),
        ({"message": "invalid_device_code"}, "expired_token"),
        ({"error": "invalid_grant"}, "invalid_grant"),
        ({"message": "Invalid refresh token"}, "invalid_grant"),
    ],
)
def test_oauth_errors_are_classified_without_echoing_provider_body(payload, code):
    session = Session(Reply(400, {**payload, "access_token": "ACCESS-MUST-NOT-LEAK"}))
    with pytest.raises(AccountError) as exc:
        TwitchProvider("public-client", session=session).poll("DEVICE")
    assert exc.value.code == code
    assert "ACCESS-MUST-NOT-LEAK" not in str(exc.value)


@pytest.mark.parametrize(
    "reply",
    [
        Reply(302, {"message": "ACCESS-MUST-NOT-LEAK"}),
        Reply(500, {"message": "ACCESS-MUST-NOT-LEAK"}),
        requests.ConnectionError("ACCESS-MUST-NOT-LEAK"),
        Reply(400, ValueError("ACCESS-MUST-NOT-LEAK")),
    ],
)
def test_unknown_failures_never_echo_secret_or_follow_redirects(reply):
    with pytest.raises(AccountError) as exc:
        TwitchProvider("public-client", session=Session(reply)).poll("DEVICE")
    assert "ACCESS-MUST-NOT-LEAK" not in str(exc.value)


def test_refresh_rotates_public_client_tokens_without_secret():
    session = Session(Reply(data={**token(), "refresh_token": "ROTATED"}))
    refreshed = TwitchProvider("public-client", session=session).refresh("OLD")
    assert refreshed["refresh_token"] == "ROTATED"
    assert session.calls[0][2]["data"] == {
        "client_id": "public-client",
        "refresh_token": "OLD",
        "grant_type": "refresh_token",
    }


def test_validate_rejects_another_client_and_uses_oauth_header():
    session = Session(
        Reply(
            data={
                "client_id": "different",
                "user_id": "42",
                "login": "streamer",
                "scopes": [],
                "expires_in": 900,
            }
        )
    )
    with pytest.raises(AccountError) as exc:
        TwitchProvider("public-client", session=session).validate("ACCESS-MUST-NOT-LEAK")
    assert exc.value.code == "invalid_token"
    assert session.calls[0][2]["headers"]["Authorization"] == "OAuth ACCESS-MUST-NOT-LEAK"


def test_validate_and_identity_only_return_identity_metadata():
    session = Session(
        Reply(
            data={
                "client_id": "public-client",
                "user_id": "42",
                "login": "streamer",
                "scopes": ["clips:edit"],
                "expires_in": 900,
                "access_token": "ACCESS-MUST-NOT-LEAK",
            }
        ),
        Reply(
            data={
                "data": [
                    {
                        "id": "42",
                        "login": "streamer",
                        "display_name": "Streamer",
                        "email": "private@example.com",
                    }
                ]
            }
        ),
    )
    provider = TwitchProvider("public-client", session=session)
    validated = provider.validate("TOKEN")
    identity = provider.identity("TOKEN")
    assert validated["user_id"] == "42"
    assert identity == {"id": "42", "login": "streamer", "display_name": "Streamer"}
    assert "ACCESS-MUST-NOT-LEAK" not in json.dumps(validated)
    assert session.calls[1][2]["headers"]["Authorization"] == "Bearer TOKEN"
    assert session.calls[1][2]["headers"]["Client-Id"] == "public-client"


def test_missing_clip_permission_is_checked_before_network():
    session = Session()
    with pytest.raises(AccountError) as exc:
        TwitchProvider("public-client", session=session).perform("TOKEN", account(), "create_clip")
    assert exc.value.code == "missing_scope"
    assert session.calls == []


def test_native_clip_returns_only_share_link_and_pending_creation_status():
    session = Session(Reply(202, {"data": [{"id": "New-Clip_123", "edit_url": "SECRET-EDIT-URL"}]}))
    result = TwitchProvider("public-client", session=session).perform(
        "TOKEN", account("clips:edit"), "create_clip", broadcaster_id="42"
    )
    assert result == {
        "kind": "native_clip",
        "id": "New-Clip_123",
        "status": "processing",
        "url": "https://clips.twitch.tv/New-Clip_123",
    }
    assert "SECRET-EDIT-URL" not in json.dumps(result)
    assert session.calls[0][2]["params"]["broadcaster_id"] == "42"


def test_vod_timestamp_verifies_broadcaster_instead_of_creating_historic_native_clip():
    session = Session(Reply(data={"data": [{"id": "987", "user_id": "42"}]}))
    result = TwitchProvider("public-client", session=session).perform(
        "TOKEN", account(), "vod_link", video_id="987", seconds=3661.9
    )
    assert result == {
        "kind": "timestamp",
        "video_id": "987",
        "seconds": 3661,
        "broadcaster_id": "42",
        "stream_id": None,
        "duration": "",
        "duration_seconds": None,
        "type": "",
        "url": "https://www.twitch.tv/videos/987?t=1h1m1s",
    }
    assert session.calls[0][0] == "GET"
    session = Session(Reply(data={"data": [{"id": "987", "user_id": "99"}]}))
    with pytest.raises(AccountError) as exc:
        TwitchProvider("public-client", session=session).perform(
            "TOKEN", account(), "vod_link", video_id="987"
        )
    assert exc.value.code == "permission_denied"


@pytest.mark.parametrize("seconds", [True, -1, float("inf"), float("nan"), "50"])
def test_vod_timestamp_rejects_unbounded_or_invalid_values_before_network(seconds):
    with pytest.raises(AccountError) as exc:
        TwitchProvider("public-client", session=Session()).perform(
            "TOKEN", account(), "vod_link", video_id="987", seconds=seconds
        )
    assert exc.value.code == "invalid_request"


def test_chat_sender_is_always_token_subject_and_result_is_sanitized():
    session = Session(
        Reply(
            data={
                "data": [{"message_id": "message", "is_sent": True, "access_token": "ACCESS-MUST-NOT-LEAK"}]
            }
        )
    )
    result = TwitchProvider("public-client", session=session).perform(
        "TOKEN", account("user:write:chat"), "send_chat", broadcaster_id="42", message="Hello"
    )
    assert result == {"message_id": "message", "is_sent": True}
    assert session.calls[0][2]["json"] == {"broadcaster_id": "42", "sender_id": "42", "message": "Hello"}


def test_sender_spoofing_and_historical_native_clip_parameters_are_rejected():
    provider = TwitchProvider("public-client", session=Session())
    for operation, params in [
        ("send_chat", {"message": "Hi", "sender_id": "99"}),
        ("create_clip", {"video_id": "987", "seconds": 60}),
    ]:
        with pytest.raises(AccountError) as exc:
            provider.perform("TOKEN", account("clips:edit", "user:write:chat"), operation, **params)
        assert exc.value.code == "invalid_request"


def test_oauth_http_slow_down_preserves_flow_polling_semantics():
    with pytest.raises(AccountError) as exc:
        TwitchProvider("public-client", session=Session(Reply(429, {"error": "slow_down"}))).poll("DEVICE")
    assert exc.value.code == "slow_down"


def test_eventsub_is_bound_to_host_session_connected_broadcaster_and_chat_scope():
    session = Session(Reply(202, {"data": [{"id": "sub-id", "status": "enabled"}]}))
    result = TwitchProvider("public-client", session=session).perform(
        "TOKEN",
        account("user:read:chat"),
        "subscribe_events",
        broadcaster_id="42",
        event_types=["channel.chat.message"],
        session_id="host-session",
    )
    assert result == {
        "subscriptions": [{"id": "sub-id", "type": "channel.chat.message", "status": "enabled"}]
    }
    body = session.calls[0][2]["json"]
    assert body == {
        "type": "channel.chat.message",
        "version": "1",
        "condition": {"broadcaster_user_id": "42", "user_id": "42"},
        "transport": {"method": "websocket", "session_id": "host-session"},
    }
    public = Session(Reply(202, {"data": [{"id": "public-sub", "status": "enabled"}]}))
    TwitchProvider("public-client", session=public).perform(
        "TOKEN", account("user:read:chat"), "subscribe_events", broadcaster_id="99",
        event_types=["channel.chat.message"], session_id="host-session",
    )
    assert public.calls[0][2]["json"]["condition"] == {"broadcaster_user_id": "99", "user_id": "42"}
    with pytest.raises(AccountError) as exc:
        TwitchProvider("public-client", session=Session()).perform(
            "TOKEN",
            account(),
            "subscribe_events",
            broadcaster_id="42",
            event_types=["channel.chat.message"],
            session_id="host-session",
        )
    assert exc.value.code == "missing_scope"


def test_unknown_operations_and_arbitrary_event_types_fail_closed():
    provider = TwitchProvider("public-client", session=Session())
    with pytest.raises(AccountError) as exc:
        provider.perform("TOKEN", account(), "https://attacker.test/")
    assert exc.value.code == "unsupported_operation"
    with pytest.raises(AccountError) as exc:
        provider.perform(
            "TOKEN", account(), "subscribe_events", session_id="host", event_types=["channel.ban"]
        )
    assert exc.value.code == "invalid_request"


def test_videos_use_fixed_urls_and_bounded_listing():
    session = Session(
        Reply(
            data={
                "data": [
                    {
                        "id": "987",
                        "user_id": "42",
                        "title": "A VOD",
                        "url": "https://attacker.test/",
                        "created_at": "2026-10-23T12:00:00Z",
                        "duration": "2h5m",
                        "view_count": 12,
                    }
                ]
            }
        )
    )
    result = TwitchProvider("public-client", session=session).perform("TOKEN", account(), "videos", first=15)
    assert result["videos"][0]["url"] == "https://www.twitch.tv/videos/987"
    assert session.calls[0][2]["params"] == {"user_id": "42", "type": "archive", "first": 15}
    with pytest.raises(AccountError):
        TwitchProvider("public-client", session=Session()).perform("TOKEN", account(), "videos", first=1000)


def test_revoke_posts_token_only_to_fixed_twitch_endpoint():
    session = Session(Reply())
    assert TwitchProvider("public-client", session=session).revoke("TOKEN") is None
    assert session.calls[0][:2] == ("POST", "https://id.twitch.tv/oauth2/revoke")
    assert session.calls[0][2]["data"] == {"client_id": "public-client", "token": "TOKEN"}


def archive(**overrides):
    return {
        "id": "987",
        "user_id": "42",
        "stream_id": "12345",
        "type": "archive",
        "duration": "2h5m",
        "created_at": "2026-10-24T10:00:00Z",
        "published_at": "2026-10-24T10:00:10Z",
        **overrides,
    }


def clip(**overrides):
    return {
        "id": "Returned-Clip",
        "broadcaster_id": "42",
        "video_id": "987",
        "url": "https://clips.twitch.tv/Returned-Clip",
        "vod_offset": 100,
        "duration": 30.0,
        **overrides,
    }


@pytest.mark.parametrize("grant", ["channel:manage:clips", "editor:manage:clips"])
def test_native_vod_clip_uses_either_scope_and_query_parameters_after_ownership_lookup(grant):
    session = Session(
        Reply(data={"data": [archive()]}),
        Reply(
            202, {"data": [{"id": "Returned-Clip", "edit_url": "SECRET-EDIT-URL", "access_token": "SECRET"}]}
        ),
    )
    provider = TwitchProvider("public-client", session=session)
    result = provider.perform(
        "TOKEN",
        account(grant),
        "create_vod_clip",
        video_id="987",
        start_seconds=100.1,
        end_seconds=130,
        title="A factual chase",
    )
    assert result == {
        "kind": "native_vod_clip",
        "id": "Returned-Clip",
        "status": "processing",
        "video_id": "987",
        "broadcaster_id": "42",
        "stream_id": "12345",
        "start_seconds": 100.1,
        "end_seconds": 130,
        "duration": 29.9,
    }
    assert session.calls[0][:2] == ("GET", "https://api.twitch.tv/helix/videos")
    assert session.calls[0][2]["params"] == {"id": "987"}
    assert session.calls[1][:2] == ("POST", "https://api.twitch.tv/helix/videos/clips")
    assert session.calls[1][2]["params"] == {
        "editor_id": "42",
        "broadcaster_id": "42",
        "vod_id": "987",
        "vod_offset": 130,
        "duration": 29.9,
        "title": "A factual chase",
    }
    assert "json" not in session.calls[1][2]
    assert "url" not in result and "SECRET" not in json.dumps(result)
    assert provider.operation_scopes["create_vod_clip"] == ()
    assert {"channel:manage:clips", "editor:manage:clips"} <= provider.supported_scopes
    assert provider.default_scopes == ()


def test_vod_clip_requires_manage_scope_and_connected_broadcaster_before_network():
    session = Session()
    provider = TwitchProvider("public-client", session=session)
    for operation, scopes, params, code in [
        ("create_vod_clip", ("clips:edit",), {}, "missing_scope"),
        ("create_vod_clip", ("channel:manage:clips",), {"broadcaster_id": "99"}, "permission_denied"),
        ("get_clip", (), {"broadcaster_id": "99", "clip_id": "Returned-Clip"}, "permission_denied"),
    ]:
        with pytest.raises(AccountError) as exc:
            provider.perform("TOKEN", account(*scopes), operation, **params)
        assert exc.value.code == code
    assert session.calls == []


@pytest.mark.parametrize(
    "changes",
    [
        {"start_seconds": True},
        {"start_seconds": "100"},
        {"start_seconds": -1},
        {"end_seconds": float("nan")},
        {"end_seconds": float("inf")},
        {"end_seconds": 10**400},
        {"end_seconds": 130.5},
        {"start_seconds": 126},
        {"start_seconds": 69},
        {"start_seconds": 100.01},
        {"title": ""},
        {"title": " "},
        {"title": "x" * 141},
        {"title": "Bad\nTitle"},
        {"title": False},
        {"video_id": "not-a-video"},
        {"stream_id": "12345"},
        {"editor_id": "99"},
    ],
)
def test_invalid_vod_clip_inputs_fail_before_network(changes):
    session = Session()
    params = {
        "video_id": "987",
        "start_seconds": 100,
        "end_seconds": 130,
        "title": "A factual title",
        **changes,
    }
    with pytest.raises(AccountError) as exc:
        TwitchProvider("public-client", session=session).perform(
            "TOKEN", account("channel:manage:clips"), "create_vod_clip", **params
        )
    assert exc.value.code == "invalid_request"
    assert session.calls == []


@pytest.mark.parametrize(
    ("rows", "code"),
    [
        ([], "not_found"),
        ([archive(user_id="99")], "permission_denied"),
        ([archive(id="999")], "provider_response"),
        ([archive(type="highlight")], "provider_response"),
        ([archive(stream_id=None)], "provider_response"),
        ([archive(duration="unknown")], "provider_response"),
        ([archive(duration="1m")], "invalid_request"),
    ],
)
def test_vod_clip_never_posts_without_verified_own_archive_identity_and_available_range(rows, code):
    session = Session(Reply(data={"data": rows}))
    with pytest.raises(AccountError) as exc:
        TwitchProvider("public-client", session=session).perform(
            "TOKEN",
            account("channel:manage:clips"),
            "create_vod_clip",
            video_id="987",
            start_seconds=100,
            end_seconds=130,
            title="A factual title",
        )
    assert exc.value.code == code
    assert len(session.calls) == 1 and session.calls[0][0] == "GET"


@pytest.mark.parametrize(
    ("status", "code"),
    [(403, "permission_denied"), (404, "not_found"), (429, "rate_limited"), (503, "provider_unavailable")],
)
def test_vod_clip_disabled_unavailable_and_rate_limits_do_not_retry_write_or_leak(status, code):
    session = Session(Reply(data={"data": [archive()]}), Reply(status, {"message": "SECRET-UPSTREAM"}))
    with pytest.raises(AccountError) as exc:
        TwitchProvider("public-client", session=session).perform(
            "TOKEN",
            account("editor:manage:clips"),
            "create_vod_clip",
            video_id="987",
            start_seconds=100,
            end_seconds=130,
            title="A factual title",
        )
    assert exc.value.code == code and "SECRET" not in str(exc.value)
    assert len(session.calls) == 2


def test_get_clip_is_pending_then_returns_only_the_verified_public_url_and_source():
    session = Session(
        Reply(data={"data": []}),
        Reply(data={"data": [clip(url="https://www.twitch.tv/streamer/clip/Returned-Clip?tracking=SECRET")]}),
        Reply(data={"data": [archive()]}),
    )
    provider = TwitchProvider("public-client", session=session)
    assert provider.perform("TOKEN", account(), "get_clip", clip_id="Returned-Clip") == {
        "kind": "native_vod_clip",
        "id": "Returned-Clip",
        "status": "pending",
    }
    result = provider.perform("TOKEN", account(), "get_clip", clip_id="Returned-Clip", video_id="987")
    assert result == {
        "kind": "native_vod_clip",
        "id": "Returned-Clip",
        "status": "ready",
        "url": "https://www.twitch.tv/streamer/clip/Returned-Clip",
        "broadcaster_id": "42",
        "video_id": "987",
        "vod_offset": 100,
        "duration": 30.0,
        "stream_id": "12345",
    }
    assert session.calls[0][:2] == ("GET", "https://api.twitch.tv/helix/clips")
    assert session.calls[0][2]["params"] == {"id": "Returned-Clip"}
    assert "SECRET" not in json.dumps(result)


def test_get_clip_with_pending_vod_association_does_not_guess_offset_or_source():
    session = Session(Reply(data={"data": [clip(video_id="", vod_offset=None)]}))
    result = TwitchProvider("public-client", session=session).perform(
        "TOKEN",
        account(),
        "get_clip",
        clip_id="Returned-Clip",
        video_id="987",
    )
    assert result["status"] == "pending" and "url" not in result
    assert len(session.calls) == 1


def test_get_clip_can_return_public_clip_after_original_vod_is_deleted():
    session = Session(Reply(data={"data": [clip(video_id="", vod_offset=None)]}))
    result = TwitchProvider("public-client", session=session).perform(
        "TOKEN", account(), "get_clip", clip_id="Returned-Clip"
    )
    assert result["status"] == "ready" and result["video_id"] == ""
    assert result["vod_offset"] is None and result["stream_id"] is None


@pytest.mark.parametrize(
    ("changes", "code"),
    [
        ({"broadcaster_id": "99"}, "permission_denied"),
        ({"video_id": "999"}, "permission_denied"),
        ({"id": "Other-Clip"}, "provider_response"),
        ({"url": "https://attacker.test/Returned-Clip"}, "provider_response"),
        ({"url": "https://clips.twitch.tv/Other-Clip"}, "provider_response"),
        ({"url": "https://clips.twitch.tv:8443/Returned-Clip"}, "provider_response"),
        ({"vod_offset": -1}, "provider_response"),
        ({"duration": float("nan")}, "provider_response"),
    ],
)
def test_get_clip_checks_channel_expected_vod_returned_id_and_public_url(changes, code):
    session = Session(Reply(data={"data": [clip(**changes)]}), Reply(data={"data": [archive()]}))
    with pytest.raises(AccountError) as exc:
        TwitchProvider("public-client", session=session).perform(
            "TOKEN", account(), "get_clip", clip_id="Returned-Clip", video_id="987"
        )
    assert exc.value.code == code


def test_vod_link_metadata_comes_from_helix_not_creation_date_assumptions():
    session = Session(Reply(data={"data": [archive()]}))
    result = TwitchProvider("public-client", session=session).perform(
        "TOKEN", account(), "vod_link", video_id="987", seconds=45
    )
    assert result["stream_id"] == "12345" and result["duration_seconds"] == 7500
    assert result["duration"] == "2h5m" and result["type"] == "archive"
    session = Session(Reply(data={"data": [archive()]}))
    video = TwitchProvider("public-client", session=session).perform("TOKEN", account(), "videos")["videos"][
        0
    ]
    assert video["user_id"] == "42" and video["stream_id"] == "12345"
    assert video["created_at"] != video["published_at"]


@pytest.mark.parametrize("duration", [5, 60])
def test_native_vod_clip_accepts_documented_duration_boundaries(duration):
    session = Session(Reply(data={"data": [archive()]}), Reply(202, {"data": [{"id": "Returned-Clip"}]}))
    result = TwitchProvider("public-client", session=session).perform(
        "TOKEN",
        account("channel:manage:clips"),
        "create_vod_clip",
        video_id="987",
        start_seconds=100,
        end_seconds=100 + duration,
        title="A factual title",
    )
    assert result["duration"] == duration


def test_get_clip_expected_vod_requires_an_archive_stream_not_video_creation_time():
    session = Session(
        Reply(data={"data": [clip()]}),
        Reply(data={"data": [archive(type="upload", stream_id="12345")]}),
    )
    with pytest.raises(AccountError) as exc:
        TwitchProvider("public-client", session=session).perform(
            "TOKEN",
            account(),
            "get_clip",
            clip_id="Returned-Clip",
            video_id="987",
        )
    assert exc.value.code == "provider_response"
