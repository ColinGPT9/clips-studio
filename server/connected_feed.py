"""Use a connected streamer's Twitch metadata in the existing watch workflow.

Connections do not change download rights or confer authority over other
channels. Unconnected channels and the other platforms retain their existing
feed, while readiness still checks that a broadcast has actually finished.
"""

from __future__ import annotations

import hashlib
import re
from datetime import datetime
from pathlib import Path
from urllib.parse import urlsplit

from sources import channel_feed

_LOGIN = re.compile(r"^[a-zA-Z0-9_]{1,25}$")
_VOD = re.compile(r"^tw_([0-9]+)$")
_RECONNECT = {
    "auth_required", "reconnect_required", "invalid_token", "invalid_grant",
    "unauthorized", "missing_scope", "scope_required", "scope_missing",
}


def source_identity(path) -> str | None:
    """Conservative local-media identity for explicitly calibrated timelines.

    This samples file contents and metadata, not an adversarial whole-file
    checksum. Moved or replaced source files need calibration again.
    """
    try:
        source = Path(path).resolve(strict=True)
        before = source.stat()
        if not source.is_file() or before.st_size <= 0:
            return None
        digest = hashlib.sha256()
        digest.update(str(source).encode("utf-8"))
        digest.update(f"\0{before.st_size}\0{before.st_mtime_ns}\0".encode())
        with source.open("rb") as stream:
            digest.update(stream.read(65536))
            if before.st_size > 65536:
                stream.seek(max(0, before.st_size - 65536))
                digest.update(stream.read(65536))
        after = source.stat()
        if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
            return None
        return "sha256:" + digest.hexdigest()
    except (OSError, TypeError, ValueError):
        return None


def _login(text: str) -> str | None:
    """Recognise an account name without accepting a lookalike Twitch host."""
    if not isinstance(text, str):
        return None
    text = text.strip()
    if "://" in text:
        try:
            parsed = urlsplit(text)
            if (parsed.scheme != "https" or parsed.hostname not in {"twitch.tv", "www.twitch.tv"}
                    or parsed.username or parsed.password or parsed.port):
                return None
        except ValueError:
            return None
        text = parsed.path.strip("/")
    else:
        text = text.lstrip("@")
    return text.lower() if _LOGIN.fullmatch(text) else None


def _published(text: str) -> float:
    try:
        return datetime.fromisoformat(str(text).replace("Z", "+00:00")).timestamp()
    except (ValueError, TypeError, OverflowError):
        return 0.0


class ConnectedFeed:
    """The channel_feed interface with an authenticated path for own channels.

    ``service`` is the trusted ConnectedAccounts service. Neither this adapter
    nor its consumers see access or refresh tokens.
    """

    def __init__(self, service, *, fallback=channel_feed):
        self.service = service
        self.fallback = fallback

    def _accounts(self) -> list[dict]:
        return [a for a in self.service.accounts("twitch")
                if a.get("provider") == "twitch" and a.get("user_id")]

    def _for_login(self, login: str | None) -> dict | None:
        if not login:
            return None
        matches = [a for a in self._accounts() if str(a.get("login", "")).lower() == login]
        # A stale connection stays on the authorised path so the watch reports
        # that reconnection is needed instead of silently using another route.
        return next((a for a in matches if a.get("status") == "connected"), next(iter(matches), None))

    def resolve(self, platform: str, text: str, **kwargs):
        if platform == "twitch":
            account = self._for_login(_login(text))
            if account:
                return channel_feed.Channel(
                    "twitch", str(account["login"]).lower(),
                    account.get("display_name") or account["login"],
                )
        return self.fallback.resolve(platform, text, **kwargs)

    def latest(self, platform: str, channel_key: str, **kwargs) -> list[channel_feed.NewSourceVideo]:
        account = self._for_login(_login(channel_key)) if platform == "twitch" else None
        if not account:
            return self.fallback.latest(platform, channel_key, **kwargs)
        try:
            result = self.service.perform(
                account["id"], "videos", broadcaster_id=account["user_id"], first=channel_feed.LISTING_SIZE,
            )
        except Exception as exc:
            if getattr(exc, "code", "") in _RECONNECT:
                message = "Reconnect Twitch in Connected Accounts to keep watching this channel."
            else:
                message = "Couldn't read the connected Twitch channel. Test the connection in Settings."
            # Account/provider errors must not enter the watch's log verbatim.
            raise ValueError(message) from None
        rows = result.get("videos") if isinstance(result, dict) else None
        if not isinstance(rows, list):
            raise ValueError("Twitch did not return a valid list of broadcasts.")
        videos = []
        seen = set()
        for row in rows[:channel_feed.LISTING_SIZE]:
            if not isinstance(row, dict):
                continue
            video_id = str(row.get("id", ""))
            if not video_id.isascii() or not video_id.isdecimal() or video_id in seen:
                continue
            if row.get("user_id") is not None and str(row["user_id"]) != str(account["user_id"]):
                continue
            if row.get("type") not in (None, "", "archive"):
                continue
            seen.add(video_id)
            videos.append(channel_feed.NewSourceVideo(
                platform="twitch", channel_key=str(account["login"]).lower(), video_id=f"tw_{video_id}",
                url=f"https://www.twitch.tv/videos/{video_id}", title=str(row.get("title") or ""),
                published_at=_published(row.get("created_at") or row.get("published_at") or ""),
            ))
        return sorted(videos, key=lambda video: video.published_at, reverse=True)

    def readiness(self, url: str, min_seconds: float = 0, **kwargs):
        return self.fallback.readiness(url, min_seconds, **kwargs)

    def binding_for_video(self, video) -> dict | None:
        """Bind a plugin run only to a VOD proven to belong to its connection.

        The video's display-channel string and any plugin-supplied identifiers
        are insufficient evidence. The provider fetches the VOD's real owner
        from Twitch and the host compares it with the connected user.
        """
        match = _VOD.fullmatch(str(getattr(video, "video_id", "")))
        if not match:
            return None
        video_id = match.group(1)
        for account in self._accounts():
            try:
                link = self.service.perform(account["id"], "vod_link", video_id=video_id, seconds=0)
            except Exception:
                continue
            if (isinstance(link, dict) and str(link.get("broadcaster_id", "")) == str(account["user_id"])
                    and str(link.get("video_id", "")) == video_id):
                binding = {"account_id": account["id"], "broadcaster_id": str(account["user_id"]),
                           "video_id": video_id}
                identity = source_identity(getattr(video, "path", None))
                if identity:
                    binding["source_id"] = identity
                return binding
        return None
