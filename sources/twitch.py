"""Twitch source for finished VODs and viewer clips.

Live streams are deliberately rejected: processing a live channel means
capturing it in real time for however long the stream runs — open-ended,
heavy compute with no known duration, and pulling a stream while it airs
can even degrade the broadcast. A VOD is a finished file with a known
length, which is what the pipeline is built around. Wait for the VOD.

Twitch video ids are numeric; they're stored prefixed as `tw_<id>` so they
can never collide with YouTube's 11-character ids anywhere in the app
(state DB, downloads folder, clips folders).
"""

import re
from pathlib import Path
from urllib.parse import urlsplit

import yt_dlp

from core.models import DownloadedVideo
from sources.urlmatch import host_matches
from sources.ytdlp_common import games_from_info, progress_opts

_VOD_RE = re.compile(r"twitch\.tv/videos?/(\d+)", re.IGNORECASE)


def is_twitch_url(url: str) -> bool:
    return host_matches(url, "twitch.tv")


def extract_vod_id(url: str) -> str | None:
    """`tw_<numeric id>` for VOD links; None for anything else on twitch.tv
    (live channel pages, clips, etc.)."""
    m = _VOD_RE.search(url)
    return f"tw_{m.group(1)}" if m else None


def extract_clip_id(url: str) -> str | None:
    try:
        parsed = urlsplit(url)
        if (parsed.scheme != "https" or parsed.username or parsed.password
                or parsed.port not in (None, 443)):
            return None
        if parsed.hostname == "clips.twitch.tv":
            match = re.fullmatch(r"/([A-Za-z0-9_-]{1,200})/?", parsed.path)
        elif parsed.hostname in {"twitch.tv", "www.twitch.tv"}:
            match = re.fullmatch(r"/[A-Za-z0-9_]{1,25}/clip/([A-Za-z0-9_-]{1,200})/?", parsed.path)
        else:
            return None
        return f"tw_clip_{match.group(1)}" if match else None
    except ValueError:
        return None


def download(url: str, output_dir: Path, vertical: bool = False) -> DownloadedVideo:
    """`vertical`: the vertical version only, for Vertical Live (sources/vertical.py)."""
    video_id = extract_clip_id(url) or extract_vod_id(url)
    if video_id is None:
        raise ValueError(
            "Paste a finished Twitch clip or VOD link like "
            "twitch.tv/videos/123456789. Live channels can't be processed "
            "(that would mean capturing the stream in real time); wait for "
            "the VOD to appear on the channel's Videos page."
        )
    output_dir.mkdir(parents=True, exist_ok=True)

    probe_opts = {"quiet": True, "no_warnings": True}
    with yt_dlp.YoutubeDL(probe_opts) as ydl:
        info = ydl.extract_info(url, download=False)
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
        **progress_opts(video_id),
    }
    if vertical:
        opts["format"] = vertical_src.HLS_FORMAT
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=True)
    except yt_dlp.utils.DownloadError as e:
        if vertical and vertical_src.is_missing_format(str(e)):
            raise ValueError(vertical_src.no_vertical_version("twitch")) from e
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
        games=games_from_info(info, "twitch"),
    )
