"""The second speaker's caption colour, from the caption style to the file (#126).

The render asks analysis/voice_turns.py who is talking only when the clip's
caption style has the option on, burns the other speaker's lines in the
second colour, and saves the turns with the clip for the editor. What it must
not do:
- load the voice models, or change a byte, for a clip without the option;
- fail a render because the listening failed;
- listen to a piece of a video another PC sent it to render;
- leave a clip holding turns from a render that is gone.

The listening itself is tests/test_voice_turns.py; here it is stood in for.
"""

import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from core import modes
from core.models import ClipCandidate, Segment

SECOND = "&HFFE15C&"      # the default second colour, #5CE1FF, as ASS writes it
CLIP = ClipCandidate(start=10.0, end=20.0, score=80)
TURNS = [[2.0, 4.0, 1], [7.0, 8.0, 1]]
ON = {"second_speaker": True}


def _segments() -> list[Segment]:
    """A word every half second for a minute: w0 at 0.0-0.5, w1 at 0.5-1.0, ..."""
    words = [{"start": i * 0.5, "end": i * 0.5 + 0.5, "word": f"w{i}"} for i in range(120)]
    return [Segment(start=0.0, end=60.0, text=" ".join(w["word"] for w in words), words=words)]


def _gapped() -> list[Segment]:
    """The same, with a second and a half of silence where w31-w33 were."""
    words = [w for w in _segments()[0].words if w["word"] not in ("w31", "w32", "w33")]
    return [Segment(start=0.0, end=60.0, text=" ".join(w["word"] for w in words), words=words)]


@pytest.fixture
def pipeline(monkeypatch):
    pytest.importorskip("numpy")
    pytest.importorskip("cv2")
    from core import pipeline as pipeline_mod

    def framing(*_a, **_k):
        raise AssertionError("framing ran")

    import video.asd as asd
    import video.cropper as cropper
    import video.podcast as podcast
    import video.tracker as tracker

    for module, name in ((tracker, "compute_tracking"), (podcast, "analyze"), (cropper, "render_vertical"),
                         (asd, "_load"), (pipeline_mod, "_share_the_cpu")):
        monkeypatch.setattr(module, name, framing)
    return pipeline_mod


@pytest.fixture
def heard(monkeypatch):
    """Stands in for the listening: every clip has TURNS. `heard.calls` counts
    how often it was asked, and `heard.answer` can be made to raise."""
    from analysis import voice_turns

    state = SimpleNamespace(calls=0, answer=lambda: [list(t) for t in TURNS])

    def turns_for(_source, _candidate, _segments, _config):
        state.calls += 1
        return state.answer()

    monkeypatch.setattr(voice_turns, "turns_for", turns_for)
    return state


def _render(pipeline, monkeypatch, tmp_path, style=None, opts=None, source=None, segments=None):
    """One clip through _render_files as a Vertical Live (one encode, nothing
    framed). Returns (the caption file as it was burned, the saved options)."""
    burned = {}

    def fake_cut(_source, _candidate, output, ass_path=None, vf_extra="", normalize=True):
        burned["ass"] = Path(ass_path).read_text(encoding="utf-8") if ass_path else None
        Path(output).write_bytes(b"clip")

    def fake_edits(_input, _edit, output, ass_path=None, normalize=False):
        # A clip with editor cuts is cut plain first, and captioned here.
        burned["ass"] = Path(ass_path).read_text(encoding="utf-8") if ass_path else None
        Path(output).write_bytes(b"clip")

    import video_editor.export as export

    monkeypatch.setattr(pipeline, "cut_clip", fake_cut)
    monkeypatch.setattr(export, "apply_edits", fake_edits)
    monkeypatch.setattr(modes, "probe_size", lambda _path: (1080, 1920))
    config = {
        "clips": {"captions": True, "outro": False, "vertical": True, "vertical_live": True,
                  **({"caption_style": style} if style else {})},
        "paths": {"data_dir": str(tmp_path)},
        "tracking": {"detector": "yolov8n-pose.pt", "sample_fps": 8},
    }
    if source is None:
        source = tmp_path / "downloads" / "v1.mp4"
        source.parent.mkdir(exist_ok=True)
        source.write_bytes(b"video")
    _final, opts_json = pipeline._render_files(
        source, CLIP, segments or _segments(), tmp_path / "clips", config, opts
    )
    return burned["ass"], json.loads(opts_json) if opts_json else {}


def _events(ass: str) -> list[str]:
    return [line.split(",,", 2)[-1] for line in ass.splitlines() if line.startswith("Dialogue:")]


# ---- off ----------------------------------------------------------------------------


def test_a_clip_without_the_option_never_loads_the_voice_models(pipeline, monkeypatch, tmp_path):
    import analysis

    monkeypatch.delitem(sys.modules, "analysis.voice_turns", raising=False)
    monkeypatch.delattr(analysis, "voice_turns", raising=False)

    ass, saved = _render(pipeline, monkeypatch, tmp_path, {"color": "#FFFFFF"})

    assert "analysis.voice_turns" not in sys.modules
    assert "\\1c" not in ass and "speaker_turns" not in saved


def test_the_caption_file_is_the_same_with_the_option_off_as_with_none(pipeline, monkeypatch, tmp_path, heard):
    plain, _ = _render(pipeline, monkeypatch, tmp_path)
    off, _ = _render(pipeline, monkeypatch, tmp_path, {"second_speaker": False})

    assert off == plain and heard.calls == 0


def test_turns_left_by_an_earlier_render_go_when_the_option_is_off(pipeline, monkeypatch, tmp_path, heard):
    ass, saved = _render(pipeline, monkeypatch, tmp_path, opts={"speaker_turns": TURNS, "crop": "track"})

    assert "speaker_turns" not in saved and saved["crop"] == "track"
    assert "\\1c" not in ass and heard.calls == 0


def test_the_highlights_look_keeps_its_own_colour(pipeline, monkeypatch, tmp_path, heard):
    ass, saved = _render(pipeline, monkeypatch, tmp_path, {**ON, "post_style": "highlights"})

    assert heard.calls == 0 and "speaker_turns" not in saved
    assert SECOND not in ass


# ---- on -----------------------------------------------------------------------------


def test_the_other_speakers_lines_burn_in_the_second_colour_and_the_turns_are_saved(
        pipeline, monkeypatch, tmp_path, heard):
    ass, saved = _render(pipeline, monkeypatch, tmp_path, ON)

    assert heard.calls == 1 and saved["speaker_turns"] == TURNS
    assert saved["caption_style"] == ON
    # Lines end where the speaker changes: w23 alone, then theirs.
    assert _events(ass)[:5] == [
        "W20 W21 W22", "W23", f"{{\\1c{SECOND}}}W24 W25 W26", f"{{\\1c{SECOND}}}W27", "W28 W29 W30",
    ]


def test_a_clips_own_style_turns_it_on_for_that_clip(pipeline, monkeypatch, tmp_path, heard):
    ass, saved = _render(pipeline, monkeypatch, tmp_path, opts={"caption_style": {**ON, "uppercase": False}})

    assert saved["speaker_turns"] == TURNS and f"{{\\1c{SECOND}}}w24 w25 w26" in _events(ass)


def test_lines_the_user_saved_keep_their_words_and_take_the_colour(pipeline, monkeypatch, tmp_path, heard):
    lines = [{"start": 0.0, "end": 2.0, "text": "what was said"},
             {"start": 2.0, "end": 4.0, "text": "and the answer", "speaker": 9},
             {"start": 4.0, "end": 6.0, "text": "back to me", "speaker": 1}]     # stale marks

    ass, saved = _render(pipeline, monkeypatch, tmp_path, ON, opts={"caption_lines": lines})

    assert _events(ass) == ["WHAT WAS SAID", f"{{\\1c{SECOND}}}AND THE ANSWER", "BACK TO ME"]
    assert saved["caption_lines"] == lines          # as the user saved them


def test_the_turns_follow_a_line_through_a_cut(pipeline, monkeypatch, tmp_path, heard):
    """The second before the other speaker is cut out: their line starts a
    second earlier in the clip, and is still theirs."""
    ass, _ = _render(pipeline, monkeypatch, tmp_path, ON, opts={"edit": {"keep": [[0.0, 1.0], [2.0, 10.0]]}})

    theirs = [line for line in ass.splitlines() if f"{{\\1c{SECOND}}}W24 W25 W26" in line]
    assert len(theirs) == 1 and theirs[0].startswith("Dialogue: 0,0:00:01.00,0:00:02.50,")


def test_a_clip_with_one_voice_saves_no_turns(pipeline, monkeypatch, tmp_path, heard):
    heard.answer = lambda: []
    plain, _ = _render(pipeline, monkeypatch, tmp_path)

    ass, saved = _render(pipeline, monkeypatch, tmp_path, ON, opts={"speaker_turns": TURNS})

    assert ass == plain and "speaker_turns" not in saved


def test_a_render_goes_on_when_the_listening_fails(pipeline, monkeypatch, tmp_path, heard, capsys):
    def broken():
        raise RuntimeError("no audio stream")

    heard.answer = broken
    plain, _ = _render(pipeline, monkeypatch, tmp_path)

    ass, saved = _render(pipeline, monkeypatch, tmp_path, ON)

    assert ass == plain and "speaker_turns" not in saved
    assert "caption colour skipped: no audio stream" in capsys.readouterr().out


def test_cancelling_the_video_is_not_swallowed_as_a_failure(pipeline, monkeypatch, tmp_path, heard):
    from core import cancel

    def cancelled():
        raise cancel.CancelledError("v1")

    heard.answer = cancelled
    with pytest.raises(cancel.CancelledError):
        _render(pipeline, monkeypatch, tmp_path, ON)


def test_a_piece_sent_by_another_pc_uses_the_turns_that_came_with_it(pipeline, monkeypatch, tmp_path, heard):
    """A render worker gets a cut of the video: who its main speaker is can't
    be heard from that, so the PC that sent it listened first."""
    piece = tmp_path / "remote_render" / "jobs" / "j1" / "piece.mp4"
    piece.parent.mkdir(parents=True)
    piece.write_bytes(b"piece")

    ass, saved = _render(pipeline, monkeypatch, tmp_path, ON, opts={"speaker_turns": TURNS}, source=piece)
    assert heard.calls == 0 and saved["speaker_turns"] == TURNS
    assert f"{{\\1c{SECOND}}}W24 W25 W26" in _events(ass)

    ass, saved = _render(pipeline, monkeypatch, tmp_path, ON, source=piece)      # none came: plain
    assert heard.calls == 0 and "speaker_turns" not in saved and "\\1c" not in ass


def test_the_pc_that_sends_a_clip_out_listens_first(pipeline, monkeypatch, tmp_path, heard):
    """remote_render/dispatch.py asks the same two questions before a job leaves."""
    source = tmp_path / "downloads" / "v1.mp4"
    source.parent.mkdir()
    source.write_bytes(b"video")
    config = {"clips": {"caption_style": ON}, "paths": {"data_dir": str(tmp_path)}}

    assert pipeline._wants_second_speaker(config, {})
    assert not pipeline._wants_second_speaker(config, {"captions": False})
    assert not pipeline._wants_second_speaker(config, {"caption_style": {"color": "#FF0000"}})
    assert not pipeline._wants_second_speaker({**config, "clips": {}}, {})
    assert pipeline._speaker_turns(source, CLIP, _segments(), config, {}) == TURNS


# ---- fixed by hand ------------------------------------------------------------------
# The editor's Fix speakers saves what a person said about who is talking
# (speaker_edits). It is laid over what the render hears, and kept apart from it.


def test_a_hand_fix_colours_a_clip_the_render_heard_nobody_else_in(pipeline, monkeypatch, tmp_path, heard):
    heard.answer = lambda: []
    fix = [[2.0, 4.0, 1]]

    ass, saved = _render(pipeline, monkeypatch, tmp_path, ON, opts={"speaker_edits": fix})

    assert _events(ass)[:5] == [
        "W20 W21 W22", "W23", f"{{\\1c{SECOND}}}W24 W25 W26", f"{{\\1c{SECOND}}}W27", "W28 W29 W30",
    ]
    assert saved["speaker_edits"] == fix and "speaker_turns" not in saved     # heard: nobody


def test_a_hand_fix_overrules_what_was_heard_and_what_was_heard_is_saved_as_heard(
        pipeline, monkeypatch, tmp_path, heard):
    fix = [[2.0, 3.0, 0], [5.0, 6.0, 1]]      # w24-w25 are the main speaker after all; w30-w31 are not

    ass, saved = _render(pipeline, monkeypatch, tmp_path, ON, opts={"speaker_edits": fix})

    assert _events(ass)[:6] == [
        "W20 W21 W22", "W23 W24 W25", f"{{\\1c{SECOND}}}W26 W27", "W28 W29",
        f"{{\\1c{SECOND}}}W30 W31", "W32 W33",
    ]
    assert saved["speaker_turns"] == TURNS and saved["speaker_edits"] == fix


def test_with_the_option_off_a_hand_fix_stays_saved_and_does_nothing(pipeline, monkeypatch, tmp_path, heard):
    plain, _ = _render(pipeline, monkeypatch, tmp_path)

    ass, saved = _render(pipeline, monkeypatch, tmp_path, opts={"speaker_edits": [[2.0, 4.0, 1]]})

    assert ass == plain and heard.calls == 0
    assert saved["speaker_edits"] == [[2.0, 4.0, 1]]        # there when the option is ticked again


def test_a_mark_left_on_a_saved_line_colours_nothing_when_nobody_else_is_heard(
        pipeline, monkeypatch, tmp_path, heard):
    """An earlier render's mark, saved with the line: it used to burn."""
    heard.answer = lambda: []
    lines = [{"start": 0.0, "end": 2.0, "text": "what was said", "speaker": 1},
             {"start": 2.0, "end": 4.0, "text": "and the answer"}]

    ass, _ = _render(pipeline, monkeypatch, tmp_path, ON, opts={"caption_lines": lines})

    assert _events(ass) == ["WHAT WAS SAID", "AND THE ANSWER"]


def test_lines_the_editor_sends_with_a_fix_burn_as_the_editor_showed_them(pipeline, monkeypatch, tmp_path, heard):
    """A word muted in the same visit: the editor sends its lines, ending
    where the speaker changes, and the fix beside them. A caption made of the
    other speaker's words either side of a pause is still theirs."""
    from video.captions import build_caption_lines, paint_turns

    fix = [[5.0, 5.5, 1]]                           # w30, by hand; w34-w35 were heard
    heard.answer = lambda: [[7.0, 8.0, 1]]
    shown = build_caption_lines(_gapped(), CLIP, 3, paint_turns([[7.0, 8.0, 1]], fix))
    assert [(line["text"], line.get("speaker")) for line in shown] == [
        ("w20 w21 w22", None), ("w23 w24 w25", None), ("w26 w27 w28", None), ("w29", None),
        ("w30 w34 w35", 1), ("w36 w37 w38", None), ("w39", None),
    ]
    sent = [{k: v for k, v in line.items() if k != "speaker"} for line in shown]

    ass, _ = _render(pipeline, monkeypatch, tmp_path, ON, opts={"caption_lines": sent, "speaker_edits": fix},
                     segments=_gapped())

    assert _events(ass)[4] == f"{{\\1c{SECOND}}}W30 W34 W35"
    assert [e for e in _events(ass) if "\\1c" in e] == [f"{{\\1c{SECOND}}}W30 W34 W35"]


def test_a_hand_fix_marks_saved_lines_whole(pipeline, monkeypatch, tmp_path, heard):
    heard.answer = lambda: []
    lines = [{"start": 0.0, "end": 2.0, "text": "what was said"},
             {"start": 2.0, "end": 4.0, "text": "and the answer"}]

    ass, _ = _render(pipeline, monkeypatch, tmp_path, ON,
                     opts={"caption_lines": lines, "speaker_edits": [[2.0, 4.0, 1]]})

    assert _events(ass) == ["WHAT WAS SAID", f"{{\\1c{SECOND}}}AND THE ANSWER"]


# ---- what the clip keeps ------------------------------------------------------------


def _library(db, tmp_path, render_opts: dict) -> int:
    """One video with one clip in the database and its files on disk."""
    db.conn.execute(
        "INSERT INTO videos (video_id, title, status, created_at, updated_at) "
        "VALUES ('v1', 't', 'done', '2026-01-01', '2026-01-01')"
    )
    db.conn.execute(
        "INSERT INTO clips (video_id, start_s, end_s, score, render_opts, created_at) "
        "VALUES ('v1', 10.0, 20.0, 90, ?, '2026-01-01')",
        (json.dumps(render_opts),),
    )
    db.conn.commit()
    (tmp_path / "downloads").mkdir(exist_ok=True)
    (tmp_path / "downloads" / "v1.mp4").write_bytes(b"source")
    (tmp_path / "transcripts").mkdir(exist_ok=True)
    seg = _segments()[0]
    (tmp_path / "transcripts" / "v1.json").write_text(
        json.dumps({"segments": [{"start": seg.start, "end": seg.end, "text": seg.text, "words": seg.words}]}),
        encoding="utf-8",
    )
    return db.conn.execute("SELECT id FROM clips").fetchone()["id"]


def _saved(db) -> dict:
    return json.loads(db.conn.execute("SELECT render_opts FROM clips").fetchone()["render_opts"])


@pytest.mark.parametrize(("before", "rendered", "after"), [
    (None, TURNS, TURNS),                      # first render with the option on
    ([[0.5, 1.5, 1]], TURNS, TURNS),           # re-rendered: this render's, not the last one's
    ([[0.5, 1.5, 1]], None, None),             # option off now, or one voice: none left behind
])
def test_an_editor_rerender_leaves_the_clip_with_the_turns_of_that_render(
        pipeline, monkeypatch, db, tmp_path, before, rendered, after):
    from server.jobs import Worker

    opts = {"caption_style": ON, **({"speaker_turns": before} if before else {})}
    clip_id = _library(db, tmp_path, opts)

    def fake_render(_source, _candidate, _segments, clip_dir, _config, render_opts, _lang):
        clip_dir.mkdir(parents=True, exist_ok=True)
        (clip_dir / "clip.mp4").write_bytes(b"clip")
        out = {k: v for k, v in render_opts.items() if k != "speaker_turns"}
        return clip_dir / "clip.mp4", json.dumps({**out, **({"speaker_turns": rendered} if rendered else {})})

    monkeypatch.setattr(pipeline, "_render_files", fake_render)
    job = SimpleNamespace(config={"paths": {"data_dir": str(tmp_path)}, "clips": {"outro": False}})

    Worker._rerender_clip(job, db, {"clip_id": clip_id})

    assert _saved(db).get("speaker_turns") == after
    assert _saved(db)["caption_style"] == ON


@pytest.mark.parametrize(("before", "rendered", "after"), [
    (None, TURNS, TURNS),
    ([[0.5, 1.5, 1]], TURNS, TURNS),
    ([[0.5, 1.5, 1]], None, None),
])
def test_processing_the_video_again_does_the_same_for_a_clip_it_already_has(
        pipeline, db, tmp_path, before, rendered, after):
    from analysis.metadata import ClipMetadata

    _library(db, tmp_path, {"crop": "track", **({"speaker_turns": before} if before else {})})
    fresh = json.dumps({"caption_style": ON, **({"speaker_turns": rendered} if rendered else {})})

    pipeline._register_clip(db, "v1", ClipCandidate(start=10.0, end=20.0, score=91), tmp_path / "new.mp4",
                            ClipMetadata(title="t", description="", hashtags=[]), fresh,
                            {"paths": {"data_dir": str(tmp_path)}, "clips": {"outro": False}})

    assert _saved(db).get("speaker_turns") == after
    assert _saved(db)["crop"] == "track"        # the rest of what the clip had stays


def _rerender(pipeline, monkeypatch, db, tmp_path, payload: dict) -> dict:
    """One editor re-render with the render itself stood in for. Returns the
    options that render was handed."""
    from server.jobs import Worker

    handed = {}

    def fake_render(_source, candidate, _segments, clip_dir, _config, render_opts, _lang):
        handed.update(json.loads(json.dumps(render_opts)), window=(candidate.start, candidate.end))
        clip_dir.mkdir(parents=True, exist_ok=True)
        (clip_dir / "clip.mp4").write_bytes(b"clip")
        return clip_dir / "clip.mp4", json.dumps(render_opts)

    monkeypatch.setattr(pipeline, "_render_files", fake_render)
    job = SimpleNamespace(config={"paths": {"data_dir": str(tmp_path)}, "clips": {"outro": False}})
    clip_id = db.conn.execute("SELECT id FROM clips").fetchone()["id"]
    Worker._rerender_clip(job, db, {"clip_id": clip_id, **payload})
    return handed


@pytest.mark.parametrize("saved_lines", [False, True])
def test_a_hand_fix_stays_on_its_words_when_the_clips_start_moves(
        pipeline, monkeypatch, db, tmp_path, saved_lines):
    """The clip is 10-20 and w24-w27 (2.0-4.0 of it) were said to be the other
    speaker. Trimmed to start at 13, that stretch begins a second before the
    clip does; given its start back, the fix is where it was."""
    opts = {"caption_style": ON, "speaker_edits": [[2.0, 4.0, 1]]}
    if saved_lines:
        opts["caption_lines"] = [{"start": 2.0, "end": 4.0, "text": "the answer"}]
    _library(db, tmp_path, opts)

    handed = _rerender(pipeline, monkeypatch, db, tmp_path, {"start": 13.0})
    assert handed["window"] == (13.0, 20.0)
    assert handed["speaker_edits"] == [[-1.0, 1.0, 1]] and _saved(db)["speaker_edits"] == [[-1.0, 1.0, 1]]

    handed = _rerender(pipeline, monkeypatch, db, tmp_path, {"start": 10.0})
    assert handed["speaker_edits"] == [[2.0, 4.0, 1]] and _saved(db)["speaker_edits"] == [[2.0, 4.0, 1]]

    handed = _rerender(pipeline, monkeypatch, db, tmp_path, {"end": 25.0})      # the end alone: nothing moves
    assert handed["speaker_edits"] == [[2.0, 4.0, 1]]


def test_the_editor_sends_its_fixes_whole_and_an_empty_list_means_none(pipeline, monkeypatch, db, tmp_path):
    _library(db, tmp_path, {"caption_style": ON, "speaker_edits": [[2.0, 4.0, 1]]})

    handed = _rerender(pipeline, monkeypatch, db, tmp_path, {"render_opts": {"speaker_edits": [[5.0, 6.0, 0]]}})
    assert handed["speaker_edits"] == [[5.0, 6.0, 0]] and _saved(db)["speaker_edits"] == [[5.0, 6.0, 0]]

    handed = _rerender(pipeline, monkeypatch, db, tmp_path, {"render_opts": {"crop": "center"}})   # not sent: kept
    assert _saved(db)["speaker_edits"] == [[5.0, 6.0, 0]] and _saved(db)["crop"] == "center"

    handed = _rerender(pipeline, monkeypatch, db, tmp_path, {"render_opts": {"speaker_edits": []}})
    assert "speaker_edits" not in handed and "speaker_edits" not in _saved(db)


# ---- the app's own roads ------------------------------------------------------------


@pytest.fixture
def api(db, tmp_path):
    """The API over the same data folder as `db`, with no worker running."""
    pytest.importorskip("fastapi")
    pytest.importorskip("httpx")
    pytest.importorskip("yaml")
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


def test_the_editor_is_given_the_words_the_render_goes_by(api, db, tmp_path):
    from video.captions import words_of

    clip_id = _library(db, tmp_path, {})
    db.conn.execute("UPDATE clips SET start_s = 10.37, end_s = 20.21")
    db.conn.commit()

    words = api.get(f"/clips/{clip_id}/words").json()["words"]

    assert words == words_of(_segments(), ClipCandidate(start=10.37, end=20.21, score=0))
    assert words[0] == {"start": 0.0, "end": 0.13, "word": "w20"} and words[-1]["end"] == 9.84


def test_the_editor_and_the_render_go_by_the_same_words_in_an_odd_transcript(api, db, tmp_path):
    """A word that runs on past the end of its own segment, into the clip, and
    a segment with no word timings (its text is spread out evenly for the
    captions; nobody can click those words). The render marks saved lines by
    the words the editor is given: those, and no others."""
    from video.captions import words_of

    clip_id = _library(db, tmp_path, {})
    segments = [
        {"start": 8.0, "end": 9.8, "text": "a b",
         "words": [{"start": 8.0, "end": 9.0, "word": "a"}, {"start": 9.0, "end": 10.6, "word": "b"}]},
        {"start": 11.0, "end": 13.0, "text": "nobody timed these", "words": None},
        {"start": 13.0, "end": 14.0, "text": "c d",
         "words": [{"start": 13.0, "end": 13.5, "word": "c"}, {"start": 13.5, "end": 14.0, "word": "d"}]},
    ]
    (tmp_path / "transcripts" / "v1.json").write_text(json.dumps({"segments": segments}), encoding="utf-8")

    words = api.get(f"/clips/{clip_id}/words").json()["words"]

    assert words == words_of([Segment(**s) for s in segments], CLIP)
    assert [(w["word"], w["start"], w["end"]) for w in words] == [("b", 0.0, 0.6), ("c", 3.0, 3.5), ("d", 3.5, 4.0)]


def test_the_caption_lines_other_screens_read_stay_in_the_plain_grouping(api, db, tmp_path):
    """The translation review pairs these row by row with lines the translate
    job builds without turns; broken at speaker changes they would slip."""
    from video.captions import build_caption_lines

    clip_id = _library(db, tmp_path, {"caption_style": ON, "speaker_turns": TURNS, "speaker_edits": [[5.0, 5.5, 1]]})

    assert api.get(f"/clips/{clip_id}/captions").json()["lines"] == build_caption_lines(_segments(), CLIP, 3)


@pytest.mark.parametrize("on", [True, False])
def test_the_ai_edit_is_given_a_two_voice_clips_captions_as_they_burn(api, db, tmp_path, monkeypatch, on):
    """It saves every line it is given, and saved lines keep their grouping
    and take one colour each: given the plain grouping, a caption fix moved
    words of both people into one caption and one colour."""
    import analysis.clip_edit as clip_edit
    import core.pipeline as pipeline_mod
    import llm.registry as registry
    from video.captions import build_caption_lines, paint_turns

    opts = {"speaker_turns": TURNS, "speaker_edits": [[5.0, 5.5, 1]], **({"caption_style": ON} if on else {})}
    clip_id = _library(db, tmp_path, opts)
    given = {}

    def fake_interpret(_message, *, clip_state, caption_lines, source_duration, llm):
        given["lines"] = caption_lines
        return {"reply": "ok", "needs_render": False, "start": None, "end": None, "render_opts": None}

    monkeypatch.setattr(clip_edit, "interpret_edit", fake_interpret)
    monkeypatch.setattr(pipeline_mod, "_with_usable_model", lambda llm: llm)
    monkeypatch.setattr(registry, "create_backend", lambda _cfg: object())

    assert api.post(f"/clips/{clip_id}/ai-edit", json={"message": "fix caption 1"}).json()["reply"] == "ok"

    plain = build_caption_lines(_segments(), CLIP, 3)
    if not on:
        assert given["lines"] == plain          # the option off: exactly as before
        return
    burned = build_caption_lines(_segments(), CLIP, 3, paint_turns(TURNS, opts["speaker_edits"]))
    assert given["lines"] == [{k: v for k, v in line.items() if k != "speaker"} for line in burned]
    assert given["lines"] != plain and all("speaker" not in line for line in given["lines"])


def test_update_preview_burns_the_fixes_still_pending_in_the_editor(api, db, tmp_path, monkeypatch):
    """The preview takes only the fields it names: without one for these, the
    draft showed the clip without the fix and called it the export."""
    import core.pipeline as pipeline_mod

    clip_id = _library(db, tmp_path, {"caption_style": ON, "speaker_edits": [[2.0, 4.0, 1]]})
    handed = []

    def fake_render(_source, _candidate, _segments, clip_dir, _config, render_opts, _lang):
        handed.append(render_opts.get("speaker_edits"))
        clip_dir.mkdir(parents=True, exist_ok=True)
        (clip_dir / "draft.mp4").write_bytes(b"clip")
        return clip_dir / "draft.mp4", ""

    monkeypatch.setattr(pipeline_mod, "_render_files", fake_render)

    for body in ({}, {"speaker_edits": None}, {"speaker_edits": [[5.0, 6.0, 0]]}, {"speaker_edits": []}):
        assert api.post(f"/clips/{clip_id}/preview", json={"edit": None, **body}).status_code == 200

    assert handed == [[[2.0, 4.0, 1]], [[2.0, 4.0, 1]], [[5.0, 6.0, 0]], []]      # kept, kept, replaced, none
    assert _saved(db)["speaker_edits"] == [[2.0, 4.0, 1]]                           # a preview saves nothing


def test_a_deleted_videos_voices_count_with_its_transcript_as_leftovers(db, tmp_path):
    from core import housekeeping

    _library(db, tmp_path, {})
    (tmp_path / "voice_profiles").mkdir()
    (tmp_path / "voice_profiles" / "v1.json").write_text("{}", encoding="utf-8")
    (tmp_path / "voice_profiles" / "gone.json").write_text("{}", encoding="utf-8")

    found = housekeeping.survey(db, tmp_path)

    assert [f.name for f in found["_groups"]["orphan_transcripts"]] == ["gone.json"]
