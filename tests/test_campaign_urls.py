"""A submission's public link: found where the post was recorded, or typed by
the user and checked, and never made up.

WoopSocial and Upload-Post posts are read from clip_publishes by the clip's id
or by the post's window (a re-render gives the clip a new id), with no
provider filter: a clip has one row per platform, and publishing it again
through the other provider takes that row over. A direct YouTube upload is
followed through its publish job, then read as posts.youtube_view sees it.
"""

from datetime import datetime, timedelta, timezone

import pytest

from campaigns import urls
from core.state import StateDB

NOW = datetime(2026, 10, 1, 12, 0, tzinfo=timezone.utc)
TIKTOK = "https://www.tiktok.com/@example/video/7300000000000000001"
YT_ID = "abcdefghijk"


def _z(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _clip(db: StateDB, start=0.0, end=30.0, **kw) -> int:
    db.upsert_video("vid", title="t")
    return db.add_clip("vid", start, end, 90, "hook", path="clip.mp4", **kw)


def _sub(clip_id, *, platform="tiktok", provider="woopsocial", start=0.0, end=30.0, **fields) -> dict:
    return {"id": 1, "campaign_id": 1, "video_id": "vid", "start_s": start, "end_s": end,
            "platform": platform, "clip_id": clip_id, "provider": provider, "post_url": "",
            "post_id": "", "publish_job_id": 0, **fields}


def _post(db, clip_id, *, platform="tiktok", provider="woopsocial", state="published", url="",
          window=(0.0, 30.0), error="", scheduled="", caption="", visibility="default"):
    db.record_clip_publish(clip_id, platform, {
        "provider": provider, "video_id": "vid", "start_s": window[0], "end_s": window[1],
        "state": state, "post_url": url, "post_id": "p-1" if url else "", "error": error,
        "request_id": "r-1", "scheduled_for": scheduled, "caption": caption, "visibility": visibility,
    })


# ---- checking a link --------------------------------------------------------------------------

ACCEPTED = [
    ("tiktok", TIKTOK, TIKTOK),
    ("tiktok", "https://m.tiktok.com/@ex.ample_1/video/7300000000000000001/?is_from_webapp=1",
     "https://www.tiktok.com/@ex.ample_1/video/7300000000000000001"),
    ("tiktok", "https://vm.tiktok.com/ZMexample1/", "https://vm.tiktok.com/ZMexample1"),
    ("tiktok", "https://vt.tiktok.com/ZSexample2", "https://vt.tiktok.com/ZSexample2"),
    ("instagram", "https://www.instagram.com/reel/Cexample_1/?igsh=abc",
     "https://www.instagram.com/reel/Cexample_1"),
    ("instagram", "https://instagram.com/p/Cexample-2", "https://www.instagram.com/p/Cexample-2"),
    ("instagram", "https://www.instagram.com/reels/Cexample3/", "https://www.instagram.com/reels/Cexample3"),
    ("instagram", "https://www.instagram.com/tv/Cexample4", "https://www.instagram.com/tv/Cexample4"),
    ("youtube", f"https://www.youtube.com/shorts/{YT_ID}?feature=share",
     f"https://www.youtube.com/shorts/{YT_ID}"),
    ("youtube", f"https://m.youtube.com/watch?v={YT_ID}&t=10s#c", f"https://www.youtube.com/watch?v={YT_ID}"),
    ("youtube", f"https://youtube.com/watch?list=x&v={YT_ID}", f"https://www.youtube.com/watch?v={YT_ID}"),
    ("youtube", f"https://youtu.be/{YT_ID}?si=abc", f"https://youtu.be/{YT_ID}"),
    ("youtube", f"https://www.youtu.be/{YT_ID}/", f"https://youtu.be/{YT_ID}"),
    ("facebook", "https://www.facebook.com/reel/123456789", "https://www.facebook.com/reel/123456789"),
    ("facebook", "https://web.facebook.com/example/videos/1/", "https://www.facebook.com/example/videos/1"),
    ("facebook", "https://fb.watch/abcDEF/", "https://fb.watch/abcDEF"),
    ("x", "https://x.com/example/status/1800000000000000000?s=20",
     "https://x.com/example/status/1800000000000000000"),
    ("x", "https://twitter.com/example/status/1800000000000000000",
     "https://twitter.com/example/status/1800000000000000000"),
    ("threads", "https://www.threads.net/@example/post/Cexample", "https://www.threads.net/@example/post/Cexample"),
    ("threads", "https://threads.com/@example/post/C1", "https://threads.com/@example/post/C1"),
    ("linkedin", "https://www.linkedin.com/posts/example-1", "https://www.linkedin.com/posts/example-1"),
    ("pinterest", "https://pinterest.com/pin/123/", "https://pinterest.com/pin/123"),
    ("pinterest", "https://uk.pinterest.co.uk/pin/123/", "https://uk.pinterest.co.uk/pin/123"),
    ("bluesky", "https://bsky.app/profile/example.bsky.social/post/3k",
     "https://bsky.app/profile/example.bsky.social/post/3k"),
    ("reddit", "https://www.reddit.com/r/example/comments/abc/x/", "https://www.reddit.com/r/example/comments/abc/x"),
]


@pytest.mark.parametrize("platform, given, normalised", ACCEPTED)
def test_a_post_link_per_platform_is_accepted_without_its_tracking(platform, given, normalised):
    assert urls.validate(platform, given) == (normalised, "")
    assert urls.platform_of(given) == platform


SPELLINGS = {
    "tiktok": ["https://www.tiktok.com/@example/video/7300000000000000001",
               "https://www.tiktok.com/@example/video/7300000000000000001/",
               "https://tiktok.com/@example/video/7300000000000000001?lang=en",
               "https://m.tiktok.com/@example/video/7300000000000000001/"],
    "youtube": [f"https://www.youtube.com/shorts/{YT_ID}",
                f"https://youtube.com/shorts/{YT_ID}?feature=share",
                f"https://www.youtube.com/shorts/{YT_ID}/",
                f"https://m.youtube.com/shorts/{YT_ID}"],
    "youtube_watch": [f"https://www.youtube.com/watch?v={YT_ID}",
                      f"https://youtube.com/watch?v={YT_ID}",
                      f"https://m.youtube.com/watch/?v={YT_ID}&t=3"],
    "instagram": ["https://www.instagram.com/reel/AbC123", "https://www.instagram.com/reel/AbC123/",
                  "https://instagram.com/reel/AbC123/?igsh=x"],
    "facebook": ["https://www.facebook.com/reel/123456789", "https://m.facebook.com/reel/123456789/",
                 "https://facebook.com/reel/123456789", "https://web.facebook.com/reel/123456789"],
}


@pytest.mark.parametrize("key", sorted(SPELLINGS))
def test_one_post_spelled_several_ways_is_one_link(key):
    platform = key.split("_")[0]
    found = {urls.validate(platform, given) for given in SPELLINGS[key]}
    assert len(found) == 1
    (normalised, problem), = found
    assert problem == "" and normalised == SPELLINGS[key][0]
    # The one spelling is one Clips Kitty can open: the www. host and no
    # trailing slash (ui/src/main/index.ts EXTERNAL_ALLOWED).
    assert not normalised.endswith("/") and normalised.startswith("https://www.")


REFUSED = [
    ("tiktok", "", "Enter the post's link"),
    ("tiktok", "http://www.tiktok.com/@example/video/7300000000000000001", "https://"),
    ("tiktok", "www.tiktok.com/@example/video/7300000000000000001", "https://"),
    ("tiktok", "https://www.tiktok.com/@example", "doesn't look like the link of a TikTok post"),
    ("tiktok", "https://www.tiktok.com:8443/@example/video/7300000000000000001", "isn't a link to a post"),
    ("tiktok", "https://user@www.tiktok.com/@example/video/1", "isn't a link to a post"),
    ("tiktok", TIKTOK.replace("tiktok.com", "tiktok.com.example.com"), "can't tell which platform"),
    ("tiktok", f"https://www.youtube.com/shorts/{YT_ID}", "That's a YouTube link, not a TikTok one"),
    ("instagram", "https://www.instagram.com/example/", "doesn't look like the link"),
    ("youtube", "https://www.youtube.com/watch?list=x", "doesn't look like the link"),
    ("youtube", "https://www.youtube.com/shorts/short", "doesn't look like the link"),
    ("youtube", "https://www.youtube.com/@example", "doesn't look like the link"),
    ("x", "https://x.com/example", "doesn't look like the link"),
    ("reddit", "https://www.reddit.com/", "doesn't look like the link"),
    ("google_business", "https://www.google.com/maps/place/x", "can't tell which platform"),
    ("youtube", "not a link at all", "https://"),
]


@pytest.mark.parametrize("platform, given, problem", REFUSED)
def test_a_link_that_is_not_a_public_post_on_the_platform_is_refused(platform, given, problem):
    normalised, found = urls.validate(platform, given)
    assert normalised == "" and problem in found


def test_a_tiktok_short_link_passes_with_a_warning():
    assert urls.link_warning("tiktok", "https://vm.tiktok.com/ZMexample1/") == urls.SHORT_LINK_WARNING
    assert urls.link_warning("tiktok", TIKTOK) == ""
    assert urls.link_warning("youtube", f"https://youtu.be/{YT_ID}") == ""


def test_the_account_a_link_shows():
    assert urls.account_from_url("tiktok", TIKTOK) == "@example"
    assert urls.account_from_url("tiktok", "https://vm.tiktok.com/ZMexample1/") == ""
    assert urls.account_from_url("x", "https://x.com/example/status/1") == ""


# ---- WoopSocial and Upload-Post ---------------------------------------------------------------

@pytest.mark.parametrize("state", ["queued", "processing", "sending"])
def test_a_post_on_its_way_is_not_published_yet(db, state):
    clip_id = _clip(db)
    _post(db, clip_id, state=state)
    res = urls.resolve(db, _sub(clip_id), now=NOW)
    assert (res.state, res.url, res.outcome, res.provider) == ("not_published", "", "", "woopsocial")


def test_a_published_post_with_no_link_yet(db):
    clip_id = _clip(db)
    _post(db, clip_id)
    assert urls.resolve(db, _sub(clip_id), now=NOW).state == "published_no_url"


def test_a_published_post_with_its_link_is_live(db):
    clip_id = _clip(db)
    _post(db, clip_id, url=TIKTOK + "?lang=en", caption="So good #clips @example", visibility="default")
    res = urls.resolve(db, _sub(clip_id), now=NOW)
    assert (res.state, res.url, res.source, res.provider) == ("live", TIKTOK, "provider", "woopsocial")
    assert (res.account, res.caption, res.visibility, res.post_id) == (
        "@example", "So good #clips @example", "default", "p-1")


def test_a_link_from_the_publisher_that_is_not_a_post_is_held_for_a_person(db):
    clip_id = _clip(db)
    _post(db, clip_id, url="https://www.tiktok.com/@example")
    res = urls.resolve(db, _sub(clip_id), now=NOW)
    assert res.state == "blocked" and res.reason == urls.BLOCKED_PROVIDER
    assert res.url == "https://www.tiktok.com/@example"


@pytest.mark.parametrize("state", ["failed", "skipped"])
def test_a_failed_or_skipped_post_says_why(db, state):
    clip_id = _clip(db)
    _post(db, clip_id, state=state, error="TikTok isn't connected")
    res = urls.resolve(db, _sub(clip_id), now=NOW)
    assert (res.state, res.outcome, res.reason) == ("not_published", state, "TikTok isn't connected")
    assert res.failed_at.endswith("Z")


def test_the_post_is_found_by_its_window_after_a_rerender(db):
    old = _clip(db)
    _post(db, old, url=TIKTOK)
    new = _clip(db, 0.0, 25.0)
    db.follow_clip_id(old, new)
    # The submission was attached before the re-render: the old id and window.
    res = urls.resolve(db, _sub(old), now=NOW)
    assert (res.state, res.url) == ("live", TIKTOK)
    # And one attached afterwards finds it by the new id.
    assert urls.resolve(db, _sub(new, end=25.0), now=NOW).url == TIKTOK


def test_a_clip_published_again_through_the_other_provider_still_resolves(db):
    clip_id = _clip(db)
    _post(db, clip_id, state="failed", error="Rejected")
    _post(db, clip_id, provider="upload_post", url=TIKTOK)
    res = urls.resolve(db, _sub(clip_id, provider="woopsocial"), now=NOW)
    assert (res.state, res.provider, res.url) == ("live", "upload_post", TIKTOK)


def test_the_published_row_wins_over_a_newer_one_that_failed(db):
    old = _clip(db)
    _post(db, old, url=TIKTOK)
    new = _clip(db, 0.0, 25.0)
    _post(db, new, state="failed", error="Rejected", window=(0.0, 25.0))
    db.conn.execute("UPDATE clip_publishes SET updated_at = '2099-01-01T00:00:00' WHERE clip_id = ?", (new,))
    db.conn.commit()
    assert urls.resolve(db, _sub(new, start=0.0, end=30.0), now=NOW).url == TIKTOK


def test_another_clips_post_is_never_taken(db):
    clip_id = _clip(db)
    other = _clip(db, 40.0, 70.0)
    _post(db, other, url=TIKTOK, window=(40.0, 70.0))
    _post(db, clip_id, platform="youtube", url=f"https://www.youtube.com/shorts/{YT_ID}")
    assert urls.resolve(db, _sub(clip_id), now=NOW).state == "not_published"


def test_due_at_comes_from_the_scheduled_time(db):
    clip_id = _clip(db)
    _post(db, clip_id, state="queued", scheduled="2030-01-01T09:00:00Z")
    assert urls.resolve(db, _sub(clip_id), now=NOW).due_at == "2030-01-01T09:00:00Z"

    db.conn.execute("UPDATE clip_publishes SET scheduled_for = '', created_at = '2026-09-30T10:00:00'")
    db.conn.commit()
    sent = datetime(2026, 9, 30, 10, 0).astimezone()
    assert urls.resolve(db, _sub(clip_id), now=NOW).due_at == _z(sent)


def test_no_row_at_all_is_not_published(db):
    clip_id = _clip(db)
    res = urls.resolve(db, _sub(clip_id), now=NOW)
    assert (res.state, res.url, res.reason) == ("not_published", "", "")


# ---- a direct YouTube upload --------------------------------------------------------------------

def _upload(db, clip_id, *, window=(0.0, 30.0), **fields):
    db.record_publish(clip_id, {"youtube_id": YT_ID, "video_id": "vid", "start_s": window[0],
                                "end_s": window[1], "state": "uploaded", "channel_title": "Example Channel",
                                "description": "Watch this #clips", **fields})


def _yt(clip_id, **fields):
    return _sub(clip_id, platform="youtube", provider="youtube", **fields)


def test_a_public_short_is_live_with_its_shorts_link(db):
    clip_id = _clip(db)
    _upload(db, clip_id, privacy="public", actual_privacy="public")
    res = urls.resolve(db, _yt(clip_id), now=NOW)
    assert (res.state, res.url, res.source) == ("live", f"https://www.youtube.com/shorts/{YT_ID}", "youtube")
    assert (res.account, res.caption, res.visibility, res.post_id) == (
        "Example Channel", "Watch this #clips", "public", YT_ID)
    assert res.posted_at.endswith("Z") and res.due_at == res.posted_at


def test_a_longform_upload_links_to_its_watch_page(db):
    clip_id = _clip(db, 0.0, 600.0, render_opts='{"profile": "longform"}')
    _upload(db, clip_id, window=(0.0, 600.0), privacy="public")
    res = urls.resolve(db, _yt(clip_id, end=600.0), now=NOW)
    assert res.url == f"https://www.youtube.com/watch?v={YT_ID}"


def test_an_unlisted_upload_is_live_but_not_public(db):
    clip_id = _clip(db)
    _upload(db, clip_id, privacy="unlisted", actual_privacy="unlisted")
    res = urls.resolve(db, _yt(clip_id), now=NOW)
    assert (res.state, res.visibility) == ("live", "unlisted")


def test_a_scheduled_upload_waits_for_its_time_which_is_its_due_time(db):
    clip_id = _clip(db)
    at = NOW + timedelta(days=2)
    _upload(db, clip_id, privacy="public", actual_privacy="private", publish_at=_z(at))
    res = urls.resolve(db, _yt(clip_id), now=NOW)
    assert (res.state, res.due_at, res.url) == ("not_published", _z(at), "")


def test_a_scheduled_upload_is_not_live_until_read_back_public(db):
    clip_id = _clip(db)
    at = NOW - timedelta(hours=1)
    _upload(db, clip_id, privacy="public", actual_privacy="private", publish_at=_z(at))
    assert urls.resolve(db, _yt(clip_id), now=NOW).state == "not_published"
    db.set_upload_check(clip_id, checked_at=_z(NOW), actual_privacy="public")
    res = urls.resolve(db, _yt(clip_id), now=NOW)
    assert (res.state, res.visibility, res.posted_at) == ("live", "public", _z(at))


def test_a_scheduled_upload_still_private_a_day_after_is_held_for_a_person(db):
    clip_id = _clip(db)
    at = NOW - timedelta(days=2)
    _upload(db, clip_id, privacy="public", actual_privacy="private", publish_at=_z(at),
            checked_at=_z(at + timedelta(hours=25)))
    res = urls.resolve(db, _yt(clip_id), now=NOW)
    assert (res.state, res.reason) == ("blocked", urls.STILL_PRIVATE)


@pytest.mark.parametrize("fields", [{"state": "locked_private", "privacy": "public"},
                                    {"privacy": "private", "actual_privacy": "private"}])
def test_a_private_video_is_held_for_a_person(db, fields):
    clip_id = _clip(db)
    _upload(db, clip_id, **fields)
    res = urls.resolve(db, _yt(clip_id), now=NOW)
    assert (res.state, res.reason, res.url) == ("blocked", urls.PRIVATE, "")


def test_a_rejected_upload_failed(db):
    clip_id = _clip(db)
    _upload(db, clip_id, state="rejected", error="Rejected for a copyright claim", privacy="public")
    res = urls.resolve(db, _yt(clip_id), now=NOW)
    assert (res.state, res.outcome, res.reason) == ("not_published", "failed", "Rejected for a copyright claim")


def test_the_publish_job_is_followed_while_it_runs_and_when_it_fails(db):
    clip_id = _clip(db)
    job = db.add_publish_job(clip_id, "{}", video_id="vid", start_s=0.0, end_s=30.0)
    sub = _yt(clip_id, publish_job_id=job)
    assert urls.resolve(db, sub, now=NOW).state == "not_published"
    db.finish_publish_job(job, "failed", error="The YouTube quota ran out")
    res = urls.resolve(db, sub, now=NOW)
    assert (res.state, res.outcome, res.reason) == ("not_published", "failed", "The YouTube quota ran out")
    assert res.failed_at.endswith("Z")
    db.finish_publish_job(job, "interrupted")
    assert urls.resolve(db, sub, now=NOW).reason == "The upload interrupted."


def test_the_publish_job_leads_to_the_upload_across_a_rerender(db):
    old = _clip(db)
    job = db.add_publish_job(old, "{}", video_id="vid", start_s=0.0, end_s=30.0)
    # Unsaved trims rendered first: the clip gets a new id and window, and
    # _publish points the job at it before it uploads.
    new = _clip(db, 0.0, 25.0)
    db.set_publish_job_clip(job, new)
    db.finish_publish_job(job, "done", youtube_id=YT_ID)
    _upload(db, new, window=(0.0, 25.0), privacy="public")

    without_job = urls.resolve(db, _yt(old), now=NOW)
    assert without_job.state == "not_published"
    res = urls.resolve(db, _yt(old, publish_job_id=job), now=NOW)
    assert (res.state, res.url) == ("live", f"https://www.youtube.com/shorts/{YT_ID}")


def test_a_deleted_clips_upload_offers_the_link_it_had_for_checking(db):
    res = urls.resolve(db, _yt(0, post_id=YT_ID), now=NOW)
    assert (res.state, res.url, res.reason) == (
        "blocked", f"https://www.youtube.com/shorts/{YT_ID}", urls.UPLOAD_GONE)
    assert urls.resolve(db, _yt(0), now=NOW).state == "not_published"


def test_a_youtube_submission_with_no_provider_yet_looks_for_the_direct_upload(db):
    clip_id = _clip(db)
    _upload(db, clip_id, privacy="public")
    res = urls.resolve(db, _sub(clip_id, platform="youtube", provider=""), now=NOW)
    assert (res.state, res.provider) == ("live", "youtube")


# ---- a link the user typed ---------------------------------------------------------------------

def test_a_typed_link_is_taken_on_the_users_word_once_it_checks_out(db):
    res = urls.resolve(db, _sub(0, provider="manual", post_url=TIKTOK + "?x=1"), now=NOW)
    assert (res.state, res.url, res.source, res.account) == ("live", TIKTOK, "manual", "@example")
    bad = urls.resolve(db, _sub(0, provider="manual", post_url="https://www.tiktok.com/@example"), now=NOW)
    assert bad.state == "blocked" and "doesn't look like" in bad.reason
    # No publisher will ever answer for it: it goes to the user at once
    # instead of waiting 14 days for a post nobody follows.
    none = urls.resolve(db, _sub(0, provider="manual"), now=NOW)
    assert (none.state, none.reason, none.url) == ("blocked", "Enter the post's link", "")
