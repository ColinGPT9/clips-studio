# SPDX-License-Identifier: MIT
# Copyright (c) 2026 ColinGPT9. The Clips Kitty SDK; see sdk/python/LICENSE.
"""Connected-account actions authorized for the current plugin run.

The host selects one connection and channel from the source video, checks
manifest permissions and OAuth scopes, and retains provider credentials.
This helper cannot select another account or send arbitrary API requests.
No Clips Kitty account, provider login or raw token belongs in a plugin.
"""
from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
import uuid

from . import _loopback

ENDPOINT_ENV = "CLIPSKITTY_ACCOUNT_ENDPOINT"
CAPABILITY_ENV = "CLIPSKITTY_ACCOUNT_CAPABILITY"
ROUTE = "/plugin-accounts/perform"
HEADER = "X-Clips-Kitty-Capability"


class AccountOperationError(RuntimeError):
    """A host operation that is unavailable, not granted or refused by Twitch."""

    def __init__(self, message, status=0):
        self.status = status
        super().__init__(message)


class ConnectedAccounts:
    """Only the account actions granted by Clips Kitty for this run.

    Use ``job.accounts.twitch.context()`` or ``ConnectedAccounts().twitch``.
    ``available`` is false when running a standalone SDK sample, when the
    source cannot be bound safely to an account, or without account permissions.
    """

    def __init__(self, *, timeout=20.0):
        self._endpoint = os.environ.get(ENDPOINT_ENV, "")
        self._capability = os.environ.get(CAPABILITY_ENV, "")
        self.timeout = timeout
        self.twitch = Twitch(self)

    def __repr__(self):
        return f"ConnectedAccounts(available={self.available})"

    @property
    def available(self):
        try:
            parts = urllib.parse.urlsplit(self._endpoint)
            return bool(self._capability and re.fullmatch(r"[A-Za-z0-9_-]{32,128}", self._capability)
                        and parts.scheme == "http" and _loopback.is_this_pc(self._endpoint)
                        and _loopback.port_ok(self._endpoint) and parts.path == ROUTE
                        and not parts.username and not parts.password and not parts.query and not parts.fragment)
        except ValueError:
            return False

    def _perform(self, operation, parameters):
        if not self.available:
            raise AccountOperationError("Clips Kitty has not granted account operations for this run.")
        data = json.dumps({"operation": operation, "parameters": parameters,
                           "request_id": uuid.uuid4().hex}).encode("utf-8")
        req = urllib.request.Request(self._endpoint, data=data, method="POST",
                                     headers={"Content-Type": "application/json", HEADER: self._capability})
        try:
            with _loopback.urlopen(req, timeout=self.timeout) as response:
                raw = response.read(1_000_001)
            if len(raw) > 1_000_000:
                raise ValueError("oversized response")
            answer = json.loads(raw)
            return answer["result"]
        except urllib.error.HTTPError as error:
            # No response body or request URL is reflected into plugin logs.
            messages = {403: "Clips Kitty has not granted that account operation for this run.",
                        409: "Clips Kitty already attempted that account operation. Check its outcome in the app.",
                        429: "This run has reached its connected-account operation limit."}
            raise AccountOperationError(messages.get(error.code, "Clips Kitty could not complete that account operation."),
                                        error.code) from None
        except (urllib.error.URLError, OSError, ValueError, KeyError, TypeError):
            # Never retry a write automatically after an ambiguous timeout.
            raise AccountOperationError("Clips Kitty's account service did not answer. Check its outcome in the app.") from None


class Twitch:
    """Fixed operations for this run's Twitch channel and source VOD."""

    def __init__(self, accounts):
        self._accounts = accounts

    def __repr__(self):
        return "Twitch(connected_account_operations)"

    def context(self):
        """Sanitized broadcaster, stream and VOD context; needs twitch.context.read."""
        return self._accounts._perform("stream_context", {})

    def videos(self, *, first=20):
        """Channel VOD metadata; needs twitch.context.read."""
        return self._accounts._perform("videos", {"first": first})

    def create_clip(self, *, has_delay=False):
        """Create a native clip from the live broadcast, never from VOD time.

        Needs twitch.clips.create and broadcaster authorization/scopes. A
        completed VOD may use create_vod_clip() when the host verifies its
        timeline and Twitch grants the separate VOD-clip permissions.
        """
        return self._accounts._perform("create_clip", {"has_delay": has_delay})

    def timestamp_link(self, seconds=0):
        """Source-media time mapped to a VOD link by a verified host timeline."""
        return self._accounts._perform("vod_link", {"seconds": seconds})

    def audience_signals(self):
        """PII-free viewer nominations mapped to this source's verified timeline."""
        return self._accounts._perform("audience_signals", {})

    def source_info(self):
        """Bound source fingerprint, VOD ID and duration, without local file paths."""
        return self._accounts._perform("source_info", {})

    def create_vod_clip(self, start_seconds, end_seconds, title):
        """Request a native VOD clip from verified source-media times, when permitted.

        Needs twitch.clips.create and Twitch's channel/editor manage-clips
        authorization. A processing ID is not a published clip URL; get_clip
        checks the result. There is no automatic retry after uncertain writes.
        """
        return self._accounts._perform("create_vod_clip", {
            "start_seconds": start_seconds, "end_seconds": end_seconds, "title": title,
        })

    def get_clip(self, clip_id):
        """Check a clip for this bound VOD; only Twitch's returned public URL is used."""
        return self._accounts._perform("get_clip", {"clip_id": clip_id})

    def send_chat(self, message):
        """Send a chat message to this channel; needs twitch.chat.send and OAuth authorization."""
        return self._accounts._perform("send_chat", {"message": message})

    def start_events(self, event_types=None):
        """Start the host's EventSub connection; needs twitch.events.read.

        channel.chat.message additionally needs twitch.chat.read. The host
        owns the socket and session; neither OAuth tokens nor session IDs
        enter the plugin.
        """
        return self._accounts._perform("events_start", {"event_types": event_types or ["stream.online", "stream.offline"]})

    def events(self, *, after=0, limit=100):
        """Read bounded sanitized events after a cursor; chat also needs twitch.chat.read."""
        return self._accounts._perform("events", {"after": after, "limit": limit})
