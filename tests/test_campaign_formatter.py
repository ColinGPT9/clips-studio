"""The words of a submission: placeholders, the command a person types in
Discord, and the message a webhook posts.

Placeholders are filled once, by plain replacement: an unknown one stays as
typed. The link is never cut to fit Discord's 2,000 characters; the hashtags
and then the title are. A webhook never mentions anyone.
"""

import json
from datetime import datetime, timezone

import pytest

from campaigns import formatter
from campaigns.model import FAIL

TIKTOK = "https://www.tiktok.com/@example/video/7300000000000000001"


def _campaign(**fields):
    base = {"id": 1, "name": "Example Campaign", "method": "discord_assisted", "command_name": "",
            "command_args": "[]", "url_argument": "", "extra_fields": "[]", "message_template": "{url}",
            "thread_name_template": "{title}"}
    return {**base, **{k: json.dumps(v) if isinstance(v, (list, dict)) else v for k, v in fields.items()}}


def _sub(**fields):
    return {"id": 1, "campaign_id": 1, "video_id": "vid", "start_s": 0.0, "end_s": 29.6,
            "platform": "tiktok", "clip_id": 0, "clip_title": "A great moment", "post_url": TIKTOK,
            "account": "@example", "posted_at": "2026-10-01T12:00:00Z", "message_text": "",
            "extra_values": "{}", "arg_values": "{}", **fields}


VALUES = {"url": TIKTOK, "platform": "TikTok", "platform_id": "tiktok", "title": "A great moment",
          "campaign": "Example Campaign", "hashtags": "#clips #fun"}


# ---- values ---------------------------------------------------------------------------------------

def test_every_builtin_value_for_a_submission(db):
    db.upsert_video("vid", title="t", channel_name="Example Creator")
    db.set_video_source("vid", url="https://www.youtube.com/watch?v=xxxxxxxxxxx")
    clip_id = db.add_clip("vid", 0.0, 29.6, 90, "h", hashtags=json.dumps(["clips", "#fun#games"]))
    extras = [{"name": "goal", "default": "1000"}, {"name": "handle", "default": "x"}]
    values = formatter.values_for(db, _sub(clip_id=clip_id, extra_values=json.dumps({"handle": "me"})),
                                  _campaign(extra_fields=extras), discord_user="example_user")
    posted = datetime(2026, 10, 1, 12, 0, tzinfo=timezone.utc).astimezone().date().isoformat()
    assert values == {
        "url": TIKTOK, "platform": "TikTok", "platform_id": "tiktok", "title": "A great moment",
        "campaign": "Example Campaign", "account": "@example", "creator": "Example Creator",
        "source_url": "https://www.youtube.com/watch?v=xxxxxxxxxxx", "duration": "30",
        "posted_at": posted, "hashtags": "#clips #fun #games", "discord_user": "example_user",
        "goal": "1000", "handle": "me",
    }
    assert set(formatter.BUILTINS) <= set(values)


def test_values_that_are_not_known_are_blank_never_made_up(db):
    values = formatter.values_for(db, _sub(post_url="", account="", posted_at=""), _campaign())
    assert values["url"] == values["account"] == values["posted_at"] == ""
    assert values["creator"] == values["source_url"] == values["hashtags"] == values["discord_user"] == ""


# ---- placeholders -----------------------------------------------------------------------------------

def test_placeholders_are_filled_once_and_unknown_ones_kept():
    text = formatter.render("{title} {url} {nope} {URL} {} {{url}}", {**VALUES, "title": "{url}"})
    assert text == "{url} " + TIKTOK + " {nope} {URL} {} {" + TIKTOK + "}"
    assert formatter.render("{url} {title}", {"url": "x{title}", "title": "T"}) == "x{title} T"
    assert formatter.render("", VALUES) == ""


# ---- the command ------------------------------------------------------------------------------------

def _command_campaign(**fields):
    return _campaign(command_name="submit", url_argument="link",
                     command_args=[{"name": "platform", "value": "{platform_id}"},
                                   {"name": "link", "value": ""},
                                   {"name": "note", "value": "{title} by {creator}"}], **fields)


def test_the_command_puts_the_link_first():
    found = formatter.command(_command_campaign(), {**VALUES, "creator": "Example Creator"})
    assert found == {
        "name": "submit", "url_arg": "link",
        "args": [{"name": "link", "value": TIKTOK}, {"name": "platform", "value": "tiktok"},
                 {"name": "note", "value": "A great moment by Example Creator"}],
        "line": f"/submit link:{TIKTOK} platform:tiktok note:A great moment by Example Creator",
    }


def test_one_argument_can_be_replaced_without_retyping_the_command():
    found = formatter.command(_command_campaign(), VALUES, arg_values={"platform": "tt", "missing": "x"})
    assert [a["value"] for a in found["args"]][:2] == [TIKTOK, "tt"]
    assert formatter.command(_command_campaign(), VALUES, arg_values={"link": ""})["args"][0]["value"] == ""


def test_a_plain_message_campaign_has_no_command():
    assert formatter.command(_campaign(), VALUES) == {"name": "", "args": [], "url_arg": "", "line": ""}


def test_what_a_person_submits_by_hand():
    assert formatter.assisted_text(_command_campaign(), VALUES).startswith(f"/submit link:{TIKTOK}")
    message = _campaign(message_template="Clip for {campaign}: {url}")
    assert formatter.assisted_text(message, VALUES) == f"Clip for Example Campaign: {TIKTOK}"
    own = "My own words {url}"
    assert formatter.assisted_text(_command_campaign(), VALUES, message_text=own) == f"My own words {TIKTOK}"
    assert formatter.assisted_text(message, VALUES, message_text="   ") == f"Clip for Example Campaign: {TIKTOK}"


# ---- the webhook payload ----------------------------------------------------------------------------

def test_the_webhook_payload_never_mentions_anyone():
    payload = formatter.webhook_payload(_campaign(message_template="@everyone {url}"), _sub(), VALUES, None)
    assert payload == {"content": f"@everyone {TIKTOK}", "allowed_mentions": {"parse": []}}


def test_the_rows_own_text_replaces_the_message():
    payload = formatter.webhook_payload(_campaign(), _sub(message_text="Look: {url}"), VALUES, None)
    assert payload["content"] == f"Look: {TIKTOK}"


def test_a_thread_title_only_for_a_forum_without_a_thread():
    campaign = _campaign(thread_name_template="{campaign}: {title}")
    forum = {"is_forum": 1, "thread_id": ""}
    assert formatter.webhook_payload(campaign, _sub(), VALUES, forum)["thread_name"] == (
        "Example Campaign: A great moment")
    assert "thread_name" not in formatter.webhook_payload(campaign, _sub(), VALUES, {"is_forum": 0})
    assert "thread_name" not in formatter.webhook_payload(
        campaign, _sub(), VALUES, {"is_forum": 1, "thread_id": "500000000000000005"})
    long_title = formatter.webhook_payload(campaign, _sub(), {**VALUES, "title": "x" * 300}, forum)
    assert len(long_title["thread_name"]) == formatter.MAX_THREAD_NAME
    blank = formatter.webhook_payload(_campaign(thread_name_template=""), _sub(), {**VALUES, "title": ""}, forum)
    assert blank["thread_name"] == "Example Campaign"


def test_2000_characters_trims_the_hashtags_then_the_title_never_the_link():
    template = "{title}\n{url}\n{hashtags}"
    tags = " ".join(f"#tag{i}" for i in range(200))
    values = {**VALUES, "title": "T" * 1500, "hashtags": tags}
    content = formatter.webhook_payload(_campaign(message_template=template), _sub(), values, None)["content"]
    assert len(content) <= formatter.MAX_CONTENT
    assert TIKTOK in content
    assert content.startswith("T" * 1500)  # the hashtags went first, and that was enough
    assert content.split("\n")[2].startswith("#tag0 ")

    values = {**VALUES, "title": "T" * 2500, "hashtags": "#a #b"}
    content = formatter.webhook_payload(_campaign(message_template=template), _sub(), values, None)["content"]
    assert len(content) <= formatter.MAX_CONTENT and content.endswith(f"\n{TIKTOK}\n")
    assert content.split("\n")[0].endswith("…")

    # The link alone is over: nothing can be trimmed, and it is never cut.
    huge = {**VALUES, "url": "https://example.com/" + "x" * 2100}
    content = formatter.webhook_payload(_campaign(), _sub(), huge, None)["content"]
    assert content == huge["url"]


# ---- what stops the words going out -----------------------------------------------------------------

def test_an_empty_link_or_required_field_needs_filling_in():
    campaign = _campaign(extra_fields=[{"name": "goal", "label": "Views goal", "required": True},
                                       {"name": "note", "required": False}])
    checks = formatter.message_checks(campaign, {**VALUES, "url": "", "goal": "", "note": ""},
                                      method="discord_assisted")
    assert [(c.name, c.result, c.message) for c in checks] == [
        ("fields", FAIL, "Fill in the post's link"), ("fields", FAIL, "Fill in Views goal")]
    assert formatter.message_checks(campaign, {**VALUES, "goal": "5"}, method="discord_assisted") == []


def test_a_command_typed_by_hand_that_leaves_out_the_link_is_stopped():
    linkless = _campaign(command_name="submit", command_args=[{"name": "platform", "value": "{platform}"}])
    checks = formatter.message_checks(linkless, VALUES, method="discord_assisted")
    assert [(c.name, c.result) for c in checks] == [("fields", FAIL)]
    assert "leaves out the post's link" in checks[0].message
    # The form's preview says so before the campaign is saved.
    shown = formatter.preview(linkless, None)
    assert shown["problems"] == [checks[0].message]
    # The row's own words replace the command, and a webhook never runs one.
    assert formatter.message_checks(linkless, VALUES, method="discord_assisted", message_text="{url}") == []
    assert formatter.message_checks(linkless, VALUES, method="discord_webhook") == []
    # A command that takes the link, or no argument at all, is fine.
    assert formatter.message_checks(_command_campaign(), VALUES, method="discord_assisted") == []
    assert formatter.message_checks(_campaign(command_name="submit"), VALUES, method="discord_assisted") == []


def test_a_webhook_message_still_too_long_or_empty_is_stopped():
    long_link = {**VALUES, "url": "https://example.com/" + "x" * 2100}
    checks = formatter.message_checks(_campaign(), long_link, method="discord_webhook")
    assert [c.name for c in checks] == ["length"]
    assert formatter.message_checks(_campaign(), long_link, method="discord_assisted") == []
    empty = formatter.message_checks(_campaign(message_template="{title}"), {**VALUES, "title": ""},
                                     method="discord_webhook")
    assert [c.message for c in empty] == ["The message is empty"]
    own = formatter.message_checks(_campaign(), VALUES, method="discord_webhook", message_text="x" * 2001)
    assert [c.name for c in own] == ["length"]


# ---- the form's preview -----------------------------------------------------------------------------

def test_the_preview_shows_what_the_settings_produce():
    campaign = _command_campaign(message_template="New clip: {url} {goal}", method="discord_webhook",
                                 extra_fields=[{"name": "goal", "default": "1k", "required": True}])
    shown = formatter.preview(campaign, None)
    assert shown["message"] == f"New clip: {formatter.SAMPLE['url']} 1k"
    assert shown["length"] == len(shown["message"])
    assert shown["command"]["line"].startswith(f"/submit link:{formatter.SAMPLE['url']} ")
    assert shown["thread_name"] == formatter.SAMPLE["title"]
    assert shown["problems"] == []

    real = formatter.preview(campaign, {"url": "", "title": "Mine"})
    assert real["problems"] == ["Fill in the post's link"]
    assert real["thread_name"] == "Mine"


@pytest.mark.parametrize("sample", [None, {}])
def test_the_preview_names_the_campaign(sample):
    assert formatter.preview(_campaign(name="", message_template="{campaign}"), sample)["message"] == (
        "Example Campaign")
