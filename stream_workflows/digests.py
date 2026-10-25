"""Persistent, approval-first catch-up digests with optional delivery.

The source resolver is trusted host code: it verifies ownership and recording
alignment before returning a public Twitch link. No transport receipt or
exception text is persisted. A durable ``sending`` transition prevents retries
after an ambiguous delivery or process crash.
"""

from __future__ import annotations

import copy
import json
import math
import re
import sqlite3
import threading
import time
import uuid
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from connected_accounts.store import atomic_write
from stream_workflows.models import WorkflowError

_VIDEO = re.compile(r"^[0-9]{1,30}$")
_CLIP = re.compile(r"^[A-Za-z0-9_-]{1,160}$")
_DIGEST = re.compile(r"^[0-9a-f]{32}$")
_TIME = re.compile(r"^(?:(\d{1,6})h)?(?:(\d{1,6})m)?(?:(\d{1,6})s)?$")
_MAX_SECONDS = 7 * 24 * 60 * 60


def _key(value, name, limit=200):
    if (
        not isinstance(value, str)
        or not 1 <= len(value) <= limit
        or any(ord(c) < 33 or ord(c) == 127 for c in value)
    ):
        raise WorkflowError("invalid_request", f"Choose a valid {name}.")
    return value


def _title(value):
    if not isinstance(value, str) or len(value) > 160:
        raise WorkflowError("invalid_request", "Highlight titles must be at most 160 characters.")
    # Keep titles as one bounded plain-text line, including when a transport
    # supports mentions or inline formatting.
    text = " ".join("".join(c if ord(c) >= 32 and ord(c) != 127 else " " for c in value).split())
    return text.replace("@", "＠").replace("`", "")


def _seconds(value):
    if (
        not isinstance(value, (int, float))
        or isinstance(value, bool)
        or not math.isfinite(value)
        or not 0 <= value <= _MAX_SECONDS
    ):
        raise WorkflowError("invalid_request", "Choose a valid recording offset.")
    return float(value)


def _timestamp(seconds):
    seconds = int(seconds)
    return f"{seconds // 3600:02d}:{seconds % 3600 // 60:02d}:{seconds % 60:02d}"


class DigestService:
    def __init__(self, data_dir, *, source_resolver, transport=None, clock=time.time):
        self._directory = Path(data_dir) / "stream-digests"
        self._directory.mkdir(parents=True, exist_ok=True)
        self._resolver = source_resolver
        self._transport = transport
        self._clock = clock
        self._lock = threading.RLock()
        self._closed = False
        self._db = sqlite3.connect(Path(data_dir) / "stream-digests.sqlite3", check_same_thread=False)
        try:
            self._open()
        except Exception:
            # Release the file so the caller can set a damaged one aside.
            self._db.close()
            raise

    def _open(self):
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute("PRAGMA synchronous=FULL")
        self._db.execute("""CREATE TABLE IF NOT EXISTS digests (
            id TEXT PRIMARY KEY, account_id TEXT NOT NULL, request_id TEXT NOT NULL,
            payload TEXT NOT NULL, UNIQUE(account_id, request_id))""")
        self._db.commit()
        # A crash can occur after a remote write but before its receipt. Never
        # infer that an interrupted send failed, or retry it automatically.
        for digest in self._rows():
            if digest["status"] == "sending":
                digest["status"] = "delivery_unknown"
                digest["updated_at"] = float(self._clock())
                self._save(digest)

    def _require_open(self):
        if self._closed:
            raise WorkflowError("service_closed", "Stream workflows are stopping.", status=503)

    def _rows(self, account_id=None):
        if account_id is None:
            rows = self._db.execute("SELECT payload FROM digests").fetchall()
        else:
            rows = self._db.execute(
                "SELECT payload FROM digests WHERE account_id=?", (account_id,)
            ).fetchall()
        return [json.loads(row[0]) for row in rows]

    def _save(self, digest):
        raw = json.dumps(digest, ensure_ascii=False, sort_keys=True, allow_nan=False)
        with self._db:
            self._db.execute(
                """INSERT INTO digests(id,account_id,request_id,payload) VALUES(?,?,?,?)
                ON CONFLICT(id) DO UPDATE SET payload=excluded.payload""",
                (digest["id"], digest["account_id"], digest["request_id"], raw),
            )
        # SQLite is authoritative. Persist a portable JSON snapshot after each
        # state change; a later export also reads the authoritative state.
        try:
            atomic_write(self._directory / f"{digest['id']}.json", raw.encode("utf-8"))
        except OSError:
            raise WorkflowError(
                "digest_store_unavailable", "The digest export could not be saved.", status=503
            ) from None

    @staticmethod
    def _candidate(candidate):
        if not isinstance(candidate, dict):
            raise WorkflowError("invalid_request", "Choose valid highlight candidates.")
        fields = set(candidate)
        common = {"title", "score"}
        if "vod_id" in fields:
            required, allowed = {"vod_id", "offset_seconds"}, common | {"vod_id", "offset_seconds"}
        else:
            required, allowed = {"clip_id"}, common | {"clip_id"}
        if not required <= fields or not fields <= allowed:
            raise WorkflowError(
                "invalid_request", "Candidates need a recording offset or a clip ID, without URLs."
            )
        result = {"title": _title(candidate.get("title", ""))}
        score = candidate.get("score", 0)
        if not isinstance(score, (int, float)) or isinstance(score, bool) or not math.isfinite(score):
            raise WorkflowError("invalid_request", "Highlight scores must be finite numbers.")
        result["score"] = float(score)
        if "vod_id" in candidate:
            if not isinstance(candidate["vod_id"], str) or not _VIDEO.fullmatch(candidate["vod_id"]):
                raise WorkflowError("invalid_request", "Choose a valid Twitch recording.")
            result.update(vod_id=candidate["vod_id"], offset_seconds=_seconds(candidate["offset_seconds"]))
        else:
            if not isinstance(candidate["clip_id"], str) or not _CLIP.fullmatch(candidate["clip_id"]):
                raise WorkflowError("invalid_request", "Choose a valid Twitch clip.")
            result["clip_id"] = candidate["clip_id"]
        return result

    @staticmethod
    def _resolved(candidate, resolved):
        if not isinstance(resolved, dict) or not isinstance(resolved.get("url"), str):
            raise WorkflowError("unverified_source", "A highlight source could not be verified.", status=409)
        try:
            parsed = urlsplit(resolved["url"])
            valid = (
                parsed.scheme == "https"
                and parsed.username is None
                and parsed.password is None
                and parsed.port is None
                and not parsed.fragment
                and len(resolved["url"]) <= 500
            )
        except ValueError:
            valid = False
        if not valid:
            raise WorkflowError("unverified_source", "A highlight source could not be verified.", status=409)
        result = {"score": candidate["score"]}
        if "vod_id" in candidate:
            seconds = int(candidate["offset_seconds"])
            query = parse_qs(parsed.query, keep_blank_values=True)
            match = _TIME.fullmatch(query.get("t", [""])[0])
            linked_seconds = (
                sum(int(v or 0) * scale for v, scale in zip(match.groups(), (3600, 60, 1)))
                if match and any(match.groups())
                else -1
            )
            if (
                parsed.hostname != "www.twitch.tv"
                or parsed.path != f"/videos/{candidate['vod_id']}"
                or set(query) != {"t"}
                or len(query["t"]) != 1
                or linked_seconds != seconds
                or resolved.get("video_id", candidate["vod_id"]) != candidate["vod_id"]
                or resolved.get("kind") not in {"vod_timestamp", "vod"}
            ):
                raise WorkflowError(
                    "unverified_source", "A highlight source could not be verified.", status=409
                )
            # Build canonical public links ourselves; no provider query string
            # other than the verified recording timestamp can survive.
            result.update(
                kind="vod_timestamp",
                video_id=candidate["vod_id"],
                seconds=seconds,
                url=f"https://www.twitch.tv/videos/{candidate['vod_id']}?t={seconds // 3600}h{seconds % 3600 // 60}m{seconds % 60}s",
            )
            fallback = f"Highlight at {_timestamp(seconds)}"
        else:
            if (
                parsed.hostname != "clips.twitch.tv"
                or parsed.path != f"/{candidate['clip_id']}"
                or parsed.query
                or resolved.get("clip_id", candidate["clip_id"]) != candidate["clip_id"]
                or resolved.get("kind") not in {"native_clip", "clip"}
            ):
                raise WorkflowError(
                    "unverified_source", "A highlight source could not be verified.", status=409
                )
            result.update(
                kind="native_clip",
                clip_id=candidate["clip_id"],
                url=f"https://clips.twitch.tv/{candidate['clip_id']}",
            )
            fallback = "Twitch highlight"
        result["title"] = candidate["title"] or _title(resolved.get("title", "")) or fallback
        return result

    def prepare(self, account_id, *, request_id, candidates, top_k=5, auto_publish=False):
        account_id = _key(account_id, "connected account")
        request_id = _key(request_id, "request ID", 128)
        if not isinstance(top_k, int) or isinstance(top_k, bool) or top_k not in (3, 5, 10):
            raise WorkflowError("invalid_request", "Choose a top 3, 5, or 10 digest.")
        if not isinstance(auto_publish, bool):
            raise WorkflowError("invalid_request", "Automatic publishing requires explicit opt-in.")
        if not isinstance(candidates, list) or not 1 <= len(candidates) <= 100:
            raise WorkflowError("invalid_request", "Choose between one and 100 highlight candidates.")
        with self._lock:
            self._require_open()
            row = self._db.execute(
                "SELECT payload FROM digests WHERE account_id=? AND request_id=?", (account_id, request_id)
            ).fetchone()
            if row is not None:
                return json.loads(row[0])
            clean = [self._candidate(c) for c in candidates]
            ranked = sorted(clean, key=lambda c: c["score"], reverse=True)[:top_k]
            items = []
            for candidate in ranked:
                try:
                    resolved = self._resolver(account_id, copy.deepcopy(candidate))
                except WorkflowError:
                    raise
                except Exception:
                    raise WorkflowError(
                        "unverified_source", "A highlight source could not be verified.", status=409
                    ) from None
                items.append(self._resolved(candidate, resolved))
            now = float(self._clock())
            digest = {
                "id": uuid.uuid4().hex,
                "account_id": account_id,
                "request_id": request_id,
                "status": "pending_approval",
                "top_k": top_k,
                "auto_publish": auto_publish,
                "items": items,
                "created_at": now,
                "updated_at": now,
            }
            self._save(digest)
            if auto_publish:
                return self._approve(digest)
            return copy.deepcopy(digest)

    def list(self, account_id):
        account_id = _key(account_id, "connected account")
        with self._lock:
            self._require_open()
            return sorted(self._rows(account_id), key=lambda d: (d["created_at"], d["id"]), reverse=True)

    def get(self, account_id, digest_id):
        account_id = _key(account_id, "connected account")
        with self._lock:
            self._require_open()
            if not isinstance(digest_id, str) or not _DIGEST.fullmatch(digest_id):
                raise WorkflowError("digest_not_found", "This digest does not exist.", status=404)
            row = self._db.execute(
                "SELECT payload FROM digests WHERE account_id=? AND id=?", (account_id, digest_id)
            ).fetchone()
            if row is None:
                raise WorkflowError("digest_not_found", "This digest does not exist.", status=404)
            return json.loads(row[0])

    def _approve(self, digest):
        if digest["status"] in {"sent", "sending", "delivery_unknown"}:
            return copy.deepcopy(digest)
        if digest["status"] == "unsent" and self._transport is None:
            return copy.deepcopy(digest)
        digest["updated_at"] = float(self._clock())
        if self._transport is None:
            digest["status"] = "unsent"
            self._save(digest)
            return copy.deepcopy(digest)
        digest["status"] = "sending"
        self._save(digest)
        try:
            self._transport.send(copy.deepcopy(digest), idempotency_key=f"stream-digest:{digest['id']}")
        except Exception:
            digest["status"] = "delivery_unknown"
        else:
            digest["status"] = "sent"
        digest["updated_at"] = float(self._clock())
        self._save(digest)
        return copy.deepcopy(digest)

    def approve(self, account_id, digest_id):
        with self._lock:
            return self._approve(self.get(account_id, digest_id))

    def export(self, account_id, digest_id):
        return {"schema_version": 1, **self.get(account_id, digest_id)}

    def close(self):
        with self._lock:
            if not self._closed:
                self._closed = True
                self._db.close()
