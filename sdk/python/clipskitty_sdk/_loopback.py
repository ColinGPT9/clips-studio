# SPDX-License-Identifier: MIT
# Copyright (c) 2026 ColinGPT9. The Clips Kitty SDK; see sdk/python/LICENSE.
"""Requests to this PC that never go through a proxy (private to the SDK).

urllib's default opener takes proxy settings from the environment
(http_proxy, HTTP_PROXY, HTTPS_PROXY and the like) and, on Windows, from the
system's settings, and it sends a request for 127.0.0.1 or localhost to that
proxy unless no_proxy says otherwise. Clips Kitty passes HTTP_PROXY on to
plugins, so a plugin's request to a model or to Clips Kitty on this PC could
leave the PC. Checking the address doesn't help; the route does: urlopen()
here sends every request through one opener built with an empty
ProxyHandler, which uses no proxy at all. That opener follows a redirect
only to the same address (scheme, host and port): urllib's own would send
the request, and its headers, wherever a redirect points, even off this PC.

local_model and LocalAPI (for this PC's addresses) use it. Standard library
only.
"""

from __future__ import annotations

import urllib.error
import urllib.parse
import urllib.request

# The addresses that mean this PC. Only these count; another 127.x address,
# or this PC's name, is treated as somewhere else.
THIS_PC = frozenset({"127.0.0.1", "localhost", "::1"})

# Why a redirect to another address isn't followed: the HTTPError's reason.
REDIRECT_REFUSED = "it redirected to another address, and requests to this PC don't follow that"
_DEFAULT_PORTS = {"http": 80, "https": 443}


def _origin(url: str):
    """(scheme, host, port) of `url`, or None when its port can't be read."""
    try:
        parts = urllib.parse.urlsplit(str(url))
        port = parts.port
    except ValueError:
        return None
    scheme = parts.scheme.lower()
    return scheme, (parts.hostname or "").lower(), port or _DEFAULT_PORTS.get(scheme)


class _SameAddressRedirects(urllib.request.HTTPRedirectHandler):
    """Follows a redirect only to the same scheme, host and port, such as a
    web framework's added or removed slash. Any other is answered with an
    HTTPError of the redirect's own code: a request to this PC, and the
    session header the installer sends, never go somewhere else."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        here = _origin(req.full_url)
        if here is None or _origin(newurl) != here:
            raise urllib.error.HTTPError(req.full_url, code, REDIRECT_REFUSED, headers, fp)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}), _SameAddressRedirects())


def is_this_pc(url: str) -> bool:
    """Whether `url` (such as "http://127.0.0.1:11434") points at this PC:
    its host is 127.0.0.1, localhost or ::1."""
    try:
        host = urllib.parse.urlsplit(str(url)).hostname
    except ValueError:
        return False
    return (host or "").lower() in THIS_PC


def port_ok(url: str) -> bool:
    """Whether `url` has no port or a port from 1 to 65535: urllib can't
    send a request otherwise ("http://localhost:87x5")."""
    try:
        port = urllib.parse.urlsplit(str(url)).port
    except ValueError:
        return False
    return port is None or port > 0


def urlopen(request, timeout: float):
    """Send `request` (a urllib Request or a URL) with no proxy, whatever the
    environment or the system's settings say, following a redirect only to
    the same address. Raises what urllib's urlopen raises, http.client's
    errors (a cut-off answer, one that isn't HTTP) included."""
    return _OPENER.open(request, timeout=timeout)
