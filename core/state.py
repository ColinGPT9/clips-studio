"""SQLite state database.

Single source of truth for what has been seen, processed, rendered,
scheduled, and uploaded. Every pipeline stage commits its status here
BEFORE the next stage runs, so a crash at any point resumes cleanly and
nothing is ever reprocessed or double-uploaded.
"""

import json
import sqlite3
from contextlib import contextmanager
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

-- One row per publish attempt, kept OUT of the jobs table on purpose.
--
-- jobs rows are re-queued on startup by recover_interrupted_jobs(). For a
-- video that means "resume a stage"; for an upload it would mean posting the
-- same video to someone's channel a second time. A publish left running by a
-- crash has to become 'interrupted' and stop there, which is the opposite
-- recovery rule, so it needs its own table.
--
-- The Queue page also renders every jobs row, and publishing is meant to have
-- no footprint at all for people who never turn it on.
CREATE TABLE IF NOT EXISTS publish_jobs (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    -- Deliberately NOT a foreign key: a re-render deletes the clip row and
    -- inserts a new one with a new id (server/jobs.py::_rerender_clip), so a
    -- publish chained after a render has to re-find its clip by timestamps.
    clip_id      INTEGER NOT NULL,
    video_id     TEXT NOT NULL DEFAULT '',
    start_s      REAL NOT NULL DEFAULT 0,
    end_s        REAL NOT NULL DEFAULT 0,
    request      TEXT NOT NULL DEFAULT '{}',  -- JSON PublishRequest. No credentials.
    after_job_id INTEGER NOT NULL DEFAULT 0,  -- jobs.id of a render to wait for, 0 = none
    status       TEXT NOT NULL DEFAULT 'queued',
    error        TEXT NOT NULL DEFAULT '',
    youtube_id   TEXT NOT NULL DEFAULT '',
    created_at   TEXT NOT NULL,
    updated_at   TEXT NOT NULL
);

-- One row per clip per destination, for providers that publish to several
-- platforms from a single upload.
--
-- The older `uploads` table cannot hold this: its primary key is clip_id, so
-- one clip has exactly one upload row and a second destination would
-- overwrite the first. That was correct while YouTube was the only
-- destination. It is left exactly as it is, still owning the direct-YouTube
-- history, rather than migrated — the two paths keep their own records and
-- neither can corrupt the other.
--
-- Keyed the same way clip_translations is, for the same reason: one clip, N
-- targets, each with its own state and its own error.
CREATE TABLE IF NOT EXISTS clip_publishes (
    clip_id    INTEGER NOT NULL,
    platform   TEXT NOT NULL,               -- youtube | tiktok | instagram | ...
    provider   TEXT NOT NULL DEFAULT '',    -- which publisher delivered it
    -- Identity that outlives a re-render, matching uploads: applying edits
    -- gives the clip a new id, so clip_id alone cannot find this row again.
    video_id   TEXT NOT NULL DEFAULT '',
    start_s    REAL NOT NULL DEFAULT 0,
    end_s      REAL NOT NULL DEFAULT 0,
    -- queued | processing | published | failed | skipped
    -- 'skipped' is its own state on purpose: a platform the user ticked but
    -- has not connected is not a failure, and reporting it as one sends
    -- people hunting for a bug that is not there.
    state      TEXT NOT NULL DEFAULT 'queued',
    post_id    TEXT NOT NULL DEFAULT '',
    post_url   TEXT NOT NULL DEFAULT '',
    error      TEXT NOT NULL DEFAULT '',
    -- The provider's own id for the whole fan-out, so every row from one
    -- operation can be found together and retried as a group.
    request_id TEXT NOT NULL DEFAULT '',
    -- When this post is due, for a run spread across days.
    scheduled_for TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (clip_id, platform)
);

-- Small key/value store for app-level flags that must outlive a restart.
-- Currently just the queue's paused state: stopping the queue is a decision
-- the user made, so a crash or a reboot must not quietly resume processing.
CREATE TABLE IF NOT EXISTS app_state (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

-- A livestream an integration (the OBS plugin) handed over after it ended. One
-- row per stream: session_id comes from the integration, so a repeated request,
-- or the same stream reported again after a crash, lands on this row instead of
-- creating a second job.
CREATE TABLE IF NOT EXISTS streams (
    session_id     TEXT PRIMARY KEY,
    source         TEXT NOT NULL DEFAULT '',  -- which integration, e.g. 'obs'
    platform       TEXT NOT NULL DEFAULT '',  -- twitch | youtube | kick
    channel        TEXT NOT NULL DEFAULT '',
    started_at     REAL NOT NULL DEFAULT 0,   -- unix seconds, as the integration saw them
    ended_at       REAL NOT NULL DEFAULT 0,
    preset         TEXT NOT NULL DEFAULT '',
    state          TEXT NOT NULL DEFAULT 'waiting_for_vod',
    vod_url        TEXT NOT NULL DEFAULT '',
    video_id       TEXT NOT NULL DEFAULT '',
    job_id         INTEGER NOT NULL DEFAULT 0,
    waiting_behind INTEGER NOT NULL DEFAULT 0,  -- other videos queued ahead when handed over
    error          TEXT NOT NULL DEFAULT '',
    next_check_at  REAL NOT NULL DEFAULT 0,     -- unix seconds; when to look for the VOD again
    created_at     TEXT NOT NULL,
    updated_at     TEXT NOT NULL
);

-- ---- Watched channels (server/automation.py) -------------------------------
-- A channel whose new videos are queued without anyone pasting a link. Only
-- the detection facts and the publish decision live here: once a video has a
-- job, the job and clip_publishes are the truth about what became of it.

CREATE TABLE IF NOT EXISTS watches (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    platform        TEXT NOT NULL,                -- youtube | twitch | kick
    channel_key     TEXT NOT NULL,                -- UC id | Twitch login | Kick slug
    name            TEXT NOT NULL DEFAULT '',
    enabled         INTEGER NOT NULL DEFAULT 1,
    options         TEXT NOT NULL DEFAULT '{}',   -- JSON: job options, as the Generate bar sends them
    publish         TEXT NOT NULL DEFAULT '{}',   -- JSON: mode, platforms, per_day, gap_hours, ...
    backlog         TEXT NOT NULL DEFAULT 'newest',  -- all | newest | day | none
    min_minutes     REAL NOT NULL DEFAULT 3,      -- shorter videos (Shorts) are not clipped
    last_ok_poll_at REAL NOT NULL DEFAULT 0,      -- unix seconds; 0 = never looked yet
    next_poll_at    REAL NOT NULL DEFAULT 0,
    last_error      TEXT NOT NULL DEFAULT '',
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL,
    UNIQUE (platform, channel_key)
);

-- One row per video a watch has ever seen. video_id is unique across ALL
-- watches, so a video found twice (two watches, a re-added watch, a feed that
-- repeats itself) is still one row and at most one job.
CREATE TABLE IF NOT EXISTS watch_items (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    watch_id      INTEGER NOT NULL,
    platform      TEXT NOT NULL DEFAULT '',
    video_id      TEXT NOT NULL UNIQUE,
    url           TEXT NOT NULL DEFAULT '',
    title         TEXT NOT NULL DEFAULT '',
    published_at  REAL NOT NULL DEFAULT 0,
    detected_at   REAL NOT NULL DEFAULT 0,
    -- baseline | new | not_ready | waiting | queued | skipped | error
    state         TEXT NOT NULL DEFAULT 'new',
    reason        TEXT NOT NULL DEFAULT '',
    job_id        INTEGER NOT NULL DEFAULT 0,
    next_check_at REAL NOT NULL DEFAULT 0,
    -- '' (not decided yet) | off | ask | publishing | done
    publish_state TEXT NOT NULL DEFAULT '',
    publish_error TEXT NOT NULL DEFAULT '',
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_watch_items_watch ON watch_items(watch_id, id);

-- ---- Clipping campaigns (campaigns/) -----------------------------------------
-- Discord ids are snowflakes kept as TEXT: they overflow JavaScript numbers.
-- Secrets (OAuth tokens, webhook tokens) are NEVER here: core/secrets only.
-- No foreign keys: like publish_jobs, a submission outlives a re-render that
-- deletes its clip row.

CREATE TABLE IF NOT EXISTS campaigns (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    name                 TEXT NOT NULL,
    enabled              INTEGER NOT NULL DEFAULT 1,
    archived             INTEGER NOT NULL DEFAULT 0,
    method               TEXT NOT NULL DEFAULT 'discord_assisted', -- discord_assisted | discord_webhook | website
    guild_id             TEXT NOT NULL DEFAULT '',
    guild_name           TEXT NOT NULL DEFAULT '',
    channel_id           TEXT NOT NULL DEFAULT '',
    channel_name         TEXT NOT NULL DEFAULT '',
    channel_link         TEXT NOT NULL DEFAULT '',                 -- as pasted
    destination_id       INTEGER NOT NULL DEFAULT 0,               -- discord_destinations.id (discord_webhook)
    website_url          TEXT NOT NULL DEFAULT '',                 -- https only (website)
    requirements         TEXT NOT NULL DEFAULT '',                 -- free text, shown at review
    platforms            TEXT NOT NULL DEFAULT '[]',               -- JSON ["tiktok", ...]; [] = any
    required_accounts    TEXT NOT NULL DEFAULT '{}',               -- JSON {"tiktok": "handle"} (no @)
    trust_account        INTEGER NOT NULL DEFAULT 0,               -- 1: an account Clips Kitty can't see passes
    required_hashtags    TEXT NOT NULL DEFAULT '[]',               -- JSON, no '#'
    required_mentions    TEXT NOT NULL DEFAULT '[]',               -- JSON, no '@'
    required_text        TEXT NOT NULL DEFAULT '',                 -- phrase the caption must contain
    min_seconds          REAL NOT NULL DEFAULT 0,
    max_seconds          REAL NOT NULL DEFAULT 0,                  -- 0 = no limit
    deadline             TEXT NOT NULL DEFAULT '',                 -- UTC RFC 3339; '' = none
    approval_required    INTEGER NOT NULL DEFAULT 1,
    instructions         TEXT NOT NULL DEFAULT '',
    command_name         TEXT NOT NULL DEFAULT '',                 -- "submit" (no slash); '' = plain message
    command_args         TEXT NOT NULL DEFAULT '[]',               -- JSON [{"name","value"}]; value is a template
    url_argument         TEXT NOT NULL DEFAULT '',                 -- the arg whose value is {url}
    extra_fields         TEXT NOT NULL DEFAULT '[]',               -- JSON [{"name","label","default","required"}]
    message_template     TEXT NOT NULL DEFAULT '{url}',
    thread_name_template TEXT NOT NULL DEFAULT '{title}',          -- forum destinations
    review_first         INTEGER NOT NULL DEFAULT 0,               -- applied to every new submission (off for new campaigns)
    filters              TEXT NOT NULL DEFAULT '{}',               -- JSON {"plugins":[],"modes":[],"creator_ids":[]}
    notes                TEXT NOT NULL DEFAULT '',
    created_at           TEXT NOT NULL,
    updated_at           TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS discord_destinations (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    kind          TEXT NOT NULL DEFAULT 'webhook',   -- webhook (the only kind)
    source        TEXT NOT NULL DEFAULT 'pasted',    -- pasted | oauth
    label         TEXT NOT NULL DEFAULT '',
    guild_id      TEXT NOT NULL DEFAULT '',
    guild_name    TEXT NOT NULL DEFAULT '',
    channel_id    TEXT NOT NULL DEFAULT '',
    webhook_id    TEXT NOT NULL DEFAULT '',          -- not secret; the token is in core/secrets
    webhook_name  TEXT NOT NULL DEFAULT '',
    is_forum      INTEGER NOT NULL DEFAULT 0,
    thread_id     TEXT NOT NULL DEFAULT '',          -- post into an existing thread
    state         TEXT NOT NULL DEFAULT 'connected', -- connected | gone | denied
    last_error    TEXT NOT NULL DEFAULT '',
    last_sent_at  REAL NOT NULL DEFAULT 0,           -- unix seconds; spacing sends
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS campaign_submissions (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    campaign_id        INTEGER NOT NULL,
    -- The post's lasting identity, as in clip_publishes: never changes after insert.
    video_id           TEXT NOT NULL,
    start_s            REAL NOT NULL,                 -- round(x, 2), as clips do
    end_s              REAL NOT NULL,
    platform           TEXT NOT NULL,
    clip_id            INTEGER NOT NULL DEFAULT 0,    -- last known id; follows re-renders
    clip_title         TEXT NOT NULL DEFAULT '',      -- snapshot for lists and {title}
    provider           TEXT NOT NULL DEFAULT '',      -- woopsocial | upload_post | youtube | manual (copied from the row found)
    publish_job_id     INTEGER NOT NULL DEFAULT 0,    -- direct YouTube upload being followed
    origin             TEXT NOT NULL DEFAULT 'editor',-- editor | later | publish_all | watch
    origin_ref         TEXT NOT NULL DEFAULT '',      -- "watch:3/item:41"
    method             TEXT NOT NULL DEFAULT 'discord_assisted', -- copied from the campaign; editable
    destination_id     INTEGER NOT NULL DEFAULT 0,
    overridden         TEXT NOT NULL DEFAULT '[]',    -- JSON: which of "method", "destination_id" the user set on this row
    hold               INTEGER NOT NULL DEFAULT 0,    -- 1 = saved for later
    review_first       INTEGER NOT NULL DEFAULT 0,
    status             TEXT NOT NULL DEFAULT 'waiting_publication',
    status_reason      TEXT NOT NULL DEFAULT '',      -- why it is in this state (shown)
    due_at             TEXT NOT NULL DEFAULT '',      -- UTC; the followed post's due time (posts.due_time / publish_at)
    post_url           TEXT NOT NULL DEFAULT '',
    url_source         TEXT NOT NULL DEFAULT '',      -- provider | youtube | manual
    post_id            TEXT NOT NULL DEFAULT '',      -- provider post id / youtube_id
    account            TEXT NOT NULL DEFAULT '',      -- posting account when known
    posted_at          TEXT NOT NULL DEFAULT '',      -- when first seen live (UTC)
    checks             TEXT NOT NULL DEFAULT '[]',    -- JSON [{"name","result","message"}]
    message_text       TEXT NOT NULL DEFAULT '',      -- user override of the message or the whole assisted line; '' = render
    extra_values       TEXT NOT NULL DEFAULT '{}',    -- JSON {extra field name: value}
    arg_values         TEXT NOT NULL DEFAULT '{}',    -- JSON {command argument name: value}; per-row overrides
    attempts           INTEGER NOT NULL DEFAULT 0,
    next_attempt_at    REAL NOT NULL DEFAULT 0,       -- unix seconds: next resolve check (waiting rows) or next send (ready)
    sending_started_at REAL NOT NULL DEFAULT 0,
    sent_at            TEXT NOT NULL DEFAULT '',
    sent_by            TEXT NOT NULL DEFAULT '',      -- webhook | user | '' (not known: an ambiguous send or a restart)
    discord_message_id TEXT NOT NULL DEFAULT '',
    discord_channel_id TEXT NOT NULL DEFAULT '',
    opened_at          TEXT NOT NULL DEFAULT '',
    confirmed_at       TEXT NOT NULL DEFAULT '',
    confirmation_note  TEXT NOT NULL DEFAULT '',
    error              TEXT NOT NULL DEFAULT '',
    created_at         TEXT NOT NULL,
    updated_at         TEXT NOT NULL,
    UNIQUE (campaign_id, video_id, start_s, end_s, platform)
);
-- One URL is submitted to one campaign once, whatever path created the rows.
CREATE UNIQUE INDEX IF NOT EXISTS idx_campaign_submissions_url
    ON campaign_submissions(campaign_id, post_url)
    WHERE post_url != '' AND status NOT IN ('cancelled', 'expired');
CREATE INDEX IF NOT EXISTS idx_campaign_submissions_status
    ON campaign_submissions(status, next_attempt_at);
CREATE INDEX IF NOT EXISTS idx_campaign_submissions_clip
    ON campaign_submissions(video_id, start_s, end_s);
"""

# Columns set_stream() may change. Names are interpolated into SQL, so they come
# from this list and never from a caller.
STREAM_COLUMNS = frozenset({
    "source", "platform", "channel", "started_at", "ended_at", "preset", "state",
    "vod_url", "video_id", "job_id", "waiting_behind", "error", "next_check_at",
})

# Same rule for the watch tables.
WATCH_COLUMNS = frozenset({
    "name", "enabled", "options", "publish", "backlog", "min_minutes",
    "last_ok_poll_at", "next_poll_at", "last_error",
})
WATCH_ITEM_COLUMNS = frozenset({
    "watch_id", "platform", "url", "title", "published_at", "detected_at", "state",
    "reason", "job_id", "next_check_at", "publish_state", "publish_error",
    "retries", "retry_at", "publish_attempts", "publish_retry_at", "delivery_retries",
    "source_freed", "requested", "orientation", "chosen_clips",
})

# Same rule for the campaign tables. A submission's identity (campaign, video,
# window, platform) is left out on purpose: it is the dedupe key, fixed at
# insert, so no update can move a row onto another post.
CAMPAIGN_COLUMNS = frozenset({
    "name", "enabled", "archived", "method", "guild_id", "guild_name", "channel_id",
    "channel_name", "channel_link", "destination_id", "website_url", "requirements",
    "platforms", "required_accounts", "trust_account", "required_hashtags",
    "required_mentions", "required_text", "min_seconds", "max_seconds", "deadline",
    "approval_required", "instructions", "command_name", "command_args", "url_argument",
    "extra_fields", "message_template", "thread_name_template", "review_first", "filters",
    "notes",
})
DESTINATION_COLUMNS = frozenset({
    "kind", "source", "label", "guild_id", "guild_name", "channel_id", "webhook_id",
    "webhook_name", "is_forum", "thread_id", "state", "last_error", "last_sent_at",
})
SUBMISSION_COLUMNS = frozenset({
    "clip_id", "clip_title", "provider", "publish_job_id", "origin", "origin_ref", "method",
    "destination_id", "overridden", "hold", "review_first", "status", "status_reason",
    "due_at", "post_url", "url_source", "post_id", "account", "posted_at", "checks",
    "message_text", "extra_values", "arg_values", "attempts", "next_attempt_at",
    "sending_started_at", "sent_at", "sent_by", "discord_message_id", "discord_channel_id",
    "opened_at", "confirmed_at", "confirmation_note", "error",
})
SUBMISSION_IDENTITY = ("campaign_id", "video_id", "start_s", "end_s", "platform")
#: A submission whose campaign is on and not archived: the only ones the
#: background step follows or sends.
_LIVE_CAMPAIGN = "campaign_id IN (SELECT id FROM campaigns WHERE enabled = 1 AND archived = 0)"


def _cells(fields: dict) -> dict:
    """Column values as sqlite takes them: a list or dict goes in as JSON, so
    a caller can pass `platforms=["tiktok"]` as readily as its text."""
    return {k: json.dumps(v) if isinstance(v, (list, dict)) else v for k, v in fields.items()}


def _checked(fields: dict, allowed: frozenset, what: str) -> dict:
    bad = set(fields) - allowed
    if bad:
        raise ValueError(f"unknown {what} columns: {sorted(bad)}")
    return _cells(fields)

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
        for column in ("title", "description", "hashtags", "scores", "render_opts",
                       "exported_at"):
            if column not in existing:
                self.conn.execute(f"ALTER TABLE clips ADD COLUMN {column} TEXT DEFAULT ''")
        if "exported_at" not in existing:
            # Exports were only logged as a learning signal before the mark
            # existed. Carry that history over once, so a clip exported last
            # week doesn't read as never exported.
            self.conn.execute(
                "UPDATE clips SET exported_at = ("
                " SELECT MAX(f.created_at) FROM clip_feedback f"
                " WHERE f.clip_id = clips.id AND f.action = 'exported')"
                " WHERE EXISTS (SELECT 1 FROM clip_feedback f"
                " WHERE f.clip_id = clips.id AND f.action = 'exported')"
            )
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
        # Where the video came from, when known: the link it was clipped from
        # (a pasted link, or the original of an uploaded file if the user gave
        # it) and its platform. Clips reach them through video_id. Blank means
        # not known; nothing is ever made up to fill them.
        if "source_url" not in video_cols:
            self.conn.execute("ALTER TABLE videos ADD COLUMN source_url TEXT DEFAULT ''")
        if "source_platform" not in video_cols:
            self.conn.execute("ALTER TABLE videos ADD COLUMN source_platform TEXT DEFAULT ''")
        # The game(s) the platform says the video shows, as JSON
        # [{"name", "start", "end"}], so a re-run from the cached file still
        # knows them (analysis/gaming.py). Blank: not known.
        if "games" not in video_cols:
            self.conn.execute("ALTER TABLE videos ADD COLUMN games TEXT DEFAULT ''")
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
        rejection_cols = {r["name"] for r in self.conn.execute("PRAGMA table_info(rejections)")}
        if "subscores" not in rejection_cols:
            # The per-signal breakdown, not just the total. Without it a run
            # that produced nothing can say "nothing scored high enough" but
            # not WHY -- and the difference between "gameplay, so the
            # person-on-screen term was zero" and "a quiet talking-head video"
            # is the whole of what a user needs told.
            self.conn.execute("ALTER TABLE rejections ADD COLUMN subscores TEXT DEFAULT ''")
        video_outcome_cols = {r["name"] for r in self.conn.execute("PRAGMA table_info(videos)")}
        if "outcome" not in video_outcome_cols:
            # Why a finished run produced the clips it did (or did not). JSON,
            # following the render_opts precedent: a blob column beats a table
            # for something only ever read whole.
            self.conn.execute("ALTER TABLE videos ADD COLUMN outcome TEXT DEFAULT ''")
        upload_cols = {r["name"] for r in self.conn.execute("PRAGMA table_info(uploads)")}
        for column, decl in (
            # Identity that outlives a re-render. clip_id alone cannot find
            # this row again, because applying edits gives the clip a new id.
            ("video_id", "TEXT NOT NULL DEFAULT ''"),
            ("start_s", "REAL NOT NULL DEFAULT 0"),
            ("end_s", "REAL NOT NULL DEFAULT 0"),
            ("title", "TEXT NOT NULL DEFAULT ''"),
            ("privacy", "TEXT NOT NULL DEFAULT ''"),  # what we asked for
            # What YouTube actually applied. These differ when an unaudited API
            # project has its uploads locked private, which is unappealable and
            # invisible unless it is read back and compared.
            ("actual_privacy", "TEXT NOT NULL DEFAULT ''"),
            ("publish_at", "TEXT NOT NULL DEFAULT ''"),  # UTC RFC3339, '' = published now
            ("channel_id", "TEXT NOT NULL DEFAULT ''"),
            ("channel_title", "TEXT NOT NULL DEFAULT ''"),
            ("thumbnail_set", "INTEGER NOT NULL DEFAULT 0"),
            ("playlist_id", "TEXT NOT NULL DEFAULT ''"),
            ("state", "TEXT NOT NULL DEFAULT 'uploaded'"),
            ("error", "TEXT NOT NULL DEFAULT ''"),
            ("checked_at", "TEXT NOT NULL DEFAULT ''"),
            # The description as it went up, hashtags included, so a rule about
            # what a post has to say can be checked against what it said.
            ("description", "TEXT NOT NULL DEFAULT ''"),
        ):
            if column not in upload_cols:
                self.conn.execute(f"ALTER TABLE uploads ADD COLUMN {column} {decl}")
        # When a scheduled post is actually due. Without it the app forgot the
        # times the moment it sent them, so a run spread over eight days could
        # not be shown, only guessed at from the platform's own dashboard.
        publish_cols = {
            r["name"] for r in self.conn.execute("PRAGMA table_info(clip_publishes)")
        }
        if "scheduled_for" not in publish_cols:
            self.conn.execute(
                "ALTER TABLE clip_publishes ADD COLUMN scheduled_for TEXT NOT NULL DEFAULT ''"
            )
        if "media_id" not in publish_cols:
            # The provider's id for the uploaded clip, written the moment the
            # upload finishes and before the post is created. WoopSocial takes
            # no reference of ours, so if Clips Kitty stops between their
            # accepting a post and this row learning its id, the media is the
            # only way to find that post again instead of sending it twice.
            self.conn.execute(
                "ALTER TABLE clip_publishes ADD COLUMN media_id TEXT NOT NULL DEFAULT ''"
            )
        for column in (
            # The caption as it was sent, so a hashtag or a mention a post has
            # to carry can be checked against the post itself.
            "caption",
            # The visibility that was asked for: public | unlisted | private |
            # friends | default (no override, so the provider's own, public).
            # '' means the row was written before this was kept.
            "visibility",
        ):
            if column not in publish_cols:
                self.conn.execute(
                    f"ALTER TABLE clip_publishes ADD COLUMN {column} TEXT NOT NULL DEFAULT ''"
                )
        creator_cols = {r["name"] for r in self.conn.execute("PRAGMA table_info(creators)")}
        if "default_branding_id" not in creator_cols:
            self.conn.execute("ALTER TABLE creators ADD COLUMN default_branding_id INTEGER")
        if "gaming_layout" not in creator_cols:
            # Gaming / Reaction: the webcam and game area a person set for this
            # creator in the editor, used for their next videos (gaming/run.py).
            self.conn.execute("ALTER TABLE creators ADD COLUMN gaming_layout TEXT")
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
        item_cols = {r["name"] for r in self.conn.execute("PRAGMA table_info(watch_items)")}
        for column, decl in (
            # A hands-off channel runs on a PC nobody is looking at, so a
            # failure is tried again rather than left for a person. These count
            # the tries and say when the next one is due.
            ("retries", "INTEGER NOT NULL DEFAULT 0"),           # processing runs retried
            ("retry_at", "REAL NOT NULL DEFAULT 0"),
            ("publish_attempts", "INTEGER NOT NULL DEFAULT 0"),  # publishes that could not start
            ("publish_retry_at", "REAL NOT NULL DEFAULT 0"),
            ("delivery_retries", "INTEGER NOT NULL DEFAULT 0"),  # re-sends of rejected posts
            # 1: the download was deleted once its clips were published. 2: there
            # was none to delete. Either way the folder is not scanned for it
            # again on every tick.
            ("source_freed", "INTEGER NOT NULL DEFAULT 0"),
            # 1: a person pressed Clip this, so the job is theirs, not the
            # watch running unattended (a signed-in plan may treat them apart).
            ("requested", "INTEGER NOT NULL DEFAULT 0"),
            # vertical | horizontal | '' (not known): whether the video has a
            # portrait version, read when it is checked for readiness.
            ("orientation", "TEXT NOT NULL DEFAULT ''"),
            # The clips the first publish chose, as a JSON list of
            # [start_s, end_s], so a re-send keeps them even when a forced
            # re-run (a rater's new scores) changed the order since. '' until
            # then, and for items published before it was kept.
            ("chosen_clips", "TEXT NOT NULL DEFAULT ''"),
        ):
            if column not in item_cols:
                self.conn.execute(f"ALTER TABLE watch_items ADD COLUMN {column} {decl}")
        # Shorts were listed at first, with a Clip this button for something
        # that cannot be clipped, and some were recorded as skipped for being
        # too short. Watching now drops them before anything is recorded, so
        # the ones already listed and never queued go, once. Only once: done
        # on every start, a short-but-not-a-Short video would be forgotten and
        # found again at every check.
        cleared = self.conn.execute(
            "SELECT 1 FROM app_state WHERE key = 'watch_shorts_cleared'"
        ).fetchone()
        if cleared is None:
            self.conn.execute(
                "DELETE FROM watch_items WHERE job_id = 0 AND "
                "(url LIKE '%/shorts/%' OR (state = 'skipped' AND reason LIKE 'Shorter than%'))"
            )
            self.conn.execute(
                "INSERT INTO app_state (key, value) VALUES ('watch_shorts_cleared', '1')"
            )
        # Channels added with the old `python main.py channels add` become
        # watches once, switched off: they were set up for the CLI daemon, and
        # having the app act on them is the user's call, not a migration's.
        imported = self.conn.execute(
            "SELECT 1 FROM app_state WHERE key = 'watches_imported'"
        ).fetchone()
        if imported is None:
            now = _now()
            self.conn.execute(
                "INSERT OR IGNORE INTO watches "
                "(platform, channel_key, name, enabled, created_at, updated_at) "
                "SELECT 'youtube', channel_id, COALESCE(name, ''), 0, ?, ? FROM channels",
                (now, now),
            )
            self.conn.execute(
                "INSERT INTO app_state (key, value) VALUES ('watches_imported', '1')"
            )

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

    # ---- re-render support ------------------------------------------------
    #
    # A re-render does not UPDATE a clip, it deletes the row and inserts a new
    # one (server/jobs.py::_rerender_clip), because the new timestamps would
    # otherwise collide with UNIQUE(video_id, start_s, end_s). That gives the
    # clip a NEW id, and foreign_keys is ON, so any row still pointing at the
    # old id makes the DELETE fail outright:
    #
    #     sqlite3.IntegrityError: FOREIGN KEY constraint failed
    #
    # Which meant translating a clip in the Subtitles tab and then pressing
    # "Apply edits" failed the render job with a message about nothing the
    # user had done. Publishing adds a second way in, via uploads.clip_id.
    # These two lift the dependent rows out of the way and put them back on
    # the new id.

    _CLIP_DEPENDENTS = ("uploads", "clip_translations", "clip_feedback")

    def detach_clip_rows(self, clip_id: int) -> dict[str, list[dict]]:
        """Read and remove every row that REFERENCES this clip, so the clip
        row itself can be deleted. Returns them for reattach_clip_rows()."""
        saved: dict[str, list[dict]] = {}
        for table in self._CLIP_DEPENDENTS:
            rows = self.conn.execute(
                f"SELECT * FROM {table} WHERE clip_id = ?", (clip_id,)
            ).fetchall()
            if rows:
                saved[table] = [dict(r) for r in rows]
                self.conn.execute(f"DELETE FROM {table} WHERE clip_id = ?", (clip_id,))
        self.conn.commit()
        return saved

    def reattach_clip_rows(self, new_clip_id: int, saved: dict[str, list[dict]]) -> None:
        """Put rows from detach_clip_rows() back, pointed at the new clip id.

        Best-effort per row: a re-render that changed the clip's boundaries can
        make an old row invalid, and losing one translation is a far better
        outcome than failing a render the user is waiting on."""
        for table, rows in saved.items():
            for row in rows:
                row = {**row, "clip_id": new_clip_id}
                columns = ", ".join(row)
                placeholders = ", ".join("?" for _ in row)
                try:
                    self.conn.execute(
                        f"INSERT OR REPLACE INTO {table} ({columns}) VALUES ({placeholders})",
                        tuple(row.values()),
                    )
                except sqlite3.Error as e:
                    print(f"Could not restore {table} row for clip {new_clip_id}: {e}")
        self.conn.commit()

    def follow_clip_id(self, old_id: int, new_id: int) -> None:
        """A re-rendered clip keeps its posts: clip_publishes rows move to the
        new id. Not a foreign key, so they were never in the way of the delete,
        and they used to stay behind on an id nothing asks for again.

        Only the id moves. video_id, start_s and end_s describe the post that
        went out, and a re-render that moved the window did not move the post.
        A platform the new id already has a row for keeps that row.

        Campaign submissions follow too: their identity is the post's, and
        their clip_id is only the last known id (for lists and {title})."""
        self.conn.execute(
            "UPDATE OR IGNORE clip_publishes SET clip_id = ? WHERE clip_id = ?",
            (int(new_id), int(old_id)),
        )
        self.conn.execute(
            "UPDATE campaign_submissions SET clip_id = ? WHERE clip_id = ?",
            (int(new_id), int(old_id)),
        )
        self.conn.commit()

    def follow_chosen_clip(self, video_id: str, old: tuple[float, float], new: tuple[float, float]) -> None:
        """A re-render that moved a clip to a new window: a watched video's
        saved first-publish choice (watch_items.chosen_clips, a JSON list of
        [start_s, end_s]) follows it there. A clip trimmed after its post was
        rejected is still one the first publish chose, so a re-send sends it."""
        before = [round(float(old[0]), 2), round(float(old[1]), 2)]
        after = [round(float(new[0]), 2), round(float(new[1]), 2)]
        if before == after:
            return
        rows = self.conn.execute(
            "SELECT id, chosen_clips FROM watch_items WHERE video_id = ? AND chosen_clips != ''",
            (video_id,),
        ).fetchall()
        for row in rows:
            try:
                saved = json.loads(row["chosen_clips"])
            except ValueError:
                continue
            if not isinstance(saved, list):
                continue
            moved = False
            for i, window in enumerate(saved):
                try:
                    same = [round(float(window[0]), 2), round(float(window[1]), 2)] == before
                except (IndexError, KeyError, TypeError, ValueError):
                    continue
                if same:
                    saved[i], moved = after, True
            if moved:
                self.set_watch_item(row["id"], chosen_clips=json.dumps(saved))

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

    # ---- gaming / reaction layout remembered per creator ----------------

    def creator_of_video(self, video_id: str) -> int | None:
        row = self.conn.execute("SELECT creator_id FROM videos WHERE video_id = ?", (video_id,)).fetchone()
        return row["creator_id"] if row and row["creator_id"] is not None else None

    def creator_gaming_layout(self, creator_id: int) -> dict | None:
        row = self.conn.execute(
            "SELECT gaming_layout FROM creators WHERE creator_id = ?", (creator_id,)
        ).fetchone()
        if not row or not row["gaming_layout"]:
            return None
        try:
            value = json.loads(row["gaming_layout"])
        except ValueError:
            return None
        return value if isinstance(value, dict) else None

    def set_creator_gaming_layout(self, creator_id: int, layout: dict | None) -> None:
        """None forgets it: their next videos find the webcam again."""
        self.conn.execute(
            "UPDATE creators SET gaming_layout = ? WHERE creator_id = ?",
            (json.dumps(layout) if layout is not None else None, creator_id),
        )
        self.conn.commit()

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

    def shorts_made(self, video_id: str) -> bool:
        """Whether a 9:16 Shorts run has finished on this video.

        What "already processed" means for a Shorts request. The status alone
        can't say: a Longform run marks the video done too, and made no Shorts
        (#98). A Shorts run records its outcome; videos from before that are
        known by a clip without a Longform profile."""
        row = self.conn.execute(
            "SELECT status, outcome FROM videos WHERE video_id = ?", (video_id,)
        ).fetchone()
        if row is None or row["status"] != "done":
            return False
        if row["outcome"]:
            # A Longform-only Sports run stores the match's report on its own
            # (longform/process.py _record_match); that isn't a Shorts run.
            try:
                recorded = json.loads(row["outcome"])
            except (ValueError, TypeError):
                recorded = {}
            if not (isinstance(recorded, dict) and recorded.get("longform_only")):
                return True
        return self.conn.execute(
            """SELECT 1 FROM clips WHERE video_id = ?
               AND COALESCE(render_opts, '') NOT LIKE '%"profile"%' LIMIT 1""",
            (video_id,),
        ).fetchone() is not None

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

    def set_video_games(self, video_id: str, games: list) -> None:
        """What the platform says was played. Only a known list is written:
        an empty one never overwrites what an earlier run recorded."""
        if games:
            self.conn.execute("UPDATE videos SET games = ? WHERE video_id = ?",
                              (json.dumps(games), video_id))
            self.conn.commit()

    def video_games(self, video_id: str) -> list:
        row = self.conn.execute("SELECT games FROM videos WHERE video_id = ?", (video_id,)).fetchone()
        try:
            games = json.loads(row["games"]) if row and row["games"] else []
        except ValueError:
            return []
        return games if isinstance(games, list) else []

    def set_video_source(self, video_id: str, url: str = "", platform: str = "") -> None:
        """Record where a video came from. Only known values are written: a
        blank never overwrites what an earlier run recorded."""
        if url:
            self.conn.execute("UPDATE videos SET source_url = ? WHERE video_id = ?", (url, video_id))
        if platform:
            self.conn.execute("UPDATE videos SET source_platform = ? WHERE video_id = ?",
                              (platform, video_id))
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
        subscores: dict | None = None,
    ) -> None:
        self.conn.execute(
            """INSERT INTO rejections (video_id, start_s, end_s, score, reason,
                                       kept_start_s, kept_end_s, subscores, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (video_id, round(start, 2), round(end, 2), score, reason, kept_start, kept_end,
             json.dumps(subscores) if subscores else "", _now()),
        )
        self.conn.commit()

    # ---- why a run produced what it produced -----------------------------

    def set_outcome(self, video_id: str, outcome: dict) -> None:
        """Record why this video ended with the clips it did.

        Read by the UI when there are none, so "no clips" can be explained
        where the user is looking rather than in a log they never open.
        """
        self.conn.execute(
            "UPDATE videos SET outcome = ? WHERE video_id = ?",
            (json.dumps(outcome), video_id),
        )
        self.conn.commit()

    def get_outcome(self, video_id: str) -> dict:
        row = self.conn.execute(
            "SELECT outcome FROM videos WHERE video_id = ?", (video_id,)
        ).fetchone()
        if not row or not row["outcome"]:
            return {}
        try:
            return json.loads(row["outcome"])
        except (ValueError, TypeError):
            return {}

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

    # ---- uploads -------------------------------------------------------

    def record_upload(self, clip_id: int, youtube_id: str) -> None:
        """Record a bare upload. Kept for core/scheduler.py's daemon path.

        An UPSERT rather than an INSERT: uploads.clip_id is the primary key, so
        publishing a clip a second time used to fail on the unique constraint.
        """
        self.conn.execute(
            "INSERT INTO uploads (clip_id, youtube_id, uploaded_at) VALUES (?, ?, ?) "
            "ON CONFLICT(clip_id) DO UPDATE SET "
            "youtube_id = excluded.youtube_id, uploaded_at = excluded.uploaded_at",
            (clip_id, youtube_id, _now()),
        )
        self.conn.execute("UPDATE clips SET status = 'uploaded' WHERE id = ?", (clip_id,))
        self.conn.commit()

    def record_publish(self, clip_id: int, fields: dict) -> None:
        """Record a full publish result against a clip.

        `fields` carries whatever of the widened uploads columns the caller
        knows; unknown keys are ignored rather than raising, so a provider that
        cannot report (say) a channel handle does not have to fake one.

        The clip's status becomes 'uploaded' for a scheduled video too — it HAS
        been uploaded; only its going-live is in the future, and that is
        YouTube's business, not a state this app has to track.
        """
        allowed = {r["name"] for r in self.conn.execute("PRAGMA table_info(uploads)")}
        row = {k: v for k, v in fields.items() if k in allowed and k != "clip_id"}
        row.setdefault("youtube_id", "")
        row["uploaded_at"] = _now()

        columns = ", ".join(["clip_id", *row])
        placeholders = ", ".join("?" for _ in range(len(row) + 1))
        updates = ", ".join(f"{k} = excluded.{k}" for k in row)
        self.conn.execute(
            f"INSERT INTO uploads ({columns}) VALUES ({placeholders}) "
            f"ON CONFLICT(clip_id) DO UPDATE SET {updates}",
            (clip_id, *row.values()),
        )
        self.conn.execute("UPDATE clips SET status = 'uploaded' WHERE id = ?", (clip_id,))
        self.conn.commit()

    def get_upload(self, clip_id: int) -> sqlite3.Row | None:
        return self.conn.execute(
            "SELECT * FROM uploads WHERE clip_id = ?", (clip_id,)
        ).fetchone()

    def set_upload_check(self, clip_id: int, *, checked_at: str, actual_privacy: str | None = None,
                         state: str | None = None, error: str | None = None) -> None:
        """Write what reading an upload back from YouTube found, and nothing
        else: a field left None is left as it is.

        A plain UPDATE, not record_publish, which stamps uploaded_at afresh and
        would make a video uploaded on Monday read as uploaded at the check.
        """
        fields = {"checked_at": checked_at}
        for name, value in (("actual_privacy", actual_privacy), ("state", state), ("error", error)):
            if value is not None:
                fields[name] = value
        sets = ", ".join(f"{name} = ?" for name in fields)
        self.conn.execute(
            f"UPDATE uploads SET {sets} WHERE clip_id = ?", (*fields.values(), clip_id)
        )
        self.conn.commit()

    def recent_uploads(self, limit: int = 50) -> list[sqlite3.Row]:
        return self.conn.execute(
            "SELECT u.*, c.hook FROM uploads u LEFT JOIN clips c ON c.id = u.clip_id "
            "ORDER BY u.uploaded_at DESC LIMIT ?",
            (limit,),
        ).fetchall()

    # ---- multi-platform publishes --------------------------------------

    def record_clip_publish(self, clip_id: int, platform: str, fields: dict) -> None:
        """Record one destination's outcome for one clip.

        Same tolerance as record_publish: unknown keys are dropped rather than
        raising, so a platform that returns no post URL does not have to
        invent one. Called once per platform per fan-out, and again on every
        status poll as a platform moves queued -> processing -> published.

        created_at marks when the clip was last sent to this platform. A
        status poll leaves it alone. A write that sends the clip again passes
        `created_at` (the publish-time writers do), so a post's waiting windows
        count from this send rather than the first one (server/posts.py
        due_time).
        """
        allowed = {r["name"] for r in self.conn.execute("PRAGMA table_info(clip_publishes)")}
        row = {
            k: v for k, v in fields.items()
            if k in allowed and k not in ("clip_id", "platform", "created_at")
        }
        row["updated_at"] = _now()
        sent = str(fields.get("created_at") or "")

        columns = ", ".join(["clip_id", "platform", "created_at", *row])
        placeholders = ", ".join("?" for _ in range(len(row) + 3))
        updates = [f"{k} = excluded.{k}" for k in row]
        if sent:
            updates.append("created_at = excluded.created_at")
        self.conn.execute(
            f"INSERT INTO clip_publishes ({columns}) VALUES ({placeholders}) "
            f"ON CONFLICT(clip_id, platform) DO UPDATE SET {', '.join(updates)}",
            (clip_id, platform, sent or _now(), *row.values()),
        )
        self.conn.commit()

    def clip_publishes(self, clip_id: int) -> list[sqlite3.Row]:
        return self.conn.execute(
            "SELECT * FROM clip_publishes WHERE clip_id = ? ORDER BY platform",
            (clip_id,),
        ).fetchall()

    def publishes_for_request(self, request_id: str) -> list[sqlite3.Row]:
        """Every destination from one fan-out, so a retry can find its group."""
        return self.conn.execute(
            "SELECT * FROM clip_publishes WHERE request_id = ? ORDER BY platform",
            (request_id,),
        ).fetchall()

    def recent_clip_publishes(self, limit: int = 50) -> list[sqlite3.Row]:
        return self.conn.execute(
            "SELECT p.*, c.hook FROM clip_publishes p "
            "LEFT JOIN clips c ON c.id = p.clip_id "
            "ORDER BY p.updated_at DESC LIMIT ?",
            (limit,),
        ).fetchall()

    # ---- publish jobs --------------------------------------------------

    def add_publish_job(
        self,
        clip_id: int,
        request: str,
        *,
        video_id: str = "",
        start_s: float = 0.0,
        end_s: float = 0.0,
        after_job_id: int = 0,
    ) -> int:
        now = _now()
        cur = self.conn.execute(
            "INSERT INTO publish_jobs "
            "(clip_id, video_id, start_s, end_s, request, after_job_id, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (clip_id, video_id, start_s, end_s, request, after_job_id, now, now),
        )
        self.conn.commit()
        return int(cur.lastrowid)

    def claim_next_publish_job(self) -> sqlite3.Row | None:
        """Take the oldest queued publish whose render (if any) has finished.

        A job waiting on a render that failed or was cancelled is failed here
        rather than left queued forever — there is no file to upload, and a row
        that never moves looks like a hang.
        """
        rows = self.conn.execute(
            "SELECT * FROM publish_jobs WHERE status = 'queued' ORDER BY id"
        ).fetchall()
        for row in rows:
            if row["after_job_id"]:
                dependency = self.conn.execute(
                    "SELECT status FROM jobs WHERE id = ?", (row["after_job_id"],)
                ).fetchone()
                if dependency is None:
                    self.finish_publish_job(row["id"], "failed", error="The render job vanished.")
                    continue
                if dependency["status"] in ("queued", "running"):
                    continue  # still rendering; look at the next one
                if dependency["status"] != "done":
                    self.finish_publish_job(
                        row["id"],
                        "failed",
                        error=f"The render {dependency['status']}, so there was nothing to upload.",
                    )
                    continue
            self.conn.execute(
                "UPDATE publish_jobs SET status = 'running', updated_at = ? WHERE id = ?",
                (_now(), row["id"]),
            )
            self.conn.commit()
            return self.conn.execute(
                "SELECT * FROM publish_jobs WHERE id = ?", (row["id"],)
            ).fetchone()
        return None

    def finish_publish_job(
        self, job_id: int, status: str, *, error: str = "", youtube_id: str = ""
    ) -> None:
        self.conn.execute(
            "UPDATE publish_jobs SET status = ?, error = ?, youtube_id = ?, updated_at = ? "
            "WHERE id = ?",
            (status, error[:500], youtube_id, _now(), job_id),
        )
        self.conn.commit()

    def get_publish_job(self, job_id: int) -> sqlite3.Row | None:
        return self.conn.execute(
            "SELECT * FROM publish_jobs WHERE id = ?", (job_id,)
        ).fetchone()

    def active_publish_job_for_clip(self, clip_id: int) -> sqlite3.Row | None:
        return self.conn.execute(
            "SELECT * FROM publish_jobs WHERE clip_id = ? AND status IN ('queued', 'running') "
            "ORDER BY id DESC LIMIT 1",
            (clip_id,),
        ).fetchone()

    def set_publish_job_clip(self, job_id: int, clip_id: int) -> None:
        """Re-point a job at the clip row a re-render just created."""
        self.conn.execute(
            "UPDATE publish_jobs SET clip_id = ?, updated_at = ? WHERE id = ?",
            (clip_id, _now(), job_id),
        )
        self.conn.commit()

    def recover_running_publish_jobs(self) -> int:
        """Mark uploads that a crash interrupted, and never retry them.

        The opposite of recover_interrupted_jobs(). We cannot tell from here
        whether YouTube finished receiving the file, so retrying risks posting
        the video twice — the user is asked to check their channel instead.
        """
        cur = self.conn.execute(
            "UPDATE publish_jobs SET status = 'interrupted', updated_at = ?, "
            "error = 'Clips Kitty closed while this was uploading. Check your "
            "channel before trying again — it may have finished.' "
            "WHERE status = 'running'",
            (_now(),),
        )
        self.conn.commit()
        return cur.rowcount

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

    # ---- streams handed over by integrations -------------------------------

    def insert_stream(self, session_id: str, **fields) -> bool:
        """Create the row for a stream. False if this session already exists,
        which is how a repeated request stays a single job."""
        bad = set(fields) - STREAM_COLUMNS
        if bad:
            raise ValueError(f"unknown stream columns: {sorted(bad)}")
        now = _now()
        row = {"session_id": session_id, "created_at": now, "updated_at": now, **fields}
        cur = self.conn.execute(
            f"INSERT OR IGNORE INTO streams ({', '.join(row)}) "
            f"VALUES ({', '.join('?' * len(row))})",
            tuple(row.values()),
        )
        self.conn.commit()
        return cur.rowcount == 1

    def get_stream(self, session_id: str) -> sqlite3.Row | None:
        return self.conn.execute(
            "SELECT * FROM streams WHERE session_id = ?", (session_id,)
        ).fetchone()

    def set_stream(self, session_id: str, **fields) -> None:
        bad = set(fields) - STREAM_COLUMNS
        if bad:
            raise ValueError(f"unknown stream columns: {sorted(bad)}")
        if not fields:
            return
        assignments = ", ".join(f"{column} = ?" for column in fields)
        self.conn.execute(
            f"UPDATE streams SET {assignments}, updated_at = ? WHERE session_id = ?",
            (*fields.values(), _now(), session_id),
        )
        self.conn.commit()

    def streams_due(self, now: float) -> list[sqlite3.Row]:
        """Streams still waiting for their VOD whose next look is due."""
        return self.conn.execute(
            "SELECT * FROM streams WHERE state = 'waiting_for_vod' AND next_check_at <= ? "
            "ORDER BY next_check_at",
            (now,),
        ).fetchall()

    # ---- watched channels ----------------------------------------------------

    def insert_watch(self, platform: str, channel_key: str, **fields) -> int | None:
        """Create a watch. None if this channel is already watched."""
        bad = set(fields) - WATCH_COLUMNS
        if bad:
            raise ValueError(f"unknown watch columns: {sorted(bad)}")
        now = _now()
        row = {"platform": platform, "channel_key": channel_key,
               "created_at": now, "updated_at": now, **fields}
        cur = self.conn.execute(
            f"INSERT OR IGNORE INTO watches ({', '.join(row)}) "
            f"VALUES ({', '.join('?' * len(row))})",
            tuple(row.values()),
        )
        self.conn.commit()
        return cur.lastrowid if cur.rowcount == 1 else None

    def get_watch(self, watch_id: int) -> sqlite3.Row | None:
        return self.conn.execute("SELECT * FROM watches WHERE id = ?", (watch_id,)).fetchone()

    def find_watch(self, platform: str, channel_key: str) -> sqlite3.Row | None:
        return self.conn.execute(
            "SELECT * FROM watches WHERE platform = ? AND channel_key = ?",
            (platform, channel_key),
        ).fetchone()

    def list_watches(self) -> list[sqlite3.Row]:
        return self.conn.execute("SELECT * FROM watches ORDER BY id").fetchall()

    def set_watch(self, watch_id: int, **fields) -> None:
        bad = set(fields) - WATCH_COLUMNS
        if bad:
            raise ValueError(f"unknown watch columns: {sorted(bad)}")
        if not fields:
            return
        assignments = ", ".join(f"{column} = ?" for column in fields)
        self.conn.execute(
            f"UPDATE watches SET {assignments}, updated_at = ? WHERE id = ?",
            (*fields.values(), _now(), watch_id),
        )
        self.conn.commit()

    def delete_watch(self, watch_id: int) -> bool:
        """Stop watching and forget what it saw. Jobs and clips it produced are
        untouched: they belong to the library now, not to the watch."""
        self.conn.execute("DELETE FROM watch_items WHERE watch_id = ?", (watch_id,))
        cur = self.conn.execute("DELETE FROM watches WHERE id = ?", (watch_id,))
        self.conn.commit()
        return cur.rowcount > 0

    def watches_due(self, now: float) -> list[sqlite3.Row]:
        return self.conn.execute(
            "SELECT * FROM watches WHERE enabled = 1 AND next_poll_at <= ? ORDER BY next_poll_at",
            (now,),
        ).fetchall()

    def insert_watch_item(self, video_id: str, **fields) -> bool:
        """Record a video a watch found. False if any watch already has it,
        which is what keeps a repeated or doubly-found video to one job."""
        bad = set(fields) - WATCH_ITEM_COLUMNS
        if bad:
            raise ValueError(f"unknown watch item columns: {sorted(bad)}")
        now = _now()
        row = {"video_id": video_id, "created_at": now, "updated_at": now, **fields}
        cur = self.conn.execute(
            f"INSERT OR IGNORE INTO watch_items ({', '.join(row)}) "
            f"VALUES ({', '.join('?' * len(row))})",
            tuple(row.values()),
        )
        self.conn.commit()
        return cur.rowcount == 1

    def get_watch_item(self, item_id: int) -> sqlite3.Row | None:
        return self.conn.execute(
            "SELECT * FROM watch_items WHERE id = ?", (item_id,)
        ).fetchone()

    def watch_item_ids(self) -> set[str]:
        return {r[0] for r in self.conn.execute("SELECT video_id FROM watch_items")}

    def set_watch_item(self, item_id: int, **fields) -> None:
        bad = set(fields) - WATCH_ITEM_COLUMNS
        if bad:
            raise ValueError(f"unknown watch item columns: {sorted(bad)}")
        if not fields:
            return
        assignments = ", ".join(f"{column} = ?" for column in fields)
        self.conn.execute(
            f"UPDATE watch_items SET {assignments}, updated_at = ? WHERE id = ?",
            (*fields.values(), _now(), item_id),
        )
        self.conn.commit()

    def watch_items(
        self, *, watch_id: int | None = None, states: tuple[str, ...] = (), limit: int = 200
    ) -> list[sqlite3.Row]:
        """Newest first, optionally for one watch and in some states."""
        where, args = [], []
        if watch_id is not None:
            where.append("watch_id = ?")
            args.append(watch_id)
        if states:
            where.append(f"state IN ({','.join('?' * len(states))})")
            args.extend(states)
        clause = f"WHERE {' AND '.join(where)} " if where else ""
        return self.conn.execute(
            f"SELECT * FROM watch_items {clause}ORDER BY id DESC LIMIT ?", (*args, limit)
        ).fetchall()

    def get_clip(self, clip_id: int) -> sqlite3.Row | None:
        return self.conn.execute("SELECT * FROM clips WHERE id = ?", (clip_id,)).fetchone()

    # ---- clipping campaigns (campaigns/) ---------------------------------------
    #
    # Plain storage with column allowlists, like the watch tables. The rules
    # (campaigns.model.clean_campaign) and the status changes
    # (campaigns.statemachine) live in campaigns/, not here.

    def _write(self, sql: str, args) -> sqlite3.Cursor:
        """One write, committed. A write the database refuses (a NOT NULL,
        the URL index) is rolled back before the error goes on: sqlite3 had
        opened a transaction for it, and left open it would hold the write
        lock and every other connection would read "database is locked".

        Inside transaction() the write is left to it: committed with the
        others, or rolled back with them."""
        if getattr(self, "_in_transaction", False):
            return self.conn.execute(sql, args)
        try:
            cur = self.conn.execute(sql, args)
        except sqlite3.Error:
            self.conn.rollback()
            raise
        self.conn.commit()
        return cur

    @contextmanager
    def transaction(self):
        """Campaign writes made as one: every one of them, or none (moving a
        submission to another campaign cancels one row and attaches another).
        Only the campaign store's writes (_write) join it; a method that
        commits by itself must not be called inside. Nested, it is the outer
        one."""
        if getattr(self, "_in_transaction", False):
            yield
            return
        if self.conn.in_transaction:
            self.conn.commit()
        self.conn.execute("BEGIN IMMEDIATE")
        self._in_transaction = True
        try:
            yield
        except BaseException:
            self._in_transaction = False
            self.conn.rollback()
            raise
        self._in_transaction = False
        self.conn.commit()

    def _insert(self, table: str, row: dict) -> int:
        now = _now()
        row = {**row, "created_at": now, "updated_at": now}
        cur = self._write(
            f"INSERT INTO {table} ({', '.join(row)}) VALUES ({', '.join('?' * len(row))})",
            tuple(row.values()),
        )
        return int(cur.lastrowid)

    def _update(self, table: str, row_id: int, fields: dict) -> bool:
        if not fields:
            return False
        assignments = ", ".join(f"{column} = ?" for column in fields)
        cur = self._write(
            f"UPDATE {table} SET {assignments}, updated_at = ? WHERE id = ?",
            (*fields.values(), _now(), int(row_id)),
        )
        return cur.rowcount == 1

    def insert_campaign(self, name: str, **fields) -> int:
        fields = _checked(fields, CAMPAIGN_COLUMNS - {"name"}, "campaign")
        return self._insert("campaigns", {"name": name, **fields})

    def get_campaign(self, campaign_id: int) -> sqlite3.Row | None:
        return self.conn.execute(
            "SELECT * FROM campaigns WHERE id = ?", (int(campaign_id),)
        ).fetchone()

    def list_campaigns(self, *, archived: bool = False) -> list[sqlite3.Row]:
        """The campaigns in the order they were made. Archived ones only when
        `archived` is true, and then with the others."""
        clause = "" if archived else "WHERE archived = 0 "
        return self.conn.execute(f"SELECT * FROM campaigns {clause}ORDER BY id").fetchall()

    def set_campaign(self, campaign_id: int, **fields) -> None:
        self._update("campaigns", campaign_id, _checked(fields, CAMPAIGN_COLUMNS, "campaign"))

    def delete_campaign(self, campaign_id: int) -> str:
        """Delete a campaign nothing was ever submitted to ("deleted").
        Otherwise it is archived, so its submissions keep a campaign to
        belong to ("archived"). "" when there is no such campaign."""
        if self.get_campaign(campaign_id) is None:
            return ""
        used = self.conn.execute(
            "SELECT 1 FROM campaign_submissions WHERE campaign_id = ? LIMIT 1", (int(campaign_id),)
        ).fetchone()
        if used is not None:
            self.set_campaign(campaign_id, archived=1)
            return "archived"
        # Through _write, so queue.remove_campaign's transaction holds it too.
        self._write("DELETE FROM campaigns WHERE id = ?", (int(campaign_id),))
        return "deleted"

    def insert_destination(self, **fields) -> int:
        return self._insert(
            "discord_destinations", _checked(fields, DESTINATION_COLUMNS, "destination")
        )

    def get_destination(self, destination_id: int) -> sqlite3.Row | None:
        return self.conn.execute(
            "SELECT * FROM discord_destinations WHERE id = ?", (int(destination_id),)
        ).fetchone()

    def list_destinations(self) -> list[sqlite3.Row]:
        return self.conn.execute("SELECT * FROM discord_destinations ORDER BY id").fetchall()

    def set_destination(self, destination_id: int, **fields) -> None:
        self._update(
            "discord_destinations", destination_id,
            _checked(fields, DESTINATION_COLUMNS, "destination"),
        )

    def delete_destination(self, destination_id: int) -> bool:
        cur = self.conn.execute(
            "DELETE FROM discord_destinations WHERE id = ?", (int(destination_id),)
        )
        self.conn.commit()
        return cur.rowcount > 0

    def insert_submission(self, campaign_id: int, video_id: str, start_s: float, end_s: float,
                          platform: str, **fields) -> int | None:
        """A new submission of one post to one campaign, or None when that
        post is already submitted to it (the identity clashed).

        Only an identity clash is ignored: ON CONFLICT names the identity, so
        any other constraint (a NOT NULL, the URL index) still raises. A row
        always starts without a URL; a URL goes through evaluation, which
        checks it against the campaign's other rows first.
        """
        if "post_url" in fields:
            raise ValueError("post_url is set through evaluation, never at insert")
        fields = _checked(fields, SUBMISSION_COLUMNS, "submission")
        now = _now()
        row = {
            "campaign_id": int(campaign_id), "video_id": video_id,
            "start_s": round(float(start_s), 2), "end_s": round(float(end_s), 2),
            "platform": platform, **fields, "created_at": now, "updated_at": now,
        }
        cur = self._write(
            f"INSERT INTO campaign_submissions ({', '.join(row)}) "
            f"VALUES ({', '.join('?' * len(row))}) "
            f"ON CONFLICT({', '.join(SUBMISSION_IDENTITY)}) DO NOTHING",
            tuple(row.values()),
        )
        return int(cur.lastrowid) if cur.rowcount == 1 else None

    def find_submission(self, campaign_id: int, video_id: str, start_s: float, end_s: float,
                        platform: str) -> sqlite3.Row | None:
        return self.conn.execute(
            "SELECT * FROM campaign_submissions WHERE campaign_id = ? AND video_id = ? "
            "AND start_s = ? AND end_s = ? AND platform = ?",
            (int(campaign_id), video_id, round(float(start_s), 2), round(float(end_s), 2), platform),
        ).fetchone()

    def get_submission(self, submission_id: int) -> sqlite3.Row | None:
        return self.conn.execute(
            "SELECT * FROM campaign_submissions WHERE id = ?", (int(submission_id),)
        ).fetchone()

    def active_submission_with_url(self, campaign_id: int, post_url: str, *,
                                   exclude_id: int = 0) -> sqlite3.Row | None:
        """Another row of this campaign that holds this URL and still counts
        (not cancelled or expired): the check before a URL is applied. The
        partial unique index is the net under it."""
        if not post_url:
            return None
        return self.conn.execute(
            "SELECT * FROM campaign_submissions WHERE campaign_id = ? AND post_url = ? "
            "AND status NOT IN ('cancelled', 'expired') AND id != ? ORDER BY id LIMIT 1",
            (int(campaign_id), post_url, int(exclude_id)),
        ).fetchone()

    def list_submissions(self, *, statuses: tuple[str, ...] = (), campaign_id: int | None = None,
                         clip_id: int | None = None, video_id: str | None = None,
                         limit: int = 200) -> list[sqlite3.Row]:
        """Newest first, filtered by whichever of these are given."""
        where, args = [], []
        if statuses:
            where.append(f"status IN ({','.join('?' * len(statuses))})")
            args.extend(statuses)
        for column, value in (("campaign_id", campaign_id), ("clip_id", clip_id),
                              ("video_id", video_id)):
            if value is not None:
                where.append(f"{column} = ?")
                args.append(value)
        clause = f"WHERE {' AND '.join(where)} " if where else ""
        return self.conn.execute(
            f"SELECT * FROM campaign_submissions {clause}ORDER BY id DESC LIMIT ?",
            (*args, int(limit)),
        ).fetchall()

    def set_submission(self, submission_id: int, *, expect: tuple[str, ...] = (),
                       match: dict | None = None, **fields) -> bool:
        """Change a submission. With `expect`, only while its status is one of
        those, and with `match`, only while those columns still hold those
        values (compare-and-set), so a route and the background step cannot
        both win a status change. True when the row changed.

        The identity columns are refused: a row never moves to another post.
        """
        fixed = set(fields) & set(SUBMISSION_IDENTITY)
        if fixed:
            raise ValueError(f"a submission's identity can't change: {sorted(fixed)}")
        fields = _checked(fields, SUBMISSION_COLUMNS, "submission")
        match = _checked(match or {}, SUBMISSION_COLUMNS, "submission")
        if not fields:
            return False
        assignments = ", ".join(f"{column} = ?" for column in fields)
        guard, args = "", []
        if expect:
            guard = f" AND status IN ({','.join('?' * len(expect))})"
            args = list(expect)
        for column, value in match.items():
            guard += f" AND {column} = ?"
            args.append(value)
        cur = self._write(
            f"UPDATE campaign_submissions SET {assignments}, updated_at = ? WHERE id = ?{guard}",
            (*fields.values(), _now(), int(submission_id), *args),
        )
        return cur.rowcount == 1

    def submissions_due(self, statuses: tuple[str, ...], *, now: float, limit: int = 100,
                        no_url_manual: bool = False) -> list[sqlite3.Row]:
        """Rows whose next check (or send) is due, the longest-waiting first.
        `no_url_manual` adds manual rows still without a link, which keep
        being followed so a post published later still resolves them. Rows
        of a campaign switched off or archived are left out (in SQL, so they
        never fill the window): nothing is followed for it while it is off."""
        parts, args = [], []
        if statuses:
            parts.append(f"status IN ({','.join('?' * len(statuses))})")
            args.extend(statuses)
        if no_url_manual:
            parts.append("(status = 'manual' AND post_url = '')")
        if not parts:
            return []
        return self.conn.execute(
            f"SELECT * FROM campaign_submissions WHERE ({' OR '.join(parts)}) "
            f"AND next_attempt_at <= ? AND {_LIVE_CAMPAIGN} ORDER BY next_attempt_at, id LIMIT ?",
            (*args, float(now), int(limit)),
        ).fetchall()

    def submissions_to_send(self, *, now: float, limit: int = 20) -> list[sqlite3.Row]:
        """Webhook rows ready to post and due, the longest-waiting first. A
        row saved for later, or of a campaign switched off or archived, is
        left out here, in SQL, so any number of them can never fill the
        window, and nothing is posted for a campaign the user turned off."""
        return self.conn.execute(
            "SELECT * FROM campaign_submissions WHERE status = 'ready' AND hold = 0 "
            f"AND method = 'discord_webhook' AND next_attempt_at <= ? AND {_LIVE_CAMPAIGN} "
            "ORDER BY next_attempt_at, id LIMIT ?",
            (float(now), int(limit)),
        ).fetchall()

    def submission_counts(self, *, campaign_id: int | None = None, clip_id: int | None = None,
                          video_id: str | None = None) -> list[sqlite3.Row]:
        """(status, hold, sent_by, n) over the submissions these filters
        pick: what the lists' counts and the To submit badge are made from."""
        where, args = [], []
        for column, value in (("campaign_id", campaign_id), ("clip_id", clip_id),
                              ("video_id", video_id)):
            if value is not None:
                where.append(f"{column} = ?")
                args.append(value)
        clause = f"WHERE {' AND '.join(where)} " if where else ""
        return self.conn.execute(
            f"SELECT status, hold, sent_by, COUNT(*) AS n FROM campaign_submissions {clause}"
            "GROUP BY status, hold, sent_by",
            args,
        ).fetchall()

    def recover_sending_submissions(self, reason: str, *, started_before: float | None = None) -> int:
        """Rows left `sending` become `awaiting_confirmation` with nobody
        recorded as the sender, and are never sent again by themselves: the
        message may have reached Discord, and a webhook has no way to ask.
        Every such row at startup; with `started_before`, only the ones whose
        send began before then (the sweep at the start of each pass)."""
        sql = ("UPDATE campaign_submissions SET status = 'awaiting_confirmation', sent_by = '', "
               "status_reason = ?, updated_at = ? WHERE status = 'sending'")
        args: list = [reason, _now()]
        if started_before is not None:
            sql += " AND sending_started_at < ?"
            args.append(float(started_before))
        cur = self.conn.execute(sql, args)
        self.conn.commit()
        return cur.rowcount

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
