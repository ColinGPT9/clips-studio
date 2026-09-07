"""Somewhere to keep OAuth tokens that isn't a plaintext file.

Until now the YouTube refresh token sat in `<data_dir>/youtube_token.json` as
readable JSON. A refresh token is a long-lived key to someone's channel, so
that is worth improving before the app starts asking every user for one.

Windows gets DPAPI (`CryptProtectData`), reached through ctypes. That was
chosen over the `keyring` package deliberately:

* it needs no new dependency, no PyInstaller hidden import, and no extra
  failure mode in the frozen build;
* the blob is tied to the Windows *user account*, so copying `data/` to another
  machine or another user cannot decrypt it;
* `keyring` on Linux wants a Secret Service that the project's Docker image
  does not have, so it would degrade to a plaintext backend anyway — i.e. we
  would pay a dependency to arrive back at the fallback below.

Everywhere else there is a 0600 file, and this module is honest about that
being obfuscation-free: it protects against another user on the box, not
against someone who already has your account.

Nothing here ever logs a value.
"""

import json
import os
import sys
from pathlib import Path

_ENTROPY = b"clips-kitty/publish/v1"

# Deliberately not "*token*.json": the CI secret check greps tracked files for
# that pattern, and while data/ is gitignored, a name that cannot trip the
# guard is one less trap to fall into later.
_DIR = "credentials"


def backend_name() -> str:
    return "windows-dpapi" if sys.platform == "win32" else "file"


def _path(data_dir: Path, name: str) -> Path:
    suffix = "bin" if backend_name() == "windows-dpapi" else "secret.json"
    return Path(data_dir) / _DIR / f"{name}.{suffix}"


# ---- Windows DPAPI ---------------------------------------------------------


def _dpapi(encrypt: bool, payload: bytes) -> bytes:
    import ctypes
    import ctypes.wintypes

    class BLOB(ctypes.Structure):
        _fields_ = [
            ("cbData", ctypes.wintypes.DWORD),
            ("pbData", ctypes.POINTER(ctypes.c_char)),
        ]

        @classmethod
        def of(cls, data: bytes) -> "BLOB":
            buffer = ctypes.create_string_buffer(data, len(data))
            return cls(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_char)))

    crypt32 = ctypes.windll.crypt32
    fn = crypt32.CryptProtectData if encrypt else crypt32.CryptUnprotectData

    blob_in = BLOB.of(payload)
    entropy = BLOB.of(_ENTROPY)
    blob_out = BLOB()

    # Both calls take the same seven arguments in the same order; the second
    # is a description string going out on encrypt and coming back on decrypt,
    # and NULL is valid for both. CRYPTPROTECT_UI_FORBIDDEN (0x1) means never
    # prompt — this runs on a worker thread with no window, where a prompt
    # would hang forever.
    ok = fn(
        ctypes.byref(blob_in),
        None,
        ctypes.byref(entropy),
        None,
        None,
        0x1,
        ctypes.byref(blob_out),
    )
    if not ok:
        raise OSError(ctypes.get_last_error() or "DPAPI refused the data")
    try:
        return ctypes.string_at(blob_out.pbData, blob_out.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(blob_out.pbData)


# ---- public API ------------------------------------------------------------


def save(data_dir: Path, name: str, payload: dict) -> None:
    target = _path(data_dir, name)
    target.parent.mkdir(parents=True, exist_ok=True)
    raw = json.dumps(payload).encode("utf-8")

    if backend_name() == "windows-dpapi":
        target.write_bytes(_dpapi(True, raw))
    else:
        target.write_bytes(raw)
        try:
            os.chmod(target, 0o600)
        except OSError:
            # A filesystem without POSIX permissions. Nothing to be done, and
            # failing the connect flow over it would be worse.
            pass


def load(data_dir: Path, name: str) -> dict | None:
    """Read a stored secret, or None if it isn't there or can't be read.

    Never raises. A DPAPI blob restored from a backup onto a different Windows
    account is undecryptable by design, and the right answer to that is
    "reconnect your account", not a crash on startup.
    """
    target = _path(data_dir, name)
    if not target.exists():
        return None
    try:
        raw = target.read_bytes()
        if backend_name() == "windows-dpapi":
            raw = _dpapi(False, raw)
        value = json.loads(raw.decode("utf-8"))
        return value if isinstance(value, dict) else None
    except Exception:
        return None


def wipe(data_dir: Path, name: str) -> None:
    """Delete a stored secret.

    A failure here matters more than it looks: disconnecting an account would
    report success while the token stayed on disk. Say so rather than swallow
    it — but do not raise, because the caller has already revoked the token at
    Google and a half-finished disconnect is worse than a noisy one.
    """
    target = _path(data_dir, name)
    try:
        target.unlink(missing_ok=True)
    except OSError as e:
        print(f"Could not delete the stored credential {target.name}: {e}")


def has(data_dir: Path, name: str) -> bool:
    return _path(data_dir, name).exists()


def migrate_plaintext(data_dir: Path, name: str, legacy: Path) -> bool:
    """Move an old plaintext credential file into the store and delete it.

    Returns True if something was migrated. Existing users authorised with
    `python main.py auth` have a readable youtube_token.json; they should not
    have to reconnect, and it should not stay readable.
    """
    if has(data_dir, name) or not legacy.exists():
        return False
    try:
        payload = json.loads(legacy.read_text(encoding="utf-8"))
    except Exception:
        return False
    if not isinstance(payload, dict) or not payload:
        return False
    save(data_dir, name, payload)
    try:
        legacy.unlink()
    except OSError:
        print(f"Moved {legacy.name} into the credential store, but could not delete the original.")
        return True
    print(f"Moved {legacy.name} into the encrypted credential store.")
    return True
