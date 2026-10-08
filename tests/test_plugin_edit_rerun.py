"""A run that makes a clip again keeps the creator's edits (D33).

When a run finds a window that already has a clip, and the creator made
that clip again by hand (its saved options hold something only a person
sets on one clip), the run renders it with what they saved, as "Make it
again with my edits" does, less what the job decides for every clip. A
Sports job keeps its own look. Every other clip, and every new window,
renders with exactly the job's options, as before (_clip_opts(meta)).

These tests run process_video and Longform's clip mode with download,
transcription, analysis, the title writer and the render stubbed out, on
throwaway databases, rendering here or on a stand-in render PC, with a
match's story reels spied. The suggestion data is shared with
tests/test_plugin_edit_suggestions.py (tests/fixtures/edit_marks). Every
game named is Quarkbloom Arena, a made-up game.
"""

import copy
import hashlib
import json
from functools import partial
from pathlib import Path
from types import SimpleNamespace

import pytest

from core.models import ClipCandidate, DownloadedVideo

ROOT = Path(__file__).resolve().parent.parent
CASES = ROOT / "tests" / "fixtures" / "edit_marks"

pytest.importorskip("yaml")


def _shared(name: str) -> dict:
    return {c["name"]: c for c in json.loads((CASES / f"{name}.json").read_text(encoding="utf-8"))["cases"]}


_RENDER = _shared("after_render")
# A clip's saved options with a suggestion used: its cut, mute, hidden word, fade, hook title and layout.
WITH_IT = _RENDER["used_keeps_only_what_the_render_changed"]["after"]
# That suggestion once used.
USED = _RENDER["take_it_back"]["entries"][0]

# A split set up before processing, as a first run saves it on every clip it makes (gaming/run.py render()).
SETUP = {"by": "user", "cam": [0.7, 0, 0.3, 0.3], "preset": "split", "panels": [], "layout": "split",
         "used_preset": "split", "used_cam": [0.7, 0, 0.3, 0.3]}
# A first run's snapshot of the job's look, in a Highlights job.
LOOK = {"caption_style": {"font": "Georgia", "post_style": "highlights"}, "filter": "warm",
        "watermark": {"text": "@quarkbloom"}, "headline": "OLD", "subline": "QUARKBLOOM"}
HIGHLIGHTS = {"caption_style": {"post_style": "highlights"}}
SOCCER = {"sport": {"name": "soccer"}}
# Two of the creator's own cuts.
E = {"keep": [[0, 12], [14, 20]], "fade_out": 0.3}
E2 = {"keep": [[0, 8]], "speed": 1.5}
# The creator's whole hand edit of one clip, look and card words included.
HAND = {"edit": WITH_IT["edit"], "crop": "center",
        "caption_lines": [{"start": 0.0, "end": 2.0, "text": "Quark burst!"}],
        "caption_style": {"font": "Georgia", "post_style": "highlights"}, "filter": "warm",
        "adjust": {"brightness": 0.1, "saturation": 1.2}, "watermark": {"text": "@quarkbloom"},
        "headline": "MY CARD", "subline": "MINE"}
STARTS = (10.0, 100.0, 200.0, 300.0)
EDITED_LINE = "1 clip(s) you edited are made again with your edits"
SPORTS_NOTE = "(caption style, colours, branding and title card as this job sets them)"


def _cand(start, score=80, length=20.0):
    return ClipCandidate(start=float(start), end=float(start) + length, score=score, hook=f"Moment at {start:g}",
                         reason="loud reaction")


def _found(starts=STARTS, length=20.0):
    return [_cand(s, 90 - i, length) for i, s in enumerate(starts)]


def _card(start: float) -> dict:
    """The Highlights card _clip_opts(meta) gives the clip at `start`."""
    return {"headline": f"AT {start:g}", "subline": "QUARKBLOOM"}


def _parsed(raw):
    return json.loads(raw) if raw else None


@pytest.fixture
def rerun(monkeypatch, tmp_path, capsys):
    """process_video from finding to the clip rows, with download,
    transcription, analysis, the title writer and the render stubbed out.

    `rerun(found, rows=[(start, end, opts[, columns])], fresh=split, **clips)`
    seeds a fresh database with the clip rows a video already has, then runs
    a forced job on it whose finder finds `found`. A Gaming job's webcam
    search gives `fresh`. It returns the clip rows by window (render_opts and
    scores parsed), each render as ((start, end), the options it was given)
    in window order, what the run printed, and `again(**clips)`, which runs
    the same job once more on the same database."""
    pytest.importorskip("numpy")
    pytest.importorskip("cv2")
    pytest.importorskip("PIL")  # the end card (video/outro.py)
    import core.pipeline as pipeline
    from analysis.metadata import ClipMetadata
    from core.state import StateDB
    from main import BUNDLED_CONFIG, load_config

    source = tmp_path / "match.mp4"
    source.write_bytes(b"not really a video")
    video = DownloadedVideo(video_id="vid321", title="A Quarkbloom Arena match", path=source, duration=600.0)
    monkeypatch.setattr(pipeline, "_cached_or_download", lambda *_a, **_k: video)
    monkeypatch.setattr(pipeline, "transcribe", lambda *_a, **_k: [])
    monkeypatch.setattr("transcription.transcriber.detected_language", lambda *_a, **_k: "en")
    monkeypatch.setattr("video.encoding.source_codec", lambda _p: "h264")
    monkeypatch.setattr("analysis.audio_features.extract_audio_features", lambda _p: {})
    monkeypatch.setattr("analysis.visual_features.extract_visual_features", lambda _p: {})
    monkeypatch.setattr("analysis.hype.audience_signals", lambda *_a, **_k: (None, None))
    monkeypatch.setattr(pipeline, "_with_usable_model", lambda cfg: cfg)
    monkeypatch.setattr(pipeline, "create_backend", lambda cfg: object())
    monkeypatch.setattr(pipeline, "clip_direction", lambda *_a, **_k: None)
    monkeypatch.setattr(pipeline, "_share_the_cpu", lambda workers: None)
    # Gaming / Reaction: the webcam search this run makes once per video.
    job: dict = {}
    monkeypatch.setattr(pipeline, "_gaming_prepare", lambda *_a, **_k: copy.deepcopy(job["split"]))
    monkeypatch.setattr(pipeline, "_gaming_scoring_inputs", lambda *_a, **_k: (None, None, None))

    # Sports: a match read with nothing found, filmed 16:9.
    class Reading:
        def __init__(self, config, video):
            self.config = config

        def finish(self, hype_out=None):
            return None, None, None

    monkeypatch.setattr(pipeline, "MatchReading", Reading)
    monkeypatch.setattr(pipeline, "_listening_for", lambda *_a, **_k: None)
    monkeypatch.setattr("core.modes.probe_size", lambda _p: (1920, 1080))

    found: list = []
    renders: list = []
    monkeypatch.setattr(pipeline, "find_clips", lambda *_a, **_k: ([copy.deepcopy(c) for c in found], []))

    def titles(candidates, *_a, **_k):
        return [ClipMetadata(title=f"Clip at {c.start:g}", description="", hashtags=[],
                             headline=f"AT {c.start:g}", subline="QUARKBLOOM") for c in candidates]

    def render(source, candidate, segments, clip_dir, config, render_opts=None, content_language="en"):
        """What the real render saves, as far as _register_clip reads it: the
        options it was given, with the caption style it resolved."""
        renders.append(((candidate.start, candidate.end), copy.deepcopy(render_opts)))
        clip_dir.mkdir(parents=True, exist_ok=True)
        out = clip_dir / f"clip_{int(candidate.start):05d}-{int(candidate.end):05d}.mp4"
        out.write_bytes(b"a clip")
        style = (render_opts or {}).get("caption_style") or config["clips"].get("caption_style")
        saved = {**(render_opts or {}), **({"caption_style": style} if style else {})}
        return out, json.dumps(saved) if saved else ""

    monkeypatch.setattr(pipeline, "generate_metadata_batch", titles)
    monkeypatch.setattr(pipeline, "_render_files", render)
    databases: list = []

    def process(db, moments, fresh, clips):
        config = load_config(BUNDLED_CONFIG)
        config["paths"]["data_dir"] = str(tmp_path / "data")
        config["clips"].update({"captions": False, "min_duration": 10, "max_duration": 60, "min_score": 55,
                                "max_clips_per_video": 0, **clips})
        found[:] = moments
        renders.clear()
        job["split"] = {"gaming": fresh if fresh is not None else {}}
        capsys.readouterr()
        pipeline.process_video("local:stream", config, db, force=True)
        out = capsys.readouterr().out
        rows = {}
        for r in db.conn.execute("SELECT start_s, end_s, title, status, path, scores, render_opts FROM clips"
                                 " WHERE video_id = 'vid321'"):
            rows[(r["start_s"], r["end_s"])] = {**dict(r), "scores": _parsed(r["scores"]),
                                                "render_opts": _parsed(r["render_opts"])}
        return SimpleNamespace(rows=rows, renders=sorted(renders, key=lambda x: x[0]), out=out,
                               again=lambda **more: process(db, moments, fresh, {**clips, **more}))

    def run(moments, rows=(), fresh=None, **clips):
        db = StateDB(tmp_path / f"state-{len(databases)}.db")
        databases.append(db)
        db.upsert_video("vid321", title="A Quarkbloom Arena match", channel_name="Quarkbloom Arena")
        for start, end, opts, *more in rows:
            columns = {"path": f"/old/clip_{int(start):05d}-{int(end):05d}.mp4", "status": "queued",
                       "title": "Creator title", "scores": json.dumps({"text": 61}),
                       "render_opts": json.dumps(opts), **(more[0] if more else {})}
            db.add_clip("vid321", start, end, 70, "h", **columns)
        return process(db, moments, fresh, clips)

    yield run
    for db in databases:
        db.conn.close()


# ---- what renders as before ---------------------------------------------------------------


def test_a_window_with_no_row_renders_with_exactly_the_jobs_options(rerun):
    """The pin: a window with no clip yet renders with _clip_opts(meta), byte
    for byte, whatever the video's other clips hold."""
    import core.pipeline as pipeline

    fresh = {"by": "video", "cam": [0.1, 0.1, 0.2, 0.2]}
    elsewhere = [(500.0, 520.0, HAND)]
    for clips, options in (({}, [None] * 4),
                           (HIGHLIGHTS, [_card(s) for s in STARTS]),
                           ({"gaming": True}, [{"gaming": fresh}] * 4)):
        got = rerun(_found(), rows=elsewhere, fresh=fresh, **clips)
        assert json.dumps(got.renders) == json.dumps([((s, s + 20.0), o) for s, o in zip(STARTS, options)])
        assert got.rows[(500.0, 520.0)]["render_opts"] == HAND
        assert got.rows[(500.0, 520.0)]["path"] == "/old/clip_00500-00520.mp4"
        assert "you edited" not in got.out
    job_opts = {"gaming": fresh}
    assert pipeline._with_choices(job_opts, None) is job_opts
    assert pipeline._with_choices(None, {}) is None


def test_clips_nobody_edited_render_as_the_job_says_after_a_set_up_split(rerun):
    """A split set up before processing is saved as "user" on every clip the
    first run made, with that run's look. On its own it is not an edit: a
    re-run renders those clips with the job's options, in a Gaming job, with
    Gaming off and in a Soccer job, for a remembered split too."""
    fresh = {"by": "user", "cam": [0.5, 0.5, 0.2, 0.2], "preset": "split", "panels": []}
    for split in (SETUP, {**SETUP, "by": "creator"}):
        rows = [(s, s + 20.0, {**LOOK, "gaming": split}) for s in STARTS]
        for clips, options in (({"gaming": True, **HIGHLIGHTS}, [{"gaming": fresh, **_card(s)} for s in STARTS]),
                               (HIGHLIGHTS, [_card(s) for s in STARTS]),
                               ({**SOCCER, **HIGHLIGHTS}, [_card(s) for s in STARTS])):
            got = rerun(_found(), rows=rows, fresh=fresh, **clips)
            assert got.renders == [((s, s + 20.0), o) for s, o in zip(STARTS, options)], (split["by"], clips)
            assert "you edited" not in got.out
    # Two runs on the same library: the second renders as the first did.
    first = rerun(_found(), rows=[(s, s + 20.0, {**LOOK, "gaming": SETUP}) for s in STARTS], fresh=fresh,
                  gaming=True, **HIGHLIGHTS)
    second = first.again()
    assert json.dumps(second.renders) == json.dumps(first.renders)
    assert "you edited" not in second.out


def test_a_clip_nobody_edited_is_made_as_the_job_says(rerun):
    got = rerun(_found(), rows=[(100.0, 120.0, LOOK)], **HIGHLIGHTS)
    assert dict(got.renders)[(100.0, 120.0)] == _card(100.0)
    # Its Highlights card words are this run's, as before; the rest of its options stay.
    assert got.rows[(100.0, 120.0)]["render_opts"] == {**LOOK, "headline": "AT 100"}
    assert "you edited" not in got.out


# ---- what a clip the creator edited gets ----------------------------------------------------


def test_a_rerun_makes_a_clip_the_creator_edited_with_their_choices(rerun):
    got = rerun(_found(), rows=[(100.0, 120.0, HAND, {"status": "uploaded"})], **HIGHLIGHTS)
    renders = dict(got.renders)
    assert renders[(100.0, 120.0)] == {**_card(100.0), **HAND}
    assert [renders[(s, s + 20.0)] for s in (10.0, 200.0, 300.0)] == [_card(s) for s in (10.0, 200.0, 300.0)]
    row = got.rows[(100.0, 120.0)]
    assert row["render_opts"] == HAND
    assert (row["title"], row["status"]) == ("Creator title", "uploaded")
    assert Path(row["path"]).name == "clip_00100-00120.mp4" and row["path"] != "/old/clip_00100-00120.mp4"
    assert EDITED_LINE in got.out and SPORTS_NOTE not in got.out


def test_a_soccer_rerun_keeps_the_jobs_sport_framing_and_look(rerun):
    """Only what a person set on the clip alone reaches a match's file: the
    sport, Vertical Live, the split and the look are the job's."""
    saved = {**LOOK, "edit": E, "crop": "bias_left", "sport": "soccer", "vertical_live": True, "gaming": SETUP}
    got = rerun(_found(), rows=[(100.0, 120.0, saved)], **SOCCER)
    renders = dict(got.renders)
    assert renders[(100.0, 120.0)] == {"edit": E, "crop": "bias_left"}
    assert [renders[(s, s + 20.0)] for s in (10.0, 200.0, 300.0)] == [None] * 3
    assert got.rows[(100.0, 120.0)]["render_opts"] == saved
    assert f"{EDITED_LINE} {SPORTS_NOTE}" in got.out


def test_a_soccer_highlights_rerun_gives_an_edited_clip_this_runs_card(rerun):
    """In a match, an edited clip's file gets this run's card words and the
    job's caption style, colours and branding. The clip keeps the creator's
    saved ones: a run never replaces them."""
    a = {**LOOK, "edit": E}
    b = {**LOOK, "edit": E2, "headline": "MY CARD", "subline": "MINE"}
    got = rerun(_found(), rows=[(100.0, 120.0, a), (200.0, 220.0, b)], **SOCCER, **HIGHLIGHTS)
    renders = dict(got.renders)
    assert renders[(100.0, 120.0)] == {**_card(100.0), "edit": E}
    assert renders[(200.0, 220.0)] == {**_card(200.0), "edit": E2}
    assert got.rows[(100.0, 120.0)]["render_opts"] == a
    assert got.rows[(200.0, 220.0)]["render_opts"] == b
    assert f"2 clip(s) you edited are made again with your edits {SPORTS_NOTE}" in got.out


def test_a_gaming_rerun_keeps_an_edited_clips_split_and_finds_the_rest_again(rerun):
    fresh = {"by": "video", "cam": [0.1, 0.1, 0.2, 0.2]}
    old = {"by": "video", "cam": [0.0, 0.6, 0.3, 0.4], "preset": "small_cam", "order": "game_top"}
    rows = [(10.0, 30.0, {"edit": None, "gaming": old}),
            (100.0, 120.0, {"gaming": None, "crop": "center"}),
            (200.0, 220.0, {"gaming": SETUP}),
            (300.0, 320.0, {"gaming": {"by": "video", "cam": [0.2, 0.2, 0.2, 0.2]}})]
    got = rerun(_found(), rows=rows, fresh=fresh, gaming=True)
    assert got.renders == [((10.0, 30.0), {"gaming": old, "edit": None}),
                           ((100.0, 120.0), {"gaming": None, "crop": "center"}),
                           ((200.0, 220.0), {"gaming": fresh}),
                           ((300.0, 320.0), {"gaming": fresh})]
    assert got.rows[(10.0, 30.0)]["render_opts"] == {"edit": None, "gaming": old}
    assert got.rows[(100.0, 120.0)]["render_opts"] == {"gaming": None, "crop": "center"}
    assert got.rows[(200.0, 220.0)]["render_opts"]["gaming"] == fresh
    # Gaming off: a split a run found isn't the job's any more; one turned off stays off.
    plain = rerun(_found(), rows=rows)
    assert plain.renders == [((10.0, 30.0), {"edit": None}),
                             ((100.0, 120.0), {"gaming": None, "crop": "center"}),
                             ((200.0, 220.0), None), ((300.0, 320.0), None)]


@pytest.mark.parametrize("by", ["user", "creator", "video"])
def test_a_webcam_a_person_chose_for_the_job_reaches_the_clips_they_edited(rerun, by):
    """A webcam set up or remembered for this job reaches an edited clip
    whose split a run found or remembered, in that clip's own layout. One
    drawn for the clip, or a split turned off for it, stays. A webcam this
    run found itself (by "video") goes to the clips nobody edited only."""
    new, old, drawn = [0.5, 0.5, 0.2, 0.2], [0.0, 0.6, 0.3, 0.4], [0.6, 0.0, 0.4, 0.3]
    fresh = {"by": by, "cam": new, "preset": "half", "panels": []}
    found_split = {"by": "video", "cam": old, "preset": "small_cam", "order": "game_top"}
    remembered = {**SETUP, "by": "creator"}
    mine = {"by": "user", "cam": drawn, "preset": "split"}
    rows = [(10.0, 30.0, {"edit": None, "gaming": found_split}),
            (100.0, 120.0, {"edit": None, "gaming": remembered}),
            (200.0, 220.0, {"edit": None, "gaming": mine}),
            (300.0, 320.0, {"gaming": None, "crop": "center"}),
            (400.0, 420.0, {"gaming": {"by": "video", "cam": old}})]
    got = rerun(_found((10.0, 100.0, 200.0, 300.0, 400.0)), rows=rows, fresh=fresh, gaming=True)
    if by == "video":
        expected = [found_split, remembered, mine, None, fresh]
    else:
        expected = [{**found_split, "cam": new, "by": by}, {**remembered, "cam": new, "by": by}, mine, None, fresh]
    assert [opts["gaming"] for _w, opts in got.renders] == expected
    # The row keeps the split its file was rendered with.
    assert [got.rows[w]["render_opts"]["gaming"] for w, _o in got.renders] == expected


# ---- remade ------------------------------------------------------------------------------


def test_a_used_suggestion_is_not_marked_remade_when_the_file_has_the_edit(rerun):
    scores = json.dumps({"text": 61, "plugin_edits": [{**USED, "remade": True}]})
    for clips in ({}, SOCCER):
        got = rerun(_found((10.0, 100.0), length=30.0), rows=[(100.0, 130.0, WITH_IT, {"scores": scores})],
                    **clips)
        assert dict(got.renders)[(100.0, 130.0)] == WITH_IT
        assert got.rows[(100.0, 130.0)]["scores"]["plugin_edits"] == [USED]


def test_a_file_still_made_without_the_saved_edit_is_marked_remade(rerun, monkeypatch):
    """The edit was saved after the run read the clip's options (two
    processes on one library): the file lacks it, and the suggestion says so."""
    import core.pipeline as pipeline

    monkeypatch.setattr(pipeline, "_creator_choices", lambda *_a, **_k: {})
    scores = json.dumps({"text": 61, "plugin_edits": [USED]})
    got = rerun(_found((10.0, 100.0), length=30.0), rows=[(100.0, 130.0, WITH_IT, {"scores": scores})])
    assert dict(got.renders)[(100.0, 130.0)] is None
    assert got.rows[(100.0, 130.0)]["scores"]["plugin_edits"] == [{**USED, "remade": True}]
    assert got.rows[(100.0, 130.0)]["render_opts"] == WITH_IT


def test_a_trimmed_clip_is_left_as_it_was(rerun):
    """A clip the creator trimmed is another window (R4, unchanged): the
    run's window renders as the job says and gets a row of its own."""
    got = rerun(_found(), rows=[(102.0, 118.0, {"edit": {"fade_in": 1.0}})])
    assert dict(got.renders)[(100.0, 120.0)] is None
    assert got.rows[(102.0, 118.0)]["render_opts"] == {"edit": {"fade_in": 1.0}}
    assert got.rows[(102.0, 118.0)]["path"] == "/old/clip_00102-00118.mp4"
    assert got.rows[(100.0, 120.0)]["title"] == "Clip at 100" and got.rows[(100.0, 120.0)]["render_opts"] is None
    assert "you edited" not in got.out


# ---- the render, reels and Longform -------------------------------------------------------


class _RenderPC:
    """A render PC that takes every clip, called as remote_render/dispatch.py's
    render_all is. It notes the options each clip is sent with (opts_for, or
    the job's without it, as dispatch does), and the stubbed render makes it."""

    def __init__(self):
        self.job_opts = "not called"
        self.sent: dict = {}

    def renderer_for(self, _config):
        return self

    def render_all(self, _video_id, source, items, segments, clip_dir, config, render_opts, language, _workers,
                   opts_for=None):
        import core.pipeline as pipeline

        self.job_opts = render_opts
        for candidate, meta in items:
            opts = opts_for(candidate, meta) if opts_for else render_opts
            self.sent[(round(candidate.start, 2), round(candidate.end, 2))] = opts
            yield candidate, meta, partial(pipeline._render_files, source, candidate, segments, clip_dir, config,
                                           opts, language)


def _match_with_reels(monkeypatch) -> list:
    """A match read with story reels asked for, and the reels spied: for
    each call, the length each clip was made, by its window's start."""
    pytest.importorskip("numpy")
    pytest.importorskip("cv2")
    import core.pipeline as pipeline

    class Reading:
        def __init__(self, config, video):
            self.config = config

        def finish(self, hype_out=None):
            return SimpleNamespace(option={"reels": ["recap"]}), None, None

    asked: list = []

    def reels(_db, _video_id, _profile, clips, *_a, made_seconds=None, **_k):
        asked.append({round(c.candidate.start, 2): round((made_seconds or {}).get(str(c.path), -1.0), 6)
                      for c in clips})
        return []

    monkeypatch.setattr(pipeline, "MatchReading", Reading)
    monkeypatch.setattr(pipeline, "_sport_reels", reels)
    return asked



def test_a_split_turned_off_for_a_clip_stays_off_in_a_gaming_job(monkeypatch, tmp_path):
    pytest.importorskip("numpy")
    pytest.importorskip("cv2")
    import core.pipeline as pipeline
    from video import cropper, tracker

    ran: list = []
    tried: list = []

    def standard(_i, _t, out, **_k):
        ran.append("standard")
        Path(out).write_bytes(b"std")

    def gaming(*_a, **_k):
        tried.append("gaming")
        return None     # declined: the standard renderer takes it

    monkeypatch.setattr(pipeline, "cut_clip", lambda _s, _c, out, **_k: Path(out).write_bytes(b"clip"))
    monkeypatch.setattr(tracker, "compute_tracking", lambda *_a, **_k: {"mode": "track", "path": [(0.0, 0.5)]})
    monkeypatch.setattr(cropper, "render_vertical", standard)
    monkeypatch.setattr(pipeline, "_try_gaming_render", gaming)
    config = {"clips": {"captions": False, "outro": False, "vertical": True, "gaming": True},
              "paths": {"data_dir": str(tmp_path)}, "tracking": {"detector": "yolov8n-pose.pt", "sample_fps": 8}}

    def render(opts):
        _final, saved = pipeline._render_files(tmp_path / "s.mp4", ClipCandidate(start=10, end=40, score=80),
                                               [], tmp_path / "clips", config, opts)
        return json.loads(saved) if saved else {}

    off = render({"gaming": None, "crop": "center"})
    assert tried == [] and ran == ["standard"]
    assert "gaming" in off and off["gaming"] is None and off["crop"] == "center"
    # The failed search's {} isn't a split turned off: the job's Gaming is tried.
    render({"gaming": {}})
    assert tried == ["gaming"] and ran == ["standard", "standard"]


def test_a_story_reel_trims_an_edited_clips_card_by_its_made_length(monkeypatch, tmp_path, db):
    pytest.importorskip("numpy")
    pytest.importorskip("cv2")
    import core.pipeline as pipeline
    from sports.core import reels
    from video import outro

    asked: list = []

    def has_outro(path, seconds):
        asked.append((path, seconds))
        return True

    monkeypatch.setattr(outro, "has_outro", has_outro)
    monkeypatch.setattr(outro, "enabled", lambda _config: False)
    monkeypatch.setattr(reels, "join", lambda paths, _out, _trims: [9.0 for _p in paths])
    monkeypatch.setattr(reels, "plan", lambda clips, *_a, **_k: [
        SimpleNamespace(kind="recap", subject="", title="Match recap", parts=list(clips))])
    db.upsert_video("vid", title="A Quarkbloom Arena match")
    path = tmp_path / "clip_00100-00130.mp4"
    clip = SimpleNamespace(candidate=ClipCandidate(start=100.0, end=130.0, score=80, hook="",
                                                   subscores={"sport_label": "Goal"}), path=path)
    profile = SimpleNamespace(option={"reels": ["recap"]}, report_data={})
    pipeline._sport_reels(db, "vid", profile, [clip], tmp_path, {}, made_seconds={str(path): 9.0})
    assert asked == [(path, 9.0)]
    asked.clear()
    # A clip with no edit: its window, as before.
    pipeline._sport_reels(db, "vid", profile, [clip], tmp_path, {})
    assert asked == [(path, 30.0)]

    window = ClipCandidate(start=10.0, end=40.0, score=80)
    assert pipeline._made_seconds(window, json.dumps({"edit": {"keep": [[0, 10]], "speed": 2}})) == 5.0
    assert pipeline._made_seconds(window, "") == 30.0
    assert pipeline._made_seconds(window, "{not json") == 30.0


def test_a_match_reel_gets_the_length_an_edited_clip_was_made(rerun, monkeypatch):
    """A re-run's story reels join its clips as made: the clip the creator
    cut reaches them with the length its edit keeps, the others with their
    window."""
    asked = _match_with_reels(monkeypatch)
    got = rerun(_found(), rows=[(100.0, 120.0, {"edit": E})], **SOCCER)
    assert dict(got.renders)[(100.0, 120.0)] == {"edit": E}
    assert asked == [{10.0: 20.0, 100.0: 18.0, 200.0: 20.0, 300.0: 20.0}]


def _lf(start: float) -> tuple:
    """The window of Longform's clip found at `start`, nudged as Longform nudges it."""
    return round(start + 0.011, 2), round(start + 20.011, 2)


def _longform(monkeypatch, tmp_path, saved: dict, pc=None, **clips) -> SimpleNamespace:
    """Longform's clip mode on a video whose window at 100 s already has a
    clip saved with `saved`, with download, transcription, analysis, the
    title writer and the render stubbed out, rendered here or on `pc`. It
    returns each render's options by window and that clip's options after
    the run."""
    pytest.importorskip("numpy")
    pytest.importorskip("cv2")
    import core.pipeline as pipeline
    from analysis.metadata import ClipMetadata
    from core.state import StateDB
    from longform import process as longform
    from main import BUNDLED_CONFIG, load_config

    source = tmp_path / "stream.mp4"
    source.write_bytes(b"not really a video")
    video = DownloadedVideo(video_id="vid321", title="A Quarkbloom Arena stream", path=source, duration=600.0)
    renders: dict = {}

    def render(source, candidate, segments, clip_dir, config, render_opts=None, content_language="en"):
        renders[(round(candidate.start, 2), round(candidate.end, 2))] = copy.deepcopy(render_opts)
        clip_dir.mkdir(parents=True, exist_ok=True)
        out = clip_dir / f"clip_{int(candidate.start):05d}-{int(candidate.end):05d}.mp4"
        out.write_bytes(b"a clip")
        return out, json.dumps(render_opts) if render_opts else ""

    def titles(candidates, *_a, **_k):
        return [ClipMetadata(title=f"Clip at {c.start:g}", description="", hashtags=[]) for c in candidates]

    monkeypatch.setattr(pipeline, "_cached_or_download", lambda *_a, **_k: video)
    monkeypatch.setattr(pipeline, "convert_slow_source", lambda *_a, **_k: None)
    monkeypatch.setattr(pipeline, "online_transcription", lambda _config: None)
    monkeypatch.setattr(pipeline, "clip_direction", lambda *_a, **_k: None)
    monkeypatch.setattr(pipeline, "_with_usable_model", lambda cfg: cfg)
    monkeypatch.setattr(pipeline, "_remote_renderer", pc.renderer_for if pc else (lambda _config: None))
    monkeypatch.setattr(pipeline, "_render_files", render)
    monkeypatch.setattr("transcription.transcriber.transcribe", lambda *_a, **_k: [])
    monkeypatch.setattr("transcription.transcriber.detected_language", lambda *_a, **_k: "en")
    monkeypatch.setattr("llm.registry.create_backend", lambda *_a, **_k: object())
    monkeypatch.setattr("analysis.fusion.find_clips", lambda *_a, **_k: (_found(), []))
    monkeypatch.setattr("analysis.metadata.generate_metadata_batch", titles)
    config = load_config(BUNDLED_CONFIG)
    config["paths"]["data_dir"] = str(tmp_path / "data")
    config["clips"].update({"captions": False, **clips})
    db = StateDB(tmp_path / "state.db")
    try:
        db.upsert_video("vid321", title="A Quarkbloom Arena stream")
        db.add_clip("vid321", 100.011, 120.011, 70, "h", path="/old/clip.mp4", title="Creator title",
                    render_opts=json.dumps(saved))
        longform.process_longform("local:stream", config, db, {"mode": "short_clips"})
        row = db.conn.execute("SELECT render_opts FROM clips WHERE start_s = 100.01").fetchone()
        return SimpleNamespace(renders=renders, saved=json.loads(row["render_opts"]))
    finally:
        db.conn.close()


def test_a_longform_rerun_makes_an_edited_16x9_clip_with_its_edits(monkeypatch, tmp_path):
    saved = {"profile": "short_clips", "edit": E}
    got = _longform(monkeypatch, tmp_path, saved)
    assert got.renders == {_lf(s): saved if s == 100.0 else {"profile": "short_clips"} for s in STARTS}
    assert got.saved == saved


def test_a_longform_match_keeps_an_edited_clips_card_words_and_made_length(monkeypatch, tmp_path):
    """A match's 16:9 clip the creator edited is made with their cut in the
    job's look, keeps the card words saved on it, and reaches the reels with
    the length its edit keeps."""
    asked = _match_with_reels(monkeypatch)
    saved = {"profile": "short_clips", "edit": E, "caption_style": {"post_style": "highlights"},
             "headline": "MY CARD", "subline": "MINE"}
    got = _longform(monkeypatch, tmp_path, saved, **SOCCER)
    assert got.renders == {_lf(s): {"profile": "short_clips", **({"edit": E} if s == 100.0 else {})}
                           for s in STARTS}
    assert got.saved == saved
    assert asked == [{_lf(s)[0]: 18.0 if s == 100.0 else 20.0 for s in STARTS}]


# ---- render PCs ----------------------------------------------------------------------------


def test_a_render_pc_gets_an_edited_clip_with_its_choices(rerun, monkeypatch):
    """Sent to a render PC, as when made here: the clip the creator edited
    goes with its saved choices over the job's options, and each window
    with no clip yet with exactly the job's (_clip_opts(meta))."""
    import core.pipeline as pipeline

    new = [0.5, 0.5, 0.2, 0.2]
    fresh = {"by": "user", "cam": new, "preset": "half", "panels": []}
    found_split = {"by": "video", "cam": [0.0, 0.6, 0.3, 0.4], "preset": "small_cam", "order": "game_top"}
    pc = _RenderPC()
    monkeypatch.setattr(pipeline, "_remote_renderer", pc.renderer_for)
    got = rerun(_found(), rows=[(100.0, 120.0, {"edit": E, "gaming": found_split})], fresh=fresh, gaming=True)
    assert pc.job_opts == {"gaming": fresh}
    assert pc.sent[(100.0, 120.0)] == {"gaming": {**found_split, "cam": new, "by": "user"}, "edit": E}
    others = [w for w in pc.sent if w != (100.0, 120.0)]
    assert len(others) == 3 and all(pc.sent[w] is pc.job_opts for w in others)
    assert dict(got.renders) == pc.sent
    assert Path(got.rows[(100.0, 120.0)]["path"]).name == "clip_00100-00120.mp4"
    assert EDITED_LINE in got.out

    # A Highlights job: each clip's own card, and the edited clip's choices over it.
    pc = _RenderPC()
    monkeypatch.setattr(pipeline, "_remote_renderer", pc.renderer_for)
    got = rerun(_found(), rows=[(100.0, 120.0, HAND)], **HIGHLIGHTS)
    assert pc.job_opts is None
    assert pc.sent == {(s, s + 20.0): {**_card(s), **HAND} if s == 100.0 else _card(s) for s in STARTS}
    assert got.rows[(100.0, 120.0)]["render_opts"] == HAND


def test_a_longform_render_pc_gets_an_edited_clip_with_its_edits(monkeypatch, tmp_path):
    saved = {"profile": "short_clips", "edit": E, "crop": "center"}
    pc = _RenderPC()
    got = _longform(monkeypatch, tmp_path, saved, pc=pc)
    assert pc.job_opts == {"profile": "short_clips"}
    assert pc.sent == {_lf(s): saved if s == 100.0 else {"profile": "short_clips"} for s in STARTS}
    assert got.renders == pc.sent and got.saved == saved



def test_a_render_pc_clip_is_checked_against_what_its_edit_keeps(monkeypatch, tmp_path):
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient

    from remote_render import gateway, piece, protocol
    from remote_render.queue import RenderQueue

    q = RenderQueue(tmp_path)
    client = TestClient(gateway.create_app(q, tmp_path))
    monkeypatch.setattr(piece, "duration", lambda _p: 8.0)
    caps = {"protocol": protocol.PROTOCOL, "encoders": ["cpu"], "framing": True, "name": "Render PC"}
    wid, secret = q.redeem(q.new_pairing_code(), "Render PC", caps)
    headers = {"X-Worker-Id": wid, "Authorization": f"Bearer {secret}"}
    clip = b"clip-bytes" * 1000

    def returned(jid: str, spec: dict) -> dict:
        p = tmp_path / f"{jid}.piece.mp4"
        p.write_bytes(b"piece")
        q.submit({"id": jid, "video_id": "v", "spec": spec, "piece_path": str(p),
                  "piece_sha": piece.sha256(p), "piece_size": p.stat().st_size})
        claimed = client.post("/v1/claim", headers=headers).json()
        assert claimed["id"] == jid
        client.put(f"/v1/jobs/{jid}/result", params={"offset": 0}, content=clip, headers=headers)
        return client.post(f"/v1/jobs/{jid}/complete", headers=headers, json={
            "sha256": hashlib.sha256(clip).hexdigest(), "size": len(clip), "render_opts": "{}"}).json()

    # 8 s back for a 30 s window whose edit keeps 8 s: whole.
    edited = returned("e" * 32, {"start": 0, "end": 30, "render_opts": {"edit": {"keep": [[0, 4], [20, 24]]}}})
    assert edited == {"ok": True}
    # The same 8 s for a clip with no edit is damaged, as before (8 < 15).
    plain = returned("f" * 32, {"start": 0, "end": 30})
    assert plain["ok"] is False and "expected about 30.0s" in plain["error"]


# ---- the rule itself (pure) -----------------------------------------------------------------


def test_made_seconds_follows_what_the_edit_keeps():
    from video_editor.timeline import made_seconds

    assert made_seconds({"keep": [[0, 10], [20, 30]], "speed": 2}, 30.0) == 10.0
    assert made_seconds(None, 30.0) == 30.0
    assert made_seconds({"keep": [[50, 60]]}, 30.0) == 30.0     # keeps nothing: the render applies none of it
    assert made_seconds({"speed": 3}, 30.0) == 10.0


def test_a_hand_edit_is_known_by_its_keys():
    pytest.importorskip("numpy")
    import core.pipeline as pipeline

    # A first run's options are never an edit.
    for first in ({"gaming": SETUP}, {"gaming": {**SETUP, "by": "creator"}}, LOOK, {**LOOK, "gaming": SETUP},
                  {"gaming": {"by": "video", "cam": [0, 0, 0.2, 0.2]}}, {"gaming": {}}, {}):
        assert not pipeline._edited(first), first
    for key in pipeline._BY_HAND:
        assert pipeline._edited({**LOOK, key: None}), key
    assert pipeline._edited({"edit": None})
    assert pipeline._edited({"gaming": None})
    assert pipeline._edited({"gaming": {"by": "clip", "preset": "split"}})
    assert not pipeline._edited({"gaming": {"by": "user", "cam": [0.6, 0, 0.4, 0.3]}})


def test_the_choices_a_rerun_takes(db):
    pytest.importorskip("numpy")
    import core.pipeline as pipeline

    found_split = {"by": "video", "cam": [0, 0.6, 0.3, 0.4], "preset": "small_cam", "order": "game_top"}
    remembered = {**SETUP, "by": "creator"}
    clip_only = {"by": "clip", "preset": "split"}
    fade = {"fade_in": 0.5}
    rows = {
        "first_setup": {**LOOK, "gaming": SETUP},
        "first_remembered": {**LOOK, "gaming": remembered},
        "hand": {**LOOK, "edit": None, "crop": "center", "speaker_turns": [[1, 2, 1]], "sport": "soccer",
                 "vertical_live": True},
        "found_split": {"gaming": found_split, "edit": fade},
        "setup_split": {"gaming": SETUP, "edit": fade},
        "remembered_split": {"gaming": remembered, "edit": fade},
        "off_alone": {"gaming": None},
        "clip_alone": {"gaming": clip_only},
        "reel": {"reel": "recap", "edit": {"fade_in": 1}},
        "longform": {"profile": "short_clips", "edit": {"fade_in": 1}},
    }
    db.upsert_video("v", title="A Quarkbloom Arena match")
    names = {}
    for i, (name, opts) in enumerate(rows.items()):
        db.add_clip("v", 10 + 100 * i, 30 + 100 * i, 80, "h", render_opts=json.dumps(opts))
        names[(round(10.0 + 100 * i, 2), round(30.0 + 100 * i, 2))] = name
    db.add_clip("v", 1700, 1720, 80, "h", render_opts="{not json")

    def choices(config, gaming_opts=None, profile=None):
        got = pipeline._creator_choices(db, "v", config, gaming_opts, profile)
        return {names[k]: v for k, v in got.items()}

    hand = {**LOOK, "edit": None, "crop": "center"}
    new_cam = [0.5, 0.5, 0.2, 0.2]
    person = {"gaming": {"by": "user", "cam": new_cam, "preset": "half"}}
    gaming = {"clips": {"gaming": True}}
    assert choices({"clips": {}}) == {
        "hand": hand, "found_split": {"edit": fade}, "setup_split": {"edit": fade, "gaming": SETUP},
        "remembered_split": {"edit": fade}, "off_alone": {"gaming": None}, "clip_alone": {"gaming": clip_only}}
    for job_split in ({"gaming": {"by": "video", "cam": [0.1, 0.1, 0.2, 0.2]}}, {"gaming": {}}):
        assert choices(gaming, job_split) == {
            "hand": hand, "found_split": {"edit": fade, "gaming": found_split},
            "setup_split": {"edit": fade, "gaming": SETUP}, "remembered_split": {"edit": fade, "gaming": remembered},
            "off_alone": {"gaming": None}, "clip_alone": {"gaming": clip_only}}
    # A webcam a person chose for this job: the edited clips' found or remembered splits take it.
    assert choices(gaming, person) == {
        "hand": hand, "found_split": {"edit": fade, "gaming": {**found_split, "cam": new_cam, "by": "user"}},
        "setup_split": {"edit": fade, "gaming": SETUP},
        "remembered_split": {"edit": fade, "gaming": {**remembered, "cam": new_cam, "by": "user"}},
        "off_alone": {"gaming": None}, "clip_alone": {"gaming": clip_only}}
    # Sports: only what a person set on the clip alone.
    assert choices({"clips": {"sport": "soccer"}}) == {
        "hand": {"edit": None, "crop": "center"}, "found_split": {"edit": fade}, "setup_split": {"edit": fade},
        "remembered_split": {"edit": fade}, "off_alone": {}, "clip_alone": {}}
    # Longform's clips, by their profile; never a reel.
    assert choices({"clips": {}}, profile="short_clips") == {"longform": {"edit": {"fade_in": 1}}}
