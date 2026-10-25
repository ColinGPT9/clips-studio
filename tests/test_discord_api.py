"""The /discord routes (server/discord_api.py): the session header on every
route that changes something, the same header value the plugin routes take,
parse-link reading its link from a JSON body and never logging it, the
callback's one-time state, destinations checked with Discord before they
are kept, and a fake Discord used only on this PC's loopback.

No real Discord: a scripted transport, or a fake server on 127.0.0.1.
"""

import logging
import shutil
import time
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest

fastapi = pytest.importorskip("fastapi")
pytest.importorskip("httpx")

import requests  # noqa: E402
from campaign_fake import FakePublisher, clip, link, webhook_destination  # noqa: E402
from discord_fake import (  # noqa: E402
    CHANNEL_ID,
    GUILDS,
    USER,
    WEBHOOK_ID,
    WEBHOOK_TOKEN,
    FakeDiscordHTTP,
    FakeDiscordServer,
    tokens,
    webhook,
)
from fastapi.testclient import TestClient  # noqa: E402

from campaigns import discord_http, discord_oauth  # noqa: E402
from core.state import StateDB  # noqa: E402
from server import discord_api, publisher  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
SECRET = "test-session-secret-0123456789"
HEADERS = {"X-Clips-Kitty-Session": SECRET}
WEBHOOK_URL = f"https://discord.com/api/webhooks/{WEBHOOK_ID}/{WEBHOOK_TOKEN}"


@pytest.fixture(autouse=True)
def _real_discord_addresses(monkeypatch):
    monkeypatch.delenv(discord_http.API_ENV, raising=False)
    monkeypatch.delenv(discord_http.AUTHORIZE_ENV, raising=False)


@pytest.fixture
def events(monkeypatch):
    from server.events import broadcaster

    seen: list[dict] = []
    monkeypatch.setattr(broadcaster, "publish", seen.append)
    return seen


def _app(tmp_path, *, http=None, base_url="http://127.0.0.1"):
    app = fastapi.FastAPI()
    db_path = tmp_path / "state.db"
    StateDB(db_path).close()
    discord_api.install(app, db=lambda: StateDB(db_path), data_dir=tmp_path, session_secret=SECRET, http=http)
    client = TestClient(app, base_url=base_url)
    client.db_path = db_path
    return client


@pytest.fixture
def fake_http():
    return FakeDiscordHTTP()


@pytest.fixture
def client(tmp_path, fake_http):
    return _app(tmp_path, http=fake_http)


def _state(url: str) -> dict:
    return {k: v[0] for k, v in parse_qs(urlsplit(url).query).items()}


# ---- the session header -------------------------------------------------------------------


MUTATING = {
    ("PATCH", "/discord/settings"), ("POST", "/discord/connect"), ("POST", "/discord/disconnect"),
    ("POST", "/discord/destinations"), ("POST", "/discord/destinations/authorize"),
    ("PATCH", "/discord/destinations/{destination_id}"), ("DELETE", "/discord/destinations/{destination_id}"),
    ("POST", "/discord/destinations/{destination_id}/test"),
    ("POST", "/discord/clips/{clip_id}/send"),
}


def test_every_route_that_changes_something_needs_the_session_header(client, fake_http):
    from server import api_stability as st

    routes = {(m, p) for m, p, _mod in st.app_routes(client.app) if p.startswith("/discord")}
    changing = {(m, p) for m, p in routes if m in ("POST", "PATCH", "PUT", "DELETE")}
    assert changing - MUTATING == {("POST", "/discord/parse-link")}
    assert MUTATING <= changing
    bodies = {"/discord/destinations": {"webhook_url": WEBHOOK_URL}, "/discord/settings": {"client_id": ""}}
    for method, path in sorted(MUTATING):
        concrete = path.replace("{destination_id}", "1").replace("{clip_id}", "1")
        for headers in ({}, {"X-Clips-Kitty-Session": "wrong"}, {"X-Clips-Kitty-Session": ""}):
            r = client.request(method, concrete, json=bodies.get(path, {}), headers=headers)
            assert r.status_code == 403, (method, path, r.text)
            assert "X-Clips-Kitty-Session" in r.json()["detail"]
    assert fake_http.calls == []
    assert all(st.label(m, p) == st.INTERNAL for m, p in routes)


def test_reading_needs_no_header(client):
    assert client.get("/discord/status").status_code == 200
    assert client.get("/discord/destinations").json() == {"destinations": []}


@pytest.mark.parametrize("provider", ["woopsocial", "upload_post", "youtube"])
def test_the_editor_sends_an_existing_published_link_once(client, fake_http, tmp_path, provider):
    db = StateDB(client.db_path)
    try:
        clip_id = clip(db, title="Example title")
        dest = webhook_destination(db, tmp_path)
        writer = FakePublisher(db, provider if provider != "youtube" else "woopsocial")
        if provider == "youtube":
            writer.youtube(clip_id, youtube_id="abcdefghi01")
        else:
            writer.publish(clip_id, ["youtube"], state="published", url=link("youtube"))
        body = {"destination_id": dest, "provider": provider, "platform": "youtube"}
        response = client.post(f"/discord/clips/{clip_id}/send", headers=HEADERS, json=body)
        assert response.status_code == 200, response.text
        assert response.json()["already_sent"] is False
        assert fake_http.calls == [], "the request queues delivery; it never publishes or posts directly"
        http = FakeDiscordHTTP((200, {"id": "600000000000000001", "channel_id": CHANNEL_ID}))
        worker = publisher.PublishWorker({}, client.db_path, tmp_path)
        worker.campaign_http = http
        worker._advance_campaigns(db, now=time.time())
        response = client.post(f"/discord/clips/{clip_id}/send", headers=HEADERS, json=body)
        assert response.status_code == 200
        assert response.json()["already_sent"] is True
        worker._campaigns_now = True
        worker._advance_campaigns(db, now=time.time() + 120)
        assert [c["json"]["content"] for c in http.calls] == [f"Example title\n{link('youtube')}"]
    finally:
        db.close()


@pytest.mark.parametrize("state,visibility", [("queued", "default"), ("failed", "default"),
                                               ("published", "private")])
def test_the_editor_refuses_posts_without_a_public_live_link(client, fake_http, tmp_path, state, visibility):
    db = StateDB(client.db_path)
    try:
        clip_id = clip(db)
        dest = webhook_destination(db, tmp_path)
        FakePublisher(db).publish(clip_id, ["youtube"], state=state, url=link("youtube"), visibility=visibility)
        response = client.post(f"/discord/clips/{clip_id}/send", headers=HEADERS,
                               json={"destination_id": dest, "platform": "youtube", "provider": "woopsocial"})
        assert response.status_code == 409
        assert db.list_submissions() == []
        assert fake_http.calls == []
    finally:
        db.close()


def test_one_header_value_works_for_plugins_and_discord_in_the_real_app(tmp_path, monkeypatch):
    pytest.importorskip("yt_dlp")  # sources.dispatch imports the YouTube source
    from main import BUNDLED_CONFIG, load_config
    from plugins import session
    from server.api import create_app

    # No desktop app: the engine makes the secret itself, once. A second
    # secret_for() call anywhere would write a different one over it.
    monkeypatch.delenv("CLIPS_KITTY_SESSION_SECRET", raising=False)
    settings = tmp_path / "settings.yaml"
    shutil.copy(ROOT / "config" / "settings.yaml", settings)
    config = load_config(BUNDLED_CONFIG)
    data_dir = tmp_path / "data"
    config["paths"]["data_dir"] = str(data_dir)
    client = TestClient(create_app(config, settings), base_url="http://127.0.0.1")
    secret = session.path(data_dir).read_text().strip()
    header = {"X-Clips-Kitty-Session": secret}

    plan = client.post("/plugins/plan", json={"source": {"kind": "folder", "path": str(tmp_path / "none")}},
                       headers=header)
    settings_patch = client.patch("/discord/settings", json={"client_id": ""}, headers=header)
    assert plan.status_code != 403, plan.text
    assert settings_patch.status_code == 200, settings_patch.text
    assert client.patch("/discord/settings", json={"client_id": ""}).status_code == 403
    assert client.post("/plugins/plan", json={"source": {}}).status_code == 403


# ---- parse-link ------------------------------------------------------------------------------------


def test_parse_link_reads_a_json_body_and_never_echoes_or_logs_a_webhook(client, caplog, capsys):
    caplog.set_level(logging.DEBUG)
    got = client.post("/discord/parse-link", json={"link": WEBHOOK_URL})
    assert got.status_code == 200
    body = got.json()
    assert body["kind"] == "webhook" and "Settings → Discord → Destinations" in body["problem"]
    assert WEBHOOK_TOKEN not in got.text
    out = capsys.readouterr()
    assert WEBHOOK_TOKEN not in caplog.text + out.out + out.err
    channel = client.post("/discord/parse-link",
                          json={"link": "https://discord.com/channels/300000000000000001/500000000000000001"})
    assert channel.json() == {"kind": "channel", "guild_id": "300000000000000001",
                              "channel_id": "500000000000000001", "message_id": "", "problem": ""}


def test_parse_link_is_not_a_query_string_route(client):
    assert client.get("/discord/parse-link", params={"link": "x"}).status_code == 405


# ---- settings -----------------------------------------------------------------------------------------


def test_the_application_id_override(client):
    bad = client.patch("/discord/settings", json={"client_id": "12345"}, headers=HEADERS)
    assert bad.status_code == 400 and "17 to 20 digits" in bad.json()["detail"]
    got = client.patch("/discord/settings", json={"client_id": "100000000000000002"}, headers=HEADERS).json()
    assert (got["client_id"], got["client_id_source"], got["configured"]) == ("100000000000000002", "override", True)
    url = client.post("/discord/connect", headers=HEADERS).json()["url"]
    assert _state(url)["client_id"] == "100000000000000002"
    back = client.patch("/discord/settings", json={"client_id": ""}, headers=HEADERS).json()
    assert back["client_id_source"] == "shipped"


def test_with_no_client_id_connect_says_it_isnt_set_up(client, monkeypatch):
    monkeypatch.setattr(discord_oauth, "DISCORD_CLIENT_ID", "")
    assert client.get("/discord/status").json()["configured"] is False
    for path in ("/discord/connect", "/discord/destinations/authorize"):
        r = client.post(path, json={}, headers=HEADERS)
        assert r.status_code == 400
        assert r.json()["detail"] == "Discord sign-in isn't set up in this version of Clips Kitty."


# ---- signing in -----------------------------------------------------------------------------------------


def test_the_redirect_comes_from_the_requests_port_and_the_exchange_reuses_it(tmp_path, events):
    http = FakeDiscordHTTP((200, tokens()), (200, USER))
    client = _app(tmp_path, http=http, base_url="http://127.0.0.1:8791")
    assert client.get("/discord/status").json()["redirect_uri"] == "http://127.0.0.1:8791/discord/callback"
    query = _state(client.post("/discord/connect", headers=HEADERS).json()["url"])
    assert query["redirect_uri"] == "http://127.0.0.1:8791/discord/callback"
    page = client.get("/discord/callback", params={"state": query["state"], "code": "c0de"})
    assert page.status_code == 200 and "Connected to Discord as Example User" in page.text
    assert http.calls[0]["data"]["redirect_uri"] == query["redirect_uri"]
    assert events == [{"type": "campaign", "discord": "connected"}]
    status = client.get("/discord/status").json()
    assert status["connected"] and status["user"]["username"] == "example-user"


def test_the_default_redirect_is_the_registered_one(client):
    query = _state(client.post("/discord/connect", headers=HEADERS).json()["url"])
    assert query["redirect_uri"] == "http://127.0.0.1:8765/discord/callback"
    assert query["code_challenge_method"] == "S256"


def test_a_callback_state_is_good_once_and_unknown_ones_change_nothing(client, fake_http, events):
    stranger = client.get("/discord/callback", params={"state": "made-up", "code": "c"})
    assert stranger.status_code == 400 and "expired or was already used" in stranger.text
    assert events == [] and fake_http.calls == []

    query = _state(client.post("/discord/connect", headers=HEADERS).json()["url"])
    fake_http.add((200, tokens()), (200, USER))
    assert client.get("/discord/callback", params={"state": query["state"], "code": "c"}).status_code == 200
    again = client.get("/discord/callback", params={"state": query["state"], "code": "c"})
    assert again.status_code == 400 and len(fake_http.calls) == 2


def test_a_cancelled_sign_in_says_so_and_tells_the_app(client, events):
    query = _state(client.post("/discord/connect", headers=HEADERS).json()["url"])
    page = client.get("/discord/callback", params={"state": query["state"], "error": "access_denied"})
    assert page.status_code == 400 and "didn&#x27;t approve Clips Kitty" in page.text
    assert events == [{"type": "campaign", "discord": "failed"}]


def test_disconnect_revokes_and_answers_the_status(client, fake_http, events):
    query = _state(client.post("/discord/connect", headers=HEADERS).json()["url"])
    fake_http.add((200, tokens()), (200, USER))
    client.get("/discord/callback", params={"state": query["state"], "code": "c"})
    fake_http.add((200, {}))
    got = client.post("/discord/disconnect", headers=HEADERS).json()
    assert got["revoked"] is True and got["connected"] is False and got["user"]["id"] == ""
    assert events[-1] == {"type": "campaign", "discord": "disconnected"}


def test_guilds_need_a_sign_in(client):
    r = client.get("/discord/guilds")
    assert r.status_code == 409 and "Connect Discord" in r.json()["detail"]


def test_a_server_list_that_finds_the_sign_in_expired_tells_the_app(client, fake_http, events):
    query = _state(client.post("/discord/connect", headers=HEADERS).json()["url"])
    fake_http.add((200, tokens()), (200, USER))
    client.get("/discord/callback", params={"state": query["state"], "code": "c"})
    fake_http.add((401, {"message": "401: Unauthorized", "code": 0}))
    r = client.get("/discord/guilds")
    assert r.status_code == 409 and "expired" in r.json()["detail"]
    assert events[-1] == {"type": "campaign", "discord": "expired"}
    status = client.get("/discord/status").json()
    assert (status["connected"], status["expired"]) == (False, True)


# ---- destinations -------------------------------------------------------------------------------------------


def test_a_destination_is_checked_with_discord_before_it_is_added(client, fake_http):
    fake_http.add((404, {"message": "Unknown Webhook", "code": 10015}))
    refused = client.post("/discord/destinations", json={"webhook_url": WEBHOOK_URL}, headers=HEADERS)
    assert refused.status_code == 400 and WEBHOOK_TOKEN not in refused.text
    bad = client.post("/discord/destinations", json={"webhook_url": "https://discord.com/channels/1/2"},
                      headers=HEADERS)
    assert bad.status_code == 400 and "isn't a Discord webhook link" in bad.json()["detail"]

    fake_http.add((200, webhook()))
    added = client.post("/discord/destinations", json={"webhook_url": WEBHOOK_URL, "label": "Example Campaign"},
                        headers=HEADERS)
    assert added.status_code == 200 and WEBHOOK_TOKEN not in added.text
    dest = added.json()["destination"]
    assert (dest["label"], dest["state"], dest["has_token"]) == ("Example Campaign", "connected", True)
    listed = client.get("/discord/destinations").json()["destinations"]
    assert [d["id"] for d in listed] == [dest["id"]]
    assert client.get("/discord/status").json()["destinations"] == listed


def test_a_destination_can_be_changed_tested_and_removed(client, fake_http):
    fake_http.add((200, webhook()))
    dest = client.post("/discord/destinations", json={"webhook_url": WEBHOOK_URL}, headers=HEADERS).json()["destination"]
    path = f"/discord/destinations/{dest['id']}"
    changed = client.patch(path, json={"label": "Renamed", "is_forum": True}, headers=HEADERS).json()["destination"]
    assert (changed["label"], changed["is_forum"]) == ("Renamed", True)
    assert client.patch(path, json={"thread_id": "12"}, headers=HEADERS).status_code == 400

    fake_http.add((200, {"id": "600000000000000001", "channel_id": "700000000000000001"}))
    tested = client.post(f"{path}/test", headers=HEADERS).json()
    assert tested["ok"] and tested["message_url"].endswith("/700000000000000001/600000000000000001")
    assert fake_http.calls[-1]["json"]["thread_name"] == "Clips Kitty test"

    removed = client.delete(path, headers=HEADERS)
    assert removed.json() == {"removed": True, "campaigns_affected": 0}
    assert client.get("/discord/destinations").json() == {"destinations": []}
    for method in ("PATCH", "DELETE"):
        assert client.request(method, path, json={}, headers=HEADERS).status_code == 404
    assert client.post(f"{path}/test", headers=HEADERS).status_code == 404


def test_create_with_discord_starts_a_webhook_sign_in(client, fake_http, events):
    query = _state(client.post("/discord/destinations/authorize", json={"label": "From Discord"},
                               headers=HEADERS).json()["url"])
    assert query["scope"] == "webhook.incoming"
    hook = webhook(id="400000000000000009", token="made-by-oauth-token", name="Clips Kitty")
    fake_http.add((200, tokens(scope="webhook.incoming", webhook=hook)), (200, {}))
    page = client.get("/discord/callback", params={"state": query["state"], "code": "c"})
    assert page.status_code == 200 and "made-by-oauth-token" not in page.text
    assert events == [{"type": "campaign", "discord": "destination_added"}]
    listed = client.get("/discord/destinations").json()["destinations"]
    assert [(d["label"], d["source"]) for d in listed] == [("From Discord", "oauth")]
    assert client.get("/discord/status").json()["connected"] is False


# ---- a fake Discord on the loopback only --------------------------------------------------------------------


def test_the_whole_sign_in_against_a_fake_discord_on_this_pc(tmp_path, monkeypatch, events):
    with FakeDiscordServer() as fake:
        monkeypatch.setenv(discord_http.API_ENV, fake.api)
        monkeypatch.setenv(discord_http.AUTHORIZE_ENV, fake.authorize)
        client = _app(tmp_path)  # the real transport
        url = client.post("/discord/connect", headers=HEADERS).json()["url"]
        assert url.startswith(fake.authorize + "?")
        back = requests.get(url, allow_redirects=False, timeout=5)
        location = urlsplit(back.headers["Location"])
        assert f"{location.scheme}://{location.netloc}{location.path}" == "http://127.0.0.1:8765/discord/callback"
        page = client.get(f"{location.path}?{location.query}")
        assert page.status_code == 200, page.text
        exchange = fake.forms[0]
        assert exchange["grant_type"] == "authorization_code" and "client_secret" not in exchange
        assert client.get("/discord/status").json()["connected"]
        assert [g["name"] for g in client.get("/discord/guilds").json()["guilds"]] == [g["name"] for g in GUILDS]

        added = client.post("/discord/destinations", json={"webhook_url": WEBHOOK_URL}, headers=HEADERS).json()
        assert added["destination"]["guild_name"] == "example-server"
        tested = client.post(f"/discord/destinations/{added['destination']['id']}/test", headers=HEADERS).json()
        assert tested["ok"] and fake.messages[0]["body"]["allowed_mentions"] == {"parse": []}

        assert client.post("/discord/disconnect", headers=HEADERS).json()["revoked"] is True
        assert fake.revoked and not client.get("/discord/status").json()["connected"]


def test_a_fake_discord_elsewhere_is_ignored(tmp_path, monkeypatch):
    monkeypatch.setenv(discord_http.API_ENV, "https://discord.example.com/api/v10")
    monkeypatch.setenv(discord_http.AUTHORIZE_ENV, "http://localhost:9999/oauth2/authorize")
    client = _app(tmp_path)
    url = client.post("/discord/connect", headers=HEADERS).json()["url"]
    assert url.startswith("https://discord.com/oauth2/authorize?")
    assert discord_http.api_base() == "https://discord.com/api/v10"
