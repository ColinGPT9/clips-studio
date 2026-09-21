"""WoopSocial: the post body, the response parsing, and the client.

Offline, like the rest of the suite.
"""

from pathlib import Path

import pytest

from publish.errors import AuthRequired, NotConnected, QuotaExceeded, RateLimited
from publish.woopsocial import (
    FROM_PLATFORM,
    PLATFORMS,
    WoopSocialClient,
    WoopSocialError,
    WoopSocialPublisher,
    _classify,
    build_post,
    parse_post,
)

# ---- the post body ---------------------------------------------------------


def test_one_parent_post_carries_a_child_per_destination():
    """The fan-out: one request, several socialAccounts."""
    body = build_post(
        media_id="m1",
        text="caption",
        accounts=[
            {"platform": "YOUTUBE", "id": "a1"},
            {"platform": "TIKTOK", "id": "a2"},
        ],
        title="My clip",
    )
    assert body["content"][0]["media"][0] == {"type": "MEDIA_LIBRARY", "mediaId": "m1"}
    assert [c["platform"] for c in body["socialAccounts"]] == ["YOUTUBE", "TIKTOK"]
    assert [c["socialAccountId"] for c in body["socialAccounts"]] == ["a1", "a2"]


def test_youtube_always_gets_a_title_because_it_is_rejected_without_one():
    body = build_post(media_id="m", text="t", accounts=[{"platform": "YOUTUBE", "id": "a"}],
                      title="The title")
    child = body["socialAccounts"][0]
    assert child["title"] == "The title"
    assert child["privacy"] == "public"


def test_a_youtube_title_is_clamped_to_youtubes_limit():
    body = build_post(media_id="m", text="t", accounts=[{"platform": "YOUTUBE", "id": "a"}],
                      title="x" * 300)
    assert len(body["socialAccounts"][0]["title"]) == 100


def test_per_platform_overrides_land_on_their_own_child():
    """Different objects, so an Instagram caption cannot reach YouTube."""
    body = build_post(
        media_id="m",
        text="common",
        accounts=[{"platform": "YOUTUBE", "id": "a1"}, {"platform": "INSTAGRAM", "id": "a2"}],
        title="Common title",
        overrides={"instagram": {"title": "IG only"}, "youtube": {"privacy": "unlisted"}},
    )
    by_platform = {c["platform"]: c for c in body["socialAccounts"]}
    assert by_platform["YOUTUBE"]["title"] == "Common title"
    assert by_platform["YOUTUBE"]["privacy"] == "unlisted"
    assert by_platform["INSTAGRAM"]["title"] == "IG only"


def test_publish_now_versus_scheduled():
    now = build_post(media_id="m", text="t", accounts=[])
    assert now["schedule"] == {"type": "PUBLISH_NOW"}

    later = build_post(media_id="m", text="t", accounts=[], scheduled_for="2026-10-01T12:00:00Z")
    assert later["schedule"] == {
        "type": "SCHEDULE_FOR_LATER",
        "scheduledFor": "2026-10-01T12:00:00Z",
    }


def test_an_unknown_platform_is_dropped_rather_than_guessed():
    body = build_post(media_id="m", text="t", accounts=[{"platform": "MYSPACE", "id": "a"}])
    assert body["socialAccounts"] == []


# ---- reading the response --------------------------------------------------


def test_child_statuses_become_per_platform_outcomes():
    got = parse_post(
        {
            "id": "p1",
            "socialAccountPosts": [
                {"platform": "YOUTUBE", "status": "PUBLISHED",
                 "permalink": "https://youtu.be/x", "externalPostId": "x"},
                {"platform": "TIKTOK", "status": "FAILED", "error": "token expired"},
            ],
        }
    )
    assert got.request_id == "p1"
    assert [o.platform for o in got.published] == ["youtube"]
    assert got.failed[0].error == "token expired"
    # Outcomes are sorted by platform, so look it up rather than index.
    assert next(o for o in got.outcomes if o.platform == "youtube").post_url == "https://youtu.be/x"


def test_scheduled_children_read_as_queued_not_done():
    got = parse_post(
        {"id": "p", "socialAccountPosts": [{"platform": "YOUTUBE", "status": "SCHEDULED"}]}
    )
    assert got.outcomes[0].state == "queued"
    assert not got.done


def test_an_empty_or_odd_response_does_not_crash():
    assert parse_post({}).outcomes == []
    assert parse_post({"socialAccountPosts": None}).outcomes == []


def test_platform_names_round_trip_to_the_apps_own_lowercase():
    for lower, upper in PLATFORMS.items():
        assert FROM_PLATFORM[upper] == lower


# ---- errors ----------------------------------------------------------------


def test_a_bad_key_points_at_settings():
    assert isinstance(_classify(401, {}, ""), AuthRequired)
    assert isinstance(_classify(403, {}, ""), AuthRequired)


def test_rate_limit_is_retryable_but_credits_are_not():
    assert isinstance(_classify(429, {}, ""), RateLimited)
    assert _classify(429, {}, "").retryable
    out_of_credits = _classify(402, {"message": "Not enough credits"}, "")
    assert isinstance(out_of_credits, QuotaExceeded)
    assert not out_of_credits.retryable


def test_server_errors_are_retryable():
    assert _classify(500, {}, "boom").retryable


# ---- the client ------------------------------------------------------------


def test_no_key_refuses_before_any_request():
    with pytest.raises(NotConnected):
        WoopSocialClient("").projects()


def test_the_key_is_sent_as_a_bearer_token(monkeypatch):
    seen = {}

    class FakeResponse:
        def read(self):
            return b"[]"

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def fake_urlopen(req, timeout=0):
        seen["url"] = req.full_url
        seen["auth"] = req.get_header("Authorization")
        return FakeResponse()

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    WoopSocialClient("k123").projects()

    # Bearer, not Apikey — the other provider uses the other scheme.
    assert seen["auth"] == "Bearer k123"
    assert seen["url"] == "https://api.woopsocial.com/v1/projects"


@pytest.mark.parametrize(
    "post_id",
    ["../../api/keys", "abc/def", "a b", "", "x" * 129, "id?x=1", "id#frag"],
)
def test_a_post_id_that_could_steer_the_request_is_refused(post_id, monkeypatch):
    """quote() leaves slashes alone, so an unchecked id walks off /posts/.

    This is the SSRF guard: nothing may reach the network at all.
    """

    def never(*a, **k):  # pragma: no cover - the point is it is not called
        raise AssertionError("a request was made for an invalid id")

    monkeypatch.setattr("urllib.request.urlopen", never)

    with pytest.raises(WoopSocialError, match="post id"):
        WoopSocialClient("k").get_post(post_id)


def test_a_normal_post_id_still_reaches_its_own_endpoint(monkeypatch):
    seen = {}

    class FakeResponse:
        def read(self):
            return b"{}"

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def fake_urlopen(req, timeout=0):
        seen["url"] = req.full_url
        return FakeResponse()

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    WoopSocialClient("k").get_post("post_ABC-123")

    assert seen["url"] == "https://api.woopsocial.com/v1/posts/post_ABC-123"


def test_an_oversized_clip_is_refused_before_uploading(tmp_path: Path, monkeypatch):
    """Their single-request endpoint stops at 100 MB; say so rather than
    sending 400 MB and waiting for a failure."""
    from publish import woopsocial as ws

    clip = tmp_path / "big.mp4"
    clip.write_bytes(b"x")
    monkeypatch.setattr(ws, "SINGLE_UPLOAD_MAX_BYTES", 0)

    with pytest.raises(WoopSocialError, match="100 MB"):
        ws.WoopSocialClient("k").upload_media(clip, "proj")


# ---- the publisher ---------------------------------------------------------


class _FakeClient:
    def __init__(self, accounts):
        self._accounts = accounts
        self.uploaded = []
        self.posted = []

    def social_accounts(self, project_id=""):
        return self._accounts

    def upload_media(self, video, project_id):
        self.uploaded.append(str(video))
        return "media-1"

    def create_post(self, body):
        self.posted.append(body)
        return {
            "id": "post-1",
            "socialAccountPosts": [
                {"platform": c["platform"], "status": "SCHEDULED"}
                for c in body["socialAccounts"]
            ],
        }


def test_publishing_uploads_once_then_posts_once(tmp_path: Path):
    clip = tmp_path / "c.mp4"
    clip.write_bytes(b"v")
    client = _FakeClient([{"platform": "YOUTUBE", "id": "a1"}, {"platform": "TIKTOK", "id": "a2"}])

    got = WoopSocialPublisher(client, "proj").start(
        clip, platforms=["youtube", "tiktok"], title="T", text="body"
    )

    assert len(client.uploaded) == 1, "the clip is uploaded once, not once per platform"
    assert len(client.posted) == 1, "one post covers every destination"
    assert {o.platform for o in got.outcomes} == {"tiktok", "youtube"}


def test_a_platform_with_no_connected_account_is_skipped_not_failed(tmp_path: Path):
    clip = tmp_path / "c.mp4"
    clip.write_bytes(b"v")
    client = _FakeClient([{"platform": "YOUTUBE", "id": "a1"}])

    got = WoopSocialPublisher(client, "proj").start(
        clip, platforms=["youtube", "pinterest"], title="T", text="b"
    )

    states = {o.platform: o.state for o in got.outcomes}
    assert states["pinterest"] == "skipped"
    assert states["pinterest"] != "failed"
    assert "Not connected" in next(o for o in got.outcomes if o.platform == "pinterest").error


def test_nothing_connected_at_all_is_refused_before_uploading(tmp_path: Path):
    clip = tmp_path / "c.mp4"
    clip.write_bytes(b"v")
    client = _FakeClient([])
    with pytest.raises(NotConnected):
        WoopSocialPublisher(client, "proj").start(
            clip, platforms=["youtube"], title="T", text="b"
        )
    assert client.uploaded == [], "must not upload a clip it cannot post"


def test_no_project_is_refused(tmp_path: Path):
    clip = tmp_path / "c.mp4"
    clip.write_bytes(b"v")
    with pytest.raises(NotConnected):
        WoopSocialPublisher(_FakeClient([]), "").start(
            clip, platforms=["youtube"], title="T", text="b"
        )
