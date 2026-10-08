"""Rate & understand and Suggest edits: plugins that look at a video's moments
once they're found, and at the clips chosen from them (plugins/steps.py, and
plugins/runner.answer_moments for each run).

These tests run real child processes: tests/fixtures/plugins/stepper rates
the moments it is handed and says what happens in them as its settings say,
and tests/fixtures/plugins/trimmer suggests edits for the clips it is
handed; each fails the way it is told to. Copies of them under other ids
stand in for several plugins chosen in one job. Every game named is
Quarkbloom Arena, a made-up game.
"""

import copy
import hashlib
import importlib
import json
import shutil
import sys
import threading
from pathlib import Path

import pytest

from core.models import ClipCandidate, DownloadedVideo, Rejection, Segment

ROOT = Path(__file__).resolve().parent.parent
STEPPER = ROOT / "tests" / "fixtures" / "plugins" / "stepper"
ECHO = ROOT / "tests" / "fixtures" / "plugins" / "echo"

pytest.importorskip("yaml")

SEGMENTS = [Segment(0.0, 30.0, "round one starts in the arena"),
            Segment(100.0, 130.0, "a quark burst, what a play"),
            Segment(200.0, 230.0, "the final round, they are back in it")]


@pytest.fixture
def video(tmp_path):
    path = tmp_path / "match.mp4"
    path.write_bytes(b"not really a video")
    return DownloadedVideo(video_id="vid321", title="A Quarkbloom Arena match", path=path, duration=600.0)


@pytest.fixture
def data_dir(tmp_path, install_plugin):
    """A data folder with the stepper installed (fixture-dev/stepper)."""
    folder = tmp_path / "data"
    install_plugin(folder, STEPPER)
    return folder


@pytest.fixture
def add_stepper(tmp_path, data_dir, install_plugin):
    """Install a copy of the stepper under another id and name."""
    def add(pid: str, name: str, *, enabled: bool = True) -> str:
        folder = tmp_path / "copies" / pid.replace("/", "-")
        shutil.copytree(STEPPER, folder)
        manifest = (folder / "clipskitty.yaml").read_text(encoding="utf-8")
        manifest = manifest.replace("id: fixture-dev/stepper", f"id: {pid}").replace("name: Stepper", f"name: {name}")
        (folder / "clipskitty.yaml").write_text(manifest, encoding="utf-8")
        install_plugin(data_dir, folder, enabled=enabled)
        return pid

    return add


def _config(**clips):
    return {"clips": {"min_score": 55, "min_duration": 10, "max_duration": 60, "max_clips_per_video": 0, **clips},
            "llm": {"backend": "ollama/local", "ollama_host": "http://localhost:11434"}}


def _cand(start, score, **subscores):
    return ClipCandidate(start=float(start), end=float(start) + 20, score=score, hook=f"Moment at {start:g}",
                         reason="loud reaction", subscores=dict(subscores) or None)


def _choice(pid="fixture-dev/stepper", **settings):
    return {"id": pid, **({"settings": settings} if settings else {})}


def _after(data_dir, video, candidates, rejections=(), **clips):
    from plugins import steps

    return steps.after_finding(list(candidates), list(rejections), video=video, segments=SEGMENTS, language="en",
                               config=_config(**clips), data_dir=data_dir)


def _seen(data_dir, tag="") -> dict:
    runs = sorted((data_dir / "plugins" / "runs").iterdir(), key=lambda p: p.stat().st_mtime)
    runs = [r for r in runs if r.name.endswith(tag)] if tag else runs
    return json.loads((runs[-1] / "out" / "seen.json").read_text(encoding="utf-8"))["job"]


def _trace(path) -> list[dict]:
    return [json.loads(line) for line in Path(path).read_text(encoding="utf-8").splitlines()]


STEPPER_WHO = {"plugin": "fixture-dev/stepper", "version": "1.0.0", "name": "Stepper"}


# ---- the shortlist -----------------------------------------------------------------


def test_shortlist_config_returns_the_same_object_without_a_rater_or_limit():
    from plugins import steps

    rater = [{"id": "example-dev/quarkbloom-rater"}]
    for clips in ({}, {"max_clips_per_video": 3},
                  {"understand": [{"id": "example-dev/quarkbloom-notes"}], "max_clips_per_video": 3},
                  {"rate": rater}, {"rate": rater, "max_clips_per_video": 0}, {"rate": [], "max_clips_per_video": 3}):
        config = _config(**clips)
        assert steps.shortlist_config(config) is config, clips


def test_shortlist_config_copies_clips_and_never_changes_the_job_config():
    from plugins import steps

    config = _config(rate=[{"id": "example-dev/quarkbloom-rater"}], max_clips_per_video=3)
    before = copy.deepcopy(config)
    short = steps.shortlist_config(config)
    assert short is not config and short["clips"] is not config["clips"]
    assert short["clips"]["max_clips_per_video"] == 3 * steps.RATE_POOL_FACTOR == 9
    assert {k: v for k, v in short["clips"].items() if k != "max_clips_per_video"} == \
        {k: v for k, v in config["clips"].items() if k != "max_clips_per_video"}
    assert short["llm"] is config["llm"]
    assert config == before  # the job's own config, untouched
    big = steps.shortlist_config(_config(rate=[{"id": "example-dev/quarkbloom-rater"}], max_clips_per_video=100))
    assert big["clips"]["max_clips_per_video"] == 200  # never more than a run may be handed


# ---- rating ------------------------------------------------------------------------


def test_a_rater_rescores_reorders_and_the_limit_applies_after(data_dir, video, capsys):
    found = [_cand(10, 90), _cand(100, 80), _cand(200, 70), _cand(300, 60)]
    duplicate = Rejection(_cand(12, 85), "timestamp_overlap", kept=found[0])
    kept, rejections, report = _after(data_dir, video, found, [duplicate], max_clips_per_video=2,
                                      rate=[_choice(scores="m3=95,m4=99,m1=+2")])
    assert [(c.start, c.score) for c in kept] == [(300, 99), (200, 95)]
    assert rejections[0] is duplicate
    assert [(r.candidate.start, r.candidate.score, r.reason) for r in rejections[1:]] == [
        (10, 92, "over_limit"), (100, 80, "over_limit")]
    m1 = found[0].subscores
    assert m1["found_score"] == 90
    assert m1["plugin_ratings"] == [{**STEPPER_WHO, "score": 92, "reason": "rated 92 in the test"}]
    assert "plugin_ratings" not in found[1].subscores and "found_score" not in found[1].subscores
    assert report == [{**STEPPER_WHO, "steps": ["rate"], "ok": True, "given": 4, "noted": 0, "rated": 3,
                       "set_aside": 0}]

    job = _seen(data_dir)
    assert job["steps"] == ["rate"] and [m["id"] for m in job["moments"]] == ["m1", "m2", "m3", "m4"]
    # The creator's own limit and minimum score, not the shortlist.
    assert job["limits"] == {"max_clips": 2, "min_duration": 10, "max_duration": 60, "min_score": 55}
    out = capsys.readouterr().out
    assert "      Rate: Stepper 1.0.0 (fixture-dev/stepper) on 4 moment(s)" in out
    assert "      Stepper rated 3 of 4 moment(s)" in out


def test_rated_under_min_score_are_set_aside_as_below_min_score(data_dir, video, capsys):
    found = [_cand(10, 90), _cand(100, 80), _cand(200, 70)]
    kept, rejections, report = _after(data_dir, video, found, rate=[_choice(scores="m2=10,m3=72")])
    assert [(c.start, c.score) for c in kept] == [(10, 90), (200, 72)]
    assert [(r.candidate.start, r.candidate.score, r.reason) for r in rejections] == [(100, 10, "below_min_score")]
    assert report[0]["set_aside"] == 1 and report[0]["rated"] == 2
    assert "      1 moment(s) rated under the minimum score (55) set aside" in capsys.readouterr().out


def test_must_haves_survive_a_low_rating_and_stay_first(data_dir, video):
    found = [_cand(10, 90), _cand(100, 70, required="the quark burst"), _cand(200, 80)]
    kept, rejections, report = _after(data_dir, video, found, max_clips_per_video=2,
                                      rate=[_choice(scores="m1=95,m2=10")])
    assert [(c.start, c.score) for c in kept] == [(100, 10), (10, 95)]
    assert [(r.candidate.start, r.reason) for r in rejections] == [(200, "over_limit")]
    assert report[0]["set_aside"] == 0


def test_unrated_moments_keep_their_score(data_dir, video):
    """A pipeline's own scores are not held to the minimum (D10): only a
    score a rater gave is."""
    found = [_cand(10, 80, plugin="example-dev/quark-finder", plugin_label="quark_burst"),
             _cand(100, 40, plugin="example-dev/quark-finder", plugin_label="quiet")]
    kept, rejections, _report = _after(data_dir, video, found, rate=[_choice(scores="m1=60")])
    assert [(c.start, c.score) for c in kept] == [(10, 60), (100, 40)] and rejections == []
    assert found[1].subscores == {"plugin": "example-dev/quark-finder", "plugin_label": "quiet"}
    moments = _seen(data_dir)["moments"]
    assert [(m["found_by"], m["label"]) for m in moments] == [("example-dev/quark-finder", "quark_burst"),
                                                              ("example-dev/quark-finder", "quiet")]


def test_a_failed_rater_keeps_the_order_and_still_cuts(data_dir, video, capsys):
    # A must-have first, as fusion leaves it, then by score.
    found = [_cand(10, 70, required="the opening"), _cand(100, 90), _cand(200, 80), _cand(300, 60)]
    kept, rejections, report = _after(data_dir, video, found, max_clips_per_video=2,
                                      rate=[_choice(mode="fail", scores="*=100")])
    assert [(c.start, c.score) for c in kept] == [(10, 70), (100, 90)]
    assert [(r.candidate.start, r.candidate.score, r.reason) for r in rejections] == [
        (200, 80, "over_limit"), (300, 60, "over_limit")]
    assert not any("plugin_ratings" in c.subscores for c in found)
    assert report == [{**STEPPER_WHO, "steps": ["rate"], "ok": False, "given": 4, "noted": 0, "rated": 0,
                       "set_aside": 0, "error": "It said: the arena feed could not be read."}]
    out = capsys.readouterr().out
    assert "      Going on without Stepper: It said: the arena feed could not be read.\n" in out
    assert "      (Stepper failed: the arena feed could not be read)" in out


# ---- understanding -----------------------------------------------------------------


def test_understanders_never_change_which_clips_are_made(data_dir, video, capsys):
    found = [_cand(200, 70), _cand(100, 90), _cand(10, 50)]
    duplicate = Rejection(_cand(105, 60), "transcript_similarity", kept=found[1])
    kept, rejections, report = _after(data_dir, video, found, [duplicate], min_score=80, max_clips_per_video=3,
                                      understand=[_choice(notes="*=A Quarkbloom Arena moment|{said}",
                                                          scores="*=5")])
    assert kept == found and [c.score for c in kept] == [70, 90, 50]
    assert rejections == [duplicate]
    assert found[1].subscores == {"plugin_notes": [{**STEPPER_WHO, "text": "A Quarkbloom Arena moment"},
                                                   {**STEPPER_WHO, "text": "a quark burst, what a play"}]}
    assert report == [{**STEPPER_WHO, "steps": ["understand"], "ok": True, "given": 3, "noted": 3, "rated": 0,
                       "set_aside": 0}]
    out = capsys.readouterr().out
    assert "      Understand: Stepper 1.0.0 (fixture-dev/stepper) on 3 moment(s)" in out
    assert "      Stepper added notes to 3 of 3 moment(s)" in out
    # Its scores weren't asked for: the SDK says so, and nothing of them is used.
    assert "rate ignored: this job didn't ask for ratings" in out


# ---- several plugins ---------------------------------------------------------------


def test_runs_go_in_order_and_see_earlier_scores_and_notes(data_dir, video, add_stepper, tmp_path):
    trace = str(tmp_path / "trace.jsonl")
    a, b = add_stepper("fixture-dev/notes-a", "Notes A"), add_stepper("fixture-dev/notes-b", "Notes B")
    c, d = add_stepper("fixture-dev/rater-c", "Rater C"), add_stepper("fixture-dev/rater-d", "Rater D")
    found = [_cand(10, 70), _cand(100, 65)]
    kept, _rejections, report = _after(
        data_dir, video, found,
        understand=[_choice(a, notes="m1=Round one", trace=trace), _choice(b, notes="m1=A quark burst", trace=trace)],
        rate=[_choice(c, scores="*=+10", trace=trace), _choice(d, scores="m1=60", trace=trace)])
    runs = _trace(trace)
    assert [(r["plugin"], r["steps"]) for r in runs] == [
        (a, ["understand"]), (b, ["understand"]), (c, ["rate"]), (d, ["rate"])]
    assert runs[1]["moments"][0]["context"] == ["Round one"]
    assert runs[2]["moments"][0]["context"] == ["Round one", "A quark burst"]
    assert [(m["score"], m["found_score"]) for m in runs[2]["moments"]] == [(70, 70), (65, 65)]
    assert [(m["score"], m["found_score"]) for m in runs[3]["moments"]] == [(80, 70), (75, 65)]
    # The ratings chain, and the last one counts.
    assert [(x.start, x.score) for x in kept] == [(100, 75), (10, 60)]
    assert [(r["name"], r["score"]) for r in found[0].subscores["plugin_ratings"]] == [("Rater C", 80),
                                                                                      ("Rater D", 60)]
    assert [n["name"] for n in found[0].subscores["plugin_notes"]] == ["Notes A", "Notes B"]
    assert [(r["name"], r["noted"], r["rated"]) for r in report] == [
        ("Notes A", 1, 0), ("Notes B", 1, 0), ("Rater C", 0, 2), ("Rater D", 0, 1)]


def test_a_plugin_in_both_lists_runs_once_in_its_rate_place(data_dir, video, add_stepper, tmp_path):
    from plugins import steps

    trace = str(tmp_path / "trace.jsonl")
    other = add_stepper("fixture-dev/notes-a", "Notes A")
    both = _choice(notes="m1=The caster calls a quark burst", scores="m1=88", trace=trace)
    found = [_cand(10, 70)]
    _kept, _rejections, report = _after(data_dir, video, found, understand=[both, _choice(other, notes="m1=Round one",
                                                                                             trace=trace)],
                                        rate=[both])
    runs = _trace(trace)
    assert [(r["plugin"], r["steps"]) for r in runs] == [(other, ["understand"]),
                                                       ("fixture-dev/stepper", ["understand", "rate"])]
    assert runs[1]["moments"][0]["context"] == ["Round one"]  # it rates knowing the other's notes
    assert [(r["name"], r["steps"], r["noted"], r["rated"]) for r in report] == [
        ("Notes A", ["understand"], 1, 0), ("Stepper", ["understand", "rate"], 1, 1)]
    assert found[0].score == 88 and len(found[0].subscores["plugin_notes"]) == 2
    folders = sorted(p.name for p in (data_dir / "plugins" / "runs").iterdir())
    assert sum(name.endswith("-understand-rate") for name in folders) == 1
    # Other settings make it another choice, which runs in each list.
    assert steps.plan([{"id": "fixture-dev/stepper", "settings": {"notes": "x"}}], ["fixture-dev/stepper"]) == [
        ({"id": "fixture-dev/stepper", "settings": {"notes": "x"}}, ["understand"]),
        ("fixture-dev/stepper", ["rate"])]
    assert steps.plan(["fixture-dev/stepper"], [{"id": "fixture-dev/stepper"}]) == [
        ({"id": "fixture-dev/stepper"}, ["understand", "rate"])]


# ---- failures ----------------------------------------------------------------------


def test_a_failing_step_is_reported_and_nothing_partial_lands(data_dir, video, add_stepper, capsys):
    good = add_stepper("fixture-dev/rater-c", "Rater C")
    found = [_cand(10, 70), _cand(100, 60)]
    kept, _rejections, report = _after(
        data_dir, video, found,
        understand=[_choice(mode="fail", notes="*=A quark burst")],
        rate=[_choice(mode="bad"), _choice(good, scores="m2=75")])
    # m1's score of 90 came in an answer refused as a whole, and the failed
    # understander's notes were never written: only the good rater landed.
    assert [(c.start, c.score) for c in kept] == [(100, 75), (10, 70)]
    assert "plugin_notes" not in found[0].subscores and "plugin_ratings" not in found[0].subscores
    assert [(r["name"], r["ok"], r.get("error")) for r in report] == [
        ("Stepper", False, "It said: the arena feed could not be read."),
        ("Stepper", False, "Clips Kitty couldn't use its answer."),
        ("Rater C", True, None)]
    out = capsys.readouterr().out
    assert "      Going on without Stepper: Clips Kitty couldn't use its answer.\n" in out
    assert ("      (Stepper gave an answer Clips Kitty can't use: result: moments[1]: score must be a number "
            "from 0 to 100, or left out)") in out


@pytest.mark.parametrize(("mode", "logged"), [
    ("folder", "result: result.json is not a file"),
    ("huge", "result: moments[0]: score must be a number from 0 to 100, or left out"),
    ("deep", "result: result.json could not be read (maximum recursion depth exceeded"),
])
def test_an_answer_that_cant_even_be_read_is_skipped_and_the_job_goes_on(data_dir, video, capsys, mode, logged):
    """result.json made a folder, a score too large for a float, notes nested
    too deep to parse: each used to escape as a Python error and fail the job.
    Each is an answer Clips Kitty can't use, so the run is skipped and reported."""
    found = [_cand(10, 70), _cand(100, 60)]
    kept, _rejections, report = _after(data_dir, video, found, rate=[_choice(mode=mode)])
    assert [(c.start, c.score) for c in kept] == [(10, 70), (100, 60)]
    assert "plugin_ratings" not in found[0].subscores
    assert [(r["ok"], r["error"]) for r in report] == [(False, "Clips Kitty couldn't use its answer.")]
    out = capsys.readouterr().out
    assert "      Going on without Stepper: Clips Kitty couldn't use its answer.\n" in out
    assert f"      (Stepper gave an answer Clips Kitty can't use: {logged}" in out


def test_anything_else_reading_an_answer_raises_is_a_skip_too(data_dir, video, capsys, monkeypatch):
    from plugins import runner

    def broken(*_a, **_k):
        raise OSError("the disk went away")

    monkeypatch.setattr(runner.host, "read_answers", broken)
    found = [_cand(10, 70)]
    kept, _rejections, report = _after(data_dir, video, found, rate=[_choice(scores="*=99")])
    assert [(c.start, c.score) for c in kept] == [(10, 70)]
    assert [(r["ok"], r["error"]) for r in report] == [(False, "Clips Kitty couldn't use its answer.")]
    assert ("      (Stepper gave an answer Clips Kitty couldn't read: OSError: the disk went away)"
            in capsys.readouterr().out)


def test_answers_about_other_moments_and_new_ranges_are_logged_and_left_out(data_dir, video, capsys):
    found = [_cand(10, 70), _cand(100, 60)]
    kept, _rejections, report = _after(data_dir, video, found, rate=[_choice(mode="unknown_ids")])
    assert [(c.start, c.score) for c in kept] == [(10, 90), (100, 60)] and report[0]["rated"] == 1
    out = capsys.readouterr().out
    assert ("      [fixture-dev/stepper] ignored: an answer for m999, which isn't one of this run's moments"
            in out)

    found = [_cand(10, 70), _cand(100, 60)]
    kept, _rejections, report = _after(data_dir, video, found, understand=[_choice(mode="ranges")])
    assert [n["text"] for n in found[0].subscores["plugin_notes"]] == ["A quark burst opens the round"]
    assert kept == found and report[0]["noted"] == 1
    out = capsys.readouterr().out
    assert ("      [fixture-dev/stepper] ignored: 2 range(s): this run was asked about moments, not to find "
            "new ones") in out


def _fake_outcome(monkeypatch, **outcome):
    from plugins import runner

    def run_plugin(command, **_kwargs):
        return runner.host.RunOutcome(**outcome)

    monkeypatch.setattr(runner.host, "run_plugin", run_plugin)


def test_failure_reasons_read_in_creator_words(tmp_path, data_dir, video, add_stepper, install_plugin, monkeypatch,
                                               capsys):
    from plugins import registry, runner, store

    install_plugin(data_dir, ECHO)
    off = add_stepper("fixture-dev/turned-off", "Turned Off", enabled=False)
    real_manifest = store.read_manifest

    def why(rate=None, understand=None):
        _kept, _rejections, report = _after(data_dir, video, [_cand(10, 70)], rate=rate, understand=understand)
        (entry,) = report
        assert entry["ok"] is False
        return entry["error"]

    cases = [
        (lambda m: None, {"rate": ["fixture-dev/gone"]}, "It isn't installed any more."),
        (lambda m: None, {"rate": [off]}, "It's turned off in Marketplace › Installed."),
        (lambda m: m.setattr(store, "app_version", lambda: "1.9.0"), {"rate": [_choice()]},
         "It can't run on this version of Clips Kitty."),
        (lambda m: m.setattr(registry, "blocked_check", lambda d: lambda pid, version: {
            "severity": "blocked", "reason": "it sends videos it does not declare."}), {"rate": [_choice()]},
         "It was blocked: it sends videos it does not declare."),
        (lambda m: None, {"rate": ["fixture-dev/echo"]}, "It can no longer rate moments."),
        (lambda m: None, {"understand": ["fixture-dev/echo"]}, "It can no longer understand moments."),
        (lambda m: None, {"rate": [_choice(colour="red")]},
         "A setting chosen for it no longer fits: this pipeline has no setting called 'colour'."),
        (lambda m: m.setattr(store, "read_manifest", lambda folder: {**real_manifest(folder), "run": {}}),
         {"rate": [_choice()]}, "Its files are damaged. Install it again."),
        (lambda m: m.setattr(runner.host, "find_python", lambda setting=None: None), {"rate": [_choice()]},
         "It needs Python, and none was found on this PC."),
        (lambda m: m.setattr(runner.plugin_models, "for_job", lambda *a, **k: ({}, ["The arena model is missing."])),
         {"rate": [_choice()]}, "A model it needs isn't on this PC. Get it in Marketplace › Installed."),
        (lambda m: _fake_outcome(m, exit_code=-9, timed_out=True), {"rate": [_choice()]},
         "It took longer than its 5 minute limit, so Clips Kitty stopped it."),
        (lambda m: None, {"rate": [_choice(mode="fail")]}, "It said: the arena feed could not be read."),
        (lambda m: None, {"rate": [_choice(mode="fail", message="The arena feed is down.")]},
         "It said: The arena feed is down."),
        (lambda m: None, {"rate": [_choice(mode="fail", message="Out of quark tokens!")]},
         "It said: Out of quark tokens!"),
        (lambda m: None, {"rate": [_choice(mode="fail", message="x" * 400)]}, "It said: " + "x" * 300 + "."),
        # A blank error line says nothing: never "It said: .".
        (lambda m: None, {"rate": [_choice(mode="fail", message=" ")]}, "It stopped before it finished."),
        (lambda m: _fake_outcome(m, exit_code=3, error="half way"), {"rate": [_choice()]},
         "It stopped before it finished."),
        (lambda m: _fake_outcome(m, exit_code=None, error="could not start the plugin"), {"rate": [_choice()]},
         "Clips Kitty couldn't start it."),
        (lambda m: None, {"rate": [_choice(mode="bad")]}, "Clips Kitty couldn't use its answer."),
    ]
    for patch, chosen, expected in cases:
        with monkeypatch.context() as m:
            patch(m)
            got = why(**chosen)
        assert got == expected, chosen
        # Never the plugin's name (the line around it says it), never two
        # full stops, never a bug report about someone else's plugin.
        for word in ("Stepper", "Echo", "Turned Off", "fixture-dev/"):
            assert word not in got
        assert ".." not in got and got.endswith((".", "!"))
        assert "bug report" not in got and "Feedback" not in got and "Try again" not in got
    out = capsys.readouterr().out
    assert "      Going on without fixture-dev/gone: It isn't installed any more.\n" in out
    assert "      (The pipeline fixture-dev/gone isn't installed)" in out


def test_a_blank_error_line_is_passed_over_for_one_with_words():
    from types import SimpleNamespace

    from plugins import runner

    plugin, outcome = SimpleNamespace(name="Stepper"), runner.host.RunOutcome(1, error="half way")
    assert runner._moment_failure(plugin, outcome, ["The arena feed is down", " \n\t"], 600).why == \
        "It said: The arena feed is down."
    assert runner._moment_failure(plugin, outcome, ["   "], 600).why == "It stopped before it finished."


def test_notes_past_eight_are_logged(data_dir, video, add_stepper, capsys):
    a, b = add_stepper("fixture-dev/notes-a", "Notes A"), add_stepper("fixture-dev/notes-b", "Notes B")
    found = [_cand(10, 70, plugin_notes=[{"plugin": "example-dev/quark-finder", "version": "1.0.0",
                                          "name": "Quark Finder", "text": "The finder's own note"}])]
    _kept, _rejections, report = _after(data_dir, video, found, understand=[
        _choice(a, notes="m1=a1|a2|a3|a4|a5"), _choice(b, notes="m1=b1|b2|b3|b4|b5")])
    notes = found[0].subscores["plugin_notes"]
    assert [n["text"] for n in notes] == ["The finder's own note", "a1", "a2", "a3", "a4", "a5", "b1", "b2"]
    assert [(r["name"], r["noted"]) for r in report] == [("Notes A", 1), ("Notes B", 1)]
    out = capsys.readouterr().out
    assert "      Notes B: 3 note(s) not kept: at most 8 for each moment" in out
    assert "Notes A: " not in out


# ---- what a plugin is handed --------------------------------------------------------


def test_an_unmeasured_moment_has_no_reaction_signal(data_dir, video):
    from plugins import steps

    unmeasured = _cand(10, 72, text=61, audio=70, visual=44, reaction=50, engagement=66, source="transcript")
    measured = _cand(100, 64, text=50, audio=40, visual=30, reaction=7, reaction_measured=1, engagement=55,
                     game=80, sport_label="Goal", required="")
    assert steps.moments_of("m1", unmeasured) == {
        "id": "m1", "start": 10.0, "end": 30.0, "score": 72, "found_score": 72, "found_by": "clipskitty", "label": "",
        "signals": {"text": 61, "audio": 70, "visual": 44, "engagement": 66},
        "title": "Moment at 10", "reason": "loud reaction", "context": []}
    assert steps.moments_of("m2", measured)["signals"] == {"text": 50, "audio": 40, "visual": 30, "engagement": 55,
                                                          "game": 80, "reaction": 7}
    assert steps.moments_of("m2", measured)["label"] == "Goal"

    _after(data_dir, video, [unmeasured, measured], rate=[_choice()])
    handed = _seen(data_dir)["moments"]
    assert "reaction" not in handed[0]["signals"] and handed[1]["signals"]["reaction"] == 7
    assert handed[0]["title"] == "Moment at 10"  # with transcript.read


def test_cancel_during_a_step_cancels_the_job(data_dir, video):
    from core import cancel

    timer = threading.Timer(1.0, cancel.request_cancel, args=("vid321",))
    timer.start()
    try:
        with pytest.raises(cancel.CancelledError):
            _after(data_dir, video, [_cand(10, 70)], rate=[_choice(mode="sleep")])
    finally:
        timer.cancel()
        cancel.clear("vid321")
    # Cancelled before a run starts: none starts.
    shutil.rmtree(data_dir / "plugins" / "runs")
    cancel.set_active("vid321")
    cancel.request_cancel("vid321")
    try:
        with pytest.raises(cancel.CancelledError):
            _after(data_dir, video, [_cand(10, 70)], rate=[_choice()])
    finally:
        cancel.clear("vid321")
        cancel.set_active(None)
    assert not (data_dir / "plugins" / "runs").exists()


def test_over_200_moments_hand_over_the_first_200(data_dir, video):
    found = [_cand(i, 60) for i in range(205)]
    kept, rejections, report = _after(data_dir, video, found, min_score=0, rate=[_choice(scores="*=+1")])
    handed = _seen(data_dir)["moments"]
    assert len(handed) == 200 and handed[0]["id"] == "m1" and handed[-1]["id"] == "m200"
    assert handed[-1]["start"] == 199.0
    assert report[0]["given"] == 200 and report[0]["rated"] == 200
    assert kept == found and rejections == []
    assert all(c.score == 61 for c in found[:200])
    assert [(c.score, c.subscores) for c in found[200:]] == [(60, None)] * 5  # never handed over, never touched
    # No moments: no run is started.
    runs = len(list((data_dir / "plugins" / "runs").iterdir()))
    assert _after(data_dir, video, [], rate=[_choice()]) == ([], [], [])
    assert len(list((data_dir / "plugins" / "runs").iterdir())) == runs


def test_progress_uses_ranking_and_understand_stages_with_the_plugin_name(data_dir, video, add_stepper):
    from core import progress

    notes = add_stepper("fixture-dev/notes-a", "Notes A")
    events = []
    progress.set_handler(events.append)
    try:
        _after(data_dir, video, [_cand(10, 70)], understand=[_choice(notes, notes="m1=Round one")],
               rate=[_choice(scores="m1=80")])
    finally:
        progress.set_handler(None)
    assert [(e["stage"], e["fraction"], e["plugin"]) for e in events] == [
        ("understand", 0.0, "Notes A"), ("understand", 0.5, "Notes A"), ("understand", 1.0, "Notes A"),
        ("ranking", 0.0, "Stepper"), ("ranking", 0.5, "Stepper"), ("ranking", 1.0, "Stepper")]
    assert all(e["video_id"] == "vid321" for e in events)


# ---- the job's fields ---------------------------------------------------------------


def test_clean_and_check_installed_name_the_field_and_the_item(data_dir):
    from plugins import steps

    assert steps.clean("rate", "fixture-dev/stepper") == [{"id": "fixture-dev/stepper"}]
    assert steps.clean("understand", {"id": "fixture-dev/stepper", "settings": {"notes": "x"}}) == [
        {"id": "fixture-dev/stepper", "settings": {"notes": "x"}}]
    refused = [
        ("rate", 5, 'rate must be a plugin or a list of plugins: [{"id": "publisher/name"}]'),
        ("rate", ["a/b", "c/d", "e/f", "g/h"], "rate: at most 3 plugins for one step"),
        ("rate", ["a/b", "Not An Id"], "rate[1].id must look like publisher/name (lower case, digits and hyphens)"),
        ("understand", [{"id": "a/b", "x": 1}], "understand[0] has unknown fields: x"),
        ("rate", ["a/b", {"id": "a/b", "version": "1.0.0"}], "rate: a/b is listed twice"),
    ]
    for field, value, message in refused:
        with pytest.raises(ValueError) as e:
            steps.clean(field, value)
        assert str(e.value) == message
    steps.check_installed(data_dir, "rate", [{"id": "fixture-dev/stepper"}])
    with pytest.raises(ValueError) as e:
        steps.check_installed(data_dir, "rate", [{"id": "fixture-dev/stepper"}, {"id": "fixture-dev/gone"}])
    assert str(e.value) == "rate[1]: the pipeline fixture-dev/gone isn't installed"


# ---- Suggest edits ---------------------------------------------------------------------
# Plugins that suggest an edit for each clip about to be made. Their suggestions
# wait on the clips (subscores["plugin_edits"]); nothing about the clips changes.

TRIMMER = ROOT / "tests" / "fixtures" / "plugins" / "trimmer"
TRIMMER_WHO = {"plugin": "fixture-dev/trimmer", "version": "1.0.0", "name": "Trimmer"}
CROPS = ["track", "center", "letterbox"]
# What the trimmer is told to suggest for a clip: its spans count from the clip's start.
TRIM = {"cuts": [[2, 6.5]], "mutes": [[8.25, 8.75]], "fade_out": 0.45, "crop": "center",
        "title_overlay": {"text": "Quark burst!", "seconds": 3}, "reason": "Cuts the wait for the respawn timer"}


def _trimmed(start: float) -> dict:
    """TRIM for the clip starting at `start`, as Clips Kitty keeps it: in seconds
    of the video, the fade set to the nearest the editor offers, the reason apart."""
    return {"cuts": [[start + 2, start + 6.5]], "mutes": [[start + 8.25, start + 8.75]], "fade_out": 0.5,
            "title_overlay": {"text": "Quark burst!", "seconds": 3}, "crop": "center"}


@pytest.fixture
def add_trimmer(tmp_path, data_dir, install_plugin):
    """Install the trimmer (fixture-dev/trimmer), or a copy of it under another id and name."""
    def add(pid: str = "fixture-dev/trimmer", name: str = "Trimmer", *, enabled: bool = True) -> str:
        folder = TRIMMER
        if pid != "fixture-dev/trimmer":
            folder = tmp_path / "copies" / pid.replace("/", "-")
            shutil.copytree(TRIMMER, folder)
            manifest = (folder / "clipskitty.yaml").read_text(encoding="utf-8")
            manifest = manifest.replace("id: fixture-dev/trimmer", f"id: {pid}").replace("name: Trimmer",
                                                                                        f"name: {name}")
            (folder / "clipskitty.yaml").write_text(manifest, encoding="utf-8")
        install_plugin(data_dir, folder, enabled=enabled)
        return pid

    return add


def _trim(pid: str = "fixture-dev/trimmer", edits: dict | None = None, **settings) -> dict:
    return {"id": pid, "settings": {**({"edits": json.dumps(edits)} if edits is not None else {}), **settings}}


def _suggest(data_dir, video, clips, **clips_cfg) -> list[dict]:
    from plugins import steps

    return steps.suggest_edits(list(clips), video=video, segments=SEGMENTS, language="en",
                               config=_config(**clips_cfg), data_dir=data_dir)


def _edit_entry(**changes) -> dict:
    """One run's report entry, as suggest_edits makes it."""
    return {**TRIMMER_WHO, "steps": ["edit"], "ok": True, "given": 2, "suggested": 2, "noted": 0, "rated": 0,
            "set_aside": 0, **changes}


@pytest.mark.parametrize(("clips", "crops"), [
    ({}, CROPS),                                                   # standard vertical
    ({"podcast": True}, []),
    ({"sport": {"name": "soccer"}}, []),
    # A match filmed 9:16: process_video sets vertical_live before finding.
    ({"sport": {"name": "soccer"}, "vertical_live": True}, []),
    ({"gaming": True}, []),
    ({"vertical_live": True}, []),
    ({"vertical": False}, []),
])
def test_crops_follow_the_job(clips, crops):
    from plugins import steps

    assert list(steps.crops_for(_config(**clips))) == crops
    assert list(steps.crops_for({"clips": clips})) == crops  # a hand-written config without the defaults


def test_later_edit_plugins_see_earlier_suggestions(data_dir, video, add_trimmer, tmp_path, capsys):
    first, second = add_trimmer(), add_trimmer("fixture-dev/framer", "Framer")
    trace = tmp_path / "trace.jsonl"
    clips = [_cand(10, 80), _cand(100, 70)]
    report = _suggest(data_dir, video, clips, edit=[
        _trim(first, {"m1": TRIM}, trace=str(trace)),
        _trim(second, {"*": {"fade_in": 0.3, "reason": "Eases in"}}, trace=str(trace))])
    runs = _trace(trace)
    assert [(r["plugin"], r["steps"]) for r in runs] == [(first, ["edit"]), (second, ["edit"])]
    # Every run is handed the same clips, with what the runs before it suggested.
    assert all([(m["id"], m["start"], m["end"]) for m in r["moments"]] == [("m1", 10.0, 30.0), ("m2", 100.0, 120.0)]
               for r in runs)
    assert all("suggested" not in m for m in runs[0]["moments"])
    assert runs[1]["moments"][0]["suggested"] == [{"by": first, "name": "Trimmer", "edit": _trimmed(10),
                                                   "reason": TRIM["reason"]}]
    assert "suggested" not in runs[1]["moments"][1]
    assert all(r["limits"]["crops"] == CROPS and "min_score" not in r["limits"] for r in runs)
    # Each plugin's suggestion is kept apart, in the order they ran.
    assert [(e["plugin"], e["edit"], e["reason"]) for e in clips[0].subscores["plugin_edits"]] == [
        (first, _trimmed(10), TRIM["reason"]), (second, {"fade_in": 0.3}, "Eases in")]
    assert [(e["plugin"], e["edit"]) for e in clips[1].subscores["plugin_edits"]] == [(second, {"fade_in": 0.3})]
    assert report == [_edit_entry(suggested=1), _edit_entry(plugin=second, name="Framer")]
    out = capsys.readouterr().out
    assert "      Suggest edits: Trimmer 1.0.0 (fixture-dev/trimmer) on 2 clip(s)\n" in out
    assert "      Trimmer suggested edits for 1 of 2 clip(s)\n" in out
    assert "      Framer suggested edits for 2 of 2 clip(s)\n" in out
    assert "changed: m1's fade_out 0.45 s to 0.5 s, the nearest the editor offers" in out


def test_a_failed_edit_plugin_changes_no_clip_and_keeps_earlier_suggestions(data_dir, video, add_trimmer, capsys):
    first = add_trimmer()
    broken, late = add_trimmer("fixture-dev/broken-trimmer", "Broken Trimmer"), add_trimmer("fixture-dev/late", "Late")
    clips = [_cand(10, 80, text=61), _cand(100, 70)]
    before = [(c.start, c.end, c.score, c.hook, c.reason) for c in clips]
    report = _suggest(data_dir, video, clips, edit=[
        _trim(first, {"m1": TRIM}), _trim(broken, {"*": TRIM}, mode="fail"), _trim(late, {"*": {"fade_in": 1}})])
    assert report == [_edit_entry(suggested=1),
                      _edit_entry(plugin=broken, name="Broken Trimmer", ok=False, suggested=0,
                                  error="It said: the arena feed could not be read."),
                      _edit_entry(plugin=late, name="Late")]
    # The clips are as they were, with the suggestions of the plugins that answered.
    assert [(c.start, c.end, c.score, c.hook, c.reason) for c in clips] == before
    assert clips[0].subscores["text"] == 61
    assert [e["plugin"] for e in clips[0].subscores["plugin_edits"]] == [first, late]
    assert [e["plugin"] for e in clips[1].subscores["plugin_edits"]] == [late]
    assert "      Going on without Broken Trimmer: It said: the arena feed could not be read.\n" in capsys.readouterr().out

    # An answer Clips Kitty can't use is refused whole: nothing of it is kept,
    # and a clip with no suggestion keeps its subscores as they were.
    untouched = [_cand(10, 80, text=61), _cand(100, 70)]
    report = _suggest(data_dir, video, untouched, edit=[_trim(first, {"*": TRIM}, mode="bad")])
    assert report == [_edit_entry(ok=False, suggested=0, error="Clips Kitty couldn't use its answer.")]
    assert [c.subscores for c in untouched] == [{"text": 61}, None]
    # Nothing to suggest for: no run is started.
    runs = len(list((data_dir / "plugins" / "runs").iterdir()))
    assert _suggest(data_dir, video, [], edit=[_trim(first, {"*": TRIM})]) == []
    assert len(list((data_dir / "plugins" / "runs").iterdir())) == runs


def test_a_stored_window_is_rounded_like_the_clip_row(data_dir, video, add_trimmer, db, tmp_path):
    add_trimmer()
    trace = tmp_path / "trace.jsonl"
    clip = ClipCandidate(start=812.123456, end=841.987654, score=85, hook="He holds the bridge alone")
    _suggest(data_dir, video, [clip], edit=[_trim(edits={"m1": {"fade_out": 0.5, "reason": "Fades out"}},
                                                  trace=str(trace))])
    (handed,) = _trace(trace)[0]["moments"]
    assert (handed["start"], handed["end"]) == (812.123456, 841.987654)  # the plugin gets the window as it is
    (entry,) = clip.subscores["plugin_edits"]
    key = json.dumps({"plugin": "fixture-dev/trimmer", "edit": {"fade_out": 0.5}}, sort_keys=True)
    assert entry == {"id": hashlib.sha256(key.encode("utf-8")).hexdigest()[:12], **TRIMMER_WHO,
                     "window": [812.12, 841.99], "min_length": 10.0, "edit": {"fade_out": 0.5},
                     "reason": "Fades out", "state": "new"}
    # The clip's row keeps the same window, so the editor finds the suggestion's clip by it.
    db.conn.execute("INSERT INTO videos (video_id, title, status, created_at, updated_at)"
                    " VALUES ('vid321', 'A Quarkbloom Arena match', 'done', 'x', 'x')")
    clip_id = db.add_clip("vid321", clip.start, clip.end, clip.score, clip.hook, scores=json.dumps(clip.subscores))
    row = db.get_clip(clip_id)
    assert [row["start_s"], row["end_s"]] == entry["window"]
    # The same suggestion made again, in another run, has the same id.
    again = ClipCandidate(start=812.123456, end=841.987654, score=85, hook="He holds the bridge alone")
    _suggest(data_dir, video, [again], edit=[_trim(edits={"m1": {"fade_out": 0.5}})])
    assert again.subscores["plugin_edits"][0]["id"] == entry["id"]


# ---- Suggest edits in process_video -----------------------------------------------------

HIGHLIGHTS = {"caption_style": {"post_style": "highlights"}}


@pytest.fixture
def made(monkeypatch, tmp_path, data_dir):
    """process_video from finding to the clip rows, with download,
    transcription, analysis, the title writer and the render stubbed out.

    `made(found, **clips)` runs a job whose finder finds `found` and returns
    (rows, renders, titled, outcome): the clip rows, each render as
    ((start, end), the options it was given) in window order, the clips the
    titles were written for, in order, and the video's outcome. Each run has
    a database of its own."""
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
    monkeypatch.setattr(pipeline, "transcribe", lambda *_a, **_k: list(SEGMENTS))
    monkeypatch.setattr("transcription.transcriber.detected_language", lambda *_a, **_k: "en")
    monkeypatch.setattr("video.encoding.source_codec", lambda _p: "h264")
    monkeypatch.setattr("analysis.audio_features.extract_audio_features", lambda _p: {})
    monkeypatch.setattr("analysis.visual_features.extract_visual_features", lambda _p: {})
    monkeypatch.setattr("analysis.hype.audience_signals", lambda *_a, **_k: (None, None))
    monkeypatch.setattr(pipeline, "_with_usable_model", lambda cfg: cfg)
    monkeypatch.setattr(pipeline, "create_backend", lambda cfg: object())
    monkeypatch.setattr(pipeline, "clip_direction", lambda *_a, **_k: None)
    monkeypatch.setattr(pipeline, "_share_the_cpu", lambda workers: None)
    found: list = []
    renders: list = []
    titled: list = []
    monkeypatch.setattr(pipeline, "find_clips", lambda *_a, **_k: ([copy.deepcopy(c) for c in found], []))

    def titles(candidates, *_a, **_k):
        titled.extend((c.start, c.end, c.score) for c in candidates)
        return [ClipMetadata(title=f"Clip at {c.start:g}", description="", hashtags=[],
                             headline=f"AT {c.start:g}", subline="QUARKBLOOM") for c in candidates]

    def render(source, candidate, segments, clip_dir, config, render_opts=None, content_language="en"):
        renders.append(((candidate.start, candidate.end), copy.deepcopy(render_opts)))
        clip_dir.mkdir(parents=True, exist_ok=True)
        out = clip_dir / f"clip_{int(candidate.start):05d}-{int(candidate.end):05d}.mp4"
        out.write_bytes(b"a clip")
        return out, json.dumps(render_opts) if render_opts else ""

    monkeypatch.setattr(pipeline, "generate_metadata_batch", titles)
    monkeypatch.setattr(pipeline, "_render_files", render)
    runs = iter(range(1, 100))

    def run(moments, **clips):
        config = load_config(BUNDLED_CONFIG)
        config["paths"]["data_dir"] = str(data_dir)
        config["clips"].update({"captions": False, "min_duration": 10, "max_duration": 60, "min_score": 55,
                                "max_clips_per_video": 0, **clips})
        found[:] = moments
        renders.clear()
        titled.clear()
        db = StateDB(tmp_path / f"state-{next(runs)}.db")
        try:
            pipeline.process_video("local:stream", config, db, force=True)
            rows = [dict(r) for r in db.conn.execute(
                "SELECT start_s, end_s, score, hook, title, status, scores, render_opts FROM clips"
                " WHERE video_id = 'vid321' ORDER BY start_s")]
            return rows, sorted(renders, key=lambda r: r[0]), list(titled), db.get_outcome("vid321")
        finally:
            db.conn.close()

    return run


def _found():
    return [_cand(10, 60), _cand(100, 90), _cand(200, 75), _cand(300, 70)]


def _card(start: float) -> dict:
    """The Highlights card _clip_opts(meta) gives the clip at `start`."""
    return {"headline": f"AT {start:g}", "subline": "QUARKBLOOM"}


def test_suggestions_are_kept_on_the_clip_never_in_its_render_options(made, add_trimmer):
    add_trimmer()
    for style, options in (({}, [None] * 4), (HIGHLIGHTS, [_card(s) for s in (10, 100, 200, 300)])):
        rows, renders, _titled, outcome = made(_found(), edit=[_trim(edits={"*": TRIM})], **style)
        # Every clip renders with exactly the options it gets without the plugin: _clip_opts(meta).
        assert renders == [((s, s + 20.0), o) for s, o in zip((10.0, 100.0, 200.0, 300.0), options)]
        for row, opts in zip(rows, options, strict=True):
            assert row["render_opts"] == (json.dumps(opts) if opts else "")
            (kept,) = json.loads(row["scores"])["plugin_edits"]
            assert (kept["edit"], kept["state"]) == (_trimmed(row["start_s"]), "new")
        assert outcome["steps"] == [_edit_entry(given=4, suggested=4)]


def test_a_job_naming_only_edit_makes_the_same_clips(made, add_trimmer):
    add_trimmer()
    for style in ({}, HIGHLIGHTS):
        plain = made(_found(), **style)
        edited = made(_found(), edit=[_trim(edits={"*": TRIM})], **style)
        # The same windows, scores, titles and order, rendered with byte-identical options.
        keep = ("start_s", "end_s", "score", "hook", "title", "status", "render_opts")
        assert [{k: r[k] for k in keep} for r in edited[0]] == [{k: r[k] for k in keep} for r in plain[0]]
        assert json.dumps(edited[1]) == json.dumps(plain[1])
        assert edited[2] == plain[2] == [(10.0, 30.0, 60), (100.0, 120.0, 90), (200.0, 220.0, 75),
                                         (300.0, 320.0, 70)]
        # The scores differ only by the suggestions kept beside them.
        for a, b in zip(plain[0], edited[0], strict=True):
            scores = json.loads(b["scores"])
            kept = scores.pop("plugin_edits")
            assert kept and scores == json.loads(a["scores"])
        assert "steps" not in plain[3] and edited[3]["steps"] == [_edit_entry(given=4, suggested=4)]


def test_edit_plugins_see_only_the_clips_that_are_made(made, add_trimmer, tmp_path):
    add_trimmer()
    trace = tmp_path / "trace.jsonl"
    rows, renders, titled, outcome = made(_found(), rate=[_choice(scores="m1=95")], max_clips_per_video=2,
                                          edit=[_trim(edits={"*": {"fade_in": 0.3}}, trace=str(trace))])
    # The rater and the clip limit chose two clips; the editor saw those two, in that order.
    assert titled == [(10.0, 30.0, 95), (100.0, 120.0, 90)]
    (run,) = _trace(trace)
    assert [(m["id"], m["start"], m["end"], m["score"]) for m in run["moments"]] == [
        ("m1", 10.0, 30.0, 95), ("m2", 100.0, 120.0, 90)]
    assert run["limits"] == {"max_clips": 2, "min_duration": 10, "max_duration": 60, "crops": CROPS}
    assert [w for w, _opts in renders] == [(10.0, 30.0), (100.0, 120.0)]
    assert [len(json.loads(r["scores"])["plugin_edits"]) for r in rows] == [1, 1]
    assert [e["steps"] for e in outcome["steps"]] == [["rate"], ["edit"]]
    assert outcome["steps"][1] == _edit_entry()


def test_a_job_without_edit_never_reaches_the_edit_step_and_renders_as_before(made, monkeypatch):
    """A job that names no edit plugin renders every clip with the options it
    always did (_clip_opts(meta), byte for byte). With no step at all,
    plugins.steps is never imported; with Rate & understand alone, its edit
    step is never called."""
    plugins = importlib.import_module("plugins")
    found = _found()
    with monkeypatch.context() as m:
        m.setitem(sys.modules, "plugins.steps", None)  # importing it now fails
        m.delattr(plugins, "steps", raising=False)
        bare = made(found)
        card = made(found, **HIGHLIGHTS)
    assert json.dumps(bare[1]) == json.dumps([((s, s + 20.0), None) for s in (10.0, 100.0, 200.0, 300.0)])
    assert json.dumps(card[1]) == json.dumps([((s, s + 20.0), _card(s)) for s in (10.0, 100.0, 200.0, 300.0)])
    assert all(r["render_opts"] == "" for r in bare[0])

    from plugins import steps

    def never(*_a, **_k):
        raise AssertionError("a job without edit reached the edit step")

    monkeypatch.setattr(steps, "suggest_edits", never)
    rated = made(found, understand=[_choice(notes="*=The final round")])
    assert json.dumps(rated[1]) == json.dumps(bare[1])
    assert not any("plugin_edits" in json.loads(r["scores"]) for r in rated[0])
    assert [e["steps"] for e in rated[3]["steps"]] == [["understand"]]
