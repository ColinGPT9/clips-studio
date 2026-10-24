"""What every provider's posts have in common: when each one is due, which
ones still lack a link, and what visibility was asked for.

The background checks that follow a post until it is live all measure their
waiting windows from the post's due time, never from when the row was made.
A post scheduled three weeks ahead is not late on day two, and treating it so
would stop following it before it ever went out.

Standard library only, so CI's light job can test it with nothing else
installed.
"""

import json
from datetime import datetime, timedelta, timezone

#: After this long past its due time a post still on its way is no longer
#: followed in the background. The manual refresh and the open panel still ask.
FOLLOW_DAYS = 14


def as_utc(now=None) -> datetime:
    """`now` as an aware UTC datetime. None is the current time, a number is a
    unix time, and a naive datetime is read as UTC."""
    if now is None:
        return datetime.now(timezone.utc)
    if isinstance(now, (int, float)):
        return datetime.fromtimestamp(float(now), timezone.utc)
    if now.tzinfo is None:
        return now.replace(tzinfo=timezone.utc)
    return now.astimezone(timezone.utc)


def sent_stamp() -> str:
    """Now, the way core/state.py writes created_at (local, no zone, to the
    second). A publish-time write passes it as `created_at`, so a clip sent
    again to a platform is due from this send, not the first one."""
    return datetime.now().isoformat(timespec="seconds")


def _parse(text, *, naive_is_local: bool) -> datetime | None:
    try:
        moment = datetime.fromisoformat(str(text).strip().replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if moment.tzinfo is None:
        # created_at and uploaded_at are written in local time without a zone
        # (core/state.py _now); astimezone() reads a naive value as local.
        moment = moment.astimezone() if naive_is_local else moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc)


def _field(row, name: str):
    """A column's value, or None when the row has no such key."""
    try:
        keys = row.keys()
    except AttributeError:
        return None
    return row[name] if name in keys else None


def due_time(row, *, now=None) -> datetime:
    """When a post is due, in UTC.

    A clip_publishes row: the later of created_at (local, as written) and
    scheduled_for (UTC RFC 3339, '' for none). An uploads row: publish_at, or
    uploaded_at when there is none. A missing key or a value that cannot be
    read counts as due now, so a row from before a column existed, or a test's
    stand-in row, is followed rather than skipped.
    """
    current = as_utc(now)
    if _field(row, "uploaded_at") is not None or _field(row, "publish_at") is not None:
        publish_at = _field(row, "publish_at")
        if publish_at:
            return _parse(publish_at, naive_is_local=False) or current
        uploaded_at = _field(row, "uploaded_at")
        if not uploaded_at:
            return current
        return _parse(uploaded_at, naive_is_local=True) or current

    created_raw = _field(row, "created_at")
    scheduled_raw = _field(row, "scheduled_for")
    if created_raw is None or scheduled_raw is None:
        return current
    created = _parse(created_raw, naive_is_local=True)
    if created is None:
        return current
    if not scheduled_raw:
        return created
    scheduled = _parse(scheduled_raw, naive_is_local=False)
    if scheduled is None:
        return current
    return max(created, scheduled)


def missing_url_requests(db, provider: str, *, now=None, within_hours: float = 24) -> list[str]:
    """Requests with a post the provider calls published but gave no link for.

    The refresh only follows posts still queued or processing, so a link that
    arrived after the post went live was never asked for again. These are
    asked about for `within_hours` after the post's due time, oldest first.
    """
    current = as_utc(now)
    window = timedelta(hours=within_hours)
    rows = db.conn.execute(
        "SELECT request_id, created_at, scheduled_for FROM clip_publishes "
        "WHERE request_id != '' AND state = 'published' AND post_url = '' AND provider = ?",
        (provider,),
    ).fetchall()
    earliest: dict[str, datetime] = {}
    for row in rows:
        due = due_time(row, now=current)
        if current - due > window:
            continue
        request_id = row["request_id"]
        if request_id not in earliest or due < earliest[request_id]:
            earliest[request_id] = due
    return [r for r, _ in sorted(earliest.items(), key=lambda item: (item[1], item[0]))]


#: What a status check can change on a row.
REFRESHED = ("state", "post_id", "post_url", "error")


def news(rows, result) -> list:
    """The outcomes of a status check that say something the stored rows do not.

    `rows` are the request's rows as stored. A check that only repeats them is
    not written, so updated_at keeps meaning "last changed": committed_times()
    reads a published post's day from it, and asking again for a link all day
    must not move yesterday's post into today's allowance.

    An outcome for a platform with no row in `rows` is left out too. The row is
    one per clip and platform, so a platform sent again under a newer request
    (automation re-sends only the one that failed) moved to that request, and
    the old request still reports its old outcome: written, it would put the
    re-send back to "failed" and lose it. A check never creates a row; the send
    that made the request did.
    """
    def text(value) -> str:
        return str(value or "")

    stored = {_field(r, "platform"): r for r in rows}
    out = []
    for outcome in result.outcomes:
        row = stored.get(outcome.platform)
        if row is None:
            continue
        same = (
            text(_field(row, "request_id")) == text(result.request_id)
            and all(text(_field(row, k)) == text(getattr(outcome, k, "")) for k in REFRESHED)
        )
        if not same:
            out.append(outcome)
    return out


# ---- the visibility a post asked for ---------------------------------------

# Which override key carries the privacy, per provider and platform. Every other
# platform has none: the provider posts publicly (WoopSocial's builder sends
# public explicitly, publish/woopsocial.py build_post).
PRIVACY_KEYS = {
    "woopsocial": {"youtube": "privacy", "tiktok": "privacyLevel"},
    "upload_post": {"youtube": "privacy", "tiktok": "privacy", "linkedin": "visibility"},
}

_VISIBILITY = {
    "public": "public",
    "public_to_everyone": "public",
    "unlisted": "unlisted",
    "private": "private",
    "self_only": "private",
    "mutual_follow_friends": "friends",
    "follower_of_creator": "friends",
}


def normalise_visibility(value: str) -> str:
    """public | unlisted | private | friends. A value outside those is kept
    as it came, lowercased, so it is never mistaken for public."""
    text = str(value or "").strip().lower()
    return _VISIBILITY.get(text, text)


def requested_visibility(provider: str, platform: str, overrides: dict | None) -> str:
    """The visibility asked for on one platform, normalised; `default` when
    the request left it to the provider."""
    key = PRIVACY_KEYS.get(provider, {}).get(platform)
    mine = (overrides or {}).get(platform)
    raw = mine.get(key) if key and isinstance(mine, dict) else None
    if raw in (None, "", False):
        return "default"
    return normalise_visibility(str(raw)) or "default"


def visibilities(provider: str, platforms, overrides: dict | None) -> dict[str, str]:
    """requested_visibility for each platform."""
    return {p: requested_visibility(provider, p, overrides) for p in platforms}


def for_platform(value, platform: str) -> str:
    """A per-platform value given either once for all or as {platform: value}."""
    if isinstance(value, dict):
        return str(value.get(platform) or "")
    return str(value or "")


# ---- direct YouTube uploads ------------------------------------------------------

#: When, after a scheduled upload's time, YouTube is asked whether it went live
#: (server/youtube_service.py due_live_checks). After the last check it is not
#: asked again, and one still not public is reported as private.
LIVE_CHECK_STEPS = (
    timedelta(minutes=2), timedelta(minutes=10), timedelta(hours=1),
    timedelta(hours=6), timedelta(hours=24),
)

#: A clip this long or shorter, rendered without a Longform profile, went up
#: as a Short (longform/profiles.py: the Shorts profile has no "profile" key).
SHORTS_MAX_SECONDS = 180


def next_live_check(upload_row) -> datetime | None:
    """When a scheduled upload is next due a check that it went live (UTC), or
    None when it has no time on it or its checks are used up.

    Worked out from checked_at against publish_at, so nothing new is stored:
    a check before the scheduled time (the read-back a minute after the
    upload) or none at all means the first step is next, and each check moves
    it to the first step after it.
    """
    raw = str(_field(upload_row, "publish_at") or "")
    if not raw:
        return None
    publish_at = _parse(raw, naive_is_local=False)
    if publish_at is None:
        return None
    checked_raw = str(_field(upload_row, "checked_at") or "")
    checked = _parse(checked_raw, naive_is_local=False) if checked_raw else None
    offset = checked - publish_at if checked is not None and checked >= publish_at else None
    for step in LIVE_CHECK_STEPS:
        if offset is None or step > offset:
            return publish_at + step
    return None


def _longform(render_opts) -> bool:
    text = str(render_opts or "")
    try:
        opts = json.loads(text) if text else {}
    except ValueError:
        return '"profile"' in text
    return isinstance(opts, dict) and bool(opts.get("profile"))


def youtube_url(upload_row, clip_row=None) -> str:
    """The public link of a direct upload: its Shorts page for a short clip,
    the watch page otherwise. The window is the upload's own (the post that
    went out), or the clip's for a row recorded before it had one."""
    video_id = str(_field(upload_row, "youtube_id") or "")
    if not video_id:
        return ""
    start, end = _field(upload_row, "start_s"), _field(upload_row, "end_s")
    if not end and clip_row is not None:
        start, end = _field(clip_row, "start_s"), _field(clip_row, "end_s")
    seconds = float(end or 0) - float(start or 0)
    longform = clip_row is not None and _longform(_field(clip_row, "render_opts"))
    if seconds <= SHORTS_MAX_SECONDS and not longform:
        return f"https://www.youtube.com/shorts/{video_id}"
    return f"https://www.youtube.com/watch?v={video_id}"


def youtube_view(upload_row, clip_row=None, now=None) -> dict:
    """What a direct YouTube upload is now: {"state", "live", "post_url"}.

    state is scheduled | checking | published | private | failed. The link is
    given only while the video is live (public or unlisted). A scheduled upload
    goes up private, so until YouTube is read back as having published it, it
    is checking; after the last check (LIVE_CHECK_STEPS) still not public, it
    is private. The row's own state is never changed for that: a video the
    owner made private himself is not an audit lock (locked_private).
    """
    current = as_utc(now)
    state = str(_field(upload_row, "state") or "uploaded")
    scheduled = str(_field(upload_row, "publish_at") or "")
    actual = str(_field(upload_row, "actual_privacy") or "").lower()
    asked = str(_field(upload_row, "privacy") or "").lower()
    # Scheduling is a private upload with a time on it, whatever was asked
    # for, so for a scheduled one only what YouTube reported counts.
    effective = actual if scheduled else (actual or asked)

    def view(name: str, live: bool = False) -> dict:
        return {"state": name, "live": live,
                "post_url": youtube_url(upload_row, clip_row) if live else ""}

    if state in ("rejected", "failed"):
        return view("failed")
    if state == "locked_private" or (not scheduled and effective == "private"):
        return view("private")
    if scheduled and (_parse(scheduled, naive_is_local=False) or current) > current:
        return view("scheduled")
    if effective in ("public", "unlisted") or not scheduled:
        # Not scheduled and not private: it went up as asked. A row from
        # before privacy was recorded says nothing either way.
        return view("published", live=True)
    if next_live_check(upload_row) is not None:
        return view("checking")
    return view("private")


# ---- every post of one clip ------------------------------------------------------

def posts_for_clip(db, clip_id: int, *, now=None) -> list[dict]:
    """Where one clip went, whichever way it was sent.

    The provider rows (clip_publishes) matched by id or by the clip's window,
    so a post made before the clip was re-rendered is still found, the newest
    per provider and platform; plus the direct YouTube upload as a 'youtube'
    row (youtube_view). `live` means published with a link to open.
    """
    current = as_utc(now)
    clip = db.get_clip(clip_id)
    where, params = "clip_id = ?", [int(clip_id)]
    if clip is not None and clip["video_id"]:
        where += " OR (video_id != '' AND video_id = ? AND start_s = ? AND end_s = ?)"
        params += [clip["video_id"], clip["start_s"], clip["end_s"]]

    newest: dict = {}
    for row in db.conn.execute(f"SELECT * FROM clip_publishes WHERE {where}", params).fetchall():
        key = (row["provider"] or "", row["platform"])
        rank = (str(row["updated_at"] or ""), str(row["created_at"] or ""),
                int(row["clip_id"]) == int(clip_id))
        if key not in newest or rank > newest[key][0]:
            newest[key] = (rank, row)
    out = [
        {
            "clip_id": row["clip_id"], "platform": row["platform"], "provider": row["provider"] or "",
            "state": row["state"], "post_id": row["post_id"] or "", "post_url": row["post_url"] or "",
            "error": row["error"] or "", "request_id": row["request_id"] or "",
            "scheduled_for": row["scheduled_for"] or "",
            "live": row["state"] == "published" and bool(row["post_url"]),
            "created_at": row["created_at"], "updated_at": row["updated_at"],
        }
        for _, row in newest.values()
    ]

    upload = db.conn.execute(
        f"SELECT * FROM uploads WHERE ({where}) AND youtube_id != '' "
        "ORDER BY uploaded_at DESC LIMIT 1",
        params,
    ).fetchone()
    if upload is not None:
        seen = youtube_view(upload, clip, current)
        out.append({
            "clip_id": upload["clip_id"], "platform": "youtube", "provider": "youtube",
            "state": seen["state"], "post_id": upload["youtube_id"], "post_url": seen["post_url"],
            "error": upload["error"] or "", "request_id": "",
            "scheduled_for": upload["publish_at"] or "", "live": seen["live"],
            # Local time without a zone, as the provider rows' are; checked_at
            # is UTC, so it is not mixed in here.
            "created_at": upload["uploaded_at"], "updated_at": upload["uploaded_at"],
        })
    return sorted(out, key=lambda p: (p["platform"], p["provider"]))
