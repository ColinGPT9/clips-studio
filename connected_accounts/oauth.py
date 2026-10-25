"""OAuth redirect protections for providers that support authorization code.

Twitch's public-client device flow does not use redirects or PKCE. Future
redirect providers must keep one of these stores in the trusted host, bind
the transaction to its initiating desktop session, and redeem it once.
"""

from __future__ import annotations

import base64
import hashlib
import secrets
import threading
import time
from dataclasses import dataclass, field

from connected_accounts.models import AccountError


def pkce_pair() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode("ascii")).digest())
    return verifier, challenge.rstrip(b"=").decode("ascii")


@dataclass(repr=False)
class AuthorizationState:
    provider_id: str
    session_id: str = field(repr=False)
    redirect_uri: str
    expires_at: float
    verifier: str | None = field(default=None, repr=False)


class OAuthStateStore:
    def __init__(self, *, clock=time.time, ttl: int = 300):
        self._clock = clock
        self._ttl = ttl
        self._entries: dict[str, AuthorizationState] = {}
        self._lock = threading.Lock()

    def issue(self, provider_id: str, session_id: str, redirect_uri: str, *, pkce: bool = True) -> dict:
        verifier, challenge = pkce_pair() if pkce else (None, None)
        state = secrets.token_urlsafe(32)
        with self._lock:
            self._prune()
            self._entries[state] = AuthorizationState(
                provider_id, session_id, redirect_uri, self._clock() + self._ttl, verifier
            )
        return {"state": state, "code_challenge": challenge, "code_challenge_method": "S256" if pkce else None}

    def consume(self, state: str, *, provider_id: str, session_id: str, redirect_uri: str) -> AuthorizationState:
        with self._lock:
            self._prune()
            # Even an invalid callback consumes its matching transaction. A
            # second callback must never get a second code exchange attempt.
            entry = self._entries.pop(state, None)
        if (entry is None or entry.provider_id != provider_id or entry.redirect_uri != redirect_uri
                or not secrets.compare_digest(entry.session_id, session_id)):
            raise AccountError("invalid_state", "The sign-in request expired or did not match. Start again.")
        return entry

    def _prune(self):
        now = self._clock()
        self._entries = {key: value for key, value in self._entries.items() if value.expires_at > now}
