"""Offline checks of digest ranking, trust boundaries and durable delivery."""

import json
import sqlite3

import pytest

from stream_workflows.digests import DigestService
from stream_workflows.models import WorkflowError

ACCOUNT = "twitch:123"


def resolver(account_id, candidate):
    assert account_id == ACCOUNT
    if "vod_id" in candidate:
        seconds = int(candidate["offset_seconds"])
        return {
            "kind": "vod_timestamp",
            "video_id": candidate["vod_id"],
            "seconds": seconds,
            "url": f"https://www.twitch.tv/videos/{candidate['vod_id']}?t={seconds}s",
            "access_token": "resolver-private-token",
        }
    return {
        "kind": "native_clip",
        "clip_id": candidate["clip_id"],
        "title": "Verified clip",
        "url": f"https://clips.twitch.tv/{candidate['clip_id']}",
    }


def candidates(count=12):
    return [{"vod_id": "456", "offset_seconds": i * 20, "score": i} for i in range(count)]


class Transport:
    def __init__(self, *, fails=False):
        self.calls = []
        self.fails = fails

    def send(self, digest, *, idempotency_key):
        self.calls.append((digest, idempotency_key))
        if self.fails:
            raise RuntimeError("secret-provider-url?access_token=private")
        return {"receipt": "private-transport-receipt"}


@pytest.mark.parametrize("top_k", [3, 5, 10])
def test_top_k_stable_rank_and_bounded_resolution(tmp_path, top_k):
    resolved = []

    def resolve(account_id, candidate):
        resolved.append(candidate)
        return resolver(account_id, candidate)

    service = DigestService(tmp_path, source_resolver=resolve, clock=lambda: 100)
    values = candidates()
    values[10]["score"] = values[11]["score"]
    digest = service.prepare(ACCOUNT, request_id="top", candidates=values, top_k=top_k)
    assert len(digest["items"]) == len(resolved) == top_k
    assert [i["seconds"] for i in digest["items"][:2]] == [200, 220]
    assert digest["items"][0]["title"] == "Highlight at 00:03:20"
    assert digest["items"][0]["url"] == "https://www.twitch.tv/videos/456?t=0h3m20s"
    assert digest["status"] == "pending_approval"
    assert "resolver-private-token" not in json.dumps(service.export(ACCOUNT, digest["id"]))
    service.close()


def test_default_requires_approval_and_write_is_idempotent(tmp_path):
    transport = Transport()
    service = DigestService(tmp_path, source_resolver=resolver, transport=transport)
    first = service.prepare(ACCOUNT, request_id="same", candidates=candidates(3), top_k=3)
    assert first["status"] == "pending_approval" and not transport.calls
    # Retrying a preparation cannot replace an already reviewed digest.
    assert service.prepare(ACCOUNT, request_id="same", candidates=candidates(1), top_k=10) == first
    sent = service.approve(ACCOUNT, first["id"])
    assert sent["status"] == "sent"
    assert service.approve(ACCOUNT, first["id"]) == sent
    assert len(transport.calls) == 1
    assert transport.calls[0][1] == f"stream-digest:{first['id']}"
    assert "private-transport-receipt" not in json.dumps(service.get(ACCOUNT, first["id"]))
    service.close()


def test_no_adapter_is_unsent_and_export_is_portable(tmp_path):
    service = DigestService(tmp_path, source_resolver=resolver, clock=lambda: 100)
    digest = service.prepare(ACCOUNT, request_id="manual", candidates=candidates(1))
    digest = service.approve(ACCOUNT, digest["id"])
    assert digest["status"] == "unsent"
    assert service.approve(ACCOUNT, digest["id"]) == digest
    assert service.list(ACCOUNT) == [digest]
    assert service.export(ACCOUNT, digest["id"]) == {"schema_version": 1, **digest}
    saved = json.loads((tmp_path / "stream-digests" / f"{digest['id']}.json").read_text())
    assert saved == digest
    service.close()
    restored = DigestService(tmp_path, source_resolver=resolver)
    assert restored.get(ACCOUNT, digest["id"]) == digest
    with pytest.raises(WorkflowError) as error:
        restored.get("twitch:other", digest["id"])
    assert error.value.status == 404
    restored.close()


def test_automatic_delivery_requires_explicit_boolean(tmp_path):
    transport = Transport()
    service = DigestService(tmp_path, source_resolver=resolver, transport=transport)
    with pytest.raises(WorkflowError):
        service.prepare(ACCOUNT, request_id="bad", candidates=candidates(1), auto_publish="true")
    digest = service.prepare(ACCOUNT, request_id="auto", candidates=candidates(1), auto_publish=True)
    assert digest["auto_publish"] is True and digest["status"] == "sent"
    assert len(transport.calls) == 1
    service.close()


def test_ambiguous_transport_is_never_retried(tmp_path):
    transport = Transport(fails=True)
    service = DigestService(tmp_path, source_resolver=resolver, transport=transport)
    digest = service.prepare(ACCOUNT, request_id="unknown", candidates=candidates(1), auto_publish=True)
    assert digest["status"] == "delivery_unknown"
    assert len(transport.calls) == 1
    assert "secret-provider" not in json.dumps(service.export(ACCOUNT, digest["id"]))
    service.close()
    restored = DigestService(tmp_path, source_resolver=resolver, transport=transport)
    assert restored.approve(ACCOUNT, digest["id"])["status"] == "delivery_unknown"
    assert len(transport.calls) == 1
    restored.close()


def test_restart_marks_interrupted_send_unknown_without_retry(tmp_path):
    service = DigestService(tmp_path, source_resolver=resolver)
    digest = service.prepare(ACCOUNT, request_id="interrupted", candidates=candidates(1))
    service.close()
    # Simulate a crash after the durable sending transition and before receipt.
    digest["status"] = "sending"
    with sqlite3.connect(tmp_path / "stream-digests.sqlite3") as connection:
        connection.execute("UPDATE digests SET payload=? WHERE id=?", (json.dumps(digest), digest["id"]))
    transport = Transport()
    restored = DigestService(tmp_path, source_resolver=resolver, transport=transport)
    assert restored.approve(ACCOUNT, digest["id"])["status"] == "delivery_unknown"
    assert not transport.calls
    snapshot = json.loads((tmp_path / "stream-digests" / f"{digest['id']}.json").read_text())
    assert snapshot["status"] == "delivery_unknown"
    restored.close()


@pytest.mark.parametrize(
    "candidate",
    [
        {"vod_id": "456", "offset_seconds": 2, "url": "https://evil.invalid/"},
        {"vod_id": "456", "offset_seconds": 2, "access_token": "private"},
        {"vod_id": "456", "offset_seconds": 2, "clip_id": "clip"},
        {"vod_id": "456", "offset_seconds": -1},
        {"vod_id": "456", "offset_seconds": float("inf")},
        {"clip_id": "clip", "score": float("nan")},
        {"clip_id": "clip", "title": "x" * 161},
    ],
)
def test_candidates_cannot_supply_urls_secrets_or_invalid_values(tmp_path, candidate):
    service = DigestService(tmp_path, source_resolver=resolver)
    with pytest.raises(WorkflowError):
        service.prepare(ACCOUNT, request_id="bad", candidates=[candidate])
    assert service.list(ACCOUNT) == []
    service.close()


@pytest.mark.parametrize(
    "url",
    [
        "https://evil.invalid/videos/456?t=2s",
        "https://www.twitch.tv/videos/other?t=2s",
        "https://www.twitch.tv/videos/456?t=2s&token=private",
        "https://www.twitch.tv/videos/456?t=3s",
        "https://www.twitch.tv/videos/456?t=2s#private",
        "https://www.twitch.tv:443/videos/456?t=2s",
    ],
)
def test_resolved_vod_must_match_canonical_public_source(tmp_path, url):
    def untrusted(account_id, candidate):
        return {"url": url, "kind": "vod_timestamp", "video_id": "456"}

    service = DigestService(tmp_path, source_resolver=untrusted)
    with pytest.raises(WorkflowError):
        service.prepare(ACCOUNT, request_id="bad", candidates=[{"vod_id": "456", "offset_seconds": 2}])
    service.close()


def test_clip_resolver_keeps_only_public_record(tmp_path):
    service = DigestService(tmp_path, source_resolver=resolver)
    digest = service.prepare(
        ACCOUNT, request_id="clip", candidates=[{"clip_id": "NiceClip", "title": "@everyone `Moment`"}]
    )
    assert digest["items"] == [
        {
            "kind": "native_clip",
            "clip_id": "NiceClip",
            "score": 0.0,
            "title": "＠everyone Moment",
            "url": "https://clips.twitch.tv/NiceClip",
        }
    ]
    service.close()


@pytest.mark.parametrize(
    "params", [{"top_k": 4}, {"top_k": True}, {"candidates": []}, {"candidates": candidates(101)}]
)
def test_preparation_bounds(tmp_path, params):
    service = DigestService(tmp_path, source_resolver=resolver)
    arguments = {"request_id": "bad", "candidates": candidates(1), **params}
    with pytest.raises(WorkflowError):
        service.prepare(ACCOUNT, **arguments)
    service.close()
