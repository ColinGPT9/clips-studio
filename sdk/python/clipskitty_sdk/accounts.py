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
# The refusal's fixed code from Clips Kitty: a lower-case word, nothing else.
ERROR_HEADER = "X-Clips-Kitty-Error"
_CODE = re.compile(r"[a-z_]{1,40}")

# This SDK's own message for each status. A code below, when Clips Kitty sends
# one, says more than its status does: two refusals can share a status.
_STATUS_MESSAGES = {
    400: "Clips Kitty refused that account request. Check the values passed to it.",
    401: "Twitch needs reconnecting in Clips Kitty's Settings.",
    403: "Clips Kitty has not granted that account operation for this run.",
    404: "That Twitch item isn't available any more.",
    409: "Clips Kitty already attempted that account operation. Check its outcome in the app.",
    412: "Clips Kitty hasn't verified this video's Twitch timeline, so it can't make "
         "a Twitch link or VOD clip for it.",
    429: "This run has reached its connected-account operation limit.",
    503: "Clips Kitty's Twitch connection isn't available right now.",
}
_CODE_MESSAGES = {
    "missing_scope": "The Twitch connection doesn't include the Twitch permission this needs. "
                     "Reconnect Twitch in Clips Kitty's Settings with that permission ticked.",
    "permission_denied": "Twitch allows that only for the connected account's own channel.",
    "provider_unavailable": "Twitch could not be reached or is having trouble. Try again later.",
    "rate_limited": "Twitch is limiting requests right now. Try again later.",
    "provider_not_configured": "Twitch isn't set up in Clips Kitty's Settings.",
    "credential_store_unavailable": "Clips Kitty couldn't read the Twitch connection from this PC's "
                                    "credential store.",
    "unavailable": "That part of Clips Kitty's Twitch support isn't running right now.",
}
GENERIC_MESSAGE = "Clips Kitty could not complete that account operation."


class AccountOperationError(RuntimeError):
    """A host operation that is unavailable, not granted or refused by Twitch.

    ``status`` is the HTTP status (0 when Clips Kitty didn't answer) and
    ``code`` the fixed word Clips Kitty gave for the refusal, or "" when it
    gave none: for example ``not_permitted`` (this run isn't allowed) and
    ``missing_scope`` (the Twitch connection lacks the permission) are both 403.
    """

    def __init__(self, message, status=0, code=""):
        self.status = status
        self.code = code
        super().__init__(message)


def _refusal(error):
    """Turn the host's HTTP refusal into an error. No body or URL is reflected."""
    status = error.code
    try:
        code = (error.headers.get(ERROR_HEADER) or "") if error.headers is not None else ""
    except AttributeError:
        code = ""
    code = code if _CODE.fullmatch(code) else ""
    message = _CODE_MESSAGES.get(code) or _STATUS_MESSAGES.get(status, GENERIC_MESSAGE)
    return AccountOperationError(message, status, code)


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
            raise _refusal(error) from None
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

    def timestamp_link(self, seconds=0):
        """Source-media time mapped to a VOD link by a verified host timeline.

        Clips Kitty verifies the timeline itself when this run's file is the
        whole Twitch archive (how watched channels download). Otherwise this
        raises AccountOperationError with status 412.
        """
        return self._accounts._perform("vod_link", {"seconds": seconds})

    def audience_signals(self):
        """PII-free viewer nominations mapped to this source's verified timeline.

        Until a stream-to-VOD alignment exists for this video, which nothing
        in the app makes yet, the answer has status "unavailable" with a
        ``reason`` and a plain ``message``. Nomination times are when chat
        arrived, so they trail the moment by the viewers' delay.
        """
        return self._accounts._perform("audience_signals", {})

    def source_info(self):
        """Bound source fingerprint, VOD ID and duration, without local file paths."""
        return self._accounts._perform("source_info", {})

    def create_vod_clip(self, start_seconds, end_seconds, title):
        """Request a native VOD clip from verified source-media times, when permitted.

        Needs twitch.clips.create and Twitch's channel/editor manage-clips
        authorization. Twitch takes a whole-second end: the end is rounded to
        the nearest second and the length to 0.1 s (5 to 60 s), and the result
        gives the VOD times used. Status 412 means the timeline isn't verified
        (see timestamp_link). A processing ID is not a published clip URL;
        get_clip checks the result. There is no automatic retry after
        uncertain writes.
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
