"""What we send to videos.insert.

These run with no Google libraries installed, which is the whole reason the
body-building lives in publish/metadata.py rather than inside the uploader.
"""

from pathlib import Path

from publish.base import PublishRequest
from publish.metadata import (
    build_insert_body,
    clamp_description,
    clamp_tags,
    clamp_title,
    creator_tag,
    description_with_hashtags,
    parts_for,
    with_common_block,
)


def _request(**kwargs) -> PublishRequest:
    kwargs.setdefault("video_path", Path("clip.mp4"))
    kwargs.setdefault("title", "A title")
    return PublishRequest(**kwargs)


def test_title_is_cut_at_a_hundred_characters():
    assert len(clamp_title("x" * 500)) == 100


def test_title_keeps_a_normal_one_intact():
    assert clamp_title("  Best moment of the stream  ") == "Best moment of the stream"


def test_angle_brackets_are_stripped():
    """YouTube rejects the whole upload for these."""
    assert clamp_title("a <b> c") == "a b c"
    assert "<" not in clamp_description("<script>")


def test_description_is_cut_at_five_thousand():
    assert len(clamp_description("y" * 9000)) == 5000


def test_tags_drop_whole_entries_rather_than_truncating():
    tags = ["a" * 200, "b" * 200, "c" * 200]
    kept = clamp_tags(tags)
    assert kept == ["a" * 200, "b" * 200], "the third does not fit and must be dropped entirely"
    assert all(len(t) == 200 for t in kept)


def test_tags_lose_their_hash_and_their_blanks():
    assert clamp_tags(["#gaming", "  ", "#speedrun", ""]) == ["gaming", "speedrun"]


def test_a_tag_with_a_space_costs_two_extra_characters():
    """YouTube quotes multi-word tags, and the quotes count against the budget."""
    # 166 * 3 = 498 plus 2 separators = 500 exactly when unquoted.
    unquoted = ["a" * 166, "b" * 166, "c" * 166]
    assert len(clamp_tags(unquoted)) == 3

    spaced = ["a " + "a" * 164, "b " + "b" * 164, "c " + "c" * 164]
    assert len(clamp_tags(spaced)) < 3, "quoting pushes these over the 500-char budget"


def test_hashtags_are_capped_at_five():
    """Five is our rule. Fifteen is where YouTube ignores every one of them."""
    text = description_with_hashtags("Body", [f"#t{i}" for i in range(40)])
    assert text.count("#") == 5


def test_the_creator_tag_leads_and_survives_the_cap():
    # The cut takes the tail, so the one tag that must always appear has to be
    # at the front. This is the whole point of the feature.
    text = description_with_hashtags(
        "Body", [f"#t{i}" for i in range(40)], creator="creatorname"
    )
    assert text.count("#") == 5
    assert text.splitlines()[-1].split(" ")[0] == "#creatorname"


def test_a_channel_name_becomes_a_usable_tag():
    assert creator_tag("creatorname") == "#creatorname"
    assert creator_tag("Some Streamer") == "#SomeStreamer"
    assert creator_tag("  ") == ""
    assert creator_tag("") == ""


def test_the_creator_tag_is_not_repeated_when_the_clip_already_has_it():
    text = description_with_hashtags("Body", ["#CreatorName", "#funny"], creator="creatorname")
    assert text.lower().count("#creatorname") == 1
    assert "#funny" in text


def test_publishing_the_same_clip_twice_does_not_stack_a_second_block():
    # The editor prefills its box with what went up last time, so the input to
    # the second publish already ends in the line this function added.
    first = description_with_hashtags("Watch this", ["#funny"], creator="creatorname")
    again = description_with_hashtags(first, ["#funny"], creator="creatorname")
    assert again == first
    assert again.count("#creatorname") == 1


def test_a_description_that_ends_in_the_users_own_hashtags_is_not_doubled():
    written = "Watch this" + chr(10) + chr(10) + "#mytag"
    text = description_with_hashtags(written, ["#mytag"], creator="")
    assert text.count("#mytag") == 1
    assert text.startswith("Watch this")


def test_a_creator_with_no_usable_characters_is_skipped():
    text = description_with_hashtags("Body", ["#funny"], creator="!!!")
    assert text.endswith("#funny")


def test_hashtags_are_appended_below_the_description():
    text = description_with_hashtags("Watch this", ["#funny", "clip"])
    assert text.startswith("Watch this")
    assert text.endswith("#funny #clip"), "a bare tag should gain its hash"


def test_hashtags_alone_still_produce_a_description():
    assert description_with_hashtags("", ["#a"]) == "#a"


def test_no_hashtags_leaves_the_description_alone():
    assert description_with_hashtags("Just this", []) == "Just this"


# ---- the standing block ----------------------------------------------------


def test_the_standing_block_goes_under_the_description():
    text = with_common_block("Watch this", "Live: twitch.tv/example")
    assert text == "Watch this" + chr(10) * 2 + "Live: twitch.tv/example"


def test_an_empty_standing_block_changes_nothing():
    assert with_common_block("Watch this", "") == "Watch this"
    assert with_common_block("Watch this", "   ") == "Watch this"


def test_the_standing_block_is_not_repeated_on_a_second_publish():
    once = with_common_block("Watch this", "Discord: discord.gg/example")
    twice = with_common_block(once, "Discord: discord.gg/example")
    assert twice == once


def test_a_standing_block_ending_in_a_hashtag_survives_republishing():
    # The reason the trailing-hashtag strip takes exactly one line: this block
    # ends in a tag of its own, and a greedy strip would eat it and then put
    # the whole block back, growing the description on every publish.
    block = "Watch me live: twitch.tv/example" + chr(10) + "#streamer"
    first = description_with_hashtags(
        with_common_block("A clip", block), ["#funny"], creator="creatorname"
    )
    again = description_with_hashtags(
        with_common_block(first, block), ["#funny"], creator="creatorname"
    )
    assert again == first
    assert first.count("twitch.tv/example") == 1
    assert first.count("#streamer") == 1


def test_the_whole_description_reads_in_the_right_order():
    text = description_with_hashtags(
        with_common_block("The clip itself.", "Live: twitch.tv/example"),
        ["#funny"],
        creator="creatorname",
    )
    lines = [ln for ln in text.splitlines() if ln.strip()]
    assert lines == [
        "The clip itself.",
        "Live: twitch.tv/example",
        "#creatorname #funny",
    ]


# ---- the body itself -------------------------------------------------------


def test_body_has_only_fields_the_api_accepts():
    body = build_insert_body(_request(tags=["a"], description="d"))
    assert set(body) <= {"snippet", "status", "recordingDetails", "localizations"}
    assert set(body["snippet"]) <= {
        "title", "description", "tags", "categoryId", "defaultLanguage",
    }
    assert set(body["status"]) <= {
        "privacyStatus", "publishAt", "license", "embeddable",
        "publicStatsViewable", "selfDeclaredMadeForKids", "containsSyntheticMedia",
    }


def test_scheduling_forces_the_video_private():
    """publishAt is rejected on anything but a private video, so the pairing is
    enforced here instead of trusted to every caller."""
    body = build_insert_body(_request(privacy="public", publish_at="2026-12-01T18:00:00Z"))
    assert body["status"]["privacyStatus"] == "private"
    assert body["status"]["publishAt"] == "2026-12-01T18:00:00Z"


def test_publishing_now_carries_no_publish_at():
    body = build_insert_body(_request(privacy="public"))
    assert body["status"]["privacyStatus"] == "public"
    assert "publishAt" not in body["status"]


def test_synthetic_media_is_only_sent_when_declared():
    assert "containsSyntheticMedia" not in build_insert_body(_request())["status"]
    assert build_insert_body(_request(contains_synthetic_media=True))["status"][
        "containsSyntheticMedia"
    ] is True


def test_made_for_kids_is_always_sent():
    """YouTube requires an answer; omitting it is not the same as 'no'."""
    assert build_insert_body(_request())["status"]["selfDeclaredMadeForKids"] is False
    assert build_insert_body(_request(made_for_kids=True))["status"][
        "selfDeclaredMadeForKids"
    ] is True


def test_empty_tag_list_sends_no_tags_key():
    assert "tags" not in build_insert_body(_request(tags=[]))["snippet"]


def test_optional_blocks_appear_only_when_used():
    plain = build_insert_body(_request())
    assert "recordingDetails" not in plain and "localizations" not in plain
    assert parts_for(_request()) == "snippet,status"

    rich = _request(
        recording_date="2026-09-01T00:00:00Z",
        localizations={"es": {"title": "Hola", "description": "d"}},
    )
    body = build_insert_body(rich)
    assert body["recordingDetails"]["recordingDate"] == "2026-09-01T00:00:00Z"
    assert body["localizations"]["es"]["title"] == "Hola"
    assert parts_for(rich) == "snippet,status,recordingDetails,localizations"
