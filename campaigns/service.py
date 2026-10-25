"""Discord for Clips Kitty: the settings, the stored secrets, the sign-in and
the webhook destinations, with no FastAPI in sight (server/discord_api.py is
the routes over this), so CI's main job tests every rule here.

Where things live (DESIGN §3.4):
- the sign-in's tokens in core/secrets under model.OAUTH_SECRET, and each
  webhook destination's id and token under model.webhook_secret_name(id);
- everything else that isn't secret (who is connected, the scopes, the
  server list) in the `discord` app_state blob;
- the destinations themselves (label, server, channel, state) in the
  discord_destinations table, never with a token.

Nothing returned here, and nothing it prints, holds a token. Signing in is
optional: pasted-webhook destinations, assisted campaigns and website
campaigns all work without it, and an expired sign-in never blocks a send.
"""

import json
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from campaigns import discord_oauth, discord_webhook, links, statemachine
from campaigns.discord_http import DiscordError
from campaigns.model import OAUTH_SECRET, cell, utc_text, webhook_secret_name
from core import secrets
from server import posts

SETTINGS_KEY = "discord"

DEFAULTS = {"client_id": "", "user": {}, "scopes": [], "connected_at": "", "expired": False,
            "guilds": [], "guilds_at": 0}

#: The server list is asked for again after this long (Refresh servers asks at once).
GUILDS_CACHE_SECONDS = 10 * 60
#: The access token is refreshed once it has less than this left.
REFRESH_WITHIN_SECONDS = 24 * 3600

NOT_CONFIGURED = "Discord sign-in isn't set up in this version of Clips Kitty."
LABEL_MAX = 100

#: Held while the sign-in's tokens are refreshed, replaced or wiped. Routes
#: run in a thread pool, and Discord replaces the refresh token each time it
#: is used: two requests refreshing with the same one would see the second
#: refused, which would end a sign-in that is fine.
_TOKENS_LOCK = threading.Lock()


# ---- settings --------------------------------------------------------------------


def load_settings(db) -> dict:
    try:
        stored = json.loads(db.get_flag(SETTINGS_KEY, "") or "{}")
    except (ValueError, TypeError):
        stored = {}
    return {**DEFAULTS, **(stored if isinstance(stored, dict) else {})}


def save_settings(db, patch: dict) -> dict:
    merged = {**load_settings(db), **{k: v for k, v in patch.items() if k in DEFAULTS}}
    db.set_flag(SETTINGS_KEY, json.dumps(merged))
    return merged


def client_id(db) -> tuple[str, str]:
    """(the application id in use, where it came from): the Advanced
    override, the one Clips Kitty ships with, or none."""
    override = str(load_settings(db).get("client_id") or "").strip()
    if override:
        return override, "override"
    if discord_oauth.DISCORD_CLIENT_ID:
        return discord_oauth.DISCORD_CLIENT_ID, "shipped"
    return "", "none"


def set_client_id(db, value) -> None:
    """The Advanced override: a Discord application id, or '' for the
    shipped one. Raises ValueError with the message to show."""
    text = str(value or "").strip()
    if text and not links.SNOWFLAKE_RE.match(text):
        raise ValueError("A Discord application ID is 17 to 20 digits (Developer Portal → "
                         "your application → General Information).")
    save_settings(db, {"client_id": text})


# ---- the sign-in's tokens -----------------------------------------------------------


def save_tokens(data_dir, tokens: dict, *, now: float, keep: dict | None = None) -> dict:
    """Keep a token answer from Discord. A refresh that sends no new
    refresh token keeps the one it was made with (`keep`)."""
    payload = {
        "access_token": str(tokens.get("access_token") or ""),
        "refresh_token": str(tokens.get("refresh_token") or (keep or {}).get("refresh_token") or ""),
        "expires_at": float(now) + float(tokens.get("expires_in") or 0),
        "scope": str(tokens.get("scope") or (keep or {}).get("scope") or ""),
        "token_type": str(tokens.get("token_type") or "Bearer"),
    }
    secrets.save(Path(data_dir), OAUTH_SECRET, payload)
    return payload


def load_tokens(data_dir) -> dict | None:
    got = secrets.load(Path(data_dir), OAUTH_SECRET)
    return got if got and got.get("access_token") else None


def wipe_tokens(data_dir) -> bool:
    return secrets.wipe(Path(data_dir), OAUTH_SECRET)


def _expired(db, data_dir) -> DiscordError:
    """The sign-in can't be used any more: its tokens go, the card says
    Reconnect (the user's name stays so it can say whose)."""
    wipe_tokens(data_dir)
    save_settings(db, {"expired": True, "guilds": [], "guilds_at": 0})
    return DiscordError("expired", "Your Discord sign-in has expired. Reconnect Discord in Settings → Discord.")


def _stored_tokens(db, data_dir) -> dict:
    tokens = load_tokens(data_dir)
    if tokens is None:
        if load_settings(db).get("expired"):
            raise DiscordError("expired", "Your Discord sign-in has expired. Reconnect Discord in Settings → Discord.")
        raise DiscordError("not_connected", "Connect Discord first (Settings → Discord).")
    return tokens


def access_token(db, data_dir, http, *, now: float) -> str:
    """A usable access token, refreshed when it has less than a day left.
    Discord refusing the refresh ends the sign-in (tokens wiped, expired);
    Discord not answering keeps the old token while it still works."""
    tokens = _stored_tokens(db, data_dir)
    if float(tokens.get("expires_at") or 0) - float(now) >= REFRESH_WITHIN_SECONDS:
        return tokens["access_token"]
    with _TOKENS_LOCK:
        # Read again: another request may have refreshed while this one waited.
        tokens = _stored_tokens(db, data_dir)
        expires_at = float(tokens.get("expires_at") or 0)
        if expires_at - float(now) >= REFRESH_WITHIN_SECONDS:
            return tokens["access_token"]
        cid, _source = client_id(db)
        sent = str(tokens.get("refresh_token") or "")
        if sent and cid:
            try:
                fresh = discord_oauth.refresh(http, cid, sent)
            except DiscordError as e:
                if e.code in ("expired", "denied"):
                    stored = load_tokens(data_dir)
                    if stored is not None and str(stored.get("refresh_token") or "") != sent:
                        return stored["access_token"]  # replaced meanwhile: the refusal was for the old ones
                    raise _expired(db, data_dir) from None
                if expires_at > float(now):
                    return tokens["access_token"]
                raise
            return save_tokens(data_dir, fresh, now=now, keep=tokens)["access_token"]
        if expires_at > float(now):
            return tokens["access_token"]
        raise _expired(db, data_dir)


def connected(db, data_dir) -> bool:
    settings = load_settings(db)
    user = settings.get("user") or {}
    return bool(isinstance(user, dict) and user.get("id")) and not settings.get("expired") \
        and secrets.has(Path(data_dir), OAUTH_SECRET)


# ---- webhook secrets --------------------------------------------------------------------


def save_webhook(data_dir, dest_id: int, webhook_id: str, token: str) -> None:
    secrets.save(Path(data_dir), webhook_secret_name(dest_id), {"webhook_id": str(webhook_id), "token": str(token)})


def load_webhook(data_dir, dest_id: int) -> dict | None:
    """{"webhook_id", "token"} of a destination, or None."""
    got = secrets.load(Path(data_dir), webhook_secret_name(dest_id))
    return got if got and got.get("webhook_id") and got.get("token") else None


def wipe_webhook(data_dir, dest_id: int) -> bool:
    return secrets.wipe(Path(data_dir), webhook_secret_name(dest_id))


# ---- what the Settings card sees -------------------------------------------------------------


def destination_views(db, data_dir) -> list[dict]:
    uses: dict[int, int] = {}
    for campaign in db.list_campaigns():
        dest = int(cell(campaign, "destination_id", 0) or 0)
        if dest:
            uses[dest] = uses.get(dest, 0) + 1
    return [destination_view(db, data_dir, row, uses=uses) for row in db.list_destinations()]


def destination_view(db, data_dir, row, *, uses: dict[int, int] | None = None) -> dict:
    """A destination as the card shows it: no token, whether one is stored,
    and how many campaigns post to it."""
    dest_id = int(row["id"])
    if uses is None:
        uses = {dest_id: sum(1 for c in db.list_campaigns()
                             if int(cell(c, "destination_id", 0) or 0) == dest_id)}
    return {
        "id": dest_id, "label": row["label"], "source": row["source"], "guild_id": row["guild_id"],
        "guild_name": row["guild_name"], "channel_id": row["channel_id"],
        "webhook_name": row["webhook_name"], "is_forum": bool(int(row["is_forum"] or 0)),
        "thread_id": row["thread_id"], "state": row["state"], "last_error": row["last_error"],
        "has_token": secrets.has(Path(data_dir), webhook_secret_name(dest_id)),
        "campaigns": uses.get(dest_id, 0),
    }


def status_payload(db, data_dir, *, redirect_uri: str = "") -> dict:
    """What the renderer may know about Discord. Never a token."""
    settings = load_settings(db)
    cid, source = client_id(db)
    user = settings.get("user") if isinstance(settings.get("user"), dict) else {}
    return {
        "configured": bool(cid),
        "client_id": cid,
        "client_id_source": source,
        "connected": connected(db, data_dir),
        "expired": bool(settings.get("expired")),
        "user": {"id": str(user.get("id") or ""), "username": str(user.get("username") or ""),
                 "global_name": str(user.get("global_name") or "")},
        "scopes": list(settings.get("scopes") or []),
        "connected_at": str(settings.get("connected_at") or ""),
        "redirect_uri": redirect_uri or discord_oauth.redirect_uri(discord_oauth.DEFAULT_PORT),
        "destinations": destination_views(db, data_dir),
        "storage": secrets.backend_name(),
    }


def guild_list(db, data_dir, http, *, now: float, refresh: bool = False) -> list[dict]:
    """The user's servers, kept in the settings blob for GUILDS_CACHE_SECONDS."""
    settings = load_settings(db)
    fetched = float(settings.get("guilds_at") or 0)
    if not refresh and fetched and float(now) - fetched < GUILDS_CACHE_SECONDS and connected(db, data_dir):
        return list(settings.get("guilds") or [])
    token = access_token(db, data_dir, http, now=now)
    try:
        listed = discord_oauth.guilds(http, token)
    except DiscordError as e:
        if e.code == "expired":
            raise _expired(db, data_dir) from None
        raise
    save_settings(db, {"guilds": listed, "guilds_at": float(now)})
    return listed


def _guild_name(db, guild_id: str) -> str:
    for guild in load_settings(db).get("guilds") or []:
        if isinstance(guild, dict) and str(guild.get("id")) == str(guild_id):
            return str(guild.get("name") or "")
    return ""


# ---- signing in -----------------------------------------------------------------------------


def start_sign_in(db, pending: discord_oauth.PendingSignIns, *, purpose: str, port: int,
                  now: float, label: str = "") -> str:
    """The authorize link for the user's own browser: purpose "connect"
    (identify + guilds) or "webhook" (webhook.incoming only). The redirect
    is kept with the sign-in, so the exchange sends the same one."""
    cid, _source = client_id(db)
    if not cid:
        raise DiscordError("not_configured", NOT_CONFIGURED)
    redirect = discord_oauth.redirect_uri(port)
    scopes = (discord_oauth.WEBHOOK_SCOPE,) if purpose == "webhook" else discord_oauth.SIGN_IN_SCOPES
    extra = {"label": _label(label)} if purpose == "webhook" else {}
    state, challenge = pending.start(purpose, redirect, now=now, extra=extra)
    return discord_oauth.authorize_url(cid, redirect, state, challenge, scopes)


@dataclass
class SignIn:
    """How a browser sign-in ended: what its page says, and the event the
    app's windows get ('' when there is nothing to tell them)."""
    ok: bool
    event: str
    message: str
    destination_id: int = 0


EXPIRED_STATE = "This sign-in has expired or was already used. Start it again from Clips Kitty."
NO_WEBHOOK = ("Discord didn't hand back a webhook. Paste the channel's webhook link in Clips Kitty instead: "
              "Server Settings → Integrations → Webhooks → Copy Webhook URL.")


def finish_sign_in(db, data_dir, http, pending: discord_oauth.PendingSignIns, *, state: str,
                   code: str = "", error: str = "", now: float) -> SignIn:
    """Where the browser comes back (GET /discord/callback). Single use."""
    entry = pending.take(state, now=now)
    if entry is None:
        return SignIn(False, "", EXPIRED_STATE)
    if error or not code:
        if error == "access_denied":
            return SignIn(False, "failed", "You didn't approve Clips Kitty in Discord, so nothing changed.")
        return SignIn(False, "failed", "Discord didn't finish signing you in. Start it again from Clips Kitty.")
    cid, _source = client_id(db)
    if not cid:
        return SignIn(False, "failed", NOT_CONFIGURED)
    try:
        tokens = discord_oauth.exchange(http, cid, code, entry.verifier, entry.redirect)
    except DiscordError as e:
        return SignIn(False, "failed", e.message)
    if entry.purpose == "webhook":
        return _webhook_from(db, data_dir, http, cid, tokens, entry)
    try:
        user = discord_oauth.me(http, tokens["access_token"])
    except DiscordError as e:
        discord_oauth.revoke(http, cid, str(tokens.get("refresh_token") or tokens["access_token"]),
                             "refresh_token" if tokens.get("refresh_token") else "access_token")
        return SignIn(False, "failed", e.message)
    with _TOKENS_LOCK:
        saved = save_tokens(data_dir, tokens, now=now)
        save_settings(db, {"user": user, "scopes": saved["scope"].split(), "expired": False,
                           "connected_at": utc_text(posts.as_utc(now)), "guilds": [], "guilds_at": 0})
    name = user.get("global_name") or user.get("username") or "your account"
    return SignIn(True, "connected", f"Connected to Discord as {name}. You can close this tab and go back "
                                     "to Clips Kitty.")


def _webhook_from(db, data_dir, http, cid: str, tokens: dict, entry) -> SignIn:
    """webhook.incoming: the webhook becomes a destination; the token that
    came with it is revoked at once and never kept."""
    discord_oauth.revoke(http, cid, str(tokens.get("access_token") or ""), "access_token")
    hook = tokens.get("webhook")
    if not isinstance(hook, dict) or not links.SNOWFLAKE_RE.match(str(hook.get("id") or "")) \
            or not str(hook.get("token") or ""):
        return SignIn(False, "failed", NO_WEBHOOK)
    webhook_id, token = str(hook["id"]), str(hook["token"])
    guild_id = str(hook.get("guild_id") or "")
    name = str(hook.get("name") or "")[:LABEL_MAX]
    dest_id = db.insert_destination(
        kind="webhook", source="oauth", label=entry.extra.get("label") or name or "Webhook",
        guild_id=guild_id, guild_name=_guild_name(db, guild_id), channel_id=str(hook.get("channel_id") or ""),
        webhook_id=webhook_id, webhook_name=name, state="connected",
    )
    try:
        save_webhook(data_dir, dest_id, webhook_id, token)
    except Exception:
        db.delete_destination(dest_id)
        return SignIn(False, "failed", "Clips Kitty couldn't store the webhook on this PC. Try again.")
    return SignIn(True, "destination_added", "Added the webhook to Clips Kitty. You can close this tab and go back.",
                  dest_id)


def disconnect(db, data_dir, http) -> bool:
    """Sign out: ask Discord to revoke (the refresh token, which ends the
    grant, else the access token), then forget everything locally whatever
    Discord said. True when Discord confirmed the revoke."""
    with _TOKENS_LOCK:  # a refresh in flight can't put the tokens back after this
        tokens = load_tokens(data_dir) or {}
        cid, _source = client_id(db)
        revoked = False
        if cid and tokens.get("refresh_token"):
            revoked = discord_oauth.revoke(http, cid, tokens["refresh_token"], "refresh_token")
        if cid and not revoked and tokens.get("access_token"):
            revoked = discord_oauth.revoke(http, cid, tokens["access_token"], "access_token")
        wipe_tokens(data_dir)
        save_settings(db, {"user": {}, "scopes": [], "connected_at": "", "expired": False, "guilds": [],
                           "guilds_at": 0})
    return revoked


# ---- webhook destinations ---------------------------------------------------------------------


def _label(value) -> str:
    text = str(value or "").strip()
    if len(text) > LABEL_MAX:
        raise ValueError(f"The label is too long: at most {LABEL_MAX} characters")
    return text


def _thread(value) -> str:
    text = str(value or "").strip()
    if text and not links.SNOWFLAKE_RE.match(text):
        raise ValueError("A thread ID is 17 to 20 digits: right-click the thread → Copy Thread ID")
    return text


def _same_webhook(db, webhook_id: str, *, other_than: int = 0):
    for row in db.list_destinations():
        if row["webhook_id"] == webhook_id and int(row["id"]) != int(other_than):
            return row
    return None


def _checked_webhook(db, http, webhook_url: str, *, other_than: int = 0) -> tuple[str, str, dict]:
    webhook_id, token = links.parse_webhook_url(webhook_url)
    clash = _same_webhook(db, webhook_id, other_than=other_than)
    if clash is not None:
        raise ValueError(f"That webhook is already a destination: “{clash['label']}”. "
                         "To give it a new link, edit that one.")
    return webhook_id, token, discord_webhook.inspect(http, webhook_id, token)


def add_destination(db, data_dir, http, *, webhook_url: str, label: str = "", is_forum: bool = False,
                    thread_id: str = "") -> dict:
    """A pasted webhook URL, checked with Discord before it is kept. Raises
    ValueError (a link that isn't a webhook's, a duplicate) or DiscordError
    (Discord doesn't know it, or didn't answer)."""
    label, thread_id = _label(label), _thread(thread_id)
    webhook_id, token, info = _checked_webhook(db, http, webhook_url)
    dest_id = db.insert_destination(
        kind="webhook", source="pasted", label=label or info["name"][:LABEL_MAX] or "Webhook",
        guild_id=info["guild_id"], guild_name=_guild_name(db, info["guild_id"]),
        channel_id=info["channel_id"], webhook_id=webhook_id, webhook_name=info["name"][:LABEL_MAX],
        is_forum=int(bool(is_forum)), thread_id=thread_id, state="connected",
    )
    try:
        save_webhook(data_dir, dest_id, webhook_id, token)
    except Exception:
        db.delete_destination(dest_id)
        raise
    return destination_view(db, data_dir, db.get_destination(dest_id))


def update_destination(db, data_dir, http, dest_id: int, *, label=None, is_forum=None, thread_id=None,
                       webhook_url=None) -> dict | None:
    """Change a destination; a new webhook URL is checked like a new one
    and brings it back to connected. None when there is no such destination."""
    row = db.get_destination(dest_id)
    if row is None:
        return None
    fields: dict = {}
    if label is not None:
        fields["label"] = _label(label) or row["webhook_name"] or "Webhook"
    if is_forum is not None:
        fields["is_forum"] = int(bool(is_forum))
    if thread_id is not None:
        fields["thread_id"] = _thread(thread_id)
    token = ""
    if webhook_url is not None:
        webhook_id, token, info = _checked_webhook(db, http, webhook_url, other_than=dest_id)
        fields.update(webhook_id=webhook_id, webhook_name=info["name"][:LABEL_MAX], guild_id=info["guild_id"],
                      guild_name=_guild_name(db, info["guild_id"]), channel_id=info["channel_id"],
                      state="connected", last_error="")
    if token:
        save_webhook(data_dir, dest_id, fields["webhook_id"], token)
    if fields:
        db.set_destination(dest_id, **fields)
    return destination_view(db, data_dir, db.get_destination(dest_id))


REMOVED_REASON = "The webhook was removed: submit it by hand or pick another"


def remove_destination(db, data_dir, dest_id: int, *, now) -> dict | None:
    """Delete a destination and its token. Its submissions waiting to be
    sent go to be done by hand; its campaigns keep pointing at it, so their
    check says the webhook is missing until another is picked."""
    row = db.get_destination(dest_id)
    if row is None:
        return None
    affected = sum(1 for c in db.list_campaigns() if int(cell(c, "destination_id", 0) or 0) == int(dest_id))
    for sub in db.list_submissions(statuses=("ready",), limit=1_000_000):
        if int(cell(sub, "destination_id", 0) or 0) != int(dest_id):
            continue
        try:
            statemachine.apply(db, int(sub["id"]), "destination_unusable", now=now, status_reason=REMOVED_REASON)
        except statemachine.NotAllowed:
            continue  # not a webhook row, or it changed meanwhile
    wipe_webhook(data_dir, dest_id)
    db.delete_destination(dest_id)
    return {"removed": True, "campaigns_affected": affected}


def send_test_message(db, data_dir, http, dest_id: int) -> dict | None:
    """Post the test message (the card confirms first): {ok, message_url,
    error}. A webhook Discord no longer knows is marked gone and its token
    wiped; one not allowed to post, denied. None when there is no such
    destination."""
    row = db.get_destination(dest_id)
    if row is None:
        return None
    hook = load_webhook(data_dir, dest_id)
    if hook is None:
        return {"ok": False, "message_url": "",
                "error": "This destination has no webhook link stored on this PC. Paste its link again under Edit."}
    thread_id = str(row["thread_id"] or "")
    forum = bool(int(row["is_forum"] or 0))
    result = discord_webhook.send(http, hook["webhook_id"], hook["token"], content=discord_webhook.TEST_MESSAGE,
                                  thread_name=discord_webhook.TEST_THREAD_NAME if forum and not thread_id else "",
                                  thread_id=thread_id)
    fields: dict = {"last_sent_at": time.time()}
    if result.kind == "ok":
        fields.update(state="connected", last_error="")
    elif result.kind == "gone":
        fields.update(state="gone", last_error=result.message)
        wipe_webhook(data_dir, dest_id)
    elif result.kind == "denied":
        fields.update(state="denied", last_error=result.message)
    elif result.kind in ("forum_needs_thread", "bad_request"):
        fields["last_error"] = result.message
    db.set_destination(dest_id, **fields)
    url = links.channel_url(row["guild_id"], result.channel_id or row["channel_id"], result.message_id) \
        if result.kind == "ok" and result.message_id else ""
    return {"ok": result.kind == "ok", "message_url": url, "error": "" if result.kind == "ok" else result.message}
