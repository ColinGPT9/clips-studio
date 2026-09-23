"""Find the VOD a finished livestream left behind.

An integration such as the OBS plugin knows when a stream started and ended and
which platform it went to, but not the VOD's address: Twitch and YouTube publish
it minutes after the stream ends. This looks at the channel's newest past
broadcasts and picks the one whose start time matches the stream. It does not
just take the newest, because a streamer who went live twice that day would get
the wrong one.

Kick is not handled on purpose. yt-dlp routes a Kick channel's videos page to
its live extractor and cannot list past broadcasts, so a Kick stream asks the
streamer for the link instead.
"""

import re
from collections.abc import Callable
from dataclasses import dataclass

SUPPORTED = ("twitch", "youtube")

# How far apart the stream's start and the VOD's start may be. OBS reports when
# the encoder connected and the platform stamps when archiving began; they agree
# within seconds normally, but a slow ingest handshake or a PC clock a few
# minutes out must not lose the match.
START_TOLERANCE_SECONDS = 20 * 60

# Only the newest few broadcasts are opened for full metadata. The listing is
# one request and each full entry is another, and the one we want is almost
# always first.
CANDIDATES = 3

# Twitch logins and YouTube handles are letters, digits, underscore, dot and
# hyphen. Anything else is refused before it is placed into a URL.
_HANDLE = re.compile(r"^[A-Za-z0-9._-]{1,64}$")

# A YouTube stream that just ended is "post_live" while YouTube processes it,
# and downloading it then returns an incomplete file. Skip it and look again.
_NOT_READY = ("is_live", "is_upcoming", "post_live")


@dataclass
class FoundVod:
    url: str
    started_at: float  # unix seconds
    duration: float


def clean_handle(channel: str) -> str | None:
    """The bare channel handle, or None if it cannot be a real one."""
    handle = (channel or "").strip().lstrip("@").strip("/")
    return handle if _HANDLE.match(handle) else None


def listing_url(platform: str, channel: str) -> str | None:
    handle = clean_handle(channel)
    if handle is None:
        return None
    if platform == "twitch":
        return f"https://www.twitch.tv/{handle}/videos?filter=archives&sort=time"
    if platform == "youtube":
        return f"https://www.youtube.com/@{handle}/streams"
    return None


def pick(entries: list[dict], started_at: float, ended_at: float) -> FoundVod | None:
    """The broadcast that belongs to this stream, from full metadata entries."""
    stream_length = max(0.0, ended_at - started_at)
    best: tuple[float, FoundVod] | None = None
    for info in entries:
        if info.get("live_status") in _NOT_READY:
            continue
        start = info.get("release_timestamp") or info.get("timestamp")
        url = info.get("webpage_url") or info.get("url")
        if not start or not url:
            continue
        gap = abs(float(start) - started_at)
        if gap > START_TOLERANCE_SECONDS:
            continue
        duration = float(info.get("duration") or 0)
        # Far shorter than the stream means a different broadcast that began
        # nearby, such as a short test stream just before going live properly.
        if stream_length and duration and duration < stream_length * 0.5:
            continue
        if best is None or gap < best[0]:
            best = (gap, FoundVod(url=url, started_at=float(start), duration=duration))
    return best[1] if best else None


def find_stream_vod(
    platform: str,
    channel: str,
    started_at: float,
    ended_at: float,
    *,
    extract: Callable[..., dict | None] | None = None,
) -> FoundVod | None:
    """The VOD for this stream if the platform has published it yet, else None.

    `extract` is injectable so tests never touch the network."""
    url = listing_url(platform, channel)
    if url is None:
        return None
    extract = extract or _extract
    listing = extract(url, flat=True) or {}
    full: list[dict] = []
    for entry in [e for e in (listing.get("entries") or []) if e][:CANDIDATES]:
        target = entry.get("url") or entry.get("webpage_url")
        if not target:
            continue
        try:
            info = extract(target, flat=False)
        except Exception:
            continue  # one unreadable entry must not hide the others
        if info:
            full.append(info)
    return pick(full, started_at, ended_at)


def _extract(url: str, *, flat: bool, size: int = CANDIDATES) -> dict | None:
    import yt_dlp

    opts: dict = {"quiet": True, "no_warnings": True, "skip_download": True, "socket_timeout": 30}
    if flat:
        opts.update({"extract_flat": "in_playlist", "playlistend": size})
    with yt_dlp.YoutubeDL(opts) as ydl:
        # process=False: the start time, length and live status are all in the
        # raw metadata, so there is no reason to resolve download formats here.
        return ydl.extract_info(url, download=False, process=False)
