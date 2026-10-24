"""Re-rendering a clip that has dependent rows.

A re-render does not update a clip in place. It deletes the row and inserts a
new one (server/jobs.py::_rerender_clip), because the new timestamps would
otherwise collide with UNIQUE(video_id, start_s, end_s) — which means the clip
comes back with a NEW id.

foreign_keys is ON, and both clip_translations and uploads reference clips(id).
So before this was fixed, translating a clip in the Subtitles tab and then
pressing "Apply edits" failed the render job with

    sqlite3.IntegrityError: FOREIGN KEY constraint failed

for a reason the user could not have guessed at. Publishing a clip and then
editing it would have done the same thing through uploads.clip_id.

The app holds the id too: the editor open on the clip, and the draft preview
filed under it. Those are at the end.
"""

import json
import sqlite3

import pytest


def _video(db, video_id="v1"):
    db.conn.execute(
        "INSERT INTO videos (video_id, title, status, created_at, updated_at) "
        "VALUES (?, 't', 'done', '2026-01-01', '2026-01-01')",
        (video_id,),
    )
    db.conn.commit()


def _clip(db, video_id="v1", start=1.0, end=5.0):
    db.conn.execute(
        "INSERT INTO clips (video_id, start_s, end_s, score, created_at) "
        "VALUES (?, ?, ?, 90, '2026-01-01')",
        (video_id, start, end),
    )
    db.conn.commit()
    return db.conn.execute(
        "SELECT id FROM clips WHERE video_id = ? AND start_s = ? AND end_s = ?",
        (video_id, start, end),
    ).fetchone()["id"]


def test_the_bug_is_real_without_detaching(db):
    """The guard rail: if this ever passes, the fix below is dead code."""
    _video(db)
    clip_id = _clip(db)
    db.conn.execute(
        "INSERT INTO clip_translations (clip_id, language, updated_at) "
        "VALUES (?, 'es', '2026-01-01')",
        (clip_id,),
    )
    db.conn.commit()

    with pytest.raises(sqlite3.IntegrityError):
        db.conn.execute("DELETE FROM clips WHERE id = ?", (clip_id,))
        db.conn.commit()


def test_translated_clip_survives_a_rerender(db):
    _video(db)
    clip_id = _clip(db)
    db.conn.execute(
        "INSERT INTO clip_translations (clip_id, language, lines, updated_at) "
        "VALUES (?, 'es', '[{\"text\": \"hola\"}]', '2026-01-01')",
        (clip_id,),
    )
    db.conn.commit()

    detached = db.detach_clip_rows(clip_id)
    db.conn.execute("DELETE FROM clips WHERE id = ?", (clip_id,))
    db.conn.commit()

    new_id = _clip(db, start=1.5, end=5.0)
    assert new_id != clip_id, "a re-render is supposed to produce a new id"
    db.reattach_clip_rows(new_id, detached)

    rows = db.conn.execute(
        "SELECT * FROM clip_translations WHERE clip_id = ?", (new_id,)
    ).fetchall()
    assert len(rows) == 1
    assert rows[0]["language"] == "es"
    assert "hola" in rows[0]["lines"], "the translation itself must survive, not just the row"


def test_published_clip_survives_a_rerender(db):
    """The publishing path into the same bug: uploads.clip_id."""
    _video(db)
    clip_id = _clip(db)
    db.conn.execute(
        "INSERT INTO uploads (clip_id, youtube_id, uploaded_at) VALUES (?, 'abc123', '2026-01-01')",
        (clip_id,),
    )
    db.conn.commit()

    detached = db.detach_clip_rows(clip_id)
    db.conn.execute("DELETE FROM clips WHERE id = ?", (clip_id,))
    db.conn.commit()

    new_id = _clip(db, start=2.0, end=6.0)
    db.reattach_clip_rows(new_id, detached)

    row = db.conn.execute("SELECT * FROM uploads WHERE clip_id = ?", (new_id,)).fetchone()
    assert row is not None, "editing a published clip must not lose which video it became"
    assert row["youtube_id"] == "abc123"


def test_detaching_a_clip_with_no_dependents_is_a_no_op(db):
    _video(db)
    clip_id = _clip(db)

    assert db.detach_clip_rows(clip_id) == {}

    db.conn.execute("DELETE FROM clips WHERE id = ?", (clip_id,))
    db.conn.commit()
    db.reattach_clip_rows(_clip(db, start=3.0, end=7.0), {})


def test_reattach_does_not_raise_when_a_row_cannot_be_restored(db):
    """A lost translation must never take the whole render down with it."""
    _video(db)
    clip_id = _clip(db)
    db.conn.execute(
        "INSERT INTO clip_translations (clip_id, language, updated_at) "
        "VALUES (?, 'es', '2026-01-01')",
        (clip_id,),
    )
    db.conn.commit()
    detached = db.detach_clip_rows(clip_id)
    db.conn.execute("DELETE FROM clips WHERE id = ?", (clip_id,))
    db.conn.commit()

    # 999 does not exist, so the foreign key cannot be satisfied.
    db.reattach_clip_rows(999, detached)


def test_a_rerender_that_fails_leaves_the_clip_where_it_was(db, tmp_path, monkeypatch):
    """The row used to be deleted BEFORE the render and put back after it, so
    a render that failed (or an app closed halfway through one) lost the clip
    and its translation for good. The job re-queued at the next start then
    failed with "No clip with id ..." on a clip the person had only edited."""
    pytest.importorskip("numpy")
    pytest.importorskip("cv2")
    from types import SimpleNamespace

    from core import pipeline
    from server.jobs import Worker

    _video(db)
    clip_id = _clip(db)
    db.conn.execute(
        "INSERT INTO clip_translations (clip_id, language, updated_at) VALUES (?, 'es', '2026-01-01')",
        (clip_id,),
    )
    db.conn.commit()
    (tmp_path / "downloads").mkdir()
    (tmp_path / "downloads" / "v1.mp4").write_bytes(b"source")
    (tmp_path / "transcripts").mkdir()
    (tmp_path / "transcripts" / "v1.json").write_text('{"segments": []}', encoding="utf-8")

    def broken(*_a, **_k):
        raise RuntimeError("FFmpeg failed")

    monkeypatch.setattr(pipeline, "_render_files", broken)
    job = SimpleNamespace(config={"paths": {"data_dir": str(tmp_path)}})
    with pytest.raises(RuntimeError):
        Worker._rerender_clip(job, db, {"clip_id": clip_id, "start": 2.0})

    assert db.get_clip(clip_id) is not None
    assert db.conn.execute("SELECT COUNT(*) FROM clip_translations WHERE clip_id = ?",
                           (clip_id,)).fetchone()[0] == 1


# ---- what holds the old id ------------------------------------------------------------


def test_a_rerender_drops_the_old_draft_and_says_where_the_clip_went(db, tmp_path, monkeypatch):
    """#147: the editor had the clip open by its old id, so after Apply it lost
    the clip, and the draft preview of the edits before stayed on disk under
    an id nothing would ask for again. Now the draft goes, and the app is told
    the new id to follow."""
    pytest.importorskip("numpy")
    pytest.importorskip("cv2")
    from types import SimpleNamespace

    from core import pipeline
    from server import jobs

    _video(db)
    clip_id = _clip(db)
    other_id = _clip(db, start=10.0, end=14.0)
    (tmp_path / "downloads").mkdir()
    (tmp_path / "downloads" / "v1.mp4").write_bytes(b"source")
    (tmp_path / "transcripts").mkdir()
    (tmp_path / "transcripts" / "v1.json").write_text('{"segments": []}', encoding="utf-8")
    (tmp_path / "previews").mkdir()
    draft = tmp_path / "previews" / f"clip_{clip_id}.mp4"
    draft.write_bytes(b"draft")
    # The video's other clip has a draft too: only the re-rendered clip's
    # own draft goes.
    others_draft = tmp_path / "previews" / f"clip_{other_id}.mp4"
    others_draft.write_bytes(b"draft")

    def render(_source, _candidate, _segments, clip_dir, _config, render_opts, _lang):
        clip_dir.mkdir(parents=True, exist_ok=True)
        (clip_dir / "clip.mp4").write_bytes(b"clip")
        return clip_dir / "clip.mp4", json.dumps(render_opts)

    monkeypatch.setattr(pipeline, "_render_files", render)
    events = []
    monkeypatch.setattr(jobs.broadcaster, "publish", events.append)
    job = SimpleNamespace(config={"paths": {"data_dir": str(tmp_path)}, "clips": {"outro": False}})

    jobs.Worker._rerender_clip(job, db, {"clip_id": clip_id, "start": 2.0})

    ids = {row["id"] for row in db.conn.execute("SELECT id FROM clips")}
    assert clip_id not in ids and other_id in ids
    (new_id,) = ids - {other_id}
    assert not draft.exists()
    assert others_draft.exists()
    assert [e for e in events if e["type"] == "clip_replaced"] == [
        {"type": "clip_replaced", "old": clip_id, "new": new_id, "video_id": "v1"}
    ]


@pytest.fixture
def api(db, tmp_path):
    """The API over the same data folder as `db`, with no worker running."""
    pytest.importorskip("fastapi")
    pytest.importorskip("httpx")
    pytest.importorskip("yaml")
    pytest.importorskip("numpy")
    pytest.importorskip("yt_dlp")  # sources.dispatch imports the YouTube source
    import shutil

    from fastapi.testclient import TestClient

    from main import BUNDLED_CONFIG, load_config
    from server.api import create_app

    settings = tmp_path / "settings.yaml"
    shutil.copy(BUNDLED_CONFIG, settings)
    config = load_config(BUNDLED_CONFIG)
    config["paths"]["data_dir"] = str(tmp_path)
    # No `with`: startup never runs, so no worker thread starts.
    return TestClient(create_app(config, settings), base_url="http://127.0.0.1")


def test_the_editor_is_never_shown_a_video_from_the_cache(api, db, tmp_path):
    """A clip's file and its draft are both written again in place, so a
    cached copy is one from before (#147)."""
    _video(db)
    clip_id = _clip(db)
    (tmp_path / "clips").mkdir()
    (tmp_path / "clips" / "clip.mp4").write_bytes(b"clip")
    db.conn.execute("UPDATE clips SET path = ? WHERE id = ?", (str(tmp_path / "clips" / "clip.mp4"), clip_id))
    db.conn.commit()
    (tmp_path / "previews").mkdir()
    (tmp_path / "previews" / f"clip_{clip_id}.mp4").write_bytes(b"draft")

    for route in (f"/media/{clip_id}", f"/media/preview/{clip_id}"):
        r = api.get(route)
        assert r.status_code == 200, route
        assert r.headers["cache-control"] == "no-store", route


# ---- posts on other platforms (clip_publishes) ------------------------------------
#
# Not a foreign key, so these were never in the way of the delete. They just
# stayed on the old id, and "Where it went" lost the clip's posts the moment
# its edits were applied.


def _sent(db, clip_id, platform="tiktok", *, provider="woopsocial", caption="As sent",
          scheduled="2026-10-02T09:00:00Z"):
    """A post as the publisher writes it at publish time."""
    from publish.uploadpost import FanOutResult, PlatformOutcome
    from server import uploadpost_service, woopsocial_service

    service = woopsocial_service if provider == "woopsocial" else uploadpost_service
    service.record_outcomes(
        db, clip_id, db.get_clip(clip_id),
        FanOutResult(request_id="req-1", outcomes=[PlatformOutcome(platform=platform)]),
        scheduled_for=scheduled, caption=caption, visibility="default",
    )


def _learned(platform="tiktok", url="https://www.tiktok.com/@someone/video/7300000000000000001"):
    from publish.uploadpost import FanOutResult, PlatformOutcome

    return FanOutResult(request_id="req-1", outcomes=[
        PlatformOutcome(platform=platform, state="published", post_id="73", post_url=url),
    ])


def _publishes(db):
    return [dict(r) for r in db.conn.execute("SELECT * FROM clip_publishes ORDER BY platform")]


def test_posts_follow_the_clip_to_its_new_id(db):
    _video(db)
    clip_id = _clip(db)
    _sent(db, clip_id, "tiktok")
    _sent(db, clip_id, "instagram")
    db.conn.execute("DELETE FROM clips WHERE id = ?", (clip_id,))
    db.conn.commit()
    new_id = _clip(db, start=1.5, end=5.0)

    db.follow_clip_id(clip_id, new_id)

    rows = _publishes(db)
    assert {r["clip_id"] for r in rows} == {new_id}
    # The post that went out covered the old window, and still does.
    assert {(r["video_id"], r["start_s"], r["end_s"]) for r in rows} == {("v1", 1.0, 5.0)}


def test_a_platform_the_new_id_already_has_keeps_its_own_row(db):
    _video(db)
    old_id = _clip(db)
    new_id = _clip(db, start=10.0, end=14.0)
    _sent(db, old_id, "tiktok", caption="old post")
    _sent(db, new_id, "tiktok", caption="new post")

    db.follow_clip_id(old_id, new_id)

    rows = {(r["clip_id"], r["caption"]) for r in _publishes(db)}
    assert rows == {(old_id, "old post"), (new_id, "new post")}


@pytest.mark.parametrize("provider", ["woopsocial", "upload_post"])
def test_a_refresh_after_a_rerender_keeps_the_posts_window_and_caption(db, provider):
    """The refresh looks the clip up by the row's id, which is now the
    re-rendered clip with another window. It must not write that window, or
    the caption, over the post's own."""
    from server import uploadpost_service, woopsocial_service

    service = woopsocial_service if provider == "woopsocial" else uploadpost_service
    _video(db)
    clip_id = _clip(db)
    _sent(db, clip_id, provider=provider)
    db.conn.execute("DELETE FROM clips WHERE id = ?", (clip_id,))
    db.conn.commit()
    new_id = _clip(db, start=1.5, end=4.0)
    db.follow_clip_id(clip_id, new_id)

    service.record_refresh(db, new_id, _learned())

    (row,) = _publishes(db)
    assert (row["clip_id"], row["state"]) == (new_id, "published")
    assert row["post_url"].endswith("/video/7300000000000000001")
    assert (row["video_id"], row["start_s"], row["end_s"]) == ("v1", 1.0, 5.0)
    assert row["caption"] == "As sent"
    assert row["scheduled_for"] == "2026-10-02T09:00:00Z"
    assert row["provider"] == provider


@pytest.mark.parametrize("provider", ["woopsocial", "upload_post"])
def test_refreshing_a_deleted_clips_post_keeps_its_identity(db, provider):
    from server import uploadpost_service, woopsocial_service

    service = woopsocial_service if provider == "woopsocial" else uploadpost_service
    _video(db)
    clip_id = _clip(db)
    _sent(db, clip_id, provider=provider)
    db.delete_clip(clip_id)

    service.record_refresh(db, clip_id, _learned())
    # Nor does a publish-time write with the clip row gone: identity is
    # written only while the row exists (it used to be blanked to '', 0, 0).
    from publish.uploadpost import FanOutResult, PlatformOutcome

    service.record_outcomes(
        db, clip_id, None,
        FanOutResult(request_id="req-1", outcomes=[PlatformOutcome(platform="tiktok", state="published")]),
    )

    (row,) = _publishes(db)
    assert (row["video_id"], row["start_s"], row["end_s"]) == ("v1", 1.0, 5.0)


def test_deleting_a_clip_keeps_its_posts(db):
    """The post is still out there after the clip is culled from the library."""
    _video(db)
    clip_id = _clip(db)
    _sent(db, clip_id)
    db.delete_clip(clip_id)
    assert [r["clip_id"] for r in _publishes(db)] == [clip_id]


def test_a_rerender_carries_the_clips_posts(db, tmp_path, monkeypatch):
    pytest.importorskip("numpy")
    pytest.importorskip("cv2")
    from types import SimpleNamespace

    from core import pipeline
    from server import jobs

    _video(db)
    clip_id = _clip(db)
    _sent(db, clip_id)
    (tmp_path / "downloads").mkdir()
    (tmp_path / "downloads" / "v1.mp4").write_bytes(b"source")
    (tmp_path / "transcripts").mkdir()
    (tmp_path / "transcripts" / "v1.json").write_text('{"segments": []}', encoding="utf-8")

    def render(_source, _candidate, _segments, clip_dir, _config, render_opts, _lang):
        clip_dir.mkdir(parents=True, exist_ok=True)
        (clip_dir / "clip.mp4").write_bytes(b"clip")
        return clip_dir / "clip.mp4", json.dumps(render_opts)

    monkeypatch.setattr(pipeline, "_render_files", render)
    monkeypatch.setattr(jobs.broadcaster, "publish", lambda _event: None)
    job = SimpleNamespace(config={"paths": {"data_dir": str(tmp_path)}, "clips": {"outro": False}})

    jobs.Worker._rerender_clip(job, db, {"clip_id": clip_id, "start": 2.0})

    (new_id,) = {row["id"] for row in db.conn.execute("SELECT id FROM clips")}
    (row,) = _publishes(db)
    assert row["clip_id"] == new_id != clip_id
    assert (row["start_s"], row["end_s"], row["caption"]) == (1.0, 5.0, "As sent")
