"""Kick VOD source — VODs ONLY, by design (same policy as Twitch).

Live channel pages are rejected before any network call: processing a live
stream means an open-ended real-time capture, which the pipeline deliberately
does not do. Wait for the VOD on the channel's Videos tab and paste that link.

Kick VOD links look like  kick.com/video/<uuid>  (also accepted:
kick.com/<channel>/videos/<uuid>). Ids are stored prefixed as `kick_<uuid>`
so they can never collide with YouTube or Twitch ids anywhere in the app.
"""

import re
from contextlib import contextmanager
from pathlib import Path

import yt_dlp

from core.models import DownloadedVideo
from sources.urlmatch import host_matches
from sources.ytdlp_common import progress_opts

_VOD_RE = re.compile(
    r"kick\.com/(?:video/|[\w.-]+/videos/)([0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12})",
    re.IGNORECASE,
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
        raise ValueError(
            "That Kick VOD no longer exists. Kick deletes VODs after a while, "
            "so a link that worked before can stop working without notice. "
            "Open the channel's Videos tab and pick one that is still listed "
            "— Kick itself is working."
        ) from e


def download(url: str, output_dir: Path) -> DownloadedVideo:
    video_id = extract_vod_id(url)
    if video_id is None:
        raise ValueError(
            "Only Kick VODs are supported — paste a link like "
            "kick.com/video/xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx (from the "
            "channel's Videos tab). Live channels can't be processed; wait "
            "for the VOD."
        )
    output_dir.mkdir(parents=True, exist_ok=True)

    # Both calls below go through _gone_or_raise: a deleted VOD fails at
    # whichever one reaches Kick's API first, which is the probe, not the
    # download. Handling only the download would have left the real failure
    # path untouched.
    with yt_dlp.YoutubeDL({"quiet": True, "no_warnings": True, **_impersonation()}) as probe:
        with _gone_or_raise():
            info = probe.extract_info(url, download=False)
    if info.get("is_live"):
        raise ValueError("This VOD is still being streamed — wait until the broadcast ends.")

    opts = {
        "format": "best[ext=mp4]/best",
        "outtmpl": str(output_dir / f"{video_id}.%(ext)s"),
        "merge_output_format": "mp4",
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "progress": True,
        **_impersonation(),
        **progress_opts(video_id),
    }
    with yt_dlp.YoutubeDL(opts) as ydl:
        with _gone_or_raise():
            info = ydl.extract_info(url, download=True)

    path = output_dir / f"{video_id}.mp4"
    if not path.exists():
        matches = list(output_dir.glob(f"{video_id}.*"))
        if not matches:
            raise FileNotFoundError(f"yt-dlp finished but no file found for {video_id}")
        path = matches[0]

    return DownloadedVideo(
        video_id=video_id,
        title=info.get("title", video_id),
        path=path,
        duration=float(info.get("duration") or 0),
        channel=info.get("uploader") or info.get("channel") or "",
    )
