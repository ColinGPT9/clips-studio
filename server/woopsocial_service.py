"""Shared state for WoopSocial publishing: settings, the API key, the project.

A sibling of server/uploadpost_service.py, kept separate for the same reason
that one is kept separate from the YouTube service: the two providers share
no configuration, and a change to one must not be able to break the other.
A creator uses whichever they already have an account with, or both.

The API key goes through core/secrets.py, not into the settings blob.
"""

import json
from datetime import datetime, timezone
from pathlib import Path

from core import secrets

SETTINGS_KEY = "woopsocial"
KEY_SECRET = "woopsocial_key"

# A WoopSocial "project" is what their UI calls a Business Profile: the thing
# connected social accounts belong to. Created on demand under this name so a
# creator is never asked to invent one.
DEFAULT_PROJECT_NAME = "Clips Kitty"

# The approved WoopSocial referral URL, shipped to everyone.
#
# A value saved in one install's settings only affects that machine, so the
# link users actually see has to live here, in the code that ships. Setting
# it turns on the affiliate CTA and, with it, the disclosure that has to sit
# beside it — the two are never shown apart.
AFFILIATE_URL = "https://woopsocial.com/?via=colin279"

DEFAULTS = {
    "enabled": False,
    # Their project id, resolved once and remembered.
    "project_id": "",
    "platforms": [],
    "common_description": "",
    # Overrides AFFILIATE_URL above for this install only, for trying a link
    # before committing it.
    "affiliate_url": "",
}


def load_settings(db) -> dict:
    try:
        stored = json.loads(db.get_flag(SETTINGS_KEY, "") or "{}")
    except (ValueError, TypeError):
        stored = {}
    return {**DEFAULTS, **(stored if isinstance(stored, dict) else {})}


def save_settings(db, patch: dict) -> dict:
    merged = {**load_settings(db), **{k: v for k, v in patch.items() if k in DEFAULTS}}
    db.set_flag(SETTINGS_KEY, json.dumps(merged))
    return merged


def is_enabled(db) -> bool:
    return bool(load_settings(db).get("enabled"))


# ---- the API key -----------------------------------------------------------


def save_key(data_dir: Path, api_key: str) -> None:
    secrets.save(Path(data_dir), KEY_SECRET, {"api_key": (api_key or "").strip()})


def load_key(data_dir: Path) -> str:
    got = secrets.load(Path(data_dir), KEY_SECRET) or {}
    return str(got.get("api_key") or "")


def has_key(data_dir: Path) -> bool:
    return bool(load_key(data_dir))


def wipe_key(data_dir: Path) -> bool:
    return secrets.wipe(Path(data_dir), KEY_SECRET)


def key_tail(data_dir: Path) -> str:
    key = load_key(data_dir)
    return key[-4:] if len(key) > 8 else ""


def make_client(data_dir: Path):
    from publish.woopsocial import WoopSocialClient

    return WoopSocialClient(load_key(data_dir))


def resolve_project(db, client) -> str:
    """The project id, created on first use.

    Their accounts hang off a project, and a creator should never be asked to
    make one — so if none is remembered, an existing one is adopted or a new
    one is created under a fixed name.
    """
    settings = load_settings(db)
    existing = settings.get("project_id") or ""
    if existing:
        return existing
    projects = client.projects()
    if projects:
        found = str(projects[0].get("id") or "")
    else:
        found = str((client.create_project(DEFAULT_PROJECT_NAME) or {}).get("id") or "")
    if found:
        save_settings(db, {"project_id": found})
    return found


def tags_of(clip) -> list[str]:
    """A clip's hashtags, stored as a JSON list in one column."""
    raw = clip["hashtags"] if "hashtags" in clip.keys() else ""
    if not raw:
        return []
    try:
        parsed = json.loads(raw)
    except (TypeError, ValueError):
        return []
    if not isinstance(parsed, list):
        return []
    return [str(t).lstrip("#") for t in parsed if str(t).strip()]


def record_outcomes(db, clip_id: int, clip, result, *, scheduled_for: str = "") -> None:
    """Write one row per platform for a finished fan-out.

    `scheduled_for` is when the post is actually due. It is kept because a run
    spread over days is otherwise invisible: the app sent the times and then
    forgot them, so the only way to see the plan was the provider's own site.
    Empty on a refresh, which learns states rather than setting times.
    """
    for outcome in result.outcomes:
        fields = {
            "provider": "woopsocial",
            "video_id": (clip["video_id"] if clip is not None else "") or "",
            "start_s": (clip["start_s"] if clip is not None else 0) or 0,
            "end_s": (clip["end_s"] if clip is not None else 0) or 0,
            "state": outcome.state,
            "post_id": outcome.post_id,
            "post_url": outcome.post_url,
            "error": outcome.error,
            "request_id": result.request_id,
        }
        if scheduled_for:
            fields["scheduled_for"] = scheduled_for
        db.record_clip_publish(clip_id, outcome.platform, fields)


def committed_times(db) -> list[str]:
    """Every slot already spoken for, so a new batch queues behind them.

    A daily budget is per day, not per batch: without this, publishing a
    second video while the first is still going out puts two runs on the same
    days and the platform rejects the overflow. That is how 32 posts were
    lost the first time.

    Counted: anything still queued or processing with a time on it, and
    anything published today, which has already spent part of today's
    allowance. Not counted: failures, which hold nothing.
    """
    rows = db.conn.execute(
        "SELECT state, scheduled_for, updated_at FROM clip_publishes "
        "WHERE state IN ('queued', 'processing', 'published')"
    ).fetchall()
    today = datetime.now(timezone.utc).date().isoformat()
    out: list[str] = []
    for r in rows:
        when = r["scheduled_for"] or ""
        if r["state"] == "published":
            # Only today's posts matter; older ones spent an allowance that
            # has since reset.
            stamp = when or (r["updated_at"] or "")
            if not stamp.startswith(today):
                continue
            when = stamp
        if when:
            out.append(when)
    return out


def publish_clips(
    db,
    data_dir: Path,
    *,
    clip_ids: list[int],
    platforms: list[str],
    hashtags: list[str] | None = None,
    every_hours: float = 0,
    start_at: str = "",
    overrides: dict | None = None,
    exclude: dict | None = None,
    per_day: int = 0,
    gap_hours: float = 1,
) -> dict:
    """Publish a set of clips, optionally spaced out over time.

    Lives here rather than in the route because two callers need it: the
    /woopsocial/batch endpoint, and the worker finishing a job that was asked
    to publish when it was queued (server/jobs.py). A second copy of this loop
    would drift from the first the moment either changed.

    One post per clip, because each clip is different media. The spacing uses
    WoopSocial's own scheduler rather than a timer here, so a run stretching
    over days keeps going with Clips Kitty closed.
    """
    from datetime import datetime, timedelta
    from datetime import timezone as tz
    from pathlib import Path as _Path

    from publish.errors import PublishError
    from publish.woopsocial import WoopSocialPublisher

    client = make_client(data_dir)
    publisher = WoopSocialPublisher(client, resolve_project(db, client))
    standing = (load_settings(db).get("common_description") or "").strip()

    start = datetime.now(tz.utc)
    if start_at:
        try:
            start = datetime.fromisoformat(start_at.replace("Z", "+00:00"))
        except ValueError:
            start = datetime.now(tz.utc)

    # A daily budget, when one was asked for. Posting limits are daily -
    # WoopSocial rations YouTube to five a day to protect the Google Cloud
    # quota it shares between all its users - so "five a day, an hour apart"
    # is the shape that works, and a flat interval cannot express it.
    slot_times: list[str] = []
    if per_day:
        from publish.schedule import MIN_LEAD_SECONDS, daily_after, to_rfc3339

        first = start
        if not start_at:
            # "Start now" cannot mean this instant: every generated time goes
            # through the same validator a hand-picked one does, and that
            # refuses anything inside the lead time. Begin at the first moment
            # it would accept rather than failing the whole run.
            first = datetime.now(tz.utc) + timedelta(seconds=MIN_LEAD_SECONDS + 60)
        # Queue behind whatever is already scheduled rather than on top of
        # it. An explicit start_at is an instruction, so it still wins.
        slot_times = daily_after(
            [] if start_at else committed_times(db),
            len(clip_ids),
            int(per_day),
            float(gap_hours or 1),
            to_rfc3339(first),
        )

    # Exceptions, keyed by clip id. JSON turns integer keys into strings on
    # the way in, so both are accepted rather than one silently missing.
    skip_map: dict[int, set[str]] = {}
    for key, values in (exclude or {}).items():
        try:
            skip_map[int(key)] = {str(v) for v in values}
        except (TypeError, ValueError):
            continue

    started: list[dict] = []
    skipped: list[dict] = []
    slot = 0
    for index, clip_id in enumerate(clip_ids):
        clip = db.get_clip(clip_id)
        if clip is None:
            skipped.append({"clip_id": clip_id, "reason": "no such clip"})
            continue

        # Some platforms are stricter than others, so a clip that is fine on
        # one is held back from another rather than dropped everywhere.
        wanted = [p for p in platforms if p not in skip_map.get(clip_id, set())]
        if not wanted:
            skipped.append({"clip_id": clip_id, "reason": "excluded from every platform"})
            continue
        if not (clip["path"] and _Path(clip["path"]).exists()):
            skipped.append({"clip_id": clip_id, "reason": "no rendered file yet"})
            continue

        text = (clip["description"] or "").strip()
        if standing:
            text = (text + "\n\n" + standing).strip()
        # The clip's own tags first, then anything asked for across the whole
        # run, with duplicates dropped so a tag the clip already had is not
        # repeated.
        tags = tags_of(clip)
        for extra in hashtags or []:
            cleaned = str(extra).lstrip("#").strip()
            if cleaned and cleaned.lower() not in {t.lower() for t in tags}:
                tags.append(cleaned)
        if tags:
            text = (text + "\n\n" + " ".join("#" + t for t in tags)).strip()

        when = ""
        if slot_times:
            # `slot` counts clips actually being sent, not the loop index: a
            # clip skipped for being excluded or unrendered must not burn one
            # of the day's five slots.
            when = slot_times[slot] if slot < len(slot_times) else slot_times[-1]
        elif every_hours or start_at:
            at = start + timedelta(hours=every_hours * slot)
            # Their scheduler wants UTC and refuses a past time; the first slot
            # of a "starting now" run would otherwise be a second or two behind
            # by the time it arrives.
            if at <= datetime.now(tz.utc):
                at = datetime.now(tz.utc) + timedelta(minutes=2)
            when = at.isoformat()

        try:
            result = publisher.start(
                _Path(clip["path"]),
                platforms=wanted,
                title=(clip["title"] or clip["hook"] or f"Clip {clip_id}").strip(),
                text=text,
                scheduled_for=when,
                overrides=overrides or {},
            )
        except PublishError as e:
            # One clip's problem must not cost the rest of the batch.
            skipped.append({"clip_id": clip_id, "reason": e.message})
            continue

        record_outcomes(db, clip_id, clip, result, scheduled_for=when)
        started.append(
            {"clip_id": clip_id, "request_id": result.request_id, "scheduled_for": when}
        )
        slot += 1

    save_settings(db, {"platforms": platforms})
    return {"started": started, "skipped": skipped}


def in_flight(db) -> list[str]:
    """Request ids with at least one destination still unfinished.

    One id per fan-out, not per row: a 37-clip batch is 37 rows but they were
    accepted as separate posts, so this returns each distinct request once and
    the caller asks about each once.
    """
    rows = db.conn.execute(
        "SELECT DISTINCT request_id FROM clip_publishes "
        "WHERE request_id != '' AND state IN ('queued', 'processing') "
        "ORDER BY created_at DESC"
    ).fetchall()
    return [r["request_id"] for r in rows]


def refresh_in_flight(db, data_dir: Path, *, limit: int = 60) -> dict:
    """Ask WoopSocial what became of everything still in the air.

    Nothing did this before, so a batch stayed at "processing" forever: the
    clips were delivered at WoopSocial's own pace and the app never looked
    again, which left no way to tell a slow queue from a failure. That is the
    whole reason this exists.

    Failures are counted, not raised. A refresh is a read; one unreachable
    request must not stop the rest being updated.
    """
    from publish.errors import PublishError
    from publish.woopsocial import WoopSocialPublisher

    ids = in_flight(db)[:limit]
    if not ids:
        return {"checked": 0, "updated": 0, "still_waiting": 0, "failed": 0}

    client = make_client(data_dir)
    publisher = WoopSocialPublisher(client, resolve_project(db, client))

    updated = failed = 0
    for request_id in ids:
        rows = db.publishes_for_request(request_id)
        if not rows:
            continue
        clip_id = int(rows[0]["clip_id"])
        try:
            result = publisher.check(request_id)
        except PublishError:
            failed += 1
            continue
        except Exception:
            failed += 1
            continue
        if result.outcomes:
            record_outcomes(db, clip_id, db.get_clip(clip_id), result)
            updated += 1

    return {
        "checked": len(ids),
        "updated": updated,
        "still_waiting": len(in_flight(db)),
        "failed": failed,
    }


def upcoming(db, *, limit: int = 200) -> list[dict]:
    """What is due, soonest first, with the clip's own title.

    The point of storing scheduled_for: a run spread across eight days is a
    plan, and a plan you cannot see is indistinguishable from a queue that
    silently dropped your posts. Which is exactly what happened.
    """
    rows = db.conn.execute(
        "SELECT p.clip_id, p.platform, p.state, p.scheduled_for, p.post_url, "
        "       p.error, c.title, c.hook "
        "FROM clip_publishes p LEFT JOIN clips c ON c.id = p.clip_id "
        "WHERE p.scheduled_for != '' "
        "ORDER BY p.scheduled_for ASC LIMIT ?",
        (int(limit),),
    ).fetchall()
    out = []
    for r in rows:
        out.append(
            {
                "clip_id": r["clip_id"],
                "platform": r["platform"],
                "state": r["state"],
                "scheduled_for": r["scheduled_for"],
                "post_url": r["post_url"] or "",
                "error": r["error"] or "",
                "title": (r["title"] or r["hook"] or f"Clip {r['clip_id']}"),
            }
        )
    return out


def status_payload(db, data_dir: Path) -> dict:
    """What the renderer is allowed to know. No key, ever."""
    settings = load_settings(db)
    return {
        "enabled": bool(settings.get("enabled")),
        "has_key": has_key(data_dir),
        "key_tail": key_tail(data_dir),
        "project_id": settings.get("project_id") or "",
        "platforms": settings.get("platforms") or [],
        "common_description": settings.get("common_description") or "",
        "affiliate_url": settings.get("affiliate_url") or AFFILIATE_URL,
        "storage": secrets.backend_name(),
    }
