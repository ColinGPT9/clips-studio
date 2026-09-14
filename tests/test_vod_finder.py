"""Finding a finished stream's VOD, without touching the network.

The mistake that matters is picking the wrong broadcast: a streamer who went
live twice that day, or ran a short test stream first, must get the VOD of the
stream that just ended.
"""

import pytest

from sources.vod_finder import clean_handle, find_stream_vod, listing_url, pick

STARTED, ENDED = 1_700_000_000.0, 1_700_007_200.0


def entry(url, start, duration=7000, live_status="was_live", key="timestamp"):
    return {"webpage_url": url, key: start, "duration": duration, "live_status": live_status}


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("emjayplayss", "emjayplayss"),
        ("@LinusTechTips", "LinusTechTips"),
        (" name/ ", "name"),
        ("", None),
        ("bad handle", None),
        ("a/b", None),
        ("x" * 65, None),
        ("evil.com?x=1", None),
    ],
)
def test_only_real_handles_reach_a_url(raw, expected):
    assert clean_handle(raw) == expected


def test_listing_urls_per_platform():
    assert (
        listing_url("twitch", "emjayplayss")
        == "https://www.twitch.tv/emjayplayss/videos?filter=archives&sort=time"
    )
    assert listing_url("youtube", "@LinusTechTips") == "https://www.youtube.com/@LinusTechTips/streams"
    assert listing_url("kick", "adinross") is None
    assert listing_url("twitch", "bad handle") is None


def test_picks_the_broadcast_that_started_with_the_stream():
    earlier_today = entry("https://www.twitch.tv/videos/1", STARTED - 6 * 3600)
    this_one = entry("https://www.twitch.tv/videos/2", STARTED + 45)
    assert pick([earlier_today, this_one], STARTED, ENDED).url.endswith("/2")


def test_youtube_start_comes_from_release_timestamp():
    found = pick([entry("u", STARTED + 30, key="release_timestamp")], STARTED, ENDED)
    assert found is not None
    assert found.started_at == STARTED + 30


def test_a_youtube_stream_still_processing_is_not_ready():
    assert pick([entry("u", STARTED, live_status="post_live")], STARTED, ENDED) is None


def test_a_short_test_stream_just_before_is_not_mistaken_for_it():
    assert pick([entry("test", STARTED - 300, duration=120)], STARTED, ENDED) is None


def test_nothing_close_in_time_means_not_published_yet():
    assert pick([entry("old", STARTED - 3 * 3600)], STARTED, ENDED) is None


def test_only_the_newest_few_are_opened():
    opened = []

    def extract(url, *, flat):
        if flat:
            return {"entries": [{"url": f"https://www.twitch.tv/videos/{n}"} for n in range(10)]}
        opened.append(url)
        n = int(url.rsplit("/", 1)[1])
        return entry(url, STARTED if n == 1 else STARTED - 86400 * (n + 1))

    found = find_stream_vod("twitch", "emjayplayss", STARTED, ENDED, extract=extract)
    assert found is not None
    assert found.url.endswith("/1")
    assert len(opened) == 3


def test_one_unreadable_entry_does_not_hide_the_others():
    def extract(url, *, flat):
        if flat:
            return {"entries": [{"url": "broken"}, {"url": "good"}]}
        if url == "broken":
            raise RuntimeError("HTTP 500")
        return entry("good", STARTED + 10)

    assert find_stream_vod("youtube", "somebody", STARTED, ENDED, extract=extract).url == "good"


def test_kick_and_bad_handles_never_touch_the_network():
    def extract(*args, **kwargs):
        raise AssertionError("the network must not be used")

    assert find_stream_vod("kick", "adinross", STARTED, ENDED, extract=extract) is None
    assert find_stream_vod("twitch", "not a handle", STARTED, ENDED, extract=extract) is None
