"""The export compiler's Branch A (editor suite step 7): a project that still
fits today's render options is made by today's re-render job, with
`render_opts_replace` (server/jobs.py _rerender_clip).

- The job's render options replace the clip's saved ones; nothing is merged.
- They are not shifted a second time when the window moved: the projection
  (video_editor/project/projection.py) already counted the edit, the caption
  lines and the speaker fixes from the start it returns.
- The window is rounded to the hundredth of a second the clip's row holds.
- A speaker fix that ends before a later start says nothing on the clip: the
  caption render ignores it (it is kept, as Apply keeps one past the end).
- A job without the key is today's job, shifts and all.
"""

import json

import pytest

from core.models import ClipCandidate, Segment
from core.state import StateDB

SAVED = {"edit": {"keep": [[0.0, 10.0], [12.0, 30.0]], "mutes": [[3.1, 3.4]], "volume": 1.2},
         "caption_lines": [{"start": 1.0, "end": 2.0, "text": "one"}, {"start": 20.0, "end": 21.0, "text": "two"}],
         "speaker_edits": [[4.0, 6.0, 1]],
         "caption_style": {"font_size": 80, "highlight": True},
         "filter": "vibrant"}


def _word(start: float, end: float, word: str) -> dict:
    return {"start": start, "end": end, "word": word}


# The video's words: two said before the clip's saved start (100 s), the
# rest inside it.
WORDS = [_word(96.0, 96.4, "early"), _word(96.5, 96.9, "bird"), _word(101.0, 101.5, "one"),
         _word(120.0, 120.5, "two")]


@pytest.fixture
def rerender(tmp_path, monkeypatch):
    """A 100-130 s clip saved with SAVED, and a render that records what it was given."""
    pytest.importorskip("numpy")
    pytest.importorskip("cv2")
    from types import SimpleNamespace

    from core import pipeline
    from server import jobs

    db = StateDB(tmp_path / "state.db")
    db.conn.execute("INSERT INTO videos (video_id, title, status, created_at, updated_at) "
                    "VALUES ('v1', 't', 'done', '2026-01-01', '2026-01-01')")
    db.conn.commit()
    clip_id = db.add_clip("v1", 100.0, 130.0, 90, "hook", status="queued", render_opts=json.dumps(SAVED))
    (tmp_path / "downloads").mkdir()
    (tmp_path / "downloads" / "v1.mp4").write_bytes(b"source")
    (tmp_path / "transcripts").mkdir()
    segments = [{"start": 96.0, "end": 121.0, "text": "early bird one two", "words": WORDS}]
    (tmp_path / "transcripts" / "v1.json").write_text(json.dumps({"segments": segments}), encoding="utf-8")
    seen: dict = {}

    def render(_source, candidate, _segments, clip_dir, _config, render_opts, _lang):
        seen["window"] = (candidate.start, candidate.end)
        seen["opts"] = json.loads(json.dumps(render_opts))
        clip_dir.mkdir(parents=True, exist_ok=True)
        (clip_dir / "clip.mp4").write_bytes(b"clip")
        return clip_dir / "clip.mp4", json.dumps(render_opts)

    monkeypatch.setattr(pipeline, "_render_files", render)
    monkeypatch.setattr(jobs.broadcaster, "publish", lambda _e: None)
    job = SimpleNamespace(config={"paths": {"data_dir": str(tmp_path)}, "clips": {"outro": False}})

    def run(payload):
        jobs.Worker._rerender_clip(job, db, {"clip_id": clip_id, **payload})
        row = db.conn.execute("SELECT * FROM clips WHERE video_id = 'v1'").fetchone()
        return row, json.loads(row["render_opts"])

    yield run, seen
    db.close()


def test_the_options_replace_the_saved_ones(rerender):
    run, seen = rerender
    given = {"edit": {"speed": 1.5}, "caption_style": {"font_size": 60}}
    _row, saved = run({"render_opts": given, "render_opts_replace": True})
    # Nothing of SAVED is merged in: no filter, no caption lines, no
    # highlight from the old caption style, no speaker fixes.
    assert seen["opts"] == given
    assert saved == given


def test_without_the_key_the_options_are_merged_as_today(rerender):
    run, seen = rerender
    run({"render_opts": {"caption_style": {"font_size": 60}}})
    assert seen["opts"]["filter"] == "vibrant"
    assert seen["opts"]["caption_style"] == {"font_size": 60, "highlight": True}
    assert seen["opts"]["edit"] == SAVED["edit"]


def test_a_moved_window_is_not_shifted_a_second_time(rerender):
    """Branch A's options for a clip whose start moved from 100 to 95 s are
    counted from 95 already: rendered and saved as given."""
    run, seen = rerender
    given = {"edit": {"keep": [[0.0, 15.0], [17.0, 35.0]], "mutes": [[8.1, 8.4]], "volume": 1.2},
             "caption_lines": [{"start": 6.0, "end": 7.0, "text": "one"}, {"start": 25.0, "end": 26.0, "text": "two"}],
             "speaker_edits": [[9.0, 11.0, 1]]}
    row, saved = run({"start": 95.0, "end": 130.0, "render_opts": given, "render_opts_replace": True})
    assert seen["window"] == (95.0, 130.0)
    assert seen["opts"]["edit"] == given["edit"]
    assert seen["opts"]["speaker_edits"] == given["speaker_edits"]
    # The given lines stay where they are; the head the clip gained (95-100 s)
    # is captioned from the transcript, as Apply captions it.
    lines = seen["opts"]["caption_lines"]
    assert [(l["start"], l["end"], l["text"]) for l in lines if l["text"] in ("one", "two")] == [
        (6.0, 7.0, "one"), (25.0, 26.0, "two")]
    assert [l["text"] for l in lines] == ["early bird", "one", "two"]
    assert lines[0]["start"] == pytest.approx(1.0)
    assert saved["edit"] == given["edit"] and saved["caption_lines"] == lines
    assert (row["start_s"], row["end_s"]) == (95.0, 130.0)


def test_the_same_move_without_the_key_shifts_as_today(rerender):
    """The contrast: today's job carries saved times to the new start."""
    run, seen = rerender
    run({"start": 95.0, "end": 130.0})
    assert seen["opts"]["edit"]["keep"] == [[0.0, 15.0], [17.0, 35.0]]
    assert seen["opts"]["speaker_edits"] == [[9.0, 11.0, 1]]
    assert [l["start"] for l in seen["opts"]["caption_lines"] if l["text"] in ("one", "two")] == [6.0, 25.0]


def test_given_lines_that_already_cover_the_head_get_nothing_added(rerender):
    """A project keeps its caption lines in the video's time, so lines for the
    gained head can come with the options: they are not captioned twice."""
    run, seen = rerender
    given = {"caption_lines": [{"start": 1.0, "end": 1.9, "text": "EARLY BIRD"},
                               {"start": 6.0, "end": 7.0, "text": "one"}]}
    run({"start": 95.0, "end": 130.0, "render_opts": given, "render_opts_replace": True})
    assert [l["text"] for l in seen["opts"]["caption_lines"]] == ["EARLY BIRD", "one", "two"]


def test_an_unmoved_window_gets_no_head_captions(rerender):
    """As Apply: the head is only what the clip gained before its old start."""
    run, seen = rerender
    given = {"caption_lines": [{"start": 20.0, "end": 21.0, "text": "two"}]}
    run({"render_opts": given, "render_opts_replace": True})
    assert [l["text"] for l in seen["opts"]["caption_lines"]] == ["two"]


@pytest.mark.parametrize("sent, kept", [
    ((95.004, 129.996), (95.0, 130.0)),
    ((95.006, 130.004), (95.01, 130.0)),
    ((95.000001, 130.0), (95.0, 130.0)),
])
def test_the_window_is_rounded_to_the_rows_hundredth(rerender, sent, kept):
    """The row holds hundredths; the file is made from the same window, so
    the two agree. The options' times are kept as given."""
    run, seen = rerender
    given = {"edit": {"keep": [[0.0, 10.0]]}}
    row, saved = run({"start": sent[0], "end": sent[1], "render_opts": given, "render_opts_replace": True})
    assert seen["window"] == kept
    assert (row["start_s"], row["end_s"]) == kept
    assert saved["edit"] == given["edit"]


def test_without_the_key_the_window_is_not_rounded(rerender):
    run, seen = rerender
    run({"start": 95.004, "end": 130.0})
    assert seen["window"] == (95.004, 130.0)


# ---- speaker fixes before a later start ------------------------------------------------------


def test_a_speaker_fix_that_ends_before_the_start_changes_no_caption():
    """Branch A's speaker fixes are counted from the new start, so one made
    on the seconds the clip lost has negative times. It is kept (as Apply
    keeps one past the end, for when the clip grows again), and the caption
    render ignores it: the lines and their speakers are the same with and
    without it, built fresh or tagged from saved lines."""
    from video.captions import build_caption_lines, paint_turns, tag_lines, words_of

    words = [_word(100.0 + i, 100.0 + i + 0.5, f"w{i}") for i in range(12)]
    segments = [Segment(start=100.0, end=112.0, text=" ".join(w["word"] for w in words), words=words)]
    clip = ClipCandidate(start=100.0, end=112.0, score=1)
    heard = [[3.0, 6.0, 1]]
    inside = [[8.0, 9.6, 1]]
    before = [[-6.0, -1.0, 1], [-3.0, -0.2, 0]]

    with_fix = paint_turns(heard, before + inside)
    without = paint_turns(heard, inside)
    fresh_with = build_caption_lines(segments, clip, 3, with_fix)
    fresh_without = build_caption_lines(segments, clip, 3, without)
    assert fresh_with == fresh_without
    assert any(l.get("speaker") for l in fresh_without)       # the test can see a speaker at all

    saved = [{k: v for k, v in l.items() if k != "speaker"} for l in fresh_without]
    clip_words = words_of(segments, clip)
    assert tag_lines(saved, with_fix, clip_words) == tag_lines(saved, without, clip_words)


def test_refit_with_head_until_none_is_the_rule_as_before():
    """The new keyword's default leaves step 3's refit exactly as it was."""
    from video.captions import refit_caption_lines

    segments = [Segment(start=96.0, end=121.0, text="", words=WORDS)]
    lines = [{"start": 1.0, "end": 2.0, "text": "one"}]
    assert refit_caption_lines(lines, segments, 100.0, 95.0, 130.0, 3) == refit_caption_lines(
        lines, segments, 100.0, 95.0, 130.0, 3, head_until=None)
    shifted = refit_caption_lines(lines, segments, 100.0, 95.0, 130.0, 3)
    assert [l["text"] for l in shifted] == ["early bird", "one", "two"]
    assert [l["start"] for l in shifted if l["text"] == "one"] == [6.0]


def test_branch_a_of_an_unchanged_project_renders_what_apply_renders(rerender):
    """The export of a project nobody changed, with the clip's own preset,
    gives today's render exactly what Apply gives it: the same window and
    the same render options (so the same commands)."""
    from video_editor.project import importer, projection

    run, seen = rerender
    clip = {"video_id": "v1", "start": 100.0, "end": 130.0, "render_opts": SAVED}
    answer = projection.to_clip(importer.import_clip(clip))
    assert answer["branch"] == "A"
    row, _opts = run({})                    # Apply, on the clip as saved
    applied = json.loads(json.dumps(seen))
    # The export, on the clip Apply left (a re-render gives it a new id).
    run({"clip_id": row["id"], "start": answer["start"], "end": answer["end"],
         "render_opts": answer["render_opts"], "render_opts_replace": True})
    assert json.loads(json.dumps(seen)) == applied


def test_two_rerenders_of_one_clip_never_run_at_once(monkeypatch):
    """Fix 7-D8: a re-render holds the clip's lock (server/jobs.py
    clip_lock), so Branch A on the editor lane and a queued Apply of the
    same clip can't work on its rows and files together. A waiting one
    runs its `while_waiting` check (Branch A's cancel) as it waits."""
    from server import jobs

    calls = []
    monkeypatch.setattr(jobs.Worker, "_rerender_clip_now",
                        lambda self, db, payload, before_register=None: calls.append(payload["clip_id"]) or 7)
    lock = jobs.clip_lock(424242)
    assert jobs.clip_lock(424242) is lock and jobs.clip_lock(424243) is not lock
    lock.acquire()
    waits = []

    def waiting():
        waits.append(1)
        if len(waits) == 2:
            raise RuntimeError("cancelled while it waited")

    with pytest.raises(RuntimeError):
        jobs.Worker._rerender_clip(None, None, {"clip_id": 424242}, while_waiting=waiting)
    assert calls == [] and len(waits) == 2
    lock.release()
    assert jobs.Worker._rerender_clip(None, None, {"clip_id": 424242}) == 7
    assert calls == [424242] and not lock.locked()
