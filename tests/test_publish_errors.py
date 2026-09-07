"""Telling API failures apart.

The distinction that matters most: quotaExceeded means stop until tomorrow,
rateLimitExceeded means wait ten seconds, and uploadLimitExceeded means the
CHANNEL is limited and no amount of waiting on the API key will help. Treating
them alike produces either a pointless retry loop or a dead end that reads as
a crash.
"""

import json

from publish.errors import (
    AuthRequired,
    PublishError,
    QuotaExceeded,
    RateLimited,
    UploadLimitExceeded,
    classify,
    from_http_error,
)


def test_daily_quota_is_not_retryable():
    err = classify(403, "quotaExceeded", "The request cannot be completed.")
    assert isinstance(err, QuotaExceeded)
    assert not err.retryable
    assert "midnight Pacific" in err.message


def test_rate_limiting_is_retryable():
    err = classify(403, "rateLimitExceeded")
    assert isinstance(err, RateLimited)
    assert err.retryable


def test_a_429_is_rate_limiting_even_with_no_reason():
    assert isinstance(classify(429), RateLimited)


def test_channel_upload_limit_is_its_own_thing():
    err = classify(400, "uploadLimitExceeded")
    assert isinstance(err, UploadLimitExceeded)
    assert not err.retryable
    assert "channel" in err.message
    assert "different key will not help" in err.message


def test_expired_credentials_ask_for_a_reconnect():
    err = classify(401, "authError")
    assert isinstance(err, AuthRequired)
    assert "Reconnect" in err.message or "reconnect" in err.message


def test_a_bare_403_is_treated_as_permission_not_quota():
    """A 403 with no recognised reason is far more often a scope problem."""
    err = classify(403, "", "Forbidden")
    assert isinstance(err, AuthRequired)
    assert not isinstance(err, QuotaExceeded)


def test_an_account_with_no_channel_is_explained():
    err = classify(401, "youtubeSignupRequired")
    assert "no YouTube channel" in err.message


def test_an_expired_upload_session_says_nothing_was_published():
    err = classify(404)
    assert "Nothing was published" in err.message
    assert not err.retryable, "retrying a dead session can create a second video"


def test_server_errors_are_retryable():
    for status in (500, 502, 503, 504):
        assert classify(status).retryable, f"{status} should be retryable"


def test_an_unknown_failure_still_produces_something_showable():
    err = classify(400, "somethingNew", "surprise")
    assert isinstance(err, PublishError)
    assert err.message
    assert err.detail == "surprise"


class _FakeResponse:
    def __init__(self, status):
        self.status = status


class _FakeHttpError(Exception):
    def __init__(self, status, payload):
        self.resp = _FakeResponse(status)
        self.content = json.dumps(payload).encode()


def test_a_google_http_error_is_unwrapped_without_importing_google():
    exc = _FakeHttpError(403, {
        "error": {
            "message": "The user has exceeded the number of videos they may upload.",
            "errors": [{"reason": "uploadLimitExceeded"}],
        }
    })
    assert isinstance(from_http_error(exc), UploadLimitExceeded)


def test_an_unparseable_body_falls_back_to_the_status_code():
    exc = _FakeHttpError(503, {})
    exc.content = b"<html>gateway</html>"
    assert from_http_error(exc).retryable


def test_an_error_with_no_response_object_does_not_explode():
    assert isinstance(from_http_error(Exception("boom")), PublishError)
