"""Shared state for Upload-Post publishing: settings, the API key, the profile.

A copy of the shape `server/youtube_service.py` settled on rather than an
extension of it — the two providers share no configuration, and coupling them
would mean a YouTube change could break multi-platform publishing. Settings
live as a JSON blob in `app_state` for the reason given there: PATCH /settings
rewrites settings.yaml with a regex per key, and every nested setting has
needed a bespoke handler.

The API key does NOT live in that blob. It goes through `core/secrets.py`,
which is DPAPI-backed on Windows, so it is tied to the user's account and is
not sitting in a file anyone can paste into a bug report.

The feature is OFF until someone turns it on and enters a key. An install that
never touches Upload-Post has no Upload-Post anything, and in particular makes
no network calls to it.
"""

import json
from datetime import timedelta
from pathlib import Path

from core import secrets
from server import posts

SETTINGS_KEY = "uploadpost"
KEY_SECRET = "uploadpost_key"

# Every upload has to name an Upload-Post "profile", the thing that owns the
# connected social accounts. A creator with one set of accounts does not care
# what it is called, so they are never asked: this is used unless they change
# it. Someone managing several channels can still pick their own.
DEFAULT_PROFILE = "clips-kitty"

# The approved Upload-Post referral URL, shipped to everyone.
#
# This is the one that matters for releases: a value saved in an individual
# install's settings only affects that machine, so the link users actually
# see has to be here, in the code that ships. Empty means no affiliate claim
# is made anywhere — the app shows a plain signup link instead, which is the
# correct behaviour until a referral URL has actually been approved.
#
# Setting it in the app overrides this locally, which is how to try it out
# before committing the real thing.
AFFILIATE_URL = ""

DEFAULTS = {
    "enabled": False,
    "profile": "",
    # Platforms the user last published to, so the tick-list remembers.
    "platforms": [],
    # Standing text appended to every description, same idea as the YouTube
    # common block. Empty by default — a block nobody asked for is noise.
    "common_description": "",
    "first_comment": "",
    # Overrides AFFILIATE_URL above for this install only, for trying a
    # referral link before it is committed. Empty means "use the shipped one".
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
    """The last few characters, so the UI can show WHICH key is stored.

    Never the key itself, and never enough of it to be useful to anyone who
    reads it — the same rule the YouTube routes follow with client_id_tail.
    """
    key = load_key(data_dir)
    return key[-4:] if len(key) > 8 else ""


def make_client(data_dir: Path):
    """Build a client from the stored key. Raises if there is no key."""
    from publish.uploadpost import UploadPostClient

    return UploadPostClient(load_key(data_dir))


def caption_for(platform: str, *, title: str, description: str = "", tags=None,
                overrides: dict | None = None) -> str:
    """The text one platform was sent: its title, its description and the
    hashtags, with that platform's own wording when it was given one. An
    override the platform has no field for is never sent
    (publish/uploadpost.py platform_overrides), so it is not counted either."""
    from publish.uploadpost import PLATFORM_FIELDS

    spec = PLATFORM_FIELDS.get(platform) or {}
    mine = (overrides or {}).get(platform)
    mine = mine if isinstance(mine, dict) else {}
    own_title = mine.get("title") if spec.get("title") else ""
    own_description = mine.get("description") if spec.get("description") else ""
    parts = [
        str(own_title or title or "").strip(),
        str(own_description or description or "").strip(),
        " ".join("#" + str(t).lstrip("#").strip() for t in (tags or []) if str(t).strip("# ")),
    ]
    return "\n\n".join(p for p in parts if p)


def captions(platforms, **kwargs) -> dict[str, str]:
    """caption_for each platform."""
    return {p: caption_for(p, **kwargs) for p in platforms}


# ---- what became of a publish ------------------------------------------------


def record_outcomes(db, clip_id: int, clip, result, *, scheduled_for: str = "",
                    caption="", visibility="") -> None:
    """Write one row per destination for a fan-out that was just sent.

    Publish time only: this is what the post IS, so it carries the clip's
    identity (only while the clip row exists, never blanked), when the post is
    due ('' for now, or a time Upload-Post picks from its queue), the text
    that went out and the visibility asked for. `caption` and `visibility`
    may be one value or {platform: value}. Status checks use record_refresh.
    This is a send, so the row's clock starts again (created_at): a clip sent
    again to a platform is due from this send, not the first one.
    """
    sent = posts.sent_stamp()
    for outcome in result.outcomes:
        fields = {
            "provider": "upload_post",
            "created_at": sent,
            "state": outcome.state,
            "post_id": outcome.post_id,
            "post_url": outcome.post_url,
            "error": outcome.error,
            "request_id": result.request_id,
            "scheduled_for": scheduled_for or "",
            "caption": posts.for_platform(caption, outcome.platform),
            "visibility": posts.for_platform(visibility, outcome.platform),
        }
        if clip is not None:
            fields["video_id"] = clip["video_id"] or ""
            fields["start_s"] = clip["start_s"] or 0
            fields["end_s"] = clip["end_s"] or 0
        db.record_clip_publish(clip_id, outcome.platform, fields)


def record_refresh(db, clip_id: int, result, *, known=None) -> int:
    """Write what a status check or a retry learned, and nothing else.

    The row may by now hang off a re-rendered clip with another window, so the
    post's identity, caption and time are never rewritten here. With `known`,
    the request's rows as stored, an outcome that only repeats its row is not
    written, nor one for a platform sent again since under a newer request
    (server/posts.py news). Returns how many were.
    """
    outcomes = result.outcomes if known is None else posts.news(known, result)
    for outcome in outcomes:
        db.record_clip_publish(clip_id, outcome.platform, {
            "state": outcome.state,
            "post_id": outcome.post_id,
            "post_url": outcome.post_url,
            "error": outcome.error,
            "request_id": result.request_id,
        })
    return len(outcomes)


def record_retry(db, clip_id: int, result, rows) -> int:
    """Write what Upload-Post's retry of a request answered.

    `rows` are the request's rows as stored. A retry sends the platforms that
    failed again, so each of those starts its clock again (created_at), as a
    publish-time write does: retried 20 days after its first send, a post is
    due now and followed, not 20 days late. The platforms that had already
    gone out keep theirs. Otherwise this is a status check (record_refresh
    with `known`): identity, caption and time are never rewritten, and a
    platform sent again under a newer request is left alone. An answer with
    no per-platform detail puts the failed rows back in flight, so the poller
    picks the real states up. Returns how many rows were written.
    """
    failed = {r["platform"] for r in rows if r["state"] == "failed"}
    sent = posts.sent_stamp()
    if not result.outcomes:
        for platform in sorted(failed):
            db.record_clip_publish(
                clip_id, platform, {"state": "queued", "error": "", "created_at": sent}
            )
        return len(failed)
    outcomes = posts.news(rows, result)
    for outcome in outcomes:
        fields = {
            "state": outcome.state,
            "post_id": outcome.post_id,
            "post_url": outcome.post_url,
            "error": outcome.error,
            "request_id": result.request_id,
        }
        if outcome.platform in failed:
            fields["created_at"] = sent
        db.record_clip_publish(clip_id, outcome.platform, fields)
    return len(outcomes)


def in_flight(db) -> list[str]:
    """Upload-Post request ids with a destination still unfinished, newest first."""
    rows = db.conn.execute(
        "SELECT DISTINCT request_id FROM clip_publishes "
        "WHERE request_id != '' AND state IN ('queued', 'processing') "
        "AND provider = 'upload_post' ORDER BY created_at DESC"
    ).fetchall()
    return [r["request_id"] for r in rows]


#: How far ahead of its time a post is already worth asking about.
DUE_AHEAD_SECONDS = 120
#: After this long past its due time a request is no longer followed.
FOLLOW_DAYS = posts.FOLLOW_DAYS
#: Leeway on each interval, so a check on the worker's three-minute beat is
#: not pushed to the next beat by a second of drift.
CHECK_SLACK_SECONDS = 5


def check_interval(age_seconds: float, *, has_time: bool) -> float:
    """How long to wait between checks of a request this long past its due time.

    Every 3 minutes in the first hour (what the open panel already does), then
    every 30 minutes until a day has passed, then every 3 hours. A request with
    no time on it, which is what "add to queue" sends (Upload-Post picks the
    time), starts at the 30-minute step: its due time says nothing about when
    it will actually go out.
    """
    if age_seconds < 3600 and has_time:
        return 3 * 60
    if age_seconds < 24 * 3600:
        return 30 * 60
    return 3 * 3600


def due_requests(db, *, now=None, last_checked: dict | None = None, limit: int = 60) -> list[str]:
    """The Upload-Post requests worth asking about now, oldest-due first.

    Still queued or processing, due (no more than two minutes ahead), due less
    than 14 days ago, and not checked within their interval (check_interval).
    Every window counts from the due time (server/posts.py), so a post
    scheduled 20 days out is followed once it comes due. `last_checked` maps
    request id to the unix time of its last check; requests no longer in
    flight are dropped from it.
    """
    current = posts.as_utc(now)
    stamp = current.timestamp()
    checked = last_checked if last_checked is not None else {}
    rows = db.conn.execute(
        "SELECT request_id, created_at, scheduled_for FROM clip_publishes "
        "WHERE request_id != '' AND state IN ('queued', 'processing') "
        "AND provider = 'upload_post'"
    ).fetchall()
    earliest: dict = {}
    timed: set[str] = set()
    for row in rows:
        request_id = row["request_id"]
        due = posts.due_time(row, now=current)
        if request_id not in earliest or due < earliest[request_id]:
            earliest[request_id] = due
        if "scheduled_for" in row.keys() and row["scheduled_for"]:
            timed.add(request_id)
    for gone in [r for r in checked if r not in earliest]:
        checked.pop(gone, None)

    horizon = current + timedelta(seconds=DUE_AHEAD_SECONDS)
    oldest = current - timedelta(days=FOLLOW_DAYS)
    out = []
    for request_id, due in earliest.items():
        if due > horizon or due < oldest:
            continue
        last = checked.get(request_id)
        if last is not None:
            age = (current - due).total_seconds()
            wait = check_interval(age, has_time=request_id in timed)
            if stamp - float(last) < wait - CHECK_SLACK_SECONDS:
                continue
        out.append((due, request_id))
    return [request_id for _, request_id in sorted(out)][:limit]


def refresh_in_flight(db, data_dir: Path, *, ids=None, limit: int = 60, now=None,
                      last_checked: dict | None = None) -> dict:
    """Ask Upload-Post what became of publishes still in the air.

    Before this only the open panel asked (POST /uploadpost/refresh/{id}), so
    a scheduled post never got its link. Without `ids` it asks about
    due_requests; each request asked about is stamped in `last_checked`.
    Failures are counted, not raised: one unreachable request must not stop
    the rest being updated.
    """
    from publish.uploadpost import UploadPostPublisher

    current = posts.as_utc(now)
    if ids is None:
        ids = due_requests(db, now=current, last_checked=last_checked, limit=limit)
    ids = list(ids)[:limit]
    if not ids:
        return {"checked": 0, "updated": 0, "still_waiting": 0, "failed": 0}

    publisher = UploadPostPublisher(make_client(data_dir), load_settings(db).get("profile") or "")
    updated = failed = 0
    for request_id in ids:
        if last_checked is not None:
            last_checked[request_id] = current.timestamp()
        rows = db.publishes_for_request(request_id)
        if not rows:
            continue
        clip_id = int(rows[0]["clip_id"])
        try:
            result = publisher.check(request_id)
        except Exception:
            failed += 1
            continue
        # Only what changed is written: a check that repeats the rows leaves
        # updated_at alone (server/posts.py news).
        if result.outcomes and record_refresh(db, clip_id, result, known=rows):
            updated += 1

    return {
        "checked": len(ids),
        "updated": updated,
        "still_waiting": len(in_flight(db)),
        "failed": failed,
    }


def refresh_missing_urls(db, data_dir: Path, *, now=None) -> dict:
    """Ask again about posts Upload-Post called published without a link, for
    a day after each one was due (server/posts.py missing_url_requests)."""
    ids = posts.missing_url_requests(db, "upload_post", now=now)
    return refresh_in_flight(db, data_dir, ids=ids, now=now)


def status_payload(db, data_dir: Path) -> dict:
    """What the renderer is allowed to know. No key, ever."""
    settings = load_settings(db)
    return {
        "enabled": bool(settings.get("enabled")),
        "has_key": has_key(data_dir),
        "key_tail": key_tail(data_dir),
        # Never blank: a creator should not have to invent a name for
        # something they will never look at again.
        "profile": settings.get("profile") or DEFAULT_PROFILE,
        "platforms": settings.get("platforms") or [],
        "common_description": settings.get("common_description") or "",
        "first_comment": settings.get("first_comment") or "",
        # The local override if one is set, otherwise whatever ships. Empty
        # means no affiliate CTA and no commission claim anywhere — just a
        # plain official signup link.
        "affiliate_url": settings.get("affiliate_url") or AFFILIATE_URL,
        "storage": secrets.backend_name(),
    }
