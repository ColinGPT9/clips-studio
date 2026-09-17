"""Job webhooks: signed, single-attempt, and never able to break a job.

Nothing here touches the network: the transport is replaced, so these run in
CI where the pipeline's own dependencies do not exist.
"""

import hashlib
import hmac
import json

from server import webhooks


class _Response:
    def __init__(self, status_code: int) -> None:
        self.status_code = status_code


def test_only_http_urls_are_deliverable():
    assert webhooks.is_deliverable("http://127.0.0.1:5678/hook")
    assert webhooks.is_deliverable("https://example.com/hook")
    for bad in ("", None, "file:///etc/passwd", "javascript:alert(1)", "not a url",
                "ftp://example.com/x", "https://"):
        assert not webhooks.is_deliverable(bad), bad


def test_the_signature_covers_the_exact_bytes_sent(monkeypatch):
    sent = {}

    def fake_post(url, data=None, headers=None, timeout=None):
        sent["data"], sent["headers"] = data, headers
        return _Response(200)

    monkeypatch.setattr(webhooks.requests, "post", fake_post)
    assert webhooks.deliver("https://example.com/hook", {"event": "job.done"}, "sekrit")

    expected = hmac.new(b"sekrit", sent["data"], hashlib.sha256).hexdigest()
    assert sent["headers"][webhooks.HEADER] == f"sha256={expected}"
    # Verifying against a re-serialised body is what breaks in the field, so
    # the bytes on the wire must be the ones that were signed.
    assert json.loads(sent["data"])["event"] == "job.done"


def test_no_secret_means_no_signature_header(monkeypatch):
    sent = {}
    monkeypatch.setattr(webhooks.requests, "post",
                        lambda url, **kw: (sent.update(kw), _Response(204))[1])
    assert webhooks.deliver("https://example.com/hook", {"event": "job.failed"})
    assert webhooks.HEADER not in sent["headers"]


def test_a_refused_connection_is_reported_not_raised(monkeypatch):
    def refuse(*_a, **_kw):
        raise OSError("connection refused")

    monkeypatch.setattr(webhooks.requests, "post", refuse)
    assert webhooks.deliver("https://example.com/hook", {"event": "job.done"}) is False


def test_an_error_status_is_a_failed_delivery(monkeypatch):
    monkeypatch.setattr(webhooks.requests, "post", lambda *a, **kw: _Response(500))
    assert webhooks.deliver("https://example.com/hook", {"event": "job.done"}) is False


def test_a_bad_url_never_reaches_the_transport(monkeypatch):
    def explode(*_a, **_kw):
        raise AssertionError("must not be called for an undeliverable URL")

    monkeypatch.setattr(webhooks.requests, "post", explode)
    assert webhooks.deliver("file:///tmp/x", {"event": "job.done"}) is False


def test_the_body_names_the_terminal_state():
    body = webhooks.body_for({
        "type": "job", "job_id": 149, "job_type": "process", "status": "done",
        "title": "Friday stream", "video_id": "aB3", "clips": 38, "remaining": 2,
    })
    assert body["event"] == "job.done"
    assert body["job_id"] == 149 and body["video_id"] == "aB3" and body["clips"] == 38


def test_a_failed_job_carries_its_error():
    body = webhooks.body_for({"job_id": 7, "status": "failed", "error": "no video found"})
    assert body["event"] == "job.failed" and body["error"] == "no video found"


def test_every_key_is_present_even_on_a_sparse_event():
    # A listener should never have to guard each field, so a job that ended
    # before its video was identified still delivers the full shape.
    body = webhooks.body_for({"job_id": 9, "status": "cancelled"})
    assert set(body) == {"event", "job_id", "job_type", "status", "video_id",
                         "title", "clips", "error"}
    assert body["video_id"] == "" and body["clips"] == 0


def test_one_attempt_only(monkeypatch):
    calls = []
    monkeypatch.setattr(webhooks.requests, "post",
                        lambda *a, **kw: (calls.append(1), _Response(503))[1])
    webhooks.deliver("https://example.com/hook", {"event": "job.done"})
    assert len(calls) == 1
