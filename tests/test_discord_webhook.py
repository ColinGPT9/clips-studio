"""Posting through a webhook (campaigns/discord_webhook.py): the request
Discord gets, what each answer means, and the token never coming back in a
result. Also the transport's address: a fake Discord is used only on this
PC's loopback.

No real Discord: a scripted transport, and once a local fake server.
"""

import http.client
import socket

import pytest
import requests
from discord_fake import (
    CHANNEL_ID,
    WEBHOOK_ID,
    WEBHOOK_TOKEN,
    FakeDiscordHTTP,
    FakeDiscordServer,
    FakeResponse,
)
from urllib3 import exceptions as u3

from campaigns import discord_http, discord_webhook
from campaigns.discord_http import DiscordError


@pytest.fixture(autouse=True)
def _real_discord_addresses(monkeypatch):
    monkeypatch.delenv(discord_http.API_ENV, raising=False)
    monkeypatch.delenv(discord_http.AUTHORIZE_ENV, raising=False)


def _send(*answers, **kw):
    http = FakeDiscordHTTP(*answers)
    result = discord_webhook.send(http, WEBHOOK_ID, WEBHOOK_TOKEN, content=kw.pop("content", "https://example.com/v/1"),
                                  **kw)
    return http, result


# ---- the request --------------------------------------------------------------------------


def test_a_message_waits_for_discords_answer_and_pings_nobody():
    http, result = _send((200, {"id": "600000000000000001", "channel_id": CHANNEL_ID}), content="@everyone look")
    call = http.calls[0]
    assert call["method"] == "POST"
    assert call["url"] == f"https://discord.com/api/v10/webhooks/{WEBHOOK_ID}/{WEBHOOK_TOKEN}"
    assert call["params"] == {"wait": "true"}
    assert call["json"] == {"content": "@everyone look", "allowed_mentions": {"parse": []}}
    assert (result.kind, result.message_id, result.channel_id, result.status) == (
        "ok", "600000000000000001", CHANNEL_ID, 200)


def test_a_forum_post_gets_a_thread_name_and_an_existing_thread_its_id():
    http, _ = _send((200, {"id": "1"}), thread_name="Example clip")
    assert http.calls[0]["json"]["thread_name"] == "Example clip"
    assert http.calls[0]["params"] == {"wait": "true"}
    http, _ = _send((200, {"id": "1"}), thread_name="Example clip", thread_id="700000000000000001")
    assert "thread_name" not in http.calls[0]["json"]
    assert http.calls[0]["params"] == {"wait": "true", "thread_id": "700000000000000001"}


# ---- what an answer means ---------------------------------------------------------------------


@pytest.mark.parametrize("status, body, kind", [
    (200, {"id": "1", "channel_id": "2"}, "ok"),
    (429, {"message": "You are being rate limited.", "retry_after": 1.5, "global": False}, "rate_limited"),
    (404, {"message": "Unknown Webhook", "code": 10015}, "gone"),
    (401, {"message": "Invalid Webhook Token", "code": 50027}, "gone"),
    (404, None, "gone"),
    (403, {"message": "Missing Permissions", "code": 50013}, "denied"),
    (401, {"message": "401: Unauthorized", "code": 0}, "denied"),
    (400, {"message": "Missing Permissions", "code": 50013}, "denied"),
    (400, {"message": "Webhooks posted to forum channels must have a thread_name or thread_id",
           "code": 220001}, "forum_needs_thread"),
    (400, {"message": "Invalid Form Body", "code": 50035}, "bad_request"),
    (413, None, "bad_request"),
    (500, None, "server_error"),
    (502, {"message": "Bad Gateway"}, "server_error"),
])
def test_each_answer_has_its_kind(status, body, kind):
    _, result = _send((status, body))
    assert result.kind == kind
    assert result.status == status
    if body and body.get("code"):
        assert result.code == body["code"]


def test_retry_after_comes_from_the_body_else_the_header():
    _, result = _send((429, {"retry_after": 2.25, "global": True}))
    assert (result.retry_after, result.is_global) == (2.25, True)
    _, result = _send((429, None, {"Retry-After": "7", "X-RateLimit-Global": "true"}))
    assert (result.retry_after, result.is_global) == (7.0, True)
    _, result = _send((429, {"message": "slow"}, {"Retry-After": "3", "X-RateLimit-Scope": "user"}))
    assert (result.retry_after, result.is_global) == (3.0, False)


@pytest.mark.parametrize("failure, kind", [
    (requests.ConnectTimeout("connect timed out"), "unreachable"),
    (requests.ConnectionError(u3.MaxRetryError(None, "/", u3.NewConnectionError(None, "refused"))), "unreachable"),
    (requests.ConnectionError(u3.MaxRetryError(None, "/", u3.NameResolutionError("discord.com", None,
                                                                                  socket.gaierror()))),
     "unreachable"),
    (requests.ConnectionError(u3.MaxRetryError(None, "/", ConnectionRefusedError(10061, "refused"))),
     "unreachable"),
    (requests.exceptions.SSLError("record layer failure"), "ambiguous"),
    (requests.ReadTimeout("read timed out"), "ambiguous"),
    (requests.ConnectionError(u3.ProtocolError("Connection aborted.",
                                               http.client.RemoteDisconnected("closed"))), "ambiguous"),
    (requests.exceptions.ChunkedEncodingError("broken"), "ambiguous"),
    (RuntimeError("anything else"), "ambiguous"),
])
def test_a_request_that_raised_is_never_sent_or_maybe_sent(failure, kind):
    _, result = _send(failure)
    assert result.kind == kind


# ---- the token stays out ---------------------------------------------------------------------------------


def test_the_token_never_comes_back_in_a_result():
    url = f"https://discord.com/api/v10/webhooks/{WEBHOOK_ID}/{WEBHOOK_TOKEN}?wait=true"
    answers = [
        (400, {"message": f"Bad request for {url}", "code": 50035}),
        (404, {"message": f"Unknown Webhook {WEBHOOK_TOKEN}", "code": 10015}),
        (403, {"message": WEBHOOK_TOKEN, "code": 50013}),
        (500, {"message": WEBHOOK_TOKEN}),
        requests.ConnectionError(f"HTTPSConnectionPool(host='discord.com'): Max retries exceeded with url: {url}"),
        requests.ReadTimeout(f"Read timed out: {url}"),
    ]
    for answer in answers:
        _, result = _send(answer)
        assert WEBHOOK_TOKEN not in repr(result), answer


@pytest.mark.parametrize("webhook_id, token", [
    ("../../users/@me", WEBHOOK_TOKEN),
    (WEBHOOK_ID, "../../oauth2/token"),
    (WEBHOOK_ID, "tok/en"),
    (WEBHOOK_ID, "tok?wait=false"),
    ("4000", WEBHOOK_TOKEN),
    (WEBHOOK_ID, ""),
])
def test_an_id_or_token_that_could_steer_the_request_is_never_sent(webhook_id, token):
    http = FakeDiscordHTTP()
    result = discord_webhook.send(http, webhook_id, token, content="x")
    assert result.kind == "gone" and http.calls == []
    with pytest.raises(DiscordError) as caught:
        discord_webhook.inspect(http, webhook_id, token)
    assert caught.value.code == "gone" and http.calls == []


# ---- inspect --------------------------------------------------------------------------------------------------


def test_inspect_says_where_a_webhook_posts_and_drops_its_token():
    body = {"id": WEBHOOK_ID, "type": 1, "guild_id": "300000000000000001", "channel_id": CHANNEL_ID,
            "name": "Example hook", "token": WEBHOOK_TOKEN}
    http = FakeDiscordHTTP(FakeResponse(200, body))
    got = discord_webhook.inspect(http, WEBHOOK_ID, WEBHOOK_TOKEN)
    assert got == {"id": WEBHOOK_ID, "guild_id": "300000000000000001", "channel_id": CHANNEL_ID,
                   "name": "Example hook"}
    assert http.calls[0]["method"] == "GET"
    assert http.calls[0]["url"] == f"https://discord.com/api/v10/webhooks/{WEBHOOK_ID}/{WEBHOOK_TOKEN}"


@pytest.mark.parametrize("answer, code", [
    ((404, {"message": "Unknown Webhook", "code": 10015}), "gone"),
    ((401, {"message": "Invalid Webhook Token", "code": 50027}), "gone"),
    ((429, {"retry_after": 1}), "rate_limited"),
    ((500, None), "bad_response"),
    (requests.ConnectTimeout(f"/webhooks/{WEBHOOK_ID}/{WEBHOOK_TOKEN}"), "network"),
])
def test_inspect_raises_for_a_webhook_it_cant_use(answer, code):
    with pytest.raises(DiscordError) as caught:
        discord_webhook.inspect(FakeDiscordHTTP(answer), WEBHOOK_ID, WEBHOOK_TOKEN)
    assert caught.value.code == code
    assert WEBHOOK_TOKEN not in str(caught.value)


# ---- the address --------------------------------------------------------------------------------------------


@pytest.mark.parametrize("value, used", [
    ("http://127.0.0.1:5005/api/v10", True),
    ("http://127.0.0.1:5005/api/v10/", True),
    ("http://127.0.0.1:5005", True),
    ("https://127.0.0.1:5005/api/v10", False),
    ("http://localhost:5005/api/v10", False),
    ("http://127.0.0.1.example.com:5005/api", False),
    ("http://127.0.0.1:5005@example.com/api", False),
    ("http://10.0.0.5:5005/api/v10", False),
    ("https://example.com/api/v10", False),
    ("", False),
])
def test_a_fake_discord_is_used_only_on_the_loopback(monkeypatch, value, used):
    monkeypatch.setenv(discord_http.API_ENV, value)
    monkeypatch.setenv(discord_http.AUTHORIZE_ENV, value)
    expected_api = value.rstrip("/") if used else discord_http.API_BASE
    expected_authorize = value.rstrip("/") if used else discord_http.AUTHORIZE_URL
    assert discord_http.api_base() == expected_api
    assert discord_http.authorize_base() == expected_authorize


def test_a_real_request_reaches_the_fake_server_with_the_timeouts(monkeypatch):
    with FakeDiscordServer() as fake:
        monkeypatch.setenv(discord_http.API_ENV, fake.api)
        result = discord_webhook.send(discord_http.Http(), WEBHOOK_ID, WEBHOOK_TOKEN, content="https://example.com/v/2")
        assert result.kind == "ok" and result.message_id
        assert fake.messages[0]["query"] == {"wait": "true"}
        assert fake.messages[0]["body"] == {"content": "https://example.com/v/2", "allowed_mentions": {"parse": []}}
        fake.fail("/api/v10/webhooks/", (404, {"message": "Unknown Webhook", "code": 10015}))
        gone = discord_webhook.send(discord_http.Http(), WEBHOOK_ID, WEBHOOK_TOKEN, content="x")
        assert gone.kind == "gone"


def test_a_webhook_that_hangs_reads_as_maybe_sent(monkeypatch):
    monkeypatch.setattr(discord_http, "TIMEOUT", (2, 0.3))
    with FakeDiscordServer() as fake:
        monkeypatch.setenv(discord_http.API_ENV, fake.api)
        fake.fail("/api/v10/webhooks/", "hang")
        result = discord_webhook.send(discord_http.Http(), WEBHOOK_ID, WEBHOOK_TOKEN, content="x")
    assert result.kind == "ambiguous"


def test_the_transport_sends_its_user_agent_and_timeouts(monkeypatch):
    seen = {}

    def fake_request(method, url, **kw):
        seen.update(method=method, url=url, **kw)
        return FakeResponse(200, {})

    monkeypatch.setattr(requests, "request", fake_request)
    discord_http.Http().request("GET", "https://discord.com/api/v10/users/@me", headers={"Authorization": "Bearer x"})
    assert seen["timeout"] == (5, 15)
    assert seen["headers"]["User-Agent"].startswith("ClipsKitty")
    assert seen["headers"]["Authorization"] == "Bearer x"
