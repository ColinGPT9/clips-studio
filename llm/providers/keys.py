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


def save_key(data_dir, provider: str, key: str) -> None:
    secrets.save(Path(data_dir), _name(provider), {"api_key": key.strip()})


def load_key(data_dir, provider: str) -> str:
    payload = secrets.load(Path(data_dir), _name(provider)) or {}
    return str(payload.get("api_key") or "")


def has_key(data_dir, provider: str) -> bool:
    return secrets.has(Path(data_dir), _name(provider))


def wipe_key(data_dir, provider: str) -> bool:
    return secrets.wipe(Path(data_dir), _name(provider))


def key_tail(data_dir, provider: str) -> str:
    """The last four characters, for "••••abcd" in the UI. Nothing for a key
    too short for four characters to be safe to show."""
    key = load_key(data_dir, provider)
    return key[-4:] if len(key) > 8 else ""
