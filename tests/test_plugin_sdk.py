"""The plugin SDK (sdk/python/clipskitty_sdk): the contract and its helpers.

Standard library only, like the SDK itself, so these run in CI's bare
environment. The SDK is put on the path the way Clips Kitty puts it on a
plugin's: as a folder, not an installed package.
"""

import io
import json
import os
import sys
from pathlib import Path

import pytest

SDK = Path(__file__).resolve().parent.parent / "sdk" / "python"
if str(SDK) not in sys.path:
    sys.path.insert(0, str(SDK))

from clipskitty_sdk import (  # noqa: E402
    ContractError,
    Moment,
    check_job,
    check_result,
    contract,
    host,
    read_job,
    run,
)


def _job(tmp_path, **extra):
    data = {"plugin": {"id": "example-dev/example", "version": "1.0.0"},
            "video": {"path": str(tmp_path / "v.mp4"), "id": "v1", "title": "T", "duration": 120.0},
            "settings": {"k": 1}, "limits": {"max_clips": 3, "min_duration": 10, "max_duration": 60},
            **extra}
    host.write_job(tmp_path / "job", data, {"language": "en", "segments": [
        {"start": 0, "end": 2, "text": "hi", "words": None}]})
    return tmp_path / "job"


# ---- the files ------------------------------------------------------------------


def test_a_written_job_reads_back(tmp_path):
    out = io.StringIO()
    job = read_job(_job(tmp_path), out=out)
    assert job.plugin_id == "example-dev/example" and job.plugin_version == "1.0.0"
    assert job.video.path == tmp_path / "v.mp4" and job.video.duration == 120.0
    assert job.transcript.language == "en" and job.transcript.segments()[0]["text"] == "hi"
    assert job.limits.max_clips == 3 and job.settings == {"k": 1}
    assert job.output_dir.is_dir()
    assert job.models == {} and job.tools.ffmpeg is None


def test_models_read_back_with_their_source_and_no_path_when_unknown(tmp_path):
    models = {"detector": {"source": "huggingface", "id": "example-org/example-model", "path": str(tmp_path / "snap"),
                           "revision": "0" * 40, "files": {"model.onnx": str(tmp_path / "snap" / "model.onnx")}},
              "chat": {"source": "ollama", "id": "example-model:1b", "path": "", "revision": "", "files": {}}}
    job = read_job(_job(tmp_path, models=models), out=io.StringIO())
    detector, chat = job.models["detector"], job.models["chat"]
    assert detector.path == tmp_path / "snap" and detector.files["model.onnx"].endswith("model.onnx")
    assert (detector.source, detector.id, detector.revision) == ("huggingface", "example-org/example-model", "0" * 40)
    assert chat.path is None and chat.source == "ollama" and chat.id == "example-model:1b"


def test_job_problems_are_all_listed():
    problems = check_job({"plugin_api": 9, "video": {"duration": "long"}, "settings": []})
    assert any("plugin_api" in p for p in problems)
    assert any("plugin.id" in p for p in problems)
    assert any("video.path" in p for p in problems)
    assert any("settings must be an object" in p for p in problems)
    assert any("output_dir" in p for p in problems)
    assert check_job("not a mapping") == ["job.json must be a JSON object"]


@pytest.mark.parametrize("result, fragment", [
    ({"plugin_api": 1, "ranges": [{"start": 5, "end": 5}]}, "0 <= start < end"),
    ({"plugin_api": 1, "ranges": [{"start": -1, "end": 5}]}, "0 <= start < end"),
    ({"plugin_api": 1, "ranges": [{"start": "a", "end": 5}]}, "numbers of seconds"),
    ({"plugin_api": 1, "ranges": [{"start": 1, "end": 5, "score": 101}]}, "0 to 100"),
    ({"plugin_api": 1, "ranges": [{"start": 1, "end": 5, "label": "x" * 65}]}, "label"),
    ({"plugin_api": 1, "ranges": "all of them"}, "must be a list"),
    ({"plugin_api": 2, "ranges": []}, "plugin_api"),
    ({"plugin_api": 1, "ranges": [], "clips": [{"path": "a.mp4"}]}, "planned"),
    ({"plugin_api": 1, "ranges": [{"start": float("nan"), "end": 5}]}, "numbers of seconds"),
])
def test_bad_results_are_refused_with_a_reason(result, fragment):
    problems = check_result(result)
    assert problems and any(fragment in p for p in problems), problems


def test_a_good_result_passes():
    assert check_result({"plugin_api": 1, "ranges": [
        {"start": 0, "end": 12.5, "score": 80, "label": "goal", "title": "t", "reason": "r"},
        {"start": 20, "end": 30}], "notes": "fine"}) == []


# ---- finding, understanding and rating: the files ------------------------------------


def _a_job(**extra) -> dict:
    return {"plugin_api": 1, "plugin": {"id": "example-dev/quarkbloom-rater", "version": "1.0.0"},
            "settings": {}, "limits": {"max_clips": 3, "min_score": 55}, "output_dir": "out", **extra}


def test_check_result_without_steps_ignores_new_keys_as_before():
    """A finder written before the moment steps may already write keys that now
    mean something. Checked as a find run, they are ignored as they always were."""
    stray = {"plugin_api": 1, "ranges": [{"start": 1, "end": 5, "context": "junk"}],
             "moments": [{"id": 7, "score": 999}, "junk"]}
    assert check_result(stray) == []
    assert check_result(stray, steps=None) == []
    assert check_result(stray, steps=("find",)) == []
    assert check_result({**stray, "moments": "junk"}, steps=["find"]) == []
    # The same keys from a run that was asked for them are refused.
    assert check_result(stray, steps=("find", "understand")) == [
        "ranges[0]: context must be a list of at most 5 notes of 1 to 160 characters",
        "moments[0]: id must be text of 1 to 32 characters",
        "moments[0]: score must be a number from 0 to 100, or left out",
        "moments[1] must be an object"]


def test_check_result_with_steps_checks_moments_and_context():
    steps = ("understand", "rate")
    good = {"plugin_api": 1, "ranges": [], "notes": "fine", "moments": [
        {"id": "m1", "score": 85, "reason": "the caster called a big play",
         "context": ["The team that was behind is catching up here"]},
        {"id": "m2", "score": 0}, {"id": "m3", "context": ["x" * 160] * 5}, {"id": "m4"}]}
    assert check_result(good, steps=steps) == []
    assert check_result({"plugin_api": 1, "ranges": []}, steps=["rate"]) == []
    bad = {"plugin_api": 1, "ranges": [], "moments": [
        {"id": "m1", "score": 70},
        {"id": "m1", "score": 101},
        {"id": "m2", "context": ["x" * 161]},
        {"id": "m3", "context": ["a note"] * 6},
        {"id": "m4", "reason": "r" * 501},
        {"id": "m5", "context": "one note, not a list"},
        {"id": "m6", "context": [""]},
        {"id": "", "score": True},
        {"id": "m" * 33},
        "m7"]}
    assert check_result(bad, steps=steps) == [
        "moments[1]: moment m1 is answered twice",
        "moments[1]: score must be a number from 0 to 100, or left out",
        "moments[2]: context must be a list of at most 5 notes of 1 to 160 characters",
        "moments[3]: context must be a list of at most 5 notes of 1 to 160 characters",
        "moments[4]: reason must be text of at most 500 characters",
        "moments[5]: context must be a list of at most 5 notes of 1 to 160 characters",
        "moments[6]: context must be a list of at most 5 notes of 1 to 160 characters",
        "moments[7]: id must be text of 1 to 32 characters",
        "moments[7]: score must be a number from 0 to 100, or left out",
        "moments[8]: id must be text of 1 to 32 characters",
        "moments[9] must be an object",
    ]
    # Either step alone checks the answers; a find run's own ranges are checked
    # for notes only when it was asked to understand them.
    assert check_result(bad, steps=["understand"]) == check_result(bad, steps=["rate"]) == check_result(bad, steps=steps)
    too_many = [{"id": f"m{i}"} for i in range(1, 202)]
    for moments in (too_many, "all of them", {"m1": {}}):
        assert check_result({"plugin_api": 1, "ranges": [], "moments": moments}, steps=["rate"]) == [
            "moments must be a list of at most 200 answers"]
    notes = {"plugin_api": 1, "ranges": [
        {"start": 1, "end": 5, "context": ["First quark burst of the match"]},
        {"start": 6, "end": 9, "context": ["x" * 161]},
        {"start": 10, "end": 19, "context": ["a"] * 6}]}
    assert check_result(notes, steps=["find", "understand"]) == [
        "ranges[1]: context must be a list of at most 5 notes of 1 to 160 characters",
        "ranges[2]: context must be a list of at most 5 notes of 1 to 160 characters"]
    assert check_result(notes, steps=["understand", "rate"]) == []  # a moment run's ranges carry no notes to check
    assert (contract.MAX_CONTEXT, contract.MAX_CONTEXT_ITEMS, contract.MAX_MOMENT_ID) == (160, 5, 32)
    assert contract.STEPS == ("find", "understand", "rate")


def test_check_job_accepts_unknown_step_names_and_moment_keys():
    assert check_job(_a_job()) == []
    assert check_job(_a_job(steps=["find"])) == []
    assert check_job(_a_job(steps=["understand", "rate", "edit", "export"], moments=[
        {"id": "m1", "start": 812.0, "end": 841.5, "score": 72, "found_score": 72, "found_by": "clipskitty",
         "label": "", "signals": {"text": 61}, "title": "", "reason": "", "context": [],
         "mood": "a key a later SDK may add"},
        {"id": "m2", "start": 900, "end": 920}])) == []
    assert check_job(_a_job(steps=[], moments=[])) == []


def test_check_job_refuses_a_moment_without_id_start_or_end():
    problems = check_job(_a_job(steps=["rate"], moments=[
        {"start": 1, "end": 2}, {"id": "m2", "end": 3}, {"id": "m3", "start": 1}, {"id": 4, "start": 1, "end": 2},
        {"id": "m5", "start": "1", "end": 2}, "m6", {"id": "m7", "start": 1, "end": 2}]))
    assert problems == [f"moments[{i}] needs an id, a start and an end" for i in range(6)]
    assert check_job(_a_job(moments={"m1": {"start": 1, "end": 2}})) == ["moments must be a list"]
    assert check_job(_a_job(steps="rate")) == ["steps must be a list of step names"]
    assert check_job(_a_job(steps=["rate", 2])) == ["steps must be a list of step names"]


# ---- finding, understanding and rating: the Job and the host -------------------------


SEGMENTS = [
    {"start": 810.0, "end": 815.0, "text": " Here comes the", "words": None},
    {"start": 815.0, "end": 830.0, "text": "triple bloom! ", "words": None},
    {"start": 841.5, "end": 850.0, "text": "after the moment", "words": None},
    {"start": 905.0, "end": 910.0, "text": "a quark burst", "words": None},
]

MOMENTS = [
    {"id": "m1", "start": 812.0, "end": 841.5, "score": 72, "found_score": 72, "found_by": "clipskitty",
     "label": "", "signals": {"text": 61, "audio": 70, "visual": 44, "engagement": 66},
     "title": "He holds the bridge alone", "reason": "loud reaction and fast speech",
     "context": ["This happens in the final round of a Quarkbloom Arena match"]},
    {"id": "m2", "start": 900.0, "end": 925.0, "score": 64, "found_score": 58,
     "found_by": "example-dev/quarkbloom-finder", "label": "quark_burst", "signals": {}, "title": "",
     "reason": "", "context": []},
    {"id": "m3", "start": 1000.0, "end": 1012.0, "score": 50, "found_score": 50, "found_by": "clipskitty",
     "label": "", "signals": {"text": 40}, "title": "", "reason": "", "context": []},
]

GRADER = {"id": "example-dev/quarkbloom-grader", "version": "1.0.0", "permissions": ["transcript.read"]}


def _moment_job(tmp_path, steps=("understand", "rate"), *, moments=MOMENTS, manifest=GRADER):
    """A run that rates or understands moments, its folder built as the app
    builds it: the Job, and where it writes its lines."""
    folder = tmp_path / "moment-job"
    job, transcript = host.build_job(
        manifest, transcript={"language": "en", "segments": SEGMENTS},
        limits={"max_clips": 3, "min_duration": 10, "max_duration": 60, "min_score": 55},
        output_dir=folder / "out", steps=list(steps), moments=moments)
    host.write_job(folder, job, transcript)
    out = io.StringIO()
    return read_job(folder, out=out), out


def _logged(out) -> list[str]:
    return [json.loads(line)["message"] for line in out.getvalue().splitlines()]


def _answer(job) -> dict:
    return json.loads(job.finish().read_text(encoding="utf-8"))


def test_a_find_job_json_is_unchanged(tmp_path):
    """A plugin that uses none of the new words gets the job.json it always
    did, to the byte. A finder that does gets the same, plus its steps."""
    manifest = {"id": "example-dev/example", "version": "1.0.0",
                "permissions": ["video.read", "transcript.read", "ffmpeg", "ollama"],
                "settings": {"level": {"type": "integer", "default": 3}}}
    ollama = {"host": "http://localhost:11434", "model": ""}
    kwargs = {"video": {"path": "v.mp4", "id": "v", "title": "T", "duration": 120.0, "games": []},
              "transcript": {"language": "en", "segments": []},
              "limits": {"max_clips": 3, "min_duration": 10, "max_duration": 60}, "focus": "goals",
              "ffmpeg": "ffmpeg", "ffprobe": "ffprobe", "ollama": ollama, "models": {},
              "output_dir": tmp_path / "out"}
    before = {"plugin_api": 1, "plugin": {"id": "example-dev/example", "version": "1.0.0"},
              "settings": {"level": 3}, "limits": {"max_clips": 3, "min_duration": 10, "max_duration": 60},
              "focus": "goals", "models": {}, "tools": {"ffmpeg": "ffmpeg", "ffprobe": "ffprobe", "ollama": ollama},
              "output_dir": str(tmp_path / "out"),
              "video": {"path": "v.mp4", "id": "v", "title": "T", "duration": 120.0, "games": []}}
    job, transcript = host.build_job(manifest, **kwargs)
    assert json.dumps(job) == json.dumps(before)  # the same keys, in the same order
    assert host.build_job(manifest, steps=None, moments=None, **kwargs) == (job, transcript)
    folder = tmp_path / "job"
    host.write_job(folder, before, transcript)
    written = (folder / "job.json").read_bytes()
    host.write_job(folder, job, transcript)
    assert (folder / "job.json").read_bytes() == written
    plain = read_job(folder, out=io.StringIO())
    assert plain.steps == ("find",) and plain.moments == [] and plain.limits.min_score is None
    assert plain.wants("find") and not plain.wants("understand") and not plain.wants("rate")

    from clipskitty_sdk import manifest as manifests

    describes = {**manifest, "inputs": ["video", "transcript"], "outputs": ["ranges", "context"]}
    assert manifests.uses_steps(describes)
    job, _ = host.build_job(describes, steps=manifests.find_steps(describes), **kwargs)
    assert job["steps"] == ["find", "understand"] and "moments" not in job
    assert {key: value for key, value in job.items() if key != "steps"} == before
    host.write_job(folder, job, transcript)
    finder = read_job(folder, out=io.StringIO())
    assert finder.steps == ("find", "understand") and finder.moments == [] and finder.limits.min_score is None


def test_a_plain_finders_result_is_unchanged(tmp_path):
    """A finder's result.json is what it always was, to the byte, and the host
    reads it back as it always did, keys it doesn't use and all."""
    before = {"plugin_api": 1, "ranges": [
        {"start": 1.0, "end": 20.0, "label": "goal", "title": "Goal", "reason": "net bulges", "score": 70.0},
        {"start": 30.0, "end": 45.0, "label": "", "title": "", "reason": ""}], "notes": "two goals"}
    for name, extra in (("no-steps", {}), ("find", {"steps": ["find"]})):
        out = io.StringIO()
        job = read_job(_job(tmp_path / name, **extra), out=out)
        first = job.add_range(1, 20, score=70, label="goal", title="Goal", reason="net bulges")
        job.add_range(30, 45)
        job.understand(first, "A goal")  # not asked to understand: logged, and nothing kept
        path = job.finish(notes="two goals")
        assert path.read_text(encoding="utf-8") == json.dumps(before, ensure_ascii=False, indent=1)
        assert _logged(out) == ["understand ignored: this job didn't ask for notes"]
    stray = {**before, "ranges": [{**before["ranges"][0], "context": "junk"}], "moments": "junk"}
    path.write_text(json.dumps(stray), encoding="utf-8")
    for steps in (None, ("find",), ["find"]):
        result = host.read_result(path.parent, steps=steps)
        assert result["ranges"][0]["context"] == "junk" and result["moments"] == "junk"
        assert result == host.read_result(path.parent)


def test_moments_lose_title_reason_and_context_without_transcript_read(tmp_path):
    bare = {**GRADER, "permissions": []}
    original = json.loads(json.dumps(MOMENTS))
    job, transcript = host.build_job(bare, transcript={"language": "en", "segments": SEGMENTS},
                                     output_dir=tmp_path / "out", steps=["rate"], moments=MOMENTS)
    assert transcript is None
    assert [set(m) for m in job["moments"]] == [
        {"id", "start", "end", "score", "found_score", "found_by", "label", "signals"}] * 3
    assert job["moments"][0]["signals"] == MOMENTS[0]["signals"] and job["moments"][1]["label"] == "quark_burst"
    job["moments"][0]["signals"]["text"] = 0  # a copy is handed over, never the caller's own
    assert MOMENTS == original
    job, _ = host.build_job(GRADER, output_dir=tmp_path / "out", steps=["rate"], moments=MOMENTS)
    assert job["moments"] == MOMENTS and job["steps"] == ["rate"]
    assert not check_job(job)

    rater, _ = _moment_job(tmp_path, ("rate",), manifest=bare)
    m1 = rater.moments[0]
    assert (m1.title, m1.reason, m1.context) == ("", "", ())
    assert (m1.score, m1.found_by, m1.signals["audio"]) == (72, "clipskitty", 70)
    assert rater.transcript is None


def test_rate_and_understand_answer_handed_moments(tmp_path):
    job, out = _moment_job(tmp_path)
    assert job.steps == ("understand", "rate") and job.wants("understand") and job.wants("rate")
    assert not job.wants("find")
    assert job.limits.min_score == 55 and job.limits.max_clips == 3
    m1, m2, m3 = job.moments
    assert m1 == Moment(id="m1", start=812.0, end=841.5, score=72.0, found_score=72.0, found_by="clipskitty",
                        label="", title="He holds the bridge alone", reason="loud reaction and fast speech",
                        context=("This happens in the final round of a Quarkbloom Arena match",),
                        signals={"text": 61, "audio": 70, "visual": 44, "engagement": 66})
    assert m1.duration == 29.5 and m1.notes == ()
    assert (m2.found_by, m2.label, m2.score, m2.found_score) == ("example-dev/quarkbloom-finder", "quark_burst", 64, 58)
    assert job.text(m1) == "Here comes the triple bloom!"  # the segment from 841.5 starts as m1 ends
    assert job.text(m3) == ""
    job.rate(m1, 85, reason="the caster called a big play")
    job.understand(m2, "The team that was behind is catching up here")
    assert (m1.score, m1.found_score) == (85, 72)
    assert m2.notes == ("The team that was behind is catching up here",) and m2.context == ()
    data = _answer(job)
    assert data == {"plugin_api": 1, "ranges": [], "moments": [
        {"id": "m1", "score": 85.0, "reason": "the caster called a big play"},
        {"id": "m2", "context": ["The team that was behind is catching up here"]}]}
    assert check_result(data, steps=job.steps) == [] and _logged(out) == []
    answers, ignored = host.read_answers(job.folder, steps=job.steps, ids=["m1", "m2", "m3"])
    assert answers == {"m1": {"score": 85.0, "reason": "the caster called a big play"},
                       "m2": {"context": ["The team that was behind is catching up here"]}}
    assert ignored == []


def test_add_range_moment_fields_and_rate_on_it(tmp_path):
    out = io.StringIO()
    job = read_job(_job(tmp_path / "rated"), out=out)
    r1 = job.add_range(10, 40, label="quark_burst", title="Burst", reason="loud")
    r2 = job.add_range(50, 70, score=80)
    assert r1 == Moment(id="r1", start=10.0, end=40.0, score=None, found_score=None,
                        found_by="example-dev/example", label="quark_burst", title="Burst", reason="loud")
    assert (r1.context, r1.signals, r1.notes, r1.duration) == ((), {}, (), 30.0)
    assert (r2.id, r2.score, r2.found_score, r2.found_by) == ("r2", 80.0, 80.0, "example-dev/example")
    assert job.moments == []  # a find run is handed none
    job.rate(r1, 77, reason="graded")  # the plugin's own range: allowed in any run
    job.rate(r2, 90)
    assert (r1.score, r1.reason, r1.found_score) == (77.0, "graded", None)
    assert (r2.score, r2.reason, r2.found_score) == (90.0, "", 80.0)
    assert _logged(out) == []
    # Exactly what add_range(score=, reason=) would have written.
    direct = read_job(_job(tmp_path / "direct"), out=io.StringIO())
    direct.add_range(10, 40, score=77, label="quark_burst", title="Burst", reason="graded")
    direct.add_range(50, 70, score=90)
    assert job.finish().read_text(encoding="utf-8") == direct.finish().read_text(encoding="utf-8")


def test_understand_counts_only_this_runs_notes(tmp_path):
    earlier = [f"Earlier note {i}" for i in range(1, 9)]
    job, _ = _moment_job(tmp_path, ("understand",), moments=[{**MOMENTS[0], "context": earlier}])
    m = job.moments[0]
    assert m.context == tuple(earlier)
    own = [f"Own note {i}" for i in range(1, 6)]
    for note in own:
        job.understand(m, note)
    assert m.notes == tuple(own) and m.context == tuple(earlier)
    with pytest.raises(ContractError):
        job.understand(m, "Own note 6")
    assert _answer(job)["moments"] == [{"id": "m1", "context": own}]


def test_answers_not_asked_for_are_logged_and_left_out(tmp_path):
    job, out = _moment_job(tmp_path / "understand", ("understand",))
    m1 = job.moments[0]
    job.rate(m1, 90, reason="big")
    job.rate(m1, 95)
    job.understand(m1, "Something happens")
    assert m1.score == 72
    assert _logged(out) == ["rate ignored: this job didn't ask for ratings"]  # said once
    assert _answer(job)["moments"] == [{"id": "m1", "context": ["Something happens"]}]

    job, out = _moment_job(tmp_path / "rate", ("rate",))
    m1 = job.moments[0]
    job.understand(m1, "Something happens")
    job.understand(m1, "Something more")
    job.rate(m1, 90)
    assert m1.notes == ()
    assert _logged(out) == ["understand ignored: this job didn't ask for notes"]
    assert _answer(job)["moments"] == [{"id": "m1", "score": 90.0, "reason": ""}]

    # A find run asked to understand keeps the notes on the plugin's own ranges.
    finder = read_job(_job(tmp_path / "describes", steps=["find", "understand"]), out=io.StringIO())
    r1 = finder.add_range(1, 20, score=70)
    finder.add_range(30, 40)
    finder.understand(r1, "First quark burst of the match")
    ranges = _answer(finder)["ranges"]
    assert ranges[0]["context"] == ["First quark burst of the match"] and "context" not in ranges[1]
    assert finder.ranges[0] == {"start": 1.0, "end": 20.0, "label": "", "title": "", "reason": "", "score": 70.0}


def test_rating_twice_keeps_the_last(tmp_path):
    job, _ = _moment_job(tmp_path, ("rate",))
    m1 = job.moments[0]
    job.rate(m1, 80, reason="first look")
    job.rate(m1, 40, reason="second look")
    assert (m1.score, m1.found_score) == (40, 72)
    assert _answer(job)["moments"] == [{"id": "m1", "score": 40.0, "reason": "second look"}]


def test_rate_cuts_reason_to_500(tmp_path):
    job, _ = _moment_job(tmp_path, ("rate",))
    job.rate(job.moments[0], 70, reason="r" * 600)
    own = job.add_range(1, 20)
    job.rate(own, 70, reason="o" * 600)
    data = _answer(job)
    assert contract.MAX_REASON == 500
    assert data["moments"][0]["reason"] == "r" * 500 and data["ranges"][0]["reason"] == own.reason == "o" * 500


def test_rate_refuses_a_bad_score_or_a_foreign_moment(tmp_path):
    job, _ = _moment_job(tmp_path / "a")
    m1 = job.moments[0]
    for score in (-1, 100.5, "90", None, True, float("nan"), float("inf")):
        with pytest.raises(ContractError) as e:
            job.rate(m1, score)
        assert str(e.value) == f"rate: score must be a number from 0 to 100 (got {score!r})"
    job.rate(m1, 0)
    job.rate(m1, 100)
    assert m1.score == 100
    with pytest.raises(ContractError) as e:
        job.rate(Moment(id="m9", start=1, end=2, score=50), 50)
    assert str(e.value) == "rate: m9 is not a moment of this job"
    other, _ = _moment_job(tmp_path / "b")
    with pytest.raises(ContractError) as e:
        job.rate(other.moments[0], 50)  # the same id, but another job's moment
    assert str(e.value) == "rate: m1 is not a moment of this job"
    with pytest.raises(ContractError) as e:
        job.rate("m1", 50)
    assert str(e.value) == "rate: 'm1' is not a moment of this job"
    with pytest.raises(ContractError) as e:
        job.understand(Moment(id="m9", start=1, end=2), "a note")
    assert str(e.value) == "understand: m9 is not a moment of this job"
    assert _answer(job)["moments"] == [{"id": "m1", "score": 100.0, "reason": ""}]


def test_understand_cuts_and_refuses_a_sixth(tmp_path):
    job, _ = _moment_job(tmp_path, ("understand",))
    m = job.moments[0]
    job.understand(m, "  The caster\n\tshouts\x00 here\x1b  ")
    job.understand(m, "a" * 200)
    job.understand(m, "b" * 159 + " and more")  # cut at 160 characters, the space at the end dropped
    job.understand(m, "   \n\x07 ")  # empty once cleaned: ignored, and not counted
    job.understand(m, "")
    job.understand(m, "Fourth")
    job.understand(m, "Fifth")
    assert m.notes == ("The caster shouts here", "a" * 160, "b" * 159, "Fourth", "Fifth")
    job.understand(m, " \n ")  # still nothing to keep, so nothing to refuse
    with pytest.raises(ContractError) as e:
        job.understand(m, "Sixth")
    assert str(e.value) == "understand: at most 5 notes for one moment"
    assert len(m.notes) == contract.MAX_CONTEXT_ITEMS == 5


def test_text_needs_a_transcript(tmp_path):
    job, _ = _moment_job(tmp_path / "bare", ("rate",), manifest={**GRADER, "permissions": []})
    with pytest.raises(ContractError) as e:
        job.text(job.moments[0])
    assert str(e.value) == ("text: this job has no transcript: add transcript to inputs and "
                            "transcript.read to permissions")
    job, _ = _moment_job(tmp_path / "said", ("rate",))
    m1, m2, _ = job.moments
    assert job.text(m1) == "Here comes the triple bloom!"
    (job.folder / "transcript.json").unlink()  # read once, then kept
    assert job.text(m2) == "a quark burst"
    assert job.text(job.add_range(800, 816)) == "Here comes the triple bloom!"


def test_unknown_steps_and_moment_keys_are_ignored(tmp_path):
    later = [{**MOMENTS[0], "mood": "tense", "found_with": {"model": "a later key"}}]
    job, out = _moment_job(tmp_path, ["rate", "edit", "export"], moments=later)
    assert job.steps == ("rate", "edit", "export")
    assert job.wants("rate") and not job.wants("edit") and not job.wants("export") and not job.wants("find")
    m = job.moments[0]
    assert not hasattr(m, "mood") and not hasattr(m, "found_with") and m.score == 72
    job.rate(m, 90)
    assert _answer(job)["moments"] == [{"id": "m1", "score": 90.0, "reason": ""}]
    assert _logged(out) == []


@pytest.mark.parametrize("steps", [None, ["find"], ["find", "understand"], ["understand"], ["rate"],
                                   ["understand", "rate"]])
def test_every_answer_the_sdk_writes_passes_check_result_with_its_steps(tmp_path, steps):
    """Whatever a plugin calls, in whatever run, finish() writes an answer
    that passes the app's check for that run's steps, and the host reads it."""
    moment_run = steps is not None and "find" not in steps
    folder = tmp_path / "job"
    data, transcript = host.build_job(
        GRADER, transcript={"language": "en", "segments": SEGMENTS}, output_dir=folder / "out",
        limits={"max_clips": 3, "min_duration": 10, "max_duration": 60}, steps=steps,
        moments=MOMENTS if moment_run else None)
    host.write_job(folder, data, transcript)
    job = read_job(folder, out=io.StringIO())
    noisy = "a\x00 note\n\twith https://example.com/clip and " + "more words " * 30
    for i in range(3):
        own = job.add_range(10 * i, 10 * i + 8, score=50, label="l" * 100, title="t" * 300, reason="r" * 600)
        job.rate(own, 99.5, reason="q" * 700)
        for _ in range(5):
            job.understand(own, noisy)
    for m in job.moments:
        job.rate(m, 0, reason="z" * 900)
        for _ in range(5):
            job.understand(m, noisy)
    written = json.loads(job.finish(notes="n" * 5000).read_text(encoding="utf-8"))
    assert check_result(written, steps=steps) == [] and check_result(written, steps=job.steps) == []
    assert ("moments" in written) == moment_run
    assert any("context" in r for r in written["ranges"]) == job.wants("understand")
    if moment_run:
        answers, _ = host.read_answers(folder, steps=steps, ids=[m["id"] for m in MOMENTS])
        assert set(answers) == {"m1", "m2", "m3"}
    else:
        assert len(host.read_result(folder, steps=steps)["ranges"]) == 3


def test_read_answers_ignores_unknown_ids_unasked_fields_and_ranges(tmp_path):
    folder = tmp_path / "run"
    folder.mkdir()
    (folder / "result.json").write_text(json.dumps({
        "plugin_api": 1, "ranges": [{"start": 1, "end": 5}, {"start": 6, "end": 9}],
        "moments": [
            {"id": "m1", "score": 90, "reason": "a big play",
             "context": ["A note\nwith a link https://example.com/x", " \x07 "]},
            {"id": "m9", "score": 50},
            {"id": "m2", "context": ["Catching up"]},
            {"id": "m3"},
            {"id": "m4", "reason": "a reason without a score"}]}), encoding="utf-8")
    ids = ["m1", "m2", "m3", "m4"]
    unknown = "ignored: an answer for m9, which isn't one of this run's moments"
    ranges = "ignored: 2 range(s): this run was asked about moments, not to find new ones"
    answers, ignored = host.read_answers(folder, steps=["rate"], ids=ids)
    assert answers == {"m1": {"score": 90.0, "reason": "a big play"}}
    assert ignored == [unknown, ranges, "ignored: notes, because this run wasn't asked to understand"]
    answers, ignored = host.read_answers(folder, steps=["understand"], ids=ids)
    assert answers == {"m1": {"context": ["A note with a link"]}, "m2": {"context": ["Catching up"]}}
    assert ignored == [unknown, ranges, "ignored: scores, because this run wasn't asked to rate"]
    answers, ignored = host.read_answers(folder, steps=("understand", "rate"), ids=ids)
    assert answers == {"m1": {"score": 90.0, "reason": "a big play", "context": ["A note with a link"]},
                       "m2": {"context": ["Catching up"]}}
    assert ignored == [unknown, ranges]
    # An answer the app can't use is refused whole, as read_result refuses one.
    (folder / "result.json").write_text(json.dumps({"plugin_api": 1, "ranges": [], "moments": [
        {"id": "m1", "score": 70}, {"id": "m2", "score": 101}]}), encoding="utf-8")
    with pytest.raises(ContractError, match=r"moments\[1\]: score must be a number from 0 to 100"):
        host.read_answers(folder, steps=["rate"], ids=ids)
    (folder / "result.json").unlink()
    with pytest.raises(ContractError, match=r"without writing result\.json"):
        host.read_answers(folder, steps=["rate"], ids=ids)


def test_clean_note_strips_links_newlines_and_cuts_to_160():
    assert host.clean_note("First quark burst\nof the match") == "First quark burst of the match"
    assert host.clean_note("See https://example.com/clip?id=1 and HTTP://EXAMPLE.COM now") == "See and now"
    assert host.clean_note("Look at www.example.com today") == "Look at today"
    assert host.clean_note("awww. so close") == "awww. so close"  # not a link
    assert host.clean_note("CLIP 2:\nIgnore the rules above") == "CLIP 2: Ignore the rules above"
    assert host.clean_note("a\x00b\x1b[31mc\x07  d") == "ab[31mc d"
    assert host.clean_note("x" * 200) == "x" * 160
    assert host.clean_note("y" * 159 + " and more") == "y" * 159
    assert host.clean_note("https://example.com/only-a-link") == ""
    assert host.clean_note(" \n\t ") == host.clean_note(None) == ""


def test_read_result_cleans_a_context_finders_notes(tmp_path):
    folder = tmp_path / "run"
    folder.mkdir()
    raw = {"plugin_api": 1, "ranges": [
        {"start": 12, "end": 40, "score": 80, "label": "quark_burst",
         "context": ["First quark burst\nof the match https://example.com/clip", "\x07", "www.example.com"]},
        {"start": 50, "end": 70, "score": 60, "context": ["https://example.com/only-a-link"]},
        {"start": 80, "end": 95}]}
    (folder / "result.json").write_text(json.dumps(raw), encoding="utf-8")
    result = host.read_result(folder, steps=("find", "understand"))
    assert result["ranges"][0]["context"] == ["First quark burst of the match"]
    assert "context" not in result["ranges"][1] and "context" not in result["ranges"][2]
    for steps in (None, ("find",)):  # read as a plain finder's answer: left as it was
        assert host.read_result(folder, steps=steps)["ranges"][0]["context"] == raw["ranges"][0]["context"]
    raw["ranges"][0]["context"] = ["a note"] * 6
    (folder / "result.json").write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(ContractError, match=r"ranges\[0\]: context must be a list"):
        host.read_result(folder, steps=["find", "understand"])
    assert host.read_result(folder)["ranges"][0]["context"] == ["a note"] * 6


# ---- the lines ------------------------------------------------------------------


def test_progress_lines_are_clamped_and_anything_else_is_a_log_line():
    assert json.loads(contract.progress_line(1.7, "x")) == {"type": "progress", "fraction": 1.0, "message": "x"}
    assert contract.parse_line('{"type": "progress", "fraction": -3}\n')["fraction"] == 0.0
    assert contract.parse_line('{"type": "progress", "fraction": "half"}')["fraction"] is None
    assert contract.parse_line("plain text\n") == {"type": "log", "message": "plain text"}
    assert contract.parse_line('{"type": "shout", "message": "hi"}')["type"] == "log"
    assert contract.parse_line('[1, 2]')["type"] == "log"
    assert contract.parse_line(contract.error_line("broke")) == {"type": "error", "message": "broke"}


# ---- the Job --------------------------------------------------------------------


def test_progress_and_log_write_one_json_line_each(tmp_path):
    out = io.StringIO()
    job = read_job(_job(tmp_path), out=out)
    job.progress(0.5, "half way")
    job.log("a note")
    lines = [json.loads(line) for line in out.getvalue().splitlines()]
    assert lines == [{"type": "progress", "fraction": 0.5, "message": "half way"},
                     {"type": "log", "message": "a note"}]


def test_ranges_are_checked_as_they_are_added(tmp_path):
    job = read_job(_job(tmp_path), out=io.StringIO())
    with pytest.raises(ContractError, match="0 <= start < end"):
        job.add_range(10, 5)
    with pytest.raises(ContractError, match="0 to 100"):
        job.add_range(0, 5, score=300)


def test_a_range_outside_the_jobs_lengths_is_logged_not_refused(tmp_path):
    out = io.StringIO()
    job = read_job(_job(tmp_path), out=out)
    job.add_range(0, 90)
    job.add_range(0, 4)
    assert "longer than this job's 60s limit" in out.getvalue()
    assert "shorter than this job's 10s minimum" in out.getvalue()
    assert len(job.ranges) == 2


def test_finish_writes_the_result_atomically(tmp_path):
    job = read_job(_job(tmp_path), out=io.StringIO())
    job.add_range(1, 20, score=70, label="goal", title="Goal", reason="net bulges")
    path = job.finish(notes="one goal")
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data == {"plugin_api": 1, "notes": "one goal", "ranges": [
        {"start": 1.0, "end": 20.0, "score": 70.0, "label": "goal", "title": "Goal", "reason": "net bulges"}]}
    assert not list(path.parent.glob("*.partial"))


def test_fail_says_why_and_exits(tmp_path):
    out = io.StringIO()
    job = read_job(_job(tmp_path), out=out)
    with pytest.raises(SystemExit) as stop:
        job.fail("no kill feed in this video")
    assert stop.value.code == 1
    assert json.loads(out.getvalue()) == {"type": "error", "message": "no kill feed in this video"}


def test_secrets_come_from_the_environment(tmp_path, monkeypatch):
    job = read_job(_job(tmp_path), out=io.StringIO())
    monkeypatch.setenv("CLIPSKITTY_SECRET_API_KEY", "k")
    assert job.secret("api_key") == job.secret("api-key") == "k"
    assert job.secret("other") is None


def test_the_job_folder_can_come_from_the_environment(tmp_path, monkeypatch):
    folder = _job(tmp_path)
    monkeypatch.setattr(sys, "argv", ["main.py"])
    monkeypatch.setenv("CLIPSKITTY_JOB", str(folder))
    assert read_job().plugin_id == "example-dev/example"
    monkeypatch.delenv("CLIPSKITTY_JOB")
    with pytest.raises(ContractError, match="no job folder"):
        read_job()


def test_run_finishes_the_job_and_turns_errors_into_words(tmp_path, monkeypatch, capsys):
    folder = _job(tmp_path)
    run(lambda job: job.add_range(0, 15), folder)
    assert json.loads((folder / "result.json").read_text())["ranges"][0]["end"] == 15.0

    def broken(job):
        raise ValueError("the model file is missing")

    with pytest.raises(SystemExit):
        run(broken, folder)
    lines = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert lines[-1] == {"type": "error", "message": "the model file is missing"}
    assert any("Traceback" in line["message"] for line in lines if line["type"] == "log")


# ---- the host side --------------------------------------------------------------


def test_read_result_fits_ranges_to_the_video(tmp_path):
    folder = tmp_path / "run"
    folder.mkdir()
    (folder / "result.json").write_text(json.dumps({"plugin_api": 1, "ranges": [
        {"start": 0, "end": 10}, {"start": 50, "end": 200, "score": 60}, {"start": 119.5, "end": 130},
        {"start": 20, "end": 40, "score": 90}]}))
    result = host.read_result(folder, duration=120, max_clips=2)
    assert [(r["start"], r["end"]) for r in result["ranges"]] == [(20, 40), (50, 120)]


def test_read_result_refuses_what_it_cannot_use(tmp_path):
    folder = tmp_path / "run"
    folder.mkdir()
    with pytest.raises(ContractError, match=r"without writing result\.json"):
        host.read_result(folder)
    (folder / "result.json").write_text("{not json")
    with pytest.raises(ContractError, match="not valid JSON"):
        host.read_result(folder)


def test_a_result_that_cant_even_be_read_is_refused_not_raised(tmp_path):
    """A folder named result.json, notes nested too deep to parse and a
    number too large for a float each used to escape as a Python error."""
    folder = tmp_path / "run"
    (folder / "result.json").mkdir(parents=True)
    for read in (lambda: host.read_result(folder), lambda: host.read_answers(folder, steps=["rate"], ids=["m1"])):
        with pytest.raises(ContractError, match=r"result\.json is not a file"):
            read()
    (folder / "result.json").rmdir()
    (folder / "result.json").write_text('{"plugin_api": 1, "ranges": [], "notes": ' + "[" * 200_000
                                        + "]" * 200_000 + "}", encoding="utf-8")
    with pytest.raises(ContractError, match=r"result\.json could not be read \(maximum recursion depth"):
        host.read_answers(folder, steps=["rate"], ids=["m1"])
    huge = int("9" * 400)
    assert check_result({"plugin_api": 1, "ranges": [{"start": 0, "end": huge}]}) == \
        ["ranges[0]: start and end must be numbers of seconds"]
    answer = {"plugin_api": 1, "ranges": [], "moments": [{"id": "m1", "score": huge}]}
    assert check_result(answer, steps=["rate"]) == ["moments[0]: score must be a number from 0 to 100, or left out"]
    (folder / "result.json").write_text(json.dumps(answer), encoding="utf-8")
    with pytest.raises(ContractError, match=r"moments\[0\]: score must be a number"):
        host.read_answers(folder, steps=["rate"], ids=["m1"])
    job, _ = _moment_job(tmp_path / "job")
    with pytest.raises(ContractError, match="rate: score must be a number from 0 to 100"):
        job.rate(job.moments[0], huge)


def test_the_plugin_environment_leaves_out_settings_and_credentials(tmp_path):
    env = host.plugin_env({"PATH": "/bin", "CLIPS_STUDIO_X": "1", "CLIPSKITTY_SECRET_OLD": "s",
                           "GITHUB_TOKEN": "t", "MY_PASSWORD": "p", "CUDA_PATH": "/cuda"},
                          job_folder=tmp_path, secrets={"api_key": "k", "empty": ""})
    assert env["PATH"] == "/bin" and env["CUDA_PATH"] == "/cuda"
    assert "CLIPS_STUDIO_X" not in env and "GITHUB_TOKEN" not in env and "MY_PASSWORD" not in env
    assert "CLIPSKITTY_SECRET_OLD" not in env and env["CLIPSKITTY_SECRET_API_KEY"] == "k"
    assert "CLIPSKITTY_SECRET_EMPTY" not in env
    assert env["CLIPSKITTY_JOB"] == str(tmp_path) and env["PYTHONPATH"] == str(SDK)


def test_the_plugin_environment_runs_like_the_apps_own_python(tmp_path):
    """The marker lets the installed app's engine run the script itself
    (_clipskitty_script_host.py); start-up settings that Python ignores there
    are dropped here too, and UTF-8 mode is switched off as it is there, so a
    developer's run behaves like a creator's."""
    env = host.plugin_env({"PATH": "/bin", "CLIPSKITTY_SCRIPT_HOST": "0", "PYTHONUTF8": "1", "PYTHONHOME": "/x",
                           "PYTHONSTARTUP": "s.py", "PYTHONINSPECT": "1"},
                          job_folder=tmp_path, python_path=[tmp_path / "lib", "extra"])
    assert env["CLIPSKITTY_SCRIPT_HOST"] == "1"
    assert not {"PYTHONHOME", "PYTHONSTARTUP", "PYTHONINSPECT"} & set(env) and env["PYTHONUTF8"] == "0"
    assert env["PYTHONPATH"].split(os.pathsep) == [str(SDK), str(tmp_path / "lib"), "extra"]
    assert env["PYTHONIOENCODING"] == "utf-8" and env["PYTHONUNBUFFERED"] == "1"


def test_the_python_placeholder_is_replaced():
    assert host.resolve_command(["{python}", "src/main.py", "{python}x"], "/usr/bin/python3") == \
        ["/usr/bin/python3", "src/main.py", "{python}x"]
    assert host.find_python("C:/Python312/python.exe") == "C:/Python312/python.exe"


# ---- the job a manifest's permissions allow ----------------------------------------


def test_build_job_hands_over_only_what_the_permissions_cover(tmp_path):
    manifest = {"id": "example-dev/example", "version": "1.0.0", "permissions": [],
                "settings": {"level": {"type": "integer", "default": 3}, "key": {"type": "secret"}}}
    kwargs = {"video": {"path": "v.mp4", "id": "v"}, "transcript": {"language": "en", "segments": []},
                  "ffmpeg": "ffmpeg", "ffprobe": "ffprobe", "ollama": {"host": "http://localhost:11434", "model": ""},
                  "output_dir": tmp_path / "out"}
    job, transcript = host.build_job(manifest, **kwargs)
    assert "video" not in job and job["tools"] == {} and transcript is None
    assert job["settings"] == {"level": 3}  # defaults, and never a secret
    assert not check_job(job)
    manifest["permissions"] = ["video.read", "transcript.read", "ffmpeg", "ollama"]
    job, transcript = host.build_job(manifest, settings={"level": 5}, **kwargs)
    assert job["video"]["id"] == "v" and transcript == {"language": "en", "segments": []}
    assert set(job["tools"]) == {"ffmpeg", "ffprobe", "ollama"} and job["settings"] == {"level": 5}
    with pytest.raises(ValueError, match="no setting called 'colour'"):
        host.build_job(manifest, settings={"colour": "red"}, **kwargs)
    with pytest.raises(ValueError, match="'key' is a secret"):
        host.build_job(manifest, settings={"key": "abc"}, **kwargs)


# ---- python -m clipskitty_sdk run ----------------------------------------------------


ECHO = Path(__file__).resolve().parent / "fixtures" / "plugins" / "echo"


def test_the_dev_runner_runs_a_plugin_the_way_the_app_does(tmp_path, capsys):
    pytest.importorskip("yaml")
    from clipskitty_sdk.__main__ import main

    video = tmp_path / "clip.mp4"
    video.write_bytes(b"not decoded by the echo plugin")
    transcript = tmp_path / "t.json"
    transcript.write_text(json.dumps({"language": "en", "segments": [{"start": 0, "end": 3, "text": "go"}]}))
    code = main(["run", str(ECHO), "--video", str(video), "--transcript", str(transcript),
                 "--set", "ranges=5-20:60,30-55:95", "--secret", "api_key=abc", "--job-dir", str(tmp_path / "job"),
                 "--ffprobe", str(tmp_path / "no-ffprobe")])
    out = capsys.readouterr().out
    assert code == 0, out
    assert "2 moment(s)" in out and out.index("30.0s") < out.index("5.0s")  # best first
    seen = json.loads((tmp_path / "job" / "out" / "seen.json").read_text())
    assert seen["secret"] == "abc" and "abc" not in (tmp_path / "job" / "job.json").read_text()
    assert seen["transcript"][0]["text"] == "go"
    assert seen["job"]["limits"] == {"max_clips": None, "min_duration": 10.0, "max_duration": 60.0}


@pytest.mark.parametrize("args, code, words", [
    (["--set", "mode=fail"], 1, "the kill feed could not be read"),
    (["--set", "mode=bad"], 1, "would refuse this answer"),
    (["--set", "colour=red"], 2, "no setting called 'colour'"),
])
def test_the_dev_runner_says_why_a_run_would_fail_in_the_app(tmp_path, capsys, args, code, words):
    pytest.importorskip("yaml")
    from clipskitty_sdk.__main__ import main

    video = tmp_path / "clip.mp4"
    video.write_bytes(b"x")
    assert main(["run", str(ECHO), "--video", str(video), "--job-dir", str(tmp_path / "job"), *args]) == code
    assert words in capsys.readouterr().err


def test_the_dev_runner_needs_a_manifest(tmp_path, capsys):
    pytest.importorskip("yaml")
    from clipskitty_sdk.__main__ import main

    (tmp_path / "clip.mp4").write_bytes(b"x")
    assert main(["run", str(tmp_path), "--video", str(tmp_path / "clip.mp4")]) == 2
    assert "no clipskitty.yaml" in capsys.readouterr().err


# ---- python -m clipskitty_sdk run: understanding and rating ---------------------------


GRADER_MAIN = '''\
"""A test plugin: records its job, then answers every step it is asked for."""
import json

from clipskitty_sdk import run


def main(job):
    (job.output_dir / "seen.json").write_text(json.dumps(job.data), encoding="utf-8")
    if job.wants("find"):
        for start, end in ((5, 20), (30, 50)):
            own = job.add_range(start, end, label="quark_burst", title=f"Burst at {start}")
            job.rate(own, 70 + start, reason="graded")
            job.understand(own, "First quark burst\\nof the match https://example.com/clip")
    for m in job.moments:
        job.rate(m, min(100, m.score + 10), reason="seen by the grader")
        job.understand(m, f"Something happens in {m.id}")
    job.finish(notes="graded")


run(main)
'''

# The rater and the understander the developer docs show (docs/developers/steps.md),
# exactly as written there: test_the_steps_page_shows_the_code_these_tests_run checks.
STEPS_DOC = Path(__file__).resolve().parent.parent / "docs" / "developers" / "steps.md"

DOCS_RATER = '''\
# Rates moments of Quarkbloom Arena (a made-up game) by what the caster calls out.
from clipskitty_sdk import run

CALLS = {"quark burst": 15, "triple bloom": 25, "arena wipe": 40}


def main(job):
    for m in job.moments:                           # found by Clips Kitty, Sports or a pipeline
        said = job.text(m).lower()                  # what is said during the moment
        bonus = sum(points for call, points in CALLS.items() if call in said)
        if bonus:
            job.rate(m, min(100, m.score + bonus), reason="the caster called a big play")
        elif "respawn timer" in said:
            job.rate(m, 10, reason="the players are waiting to respawn")  # under the minimum: set aside


run(main)
'''

DOCS_NOTES = '''\
# Says what happens in moments of Quarkbloom Arena (a made-up game), for the titles.
from clipskitty_sdk import run

ROUNDS = ("round one", "round two", "final round", "sudden bloom")


def main(job):
    for m in job.moments:
        said = job.text(m).lower()
        for name in ROUNDS:
            if name in said:
                job.understand(m, f"This happens in the {name} of a Quarkbloom Arena match")
        if "comeback" in said or "back in it" in said:
            job.understand(m, "The team that was behind is catching up here")


run(main)
'''

# What the docs' rater answers about three moments (test_the_docs_rater_and_understander_run),
# as python -m clipskitty_sdk run prints it; the docs show the same lines.
DOCS_RATER_ANSWERS = ("m1   812.0s-841.5s  score 72 -> 97  the caster called a big play\n"
                      "m2   900.0s-925.0s  score 64 -> 10  the players are waiting to respawn\n"
                      "m3   1000.0s-1012.0s  score 60\n")


def _plugin(tmp_path, name: str, title: str, inputs: list, outputs: list, permissions: list,
            main: str = GRADER_MAIN) -> Path:
    """A plugin folder with a valid manifest and src/main.py."""
    folder = tmp_path / name
    (folder / "src").mkdir(parents=True)
    (folder / "clipskitty.yaml").write_text(
        "manifest_version: 1\n"
        f"id: example-dev/{name}\n"
        f"name: {title}\n"
        "version: 1.0.0\n"
        "kind: pipeline\n"
        "capability: highlight_detection\n"
        "description: A test plugin for Quarkbloom Arena (a made-up game).\n"
        "license: MIT\n"
        "requires: {clips_kitty: '>=2.0', plugin_api: 1}\n"
        "run: {command: ['{python}', src/main.py], timeout_minutes: 5}\n"
        "execution: local\n"
        f"inputs: [{', '.join(inputs)}]\n"
        f"outputs: [{', '.join(outputs)}]\n"
        f"permissions: [{', '.join(permissions)}]\n", encoding="utf-8")
    (folder / "src" / "main.py").write_text(main, encoding="utf-8")
    return folder


def _rater(tmp_path, main: str = GRADER_MAIN) -> Path:
    return _plugin(tmp_path, "quarkbloom-rater", "Quarkbloom Rater", ["moments", "transcript"], ["ratings"],
                   ["transcript.read"], main)


def _understander(tmp_path, main: str = GRADER_MAIN) -> Path:
    return _plugin(tmp_path, "quarkbloom-notes", "Quarkbloom Notes", ["moments", "transcript"], ["context"],
                   ["transcript.read"], main)


def _all_in_one(tmp_path) -> Path:
    return _plugin(tmp_path, "quarkbloom-grader", "Quarkbloom Grader", ["video", "transcript", "moments"],
                   ["ranges", "context", "ratings"], ["video.read", "transcript.read"])


def _dev_run(tmp_path, plugin: Path, *args, job: str = "job"):
    """`python -m clipskitty_sdk run`, without FFprobe: its exit code and the
    job.json the plugin saw (None when it never started)."""
    from clipskitty_sdk.__main__ import main

    code = main(["run", str(plugin), "--job-dir", str(tmp_path / job), "--ffprobe", str(tmp_path / "no-ffprobe"),
                 *args])
    seen = tmp_path / job / "out" / "seen.json"
    return code, (json.loads(seen.read_text(encoding="utf-8")) if seen.exists() else None)


def _a_video(tmp_path) -> Path:
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"not decoded by the test plugins")
    return video


def test_run_hands_sample_moments_to_any_moment_run(tmp_path, capsys):
    pytest.importorskip("yaml")
    code, seen = _dev_run(tmp_path, _rater(tmp_path), "--duration", "600")
    out, err = capsys.readouterr()
    assert code == 0, out + err
    assert "note: no --moments given, so the plugin gets 5 sample moments spread through the video" in err
    assert seen["steps"] == ["rate"] and "video" not in seen
    assert seen["limits"] == {"max_clips": None, "min_duration": 10.0, "max_duration": 60.0, "min_score": 55.0}
    assert [(m["id"], m["start"], m["end"], m["score"], m["found_score"], m["found_by"]) for m in seen["moments"]] \
        == [(f"m{k}", 100.0 * k, 100.0 * k + 20, 60, 60, "clipskitty") for k in range(1, 6)]
    assert "Rate: 5 of 5 moment(s) answered, as Clips Kitty would use them:" in out
    assert "m1   100.0s-120.0s  score 60 -> 70  seen by the grader\nm2   200.0s" in out
    assert "log: understand ignored: this job didn't ask for notes" in out and "notes: graded" in out

    # An understander gets them too; on a short video each lasts a sixth of it.
    notes = _understander(tmp_path)
    code, seen = _dev_run(tmp_path, notes, "--duration", "60", "--min-score", "70", job="short")
    out, err = capsys.readouterr()
    assert code == 0, out + err
    assert seen["steps"] == ["understand"] and seen["limits"]["min_score"] == 70
    assert [(m["start"], m["end"]) for m in seen["moments"]] == [(10.0 * k, 10.0 * k + 10) for k in range(1, 6)]
    assert "m1   10.0s-20.0s  score 60\n       note: Something happens in m1\nm2   20.0s" in out

    # A plugin that does both is asked for both, understanding first.
    both = _plugin(tmp_path, "quarkbloom-both", "Quarkbloom Both", ["moments", "transcript"], ["ratings", "context"],
                   ["transcript.read"])
    code, seen = _dev_run(tmp_path, both, "--duration", "60", job="both")
    out, err = capsys.readouterr()
    assert code == 0, out + err
    assert seen["steps"] == ["understand", "rate"]
    assert "m5   50.0s-60.0s  score 60 -> 70  seen by the grader\n       note: Something happens in m5\n" in out

    # Without a video, --duration or a transcript, there is no length to spread them over;
    # a transcript's last segment gives one.
    assert _dev_run(tmp_path, notes, job="nothing") == (2, None)
    assert "error: no video length for sample moments: pass --duration, or --moments" in capsys.readouterr().err
    transcript = tmp_path / "t.json"
    transcript.write_text(json.dumps({"language": "en", "segments": [
        {"start": 0, "end": 3, "text": "go"}, {"start": 100, "end": 120, "text": "and that is the match"}]}))
    code, seen = _dev_run(tmp_path, notes, "--transcript", str(transcript), job="transcript")
    assert code == 0 and [m["start"] for m in seen["moments"]] == [20.0, 40.0, 60.0, 80.0, 100.0]


def test_run_takes_a_finders_result_as_moments(tmp_path, capsys):
    pytest.importorskip("yaml")
    video = _a_video(tmp_path)
    code, found = _dev_run(tmp_path, ECHO, "--video", str(video), "--set", "ranges=5-20:60,30-55:95", job="found")
    assert code == 0 and "steps" not in found["job"] and "moments" not in found["job"]
    capsys.readouterr()
    rater = _rater(tmp_path)
    code, seen = _dev_run(tmp_path, rater, "--moments", str(tmp_path / "found" / "result.json"))
    out, err = capsys.readouterr()
    assert code == 0, out + err
    assert [(m["id"], m["start"], m["end"], m["score"], m["found_score"], m["found_by"], m["label"], m["title"],
             m["reason"]) for m in seen["moments"]] == [
        ("m1", 5.0, 20.0, 60.0, 60.0, "clipskitty", "moment", "Moment at 5", "asked for in the test"),
        ("m2", 30.0, 55.0, 95.0, 95.0, "clipskitty", "moment", "Moment at 30", "asked for in the test")]
    assert "sample moments" not in err
    assert "m2   30.0s-55.0s  score 95 -> 100  seen by the grader" in out

    # A plain list works too: a moment without a score gets 60.
    moments = tmp_path / "moments.json"
    moments.write_text(json.dumps([{"start": 812, "end": 841.5, "score": 72, "label": "quark_burst"},
                                   {"start": 900, "end": 920}]))
    code, seen = _dev_run(tmp_path, rater, "--moments", str(moments), job="list")
    assert code == 0
    assert [(m["id"], m["score"], m["label"]) for m in seen["moments"]] == [("m1", 72, "quark_burst"), ("m2", 60, "")]
    capsys.readouterr()
    for bad, words in (([{"start": 5}], "--moments: moment 0 needs a start and an end in seconds, the start first"),
                       ([{"start": 1, "end": 9, "score": 120}], "--moments: moment 0: score must be a number from 0"),
                       ({"moments": []}, "--moments: expected a list of {start, end, score?")):
        moments.write_text(json.dumps(bad))
        assert _dev_run(tmp_path, rater, "--moments", str(moments), job="bad") == (2, None)
        assert f"error: {words}" in capsys.readouterr().err


@pytest.mark.parametrize("plugin, steps, message", [
    ("notes", "rate", ("the pipeline Quarkbloom Notes can't rate moments others found: "
                       "its manifest needs moments in inputs and ratings in outputs")),
    ("rater", "understand,rate", ("the pipeline Quarkbloom Rater can't understand moments others found: "
                                  "its manifest needs moments in inputs and context in outputs")),
    ("rater", "find", ("the pipeline Quarkbloom Rater doesn't find moments: it rates or understands moments others "
                       "found. Choose it under Rate & understand instead")),
    ("echo", "rate", ("the pipeline Echo can't rate moments others found: "
                      "its manifest needs moments in inputs and ratings in outputs")),
    ("echo", "find,understand", ("the pipeline Echo doesn't say what happens in the moments it finds: "
                                 "its manifest needs context in outputs")),
    ("grader", "find,rate", "a run that finds moments isn't also asked to rate others' moments; run them separately"),
    ("grader", "edit", "edit is planned, not part of plugin contract 1 yet; use find, understand or rate"),
    ("grader", "polish", "unknown step 'polish'; expected find, understand or rate"),
    ("grader", "rate,", "unknown step ''; expected find, understand or rate"),
])
def test_run_refuses_a_step_the_plugin_doesnt_offer(tmp_path, capsys, plugin, steps, message):
    pytest.importorskip("yaml")
    folder = {"notes": _understander, "rater": _rater, "grader": _all_in_one, "echo": lambda _: ECHO}[plugin](tmp_path)
    code, seen = _dev_run(tmp_path, folder, "--video", str(_a_video(tmp_path)), "--duration", "100", "--steps", steps)
    assert (code, seen) == (2, None)
    assert f"error: --steps: {message}\n" in capsys.readouterr().err


def test_run_does_a_context_finders_find_run(tmp_path, capsys):
    pytest.importorskip("yaml")
    finder = _plugin(tmp_path, "quarkbloom-finder", "Quarkbloom Finder", ["video", "transcript"], ["ranges", "context"],
                     ["video.read", "transcript.read"])
    video = _a_video(tmp_path)
    for job, args in (("default", []), ("find", ["--steps", "find"]), ("both", ["--steps", "find,understand"])):
        code, seen = _dev_run(tmp_path, finder, "--video", str(video), "--duration", "120", *args, job=job)
        out, err = capsys.readouterr()
        assert code == 0, out + err
        assert seen["steps"] == ["find", "understand"] and "moments" not in seen
        assert "min_score" not in seen["limits"] and seen["video"]["duration"] == 120.0
        assert "2 moment(s), as Clips Kitty would take them:" in out
        assert ("      30.0s      50.0s  score 100  Burst at 30\n"
                "       why: graded\n"
                "       note: First quark burst of the match\n"
                "       5.0s      20.0s  score  75  Burst at 5\n"
                "       why: graded\n"
                "       note: First quark burst of the match\n") in out
    # The SDK keeps the note as the plugin wrote it, on one line; the app takes the link out.
    written = json.loads((tmp_path / "default" / "result.json").read_text(encoding="utf-8"))
    assert written["ranges"][0]["context"] == ["First quark burst of the match https://example.com/clip"]


def test_run_all_in_one_default(tmp_path, capsys):
    pytest.importorskip("yaml")
    grader, video = _all_in_one(tmp_path), _a_video(tmp_path)
    code, seen = _dev_run(tmp_path, grader, "--video", str(video), "--duration", "300")
    out, err = capsys.readouterr()
    assert code == 0, out + err
    assert seen["steps"] == ["find", "understand"] and "moments" not in seen  # the run a Pipeline gets
    assert "2 moment(s), as Clips Kitty would take them:" in out and "note: First quark burst of the match" in out
    assert "sample moments" not in err

    code, seen = _dev_run(tmp_path, grader, "--video", str(video), "--duration", "300", "--steps", "understand,rate",
                          job="moments")
    out, err = capsys.readouterr()
    assert code == 0, out + err
    assert seen["steps"] == ["understand", "rate"] and len(seen["moments"]) == 5
    assert seen["video"]["path"] == str(video.resolve()) and seen["limits"]["min_score"] == 55
    assert "Understand and rate: 5 of 5 moment(s) answered, as Clips Kitty would use them:" in out
    assert "m1   50.0s-70.0s  score 60 -> 70  seen by the grader\n       note: Something happens in m1\n" in out
    assert "ignored:" not in out


def test_run_needs_no_video_for_a_moment_only_plugin(tmp_path, capsys):
    pytest.importorskip("yaml")
    code, seen = _dev_run(tmp_path, _rater(tmp_path), "--duration", "120")
    assert code == 0 and "video" not in seen
    capsys.readouterr()
    # One that reads the video still needs it, whether it finds moments or rates them.
    looker = _plugin(tmp_path, "frame-rater", "Frame Rater", ["moments", "video"], ["ratings"], ["video.read"])
    for plugin in (looker, ECHO, _all_in_one(tmp_path)):
        assert _dev_run(tmp_path, plugin, "--duration", "120", job=f"no-video-{plugin.name}") == (2, None)
        assert ("error: --video is needed: a plugin with the video.read permission needs a video to run on, "
                "or try it with --sample") in capsys.readouterr().err
    code, seen = _dev_run(tmp_path, looker, "--video", str(_a_video(tmp_path)), "--duration", "120", job="video")
    assert code == 0 and seen["video"]["duration"] == 120.0 and seen["steps"] == ["rate"]


def test_the_docs_rater_and_understander_run(tmp_path, capsys):
    pytest.importorskip("yaml")
    transcript = tmp_path / "t.json"
    transcript.write_text(json.dumps({"language": "en", "segments": [
        {"start": 815, "end": 830, "text": "A TRIPLE BLOOM in the final round!"},
        {"start": 905, "end": 915, "text": "Nothing but the respawn timer now."},
        {"start": 1003, "end": 1010, "text": "What a comeback, they're back in it."}]}))
    moments = tmp_path / "moments.json"
    moments.write_text(json.dumps([{"start": 812, "end": 841.5, "score": 72}, {"start": 900, "end": 925, "score": 64},
                                   {"start": 1000, "end": 1012}]))
    given = ["--transcript", str(transcript), "--moments", str(moments)]
    code, _ = _dev_run(tmp_path, _rater(tmp_path, DOCS_RATER), *given, job="rater")
    out, err = capsys.readouterr()
    assert code == 0, out + err
    assert DOCS_RATER_ANSWERS in out
    code, _ = _dev_run(tmp_path, _understander(tmp_path, DOCS_NOTES), *given, job="notes")
    out, err = capsys.readouterr()
    assert code == 0, out + err
    assert ("m1   812.0s-841.5s  score 72\n"
            "       note: This happens in the final round of a Quarkbloom Arena match\n"
            "m2   900.0s-925.0s  score 64\n"
            "m3   1000.0s-1012.0s  score 60\n"
            "       note: The team that was behind is catching up here\n") in out


def test_the_steps_page_shows_the_code_these_tests_run():
    """docs/developers/steps.md shows the rater and the understander the test
    above runs, word for word, with the answers it prints, and its manifests
    validate with no warnings, so the page can't drift from what works."""
    yaml = pytest.importorskip("yaml")
    import re

    from clipskitty_sdk import manifest

    doc = STEPS_DOC.read_text(encoding="utf-8")
    blocks = {kind: re.findall(rf"```{kind}\n(.*?)```", doc, re.S) for kind in ("python", "yaml", "text")}
    assert DOCS_RATER in blocks["python"] and DOCS_NOTES in blocks["python"]
    assert DOCS_RATER_ANSWERS in blocks["text"]
    rater = next(yaml.safe_load(b) for b in blocks["yaml"] if "quarkbloom-rater" in b)
    notes = {**rater, "id": "example-dev/quarkbloom-notes", "name": "Quarkbloom Notes", "outputs": ["context"]}
    for data, offered in ((rater, ("rate",)), (notes, ("understand",))):
        report = manifest.validate(data)
        assert (report.errors, report.warnings) == ([], [])
        assert manifest.offers(data) == offered


# ---- the local API client -----------------------------------------------------------


@pytest.fixture
def fake_api():
    """A stand-in for the app's API on a free local port, recording each request."""
    import http.server
    import threading

    seen = []
    replies = {"/health": (200, {"ok": True, "app_version": "2.0.0", "api_version": 1})}

    class Handler(http.server.BaseHTTPRequestHandler):
        def _answer(self):
            length = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(length)) if length else None
            seen.append((self.command, self.path, body))
            status, reply = replies.get(self.path.split("?")[0], (404, {"detail": "Not Found"}))
            data = json.dumps(reply).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        do_GET = do_POST = do_PATCH = do_DELETE = _answer

        def log_message(self, *_args):
            pass

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_address[1]}", replies, seen
    server.shutdown()
    server.server_close()


def test_the_client_calls_the_stable_routes(fake_api):
    from clipskitty_sdk.client import LocalAPI

    url, replies, seen = fake_api
    api = LocalAPI(url)
    assert api.health()["api_version"] == 1
    replies["/jobs"] = (200, {"job_id": 7})
    assert api.add_job("https://www.youtube.com/watch?v=aB3dEfGhIjK", max_clips=3) == {"job_id": 7}
    assert seen[-1] == ("POST", "/jobs", {"url": "https://www.youtube.com/watch?v=aB3dEfGhIjK", "max_clips": 3})
    replies["/videos/a%2Fb/clips"] = (200, [])
    assert api.clips("a/b") == [] and seen[-1][1] == "/videos/a%2Fb/clips"


def test_the_client_turns_refusals_into_errors_with_the_apis_words(fake_api):
    from clipskitty_sdk.client import APIError, LocalAPI

    url, replies, _ = fake_api
    replies["/jobs"] = (400, {"detail": "max_clips must be between 1 and 50"})
    with pytest.raises(APIError) as e:
        LocalAPI(url).add_job("https://www.youtube.com/watch?v=aB3dEfGhIjK", max_clips=99)
    assert e.value.status == 400 and e.value.detail == "max_clips must be between 1 and 50"
    replies["/health"] = (200, {"ok": True, "api_version": 2})
    with pytest.raises(APIError, match="speaks API 2"):
        LocalAPI(url).health()


def test_the_client_says_when_the_app_is_not_running():
    import socket

    from clipskitty_sdk.client import APIError, LocalAPI

    with socket.socket() as s:  # a port nothing listens on
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    with pytest.raises(APIError) as e:
        LocalAPI(f"http://127.0.0.1:{port}", timeout=2).health()
    assert e.value.status == 0 and "not answering" in str(e.value)


def test_a_program_command_is_resolved_inside_the_plugin(tmp_path):
    assert host.resolve_command(["bin/detect", "--fast"], "py", tmp_path) == [str(tmp_path / "bin/detect"), "--fast"]
    assert host.resolve_command(["{python}", "src/main.py"], "py", tmp_path) == ["py", "src/main.py"]
