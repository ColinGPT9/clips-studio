"""Deliver approved generic digests using existing saved Discord webhooks.

The digest service owns the durable send transition and crash protection.
Discord webhooks do not support an idempotency key: never retry an ambiguous
response, and never log a webhook, response body, or exception text here.
"""

from __future__ import annotations

import math
import threading
import time
from contextlib import closing

from campaigns import discord_webhook, service
from campaigns.model import SEND_SPACING_SECONDS
from stream_workflows.digests import DeliveryNotSent

_GLOBAL_PAUSE = "stream_digest_discord_paused_until"


def _units(text):
    return len(text.encode("utf-16-le")) // 2


def _shorten(text, limit):
    # Titles are already bounded, single-line plain text. Remove Discord's
    # inline formatting characters so a title cannot disguise another link.
    text = "".join(c for c in text if c not in "\\`*_~|[]()<>")
    while text and _units(text) > limit:
        text = text[:-1]
    return text


def content_for(digest):
    """One message within Discord's 2,000 UTF-16-unit content limit."""
    items = digest["items"]
    header = "Stream catch-up"
    fixed = header + "".join(f"\n{i}. \n{item['url']}" for i, item in enumerate(items, 1))
    budget = max(0, (2000 - _units(fixed)) // max(1, len(items)))
    return header + "".join(
        f"\n{i}. {_shorten(item['title'], budget)}\n{item['url']}"
        for i, item in enumerate(items, 1)
    )


class DiscordDigestDelivery:
    def __init__(self, db, data_dir, http, *, clock=time.time):
        # Match the host's db() factory: SQLite connections are created and
        # closed on the sending thread, never shared between API workers.
        self.db, self.data_dir, self.http = db, data_dir, http
        self.clock = clock
        self._lock = threading.Lock()

    def send(self, digest, *, idempotency_key):
        # The reviewed digest freezes its destination; never fall back to the
        # first saved webhook or derive a destination from an account.
        destination_id = digest.get("destination_id")
        if not isinstance(destination_id, int) or isinstance(destination_id, bool):
            raise DeliveryNotSent()
        with self._lock, closing(self.db()) as database:
            now = float(self.clock())
            row = database.get_destination(destination_id)
            if row is None or row["state"] != "connected":
                raise DeliveryNotSent()
            hook = service.load_webhook(self.data_dir, destination_id)
            if hook is None:
                raise DeliveryNotSent()
            paused_until = float(database.get_flag(_GLOBAL_PAUSE, "0") or 0)
            if now < paused_until or now - float(row["last_sent_at"] or 0) < SEND_SPACING_SECONDS:
                raise DeliveryNotSent()
            thread_id = str(row["thread_id"] or "")
            # The existing worker also reads this shared spacing reservation.
            database.set_destination(destination_id, last_sent_at=now)
            result = discord_webhook.send(
                self.http, hook["webhook_id"], hook["token"], content=content_for(digest),
                thread_id=thread_id,
                thread_name="Stream catch-up" if row["is_forum"] and not thread_id else "",
            )
            if result.kind == "ok":
                return
            if result.kind == "rate_limited":
                wait = result.retry_after
                if not math.isfinite(wait) or wait < 0:
                    wait = 60.0
                deadline = now + max(1.0, wait)
                # A future last-send time persists the destination cooldown
                # across restart and is honored by existing webhook sends.
                database.set_destination(destination_id, last_sent_at=deadline)
                if result.is_global:
                    database.set_flag(_GLOBAL_PAUSE, str(max(paused_until, deadline)))
                raise DeliveryNotSent()
            if result.kind in {"gone", "denied", "forum_needs_thread", "bad_request", "unreachable"}:
                raise DeliveryNotSent()
            # A timeout, dropped connection, or 5xx may follow a remote write.
            raise RuntimeError("Digest delivery could not be confirmed.")
