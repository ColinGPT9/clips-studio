"""Frames of a video BEFORE it is processed, for setting up a Gaming / Reaction
split on the real picture (the Generate bar's split setup, like the layout
step StreamLadder shows before processing).

Nothing is downloaded. A link is asked once for its media URL (yt-dlp, no
download, a 720p-or-smaller video stream) and FFmpeg seeks straight to the
moment wanted, reading only what that frame needs; a file on this computer is
read in place. Only links to the platforms Clips Kitty clips from are
accepted, so this can't be pointed at anything else on the network.

YouTube's media URLs are tied to the IPv4 address that asked for them, and
FFmpeg (which has no "IPv4 only" switch) tries IPv6 first: on a network with
a broken IPv6 route that read hung for the full timeout, where the same byte
range over IPv4 took half a second. So YouTube is read through _Relay, a
loopback-only relay that forwards FFmpeg's range requests over IPv4.
"""

import hashlib
import os
import secrets
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import ClassVar

from core.binaries import ffmpeg, ffprobe
from core.paths import discard

FRAME_WIDTH = 960
_TTL = 1800.0                  # media URLs from YouTube expire in hours; keep ours short
_probed: dict[str, tuple[float, str, float, dict]] = {}
_lock = threading.Lock()


class _Relay(BaseHTTPRequestHandler):
    """Forwards GET/HEAD for a registered token to its media URL over IPv4,
    passing the Range header through. Bound to 127.0.0.1; only tokens this
    module registered (a random 128-bit name per URL) are served."""

    targets: ClassVar[dict[str, tuple[str, dict]]] = {}
    _session = None

    @classmethod
    def session(cls):
        if cls._session is None:
            import requests
            from requests.adapters import HTTPAdapter

            class IPv4(HTTPAdapter):
                def init_poolmanager(self, *args, **kwargs):
                    # Bound to an IPv4 address, an IPv6 connect fails at once
                    # and urllib3 moves on to the IPv4 address.
                    kwargs["source_address"] = ("0.0.0.0", 0)
                    super().init_poolmanager(*args, **kwargs)

            s = requests.Session()
            s.mount("https://", IPv4())
            cls._session = s
        return cls._session

    def _forward(self, body: bool) -> None:
        target = self.targets.get(self.path.lstrip("/"))
        if target is None:
            self.send_error(404)
            return
        url, headers = target
        send = dict(headers)
        if self.headers.get("Range"):
            send["Range"] = self.headers["Range"]
        try:
            with self.session().get(url, headers=send, stream=True, timeout=(10, 30)) as r:
                self.send_response(r.status_code)
                for key in ("Content-Type", "Content-Length", "Content-Range", "Accept-Ranges"):
                    if key in r.headers:
                        self.send_header(key, r.headers[key])
                self.end_headers()
                if body:
                    for chunk in r.iter_content(64 * 1024):
                        self.wfile.write(chunk)
        except (ConnectionError, OSError):
            pass                                    # FFmpeg closed the connection: it had enough

    def do_GET(self):
        self._forward(body=True)

    def do_HEAD(self):
        self._forward(body=False)

    def log_message(self, *_args) -> None:
        pass


_relay_server: ThreadingHTTPServer | None = None


def _relayed(url: str, headers: dict) -> str:
    """A loopback URL that reads `url` over IPv4 (see _Relay)."""
    global _relay_server
    with _lock:
        if _relay_server is None:
            _relay_server = ThreadingHTTPServer(("127.0.0.1", 0), _Relay)
            _relay_server.daemon_threads = True
            threading.Thread(target=_relay_server.serve_forever, daemon=True, name="frame-relay").start()
        token = secrets.token_hex(16)
        _Relay.targets[token] = (url, headers)
        port = _relay_server.server_address[1]
    return f"http://127.0.0.1:{port}/{token}"


_making: dict[str, threading.Lock] = {}


def made_once(folder: Path, name: str, make) -> Path:
    """`folder`/`name`, made by make(part_path) only once however many
    requests ask for it at the same moment (the layout editor asks for a frame
    and its thumbnail together). The others wait and get the same file, and a
    file is never replaced while it is being served: on Windows that fails
    outright.

    The name comes from the request (a hash, a clip id, a moment), so it is
    held to `folder`: nothing it could contain reaches outside it."""
    base = os.path.normpath(folder)
    target = os.path.normpath(os.path.join(base, name))
    if not target.startswith(base + os.sep):
        raise ValueError(f"{name!r} is not a file in {folder}")
    out = Path(target)
    with _lock:
        lock = _making.setdefault(str(out), threading.Lock())
    with lock:
        if out.exists() and out.stat().st_size > 0:
            return out
        out.parent.mkdir(parents=True, exist_ok=True)
        part = out.with_name(f"{out.stem}.{threading.get_ident()}.part{out.suffix}")
        try:
            make(part)
            part.replace(out)
        finally:
            discard(part)
    return out


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
    if source == "youtube":
        media, headers = _relayed(media, headers), {}
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

    def make(part: Path) -> None:
        headers: dict = {}
        if url is not None:
            media, duration, headers = _probe_link(url)
        else:
            media, duration = str(path), _file_duration(path)
        when = at * duration if duration > 0 else 0.0
        cmd = [ffmpeg(), "-y", "-v", "error"]
        if headers:
            cmd += ["-headers", "".join(f"{k}: {v}\r\n" for k, v in headers.items())]
        cmd += ["-ss", f"{when:.2f}", "-i", media, "-frames:v", "1",
                "-vf", f"scale={FRAME_WIDTH}:-2", "-q:v", "3", str(part)]
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=90)
        except subprocess.TimeoutExpired as e:
            raise NotFrameable("The video took too long to answer. Try another frame.") from e
        if r.returncode != 0 or not part.exists() or part.stat().st_size == 0:
            raise NotFrameable("Couldn't read a frame at that point of the video. Try another one.")

    return made_once(cache_dir, f"src_{key}.jpg", make)
