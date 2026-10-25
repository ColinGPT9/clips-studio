"""Trusted host bridge: account ownership, verified timelines and source links."""

from __future__ import annotations

import math
import re
import secrets
import threading

from connected_accounts.models import AccountError

from .models import WorkflowError
from .nominations import MAX_OFFSET, NUMBER, Nominations, iso


def _duration(value):
    if (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
        and 0 < value <= MAX_OFFSET
    ):
        return float(value)
    if isinstance(value, str) and re.fullmatch(r"(?:(\d+)h)?(?:(\d+)m)?(?:(\d+)s)?", value) and value:
        parts = re.fullmatch(r"(?:(\d+)h)?(?:(\d+)m)?(?:(\d+)s)?", value).groups()
        result = sum(int(part or 0) * factor for part, factor in zip(parts, (3600, 60, 1)))
        return float(result) if 0 < result <= MAX_OFFSET else None
    return None


def vod_url(video_id, seconds):
    hours, rest = divmod(int(seconds), 3600)
    minutes, secs = divmod(rest, 60)
    return f"https://www.twitch.tv/videos/{video_id}?t={hours}h{minutes}m{secs}s"


class WorkflowRuntime:
    def __init__(self, data_dir, service, event_manager, *, transport=None, clock=None):
        from .digests import DigestService

        self.service = service
        self.event_manager = event_manager
        self._lock = threading.RLock()
        self._monitored = set()
        self._lifecycle_revisions = {}
        self._closed = False
        options = {"clock": clock} if clock is not None else {}
        self.nominations = Nominations(data_dir, **options)
        self.digests = DigestService(
            data_dir, source_resolver=self._resolve_source, transport=transport, **options
        )

    def _account(self, account_id, *, chat=False):
        account = next((row for row in self.service.accounts() if row.get("id") == account_id), None)
        if account is None or account.get("provider") != "twitch":
            raise WorkflowError("account_not_found", "Connect your Twitch account first.", status=404)
        if account.get("status") != "connected":
            raise WorkflowError("reconnect_required", "Reconnect Twitch to use stream workflows.", status=401)
        if not isinstance(account.get("user_id"), str) or not NUMBER.fullmatch(account["user_id"]):
            raise WorkflowError("account_not_found", "Reconnect your Twitch account first.", status=409)
        if chat and "user:read:chat" not in account.get("scopes", []):
            raise WorkflowError("missing_scope", "Reconnect Twitch with permission to read chat.", status=403)
        return account

    def start_monitor(self, account_id):
        account = self._account(account_id, chat=True)
        with self._lock:
            if self._closed:
                raise WorkflowError("service_closed", "Stream workflows are stopping.", status=503)
            if account_id not in self._monitored:
                self.nominations.deactivate(account_id)
            self._monitored.add(account_id)
            revision = self._lifecycle_revisions.get(account_id, 0)
            try:
                state = self.event_manager.start(
                    account_id,
                    account["user_id"],
                    ["stream.online", "stream.offline", "channel.chat.message"],
                )
            except Exception:
                self._monitored.discard(account_id)
                raise
        # Twitch does not replay stream.online for a listener enabled mid-live.
        # Bootstrap only from the host's actual own-channel stream lookup.
        try:
            context = self.service.perform(
                account_id, "stream_context", broadcaster_id=account["user_id"], first=1
            )
            stream = context.get("stream") if isinstance(context, dict) else None
            owner = context.get("broadcaster") if isinstance(context, dict) else None
            with self._lock:
                if (
                    self._lifecycle_revisions.get(account_id, 0) != revision
                    or account_id not in self._monitored
                    or self._closed
                ):
                    return {"monitoring": account_id in self._monitored, **state}
                if (
                    isinstance(stream, dict)
                    and isinstance(owner, dict)
                    and owner.get("id") == account["user_id"]
                ):
                    self.record_event(
                        account_id,
                        {
                            "id": "workflow-bootstrap-" + secrets.token_hex(16),
                            "type": "stream.online",
                            "broadcaster_id": account["user_id"],
                            "timestamp": iso(self.nominations.clock()),
                            "data": {"id": stream.get("id"), "started_at": stream.get("started_at")},
                        },
                    )
        except AccountError:
            # EventSub can still establish the next verified stream lifecycle.
            pass
        return {"monitoring": True, **state}

    def stop_monitor(self, account_id):
        self._account(account_id)
        with self._lock:
            self._monitored.discard(account_id)
            self._lifecycle_revisions[account_id] = self._lifecycle_revisions.get(account_id, 0) + 1
            self.nominations.deactivate(account_id)
        # The shared account listener can also serve plugins. Stop this sink
        # without tearing down another consumer's EventSub subscriptions.
        return {"monitoring": False}

    def account_disconnected(self, account_id):
        """Invalidate monitoring consent even after the roster entry is gone."""
        with self._lock:
            self._monitored.discard(account_id)
            self._lifecycle_revisions[account_id] = self._lifecycle_revisions.get(account_id, 0) + 1
            if not self._closed:
                self.nominations.deactivate(account_id)

    def record_event(self, account_id, event):
        with self._lock:
            if self._closed or account_id not in self._monitored:
                return False
            try:
                account = self._account(account_id, chat=True)
            except (WorkflowError, AccountError):
                self._monitored.discard(account_id)
                self.nominations.deactivate(account_id)
                return False
            if not isinstance(event, dict) or event.get("broadcaster_id") != account["user_id"]:
                return False
            if event.get("type") in {"stream.online", "stream.offline"}:
                self._lifecycle_revisions[account_id] = self._lifecycle_revisions.get(account_id, 0) + 1
            return self.nominations.record_event(account_id, event)

    def streams(self, account_id):
        self._account(account_id)
        with self._lock:
            monitoring = account_id in self._monitored
        return {"monitoring": monitoring, "streams": self.nominations.streams(account_id)}

    def bind_vod(
        self,
        account_id,
        *,
        broadcaster_id,
        vod_id,
        stream_id,
        stream_start,
        offset_seconds,
        verified,
        source_offset_seconds=None,
        source_id=None,
    ):
        account = self._account(account_id)
        if broadcaster_id != account["user_id"]:
            raise WorkflowError(
                "permission_denied", "A stream mapping must belong to your connected channel.", status=403
            )
        if (
            not isinstance(vod_id, str)
            or not NUMBER.fullmatch(vod_id)
            or not isinstance(stream_id, str)
            or not NUMBER.fullmatch(stream_id)
            or verified is not True
        ):
            raise WorkflowError("invalid_alignment", "Supply an explicitly verified stream-to-VOD alignment.")
        source = self.service.perform(
            account_id, "vod_link", broadcaster_id=broadcaster_id, video_id=vod_id, seconds=0
        )
        if (
            not isinstance(source, dict)
            or source.get("video_id") != vod_id
            or source.get("broadcaster_id") != broadcaster_id
            or source.get("stream_id") != stream_id
            or source.get("type") != "archive"
        ):
            raise WorkflowError(
                "unverified_vod",
                "Twitch must confirm this archive belongs to the recorded stream.",
                status=409,
            )
        return self.nominations.bind_vod(
            account_id=account_id,
            broadcaster_id=broadcaster_id,
            vod_id=vod_id,
            stream_id=stream_id,
            stream_start=stream_start,
            offset_seconds=offset_seconds,
            verified=True,
            duration=_duration(source.get("duration_seconds")) or _duration(source.get("duration")),
            source_offset_seconds=source_offset_seconds,
            source_id=source_id,
        )

    def audience_for_binding(self, binding):
        empty = {
            "status": "unavailable",
            "reason": "unverified_binding",
            "source": "viewer_nominations",
            "nominations": [],
        }
        if not isinstance(binding, dict):
            return empty
        try:
            account = self._account(binding.get("account_id"))
        except (WorkflowError, AccountError):
            return {**empty, "reason": "account_unavailable"}
        if account["user_id"] != binding.get("broadcaster_id"):
            return empty
        return self.nominations.audience_for_binding(binding)

    def source_to_vod(self, binding, seconds):
        if not isinstance(binding, dict):
            raise WorkflowError(
                "timeline_unknown",
                "Verify the local source timeline before requesting a Twitch timestamp.",
                status=409,
            )
        account = self._account(binding.get("account_id"))
        vod_id = binding.get("vod_id", binding.get("video_id"))
        mapping = self.nominations.mapping(account["id"], vod_id) if isinstance(vod_id, str) else None
        if (
            mapping is None
            or mapping["broadcaster_id"] != account["user_id"]
            or binding.get("broadcaster_id") != account["user_id"]
            or mapping["source_offset_seconds"] is None
            or mapping["source_id"] is None
            or binding.get("source_id") != mapping["source_id"]
        ):
            raise WorkflowError(
                "timeline_unknown",
                "Verify the local source timeline before requesting a Twitch timestamp.",
                status=409,
            )
        if (
            isinstance(seconds, bool)
            or not isinstance(seconds, (int, float))
            or not math.isfinite(seconds)
            or not 0 <= seconds <= MAX_OFFSET
        ):
            raise WorkflowError("invalid_timestamp", "Choose a valid local source timestamp.")
        source_duration = binding.get("duration")
        if (
            isinstance(source_duration, (int, float))
            and not isinstance(source_duration, bool)
            and math.isfinite(source_duration)
            and source_duration > 0
            and seconds > source_duration
        ):
            raise WorkflowError("invalid_timestamp", "The timestamp must fit within the local source.")
        result = seconds + mapping["source_offset_seconds"]
        if result > MAX_OFFSET or (mapping["duration"] is not None and result > mapping["duration"]):
            raise WorkflowError("invalid_timestamp", "The timestamp must fit within the verified VOD.")
        return result

    def _resolve_source(self, account_id, candidate):
        account = self._account(account_id)
        broadcaster = account["user_id"]
        if "vod_id" in candidate:
            vod_id, seconds = candidate["vod_id"], candidate["offset_seconds"]
            mapping = self.nominations.mapping(account_id, vod_id)
            if mapping is None or mapping["broadcaster_id"] != broadcaster:
                raise WorkflowError(
                    "unverified_vod", "Verify the VOD timeline before preparing its digest.", status=409
                )
            if mapping["duration"] is not None and seconds >= mapping["duration"]:
                raise WorkflowError("invalid_timestamp", "The highlight must fit within the verified VOD.")
            source = self.service.perform(
                account_id, "vod_link", broadcaster_id=broadcaster, video_id=vod_id, seconds=seconds
            )
            if (
                not isinstance(source, dict)
                or source.get("video_id") != vod_id
                or source.get("broadcaster_id") != broadcaster
                or source.get("stream_id") != mapping["stream_id"]
            ):
                raise WorkflowError("unverified_vod", "Twitch could not confirm this mapped VOD.", status=409)
            seconds = int(seconds)
            return {
                "title": candidate.get("title")
                or f"Highlight at {seconds // 3600:02d}:{seconds // 60 % 60:02d}:{seconds % 60:02d}",
                "kind": "vod_timestamp",
                "url": vod_url(vod_id, seconds),
                "video_id": vod_id,
                "seconds": seconds,
            }
        clip_id = candidate["clip_id"]
        source = self.service.perform(account_id, "get_clip", broadcaster_id=broadcaster, clip_id=clip_id)
        if (
            not isinstance(source, dict)
            or source.get("id", source.get("clip_id")) != clip_id
            or source.get("broadcaster_id") != broadcaster
        ):
            raise WorkflowError(
                "unverified_clip", "Twitch must confirm a public clip from your channel.", status=409
            )
        return {
            "title": candidate.get("title") or source.get("title") or "Twitch clip",
            "kind": "native_clip",
            "url": f"https://clips.twitch.tv/{clip_id}",
            "clip_id": clip_id,
        }

    def close(self):
        with self._lock:
            if self._closed:
                return
            self._closed = True
            self._monitored.clear()
        self.nominations.close()
        self.digests.close()
