"""The user's own provider keys, in the app's credential store.

core/secrets.py encrypts them with Windows DPAPI (a private file elsewhere),
the same as the WoopSocial and Upload-Post keys. Nothing here prints, logs or
returns a key to anything but the request that needs it.

Stored as "ai_key_<provider>": the CI secret-file check rejects any tracked
file named like a token, and these names can never trip it.
"""

from pathlib import Path

from core import secrets

PREFIX = "ai_key_"


def _name(provider: str) -> str:
    return f"{PREFIX}{provider}"


def save_key(data_dir, provider: str, key: str, region: str = "") -> None:
    payload = {"api_key": key.strip()}
    if region:
        payload["region"] = region
    secrets.save(Path(data_dir), _name(provider), payload)


def load_key(data_dir, provider: str) -> str:
    payload = secrets.load(Path(data_dir), _name(provider)) or {}
    return str(payload.get("api_key") or "")


def load_region(data_dir, provider: str) -> str:
    """The region the saved key belongs to, for providers whose keys are
    tied to one ("" otherwise, and for a key saved before regions existed)."""
    payload = secrets.load(Path(data_dir), _name(provider)) or {}
    return str(payload.get("region") or "")


def resolve(data_dir, spec):
    """(spec at the address the saved key works at, key). The key is "" when
    none is saved; callers say so in their own words."""
    payload = secrets.load(Path(data_dir), _name(spec.id)) or {}
    return spec.in_region(str(payload.get("region") or "")), str(payload.get("api_key") or "")


def has_key(data_dir, provider: str) -> bool:
    return secrets.has(Path(data_dir), _name(provider))


def wipe_key(data_dir, provider: str) -> bool:
    return secrets.wipe(Path(data_dir), _name(provider))


def key_tail(data_dir, provider: str) -> str:
    """The last four characters, for "••••abcd" in the UI. Nothing for a key
    too short for four characters to be safe to show."""
    key = load_key(data_dir, provider)
    return key[-4:] if len(key) > 8 else ""
