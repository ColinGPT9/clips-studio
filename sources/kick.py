"""Kick VOD source — VODs ONLY, by design (same policy as Twitch).

Live channel pages are rejected before any network call: processing a live
stream means an open-ended real-time capture, which the pipeline deliberately
does not do. Wait for the VOD on the channel's Videos tab and paste that link.

Kick VOD links look like  kick.com/video/<uuid>  (also accepted:
kick.com/<channel>/videos/<uuid>). Ids are stored prefixed as `kick_<uuid>`
so they can never collide with YouTube or Twitch ids anywhere in the app.
"""

import re
from pathlib import Path

import yt_dlp

from core.models import DownloadedVideo

_VOD_RE = re.compile(
    r"kick\.com/(?:video/|[\w.-]+/videos/)([0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12})",
    re.IGNORECASE,
)


def is_kick_url(url: str) -> bool:
    return "kick.com" in url.lower()


def extract_vod_id(url: str) -> str | None:
    """`kick_<uuid>` for VOD links; None for anything else on kick.com
    (live channel pages, clips, etc.)."""
    m = _VOD_RE.search(url)
    return f"kick_{m.group(1).lower()}" if m else None


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

    with yt_dlp.YoutubeDL({"quiet": True, "no_warnings": True}) as probe:
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
    }
    with yt_dlp.YoutubeDL(opts) as ydl:
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
