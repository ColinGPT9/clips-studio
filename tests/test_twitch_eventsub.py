"""Twitch EventSub transport and trust boundaries, without Twitch or tokens."""

import json
import queue
import threading
import time

import pytest

from connected_accounts.eventsub import EVENT_TYPES, EventSubManager, _reconnect_url
from connected_accounts.models import AccountError


def packet(kind, payload=None, message_id="message-1"):
    return json.dumps({"metadata": {"message_type": kind, "message_id": message_id,
                                    "message_timestamp": "2026-01-01T00:00:00Z"},
                       "payload": payload or {}})


def welcome(session_id="host-session"):
    return packet("session_welcome", {"session": {"id": session_id, "keepalive_timeout_seconds": 10}})


def notification(kind="stream.online", uid="12", message_id="message-1", **extra):
    event = {"broadcaster_user_id": uid, "broadcaster_user_login": "streamer",
             "broadcaster_user_name": "Streamer", "id": "stream-1", "type": "live", **extra}
    return packet("notification", {"subscription": {"type": kind,
                                                    "condition": {"broadcaster_user_id": uid}},
                                    "event": event}, message_id)


class Socket:
    def __init__(self, *messages):
        self.messages = queue.Queue()
        self.closed = False
        self.received_timeouts = []
        for message in messages:
            self.messages.put(message)

    def recv(self, timeout):
        self.received_timeouts.append(timeout)
        try:
            value = self.messages.get(timeout=timeout)
        except queue.Empty:
            raise TimeoutError("quiet") from None
        if isinstance(value, BaseException):
            raise value
        return value

    def close(self):
        self.closed = True
        self.messages.put(ConnectionError("closed"))


class Service:
    def __init__(self):
        self.calls = []
        self.roster = [{"id": "twitch:12", "provider": "twitch", "user_id": "12",
                        "status": "connected", "scopes": ["user:read:chat"]}]

    def accounts(self):
        return self.roster

    def perform(self, account_id, operation, **params):
        self.calls.append((account_id, operation, params))
        return {"subscriptions": [{"id": "subscription", "type": t, "status": "enabled"}
                                  for t in params["event_types"]]}


def eventually(fn, timeout=3):
    until = time.monotonic() + timeout
    while time.monotonic() < until:
        if fn():
            return
        threading.Event().wait(0.005)
    assert fn()


@pytest.fixture
def manager():
    sockets = []
    service = Service()

    def connect(url):
        assert url == "wss://eventsub.wss.twitch.tv/ws"
        socket = Socket(welcome())
        sockets.append(socket)
        return socket

    manager = EventSubManager(service, connector=connect)
    yield manager, service, sockets
    manager.close()


def test_session_id_is_host_owned_and_notifications_are_allowlisted(manager):
    manager, service, sockets = manager
    manager.start("twitch:12", "12", ["stream.online", "channel.chat.message"])
    eventually(lambda: manager.events("twitch:12")["status"] == "connected")
    assert service.calls == [("twitch:12", "subscribe_events", {
        "broadcaster_id": "12", "event_types": ["channel.chat.message", "stream.online"],
        "session_id": "host-session"})]
    socket = sockets[0]
    socket.messages.put(notification(access_token="should-not-leave-host"))
    socket.messages.put(notification())  # Twitch can redeliver the same notification.
    socket.messages.put(notification(uid="999", message_id="wrong-channel"))
    socket.messages.put(notification(kind="channel.update", message_id="unsubscribed"))
    socket.messages.put(notification(kind="channel.chat.message", message_id="chat-1",
                                     chatter_user_id="9", message={"text": "Hello", "raw": "secret"},
                                     refresh_token="should-not-leave-host"))
    eventually(lambda: len(manager.events("twitch:12")["events"]) == 2)
    body = manager.events("twitch:12")
    assert [e["type"] for e in body["events"]] == ["stream.online", "channel.chat.message"]
    assert body["events"][1]["data"]["text"] == "Hello"
    assert "should-not-leave-host" not in json.dumps(body)
    assert "secret" not in json.dumps(body)
    assert manager.events("twitch:12", broadcaster_id="999")["events"] == []
    assert manager.events("twitch:12", after=body["cursor"])["events"] == []


def test_subscription_authority_and_scopes_are_checked_before_opening(manager):
    manager, service, sockets = manager
    for account, broadcaster, event_types, code in [
        ("twitch:unknown", "12", ["stream.online"], "account_not_found"),
        ("twitch:12", "99", ["stream.online"], "permission_denied"),
        ("twitch:12", "12", ["arbitrary.permission"], "invalid_request"),
        ("twitch:12", "12", [], "invalid_request"),
        ("twitch:12", "bad", ["stream.online"], "invalid_request"),
    ]:
        with pytest.raises(AccountError) as error:
            manager.start(account, broadcaster, event_types)
        assert error.value.code == code
    service.roster[0]["scopes"] = []
    with pytest.raises(AccountError, match="read chat"):
        manager.start("twitch:12", "12", ["channel.chat.message"])
    assert not sockets and not service.calls


@pytest.mark.parametrize("url", [
    "ws://eventsub.wss.twitch.tv/ws", "wss://evil.example/ws",
    "wss://eventsub.wss.twitch.tv.evil.example/ws", "wss://user@eventsub.wss.twitch.tv/ws",
    "wss://eventsub.wss.twitch.tv:443/ws", "wss://eventsub.wss.twitch.tv/ws#fragment",
    "wss://eventsub.wss.twitch.tv/elsewhere", "wss://eventsub.wss.twitch.tv/ws\n",
    "wss://eventsub.wss.twitch.tv:bad/ws", None,
])
def test_reconnect_destination_cannot_escape_twitch(url):
    with pytest.raises((ValueError, TypeError)):
        _reconnect_url(url)


def test_directed_reconnect_keeps_old_socket_until_welcome_and_transfers_subscriptions():
    reconnect_url = "wss://eventsub.wss.twitch.tv/ws?session=transferred"
    first = Socket(welcome("first"), packet("session_reconnect", {
        "session": {"reconnect_url": reconnect_url}}))
    second = Socket(welcome("second"), notification(message_id="after-reconnect"))
    calls = []

    def connect(url):
        calls.append(url)
        if len(calls) == 1:
            return first
        assert url == reconnect_url
        assert not first.closed  # No event loss between old and replacement socket.
        return second

    service = Service()
    manager = EventSubManager(service, connector=connect)
    try:
        manager.start("twitch:12", "12", ["stream.online"])
        eventually(lambda: len(manager.events("twitch:12")["events"]) == 1)
        assert len(service.calls) == 1  # Twitch transferred the existing subscription.
        assert first.closed
        assert calls == ["wss://eventsub.wss.twitch.tv/ws", reconnect_url]
    finally:
        manager.close()
    assert second.closed


def test_broken_socket_creates_fresh_subscriptions_and_never_exposes_exception_text():
    first = Socket(welcome("first"), OSError("access-token-sensitive-upstream-detail"))
    second = Socket(welcome("second"), notification(message_id="new-session"))
    sockets = iter([first, second])
    service = Service()
    manager = EventSubManager(service, connector=lambda url: next(sockets))
    try:
        manager.start("twitch:12", "12", ["stream.online"])
        eventually(lambda: len(service.calls) == 2)
        eventually(lambda: bool(manager.events("twitch:12")["events"]))
        assert [call[2]["session_id"] for call in service.calls] == ["first", "second"]
        assert "access-token" not in json.dumps(manager.events("twitch:12"))
    finally:
        manager.close()


def test_revocation_stops_listener_and_reports_reconnect_without_raw_payload(manager):
    manager, _, sockets = manager
    manager.start("twitch:12", "12", ["stream.online"])
    eventually(lambda: bool(sockets))
    sockets[0].messages.put(packet("revocation", {"subscription": {
        "type": "stream.online", "status": "authorization_revoked", "access_token": "private"}}))
    eventually(lambda: manager.events("twitch:12")["status"] == "reconnect_required")
    assert "private" not in json.dumps(manager.events("twitch:12"))
    assert sockets[0].closed


def test_event_buffer_is_bounded_and_cursor_survives_listener_restart(manager):
    manager, _, sockets = manager
    manager.start("twitch:12", "12", ["stream.online"])
    eventually(lambda: bool(sockets))
    for index in range(510):
        sockets[0].messages.put(notification(message_id=f"event-{index}"))
    eventually(lambda: manager.events("twitch:12", after=0)["cursor"] >= 110)
    stream = manager._streams["twitch:12"]
    eventually(lambda: stream.cursor >= 510)
    assert len(stream.buffer) == 500
    assert manager.events("twitch:12")["truncated"]
    last = stream.cursor
    manager.stop("twitch:12")
    assert manager.events("twitch:12")["events"] == []
    assert sockets[0].closed
    manager.start("twitch:12", "12", ["stream.online"])
    eventually(lambda: len(sockets) == 2)
    sockets[1].messages.put(notification(message_id="fresh"))
    eventually(lambda: bool(manager.events("twitch:12", after=last)["events"]))


def test_poll_limits_and_closed_lifecycle(manager):
    manager, _, _ = manager
    for kwargs in [{"limit": 0}, {"limit": 101}, {"after": -1}, {"after": True}]:
        with pytest.raises(AccountError):
            manager.events("twitch:12", **kwargs)
    manager.close()
    with pytest.raises(AccountError, match="stopping"):
        manager.start("twitch:12", "12", list(EVENT_TYPES))


def test_account_revocation_and_scope_downgrade_block_cached_chat(manager):
    manager, service, sockets = manager
    manager.start("twitch:12", "12", ["channel.chat.message", "stream.online"])
    eventually(lambda: bool(sockets))
    sockets[0].messages.put(notification(kind="channel.chat.message", message_id="chat",
                                         message={"text": "cached chat"}))
    sockets[0].messages.put(notification(message_id="stream"))
    eventually(lambda: len(manager.events("twitch:12")["events"]) == 2)
    service.roster[0]["scopes"] = []
    assert [e["type"] for e in manager.events("twitch:12")["events"]] == ["stream.online"]
    service.roster[0]["status"] = "reconnect_required"
    assert manager.events("twitch:12")["events"] == []
    assert sockets[0].closed


def test_multiple_clients_extend_shared_event_types_instead_of_removing_them(manager):
    manager, service, sockets = manager
    manager.start("twitch:12", "12", ["channel.chat.message"])
    eventually(lambda: manager.events("twitch:12")["status"] == "connected")
    manager.start("twitch:12", "12", ["stream.online"])
    eventually(lambda: len(service.calls) == 2)
    assert service.calls[-1][2]["event_types"] == ["channel.chat.message", "stream.online"]
    assert sockets[0].closed
    assert len(sockets) == 2


def test_the_last_consumer_out_stops_the_listener_and_chat_ends_with_its_reader(manager):
    manager, service, sockets = manager
    manager.start("twitch:12", "12", ["stream.online", "stream.offline", "channel.chat.message"],
                  consumer="stream-workflow")
    manager.start("twitch:12", "12", ["stream.online"], consumer="plugin-run:1")
    eventually(lambda: manager.events("twitch:12")["status"] == "connected")
    assert len(sockets) == 1  # one shared listener
    sockets[0].messages.put(notification(kind="channel.chat.message", message_id="chat",
                                         message={"text": "viewer chat"}))
    eventually(lambda: manager.events("twitch:12")["events"])
    # Collection stops: only it wanted chat, so chat is no longer read and
    # the buffered chat goes; the run keeps its stream events.
    manager.release("twitch:12", "stream-workflow")
    assert sockets[0].closed
    eventually(lambda: manager.events("twitch:12")["status"] == "connected")
    body = manager.events("twitch:12")
    assert body["event_types"] == ["stream.online"] and body["events"] == []
    assert service.calls[-1][2]["event_types"] == ["stream.online"]
    manager.release("twitch:12", "plugin-run:1")
    assert manager.events("twitch:12")["status"] == "stopped"
    assert sockets[1].closed
    manager.release("twitch:12", "plugin-run:1")  # a second release changes nothing


def test_sinks_hear_of_every_fresh_subscription(manager):
    manager, service, sockets = manager
    heard = []
    manager.add_sink(lambda account_id, event: heard.append((account_id, event["type"])))
    manager.start("twitch:12", "12", ["stream.online"])
    eventually(lambda: heard)
    assert heard == [("twitch:12", "session.subscribed")]
    assert manager.events("twitch:12")["events"] == []  # never buffered for plugins


def test_expired_keepalive_is_a_transport_failure_not_a_connected_listener(manager):
    manager, _, _ = manager
    times = iter([10, 21])
    manager._clock = lambda: next(times)
    from connected_accounts.eventsub import _Stream

    stream = _Stream("twitch:12", "12", ("stream.online",))
    with pytest.raises(TimeoutError, match="keepalive"):
        manager._listen(stream, Socket(), timeout=10)
