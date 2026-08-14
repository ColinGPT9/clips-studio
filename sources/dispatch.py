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
    if url.startswith("local:"):
        # Uploaded file: "local:<video_id>". The file was already placed in
        # downloads/ by the upload endpoint — there is nothing to fetch.
        return "local", url.split(":", 1)[1]
    if twitch.is_twitch_url(url):
        return "twitch", twitch.extract_vod_id(url)
    if kick.is_kick_url(url):
        return "kick", kick.extract_vod_id(url)
    return "youtube", youtube.extract_video_id(url)


def _clear_stale_partials(output_dir: Path, video_id: str | None) -> None:
    """Remove leftover .part fragments from an earlier failed download of this
    video before starting a new one.

    A download that dies partway (a 403, a dropped connection) leaves yt-dlp
    .part / .part-Frag files — the 900 MB video stream can be most of the way
    done. yt-dlp cannot resume them: the platform's fragment URLs expire in
    hours, so the next attempt restarts from zero regardless and just orphans
    the old partial, which then sits on disk until a manual cleanup. Clearing
    them here means a re-download reclaims that space instead of doubling it.
    """
    if not video_id or not output_dir.is_dir():
        return
    for f in output_dir.iterdir():
        if not f.is_file() or not f.name.startswith(video_id):
            continue
        if f.suffix in (".part", ".ytdl") or ".part-Frag" in f.name:
            try:
                f.unlink()
            except OSError:
                pass  # locked or already gone — never block the download


def metadata(url: str) -> tuple[str, str]:
    """(title, channel) for a URL, without downloading the video.

    For the case where the source file is already on disk but nothing is known
    about it — a database that was reset or moved, or a file copied into
    downloads/ by hand. Without this the title falls back to the raw video ID
    and the channel to an empty string, and an empty channel means the video is
    attached to no creator, so catchphrase learning and preference history
    never run on it.

    One metadata request rather than re-fetching several GB. Raises on failure
    rather than returning empty strings, so the caller can decide whether being
    offline is fatal — in the cached path it is not, and the ID is still a
    usable fallback.
    """
    source, _ = identify(url)
    if source == "local":
        # An imported file has no platform to ask; whatever the user typed at
        # import is all there ever was.
        raise ValueError("local uploads carry no remote metadata")

    import yt_dlp

    opts = {"quiet": True, "no_warnings": True, "skip_download": True}
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=False)

    return (
        info.get("title") or "",
        info.get("channel") or info.get("uploader") or "",
    )


def download(url: str, output_dir: Path) -> DownloadedVideo:
    source, video_id = identify(url)
    if source == "local":
        # Only reachable if the imported copy in downloads/ was deleted.
        raise ValueError(
            "The uploaded video's imported copy is gone — upload the file again."
        )
    _clear_stale_partials(output_dir, video_id)
    if source == "twitch":
        return twitch.download(url, output_dir)
    if source == "kick":
        return kick.download(url, output_dir)
    return youtube.download(url, output_dir)
