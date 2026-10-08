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
ProxyHandler, which uses no proxy at all.

local_model and LocalAPI (for this PC's addresses) use it. Standard library
only.
"""

from __future__ import annotations

import urllib.parse
import urllib.request

# The addresses that mean this PC. Only these count; another 127.x address,
# or this PC's name, is treated as somewhere else.
THIS_PC = frozenset({"127.0.0.1", "localhost", "::1"})

_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def is_this_pc(url: str) -> bool:
    """Whether `url` (such as "http://127.0.0.1:11434") points at this PC:
    its host is 127.0.0.1, localhost or ::1."""
    try:
        host = urllib.parse.urlsplit(str(url)).hostname
    except ValueError:
        return False
    return (host or "").lower() in THIS_PC


def urlopen(request, timeout: float):
    """Send `request` (a urllib Request or a URL) with no proxy, whatever the
    environment or the system's settings say. Raises what urllib's urlopen
    raises."""
    return _OPENER.open(request, timeout=timeout)
