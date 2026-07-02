"""Source dispatcher: route any pasted URL to the right platform module.

Adding a platform (Kick is next on the roadmap) means one new source module
and one branch here — nothing downstream changes, because every source
returns the same DownloadedVideo and uses ids that can't collide.
"""

from pathlib import Path

from core.models import DownloadedVideo
from sources import twitch, youtube


def identify(url: str) -> tuple[str, str | None]:
    """(source_name, video_id) — video_id is None when the URL doesn't
    contain a recognizable video (e.g. a Twitch live-channel page)."""
    if twitch.is_twitch_url(url):
        return "twitch", twitch.extract_vod_id(url)
    return "youtube", youtube.extract_video_id(url)


def download(url: str, output_dir: Path) -> DownloadedVideo:
    source, _ = identify(url)
    if source == "twitch":
        return twitch.download(url, output_dir)
    return youtube.download(url, output_dir)
