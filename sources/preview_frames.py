"""Frames of a video BEFORE it is processed, for setting up a Gaming / Reaction
split on the real picture (the Generate bar's split setup, like the layout
step StreamLadder shows before processing).

Nothing is downloaded. A link is asked once for its media URL (yt-dlp, no
download, a 720p-or-smaller video stream) and FFmpeg seeks straight to the
moment wanted, reading only what that frame needs; a file on this computer is
read in place. Only links to the platforms Clips Kitty clips from are
accepted, so this can't be pointed at anything else on the network.
"""

import hashlib
import subprocess
import threading
import time
from pathlib import Path

from core.binaries import ffmpeg, ffprobe

FRAME_WIDTH = 960
_TTL = 1800.0                  # media URLs from YouTube expire in hours; keep ours short
_probed: dict[str, tuple[float, str, float, dict]] = {}
_lock = threading.Lock()


class NotFrameable(ValueError):
    """A link this can't show frames of (not a video on a supported platform,
    a live stream, gone), said in words the Generate bar can show."""


def _probe_link(url: str) -> tuple[str, float, dict]:
    """(media URL, duration seconds, HTTP headers) for a video link."""
    from sources.dispatch import identify, platform_of_link

    if platform_of_link(url) not in ("youtube", "twitch", "kick"):
        raise NotFrameable("Paste a YouTube, Twitch or Kick video link to see its frames.")
    source, video_id = identify(url)
    if not video_id:
        raise NotFrameable("That link isn't a single video, so there are no frames to show.")
    with _lock:
        hit = _probed.get(url)
        if hit and hit[0] > time.monotonic():
            return hit[1], hit[2], hit[3]

    import yt_dlp

    opts = {
        "quiet": True, "no_warnings": True, "skip_download": True, "noplaylist": True,
        # One video-only (or muxed) stream, small enough to seek in quickly, and
        # not AV1, which some FFmpeg builds decode slowly.
        "format": "bv*[height<=720][vcodec!^=av01]/b[height<=720]/bv*[height<=1080]/b",
    }
    if source == "kick":
        from sources.kick import _impersonation

        opts.update(_impersonation())
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=False)
    except yt_dlp.utils.DownloadError as e:
        raise NotFrameable(f"Couldn't open that video: {str(e).splitlines()[0][:200]}") from e
    if info.get("is_live") and source != "twitch":
        # A Twitch VOD of a stream still going is a recording that can be
        # clipped and seeked; a YouTube live is a real-time feed.
        raise NotFrameable("That's a live stream. Set the split up once the stream has ended.")
    chosen = info.get("requested_formats") or [info]
    media = chosen[0].get("url") or info.get("url")
    if not media:
        raise NotFrameable("Couldn't find a video stream for that link.")
    headers = chosen[0].get("http_headers") or info.get("http_headers") or {}
    duration = float(info.get("duration") or 0.0)
    with _lock:
        _probed[url] = (time.monotonic() + _TTL, media, duration, headers)
    return media, duration, headers


def _file_duration(path: Path) -> float:
    out = subprocess.run(
        [ffprobe(), "-v", "error", "-show_entries", "format=duration", "-of", "default=nw=1:nk=1", str(path)],
        capture_output=True, text=True, timeout=30,
    ).stdout.strip()
    try:
        return float(out)
    except ValueError:
        return 0.0


def frame(cache_dir: Path, at: float, *, url: str | None = None, path: Path | None = None) -> Path:
    """A JPEG of the video `at` (0-1) of the way through: from a link, or from
    a file already checked by core.paths.picked_file. Cached, so moving
    between the frames again is instant."""
    at = min(max(at, 0.0), 1.0)
    key = hashlib.sha1(f"{url or path}|{at:.3f}".encode()).hexdigest()[:20]
    out = cache_dir / f"src_{key}.jpg"
    if out.exists() and out.stat().st_size > 0:
        return out
    cache_dir.mkdir(parents=True, exist_ok=True)
    headers: dict = {}
    if url is not None:
        media, duration, headers = _probe_link(url)
    else:
        media, duration = str(path), _file_duration(path)
    when = at * duration if duration > 0 else 0.0
    cmd = [ffmpeg(), "-y", "-v", "error"]
    if headers:
        cmd += ["-headers", "".join(f"{k}: {v}\r\n" for k, v in headers.items())]
    # Written aside and swapped in, so two requests for the same frame never
    # serve a half-written file.
    part = out.with_name(f"{out.stem}.{threading.get_ident()}.part.jpg")
    cmd += ["-ss", f"{when:.2f}", "-i", media, "-frames:v", "1",
            "-vf", f"scale={FRAME_WIDTH}:-2", "-q:v", "3", str(part)]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=90)
    except subprocess.TimeoutExpired as e:
        part.unlink(missing_ok=True)
        raise NotFrameable("The video took too long to answer. Try another frame.") from e
    if r.returncode != 0 or not part.exists() or part.stat().st_size == 0:
        part.unlink(missing_ok=True)
        raise NotFrameable("Couldn't read a frame at that point of the video. Try another one.")
    part.replace(out)
    return out
