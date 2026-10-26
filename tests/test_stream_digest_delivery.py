"""Real saved destinations and digest state, with scripted Discord requests."""

import copy
import json
import sqlite3

import pytest
import requests

from campaigns import service
from campaigns.model import SEND_SPACING_SECONDS
from core.state import StateDB
from server.stream_digest_delivery import DiscordDigestDelivery, content_for
from stream_workflows.digests import DigestService

ACCOUNT = "twitch:123"
TOKEN = "private-webhook-token"


class Response:
    def __init__(self, status=200, body=None):
        self.status_code, self.payload = status, body or {"id": "receipt-private"}
        self.headers = {}

    def json(self):
        return self.payload


class Http:
    def __init__(self, *answers):
        self.answers = list(answers) or [Response()]
        self.calls = []

    def request(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        answer = self.answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer


def resolve(account, candidate):
    return {"kind": "native_clip", "url": f"https://clips.twitch.tv/{candidate['clip_id']}"}


@pytest.fixture
def delivery(db, tmp_path, monkeypatch):
    destination = db.insert_destination(label="Highlights")
    hooks = {destination: {"webhook_id": "12345678901234567", "token": TOKEN}}
    monkeypatch.setattr(service, "load_webhook", lambda directory, key: copy.deepcopy(hooks.get(key)))
    clock = [100.0]
    http = Http()
    adapter = DiscordDigestDelivery(lambda: StateDB(tmp_path / "state.db"), tmp_path, http,
                                   clock=lambda: clock[0])
    digests = DigestService(tmp_path, source_resolver=resolve, transport=adapter, clock=lambda: clock[0])
    yield digests, adapter, http, destination, hooks, clock
    digests.close()


def prepare(digests, destination, request="first", **kwargs):
    return digests.prepare(
        ACCOUNT, request_id=request, destination_id=destination,
        candidates=[{"clip_id": "ActualClip", "title": "A factual moment"}], **kwargs,
    )


def test_approval_posts_to_reviewed_destination_once_without_storing_secrets(delivery):
    digests, _, http, destination, _, _ = delivery
    draft = prepare(digests, destination)
    assert not http.calls
    # Idempotent preparation cannot redirect an existing reviewed digest.
    assert prepare(digests, destination + 1) == draft
    result = digests.approve(ACCOUNT, draft["id"])
    assert result["status"] == "sent"
    assert digests.approve(ACCOUNT, draft["id"]) == result
    assert len(http.calls) == 1
    method, url, kwargs = http.calls[0]
    assert method == "POST" and url.endswith("/12345678901234567/" + TOKEN)
    assert kwargs["params"] == {"wait": "true"}
    assert kwargs["json"]["allowed_mentions"] == {"parse": []}
    assert "https://clips.twitch.tv/ActualClip" in kwargs["json"]["content"]
    exported = json.dumps(digests.export(ACCOUNT, draft["id"]))
    assert TOKEN not in exported and "receipt-private" not in exported


@pytest.mark.parametrize("missing", ["selection", "destination", "token", "revoked"])
def test_missing_delivery_authorization_stays_unsent(delivery, db, missing):
    digests, _, http, destination, hooks, _ = delivery
    if missing == "selection":
        destination = None
    elif missing == "destination":
        db.delete_destination(destination)
    elif missing == "token":
        hooks.clear()
    elif missing == "revoked":
        db.set_destination(destination, state="denied")
    result = prepare(digests, destination, auto_publish=True)
    assert result["status"] == "unsent" and not http.calls


def test_removed_secret_is_rechecked_at_approval(delivery):
    digests, _, http, destination, hooks, _ = delivery
    draft = prepare(digests, destination)
    hooks.clear()
    assert digests.approve(ACCOUNT, draft["id"])["status"] == "unsent"
    assert not http.calls


@pytest.mark.parametrize("thread_id", ["", "98765432109876543"])
def test_forum_uses_new_or_existing_thread(delivery, db, thread_id):
    digests, _, http, destination, _, _ = delivery
    db.set_destination(destination, is_forum=1, thread_id=thread_id)
    assert prepare(digests, destination, auto_publish=True)["status"] == "sent"
    kwargs = http.calls[0][2]
    if thread_id:
        assert kwargs["params"]["thread_id"] == thread_id
        assert "thread_name" not in kwargs["json"]
    else:
        assert kwargs["json"]["thread_name"] == "Stream catch-up"


def test_rate_limit_survives_adapter_restart_and_blocks_other_destinations(delivery, db, tmp_path):
    digests, _, http, destination, hooks, clock = delivery
    http.answers = [Response(429, {"retry_after": 30, "global": True}), Response()]
    draft = prepare(digests, destination, auto_publish=True)
    assert draft["status"] == "unsent"
    assert len(http.calls) == 1
    other = db.insert_destination(label="Other")
    hooks[other] = hooks[destination]
    # A fresh adapter reads the persisted global delay, rather than resetting it.
    digests._transport = DiscordDigestDelivery(lambda: StateDB(tmp_path / "state.db"), tmp_path, http,
                                             clock=lambda: clock[0])
    second = prepare(digests, other, "second", auto_publish=True)
    assert second["status"] == "unsent" and len(http.calls) == 1
    clock[0] += 30 + SEND_SPACING_SECONDS
    assert digests.approve(ACCOUNT, draft["id"])["status"] == "sent"
    assert len(http.calls) == 2
    third = prepare(digests, destination, "third", auto_publish=True)
    assert third["status"] == "unsent" and len(http.calls) == 2


@pytest.mark.parametrize("answer", [requests.ReadTimeout(TOKEN), Response(500)])
def test_ambiguous_delivery_is_never_retried(delivery, answer, capsys):
    digests, _, http, destination, _, _ = delivery
    http.answers = [answer]
    result = prepare(digests, destination, auto_publish=True)
    assert result["status"] == "delivery_unknown"
    assert digests.approve(ACCOUNT, result["id"])["status"] == "delivery_unknown"
    assert len(http.calls) == 1
    assert TOKEN not in json.dumps(digests.export(ACCOUNT, result["id"]))
    assert TOKEN not in capsys.readouterr().out


@pytest.mark.parametrize("answer", [
    requests.ConnectTimeout(TOKEN), Response(400), Response(403), Response(404),
])
def test_confirmed_non_delivery_stays_unsent_for_explicit_retry(delivery, answer):
    digests, _, http, destination, _, clock = delivery
    http.answers = [answer, Response()]
    result = prepare(digests, destination, auto_publish=True)
    assert result["status"] == "unsent" and len(http.calls) == 1
    # No background retry; another explicit approval respects spacing first.
    assert digests.approve(ACCOUNT, result["id"])["status"] == "unsent"
    assert len(http.calls) == 1
    clock[0] += SEND_SPACING_SECONDS
    assert digests.approve(ACCOUNT, result["id"])["status"] == "sent"
    assert len(http.calls) == 2


@pytest.mark.parametrize("answer", [Response(), Response(429), requests.ReadTimeout(TOKEN)])
def test_each_send_closes_its_own_database_connection(delivery, tmp_path, answer):
    digests, adapter, http, destination, _, _ = delivery
    opened = []

    def database():
        value = StateDB(tmp_path / "state.db")
        opened.append(value)
        return value

    adapter.db = database
    http.answers = [answer]
    prepare(digests, destination, auto_publish=True)
    assert len(opened) == 1
    with pytest.raises(sqlite3.ProgrammingError, match="closed"):
        opened[0].conn.execute("SELECT 1")


def test_content_keeps_all_ten_long_links_inside_discord_limit():
    items = [{"title": "😀" * 80, "url": "https://clips.twitch.tv/" + "x" * 160} for _ in range(10)]
    content = content_for({"items": items})
    assert len(content.encode("utf-16-le")) // 2 <= 2000
    assert content.count("https://clips.twitch.tv/") == 10
