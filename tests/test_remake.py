"""Make clips again (#190, core/remake.py): which clips a remake keeps, and
which it replaces, and only after a run that made clips.

"Process again" adds a forced run's clips beside the ones a video already
has. Make clips again runs the same forced job with new settings and then
replaces the old 9:16 clips nobody used. Anything a person or a feature did
with a clip keeps it, and every Longform output and story reel stays.

These run on CI's light install: the clips here have no render options for
core.pipeline._edited (D33) to read, except where a test asks for the engine.
"""

import json
import sqlite3
import sys
import threading
import time

import pytest

from core import in_use, remake
from core.state import UNMARKED_CLIPS, StateDB, _now

VID = "abcdefghijk"


@pytest.fixture
def lib(tmp_path):
    """A data folder with one processed video and a helper that adds clips,
    each with a file and an editor draft of its own."""
    data = tmp_path / "data"
    db = StateDB(data / "state.db")
    db.upsert_video(VID, title="A stream")
    db.set_video_status(VID, "done")
    folder = data / "clips" / "someone" / f"A stream [{VID}]"
    folder.mkdir(parents=True)
    (data / "previews").mkdir()

    def clip(start, end, video_id=VID, render_opts="", file=None, title="t", **fields):
        path = folder / (file or f"clip_{int(start):05d}-{int(end):05d}.mp4")
        path.write_bytes(b"x" * 1000)
        clip_id = db.add_clip(video_id, start, end, 70, "hook", path=str(path), status="queued",
                              title=title, render_opts=render_opts)
        if fields:
            db.set_clip(clip_id, **fields)
        (data / "previews" / f"clip_{clip_id}.mp4").write_bytes(b"draft")
        return clip_id

    yield db, data, clip
    db.conn.close()


def _judged(db, busy=()):
    return {c["id"]: c for c in remake.classify(db, VID, busy)}


def _reasons(db, clip_id, busy=()):
    return _judged(db, busy)[clip_id]["reasons"]


# ---- what keeps a clip ------------------------------------------------------------


def test_a_clip_nobody_used_is_replaced(lib):
    db, _, clip = lib
    clip_id = clip(10.0, 40.0)
    judged = _judged(db)[clip_id]
    assert judged["replace"] is True and judged["reasons"] == []


@pytest.mark.parametrize("fields, reason", [
    ({"edited_at": "2026-11-06T10:00:00"}, "edited"),
    ({"scores": json.dumps({"plugin_edits": [{"id": "a1", "state": "hidden"}]})}, "edited"),
    ({"status": "uploaded"}, "published"),
    ({"status": "failed"}, "published"),
    ({"status": "scheduled", "scheduled_for": "2026-11-07"}, "scheduled"),
    ({"scheduled_for": "2026-11-07"}, "scheduled"),
    ({"exported_at": "2026-11-06T10:00:00"}, "exported"),
    ({"first_exported_at": "2026-11-06T10:00:00"}, "exported"),
    ({"status": "something_new"}, "unsure"),
    ({"render_opts": "{not json"}, "unsure"),
])
def test_a_mark_on_the_clip_keeps_it(lib, fields, reason):
    db, _, clip = lib
    clip_id = clip(10.0, 40.0, **fields)
    assert _judged(db)[clip_id]["replace"] is False
    assert reason in _reasons(db, clip_id)


def test_a_suggestion_nobody_decided_about_keeps_nothing(lib):
    db, _, clip = lib
    clip_id = clip(10.0, 40.0, scores=json.dumps({"plugin_edits": [{"id": "a1", "state": "new"}]}))
    assert _judged(db)[clip_id]["replace"] is True


@pytest.mark.parametrize("action, reason", [
    ("exported", "exported"), ("captions_edited", "edited"),
    ("timestamps_adjusted", "edited"), ("rerendered", "edited"), ("anything_else", "edited"),
])
def test_a_feedback_row_keeps_it(lib, action, reason):
    db, _, clip = lib
    clip_id = clip(10.0, 40.0)
    db.conn.execute("INSERT INTO clip_feedback (clip_id, action, created_at) VALUES (?, ?, ?)",
                    (clip_id, action, _now()))
    db.conn.commit()
    assert _reasons(db, clip_id) == [reason]


def _insert(db, table, row):
    db.conn.execute(f"INSERT INTO {table} ({', '.join(row)}) VALUES ({', '.join('?' * len(row))})",
                    tuple(row.values()))
    db.conn.commit()


@pytest.mark.parametrize("by_window", [False, True])
@pytest.mark.parametrize("table, row, reason", [
    ("uploads", {"youtube_id": "yt1", "uploaded_at": "2026-11-01"}, "published"),
    ("clip_publishes", {"platform": "tiktok", "state": "queued", "created_at": "x", "updated_at": "x"},
     "published"),
    ("publish_jobs", {"status": "failed", "created_at": "x", "updated_at": "x"}, "published"),
    ("campaign_submissions", {"campaign_id": 1, "platform": "tiktok", "created_at": "x", "updated_at": "x"},
     "submitted"),
])
def test_a_post_upload_or_submission_keeps_it(lib, table, row, reason, by_window):
    """Found by the clip's id, or by its window: a post keeps the window of the
    clip it was made from, and a re-render may have moved the id."""
    db, _, clip = lib
    clip_id = clip(10.0, 40.0)
    other = clip(50.0, 80.0)
    if by_window:
        row = {**row, "clip_id": 99999 if table != "uploads" else 99998, "video_id": VID,
               "start_s": 10.0, "end_s": 40.0}
        if table == "uploads":
            db.conn.execute("PRAGMA foreign_keys=OFF")
    else:
        row = {**row, "clip_id": clip_id}
        if table in ("clip_publishes", "publish_jobs", "campaign_submissions"):
            row = {**row, "video_id": VID, "start_s": 10.0, "end_s": 40.0}
            if table == "campaign_submissions":
                row["start_s"] = 1.0   # found by its id alone
    _insert(db, table, row)
    db.conn.execute("PRAGMA foreign_keys=ON")
    assert _reasons(db, clip_id) == [reason]
    assert _judged(db)[other]["replace"] is True


def test_a_translation_keeps_it(lib):
    db, _, clip = lib
    clip_id = clip(10.0, 40.0)
    db.save_translation(clip_id, "es", "[]")
    assert _reasons(db, clip_id) == ["translated"]


def test_a_watched_channels_chosen_window_keeps_it(lib):
    db, _, clip = lib
    clip_id = clip(10.004, 40.0)
    db.insert_watch_item(VID, watch_id=1, chosen_clips=json.dumps([[10.0, 40.0], [99.0, 120.0]]))
    assert _reasons(db, clip_id) == ["chosen"]


@pytest.mark.parametrize("by_window", [False, True])
def test_a_publish_on_its_way_to_discord_keeps_it(lib, by_window):
    """A publish that sends its link to Discord saves that intent before it
    uploads (campaigns/post_links.py prepare); the submission row comes once
    the post is live. Found by the clip's id, or by its window."""
    db, _, clip = lib
    clip_id = clip(10.0, 40.0)
    other = clip(50.0, 80.0)
    intent = {"destination_id": 3, "clip_id": 99999 if by_window else clip_id, "provider": "woopsocial",
              "title": "", "video_id": VID, "start_s": 10.0 if by_window else 1.0, "end_s": 40.0,
              "before": {"tiktok": None}}
    db.set_flag(f"{remake._POST_LINK}3:woopsocial:{intent['clip_id']}", json.dumps(intent))
    # Another video's clip at the same seconds, and an intent that can't be read.
    theirs = {**intent, "video_id": "other", "clip_id": 88888, "start_s": 50.0, "end_s": 80.0}
    db.set_flag(f"{remake._POST_LINK}4:woopsocial:88888", json.dumps(theirs))
    db.set_flag(f"{remake._POST_LINK}5:woopsocial:1", "{not json")
    assert _reasons(db, clip_id) == ["submitted"]
    assert _judged(db)[other]["replace"] is True


def test_the_discord_intent_key_is_post_links_own():
    from campaigns import post_links

    assert remake._POST_LINK == post_links.PENDING_PREFIX


def test_a_render_translation_or_export_on_its_way_keeps_it(lib):
    db, _, clip = lib
    rendering, translating, exporting, idle = clip(0.0, 9.0), clip(10.0, 19.0), clip(20.0, 29.0), clip(30.0, 39.0)
    db.add_job("render", json.dumps({"clip_id": rendering, "start": 1.0}))
    db.add_job("translate", json.dumps({"clip_ids": [translating], "languages": ["es"]}))
    finished = db.add_job("translate", json.dumps({"clip_ids": [idle], "languages": ["es"], "stage": "translate"}))
    db.finish_job(finished, "done")
    judged = _judged(db, busy={exporting})
    assert judged[rendering]["reasons"] == ["edited", "busy"]    # a render a person asked for
    assert judged[translating]["reasons"] == ["busy"]
    assert judged[exporting]["reasons"] == ["busy"]
    assert judged[idle]["replace"] is True    # a translation that ran saves rows of its own


@pytest.mark.parametrize("status", ["queued", "running", "done", "failed", "cancelled"])
def test_a_render_job_that_names_it_keeps_it_in_any_state(lib, status):
    """A render only a person starts (QC fix round 6): one that failed or was
    cancelled holds their edit, for Retry, and its learning row may be erased
    since. As long as the Queue page keeps the job."""
    db, _, clip = lib
    clip_id, other = clip(10.0, 40.0), clip(50.0, 80.0)
    job = db.add_job("render", json.dumps({"clip_id": clip_id, "render_opts": {"edit": {"keep": [[0.0, 5.0]]}}}))
    if status == "running":
        db.set_job(job, status="running")
    elif status != "queued":
        db.finish_job(job, status)
    assert _reasons(db, clip_id)[0] == "edited"
    assert _judged(db)[other]["replace"] is True


def test_marked_edited_and_exported_by_the_routes_marks(lib):
    """StateDB.mark_edited (a render queued) and mark_exported (a real export):
    the star and erasing the learning leave both."""
    db, _, clip = lib
    edited, exported, starred = clip(10.0, 40.0), clip(50.0, 80.0), clip(90.0, 120.0)
    db.mark_edited(edited)
    db.mark_exported(exported)
    first = db.get_clip(exported)["first_exported_at"]
    assert first and db.get_clip(exported)["exported_at"]
    db.set_clip(exported, exported_at="")             # the star taken off
    db.set_clip(starred, exported_at=_now())          # a star by hand, taken off again
    db.set_clip(starred, exported_at="")
    db.conn.execute("DELETE FROM clip_feedback")       # the learning erased
    db.conn.commit()
    db.mark_exported(exported)                         # exported again: the first time stays
    assert db.get_clip(exported)["first_exported_at"] == first
    db.set_clip(exported, exported_at="")
    judged = _judged(db)
    assert judged[edited]["reasons"] == ["edited"]
    assert judged[exported]["reasons"] == ["exported"]
    assert judged[starred]["replace"] is True


def test_longform_outputs_and_story_reels_are_never_replaced(lib):
    db, _, clip = lib
    longform = clip(10.0, 140.0, render_opts=json.dumps({"profile": "short_clips"}))
    reel = clip(0.0, 95.0, render_opts=json.dumps({"reel": "recap"}))
    judged = _judged(db)
    assert judged[longform]["reasons"] == ["longform"]
    assert judged[reel]["reasons"] == ["story_reel"]


def test_reasons_come_in_order_and_a_clip_counts_once(lib):
    db, _, clip = lib
    clip(0.0, 9.0)
    clip(10.0, 19.0, edited_at=_now(), exported_at=_now(), status="uploaded")
    clip(20.0, 29.0, exported_at=_now())
    clip(30.0, 39.0, render_opts=json.dumps({"profile": "clips_140"}))
    judged = remake.classify(db, VID)
    assert [c["reasons"] for c in judged] == [[], ["edited", "published", "exported"], ["exported"], ["longform"]]
    assert remake.counts(judged) == {"total": 4, "replaced": 1, "kept": 3,
                                     "kept_because": {"edited": 1, "exported": 1, "longform": 1}}


def test_another_videos_marks_say_nothing_about_this_one(lib):
    db, _, clip = lib
    db.upsert_video("other", title="Other")
    mine = clip(10.0, 40.0)
    theirs = clip(10.0, 40.0, video_id="other", file="other.mp4")
    db.record_upload(theirs, "yt9")
    db.conn.execute("UPDATE clips SET status = 'queued' WHERE id = ?", (theirs,))
    db.conn.commit()
    assert _judged(db)[mine]["replace"] is True


def test_old_database_gets_edited_marks_from_its_edit_history(tmp_path):
    """Re-renders a person asked for were only logged as learning rows before
    edited_at existed: carried over once, so wiping a creator's learning
    later can't make such a clip read as one nobody changed."""
    if sqlite3.sqlite_version_info < (3, 35, 0):
        pytest.skip("needs DROP COLUMN to build a pre-migration database")
    path = tmp_path / "state.db"
    db = StateDB(path)
    db.upsert_video(VID, title="A stream")
    recaptioned, exported, plain = (db.add_clip(VID, s, s + 30.0, 70, "h") for s in (0.0, 40.0, 80.0))
    db.conn.executemany("INSERT INTO clip_feedback (clip_id, action, created_at) VALUES (?, ?, ?)", [
        (recaptioned, "rerendered", "2026-08-01T10:00:00"),
        (recaptioned, "captions_edited", "2026-09-01T10:00:00"),   # the latest wins
        (exported, "exported", "2026-09-02T10:00:00"),             # an export isn't an edit
    ])
    db.conn.commit()
    db.conn.close()

    raw = sqlite3.connect(path)
    raw.execute("ALTER TABLE clips DROP COLUMN edited_at")
    raw.execute("ALTER TABLE clips DROP COLUMN as_made")
    raw.commit()
    raw.close()

    db = StateDB(path)
    rows = {r["id"]: r["edited_at"] for r in db.conn.execute("SELECT id, edited_at FROM clips")}
    db.conn.execute("DELETE FROM clip_feedback")                    # the learning wiped
    db.conn.commit()
    judged = {c["id"]: c["reasons"] for c in remake.classify(db, VID)}
    db.conn.close()
    assert rows == {recaptioned: "2026-09-01T10:00:00", exported: "", plain: ""}
    # All three were made before edits were marked, so all three are kept
    # anyway (test_clips_made_before_edits_were_marked_are_kept).
    assert judged == {recaptioned: ["edited", "older"], exported: ["older"], plain: ["older"]}


def test_old_database_gets_lasting_export_marks_from_its_history(tmp_path):
    """Exports were only logged as learning rows (and exported_at, which the
    star clears), and exports in other languages only as their translate
    job's row, which the Queue page's Clear deletes: each gets its lasting
    mark once, as first_exported_at is added (QC fix round 6)."""
    if sqlite3.sqlite_version_info < (3, 35, 0):
        pytest.skip("needs DROP COLUMN to build a pre-migration database")
    path = tmp_path / "state.db"
    db = StateDB(path)
    db.upsert_video(VID, title="A stream")
    copied, languages, reviewed, waiting, plain = (db.add_clip(VID, s, s + 30.0, 70, "h")
                                                   for s in (0.0, 40.0, 80.0, 120.0, 160.0))
    db.conn.executemany("INSERT INTO clip_feedback (clip_id, action, created_at) VALUES (?, ?, ?)", [
        (copied, "exported", "2026-09-01T10:00:00"),          # the earliest is the first export
        (copied, "exported", "2026-10-01T10:00:00"),
        (plain, "captions_edited", "2026-09-02T10:00:00"),    # not an export
    ])
    db.conn.commit()
    ran = db.add_job("translate", json.dumps({"clip_ids": [languages, 99999], "languages": ["es", "de"],
                                              "stage": "export", "folder": "x"}))
    db.finish_job(ran, "failed")                               # stopped partway: some files may be there
    review = db.add_job("translate", json.dumps({"clip_ids": [reviewed], "languages": ["es"],
                                                 "stage": "translate"}))
    db.finish_job(review, "done")                              # text for review only
    db.add_job("translate", json.dumps({"clip_ids": [waiting], "languages": ["es"]}))   # hasn't run
    db.conn.close()

    raw = sqlite3.connect(path)                                # as the app left it before step 2
    for column in ("edited_at", "as_made", "first_exported_at"):
        raw.execute(f"ALTER TABLE clips DROP COLUMN {column}")
    raw.execute("DELETE FROM app_state WHERE key = ?", (UNMARKED_CLIPS,))
    raw.commit()
    raw.close()

    db = StateDB(path)
    first = {r["id"]: r["first_exported_at"] for r in db.conn.execute("SELECT id, first_exported_at FROM clips")}
    marks = {r["key"]: json.loads(r["value"]) for r in db.conn.execute(
        "SELECT key, value FROM app_state WHERE key GLOB 'clip_language_export:*'")}
    db.conn.execute("DELETE FROM clip_feedback")               # the learning erased
    db.conn.execute("DELETE FROM jobs")                        # the Queue page cleared
    db.conn.commit()
    judged = {c["id"]: c["reasons"] for c in remake.classify(db, VID)}
    StateDB(path).conn.close()                                 # opened again: nothing is copied twice
    again = {r["key"] for r in db.conn.execute("SELECT key FROM app_state WHERE key GLOB 'clip_language_export:*'")}
    db.conn.close()
    assert first == {copied: "2026-09-01T10:00:00", languages: "", reviewed: "", waiting: "", plain: ""}
    assert list(marks) == [f"clip_language_export:{languages}"]
    assert marks[f"clip_language_export:{languages}"]["languages"] == ["de", "es"]
    assert marks[f"clip_language_export:{languages}"]["video_id"] == VID
    assert again == set(marks)
    # All were made before edits were marked, so `older` keeps them anyway;
    # the exports are kept as exports.
    assert judged == {copied: ["exported", "older"], languages: ["exported", "older"],
                      reviewed: ["older"], waiting: ["older"], plain: ["edited", "older"]}


def test_by_hand_render_options_keep_it(lib):
    """The keys only a person sets on one clip (core.pipeline._BY_HAND, D33),
    read through the engine itself."""
    pytest.importorskip("numpy")
    pytest.importorskip("cv2")
    pytest.importorskip("PIL")
    from core import pipeline

    db, _, clip = lib
    ids = {key: clip(i * 10.0, i * 10.0 + 9.0, render_opts=json.dumps({key: None}))
           for i, key in enumerate(pipeline._BY_HAND)}
    split = clip(500.0, 509.0, render_opts=json.dumps({"gaming": {"by": "clip", "preset": "split"}}))
    plain = clip(600.0, 609.0, render_opts=json.dumps({"job_captions": True, "caption_style": {"font": "Arial"},
                                                         "gaming": {"by": "video"}}))
    judged = _judged(db)
    for key, clip_id in ids.items():
        assert judged[clip_id]["reasons"] == ["edited"], key
    assert judged[split]["reasons"] == ["edited"]
    assert judged[plain]["replace"] is True    # what every run writes says nothing


def test_a_thumbnail_a_person_chose_keeps_it(lib):
    """POST /clips/{id}/thumbnail keeps a person's choice (their own image, a
    frame or a generated one) only as thumbnails/clip_<id>_chosen.jpg, which
    both publish dialogs send. The frames and candidates the picker shows
    are only offered, not chosen."""
    db, data, clip = lib
    chosen, offered, plain = clip(10.0, 40.0), clip(50.0, 80.0), clip(90.0, 120.0)
    (data / "thumbnails").mkdir()
    (data / "thumbnails" / f"clip_{chosen}_chosen.jpg").write_bytes(b"jpg")
    for name in (f"clip_{offered}_gen0.jpg", f"clip_{offered}_2500.jpg"):
        (data / "thumbnails" / name).write_bytes(b"jpg")
    judged = {c["id"]: c for c in remake.classify(db, VID, data_dir=data)}
    assert judged[chosen]["reasons"] == ["edited"]
    assert judged[offered]["replace"] is True and judged[plain]["replace"] is True
    run = remake.Run(db, VID, data)
    _make(run, 500.0, 530.0)
    assert run.finish()["removed"] == sorted([offered, plain])
    assert db.get_clip(chosen) is not None


# ---- clips made before edits were marked ----------------------------------------------


def _before_marks(path, setup):
    """A database as the app left it before clips.edited_at and clips.as_made
    existed: built, given its clips by `setup`, then without either column
    (and the mark of where edit marks start), so opening it again runs the
    migration."""
    if sqlite3.sqlite_version_info < (3, 35, 0):
        pytest.skip("needs DROP COLUMN to build a pre-migration database")
    db = StateDB(path)
    db.upsert_video(VID, title="A stream")
    db.set_video_status(VID, "done")
    made = setup(db)
    db.conn.execute("DELETE FROM app_state WHERE key = ?", (UNMARKED_CLIPS,))
    db.conn.commit()
    db.conn.close()
    raw = sqlite3.connect(path)
    raw.execute("ALTER TABLE clips DROP COLUMN edited_at")
    raw.execute("ALTER TABLE clips DROP COLUMN as_made")
    raw.commit()
    raw.close()
    return made


def test_a_new_library_has_no_clips_from_before(tmp_path):
    db = StateDB(tmp_path / "state.db")
    try:
        assert db.get_flag(UNMARKED_CLIPS) == "0"
    finally:
        db.conn.close()


def test_clips_made_before_edits_were_marked_are_kept(tmp_path, capsys):
    """A title, description or hashtags changed before this version left no
    mark (the old PATCH /clips/{id} wrote those columns and nothing else),
    and neither did a start or end changed by hand whose learning rows were
    wiped since. Such a clip can't be told from one nobody touched, so every
    clip from before is kept: when in doubt, keep the clip."""
    path = tmp_path / "data" / "state.db"

    def setup(db):
        retitled = db.add_clip(VID, 10.0, 40.0, 70, "h", status="queued", title="Model title")
        db.set_clip(retitled, title="My own title", description="My words", hashtags=json.dumps(["mine"]))
        trimmed = db.add_clip(VID, 52.5, 78.0, 70, "h", status="queued", title="t")
        db.conn.execute("INSERT INTO clip_feedback (creator_id, clip_id, action, created_at) "
                        "VALUES (1, ?, 'timestamps_adjusted', ?)", (trimmed, _now()))
        db.conn.execute("DELETE FROM clip_feedback WHERE creator_id = 1")   # what was learned, forgotten
        db.conn.commit()
        untouched = db.add_clip(VID, 100.0, 130.0, 70, "h", status="queued", title="Model title")
        return retitled, trimmed, untouched

    old = _before_marks(path, setup)
    db = StateDB(path)                  # the update opens it: the migration runs
    try:
        assert db.get_flag(UNMARKED_CLIPS) == str(max(old))
        judged = {c["id"]: c for c in remake.classify(db, VID)}
        assert [judged[c]["reasons"] for c in old] == [["older"]] * 3
        assert remake.counts(list(judged.values()))["kept_because"] == {"older": 3}
        run = remake.Run(db, VID, tmp_path / "data")
        _make(run, 500.0, 530.0)
        out = run.finish()
        assert all(db.get_clip(c) is not None for c in old)
        assert out["removed"] == [] and out["kept_because"] == {"older": 3}
        assert "Kept 3: 3 made before this update or by another version." in capsys.readouterr().out
        # Made after the update, a clip nobody used is replaced as usual.
        later = db.add_clip(VID, 600.0, 630.0, 70, "h", status="queued")
        assert {c["id"]: c for c in remake.classify(db, VID)}[later]["replace"] is True
    finally:
        db.conn.close()


def test_a_clip_from_before_that_the_run_makes_again_is_made_again_in_place(tmp_path, capsys):
    """Its row is the run's (core/pipeline.py _register_clip updates the same
    window), so it takes what the run made its new file with, as an unused
    clip does. Its title, description and hashtags, the only things a person
    could have changed with no mark, stay.

    It is counted kept, as the dialog counted it (made before this update),
    not replaced as a clip nobody used is: the job log and what the job
    says it did agree with the dialog (black-box S3)."""
    _engine()
    path = tmp_path / "data" / "state.db"
    old_opts = {"job_captions": True}

    def setup(db):
        clip_id = db.add_clip(VID, 100.0, 130.0, 60, "old hook", status="queued", title="Model title",
                              render_opts=json.dumps(old_opts))
        db.set_clip(clip_id, title="My own title")
        return clip_id

    again = _before_marks(path, setup)
    db = StateDB(path)
    try:
        run = remake.Run(db, VID, tmp_path / "data")
        new_opts = {"job_captions": False}
        run.db.add_clip(VID, 100.0, 130.0, 95, "new hook", path=_rendered(run, 100.0, 130.0), status="queued",
                        title="new title", render_opts=json.dumps(new_opts))
        out = run.finish()
        row = db.get_clip(again)
        assert json.loads(row["render_opts"]) == new_opts
        assert (row["score"], row["hook"], row["title"]) == (95, "new hook", "My own title")
        assert out["removed"] == [] and out["replaced"] == 0 and out["kept"] == 1
        assert out["kept_because"] == {"older": 1}
        assert (out["made"], out["new"], out["found_again"], out["again"], out["unchanged"]) == (1, 0, 1, 1, 0)
        said = capsys.readouterr().out
        assert ("Make clips again: this run made 1 clip(s): 0 new, 1 at the same start and end as a clip from "
                "before. Replaced 0 clip(s) from before that nobody had used (0 removed, 0 made again with the "
                "new settings). Kept 1: 1 made before this update or by another version." in said)
        assert ("Make clips again: 1 of the kept clip(s), made before this update, were found again at the same "
                "start and end, so their video was made again with the new settings; their title, description "
                "and hashtags stay" in said)
        assert remake.summary(out) == {"made": 1, "new": 0, "again": 1, "replaced": 0, "kept": 1,
                                       "unchanged": 0, "removed": 0}
    finally:
        db.conn.close()


def test_a_clip_from_before_with_another_reason_is_kept_as_it_is(tmp_path):
    """Made again in place only when being from before is its one reason."""
    _engine()
    path = tmp_path / "data" / "state.db"

    def setup(db):
        clip_id = db.add_clip(VID, 100.0, 130.0, 60, "old hook", status="queued",
                              render_opts=json.dumps({"job_captions": True}))
        db.record_upload(clip_id, "yt1")
        db.set_clip(clip_id, status="queued")
        return clip_id

    published = _before_marks(path, setup)
    db = StateDB(path)
    try:
        run = remake.Run(db, VID, tmp_path / "data")
        run.db.add_clip(VID, 100.0, 130.0, 95, "new hook", render_opts=json.dumps({"job_captions": False}))
        out = run.finish()
        assert json.loads(db.get_clip(published)["render_opts"]) == {"job_captions": True}
        assert out["kept_because"] == {"published": 1}
    finally:
        db.conn.close()


def test_the_column_without_where_its_marks_start_counts_every_clip_so_far(tmp_path):
    """edited_at there but the mark of where edit marks start missing (the
    app stopped between the two): every clip there is counted as from
    before, since nothing says otherwise."""
    path = tmp_path / "state.db"
    db = StateDB(path)
    db.upsert_video(VID, title="A stream")
    ids = [db.add_clip(VID, s, s + 30.0, 70, "h") for s in (0.0, 40.0)]
    db.conn.execute("DELETE FROM app_state WHERE key = ?", (UNMARKED_CLIPS,))
    db.conn.commit()
    db.conn.close()
    db = StateDB(path)
    try:
        assert db.get_flag(UNMARKED_CLIPS) == str(max(ids))
        assert [c["reasons"] for c in remake.classify(db, VID)] == [["older"], ["older"]]
    finally:
        db.conn.close()


# ---- what a run made, and what changed since, in any version ------------------------------


def test_a_clips_fingerprint_is_its_words_and_window_as_its_run_made_them():
    from core.state import as_made

    made = as_made("A title", "Its words", json.dumps(["#a", "#b"]), 10.004, 40.0)
    assert made == as_made("A title", "Its words", '["#a","#b"]', 10.0, 40.0)   # however the list is written
    assert as_made("", None, "", 1.0, 2.0) == as_made(None, "", "[]", 1.0, 2.0)  # nothing is nothing
    for changed in (("Another title", "Its words", '["#a", "#b"]', 10.0, 40.0),
                    ("A title", "Other words", '["#a", "#b"]', 10.0, 40.0),
                    ("A title", "Its words", '["#b", "#a"]', 10.0, 40.0),
                    ("A title", "Its words", '["#a", "#b"]', 10.5, 40.0),
                    ("A title", "Its words", '["#a", "#b"]', 10.0, 40.5)):
        assert as_made(*changed) != made, changed


def test_words_or_a_window_changed_after_the_run_keep_a_clip_whatever_version_changed_them(lib):
    """add_clip saves the fingerprint of what the run gave the clip. A change
    that left no other mark (the released app's PATCH /clips/{id} wrote only
    the new words; a start or end moved in place) keeps the clip as edited.
    The same words saved again change nothing."""
    db, _, clip = lib
    words = {"title": "Their title", "description": "Their words", "hashtags": json.dumps(["#mine"])}
    ids = {k: clip(i * 50.0, i * 50.0 + 30.0) for i, k in enumerate((*words, "moved", "same"))}
    for key, value in words.items():
        db.set_clip(ids[key], **{key: value})
    db.conn.execute("UPDATE clips SET end_s = end_s + 2 WHERE id = ?", (ids["moved"],))
    db.conn.commit()
    db.set_clip(ids["same"], title="t", description="", hashtags="")
    judged = _judged(db)
    assert {k: judged[c]["reasons"] for k, c in ids.items()} == {
        "title": ["edited"], "description": ["edited"], "hashtags": ["edited"], "moved": ["edited"], "same": []}
    assert all(db.get_clip(c)["edited_at"] == "" for c in ids.values())     # edited_at says nothing here


def test_a_clip_an_older_version_made_has_no_fingerprint_and_is_kept_as_from_before(lib):
    """Its add_clip lists its own columns, so the fingerprint stays blank: a
    title it changed later can't be told from its run's."""
    db, data, clip = lib
    unused = clip(10.0, 40.0)
    db.conn.execute("INSERT INTO clips (video_id, start_s, end_s, score, hook, path, status, title, description, "
                    "hashtags, scores, render_opts, created_at) VALUES (?, 50.0, 80.0, 70, 'h', '', 'queued', "
                    "'My own title', '', '', '', '', 'x')", (VID,))
    db.conn.commit()
    older = db.conn.execute("SELECT id FROM clips WHERE start_s = 50.0").fetchone()[0]
    assert _reasons(db, older) == ["older"] and _judged(db)[unused]["replace"] is True
    run = remake.Run(db, VID, data)
    _make(run, 500.0, 530.0)
    out = run.finish()
    assert out["removed"] == [unused] and db.get_clip(older)["title"] == "My own title"


@pytest.mark.parametrize("stage, status, reason", [
    ("export", "done", "exported"), (None, "failed", "exported"), ("export", "cancelled", "exported"),
    ("translate", "done", None), ("export", "queued", "busy"),
])
def test_a_translated_export_job_keeps_the_clips_it_names(lib, stage, status, reason):
    """Another version of Clips Kitty exports a clip in other languages
    without note_language_export's mark; the translate job's row names it
    until the Queue page's history is cleared. A review pass (stage
    `translate`) writes no video: its saved text keeps the clip
    (clip_translations) if there is some."""
    db, _, clip = lib
    named, other = clip(10.0, 40.0), clip(50.0, 80.0)
    payload = {"clip_ids": [named], "languages": ["es"], **({"stage": stage} if stage else {})}
    job = db.add_job("translate", json.dumps(payload))
    db.conn.execute("UPDATE jobs SET status = ? WHERE id = ?", (status, job))
    db.conn.commit()
    judged = _judged(db)
    assert judged[named]["reasons"] == ([reason] if reason else [])
    assert judged[other]["replace"] is True


# ---- deleting -----------------------------------------------------------------------


def test_removing_takes_the_row_its_file_and_its_draft(lib):
    db, data, clip = lib
    clip_id = clip(10.0, 40.0)
    path = db.get_clip(clip_id)["path"]
    freed = remake.remove_clips(db, [clip_id], data)
    assert db.get_clip(clip_id) is None
    assert freed == 1000
    assert not (data / "previews" / f"clip_{clip_id}.mp4").exists()
    from pathlib import Path

    assert not Path(path).exists()


def test_a_file_another_clip_points_to_stays(lib):
    """A run names files by whole seconds, so a new 10.3-40.8 window writes
    over the old 10.0-40.0 one's file: deleting the old row must leave it."""
    db, data, clip = lib
    old = clip(10.0, 40.0)
    new = clip(10.3, 40.8)       # the same clip_00010-00040.mp4
    path = db.get_clip(old)["path"]
    remake.remove_clips(db, [old], data)
    assert db.get_clip(old) is None and db.get_clip(new) is not None
    from pathlib import Path

    assert Path(path).exists()


@pytest.mark.skipif(not sys.platform.startswith("win"), reason="only Windows refuses to delete an open file")
def test_a_file_a_player_holds_open_stays_and_nothing_fails(lib, capsys):
    """As DELETE /clips: the row goes, the file is left for Storage's clean-up."""
    db, data, clip = lib
    clip_id = clip(10.0, 40.0)
    path = db.get_clip(clip_id)["path"]
    with open(path, "rb"):
        assert remake.remove_clips(db, [clip_id], data) == 0
    assert db.get_clip(clip_id) is None
    from pathlib import Path

    assert Path(path).exists()
    assert "could not remove" in capsys.readouterr().out


def test_a_file_outside_the_data_folder_is_never_deleted(lib, tmp_path):
    db, data, clip = lib
    clip_id = clip(10.0, 40.0)
    elsewhere = tmp_path / "Videos" / "keep.mp4"
    elsewhere.parent.mkdir()
    elsewhere.write_bytes(b"precious")
    db.set_clip(clip_id, path=str(elsewhere))
    remake.remove_clips(db, [clip_id], data)
    assert db.get_clip(clip_id) is None
    assert elsewhere.exists()


# ---- around a run -------------------------------------------------------------------


def _rendered(run, start, end) -> str:
    """The run's render of a window, as core/pipeline.py _render_files writes
    it: under its whole seconds, in the video's clip folder. finish() gives
    a clip made again in place only a file that is there."""
    folder = run.data_dir / "clips" / "someone" / f"A stream [{VID}]"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"clip_{int(start):05d}-{int(end):05d}.mp4"
    path.write_bytes(b"the run's render")
    return str(path)


def _make(run, start, end, render_opts=""):
    """What core/pipeline.py does for a rendered clip: its file, then
    _register_clip: an insert, or the update of the same window's row."""
    path = _rendered(run, start, end)
    clip_id = run.db.add_clip(VID, start, end, 80, "hook", path=path, status="queued",
                              title="new", render_opts=render_opts)
    if clip_id is None:
        row = run.db.conn.execute("SELECT id FROM clips WHERE video_id = ? AND start_s = ? AND end_s = ?",
                                  (VID, round(start, 2), round(end, 2))).fetchone()
        run.db.set_clip(row["id"], path=path, scores="{}")
    return clip_id


def test_after_a_run_the_unused_clips_it_didnt_make_again_are_replaced(lib, capsys):
    db, data, clip = lib
    unused = clip(10.0, 70.0)
    again = clip(100.0, 130.0)
    edited = clip(200.0, 270.0, edited_at=_now())
    published = clip(300.0, 370.0)
    db.record_upload(published, "yt1")
    longform = clip(400.0, 520.0, render_opts=json.dumps({"profile": "short_clips"}))

    run = remake.Run(db, VID, data)
    _make(run, 12.0, 40.0)            # new
    _make(run, 100.0, 130.0)          # the same window as before
    out = run.finish()

    left = {r["id"] for r in db.conn.execute("SELECT id FROM clips")}
    assert unused not in left
    assert {again, edited, published, longform} <= left
    assert len(left) == 5             # and the new one
    assert out["made"] == 2 and out["removed"] == [unused] and out["replaced"] == 2
    assert out["kept"] == 3 and out["kept_because"] == {"longform": 1, "edited": 1, "published": 1}
    said = capsys.readouterr().out
    assert "made 2 clip(s)" in said and "1 removed, 1 made again" in said
    assert "Kept 3: 1 16:9 Longform, 1 edited, 1 published or sent to publish." in said


def _engine():
    """core.pipeline._edited (D33) reads a clip's render options: the engine
    itself, as in test_by_hand_render_options_keep_it."""
    pytest.importorskip("numpy")
    pytest.importorskip("cv2")
    pytest.importorskip("PIL")


@pytest.mark.parametrize("positional", [False, True])
def test_an_unused_clip_made_again_takes_what_the_run_made_it_with(lib, positional):
    """A forced run updates the same window's row and keeps the rest of it
    (core/pipeline.py _register_clip). An unused one gets the render options,
    score and hook its new file was made with: the editor and every later
    render of it read those (captions off stays off). Its title stays."""
    _engine()
    db, data, clip = lib
    old_opts = {"job_captions": True, "caption_style": {"font": "Arial", "font_size": 70}}
    again = clip(100.0, 130.0, render_opts=json.dumps(old_opts), score=60, hook="old hook", title="My title")
    run = remake.Run(db, VID, data)
    new_opts = {"job_captions": False, "caption_style": {"font": "Impact", "font_size": 90}}
    new_file = _rendered(run, 100.0, 130.0)
    if positional:
        made = run.db.add_clip(VID, 100.0, 130.0, 95, "new hook", new_file, "queued", "new title", "", "[]", "{}",
                               json.dumps(new_opts))
    else:
        made = run.db.add_clip(VID, 100.0, 130.0, 95, "new hook", path=new_file, status="queued",
                               title="new title", render_opts=json.dumps(new_opts))
    assert made is None                     # the same window: an update, as in any forced run
    out = run.finish()

    row = db.get_clip(again)
    assert json.loads(row["render_opts"]) == new_opts
    assert (row["score"], row["hook"], row["title"]) == (95, "new hook", "My title")
    assert row["path"] == new_file
    assert out["removed"] == [] and out["replaced"] == 1


def test_a_used_clip_made_again_keeps_its_own(lib):
    """Made again with its saved edits (D33), it keeps everything it had."""
    _engine()
    db, data, clip = lib
    old_opts = {"job_captions": True, "caption_style": {"font": "Arial"}}
    kept = clip(100.0, 130.0, render_opts=json.dumps(old_opts), score=60, edited_at=_now())
    run = remake.Run(db, VID, data)
    run.db.add_clip(VID, 100.0, 130.0, 95, "new hook", render_opts=json.dumps({"job_captions": False}))
    run.finish()
    row = db.get_clip(kept)
    assert json.loads(row["render_opts"]) == old_opts and row["score"] == 60


def test_a_run_that_stopped_says_so_and_replaces_nothing(lib, capsys):
    """A run that failed or was cancelled (server/jobs.py calls stopped())."""
    db, data, clip = lib
    ids = {clip(10.0, 70.0), clip(100.0, 130.0)}
    run = remake.Run(db, VID, data)
    _make(run, 12.0, 40.0)
    run.stopped("was cancelled")
    said = capsys.readouterr().out
    assert "this run was cancelled, so nothing was replaced and the 2 clip(s) from before are all kept" in said
    assert ids <= {r["id"] for r in db.conn.execute("SELECT id FROM clips")}


def test_a_run_that_stopped_leaves_every_clip_from_before_as_it_was(lib):
    """Nothing is replaced: an unused clip whose window the run made again
    before it stopped keeps its own file and render options (its file was
    out of the run's way), and what the run would have written on its row is
    dropped. Nothing is deleted."""
    from pathlib import Path

    _engine()
    db, data, clip = lib
    again = clip(100.0, 130.0, render_opts=json.dumps({"job_captions": True}))
    unused = clip(10.0, 70.0)
    before = {c: dict(db.get_clip(c)) for c in (again, unused)}
    run = remake.Run(db, VID, data)
    first = Path(before[again]["path"])
    first.write_bytes(b"the run's render")            # as the run's render writes it
    run.db.add_clip(VID, 100.0, 130.0, 95, "new hook", path=str(first),
                    render_opts=json.dumps({"job_captions": False}))
    run.db.set_clip(again, path=str(first), scores='{"new": 1}')    # core/pipeline.py _register_clip
    run.stopped("failed")
    assert {c: dict(db.get_clip(c)) for c in (again, unused)} == before
    assert first.read_bytes() == b"x" * 1000           # its own file, back under its name


def test_a_job_that_fails_after_its_run_finished_says_nothing_more(lib, capsys):
    """A job can still fail once its run is done (what it was asked to do
    next): the clips were replaced, and the log mustn't say otherwise."""
    db, data, clip = lib
    clip(10.0, 70.0)
    run = remake.Run(db, VID, data)
    _make(run, 12.0, 40.0)
    run.finish()
    capsys.readouterr()
    run.stopped("failed")
    assert capsys.readouterr().out == ""


def test_a_run_that_made_nothing_replaces_nothing(lib, capsys):
    db, data, clip = lib
    ids = {clip(10.0, 70.0), clip(100.0, 130.0)}
    run = remake.Run(db, VID, data)
    out = run.finish()
    assert {r["id"] for r in db.conn.execute("SELECT id FROM clips")} == ids
    assert out["made"] == 0 and out["removed"] == []
    assert "made no clips" in capsys.readouterr().out


def test_longform_and_reels_a_run_made_dont_count_as_made(lib):
    """A job with Longform beside it makes 16:9 clips and reels too: only
    9:16 clips are the run's clips."""
    db, data, clip = lib
    unused = clip(10.0, 70.0)
    run = remake.Run(db, VID, data)
    _make(run, 0.0, 300.0, render_opts=json.dumps({"profile": "highlights"}))
    _make(run, 0.0, 95.0, render_opts=json.dumps({"reel": "recap"}))
    assert run.finish()["made"] == 0
    assert db.get_clip(unused) is not None


def test_what_a_person_does_while_the_run_works_counts(lib):
    """Old clips are judged when the run ends, not when it starts."""
    db, data, clip = lib
    later = clip(10.0, 70.0)
    run = remake.Run(db, VID, data)
    _make(run, 200.0, 230.0)
    db.set_clip(later, exported_at=_now())       # exported meanwhile
    run.finish()
    assert db.get_clip(later) is not None


def test_only_clips_from_before_the_run_are_old(lib):
    db, data, clip = lib
    run = remake.Run(db, VID, data)
    meanwhile = clip(500.0, 530.0)      # not there when the run started
    _make(run, 200.0, 230.0)
    run.finish()
    assert db.get_clip(meanwhile) is not None


def test_other_videos_are_never_touched(lib):
    db, data, clip = lib
    db.upsert_video("other", title="Other")
    theirs = clip(10.0, 70.0, video_id="other", file="other.mp4")
    run = remake.Run(db, VID, data)
    _make(run, 200.0, 230.0)
    run.db.add_clip("other", 1.0, 2.0, 50, "h")    # a clip of another video isn't this run's
    assert run.made == {(200.0, 230.0)}
    run.finish()
    assert db.get_clip(theirs) is not None


def test_the_runs_database_is_the_workers_own_in_every_other_way(lib):
    db, data, clip = lib
    run = remake.Run(db, VID, data)
    assert run.db.conn is db.conn
    assert run.db.shorts_made(VID) == db.shorts_made(VID)
    new_id = _make(run, 1.0, 31.0)
    assert isinstance(new_id, int) and db.get_clip(new_id) is not None
    assert _make(run, 1.0, 31.0) is None          # the same window: an update, as before


def test_finishing_never_fails_the_job(lib, monkeypatch, capsys):
    db, data, clip = lib
    unused = clip(10.0, 70.0)
    run = remake.Run(db, VID, data)
    _make(run, 200.0, 230.0)

    def broken(*_a, **_k):
        raise RuntimeError("database is locked")

    monkeypatch.setattr(remake, "classify", broken)
    out = run.finish()
    assert out["removed"] == [] and "database is locked" in out["error"]
    assert db.get_clip(unused) is not None
    assert "they're all kept" in capsys.readouterr().out


def test_a_failure_partway_says_how_many_were_removed(lib, monkeypatch, capsys):
    """What was deleted before the failure went whole (row, file and draft),
    and the log says so rather than that everything was kept."""
    db, data, clip = lib
    first, second = clip(10.0, 40.0), clip(50.0, 80.0)
    path = db.get_clip(first)["path"]
    run = remake.Run(db, VID, data)
    _make(run, 200.0, 230.0)
    real, calls = db.delete_clip, []

    def delete_clip(clip_id):
        calls.append(clip_id)
        if len(calls) == 2:
            raise sqlite3.OperationalError("disk I/O error")
        return real(clip_id)

    monkeypatch.setattr(db, "delete_clip", delete_clip)
    out = run.finish()
    assert db.get_clip(first) is None and db.get_clip(second) is not None
    from pathlib import Path

    assert not Path(path).exists() and not (data / "previews" / f"clip_{first}.mp4").exists()
    assert out["removed"] == [first] and "disk I/O error" in out["error"]
    assert ("1 clip(s) from before that nobody had used were removed, then the rest couldn't be "
            "(disk I/O error), so they're kept") in capsys.readouterr().out


# ---- clips in use at the moment of deleting ---------------------------------------------


def test_a_clip_a_request_is_working_with_is_kept(lib):
    """A publish, an export, a thumbnail choice or an AI edit holds its clips
    (core/in_use.py) until what it did is recorded, which for an upload or
    a copy is only once it's done: Make clips again keeps them meanwhile."""
    db, data, clip = lib
    publishing, idle = clip(10.0, 40.0), clip(50.0, 80.0)
    run = remake.Run(db, VID, data)
    _make(run, 200.0, 230.0)
    with in_use.holding([publishing]):
        assert _reasons(db, publishing) == ["busy"]
        out = run.finish()
    assert db.get_clip(publishing) is not None and db.get_clip(idle) is None
    assert out["kept_because"] == {"busy": 1}
    assert in_use.held() == set()


def test_holding_counts_each_request_and_lets_go_however_it_ends():
    with in_use.holding([1, 2]):
        with pytest.raises(RuntimeError):
            with in_use.holding(["2", "not an id"]):
                assert in_use.held() == {1, 2}
                raise RuntimeError("the upload failed")
        assert in_use.held() == {1, 2}          # the first request still holds 2
    assert in_use.held() == set()


def test_a_use_recorded_after_the_clips_were_judged_still_keeps_it(lib, monkeypatch):
    """Each clip is judged again in the moment it would be deleted, inside a
    write transaction: a use recorded since the first look keeps it."""
    db, data, clip = lib
    retitled, idle = clip(10.0, 40.0), clip(50.0, 80.0)
    run = remake.Run(db, VID, data)
    _make(run, 200.0, 230.0)
    real = remake.classify

    def judged_then_used(*args, **kwargs):
        out = real(*args, **kwargs)
        other = StateDB(data / "state.db")       # a person's request, on a connection of its own
        other.set_clip(retitled, title="Mine", edited_at=_now())
        other.close()
        return out

    monkeypatch.setattr(remake, "classify", judged_then_used)
    out = run.finish()
    assert db.get_clip(retitled) is not None and db.get_clip(idle) is None
    assert out["removed"] == [idle] and out["kept_because"] == {"edited": 1}


def test_a_request_starting_while_a_clip_is_deleted_finds_it_gone(lib, monkeypatch):
    """Between a clip's last judging and its deletion being committed, no
    request can start holding it: one that starts then waits, and finds the
    clip gone rather than one about to go."""
    db, data, clip = lib
    unused = clip(10.0, 40.0)
    run = remake.Run(db, VID, data)
    _make(run, 200.0, 230.0)
    seen: dict = {}
    real_delete = db.delete_clip

    def request():
        with in_use.holding([unused]):
            other = StateDB(data / "state.db")
            seen["row"] = other.get_clip(unused)
            other.close()

    def delete_clip(clip_id):
        seen["thread"] = threading.Thread(target=request, daemon=True)
        seen["thread"].start()
        time.sleep(0.3)                          # time for it to reach the door
        seen["waited"] = seen["thread"].is_alive() and unused not in in_use.held()
        return real_delete(clip_id)

    monkeypatch.setattr(db, "delete_clip", delete_clip)
    run.finish()
    seen["thread"].join(10)
    assert seen["waited"] is True
    assert "row" in seen and seen["row"] is None and db.get_clip(unused) is None


# ---- the last run's options -------------------------------------------------------


def _job(db, payload, status="done", video_id=VID):
    job_id = db.add_job("process", json.dumps(payload), video_id=video_id)
    if status == "running":
        db.set_job(job_id, status="running")
    elif status != "queued":
        db.finish_job(job_id, status)
    return job_id


def test_options_come_from_the_last_finished_shorts_run(lib):
    db, _, _ = lib
    _job(db, {"url": "u", "long_clips": True})
    shorts = _job(db, {"url": "u", "force": True, "captions": False, "max_clips": 4, "long_clips": True,
                       "then": {"action": "publish"}, "webhook_url": "https://x", "webhook_secret": "s",
                       "origin": "watch", "replace_untouched": True})
    _job(db, {"url": "u", "longform": {"mode": "highlights"}})          # 16:9 only
    _job(db, {"url": "u", "min_score": 90}, status="failed")            # later, but it failed
    _job(db, {"url": "u", "min_score": 10}, status="queued")            # still waiting
    _job(db, {"url": "u", "captions": True}, video_id="other")
    options, job_id = remake.last_options(db, VID)
    assert job_id == shorts
    assert options == {"captions": False, "max_clips": 4, "long_clips": True}


def test_both_formats_count_as_a_shorts_run_without_their_longform(lib):
    db, _, _ = lib
    both = _job(db, {"url": "u", "longform": {"mode": "short_clips", "shorts": True}, "podcast": True})
    assert remake.last_options(db, VID) == ({"podcast": True}, both)


def test_a_failed_run_is_used_when_none_finished(lib):
    db, _, _ = lib
    _job(db, {"url": "u", "min_score": 50}, status="cancelled")
    failed = _job(db, {"url": "u", "min_score": 60}, status="failed")
    assert remake.last_options(db, VID) == ({"min_score": 60}, failed)


def test_with_no_job_left_the_settings_own_options(lib):
    db, _, _ = lib
    assert remake.last_options(db, VID) == ({}, None)


# ---- the source -----------------------------------------------------------------------


def test_a_file_from_this_pc_needs_its_copy(lib):
    db, data, _ = lib
    vid = "local_0123456789ab"
    db.upsert_video(vid, title="My file")
    db.set_video_source(vid, "https://www.youtube.com/watch?v=abcdefghijk", "youtube")  # its original
    gone = remake.source(db, vid, data / "downloads")
    assert gone == {"kind": "file", "downloaded": False, "link": "", "url": "", "usable": False}
    (data / "downloads").mkdir()
    (data / "downloads" / f"{vid}.mp4").write_bytes(b"v")
    assert remake.source(db, vid, data / "downloads")["url"] == f"local:{vid}"


def test_a_linked_video_uses_its_link_with_or_without_its_download(lib):
    pytest.importorskip("yt_dlp")  # sources.dispatch reads the link
    db, data, _ = lib
    link = f"https://www.youtube.com/watch?v={VID}"
    db.set_video_source(VID, link, "youtube")
    src = remake.source(db, VID, data / "downloads")
    assert src == {"kind": "link", "downloaded": False, "link": link, "url": link, "usable": True}
    (data / "downloads").mkdir()
    (data / "downloads" / f"{VID}__vertical.mp4").write_bytes(b"v")
    assert remake.source(db, VID, data / "downloads")["downloaded"] is True


def test_only_a_link_to_this_very_video_counts(lib):
    pytest.importorskip("yt_dlp")
    db, data, _ = lib
    db.set_video_source(VID, "https://www.youtube.com/@somechannel/live", "youtube")
    _job(db, {"url": "https://www.youtube.com/watch?v=zzzzzzzzzzz"})
    assert remake.find_link(db, VID) == ""
    _job(db, {"url": f"https://youtu.be/{VID}"})
    assert remake.find_link(db, VID) == f"https://youtu.be/{VID}"


def test_a_linked_video_with_no_link_left_uses_its_download(lib):
    pytest.importorskip("yt_dlp")
    db, data, _ = lib
    assert remake.source(db, VID, data / "downloads")["usable"] is False
    (data / "downloads").mkdir()
    (data / "downloads" / f"{VID}.mp4").write_bytes(b"v")
    src = remake.source(db, VID, data / "downloads")
    assert src["usable"] and src["url"] == f"local:{VID}" and src["link"] == ""
