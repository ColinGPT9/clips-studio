"""OS-protected credentials. Never fall back to a plaintext token file.

Windows uses core/secrets (DPAPI), the repository's one credential store, under
one name per account. core/secrets' non-Windows fallback is a 0600 plaintext
file, which Connected Accounts refuses; outside Windows an OS keychain is used
through ``keyring`` (installed only there), or connecting fails closed.
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
import tempfile
from pathlib import Path

from connected_accounts.models import AccountError
from core import secrets as core_secrets
from core.paths import discard

# Keychains that keep secrets in the OS's own protected store. A plaintext,
# null or third-party backend is refused; a chained backend only when every
# backend it chains is one of these.
_SECURE_KEYRINGS = frozenset({
    "keyring.backends.SecretService", "keyring.backends.libsecret",
    "keyring.backends.kwallet", "keyring.backends.macOS",
})


def atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".connected-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        # A no-op after os.replace; never lets a locked file replace the
        # exception already in flight (issue #74).
        discard(Path(temporary))


def set_aside(path: Path) -> bool:
    """Move an unreadable optional state file out of the way. True if moved.

    Connected Accounts and stream workflows are opt-in; a damaged file of
    theirs must never stop the engine, and with it every other feature, from
    starting. The file is kept as ``<name>.corrupt`` rather than deleted.
    """
    try:
        os.replace(path, path.with_name(path.name + ".corrupt"))
        return True
    except OSError:
        return False


def _secure_keyring(backend) -> bool:
    module = type(backend).__module__
    if module == "keyring.backends.chainer":
        chained = list(getattr(backend, "backends", None) or [])
        return bool(chained) and all(type(b).__module__ in _SECURE_KEYRINGS for b in chained)
    return module in _SECURE_KEYRINGS


class SecureTokenStore:
    """DPAPI on Windows; an OS keychain on macOS/Linux, or fail closed.

    Plugins currently run with the user's OS rights. This prevents accidental
    token handoff and plaintext persistence, not a same-user process attack.

    An entry that exists but cannot be decrypted or parsed (a data folder
    restored onto another Windows account, a damaged file) loads as None, so
    the service asks the person to reconnect instead of failing every call.
    """

    def __init__(self, data_dir: Path):
        self._data_dir = Path(data_dir)
        self._keyring = None
        self._backend = "unavailable"
        if sys.platform == "win32":
            self._backend = "windows-dpapi"
        else:
            try:
                import keyring

                backend = keyring.get_keyring()
                if _secure_keyring(backend):
                    self._keyring = backend
                    self._backend = "os-keyring"
            except Exception:
                # No usable keychain: the store stays "unavailable" and
                # connecting fails closed with a plain message.
                pass
        # Isolate separate development profiles in the same OS keychain.
        digest = hashlib.sha256(str(self._data_dir.resolve()).encode()).hexdigest()[:24]
        self._service = f"ClipsKitty.ConnectedAccounts.{digest}"

    @property
    def available(self) -> bool:
        return self._backend != "unavailable"

    @property
    def backend(self) -> str:
        return self._backend

    @staticmethod
    def _name(account_id: str) -> str:
        return "connected-account-" + hashlib.sha256(account_id.encode()).hexdigest()

    def _require(self):
        if not self.available:
            raise AccountError("credential_store_unavailable",
                               "An unlocked OS credential store is required to connect accounts.", status=503)

    def save(self, account_id: str, payload: dict) -> None:
        self._require()
        try:
            if self._backend == "windows-dpapi":
                core_secrets.save(self._data_dir, self._name(account_id), payload)
            else:
                self._keyring.set_password(self._service, account_id, json.dumps(payload))
        except Exception:
            raise AccountError("credential_store_unavailable", "The OS credential store could not save this account.",
                               status=503) from None

    def load(self, account_id: str) -> dict | None:
        self._require()
        if self._backend == "windows-dpapi":
            # Never raises: unreadable or undecryptable means "reconnect".
            return core_secrets.load(self._data_dir, self._name(account_id))
        try:
            raw = self._keyring.get_password(self._service, account_id)
        except Exception:
            # A locked or unreachable keychain is transient, not a lost grant.
            raise AccountError("credential_store_unavailable", "The OS credential store could not read this account.",
                               status=503) from None
        try:
            value = json.loads(raw) if raw else None
        except ValueError:
            return None
        return value if isinstance(value, dict) else None

    def delete(self, account_id: str) -> None:
        self._require()
        if self._backend == "windows-dpapi":
            if not core_secrets.wipe(self._data_dir, self._name(account_id)):
                raise AccountError("credential_store_unavailable",
                                   "The OS credential store could not delete this account.", status=503)
            return
        try:
            if self._keyring.get_password(self._service, account_id) is not None:
                self._keyring.delete_password(self._service, account_id)
        except Exception:
            raise AccountError("credential_store_unavailable", "The OS credential store could not delete this account.",
                               status=503) from None
