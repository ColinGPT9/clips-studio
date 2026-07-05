"""SQLite state database.

Single source of truth for what has been seen, processed, rendered,
scheduled, and uploaded. Every pipeline stage commits its status here
BEFORE the next stage runs, so a crash at any point resumes cleanly and
nothing is ever reprocessed or double-uploaded.
"""

import sqlite3
from datetime import date, datetime
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS videos (
    video_id   TEXT PRIMARY KEY,
    channel_id TEXT,
    title      TEXT,
    status     TEXT NOT NULL DEFAULT 'queued',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS clips (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    video_id      TEXT NOT NULL REFERENCES videos(video_id),
    start_s       REAL NOT NULL,
    end_s         REAL NOT NULL,
    score         INTEGER NOT NULL,
    hook          TEXT,
    path          TEXT,
    status        TEXT NOT NULL DEFAULT 'rendered',
    scheduled_for TEXT,
    created_at    TEXT NOT NULL,
    UNIQUE (video_id, start_s, end_s)
);

CREATE TABLE IF NOT EXISTS rejections (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    video_id    TEXT NOT NULL,
    start_s     REAL,
    end_s       REAL,
    score       INTEGER,
    reason      TEXT NOT NULL,
    kept_start_s REAL,
    kept_end_s  REAL,
    created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS uploads (
    clip_id     INTEGER PRIMARY KEY REFERENCES clips(id),
    youtube_id  TEXT NOT NULL,
    uploaded_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS channels (
    channel_id TEXT PRIMARY KEY,
    name       TEXT,
    added_at   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS jobs (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    type       TEXT NOT NULL DEFAULT 'process',   -- process | render
    payload    TEXT NOT NULL,                     -- JSON: {url} or {clip_id, start, end}
    status     TEXT NOT NULL DEFAULT 'queued',    -- queued | running | done | failed
    error      TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
"""

# Video lifecycle:  queued -> downloaded -> transcribed -> analyzed -> done | failed
# Clip lifecycle:   rendered -> queued -> scheduled -> uploaded | failed


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


class StateDB:
    def __init__(self, db_path: Path):
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(db_path)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA foreign_keys=ON")
        self.conn.executescript(SCHEMA)
        self._migrate()
        self.conn.commit()

    def _migrate(self) -> None:
        """Add columns introduced after a DB was first created."""
        existing = {r["name"] for r in self.conn.execute("PRAGMA table_info(clips)")}
        for column in ("title", "description", "hashtags", "scores", "render_opts"):
            if column not in existing:
                self.conn.execute(f"ALTER TABLE clips ADD COLUMN {column} TEXT DEFAULT ''")
        video_cols = {r["name"] for r in self.conn.execute("PRAGMA table_info(videos)")}
        if "channel_name" not in video_cols:
            self.conn.execute("ALTER TABLE videos ADD COLUMN channel_name TEXT DEFAULT ''")
        if "process_seconds" not in video_cols:
            self.conn.execute("ALTER TABLE videos ADD COLUMN process_seconds REAL DEFAULT 0")

    def recover_stuck_videos(self) -> int:
        """Videos left mid-pipeline by a crash/force-close (downloaded,
        transcribed, analyzed) are marked failed so they're deletable and
        clearly not running. Returns how many were recovered."""
        cur = self.conn.execute(
            "UPDATE videos SET status = 'failed', updated_at = ? "
            "WHERE status IN ('downloaded', 'transcribed', 'analyzed')",
            (_now(),),
        )
        self.conn.commit()
        return cur.rowcount

    def set_process_seconds(self, video_id: str, seconds: float) -> None:
        self.conn.execute(
            "UPDATE videos SET process_seconds = ? WHERE video_id = ?", (round(seconds, 1), video_id)
        )
        self.conn.commit()

    def delete_video(self, video_id: str) -> None:
        """Remove a video and its clips/rejections/uploads from the DB."""
        self.conn.execute(
            "DELETE FROM uploads WHERE clip_id IN (SELECT id FROM clips WHERE video_id = ?)",
            (video_id,),
        )
        self.conn.execute("DELETE FROM clips WHERE video_id = ?", (video_id,))
        self.conn.execute("DELETE FROM rejections WHERE video_id = ?", (video_id,))
        self.conn.execute("DELETE FROM videos WHERE video_id = ?", (video_id,))
        self.conn.commit()

    # ---- videos -------------------------------------------------------

    def video_status(self, video_id: str) -> str | None:
        row = self.conn.execute(
            "SELECT status FROM videos WHERE video_id = ?", (video_id,)
        ).fetchone()
        return row["status"] if row else None

    def upsert_video(
        self, video_id: str, channel_id: str = "", title: str = "", channel_name: str = ""
    ) -> None:
        self.conn.execute(
            """INSERT INTO videos (video_id, channel_id, title, channel_name, status, created_at, updated_at)
               VALUES (?, ?, ?, ?, 'queued', ?, ?)
               ON CONFLICT(video_id) DO UPDATE SET
                 title = CASE WHEN excluded.title != '' THEN excluded.title ELSE videos.title END,
                 channel_name = CASE WHEN excluded.channel_name != '' THEN excluded.channel_name ELSE videos.channel_name END""",
            (video_id, channel_id, title, channel_name, _now(), _now()),
        )
        self.conn.commit()

    def set_video_status(self, video_id: str, status: str) -> None:
        self.conn.execute(
            "UPDATE videos SET status = ?, updated_at = ? WHERE video_id = ?",
            (status, _now(), video_id),
        )
        self.conn.commit()

    def videos_with_status(self, *statuses: str) -> list[sqlite3.Row]:
        marks = ",".join("?" * len(statuses))
        return self.conn.execute(
            f"SELECT * FROM videos WHERE status IN ({marks}) ORDER BY created_at",
            statuses,
        ).fetchall()

    def known_video_ids(self) -> set[str]:
        return {r["video_id"] for r in self.conn.execute("SELECT video_id FROM videos")}

    # ---- clips --------------------------------------------------------

    def add_clip(
        self,
        video_id: str,
        start: float,
        end: float,
        score: int,
        hook: str,
        path: str = "",
        status: str = "rendered",
        title: str = "",
        description: str = "",
        hashtags: str = "",
        scores: str = "",
        render_opts: str = "",
    ) -> int | None:
        """Insert a clip; returns its id, or None if this exact clip already
        exists (the UNIQUE constraint is the last line of duplicate defense)."""
        try:
            cur = self.conn.execute(
                """INSERT INTO clips (video_id, start_s, end_s, score, hook, path, status,
                                      title, description, hashtags, scores, render_opts, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (video_id, round(start, 2), round(end, 2), score, hook, path, status,
                 title, description, hashtags, scores, render_opts, _now()),
            )
            self.conn.commit()
            return cur.lastrowid
        except sqlite3.IntegrityError:
            return None

    def clips_with_status(self, *statuses: str) -> list[sqlite3.Row]:
        marks = ",".join("?" * len(statuses))
        return self.conn.execute(
            f"SELECT * FROM clips WHERE status IN ({marks}) ORDER BY score DESC, created_at",
            statuses,
        ).fetchall()

    def set_clip(self, clip_id: int, **fields) -> None:
        cols = ", ".join(f"{k} = ?" for k in fields)
        self.conn.execute(
            f"UPDATE clips SET {cols} WHERE id = ?", (*fields.values(), clip_id)
        )
        self.conn.commit()

    def clips_for_video(self, video_id: str) -> list[sqlite3.Row]:
        return self.conn.execute(
            "SELECT * FROM clips WHERE video_id = ? ORDER BY score DESC", (video_id,)
        ).fetchall()

    # ---- duplicate audit trail ---------------------------------------

    def log_rejection(
        self,
        video_id: str,
        start: float,
        end: float,
        score: int,
        reason: str,
        kept_start: float | None = None,
        kept_end: float | None = None,
    ) -> None:
        self.conn.execute(
            """INSERT INTO rejections (video_id, start_s, end_s, score, reason,
                                       kept_start_s, kept_end_s, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (video_id, round(start, 2), round(end, 2), score, reason, kept_start, kept_end, _now()),
        )
        self.conn.commit()

    # ---- daily scheduling --------------------------------------------

    def count_scheduled_on(self, day: date | None = None) -> int:
        day_str = (day or date.today()).isoformat()
        return self.conn.execute(
            "SELECT COUNT(*) AS n FROM clips WHERE scheduled_for = ?", (day_str,)
        ).fetchone()["n"]

    def promote_queued_clips(self, daily_limit: int, day: date | None = None) -> list[sqlite3.Row]:
        """Promote queued clips into today's schedule, highest score first,
        never exceeding daily_limit for the day. Returns the promoted rows."""
        day_str = (day or date.today()).isoformat()
        slots = daily_limit - self.count_scheduled_on(day)
        if slots <= 0:
            return []
        rows = self.conn.execute(
            "SELECT * FROM clips WHERE status = 'queued' ORDER BY score DESC, created_at LIMIT ?",
            (slots,),
        ).fetchall()
        for row in rows:
            self.conn.execute(
                "UPDATE clips SET status = 'scheduled', scheduled_for = ? WHERE id = ?",
                (day_str, row["id"]),
            )
        self.conn.commit()
        return rows

    # ---- uploads (consumed by the future YouTube upload module) -------

    def record_upload(self, clip_id: int, youtube_id: str) -> None:
        self.conn.execute(
            "INSERT INTO uploads (clip_id, youtube_id, uploaded_at) VALUES (?, ?, ?)",
            (clip_id, youtube_id, _now()),
        )
        self.conn.execute("UPDATE clips SET status = 'uploaded' WHERE id = ?", (clip_id,))
        self.conn.commit()

    # ---- monitored channels --------------------------------------------

    def add_channel(self, channel_id: str, name: str = "") -> None:
        self.conn.execute(
            """INSERT INTO channels (channel_id, name, added_at) VALUES (?, ?, ?)
               ON CONFLICT(channel_id) DO UPDATE SET
                 name = CASE WHEN excluded.name != '' THEN excluded.name ELSE channels.name END""",
            (channel_id, name, _now()),
        )
        self.conn.commit()

    def remove_channel(self, channel_id: str) -> bool:
        cur = self.conn.execute("DELETE FROM channels WHERE channel_id = ?", (channel_id,))
        self.conn.commit()
        return cur.rowcount > 0

    def list_channels(self) -> list[sqlite3.Row]:
        return self.conn.execute("SELECT * FROM channels ORDER BY added_at").fetchall()

    # ---- job queue (used by the API server's worker) --------------------

    def add_job(self, type_: str, payload: str) -> int:
        cur = self.conn.execute(
            "INSERT INTO jobs (type, payload, created_at, updated_at) VALUES (?, ?, ?, ?)",
            (type_, payload, _now(), _now()),
        )
        self.conn.commit()
        return cur.lastrowid

    def claim_next_job(self) -> sqlite3.Row | None:
        """Atomically claim the oldest queued job (single-worker model)."""
        row = self.conn.execute(
            "SELECT * FROM jobs WHERE status = 'queued' ORDER BY id LIMIT 1"
        ).fetchone()
        if row is None:
            return None
        self.conn.execute(
            "UPDATE jobs SET status = 'running', updated_at = ? WHERE id = ?",
            (_now(), row["id"]),
        )
        self.conn.commit()
        return row

    def set_job(self, job_id: int, **fields) -> None:
        cols = ", ".join(f"{k} = ?" for k in fields)
        self.conn.execute(
            f"UPDATE jobs SET {cols}, updated_at = ? WHERE id = ?",
            (*fields.values(), _now(), job_id),
        )
        self.conn.commit()

    def get_job(self, job_id: int) -> sqlite3.Row | None:
        return self.conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()

    def list_jobs(self, limit: int = 50) -> list[sqlite3.Row]:
        return self.conn.execute(
            "SELECT * FROM jobs ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()

    def recover_interrupted_jobs(self) -> int:
        """Server-start crash recovery: anything left 'running' goes back to
        'queued' (the pipeline itself resumes from its last completed stage)."""
        cur = self.conn.execute(
            "UPDATE jobs SET status = 'queued', updated_at = ? WHERE status = 'running'",
            (_now(),),
        )
        self.conn.commit()
        return cur.rowcount

    def get_clip(self, clip_id: int) -> sqlite3.Row | None:
        return self.conn.execute("SELECT * FROM clips WHERE id = ?", (clip_id,)).fetchone()

    # ---- reporting ----------------------------------------------------

    def summary(self) -> dict:
        def count(sql: str) -> list[sqlite3.Row]:
            return self.conn.execute(sql).fetchall()

        return {
            "videos": count("SELECT status, COUNT(*) AS n FROM videos GROUP BY status"),
            "clips": count("SELECT status, COUNT(*) AS n FROM clips GROUP BY status"),
            "rejections": count("SELECT reason, COUNT(*) AS n FROM rejections GROUP BY reason"),
            "scheduled_today": self.count_scheduled_on(),
        }

    def close(self) -> None:
        self.conn.close()
