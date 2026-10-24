"""Posting a message through a Discord webhook, and checking a webhook.

A webhook is the one way Clips Kitty posts to Discord by itself: a server
admin made it (or approved it through `webhook.incoming`) for one channel,
and its URL carries the token that lets anyone holding it post there. So
the token goes to Discord in the request path only, and never into a result,
a message or a log line; a requests exception names the URL it failed on,
so none is ever passed on either.

Only a message is posted, with mentions switched off. A webhook can't run
another bot's slash command.
"""

import re
from dataclasses import dataclass
from urllib.parse import quote

from campaigns import discord_http
from campaigns.discord_http import DiscordError
from core.scrub import scrub_secrets

#: What Test posts (Settings → Discord → Destinations), after a confirm.
TEST_MESSAGE = "Clips Kitty test message. You can delete it."
#: A forum channel takes a message only as the first post of a new thread.
TEST_THREAD_NAME = "Clips Kitty test"

#: Discord's error codes: Unknown Webhook, Invalid Webhook Token, Missing
#: Permissions, a forum channel's message needing a thread.
UNKNOWN_WEBHOOK, INVALID_TOKEN, MISSING_PERMISSIONS, FORUM_NEEDS_THREAD = 10015, 50027, 50013, 220001


@dataclass
class SendResult:
    # ok | rate_limited | gone | denied | forum_needs_thread | bad_request | server_error | unreachable | ambiguous
    kind: str
    message_id: str = ""
    channel_id: str = ""
    retry_after: float = 0.0
    is_global: bool = False
    status: int = 0
    code: int = 0
    message: str = ""  # already scrubbed


_ID_SHAPE = re.compile(r"\d{17,20}")
_TOKEN_SHAPE = re.compile(r"[\w-]{1,200}")
NOT_A_WEBHOOK = "The stored webhook link isn't a Discord webhook's. Paste its link again."


def _url(webhook_id: str, token: str) -> str:
    """The webhook's address. Its id and token must have a webhook's shape
    (a slash or a dot would walk off /webhooks/), and are quoted anyway.
    Raises ValueError otherwise."""
    webhook_id, token = str(webhook_id or ""), str(token or "")
    if not _ID_SHAPE.fullmatch(webhook_id) or not _TOKEN_SHAPE.fullmatch(token):
        raise ValueError(NOT_A_WEBHOOK)
    return f"{discord_http.api_base()}/webhooks/{quote(webhook_id, safe='')}/{quote(token, safe='')}"


def _clean(text: str, token: str) -> str:
    """Discord's words, safe to show and store: no token, nothing key-shaped."""
    text = str(text or "")
    if token:
        text = text.replace(token, "[redacted]")
    return scrub_secrets(text)[:300]


def _code(got) -> int:
    try:
        return int(got.get("code") or 0) if isinstance(got, dict) else 0
    except (TypeError, ValueError):
        return 0


def _retry_after(response, got) -> float:
    for value in ((got or {}).get("retry_after") if isinstance(got, dict) else None,
                  (getattr(response, "headers", None) or {}).get("Retry-After")):
        try:
            seconds = float(value)
        except (TypeError, ValueError):
            continue
        if seconds >= 0:
            return seconds
    return 0.0


def _is_global(response, got) -> bool:
    headers = getattr(response, "headers", None) or {}
    return bool(isinstance(got, dict) and got.get("global")) \
        or str(headers.get("X-RateLimit-Global") or "").lower() == "true" \
        or str(headers.get("X-RateLimit-Scope") or "").lower() == "global"


def classify(response, token: str = "") -> SendResult:
    """What Discord's answer to a webhook request means (DESIGN §5.5)."""
    status = int(getattr(response, "status_code", 0) or 0)
    got = discord_http.body(response)
    code = _code(got)
    said = _clean(got.get("message") if isinstance(got, dict) else "", token)
    if 200 <= status < 300:
        message = got if isinstance(got, dict) else {}
        return SendResult("ok", message_id=str(message.get("id") or ""),
                          channel_id=str(message.get("channel_id") or ""), status=status)
    if status == 429:
        return SendResult("rate_limited", retry_after=_retry_after(response, got),
                          is_global=_is_global(response, got), status=status, code=code,
                          message="Discord asked Clips Kitty to slow down")
    detail = f": {said}" if said else ""
    if status == 404 or code in (UNKNOWN_WEBHOOK, INVALID_TOKEN):
        return SendResult("gone", status=status, code=code,
                          message=f"The webhook doesn't exist any more{detail}")
    if status in (401, 403) or code == MISSING_PERMISSIONS:
        return SendResult("denied", status=status, code=code,
                          message=f"The webhook isn't allowed to post there{detail}")
    if status == 400 and code == FORUM_NEEDS_THREAD:
        return SendResult("forum_needs_thread", status=status, code=code,
                          message="This channel is a forum: set Forum on the destination")
    if 400 <= status < 500:
        return SendResult("bad_request", status=status, code=code,
                          message=f"Discord refused the message ({status}){detail}")
    return SendResult("server_error", status=status, code=code,
                      message=f"Discord had a problem ({status}). Clips Kitty will try again.")


def failed(exc: BaseException) -> SendResult:
    """A request that raised: never sent, or maybe sent. Nothing of the
    exception's text, which names the URL and so the token."""
    if discord_http.never_sent(exc):
        return SendResult("unreachable", message="Couldn't reach Discord")
    return SendResult("ambiguous", message="Discord didn't answer in time: check the channel, "
                                           "it may have been posted")


def send(http, webhook_id: str, token: str, *, content: str, thread_name: str = "",
         thread_id: str = "") -> SendResult:
    """Post `content` through the webhook and wait for Discord's answer."""
    params = {"wait": "true"}
    if thread_id:
        params["thread_id"] = str(thread_id)
    payload: dict = {"content": content, "allowed_mentions": {"parse": []}}
    if thread_name and not thread_id:
        payload["thread_name"] = thread_name
    try:
        url = _url(webhook_id, token)
    except ValueError:
        return SendResult("gone", message=NOT_A_WEBHOOK)
    try:
        response = http.request("POST", url, params=params, json=payload)
    except Exception as e:
        return failed(e)
    return classify(response, token)


def inspect(http, webhook_id: str, token: str) -> dict:
    """What a webhook is: {id, guild_id, channel_id, name}. Raises
    DiscordError ("gone" for a webhook that doesn't exist or whose token is
    wrong) with a message safe to show."""
    try:
        url = _url(webhook_id, token)
    except ValueError:
        raise DiscordError("gone", NOT_A_WEBHOOK) from None
    try:
        response = http.request("GET", url)
    except Exception:
        raise DiscordError("network", "Couldn't reach Discord to check the webhook. Try again.") from None
    result = classify(response, token)
    if result.kind == "ok":
        got = discord_http.body(response)
        got = got if isinstance(got, dict) else {}
        return {"id": str(got.get("id") or webhook_id), "guild_id": str(got.get("guild_id") or ""),
                "channel_id": str(got.get("channel_id") or ""), "name": str(got.get("name") or "")}
    if result.kind == "gone":
        raise DiscordError("gone", "Discord doesn't know that webhook: it was deleted or its link "
                                   "was reset. Copy its link again in Discord.")
    if result.kind == "rate_limited":
        raise DiscordError("rate_limited", "Discord is busy. Wait a minute and try again.")
    if result.kind == "denied":
        raise DiscordError("denied", result.message)
    raise DiscordError("bad_response", result.message)
