"""Shared state for YouTube publishing: settings, credentials, quota.

The routes and the publish worker both need these, and neither should own
them. Everything persists in the `app_state` key/value table rather than in
settings.yaml, because PATCH /settings rewrites that file with a regex per key
and 400s when the key line is missing — every nested setting so far has needed
a bespoke handler plus an insert fallback. A JSON blob in app_state is the
existing pattern for "a decision the user made that must outlive a restart",
and it keeps credentials-adjacent configuration out of a file people paste
into bug reports.

The feature is OFF until someone turns it on. `enabled` false is the shipped
state, and the API says so and nothing else, so an install that never touches
YouTube has no YouTube anything.
"""

import json
from datetime import timedelta
from pathlib import Path

from server import posts

SETTINGS_KEY = "youtube"
QUOTA_KEY = "youtube_quota"

DEFAULTS = {
    "enabled": False,
    "privacy": "public",
    "category_id": "22",
    "made_for_kids": False,
    "playlists_enabled": False,
    "notify_subscribers": True,
    "region": "US",
    # Text put under every video's own description: where to watch live, the
    # Discord, the socials. Empty by default, because a standing block nobody
    # asked for is just noise on someone's channel.
    "common_description": "",
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


# ---- quota ledger ----------------------------------------------------------


def load_ledger(db):
    from publish.quota import Ledger

    try:
        return Ledger(json.loads(db.get_flag(QUOTA_KEY, "") or "{}"))
    except (ValueError, TypeError):
        return Ledger()


def save_ledger(db, ledger) -> None:
    db.set_flag(QUOTA_KEY, json.dumps(ledger.as_dict()))


# ---- the publisher ---------------------------------------------------------


ACCOUNTS_KEY = "youtube_accounts"


# ---- connected channels ----------------------------------------------------
#
# A creator with a main channel and a clips channel needs both connected at
# once. Each is a separate Google consent with its own token, because YouTube
# binds a token to whichever channel was chosen on the consent screen — so the
# roster here is a list of channels, and the token for each lives in the
# credential store under a name derived from its channel id.


def load_accounts(db, data_dir: Path | None = None) -> list[dict]:
    """Every connected channel, newest last. `default` marks the one used when
    a publish does not name a channel."""
    try:
        stored = json.loads(db.get_flag(ACCOUNTS_KEY, "") or "[]")
    except (ValueError, TypeError):
        stored = []
    accounts = [a for a in stored if isinstance(a, dict) and a.get("id")]

    # An install that connected before multi-channel support has a channel in
    # the old single-slot flag and nothing in the roster. Adopt it rather than
    # making someone reconnect a working account.
    if not accounts:
        legacy = json.loads(db.get_flag("youtube_channel", "") or "null")
        if isinstance(legacy, dict) and legacy.get("id"):
            accounts = [{**legacy, "default": True, "legacy_token": True}]
            db.set_flag(ACCOUNTS_KEY, json.dumps(accounts))

    if accounts and not any(a.get("default") for a in accounts):
        accounts[0]["default"] = True
    return accounts


def save_accounts(db, accounts: list[dict]) -> None:
    db.set_flag(ACCOUNTS_KEY, json.dumps(accounts))
    # Keep the single-slot flag pointing at the default, so anything still
    # reading it (and the status payload's `channel`) stays truthful.
    default = next((a for a in accounts if a.get("default")), None)
    db.set_flag("youtube_channel", json.dumps(default) if default else "")


def add_account(db, channel: dict, scopes: list[str]) -> list[dict]:
    """Record a channel that just finished consent. Re-connecting the same
    channel updates it in place rather than adding a duplicate."""
    accounts = load_accounts(db)
    entry = {
        "id": channel["id"],
        "title": channel.get("title", ""),
        "handle": channel.get("handle", ""),
        "scopes": list(scopes or []),
    }
    for i, existing in enumerate(accounts):
        if existing["id"] == entry["id"]:
            entry["default"] = existing.get("default", False)
            entry["legacy_token"] = existing.get("legacy_token", False)
            accounts[i] = entry
            break
    else:
        entry["default"] = not accounts  # the first one connected wins by default
        accounts.append(entry)
    save_accounts(db, accounts)
    return accounts


def remove_account(db, channel_id: str) -> list[dict]:
    accounts = [a for a in load_accounts(db) if a["id"] != channel_id]
    if accounts and not any(a.get("default") for a in accounts):
        accounts[0]["default"] = True
    save_accounts(db, accounts)
    return accounts


def set_default_account(db, channel_id: str) -> list[dict]:
    accounts = load_accounts(db)
    if not any(a["id"] == channel_id for a in accounts):
        return accounts
    for a in accounts:
        a["default"] = a["id"] == channel_id
    save_accounts(db, accounts)
    return accounts


def default_channel_id(db) -> str | None:
    for a in load_accounts(db):
        if a.get("default"):
            return a["id"]
    return None


def token_name_for_account(db, channel_id: str | None) -> str | None:
    """The credential-store name holding this channel's token.

    Returns None for a channel connected before per-channel names existed, so
    the publisher falls back to the unqualified slot.
    """
    if not channel_id:
        return None
    for a in load_accounts(db):
        if a["id"] == channel_id and a.get("legacy_token"):
            return None
    return channel_id


def make_publisher(
    config: dict, data_dir: Path, privacy: str = "private", channel_id: str | None = None
):
    """A publisher backed by the encrypted credential store.

    Imported lazily by callers: this pulls in the Google client libraries,
    which a CI runner does not have and a user who never enables the feature
    does not need loaded.
    """
    from publish.youtube_shorts import YouTubeShortsPublisher

    return YouTubeShortsPublisher(
        # Legacy locations, so someone who ran `python main.py auth` before
        # this feature existed is migrated into the store rather than being
        # asked to reconnect for no reason.
        client_secret=_legacy_client_secret(config),
        token_path=Path(data_dir) / "youtube_token.json",
        privacy=privacy,
        data_dir=Path(data_dir),
        channel_id=channel_id,
    )


def _legacy_client_secret(config: dict) -> Path | None:
    from core.paths import resolve_config_file

    raw = (config.get("upload") or {}).get("client_secret")
    return resolve_config_file(config, raw) if raw else None


def status_payload(db, config: dict, data_dir: Path) -> dict:
    """What GET /youtube/status returns.

    Disabled means `{"enabled": false}` and nothing else — not a channel, not a
    quota count, not whether a key is configured. A feature that is switched
    off should look absent, and the editor uses exactly this to decide whether
    to render anything at all.
    """
    settings = load_settings(db)
    if not settings.get("enabled"):
        return {"enabled": False}

    from core import secrets
    from publish.quota import UPLOAD_LIMIT
    from publish.youtube_shorts import CLIENT_SECRET, SCOPE_FULL, TOKEN_SECRET, token_name_for

    data_dir = Path(data_dir)
    has_client = secrets.has(data_dir, CLIENT_SECRET) or bool(
        (_legacy_client_secret(config) or Path("nowhere")).exists()
    )
    accounts = load_accounts(db)
    # A roster entry only counts as connected while its token is actually
    # there — a wiped credential store would otherwise keep listing channels
    # that cannot publish.
    connected = []
    for account in accounts:
        name = TOKEN_SECRET if account.get("legacy_token") else token_name_for(account["id"])
        if secrets.has(data_dir, name):
            connected.append(account)
    if connected != accounts:
        save_accounts(db, connected)
        accounts = connected

    default = next((a for a in accounts if a.get("default")), None)
    scopes = list((default or {}).get("scopes") or [])
    if not scopes:
        # Nothing in the roster yet (or an install predating it): read the
        # scopes off whatever token is in the unqualified slot.
        scopes = list((secrets.load(data_dir, TOKEN_SECRET) or {}).get("scopes") or [])
    ledger = load_ledger(db)

    payload = {
        "enabled": True,
        "backend": secrets.backend_name(),
        "has_client": has_client,
        "connected": bool(accounts) or secrets.has(data_dir, TOKEN_SECRET),
        "scopes": scopes,
        "playlists_available": SCOPE_FULL in scopes,
        "accounts": accounts,
        "channel": default or json.loads(db.get_flag("youtube_channel", "") or "null"),
        "settings": settings,
        "quota": {
            "uploads_used": ledger.uploads,
            "uploads_limit": UPLOAD_LIMIT,
            "remaining": ledger.remaining(),
            "resets_at": ledger.blocked_until,
        },
    }
    return payload


def remember_channel(db, channel: dict | None) -> None:
    """Cache the connected channel so the UI can name it without a quota call."""
    db.set_flag("youtube_channel", json.dumps(channel) if channel else "")


# ---- scheduled uploads going live ------------------------------------------
#
# A scheduled upload goes up private with a time on it, and YouTube makes it
# public at that time. The only read-back was in memory a minute after the
# upload, so here it stayed private for good, and a restart lost even that.
# These ask a few times after the time (server/posts.py LIVE_CHECK_STEPS), one
# quota unit each, and the answers live in the uploads row.

#: How long after its time a failing check of a scheduled upload is tried
#: again on every pass. Past it, an upload with a step left is tried once.
LIVE_CHECK_DAYS = 7


def due_live_checks(db, now=None, limit: int = 10, *, last_tried: dict | None = None) -> list:
    """The scheduled uploads due a check that they went live, at most `limit`.

    Uploaded with a time, that time two minutes or more ago, not yet read back
    public or unlisted, and due by the step schedule (posts.next_live_check).
    Never-tried first, then the ones tried longest ago, oldest-due among
    equals: `last_tried` maps clip id to the unix time of its last try, so
    uploads whose check keeps failing take turns with the rest instead of
    filling every pass. Entries no longer due are dropped.

    One whose time is more than LIVE_CHECK_DAYS ago with a step still left
    (it went up before these checks, the app was closed, or YouTube publishing
    was off) is due only until it has been tried: that one check comes after
    the last step, so whatever YouTube answers ends its schedule and it reads
    live or private, never "checking" for good. If the check fails, it waits
    for the next start of the app. Among the never-tried, these come after the
    uploads still inside their week, so a backlog never holds up a new one.
    """
    current = posts.as_utc(now)
    tried = last_tried if last_tried is not None else {}
    rows = db.conn.execute(
        "SELECT * FROM uploads WHERE publish_at != '' AND state = 'uploaded' "
        "AND youtube_id != '' AND actual_privacy NOT IN ('public', 'unlisted')"
    ).fetchall()
    oldest = current - timedelta(days=LIVE_CHECK_DAYS)
    due, keep = [], set()
    for row in rows:
        at = posts.next_live_check(row)
        if at is None or at > current:
            continue
        clip_id = row["clip_id"]
        keep.add(clip_id)
        late = posts.due_time(row, now=current) < oldest
        if late and clip_id in tried:
            continue
        due.append((float(tried.get(clip_id, 0.0)), late, at, clip_id, row))
    for gone in [c for c in tried if c not in keep]:
        tried.pop(gone, None)
    due.sort(key=lambda item: item[:4])
    return [row for *_, row in due[:limit]]


def live_check_fields(status: dict, *, checked_at: str) -> dict:
    """What one check of a scheduled upload writes (StateDB.set_upload_check).

    Always when it was checked. The privacy YouTube reports: public or
    unlisted is live, still private leaves the next step due. A rejection or a
    processing failure is the row's state and error, as the read-back after an
    upload records them. Nothing returned means only the time is written.
    """
    fields: dict = {"checked_at": checked_at}
    if not status:
        return fields
    if status.get("privacy"):
        fields["actual_privacy"] = status["privacy"]
    if status.get("upload_status") == "rejected":
        fields["state"] = "rejected"
        fields["error"] = (
            f"YouTube rejected this video: {status.get('rejection_reason') or 'unknown'}."
        )
    elif status.get("upload_status") == "failed":
        fields["state"] = "failed"
        fields["error"] = (
            f"YouTube could not process this video: {status.get('failure_reason') or 'unknown'}."
        )
    return fields
