"""Shared state for WoopSocial publishing: settings, the API key, the project.

A sibling of server/uploadpost_service.py, kept separate for the same reason
that one is kept separate from the YouTube service: the two providers share
no configuration, and a change to one must not be able to break the other.
A creator uses whichever they already have an account with, or both.

The API key goes through core/secrets.py, not into the settings blob.
"""

import json
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


def record_outcomes(db, clip_id: int, clip, result) -> None:
    """Write one row per platform for a finished fan-out."""
    for outcome in result.outcomes:
        db.record_clip_publish(
            clip_id,
            outcome.platform,
            {
                "provider": "woopsocial",
                "video_id": (clip["video_id"] if clip is not None else "") or "",
                "start_s": (clip["start_s"] if clip is not None else 0) or 0,
                "end_s": (clip["end_s"] if clip is not None else 0) or 0,
                "state": outcome.state,
                "post_id": outcome.post_id,
                "post_url": outcome.post_url,
                "error": outcome.error,
                "request_id": result.request_id,
            },
        )


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

    started: list[dict] = []
    skipped: list[dict] = []
    for index, clip_id in enumerate(clip_ids):
        clip = db.get_clip(clip_id)
        if clip is None:
            skipped.append({"clip_id": clip_id, "reason": "no such clip"})
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
        if every_hours or start_at:
            at = start + timedelta(hours=every_hours * index)
            # Their scheduler wants UTC and refuses a past time; the first slot
            # of a "starting now" run would otherwise be a second or two behind
            # by the time it arrives.
            if at <= datetime.now(tz.utc):
                at = datetime.now(tz.utc) + timedelta(minutes=2)
            when = at.isoformat()

        try:
            result = publisher.start(
                _Path(clip["path"]),
                platforms=platforms,
                title=(clip["title"] or clip["hook"] or f"Clip {clip_id}").strip(),
                text=text,
                scheduled_for=when,
                overrides=overrides or {},
            )
        except PublishError as e:
            # One clip's problem must not cost the rest of the batch.
            skipped.append({"clip_id": clip_id, "reason": e.message})
            continue

        record_outcomes(db, clip_id, clip, result)
        started.append(
            {"clip_id": clip_id, "request_id": result.request_id, "scheduled_for": when}
        )

    save_settings(db, {"platforms": platforms})
    return {"started": started, "skipped": skipped}


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
