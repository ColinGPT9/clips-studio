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

    def begin(self, scopes: list[str]) -> dict:
        """Start a device authorization for these scopes."""

    def poll(self, device_code: str) -> dict:
        """Exchange an approved device code for a token dictionary."""

    def validate(self, access_token: str) -> dict:
        """Confirm a token's client, user and scopes with the provider."""

    def identity(self, access_token: str) -> dict:
        """Return the account the token belongs to."""

    def refresh(self, refresh_token: str) -> dict:
        """Exchange a refresh token for a new token dictionary."""

    def revoke(self, access_token: str, *, client_id: str | None = None) -> None:
        """Revoke a token, using the client ID that issued it when it differs."""

    def perform(self, access_token: str, account: dict, operation: str, **params):
        """Run one named, validated provider operation for the account."""


class TokenStore(Protocol):
    @property
    def available(self) -> bool:
        """Whether an OS-protected store can hold credentials."""

    @property
    def backend(self) -> str:
        """The store's backend name, shown in Settings."""

    def save(self, account_id: str, payload: dict) -> None:
        """Store an account's token dictionary."""

    def load(self, account_id: str) -> dict | None:
        """Read an account's token dictionary, or None when it is unusable."""

    def delete(self, account_id: str) -> None:
        """Remove an account's stored credentials."""
