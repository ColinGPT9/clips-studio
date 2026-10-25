"""The campaigns screens' client (ui/src/renderer/src/lib/campaigns.ts), run
under Node against a recording fetch: a pasted link (which may be a
webhook's, and so its token) goes to parse-link in a POST body, never in a
query string the engine's access log would print; every route that changes
something carries the session header, and without the desktop app's secret
nothing is sent. Plus the Discord card's pure helpers.

Skips without Node, or with a Node that can't run TypeScript directly.
"""

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

LIB = Path(__file__).resolve().parent.parent / "ui" / "src" / "renderer" / "src" / "lib"
WEBHOOK = "https://discord.com/api/webhooks/400000000000000001/fake-webhook-token-Abc123_xyz"
TOKEN = "fake-webhook-token-Abc123_xyz"


def _run(tmp_path, body: str, *, secret: str = "sess-1"):
    """Run async `body` with `m` = lib/campaigns.ts, its imports replaced by
    stubs, `calls` the fetches it made; return what it returns, as JSON."""
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node isn't available")
    source = (LIB / "campaigns.ts").read_text(encoding="utf-8")
    source = re.sub(r"from '\./(api|plugins)'", r"from './\1.ts'", source)
    (tmp_path / "campaigns.ts").write_text(source, encoding="utf-8")
    (tmp_path / "api.ts").write_text("export const API_BASE = 'http://engine'\n", encoding="utf-8")
    (tmp_path / "plugins.ts").write_text(
        f"export function sessionSecret() {{ return Promise.resolve({json.dumps(secret)}) }}\n", encoding="utf-8")
    script = (
        "const calls = [];"
        "globalThis.fetch = async (url, init = {}) => {"
        "  calls.push({url, method: init.method || 'GET', headers: init.headers || {}, body: init.body ?? null});"
        "  return {ok: true, status: 200, json: async () => ({}), text: async () => '{}'};"
        "};"
        f"const m = await import({json.dumps((tmp_path / 'campaigns.ts').as_uri())});"
        f"const out = await (async () => {{ {body} }})();"
        "console.log(JSON.stringify(out));"
    )
    run = subprocess.run([node, "--experimental-strip-types", "--no-warnings", "--input-type=module", "-e", script],
                         capture_output=True, text=True, timeout=120)
    if run.returncode != 0 and "strip-types" in run.stderr:
        pytest.skip("this Node can't run TypeScript directly")
    assert run.returncode == 0, run.stderr
    return json.loads(run.stdout)


def test_parse_link_sends_the_link_in_a_post_body_never_a_query_string(tmp_path):
    got = _run(tmp_path, f"await m.discord.parseLink({json.dumps(WEBHOOK)}); return calls")
    assert len(got) == 1
    call = got[0]
    assert call["url"] == "http://engine/discord/parse-link"
    assert "?" not in call["url"] and TOKEN not in call["url"]
    assert call["method"] == "POST"
    assert json.loads(call["body"]) == {"link": WEBHOOK}
    assert "X-Clips-Kitty-Session" not in call["headers"]  # it changes nothing


def test_every_changing_call_sends_the_session_header_and_webhooks_go_in_bodies(tmp_path):
    body = (
        f"await m.discord.addDestination({{webhook_url: {json.dumps(WEBHOOK)}, label: 'Example'}});"
        f"await m.discord.updateDestination(3, {{webhook_url: {json.dumps(WEBHOOK)}}});"
        "await m.discord.setClientId('100000000000000002');"
        "await m.discord.connect();"
        "await m.discord.disconnect();"
        "await m.discord.authorizeDestination('Example');"
        "await m.discord.removeDestination(3);"
        "await m.discord.testDestination(3);"
        "await m.discord.sendPublished(7, {destination_id: 3, platform: 'youtube', provider: 'woopsocial'});"
        "return calls"
    )
    got = _run(tmp_path, body)
    assert [(c["method"], c["url"].replace("http://engine", "")) for c in got] == [
        ("POST", "/discord/destinations"),
        ("PATCH", "/discord/destinations/3"),
        ("PATCH", "/discord/settings"),
        ("POST", "/discord/connect"),
        ("POST", "/discord/disconnect"),
        ("POST", "/discord/destinations/authorize"),
        ("DELETE", "/discord/destinations/3"),
        ("POST", "/discord/destinations/3/test"),
        ("POST", "/discord/clips/7/send"),
    ]
    assert all(c["headers"].get("X-Clips-Kitty-Session") == "sess-1" for c in got)
    assert all(TOKEN not in c["url"] for c in got)
    assert json.loads(got[0]["body"])["webhook_url"] == WEBHOOK
    assert json.loads(got[1]["body"]) == {"webhook_url": WEBHOOK}


def test_reading_calls_send_no_header(tmp_path):
    got = _run(tmp_path, "await m.discord.status(); await m.discord.destinations();"
                         "await m.discord.guilds(true); return calls")
    assert [c["url"].replace("http://engine", "") for c in got] == [
        "/discord/status", "/discord/destinations", "/discord/guilds?refresh=true"]
    assert all("X-Clips-Kitty-Session" not in c["headers"] for c in got)


def test_without_the_desktop_app_nothing_that_changes_is_sent(tmp_path):
    body = (
        f"try {{ await m.discord.addDestination({{webhook_url: {json.dumps(WEBHOOK)}}}); return 'sent' }}"
        "catch (e) { return [String(e.message), calls.length] }"
    )
    got = _run(tmp_path, body, secret="")
    assert got == ["Changing Discord settings needs the Clips Kitty desktop app.", 0]


def test_the_cards_four_states_and_initials(tmp_path):
    body = (
        "return ["
        "  m.discordCardState({configured: false, connected: false, expired: false}),"
        "  m.discordCardState({configured: true, connected: false, expired: false}),"
        "  m.discordCardState({configured: true, connected: true, expired: false}),"
        "  m.discordCardState({configured: true, connected: false, expired: true}),"
        "  m.discordCardState({configured: false, connected: true, expired: false}),"
        "  m.discordName({id: '1', username: 'example-user', global_name: ''}),"
        "  m.discordName({id: '1', username: 'example-user', global_name: 'Example User'}),"
        "  m.initials('Example User'), m.initials('example-server'), m.initials('kitty'), m.initials('')"
        "]"
    )
    assert _run(tmp_path, body) == ["not_set_up", "not_connected", "connected", "expired", "connected",
                                    "example-user", "Example User", "EU", "ES", "KI", "?"]


def test_an_edit_sends_only_what_changed_and_a_link_only_when_pasted(tmp_path):
    dest = {"id": 3, "label": "Example", "source": "pasted", "guild_id": "", "guild_name": "",
            "channel_id": "", "webhook_name": "Example hook", "is_forum": False, "thread_id": "",
            "state": "gone", "last_error": "", "has_token": False, "campaigns": 1}
    body = (
        f"const d = {json.dumps(dest)};"
        "const same = m.destinationDraft(d);"
        "return ["
        "  same,"
        "  m.destinationPatch(d, same),"
        "  m.destinationPatch(d, {...same, label: ' Renamed ', thread_id: '700000000000000001 '}),"
        "  m.destinationPatch(d, {...same, is_forum: true}),"
        f"  m.destinationPatch(d, {{...same, webhook_url: {json.dumps(' ' + WEBHOOK + ' ')}}}),"
        "  m.destinationPatch(d, {...same, webhook_url: '   '}),"
        "]"
    )
    assert _run(tmp_path, body) == [
        {"label": "Example", "is_forum": False, "thread_id": "", "webhook_url": ""},
        {},
        {"label": "Renamed", "thread_id": "700000000000000001"},
        {"is_forum": True},
        {"webhook_url": WEBHOOK},
        {},
    ]


def test_the_card_offers_edit_on_each_destination():
    """The engine's messages send people to a destination's Edit (a link
    pasted twice, a forum channel, a link missing on this PC), so the card
    must have one, and it must reach the PATCH route."""
    card = (LIB.parent / "components" / "DiscordCard.tsx").read_text(encoding="utf-8")
    assert "discord.updateDestination(d.id, patch)" in card
    assert "const patch = destinationPatch(d, draft)" in card
    assert "onClick={() => startEdit(d)}" in card
