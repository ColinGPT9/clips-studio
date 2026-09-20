"""Upload-Post client and fan-out parsing.

Offline, like the rest of the suite. The HTTP layer is exercised by replacing
the client's own _request, which is the convention tests/test_mcp.py uses.
"""

from pathlib import Path

import pytest

from publish.errors import AuthRequired, NotConnected, QuotaExceeded, RateLimited
from publish.uploadpost import (
    UploadPostClient,
    UploadPostError,
    UploadPostPublisher,
    _classify,
    _encode_multipart,
    build_fields,
    missing_required,
    parse_results,
    platform_overrides,
    schedule_fields,
    validate_schedule,
)

# ---- result parsing --------------------------------------------------------


def test_upload_response_keyed_by_platform_is_parsed():
    got = parse_results(
        {
            "success": True,
            "results": {
                "instagram": {"success": True, "url": "https://instagram.com/p/abc"},
                "youtube": {"success": True, "video_id": "xyz"},
            },
        }
    )
    assert [o.platform for o in got.outcomes] == ["instagram", "youtube"]
    assert got.outcomes[0].post_url == "https://instagram.com/p/abc"
    assert got.outcomes[1].post_id == "xyz"
    assert got.done


def test_status_response_as_a_list_is_parsed():
    """The status endpoint answers with a list, the upload call with a dict."""
    got = parse_results(
        {
            "request_id": "req-1",
            "status": "in_progress",
            "results": [
                {"platform": "x", "success": True, "url": "https://x.com/i/1"},
                {"platform": "tiktok", "status": "processing"},
            ],
        }
    )
    assert got.request_id == "req-1"
    assert {o.platform: o.state for o in got.outcomes} == {
        "tiktok": "processing",
        "x": "published",
    }
    assert not got.done


def test_partial_failure_is_not_reported_as_success():
    """The top-level success stays True when a platform failed.

    Trusting it would tell a user everything published when it did not, which
    is the single easiest way to get this integration wrong.
    """
    got = parse_results(
        {
            "success": True,
            "results": {
                "instagram": {"success": True, "url": "https://instagram.com/p/a"},
                "linkedin": {"success": False, "error": "Expired access token"},
            },
        }
    )
    assert [o.platform for o in got.published] == ["instagram"]
    assert [o.platform for o in got.failed] == ["linkedin"]
    assert got.failed[0].error == "Expired access token"


def test_unconnected_platform_is_skipped_not_failed():
    got = parse_results(
        {
            "results": {
                "tiktok": {"skipped": True, "reason": "profile_platform_not_configured"}
            }
        }
    )
    outcome = got.outcomes[0]
    assert outcome.state == "skipped"
    assert outcome.skipped
    assert outcome not in got.failed
    assert "Not connected" in outcome.error


def test_platform_id_fields_vary_by_platform():
    got = parse_results(
        {
            "results": {
                "facebook": {"success": True, "container_id": "111"},
                "linkedin": {"success": True, "video_urn": "urn:li:222"},
            }
        }
    )
    assert {o.platform: o.post_id for o in got.outcomes} == {
        "facebook": "111",
        "linkedin": "urn:li:222",
    }


def test_missing_results_gives_no_outcomes_rather_than_crashing():
    assert parse_results({}).outcomes == []
    assert parse_results({"results": None}).outcomes == []


# ---- multipart encoding ----------------------------------------------------


def test_repeated_fields_survive_encoding():
    """platform[] must appear once per destination — that is the fan-out."""
    body, content_type = _encode_multipart(
        [("platform[]", "youtube"), ("platform[]", "tiktok"), ("title", "hi")], []
    )
    text = body.decode("utf-8")
    assert content_type.startswith("multipart/form-data; boundary=")
    assert text.count('name="platform[]"') == 2
    assert "youtube" in text and "tiktok" in text


def test_file_part_carries_a_filename_and_type(tmp_path: Path):
    clip = tmp_path / "clip.mp4"
    clip.write_bytes(b"\x00\x01\x02")
    body, _ = _encode_multipart([], [("video", clip)])
    text = body.decode("utf-8", "replace")
    assert 'name="video"; filename="clip.mp4"' in text
    assert "Content-Type: video/mp4" in text
    assert b"\x00\x01\x02" in body


def test_multipart_uses_crlf_line_endings():
    """Bare newlines make a body some servers reject outright."""
    body, _ = _encode_multipart([("a", "b")], [])
    assert b"\r\n" in body
    assert body.count(b"\n") == body.count(b"\r\n")


# ---- error classification --------------------------------------------------


def test_bad_key_asks_the_user_to_fix_the_key():
    err = _classify(401, {"error": "Invalid or expired token"}, "")
    assert isinstance(err, AuthRequired)
    assert "API key" in err.message


def test_monthly_allowance_and_rate_limit_are_different_429s():
    """One is worth retrying in seconds, the other not until next month."""
    monthly = _classify(429, {"usage": {"count": 10, "limit": 10}}, "")
    assert isinstance(monthly, QuotaExceeded)
    assert "10 of 10" in monthly.message
    assert not monthly.retryable

    slow_down = _classify(429, {"error": "Too many requests"}, "")
    assert isinstance(slow_down, RateLimited)
    assert slow_down.retryable


def test_plan_restriction_keeps_the_platform_name():
    err = _classify(
        403, {"error": "TikTok uploads are not available on the Free plan."}, ""
    )
    assert isinstance(err, UploadPostError)
    assert "TikTok" in err.message


def test_no_connected_platforms_names_them():
    err = _classify(400, {"invalid_platforms": {"tiktok": "x", "threads": "y"}}, "")
    assert isinstance(err, NotConnected)
    assert "threads, tiktok" in err.message


def test_server_error_is_retryable():
    assert _classify(500, {}, "boom").retryable


# ---- the client ------------------------------------------------------------


def test_no_key_refuses_before_making_a_request():
    with pytest.raises(NotConnected):
        UploadPostClient("").validate_key()


def test_key_is_sent_as_an_apikey_header(monkeypatch):
    seen = {}

    class FakeResponse:
        def read(self):
            return b'{"success": true, "plan": "Free"}'

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def fake_urlopen(req, timeout=0):
        seen["url"] = req.full_url
        seen["auth"] = req.get_header("Authorization")
        return FakeResponse()

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    got = UploadPostClient("secret-key").validate_key()

    assert got["plan"] == "Free"
    assert seen["auth"] == "Apikey secret-key"
    # The host carries no /api suffix of its own; that URL is a 404.
    assert seen["url"] == "https://api.upload-post.com/api/uploadposts/me"


def test_unreadable_reply_is_an_error_not_a_crash(monkeypatch):
    class FakeResponse:
        def read(self):
            return b"<html>gateway</html>"

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    monkeypatch.setattr("urllib.request.urlopen", lambda req, timeout=0: FakeResponse())
    with pytest.raises(UploadPostError):
        UploadPostClient("k").validate_key()


# ---- the publisher ---------------------------------------------------------


class _FakeClient:
    def __init__(self, payload=None):
        self.payload = payload or {"request_id": "req-9"}
        self.calls = []

    def upload_video(self, video, *, profile, platforms, fields, thumbnail=None,
                     idempotency_key=""):
        self.calls.append(
            {"platforms": platforms, "fields": fields, "profile": profile,
             "idempotency_key": idempotency_key}
        )
        return self.payload

    def get_status(self, request_id):
        return {"request_id": request_id, "results": []}


def test_start_seeds_a_queued_row_for_every_platform(tmp_path: Path):
    """An async start answers with only a request_id, so the UI needs rows."""
    clip = tmp_path / "c.mp4"
    clip.write_bytes(b"x")
    pub = UploadPostPublisher(_FakeClient(), profile="me")

    got = pub.start(clip, platforms=["youtube", "tiktok"], fields=[("title", "t")])

    assert got.request_id == "req-9"
    assert {o.platform for o in got.outcomes} == {"tiktok", "youtube"}
    assert all(o.state == "queued" for o in got.outcomes)


def test_platforms_that_cannot_take_video_are_dropped(tmp_path: Path):
    clip = tmp_path / "c.mp4"
    clip.write_bytes(b"x")
    client = _FakeClient()
    pub = UploadPostPublisher(client, profile="me")

    pub.start(clip, platforms=["youtube", "hashnode"], fields=[])

    assert client.calls[0]["platforms"] == ["youtube"]


def test_no_usable_platform_is_refused(tmp_path: Path):
    clip = tmp_path / "c.mp4"
    clip.write_bytes(b"x")
    pub = UploadPostPublisher(_FakeClient(), profile="me")
    with pytest.raises(UploadPostError):
        pub.start(clip, platforms=["hashnode"], fields=[])


def test_no_profile_is_refused_before_uploading(tmp_path: Path):
    clip = tmp_path / "c.mp4"
    clip.write_bytes(b"x")
    pub = UploadPostPublisher(_FakeClient(), profile="")
    with pytest.raises(NotConnected):
        pub.start(clip, platforms=["youtube"], fields=[])


# ---- metadata mapping ------------------------------------------------------


def test_tags_are_repeated_fields_not_a_joined_string():
    fields = build_fields(title="T", tags=["a", "b"])
    assert fields.count(("tags[]", "a")) == 1
    assert fields.count(("tags[]", "b")) == 1
    assert not any(name == "tags" for name, _ in fields)


def test_empty_optional_metadata_is_left_out_entirely():
    assert build_fields(title="T") == [("title", "T")]


def test_overrides_are_appended_and_blanks_dropped():
    fields = build_fields(
        title="T", overrides={"youtube_title": "Y", "tiktok_title": ""}
    )
    assert ("youtube_title", "Y") in fields
    assert not any(name == "tiktok_title" for name, _ in fields)


# ---- per-platform overrides ------------------------------------------------


def test_an_instagram_caption_cannot_touch_the_youtube_title():
    """The whole point of overrides: they are separate fields, not one."""
    fields = platform_overrides(
        {"instagram": {"title": "IG caption"}, "youtube": {"title": "YT title"}}
    )
    assert ("instagram_title", "IG caption") in fields
    assert ("youtube_title", "YT title") in fields
    # Neither writes the generic field that every other platform falls back to.
    assert not any(name == "title" for name, _ in fields)


def test_youtube_keeps_its_camelcase_api_names():
    fields = dict(platform_overrides({"youtube": {"privacy": "public", "made_for_kids": True}}))
    assert fields["privacyStatus"] == "public"
    assert fields["selfDeclaredMadeForKids"] == "true"


def test_a_field_a_platform_does_not_support_is_dropped():
    """X has no separate description; inventing a name gets an opaque 400."""
    assert platform_overrides({"x": {"description": "nope"}}) == []
    assert platform_overrides({"nosuchplatform": {"title": "x"}}) == []


def test_blank_overrides_are_not_sent():
    assert platform_overrides({"tiktok": {"title": "", "first_comment": None}}) == []


# ---- required platform fields ----------------------------------------------


def test_facebook_and_pinterest_required_ids_are_caught_before_uploading():
    assert missing_required(["facebook", "pinterest"], {}) == [
        "Facebook page ID",
        "Pinterest board ID",
    ]
    satisfied = {"facebook_page_id": "123", "pinterest_board_id": "456"}
    assert missing_required(["facebook", "pinterest"], satisfied) == []


def test_platforms_without_required_fields_need_nothing():
    assert missing_required(["youtube", "tiktok", "x"], {}) == []


# ---- scheduling and the queue ----------------------------------------------


def test_a_time_and_the_queue_cannot_both_be_sent():
    with pytest.raises(UploadPostError):
        schedule_fields(scheduled_date="2099-01-01T00:00:00Z", add_to_queue=True)


def test_queue_sends_only_the_queue_flag():
    assert schedule_fields(add_to_queue=True) == [("add_to_queue", "true")]


def test_a_timezone_is_sent_because_their_default_is_utc():
    """Without it a creator's 7pm silently becomes 7pm UTC."""
    fields = dict(
        schedule_fields(scheduled_date="2026-10-01T19:00:00-04:00", timezone="America/Toronto")
    )
    assert fields["timezone"] == "America/Toronto"


def test_publishing_now_adds_no_timing_fields():
    assert schedule_fields() == []


def test_a_time_in_the_past_is_refused():
    with pytest.raises(UploadPostError, match="already passed"):
        validate_schedule("2020-01-01T00:00:00Z")


def test_beyond_their_year_horizon_is_refused():
    with pytest.raises(UploadPostError, match="365"):
        validate_schedule("2099-01-01T00:00:00Z")


def test_an_unreadable_date_is_refused_clearly():
    with pytest.raises(UploadPostError, match="could read"):
        validate_schedule("next tuesday-ish")


def test_no_schedule_is_not_an_error():
    assert validate_schedule("") == ""


# ---- retry -----------------------------------------------------------------


def test_retry_goes_through_their_retry_not_a_fresh_upload():
    """Re-uploading would duplicate the posts that already succeeded."""
    calls = []

    class Client:
        def retry(self, request_id):
            calls.append(request_id)
            return {"request_id": request_id, "results": {"x": {"success": True}}}

        def upload_video(self, *a, **k):
            raise AssertionError("retry must not re-upload the media")

    got = UploadPostPublisher(Client(), profile="me").retry("req-3")

    assert calls == ["req-3"]
    assert got.request_id == "req-3"
    assert [o.platform for o in got.published] == ["x"]


# ---- reading connected accounts --------------------------------------------


def _client_with(profiles):
    c = UploadPostClient("k")
    c.list_profiles = lambda: profiles  # type: ignore[method-assign]
    return c


def test_connected_platforms_reads_a_dict_shape():
    c = _client_with([{"username": "me", "social_accounts": {"youtube": {"id": 1}, "tiktok": {}}}])
    # A falsy value means not linked, so tiktok's empty dict drops out.
    assert c.connected_platforms("me") == ["youtube"]


def test_connected_platforms_reads_a_list_of_names():
    c = _client_with([{"username": "me", "accounts": ["youtube", "x"]}])
    assert c.connected_platforms("me") == ["x", "youtube"]


def test_connected_platforms_reads_a_list_of_objects():
    c = _client_with([{"username": "me", "connections": [{"platform": "instagram"}]}])
    assert c.connected_platforms("me") == ["instagram"]


def test_connected_platforms_of_an_unknown_profile_is_empty():
    assert _client_with([{"username": "someone-else"}]).connected_platforms("me") == []


def test_an_unrecognised_profile_shape_does_not_crash():
    """Their docs do not pin the key down, so a shape we have never seen has
    to read as 'nothing linked' rather than raise."""
    assert _client_with([{"username": "me", "somethingNew": {"youtube": 1}}]).connected_platforms(
        "me"
    ) == []
