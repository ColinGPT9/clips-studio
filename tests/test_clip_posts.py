"""Where one clip went, whichever way it was sent.

The publish panel read Upload-Post's own route, which answers 404 while
Upload-Post is off, so a creator publishing only through WoopSocial lost
"Where it went" on reopening a clip, and direct YouTube uploads never showed
there at all. posts_for_clip reads every provider's rows and the direct upload,
behind no provider's switch.
"""

import json
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import pytest

from core.state import StateDB
from server import posts

NOW = datetime(2026, 10, 1, 12, 0, tzinfo=timezone.utc)
SHAPE = {"clip_id", "platform", "provider", "state", "post_id", "post_url", "error",
         "request_id", "scheduled_for", "live", "created_at", "updated_at"}


def _clip(db, start=0.0, end=30.0):
    db.upsert_video("vid", title="t")
    return db.add_clip("vid", start, end, 90, "hook", path="clip.mp4")


def _post(db, clip_id, platform, provider, *, state="published", url="", request_id="r1",
          window=None, scheduled="", updated=None):
    clip = db.get_clip(clip_id)
    video_id, start, end = window or (clip["video_id"], clip["start_s"], clip["end_s"])
    db.record_clip_publish(clip_id, platform, {
        "provider": provider, "video_id": video_id, "start_s": start, "end_s": end,
        "state": state, "post_url": url, "request_id": request_id, "scheduled_for": scheduled,
    })
    if updated:
        db.conn.execute("UPDATE clip_publishes SET updated_at = ? WHERE clip_id = ? AND platform = ?",
                        (updated, clip_id, platform))
        db.conn.commit()


def _by(found):
    return {(p["provider"], p["platform"]): p for p in found}


def test_every_provider_and_the_direct_upload_in_one_list(db):
    clip_id = _clip(db)
    _post(db, clip_id, "tiktok", "woopsocial",
          url="https://www.tiktok.com/@someone/video/7300000000000000001")
    _post(db, clip_id, "instagram", "upload_post", state="queued", request_id="up-1",
          scheduled="2026-10-03T09:00:00Z")
    db.record_publish(clip_id, {"youtube_id": "abcdefghijk", "privacy": "public",
                                "actual_privacy": "public", "state": "uploaded"})

    found = posts.posts_for_clip(db, clip_id, now=NOW)

    assert all(set(p) == SHAPE for p in found)
    got = _by(found)
    assert set(got) == {("woopsocial", "tiktok"), ("upload_post", "instagram"), ("youtube", "youtube")}
    assert got[("woopsocial", "tiktok")]["live"] is True
    queued = got[("upload_post", "instagram")]
    assert (queued["live"], queued["scheduled_for"], queued["request_id"]) == (
        False, "2026-10-03T09:00:00Z", "up-1")
    direct = got[("youtube", "youtube")]
    assert (direct["state"], direct["live"], direct["post_id"], direct["post_url"]) == (
        "published", True, "abcdefghijk", "https://www.youtube.com/shorts/abcdefghijk")


def test_the_direct_upload_keeps_its_upload_time_and_needs_an_id(db):
    """Its times are local like the provider rows', so not checked_at (UTC);
    an uploads row that never got a video id is no post at all."""
    clip_id, failed = _clip(db), _clip(db, 40, 70)
    db.record_publish(clip_id, {"youtube_id": "abcdefghijk", "actual_privacy": "public",
                                "checked_at": "2026-10-01T11:00:00Z"})
    db.record_publish(failed, {"youtube_id": "", "state": "failed"})

    direct = posts.posts_for_clip(db, clip_id, now=NOW)[0]

    uploaded = db.get_upload(clip_id)["uploaded_at"]
    assert (direct["created_at"], direct["updated_at"]) == (uploaded, uploaded)
    assert posts.posts_for_clip(db, failed, now=NOW) == []


def test_a_published_post_with_no_link_yet_is_not_live(db):
    clip_id = _clip(db)
    _post(db, clip_id, "tiktok", "woopsocial")
    assert posts.posts_for_clip(db, clip_id, now=NOW)[0]["live"] is False


def test_a_scheduled_direct_upload_shows_as_scheduled(db):
    clip_id = _clip(db)
    db.record_publish(clip_id, {"youtube_id": "abcdefghijk", "privacy": "public",
                                "actual_privacy": "private", "publish_at": "2026-10-05T18:00:00Z"})
    direct = posts.posts_for_clip(db, clip_id, now=NOW)[0]
    assert (direct["state"], direct["live"], direct["post_url"], direct["scheduled_for"]) == (
        "scheduled", False, "", "2026-10-05T18:00:00Z")


def test_posts_are_found_by_the_clips_window_after_a_re_render(db):
    """Applying edits deletes the clip row and makes a new one; a row still on
    the old id is found by the window it was posted with, and the direct
    upload comes along with the clip (reattach_clip_rows)."""
    old_id = _clip(db)
    _post(db, old_id, "tiktok", "woopsocial", url="https://www.tiktok.com/@someone/video/1")
    db.record_publish(old_id, {"youtube_id": "abcdefghijk", "actual_privacy": "public"})

    saved = db.detach_clip_rows(old_id)
    db.conn.execute("DELETE FROM clips WHERE id = ?", (old_id,))
    db.conn.commit()
    new_id = db.add_clip("vid", 0.0, 30.0, 90, "hook", path="clip2.mp4")
    db.reattach_clip_rows(new_id, saved)

    got = _by(posts.posts_for_clip(db, new_id, now=NOW))
    assert set(got) == {("woopsocial", "tiktok"), ("youtube", "youtube")}
    assert got[("woopsocial", "tiktok")]["clip_id"] == old_id
    assert got[("youtube", "youtube")]["clip_id"] == new_id


def test_a_trimmed_clip_keeps_its_posts_by_id(db):
    """A re-render that moved the window moves the rows to the new id
    (follow_clip_id); they keep the window they were posted with."""
    old_id = _clip(db)
    _post(db, old_id, "tiktok", "woopsocial")
    db.conn.execute("DELETE FROM clips WHERE id = ?", (old_id,))
    new_id = db.add_clip("vid", 2.0, 30.0, 90, "hook", path="clip2.mp4")
    db.follow_clip_id(old_id, new_id)

    assert [p["platform"] for p in posts.posts_for_clip(db, new_id, now=NOW)] == ["tiktok"]


def test_one_row_per_provider_and_platform_the_newest(db):
    old_id = _clip(db)
    _post(db, old_id, "tiktok", "woopsocial", state="failed", request_id="first",
          updated="2026-09-01T08:00:00")
    db.conn.execute("DELETE FROM clips WHERE id = ?", (old_id,))
    new_id = db.add_clip("vid", 0.0, 30.0, 90, "hook", path="clip2.mp4")
    _post(db, new_id, "tiktok", "woopsocial", state="queued", request_id="again",
          updated="2026-09-02T08:00:00")
    _post(db, new_id, "youtube", "upload_post", state="queued", request_id="again-up")

    found = posts.posts_for_clip(db, new_id, now=NOW)

    assert [(p["provider"], p["platform"], p["request_id"]) for p in found] == [
        ("woopsocial", "tiktok", "again"), ("upload_post", "youtube", "again-up")]


def test_on_a_tie_the_clips_own_row_wins(db):
    old_id = _clip(db)
    _post(db, old_id, "tiktok", "woopsocial", state="failed", request_id="first")
    db.conn.execute("DELETE FROM clips WHERE id = ?", (old_id,))
    new_id = db.add_clip("vid", 0.0, 30.0, 90, "hook", path="clip2.mp4")
    _post(db, new_id, "tiktok", "woopsocial", state="queued", request_id="again")
    db.conn.execute("UPDATE clip_publishes SET created_at = '2026-09-02T08:00:00', "
                    "updated_at = '2026-09-02T08:00:00'")
    db.conn.commit()

    found = posts.posts_for_clip(db, new_id, now=NOW)

    assert [p["request_id"] for p in found] == ["again"]


def test_another_clip_of_the_same_video_is_not_mixed_in(db):
    mine, other = _clip(db, 0, 30), _clip(db, 40, 70)
    _post(db, other, "tiktok", "woopsocial")
    assert posts.posts_for_clip(db, mine, now=NOW) == []


def test_a_bare_upload_from_before_privacy_was_recorded_is_live(db):
    clip_id = _clip(db)
    db.record_publish(clip_id, {"youtube_id": "abcdefghijk"})
    direct = posts.posts_for_clip(db, clip_id, now=NOW)[0]
    assert (direct["state"], direct["live"], direct["post_url"]) == (
        "published", True, "https://www.youtube.com/shorts/abcdefghijk")


def test_the_link_follows_the_window_that_went_out_not_the_clips_new_one(db):
    """Uploaded as a 4-minute video, then trimmed to 30 s: the post that went
    out is still a watch page."""
    clip_id = _clip(db)
    db.record_publish(clip_id, {"youtube_id": "abcdefghijk", "video_id": "vid",
                                "start_s": 0.0, "end_s": 240.0, "actual_privacy": "public"})
    assert posts.posts_for_clip(db, clip_id, now=NOW)[0]["post_url"] == (
        "https://www.youtube.com/watch?v=abcdefghijk")


# ---- the route --------------------------------------------------------------------


@pytest.fixture
def client(tmp_path):
    pytest.importorskip("fastapi")
    pytest.importorskip("httpx")
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from server import posts_api

    path = tmp_path / "state.db"
    app = FastAPI()
    posts_api.install(app, db=lambda: StateDB(path))
    c = TestClient(app, base_url="http://127.0.0.1")
    c.db_path = path
    return c


def test_the_route_answers_with_every_provider_switched_off(client):
    d = StateDB(client.db_path)
    try:
        clip_id = _clip(d)
        _post(d, clip_id, "tiktok", "woopsocial",
              url="https://www.tiktok.com/@someone/video/7300000000000000001")
        empty = _clip(d, 40, 70)
    finally:
        d.close()

    got = client.get(f"/clips/{clip_id}/posts")

    assert got.status_code == 200
    body = got.json()
    assert body["clip_id"] == clip_id
    assert [(p["provider"], p["platform"], p["live"]) for p in body["posts"]] == [
        ("woopsocial", "tiktok", True)]
    assert client.get(f"/clips/{empty}/posts").json() == {"clip_id": empty, "posts": []}
    assert client.get("/clips/999/posts").status_code == 404


def test_the_route_still_answers_for_a_deleted_clip_with_posts(client):
    d = StateDB(client.db_path)
    try:
        clip_id = _clip(d)
        _post(d, clip_id, "tiktok", "woopsocial")
        d.conn.execute("DELETE FROM clips WHERE id = ?", (clip_id,))
        d.conn.commit()
    finally:
        d.close()

    got = client.get(f"/clips/{clip_id}/posts")

    assert got.status_code == 200
    assert [p["platform"] for p in got.json()["posts"]] == ["tiktok"]


# ---- what the panel makes of the rows (Node) ---------------------------------------


def _panel(tmp_path, body: str, data):
    """Run `body` against ui/src/renderer/src/lib/uploadpost.ts (`m`), with
    `data` the JSON given, and return what it prints as JSON."""
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node isn't available")
    lib = Path(__file__).resolve().parent.parent / "ui" / "src" / "renderer" / "src" / "lib"
    module = tmp_path / "uploadpost.ts"
    module.write_text((lib / "uploadpost.ts").read_text(encoding="utf-8"), encoding="utf-8")
    script = (
        f"const m = await import({json.dumps(module.as_uri())});"
        f"const data = {json.dumps(data)};"
        f"console.log(JSON.stringify((() => {{ {body} }})()));"
    )
    run = subprocess.run([node, "--experimental-strip-types", "--no-warnings", "--input-type=module",
                          "-e", script], capture_output=True, text=True, timeout=120)
    if run.returncode != 0 and "strip-types" in run.stderr:
        pytest.skip("this Node can't run TypeScript directly")
    assert run.returncode == 0, run.stderr
    return json.loads(run.stdout)


def test_the_panel_names_every_state_a_direct_upload_can_be_in(tmp_path):
    states = ["scheduled", "checking", "published", "private", "failed"]
    got = _panel(tmp_path, "return data.map((state) => m.describeState({state, error: ''}))", states)
    assert got == ["Scheduled", "Checking it went live", "Published", "Private", "Failed"]


def test_the_panel_picks_up_only_its_own_providers_request(tmp_path):
    got = _panel(tmp_path, "return data.map((provider) => m.rowProvider({provider}))",
                 ["upload_post", "woopsocial", "youtube", ""])
    assert got == ["uploadpost", "woopsocial", "youtube", ""]


def test_the_panel_polls_only_a_post_that_is_due(tmp_path):
    rows = [
        {"state": "queued", "scheduled_for": ""},
        {"state": "processing", "scheduled_for": "2026-10-01T09:00:00Z"},
        {"state": "queued", "scheduled_for": "2026-10-09T09:00:00Z"},
        {"state": "failed", "scheduled_for": ""},
        {"state": "checking", "scheduled_for": "2026-10-01T09:00:00Z"},
    ]
    now = "Date.parse('2026-10-02T09:00:00Z')"
    got = _panel(tmp_path, f"return data.map((row) => m.stillOnItsWay(row, {now}))", rows)
    assert got == [True, True, False, False, False]


def test_an_answer_from_a_provider_keeps_the_direct_upload_in_the_list(tmp_path):
    shown = [
        {"clip_id": 2, "platform": "youtube", "provider": "youtube", "state": "published"},
        {"clip_id": 1, "platform": "tiktok", "provider": "woopsocial", "state": "failed"},
        {"clip_id": 2, "platform": "instagram", "provider": "upload_post", "state": "published"},
    ]
    fresh = [
        {"clip_id": 2, "platform": "tiktok", "provider": "woopsocial", "state": "queued"},
        # Sent again through the other provider: the row is one per clip and platform.
        {"clip_id": 2, "platform": "instagram", "provider": "woopsocial", "state": "queued"},
    ]
    got = _panel(tmp_path, "return m.mergeRows(data.shown, data.fresh)", {"shown": shown, "fresh": fresh})
    assert [(r["provider"], r["platform"], r["state"]) for r in got] == [
        ("woopsocial", "instagram", "queued"),
        ("woopsocial", "tiktok", "queued"),
        ("youtube", "youtube", "published"),
    ]


def test_a_post_scheduled_days_ahead_does_not_lock_publish(tmp_path):
    """Reopening a clip with a post scheduled for next week lists it but
    leaves Publish free; a send under way, a request this panel sent and a
    post already due still lock it."""
    rows = [
        {"state": "queued", "request_id": "w1", "scheduled_for": "2026-10-09T09:00:00Z"},
        {"state": "queued", "request_id": "mine", "scheduled_for": "2026-10-09T09:00:00Z"},
        {"state": "processing", "request_id": "w2", "scheduled_for": ""},
        {"state": "queued", "request_id": "w3", "scheduled_for": "2026-10-01T09:00:00Z"},
        {"state": "sending", "request_id": "", "scheduled_for": ""},
        {"state": "published", "request_id": "mine", "scheduled_for": ""},
        {"state": "checking", "request_id": "", "scheduled_for": "2026-10-01T09:00:00Z"},
    ]
    now = "Date.parse('2026-10-02T09:00:00Z')"
    got = _panel(tmp_path, f"return data.map((row) => m.holdsPublish(row, new Set(['mine']), {now}))",
                 rows)
    assert got == [False, True, True, True, True, False, False]


def test_the_panel_picks_up_a_request_it_can_retry(tmp_path):
    """Nothing on its way: the request with a failed platform, so "Retry the
    ones that failed" is not sent to one with nothing failed."""
    rows = [
        {"provider": "upload_post", "platform": "instagram", "state": "published", "request_id": "R2",
         "scheduled_for": ""},
        {"provider": "upload_post", "platform": "tiktok", "state": "failed", "request_id": "R1",
         "scheduled_for": ""},
        {"provider": "woopsocial", "platform": "x", "state": "failed", "request_id": "W1",
         "scheduled_for": ""},
    ]
    now = "Date.parse('2026-10-02T09:00:00Z')"
    body = f"return [m.pickRequest(data, 'uploadpost', {now})?.request_id, " \
           f"m.pickRequest(data.slice(0, 1), 'uploadpost', {now})?.request_id, " \
           f"m.pickRequest(data, 'woopsocial', {now})?.request_id]"
    assert _panel(tmp_path, body, rows) == ["R1", "R2", "W1"]
