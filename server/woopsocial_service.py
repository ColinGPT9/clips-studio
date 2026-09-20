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
