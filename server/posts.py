"""What every provider's posts have in common: when each one is due, which
ones still lack a link, and what visibility was asked for.

The background checks that follow a post until it is live all measure their
waiting windows from the post's due time, never from when the row was made.
A post scheduled three weeks ahead is not late on day two, and treating it so
would stop following it before it ever went out.

Standard library only, so CI's light job can test it with nothing else
installed.
"""

from datetime import datetime, timedelta, timezone


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


# ---- the visibility a post asked for ---------------------------------------

# Which override key carries the privacy, per provider and platform. Every other
# platform has none: the provider posts publicly (WoopSocial's builder sends
# public explicitly, publish/woopsocial.py build_post).
PRIVACY_KEYS = {
    "woopsocial": {"youtube": "privacy", "tiktok": "privacyLevel"},
    "upload_post": {"youtube": "privacy", "tiktok": "privacy"},
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
