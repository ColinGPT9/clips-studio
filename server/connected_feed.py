"""Use a connected streamer's Twitch metadata in the existing watch workflow.

Connections do not change download rights or confer authority over other
channels. Unconnected channels and the other platforms retain their existing
feed, while readiness still checks that a broadcast has actually finished.
"""

from __future__ import annotations

import hashlib
import re
import subprocess
from datetime import datetime
from pathlib import Path
from urllib.parse import urlsplit

from sources import channel_feed

_LOGIN = re.compile(r"^[a-zA-Z0-9_]{1,25}$")
_VOD = re.compile(r"^tw_([0-9]+)$")
# A local file whose length is within this of Twitch's own figure for the
# VOD is the whole archive, so its time 0 is the VOD's time 0.
_WHOLE_VOD_TOLERANCE = 2.0


def _file_seconds(path) -> float | None:
    """The media's real length. Twitch's metadata length says nothing about
    whether this file is all of it."""
    try:
        from core.binaries import ffprobe

        out = subprocess.run(
            [ffprobe(), "-v", "error", "-show_entries", "format=duration", "-of", "default=nw=1:nk=1",
             str(Path(path))],
            capture_output=True, text=True, timeout=30,
        ).stdout.strip()
        seconds = float(out)
    except (OSError, ValueError, TypeError, subprocess.SubprocessError):
        return None
    return seconds if seconds > 0 else None


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

    def __init__(self, service, *, fallback=channel_feed, file_seconds=_file_seconds):
        self.service = service
        self.fallback = fallback
        self.file_seconds = file_seconds
        self._noticed: set[str] = set()

    def _accounts(self) -> list[dict]:
        return [a for a in self.service.accounts("twitch")
                if a.get("provider") == "twitch" and a.get("user_id")]

    def _for_login(self, login: str | None) -> dict | None:
        if not login:
            return None
        matches = [a for a in self._accounts() if str(a.get("login", "")).lower() == login]
        return next((a for a in matches if a.get("status") == "connected"), next(iter(matches), None))

    def _public(self, account: dict, platform: str, channel_key: str, **kwargs):
        """The connection lapsed or failed: keep watching through the public
        listing, as before Twitch was connected. The ids are the same
        tw_<id>, so nothing is found twice. Said once until Twitch answers."""
        if account["id"] not in self._noticed:
            self._noticed.add(account["id"])
            if account.get("status") == "connected":
                print("Couldn't read the connected Twitch channel. The watch reads its public video "
                      "list until Twitch answers again.")
            else:
                print("Twitch needs reconnecting in Settings. Until then the watch reads the channel's "
                      "public video list.")
        return self.fallback.latest(platform, channel_key, **kwargs)

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
        if account.get("status") != "connected":
            return self._public(account, platform, channel_key, **kwargs)
        try:
            result = self.service.perform(
                account["id"], "videos", broadcaster_id=account["user_id"], first=channel_feed.LISTING_SIZE,
            )
        except Exception:
            # Account/provider errors never enter the watch's log verbatim,
            # and a lapsed or failing connection never stops the watch.
            return self._public(account, platform, channel_key, **kwargs)
        rows = result.get("videos") if isinstance(result, dict) else None
        if not isinstance(rows, list):
            return self._public(account, platform, channel_key, **kwargs)
        self._noticed.discard(account["id"])
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
                path = getattr(video, "path", None)
                identity = source_identity(path)
                if identity:
                    binding["source_id"] = identity
                # The watch downloads whole archives. When the file's real
                # length matches Twitch's, source time is VOD time, so links
                # and VOD clips need no separate calibration.
                vod_seconds = link.get("duration_seconds")
                file_seconds = self.file_seconds(path) if identity and path is not None else None
                if (isinstance(vod_seconds, (int, float)) and not isinstance(vod_seconds, bool) and vod_seconds > 0
                        and file_seconds is not None
                        and abs(file_seconds - vod_seconds) <= _WHOLE_VOD_TOLERANCE):
                    binding["whole_vod"] = True
                    binding["vod_duration"] = float(vod_seconds)
                return binding
        return None
