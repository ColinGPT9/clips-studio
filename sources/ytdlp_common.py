"""Shared yt-dlp behavior for all sources: live download progress + cancel,
parallel fragment fetching, and pointing yt-dlp at the bundled FFmpeg.

Without a progress hook the UI's bar sits still during a long VOD download
(the "stuck at 3%" feeling). This emits real percent as bytes arrive and
aborts promptly if the video is cancelled.
"""

import os
import re
import shutil
import tempfile
from contextlib import contextmanager
from pathlib import Path

from core import cancel, progress
from core.binaries import ffmpeg
from core.paths import discard


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
# "ipv4_only" is set once a request only got through over IPv4. The rest of
# the session's YouTube requests start there, rather than being refused again
# at every step of a video.
_session = {"ipv4_only": False}
# Only YouTube answers like this. A Twitch or Kick link is asked for the way
# it always was, whatever YouTube has been doing.
_YOUTUBE = re.compile(r"(?:^|//|\.)(?:youtube\.com|youtu\.be|youtube-nocookie\.com)(?:[/:?#]|$)", re.I)
# A signed-in session to fall back on, set in Settings → Advanced settings
# (#136): "" for none, "firefox" for that browser's, or "file" for the
# cookies.txt the person chose, kept in the data folder as COOKIES_FILE.
# Asked with only after YouTube has refused both ways: every other request
# stays anonymous, as it always was. Chrome and Edge keep theirs locked on
# Windows, so they aren't offered.
COOKIES = ("", "firefox", "file")
COOKIES_FILE = "youtube_cookies.txt"
COOKIES_MAX = 1024 * 1024       # a YouTube sign-in is a few KB
_cookies = ""
_cookie_file: Path | None = None


def set_cookies(value, data_dir) -> None:
    """The YouTube cookies setting (config `youtube_cookies`), as saved. Any
    other value is off: the file is always the one in `data_dir`."""
    global _cookies, _cookie_file
    value = str(value or "").strip().lower()
    _cookies = value if value in COOKIES else ""
    _cookie_file = Path(data_dir) / COOKIES_FILE if data_dir else None


def cookies() -> str:
    return _cookies


def cookies_text_ok(text: str) -> bool:
    """Whether `text` is a cookies.txt yt-dlp will load: its very first line
    is the Netscape header (yt-dlp refuses a file without it). A byte-order
    mark is let through here and left off when the file is kept."""
    if len(text.encode("utf-8")) > COOKIES_MAX:
        return False
    return re.match(r"#( Netscape)? HTTP Cookie File", text.lstrip("﻿")) is not None


@contextmanager
def _signed_in(opts: dict):
    """`opts` asking as the person signed in to YouTube: Firefox's cookies, or
    a copy of their cookies.txt (yt-dlp writes its cookie jar back to the file
    it was given, which would rewrite the person's own, or fail on a read-only
    one). Signed in, YouTube's players need their JavaScript run, so yt-dlp is
    pointed at the bundled QuickJS (core.binaries.quickjs); no other request
    runs any. A cookies file that is gone is a download error, said as such."""
    import yt_dlp

    from core.binaries import quickjs

    qjs = quickjs()
    signed = {**opts, "js_runtimes": {"quickjs": {"path": qjs} if Path(qjs).is_file() else {}}}
    if _cookies == "firefox":
        yield {**signed, "cookiesfrombrowser": ("firefox",)}
        return
    if _cookie_file is None or not _cookie_file.is_file():
        raise yt_dlp.utils.DownloadError(
            "The YouTube cookies file set in Settings is not there any more: choose it again "
            "in Settings → Advanced settings")
    handle, copy = tempfile.mkstemp(prefix="clips-cookies-", suffix=".txt")
    os.close(handle)
    try:
        shutil.copyfile(_cookie_file, copy)
        yield {**signed, "cookiefile": copy}
    finally:
        discard(Path(copy))


def is_bot_check(error) -> bool:
    return _BOT_CHECK in str(error)


def extract_info(url: str, opts: dict, *, download: bool = False, process: bool = True):
    """yt-dlp's extract_info with `opts`, asked once more the other way when
    YouTube answers with its bot check: over IPv4 after an ordinary request,
    and an ordinary one after IPv4.

    A request that works is made exactly as before, once. Any other failure,
    and a second refusal, is raised as it is. Nobody signs in: this only
    changes which of the PC's own addresses the request leaves from. Unless
    the person set YouTube cookies in Settings: then a second refusal is
    asked a third time, with them.
    """
    import yt_dlp

    # Asked as each caller used to ask: `process` only where one turned it off.
    how = {"download": download} if process else {"download": download, "process": False}

    def ask(options: dict):
        with yt_dlp.YoutubeDL(options) as ydl:
            return ydl.extract_info(url, **how)

    if "source_address" in opts or not _YOUTUBE.search(url):
        return ask(opts)        # an address the caller chose, or not YouTube: as it always was
    ipv4 = {**opts, "source_address": _IPV4}
    first, other = (ipv4, opts) if _session["ipv4_only"] else (opts, ipv4)
    try:
        return ask(first)
    except yt_dlp.utils.DownloadError as e:
        # Starting on IPv4 is a habit of this session, not a need: after a
        # failure of any kind the next request starts the ordinary way.
        _session["ipv4_only"] = False
        if not is_bot_check(e):
            raise
        refused = e
    try:
        info = ask(other)
    except yt_dlp.utils.DownloadError as e:
        if not _cookies:
            if is_bot_check(e):
                raise
            # The other way failed for a reason of its own (no such route, a
            # timeout). The refusal is what stands in the way, and what is said.
            raise refused from e
        # Asked signed in the way that reached YouTube: over IPv4 when both
        # were refused (a PC's IPv6 to YouTube can stall), else the way that
        # was refused while the other failed for a reason of its own.
        print("      (YouTube took the request for a bot; asking again with the "
              "YouTube cookies set in Settings)")
        with _signed_in(ipv4 if is_bot_check(e) else first) as signed:
            return ask(signed)
    # yt-dlp has already printed its refusal, cookie advice and all: say what
    # became of it, so the log does not read as a failure.
    print("      (YouTube took the request for a bot; asked again "
          f"{'over IPv4' if other is ipv4 else 'the ordinary way'} and was served)")
    _session["ipv4_only"] = other is ipv4
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


# ---- which yt-dlp this copy of the app is running ---------------------------
#
# Twitch, Kick and YouTube change something regularly, and yt-dlp fixes it
# within days — but the copy inside Clips Kitty is frozen at build time
# (clips-studio.spec collects yt_dlp into the installer), so an installed app
# can be carrying an extractor that was fixed upstream last week. The gap
# shows up as a download that stopped working, and the failure itself never
# says which yt-dlp produced it: the version is the first thing needed to
# triage a report, so it travels with the failure. Issue #39, and the
# user-facing note in KNOWN-ISSUES.md.

_version: str | None = None


def installed_version() -> str:
    """The yt-dlp this process is actually running: the bundled one in an
    installed build, and site-packages in a checkout. Either way it is the same
    import, and the same one `server/feedback.py` puts in a report.

    Read once and cached. "unknown" when it cannot be read at all: this is
    called from an error path, and a diagnostic is not worth raising a second
    exception over.
    """
    global _version
    if _version is None:
        try:
            from yt_dlp.version import __version__

            _version = str(__version__)
        except Exception:
            _version = "unknown"
    return _version


# The words yt-dlp uses when the site changed under the extractor, rather than
# the network or the account failing. "Confirm you are on the latest version"
# is yt-dlp's own hint, printed with exactly these errors, which makes it the
# strongest evidence available that this copy is the old one.
_EXTRACTOR_SIGNS = (
    "unable to extract",
    "no video formats found",
    "unsupported url",
    "nsig extraction failed",
    "confirm you are on the latest version",
)

# Reaching the site failed. A newer yt-dlp would not have helped, so it must
# not be suggested: pointing someone at a version when their connection is the
# problem only moves them further from the answer.
_NETWORK_SIGNS = (
    "timed out", "timeout", "connection refused", "connection reset",
    "connection aborted", "temporary failure in name resolution", "getaddrinfo",
    "network is unreachable", "no route to host", "incomplete read",
    "unable to download webpage", "http error 5",
)

# The video's own access rules: signed-in, members-only, geo-blocked. Also not
# a version problem, and also not something a re-download fixes.
_ACCESS_SIGNS = (
    "sign in", "not a bot", "cookies", "login required", "private video",
    "video unavailable", "has been removed", "members-only", "members only",
    "join this channel", "age-restricted", "confirm your age", "premieres in",
    "forbidden", "unauthorized", "http error 401", "http error 403",
    "not available in your country", "unavailable in your country",
)

_RELEASES = "https://github.com/yt-dlp/yt-dlp/releases"


def failure_note(error: BaseException) -> str | None:
    """One line to add to a download failure, or None to leave it exactly as
    it is.

    None for a cancellation (nothing failed) and for anything the source
    modules have already put into the app's own words — every one of those
    raises ValueError (`youtube._friendly_errors`, `kick._gone_or_raise`: an
    age-gated video, a deleted Kick VOD, a live stream, a link for the wrong
    platform). Those already say what happened, and a version appended to them
    reads as noise.

    What is left is yt-dlp's own text, which is where the version earns its
    place: it is not in the message, it is the first thing a maintainer needs,
    and someone reporting "Kick stopped working" can now paste it. A newer
    version is named only when the failure has the shape of a site change;
    otherwise the version is given as context and nothing is claimed about the
    cause. See tests/test_ytdlp_failure_diagnostics.py.

    Nothing from `error` is copied into the note, so this cannot put a cookie
    or a private URL back into a message the job layer scrubbed on the way
    out (server/jobs.py).
    """
    if isinstance(error, (cancel.CancelledError, ValueError)):
        return None
    text = str(error).lower()
    version = installed_version()
    if any(sign in text for sign in _EXTRACTOR_SIGNS):
        return (
            f"Clips Kitty's bundled yt-dlp is {version}. This reads like a change on the "
            f"site, which yt-dlp fixes within days — but the copy inside the app is fixed "
            f"at build time, so a newer yt-dlp may already have the fix: {_RELEASES}"
        )
    if any(sign in text for sign in _NETWORK_SIGNS):
        return (
            f"Clips Kitty's bundled yt-dlp is {version}. This reads as the connection "
            f"failing rather than the app or the link, so a newer yt-dlp is very unlikely "
            f"to change it."
        )
    if any(sign in text for sign in _ACCESS_SIGNS):
        return (
            f"Clips Kitty's bundled yt-dlp is {version}. This reads as the video's own "
            f"access rules rather than the app, so a newer yt-dlp is very unlikely to "
            f"change it."
        )
    return f"Clips Kitty's bundled yt-dlp is {version}."
