"""OS-protected credentials. Never fall back to the legacy plaintext store."""

from __future__ import annotations

import hashlib
import json
import os
import sys
import tempfile
from pathlib import Path

from connected_accounts.models import AccountError


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
        Path(temporary).unlink(missing_ok=True)


class SecureTokenStore:
    """DPAPI on Windows; an OS keychain on macOS/Linux, or fail closed.

    Plugins currently run with the user's OS rights. This prevents accidental
    token handoff and plaintext persistence, not a same-user process attack.
    """

    def __init__(self, data_dir: Path):
        self._root = Path(data_dir) / "credentials" / "connected-accounts"
        self._keyring = None
        self._backend = "unavailable"
        if sys.platform == "win32":
            self._backend = "windows-dpapi"
        else:
            try:
                import keyring

                backend = keyring.get_keyring()
                # Reject plaintext, null, third-party and chained backends.
                # A chained backend may silently include a plaintext fallback.
                if type(backend).__module__ in {"keyring.backends.SecretService", "keyring.backends.macOS"}:
                    self._keyring = backend
                    self._backend = "os-keyring"
            except Exception:
                pass
        # Isolate separate development profiles in the same OS keychain.
        digest = hashlib.sha256(str(Path(data_dir).resolve()).encode()).hexdigest()[:24]
        self._service = f"ClipsKitty.ConnectedAccounts.{digest}"

    @property
    def available(self) -> bool:
        return self._backend != "unavailable"

    @property
    def backend(self) -> str:
        return self._backend

    def _path(self, account_id: str) -> Path:
        return self._root / (hashlib.sha256(account_id.encode()).hexdigest() + ".bin")

    def _require(self):
        if not self.available:
            raise AccountError("credential_store_unavailable",
                               "An unlocked OS credential store is required to connect accounts.", status=503)

    def save(self, account_id: str, payload: dict) -> None:
        self._require()
        try:
            raw = json.dumps(payload)
            if self._backend == "windows-dpapi":
                from core.secrets import _dpapi

                atomic_write(self._path(account_id), _dpapi(True, raw.encode("utf-8")))
            else:
                self._keyring.set_password(self._service, account_id, raw)
        except Exception:
            raise AccountError("credential_store_unavailable", "The OS credential store could not save this account.",
                               status=503) from None

    def load(self, account_id: str) -> dict | None:
        self._require()
        try:
            if self._backend == "windows-dpapi":
                path = self._path(account_id)
                if not path.exists():
                    return None
                from core.secrets import _dpapi

                raw = _dpapi(False, path.read_bytes()).decode("utf-8")
            else:
                raw = self._keyring.get_password(self._service, account_id)
            value = json.loads(raw) if raw else None
            return value if isinstance(value, dict) else None
        except Exception:
            raise AccountError("credential_store_unavailable", "The OS credential store could not read this account.",
                               status=503) from None

    def delete(self, account_id: str) -> None:
        self._require()
        try:
            if self._backend == "windows-dpapi":
                self._path(account_id).unlink(missing_ok=True)
            elif self._keyring.get_password(self._service, account_id) is not None:
                self._keyring.delete_password(self._service, account_id)
        except Exception:
            raise AccountError("credential_store_unavailable", "The OS credential store could not delete this account.",
                               status=503) from None
