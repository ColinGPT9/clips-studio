"""Source dispatcher: route any pasted URL to the right platform module.

Adding a platform means one new source module and one branch here — nothing
downstream changes, because every source returns the same DownloadedVideo
and uses ids that can't collide (YouTube: 11-char, Twitch: tw_, Kick: kick_).
"""

from pathlib import Path

from core.models import DownloadedVideo
from sources import kick, twitch, youtube


def identify(url: str) -> tuple[str, str | None]:
    """(source_name, video_id) — video_id is None when the URL doesn't
    contain a recognizable video (e.g. a live-channel page)."""
    if twitch.is_twitch_url(url):
        return "twitch", twitch.extract_vod_id(url)
    if kick.is_kick_url(url):
        return "kick", kick.extract_vod_id(url)
    return "youtube", youtube.extract_video_id(url)


def download(url: str, output_dir: Path) -> DownloadedVideo:
    source, _ = identify(url)
    if source == "twitch":
        return twitch.download(url, output_dir)
    if source == "kick":
        return kick.download(url, output_dir)
    return youtube.download(url, output_dir)
