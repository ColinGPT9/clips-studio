"""clipskitty_sdk.testing: running a plugin from a test the way Clips Kitty
would, and making the job folder it would make.

Like every tests/test_plugin_sdk_*.py file, this needs only pytest and
PyYAML and imports nothing from Clips Kitty's engine, so it also runs in CI's
SDK (Windows) job. Only the test that makes the sample video needs FFmpeg;
the transcript tests clear PATH of it, so they run everywhere.
"""

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SDK = ROOT / "sdk" / "python"
if str(SDK) not in sys.path:
    sys.path.insert(0, str(SDK))

from clipskitty_sdk import ContractError, Moment, devrun, read_job, samples, testing  # noqa: E402
from clipskitty_sdk.job import MISTAKE  # noqa: E402
from clipskitty_sdk.manifest import validate_folder  # noqa: E402

FFMPEG, FFPROBE = shutil.which("ffmpeg"), shutil.which("ffprobe")

MANIFEST = """\
manifest_version: 1
id: example-dev/{name}
name: {title}
version: 1.0.0
kind: pipeline
capability: highlight_detection
description: A test plugin for Quarkbloom Arena (a made-up game).
license: MIT
requires: {{clips_kitty: '>=2.0', plugin_api: 1}}
run:
  command: ['{{python}}', src/main.py]
execution: local
inputs: [{inputs}]
outputs: [{outputs}]
permissions: [{permissions}]
"""

# Does nothing: for looking at the job it was given.
IDLE_MAIN = '''\
from clipskitty_sdk import run


def main(job):
    pass


run(main)
'''

# Finds where "quark burst" and "round one" are said, and says what happens in the first.
NOTED_FINDER_MAIN = '''\
from clipskitty_sdk import run


def main(job):
    job.progress(0.5, "Reading what is said")
    job.log("looking for: quark burst, round one")
    for s in job.transcript.segments():
        if "quark burst" in s["text"]:
            m = job.add_range(s["start"] - 4, s["end"] + 20, score=80, label="words_said",
                              reason='the commentary says "quark burst"')
            job.understand(m, "The caster calls a quark burst")
        if "round one" in s["text"]:
            job.add_range(s["start"] - 2, s["end"] + 4, score=40.5, label="words_said", title="Round one")
    job.finish(notes="checked 3 segments")


run(main)
'''

# Finds the moment where "quark burst" is said, and runs on past it.
WORDS_FINDER_MAIN = '''\
from clipskitty_sdk import run


def main(job):
    for s in job.transcript.segments():
        if "quark burst" in s["text"]:
            job.add_range(s["start"] - 4, s["end"] + 20, score=80, label="words_said",
                          reason='the commentary says "quark burst"')


run(main)
'''

# Rates and describes the moments it is handed, and adds a range nobody asked for.
RATER_MAIN = '''\
from clipskitty_sdk import run


def main(job):
    for m in job.moments:
        said = job.text(m)
        if job.wants("rate") and "quark burst" in said:
            job.rate(m, min(100, m.score + 15), reason='the commentary says "quark burst"')
        elif job.wants("rate") and "waiting" in said:
            job.rate(m, 10, reason='the commentary says "waiting"')
        if job.wants("understand") and m.context:
            job.understand(m, "Earlier: " + m.context[0])
    job.log("found by " + ", ".join(sorted({m.found_by for m in job.moments})))
    job.add_range(0, 12, label="extra")  # not asked for in this run: Clips Kitty ignores it


run(main)
'''

# Fails in the way its `how` setting says.
FAILING_MAIN = '''\
import time

from clipskitty_sdk import run


def main(job):
    how = job.settings["how"]
    if how == "fail":
        job.fail("the banner region is off the screen")
    if how == "slip":
        frame = {}
        print(frame["frame"])
    if how == "quiet":
        raise SystemExit(0)
    if how == "slow":
        time.sleep(60)
    if how == "crash":
        raise SystemExit(3)


run(main)
'''

FAILING_SETTINGS = """\
settings:
  how: {type: choice, options: [fail, slip, quiet, slow, crash], default: fail, title: How it fails}
"""

BANNER_SETTINGS = """\
settings:
  louder_by_db: {type: number, default: 6, title: How much louder}
  banner_colour: {type: string, default: e0303a, title: The banner's colour}
"""

# Looks at the video it is given: a moment on the banner, scored by the video's length.
VIDEO_FINDER_MAIN = '''\
from clipskitty_sdk import run


def main(job):
    size = job.video.path.stat().st_size
    job.add_range(22, 27, score=90, label="quark_burst",
                  reason=f"the banner shows; {job.video.duration:.0f} s, {size // 1000} kB")


run(main)
'''


def _plugin(tmp_path, name, *, inputs, outputs, permissions, main, extra="") -> Path:
    folder = tmp_path / name
    (folder / "src").mkdir(parents=True)
    (folder / "clipskitty.yaml").write_text(MANIFEST.format(
        name=name, title=name.replace("-", " ").title(), inputs=inputs, outputs=outputs,
        permissions=permissions) + extra, encoding="utf-8")
    (folder / "src" / "main.py").write_text(main, encoding="utf-8")
    return folder


def _noted_finder(tmp_path) -> Path:
    return _plugin(tmp_path, "quarkbloom-words", inputs="transcript", outputs="ranges, context",
                   permissions="transcript.read", main=NOTED_FINDER_MAIN)


def _words_finder(tmp_path) -> Path:
    return _plugin(tmp_path, "quarkbloom-said", inputs="transcript", outputs="ranges",
                   permissions="transcript.read", main=WORDS_FINDER_MAIN)


def _rater(tmp_path) -> Path:
    return _plugin(tmp_path, "quarkbloom-rater", inputs="moments, transcript", outputs="ratings, context",
                   permissions="transcript.read", main=RATER_MAIN)


def _no_ffmpeg(tmp_path, monkeypatch) -> None:
    """PATH cleared of FFmpeg (and of everything else)."""
    empty = tmp_path / "empty-path"
    empty.mkdir(exist_ok=True)
    monkeypatch.setenv("PATH", str(empty))
    assert shutil.which("ffmpeg") is None and shutil.which("ffprobe") is None


def _job_json(folder: Path) -> dict:
    """job.json, with the paths inside its own folder made relative to it."""
    job = json.loads((folder / "job.json").read_text(encoding="utf-8"))
    job["output_dir"] = Path(job["output_dir"]).relative_to(folder).as_posix()
    if "transcript" in job:
        job["transcript"]["path"] = Path(job["transcript"]["path"]).relative_to(folder).as_posix()
    return job


def _cli(*args) -> int:
    from clipskitty_sdk.__main__ import main

    return main([str(a) for a in args])


def _job_folders(where: Path) -> list:
    return sorted(p.name for p in where.glob("clipskitty-job-*"))


# ---- make_job ------------------------------------------------------------------------


def test_make_job_builds_what_the_app_would(tmp_path, capsys):
    pytest.importorskip("yaml")
    said = tmp_path / "said.json"
    said.write_text(json.dumps(samples.sample_transcript()), encoding="utf-8")
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"not decoded")

    # A finder that reads the video: the same job.json as `python -m clipskitty_sdk run` writes.
    banner = _plugin(tmp_path, "quarkbloom-banner", inputs="video, transcript", outputs="ranges",
                     permissions="video.read, transcript.read, ffmpeg", main=IDLE_MAIN, extra=BANNER_SETTINGS)
    made = testing.make_job(tmp_path / "made", banner, video=video, transcript=samples.sample_transcript(),
                            duration=40, settings={"louder_by_db": 8}, games=["Quarkbloom Arena"])
    assert made == (tmp_path / "made").resolve()
    assert _cli("run", banner, "--video", video, "--transcript", said, "--duration", "40", "--set", "louder_by_db=8",
                "--game", "Quarkbloom Arena", "--job-dir", tmp_path / "cli") == 0
    capsys.readouterr()
    job = _job_json(made)
    assert job == _job_json(tmp_path / "cli")
    assert "steps" not in job and "moments" not in job  # a plain finder's job, as it always was
    assert (made / "transcript.json").read_text(encoding="utf-8") == \
        (tmp_path / "cli" / "transcript.json").read_text(encoding="utf-8")
    read = read_job(made)
    assert read.video.path == video.resolve() and read.video.duration == 40
    assert read.video.games == [{"name": "Quarkbloom Arena", "start": 0.0, "end": 40.0}]
    assert read.settings == {"louder_by_db": 8, "banner_colour": "e0303a"}
    assert (read.tools.ffmpeg, read.tools.ffprobe) == (FFMPEG, FFPROBE)
    assert [s["text"] for s in read.transcript.segments()][1] == "what a quark burst"
    assert (read.limits.min_duration, read.limits.max_duration, read.limits.max_clips) == (10.0, 60.0, None)

    # A rater without transcript.read: the moments as handed over, without what was said in them.
    moments = tmp_path / "moments.json"
    moments.write_text(json.dumps([{"start": 10, "end": 20, "score": 70, "title": "A title", "reason": "why"}]),
                       encoding="utf-8")
    blind = _plugin(tmp_path, "quarkbloom-blind", inputs="moments", outputs="ratings", permissions="",
                    main=IDLE_MAIN)
    made = testing.make_job(tmp_path / "rate", blind, video=video, transcript=samples.sample_transcript(),
                            duration=40, moments=[{"start": 10, "end": 20, "score": 70, "title": "A title",
                                                   "reason": "why"}])
    assert _cli("run", blind, "--moments", moments, "--duration", "40", "--job-dir", tmp_path / "cli-rate") == 0
    capsys.readouterr()
    job = _job_json(made)
    assert job == _job_json(tmp_path / "cli-rate")
    assert job["steps"] == ["rate"] and job["limits"]["min_score"] == 55.0
    assert job["moments"] == [{"id": "m1", "start": 10.0, "end": 20.0, "score": 70, "found_score": 70,
                               "found_by": "clipskitty", "label": "", "signals": {}}]
    assert "video" not in job and "transcript" not in job and not (made / "transcript.json").exists()

    # Without moments, the 5 sample moments of the video, as `run` hands over.
    made = testing.make_job(tmp_path / "sampled", blind, duration=40)
    assert [(m["id"], m["start"], m["end"]) for m in read_job(made).data["moments"]] == [
        (m["id"], m["start"], m["end"]) for m in devrun.sample_moments(40.0)]

    # A run of a plugin that finds and rates, asked only to rate, as text or a list.
    both = _plugin(tmp_path, "quarkbloom-both", inputs="moments, transcript", outputs="ranges, ratings",
                   permissions="transcript.read", main=IDLE_MAIN)
    assert _job_json(testing.make_job(tmp_path / "find", both, duration=40))["steps"] == ["find"]
    for steps in ("rate", ["rate"]):
        assert _job_json(testing.make_job(tmp_path / "rate-only", both, duration=40, steps=steps))["steps"] == \
            ["rate"]


# ---- run_plugin ----------------------------------------------------------------------


def test_run_plugin_returns_moments_answers_and_notes(tmp_path):
    pytest.importorskip("yaml")
    finder = _noted_finder(tmp_path)
    found = testing.run_plugin(finder, transcript=samples.sample_transcript(), duration=testing.SAMPLE_VIDEO_SECONDS,
                               tmp_path=tmp_path / "runs")
    assert found.ok and found.error == ""
    assert found.steps == ("find", "understand")
    # Best first, fitted to the 40-second video, numbered as Clips Kitty hands them on.
    assert [(m.id, m.start, m.end, m.score, m.found_score, m.found_by, m.label, m.title, m.reason, m.notes)
            for m in found.moments] == [
        ("m1", 17.0, 40.0, 80.0, 80.0, "example-dev/quarkbloom-words", "words_said", "",
         'the commentary says "quark burst"', ("The caster calls a quark burst",)),
        ("m2", 6.0, 16.0, 40.5, 40.5, "example-dev/quarkbloom-words", "words_said", "Round one", "", ())]
    assert all(isinstance(m, Moment) for m in found.moments)
    assert found.answers == {}
    assert found.notes == "checked 3 segments"
    assert {"type": "progress", "fraction": 0.5, "message": "Reading what is said"} in found.events
    assert found.log == ["looking for: quark burst, round one"]
    assert found.folder.parent == (tmp_path / "runs").resolve() and (found.folder / "result.json").is_file()

    # Its moments handed on to a plugin that understands and rates them.
    rater = _rater(tmp_path)
    rated = testing.run_plugin(rater, transcript=samples.sample_transcript(), duration=40, moments=found.moments,
                               tmp_path=tmp_path / "runs")
    assert rated.ok, rated.error
    assert rated.steps == ("understand", "rate")
    assert rated.answers == {"m1": {"score": 95.0, "reason": 'the commentary says "quark burst"',
                                    "context": ["Earlier: The caster calls a quark burst"]}}
    assert [(m.id, m.start, m.end, m.score, m.found_score, m.found_by, m.context, m.notes)
            for m in rated.moments] == [
        ("m1", 17.0, 40.0, 95.0, 80.0, "example-dev/quarkbloom-words", ("The caster calls a quark burst",),
         ("Earlier: The caster calls a quark burst",)),
        ("m2", 6.0, 16.0, 40.5, 40.5, "example-dev/quarkbloom-words", (), ())]
    assert rated.log == ["found by example-dev/quarkbloom-words",
                         "ignored: 1 range(s): this run was asked about moments, not to find new ones"]
    assert rated.notes == ""
    assert len({found.folder, rated.folder}) == 2  # a new job folder for each run

    # Asked only to rate: no notes are kept.
    rated = testing.run_plugin(rater, transcript=samples.sample_transcript(), duration=40, moments=found.moments,
                               steps="rate", tmp_path=tmp_path / "runs")
    assert rated.ok and rated.steps == ("rate",)
    assert rated.answers == {"m1": {"score": 95.0, "reason": 'the commentary says "quark burst"'}}
    assert [m.notes for m in rated.moments] == [(), ()]


def test_run_plugin_reports_the_plugins_error_in_words(tmp_path):
    pytest.importorskip("yaml")
    plugin = _plugin(tmp_path, "quarkbloom-fails", inputs="transcript", outputs="ranges",
                     permissions="transcript.read", main=FAILING_MAIN, extra=FAILING_SETTINGS)

    def run(how, **kwargs):
        return testing.run_plugin(plugin, duration=40, settings={"how": how}, tmp_path=tmp_path / "runs", **kwargs)

    failed = run("fail")
    assert (failed.ok, failed.error) == (False, "the banner region is off the screen")
    assert failed.moments == [] and failed.answers == {}
    assert {"type": "error", "message": "the banner region is off the screen"} in failed.events

    # A slip in its own code: the plain line creators see, and the details in the log.
    slipped = run("slip")
    assert (slipped.ok, slipped.error) == (False, MISTAKE)
    line = FAILING_MAIN.splitlines().index('        print(frame["frame"])') + 1
    assert f"KeyError: 'frame' (src/main.py line {line})" in slipped.log
    assert any(entry.startswith("Traceback") for entry in slipped.log)

    quiet = run("quiet")
    assert (quiet.ok, quiet.error) == (
        False, "it gave an answer Clips Kitty can't use: result: the plugin exited without writing result.json")

    crashed = run("crash")
    assert (crashed.ok, crashed.error) == (False, "the plugin stopped with exit code 3")

    slow = run("slow", timeout=1)
    assert (slow.ok, slow.error) == (False, "it ran past the 1 second timeout")


def test_run_plugin_refuses_a_manifest_the_app_would(tmp_path):
    pytest.importorskip("yaml")
    plugin = _plugin(tmp_path, "quarkbloom-edits", inputs="transcript", outputs="ranges, edits",
                     permissions="transcript.read", main=IDLE_MAIN)
    _, report = validate_folder(plugin)
    assert report.errors == ["outputs[1]: output 'edits' is planned, not supported by plugin API 1"]
    runs = tmp_path / "runs"
    with pytest.raises(ContractError) as e:
        testing.run_plugin(plugin, duration=40, tmp_path=runs)
    assert e.value.errors == report.errors
    assert str(e.value) == "clipskitty.yaml: outputs[1]: output 'edits' is planned, not supported by plugin API 1"
    with pytest.raises(ContractError):
        testing.make_job(tmp_path / "job", plugin, duration=40)

    # Everything else `python -m clipskitty_sdk run` refuses, before a job folder exists.
    finder = _words_finder(tmp_path)
    video_finder = _plugin(tmp_path, "quarkbloom-banner", inputs="video", outputs="ranges",
                           permissions="video.read, ffmpeg", main=IDLE_MAIN)
    nowhere, missing = (tmp_path / "nowhere").resolve(), (tmp_path / "missing.mp4").resolve()
    for where, kwargs, message in (
            (nowhere, {}, f"clipskitty.yaml: no clipskitty.yaml in {nowhere}: is this the plugin's folder?"),
            (finder, {"steps": "edit"},
             "steps: edit is planned, not part of plugin contract 1 yet; use find, understand or rate"),
            (finder, {"steps": ["rate"]},
             ("steps: the pipeline Quarkbloom Said can't rate moments others found: its manifest needs moments in "
              "inputs and ratings in outputs")),
            (finder, {"settings": {"min_kills": 4}}, "settings: this pipeline has no setting called 'min_kills'"),
            (video_finder, {}, "video: " + testing.VIDEO_NEEDED),
            (finder, {"video": missing}, f"video: no such video: {missing}"),
            (finder, {"duration": 0}, "duration: must be a number of seconds above 0"),
            (finder, {"timeout": -1}, "timeout: must be a number of seconds above 0"),
            (_rater(tmp_path), {"duration": None},
             "moments: no video length for sample moments: pass duration, or moments"),
            (_rater(tmp_path / "again"), {"moments": [{"start": 5, "end": 2}]},
             "moments: moment 0 needs a start and an end in seconds, the start first")):
        kwargs = {"duration": 40, **kwargs}
        with pytest.raises(ContractError) as e:
            testing.run_plugin(where, tmp_path=runs, **kwargs)
        assert str(e.value) == message, kwargs
    assert not runs.exists() and not (tmp_path / "job").exists()


# ---- the sample -----------------------------------------------------------------------


def test_sample_video_skips_without_ffmpeg_under_pytest(tmp_path, monkeypatch):
    _no_ffmpeg(tmp_path, monkeypatch)
    with pytest.raises(pytest.skip.Exception) as skipped:
        testing.sample_video(tmp_path / "media")
    assert str(skipped.value) == "needs FFmpeg"
    # Outside a test it says how to get FFmpeg.
    monkeypatch.delenv("PYTEST_CURRENT_TEST")
    with pytest.raises(testing.SampleUnavailable) as e:
        testing.sample_video(tmp_path / "media")
    assert str(e.value) == (
        "the sample video needs FFmpeg, and there is none on PATH. Install FFmpeg, or add the folder holding it to "
        "PATH. An installed Clips Kitty has it, in resources\\backend\\_internal\\ffmpeg inside the folder it was "
        "installed to.")
    assert isinstance(e.value, samples.SampleError)
    # As in a script that doesn't use pytest: no pytest is imported.
    script = ("import sys\n"
              "from clipskitty_sdk import testing\n"
              "try:\n"
              "    testing.sample_video(sys.argv[1])\n"
              "except testing.SampleUnavailable as e:\n"
              "    print('unavailable:', 'pytest' in sys.modules)\n")
    done = subprocess.run([sys.executable, "-c", script, str(tmp_path / "media")], capture_output=True, text=True,
                          env={**os.environ, "PYTHONPATH": str(SDK), "PYTEST_CURRENT_TEST": "a test"}, timeout=60)
    assert done.stdout == "unavailable: False\n", done.stderr
    assert not (tmp_path / "media").exists()
    # Never inside a plugin's folder, which Clips Kitty copies on install.
    plugin = _words_finder(tmp_path)
    with pytest.raises(ValueError, match="inside a plugin's folder"):
        testing.sample_video(plugin / "tests")


@pytest.mark.skipif(not (FFMPEG and FFPROBE), reason="needs FFmpeg and FFprobe")
def test_a_video_plugin_runs_on_the_sample_video(tmp_path):
    pytest.importorskip("yaml")
    video = testing.sample_video(tmp_path / "media")
    assert video == tmp_path / "media" / "sample.mp4"
    assert samples.transcript_path(video).is_file()
    plugin = _plugin(tmp_path, "quarkbloom-banner", inputs="video", outputs="ranges",
                     permissions="video.read, ffmpeg", main=VIDEO_FINDER_MAIN)
    run = testing.run_plugin(plugin, video=video, tmp_path=tmp_path / "runs")
    assert run.ok, run.error
    assert [(m.start, m.end, m.score, m.label) for m in run.moments] == [(22.0, 27.0, 90.0, "quark_burst")]
    assert run.moments[0].reason.startswith("the banner shows; 40 s, ")  # its length, read with FFprobe


def test_a_transcript_plugin_runs_on_the_sample_transcript_without_ffmpeg(tmp_path, monkeypatch):
    pytest.importorskip("yaml")
    _no_ffmpeg(tmp_path, monkeypatch)
    found = testing.run_plugin(_words_finder(tmp_path), transcript=testing.sample_transcript(),
                               duration=testing.SAMPLE_VIDEO_SECONDS, tmp_path=tmp_path / "runs")
    assert found.ok, found.error
    assert [(m.start, m.end, m.score, m.label, m.reason) for m in found.moments] == [
        (17.0, 40.0, 80.0, "words_said", 'the commentary says "quark burst"')]
    assert testing.sample_transcript() == samples.sample_transcript()
    assert testing.SAMPLE_VIDEO_SECONDS == samples.SAMPLE_VIDEO_SECONDS == 40.0

    # A rater, on the 5 sample moments of a 40-second video.
    rated = testing.run_plugin(_rater(tmp_path), transcript=testing.sample_transcript(), duration=40, steps="rate",
                               tmp_path=tmp_path / "runs")
    assert rated.ok, rated.error
    assert {mid: (a["score"], a["reason"]) for mid, a in rated.answers.items()} == {
        "m3": (75.0, 'the commentary says "quark burst"'), "m5": (10.0, 'the commentary says "waiting"')}
    assert [m.score for m in rated.moments] == [60, 60, 75.0, 60, 10.0]
