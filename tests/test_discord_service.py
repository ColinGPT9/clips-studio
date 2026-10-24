"""Discord's settings, secrets and destinations (campaigns/service.py):
tokens live only in core/secrets, never in the database or a status answer;
which application id is used; what works with no sign-in at all; the token
refresh and an expired sign-in; the server list cache; disconnecting; and
the webhook destinations (added after Discord confirms them, removed with
their token, tested with a message).

No real Discord: a scripted transport (discord_fake.FakeDiscordHTTP).
"""

import json
from urllib.parse import parse_qs, urlsplit

import pytest
import requests
from discord_fake import (
    CHANNEL_ID,
    GUILDS,
    USER,
    WEBHOOK_ID,
    WEBHOOK_TOKEN,
    FakeDiscordHTTP,
    tokens,
    webhook,
)

from campaigns import discord_oauth, model, service, statemachine
from campaigns.discord_http import DiscordError
from core import secrets

NOW = 1_800_000_000.0
DAY = 24 * 3600
WEBHOOK_URL = f"https://discord.com/api/webhooks/{WEBHOOK_ID}/{WEBHOOK_TOKEN}"


@pytest.fixture(autouse=True)
def _real_discord_addresses(monkeypatch):
    monkeypatch.delenv("CLIPS_KITTY_DISCORD_API", raising=False)
    monkeypatch.delenv("CLIPS_KITTY_DISCORD_AUTHORIZE", raising=False)


def _connect(db, data_dir, *, access="access-1", refresh="refresh-1", expires_in=7 * DAY, now=NOW):
    """Sign in through the service, as the callback does."""
    pending = discord_oauth.PendingSignIns()
    url = service.start_sign_in(db, pending, purpose="connect", port=8765, now=now)
    state = parse_qs(urlsplit(url).query)["state"][0]
    http = FakeDiscordHTTP((200, tokens(access=access, refresh=refresh, expires_in=expires_in)), (200, USER))
    done = service.finish_sign_in(db, data_dir, http, pending, state=state, code="c0de", now=now)
    assert done.ok
    return done


def _add(db, data_dir, **kw):
    http = FakeDiscordHTTP((200, webhook()))
    return service.add_destination(db, data_dir, http, webhook_url=kw.pop("webhook_url", WEBHOOK_URL), **kw)


# ---- the tokens only in secrets ---------------------------------------------------------------


def test_tokens_live_only_in_the_credential_store(db, tmp_path):
    _connect(db, tmp_path, access="ACCESS-token-value-1", refresh="REFRESH-token-value-1")
    dest = _add(db, tmp_path)
    status = service.status_payload(db, tmp_path)
    db.conn.commit()
    raw = (tmp_path / "state.db").read_bytes()
    for wal in tmp_path.glob("state.db-*"):
        raw += wal.read_bytes()
    for token in ("ACCESS-token-value-1", "REFRESH-token-value-1", WEBHOOK_TOKEN):
        assert token.encode() not in raw
        assert token not in json.dumps(status)
        assert token not in json.dumps(dest)
    assert service.load_tokens(tmp_path)["refresh_token"] == "REFRESH-token-value-1"
    assert service.load_webhook(tmp_path, dest["id"]) == {"webhook_id": WEBHOOK_ID, "token": WEBHOOK_TOKEN}
    assert secrets.has(tmp_path, model.OAUTH_SECRET)
    assert secrets.has(tmp_path, model.webhook_secret_name(dest["id"]))
    assert status["connected"] and status["destinations"][0]["has_token"]


def test_the_secret_names_are_the_models():
    assert service.OAUTH_SECRET == model.OAUTH_SECRET == "discord_oauth"
    assert service.webhook_secret_name(7) == "discord_webhook_7"


def test_the_status_says_who_without_any_token(db, tmp_path):
    _connect(db, tmp_path)
    status = service.status_payload(db, tmp_path, redirect_uri="http://127.0.0.1:8791/discord/callback")
    assert set(status) == {"configured", "client_id", "client_id_source", "connected", "expired", "user",
                           "scopes", "connected_at", "redirect_uri", "destinations", "storage"}
    assert status["user"] == {"id": USER["id"], "username": "example-user", "global_name": "Example User"}
    assert status["connected_at"] == "2027-01-15T08:00:00Z"
    assert status["redirect_uri"] == "http://127.0.0.1:8791/discord/callback"
    assert status["storage"] == secrets.backend_name()


# ---- the application id -------------------------------------------------------------------------


def test_the_override_wins_over_the_shipped_id(db, monkeypatch):
    assert service.client_id(db) == (discord_oauth.DISCORD_CLIENT_ID, "shipped")
    service.set_client_id(db, " 100000000000000002 ")
    assert service.client_id(db) == ("100000000000000002", "override")
    service.set_client_id(db, "")
    assert service.client_id(db) == (discord_oauth.DISCORD_CLIENT_ID, "shipped")
    monkeypatch.setattr(discord_oauth, "DISCORD_CLIENT_ID", "")
    assert service.client_id(db) == ("", "none")


@pytest.mark.parametrize("value", ["123", "abc", "1563637373474971708x", "1" * 21, "-100000000000000002"])
def test_an_override_must_be_an_application_id(db, value):
    with pytest.raises(ValueError, match="17 to 20 digits"):
        service.set_client_id(db, value)


def test_with_no_client_id_sign_in_is_off_and_everything_else_works(db, tmp_path, monkeypatch):
    monkeypatch.setattr(discord_oauth, "DISCORD_CLIENT_ID", "")
    status = service.status_payload(db, tmp_path)
    assert (status["configured"], status["client_id_source"], status["connected"]) == (False, "none", False)
    for purpose in ("connect", "webhook"):
        with pytest.raises(DiscordError) as caught:
            service.start_sign_in(db, discord_oauth.PendingSignIns(), purpose=purpose, port=8765, now=NOW)
        assert caught.value.code == "not_configured"
        assert caught.value.message == "Discord sign-in isn't set up in this version of Clips Kitty."
    dest = _add(db, tmp_path)
    assert dest["state"] == "connected"
    sent = service.send_test_message(db, tmp_path, FakeDiscordHTTP((200, {"id": "600000000000000001",
                                                                          "channel_id": CHANNEL_ID})), dest["id"])
    assert sent["ok"]


# ---- the access token -----------------------------------------------------------------------------


def test_a_token_with_more_than_a_day_left_is_used_as_it_is(db, tmp_path):
    _connect(db, tmp_path, expires_in=7 * DAY)
    http = FakeDiscordHTTP()
    assert service.access_token(db, tmp_path, http, now=NOW + 5 * DAY) == "access-1"
    assert http.calls == []


def test_a_token_with_less_than_a_day_left_is_refreshed(db, tmp_path):
    _connect(db, tmp_path, expires_in=7 * DAY)
    http = FakeDiscordHTTP((200, tokens(access="access-2", refresh="refresh-2")))
    assert service.access_token(db, tmp_path, http, now=NOW + 6.5 * DAY) == "access-2"
    assert http.calls[0]["data"]["refresh_token"] == "refresh-1"
    kept = service.load_tokens(tmp_path)
    assert (kept["access_token"], kept["refresh_token"]) == ("access-2", "refresh-2")
    assert kept["expires_at"] == NOW + 6.5 * DAY + 7 * DAY


def test_a_refresh_with_no_new_refresh_token_keeps_the_old_one(db, tmp_path):
    _connect(db, tmp_path, expires_in=DAY)
    answer = tokens(access="access-2")
    del answer["refresh_token"]
    service.access_token(db, tmp_path, FakeDiscordHTTP((200, answer)), now=NOW + 60)
    assert service.load_tokens(tmp_path)["refresh_token"] == "refresh-1"


def test_an_expired_grant_wipes_the_tokens_and_says_reconnect(db, tmp_path):
    _connect(db, tmp_path, expires_in=DAY)
    http = FakeDiscordHTTP((400, {"error": "invalid_grant"}))
    with pytest.raises(DiscordError) as caught:
        service.access_token(db, tmp_path, http, now=NOW + 60)
    assert caught.value.code == "expired"
    assert service.load_tokens(tmp_path) is None
    assert not secrets.has(tmp_path, model.OAUTH_SECRET)
    status = service.status_payload(db, tmp_path)
    assert (status["connected"], status["expired"], status["user"]["username"]) == (False, True, "example-user")
    with pytest.raises(DiscordError) as again:
        service.access_token(db, tmp_path, FakeDiscordHTTP(), now=NOW + 120)
    assert again.value.code == "expired"


def test_discord_not_answering_keeps_a_token_that_still_works(db, tmp_path):
    _connect(db, tmp_path, expires_in=DAY)
    http = FakeDiscordHTTP(requests.ConnectTimeout())
    assert service.access_token(db, tmp_path, http, now=NOW + 60) == "access-1"
    assert service.load_tokens(tmp_path)["refresh_token"] == "refresh-1"
    assert not service.status_payload(db, tmp_path)["expired"]
    late = FakeDiscordHTTP(requests.ConnectTimeout())
    with pytest.raises(DiscordError) as caught:
        service.access_token(db, tmp_path, late, now=NOW + 2 * DAY)
    assert caught.value.code == "network"


def test_not_signed_in_is_not_connected(db, tmp_path):
    with pytest.raises(DiscordError) as caught:
        service.access_token(db, tmp_path, FakeDiscordHTTP(), now=NOW)
    assert caught.value.code == "not_connected"


# ---- servers -------------------------------------------------------------------------------------------


def test_the_server_list_is_kept_for_ten_minutes(db, tmp_path):
    _connect(db, tmp_path)
    http = FakeDiscordHTTP((200, GUILDS))
    first = service.guild_list(db, tmp_path, http, now=NOW + 10)
    assert [(g["name"], g["can_manage_webhooks"]) for g in first] == [
        ("example-server", True), ("another-server", True), ("read-only-server", False)]
    assert service.guild_list(db, tmp_path, http, now=NOW + 10 + 599) == first
    assert len(http.calls) == 1
    http.add((200, GUILDS[:1]))
    assert len(service.guild_list(db, tmp_path, http, now=NOW + 10 + 601)) == 1
    http.add((200, GUILDS))
    assert len(service.guild_list(db, tmp_path, http, now=NOW + 700, refresh=True)) == 3


def test_a_revoked_sign_in_found_listing_servers_is_expired(db, tmp_path):
    _connect(db, tmp_path)
    with pytest.raises(DiscordError) as caught:
        service.guild_list(db, tmp_path, FakeDiscordHTTP((401, {"message": "401: Unauthorized"})), now=NOW + 10)
    assert caught.value.code == "expired"
    assert service.status_payload(db, tmp_path)["expired"]
    assert service.load_tokens(tmp_path) is None


# ---- disconnecting ------------------------------------------------------------------------------------


def test_disconnect_revokes_and_forgets_everything(db, tmp_path):
    _connect(db, tmp_path)
    http = FakeDiscordHTTP((200, {}))
    assert service.disconnect(db, tmp_path, http) is True
    assert http.calls[0]["data"] == {"token": "refresh-1", "token_type_hint": "refresh_token",
                                     "client_id": discord_oauth.DISCORD_CLIENT_ID}
    assert service.load_tokens(tmp_path) is None
    status = service.status_payload(db, tmp_path)
    assert (status["connected"], status["expired"], status["user"]["id"], status["scopes"]) == (False, False, "", [])


def test_disconnect_forgets_locally_even_when_discord_refuses(db, tmp_path):
    _connect(db, tmp_path)
    http = FakeDiscordHTTP((400, {"error": "invalid_request"}), requests.ConnectTimeout())
    assert service.disconnect(db, tmp_path, http) is False
    assert [c["data"]["token_type_hint"] for c in http.calls] == ["refresh_token", "access_token"]
    assert service.load_tokens(tmp_path) is None
    assert not service.status_payload(db, tmp_path)["connected"]


# ---- destinations ----------------------------------------------------------------------------------------


def test_a_pasted_webhook_is_checked_with_discord_before_it_is_kept(db, tmp_path):
    http = FakeDiscordHTTP((200, webhook()))
    dest = service.add_destination(db, tmp_path, http, webhook_url=WEBHOOK_URL, label="Example Campaign",
                                   is_forum=True, thread_id="700000000000000001")
    assert http.calls[0]["method"] == "GET" and http.calls[0]["url"].endswith(f"/webhooks/{WEBHOOK_ID}/{WEBHOOK_TOKEN}")
    assert {k: dest[k] for k in ("label", "source", "guild_id", "channel_id", "webhook_name", "is_forum",
                                 "thread_id", "state", "has_token", "campaigns")} == {
        "label": "Example Campaign", "source": "pasted", "guild_id": GUILDS[0]["id"], "channel_id": CHANNEL_ID,
        "webhook_name": "Example hook", "is_forum": True, "thread_id": "700000000000000001",
        "state": "connected", "has_token": True, "campaigns": 0}
    assert db.get_destination(dest["id"])["webhook_id"] == WEBHOOK_ID


def test_the_server_name_comes_from_the_signed_in_list(db, tmp_path):
    _connect(db, tmp_path)
    service.guild_list(db, tmp_path, FakeDiscordHTTP((200, GUILDS)), now=NOW + 10)
    assert _add(db, tmp_path)["guild_name"] == "example-server"


def test_a_webhook_discord_doesnt_know_is_refused_and_nothing_is_kept(db, tmp_path):
    http = FakeDiscordHTTP((404, {"message": "Unknown Webhook", "code": 10015}))
    with pytest.raises(DiscordError) as caught:
        service.add_destination(db, tmp_path, http, webhook_url=WEBHOOK_URL)
    assert caught.value.code == "gone"
    assert db.list_destinations() == []
    assert not list(tmp_path.glob("credentials/discord_webhook_*"))


@pytest.mark.parametrize("url", [
    "https://discord.com/channels/300000000000000001/500000000000000001",
    "https://example.com/api/webhooks/400000000000000001/abc",
    "not a link",
])
def test_a_link_that_isnt_a_webhook_is_refused_without_echoing_it(db, tmp_path, url):
    http = FakeDiscordHTTP()
    with pytest.raises(ValueError) as caught:
        service.add_destination(db, tmp_path, http, webhook_url=url)
    assert url not in str(caught.value) and http.calls == []


def test_the_same_webhook_twice_is_refused(db, tmp_path):
    _add(db, tmp_path, label="First")
    with pytest.raises(ValueError, match="already a destination"):
        _add(db, tmp_path)
    assert len(db.list_destinations()) == 1


@pytest.mark.parametrize("fields", [{"thread_id": "12"}, {"label": "x" * 101}])
def test_bad_fields_are_refused_before_asking_discord(db, tmp_path, fields):
    http = FakeDiscordHTTP()
    with pytest.raises(ValueError):
        service.add_destination(db, tmp_path, http, webhook_url=WEBHOOK_URL, **fields)
    assert http.calls == []


def test_a_new_link_brings_a_gone_destination_back(db, tmp_path):
    dest = _add(db, tmp_path)
    db.set_destination(dest["id"], state="gone", last_error="The webhook doesn't exist any more")
    service.wipe_webhook(tmp_path, dest["id"])
    new_url = f"https://discord.com/api/webhooks/{WEBHOOK_ID}/new-token-value"
    http = FakeDiscordHTTP((200, webhook(token="new-token-value")))
    got = service.update_destination(db, tmp_path, http, dest["id"], webhook_url=new_url, label="Renamed")
    assert (got["state"], got["last_error"], got["label"], got["has_token"]) == ("connected", "", "Renamed", True)
    assert service.load_webhook(tmp_path, dest["id"])["token"] == "new-token-value"
    assert service.update_destination(db, tmp_path, FakeDiscordHTTP(), 999, label="x") is None


def _campaign(db, **fields):
    return db.insert_campaign("Example Campaign", **fields)


def _submission(db, campaign_id, platform, **fields):
    return db.insert_submission(campaign_id, "vid-example", 10.0, 40.0, platform, **fields)


def test_removing_a_destination_sends_its_waiting_rows_to_be_done_by_hand(db, tmp_path):
    dest = _add(db, tmp_path)
    other = service.add_destination(db, tmp_path, FakeDiscordHTTP((200, webhook(id="400000000000000002"))),
                                    webhook_url="https://discord.com/api/webhooks/400000000000000002/other-token")
    camp = _campaign(db, method="discord_webhook", destination_id=dest["id"])
    _campaign(db, method="discord_webhook", destination_id=other["id"])
    on_it = _submission(db, camp, "tiktok", method="discord_webhook", destination_id=dest["id"], status="ready")
    elsewhere = _submission(db, camp, "youtube", method="discord_webhook", destination_id=other["id"],
                            status="ready")
    waiting = _submission(db, camp, "instagram", method="discord_webhook", destination_id=dest["id"])
    listed = {d["id"]: d["campaigns"] for d in service.status_payload(db, tmp_path)["destinations"]}
    assert listed == {dest["id"]: 1, other["id"]: 1}

    got = service.remove_destination(db, tmp_path, dest["id"], now=NOW)

    assert got == {"removed": True, "campaigns_affected": 1}
    assert db.get_destination(dest["id"]) is None
    assert service.load_webhook(tmp_path, dest["id"]) is None
    assert service.load_webhook(tmp_path, other["id"]) is not None
    moved = db.get_submission(on_it)
    assert (moved["status"], moved["status_reason"]) == ("manual", service.REMOVED_REASON)
    assert db.get_submission(elsewhere)["status"] == "ready"
    assert db.get_submission(waiting)["status"] == "waiting_publication"
    assert service.remove_destination(db, tmp_path, dest["id"], now=NOW) is None


def test_the_move_to_manual_is_the_state_machines(db, tmp_path):
    assert statemachine.allowed({"status": "ready", "method": "discord_webhook"}, "destination_unusable")
    assert not statemachine.allowed({"status": "ready", "method": "discord_assisted"}, "destination_unusable")


# ---- the test message ------------------------------------------------------------------------------------


def test_the_test_message_posts_and_links_to_itself(db, tmp_path):
    dest = _add(db, tmp_path)
    http = FakeDiscordHTTP((200, {"id": "600000000000000001", "channel_id": CHANNEL_ID}))
    got = service.send_test_message(db, tmp_path, http, dest["id"])
    assert got == {"ok": True, "error": "",
                   "message_url": f"https://discord.com/channels/{GUILDS[0]['id']}/{CHANNEL_ID}/600000000000000001"}
    assert http.calls[0]["json"]["content"] == "Clips Kitty test message. You can delete it."
    assert http.calls[0]["json"]["allowed_mentions"] == {"parse": []}
    assert db.get_destination(dest["id"])["last_sent_at"] > 0


def test_a_forum_destination_tests_in_a_new_thread_or_its_own(db, tmp_path):
    forum = _add(db, tmp_path, is_forum=True)
    http = FakeDiscordHTTP((200, {"id": "1", "channel_id": "700000000000000005"}))
    service.send_test_message(db, tmp_path, http, forum["id"])
    assert http.calls[0]["json"]["thread_name"] == "Clips Kitty test"
    service.update_destination(db, tmp_path, FakeDiscordHTTP(), forum["id"], thread_id="700000000000000001")
    http.add((200, {"id": "2", "channel_id": "700000000000000001"}))
    service.send_test_message(db, tmp_path, http, forum["id"])
    assert "thread_name" not in http.calls[1]["json"]
    assert http.calls[1]["params"]["thread_id"] == "700000000000000001"


def test_a_test_that_finds_the_webhook_gone_marks_it_and_wipes_the_token(db, tmp_path):
    dest = _add(db, tmp_path)
    got = service.send_test_message(db, tmp_path, FakeDiscordHTTP((404, {"message": "Unknown Webhook",
                                                                         "code": 10015})), dest["id"])
    assert not got["ok"] and "doesn't exist" in got["error"]
    row = db.get_destination(dest["id"])
    assert (row["state"], bool(row["last_error"])) == ("gone", True)
    assert service.load_webhook(tmp_path, dest["id"]) is None
    again = service.send_test_message(db, tmp_path, FakeDiscordHTTP(), dest["id"])
    assert not again["ok"] and "Paste its link again" in again["error"]


def test_a_test_that_is_refused_marks_the_destination_denied(db, tmp_path):
    dest = _add(db, tmp_path)
    got = service.send_test_message(db, tmp_path, FakeDiscordHTTP((403, {"message": "Missing Permissions",
                                                                         "code": 50013})), dest["id"])
    assert not got["ok"]
    assert db.get_destination(dest["id"])["state"] == "denied"
    assert service.load_webhook(tmp_path, dest["id"]) is not None


def test_a_test_discord_didnt_answer_leaves_the_destination_alone(db, tmp_path):
    dest = _add(db, tmp_path)
    got = service.send_test_message(db, tmp_path, FakeDiscordHTTP(requests.ConnectTimeout()), dest["id"])
    assert not got["ok"] and got["error"] == "Couldn't reach Discord"
    row = db.get_destination(dest["id"])
    assert (row["state"], row["last_error"]) == ("connected", "")
    assert service.send_test_message(db, tmp_path, FakeDiscordHTTP(), 999) is None
