"""Every rule a saved campaign keeps, in clean_campaign rather than in the
route's model, so this runs in CI's main job, which has no FastAPI.

clean_campaign answers with the fields ready to store, or a ValueError whose
message is what the campaign form shows.
"""

import json

import pytest

from campaigns.model import EDITABLE, clean_campaign

GUILD, CHANNEL, MESSAGE = "100000000000000001", "200000000000000002", "300000000000000003"
TOKEN = "Example-not-a-real-token_0123456789abcdefghijklmnopqrstuvwxyzABCDEFGH"


def ok(fields, **kw):
    return clean_campaign({"name": "Example Campaign", **fields}, partial=kw.pop("partial", False), **kw)


def refused(fields, match, **kw):
    with pytest.raises(ValueError, match=match):
        ok(fields, **kw)


# ---- names, methods and where submissions go -----------------------------------------------

def test_a_name_is_all_a_new_campaign_needs():
    assert clean_campaign({"name": "  Example Campaign "}, partial=False) == {"name": "Example Campaign"}
    with pytest.raises(ValueError, match="name"):
        clean_campaign({}, partial=False)
    refused({"name": "   "}, "name")
    refused({"name": "x" * 101}, "too long")


@pytest.mark.parametrize("fields, match", [
    ({"method": "bot"}, "how this campaign takes"),
    ({"method": "discord_webhook"}, "Pick the webhook"),
    ({"method": "discord_webhook", "destination_id": 0}, "Pick the webhook"),
    ({"method": "website"}, "https:// link"),
    ({"method": "website", "website_url": "http://example.com/campaign"}, "https://"),
    ({"method": "website", "website_url": "https://example.com/a campaign"}, "https://"),
    ({"method": "website", "website_url": "example.com/campaign"}, "https://"),
    ({"destination_id": -1}, "whole number"),
    ({"destination_id": 2.5}, "whole number"),
    ({"destination_id": True}, "whole number"),
])
def test_methods_and_what_each_needs(fields, match):
    refused(fields, match)


def test_methods_that_have_what_they_need():
    assert ok({"method": "discord_assisted"})["method"] == "discord_assisted"
    assert ok({"method": "discord_webhook", "destination_id": 3})["destination_id"] == 3
    page = "https://example.com/campaigns/example"
    assert ok({"method": "website", "website_url": page})["website_url"] == page


@pytest.mark.parametrize("link, ids", [
    (f"https://discord.com/channels/{GUILD}/{CHANNEL}", (GUILD, CHANNEL)),
    (f"https://discord.com/channels/{GUILD}/{CHANNEL}/{MESSAGE}", (GUILD, CHANNEL)),
    (f"https://ptb.discord.com/channels/{GUILD}/{CHANNEL}", (GUILD, CHANNEL)),
    (f"https://discordapp.com/channels/{GUILD}/{CHANNEL}", (GUILD, CHANNEL)),
    (f"https://discord.com/channels/{GUILD}", (GUILD, "")),
])
def test_a_channel_link_fills_in_the_server_and_channel(link, ids):
    out = ok({"channel_link": link})
    assert out["channel_link"] == link
    assert (out.get("guild_id"), out.get("channel_id")) == ids


@pytest.mark.parametrize("link, match", [
    ("https://discord.gg/example", "invite link.*Copy Link"),
    ("https://discord.com/invite/example", "invite link.*Copy Link"),
    (f"https://discord.com/api/webhooks/{GUILD}/{TOKEN}", "webhook link"),
    (f"https://discord.com/channels/@me/{CHANNEL}", "direct message"),
    ("https://example.com/channels/1", "isn't a Discord channel link"),
])
def test_links_that_are_not_a_channel_are_refused(link, match):
    with pytest.raises(ValueError, match=match) as caught:
        ok({"channel_link": link})
    assert TOKEN not in str(caught.value)


def test_a_channel_link_and_typed_ids_must_agree():
    link = f"https://discord.com/channels/{GUILD}/{CHANNEL}"
    assert ok({"channel_link": link, "channel_id": CHANNEL})["channel_id"] == CHANNEL
    refused({"channel_link": link, "channel_id": MESSAGE}, "don't match")
    # A server's link names no channel, so it never keeps another one's.
    server = f"https://discord.com/channels/{MESSAGE}"
    stored = {"guild_id": GUILD, "channel_id": CHANNEL}
    assert clean_campaign({"channel_link": server}, partial=True, current=stored) == {
        "channel_link": server, "guild_id": MESSAGE, "channel_id": ""}
    assert ok({"channel_link": server, "channel_id": CHANNEL})["channel_id"] == CHANNEL
    refused({"guild_id": "12345"}, "17 to 20 digits")
    refused({"channel_id": 200000000000000002}, "must be text")
    assert ok({"guild_id": GUILD, "channel_id": CHANNEL})["guild_id"] == GUILD


# ---- the command ----------------------------------------------------------------------------

@pytest.mark.parametrize("given, stored", [("/submit", "submit"), ("submit", "submit"),
                                           ("clip-submit_2", "clip-submit_2"), ("", "")])
def test_the_command_is_kept_without_its_slash(given, stored):
    assert ok({"command_name": given})["command_name"] == stored


@pytest.mark.parametrize("given", ["submit clip", "/" + "x" * 33, "//submit", "submit!"])
def test_a_command_is_one_name(given):
    refused({"command_name": given}, "name only")


def test_command_arguments():
    args = [{"name": "link", "value": ""}, {"name": "platform", "value": "{platform_id}"}]
    out = ok({"command_name": "submit", "command_args": args, "url_argument": "link"})
    assert json.loads(out["command_args"]) == args and out["url_argument"] == "link"
    # Given as JSON text, as the form may send it.
    assert json.loads(ok({"command_args": json.dumps(args)})["command_args"]) == args


@pytest.mark.parametrize("args, match", [
    ([{"name": "post link", "value": ""}], "a name is letters"),
    ([{"name": "", "value": ""}], "a name is letters"),
    ([{"name": "x" * 33, "value": ""}], "a name is letters"),
    ([{"name": "link"}, {"name": "link"}], "twice"),
    (["link"], "needs a name and a value"),
    ([{"name": "link", "value": "x" * 2001}], "too long"),
    ([{"name": f"a{i}"} for i in range(26)], "at most 25"),
    ("not json", "valid JSON"),
    ({"name": "link"}, "must be a list"),
])
def test_bad_command_arguments(args, match):
    refused({"command_args": args}, match)


def test_the_links_argument_is_one_of_the_arguments():
    args = [{"name": "link", "value": ""}]
    refused({"command_args": args, "url_argument": "url"}, "one of the command's arguments")
    refused({"url_argument": "url"}, "one of the command's arguments")
    # An edit of the link's argument alone can't be checked without the
    # stored arguments, and is checked against them when they are given.
    assert clean_campaign({"url_argument": "url"}, partial=True) == {"url_argument": "url"}
    stored = {"command_args": json.dumps(args)}
    with pytest.raises(ValueError, match="one of the command's arguments"):
        clean_campaign({"url_argument": "url"}, partial=True, current=stored)
    assert clean_campaign({"url_argument": "link"}, partial=True, current=stored)["url_argument"] == "link"


LINKLESS = "Pick which argument takes the post's link"


@pytest.mark.parametrize("args, url_argument, carries", [
    ([{"name": "link", "value": ""}, {"name": "platform", "value": "{platform}"}], "link", True),
    ([{"name": "link", "value": "{url}?ref=clips"}], "link", True),
    ([{"name": "post", "value": "{url}"}, {"name": "platform", "value": "{platform}"}], "", True),
    # No argument at all: the bot may ask for the link itself after the command.
    ([], "", True),
    ([{"name": "platform", "value": "{platform}"}], "", False),
    ([{"name": "link", "value": ""}], "", False),
    ([{"name": "link", "value": "{title}"}, {"name": "platform", "value": "{platform}"}], "link", False),
])
def test_a_command_with_arguments_takes_the_posts_link_in_one(args, url_argument, carries):
    fields = {"command_name": "/submit", "command_args": args, "url_argument": url_argument}
    if carries:
        assert ok(fields)["command_name"] == "submit"
    else:
        refused(fields, LINKLESS)
    # A campaign posting a plain message has no command to check.
    assert ok({**fields, "command_name": ""})["command_name"] == ""


def test_an_edit_of_the_command_is_checked_against_what_is_stored():
    stored = {"command_name": "submit", "url_argument": "link",
              "command_args": json.dumps([{"name": "link", "value": ""}])}
    with pytest.raises(ValueError, match=LINKLESS):
        clean_campaign({"command_args": [{"name": "link", "value": "{title}"}]}, partial=True,
                       current=stored)
    with pytest.raises(ValueError, match=LINKLESS):
        clean_campaign({"url_argument": ""}, partial=True, current=stored)
    edit = {"command_args": [{"name": "link", "value": ""}, {"name": "views", "value": "{views}"}]}
    assert "command_args" in clean_campaign(edit, partial=True, current=stored)


# ---- extra fields and templates -------------------------------------------------------------

def test_extra_fields():
    out = ok({"extra_fields": [{"name": "views_goal", "default": "1000", "required": True},
                               {"name": "notes_2", "label": "Notes"}]})
    assert json.loads(out["extra_fields"]) == [
        {"name": "views_goal", "label": "views_goal", "default": "1000", "required": True},
        {"name": "notes_2", "label": "Notes", "default": "", "required": False},
    ]


@pytest.mark.parametrize("fields, match", [
    ([{"name": "Views"}], "lower-case"),
    ([{"name": "views-goal"}], "lower-case"),
    ([{"name": "title"}], "fills in itself"),
    ([{"name": "url"}], "fills in itself"),
    ([{"name": "discord_user"}], "fills in itself"),
    ([{"name": "goal"}, {"name": "goal"}], "twice"),
    ([{"name": "goal", "required": "yes"}], "true or false"),
])
def test_bad_extra_fields(fields, match):
    refused({"extra_fields": fields}, match)


def test_templates_are_at_most_2000_characters():
    assert ok({"message_template": "x" * 2000})["message_template"] == "x" * 2000
    refused({"message_template": "x" * 2001}, "at most 2,000")
    refused({"thread_name_template": "x" * 2001}, "at most 2,000")
    refused({"message_template": "  "}, "can't be empty")
    assert ok({"thread_name_template": ""})["thread_name_template"] == ""


# ---- rules ----------------------------------------------------------------------------------

@pytest.mark.parametrize("given, stored", [
    ("2026-12-31T23:00:00Z", "2026-12-31T23:00:00Z"),
    ("2026-12-31T23:00:00+02:00", "2026-12-31T21:00:00Z"),
    ("2026-12-31T23:00:00", "2026-12-31T23:00:00Z"),
    ("2026-12-31T23:00:00.500Z", "2026-12-31T23:00:00Z"),
    ("", ""),
])
def test_the_deadline_is_kept_in_utc(given, stored):
    assert ok({"deadline": given})["deadline"] == stored


@pytest.mark.parametrize("given", ["tomorrow", "31/12/2026", "2026-13-01T00:00:00Z"])
def test_a_deadline_that_cant_be_read(given):
    refused({"deadline": given}, "can read")


def test_platforms():
    assert json.loads(ok({"platforms": ["TikTok", "youtube", "tiktok"]})["platforms"]) == ["tiktok", "youtube"]
    assert json.loads(ok({"platforms": '["instagram"]'})["platforms"]) == ["instagram"]
    assert ok({"platforms": []})["platforms"] == "[]"
    for platform in ("discord", "telegram", "google_business", "myspace"):
        refused({"platforms": [platform]}, "can't submit")
    refused({"platforms": "tiktok"}, "valid JSON")


def test_accounts_hashtags_and_mentions_are_kept_bare():
    out = ok({"required_accounts": {"tiktok": " @example ", "youtube": ""},
              "required_hashtags": ["#Clips", "clips", " fun "],
              "required_mentions": ["@example", "example.co"]})
    assert json.loads(out["required_accounts"]) == {"tiktok": "example"}
    assert json.loads(out["required_hashtags"]) == ["Clips", "fun"]
    assert json.loads(out["required_mentions"]) == ["example", "example.co"]
    refused({"required_accounts": {"myspace": "x"}}, "isn't a platform")
    refused({"required_hashtags": ["two words"]}, "space")
    refused({"required_accounts": ["tiktok"]}, "set of named values")


@pytest.mark.parametrize("fields, match", [
    ({"min_seconds": 61, "max_seconds": 60}, "can't be longer"),
    ({"min_seconds": -1}, "negative"),
    ({"max_seconds": "long"}, "number"),
    ({"max_seconds": float("inf")}, "number"),
    ({"enabled": "yes"}, "true or false"),
    ({"review_first": 2}, "true or false"),
    ({"requirements": 5}, "text"),
])
def test_bad_rules(fields, match):
    refused(fields, match)


def test_rules_that_hold():
    out = ok({"min_seconds": 10, "max_seconds": 0, "approval_required": False, "trust_account": True,
              "review_first": 1, "enabled": 0, "required_text": " Clips Kitty "})
    assert out["min_seconds"] == 10.0 and out["max_seconds"] == 0.0
    assert (out["approval_required"], out["trust_account"], out["review_first"], out["enabled"]) == (0, 1, 1, 0)
    assert out["required_text"] == "Clips Kitty"


def test_routing_filters():
    out = ok({"filters": {"plugins": ["example-dev/demo"], "modes": ["sports", "regular"],
                          "creator_ids": [3, 3]}})
    assert json.loads(out["filters"]) == {"plugins": ["example-dev/demo"],
                                          "modes": ["sports", "regular"], "creator_ids": [3]}
    assert json.loads(ok({"filters": {}})["filters"]) == {"plugins": [], "modes": [], "creator_ids": []}
    refused({"filters": {"plugins": ["Example Dev/Demo"]}}, "plugin id")
    refused({"filters": {"modes": ["unknown"]}}, "kind of clips")
    refused({"filters": {"creator_ids": ["three"]}}, "whole number")
    refused({"filters": {"games": []}}, "Unknown routing")


# ---- the shape of the call ----------------------------------------------------------------------

def test_unknown_and_fixed_fields_are_refused():
    refused({"bogus": 1}, "Unknown campaign fields")
    for fixed in ("id", "archived", "created_at", "updated_at"):
        assert fixed not in EDITABLE
        refused({fixed: 1}, "Unknown campaign fields")
    with pytest.raises(ValueError):
        clean_campaign(["name"], partial=False)


def test_an_edit_checks_only_what_it_changes():
    assert clean_campaign({}, partial=True) == {}
    assert clean_campaign({"notes": "x"}, partial=True) == {"notes": "x"}
    with pytest.raises(ValueError, match="name"):
        clean_campaign({"name": ""}, partial=True)
    # A rule across two fields: unchecked without the stored campaign, checked with it.
    assert clean_campaign({"method": "discord_webhook"}, partial=True) == {"method": "discord_webhook"}
    with pytest.raises(ValueError, match="Pick the webhook"):
        clean_campaign({"method": "discord_webhook"}, partial=True, current={"destination_id": 0})
    assert clean_campaign({"method": "discord_webhook"}, partial=True,
                          current={"destination_id": 4}) == {"method": "discord_webhook"}
    with pytest.raises(ValueError, match="can't be longer"):
        clean_campaign({"min_seconds": 90}, partial=True, current={"max_seconds": 60.0})
    with pytest.raises(ValueError, match="Pick the webhook"):
        clean_campaign({"method": "discord_webhook", "destination_id": 0}, partial=True)
