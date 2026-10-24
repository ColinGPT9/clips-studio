"""Signing in with Discord (campaigns/discord_oauth.py, the sign-in half of
campaigns/service.py): PKCE S256 for a Public Client, no client secret
anywhere, the redirect a sign-in started with is the one its exchange sends,
an expired grant ends the sign-in, a state is good once and for 15 minutes,
and webhook.incoming keeps the webhook but never its access token.

No real Discord: a scripted transport (discord_fake.FakeDiscordHTTP).
"""

from urllib.parse import parse_qs, urlsplit

import pytest
import requests
from discord_fake import USER, FakeDiscordHTTP, challenge_of, tokens, webhook

from campaigns import discord_oauth, service
from campaigns.discord_http import DiscordError
from core import secrets

CLIENT = "100000000000000001"
NOW = 1_800_000_000.0


@pytest.fixture(autouse=True)
def _real_discord_addresses(monkeypatch):
    monkeypatch.delenv("CLIPS_KITTY_DISCORD_API", raising=False)
    monkeypatch.delenv("CLIPS_KITTY_DISCORD_AUTHORIZE", raising=False)


def _query(url: str) -> dict:
    parts = urlsplit(url)
    return {k: v[0] for k, v in parse_qs(parts.query).items()}


# ---- the authorize link ---------------------------------------------------------------


def test_the_authorize_link_asks_for_a_code_with_pkce_s256():
    url = discord_oauth.authorize_url(CLIENT, "http://127.0.0.1:8765/discord/callback", "st4te", "ch4llenge",
                                      discord_oauth.SIGN_IN_SCOPES)
    assert url.startswith("https://discord.com/oauth2/authorize?")
    assert _query(url) == {
        "response_type": "code", "client_id": CLIENT, "scope": "identify guilds",
        "redirect_uri": "http://127.0.0.1:8765/discord/callback", "state": "st4te",
        "code_challenge": "ch4llenge", "code_challenge_method": "S256",
    }
    assert "%20" in url and "client_secret" not in url


def test_the_registered_redirect_is_exactly_the_one_the_owner_set_up():
    assert discord_oauth.redirect_uri(8765) == "http://127.0.0.1:8765/discord/callback"
    assert discord_oauth.redirect_uri(discord_oauth.DEFAULT_PORT) == "http://127.0.0.1:8765/discord/callback"
    assert discord_oauth.CALLBACK_PATH == "/discord/callback"


def test_the_shipped_client_id_is_a_discord_application_id():
    assert discord_oauth.DISCORD_CLIENT_ID.isdigit() and 17 <= len(discord_oauth.DISCORD_CLIENT_ID) <= 20


def test_the_webhook_flow_asks_for_webhook_incoming_only(db):
    pending = discord_oauth.PendingSignIns()
    url = service.start_sign_in(db, pending, purpose="webhook", port=8765, now=NOW, label="Example")
    assert _query(url)["scope"] == "webhook.incoming"
    signin = service.start_sign_in(db, pending, purpose="connect", port=8765, now=NOW)
    assert _query(signin)["scope"] == "identify guilds"


def test_the_challenge_is_the_s256_of_the_verifier_kept_for_the_exchange():
    pending = discord_oauth.PendingSignIns()
    state, challenge = pending.start("connect", "http://127.0.0.1:8765/discord/callback", now=NOW)
    entry = pending.take(state, now=NOW + 1)
    assert challenge == challenge_of(entry.verifier)
    assert len(entry.verifier) >= 43


# ---- the token calls: forms with no secret -------------------------------------------------


def test_the_exchange_sends_the_code_verifier_and_redirect_and_no_secret():
    http = FakeDiscordHTTP((200, tokens()))
    got = discord_oauth.exchange(http, CLIENT, "c0de", "v3rifier", "http://127.0.0.1:8765/discord/callback")
    assert got["access_token"] == "access-1"
    call = http.calls[0]
    assert call["method"] == "POST" and call["url"] == "https://discord.com/api/v10/oauth2/token"
    assert call["data"] == {"grant_type": "authorization_code", "code": "c0de",
                            "redirect_uri": "http://127.0.0.1:8765/discord/callback", "client_id": CLIENT,
                            "code_verifier": "v3rifier"}
    assert call["headers"]["Content-Type"] == "application/x-www-form-urlencoded"


def test_refresh_and_revoke_send_the_client_id_and_no_secret():
    http = FakeDiscordHTTP((200, tokens(access="access-2")), (200, {}))
    discord_oauth.refresh(http, CLIENT, "refresh-1")
    assert discord_oauth.revoke(http, CLIENT, "refresh-1") is True
    assert http.calls[0]["data"] == {"grant_type": "refresh_token", "refresh_token": "refresh-1",
                                     "client_id": CLIENT}
    assert http.calls[1]["url"].endswith("/oauth2/token/revoke")
    assert http.calls[1]["data"] == {"token": "refresh-1", "token_type_hint": "refresh_token",
                                     "client_id": CLIENT}
    for call in http.calls:
        assert not any("secret" in key for key in call["data"])


def test_invalid_grant_reads_as_expired():
    http = FakeDiscordHTTP((400, {"error": "invalid_grant", "error_description": "Invalid refresh token"}))
    with pytest.raises(DiscordError) as caught:
        discord_oauth.refresh(http, CLIENT, "refresh-1")
    assert caught.value.code == "expired"


@pytest.mark.parametrize("answer, code", [
    ((401, {"error": "invalid_client"}), "denied"),
    ((429, {"message": "slow down", "retry_after": 2}), "rate_limited"),
    ((500, None), "bad_response"),
    ((200, {"token_type": "Bearer"}), "bad_response"),
])
def test_other_token_answers(answer, code):
    with pytest.raises(DiscordError) as caught:
        discord_oauth.exchange(FakeDiscordHTTP(answer), CLIENT, "c", "v", "r")
    assert caught.value.code == code


def test_a_network_failure_says_so_without_the_request_in_it():
    failure = requests.ConnectionError("HTTPSConnectionPool: url: /api/v10/oauth2/token code=c0de-secret")
    with pytest.raises(DiscordError) as caught:
        discord_oauth.exchange(FakeDiscordHTTP(failure), CLIENT, "c0de-secret", "v", "r")
    assert caught.value.code == "network"
    assert "c0de-secret" not in str(caught.value) and caught.value.__cause__ is None


def test_revoke_answers_false_when_refused_or_unreachable():
    assert discord_oauth.revoke(FakeDiscordHTTP((400, {"error": "invalid_request"})), CLIENT, "t") is False
    assert discord_oauth.revoke(FakeDiscordHTTP(requests.ConnectTimeout()), CLIENT, "t") is False
    assert discord_oauth.revoke(FakeDiscordHTTP(), CLIENT, "") is False


def test_me_and_guilds_send_the_bearer_token_and_mark_who_can_add_webhooks():
    guild_list = [
        {"id": "300000000000000001", "name": "owned", "owner": True, "permissions": "0"},
        {"id": "300000000000000002", "name": "webhooks", "owner": False, "permissions": str(1 << 29)},
        {"id": "300000000000000003", "name": "admin", "owner": False, "permissions": "8"},
        {"id": "300000000000000004", "name": "member", "owner": False, "permissions": "1024"},
    ]
    http = FakeDiscordHTTP((200, USER), (200, guild_list))
    assert discord_oauth.me(http, "access-1") == {"id": USER["id"], "username": "example-user",
                                                  "global_name": "Example User"}
    got = discord_oauth.guilds(http, "access-1")
    assert [(g["name"], g["can_manage_webhooks"]) for g in got] == [
        ("owned", True), ("webhooks", True), ("admin", True), ("member", False)]
    assert http.calls[0]["url"] == "https://discord.com/api/v10/users/@me"
    assert http.calls[1]["url"] == "https://discord.com/api/v10/users/@me/guilds"
    assert all(c["headers"] == {"Authorization": "Bearer access-1"} for c in http.calls)


def test_a_401_from_the_api_reads_as_expired():
    with pytest.raises(DiscordError) as caught:
        discord_oauth.me(FakeDiscordHTTP((401, {"message": "401: Unauthorized"})), "access-1")
    assert caught.value.code == "expired"


# ---- pending sign-ins ----------------------------------------------------------------------------


def test_a_state_is_good_once():
    pending = discord_oauth.PendingSignIns()
    state, _ = pending.start("connect", "r", now=NOW)
    assert pending.take(state, now=NOW + 10) is not None
    assert pending.take(state, now=NOW + 11) is None
    assert pending.take("never-issued", now=NOW) is None


def test_a_state_is_good_for_fifteen_minutes():
    pending = discord_oauth.PendingSignIns()
    early, _ = pending.start("connect", "r", now=NOW)
    late, _ = pending.start("connect", "r", now=NOW)
    assert pending.take(early, now=NOW + 15 * 60) is not None
    assert pending.take(late, now=NOW + 15 * 60 + 1) is None


def test_old_sign_ins_are_pruned_when_a_new_one_starts():
    pending = discord_oauth.PendingSignIns()
    old, _ = pending.start("connect", "r", now=NOW)
    pending.start("connect", "r", now=NOW + 16 * 60)
    assert old not in pending._waiting


# ---- the whole sign-in through the service ------------------------------------------------------


def test_the_exchange_sends_the_redirect_the_sign_in_started_with(db, tmp_path):
    pending = discord_oauth.PendingSignIns()
    url = service.start_sign_in(db, pending, purpose="connect", port=8791, now=NOW)
    query = _query(url)
    assert query["redirect_uri"] == "http://127.0.0.1:8791/discord/callback"
    http = FakeDiscordHTTP((200, tokens()), (200, USER))
    done = service.finish_sign_in(db, tmp_path, http, pending, state=query["state"], code="c0de", now=NOW + 5)
    assert done.ok and done.event == "connected" and "Example User" in done.message
    form = http.calls[0]["data"]
    assert form["redirect_uri"] == query["redirect_uri"]
    assert challenge_of(form["code_verifier"]) == query["code_challenge"]
    assert form["client_id"] == discord_oauth.DISCORD_CLIENT_ID
    status = service.status_payload(db, tmp_path)
    assert status["connected"] and status["user"]["username"] == "example-user"
    assert status["scopes"] == ["identify", "guilds"]


def test_an_unknown_or_used_state_changes_nothing(db, tmp_path):
    pending = discord_oauth.PendingSignIns()
    done = service.finish_sign_in(db, tmp_path, FakeDiscordHTTP(), pending, state="made-up", code="c", now=NOW)
    assert not done.ok and done.event == "" and "expired or was already used" in done.message
    assert service.load_tokens(tmp_path) is None


def test_cancelling_in_discord_is_a_failed_sign_in_with_no_call(db, tmp_path):
    pending = discord_oauth.PendingSignIns()
    state = _query(service.start_sign_in(db, pending, purpose="connect", port=8765, now=NOW))["state"]
    http = FakeDiscordHTTP()
    done = service.finish_sign_in(db, tmp_path, http, pending, state=state, error="access_denied", now=NOW)
    assert not done.ok and done.event == "failed" and "didn't approve" in done.message
    assert http.calls == []


def test_a_refused_exchange_keeps_nothing(db, tmp_path):
    pending = discord_oauth.PendingSignIns()
    state = _query(service.start_sign_in(db, pending, purpose="connect", port=8765, now=NOW))["state"]
    http = FakeDiscordHTTP((400, {"error": "invalid_grant"}))
    done = service.finish_sign_in(db, tmp_path, http, pending, state=state, code="old", now=NOW)
    assert not done.ok and done.event == "failed"
    assert service.load_tokens(tmp_path) is None and not service.status_payload(db, tmp_path)["connected"]


# ---- webhook.incoming --------------------------------------------------------------------------------


def _webhook_sign_in(db, tmp_path, answer):
    pending = discord_oauth.PendingSignIns()
    state = _query(service.start_sign_in(db, pending, purpose="webhook", port=8765, now=NOW,
                                         label="Example Campaign hook"))["state"]
    http = FakeDiscordHTTP((200, answer), (200, {}))
    return http, service.finish_sign_in(db, tmp_path, http, pending, state=state, code="c0de", now=NOW)


def test_webhook_incoming_becomes_a_destination_and_its_token_is_revoked_not_kept(db, tmp_path):
    answer = tokens(access="webhook-access", refresh="webhook-refresh", scope="webhook.incoming",
                    webhook=webhook(id="400000000000000009", token="made-by-oauth-token", name="Clips Kitty"))
    http, done = _webhook_sign_in(db, tmp_path, answer)
    assert done.ok and done.event == "destination_added"
    row = db.get_destination(done.destination_id)
    assert (row["source"], row["label"], row["webhook_id"], row["webhook_name"], row["state"]) == (
        "oauth", "Example Campaign hook", "400000000000000009", "Clips Kitty", "connected")
    assert service.load_webhook(tmp_path, done.destination_id) == {"webhook_id": "400000000000000009",
                                                                    "token": "made-by-oauth-token"}
    revoke = http.calls[1]
    assert revoke["url"].endswith("/oauth2/token/revoke") and revoke["data"]["token"] == "webhook-access"
    # Not kept: no sign-in stored, the card stays as it was.
    assert service.load_tokens(tmp_path) is None
    assert not secrets.has(tmp_path, service.OAUTH_SECRET)
    assert not service.status_payload(db, tmp_path)["connected"]


def test_no_webhook_in_the_answer_falls_back_to_pasting(db, tmp_path):
    http, done = _webhook_sign_in(db, tmp_path, tokens(scope="webhook.incoming"))
    assert not done.ok and done.event == "failed"
    assert "Paste the channel's webhook link" in done.message
    assert db.list_destinations() == []
    assert http.calls[1]["data"]["token"] == "access-1"  # revoked all the same
