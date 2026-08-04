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

-- ---- Creator intelligence (creator/ module) --------------------------------
-- A creator PROFILE is the person/group; platform ACCOUNTS are their channels
-- on YouTube/Twitch/Kick. Knowledge/events are structured facts extracted
-- from processed videos; feedback logs user actions on clips for later
-- preference learning. None of this affects processing when absent.

CREATE TABLE IF NOT EXISTS creators (
    creator_id       INTEGER PRIMARY KEY AUTOINCREMENT,
    display_name     TEXT NOT NULL,
    aliases          TEXT NOT NULL DEFAULT '[]',   -- JSON list of alternate names
    learning_enabled INTEGER NOT NULL DEFAULT 1,
    created_at       TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS platform_accounts (
    account_id          INTEGER PRIMARY KEY AUTOINCREMENT,
    creator_id          INTEGER NOT NULL REFERENCES creators(creator_id),
    platform            TEXT NOT NULL,             -- youtube | twitch | kick
    platform_account_id TEXT NOT NULL,             -- channel name/id on that platform
    username            TEXT NOT NULL DEFAULT '',
    display_name        TEXT NOT NULL DEFAULT '',
    UNIQUE (platform, platform_account_id)
);

-- times_seen/last_seen/last_video record how often the creator has actually
-- SAID this again, which is what makes a catchphrase a catchphrase and what
-- drives dropout: a fact nobody repeats goes dormant and stops scoring.
-- (last_used is different — that's when WE last put it in a prompt.)

CREATE TABLE IF NOT EXISTS creator_knowledge (
    knowledge_id   INTEGER PRIMARY KEY AUTOINCREMENT,
    creator_id     INTEGER NOT NULL REFERENCES creators(creator_id),
    knowledge_type TEXT NOT NULL,   -- topic | game | series | catchphrase | joke
                                    -- | collaborator | format
    information    TEXT NOT NULL,
    confidence     TEXT NOT NULL DEFAULT 'medium',  -- high | medium
    source_video   TEXT,
    created_at     TEXT NOT NULL,
    last_used      TEXT,
    times_seen     INTEGER NOT NULL DEFAULT 1,  -- times heard, across videos
    last_seen      TEXT,                        -- when it was last heard
    last_video     TEXT                         -- video that last reinforced it
);

CREATE TABLE IF NOT EXISTS creator_events (
    event_id       INTEGER PRIMARY KEY AUTOINCREMENT,
    creator_id     INTEGER NOT NULL REFERENCES creators(creator_id),
    event_name     TEXT NOT NULL,
    description    TEXT NOT NULL DEFAULT '',
    status         TEXT NOT NULL DEFAULT 'announced',  -- announced | in_progress
                                                       -- | completed | stale
    detected_date  TEXT NOT NULL,
    completed_date TEXT,
    source_video   TEXT
);

CREATE TABLE IF NOT EXISTS clip_feedback (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    creator_id INTEGER,
    clip_id    INTEGER,
    action     TEXT NOT NULL,   -- deleted | rerendered | timestamps_adjusted
                                -- | captions_edited | exported
    clip_meta  TEXT NOT NULL DEFAULT '{}',  -- JSON snapshot: score/subscores/duration
    created_at TEXT NOT NULL
);

-- ---- Watermark & branding (video_editor/watermark.py) ----------------------
-- A saved branding profile (Personal / YouTube / Twitch / …). `config` is a
-- JSON blob (type, text, font, size, colour, opacity, position, scale,
-- rotation, shadow, image_asset) so new fields need no migration. Image
-- assets are content-hashed files under data_dir/branding/assets/.

CREATE TABLE IF NOT EXISTS branding_profiles (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    name       TEXT NOT NULL,
    config     TEXT NOT NULL DEFAULT '{}',   -- JSON watermark config
    created_at TEXT NOT NULL
);

-- ---- Multilingual publishing (multilingual/ module) ------------------------
-- The machine translation of one clip's captions into one language, kept so
-- the creator can READ it and fix a bad line before it is written to a .srt
-- or painted permanently into a video. `edited` marks text a human approved,
-- which export prefers and re-translation must never overwrite.

-- Words the creator has ruled on for translation: `protect` keeps a term
-- exactly as written (channel name, sponsor, in-joke), `ignore` overrides an
-- auto-detected term that should be translated normally. creator_id NULL
-- applies everywhere, which is what a sponsor or handle usually wants.

CREATE TABLE IF NOT EXISTS creator_terms (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    creator_id INTEGER,                      -- NULL = every creator
    term       TEXT NOT NULL,
    rule       TEXT NOT NULL DEFAULT 'protect',  -- protect | ignore
    created_at TEXT NOT NULL,
    UNIQUE (creator_id, term)
);

CREATE TABLE IF NOT EXISTS clip_translations (
    clip_id    INTEGER NOT NULL REFERENCES clips(id),
    language   TEXT NOT NULL,                -- ISO code (multilingual.languages)
    lines      TEXT NOT NULL DEFAULT '[]',   -- JSON [{start, end, text}]
    post       TEXT NOT NULL DEFAULT '{}',   -- JSON {title, description, hashtags}
    edited     INTEGER NOT NULL DEFAULT 0,   -- 1 once a human has corrected it
    updated_at TEXT NOT NULL,
    PRIMARY KEY (clip_id, language)
);

-- Small key/value store for app-level flags that must outlive a restart.
-- Currently just the queue's paused state: stopping the queue is a decision
-- the user made, so a crash or a reboot must not quietly resume processing.
CREATE TABLE IF NOT EXISTS app_state (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
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
        if "creator_id" not in video_cols:
            self.conn.execute("ALTER TABLE videos ADD COLUMN creator_id INTEGER")
        if "duration" not in video_cols:
            # Source length in seconds. Processing cost scales with it, so the
            # queue's time estimate divides by this instead of assuming every
            # video takes the same hour (a 6h VOD and a 20min upload do not).
            self.conn.execute("ALTER TABLE videos ADD COLUMN duration REAL DEFAULT 0")
        job_cols = {r["name"] for r in self.conn.execute("PRAGMA table_info(jobs)")}
        for column, decl in (
            # User-defined queue order. Claiming orders by this, so reordering
            # is a position swap rather than rewriting ids.
            ("position", "INTEGER NOT NULL DEFAULT 0"),
            # Resolved once at enqueue so the queue UI can name a job, and the
            # duplicate guard can spot a video that is already waiting, without
            # re-parsing payload JSON on every read.
            ("video_id", "TEXT NOT NULL DEFAULT ''"),
            ("title", "TEXT NOT NULL DEFAULT ''"),
            # Set when crash recovery re-queues a job, so the UI can say the run
            # restarted rather than silently repeating it.
            ("interrupted", "INTEGER NOT NULL DEFAULT 0"),
            ("started_at", "TEXT NOT NULL DEFAULT ''"),
            ("finished_at", "TEXT NOT NULL DEFAULT ''"),
            ("attempts", "INTEGER NOT NULL DEFAULT 0"),
            ("log_path", "TEXT NOT NULL DEFAULT ''"),
        ):
            if column not in job_cols:
                self.conn.execute(f"ALTER TABLE jobs ADD COLUMN {column} {decl}")
        if "position" not in job_cols:
            # Existing jobs keep the order they already ran in.
            self.conn.execute("UPDATE jobs SET position = id")
        # Claiming runs on every worker tick; this is the index it wants.
        self.conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_jobs_queue ON jobs(status, position, id)"
        )
        creator_cols = {r["name"] for r in self.conn.execute("PRAGMA table_info(creators)")}
        if "default_branding_id" not in creator_cols:
            self.conn.execute("ALTER TABLE creators ADD COLUMN default_branding_id INTEGER")
        knowledge_cols = {
            r["name"] for r in self.conn.execute("PRAGMA table_info(creator_knowledge)")
        }
        if "times_seen" not in knowledge_cols:
            # Facts learned before repetition tracking start at 1 — heard once,
            # never confirmed — which is exactly how they should be treated.
            self.conn.execute(
                "ALTER TABLE creator_knowledge ADD COLUMN times_seen INTEGER NOT NULL DEFAULT 1"
            )
        if "last_seen" not in knowledge_cols:
            self.conn.execute("ALTER TABLE creator_knowledge ADD COLUMN last_seen TEXT")
        if "last_video" not in knowledge_cols:
            self.conn.execute("ALTER TABLE creator_knowledge ADD COLUMN last_video TEXT")

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
        self.conn.execute(
            "DELETE FROM clip_translations WHERE clip_id IN "
            "(SELECT id FROM clips WHERE video_id = ?)",
            (video_id,),
        )
        self.conn.execute("DELETE FROM clips WHERE video_id = ?", (video_id,))
        self.conn.execute("DELETE FROM rejections WHERE video_id = ?", (video_id,))
        self.conn.execute("DELETE FROM videos WHERE video_id = ?", (video_id,))
        self.conn.commit()

    def delete_clip(self, clip_id: int) -> str | None:
        """Remove ONE clip and its dependent rows. Returns the clip's file
        path (for the caller to delete on disk), or None if it didn't exist.

        The video and every other clip are untouched — this is a manual cull
        of a single clip the creator won't post."""
        row = self.conn.execute("SELECT path FROM clips WHERE id = ?", (clip_id,)).fetchone()
        if row is None:
            return None
        self.conn.execute("DELETE FROM uploads WHERE clip_id = ?", (clip_id,))
        self.conn.execute("DELETE FROM clip_translations WHERE clip_id = ?", (clip_id,))
        self.conn.execute("DELETE FROM clip_feedback WHERE clip_id = ?", (clip_id,))
        self.conn.execute("DELETE FROM clips WHERE id = ?", (clip_id,))
        self.conn.commit()
        return row["path"]

    def delete_creator(self, creator_id: int) -> dict:
        """Remove a creator profile and everything learned about them.

        Videos and clips are NEVER deleted — they are only unlinked, so a
        profile can be tidied away without losing footage. Any video left
        behind simply has no creator until one is detected again.
        """
        unlinked = self.conn.execute(
            "UPDATE videos SET creator_id = NULL WHERE creator_id = ?", (creator_id,)
        ).rowcount
        counts = {"videos_unlinked": unlinked}
        # Accounts/knowledge/events carry NOT NULL foreign keys, so they have
        # to go before the row they point at.
        for table in ("platform_accounts", "creator_knowledge", "creator_events",
                      "clip_feedback", "creator_terms"):
            counts[table] = self.conn.execute(
                f"DELETE FROM {table} WHERE creator_id = ?", (creator_id,)
            ).rowcount
        self.conn.execute("DELETE FROM creators WHERE creator_id = ?", (creator_id,))
        self.conn.commit()
        return counts

    # ---- translation glossary -----------------------------------------

    def set_term(self, creator_id: int | None, term: str, rule: str) -> None:
        """Rule one word for translation: 'protect' or 'ignore'."""
        self.conn.execute(
            "INSERT INTO creator_terms (creator_id, term, rule, created_at) "
            "VALUES (?, ?, ?, ?) "
            "ON CONFLICT(creator_id, term) DO UPDATE SET rule = excluded.rule",
            (creator_id, term.strip(), rule, _now()),
        )
        self.conn.commit()

    def clear_term(self, creator_id: int | None, term: str) -> None:
        """Drop the ruling, letting the automatic list decide again."""
        if creator_id is None:
            self.conn.execute(
                "DELETE FROM creator_terms WHERE creator_id IS NULL AND term = ?", (term.strip(),)
            )
        else:
            self.conn.execute(
                "DELETE FROM creator_terms WHERE creator_id = ? AND term = ?",
                (creator_id, term.strip()),
            )
        self.conn.commit()

    def terms_for(self, creator_id: int | None) -> list[sqlite3.Row]:
        """This creator's rulings plus the ones that apply to everyone."""
        return self.conn.execute(
            "SELECT term, rule FROM creator_terms WHERE creator_id IS NULL OR creator_id = ?"
            " ORDER BY term COLLATE NOCASE",
            (creator_id,),
        ).fetchall()

    # ---- translations -------------------------------------------------

    def save_translation(
        self, clip_id: int, language: str, lines: str, post: str = "{}", edited: bool = False
    ) -> None:
        """Store (or replace) one clip's translation into one language."""
        self.conn.execute(
            "INSERT INTO clip_translations (clip_id, language, lines, post, edited, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(clip_id, language) DO UPDATE SET "
            "lines = excluded.lines, post = excluded.post, "
            "edited = excluded.edited, updated_at = excluded.updated_at",
            (clip_id, language, lines, post, 1 if edited else 0, _now()),
        )
        self.conn.commit()

    def get_translation(self, clip_id: int, language: str) -> sqlite3.Row | None:
        return self.conn.execute(
            "SELECT * FROM clip_translations WHERE clip_id = ? AND language = ?",
            (clip_id, language),
        ).fetchone()

    def delete_translation(self, clip_id: int, language: str) -> None:
        """Discard a stored translation, so the next run translates it fresh.
        The way back out of a correction the creator no longer wants."""
        self.conn.execute(
            "DELETE FROM clip_translations WHERE clip_id = ? AND language = ?",
            (clip_id, language),
        )
        self.conn.commit()

    def translations_for(self, clip_id: int) -> list[sqlite3.Row]:
        return self.conn.execute(
            "SELECT * FROM clip_translations WHERE clip_id = ? ORDER BY language", (clip_id,)
        ).fetchall()

    # ---- branding profiles --------------------------------------------

    def list_branding(self) -> list[sqlite3.Row]:
        return self.conn.execute(
            "SELECT * FROM branding_profiles ORDER BY id"
        ).fetchall()

    def get_branding(self, profile_id: int) -> sqlite3.Row | None:
        return self.conn.execute(
            "SELECT * FROM branding_profiles WHERE id = ?", (profile_id,)
        ).fetchone()

    def add_branding(self, name: str, config: str) -> int:
        cur = self.conn.execute(
            "INSERT INTO branding_profiles (name, config, created_at) VALUES (?, ?, ?)",
            (name, config, _now()),
        )
        self.conn.commit()
        return cur.lastrowid

    def update_branding(self, profile_id: int, name: str, config: str) -> None:
        self.conn.execute(
            "UPDATE branding_profiles SET name = ?, config = ? WHERE id = ?",
            (name, config, profile_id),
        )
        self.conn.commit()

    def delete_branding(self, profile_id: int) -> None:
        self.conn.execute("DELETE FROM branding_profiles WHERE id = ?", (profile_id,))
        self.conn.commit()

    # ---- videos -------------------------------------------------------

    def video_status(self, video_id: str) -> str | None:
        row = self.conn.execute(
            "SELECT status FROM videos WHERE video_id = ?", (video_id,)
        ).fetchone()
        return row["status"] if row else None

    def upsert_video(
        self,
        video_id: str,
        channel_id: str = "",
        title: str = "",
        channel_name: str = "",
        duration: float = 0.0,
    ) -> None:
        self.conn.execute(
            """INSERT INTO videos (video_id, channel_id, title, channel_name, duration, status, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, 'queued', ?, ?)
               ON CONFLICT(video_id) DO UPDATE SET
                 title = CASE WHEN excluded.title != '' THEN excluded.title ELSE videos.title END,
                 channel_name = CASE WHEN excluded.channel_name != '' THEN excluded.channel_name ELSE videos.channel_name END,
                 duration = CASE WHEN excluded.duration > 0 THEN excluded.duration ELSE videos.duration END""",
            (video_id, channel_id, title, channel_name, round(duration, 1), _now(), _now()),
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

    def add_job(self, type_: str, payload: str, video_id: str = "", title: str = "") -> int:
        if type_ == "process":
            position = self.conn.execute(
                "SELECT COALESCE(MAX(position), 0) + 1 FROM jobs"
            ).fetchone()[0]
        else:
            # Re-renders and translations are seconds-to-minutes of work that
            # the user is sitting and waiting for, having just pressed Apply in
            # the editor. Behind a batch of hour-long videos they would look
            # broken, so they go to the FRONT of the waiting jobs. They cannot
            # starve video processing: each one is finite and only ever exists
            # because a person clicked something.
            row = self.conn.execute(
                "SELECT MIN(position) FROM jobs WHERE status = 'queued' AND type = 'process'"
            ).fetchone()
            ahead = row[0] if row and row[0] is not None else None
            if ahead is None:
                position = self.conn.execute(
                    "SELECT COALESCE(MAX(position), 0) + 1 FROM jobs"
                ).fetchone()[0]
            else:
                # Fractional-free: shift the video jobs back by one and take
                # the freed slot, so ordering stays plain integers.
                self.conn.execute(
                    "UPDATE jobs SET position = position + 1 "
                    "WHERE status = 'queued' AND position >= ?",
                    (ahead,),
                )
                position = ahead
        cur = self.conn.execute(
            """INSERT INTO jobs (type, payload, video_id, title, position, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (type_, payload, video_id, title, position, _now(), _now()),
        )
        self.conn.commit()
        return cur.lastrowid

    def finish_job(self, job_id: int, status: str, error: str = "") -> None:
        """Terminal state for a job, in one place.

        Clears `interrupted` on the way out — otherwise a job that was once
        crash-recovered would carry the badge for the rest of its life, long
        after the run that earned it succeeded."""
        self.conn.execute(
            "UPDATE jobs SET status = ?, error = ?, interrupted = 0, finished_at = ?, "
            "updated_at = ? WHERE id = ?",
            (status, error, _now(), _now(), job_id),
        )
        self.conn.commit()

    def claim_next_job(self) -> sqlite3.Row | None:
        """Atomically claim the next queued job (single-worker model).

        Ordered by position, not id: the user can reorder the queue, and a job
        moved to the front must genuinely run next."""
        row = self.conn.execute(
            "SELECT * FROM jobs WHERE status = 'queued' ORDER BY position, id LIMIT 1"
        ).fetchone()
        if row is None:
            return None
        self.conn.execute(
            "UPDATE jobs SET status = 'running', started_at = ?, updated_at = ? WHERE id = ?",
            (_now(), _now(), row["id"]),
        )
        self.conn.commit()
        return row

    def job_for_video(self, video_id: str, statuses: tuple[str, ...]) -> sqlite3.Row | None:
        """An existing job for this video in one of `statuses` — the guard
        against queueing the same video twice."""
        if not video_id:
            return None
        marks = ",".join("?" * len(statuses))
        return self.conn.execute(
            f"SELECT * FROM jobs WHERE video_id = ? AND status IN ({marks}) "
            "ORDER BY position, id LIMIT 1",
            (video_id, *statuses),
        ).fetchone()

    def set_job(self, job_id: int, **fields) -> None:
        cols = ", ".join(f"{k} = ?" for k in fields)
        self.conn.execute(
            f"UPDATE jobs SET {cols}, updated_at = ? WHERE id = ?",
            (*fields.values(), _now(), job_id),
        )
        self.conn.commit()

    def get_job(self, job_id: int) -> sqlite3.Row | None:
        return self.conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()

    def list_jobs(
        self, limit: int = 50, statuses: tuple[str, ...] | None = None
    ) -> list[sqlite3.Row]:
        if statuses:
            marks = ",".join("?" * len(statuses))
            return self.conn.execute(
                f"SELECT * FROM jobs WHERE status IN ({marks}) ORDER BY id DESC LIMIT ?",
                (*statuses, limit),
            ).fetchall()
        return self.conn.execute(
            "SELECT * FROM jobs ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()

    def queued_jobs(self) -> list[sqlite3.Row]:
        """Waiting jobs in the order they will actually be claimed."""
        return self.conn.execute(
            "SELECT * FROM jobs WHERE status = 'queued' ORDER BY position, id"
        ).fetchall()

    def recover_interrupted_jobs(self) -> int:
        """Server-start crash recovery: anything left 'running' goes back to
        'queued' (the pipeline itself resumes from its last completed stage).

        Flagged `interrupted` so the queue can say the run was restarted. The
        cached download and transcript are reused, so this is usually a resume
        rather than a full redo — but the user is told either way instead of
        watching a video silently begin again."""
        cur = self.conn.execute(
            "UPDATE jobs SET status = 'queued', interrupted = 1, attempts = attempts + 1, "
            "started_at = '', updated_at = ? WHERE status = 'running'",
            (_now(),),
        )
        self.conn.commit()
        return cur.rowcount

    # ---- app-level flags -------------------------------------------------

    def get_flag(self, key: str, default: str = "") -> str:
        row = self.conn.execute("SELECT value FROM app_state WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else default

    def set_flag(self, key: str, value: str) -> None:
        self.conn.execute(
            "INSERT INTO app_state (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )
        self.conn.commit()

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
