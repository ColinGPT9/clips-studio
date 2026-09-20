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
from pathlib import Path

from core import secrets

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
