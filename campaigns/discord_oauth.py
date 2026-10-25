"""Signing in with Discord: OAuth2 with PKCE for a Public Client.

The app is a Public Client, so there is no client secret anywhere: the
client id is public, and each sign-in proves itself with a fresh PKCE
verifier (S256) that never leaves this PC until the exchange. Signing in
asks for `identify` and `guilds` only, which lets Clips Kitty show who is
connected and list their servers. It can't post as the user, read their
messages or run another bot's commands; nothing in Discord's OAuth can.

`webhook.incoming` is the one other scope: a server admin picks a channel
on Discord's own page and Discord hands back a webhook for it. That flow is
separate (purpose "webhook"), and its access token is revoked at once and
never kept; only the webhook is.

Every call takes the transport (`http`, campaigns.discord_http.Http or a
test's fake) and the API address comes from discord_http.api_base().
"""

import secrets
import threading
import time
from dataclasses import dataclass, field
from urllib.parse import quote, urlencode

from campaigns import discord_http
from campaigns.discord_http import DiscordError
from core.scrub import scrub_secrets
from llm.providers.oauth import pkce_pair

#: Clips Kitty's Discord application. Public by design (it is in every
#: authorize link); Settings → Discord → Advanced can override it for testing.
DISCORD_CLIENT_ID = "1563637373474971708"

SIGN_IN_SCOPES = ("identify", "guilds")
WEBHOOK_SCOPE = "webhook.incoming"
CALLBACK_PATH = "/discord/callback"
#: The port the engine runs on, and the only redirect registered with Discord.
DEFAULT_PORT = 8765
#: How long a sign-in in the browser may take before its state is refused.
SIGN_IN_SECONDS = 15 * 60

#: Server permissions that let someone add a webhook (Manage Webhooks,
#: Administrator).
MANAGE_WEBHOOKS = 1 << 29
ADMINISTRATOR = 1 << 3


def redirect_uri(port: int) -> str:
    """Where Discord sends the browser back. Discord accepts only the one
    registered for the app (port 8765); a fake server accepts any."""
    return f"http://127.0.0.1:{int(port)}{CALLBACK_PATH}"


def authorize_url(client_id: str, redirect: str, state: str, challenge: str, scopes) -> str:
    """The page the user's own browser opens to approve Clips Kitty."""
    return discord_http.authorize_base() + "?" + urlencode({
        "response_type": "code",
        "client_id": client_id,
        "scope": " ".join(scopes),
        "redirect_uri": redirect,
        "state": state,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
    }, quote_via=quote)


def _network() -> DiscordError:
    return DiscordError("network", "Couldn't reach Discord. Check your internet connection and try again.")


def _token_call(http, path: str, form: dict):
    """POST a form to Discord's token endpoints. The form carries a code or
    a token, so no exception text (which can name the request) goes on."""
    try:
        return http.request(
            "POST", discord_http.api_base() + path, data=form,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
    except Exception:
        raise _network() from None


def _tokens(http, form: dict) -> dict:
    response = _token_call(http, "/oauth2/token", form)
    got = discord_http.body(response)
    got = got if isinstance(got, dict) else {}
    status = int(getattr(response, "status_code", 0) or 0)
    if status == 429:
        raise DiscordError("rate_limited", "Discord is busy. Wait a minute and try again.")
    if status >= 300:
        error = str(got.get("error") or "")
        if error == "invalid_grant":
            raise DiscordError("expired", "This Discord sign-in has expired or was already used. "
                                          "Connect Discord again.")
        if error in ("invalid_client", "unauthorized_client"):
            raise DiscordError("denied", "Discord didn't accept Clips Kitty's application ID. "
                                         "Check it under Settings → Discord → Advanced.")
        detail = scrub_secrets(str(got.get("error_description") or error or ""))[:200]
        raise DiscordError("bad_response", f"Discord refused the sign-in ({status}{': ' + detail if detail else ''}).")
    if not str(got.get("access_token") or ""):
        raise DiscordError("bad_response", "Discord signed you in but sent no token back. Try again.")
    return got


def exchange(http, client_id: str, code: str, verifier: str, redirect: str) -> dict:
    """The tokens for a one-time code. With `webhook.incoming` the answer
    may also hold the webhook Discord made ("webhook")."""
    return _tokens(http, {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": redirect,
        "client_id": client_id,
        "code_verifier": verifier,
    })


def refresh(http, client_id: str, refresh_token: str) -> dict:
    """New tokens for a refresh token. A Public Client sends its id, no secret."""
    return _tokens(http, {
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
        "client_id": client_id,
    })


def revoke(http, client_id: str, token: str, hint: str = "refresh_token") -> bool:
    """Ask Discord to forget a token. False when it refused or couldn't be
    reached: the caller wipes its own copy either way."""
    if not token:
        return False
    try:
        response = _token_call(http, "/oauth2/token/revoke",
                               {"token": token, "token_type_hint": hint, "client_id": client_id})
    except DiscordError:
        return False
    return 200 <= int(getattr(response, "status_code", 0) or 0) < 300


def _get(http, path: str, access_token: str):
    try:
        response = http.request("GET", discord_http.api_base() + path,
                                headers={"Authorization": f"Bearer {access_token}"})
    except Exception:
        raise _network() from None
    status = int(getattr(response, "status_code", 0) or 0)
    if status == 401:
        raise DiscordError("expired", "Your Discord sign-in has expired. Connect Discord again.")
    if status == 403:
        raise DiscordError("denied", "Discord didn't allow that. Connect Discord again.")
    if status == 429:
        raise DiscordError("rate_limited", "Discord is busy. Wait a minute and try again.")
    if status >= 300:
        raise DiscordError("bad_response", f"Discord answered {status}. Try again later.")
    return discord_http.body(response)


def me(http, access_token: str) -> dict:
    """The signed-in user: {id, username, global_name}."""
    got = _get(http, "/users/@me", access_token)
    if not isinstance(got, dict) or not str(got.get("id") or ""):
        raise DiscordError("bad_response", "Discord didn't say who signed in. Try again.")
    return {"id": str(got["id"]), "username": str(got.get("username") or ""),
            "global_name": str(got.get("global_name") or "")}


def _can_manage_webhooks(guild: dict) -> bool:
    if guild.get("owner"):
        return True
    try:
        permissions = int(str(guild.get("permissions") or "0"))
    except ValueError:
        return False
    return bool(permissions & (MANAGE_WEBHOOKS | ADMINISTRATOR))


def guilds(http, access_token: str) -> list[dict]:
    """The user's servers: [{id, name, owner, can_manage_webhooks}]."""
    got = _get(http, "/users/@me/guilds", access_token)
    if not isinstance(got, list):
        raise DiscordError("bad_response", "Discord didn't send the list of your servers. Try again.")
    out = []
    for guild in got:
        if not isinstance(guild, dict) or not str(guild.get("id") or ""):
            continue
        out.append({"id": str(guild["id"]), "name": str(guild.get("name") or ""),
                    "owner": bool(guild.get("owner")),
                    "can_manage_webhooks": _can_manage_webhooks(guild)})
    return out


# ---- sign-ins in progress ---------------------------------------------------------


@dataclass
class Pending:
    purpose: str  # connect | webhook
    verifier: str
    redirect: str
    started: float
    extra: dict = field(default_factory=dict)


class PendingSignIns:
    """Sign-ins waiting for the browser to come back, by their state. In
    memory: a sign-in outlives nothing but its browser tab. Each state is
    good once, for SIGN_IN_SECONDS."""

    def __init__(self, ttl: float = SIGN_IN_SECONDS):
        self.ttl = ttl
        self._waiting: dict[str, Pending] = {}
        self._lock = threading.Lock()  # routes run in a thread pool

    def _prune(self, now: float) -> None:
        """Drop sign-ins too old to finish. The caller holds the lock."""
        for stale in [s for s, p in self._waiting.items() if now - p.started > self.ttl]:
            self._waiting.pop(stale, None)

    def start(self, purpose: str, redirect: str, *, now: float | None = None,
              extra: dict | None = None) -> tuple[str, str]:
        """A new sign-in: (state, S256 challenge) for the authorize link."""
        now = time.time() if now is None else float(now)
        verifier, challenge = pkce_pair()
        state = secrets.token_urlsafe(18)
        with self._lock:
            self._prune(now)
            self._waiting[state] = Pending(purpose, verifier, redirect, now, dict(extra or {}))
        return state, challenge

    def take(self, state: str, *, now: float | None = None) -> Pending | None:
        """The sign-in for `state`, once; None when unknown, used or too old."""
        now = time.time() if now is None else float(now)
        with self._lock:
            found = self._waiting.pop(str(state or ""), None)
            self._prune(now)
        if found is None or now - found.started > self.ttl:
            return None
        return found
