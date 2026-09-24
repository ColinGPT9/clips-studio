"""Which creator profile a channel's videos land on.

The bug these exist for: a watched Twitch channel typed as "somestreamer"
and its downloads reporting "SomeStreamer" were two profiles, so what one
learned the other never saw.
"""

from creator import identity


def test_case_does_not_make_a_second_profile(db):
    first = identity.resolve(db, "tw_1", "somestreamer", platform="twitch")
    assert identity.resolve(db, "tw_2", "SomeStreamer", platform="twitch") == first
    assert db.conn.execute("SELECT COUNT(*) FROM creators").fetchone()[0] == 1


def test_the_same_name_on_another_platform_is_its_own_profile(db):
    twitch = identity.resolve(db, "tw_1", "somestreamer", platform="twitch")
    assert identity.resolve(db, "abcdefghijk", "SomeStreamer", platform="youtube") != twitch


def test_an_exact_match_wins_over_one_that_differs_in_case(db):
    # Two profiles made before matching ignored case keep resolving as they did.
    lower = identity.resolve(db, "tw_1", "somestreamer", platform="twitch")
    db.conn.execute(
        "INSERT INTO creators (display_name, aliases, created_at) VALUES ('SomeStreamer', '[]', '')"
    )
    upper = db.conn.execute("SELECT MAX(creator_id) FROM creators").fetchone()[0]
    db.conn.execute(
        "INSERT INTO platform_accounts (creator_id, platform, platform_account_id, username, display_name)"
        " VALUES (?, 'twitch', 'SomeStreamer', 'SomeStreamer', 'SomeStreamer')",
        (upper,),
    )
    db.conn.commit()
    assert identity.resolve(db, "tw_2", "SomeStreamer", platform="twitch") == upper
    assert identity.resolve(db, "tw_3", "somestreamer", platform="twitch") == lower


def test_a_video_already_given_a_creator_keeps_it(db):
    chosen = identity.resolve(db, "", "Some Channel", platform="youtube")
    db.upsert_video("abcdefghijk", channel_name="Some Channel")
    identity.tag_video(db, "abcdefghijk", "Some Channel", platform="youtube")
    # The download names the channel differently; the video stays put.
    assert identity.tag_video(db, "abcdefghijk", "Some Channel (Official)") == chosen
