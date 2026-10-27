"""Host-owned Twitch EventSub WebSockets and bounded, token-free events.

The welcome message, rather than a plugin or renderer, supplies the session ID
used for subscriptions. A redirected/reconnecting socket is accepted only on
Twitch's fixed EventSub host. Native plugins still run as the local OS user;
this is an API permission boundary, not an operating-system sandbox.
"""

from __future__ import annotations

import json
import re
import secrets
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from urllib.parse import urlsplit

from connected_accounts.models import AccountError

SOCKET_URL = "wss://eventsub.wss.twitch.tv/ws"
EVENT_TYPES = frozenset({"stream.online", "stream.offline", "channel.update", "channel.chat.message"})
MAX_EVENTS = 500
_NUMBER = re.compile(r"^[0-9]{1,30}$")


def _text(value, limit=500) -> str:
    return value[:limit] if isinstance(value, str) else ""


def _reconnect_url(value) -> str:
    if not isinstance(value, str) or len(value) > 4096 or any(ord(c) < 33 for c in value):
        raise ValueError("Invalid EventSub reconnect destination")
    parsed = urlsplit(value)
    if (parsed.scheme != "wss" or parsed.hostname != "eventsub.wss.twitch.tv"
            or parsed.username is not None or parsed.password is not None or parsed.port is not None
            or parsed.fragment or parsed.path != "/ws"):
        raise ValueError("Invalid EventSub reconnect destination")
    return value


def _connect(url):
    from websockets.sync.client import connect

    # No implicit redirects, unbounded incoming messages or compression state.
    return connect(url, open_timeout=10, close_timeout=2, max_size=1_048_576,
                   max_queue=16, compression=None)


@dataclass
class _Stream:
    account_id: str
    broadcaster_id: str
    types: tuple[str, ...]
    stopped: threading.Event = field(default_factory=threading.Event)
    lock: threading.RLock = field(default_factory=threading.RLock)
    sockets: list = field(default_factory=list)
    buffer: deque = field(default_factory=lambda: deque(maxlen=MAX_EVENTS))
    seen: deque = field(default_factory=lambda: deque(maxlen=1000))
    cursor: int = 0
    status: str = "connecting"
    error: str = ""
    thread: threading.Thread | None = None


class _Revoked(Exception):
    pass


class EventSubManager:
    """One listener per connected account; all subscriptions are host-owned.

    ``connector`` and ``clock`` permit deterministic offline tests. Each
    ``start`` names its consumer (the desktop, stream-workflow monitoring, one
    plugin run); the listener subscribes to the union of their event types.
    ``release`` drops one consumer: the last one out stops the listener and
    clears its buffer, and a narrower union resubscribes without the types
    nobody needs (chat stops being read when no consumer wants it). ``stop``
    and ``close`` must be called by the host on account disconnection and
    application exit.

    After every fresh subscription, sinks get a ``session.subscribed`` event
    (never buffered): EventSub does not replay ``stream.online`` for a stream
    that started while the socket was reconnecting.
    """

    def __init__(self, service, *, connector=None, clock=time.monotonic):
        self.service = service
        self._connect = connector or _connect
        self._clock = clock
        self._lock = threading.RLock()
        self._streams: dict[str, _Stream] = {}
        self._sequences: dict[str, int] = {}
        self._sequence_lock = threading.Lock()
        self._closed = False
        self._sinks = []
        self._consumers: dict[str, dict[str, tuple[str, ...]]] = {}

    def add_sink(self, callback):
        """Receive sanitized host events; OAuth credentials never reach a sink."""
        if not callable(callback):
            raise TypeError("An event sink must be callable")
        with self._lock:
            if callback not in self._sinks:
                self._sinks.append(callback)

    def remove_sink(self, callback):
        with self._lock:
            self._sinks = [sink for sink in self._sinks if sink != callback]

    def start(self, account_id: str, broadcaster_id: str, event_types, *, consumer: str = "desktop") -> dict:
        if not isinstance(broadcaster_id, str) or not _NUMBER.fullmatch(broadcaster_id):
            raise AccountError("invalid_request", "Choose a valid Twitch channel.")
        if (not isinstance(event_types, (list, tuple)) or not event_types or len(event_types) > 4
                or any(not isinstance(t, str) or t not in EVENT_TYPES for t in event_types)):
            raise AccountError("invalid_request", "Choose supported Twitch event types.")
        types = tuple(sorted(set(event_types)))
        account = next((a for a in self.service.accounts() if a.get("id") == account_id), None)
        if account is None or account.get("provider") != "twitch":
            raise AccountError("account_not_found", "Connect a Twitch account first.", status=404)
        if account.get("status") != "connected":
            raise AccountError("reconnect_required", "Reconnect Twitch to receive events.", status=401)
        if not isinstance(account.get("user_id"), str) or not _NUMBER.fullmatch(account["user_id"]):
            raise AccountError("reconnect_required", "Reconnect Twitch before receiving events.", status=401)
        if "channel.chat.message" in types and "user:read:chat" not in account.get("scopes", []):
            raise AccountError("missing_scope", "Reconnect Twitch with permission to read chat.", status=403)
        with self._lock:
            if self._closed:
                raise AccountError("service_closed", "Connected Accounts is stopping.", status=503)
            consumers = self._consumers.setdefault(account_id, {})
            existing = self._streams.get(account_id)
            if existing is not None and existing.broadcaster_id != broadcaster_id:
                if any(name != consumer for name in consumers):
                    raise AccountError("channel_in_use", "Another feature is monitoring a different channel. "
                                       "Stop it before changing the channel.", status=409)
                # Switching channels is a new selection, not permission to
                # carry the previous channel's requested chat types across.
                consumers.clear()
            consumers[consumer] = tuple(sorted(set(types) | set(consumers.get(consumer, ()))))
            types = self._union(consumers)
            if "user:read:chat" not in account.get("scopes", []):
                types = tuple(t for t in types if t != "channel.chat.message")
            if (existing is not None and existing.broadcaster_id == broadcaster_id
                    and existing.types == types and existing.thread is not None
                    and existing.thread.is_alive()):
                return self._status(existing)
            stream, replaced = self._launch(account_id, broadcaster_id, types)
        self._join(replaced)
        return self._status(stream)

    @staticmethod
    def _union(consumers: dict) -> tuple[str, ...]:
        return tuple(sorted(set().union(*consumers.values()))) if consumers else ()

    def _launch(self, account_id: str, broadcaster_id: str,
                types: tuple[str, ...]) -> tuple[_Stream, _Stream | None]:
        """Replace the account's listener. Call with the manager lock held,
        then ``_join`` the replaced one after releasing it: its thread may be
        delivering an event, which takes the manager lock."""
        existing = self._streams.get(account_id)
        # Closing the previous socket first avoids duplicate Twitch
        # subscriptions racing the replacement welcome/subscription call.
        if existing is not None:
            self._stop(existing, join=False)
        stream = _Stream(account_id, broadcaster_id, types)
        stream.thread = threading.Thread(target=self._run, args=(stream,), daemon=True,
                                         name="twitch-eventsub")
        self._streams[account_id] = stream
        stream.thread.start()
        return stream, existing

    def release(self, account_id: str, consumer: str) -> None:
        """One consumer is done. Stop or narrow the listener to what is left."""
        stopped = None
        with self._lock:
            consumers = self._consumers.get(account_id)
            if not consumers or consumers.pop(consumer, None) is None:
                return
            stream = self._streams.get(account_id)
            types = self._union(consumers)
            if not consumers or stream is None or not types:
                self._consumers.pop(account_id, None)
                stopped = self._streams.pop(account_id, None)
            elif not self._closed and set(stream.types) - set(types):
                _, stopped = self._launch(account_id, stream.broadcaster_id,
                                          tuple(t for t in stream.types if t in types))
        if stopped is not None:
            self._stop(stopped)

    @staticmethod
    def _status(stream: _Stream) -> dict:
        with stream.lock:
            result = {"status": stream.status, "broadcaster_id": stream.broadcaster_id,
                      "event_types": list(stream.types)}
            if stream.error:
                result["error"] = stream.error
            return result

    def events(self, account_id: str, *, broadcaster_id: str | None = None,
               after: int = 0, limit: int = 100) -> dict:
        if not isinstance(after, int) or isinstance(after, bool) or after < 0:
            raise AccountError("invalid_request", "The event cursor must be a nonnegative integer.")
        if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 100:
            raise AccountError("invalid_request", "Request between one and 100 events.")
        account = next((a for a in self.service.accounts() if a.get("id") == account_id), None)
        if account is None or account.get("status") != "connected":
            self.stop(account_id)
            return {"status": "reconnect_required", "events": [], "cursor": after}
        with self._lock:
            stream = self._streams.get(account_id)
        if stream is None or (broadcaster_id is not None and broadcaster_id != stream.broadcaster_id):
            return {"status": "stopped", "events": [], "cursor": after}
        with stream.lock:
            if "user:read:chat" not in account.get("scopes", []):
                stream.buffer = deque((e for e in stream.buffer if e["type"] != "channel.chat.message"),
                                      maxlen=MAX_EVENTS)
            available = [dict(e, data=dict(e["data"])) for e in stream.buffer if e["sequence"] > after]
            result = self._status(stream)
            result["events"] = available[:limit]
            result["cursor"] = result["events"][-1]["sequence"] if result["events"] else max(after, stream.cursor)
            result["truncated"] = bool(stream.buffer and after < stream.buffer[0]["sequence"] - 1)
            return result

    def stop(self, account_id: str) -> None:
        with self._lock:
            self._consumers.pop(account_id, None)
            stream = self._streams.pop(account_id, None)
        if stream is not None:
            self._stop(stream)

    def close(self) -> None:
        with self._lock:
            self._closed = True
            streams = list(self._streams.values())
            self._streams.clear()
            self._consumers.clear()
            self._sinks.clear()
        for stream in streams:
            self._stop(stream)

    @staticmethod
    def _close_socket(socket) -> None:
        try:
            socket.close()
        except Exception:
            # Closing is best effort; a socket error must not mask the reason
            # the listener is stopping or reconnecting.
            pass

    def _stop(self, stream: _Stream, *, join: bool = True) -> None:
        stream.stopped.set()
        with stream.lock:
            sockets = list(stream.sockets)
            stream.buffer.clear()
            stream.status = "stopped"
        for socket in sockets:
            self._close_socket(socket)
        if join:
            self._join(stream)

    @staticmethod
    def _join(stream: _Stream | None) -> None:
        if stream is not None and stream.thread is not None and stream.thread is not threading.current_thread():
            stream.thread.join(timeout=3)

    @staticmethod
    def _packet(socket, timeout: float) -> dict:
        raw = socket.recv(timeout=max(0.001, timeout))
        if not isinstance(raw, (str, bytes)) or len(raw) > 1_048_576:
            raise ValueError("Invalid EventSub message")
        packet = json.loads(raw)
        if not isinstance(packet, dict) or not isinstance(packet.get("metadata"), dict):
            raise ValueError("Invalid EventSub message")
        if not isinstance(packet.get("payload"), dict):
            raise ValueError("Invalid EventSub message")
        return packet

    def _welcome(self, socket) -> tuple[str, float]:
        packet = self._packet(socket, 10)
        session = packet["payload"].get("session")
        if packet["metadata"].get("message_type") != "session_welcome" or not isinstance(session, dict):
            raise ValueError("EventSub did not welcome the session")
        session_id = session.get("id")
        timeout = session.get("keepalive_timeout_seconds")
        if (not isinstance(session_id, str) or not 1 <= len(session_id) <= 512
                or not isinstance(timeout, (int, float)) or isinstance(timeout, bool)
                or not 1 <= timeout <= 600):
            raise ValueError("Invalid EventSub session")
        return session_id, float(timeout)

    def _run(self, stream: _Stream) -> None:
        socket = None
        pending_socket = None
        url, transferring = SOCKET_URL, False
        failures = 0
        try:
            while not stream.stopped.is_set():
                try:
                    pending_socket = self._connect(url)
                    with stream.lock:
                        stream.sockets.append(pending_socket)
                    if stream.stopped.is_set():
                        break
                    session_id, timeout = self._welcome(pending_socket)
                    subscribed = not transferring
                    if subscribed:
                        self.service.perform(stream.account_id, "subscribe_events",
                                             broadcaster_id=stream.broadcaster_id,
                                             event_types=list(stream.types), session_id=session_id)
                    # A Twitch-directed transfer keeps the old socket alive until
                    # the replacement welcome; Twitch transfers subscriptions.
                    if socket is not None:
                        self._close_socket(socket)
                        with stream.lock:
                            stream.sockets.remove(socket)
                    socket, pending_socket = pending_socket, None
                    with stream.lock:
                        stream.status, stream.error = "connected", ""
                    failures = 0
                    if subscribed:
                        self._announce(stream)
                    url = self._listen(stream, socket, timeout)
                    transferring = True
                    with stream.lock:
                        stream.status = "reconnecting"
                except _Revoked:
                    with stream.lock:
                        stream.status = "reconnect_required"
                        stream.error = "Twitch revoked this subscription. Reconnect your account."
                    break
                except AccountError as error:
                    if error.code in {"reconnect_required", "invalid_token", "account_not_found",
                                      "missing_scope", "permission_denied", "provider_not_configured"}:
                        with stream.lock:
                            stream.status = "reconnect_required"
                            stream.error = "Reconnect Twitch with permission for these events."
                        break
                    self._discard_sockets(stream)
                    socket = pending_socket = None
                    url, transferring = SOCKET_URL, False
                    with stream.lock:
                        stream.status, stream.error = "reconnecting", "Twitch events are temporarily unavailable."
                    failures += 1
                    stream.stopped.wait(min(30, 2 ** min(failures - 1, 5)))
                except Exception:
                    # Network exceptions may include sensitive upstream text.
                    self._discard_sockets(stream)
                    socket = pending_socket = None
                    url, transferring = SOCKET_URL, False
                    with stream.lock:
                        stream.status, stream.error = "reconnecting", "Twitch events are temporarily unavailable."
                    failures += 1
                    stream.stopped.wait(min(30, 2 ** min(failures - 1, 5)))
        finally:
            self._discard_sockets(stream)

    def _discard_sockets(self, stream: _Stream) -> None:
        with stream.lock:
            sockets = stream.sockets[:]
            stream.sockets.clear()
        for socket in sockets:
            self._close_socket(socket)

    def _listen(self, stream: _Stream, socket, timeout: float) -> str:
        deadline = self._clock() + timeout
        while not stream.stopped.is_set():
            remaining = deadline - self._clock()
            if remaining <= 0:
                raise TimeoutError("EventSub keepalive expired")
            packet = self._packet(socket, remaining)
            deadline = self._clock() + timeout
            kind = packet["metadata"].get("message_type")
            if kind == "session_reconnect":
                session = packet["payload"].get("session") or {}
                return _reconnect_url(session.get("reconnect_url"))
            if kind == "revocation":
                raise _Revoked()
            if kind == "notification":
                self._notification(stream, packet)
            elif kind != "session_keepalive":
                raise ValueError("Unexpected EventSub message")
        raise ConnectionError("EventSub stopped")

    def _announce(self, stream: _Stream) -> None:
        """Tell sinks a fresh subscription began; events in the gap are lost."""
        event = {"id": "session-" + secrets.token_hex(16), "type": "session.subscribed",
                 "broadcaster_id": stream.broadcaster_id,
                 "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "data": {}}
        self._deliver(stream, event)

    def _deliver(self, stream: _Stream, event: dict) -> None:
        with self._lock:
            sinks = list(self._sinks)
        for sink in sinks:
            try:
                sink(stream.account_id, dict(event, data=dict(event["data"])))
            except Exception:
                # A failed optional recorder must not tear down subscriptions
                # or put chat/provider data in the engine's exception log.
                continue

    def _notification(self, stream: _Stream, packet: dict) -> None:
        subscription = packet["payload"].get("subscription")
        event = packet["payload"].get("event")
        if not isinstance(subscription, dict) or not isinstance(event, dict):
            raise ValueError("Invalid EventSub notification")
        kind = subscription.get("type")
        condition = subscription.get("condition") or {}
        if (kind not in stream.types or not isinstance(condition, dict)
                or str(condition.get("broadcaster_user_id")) != stream.broadcaster_id
                or str(event.get("broadcaster_user_id")) != stream.broadcaster_id):
            return
        message_id = _text(packet["metadata"].get("message_id"), 512)
        if not message_id:
            return
        fields = ("broadcaster_user_id", "broadcaster_user_login", "broadcaster_user_name")
        data = {key: _text(event.get(key)) for key in fields}
        if kind == "stream.online":
            for key in ("id", "type", "started_at"):
                data[key] = _text(event.get(key))
        elif kind == "channel.update":
            for key in ("title", "language", "category_id", "category_name"):
                data[key] = _text(event.get(key))
        elif kind == "channel.chat.message":
            for key in ("chatter_user_id", "chatter_user_login", "chatter_user_name", "message_id"):
                data[key] = _text(event.get(key))
            message = event.get("message")
            data["text"] = _text(message.get("text") if isinstance(message, dict) else "", 2000)
        with stream.lock:
            if stream.stopped.is_set() or message_id in stream.seen:
                return
            stream.seen.append(message_id)
            # A restarted subscription must not reuse a cursor retained by a
            # plugin. A separate lock keeps channel cursors independent and
            # avoids taking the manager lock while the stream is locked.
            with self._sequence_lock:
                stream.cursor = self._sequences.get(stream.account_id, 0) + 1
                self._sequences[stream.account_id] = stream.cursor
            stream.buffer.append({"sequence": stream.cursor, "id": message_id, "type": kind,
                                  "broadcaster_id": stream.broadcaster_id,
                                  "timestamp": _text(packet["metadata"].get("message_timestamp"), 64),
                                  "data": data})
            event = dict(stream.buffer[-1], data=dict(data))
        self._deliver(stream, event)
