"""Shared yt-dlp behavior for all sources: live download progress + cancel,
parallel fragment fetching, and pointing yt-dlp at the bundled FFmpeg.

Without a progress hook the UI's bar sits still during a long VOD download
(the "stuck at 3%" feeling). This emits real percent as bytes arrive and
aborts promptly if the video is cancelled.
"""

import re
from pathlib import Path

from core import cancel, progress
from core.binaries import ffmpeg


def _ffmpeg_dir() -> str | None:
    """The folder holding ffmpeg and ffprobe, for yt-dlp's own use.

    yt-dlp does not call core.binaries — it looks for ffmpeg on PATH itself,
    and merging separate video and audio streams is the one thing it cannot do
    without one. A developer's machine has ffmpeg on PATH, so this is invisible
    there. An installed copy does not, and every download that needs merging
    dies with "ffmpeg is not installed" — which is most YouTube downloads,
    because the good video and the good audio arrive as separate streams.

    Returns None when nothing resolved, leaving yt-dlp to search PATH exactly
    as before rather than handing it a path that isn't there.
    """
    resolved = ffmpeg()
    if resolved == "ffmpeg" or not Path(resolved).exists():
        return None
    return str(Path(resolved).parent)


# What YouTube answers when it takes a connection for a bot ("Sign in to
# confirm you're not a bot"). It goes by the address the request comes from,
# and a PC on both IPv4 and IPv6 has two: refused over one, the same video
# is served over the other a second later.
_BOT_CHECK = "not a bot"
_IPV4 = "0.0.0.0"       # yt-dlp's --force-ipv4
# Set once a request only got through over IPv4. The rest of the session's
# YouTube requests start there, rather than being refused again at every step
# of a video.
_ipv4_only = False
# Only YouTube answers like this. A Twitch or Kick link is asked for the way
# it always was, whatever YouTube has been doing.
_YOUTUBE = re.compile(r"(?:^|//|\.)(?:youtube\.com|youtu\.be|youtube-nocookie\.com)(?:[/:?#]|$)", re.I)


def is_bot_check(error) -> bool:
    return _BOT_CHECK in str(error)


def extract_info(url: str, opts: dict, *, download: bool = False, process: bool = True):
    """yt-dlp's extract_info with `opts`, asked once more the other way when
    YouTube answers with its bot check: over IPv4 after an ordinary request,
    and an ordinary one after IPv4.

    A request that works is made exactly as before, once. Any other failure,
    and a second refusal, is raised as it is. Nobody signs in: this only
    changes which of the PC's own addresses the request leaves from.
    """
    global _ipv4_only
    import yt_dlp

    # Asked as each caller used to ask: `process` only where one turned it off.
    how = {"download": download} if process else {"download": download, "process": False}

    def ask(options: dict):
        with yt_dlp.YoutubeDL(options) as ydl:
            return ydl.extract_info(url, **how)

    if "source_address" in opts or not _YOUTUBE.search(url):
        return ask(opts)        # an address the caller chose, or not YouTube: as it always was
    ipv4 = {**opts, "source_address": _IPV4}
    first, other = (ipv4, opts) if _ipv4_only else (opts, ipv4)
    try:
        return ask(first)
    except yt_dlp.utils.DownloadError as e:
        # Starting on IPv4 is a habit of this session, not a need: after a
        # failure of any kind the next request starts the ordinary way.
        _ipv4_only = False
        if not is_bot_check(e):
            raise
        refused = e
    try:
        info = ask(other)
    except yt_dlp.utils.DownloadError as e:
        if is_bot_check(e):
            raise
        # The other way failed for a reason of its own (no such route, a
        # timeout). The refusal is what stands in the way, and what is said.
        raise refused from e
    # yt-dlp has already printed its refusal, cookie advice and all: say what
    # became of it, so the log does not read as a failure.
    print("      (YouTube took the request for a bot; asked again "
          f"{'over IPv4' if other is ipv4 else 'the ordinary way'} and was served)")
    _ipv4_only = other is ipv4
    return info


def progress_opts(video_id: str | None) -> dict:
    def hook(d: dict) -> None:
        if video_id and cancel.is_cancelled(video_id):
            raise cancel.CancelledError(video_id)  # aborts the yt-dlp download
        if d.get("status") == "downloading":
            total = d.get("total_bytes") or d.get("total_bytes_estimate")
            done = d.get("downloaded_bytes")
            if total and done:
                progress.emit(
                    stage="download",
                    fraction=min(1.0, done / total),
                    video_id=video_id,
                    downloaded=done,
                    total=total,
                )

    opts = {
        "progress_hooks": [hook],
        # VODs are HLS: thousands of small fragments. Fetching them one at a
        # time leaves most of the connection idle — parallel fragments cut
        # download time by 2-4x on long streams.
        "concurrent_fragment_downloads": 6,
        # Long downloads WILL hit a slow or dropped fragment. Without retries a
        # single "Read timed out" or transient 403 kills the whole job after
        # minutes of progress. Retry the fragment instead, and cap how long we
        # wait on a stalled socket so a dead connection fails fast enough to
        # retry rather than hanging.
        "retries": 10,
        "fragment_retries": 10,
        "socket_timeout": 30,
        # Pull large (non-fragmented) streams in 10 MB HTTP range chunks. A
        # 900 MB YouTube DASH stream is otherwise one long GET, and a single
        # stall late in it ("Read timed out" against googlevideo) throws away
        # the whole transfer. Chunked, a timeout loses only the current 10 MB
        # and retries just that — which is what makes the retries above
        # actually recover a big download instead of restarting it.
        "http_chunk_size": 10 * 1024 * 1024,
        # A retry immediately after a 403/timeout usually hits the same wall;
        # a short backoff lets throttling clear. Capped so we don't stall.
        "retry_sleep_functions": {
            "http": lambda n: min(5, 2 ** n),
            "fragment": lambda n: min(5, 2 ** n),
        },
    }

    # Only set when we actually have one, so a checkout with ffmpeg on PATH
    # keeps working the way it always did.
    #
    # This is what separates Twitch from YouTube on a clean install: a Twitch
    # VOD is one already-muxed HLS stream and needs no merge, while YouTube
    # serves video and audio separately and cannot be assembled without
    # ffmpeg. Miss this and half the sources look fine while the biggest one
    # fails on every single video.
    ffmpeg_dir = _ffmpeg_dir()
    if ffmpeg_dir:
        opts["ffmpeg_location"] = ffmpeg_dir

    return opts


# YouTube's broad categories, which say nothing about which game it is.
_NOT_A_GAME = {"gaming", "entertainment", "people & blogs", "comedy", "education",
               "howto & style", "music", "news & politics", "science & technology",
               "sports", "film & animation", "autos & vehicles", "travel & events",
               "pets & animals", "nonprofits & activism"}


def games_from_info(info: dict | None, platform: str) -> list[dict]:
    """The game(s) a video shows, from yt-dlp's metadata, for the gaming
    profile: [{"name", "start", "end"}], plus "hint" text when the platform
    only says "Gaming" (a YouTube video's tags often name the game).

    Twitch: its chapters are the stream's game changes, each a game's name
    over a time range (or one game for the whole VOD). Kick: the category.
    YouTube: chapters are the creator's own titles, not games, so only the
    tags go in, and only when the video is in the Gaming category."""
    if not info:
        return []
    duration = float(info.get("duration") or 0)
    games: list[dict] = []
    if platform == "twitch":
        for ch in info.get("chapters") or []:
            name = str(ch.get("title") or "").strip()
            if name:
                games.append({"name": name, "start": float(ch.get("start_time") or 0),
                              "end": float(ch.get("end_time") or duration)})
    if not games and platform in ("twitch", "kick"):
        for cat in info.get("categories") or []:
            name = str(cat or "").strip()
            if name and name.lower() not in _NOT_A_GAME:
                games.append({"name": name, "start": 0.0, "end": duration})
    if not games and platform == "youtube":
        cats = [str(x).lower() for x in info.get("categories") or []]
        if "gaming" in cats:
            tags = " ".join(str(t) for t in (info.get("tags") or [])[:25])
            games.append({"name": "", "start": 0.0, "end": duration, "hint": tags})
    return games
