"""The YouTube RSS feed, read safely.

The feed comes off the network. Parsed with entity declarations allowed, a
crafted DOCTYPE ("billion laughs") expands a few hundred bytes into gigabytes
of memory, so it is refused before anything is expanded.
"""

import pytest

pytest.importorskip("yt_dlp")
defusedxml = pytest.importorskip("defusedxml")

from sources import youtube  # noqa: E402

UC = "UC0123456789abcdefABCDEF"

FEED = b"""<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns:yt="http://www.youtube.com/xml/schemas/2015"
      xmlns="http://www.w3.org/2005/Atom">
  <title>Some Channel</title>
  <entry>
    <yt:videoId>aaaaaaaaaaa</yt:videoId>
    <title>A full video</title>
    <link rel="alternate" href="https://www.youtube.com/watch?v=aaaaaaaaaaa"/>
    <published>2026-09-20T12:00:00+00:00</published>
  </entry>
  <entry>
    <yt:videoId>bbbbbbbbbbb</yt:videoId>
    <title>A Short</title>
    <link rel="alternate" href="https://www.youtube.com/shorts/bbbbbbbbbbb"/>
    <published>2026-09-19T12:00:00+00:00</published>
  </entry>
</feed>
"""

LAUGHS = b"""<?xml version="1.0"?>
<!DOCTYPE feed [
  <!ENTITY a "lol">
  <!ENTITY b "&a;&a;&a;&a;&a;&a;&a;&a;&a;&a;">
  <!ENTITY c "&b;&b;&b;&b;&b;&b;&b;&b;&b;&b;">
  <!ENTITY d "&c;&c;&c;&c;&c;&c;&c;&c;&c;&c;">
]>
<feed xmlns="http://www.w3.org/2005/Atom"><title>&d;</title></feed>
"""


class Response:
    def __init__(self, content: bytes):
        self.content = content

    def raise_for_status(self):
        pass


@pytest.fixture
def feed(monkeypatch):
    """Serve `feed.body` as the RSS response."""
    class Serve:
        body = FEED

    monkeypatch.setattr(youtube.requests, "get", lambda url, timeout: Response(Serve.body))
    return Serve


def test_a_feed_reads_as_entries_with_shorts_marked(feed):
    entries = youtube.poll_channel(UC)
    assert [(e["video_id"], e["title"], e["short"]) for e in entries] == [
        ("aaaaaaaaaaa", "A full video", False),
        ("bbbbbbbbbbb", "A Short", True),
    ]
    assert entries[0]["url"] == "https://www.youtube.com/watch?v=aaaaaaaaaaa"
    assert entries[0]["published"] == "2026-09-20T12:00:00+00:00"


def test_a_feed_that_declares_entities_is_refused(feed):
    feed.body = LAUGHS
    with pytest.raises(defusedxml.EntitiesForbidden):
        youtube.poll_channel(UC)


def test_the_channel_name_comes_from_the_feed(feed):
    assert youtube._channel_name_from_rss(UC) == "Some Channel"


def test_a_hostile_feed_leaves_the_channel_known_by_its_id(feed):
    feed.body = LAUGHS
    assert youtube._channel_name_from_rss(UC) == UC
