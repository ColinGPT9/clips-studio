"""Talking to a voice app on this PC, and to nothing else.

Every request the optional voice engines make goes through here, from the
Python engine (never from the app's window, so no Origin header is sent: both
Voicebox and VoiceStudio refuse a browser's origin).

One rule, checked before any socket is opened: the address must be this PC's
own loopback address, written as a number (127.x.x.x or ::1) or as
"localhost", which is read as 127.0.0.1 and never looked up. A LAN address,
0.0.0.0, a hostname, an address with a user name in it or anything that is
not plain http is refused with a plain message. Redirects are refused too (a
local server could otherwise point the request anywhere), and the system's
proxy settings are ignored, so a loopback request is never handed to a proxy.
"""

from __future__ import annotations

import http.client
import ipaddress
import json
import socket
import urllib.error
import urllib.request
from urllib.parse import urlsplit, urlunsplit

# A spoken answer bigger than this is not one sentence or one script: stop
# reading instead of filling the disk. About 80 minutes of 24 kHz speech.
MAX_AUDIO_BYTES = 256 * 1024 * 1024
MAX_JSON_BYTES = 8 * 1024 * 1024

NOT_LOCAL = ("Only a voice app on this PC can be used. Enter an address like "
             "http://127.0.0.1:3900.")


class NotLocal(ValueError):
    """The address is not this PC's loopback address."""


class Unreachable(OSError):
    """Nothing answered at the address (the app is closed)."""


class BadAnswer(RuntimeError):
    """The server answered, but not with what was asked for. `status` is the
    HTTP status when there was one; `message` is the server's own words,
    shortened, or ours."""

    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


def local_base(address: str) -> str:
    """`address` as "http://<loopback>:<port>" with no path, or NotLocal.

    Accepts "127.0.0.1:3900", "http://localhost:3900/", "http://[::1]:8880".
    A path, query or fragment is dropped: the engines add their own routes.
    """
    text = (address or "").strip()
    if not text:
        raise NotLocal(NOT_LOCAL)
    if "\\" in text or any(ord(c) < 33 or ord(c) == 127 for c in text):
        raise NotLocal(NOT_LOCAL)
    if "://" not in text:
        text = "http://" + text
    try:
        parts = urlsplit(text)
        port = parts.port
    except ValueError:
        raise NotLocal(NOT_LOCAL) from None
    if parts.scheme != "http" or parts.username is not None or parts.password is not None \
            or "@" in parts.netloc:
        raise NotLocal(NOT_LOCAL)
    host = (parts.hostname or "").lower()
    if host == "localhost":
        host = "127.0.0.1"
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        raise NotLocal(NOT_LOCAL) from None
    # ::ffff:127.0.0.1 is loopback written the long way; anything else mapped
    # is not. is_loopback alone says False for mapped addresses on older
    # Pythons and True on newer ones, so unwrap it first.
    mapped = getattr(ip, "ipv4_mapped", None)
    if mapped is not None:
        ip = mapped
    if not ip.is_loopback:
        raise NotLocal(NOT_LOCAL)
    if port is None:
        port = 80
    if not 1 <= port <= 65535:
        raise NotLocal(NOT_LOCAL)
    shown = f"[{ip}]" if ip.version == 6 else str(ip)
    return urlunsplit(("http", f"{shown}:{port}", "", "", ""))


def _host_port(base: str) -> tuple[str, int]:
    parts = urlsplit(local_base(base))
    return parts.hostname or "127.0.0.1", int(parts.port or 80)


def listening(base: str, timeout: float = 0.5) -> bool:
    """Whether anything accepts a connection at `base`.

    A quick look before a real request: on Windows a refused loopback
    connection takes two seconds to report, an accepted one is immediate."""
    host, port = _host_port(base)
    family = socket.AF_INET6 if ":" in host else socket.AF_INET
    try:
        with socket.socket(family, socket.SOCK_STREAM) as s:
            s.settimeout(timeout)
            s.connect((host, port))
        return True
    except OSError:
        return False


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise BadAnswer("The voice app sent the request somewhere else, so it was stopped.", code)


def _opener() -> urllib.request.OpenerDirector:
    # ProxyHandler({}) = no proxy at all, whatever the system or the
    # environment says. Only http: no https, ftp or file handler is added.
    opener = urllib.request.OpenerDirector()
    for handler in (urllib.request.ProxyHandler({}), urllib.request.UnknownHandler(),
                    urllib.request.HTTPHandler(), urllib.request.HTTPDefaultErrorHandler(),
                    _NoRedirect(), urllib.request.HTTPErrorProcessor()):
        opener.add_handler(handler)
    return opener


def _server_words(body: bytes) -> str:
    """What a failed answer says, from OpenAI's shape ({"error": {"message"}})
    or FastAPI's ({"detail": ...}); else nothing. Kept short and on one line."""
    try:
        data = json.loads(body.decode("utf-8", "replace"))
    except ValueError:
        return ""
    found = None
    if isinstance(data, dict):
        err = data.get("error")
        if isinstance(err, dict):
            found = err.get("message")
        elif isinstance(err, str):
            found = err
        if found is None:
            found = data.get("detail") if isinstance(data.get("detail"), str) else data.get("message")
    if not isinstance(found, str):
        return ""
    return " ".join(found.split())[:200]


def request(base: str, path: str, *, body: dict | None = None, timeout: float = 10.0,
            limit: int = MAX_JSON_BYTES) -> tuple[bytes, str]:
    """GET `path` (or POST `body` as JSON) on the local server at `base`.

    Returns (bytes, content type). NotLocal for an address that is not this
    PC; Unreachable when nothing answers; BadAnswer for an HTTP error, a
    redirect or an answer over `limit` bytes.
    """
    url = local_base(base) + "/" + path.lstrip("/")
    headers = {"User-Agent": "ClipsKitty", "Accept": "*/*"}
    data = None
    if body is not None:
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers, method="POST" if data is not None else "GET")
    try:
        with _opener().open(req, timeout=timeout) as resp:
            got = resp.read(limit + 1)
            if len(got) > limit:
                raise BadAnswer("The voice app sent far more than was asked for, so it was stopped.")
            return got, (resp.headers.get("Content-Type") or "").split(";")[0].strip().lower()
    except urllib.error.HTTPError as e:
        try:
            words = _server_words(e.read(MAX_JSON_BYTES))
        except OSError:
            words = ""
        raise BadAnswer(words or f"The voice app answered with an error ({e.code}).", e.code) from None
    except BadAnswer:
        raise
    except (urllib.error.URLError, OSError, ValueError, http.client.HTTPException) as e:
        # A cut connection or a broken status line is http.client's own error;
        # a timeout is an OSError.
        reason = getattr(e, "reason", e)
        if isinstance(reason, (TimeoutError, socket.timeout)) or isinstance(e, (TimeoutError, socket.timeout)):
            raise BadAnswer("The voice app took too long to answer.") from None
        raise Unreachable(str(reason)[:120]) from None


def get_json(base: str, path: str, timeout: float = 5.0):
    got, _kind = request(base, path, timeout=timeout)
    try:
        return json.loads(got.decode("utf-8", "replace"))
    except ValueError:
        raise BadAnswer("The voice app did not answer the way this version expects.") from None
