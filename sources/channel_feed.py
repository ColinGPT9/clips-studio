"""What a watched channel has posted, on each platform that can say.

Detection only. This answers "a new video appeared, and here is its link"; the
link then goes through the same queue a pasted one does, so from there on a
watched video and a pasted one are the same thing. Nothing is downloaded here.

Per platform:
  YouTube  the channel's RSS feed (sources/youtube.py): free, unauthenticated,
           no quota. It has been returning errors and empty bodies on and off
           in 2026, so an empty or failed feed falls back to the channel's
           uploads playlist through yt-dlp, which lists the same videos.
  Twitch   the channel's past broadcasts, newest first, through yt-dlp. First
           page only: paging with cursors trips Twitch's integrity check.
  Kick     Kick's own channel videos endpoint. yt-dlp has no extractor that
           lists a Kick channel. The endpoint is unofficial, but it is the same
           API, behind the same Cloudflare impersonation, that downloading a
           Kick VOD already depends on; if it stops answering, Kick downloads
           have most likely stopped too, and the watch says so.

Every function that touches the network takes it as an argument, so tests
never do.
"""

import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone

PLATFORMS = ("youtube", "twitch", "kick")

# How many of the newest videos one look reads. The YouTube feed holds 15, so
# more would not be comparable across platforms.
LISTING_SIZE = 15

_YOUTUBE_ID = re.compile(r"^UC[0-9A-Za-z_-]{22}$")
# Kick slugs are letters, digits, underscore and hyphen. Refused before they
# are placed into a URL.
_KICK_SLUG = re.compile(r"^[A-Za-z0-9_-]{1,64}$")

# The longest a YouTube Short can be. The uploads playlist links a Short as
# /watch?v= like any other video, so its length is how it is recognised there.
SHORT_MAX_SECONDS = 180

# A broadcast that is live, about to be, or just ended and still processing
# gives an incomplete file if it is downloaded now. Wait and look again.
_NOT_READY = ("is_live", "is_upcoming", "post_live")

# Errors that no amount of waiting fixes, so the video is set aside with the
# reason rather than looked at every few minutes for a week.
_PERMANENT = (
    "members", "join this channel", "private video", "video is private",
    "subscriber", "has been removed", "video unavailable", "deleted", "404",
)


@dataclass
class Channel:
    platform: str
    channel_key: str  # UC id, Twitch login or Kick slug
    name: str


@dataclass
class NewSourceVideo:
    """One video a channel posted, the same shape on every platform."""

    platform: str
    channel_key: str
    video_id: str  # canonical, from sources.dispatch.identify: same as a pasted link
    url: str
    title: str = ""
    published_at: float = 0.0  # unix seconds; 0 when the listing does not say
    # A YouTube Short. There is nothing to clip from one, so watching skips
    # them outright: only full videos and finished streams are for clipping.
    short: bool = False


@dataclass
class Readiness:
    state: str  # ready | not_yet | skip
    reason: str = ""
    title: str = ""
    published_at: float = 0.0
    duration: float = 0.0
    # "vertical" when the video has a portrait version to download (a
    # vertical live, or a dual-format one), "horizontal" when it only comes
    # landscape, "" when the listing doesn't say. Vertical Live watches skip
    # "horizontal" videos rather than clip the wrong version.
    orientation: str = ""


# ---- adding a channel -------------------------------------------------------


def resolve(
    platform: str,
    text: str,
    *,
    extract: Callable[..., dict | None] | None = None,
    get_json: Callable[[str], object] | None = None,
    resolve_youtube: Callable[[str], dict] | None = None,
) -> Channel:
    """Turn what a person pastes (a channel link, an @handle, a bare name) into
    the channel to watch. Raises ValueError with a message worth showing."""
    raw = (text or "").strip()
    if not raw:
        raise ValueError("Paste a channel link or name.")
    if platform == "youtube":
        if resolve_youtube is None:
            from sources.youtube import resolve_channel as resolve_youtube
        try:
            info = resolve_youtube(raw)
        except ValueError:
            raise
        except Exception as e:
            raise ValueError(f"Couldn't find that YouTube channel: {_short(e)}") from e
        return Channel("youtube", info["channel_id"], info.get("name") or info["channel_id"])
    if platform == "twitch":
        handle = _handle_from(raw, "twitch.tv")
        from sources.vod_finder import clean_handle

        if handle is None or clean_handle(handle) != handle:
            raise ValueError("That doesn't look like a Twitch channel name.")
        # One look proves the channel exists and that its videos can be read.
        try:
            (extract or _extract)(_twitch_listing(handle), flat=True, size=1)
        except Exception as e:
            raise ValueError(f"Couldn't read that Twitch channel: {_short(e)}") from e
        return Channel("twitch", handle.lower(), handle)
    if platform == "kick":
        slug = _handle_from(raw, "kick.com")
        if slug is None or not _KICK_SLUG.match(slug):
            raise ValueError("That doesn't look like a Kick channel name.")
        try:
            data = (get_json or _get_json)(f"https://kick.com/api/v2/channels/{slug}")
        except Exception as e:
            raise ValueError(f"Couldn't find that Kick channel: {_short(e)}") from e
        if not isinstance(data, dict) or not data.get("slug"):
            raise ValueError("Couldn't find that Kick channel.")
        name = ((data.get("user") or {}).get("username")) or data["slug"]
        return Channel("kick", str(data["slug"]).lower(), name)
    raise ValueError(f"Clips Kitty can't watch {platform!r} channels.")


def _handle_from(raw: str, host: str) -> str | None:
    """The channel name from a pasted link on `host`, or the text itself."""
    text = raw.strip().lstrip("@")
    if host in text:
        path = text.split(host, 1)[1].lstrip("/")
        text = path.split("/", 1)[0].split("?", 1)[0].split("#", 1)[0]
    return text.strip().lstrip("@") or None


# ---- looking at a channel ---------------------------------------------------


def latest(
    platform: str,
    channel_key: str,
    *,
    extract: Callable[..., dict | None] | None = None,
    get_json: Callable[[str], object] | None = None,
    rss: Callable[[str], list[dict]] | None = None,
) -> list[NewSourceVideo]:
    """The channel's newest videos, newest first. Raises when the platform
    could not be read at all, so a watch can say so instead of looking empty."""
    extract = extract or _extract
    if platform == "youtube":
        return _youtube_latest(channel_key, extract, rss)
    if platform == "twitch":
        listing = extract(_twitch_listing(channel_key), flat=True, size=LISTING_SIZE) or {}
        return _from_listing("twitch", channel_key, listing)
    if platform == "kick":
        return _kick_latest(channel_key, get_json or _get_json)
    raise ValueError(f"unsupported platform {platform!r}")


def _youtube_latest(channel_key, extract, rss) -> list[NewSourceVideo]:
    if not _YOUTUBE_ID.match(channel_key or ""):
        raise ValueError(f"not a YouTube channel id: {channel_key!r}")
    if rss is None:
        from sources.youtube import poll_channel as rss
    try:
        entries = rss(channel_key)
    except Exception:
        entries = []
    if entries:
        out = []
        for e in entries:
            video = _video("youtube", channel_key, e.get("url") or "", e.get("title") or "",
                           _iso_seconds(e.get("published") or ""))
            video.short = bool(e.get("short"))
            out.append(video)
        return out
    # The uploads playlist is the channel id with UU for UC: every public upload,
    # newest first, the same set the feed carries.
    uploads = f"https://www.youtube.com/playlist?list=UU{channel_key[2:]}"
    listing = extract(uploads, flat=True, size=LISTING_SIZE) or {}
    return _from_listing("youtube", channel_key, listing)


def _kick_latest(slug, get_json) -> list[NewSourceVideo]:
    if not _KICK_SLUG.match(slug or ""):
        raise ValueError(f"not a Kick channel: {slug!r}")
    data = get_json(f"https://kick.com/api/v2/channels/{slug}/videos")
    if not isinstance(data, list):
        raise ValueError("Kick returned something other than a list of videos.")
    found = []
    for entry in data:
        uuid = ((entry or {}).get("video") or {}).get("uuid")
        if not uuid:
            continue
        found.append((
            _kick_time(entry.get("start_time") or entry.get("created_at") or ""),
            _video("kick", slug, f"https://kick.com/{slug}/videos/{uuid}",
                   entry.get("session_title") or "", 0.0),
        ))
    found.sort(key=lambda pair: pair[0], reverse=True)
    out = []
    for when, video in found[:LISTING_SIZE]:
        video.published_at = when
        out.append(video)
    return out


def _from_listing(platform: str, channel_key: str, listing: dict) -> list[NewSourceVideo]:
    out = []
    for entry in [e for e in (listing.get("entries") or []) if e][:LISTING_SIZE]:
        url = entry.get("url") or entry.get("webpage_url") or ""
        if platform == "youtube" and url and not url.startswith("http"):
            url = f"https://www.youtube.com/watch?v={url}"
        video = _video(platform, channel_key, url, entry.get("title") or "",
                       float(entry.get("timestamp") or 0))
        # A Short, however it is linked. The uploads playlist, which is what
        # is read when a channel's RSS feed answers 404, gives /watch?v= links
        # for Shorts as well; a length of three minutes or less gives them away.
        duration = float(entry.get("duration") or 0)
        video.short = platform == "youtube" and (
            "/shorts/" in url or 0 < duration <= SHORT_MAX_SECONDS
        )
        if video.video_id:
            out.append(video)
    return out


def _video(platform, channel_key, url, title, published_at) -> NewSourceVideo:
    from sources.dispatch import identify

    _, vid = identify(url) if url else ("", None)
    return NewSourceVideo(platform, channel_key, vid or "", url, title, published_at)


# ---- is it ready to clip? ---------------------------------------------------


def readiness(
    url: str,
    min_seconds: float = 0,
    *,
    extract: Callable[..., dict | None] | None = None,
) -> Readiness:
    """Whether a found video can be clipped now, later, or not at all.

    One metadata request, no download. `live_status` is only filled in when
    yt-dlp processes the result, and this deliberately does not, so `is_live`
    is checked too: it is how an unprocessed Twitch VOD that is still recording,
    or a Kick VOD of a stream still going, says so."""
    try:
        info = (extract or _extract)(url, flat=False) or {}
    except Exception as e:
        message = _short(e)
        if any(marker in message.lower() for marker in _PERMANENT):
            return Readiness("skip", message)
        return Readiness("not_yet", f"Couldn't read the video yet: {message}")
    title = info.get("title") or ""
    published = float(info.get("release_timestamp") or info.get("timestamp") or 0)
    duration = float(info.get("duration") or 0)
    facts = {"title": title, "published_at": published, "duration": duration,
             "orientation": _orientation(info)}
    if info.get("is_live") or info.get("live_status") in _NOT_READY:
        return Readiness("not_yet", "Still live or about to go live. Waiting for it to finish.",
                         **facts)
    if min_seconds and duration and duration < min_seconds:
        minutes = f"{min_seconds / 60:g}"
        return Readiness("skip", f"Shorter than {minutes} minutes.", **facts)
    return Readiness("ready", "", **facts)


def _orientation(info: dict) -> str:
    """Whether a video can be had in portrait, from the formats its metadata
    lists (no download). Unprocessed metadata still carries them."""
    shapes = [(f.get("width"), f.get("height")) for f in info.get("formats") or []
              if isinstance(f, dict) and f.get("vcodec") != "none"]
    shapes = [(w, h) for w, h in shapes if w and h]
    if not shapes:
        width, height = info.get("width"), info.get("height")
        shapes = [(width, height)] if width and height else []
    if not shapes:
        return ""
    return "vertical" if any(h > w for w, h in shapes) else "horizontal"


# ---- plumbing ---------------------------------------------------------------


def _twitch_listing(handle: str) -> str:
    return f"https://www.twitch.tv/{handle}/videos?filter=archives&sort=time"


def _extract(url: str, *, flat: bool, size: int = LISTING_SIZE) -> dict | None:
    from sources.vod_finder import _extract as extract

    return extract(url, flat=flat, size=size)


def _get_json(url: str) -> object:
    """GET a Kick API URL as Chrome, which Cloudflare lets through."""
    try:
        from curl_cffi import requests as http

        response = http.get(url, impersonate="chrome", timeout=30)
    except ImportError:
        import requests as http

        response = http.get(url, timeout=30)
    if response.status_code == 404:
        raise ValueError("not found on Kick (404)")
    response.raise_for_status()
    return response.json()


def _iso_seconds(text: str) -> float:
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return 0.0


def _kick_time(text: str) -> float:
    """Kick's "2026-09-23 18:40:48", which is UTC."""
    try:
        return datetime.strptime(text[:19], "%Y-%m-%d %H:%M:%S").replace(
            tzinfo=timezone.utc
        ).timestamp()
    except ValueError:
        return 0.0


def _short(error: Exception | str) -> str:
    text = str(error).strip().splitlines()[0] if str(error).strip() else type(error).__name__
    # yt-dlp prefixes every error with this; it tells the reader nothing.
    return text.replace("ERROR: ", "")[:300]
