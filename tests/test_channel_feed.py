"""Finding what a watched channel posted, without touching the network.

What matters: every platform comes back as the same NewSourceVideo with the
same video id a pasted link would get (so a watched video and a pasted one can
never be processed twice), a flaky YouTube feed does not look like a channel
that stopped posting, and nothing still live is handed over to be downloaded.
"""

import pytest

pytest.importorskip("yt_dlp")  # sources.dispatch imports the platform modules

from sources import channel_feed as cf
from sources.dispatch import identify

UC = "UCXuqSBlHAE6Xw-yeJA0Tunw"


def rss_entries(*ids):
    return [
        {"video_id": v, "title": f"Video {v}", "url": f"https://www.youtube.com/watch?v={v}",
         "published": "2026-09-23T17:30:27+00:00"}
        for v in ids
    ]


def test_youtube_feed_entries_become_source_videos():
    videos = cf.latest("youtube", UC, rss=lambda cid: rss_entries("XM04mbymDsE", "GaX0frxNwfQ"))
    assert [v.video_id for v in videos] == ["XM04mbymDsE", "GaX0frxNwfQ"]
    first = videos[0]
    assert first.platform == "youtube" and first.channel_key == UC
    assert first.url == "https://www.youtube.com/watch?v=XM04mbymDsE"
    assert first.title == "Video XM04mbymDsE"
    assert first.published_at == pytest.approx(1790184627.0)


@pytest.mark.parametrize("feed", ["error", "empty"])
def test_a_broken_youtube_feed_falls_back_to_the_uploads_playlist(feed):
    def rss(cid):
        if feed == "error":
            raise OSError("500 Server Error")
        return []

    asked = []

    def extract(url, *, flat, size=cf.LISTING_SIZE):
        asked.append(url)
        return {"entries": [{"url": "https://www.youtube.com/watch?v=XM04mbymDsE", "title": "A"}]}

    videos = cf.latest("youtube", UC, rss=rss, extract=extract)
    assert asked == ["https://www.youtube.com/playlist?list=UUXuqSBlHAE6Xw-yeJA0Tunw"]
    assert [v.video_id for v in videos] == ["XM04mbymDsE"]


def test_youtube_refuses_anything_but_a_channel_id():
    with pytest.raises(ValueError):
        cf.latest("youtube", "not-a-channel", rss=lambda cid: [])


def test_twitch_listing_gives_the_same_id_as_a_pasted_link():
    listing = {"entries": [
        {"id": "v2882100152", "url": "https://www.twitch.tv/videos/2882100152", "title": "Live",
         "duration": 4193.0},
        {"id": "v2881171681", "url": "https://www.twitch.tv/videos/2881171681", "title": "Old"},
    ]}
    asked = []

    def extract(url, *, flat, size=cf.LISTING_SIZE):
        asked.append((url, flat))
        return listing

    videos = cf.latest("twitch", "xqc", extract=extract)
    assert asked == [("https://www.twitch.tv/xqc/videos?filter=archives&sort=time", True)]
    assert videos[0].video_id == identify("https://www.twitch.tv/videos/2882100152")[1]
    assert videos[0].video_id == "tw_2882100152"


def test_kick_videos_come_back_newest_first_with_their_vod_links():
    data = [
        {"start_time": "2026-09-21 19:06:28", "session_title": "Older",
         "video": {"uuid": "3b01f5ab-5793-4017-a96f-70eb6da9d80e"}},
        {"start_time": "2026-09-23 18:40:48", "session_title": "Newest",
         "video": {"uuid": "F12121A7-72E4-4903-A4FB-C58CDEF7766E"}},
        {"start_time": "2026-09-22 17:22:11", "session_title": "No video yet", "video": None},
    ]
    videos = cf.latest("kick", "xqc", get_json=lambda url: data)
    assert [v.title for v in videos] == ["Newest", "Older"]
    assert videos[0].url == "https://kick.com/xqc/videos/F12121A7-72E4-4903-A4FB-C58CDEF7766E"
    assert videos[0].video_id == "kick_f12121a7-72e4-4903-a4fb-c58cdef7766e"
    assert videos[0].published_at == pytest.approx(1790188848.0)


def test_kick_saying_something_unexpected_is_an_error_not_an_empty_channel():
    with pytest.raises(ValueError):
        cf.latest("kick", "xqc", get_json=lambda url: {"message": "Blocked"})


@pytest.mark.parametrize(
    "info, state",
    [
        ({"is_live": True, "duration": 4193}, "not_yet"),      # Twitch VOD still recording
        ({"is_live": True, "duration": 0.0}, "not_yet"),       # Kick stream still going
        ({"live_status": "is_upcoming"}, "not_yet"),           # scheduled premiere
        ({"live_status": "post_live", "duration": 7200}, "not_yet"),
        ({"duration": 41}, "skip"),                            # a Short
        ({"duration": 1759, "timestamp": 1790184627}, "ready"),
    ],
)
def test_readiness(info, state):
    result = cf.readiness("u", 180, extract=lambda url, *, flat: info)
    assert result.state == state


def test_readiness_keeps_the_facts_it_read():
    result = cf.readiness(
        "u", 180,
        extract=lambda url, *, flat: {"title": "T", "duration": 1759, "release_timestamp": 5},
    )
    assert (result.title, result.duration, result.published_at) == ("T", 1759, 5)


def test_a_members_only_video_is_set_aside_not_retried_forever():
    def extract(url, *, flat):
        raise RuntimeError("ERROR: [youtube] abc: Join this channel to get access to members-only content")

    result = cf.readiness("u", 180, extract=extract)
    assert result.state == "skip"
    assert "ERROR:" not in result.reason


def test_a_network_blip_is_tried_again():
    def extract(url, *, flat):
        raise OSError("timed out")

    assert cf.readiness("u", 180, extract=extract).state == "not_yet"


@pytest.mark.parametrize(
    "platform, text, key",
    [
        ("twitch", "https://www.twitch.tv/EmjayPlayss/videos", "emjayplayss"),
        ("twitch", "@xqc", "xqc"),
        ("kick", "https://kick.com/xqc", "xqc"),
    ],
)
def test_resolve_takes_links_or_names(platform, text, key):
    channel = cf.resolve(
        platform, text,
        extract=lambda url, *, flat, size=1: {"entries": []},
        get_json=lambda url: {"slug": "xqc", "user": {"username": "xQc"}},
    )
    assert channel.channel_key == key


@pytest.mark.parametrize("platform, text", [("twitch", "bad name"), ("kick", "a/b?c"), ("vimeo", "x")])
def test_resolve_refuses_what_cannot_be_a_channel(platform, text):
    with pytest.raises(ValueError):
        cf.resolve(platform, text, extract=lambda *a, **k: {}, get_json=lambda url: {})


def test_youtube_shorts_are_marked_from_either_source():
    entries = rss_entries("XM04mbymDsE", "SuIhqDP2VHw")
    entries[1]["short"] = True
    videos = cf.latest("youtube", UC, rss=lambda cid: entries)
    assert [v.short for v in videos] == [False, True]

    def extract(url, *, flat, size=cf.LISTING_SIZE):
        return {"entries": [{"url": "https://www.youtube.com/shorts/SuIhqDP2VHw"},
                            {"url": "https://www.youtube.com/watch?v=XM04mbymDsE"}]}

    fallback = cf.latest("youtube", UC, rss=lambda cid: [], extract=extract)
    assert [v.short for v in fallback] == [True, False]


def test_a_short_in_the_uploads_playlist_is_known_by_its_length():
    """The fallback gives Shorts /watch?v= links, so only the length tells."""
    def extract(url, *, flat, size=cf.LISTING_SIZE):
        return {"entries": [
            {"url": "https://www.youtube.com/watch?v=eWunrMg5A70", "duration": 1689},
            {"url": "https://www.youtube.com/watch?v=raNE4g5sYkA", "duration": 17},
            {"url": "https://www.youtube.com/watch?v=SJ9M_yflbt4", "duration": 180},
            {"url": "https://www.youtube.com/watch?v=xIJarngHZVE"},  # length unknown
        ]}

    videos = cf.latest("youtube", UC, rss=lambda cid: [], extract=extract)
    assert [v.short for v in videos] == [False, True, True, False]


def test_twitch_vods_are_never_taken_for_shorts():
    listing = {"entries": [{"url": "https://www.twitch.tv/videos/1", "duration": 60}]}
    videos = cf.latest("twitch", "xqc", extract=lambda url, *, flat, size=15: listing)
    assert videos[0].short is False

