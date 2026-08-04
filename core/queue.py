"""Queue manager: the orchestration layer around the existing pipeline.

Clips Studio already ran jobs one at a time — a `jobs` row, a single worker
thread, and crash recovery (see server/jobs.py). What was missing is the part
a person needs to leave a batch running overnight: ordering, pausing, retrying,
and knowing how long it will take. That is what lives here.

Layering, deliberately one direction:

    Queue Manager (this module)
        -> Queue Item              a `jobs` row
        -> Processing Config       jobs.payload, a JSON snapshot
        -> Video Pipeline          core.pipeline.process_video
        -> Result / Error          job status + error + data/logs/job_N.log

This module knows about a StateDB and nothing else — no FastAPI, no UI. The
API layer translates HTTP to these calls, and the worker asks it whether it
may claim work. That keeps the queue testable and stops queue rules from
leaking into either end.

Invariants worth keeping:
  * One video at a time. The GPU is the bottleneck; two videos at once make
    both slower (ARCHITECTURE.md section 10).
  * A payload is a SNAPSHOT. Once a job runs, editing another job's settings
    cannot reach it.
  * Stopping never deletes. Pause survives a restart; the queue is still there.
  * A failure is contained to its own video; the queue carries on to the next.
"""

import json
from statistics import median

PAUSED_KEY = "queue_paused"

# How many videos may be waiting or running at once.
#
# Not a safety limit: the worker processes one video at a time, so memory does
# not grow with queue length, queued items are only SQLite rows, and prefetch
# fetches at most one video ahead — a long queue is slow, not unstable. This is
# a deliberate product choice to keep a batch to something a person can look at
# and predict. Raise it freely.
MAX_ACTIVE = 5

# What a video costs to process, when there is no history to go on. Roughly an
# hour per video is the observed order of magnitude; it is only ever used until
# the first real completion replaces it.
COLD_START_SECONDS = 3600.0

# Terminal states a job can be retried from.
RETRYABLE = ("failed", "cancelled")

# A video already sitting in one of these must not be queued a second time.
LIVE = ("queued", "running")


# ---- pause ----------------------------------------------------------------


def is_paused(db) -> bool:
    """Is the queue stopped?

    Defaults to STOPPED when the flag has never been set. A video costs about
    an hour, so adding one must not commit the user's evening before they have
    looked at the list — they stage a batch, check it, and press Start. Nothing
    in this app starts processing on its own."""
    return db.get_flag(PAUSED_KEY, "1") == "1"


def set_paused(db, paused: bool) -> None:
    """Stopping prevents the queue from CLAIMING new work; it never interrupts
    the video already running. Killing a job halfway would throw away an hour of
    GPU time for no reason — the user can cancel that one explicitly if they
    mean to. Stopping never deletes anything, and survives a restart."""
    db.set_flag(PAUSED_KEY, "1" if paused else "0")


# ---- enqueue --------------------------------------------------------------


def duplicate_of(db, video_id: str) -> int | None:
    """The id of a job already queued or running for this video, if any.

    The old single-video flow only guarded against re-processing something
    already `done`. With a real queue the likelier mistake is pasting the same
    link twice into a batch and quietly processing it twice."""
    row = db.job_for_video(video_id, LIVE)
    return row["id"] if row else None


def enqueue(db, type_: str, payload: dict, video_id: str = "", title: str = "") -> int:
    return db.add_job(type_, json.dumps(payload), video_id=video_id, title=title)


def active_count(db) -> int:
    """Videos waiting or running. Finished ones are history, not work."""
    return db.conn.execute(
        "SELECT COUNT(*) FROM jobs WHERE status IN ('queued','running') AND type = 'process'"
    ).fetchone()[0]


def capacity(db) -> int:
    """How many more videos may be added right now."""
    return max(0, MAX_ACTIVE - active_count(db))


# ---- ordering -------------------------------------------------------------


def move(db, job_id: int, delta: int) -> bool:
    """Move a queued job one place earlier (-1) or later (+1).

    Implemented as a swap with its neighbour rather than a reindex: two integer
    writes, no pass over the whole queue, and nothing to corrupt if the worker
    claims a job at the same moment. Only queued jobs move — the running one is
    already running, and finished ones are history."""
    if delta not in (-1, 1):
        return False
    row = db.get_job(job_id)
    if row is None or row["status"] != "queued":
        return False
    order = "DESC" if delta < 0 else "ASC"
    compare = "<" if delta < 0 else ">"
    neighbour = db.conn.execute(
        f"SELECT id, position FROM jobs WHERE status = 'queued' "
        f"AND (position, id) {compare} (?, ?) ORDER BY position {order}, id {order} LIMIT 1",
        (row["position"], row["id"]),
    ).fetchone()
    if neighbour is None:
        return False  # already at the end it was asked to move towards
    db.conn.execute(
        "UPDATE jobs SET position = ? WHERE id = ?", (neighbour["position"], row["id"])
    )
    db.conn.execute(
        "UPDATE jobs SET position = ? WHERE id = ?", (row["position"], neighbour["id"])
    )
    db.conn.commit()
    return True


# ---- item lifecycle -------------------------------------------------------


def remove(db, job_id: int) -> bool:
    """Drop a waiting job. The running job is not removable here — cancelling
    that one goes through core.cancel so the pipeline can stop cleanly."""
    row = db.get_job(job_id)
    if row is None or row["status"] != "queued":
        return False
    db.conn.execute("DELETE FROM jobs WHERE id = ?", (job_id,))
    db.conn.commit()
    return True


def retry(db, job_id: int) -> int | None:
    """Send a failed or cancelled job back to the end of the queue.

    The row is reused rather than copied. The queue is a work list, not a
    ledger: a retried video should appear once, waiting, not twice — as a
    failure sitting next to its own retry, which reads as two broken videos.
    Its settings are already on the row, so the retry runs with exactly the
    configuration the user chose. `attempts` keeps the count, and the run's
    log file is keyed by job id, so this attempt appends to the same file and
    the earlier failure stays readable."""
    row = db.get_job(job_id)
    if row is None or row["status"] not in RETRYABLE:
        return None
    end = db.conn.execute("SELECT COALESCE(MAX(position), 0) + 1 FROM jobs").fetchone()[0]
    db.conn.execute(
        "UPDATE jobs SET status = 'queued', error = '', interrupted = 0, "
        "started_at = '', finished_at = '', position = ? WHERE id = ?",
        (end, job_id),
    )
    db.conn.commit()
    return job_id


def update_settings(db, job_id: int, payload: dict) -> bool:
    """Replace a WAITING job's configuration snapshot.

    Queued only, and that restriction is the point: a job whose settings could
    change after it started would render half its clips one way and half
    another. Editing one item never touches any other — each job owns its own
    payload."""
    row = db.get_job(job_id)
    if row is None or row["status"] != "queued":
        return False
    db.set_job(job_id, payload=json.dumps(payload))
    return True


def clear(db, what: str) -> int:
    """Remove finished or waiting jobs. Never touches the running one."""
    groups = {
        "completed": ("done",),
        "failed": ("failed", "cancelled"),
        "queued": ("queued",),
        "all": ("done", "failed", "cancelled", "queued"),
    }
    statuses = groups.get(what)
    if statuses is None:
        raise ValueError(f"unknown clear target {what!r}")
    marks = ",".join("?" * len(statuses))
    cur = db.conn.execute(f"DELETE FROM jobs WHERE status IN ({marks})", statuses)
    db.conn.commit()
    return cur.rowcount


# ---- estimation -----------------------------------------------------------


def _history(db) -> tuple[list[float], list[float]]:
    """(seconds-of-processing per second-of-video, absolute seconds) from the
    most recent finished videos."""
    rows = db.conn.execute(
        "SELECT duration, process_seconds FROM videos "
        "WHERE status = 'done' AND process_seconds > 0 "
        "ORDER BY updated_at DESC LIMIT 20"
    ).fetchall()
    ratios = [
        r["process_seconds"] / r["duration"]
        for r in rows
        if r["duration"] and r["duration"] > 0
    ]
    absolutes = [r["process_seconds"] for r in rows]
    return ratios, absolutes


def estimate(db) -> dict:
    """How long the WAITING part of the queue should take.

    Built from measured runs rather than a flat hour per video: the ratio of
    processing time to source length is the stable quantity, so a known-length
    video scales by it. A video that hasn't downloaded yet has no known length,
    so it falls back to the median of past runs, and a first-ever run falls
    back to a constant.

    The caller adds the running video's own remaining time — the UI already
    extrapolates that from live progress, which is better than anything that
    can be computed here."""
    ratios, absolutes = _history(db)
    ratio = median(ratios) if ratios else None
    typical = median(absolutes) if absolutes else COLD_START_SECONDS

    total = 0.0
    for job in db.queued_jobs():
        if job["type"] != "process":
            continue  # re-render/translate jobs are minutes, not hours
        row = (
            db.conn.execute(
                "SELECT duration FROM videos WHERE video_id = ?", (job["video_id"],)
            ).fetchone()
            if job["video_id"]
            else None
        )
        duration = row["duration"] if row else 0
        if ratio is not None and duration:
            total += ratio * duration
        else:
            total += typical
    return {
        "queued_seconds": round(total),
        "per_video_seconds": round(typical),
        "samples": len(absolutes),
        # Below a few real runs the number is a guess wearing a number's
        # clothes; the UI softens the wording rather than pretending.
        "confident": len(absolutes) >= 3,
    }


# ---- read model -----------------------------------------------------------

# Jobs joined to the video they are about. The title is read from `videos`
# rather than written onto the job row, because the real title only exists
# after the download — and progress events arrive on pipeline threads, where
# this connection must not be written to (sqlite is one connection per thread).
_SELECT = """
SELECT j.*,
       COALESCE(NULLIF(j.title, ''), v.title, '') AS display_title,
       COALESCE(v.channel_name, '')               AS channel,
       COALESCE(v.duration, 0)                    AS source_seconds,
       COALESCE(v.status, '')                     AS video_status
  FROM jobs j
  LEFT JOIN videos v ON v.video_id = j.video_id
"""


def _item(row) -> dict:
    item = {k: row[k] for k in row.keys()}
    try:
        payload = json.loads(row["payload"]) if row["payload"] else {}
    except (TypeError, ValueError):
        payload = {}
    # The settings snapshot, with the url lifted out: the UI displays one and
    # edits the other, and should never have to parse payload JSON itself.
    item["url"] = payload.pop("url", "")
    item["settings"] = payload
    item.pop("payload", None)
    return item


def _rows(db, where: str, order: str, limit: int) -> list[dict]:
    return [
        _item(r)
        for r in db.conn.execute(
            f"{_SELECT} WHERE {where} ORDER BY {order} LIMIT ?", (limit,)
        ).fetchall()
    ]


def snapshot(db, history_limit: int = 50) -> dict:
    """Everything the queue screen needs, in one read."""
    return {
        "processing": _rows(db, "j.status = 'running'", "j.id", 10),
        "queued": _rows(db, "j.status = 'queued'", "j.position, j.id", 500),
        "completed": _rows(db, "j.status = 'done'", "j.id DESC", history_limit),
        "failed": _rows(db, "j.status IN ('failed','cancelled')", "j.id DESC", history_limit),
        "paused": is_paused(db),
        "estimate": estimate(db),
        "capacity": capacity(db),
        "max_active": MAX_ACTIVE,
    }
