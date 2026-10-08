"""Rate & understand: plugins that look at a video's moments once they're found
(plugins/steps.py, and plugins/runner.answer_moments for each run).

These tests run real child processes: tests/fixtures/plugins/stepper rates
the moments it is handed and says what happens in them as its settings say,
or fails the way it is told to. Copies of it under other ids stand in for
several plugins chosen in one job. Every game named is Quarkbloom Arena, a
made-up game.
"""

import copy
import json
import shutil
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
