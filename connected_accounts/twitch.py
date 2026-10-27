"""Twitch's public desktop OAuth provider and trusted, bounded Helix actions.

Twitch's device grant is deliberately used instead of embedding a client secret
in the desktop app. The user registers a *public* Twitch application and enters
only its client ID. Tokens never belong in a plugin job, API response or error.
This provider does not claim that watching a channel grants management rights.
"""

from __future__ import annotations

import math
import re
from typing import Any, ClassVar
from urllib.parse import urlsplit

import requests

from connected_accounts.models import AccountError

_AUTH = "https://id.twitch.tv/oauth2"
_HELIX = "https://api.twitch.tv/helix"
_ID = re.compile(r"[0-9]{1,30}\Z")
_CLIENT_ID = re.compile(r"[A-Za-z0-9_-]{1,128}\Z")
_SCOPES = frozenset(
    {"clips:edit", "channel:manage:clips", "editor:manage:clips", "user:read:chat", "user:write:chat"}
)
_CLIP_ID = re.compile(r"[A-Za-z0-9_-]{1,200}\Z")
_DURATION = re.compile(r"(?:(\d+)h)?(?:(\d+)m)?(?:(\d+)s)?\Z")
_EVENTS = {
    "stream.online": ("1", "broadcaster_user_id"),
    "stream.offline": ("1", "broadcaster_user_id"),
    "channel.update": ("2", "broadcaster_user_id"),
    "channel.chat.message": ("1", "broadcaster_user_id"),
}


def _text(value: Any, *, maximum: int = 4096) -> str:
    return value[:maximum] if isinstance(value, str) else ""


def _numeric_id(value: Any, name: str) -> str:
    if not isinstance(value, str) or not _ID.fullmatch(value):
        raise AccountError("invalid_request", f"A valid Twitch {name} is required.")
    return value


def _scopes(value: Any) -> set[str]:
    if isinstance(value, str):
        return set(value.split())
    if isinstance(value, (list, tuple, set)) and all(isinstance(item, str) for item in value):
        return set(value)
    return set()


def _positive_int(value: Any, *, default: int | None = None, maximum: int = 86400) -> int:
    if isinstance(value, bool):
        value = None
    try:
        result = int(value)
    except (ValueError, TypeError, OverflowError):
        result = default
    if result is None or not 0 < result <= maximum:
        raise AccountError("provider_response", "Twitch returned an invalid response.", status=502)
    return result


def _verification_url(value: Any) -> str:
    if not isinstance(value, str):
        raise AccountError("provider_response", "Twitch did not provide a verification page.", status=502)
    try:
        parsed = urlsplit(value)
        valid_port = parsed.port in (None, 443)
    except ValueError:
        raise AccountError(
            "provider_response", "Twitch returned an unexpected verification page.", status=502
        ) from None
    if not (
        parsed.scheme == "https"
        and parsed.hostname in {"twitch.tv", "www.twitch.tv"}
        and parsed.path.rstrip("/") == "/activate"
        and not parsed.username
        and not parsed.password
        and valid_port
        and not parsed.fragment
    ):
        raise AccountError(
            "provider_response", "Twitch returned an unexpected verification page.", status=502
        )
    return value


def _duration_seconds(value: Any) -> int | None:
    """Parse the bounded h/m/s duration returned by Helix; never guess zero."""
    if not isinstance(value, str) or len(value) > 100:
        return None
    match = _DURATION.fullmatch(value)
    if not match or not any(part is not None for part in match.groups()):
        return None
    hours, minutes, seconds = (int(part or 0) for part in match.groups())
    total = hours * 3600 + minutes * 60 + seconds
    return total if minutes < 60 and seconds < 60 and total <= 31536000 else None


def _clip_id(value: Any) -> str:
    if not isinstance(value, str) or not _CLIP_ID.fullmatch(value):
        raise AccountError("invalid_request", "A valid Twitch clip ID is required.")
    return value


def _clip_url(value: Any, clip_id: str) -> str:
    """Accept only an actual public URL returned by Helix, stripping tracking."""
    try:
        parsed = urlsplit(value if isinstance(value, str) else "")
        port = parsed.port
    except ValueError:
        parsed, port = None, None
    valid = (
        parsed is not None
        and parsed.scheme == "https"
        and parsed.hostname in {"clips.twitch.tv", "www.twitch.tv", "twitch.tv"}
        and not parsed.username
        and not parsed.password
        and port in (None, 443)
    )
    if valid:
        valid = (
            parsed.path == f"/{clip_id}"
            if parsed.hostname == "clips.twitch.tv"
            else re.fullmatch(rf"/[A-Za-z0-9_]{{1,25}}/clip/{re.escape(clip_id)}", parsed.path)
        )
    if not valid:
        raise AccountError("provider_response", "Twitch did not return a public clip URL.", status=502)
    return f"https://{parsed.hostname}{parsed.path}"


class TwitchProvider:
    """One public Twitch client registration; account tokens stay in the host."""

    id = "twitch"
    # Nothing beyond reading the channel unless the person ticks a permission:
    # the app itself never makes a live clip (plugins work on finished VODs).
    default_scopes: tuple[str, ...] = ()
    supported_scopes = _SCOPES
    capabilities: ClassVar[dict[str, bool]] = {
        "device_code": True,
        "authorization_code": False,
        "pkce": False,
    }
    operation_scopes: ClassVar[dict[str, tuple[str, ...]]] = {
        "stream_context": (),
        "videos": (),
        "create_clip": ("clips:edit",),
        # These grants are alternatives, checked explicitly in perform().
        "create_vod_clip": (),
        "get_clip": (),
        "vod_link": (),
        "send_chat": ("user:write:chat",),
        "subscribe_events": (),  # Chat subscriptions additionally require user:read:chat.
        "unsubscribe_events": (),
    }

    def __init__(self, client_id: str, *, session=None):
        if not isinstance(client_id, str) or (client_id and not _CLIENT_ID.fullmatch(client_id)):
            raise AccountError("invalid_client", "Enter the client ID of a public Twitch application.")
        self.client_id = client_id
        self._session = session or requests.Session()
        self._closed = False

    def close(self) -> None:
        """Release transport resources when the host replaces or stops us."""
        if self._closed:
            return
        self._closed = True
        close = getattr(self._session, "close", None)
        if callable(close):
            try:
                close()
            except Exception:
                # Cleanup is best effort and must not expose credential-bearing
                # transport errors while disconnecting or shutting down.
                pass

    def _request(
        self, method: str, url: str, *, access_token: str | None = None, validation: bool = False, **kwargs
    ) -> dict:
        if self._closed:
            raise AccountError(
                "provider_unavailable", "The Twitch connection service has stopped.", status=503
            )
        if not self.client_id:
            raise AccountError(
                "provider_not_configured",
                "Configure a public Twitch client ID before connecting.",
                status=409,
            )
        headers = {"Accept": "application/json"}
        if access_token:
            headers["Authorization"] = f"{'OAuth' if validation else 'Bearer'} {access_token}"
        if url.startswith(_HELIX + "/"):
            headers["Client-Id"] = self.client_id
        try:
            response = self._session.request(
                method, url, headers=headers, timeout=(5, 20), allow_redirects=False, **kwargs
            )
        except requests.RequestException:
            raise AccountError(
                "provider_unavailable", "Twitch could not be reached. Try again.", status=503
            ) from None
        try:
            data = response.json() if response.status_code != 204 else {}
        except (ValueError, TypeError):
            data = {}
        if not isinstance(data, dict):
            data = {}
        if response.status_code == 401:
            raise AccountError("invalid_token", "Twitch authorization expired. Reconnect Twitch.", status=401)
        if response.status_code == 429:
            if data.get("error") == "slow_down" or data.get("message") == "slow_down":
                raise AccountError("slow_down", "Wait before checking Twitch authorization again.")
            raise AccountError(
                "rate_limited", "Twitch is receiving too many requests. Try again later.", status=429
            )
        if not 200 <= response.status_code < 300:
            # Providers sometimes put an OAuth code in `message`, sometimes
            # in `error`. Never relay the response body: it may echo a token.
            code = data.get("error") or data.get("message")
            known = {
                "authorization_pending": ("authorization_pending", "Approve the connection on Twitch."),
                "slow_down": ("slow_down", "Wait before checking Twitch authorization again."),
                "access_denied": ("access_denied", "Twitch authorization was declined."),
                "user_denied": ("access_denied", "Twitch authorization was declined."),
                "expired_token": ("expired_token", "The Twitch connection request expired. Start again."),
                "invalid_device_code": (
                    "expired_token",
                    "The Twitch connection request expired. Start again.",
                ),
                "invalid_grant": (
                    "invalid_grant",
                    "Twitch authorization is no longer valid. Reconnect Twitch.",
                ),
                "Invalid refresh token": (
                    "invalid_grant",
                    "Twitch authorization is no longer valid. Reconnect Twitch.",
                ),
                "invalid_client": (
                    "invalid_client",
                    "Check the client ID and public client setting in Twitch.",
                ),
                "invalid client": (
                    "invalid_client",
                    "Check the client ID and public client setting in Twitch.",
                ),
            }
            mapped = known.get(code) if isinstance(code, str) else None
            if mapped:
                error, message = mapped
                raise AccountError(error, message, status=401 if error == "invalid_grant" else 400)
            if response.status_code == 403:
                raise AccountError(
                    "permission_denied",
                    "Twitch did not permit this action for this account or channel.",
                    status=403,
                )
            if response.status_code == 404:
                raise AccountError(
                    "not_found", "The requested Twitch stream, video, or clip is unavailable.", status=404
                )
            if response.status_code >= 500:
                raise AccountError(
                    "provider_unavailable", "Twitch is temporarily unavailable. Try again.", status=503
                )
            raise AccountError(
                "provider_error",
                "Twitch did not accept the request. Check the account permissions.",
                status=502,
            )
        return data

    def begin(self, scopes) -> dict:
        requested = _scopes(scopes)
        if requested - _SCOPES:
            raise AccountError("invalid_scope", "This Twitch permission is not supported by Clips Kitty.")
        data = self._request(
            "POST",
            _AUTH + "/device",
            data={
                "client_id": self.client_id,
                "scopes": " ".join(sorted(requested)),
            },
        )
        device = _text(data.get("device_code"), maximum=8192)
        user = _text(data.get("user_code"), maximum=256)
        if not device or not user:
            raise AccountError("provider_response", "Twitch did not provide a connection code.", status=502)
        result = {
            "device_code": device,
            "user_code": user,
            "verification_uri": _verification_url(data.get("verification_uri")),
            "expires_in": _positive_int(data.get("expires_in"), maximum=3600),
            "interval": _positive_int(data.get("interval"), default=5, maximum=60),
        }
        if data.get("verification_uri_complete"):
            result["verification_uri_complete"] = _verification_url(data["verification_uri_complete"])
        return result

    @staticmethod
    def _token(data: dict) -> dict:
        access = data.get("access_token")
        refresh = data.get("refresh_token")
        if not isinstance(access, str) or not access or len(access) > 8192:
            raise AccountError("provider_response", "Twitch did not return an access token.", status=502)
        if refresh is not None and (not isinstance(refresh, str) or len(refresh) > 8192):
            raise AccountError(
                "provider_response", "Twitch returned an invalid authorization response.", status=502
            )
        if str(data.get("token_type", "bearer")).lower() != "bearer":
            raise AccountError("provider_response", "Twitch returned an unsupported token type.", status=502)
        token = {
            "access_token": access,
            "expires_in": _positive_int(data.get("expires_in")),
            "scope": sorted(_scopes(data.get("scope", []))),
            "token_type": "bearer",
        }
        if refresh:
            token["refresh_token"] = refresh
        return token

    def poll(self, device_code: str) -> dict:
        if not isinstance(device_code, str) or not device_code or len(device_code) > 8192:
            raise AccountError("invalid_request", "The Twitch connection request is invalid.")
        return self._token(
            self._request(
                "POST",
                _AUTH + "/token",
                data={
                    "client_id": self.client_id,
                    "device_code": device_code,
                    "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
                },
            )
        )

    def refresh(self, refresh_token: str) -> dict:
        if not isinstance(refresh_token, str) or not refresh_token:
            raise AccountError("invalid_grant", "Reconnect Twitch to renew the authorization.", status=401)
        return self._token(
            self._request(
                "POST",
                _AUTH + "/token",
                data={
                    "client_id": self.client_id,
                    "refresh_token": refresh_token,
                    "grant_type": "refresh_token",
                },
            )
        )

    def validate(self, access_token: str) -> dict:
        data = self._request("GET", _AUTH + "/validate", access_token=access_token, validation=True)
        if data.get("client_id") != self.client_id:
            raise AccountError(
                "invalid_token",
                "Twitch authorization belongs to another application. Reconnect Twitch.",
                status=401,
            )
        user_id = _numeric_id(data.get("user_id"), "account ID")
        return {
            "client_id": self.client_id,
            "user_id": user_id,
            "login": _text(data.get("login"), maximum=100),
            "scopes": sorted(_scopes(data.get("scopes"))),
            "expires_in": _positive_int(data.get("expires_in")),
        }

    def identity(self, access_token: str) -> dict:
        data = self._request("GET", _HELIX + "/users", access_token=access_token)
        users = data.get("data")
        if not isinstance(users, list) or len(users) != 1 or not isinstance(users[0], dict):
            raise AccountError(
                "provider_response", "Twitch did not identify the connected account.", status=502
            )
        user = users[0]
        return {
            "id": _numeric_id(user.get("id"), "account ID"),
            "login": _text(user.get("login"), maximum=100),
            "display_name": _text(user.get("display_name"), maximum=100),
        }

    def revoke(self, access_token: str, *, client_id: str | None = None) -> None:
        """Revoke a token. Twitch only accepts the client ID that issued it,
        which differs from ours after the Client ID setting changed."""
        issuer = self.client_id if client_id is None else client_id
        if not isinstance(issuer, str) or not _CLIENT_ID.fullmatch(issuer):
            raise AccountError("invalid_client", "Enter the client ID of a public Twitch application.")
        self._request("POST", _AUTH + "/revoke", data={"client_id": issuer, "token": access_token})

    def _helix(self, method: str, path: str, access_token: str, **kwargs) -> dict:
        return self._request(method, _HELIX + path, access_token=access_token, **kwargs)

    @staticmethod
    def _rows(data: dict) -> list[dict]:
        rows = data.get("data")
        if not isinstance(rows, list) or not all(isinstance(row, dict) for row in rows):
            raise AccountError("provider_response", "Twitch returned an invalid response.", status=502)
        return rows

    @staticmethod
    def _video(row: dict) -> dict:
        video_id = _numeric_id(row.get("id"), "video ID")
        return {
            "id": video_id,
            "user_id": row.get("user_id")
            if isinstance(row.get("user_id"), str) and _ID.fullmatch(row["user_id"])
            else "",
            "stream_id": row.get("stream_id")
            if row.get("type") == "archive"
            and isinstance(row.get("stream_id"), str)
            and _ID.fullmatch(row["stream_id"])
            else None,
            "type": _text(row.get("type"), maximum=30),
            "title": _text(row.get("title")),
            "url": f"https://www.twitch.tv/videos/{video_id}",
            "created_at": _text(row.get("created_at"), maximum=100),
            "published_at": _text(row.get("published_at"), maximum=100),
            "duration": _text(row.get("duration"), maximum=100),
            "duration_seconds": _duration_seconds(row.get("duration")),
            "view_count": row.get("view_count", 0) if isinstance(row.get("view_count", 0), int) else 0,
        }

    def _owned_video(self, access_token: str, broadcaster: str, video_id: str) -> dict:
        rows = self._rows(self._helix("GET", "/videos", access_token, params={"id": video_id}))
        if not rows:
            raise AccountError("not_found", "This Twitch VOD is no longer available.", status=404)
        if len(rows) != 1 or rows[0].get("id") != video_id:
            raise AccountError("provider_response", "Twitch returned an unexpected VOD.", status=502)
        if rows[0].get("user_id") != broadcaster:
            raise AccountError(
                "permission_denied", "The Twitch VOD does not belong to the selected channel.", status=403
            )
        return self._video(rows[0])

    def perform(self, access_token: str, account: dict, operation: str, **params) -> dict:
        """Called by the account host after its plugin/channel permission gate.

        Subject- and scope-sensitive requirements are checked again here. The
        host must bind broadcaster IDs to an approved watch or plugin job.
        No operation accepts a URL, authorization header, or arbitrary API path.
        """
        if operation not in self.operation_scopes:
            raise AccountError("unsupported_operation", "This Twitch action is not supported.")
        permitted_params = {
            "stream_context": {"broadcaster_id", "broadcaster_login", "first"},
            "videos": {"broadcaster_id", "first"},
            "create_clip": {"broadcaster_id", "has_delay"},
            "create_vod_clip": {"broadcaster_id", "video_id", "start_seconds", "end_seconds", "title"},
            "get_clip": {"broadcaster_id", "clip_id", "video_id"},
            "vod_link": {"broadcaster_id", "video_id", "seconds"},
            "send_chat": {"broadcaster_id", "message"},
            "subscribe_events": {"broadcaster_id", "event_types", "session_id"},
            "unsubscribe_events": {"broadcaster_id", "subscription_id"},
        }
        if set(params) - permitted_params[operation]:
            raise AccountError("invalid_request", "This Twitch action received an unsupported parameter.")
        scopes = _scopes(account.get("scopes"))
        required = set(self.operation_scopes[operation])
        if not required <= scopes:
            raise AccountError(
                "missing_scope", "Reconnect Twitch and allow the permission for this action.", status=403
            )
        subject = _numeric_id(account.get("user_id"), "account ID")
        broadcaster = _numeric_id(params.get("broadcaster_id", subject), "channel ID")
        resolved_user = None
        if "broadcaster_login" in params:
            login = params["broadcaster_login"]
            if ("broadcaster_id" in params or not isinstance(login, str)
                    or not re.fullmatch(r"[A-Za-z0-9_]{1,25}", login)):
                raise AccountError("invalid_request", "Choose a Twitch channel name.")
            users = self._rows(self._helix("GET", "/users", access_token, params={"login": login.lower()}))
            if not users:
                raise AccountError("not_found", "The Twitch channel was not found.", status=404)
            if len(users) != 1 or str(users[0].get("login", "")).lower() != login.lower():
                raise AccountError("provider_response", "Twitch returned an unexpected channel.", status=502)
            resolved_user = users[0]
            broadcaster = _numeric_id(resolved_user.get("id"), "channel ID")
        if operation in {"create_vod_clip", "get_clip"} and broadcaster != subject:
            raise AccountError(
                "permission_denied",
                "VOD clip actions are limited to the connected streamer's channel.",
                status=403,
            )
        if operation == "create_vod_clip" and not scopes & {"channel:manage:clips", "editor:manage:clips"}:
            raise AccountError(
                "missing_scope", "Reconnect Twitch and allow managing clips for this action.", status=403
            )
        if operation in {"stream_context", "videos"}:
            first = params.get("first", 20)
            if not isinstance(first, int) or isinstance(first, bool) or not 1 <= first <= 100:
                raise AccountError("invalid_request", "Choose between 1 and 100 Twitch videos.")
            videos = self._rows(
                self._helix(
                    "GET",
                    "/videos",
                    access_token,
                    params={"user_id": broadcaster, "type": "archive", "first": first},
                )
            )
            result = {"videos": [self._video(row) for row in videos]}
            if operation == "videos":
                return result
            users = ([resolved_user] if resolved_user is not None else
                     self._rows(self._helix("GET", "/users", access_token, params={"id": broadcaster})))
            streams = self._rows(
                self._helix("GET", "/streams", access_token, params={"user_id": broadcaster})
            )
            if not users:
                raise AccountError("not_found", "The Twitch channel was not found.", status=404)
            user = users[0]
            if user.get("id") != broadcaster:
                raise AccountError("provider_response", "Twitch returned an unexpected channel.", status=502)
            result["broadcaster"] = {
                "id": _numeric_id(user.get("id"), "channel ID"),
                "login": _text(user.get("login"), maximum=100),
                "display_name": _text(user.get("display_name"), maximum=100),
            }
            stream = streams[0] if streams else None
            result["stream"] = (
                {key: _text(stream.get(key)) for key in ("id", "title", "game_id", "game_name", "started_at")}
                if stream
                else None
            )
            if result["stream"] is not None:
                result["stream"]["viewer_count"] = stream.get("viewer_count", 0)
            return result
        if operation == "create_clip":
            has_delay = params.get("has_delay", False)
            if not isinstance(has_delay, bool):
                raise AccountError("invalid_request", "The Twitch clip delay setting must be true or false.")
            rows = self._rows(
                self._helix(
                    "POST",
                    "/clips",
                    access_token,
                    params={"broadcaster_id": broadcaster, "has_delay": str(has_delay).lower()},
                )
            )
            if not rows or not re.fullmatch(r"[A-Za-z0-9_-]{1,200}", str(rows[0].get("id", ""))):
                raise AccountError("provider_response", "Twitch did not return a clip ID.", status=502)
            clip_id = rows[0]["id"]
            # Never expose edit_url, which is not the public share URL.
            return {
                "kind": "native_clip",
                "id": clip_id,
                "status": "processing",
                "url": f"https://clips.twitch.tv/{clip_id}",
            }
        if operation == "create_vod_clip":
            # These are explicitly VOD-relative positions. The host must first
            # validate any local-file-to-VOD mapping; wall-clock dates are not a
            # calibration and are deliberately not accepted by this operation.
            video_id = _numeric_id(params.get("video_id"), "video ID")
            start, end = params.get("start_seconds"), params.get("end_seconds")
            if any(
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not 0 <= value <= 31536000
                or not math.isfinite(value)
                for value in (start, end)
            ):
                raise AccountError("invalid_request", "Provide finite VOD-relative clip timestamps.")
            duration = round(end - start, 1)
            if (
                end != int(end)
                or not 5 <= duration <= 60
                or not math.isclose(end - start, duration, rel_tol=0, abs_tol=1e-8)
            ):
                raise AccountError(
                    "invalid_request",
                    "A VOD clip needs a whole-second end and 5 to 60 seconds at 0.1-second precision.",
                )
            title = params.get("title")
            # A local bounded policy, not a claimed Twitch API title limit.
            if (
                not isinstance(title, str)
                or not title.strip()
                or len(title) > 140
                or any(ord(char) < 32 or ord(char) == 127 for char in title)
            ):
                raise AccountError(
                    "invalid_request", "Use a clip title of 1 to 140 characters without control characters."
                )
            video = self._owned_video(access_token, broadcaster, video_id)
            if video["type"] != "archive" or not video["stream_id"] or video["duration_seconds"] is None:
                raise AccountError(
                    "provider_response",
                    "Twitch did not verify this archive's stream and duration.",
                    status=502,
                )
            if end > video["duration_seconds"]:
                raise AccountError("invalid_request", "The clip extends beyond the available Twitch VOD.")
            rows = self._rows(
                self._helix(
                    "POST",
                    "/videos/clips",
                    access_token,
                    params={
                        "editor_id": subject,
                        "broadcaster_id": broadcaster,
                        "vod_id": video_id,
                        "vod_offset": int(end),
                        "duration": duration,
                        "title": title.strip(),
                    },
                )
            )
            if (
                len(rows) != 1
                or not isinstance(rows[0].get("id"), str)
                or not _CLIP_ID.fullmatch(rows[0]["id"])
            ):
                raise AccountError("provider_response", "Twitch did not return a clip ID.", status=502)
            return {
                "kind": "native_vod_clip",
                "id": rows[0]["id"],
                "status": "processing",
                "video_id": video_id,
                "broadcaster_id": broadcaster,
                "stream_id": video["stream_id"],
                "start_seconds": start,
                "end_seconds": int(end),
                "duration": duration,
            }
        if operation == "get_clip":
            clip_id = _clip_id(params.get("clip_id"))
            expected_video = params.get("video_id")
            if expected_video is not None:
                expected_video = _numeric_id(expected_video, "video ID")
            rows = self._rows(self._helix("GET", "/clips", access_token, params={"id": clip_id}))
            pending = {"kind": "native_vod_clip", "id": clip_id, "status": "pending"}
            if not rows:
                return pending
            if len(rows) != 1 or rows[0].get("id") != clip_id:
                raise AccountError("provider_response", "Twitch returned an unexpected clip.", status=502)
            row = rows[0]
            if row.get("broadcaster_id") != broadcaster:
                raise AccountError(
                    "permission_denied", "The clip does not belong to the connected channel.", status=403
                )
            video_id = row.get("video_id")
            if video_id in (None, ""):
                if expected_video is not None:
                    return pending
                video = None
                video_id = ""
            else:
                video_id = _numeric_id(video_id, "video ID")
                if expected_video is not None and video_id != expected_video:
                    raise AccountError(
                        "permission_denied",
                        "The clip does not belong to the expected Twitch VOD.",
                        status=403,
                    )
                video = self._owned_video(access_token, broadcaster, video_id)
                if expected_video is not None and (video["type"] != "archive" or not video["stream_id"]):
                    raise AccountError(
                        "provider_response", "Twitch did not verify the clip's source stream.", status=502
                    )
            offset = row.get("vod_offset")
            if offset is not None and (
                isinstance(offset, bool) or not isinstance(offset, int) or not 0 <= offset <= 31536000
            ):
                raise AccountError("provider_response", "Twitch returned an invalid clip offset.", status=502)
            duration = row.get("duration")
            if (
                isinstance(duration, bool)
                or not isinstance(duration, (int, float))
                or not 0 < duration <= 31536000
                or not math.isfinite(duration)
            ):
                raise AccountError(
                    "provider_response", "Twitch returned an invalid clip duration.", status=502
                )
            return {
                "kind": "native_vod_clip",
                "id": clip_id,
                "status": "ready",
                "url": _clip_url(row.get("url"), clip_id),
                "broadcaster_id": broadcaster,
                "video_id": video_id,
                "vod_offset": offset,
                "duration": duration,
                "stream_id": video["stream_id"] if video else None,
            }
        if operation == "vod_link":
            video_id = _numeric_id(params.get("video_id"), "video ID")
            seconds = params.get("seconds", 0)
            if (
                isinstance(seconds, bool)
                or not isinstance(seconds, (int, float))
                or not 0 <= seconds <= 31536000
                or not math.isfinite(seconds)
            ):
                raise AccountError(
                    "invalid_request", "The Twitch video timestamp must be a nonnegative number."
                )
            video = self._owned_video(access_token, broadcaster, video_id)
            if video["duration_seconds"] is not None and seconds > video["duration_seconds"]:
                raise AccountError("invalid_request", "The timestamp is outside the available Twitch VOD.")
            seconds = int(seconds)
            hours, rem = divmod(seconds, 3600)
            minutes, secs = divmod(rem, 60)
            return {
                "kind": "timestamp",
                "video_id": video_id,
                "seconds": seconds,
                "broadcaster_id": broadcaster,
                "stream_id": video["stream_id"],
                "duration": video["duration"],
                "duration_seconds": video["duration_seconds"],
                "type": video["type"],
                "url": f"https://www.twitch.tv/videos/{video_id}?t={hours}h{minutes}m{secs}s",
            }
        if operation == "send_chat":
            message = params.get("message")
            if not isinstance(message, str) or not message.strip() or len(message) > 500:
                raise AccountError(
                    "invalid_request", "A Twitch chat message must contain 1 to 500 characters."
                )
            rows = self._rows(
                self._helix(
                    "POST",
                    "/chat/messages",
                    access_token,
                    json={"broadcaster_id": broadcaster, "sender_id": subject, "message": message},
                )
            )
            if not rows:
                raise AccountError(
                    "provider_response", "Twitch did not confirm the chat message.", status=502
                )
            return {
                "message_id": _text(rows[0].get("message_id"), maximum=100),
                "is_sent": rows[0].get("is_sent") is True,
            }
        if operation == "unsubscribe_events":
            subscription_id = params.get("subscription_id")
            if not isinstance(subscription_id, str) or not re.fullmatch(
                r"[A-Za-z0-9-]{1,128}", subscription_id
            ):
                raise AccountError("invalid_request", "A valid Twitch subscription ID is required.")
            self._helix("DELETE", "/eventsub/subscriptions", access_token, params={"id": subscription_id})
            return {"deleted": True}
        session_id = params.get("session_id")
        if not isinstance(session_id, str) or not re.fullmatch(r"[A-Za-z0-9_=-]{1,256}", session_id):
            raise AccountError("invalid_request", "A host-owned Twitch event session is required.")
        event_types = params.get("event_types", ["stream.online", "stream.offline"])
        if (
            not isinstance(event_types, (list, tuple))
            or not event_types
            or len(event_types) > 4
            or any(not isinstance(item, str) or item not in _EVENTS for item in event_types)
        ):
            raise AccountError("invalid_request", "Choose a supported Twitch event type.")
        # A user token may read a selected public channel. The reader remains
        # the authenticated subject in the chat condition below; channel
        # ownership is not required for these allowlisted public event types.
        if "channel.chat.message" in event_types and "user:read:chat" not in scopes:
            raise AccountError(
                "missing_scope", "Reconnect Twitch and allow reading chat messages.", status=403
            )
        subscriptions = []
        for event_type in dict.fromkeys(event_types):
            version, condition_key = _EVENTS[event_type]
            condition = {condition_key: broadcaster}
            if event_type == "channel.chat.message":
                condition["user_id"] = subject
            rows = self._rows(
                self._helix(
                    "POST",
                    "/eventsub/subscriptions",
                    access_token,
                    json={
                        "type": event_type,
                        "version": version,
                        "condition": condition,
                        "transport": {"method": "websocket", "session_id": session_id},
                    },
                )
            )
            if not rows:
                raise AccountError(
                    "provider_response", "Twitch did not confirm the event subscription.", status=502
                )
            subscriptions.append(
                {
                    "id": _text(rows[0].get("id"), maximum=128),
                    "type": event_type,
                    "status": _text(rows[0].get("status"), maximum=100),
                }
            )
        return {"subscriptions": subscriptions}
