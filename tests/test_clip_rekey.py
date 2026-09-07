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
"""

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
