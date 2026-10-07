"""The session secret the plugin manager's routes ask for.

Routes that install, change or remove plugins need it in an
`X-Clips-Kitty-Session` header. The desktop app makes a random one at each
start and gives it to the engine in CLIPS_KITTY_SESSION_SECRET and to its own
window; an engine started on its own makes one. Either way the engine writes
the current one to <data_dir>/plugins/session.secret, readable only by the
user, so scripts can find it.

What it stops: web pages, which can send some requests to 127.0.0.1 without
being able to read the answer but can't add this header (the CORS allow-list
refuses the preflight a custom header needs), and stray scripts that don't
know it. What it doesn't stop: software already running as the user, which
can read that file or the engine's environment, installed plugins included.
"""

from __future__ import annotations

import hmac
import os
import re
import secrets
from pathlib import Path

HEADER = "X-Clips-Kitty-Session"
ENV = "CLIPS_KITTY_SESSION_SECRET"
FILE = "session.secret"
_SHAPE = re.compile(r"^[A-Za-z0-9_-]{16,256}$")


def path(data_dir) -> Path:
    return Path(data_dir) / "plugins" / FILE


def secret_for(data_dir, environ=None) -> str:
    """The secret for this run of the engine: the desktop app's, else a new
    one. Written to session.secret either way."""
    environ = os.environ if environ is None else environ
    given = str(environ.get(ENV) or "").strip()
    value = given if _SHAPE.match(given) else secrets.token_urlsafe(32)
    if given and value != given:
        print(f"{ENV} is not 16 to 256 letters, digits, - or _; using a new session secret instead.")
    target = path(data_dir)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(value + "\n")
    os.replace(tmp, target)
    return value


def matches(expected: str, given: str | None) -> bool:
    return bool(given) and hmac.compare_digest(expected.encode(), str(given).strip().encode())
