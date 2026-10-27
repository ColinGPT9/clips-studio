"""Trusted host bridge: account ownership, verified timelines and source links.

The two SQLite stores open on first use, not at engine start: a person who
never uses stream workflows gets no files, and a damaged file is set aside
instead of stopping the engine (and every unrelated feature) from starting.
"""

from __future__ import annotations

import math
import re
import secrets
import sqlite3
import threading
from pathlib import Path

from connected_accounts.models import AccountError
from connected_accounts.store import set_aside

from .models import WorkflowError
from .nominations import MAX_OFFSET, NUMBER, Nominations, iso

# The EventSub consumer name for nomination collection (EventSubManager.release).
MONITOR = "stream-workflow"
# After a (re)subscription, look up the live stream again at these delays
# until Twitch answers; EventSub never replays a missed stream.online.
_BOOTSTRAP_DELAYS = (0, 15, 30, 60, 120)
_NOMINATIONS_FILE = "stream-workflows.sqlite3"
_DIGESTS_FILE = "stream-digests.sqlite3"
_RECORDING_POLL_SECONDS = 10
AUDIENCE_MESSAGES = {
    "unverified_binding": "Viewer nominations need this stream's chat timing matched to its Twitch VOD, "
                          "using Bind recording in Settings > Connected accounts.",
    "source_timeline_unknown": "Viewer nominations need this stream's chat timing matched to its Twitch VOD, "
                               "then a verified whole-VOD download or explicit local-source alignment.",
    "account_unavailable": "Reconnect Twitch in Settings to read viewer nominations.",
    "storage_unavailable": "Clips Kitty couldn't open its viewer nomination storage.",
}


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
        self.service = service
        self.event_manager = event_manager
        self._data_dir = Path(data_dir)
        self._transport = transport
        self._options = {"clock": clock} if clock is not None else {}
        self._lock = threading.RLock()
        self._monitored = set()
        self._channels = {}
        self._channel_logins = {}
        self._lifecycle_revisions = {}
        self._bootstrapping = {}
        self._closed = False
        self._closing = threading.Event()
        self._nominations = None
        self._digests = None
        self._recording_worker = None

    def start(self):
        """Resume persisted discovery when the app starts, never on construction.

        Open/recover the optional store synchronously before starting a worker;
        a route/schema inspection must not launch threads or touch stored data.
        """
        with self._lock:
            if self._closed:
                return
            try:
                store = self._stored_nominations()
                pending = store is not None and store.recordings_pending()
            except WorkflowError:
                return
        if pending:
            self._recordings_later()

    # ---- stores, opened on first use ---------------------------------------

    def _open(self, name, factory):
        path = self._data_dir / name
        try:
            return factory()
        except (sqlite3.Error, ValueError):
            # Damaged: set it aside below and start a fresh one.
            pass
        except OSError:
            raise WorkflowError("workflow_store_unavailable", "Stream workflow storage could not be opened.",
                                status=503) from None
        if not set_aside(path):
            raise WorkflowError("workflow_store_unavailable", "Stream workflow storage could not be opened.",
                                status=503)
        for suffix in ("-wal", "-shm"):
            set_aside(path.with_name(path.name + suffix))
        print(f"A damaged stream workflow file was set aside ({name}.corrupt) and a new one started.")
        try:
            return factory()
        except (sqlite3.Error, ValueError, OSError):
            raise WorkflowError("workflow_store_unavailable", "Stream workflow storage could not be opened.",
                                status=503) from None

    @property
    def nominations(self) -> Nominations:
        with self._lock:
            if self._nominations is None:
                if self._closed:
                    raise WorkflowError("service_closed", "Stream workflows are stopping.", status=503)
                self._nominations = self._open(
                    _NOMINATIONS_FILE, lambda: Nominations(self._data_dir, **self._options))
            return self._nominations

    @property
    def digests(self):
        from .digests import DigestService

        with self._lock:
            if self._digests is None:
                if self._closed:
                    raise WorkflowError("service_closed", "Stream workflows are stopping.", status=503)
                self._digests = self._open(_DIGESTS_FILE, lambda: DigestService(
                    self._data_dir, source_resolver=self._resolve_source, transport=self._transport,
                    **self._options))
            return self._digests

    def _stored_nominations(self):
        """The nominations store if it exists; reads never create it."""
        with self._lock:
            if self._nominations is None and not (self._data_dir / _NOMINATIONS_FILE).exists():
                return None
            return self.nominations

    # ---- accounts and monitoring -------------------------------------------

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

    def start_monitor(self, account_id, broadcaster_id=None):
        account = self._account(account_id, chat=True)
        broadcaster_id = account["user_id"] if broadcaster_id is None else broadcaster_id
        if not isinstance(broadcaster_id, str) or not NUMBER.fullmatch(broadcaster_id):
            raise WorkflowError("invalid_channel", "Choose a valid Twitch channel.")
        with self._lock:
            if self._closed:
                raise WorkflowError("service_closed", "Stream workflows are stopping.", status=503)
            if account_id in self._monitored and self._channels.get(account_id) != broadcaster_id:
                raise WorkflowError("channel_in_use", "Stop collection before choosing another channel.", status=409)
            nominations = self.nominations
            revision = self._lifecycle_revisions.get(account_id, 0)
            state = self.event_manager.start(
                account_id, broadcaster_id,
                ["stream.online", "stream.offline", "channel.chat.message"], consumer=MONITOR,
            )
            if account_id not in self._monitored:
                nominations.deactivate(account_id)
            if self._channels.get(account_id) != broadcaster_id:
                self._channel_logins.pop(account_id, None)
            self._channels[account_id] = broadcaster_id
            self._monitored.add(account_id)
        # Twitch does not replay stream.online for a listener enabled mid-live.
        # Bootstrap only from the host's actual selected-channel lookup, and
        # keep retrying in the background while Twitch cannot answer.
        if self._bootstrap(account_id, revision) is None:
            self._bootstrap_later(account_id)
        with self._lock:
            return {"monitoring": account_id in self._monitored, **state}

    def _bootstrap(self, account_id, revision):
        """Record the live stream if Twitch shows one. None: retry later."""
        try:
            self._account(account_id, chat=True)
            with self._lock:
                broadcaster_id = self._channels.get(account_id)
                if broadcaster_id is None or self._lifecycle_revisions.get(account_id, 0) != revision:
                    return False
            context = self.service.perform(
                account_id, "stream_context", broadcaster_id=broadcaster_id, first=1
            )
        except (AccountError, WorkflowError):
            return None
        stream = context.get("stream") if isinstance(context, dict) else None
        owner = context.get("broadcaster") if isinstance(context, dict) else None
        with self._lock:
            if (
                self._lifecycle_revisions.get(account_id, 0) != revision
                or account_id not in self._monitored
                or self._closed
            ):
                # A real lifecycle event arrived meanwhile, or collection ended.
                return False
            if not (isinstance(owner, dict) and owner.get("id") == broadcaster_id):
                return False
            login = owner.get("login")
            if isinstance(login, str) and re.fullmatch(r"[A-Za-z0-9_]{1,25}", login):
                self._channel_logins[account_id] = login
            if stream is None:
                # Verified offline. A stream that ended while the socket was
                # reconnecting sent no stream.offline either: stop counting it.
                self.nominations.deactivate(account_id)
                if self.nominations.finish_offline(account_id):
                    self._recordings_later()
                return False
            if not isinstance(stream, dict):
                return False
            return self.record_event(
                account_id,
                {
                    "id": "workflow-bootstrap-" + secrets.token_hex(16),
                    "type": "stream.online",
                    "broadcaster_id": broadcaster_id,
                    "timestamp": iso(self.nominations.clock()),
                    "data": {"id": stream.get("id"), "started_at": stream.get("started_at")},
                },
            )

    def _bootstrap_later(self, account_id):
        with self._lock:
            revision = self._lifecycle_revisions.get(account_id, 0)
            if (self._closed or account_id not in self._monitored
                    or self._bootstrapping.get(account_id) == revision):
                return
            self._bootstrapping[account_id] = revision
        threading.Thread(target=self._bootstrap_loop, args=(account_id, revision), daemon=True,
                         name="stream-workflow-bootstrap").start()

    def _bootstrap_loop(self, account_id, revision):
        try:
            for delay in _BOOTSTRAP_DELAYS:
                if self._closing.wait(delay):
                    return
                if self._bootstrap(account_id, revision) is not None:
                    return
        finally:
            with self._lock:
                if self._bootstrapping.get(account_id) == revision:
                    self._bootstrapping.pop(account_id, None)

    def stop_monitor(self, account_id):
        self._account(account_id)
        with self._lock:
            self._monitored.discard(account_id)
            self._lifecycle_revisions[account_id] = self._lifecycle_revisions.get(account_id, 0) + 1
            nominations = self._stored_nominations()
            if nominations is not None:
                nominations.deactivate(account_id)
        # Stop reading chat for collection. The shared listener keeps running
        # only for another consumer (a plugin run), and drops chat unless that
        # consumer asked for it.
        self.event_manager.release(account_id, MONITOR)
        return {"monitoring": False}

    def account_disconnected(self, account_id):
        """Invalidate monitoring consent even after the roster entry is gone."""
        with self._lock:
            self._monitored.discard(account_id)
            self._lifecycle_revisions[account_id] = self._lifecycle_revisions.get(account_id, 0) + 1
            if not self._closed and self._nominations is not None:
                self._nominations.deactivate(account_id)

    def record_event(self, account_id, event):
        if isinstance(event, dict) and event.get("type") == "session.subscribed":
            # A fresh EventSub session: a stream that went live in the gap
            # sends no stream.online, so look it up (off the socket thread).
            self._bootstrap_later(account_id)
            return False
        with self._lock:
            if self._closed or account_id not in self._monitored:
                return False
            try:
                self._account(account_id, chat=True)
                nominations = self.nominations
            except (WorkflowError, AccountError):
                self._monitored.discard(account_id)
                if self._nominations is not None:
                    self._nominations.deactivate(account_id)
                return False
            if not isinstance(event, dict) or event.get("broadcaster_id") != self._channels.get(account_id):
                return False
            if event.get("type") in {"stream.online", "stream.offline"}:
                self._lifecycle_revisions[account_id] = self._lifecycle_revisions.get(account_id, 0) + 1
            accepted = nominations.record_event(account_id, event)
            if accepted and event.get("type") == "stream.offline":
                self._recordings_later()
            return accepted

    def discover_recordings(self):
        """Match channel archives to ended stream IDs, never infer clock alignment."""
        with self._lock:
            if self._closed:
                return
            store = self._stored_nominations()
            work = store.recording_work() if store is not None else []
        groups = {}
        for row in work:
            groups.setdefault((row["account_id"], row["broadcaster_id"]), []).append(row)
        for (account_id, broadcaster_id), rows in groups.items():
            try:
                self._account(account_id)
                result = self.service.perform(account_id, "videos", broadcaster_id=broadcaster_id, first=100)
                videos = result.get("videos", []) if isinstance(result, dict) else []
                if not isinstance(videos, list):
                    videos = []
            except Exception:
                # Provider errors can include sensitive transport details.
                # Preserve backoff without saving or displaying raw errors.
                videos = []
            with self._lock:
                if self._closed:
                    return
                for row in rows:
                    matched = next((video for video in videos if isinstance(video, dict)
                                    and video.get("type") == "archive"
                                    and video.get("user_id") == row["broadcaster_id"]
                                    and video.get("stream_id") == row["stream_id"]
                                    and isinstance(video.get("id"), str) and NUMBER.fullmatch(video["id"])
                                    and _duration(video.get("duration_seconds")) is not None), None)
                    store.recording_result(account_id, row["stream_id"], matched["id"] if matched else None)

    def _recordings_later(self):
        with self._lock:
            if self._closed or self._recording_worker is not None:
                return

            def run():
                try:
                    while not self._closing.is_set():
                        self.discover_recordings()
                        with self._lock:
                            if self._closed:
                                return
                            store = self._stored_nominations()
                            if store is None or not store.recordings_pending():
                                self._recording_worker = None
                                return
                        if self._closing.wait(_RECORDING_POLL_SECONDS):
                            return
                except (WorkflowError, sqlite3.Error, OSError):
                    # A damaged optional workflow store must not stop startup.
                    pass
                finally:
                    with self._lock:
                        if self._recording_worker is threading.current_thread():
                            self._recording_worker = None

            self._recording_worker = threading.Thread(target=run, daemon=True, name="twitch-recordings")
            self._recording_worker.start()

    def retry_recording(self, account_id, stream_id):
        self._account(account_id)
        if not isinstance(stream_id, str) or not NUMBER.fullmatch(stream_id):
            raise WorkflowError("invalid_stream", "Choose a valid recorded stream.")
        self.nominations.retry_recording(account_id, stream_id)
        self._recordings_later()
        return {"status": "pending"}

    def streams(self, account_id):
        self._account(account_id)
        with self._lock:
            monitoring = account_id in self._monitored
            nominations = self._stored_nominations()
            broadcaster_id = self._channels.get(account_id)
        return {"monitoring": monitoring, "broadcaster_id": broadcaster_id,
                "broadcaster_login": self._channel_logins.get(account_id),
                "streams": nominations.streams(account_id) if nominations else []}

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
        self._account(account_id)
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

    def _mapping(self, account_id, vod_id):
        nominations = self._stored_nominations()
        return nominations.mapping(account_id, vod_id) if nominations is not None else None

    def mapped_broadcaster(self, account_id, vod_id):
        """A host-verified recorded channel, never a plugin-supplied target."""
        self._account(account_id)
        mapping = self._mapping(account_id, vod_id)
        return mapping["broadcaster_id"] if mapping is not None else None

    def audience_for_binding(self, binding):
        result = self._audience(binding)
        if result.get("status") != "available":
            # Say why in words a plugin can log. Chat time maps to VOD time
            # only through a stream-to-VOD alignment, and nothing in the app
            # makes one yet (a whole-archive download is not enough: chat
            # times are wall-clock times of the live stream).
            result["message"] = AUDIENCE_MESSAGES.get(result.get("reason"), AUDIENCE_MESSAGES["unverified_binding"])
        return result

    def _audience(self, binding):
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
        try:
            mapping = self._mapping(account["id"], binding.get("vod_id", binding.get("video_id")))
            nominations = self._stored_nominations()
        except WorkflowError:
            return {**empty, "reason": "storage_unavailable"}
        if (binding.get("broadcaster_id") != account["user_id"]
                and (mapping is None or mapping["broadcaster_id"] != binding.get("broadcaster_id"))):
            return empty
        if nominations is None:
            return empty
        if self._whole_vod(binding, account):
            nominations.attach_whole_source(
                account["id"], binding.get("vod_id", binding.get("video_id")), binding.get("source_id"),
            )
        return nominations.audience_for_binding(binding)

    def _whole_vod(self, binding, account):
        """The host verified this run's file is the whole Twitch archive."""
        duration = binding.get("vod_duration")
        mapping = self._mapping(account["id"], binding.get("vod_id", binding.get("video_id")))
        return (
            binding.get("whole_vod") is True
            and (binding.get("broadcaster_id") == account["user_id"]
                 or (mapping is not None and mapping["broadcaster_id"] == binding.get("broadcaster_id")))
            and isinstance(duration, (int, float))
            and not isinstance(duration, bool)
            and math.isfinite(duration)
            and 0 < duration <= MAX_OFFSET
        )

    def source_to_vod(self, binding, seconds):
        if not isinstance(binding, dict):
            raise WorkflowError(
                "timeline_unknown",
                "Verify the local source timeline before requesting a Twitch timestamp.",
                status=412,
            )
        account = self._account(binding.get("account_id"))
        vod_id = binding.get("vod_id", binding.get("video_id"))
        mapping = self._mapping(account["id"], vod_id) if isinstance(vod_id, str) else None
        explicit = (
            mapping is not None
            and mapping["broadcaster_id"] == binding.get("broadcaster_id")
            and mapping["source_offset_seconds"] is not None
            and mapping["source_id"] is not None
            and binding.get("source_id") == mapping["source_id"]
        )
        if not explicit and not self._whole_vod(binding, account):
            raise WorkflowError(
                "timeline_unknown",
                "Verify the local source timeline before requesting a Twitch timestamp.",
                status=412,
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
        if explicit:
            result = seconds + mapping["source_offset_seconds"]
            limit = mapping["duration"]
        else:
            # The whole archive: its time is the VOD's time.
            result, limit = float(seconds), float(binding["vod_duration"])
        if result > MAX_OFFSET or (limit is not None and result > limit):
            raise WorkflowError("invalid_timestamp", "The timestamp must fit within the verified VOD.")
        return result

    def _resolve_source(self, account_id, candidate):
        account = self._account(account_id)
        broadcaster = account["user_id"]
        if "vod_id" in candidate:
            vod_id, seconds = candidate["vod_id"], candidate["offset_seconds"]
            mapping = self._mapping(account_id, vod_id)
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
            self._closing.set()
            self._monitored.clear()
            nominations, digests = self._nominations, self._digests
            worker = self._recording_worker
        if worker is not None and worker is not threading.current_thread():
            worker.join(timeout=2)
        if nominations is not None:
            nominations.close()
        if digests is not None:
            digests.close()
