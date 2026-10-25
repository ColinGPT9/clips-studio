"""Trusted desktop account lifecycle, shared by UI, watches and integrations.

No public method returns tokens. Provider operations are the only path from
an account to its credential, and the host decides who can invoke them.

Locking: ``_lock`` guards the roster, flows and providers and is never held
across a network call, so ``accounts()`` (which the EventSub receive thread
and the Settings page read) cannot stall behind a slow Twitch request. A
per-account token lock serialises refresh and validation instead, because
Twitch public-client refresh tokens are single use. Order: token lock first,
then ``_lock``.
"""

from __future__ import annotations

import copy
import json
import secrets
import threading
import time
from pathlib import Path

from connected_accounts.models import AccountError, Provider, TokenStore
from connected_accounts.store import SecureTokenStore, atomic_write, set_aside

_ROSTER_FIELDS = {"id", "provider", "user_id", "client_id", "login", "display_name", "scopes", "status"}
_INVALID_GRANT = {"invalid_grant", "invalid_token", "revoked", "access_denied"}
_PENDING = {"authorization_pending", "slow_down"}


def _scopes(value) -> list[str]:
    if isinstance(value, str):
        value = value.split()
    return sorted({s for s in value or [] if isinstance(s, str) and len(s) <= 128})


class ConnectedAccounts:
    def __init__(self, data_dir: Path, providers: dict[str, Provider] | None = None,
                 *, store: TokenStore | None = None, clock=time.time):
        self._path = Path(data_dir) / "connected-accounts.json"
        self._store = store if store is not None else SecureTokenStore(data_dir)
        self._providers = dict(providers or {})
        self._clock = clock
        self._lock = threading.RLock()
        self._token_locks: dict[str, threading.Lock] = {}
        self._flows: dict[str, dict] = {}
        self._validated: dict[str, float] = {}
        self._stop = threading.Event()
        self._worker = None
        self._accounts: dict[str, dict] = {}
        # Set when an unreadable roster could not be moved aside: saving would
        # overwrite it, so the list stays read-only until the next start.
        self._roster_locked = False
        for row in self._read_roster():
            if isinstance(row, dict) and isinstance(row.get("id"), str):
                clean = {k: v for k, v in row.items() if k in _ROSTER_FIELDS}
                clean["scopes"] = _scopes(clean.get("scopes"))
                self._accounts[clean["id"]] = clean

    def _read_roster(self) -> list:
        try:
            saved = json.loads(self._path.read_text(encoding="utf-8"))
            if isinstance(saved, list):
                return saved
        except FileNotFoundError:
            return []
        except (OSError, ValueError):
            # Unreadable or damaged: handled below, like a non-list.
            pass
        # Connected Accounts is optional. A damaged list must not stop the
        # engine, and every unrelated feature with it, from starting.
        if set_aside(self._path):
            print("The saved Twitch connection list could not be read and was set aside "
                  "(connected-accounts.json.corrupt). Connect Twitch again in Settings.")
        else:
            self._roster_locked = True
            print("The saved Twitch connection list could not be read. "
                  "Twitch stays disconnected until it can be read again.")
        return []

    @property
    def providers(self) -> dict[str, Provider]:
        with self._lock:
            return dict(self._providers)

    def register_provider(self, provider: Provider) -> None:
        with self._lock:
            previous = self._providers.get(provider.id)
            if previous and previous.client_id != provider.client_id:
                previous_accounts = self._accounts
                self._accounts = copy.deepcopy(previous_accounts)
                for account in self._accounts.values():
                    if account["provider"] == provider.id:
                        account["status"] = "reconnect_required"
                try:
                    self._persist()
                except AccountError:
                    self._accounts = previous_accounts
                    raise
                # Only commit registry/session changes after the account list
                # is durable. An unsuccessful configuration change must leave
                # the previous provider, pending flows and accounts usable.
                self._flows = {k: f for k, f in self._flows.items() if f["provider"] != provider.id}
                for account in self._accounts.values():
                    if account["provider"] == provider.id:
                        self._validated.pop(account["id"], None)
            self._providers[provider.id] = provider
            if previous is not None and previous is not provider:
                close = getattr(previous, "close", None)
                if close:
                    try:
                        self._call(close)
                    except AccountError:
                        # Best effort: the replacement is already registered.
                        pass

    def credential_store_status(self) -> dict:
        return {"available": bool(self._store.available), "backend": self._store.backend}

    def status(self) -> dict:
        with self._lock:
            return {"credential_store": self.credential_store_status(), "accounts": self.accounts(),
                    "providers": [{"id": p.id, "configured": bool(p.client_id),
                                   "capabilities": copy.deepcopy(getattr(p, "capabilities", {}))}
                                  for p in self._providers.values()]}

    def accounts(self, provider_id: str | None = None) -> list[dict]:
        with self._lock:
            return [copy.deepcopy(a) for a in self._accounts.values()
                    if provider_id is None or a.get("provider") == provider_id]

    def _persist(self):
        with self._lock:
            if self._roster_locked:
                raise AccountError("account_store_unavailable", "The account list could not be saved.", status=503)
            try:
                atomic_write(self._path, json.dumps(list(self._accounts.values())).encode("utf-8"))
            except OSError:
                raise AccountError("account_store_unavailable", "The account list could not be saved.",
                                   status=503) from None

    def _provider(self, provider_id: str) -> Provider:
        provider = self._providers.get(provider_id)
        if provider is None or not provider.client_id:
            raise AccountError("provider_not_configured", "Configure this account provider before connecting.", status=409)
        return provider

    def _token_lock(self, account_id: str) -> threading.Lock:
        with self._lock:
            return self._token_locks.setdefault(account_id, threading.Lock())

    def _call(self, function, *args, **kwargs):
        try:
            return function(*args, **kwargs)
        except AccountError:
            raise
        except Exception:
            # Network libraries commonly put URLs, query tokens and response
            # bodies in their exceptions. Those must not reach the renderer.
            raise AccountError("provider_unavailable", "The account provider could not complete this request.",
                               status=502) from None

    def _revoke(self, provider: Provider, access_token: str, client_id: str | None) -> None:
        """Revoke with the client ID that issued the token, not just ours."""
        if client_id and client_id != provider.client_id:
            self._call(provider.revoke, access_token, client_id=client_id)
        else:
            self._call(provider.revoke, access_token)

    def _revocable(self, provider: Provider, account: dict, token: dict) -> str:
        """The access token to revoke on disconnect.

        Twitch refuses to revoke an expired access token, while its refresh
        token keeps the grant alive (an app closed for a few hours is enough).
        Refresh once with the issuing client, then revoke the fresh token.
        """
        if (token.get("expires_at", 0) > self._clock() + 60 or not token.get("refresh_token")
                or account.get("client_id") != provider.client_id):
            return token["access_token"]
        try:
            return self._token(self._call(provider.refresh, token["refresh_token"]), token)["access_token"]
        except AccountError:
            # Revoke what there is; a refusal is reported as revoked: false.
            return token["access_token"]

    def begin(self, provider_id: str, scopes: list[str] | None = None) -> dict:
        with self._lock:
            if not self._store.available:
                raise AccountError("credential_store_unavailable",
                                   "An unlocked OS credential store is required to connect accounts.", status=503)
            provider = self._provider(provider_id)
            requested = _scopes(provider.default_scopes if scopes is None else scopes)
            # Drop expired flows and bound retained device credentials.
            now = self._clock()
            self._flows = {k: f for k, f in self._flows.items() if f["expires_at"] > now}
            if len(self._flows) >= 8:
                raise AccountError("too_many_requests", "Finish or cancel an existing sign-in first.", status=429)
        grant = self._call(provider.begin, requested)
        if not grant.get("device_code") or not grant.get("verification_uri") or not grant.get("user_code"):
            raise AccountError("invalid_provider_response", "The provider did not return a usable sign-in request.", status=502)
        interval = max(1, min(float(grant.get("interval", 5)), 60))
        expires_in = max(1, min(float(grant.get("expires_in", 600)), 1800))
        flow_id = secrets.token_urlsafe(32)
        with self._lock:
            if self._providers.get(provider_id) is not provider:
                raise AccountError("invalid_flow", "The sign-in request no longer exists. Start again.", status=404)
            if len(self._flows) >= 8:
                raise AccountError("too_many_requests", "Finish or cancel an existing sign-in first.", status=429)
            self._flows[flow_id] = {"provider": provider_id, "client_id": provider.client_id,
                                    "device_code": grant["device_code"], "scopes": requested,
                                    "expires_at": now + expires_in, "interval": interval,
                                    "next_poll": now + interval}
        result = {"flow_id": flow_id, "status": "pending", "user_code": grant["user_code"],
                  "verification_uri": grant["verification_uri"], "expires_in": int(expires_in),
                  "interval": interval}
        if grant.get("verification_uri_complete"):
            result["verification_uri_complete"] = grant["verification_uri_complete"]
        return result

    def poll(self, flow_id: str) -> dict:
        with self._lock:
            flow = self._flows.get(flow_id)
            if not flow:
                raise AccountError("invalid_flow", "The sign-in request no longer exists. Start again.", status=404)
            now = self._clock()
            if flow["expires_at"] <= now:
                self._flows.pop(flow_id, None)
                raise AccountError("expired_token", "The sign-in request expired. Start again.", status=410)
            if now < flow["next_poll"]:
                return {"status": "pending", "interval": flow["interval"],
                        "expires_in": max(0, int(flow["expires_at"] - now))}
            provider = self._provider(flow["provider"])
            # Set before the request: a second poll meanwhile answers pending.
            flow["next_poll"] = now + flow["interval"]
            device_code = flow["device_code"]
        try:
            token = self._call(provider.poll, device_code)
        except AccountError as error:
            with self._lock:
                if error.code in _PENDING:
                    if error.code == "slow_down":
                        flow["interval"] += 5
                        flow["next_poll"] = now + flow["interval"]
                    return {"status": "pending", "interval": flow["interval"],
                            "expires_in": max(0, int(flow["expires_at"] - now))}
                if error.code in {"expired_token", "access_denied", "invalid_grant"}:
                    self._flows.pop(flow_id, None)
            raise
        with self._lock:
            # A successful exchange is single use even if persistence fails.
            current = self._flows.pop(flow_id, None) is flow and self._providers.get(flow["provider"]) is provider
        try:
            if not current:
                # Cancelled, or the Client ID changed, while Twitch answered.
                raise AccountError("invalid_flow", "The sign-in request no longer exists. Start again.", status=404)
            account = self._finish(provider, token)
        except Exception:
            if isinstance(token, dict) and token.get("access_token"):
                try:
                    self._call(provider.revoke, token["access_token"])
                except AccountError:
                    # Best effort: the grant is unusable here either way.
                    pass
            raise
        return {"status": "connected", "account": account}

    def cancel(self, flow_id: str) -> dict:
        with self._lock:
            self._flows.pop(flow_id, None)
            return {"status": "cancelled"}

    def _token(self, token: dict, previous: dict | None = None) -> dict:
        if not isinstance(token, dict) or not isinstance(token.get("access_token"), str) or not token["access_token"]:
            raise AccountError("invalid_provider_response", "The provider did not return a usable authorization.", status=502)
        previous = previous or {}
        return {"access_token": token["access_token"],
                "refresh_token": token.get("refresh_token") or previous.get("refresh_token"),
                "expires_at": self._clock() + max(0, float(token.get("expires_in", 0))),
                "scope": _scopes(token.get("scope", token.get("scopes", previous.get("scope", []))))}

    def _validate(self, provider: Provider, token: dict, *, user_id: str | None = None) -> dict:
        verified = self._call(provider.validate, token["access_token"])
        if (not isinstance(verified, dict) or verified.get("client_id") != provider.client_id
                or not verified.get("user_id") or (user_id is not None and verified.get("user_id") != user_id)):
            raise AccountError("invalid_token", "This authorization does not match the connected account.", status=401)
        return verified

    def _finish(self, provider: Provider, raw: dict) -> dict:
        token = self._token(raw)
        verified = self._validate(provider, token)
        identity = self._call(provider.identity, token["access_token"])
        if not isinstance(identity, dict) or identity.get("id") != verified["user_id"]:
            raise AccountError("invalid_token", "This authorization does not match the connected account.", status=401)
        account_id = provider.id + ":" + verified["user_id"]
        account = {"id": account_id, "provider": provider.id, "user_id": verified["user_id"],
                   "client_id": provider.client_id, "login": str(identity.get("login", ""))[:128],
                   "display_name": str(identity.get("display_name", ""))[:256],
                   "scopes": _scopes(verified.get("scopes")), "status": "connected"}
        token["scope"] = account["scopes"]
        if "expires_in" in verified:
            token["expires_at"] = self._clock() + max(0, float(verified["expires_in"]))
        with self._token_lock(account_id), self._lock:
            previous = copy.deepcopy(self._accounts.get(account_id))
            try:
                previous_token = self._store.load(account_id) if previous is not None else None
            except AccountError:
                # An unreadable old credential is no reason to throw away the
                # one the person just approved; there is nothing to restore.
                previous_token = None
            self._store.save(account_id, token)
            self._accounts[account_id] = account
            try:
                self._persist()
            except AccountError:
                # A newly granted token is revoked by poll after this failure.
                # Revalidate restored credentials before using them: provider
                # revocation semantics can invalidate related authorizations.
                self._validated.pop(account_id, None)
                if previous is not None:
                    self._accounts[account_id] = previous
                else:
                    self._accounts.pop(account_id, None)
                try:
                    if previous_token is not None:
                        self._store.save(account_id, previous_token)
                    else:
                        self._store.delete(account_id)
                except AccountError:
                    # Never leave a roster entry claiming that a credential which
                    # could not be restored is usable. Best-effort removal also
                    # prevents keeping the newly granted token that poll revokes.
                    if previous is not None:
                        self._accounts[account_id]["status"] = "reconnect_required"
                    self._validated.pop(account_id, None)
                    try:
                        self._store.delete(account_id)
                    except AccountError:
                        # Already failing; the status above stays truthful.
                        pass
                    raise AccountError("credential_store_unavailable",
                                       "The account could not be saved or restored. Reconnect this account.",
                                       status=503) from None
                raise
            self._validated[account_id] = self._clock()
        replaced = (previous_token or {}).get("access_token")
        old_client = (previous or {}).get("client_id")
        if replaced and replaced != token["access_token"] and old_client and old_client != provider.client_id:
            # The grant under the previous Client ID stays live on Twitch
            # unless revoked with that ID. The same app's grant is left alone:
            # revoking it could take the new authorization with it.
            try:
                self._revoke(provider, replaced, old_client)
            except AccountError:
                # Best effort: the person can still remove it on Twitch.
                pass
        return copy.deepcopy(account)

    def _account(self, account_id: str) -> dict:
        account = self._accounts.get(account_id)
        if account is None:
            raise AccountError("account_not_found", "This account is no longer connected.", status=404)
        return account

    def _reconnect(self, account_id: str):
        with self._lock:
            account = self._accounts.get(account_id)
            self._validated.pop(account_id, None)
            if account is not None and account.get("status") != "reconnect_required":
                account["status"] = "reconnect_required"
                self._persist()

    def _access(self, account: dict, *, force_validate: bool = False) -> tuple[Provider, str]:
        """Return a usable token for this snapshot. Hold its token lock."""
        account_id = account["id"]
        with self._lock:
            provider = self._provider(account["provider"])
        if account.get("client_id") != provider.client_id:
            self._reconnect(account_id)
            raise AccountError("reconnect_required", "Reconnect this account to continue.", status=401)
        if account.get("status") == "reconnect_required":
            raise AccountError("reconnect_required", "Reconnect this account to continue.", status=401)
        token = self._store.load(account_id)
        if not token or not token.get("access_token"):
            self._reconnect(account_id)
            raise AccountError("reconnect_required", "Reconnect this account to continue.", status=401)
        refreshed = False
        if token.get("expires_at", 0) <= self._clock() + 60:
            if not token.get("refresh_token"):
                self._reconnect(account_id)
                raise AccountError("reconnect_required", "Reconnect this account to continue.", status=401)
            try:
                token = self._token(self._call(provider.refresh, token["refresh_token"]), token)
                # Rotating refresh tokens must be committed before any other
                # request; the old token may already have become unusable.
                self._store.save(account_id, token)
                refreshed = True
            except AccountError as error:
                if error.code in _INVALID_GRANT:
                    self._reconnect(account_id)
                raise
        with self._lock:
            last = self._validated.get(account_id)
        if force_validate or refreshed or last is None or self._clock() - last >= 3600:
            try:
                verified = self._validate(provider, token, user_id=account["user_id"])
            except AccountError as error:
                if error.code in _INVALID_GRANT:
                    self._reconnect(account_id)
                raise
            scopes = _scopes(verified.get("scopes"))
            with self._lock:
                current = self._accounts.get(account_id)
                if current is not None and scopes != current.get("scopes"):
                    current["scopes"] = scopes
                    self._persist()
                self._validated[account_id] = self._clock()
        return provider, token["access_token"]

    def _authorise(self, account_id: str, *, force_validate: bool = False) -> tuple[Provider, str, dict]:
        with self._token_lock(account_id):
            with self._lock:
                account = copy.deepcopy(self._account(account_id))
            provider, access_token = self._access(account, force_validate=force_validate)
            with self._lock:
                return provider, access_token, copy.deepcopy(self._account(account_id))

    def test(self, account_id: str) -> dict:
        _, _, account = self._authorise(account_id, force_validate=True)
        return {"ok": True, "account": account}

    def perform(self, account_id: str, operation: str, **params):
        provider, access_token, account = self._authorise(account_id)
        scope_map = getattr(provider, "operation_scopes", {})
        required = set(scope_map.get(operation, ()))
        if not required.issubset(set(account.get("scopes", []))):
            raise AccountError("missing_scope", "Reconnect this account with permission for this action.", status=403)
        # No lock across the provider's network request.
        try:
            return self._call(provider.perform, access_token, account, operation, **params)
        except AccountError as error:
            if error.code in _INVALID_GRANT:
                self._reconnect(account_id)
            raise

    def disconnect(self, account_id: str) -> dict:
        with self._token_lock(account_id):
            with self._lock:
                account = copy.deepcopy(self._account(account_id))
                provider = self._providers.get(account["provider"])
            revoked = False
            try:
                if provider:
                    close_account = getattr(provider, "close_account", None)
                    if close_account:
                        self._call(close_account, copy.deepcopy(account))
                    token = self._store.load(account_id)
                    if token and token.get("access_token"):
                        self._revoke(provider, self._revocable(provider, account, token),
                                     account.get("client_id"))
                        revoked = True
            except AccountError:
                # An offline provider must not prevent local disconnection.
                pass
            with self._lock:
                try:
                    self._store.delete(account_id)
                except AccountError:
                    # Revocation may already have succeeded; keep any retry from
                    # reusing those credentials even if local deletion failed.
                    current = self._accounts.get(account_id)
                    if current is not None:
                        current["status"] = "reconnect_required"
                    self._validated.pop(account_id, None)
                    try:
                        self._persist()
                    except AccountError:
                        # The delete failure below is the error that matters.
                        pass
                    raise
                current = self._accounts.pop(account_id, None) or account
                self._validated.pop(account_id, None)
                try:
                    self._persist()
                except AccountError:
                    # The persisted roster may still list this account although
                    # its credentials are gone. Keep a truthful retryable entry
                    # in memory until the account list can be saved again.
                    self._accounts[account_id] = {**current, "status": "reconnect_required"}
                    raise
            return {"disconnected": True, "revoked": revoked}

    def start(self) -> None:
        """Validate restored sessions now and at least hourly while running."""
        with self._lock:
            if self._worker and self._worker.is_alive():
                return
            self._stop.clear()
            self._worker = threading.Thread(target=self._maintain, name="connected-accounts", daemon=True)
            self._worker.start()

    def _maintain(self):
        while not self._stop.is_set():
            for account in self.accounts():
                if account.get("status") == "connected":
                    try:
                        self._authorise(account["id"])
                    except AccountError:
                        # _access already marked a lost grant; anything else
                        # (offline, rate limited) is retried next round.
                        pass
            self._stop.wait(60)

    def close(self) -> None:
        self._stop.set()
        if self._worker:
            self._worker.join(timeout=2)
        with self._lock:
            self._flows.clear()
            providers = list(self._providers.values())
        for provider in providers:
            close = getattr(provider, "close", None)
            if close:
                self._call(close)
