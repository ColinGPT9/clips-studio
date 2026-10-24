"""The one way Clips Kitty talks to Discord: a thin `requests` wrapper tests
replace, the API's address, and what a failed request means.

Tests never reach the real Discord. They hand the OAuth client and the
webhook sender an object with the same `.request(method, url, **kw)` (a
scripted fake), or point the whole engine at a fake server on this PC through
CLIPS_KITTY_DISCORD_API / CLIPS_KITTY_DISCORD_AUTHORIZE. Those two are
honoured only for a plain http://127.0.0.1:<port> address, so a stray
variable can never send sign-ins or webhook tokens anywhere else.

Nothing here logs a request: a webhook's URL is its token, and an OAuth
request carries a code or a token in its body.
"""

import os
import re

import requests

API_BASE = "https://discord.com/api/v10"
AUTHORIZE_URL = "https://discord.com/oauth2/authorize"
API_ENV = "CLIPS_KITTY_DISCORD_API"
AUTHORIZE_ENV = "CLIPS_KITTY_DISCORD_AUTHORIZE"

#: (connect, read) seconds for every Discord request.
TIMEOUT = (5, 15)
USER_AGENT = "ClipsKitty (https://github.com/ColinGPT9/clips-studio)"

_LOOPBACK = re.compile(r"^http://127\.0\.0\.1:\d{1,5}(?:/[^\s?#]*)?$")


class DiscordError(Exception):
    """A Discord call that didn't work, with what to show and a code for what
    to do about it: network, expired (sign in again), denied, bad_response,
    rate_limited, not_configured, not_connected, gone (a webhook that no
    longer exists)."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def _override(name: str, default: str) -> str:
    value = str(os.environ.get(name) or "").strip().rstrip("/")
    return value if value and _LOOPBACK.match(value) else default


def api_base() -> str:
    """Discord's API, or a fake one on this PC (tests, the scratch E2E)."""
    return _override(API_ENV, API_BASE)


def authorize_base() -> str:
    """Discord's sign-in page, or a fake one on this PC."""
    return _override(AUTHORIZE_ENV, AUTHORIZE_URL)


class Http:
    """requests with Discord's timeouts and a User-Agent. Tests inject any
    object with the same request()."""

    def request(self, method: str, url: str, **kw):
        headers = {"User-Agent": USER_AGENT, **(kw.pop("headers", None) or {})}
        kw.setdefault("timeout", TIMEOUT)
        return requests.request(method, url, headers=headers, **kw)


def body(response):
    """A response's JSON, or {} when it has none Clips Kitty can read."""
    try:
        return response.json()
    except Exception:
        return {}


def _causes(exc: BaseException) -> list[BaseException]:
    """The exception and everything it wraps: requests wraps urllib3, which
    wraps the socket error, in arguments as well as in causes."""
    seen: list[BaseException] = []
    stack: list = [exc]
    while stack:
        current = stack.pop()
        if not isinstance(current, BaseException) or any(current is s for s in seen):
            continue
        seen.append(current)
        stack.extend([getattr(current, "reason", None), current.__cause__, current.__context__,
                      *getattr(current, "args", ())])
    return seen


def never_sent(exc: BaseException) -> bool:
    """Whether a failed request certainly never reached Discord: a connect
    timeout, a refused connection or a name that didn't resolve (DESIGN
    §4.3). Anything else (a read timeout, a connection dropped after it was
    made, a TLS error) may have been received, and a webhook has no way to
    tell, so it counts as maybe sent."""
    import socket

    from urllib3 import exceptions as u3

    # NameResolutionError is a NewConnectionError: the connection was never made.
    before = (requests.ConnectTimeout, u3.NewConnectionError, u3.ConnectTimeoutError, socket.gaierror,
              ConnectionRefusedError)
    if isinstance(exc, requests.ReadTimeout):
        return False
    return any(isinstance(e, before) for e in _causes(exc))
