"""Provider contracts for trusted, local Connected Accounts services.

These types deliberately do not expose a credential-bearing account object.
Only providers and the host service ever handle OAuth token dictionaries.
"""

from __future__ import annotations

from typing import Protocol


class AccountError(Exception):
    """A safe, user-facing error; never include an upstream response or token."""

    def __init__(self, code: str, message: str, *, status: int = 400):
        super().__init__(message)
        self.code = code
        self.status = status


ProviderError = AccountError


class Provider(Protocol):
    id: str
    client_id: str
    default_scopes: tuple[str, ...]

    def begin(self, scopes: list[str]) -> dict: ...
    def poll(self, device_code: str) -> dict: ...
    def validate(self, access_token: str) -> dict: ...
    def identity(self, access_token: str) -> dict: ...
    def refresh(self, refresh_token: str) -> dict: ...
    def revoke(self, access_token: str) -> None: ...
    def perform(self, access_token: str, account: dict, operation: str, **params): ...


class TokenStore(Protocol):
    @property
    def available(self) -> bool: ...
    @property
    def backend(self) -> str: ...
    def save(self, account_id: str, payload: dict) -> None: ...
    def load(self, account_id: str) -> dict | None: ...
    def delete(self, account_id: str) -> None: ...
