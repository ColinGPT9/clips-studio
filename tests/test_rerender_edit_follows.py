"""A re-render that moves a clip's start or end carries its saved edit along
(server/jobs.py _rerender_clip, video_editor/timeline.py shift_edit).

The clip's details panel and the AI chat send a new start or end with no
edit: the saved cuts and mutes used to be rendered with the same numbers, so
each landed on another moment of the video. The timeline editor sends its
edit without a range, and that is rendered exactly as sent.
"""

import json

import pytest

from core.state import StateDB

SAVED = {"keep": [[0.0, 10.0], [12.0, 30.0]], "mutes": [[3.1, 3.4]],
         "muted_words": [{"start": 3.14, "end": 3.36, "word": "darn"}], "volume": 1.2}


@pytest.fixture
def rerender(tmp_path, monkeypatch):
    """A 100-130 s clip with SAVED as its edit, and a render that records
    what it was given."""
    pytest.importorskip("numpy")
    pytest.importorskip("cv2")
    from types import SimpleNamespace

    from core import pipeline
    from server import jobs

    db = StateDB(tmp_path / "state.db")
    db.conn.execute("INSERT INTO videos (video_id, title, status, created_at, updated_at) "
                    "VALUES ('v1', 't', 'done', '2026-01-01', '2026-01-01')")
    db.conn.commit()
    scores = {"plugin_edits": [{"id": "s1", "plugin": "dev/trim", "name": "Trim", "state": "used",
                                "window": [100.0, 130.0], "edit": {"cuts": [[110.0, 112.0]]},
                                "applied": {"removed": [[110.0, 112.0]]}}]}
    clip_id = db.add_clip("v1", 100.0, 130.0, 90, "hook", status="queued",
                          scores=json.dumps(scores), render_opts=json.dumps({"edit": SAVED}))
    (tmp_path / "downloads").mkdir()
    (tmp_path / "downloads" / "v1.mp4").write_bytes(b"source")
    (tmp_path / "transcripts").mkdir()
    (tmp_path / "transcripts" / "v1.json").write_text('{"segments": []}', encoding="utf-8")
    seen: dict = {}

    def render(_source, candidate, _segments, clip_dir, _config, render_opts, _lang):
        seen["window"] = (candidate.start, candidate.end)
        seen["edit"] = render_opts.get("edit")
        clip_dir.mkdir(parents=True, exist_ok=True)
        (clip_dir / "clip.mp4").write_bytes(b"clip")
        return clip_dir / "clip.mp4", json.dumps(render_opts)

    monkeypatch.setattr(pipeline, "_render_files", render)
    monkeypatch.setattr(jobs.broadcaster, "publish", lambda _e: None)
    job = SimpleNamespace(config={"paths": {"data_dir": str(tmp_path)}, "clips": {"outro": False}})

    def run(payload):
        jobs.Worker._rerender_clip(job, db, {"clip_id": clip_id, **payload})
        row = db.conn.execute("SELECT * FROM clips WHERE video_id = 'v1'").fetchone()
        return row, json.loads(row["render_opts"])["edit"]

    yield run, seen
    db.close()


def test_a_new_start_and_end_carry_the_cuts_to_the_same_moments(rerender):
    run, seen = rerender
    row, saved = run({"start": 95.0, "end": 140.0})

    shifted = {"keep": [[0.0, 15.0], [17.0, 45.0]], "mutes": [[8.1, 8.4]],
               "muted_words": [{"start": 8.14, "end": 8.36, "word": "darn"}], "volume": 1.2}
    assert seen == {"window": (95.0, 140.0), "edit": shifted}   # rendered with it
    assert saved == shifted                                      # and saved with it
    assert (row["start_s"], row["end_s"]) == (95.0, 140.0)


def test_an_edit_sent_with_a_new_range_is_the_saved_clips_and_is_carried_too(rerender):
    run, seen = rerender
    sent = {"keep": [[0.0, 20.0]], "mutes": []}
    run({"start": 90.0, "end": 130.0, "render_opts": {"edit": sent}})
    assert seen["edit"] == {"keep": [[0.0, 30.0]], "mutes": []}   # the 10 s gained play


def test_an_edit_sent_without_a_range_is_rendered_as_sent(rerender):
    """The timeline editor's Apply: nothing moves."""
    run, seen = rerender
    sent = {"keep": [[1.0, 9.0]], "mutes": [[2.0, 2.5]]}
    _, saved = run({"render_opts": {"edit": sent}})
    assert seen["edit"] == sent and saved == sent


def test_an_edit_of_nothing_sent_with_a_new_range_stays_nothing(rerender):
    """Apply sends `edit: null` when nothing is cut: there is nothing to carry."""
    run, seen = rerender
    _, saved = run({"start": 95.0, "end": 140.0, "render_opts": {"edit": None}})
    assert seen["edit"] is None and saved is None


def test_with_no_kept_part_left_the_whole_clip_plays_and_the_log_says_so(rerender, capsys):
    """Trimmed to lie inside the 110-112 s that was cut: nothing kept is left."""
    run, seen = rerender
    run({"start": 110.2, "end": 111.9})
    assert "keep" not in seen["edit"] and seen["edit"]["volume"] == 1.2
    assert "the whole clip plays" in capsys.readouterr().out


def test_a_used_suggestions_cut_is_still_found_in_the_widened_clip(rerender):
    """plugins/edit_marks reads the saved edit at the clip's window: carried,
    the suggestion's cut is still in the clip and it stays used. Before, the
    widened clip read as not holding it, and the suggestion was hidden."""
    run, _ = rerender
    row, _ = run({"start": 95.0, "end": 140.0})
    (entry,) = json.loads(row["scores"])["plugin_edits"]
    assert entry["state"] == "used" and entry["applied"] == {"removed": [[110.0, 112.0]]}


def test_the_clip_is_marked_as_changed_by_a_person(rerender):
    """Every render job is one a person asked for: Make clips again keeps it."""
    run, _ = rerender
    row, _ = run({"render_opts": {"caption_style": {"font_size": 90}}})
    assert row["edited_at"]


def test_the_new_row_keeps_no_fingerprint_of_a_run(rerender, tmp_path):
    """Its row is inserted with the clip's words as they were, which may be a
    person's: it never gets the fingerprint of what a run made
    (core/state.py as_made), so it can't count as a clip nobody used."""
    from core import remake

    run, _ = rerender
    row, _ = run({"start": 95.0, "end": 140.0})
    assert row["as_made"] == ""
    db = StateDB(tmp_path / "state.db")
    try:
        (judged,) = remake.classify(db, "v1")
    finally:
        db.close()
    assert judged["replace"] is False and judged["reasons"][0] == "edited" and "older" in judged["reasons"]
