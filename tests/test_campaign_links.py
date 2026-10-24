"""Discord links pasted into Clips Kitty: a campaign's channel (or a message in
it), and a webhook URL for a destination.

A webhook URL's last part is a token that lets anyone post to the channel, so
no parse result, problem message or exception may carry it, and the app-wide
scrubber takes it out of any text it cleans.
"""

import pytest

from campaigns import links
from core.scrub import scrub_secrets

GUILD, CHANNEL, MESSAGE = "100000000000000001", "200000000000000002", "300000000000000003"
HOOK = "400000000000000004"
TOKEN = "Example-not-a-real-token_0123456789abcdefghijklmnopqrstuvwxyzABCDEFGH"


@pytest.mark.parametrize("host", ["discord.com", "ptb.discord.com", "canary.discord.com",
                                  "discordapp.com", "www.discord.com"])
def test_channel_and_message_links_on_every_discord_host(host):
    channel = links.parse_link(f"https://{host}/channels/{GUILD}/{CHANNEL}")
    assert channel.as_dict() == {"kind": "channel", "guild_id": GUILD, "channel_id": CHANNEL,
                                 "message_id": "", "problem": ""}
    message = links.parse_link(f"https://{host}/channels/{GUILD}/{CHANNEL}/{MESSAGE}")
    assert (message.kind, message.guild_id, message.channel_id, message.message_id) == (
        "message", GUILD, CHANNEL, MESSAGE)
    guild = links.parse_link(f"https://{host}/channels/{GUILD}")
    assert (guild.kind, guild.guild_id, guild.channel_id) == ("guild", GUILD, "")


@pytest.mark.parametrize("pasted", [
    f"  https://discord.com/channels/{GUILD}/{CHANNEL}  ",
    f"<https://discord.com/channels/{GUILD}/{CHANNEL}>",
    f"https://discord.com/channels/{GUILD}/{CHANNEL}/",
    f"https://discord.com/channels/{GUILD}/{CHANNEL}?ref=example#top",
    f"discord.com/channels/{GUILD}/{CHANNEL}",
    f"HTTPS://Discord.com/channels/{GUILD}/{CHANNEL}",
])
def test_a_channel_link_as_people_paste_it(pasted):
    found = links.parse_link(pasted)
    assert (found.kind, found.guild_id, found.channel_id) == ("channel", GUILD, CHANNEL)


@pytest.mark.parametrize("pasted, kind, problem", [
    ("https://discord.gg/example", "invite", "invite link"),
    ("https://discord.com/invite/example", "invite", "invite link"),
    ("https://discordapp.com/invite/example", "invite", "invite link"),
    (f"https://discord.com/channels/@me/{CHANNEL}", "none", "direct message"),
    (f"https://discord.com/channels/{GUILD[:10]}/{CHANNEL}", "none", "isn't a Discord channel link"),
    ("https://example.com/channels/1/2", "none", "isn't a Discord channel link"),
    ("just some words", "none", "isn't a Discord channel link"),
])
def test_links_that_are_not_a_channel(pasted, kind, problem):
    found = links.parse_link(pasted)
    assert found.kind == kind and problem in found.problem
    assert not (found.guild_id or found.channel_id)
    with pytest.raises(ValueError, match=problem):
        links.parse_channel_link(pasted)


def test_an_empty_link_is_no_link_not_a_mistake():
    assert links.parse_link("").as_dict() == {"kind": "none", "guild_id": "", "channel_id": "",
                                              "message_id": "", "problem": ""}
    assert links.parse_channel_link("   ").kind == "none"


def test_the_invite_answer_says_how_to_copy_the_channels_link():
    assert "right-click the channel → Copy Link" in links.parse_link("https://discord.gg/example").problem


@pytest.mark.parametrize("url", [
    f"https://discord.com/api/webhooks/{HOOK}/{TOKEN}",
    f"https://discord.com/api/v10/webhooks/{HOOK}/{TOKEN}",
    f"https://discordapp.com/api/webhooks/{HOOK}/{TOKEN}",
    f"https://ptb.discord.com/api/webhooks/{HOOK}/{TOKEN}",
    f"https://canary.discord.com/api/v9/webhooks/{HOOK}/{TOKEN}?wait=true",
])
def test_a_webhook_url_is_parsed_and_its_token_never_echoed(url):
    assert links.parse_webhook_url(url) == (HOOK, TOKEN)

    found = links.parse_link(url)
    assert found.kind == "webhook"
    assert found.problem == "That's a webhook link: add it in Settings → Discord → Destinations"
    assert TOKEN not in repr(found) and TOKEN not in str(found.as_dict())
    with pytest.raises(ValueError) as caught:
        links.parse_channel_link(url)
    assert TOKEN not in str(caught.value)

    assert TOKEN not in scrub_secrets(f"sending to {url} failed")


@pytest.mark.parametrize("url", [
    f"https://discord.com/api/webhooks/{HOOK}/short-token",
    f"https://ptb.discordapp.com/api/v10/webhooks/{HOOK}/short-token?wait=true",
])
def test_the_scrubber_takes_out_a_webhook_url_whatever_its_token_looks_like(url):
    # A token too short or plain for the generic key patterns is still taken
    # out, with the whole webhook path.
    cleaned = scrub_secrets(f"POST {url} answered 404")
    assert "short-token" not in cleaned and "[redacted]" in cleaned


@pytest.mark.parametrize("pasted", [
    f"https://discord.com/channels/{GUILD}/{CHANNEL}",
    f"https://discord.com/api/webhooks/{HOOK}",
    f"https://example.com/api/webhooks/{HOOK}/{TOKEN}",
    f"https://discord.com/api/webhooks/123/{TOKEN}",
    "",
])
def test_anything_else_is_not_a_webhook_and_the_answer_does_not_repeat_it(pasted):
    with pytest.raises(ValueError, match="isn't a Discord webhook link") as caught:
        links.parse_webhook_url(pasted)
    assert TOKEN not in str(caught.value)


def test_links_back_into_discord():
    assert links.channel_url(GUILD, CHANNEL) == f"https://discord.com/channels/{GUILD}/{CHANNEL}"
    assert links.channel_url(GUILD, CHANNEL, MESSAGE) == (
        f"https://discord.com/channels/{GUILD}/{CHANNEL}/{MESSAGE}")
    assert links.channel_url(GUILD) == f"https://discord.com/channels/{GUILD}"
    assert links.channel_url("", CHANNEL) == ""
    assert links.channel_url(GUILD, "", MESSAGE) == ""
    assert links.channel_url(GUILD, "../api") == ""
