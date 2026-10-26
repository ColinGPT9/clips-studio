"""Bounded event aggregates: never persist chat text or viewer identities."""

from __future__ import annotations

import hashlib
import hmac
import math
import re
import secrets
import sqlite3
import threading
import time
from collections import OrderedDict
from datetime import datetime, timezone
from pathlib import Path

from .models import WorkflowError

COMMANDS = {"!clip": "clip", "!chaos": "chaos", "!funny": "funny"}
NUMBER = re.compile(r"^[0-9]{1,30}$")
MAX_OFFSET = 31_536_000


def timestamp(value):
    if not isinstance(value, str) or len(value) > 64:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            return None
        result = dt.timestamp()
        return result if math.isfinite(result) and result >= 0 else None
    except (ValueError, OverflowError):
        return None


def iso(value):
    return datetime.fromtimestamp(value, timezone.utc).isoformat().replace("+00:00", "Z")


class Nominations:
    """One local SQLite aggregate store with session-only viewer cooldowns.

    ``event_id`` hashes are persisted for replay protection, but contain no chat
    or viewer identifiers. A fresh random HMAC key protects in-memory viewer
    cooldown entries; those entries and the key disappear on restart.
    """

    def __init__(
        self,
        data_dir,
        *,
        clock=time.time,
        cooldown_seconds=60,
        bucket_seconds=30,
        retention_days=30,
        max_events=50_000,
        max_streams=200,
        max_buckets=20_000,
        prune_interval=60,
    ):
        self.clock = clock
        self.cooldown = max(1, min(int(cooldown_seconds), 3600))
        self.bucket_seconds = max(1, min(int(bucket_seconds), 300))
        self.retention = max(1, min(int(retention_days), 90)) * 86400
        self.max_events = max(10, min(int(max_events), 100_000))
        self.max_streams = max(1, min(int(max_streams), 1000))
        self.max_buckets = max(10, min(int(max_buckets), 50_000))
        # Pruning sorts whole tables, and record_event runs on the EventSub
        # receive thread: prune once a minute, or after a tenth of the
        # smallest row bound in writes, so the bounds hold within 10%.
        self.prune_interval = max(0, float(prune_interval))
        self._prune_every = max(1, min(self.max_events, self.max_buckets) // 10)
        self._pruned_at = None
        self._writes = 0
        self._secret = secrets.token_bytes(32)
        self._viewers = OrderedDict()
        self._lock = threading.RLock()
        Path(data_dir).mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(Path(data_dir) / "stream-workflows.sqlite3", check_same_thread=False)
        try:
            self._open()
        except Exception:
            # Release the file so the caller can set a damaged one aside.
            self._db.close()
            raise

    def _open(self):
        self._db.row_factory = sqlite3.Row
        self._db.executescript("""
            PRAGMA foreign_keys=ON;
            CREATE TABLE IF NOT EXISTS streams(
              account_id TEXT NOT NULL, broadcaster_id TEXT NOT NULL, stream_id TEXT NOT NULL,
              started REAL, ended REAL, touched REAL NOT NULL,
              PRIMARY KEY(account_id, stream_id));
            CREATE TABLE IF NOT EXISTS active_streams(
              account_id TEXT PRIMARY KEY, stream_id TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS events(
              account_id TEXT NOT NULL, event_hash TEXT NOT NULL, received REAL NOT NULL,
              PRIMARY KEY(account_id,event_hash));
            CREATE TABLE IF NOT EXISTS nominations(
              account_id TEXT NOT NULL, stream_id TEXT NOT NULL, bucket INTEGER NOT NULL,
              command TEXT NOT NULL, count INTEGER NOT NULL, first_seen REAL NOT NULL,
              last_seen REAL NOT NULL, PRIMARY KEY(account_id,stream_id,bucket,command));
            CREATE TABLE IF NOT EXISTS mappings(
              account_id TEXT NOT NULL, broadcaster_id TEXT NOT NULL, vod_id TEXT NOT NULL,
              stream_id TEXT NOT NULL, stream_start REAL NOT NULL, offset_seconds REAL NOT NULL,
              verified_at REAL NOT NULL, duration REAL, source_offset_seconds REAL, source_id TEXT,
              PRIMARY KEY(account_id,vod_id));
            CREATE TABLE IF NOT EXISTS recordings(
              account_id TEXT NOT NULL, stream_id TEXT NOT NULL,
              deadline REAL NOT NULL, next_check REAL NOT NULL, attempts INTEGER NOT NULL DEFAULT 0,
              status TEXT NOT NULL DEFAULT 'pending', vod_id TEXT,
              PRIMARY KEY(account_id,stream_id),
              FOREIGN KEY(account_id,stream_id) REFERENCES streams(account_id,stream_id) ON DELETE CASCADE);
            CREATE INDEX IF NOT EXISTS streams_touched ON streams(touched);
            CREATE INDEX IF NOT EXISTS events_received ON events(received);
            CREATE INDEX IF NOT EXISTS nominations_last_seen ON nominations(last_seen);
        """)
        if "source_offset_seconds" not in {r[1] for r in self._db.execute("PRAGMA table_info(mappings)")}:
            self._db.execute("ALTER TABLE mappings ADD COLUMN source_offset_seconds REAL")
        if "source_id" not in {r[1] for r in self._db.execute("PRAGMA table_info(mappings)")}:
            self._db.execute("ALTER TABLE mappings ADD COLUMN source_id TEXT")
        # Offline transitions may have occurred while this host was stopped.
        self._db.execute("DELETE FROM active_streams")
        self._maybe_prune(force=True)
        self._db.commit()

    def _maybe_prune(self, *, force=False):
        self._writes += 1
        now = self.clock()
        if (force or self._pruned_at is None or now - self._pruned_at >= self.prune_interval
                or self._writes >= self._prune_every):
            self._prune()
            self._pruned_at, self._writes = now, 0

    def _prune(self):
        cutoff = self.clock() - self.retention
        self._db.execute("DELETE FROM streams WHERE touched < ?", (cutoff,))
        self._db.execute(
            "DELETE FROM streams WHERE rowid IN (SELECT rowid FROM streams ORDER BY touched DESC LIMIT -1 OFFSET ?)",
            (self.max_streams,),
        )
        self._db.execute(
            "DELETE FROM nominations WHERE NOT EXISTS (SELECT 1 FROM streams s WHERE s.account_id=nominations.account_id AND s.stream_id=nominations.stream_id)"
        )
        self._db.execute(
            "DELETE FROM mappings WHERE verified_at < ? OR NOT EXISTS (SELECT 1 FROM streams s WHERE s.account_id=mappings.account_id AND s.stream_id=mappings.stream_id)",
            (cutoff,),
        )
        self._db.execute(
            "DELETE FROM active_streams WHERE NOT EXISTS (SELECT 1 FROM streams s WHERE s.account_id=active_streams.account_id AND s.stream_id=active_streams.stream_id)"
        )
        self._db.execute(
            "DELETE FROM nominations WHERE rowid IN (SELECT rowid FROM nominations ORDER BY last_seen DESC LIMIT -1 OFFSET ?)",
            (self.max_buckets,),
        )
        self._db.execute("DELETE FROM events WHERE received < ?", (cutoff,))
        self._db.execute(
            "DELETE FROM events WHERE rowid IN (SELECT rowid FROM events ORDER BY received DESC,rowid DESC LIMIT -1 OFFSET ?)",
            (self.max_events,),
        )

    def record_event(self, account_id, event):
        """Consume an already sanitized host EventSub event; return acceptance."""
        if not isinstance(event, dict):
            return False
        kind = event.get("type")
        data = event.get("data")
        broadcaster = event.get("broadcaster_id")
        event_id = event.get("id")
        at = timestamp(event.get("timestamp"))
        if (
            kind not in {"stream.online", "stream.offline", "channel.chat.message"}
            or not isinstance(data, dict)
            or not isinstance(event_id, str)
            or not 1 <= len(event_id) <= 512
            or not isinstance(account_id, str)
            or not isinstance(broadcaster, str)
            or not NUMBER.fullmatch(broadcaster)
            or at is None
            or at > self.clock() + 300
            or at < self.clock() - self.retention
        ):
            return False
        # Only exact, lower-case commands participate. No raw chat is retained.
        text = data.get("text")
        command = COMMANDS.get(text) if kind == "channel.chat.message" and isinstance(text, str) else None
        if kind == "channel.chat.message" and command is None:
            return False
        event_hash = hashlib.sha256(event_id.encode()).hexdigest()
        with self._lock, self._db:
            if self._db.execute(
                "SELECT 1 FROM events WHERE account_id=? AND event_hash=?", (account_id, event_hash)
            ).fetchone():
                return False
            self._db.execute("INSERT INTO events VALUES(?,?,?)", (account_id, event_hash, self.clock()))
            try:
                return self._record(account_id, kind, data, broadcaster, at, command)
            finally:
                # Once per event at most, after its writes (see __init__).
                self._maybe_prune(force=kind == "stream.online")

    def _record(self, account_id, kind, data, broadcaster, at, command):
        """The writes for one new event, inside record_event's transaction."""
        if kind == "stream.online":
            # EventSub also announces reruns, playlists and premieres.
            # Older bootstrap envelopes omit type; an explicit type must
            # identify a live broadcast before it can anchor chat times.
            if data.get("type") not in (None, "", "live"):
                return False
            stream_id = data.get("id")
            start = timestamp(data.get("started_at"))
            if not isinstance(stream_id, str) or not NUMBER.fullmatch(stream_id):
                return False
            if start is not None and (start > at + 300 or start < self.clock() - self.retention):
                start = None
            prior = self._db.execute(
                "SELECT * FROM streams WHERE account_id=? AND stream_id=?", (account_id, stream_id)
            ).fetchone()
            active = self._db.execute(
                "SELECT s.* FROM streams s JOIN active_streams a ON a.account_id=s.account_id AND a.stream_id=s.stream_id WHERE a.account_id=?",
                (account_id,),
            ).fetchone()
            if prior is not None and prior["ended"] is not None:
                return False
            if (
                active is not None
                and active["started"] is not None
                and (start is None or start < active["started"])
            ):
                return False
            self._db.execute(
                "INSERT INTO streams VALUES(?,?,?,?,NULL,?) ON CONFLICT(account_id,stream_id) DO UPDATE SET touched=excluded.touched,started=COALESCE(streams.started,excluded.started)",
                (account_id, broadcaster, stream_id, start, self.clock()),
            )
            self._db.execute(
                "INSERT INTO active_streams VALUES(?,?) ON CONFLICT(account_id) DO UPDATE SET stream_id=excluded.stream_id",
                (account_id, stream_id),
            )
            return True
        active = self._db.execute(
            "SELECT s.* FROM streams s JOIN active_streams a ON a.account_id=s.account_id AND a.stream_id=s.stream_id WHERE a.account_id=? AND s.broadcaster_id=?",
            (account_id, broadcaster),
        ).fetchone()
        if active is None:
            return False
        if kind == "stream.offline":
            if active["started"] is not None and at < active["started"]:
                return False
            self._db.execute(
                "UPDATE streams SET ended=?,touched=? WHERE account_id=? AND stream_id=?",
                (at, self.clock(), account_id, active["stream_id"]),
            )
            self._db.execute("DELETE FROM active_streams WHERE account_id=?", (account_id,))
            self._db.execute(
                "INSERT OR IGNORE INTO recordings(account_id,stream_id,deadline,next_check) VALUES(?,?,?,?)",
                (account_id, active["stream_id"], self.clock() + 6 * 3600, self.clock()),
            )
            return True
        if active["started"] is None or at < active["started"]:
            return False
        viewer = data.get("chatter_user_id")
        if not isinstance(viewer, str) or not NUMBER.fullmatch(viewer):
            return False
        viewer_hash = hmac.new(
            self._secret, (account_id + ":" + active["stream_id"] + ":" + viewer).encode(), hashlib.sha256
        ).digest()
        previous = self._viewers.get(viewer_hash)
        # Use host receive time so delayed events cannot bypass cooldown.
        now = self.clock()
        if previous is not None and now - previous < self.cooldown:
            return False
        self._viewers[viewer_hash] = now
        self._viewers.move_to_end(viewer_hash)
        while len(self._viewers) > 10_000:
            self._viewers.popitem(last=False)
        bucket = int((at - active["started"]) // self.bucket_seconds) * self.bucket_seconds
        self._db.execute(
            "INSERT INTO nominations VALUES(?,?,?,?,1,?,?) ON CONFLICT(account_id,stream_id,bucket,command) DO UPDATE SET count=count+1,first_seen=MIN(first_seen,excluded.first_seen),last_seen=MAX(last_seen,excluded.last_seen)",
            (account_id, active["stream_id"], bucket, command, at, at),
        )
        self._db.execute(
            "UPDATE streams SET touched=? WHERE account_id=? AND stream_id=?",
            (now, account_id, active["stream_id"]),
        )
        return True

    def deactivate(self, account_id):
        with self._lock, self._db:
            self._db.execute("DELETE FROM active_streams WHERE account_id=?", (account_id,))

    def streams(self, account_id):
        with self._lock, self._db:
            self._prune()
            rows = self._db.execute(
                "SELECT s.*, (SELECT SUM(n.count) FROM nominations n WHERE n.account_id=s.account_id AND n.stream_id=s.stream_id) nomination_count FROM streams s WHERE account_id=? ORDER BY touched DESC LIMIT ?",
                (account_id, self.max_streams),
            ).fetchall()
            return [self._stream_summary(row) for row in rows]

    def _stream_summary(self, row):
        commands = dict.fromkeys(COMMANDS.values(), 0)
        buckets = {}
        for nomination in self._db.execute(
            "SELECT bucket,command,count FROM nominations WHERE account_id=? AND stream_id=? ORDER BY bucket,command",
            (row["account_id"], row["stream_id"]),
        ):
            command, count = nomination["command"], nomination["count"]
            if command not in commands:
                continue
            commands[command] += count
            bucket = buckets.setdefault(nomination["bucket"], {
                "offset_seconds": nomination["bucket"], "count": 0, "commands": {},
            })
            bucket["count"] += count
            bucket["commands"][command] = count
        mapping = self._db.execute(
            "SELECT vod_id,offset_seconds,source_offset_seconds,source_id FROM mappings "
            "WHERE account_id=? AND stream_id=? ORDER BY verified_at DESC LIMIT 1",
            (row["account_id"], row["stream_id"]),
        ).fetchone()
        recording = self._db.execute(
            "SELECT status,vod_id,attempts,next_check FROM recordings WHERE account_id=? AND stream_id=?",
            (row["account_id"], row["stream_id"]),
        ).fetchone()
        return {
            "stream_id": row["stream_id"], "broadcaster_id": row["broadcaster_id"],
            "stream_start": iso(row["started"]) if row["started"] is not None else None,
            "ended_at": iso(row["ended"]) if row["ended"] is not None else None,
            "nomination_count": row["nomination_count"] or 0,
            "commands": commands, "timeline": list(buckets.values())[-200:],
            "timeline_truncated": len(buckets) > 200, "bucket_seconds": self.bucket_seconds,
            "mapping": dict(mapping) if mapping is not None else None,
            "recording": dict(recording) if recording is not None else None,
        }

    def finish_offline(self, account_id):
        """Reconcile an offline channel after a missed stream.offline event.

        The timestamp is when offline was observed, not a claimed exact end.
        The original verified stream ID/start remain the archive matching keys.
        """
        with self._lock, self._db:
            now = self.clock()
            rows = self._db.execute(
                "SELECT stream_id FROM streams WHERE account_id=? AND ended IS NULL AND started<=?",
                (account_id, now),
            ).fetchall()
            for row in rows:
                self._db.execute(
                    "UPDATE streams SET ended=?,touched=? WHERE account_id=? AND stream_id=?",
                    (now, now, account_id, row["stream_id"]),
                )
                self._db.execute(
                    "INSERT OR IGNORE INTO recordings(account_id,stream_id,deadline,next_check) VALUES(?,?,?,?)",
                    (account_id, row["stream_id"], now + 6 * 3600, now),
                )
            return len(rows)

    def recording_work(self):
        """Due lookups only; persisted deadlines and backoff survive restart."""
        with self._lock, self._db:
            now = self.clock()
            self._db.execute("UPDATE recordings SET status='expired' WHERE status='pending' AND deadline<=?", (now,))
            return [dict(row) for row in self._db.execute(
                "SELECT r.*,s.broadcaster_id FROM recordings r JOIN streams s "
                "ON s.account_id=r.account_id AND s.stream_id=r.stream_id "
                "WHERE r.status='pending' AND r.next_check<=? ORDER BY r.next_check LIMIT 25", (now,),
            )]

    def recordings_pending(self):
        with self._lock:
            return self._db.execute("SELECT 1 FROM recordings WHERE status='pending' LIMIT 1").fetchone() is not None

    def recording_result(self, account_id, stream_id, vod_id=None):
        with self._lock, self._db:
            row = self._db.execute(
                "SELECT attempts FROM recordings WHERE account_id=? AND stream_id=? AND status='pending'",
                (account_id, stream_id),
            ).fetchone()
            if row is None:
                return
            attempts = row["attempts"] + 1
            delay = min(900, 15 * 2 ** min(attempts - 1, 6))
            self._db.execute(
                "UPDATE recordings SET status=?,vod_id=?,attempts=?,next_check=? WHERE account_id=? AND stream_id=?",
                ("ready" if vod_id else "pending", vod_id, attempts, self.clock() + delay, account_id, stream_id),
            )

    def retry_recording(self, account_id, stream_id):
        with self._lock, self._db:
            row = self._db.execute(
                "SELECT ended FROM streams WHERE account_id=? AND stream_id=?", (account_id, stream_id),
            ).fetchone()
            if row is None or row["ended"] is None:
                raise WorkflowError("recording_unavailable", "Choose an ended stream before looking for its recording.", status=409)
            self._db.execute(
                "INSERT INTO recordings(account_id,stream_id,deadline,next_check) VALUES(?,?,?,?) "
                "ON CONFLICT(account_id,stream_id) DO UPDATE SET deadline=excluded.deadline,"
                "next_check=excluded.next_check,attempts=0,status='pending',vod_id=NULL",
                (account_id, stream_id, self.clock() + 6 * 3600, self.clock()),
            )

    def attach_whole_source(self, account_id, vod_id, source_id):
        """Attach host-proven whole-archive media after live timing was checked.

        An explicit cropped-source mapping must never be replaced by this path.
        The caller supplies identity from host media provenance, not plugin input.
        """
        if not isinstance(source_id, str) or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,128}", source_id):
            return
        with self._lock, self._db:
            self._db.execute(
                "UPDATE mappings SET source_id=?,source_offset_seconds=0 "
                "WHERE account_id=? AND vod_id=? AND (source_offset_seconds IS NULL OR source_offset_seconds=0)",
                (source_id, account_id, vod_id),
            )

    def bind_vod(
        self,
        *,
        account_id,
        broadcaster_id,
        vod_id,
        stream_id,
        stream_start,
        offset_seconds,
        verified,
        duration=None,
        source_offset_seconds=None,
        source_id=None,
    ):
        start = timestamp(stream_start)
        if (
            verified is not True
            or start is None
            or not all(
                isinstance(v, str) and NUMBER.fullmatch(v) for v in (broadcaster_id, vod_id, stream_id)
            )
            or isinstance(offset_seconds, bool)
            or not isinstance(offset_seconds, (int, float))
            or not math.isfinite(offset_seconds)
            or abs(offset_seconds) > MAX_OFFSET
        ):
            raise WorkflowError("invalid_alignment", "Supply an explicitly verified stream-to-VOD alignment.")
        if source_id is not None and (
            not isinstance(source_id, str) or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,128}", source_id)
        ):
            raise WorkflowError("invalid_alignment", "Choose the local source identity for this alignment.")
        if source_offset_seconds is not None and (
            isinstance(source_offset_seconds, bool)
            or not isinstance(source_offset_seconds, (int, float))
            or not math.isfinite(source_offset_seconds)
            or not 0 <= source_offset_seconds <= MAX_OFFSET
        ):
            raise WorkflowError(
                "invalid_alignment", "Supply a verified local source position within the VOD."
            )
        if duration is not None and (
            isinstance(duration, bool)
            or not isinstance(duration, (int, float))
            or not math.isfinite(duration)
            or not 0 < duration <= MAX_OFFSET
        ):
            duration = None
        if duration is not None and source_offset_seconds is not None and source_offset_seconds >= duration:
            raise WorkflowError("invalid_alignment", "The local source must begin within the verified VOD.")
        with self._lock, self._db:
            self._prune()
            stream = self._db.execute(
                "SELECT * FROM streams WHERE account_id=? AND broadcaster_id=? AND stream_id=?",
                (account_id, broadcaster_id, stream_id),
            ).fetchone()
            if stream is None or stream["started"] is None or abs(stream["started"] - start) > 0.01:
                raise WorkflowError(
                    "timeline_unknown",
                    "The recorded stream start must match the verified alignment.",
                    status=409,
                )
            self._db.execute(
                "INSERT INTO mappings VALUES(?,?,?,?,?,?,?,?,?,?) ON CONFLICT(account_id,vod_id) DO UPDATE SET broadcaster_id=excluded.broadcaster_id,stream_id=excluded.stream_id,stream_start=excluded.stream_start,offset_seconds=excluded.offset_seconds,verified_at=excluded.verified_at,duration=excluded.duration,source_offset_seconds=excluded.source_offset_seconds,source_id=excluded.source_id",
                (
                    account_id,
                    broadcaster_id,
                    vod_id,
                    stream_id,
                    start,
                    float(offset_seconds),
                    self.clock(),
                    duration,
                    source_offset_seconds,
                    source_id,
                ),
            )
            return {
                "account_id": account_id,
                "broadcaster_id": broadcaster_id,
                "vod_id": vod_id,
                "stream_id": stream_id,
                "stream_start": iso(start),
                "offset_seconds": float(offset_seconds),
                "source_offset_seconds": source_offset_seconds,
                "source_id": source_id,
                "verified": True,
            }

    def mapping(self, account_id, vod_id):
        with self._lock, self._db:
            self._prune()
            row = self._db.execute(
                "SELECT * FROM mappings WHERE account_id=? AND vod_id=?", (account_id, vod_id)
            ).fetchone()
            return dict(row) if row is not None else None

    def audience_for_binding(self, binding):
        def empty(reason):
            return {
                "status": "unavailable",
                "reason": reason,
                "source": "viewer_nominations",
                "nominations": [],
            }

        if not isinstance(binding, dict):
            return empty("unverified_binding")
        account_id, broadcaster = binding.get("account_id"), binding.get("broadcaster_id")
        vod_id = binding.get("vod_id", binding.get("video_id"))
        if not isinstance(account_id, str) or not isinstance(vod_id, str):
            return empty("unverified_binding")
        mapping = self.mapping(account_id, vod_id)
        if mapping is None or mapping["broadcaster_id"] != broadcaster:
            return empty("unverified_binding")
        if (
            mapping["source_offset_seconds"] is None
            or mapping["source_id"] is None
            or binding.get("source_id") != mapping["source_id"]
        ):
            return empty("source_timeline_unknown")
        duration = mapping["duration"]
        source_duration = binding.get("duration")
        if (
            not isinstance(source_duration, (int, float))
            or isinstance(source_duration, bool)
            or not math.isfinite(source_duration)
            or source_duration <= 0
        ):
            source_duration = None
        with self._lock:
            rows = self._db.execute(
                "SELECT * FROM nominations WHERE account_id=? AND stream_id=? ORDER BY bucket ASC LIMIT 1000",
                (account_id, mapping["stream_id"]),
            ).fetchall()
        buckets = {}
        for row in rows:
            first_vod = row["first_seen"] - mapping["stream_start"] + mapping["offset_seconds"]
            last_vod = row["last_seen"] - mapping["stream_start"] + mapping["offset_seconds"]
            first_source = first_vod - mapping["source_offset_seconds"]
            last_source = last_vod - mapping["source_offset_seconds"]
            # An aggregate cannot say how many events were outside a cropped
            # source. Drop partial aggregates instead of misreporting counts.
            if (
                first_vod < 0
                or last_vod > MAX_OFFSET
                or first_source < 0
                or (duration is not None and last_vod >= duration)
                or (source_duration is not None and last_source >= source_duration)
            ):
                continue
            seconds = max(0, row["bucket"] + mapping["offset_seconds"] - mapping["source_offset_seconds"])
            vod_seconds = seconds + mapping["source_offset_seconds"]
            item = buckets.setdefault(
                row["bucket"],
                {
                    "offset_seconds": seconds,
                    "vod_offset_seconds": vod_seconds,
                    "count": 0,
                    "commands": {},
                    "first_seen_seconds": first_source,
                    "last_seen_seconds": last_source,
                },
            )
            item["count"] += row["count"]
            item["commands"][row["command"]] = row["count"]
            item["first_seen_seconds"] = min(item["first_seen_seconds"], first_source)
            item["last_seen_seconds"] = max(item["last_seen_seconds"], last_source)
        result = sorted(buckets.values(), key=lambda r: (-r["count"], r["offset_seconds"]))[:100]
        return {"status": "available", "reason": "", "source": "viewer_nominations", "nominations": result}

    def close(self):
        with self._lock:
            self._viewers.clear()
            self._secret = b""
            self._db.close()
