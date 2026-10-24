"""The links the desktop app will hand to the user's browser.

ui/src/main/index.ts allow-lists them (EXTERNAL_ALLOWED): anything else is
refused and logged. A campaign's Discord channel and a YouTube Short have to
open, and a Discord webhook URL never may, since it carries the webhook's
token. Read from the TypeScript source and checked with Python's re, which
reads these patterns the same way.
"""

import re
from pathlib import Path

import pytest

SOURCE = Path(__file__).resolve().parent.parent / "ui" / "src" / "main" / "index.ts"


def _allowed() -> list[re.Pattern]:
    text = SOURCE.read_text(encoding="utf-8")
    start = text.index("const EXTERNAL_ALLOWED = [")
    end = text.index("\n]", start)
    patterns = []
    for line in text[start:end].splitlines()[1:]:
        literal = re.match(r"^\s*/(.+)/([a-z]*),?\s*$", line)
        if literal:
            flags = re.IGNORECASE if "i" in literal.group(2) else 0
            patterns.append(re.compile(literal.group(1), flags))
    return patterns


ALLOWED = _allowed()


def opens(url: str) -> bool:
    return any(p.search(url) for p in ALLOWED)


def test_the_list_was_read():
    assert len(ALLOWED) > 30


@pytest.mark.parametrize("url", [
    "https://discord.com/channels/100000000000000001",
    "https://discord.com/channels/100000000000000001/100000000000000002",
    "https://discord.com/channels/100000000000000001/100000000000000002/100000000000000003",
    "https://discord.com/oauth2/authorize?client_id=100000000000000004&response_type=code",
    "https://www.youtube.com/shorts/abcdefghijk",
    "https://youtube.com/shorts/abc-defg_ij",
    "https://m.youtube.com/shorts/abcdefghijk",
    # Unchanged: links that already opened.
    "https://www.youtube.com/watch?v=abcdefghijk",
    "https://www.tiktok.com/@someone/video/7300000000000000001",
])
def test_campaign_and_post_links_open(url):
    assert opens(url)


@pytest.mark.parametrize("url", [
    # A webhook URL is a credential: the token is in the path.
    "https://discord.com/api/webhooks/100000000000000001/not-a-real-token",
    "https://discord.com/api/v10/webhooks/100000000000000001/not-a-real-token",
    "https://discord.com/api/oauth2/token",
    "https://discord.com/channels/@me",
    "https://discord.com/channels/100000000000000001/not-a-channel",
    "https://discord.com.example.net/channels/100000000000000001",
    "http://discord.com/channels/100000000000000001",
    "https://www.youtube.com/shorts/abc",
    "https://www.youtube.com/shorts/abcdefghijk/../../evil",
    "https://example.com/",
    "https://example.com/https://discord.com/channels/100000000000000001",
])
def test_anything_else_is_refused(url):
    assert not opens(url)


def test_no_pattern_lets_a_discord_api_url_through():
    for pattern in ALLOWED:
        assert not pattern.search("https://discord.com/api/webhooks/1/token"), pattern.pattern
