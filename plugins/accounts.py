"""Run-scoped access to connected-account operations, never provider tokens.

An installed pipeline receives a random capability only while its process is
running. Its grant fixes the connection, broadcaster, source VOD and manifest
permissions. It cannot request an arbitrary URL or use the desktop session.
Native plugins still run as the user's OS account: this is a hand-over
boundary, not an operating-system sandbox (plugins/permissions.py).
"""
from __future__ import annotations

import copy
import json
import math
import re
import secrets
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path

from plugins import store

HEADER = "X-Clips-Kitty-Capability"
ENDPOINT_ENV = "CLIPSKITTY_ACCOUNT_ENDPOINT"
CAPABILITY_ENV = "CLIPSKITTY_ACCOUNT_CAPABILITY"
ROUTE = "/plugin-accounts/perform"
PERMISSIONS = {
    "stream_context": "twitch.context.read",
    "videos": "twitch.context.read",
    "vod_link": "twitch.context.read",
    "audience_signals": "twitch.context.read",
    "source_info": "twitch.context.read",
    "create_vod_clip": "twitch.clips.create",
    "get_clip": "twitch.clips.create",
    "create_clip": "twitch.clips.create",
    "send_chat": "twitch.chat.send",
    "events_start": "twitch.events.read",
    "events": "twitch.events.read",
}
EVENT_TYPES = frozenset({"stream.online", "stream.offline", "channel.update", "channel.chat.message"})
_REQUEST_ID = re.compile(r"[a-f0-9]{32}\Z")
_SECRET_FIELDS = frozenset({"access_token", "refresh_token", "token", "client_secret", "device_code",
                            "authorization", "credentials", "secret"})
_GATEWAYS: dict[str, Gateway] = {}
_GATEWAYS_LOCK = threading.Lock()


class AccessDenied(RuntimeError):
    """A fixed public error; exception details and provider responses stay private."""

    def __init__(self, message="This run is not permitted to use that account operation.", status=403):
        self.status = status
        super().__init__(message)


@dataclass(repr=False)
class _Grant:
    plugin_id: str
    version: str
    permissions: frozenset
    binding: dict
    expires: float
    replies: dict = field(default_factory=dict)
    counts: dict = field(default_factory=dict)
    lock: threading.Lock = field(default_factory=threading.Lock)


def _key(data_dir):
    return str(Path(data_dir).resolve())


def _public(value):
    """Defence in depth if a future provider adds a credential-shaped field."""
    if isinstance(value, dict):
        return {k: _public(v) for k, v in value.items() if str(k).lower() not in _SECRET_FIELDS}
    if isinstance(value, list):
        return [_public(v) for v in value]
    return value


class Gateway:
    def __init__(self, data_dir, service, binding_for_video, *, base_url="http://127.0.0.1:8765",
                 event_manager=None, audience_for_binding=None, timeline_mapper=None, clock=time.monotonic):
        from clipskitty_sdk import _loopback

        if not _loopback.is_this_pc(base_url) or not _loopback.port_ok(base_url):
            raise ValueError("The account gateway must listen on this PC.")
        from urllib.parse import urlsplit
        parts = urlsplit(base_url)
        if parts.scheme != "http" or parts.username or parts.password or parts.path.strip("/") or parts.query or parts.fragment:
            raise ValueError("The account gateway must have a plain loopback HTTP origin.")
        self.data_dir = data_dir
        self.service = service
        self.binding_for_video = binding_for_video
        self.event_manager = event_manager
        self.audience_for_binding = audience_for_binding
        self.timeline_mapper = timeline_mapper
        self.endpoint = base_url.rstrip("/") + ROUTE
        self.clock = clock
        self._grants = {}
        self._lock = threading.Lock()

    @contextmanager
    def run(self, plugin, video, timeout):
        permissions = frozenset(plugin.manifest.get("permissions") or []) & (frozenset(PERMISSIONS.values()) | {"twitch.chat.read"})
        binding = None
        if permissions and self.binding_for_video:
            try:
                binding = self.binding_for_video(video)
            except Exception:
                # OAuth/network failure must not break the existing offline clipping workflow.
                binding = None
        valid = (isinstance(binding, dict) and str(binding.get("account_id", "")).startswith("twitch:")
                 and re.fullmatch(r"[0-9]+", str(binding.get("broadcaster_id", ""))))
        if not valid:
            yield {}
            return
        binding = {k: binding[k] for k in ("account_id", "broadcaster_id", "video_id", "duration", "source_id") if k in binding}
        if "duration" not in binding:
            binding["duration"] = getattr(video, "duration", None)
        token = secrets.token_urlsafe(32)
        grant = _Grant(plugin.id, plugin.version, permissions, binding,
                       self.clock() + min(86400.0, max(1.0, float(timeout))))
        with self._lock:
            self._grants[token] = grant
        try:
            yield {ENDPOINT_ENV: self.endpoint, CAPABILITY_ENV: token}
        finally:
            with self._lock:
                self._grants.pop(token, None)

    def _check(self, token, grant, permission):
        with self._lock:
            if self._grants.get(token) is not grant or self.clock() >= grant.expires:
                raise AccessDenied("The connected-account grant for this run has ended.")
        if permission not in grant.permissions:
            raise AccessDenied()
        # Re-read the installed manifest on every call: disable, removal, update,
        # block-list changes and permission withdrawal take effect immediately.
        try:
            from plugins import registry

            current = store.get(self.data_dir, grant.plugin_id, grant.version)
            if current is None or not current.enabled or store.compatibility_problem(current.manifest):
                raise AccessDenied()
            hit = registry.blocked_check(self.data_dir)(current.id, current.version)
            if hit and hit.get("severity") == "blocked":
                raise AccessDenied()
        except (ValueError, OSError, ImportError):
            raise AccessDenied() from None
        if permission not in set(current.manifest.get("permissions") or []):
            raise AccessDenied()
        return set(current.manifest.get("permissions") or []) & grant.permissions

    def perform(self, token, operation, parameters, request_id):
        if not isinstance(token, str) or not isinstance(operation, str) or not isinstance(parameters, dict):
            raise AccessDenied()
        if not isinstance(request_id, str) or not _REQUEST_ID.fullmatch(request_id):
            raise AccessDenied("A connected-account request needs a unique request ID.", 400)
        if len(json.dumps(parameters)) > 4096:
            raise AccessDenied("The connected-account request is too large.", 400)
        permission = PERMISSIONS.get(operation)
        with self._lock:
            grant = self._grants.get(token)
        if grant is None or permission is None:
            raise AccessDenied()
        with grant.lock:
            permissions = self._check(token, grant, permission)
            fingerprint = json.dumps([operation, parameters], sort_keys=True, separators=(",", ":"))
            previous = grant.replies.get(request_id)
            if previous is not None:
                if previous[0] != fingerprint:
                    raise AccessDenied("That request ID was already used for another operation.", 409)
                if previous[1] is None:
                    raise AccessDenied("That operation was already attempted. Check its outcome in Clips Kitty.", 409)
                return copy.deepcopy(previous[1])
            if len(grant.replies) >= 100:
                raise AccessDenied("This run has reached its connected-account request limit.", 429)
            maximum = {"create_clip": 10, "create_vod_clip": 10, "send_chat": 20}.get(operation, 100)
            if operation in {"create_clip", "create_vod_clip"} and sum(
                grant.counts.get(kind, 0) for kind in ("create_clip", "create_vod_clip")
            ) >= 10:
                raise AccessDenied("This run has reached its native clip creation limit.", 429)
            if grant.counts.get(operation, 0) >= maximum:
                raise AccessDenied("This run has reached its limit for this operation.", 429)
            args = self._arguments(grant, operation, parameters, permissions)
            # Mark before I/O. An ambiguous timeout must not turn a retry into
            # a duplicate native clip or chat message.
            grant.replies[request_id] = (fingerprint, None)
            grant.counts[operation] = grant.counts.get(operation, 0) + 1
            try:
                if operation == "source_info":
                    result = {k: copy.deepcopy(grant.binding[k]) for k in ("video_id", "duration", "source_id")
                              if k in grant.binding}
                elif operation == "audience_signals":
                    if self.audience_for_binding is None:
                        raise AccessDenied("Viewer nomination storage is unavailable.", 503)
                    result = self.audience_for_binding(copy.deepcopy(grant.binding))
                elif operation == "events_start":
                    if self.event_manager is None:
                        raise AccessDenied("Twitch event support is not running.", 503)
                    result = self.event_manager.start(grant.binding["account_id"], **args)
                elif operation == "events":
                    if self.event_manager is None:
                        raise AccessDenied("Twitch event support is not running.", 503)
                    result = self.event_manager.events(grant.binding["account_id"], **args)
                    if "twitch.chat.read" not in permissions and isinstance(result, dict):
                        result = {**result, "events": [e for e in result.get("events", [])
                                                      if e.get("type") != "channel.chat.message"]}
                else:
                    result = self.service.perform(grant.binding["account_id"], operation, **args)
                result = _public(result)
                grant.replies[request_id] = (fingerprint, copy.deepcopy(result))
                return result
            except AccessDenied:
                raise
            except Exception:
                # No provider body, URL, token, filesystem path or exception
                # detail is reflected into a plugin's output/log.
                raise AccessDenied("Clips Kitty could not complete the connected-account operation.", 502) from None

    def _arguments(self, grant, operation, params, permissions):
        broadcaster = str(grant.binding["broadcaster_id"])
        allowed = {"stream_context": set(), "videos": {"first"}, "create_clip": {"has_delay"},
                   "audience_signals": set(),
                   "source_info": set(),
                   "create_vod_clip": {"start_seconds", "end_seconds", "title"},
                   "get_clip": {"clip_id"},
                   "vod_link": {"seconds"}, "send_chat": {"message"}, "events_start": {"event_types"},
                   "events": {"after", "limit"}}[operation]
        if set(params) - allowed:
            raise AccessDenied("This account operation received unsupported parameters.", 400)
        args = {"broadcaster_id": broadcaster}
        if operation == "videos":
            first = params.get("first", 20)
            if type(first) is not int or not 1 <= first <= 100:
                raise AccessDenied("The video limit must be between 1 and 100.", 400)
            args["first"] = first
        elif operation == "create_clip":
            if type(params.get("has_delay", False)) is not bool:
                raise AccessDenied("The clip delay must be true or false.", 400)
            args["has_delay"] = params.get("has_delay", False)
        elif operation == "vod_link":
            video_id = str(grant.binding.get("video_id", ""))
            seconds = params.get("seconds", 0)
            duration = grant.binding.get("duration")
            if (not re.fullmatch(r"[0-9]+", video_id) or type(seconds) not in (int, float)
                    or not math.isfinite(seconds) or seconds < 0
                    or (isinstance(duration, (int, float)) and duration > 0 and seconds > duration)):
                raise AccessDenied("The timestamp must belong to this run's Twitch VOD.", 400)
            args = {"video_id": video_id, "seconds": self._vod_time(grant, seconds)}
        elif operation == "create_vod_clip":
            start, end = params.get("start_seconds"), params.get("end_seconds")
            title = params.get("title")
            duration = grant.binding.get("duration")
            if (type(start) not in (int, float) or type(end) not in (int, float)
                    or not math.isfinite(start) or not math.isfinite(end) or not 0 <= start < end
                    or (isinstance(duration, (int, float)) and duration > 0 and end > duration)
                    or not isinstance(title, str) or not title.strip() or len(title) > 140):
                raise AccessDenied("A valid bounded source highlight and title are required.", 400)
            args.update(video_id=str(grant.binding.get("video_id", "")),
                        start_seconds=self._vod_time(grant, start), end_seconds=self._vod_time(grant, end),
                        title=title)
        elif operation == "get_clip":
            clip_id = params.get("clip_id")
            if not isinstance(clip_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,200}", clip_id):
                raise AccessDenied("A valid Twitch clip identifier is required.", 400)
            args.update(clip_id=clip_id, video_id=str(grant.binding.get("video_id", "")))
        elif operation == "send_chat":
            message = params.get("message")
            if not isinstance(message, str) or not message.strip() or len(message) > 500:
                raise AccessDenied("A chat message must contain 1 to 500 characters.", 400)
            args["message"] = message
        elif operation == "events_start":
            events = params.get("event_types", ["stream.online", "stream.offline"])
            if (not isinstance(events, list) or not events or len(events) > 4
                    or any(not isinstance(e, str) or e not in EVENT_TYPES for e in events)):
                raise AccessDenied("Unsupported Twitch event types.", 400)
            if "channel.chat.message" in events and "twitch.chat.read" not in permissions:
                raise AccessDenied()
            args["event_types"] = list(dict.fromkeys(events))
        elif operation == "events":
            after, limit = params.get("after", 0), params.get("limit", 100)
            if type(after) is not int or after < 0 or type(limit) is not int or not 1 <= limit <= 100:
                raise AccessDenied("The event cursor or limit is invalid.", 400)
            args.update(after=after, limit=limit)
        return args

    def _vod_time(self, grant, seconds):
        if self.timeline_mapper is None:
            raise AccessDenied("Verify the source video's Twitch timeline before creating a link or VOD clip.", 409)
        try:
            result = self.timeline_mapper(copy.deepcopy(grant.binding), seconds)
        except Exception:
            raise AccessDenied("The source video's Twitch timing has not been verified.", 409) from None
        if type(result) not in (int, float) or not math.isfinite(result) or result < 0:
            raise AccessDenied("The source video's Twitch timing has not been verified.", 409)
        return result

    def revoke_account(self, account_id):
        """Disconnect/reconfiguration invalidates every active grant for this account."""
        with self._lock:
            self._grants = {token: grant for token, grant in self._grants.items()
                            if grant.binding["account_id"] != account_id}

    def close(self):
        with self._lock:
            self._grants.clear()
        with _GATEWAYS_LOCK:
            if _GATEWAYS.get(_key(self.data_dir)) is self:
                _GATEWAYS.pop(_key(self.data_dir), None)


@contextmanager
def run_environment(data_dir, plugin, video, timeout):
    with _GATEWAYS_LOCK:
        gateway = _GATEWAYS.get(_key(data_dir))
    if gateway is None:
        yield {}
    else:
        with gateway.run(plugin, video, timeout) as values:
            yield values


def install(app, *, data_dir, service, binding_for_video, base_url="http://127.0.0.1:8765", event_manager=None,
            audience_for_binding=None, timeline_mapper=None):
    from fastapi import Header, HTTPException
    from pydantic import BaseModel, ConfigDict, Field

    class OperationIn(BaseModel):
        model_config = ConfigDict(extra="forbid")
        operation: str = Field(max_length=40)
        parameters: dict = Field(default_factory=dict)
        request_id: str = Field(min_length=32, max_length=32)

    gateway = Gateway(data_dir, service, binding_for_video, base_url=base_url, event_manager=event_manager,
                      audience_for_binding=audience_for_binding, timeline_mapper=timeline_mapper)
    with _GATEWAYS_LOCK:
        _GATEWAYS[_key(data_dir)] = gateway

    # Local annotations must be concrete: FastAPI cannot resolve a nested
    # model through a string annotation when postponed annotations are used.
    def perform(body, x_clips_kitty_capability: str | None = Header(default=None)):
        try:
            return {"result": gateway.perform(x_clips_kitty_capability, body.operation,
                                               body.parameters, body.request_id)}
        except AccessDenied as e:
            raise HTTPException(e.status, str(e)) from None

    perform.__annotations__["body"] = OperationIn
    app.post(ROUTE)(perform)
    return gateway
