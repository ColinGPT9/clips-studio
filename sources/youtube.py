"""YouTube source: RSS channel polling + yt-dlp downloads.

RSS is used for new-upload detection because it's free and unauthenticated —
zero YouTube API quota spent on watching. The Data API is reserved for
uploads only.
"""

import re
import xml.etree.ElementTree as ET
from pathlib import Path

import requests
import yt_dlp

from core.models import DownloadedVideo

RSS_URL = "https://www.youtube.com/feeds/videos.xml?channel_id={}"
_ATOM_NS = {
    "atom": "http://www.w3.org/2005/Atom",
    "yt": "http://www.youtube.com/xml/schemas/2015",
}


def watch_url(video_id: str) -> str:
    return f"https://www.youtube.com/watch?v={video_id}"


def extract_video_id(url: str) -> str | None:
    """Pull the video id out of any common YouTube URL shape, without
    touching the network."""
    m = re.search(r"(?:v=|youtu\.be/|/shorts/|/live/)([0-9A-Za-z_-]{11})", url)
    return m.group(1) if m else None


def resolve_channel(query: str) -> dict:
    """Turn anything a YouTuber would paste — @handle, channel URL, video URL,
    or a raw UC... id — into {"channel_id", "name"}.

    Raises ValueError if nothing resolvable is found.
    """
    q = query.strip().strip('"')

    if re.fullmatch(r"UC[0-9A-Za-z_-]{22}", q):
        return {"channel_id": q, "name": _channel_name_from_rss(q)}

    if q.startswith("@"):
        url = f"https://www.youtube.com/{q}"
    elif q.startswith(("http://", "https://")):
        url = q
    elif re.fullmatch(r"[\w.-]+", q):
        url = f"https://www.youtube.com/@{q}"  # bare handle without the @
    else:
        raise ValueError(f"Can't interpret {query!r} as a channel handle, URL, or ID")

    # yt-dlp resolves any YouTube page to its channel without the Data API.
    opts = {"quiet": True, "no_warnings": True, "extract_flat": True, "playlist_items": "1"}
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=False)

    channel_id = info.get("channel_id") or info.get("uploader_id") or ""
    if not channel_id.startswith("UC"):
        raise ValueError(f"Could not resolve a channel ID from {query!r}")
    name = info.get("channel") or info.get("uploader") or info.get("title") or channel_id
    return {"channel_id": channel_id, "name": name}


def _channel_name_from_rss(channel_id: str) -> str:
    try:
        response = requests.get(RSS_URL.format(channel_id), timeout=15)
        response.raise_for_status()
        root = ET.fromstring(response.content)
        return root.findtext("atom:title", default=channel_id, namespaces=_ATOM_NS)
    except Exception:
        return channel_id


def poll_channel(channel_id: str, timeout: int = 30) -> list[dict]:
    """Fetch a channel's RSS feed. Returns newest-first entries:
    [{"video_id", "title", "url", "published"}]. The feed carries the
    channel's ~15 most recent uploads."""
    response = requests.get(RSS_URL.format(channel_id), timeout=timeout)
    response.raise_for_status()
    root = ET.fromstring(response.content)

    entries = []
    for entry in root.findall("atom:entry", _ATOM_NS):
        video_id = entry.findtext("yt:videoId", default="", namespaces=_ATOM_NS)
        if not video_id:
            continue
        entries.append(
            {
                "video_id": video_id,
                "title": entry.findtext("atom:title", default="", namespaces=_ATOM_NS),
                "url": watch_url(video_id),
                "published": entry.findtext("atom:published", default="", namespaces=_ATOM_NS),
            }
        )
    return entries


def download(url: str, output_dir: Path) -> DownloadedVideo:
    output_dir.mkdir(parents=True, exist_ok=True)

    opts = {
        # Best mp4 video up to 1080p + m4a audio; single-file mp4 fallback.
        "format": "bv*[height<=1080][ext=mp4]+ba[ext=m4a]/b[ext=mp4]/b",
        "merge_output_format": "mp4",
        "outtmpl": str(output_dir / "%(id)s.%(ext)s"),
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "progress": True,
    }

    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=True)

    video_id = info["id"]
    path = output_dir / f"{video_id}.mp4"
    if not path.exists():
        # Fallback for formats that didn't remux to mp4
        matches = list(output_dir.glob(f"{video_id}.*"))
        if not matches:
            raise FileNotFoundError(f"yt-dlp finished but no file found for {video_id}")
        path = matches[0]

    return DownloadedVideo(
        video_id=video_id,
        title=info.get("title", video_id),
        path=path,
        duration=float(info.get("duration") or 0),
        channel=info.get("channel") or info.get("uploader") or "",
    )
