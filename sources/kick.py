"""Kick VOD source — VODs ONLY, by design (same policy as Twitch).

Live channel pages are rejected before any network call: processing a live
stream means an open-ended real-time capture, which the pipeline deliberately
does not do. Wait for the VOD on the channel's Videos tab and paste that link.

Kick VOD links look like  kick.com/video/<uuid>  (also accepted:
kick.com/<channel>/videos/<uuid>). Ids are stored prefixed as `kick_<uuid>`
so they can never collide with YouTube or Twitch ids anywhere in the app.
"""

import json
import re
from contextlib import contextmanager
from pathlib import Path

import yt_dlp

from core.models import DownloadedVideo
from sources.urlmatch import host_matches
from sources.ytdlp_common import games_from_info, progress_opts

_UUID = r"[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}"
_VOD_RE = re.compile(rf"kick\.com/(?:video/|[\w.-]+/videos/)({_UUID})", re.IGNORECASE)
# A link that names its channel: the only kind Kick's newer video API can
# answer for (_from_web_api).
_CHANNEL_VOD_RE = re.compile(rf"kick\.com/([\w-]+)/videos/({_UUID})", re.IGNORECASE)

_GONE = (
    "That Kick VOD no longer exists. Kick deletes VODs after a while, "
    "so a link that worked before can stop working without notice. "
    "Open the channel's Videos tab and pick one that is still listed "
    "— Kick itself is working."
)


def is_kick_url(url: str) -> bool:
    return host_matches(url, "kick.com")


def extract_vod_id(url: str) -> str | None:
    """`kick_<uuid>` for VOD links; None for anything else on kick.com
    (live channel pages, clips, etc.)."""
    m = _VOD_RE.search(url)
    return f"kick_{m.group(1).lower()}" if m else None


def _impersonation() -> dict:
    """Kick sits behind Cloudflare, which 403s non-browser clients. With
    curl_cffi installed, yt-dlp can impersonate Chrome's TLS fingerprint —
    verified to fix the 403 on real Kick VODs."""
    try:
        from yt_dlp.networking.impersonate import ImpersonateTarget

        return {"impersonate": ImpersonateTarget.from_str("chrome")}
    except Exception:
        return {}  # curl_cffi missing: try without (may 403; error will say so)


@contextmanager
def _gone_or_raise():
    """Turn Kick's 404 into "this VOD was deleted", and leave everything else.

    Kick removes VODs after a retention window, so a link that worked last
    month stops working with no warning. The raw error for that is "HTTP Error
    404: Unable to download JSON metadata", which reads like the app broke.

    Issue #87 was reported as Kick support being broken, and it was not:
    checked at the time, the channel's API answered 200 and listed five current
    VODs, the reported id was not among them, and another VOD from that same
    channel downloaded at 1080p60 through the same yt-dlp and extractor. The
    tooling was fine; the message was the bug.

    Only the 404 is translated. A real outage has to keep its own error, or the
    next genuine breakage gets misread as a deleted video.
    """
    try:
        yield
    except yt_dlp.utils.DownloadError as e:
        if "404" not in str(e):
            raise
        raise ValueError(_GONE) from e


def _get_json(ydl, url: str):
    """Kick's answer to `url` as JSON, or None when it says there is no such
    thing (400 or 404). Through yt-dlp's own requests, as Chrome, for the
    same Cloudflare reason as _impersonation()."""
    from yt_dlp.networking import Request
    from yt_dlp.networking.exceptions import HTTPError

    try:
        with ydl.urlopen(Request(url, headers={"Accept": "application/json"},
                                 extensions=_impersonation())) as response:
            return json.loads(response.read().decode("utf-8"))
    except HTTPError as e:
        if getattr(e, "status", None) in (400, 404):
            return None
        raise


def _from_web_api(ydl, url: str) -> dict | None:
    """A VOD that yt-dlp's Kick extractor can't find, from the API Kick's own
    site reads, as the fields download() uses; None if that has no such VOD
    either.

    Kick gives new VODs ids in a newer form (version 7 UUIDs), and the API
    yt-dlp asks, kick.com/api/v1/video/<id>, answers 404 for every one of
    them: a public VOD that played on Kick was reported as deleted (#161).
    The site reads web.kick.com/api/v1/channels/<channel id>/videos/<id>,
    and that needs the channel's number, so only a link that names its
    channel can be looked up this way."""
    m = _CHANNEL_VOD_RE.search(url)
    if not m:
        return None
    slug, uuid = m.group(1).lower(), m.group(2).lower()
    channel = _get_json(ydl, f"https://kick.com/api/v2/channels/{slug}")
    channel_id = channel.get("id") if isinstance(channel, dict) else None
    if not isinstance(channel_id, int):
        return None
    got = _get_json(ydl, f"https://web.kick.com/api/v1/channels/{channel_id}/videos/{uuid}")
    data = got.get("data") if isinstance(got, dict) else None
    if not isinstance(data, dict) or not data.get("recording_url"):
        return None
    category = (data.get("category") or {}).get("name")
    return {
        "url": str(data["recording_url"]),
        "title": data.get("title") or "",
        "duration": float(data.get("duration") or 0),
        "uploader": (data.get("channel") or {}).get("username") or slug,
        "categories": [category] if category else [],
        "is_live": bool(data.get("is_live")),
    }


def download(url: str, output_dir: Path, vertical: bool = False) -> DownloadedVideo:
    """`vertical`: the vertical version only, for Vertical Live (sources/vertical.py)."""
    video_id = extract_vod_id(url)
    if video_id is None:
        raise ValueError(
            "Only Kick VODs are supported — paste a link like "
            "kick.com/video/xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx (from the "
            "channel's Videos tab). Live channels can't be processed; wait "
            "for the VOD."
        )
    output_dir.mkdir(parents=True, exist_ok=True)

    # Both calls below turn a 404 into "deleted": a deleted VOD fails at
    # whichever one reaches Kick's API first, which is the probe, not the
    # download. Handling only the download would have left the real failure
    # path untouched. The probe's 404 is only "deleted" once Kick's newer API
    # hasn't got the VOD either (_from_web_api); when it has, its recording
    # is what is downloaded, and its title and length are the video's.
    web = None
    with yt_dlp.YoutubeDL({"quiet": True, "no_warnings": True, **_impersonation()}) as probe:
        try:
            info = probe.extract_info(url, download=False)
        except yt_dlp.utils.DownloadError as e:
            if "404" not in str(e):
                raise
            web = _from_web_api(probe, url)
            if web is None:
                raise ValueError(_GONE) from e
            info = web
    if info.get("is_live"):
        raise ValueError("This VOD is still being streamed — wait until the broadcast ends.")

    from sources import vertical as vertical_src

    name = f"{video_id}{vertical_src.SUFFIX}" if vertical else video_id
    opts = {
        "format": "best[ext=mp4]/best",
        "outtmpl": str(output_dir / f"{name}.%(ext)s"),
        "merge_output_format": "mp4",
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "progress": True,
        **_impersonation(),
        **progress_opts(video_id),
    }
    if vertical:
        opts["format"] = vertical_src.HLS_FORMAT
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            with _gone_or_raise():
                got = ydl.extract_info(web["url"] if web else url, download=True)
        # A recording's own metadata is a playlist's ("master", no game).
        info = web or got
    except yt_dlp.utils.DownloadError as e:
        if vertical and vertical_src.is_missing_format(str(e)):
            raise ValueError(vertical_src.no_vertical_version("kick")) from e
        raise

    path = output_dir / f"{name}.mp4"
    if not path.exists():
        matches = list(output_dir.glob(f"{name}.*"))
        if not matches:
            raise FileNotFoundError(f"yt-dlp finished but no file found for {video_id}")
        path = matches[0]

    return DownloadedVideo(
        video_id=video_id,
        title=info.get("title", video_id),
        path=path,
        duration=float(info.get("duration") or 0),
        channel=info.get("uploader") or info.get("channel") or "",
        games=games_from_info(info, "kick"),
    )
