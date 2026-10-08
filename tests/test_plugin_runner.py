"""Running a plugin pipeline for one video (plugins/runner.py).

The runner hands a plugin process a job folder and turns its answer into the
ClipCandidates the rest of process_video renders. These tests run real child
processes: tests/fixtures/plugins/echo, which does whatever its `mode` setting
says, tests/fixtures/plugins/notes-finder, which also says what happens in the
moments it finds, tests/fixtures/plugins/trimmer, which suggests edits for the
clips it is handed, and the first-party adapter in
examples/pipelines/transcript-highlights.
"""

import json
import os
import re
import shutil
import threading
from pathlib import Path

import pytest

from core.models import DownloadedVideo, Segment

ROOT = Path(__file__).resolve().parent.parent
ECHO = ROOT / "tests" / "fixtures" / "plugins" / "echo"
NOTES_FINDER = ROOT / "tests" / "fixtures" / "plugins" / "notes-finder"
TRIMMER = ROOT / "tests" / "fixtures" / "plugins" / "trimmer"
ADAPTER = ROOT / "examples" / "pipelines" / "transcript-highlights"

pytest.importorskip("yaml")


@pytest.fixture
def video(tmp_path):
    path = tmp_path / "match.mp4"
    path.write_bytes(b"not really a video")
    return DownloadedVideo(video_id="vid123", title="A match", path=path, duration=300.0,
                           games=[{"name": "Example Game"}])


SEGMENTS = [Segment(0.0, 4.0, "kick off", [{"start": 0.0, "end": 0.5, "word": "kick"}]),
            Segment(4.0, 9.5, "what a goal", None)]


def _config(**clips):
    return {"clips": {"min_duration": 10, "max_duration": 60, "max_clips_per_video": 0, **clips},
            "llm": {"backend": "ollama/gemma:7b", "ollama_host": "http://localhost:11434"}}


def _run(data_dir, video, settings=None, config=None, plugin="fixture-dev/echo"):
    from plugins import runner

    choice = {"id": plugin, **({"settings": settings} if settings is not None else {})}
    return runner.find_clips(choice, video=video, segments=SEGMENTS, language="en",
                             config=config or _config(), data_dir=data_dir)


def _seen(data_dir) -> dict:
    runs = sorted((data_dir / "plugins" / "runs").iterdir(), key=lambda p: p.stat().st_mtime)
    return json.loads((runs[-1] / "out" / "seen.json").read_text(encoding="utf-8"))


@pytest.fixture
def echo(tmp_path, install_plugin):
    data_dir = tmp_path / "data"
    install_plugin(data_dir, ECHO)
    return data_dir


# ---- the answer -----------------------------------------------------------------


def test_ranges_become_candidates_best_first(echo, video):
    found = _run(echo, video, {"ranges": "10-40:70,100-130:95,200-230:80"})
    assert [(c.start, c.end, c.score) for c in found] == [(100, 130, 95), (200, 230, 80), (10, 40, 70)]
    best = found[0]
    assert best.hook == "Moment at 100" and best.reason == "asked for in the test"
    assert best.source == "plugin:fixture-dev/echo@1.0.0"
    assert best.subscores == {"plugin": "fixture-dev/echo", "plugin_version": "1.0.0",
                              "plugin_label": "moment", "plugin_why": "asked for in the test"}


def test_unscored_ranges_keep_their_order_and_get_scores_from_it(echo, video):
    found = _run(echo, video, {"ranges": "50-80,10-40,90-120"})
    assert [c.start for c in found] == [50, 10, 90]
    assert [c.score for c in found] == [90, 85, 80]


def test_ranges_are_fitted_to_the_video_and_the_job(echo, video):
    found = _run(echo, video, {"ranges": "280-330:90,299.5-320:80,10-40:70,50-70:60"},
                 config=_config(max_clips_per_video=2))
    # 280-330 is cut at the video's end; 299.5- has under a second left; then the cap of 2.
    assert [(c.start, c.end) for c in found] == [(280, 300), (10, 40)]


def test_no_moments_is_an_answer(echo, video):
    assert _run(echo, video, {"ranges": ""}) == []


# Keys an edit step might one day use, and their values. Edit is planned, not
# part of plugin contract 1, so none of them may change a clip today.
RANGE_EXTRAS = {"edit": {"mutes": [[0, 2]], "speed": 2.0, "title_overlay": {"text": "WATCH THIS"}},
                "music": "plugin-song.mp3", "watermark": {"text": "plugin-mark", "image": "plugin-logo.png"}}

RANGE_EXTRAS_MAIN = '''import json

from clipskitty_sdk import run


def main(job):
    extras = json.loads(job.settings.get("extras") or "{}")
    ranges = [{"start": 20, "end": 50, "score": 85, "label": "quark_burst", "title": "A quark burst",
               "reason": "the banner shows", **extras},
              {"start": 100, "end": 130, "label": "quiet", **extras}]
    (job.folder / "result.json").write_text(json.dumps({"plugin_api": 1, "ranges": ranges}), encoding="utf-8")
    job._finished = True


if __name__ == "__main__":
    run(main)
'''


def _keys_and_text(value) -> set:
    """Every key and every piece of text inside `value`, however deep."""
    if isinstance(value, dict):
        return set(value) | {x for v in value.values() for x in _keys_and_text(v)}
    if isinstance(value, (list, tuple)):
        return {x for v in value for x in _keys_and_text(v)}
    return {value} if isinstance(value, str) else set()


def test_a_range_s_unknown_keys_never_reach_the_clip(tmp_path, install_plugin, video):
    """A find run is never asked to edit: edits are suggested only by a run of
    their own, for the clips Clips Kitty makes (plugins/steps.py), and wait
    for the creator. So nothing a plugin adds to a range may change how a
    clip is made. The runner copies only the keys it knows into a
    ClipCandidate: start, end, score, title or label as the hook, reason, and
    notes when asked. A range carrying edit, music and watermark makes
    exactly the clip the same range without them makes. Apart from the
    answer's notes, printed to the log, the candidates are all of it that
    goes on past the runner, so a clip's render options (render_opts) can't
    differ either."""
    import dataclasses

    import yaml

    folder = tmp_path / "range-extras"
    (folder / "src").mkdir(parents=True)
    manifest = yaml.safe_load((ECHO / "clipskitty.yaml").read_text(encoding="utf-8"))
    manifest.update(id="fixture-dev/range-extras", name="Range Extras",
                    settings={"extras": {"type": "string", "default": "", "title": "Extra range keys, as JSON"}})
    (folder / "clipskitty.yaml").write_text(yaml.safe_dump(manifest), encoding="utf-8")
    (folder / "src" / "main.py").write_text(RANGE_EXTRAS_MAIN, encoding="utf-8")
    data_dir = tmp_path / "data"
    install_plugin(data_dir, folder)

    plain = _run(data_dir, video, {"extras": ""}, plugin="fixture-dev/range-extras")
    extra = _run(data_dir, video, {"extras": json.dumps(RANGE_EXTRAS)}, plugin="fixture-dev/range-extras")
    # The keys were in the answer the runner read...
    answers = [json.loads(p.read_text(encoding="utf-8")) for p in (data_dir / "plugins" / "runs").glob("*/result.json")]
    assert len(answers) == 2
    assert any(all(r.get("music") == "plugin-song.mp3" and "edit" in r and "watermark" in r for r in a["ranges"])
               for a in answers)
    # ...and changed nothing about the clips.
    assert [(c.start, c.end, c.score, c.hook) for c in extra] == [(20, 50, 85, "A quark burst"),
                                                                  (100, 130, 85, "quiet")]
    assert extra == plain
    kept = set().union(*(_keys_and_text(dataclasses.asdict(c)) for c in extra))
    for word in (*RANGE_EXTRAS, "title_overlay", "WATCH THIS", "plugin-song.mp3", "plugin-mark", "plugin-logo.png"):
        assert word not in kept, f"{word!r} from a plugin's range reached a ClipCandidate"
    assert "render_opts" not in {f.name for f in dataclasses.fields(extra[0])}


def test_progress_is_reported_as_the_analyze_stage(echo, video):
    from core import progress

    events = []
    progress.set_handler(events.append)
    try:
        _run(echo, video, {"ranges": "10-40:70"})
    finally:
        progress.set_handler(None)
    fractions = [e["fraction"] for e in events if e.get("stage") == "analyze"]
    assert fractions[0] == 0.0 and 0.25 in fractions and 0.75 in fractions and fractions[-1] == 1.0
    assert all(e.get("video_id") == "vid123" for e in events)


# ---- what the plugin is handed --------------------------------------------------


def test_the_job_holds_what_the_permissions_cover(echo, video):
    _run(echo, video, {"ranges": ""})
    job = _seen(echo)["job"]
    assert job["plugin"] == {"id": "fixture-dev/echo", "version": "1.0.0"}
    assert job["video"] == {"path": str(video.path), "id": "vid123", "title": "A match", "duration": 300.0,
                            "games": [{"name": "Example Game"}]}
    assert set(job["tools"]) == {"ffmpeg", "ffprobe"}  # no `ollama` permission, no model
    assert job["limits"] == {"max_clips": None, "min_duration": 10, "max_duration": 60}
    assert job["settings"] == {"mode": "ok", "ranges": ""}
    assert "api_key" not in json.dumps(job)
    transcript = _seen(echo)["transcript"]
    assert transcript == [{"start": 0.0, "end": 4.0, "text": "kick off",
                           "words": [{"start": 0.0, "end": 0.5, "word": "kick"}]},
                          {"start": 4.0, "end": 9.5, "text": "what a goal", "words": None}]


def test_a_listed_model_must_be_here_before_the_plugin_starts_and_its_path_is_handed_over(
        tmp_path, install_plugin, video):
    import hashlib

    import yaml

    from plugins import models, runner

    weights = b"example weights"
    folder = tmp_path / "echo-with-a-model"
    shutil.copytree(ECHO, folder)
    manifest = yaml.safe_load((folder / "clipskitty.yaml").read_text(encoding="utf-8"))
    manifest["models"] = [{"name": "weights", "source": "url", "id": "https://example.com/models/weights.onnx",
                           "sha256": hashlib.sha256(weights).hexdigest(), "license": "apache-2.0"}]
    (folder / "clipskitty.yaml").write_text(yaml.safe_dump(manifest), encoding="utf-8")
    data_dir = tmp_path / "data"
    install_plugin(data_dir, folder)

    with pytest.raises(runner.PluginError, match=r"^Echo can't run\. Its AI model 'weights' isn't downloaded yet\. "
                                                 r"Open Marketplace › Installed and press Download\.$"):
        _run(data_dir, video, {"ranges": ""})
    assert not (data_dir / "plugins" / "runs").exists()  # stopped before anything ran

    ref = models.refs_of(manifest)[0]
    models.download(data_dir, ref, fetcher=lambda url, dest: Path(dest).write_bytes(weights),
                    fetch_json=lambda url: pytest.fail("a url model needs no metadata"))
    _run(data_dir, video, {"ranges": ""})
    seen = _seen(data_dir)
    path = str(models.target_path(data_dir, ref, "weights.onnx"))
    assert seen["job"]["models"] == {"weights": {"source": "url", "id": ref["id"], "path": path,
                                                 "revision": ref["sha256"], "files": {"weights.onnx": path}}}
    assert seen["models"]["weights"]["path"] == path and seen["models"]["weights"]["source"] == "url"
    assert Path(path).read_bytes() == weights


def test_without_the_permissions_the_job_holds_none_of_it(tmp_path, install_plugin, video):
    folder = tmp_path / "echo"
    shutil.copytree(ECHO, folder)
    manifest = (folder / "clipskitty.yaml").read_text(encoding="utf-8")
    (folder / "clipskitty.yaml").write_text(
        manifest.replace("permissions: [video.read, transcript.read, ffmpeg]", "permissions: [ollama]"),
        encoding="utf-8")
    data_dir = tmp_path / "data"
    install_plugin(data_dir, folder)
    _run(data_dir, video, {"ranges": ""})
    seen = _seen(data_dir)
    assert "video" not in seen["job"] and "transcript" not in seen["job"] and seen["transcript"] is None
    assert seen["job"]["tools"] == {"ollama": {"host": "http://localhost:11434", "model": "gemma:7b"}}


def test_a_cloud_model_is_never_named_to_a_plugin(tmp_path, install_plugin, video):
    folder = tmp_path / "echo"
    shutil.copytree(ECHO, folder)
    manifest = (folder / "clipskitty.yaml").read_text(encoding="utf-8")
    (folder / "clipskitty.yaml").write_text(manifest.replace("transcript.read, ffmpeg]", "ollama]"), encoding="utf-8")
    data_dir = tmp_path / "data"
    install_plugin(data_dir, folder)
    config = _config()
    config["llm"]["backend"] = "openrouter/some-model"
    _run(data_dir, video, {"ranges": ""}, config=config)
    assert _seen(data_dir)["job"]["tools"]["ollama"]["model"] == ""


def test_the_process_gets_no_clips_kitty_settings_or_credentials(echo, video, monkeypatch):
    monkeypatch.setenv("CLIPS_STUDIO_OLLAMA_HOST", "http://127.0.0.1:9999")
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-not-a-real-key")
    monkeypatch.setenv("SOME_ACCESS_TOKEN", "tok")
    monkeypatch.setenv("HARMLESS_SETTING", "kept")
    _run(echo, video, {"ranges": ""})
    seen = _seen(echo)
    env = seen["env"]
    assert "CLIPS_STUDIO_OLLAMA_HOST" not in env and "OPENROUTER_API_KEY" not in env
    assert "SOME_ACCESS_TOKEN" not in env and env["HARMLESS_SETTING"] == "kept"
    from plugins._sdk import sdk_dir

    assert env["PYTHONPATH"] == str(sdk_dir())
    assert Path(env["CLIPSKITTY_JOB"]).parent.name == "runs"
    assert Path(seen["cwd"]).resolve() == ECHO.resolve()
    assert seen["secret"] is None


def test_a_secret_setting_travels_in_the_environment_only(echo, video):
    from core import secrets
    from plugins import runner

    secrets.save(echo, runner.secret_name("fixture-dev/echo"), {"api_key": "the-users-key"})
    _run(echo, video, {"ranges": ""})
    seen = _seen(echo)
    assert seen["secret"] == "the-users-key"
    run_folder = Path(seen["env"]["CLIPSKITTY_JOB"])
    assert "the-users-key" not in (run_folder / "job.json").read_text(encoding="utf-8")


# ---- refusals and failures ------------------------------------------------------


def test_a_plugin_that_is_not_installed_or_turned_off(tmp_path, install_plugin, video):
    from plugins.runner import PluginError

    data_dir = tmp_path / "data"
    with pytest.raises(PluginError, match="isn't installed"):
        _run(data_dir, video)
    install_plugin(data_dir, ECHO, enabled=False)
    with pytest.raises(PluginError, match="turned off"):
        _run(data_dir, video)


def test_settings_the_manifest_does_not_declare_are_refused(echo, video):
    from plugins.runner import PluginError

    with pytest.raises(PluginError, match="no setting called 'colour'"):
        _run(echo, video, {"colour": "red"})
    with pytest.raises(PluginError, match="is a secret"):
        _run(echo, video, {"api_key": "pasted into a job"})


@pytest.mark.parametrize("mode, message", [
    ("fail", "Echo failed: the kill feed could not be read"),
    ("crash", "Echo failed: boom inside the plugin"),
    ("bad", "Echo gave an answer Clips Kitty can't use: .*needs 0 <= start < end"),
    ("silent", "exited without writing result.json"),
])
def test_a_failed_run_says_what_went_wrong(echo, video, mode, message):
    from plugins.runner import PluginError

    with pytest.raises(PluginError, match=message):
        _run(echo, video, {"mode": mode})


def test_a_plugin_that_stops_without_saying_why_gets_a_plain_sentence(tmp_path, install_plugin, video, caplog):
    """No error line of its own (a crash outside the SDK, a program of its
    own): the user reads what to do next, and the log keeps the exit code and
    the last line for a bug report."""
    import yaml

    from plugins import runner

    folder = tmp_path / "echo-that-stops"
    shutil.copytree(ECHO, folder)
    (folder / "src" / "main.py").write_text("import sys\nprint('half way', flush=True)\nsys.exit(3)\n",
                                            encoding="utf-8")
    data_dir = tmp_path / "data"
    install_plugin(data_dir, folder)
    with pytest.raises(runner.PluginError) as e:
        _run(data_dir, video)
    assert str(e.value) == ("Echo stopped before it finished. Try again; if it happens again, send a bug report "
                            "from Feedback (it includes the details).")
    assert "stopped with exit code 3: half way" in caplog.text

    manifest = yaml.safe_load((folder / "clipskitty.yaml").read_text(encoding="utf-8"))
    manifest["run"]["command"] = ["bin/not-there"]
    (folder / "clipskitty.yaml").write_text(yaml.safe_dump(manifest), encoding="utf-8")
    with pytest.raises(runner.PluginError) as e:
        _run(data_dir, video)
    assert str(e.value).startswith("Clips Kitty couldn't start Echo. Try again;")
    assert "not-there" not in str(e.value) and "not-there" in caplog.text


def test_stray_output_goes_to_the_log_and_does_not_break_the_run(echo, video, capsys):
    found = _run(echo, video, {"mode": "noisy", "ranges": "10-40:70"})
    assert len(found) == 1
    out = capsys.readouterr().out
    assert "a plain print from the plugin" in out and "something on standard error" in out


def test_a_plugin_that_runs_too_long_is_stopped(echo, video, monkeypatch):
    from plugins import runner

    monkeypatch.setattr(runner, "timeout_seconds", lambda manifest: 1.5)
    with pytest.raises(runner.PluginError, match="longer than"):
        _run(echo, video, {"mode": "sleep"})


def test_cancel_stops_the_plugin(echo, video):
    from core import cancel

    timer = threading.Timer(1.0, cancel.request_cancel, args=("vid123",))
    timer.start()
    try:
        with pytest.raises(cancel.CancelledError):
            _run(echo, video, {"mode": "sleep"})
    finally:
        timer.cancel()
        cancel.clear("vid123")


def test_no_python_to_run_it_with_is_said_plainly(echo, video, monkeypatch):
    from plugins import runner

    monkeypatch.setattr(runner.host, "find_python", lambda setting=None: None)
    with pytest.raises(runner.PluginError, match="needs Python"):
        _run(echo, video)


def test_only_the_last_few_job_folders_are_kept(echo, video):
    from plugins import runner

    for _ in range(runner.KEEP_RUNS + 2):
        _run(echo, video, {"ranges": ""})
    assert len(list((echo / "plugins" / "runs").iterdir())) == runner.KEEP_RUNS


# ---- the steps: find, understand, rate --------------------------------------------


def test_a_plain_pipeline_job_json_and_folder_name_are_unchanged(echo, video):
    """A finder whose manifest uses none of the step words gets the job.json
    it always did, byte for byte, in a folder named as it always was, and
    its progress events carry no plugin name."""
    from core import progress

    events = []
    progress.set_handler(events.append)
    try:
        _run(echo, video, {"ranges": ""})
    finally:
        progress.set_handler(None)
    assert events and all(set(e) == {"stage", "video_id", "fraction", "message"} for e in events)
    (folder,) = list((echo / "plugins" / "runs").iterdir())
    assert re.fullmatch(r"vid123-\d{8}-\d{6}", folder.name)
    text = (folder / "job.json").read_text(encoding="utf-8")
    tools = json.loads(text)["tools"]
    assert set(tools) == {"ffmpeg", "ffprobe"}
    expected = {
        "plugin_api": 1,
        "plugin": {"id": "fixture-dev/echo", "version": "1.0.0"},
        "settings": {"mode": "ok", "ranges": ""},
        "limits": {"max_clips": None, "min_duration": 10, "max_duration": 60},
        "focus": None,
        "models": {},
        "tools": tools,
        "output_dir": str(folder / "out"),
        "video": {"path": str(video.path), "id": "vid123", "title": "A match", "duration": 300.0,
                  "games": [{"name": "Example Game"}]},
        "transcript": {"path": str(folder / "transcript.json"), "language": "en"},
    }
    assert text == json.dumps(expected, ensure_ascii=False, indent=1)


def _notes_finder(tmp_path, install_plugin, *, outputs=None) -> Path:
    folder = NOTES_FINDER
    if outputs is not None:
        folder = tmp_path / "notes-finder"
        shutil.copytree(NOTES_FINDER, folder)
        manifest = (folder / "clipskitty.yaml").read_text(encoding="utf-8")
        (folder / "clipskitty.yaml").write_text(
            manifest.replace("outputs: [ranges, context]", f"outputs: [{', '.join(outputs)}]"), encoding="utf-8")
    data_dir = tmp_path / "data"
    install_plugin(data_dir, folder)
    return data_dir


@pytest.mark.parametrize("mode", ["sdk", "raw"])
def test_a_finder_that_declares_context_adds_plugin_notes(tmp_path, install_plugin, video, mode):
    """Its notes are checked and cleaned as they are read (links taken out,
    newlines and tabs collapsed, empty ones dropped), whether the SDK wrote
    them or the plugin wrote result.json by hand."""
    from plugins._sdk import manifest as mf

    _, report = mf.validate_folder(NOTES_FINDER)
    assert report.ok and not report.warnings, (report.errors, report.warnings)
    data_dir = _notes_finder(tmp_path, install_plugin)
    found = _run(data_dir, video, {"mode": mode}, plugin="fixture-dev/notes-finder")
    job = _seen(data_dir)["job"]
    assert job["steps"] == ["find", "understand"]
    assert "moments" not in job and "min_score" not in job["limits"]

    burst, quiet = found
    who = {"plugin": "fixture-dev/notes-finder", "version": "1.0.0", "name": "Notes Finder"}
    assert (burst.start, burst.end, burst.score) == (10, 40, 80)
    assert burst.subscores == {
        "plugin": "fixture-dev/notes-finder", "plugin_version": "1.0.0", "plugin_label": "quark_burst",
        "plugin_why": "",
        "plugin_notes": [{**who, "text": "First quark burst of the match"},
                         {**who, "text": "The replay is at right now"}],
    }
    assert quiet.subscores == {"plugin": "fixture-dev/notes-finder", "plugin_version": "1.0.0",
                               "plugin_label": "quiet", "plugin_why": ""}


@pytest.mark.parametrize("mode", ["sdk", "raw"])
def test_a_finder_without_it_keeps_todays_subscores(tmp_path, install_plugin, video, mode, capsys):
    """The same plugin without `context` in its outputs: its job.json has no
    steps, a stray `context` in its answer is left alone, and its clips get
    exactly the four plugin subscores they always did."""
    data_dir = _notes_finder(tmp_path, install_plugin, outputs=["ranges"])
    found = _run(data_dir, video, {"mode": mode}, plugin="fixture-dev/notes-finder")
    assert "steps" not in _seen(data_dir)["job"]
    assert [c.subscores for c in found] == [
        {"plugin": "fixture-dev/notes-finder", "plugin_version": "1.0.0", "plugin_label": label, "plugin_why": ""}
        for label in ("quark_burst", "quiet")]
    if mode == "sdk":
        assert "understand ignored: this job didn't ask for notes" in capsys.readouterr().out


def _rater(tmp_path) -> Path:
    folder = tmp_path / "quarkbloom-rater"
    (folder / "src").mkdir(parents=True)
    shutil.copy(ROOT / "tests" / "fixtures" / "plugins" / "manifests" / "valid" / "quarkbloom-rater.yaml",
                folder / "clipskitty.yaml")
    (folder / "src" / "main.py").write_text("from clipskitty_sdk import run\n\nrun(lambda job: None)\n",
                                            encoding="utf-8")
    return folder


def test_installed_choice_refuses_a_finder_for_rate_and_a_rater_for_find(tmp_path, install_plugin, video):
    from plugins import runner, store

    data_dir = tmp_path / "data"
    install_plugin(data_dir, ECHO)
    install_plugin(data_dir, NOTES_FINDER)
    install_plugin(data_dir, _rater(tmp_path))
    echo, notes, rater = ({"id": pid} for pid in ("fixture-dev/echo", "fixture-dev/notes-finder",
                                                   "example-dev/quarkbloom-rater"))

    assert store.installed_choice(data_dir, echo).id == "fixture-dev/echo"  # find, as always
    assert store.installed_choice(data_dir, notes, step="find").id == "fixture-dev/notes-finder"
    assert store.installed_choice(data_dir, rater, step="rate").id == "example-dev/quarkbloom-rater"
    refused = [
        (echo, "rate", ("the pipeline Echo can't rate moments others found: "
                        "its manifest needs moments in inputs and ratings in outputs")),
        (echo, "understand", ("the pipeline Echo can't understand moments others found: "
                              "its manifest needs moments in inputs and context in outputs")),
        (notes, "understand", ("the pipeline Notes Finder can't understand moments others found: "
                               "its manifest needs moments in inputs and context in outputs")),
        (rater, "understand", ("the pipeline Quarkbloom Rater can't understand moments others found: "
                               "its manifest needs moments in inputs and context in outputs")),
        (rater, "find", ("the pipeline Quarkbloom Rater doesn't find moments: it rates or understands "
                         "moments others found. Choose it under Rate & understand instead")),
    ]
    for choice, step, message in refused:
        with pytest.raises(store.ChoiceProblem) as e:
            store.installed_choice(data_dir, choice, step=step)
        assert (str(e.value), e.value.code) == (message, "step")
    with pytest.raises(store.ChoiceProblem):
        store.installed_choice(data_dir, rater)  # find, the default

    # Named as a job's pipeline, a rater is refused before anything runs.
    with pytest.raises(runner.PluginError) as e:
        _run(data_dir, video, plugin="example-dev/quarkbloom-rater")
    assert str(e.value) == ("The pipeline Quarkbloom Rater doesn't find moments: it rates or understands "
                            "moments others found. Choose it under Rate & understand instead")
    assert not (data_dir / "plugins" / "runs").exists()


@pytest.mark.parametrize("value, message", [
    (5, '{} must be an object: {{"id": "publisher/name"}}'),
    ({"id": "example-dev/x", "x": 1}, "{} has unknown fields: x"),
    ({"id": "Example/X"}, "{}.id must look like publisher/name (lower case, digits and hyphens)"),
    ({"id": "example-dev/x", "version": "one"}, "{}.version must be a version like 1.2.0"),
    ({"id": "example-dev/x", "settings": []}, "{}.settings must be an object"),
    ({"id": "example-dev/x", "settings": {"notes": "x" * 16_001}}, "{}.settings is too large"),
])
def test_clean_choice_names_the_field_it_checks(value, message):
    from plugins import store

    with pytest.raises(ValueError) as e:
        store.clean_choice(value, what="rate[1]")
    assert str(e.value) == message.format("rate[1]")
    with pytest.raises(ValueError) as e:
        store.clean_choice(value)
    assert str(e.value) == message.format("pipeline")  # today's message
    assert store.clean_choice("example-dev/x", what="rate[0]") == {"id": "example-dev/x"}


def test_choice_problems_carry_a_code_and_todays_message(tmp_path, install_plugin, monkeypatch):
    """Each refusal says which check failed, with the message it always had,
    and is still a ValueError for the callers that catch one."""
    from plugins import registry, store

    data_dir = tmp_path / "data"
    echo = {"id": "fixture-dev/echo"}

    def problem(choice=echo):
        with pytest.raises(store.ChoiceProblem) as e:
            store.installed_choice(data_dir, choice)
        assert isinstance(e.value, ValueError)
        return e.value.code, str(e.value)

    assert problem() == ("missing", "the pipeline fixture-dev/echo isn't installed")
    assert problem({**echo, "version": "2.0.0"}) == ("missing", "the pipeline fixture-dev/echo 2.0.0 isn't installed")
    install_plugin(data_dir, ECHO, enabled=False)
    assert problem() == ("off", "the pipeline Echo is turned off; turn it on in Marketplace › Installed first")
    install_plugin(data_dir, ECHO)
    with monkeypatch.context() as m:
        m.setattr(store, "app_version", lambda: "1.9.0")
        assert problem() == ("incompatible", ("the pipeline Echo can't run here: it needs Clips Kitty >=2.0, "
                                              "and this is 1.9.0"))
    with monkeypatch.context() as m:
        m.setattr(registry, "blocked_check", lambda data_dir: lambda pid, version: {
            "severity": "blocked", "reason": "It sends videos it does not declare"})
        assert problem() == ("blocked", ("the pipeline Echo 1.0.0 is blocked: It sends videos it does not declare. "
                                         "Remove it in Marketplace › Installed."))
    assert problem({**echo, "settings": {"colour": "red"}}) == ("settings", "this pipeline has no setting called 'colour'")
    assert problem({**echo, "settings": {"api_key": "pasted"}}) == (
        "settings", "'api_key' is a secret: set it in the pipeline's settings, not in a job")
    assert store.installed_choice(data_dir, echo).id == "fixture-dev/echo"


def test_moment_runs_default_to_a_ten_minute_limit():
    """A plugin that rates or understands moments, without a time limit of its
    own, is stopped after 10 minutes; a find run still gets 60. A manifest's
    own limit applies to both, up to 24 hours."""
    from plugins import runner

    moment = runner.MOMENT_TIMEOUT_MINUTES
    assert moment == 10
    assert runner.timeout_seconds({"run": {}}, default=moment) == 10 * 60
    assert runner.timeout_seconds({}, default=moment) == 10 * 60
    assert runner.timeout_seconds({"run": {"timeout_minutes": "soon"}}, default=moment) == 10 * 60
    assert runner.timeout_seconds({"run": {}}) == 60 * 60
    for default in (moment, runner.DEFAULT_TIMEOUT_MINUTES):
        assert runner.timeout_seconds({"run": {"timeout_minutes": 5}}, default=default) == 5 * 60
        assert runner.timeout_seconds({"run": {"timeout_minutes": 10**6}}, default=default) == 24 * 60 * 60


def test_the_runner_and_the_sdk_share_one_time_limit_rule():
    """`python -m clipskitty_sdk run` stops a plugin when Clips Kitty would:
    the runner's timeout_seconds is a thin wrapper over the SDK's rule
    (host.timeout_seconds), with the same numbers. The runner keeps its own
    name, which scripts/check_compatibility.py and a test replace."""
    from plugins import runner
    from plugins._sdk import host

    assert (runner.DEFAULT_TIMEOUT_MINUTES, runner.MOMENT_TIMEOUT_MINUTES, runner.MAX_TIMEOUT_MINUTES) == \
        (host.FIND_TIMEOUT_MINUTES, host.MOMENT_TIMEOUT_MINUTES, host.MAX_TIMEOUT_MINUTES)
    manifests = ({}, {"run": {}}, {"run": "builtin"}, {"run": {"timeout_minutes": 5}},
                 {"run": {"timeout_minutes": 0.25}}, {"run": {"timeout_minutes": "soon"}},
                 {"run": {"timeout_minutes": 10**6}})
    for manifest in manifests:
        assert runner.timeout_seconds(manifest) == host.timeout_seconds(manifest), manifest
        assert runner.timeout_seconds(manifest, default=runner.MOMENT_TIMEOUT_MINUTES) == \
            host.timeout_seconds(manifest, host.MOMENT_TIMEOUT_MINUTES), manifest
    assert host.timeout_seconds({"run": {"timeout_minutes": 0.25}}) == 60  # never under a minute


def test_the_shared_run_steps_tag_the_folder_and_name_the_plugin_only_when_asked(echo, video):
    """_new_folder and _execute, which find runs and moment runs share: a tag
    goes on the folder's name, and a plugin name on every progress event."""
    from core import progress
    from plugins import runner

    plugin, command, python, models = runner._prepare({"id": "fixture-dev/echo"}, data_dir=echo,
                                                      config=_config(), step="find")
    folder = runner._new_folder(echo, "vid123", "understand-rate")
    assert re.fullmatch(r"vid123-\d{8}-\d{6}-understand-rate", folder.name) and not folder.exists()
    job, transcript = runner.build_job(plugin, {"id": plugin.id}, video=video, segments=SEGMENTS, language="en",
                                       config=_config(), output_dir=folder / "out", models=models,
                                       steps=["understand", "rate"], moments=[], min_score=55)
    assert job["steps"] == ["understand", "rate"] and job["moments"] == []
    assert job["limits"] == {"max_clips": None, "min_duration": 10, "max_duration": 60, "min_score": 55}
    runner.host.write_job(folder, job, transcript)
    again = runner._new_folder(echo, "vid123", "understand-rate")
    assert again.name.startswith(folder.name) and again.name.endswith("~2")

    events = []
    progress.set_handler(events.append)
    try:
        runner._execute(plugin, command, python, folder, video=video, data_dir=echo, stage="ranking",
                        timeout=60, label_name="Echo")
    finally:
        progress.set_handler(None)
    assert [(e["stage"], e["fraction"], e["plugin"]) for e in events] == [("ranking", 0.25, "Echo"),
                                                                          ("ranking", 0.75, "Echo")]

    # The time limit is the one it is given.
    folder = runner._new_folder(echo, "vid123")
    job, transcript = runner.build_job(plugin, {"id": plugin.id, "settings": {"mode": "sleep"}}, video=video,
                                       segments=SEGMENTS, language="en", config=_config(),
                                       output_dir=folder / "out", models=models)
    runner.host.write_job(folder, job, transcript)
    with pytest.raises(runner.PluginError, match="longer than"):
        runner._execute(plugin, command, python, folder, video=video, data_dir=echo, stage="analyze", timeout=1.5)


# ---- an edit run: suggestions for the clips Clips Kitty makes ---------------------------

TRIMMER_ID = "fixture-dev/trimmer"
# Two clips as plugins/steps.py hands them over.
CLIPS = [{"id": "m1", "start": 10.0, "end": 30.0, "score": 80, "found_score": 80, "found_by": "clipskitty",
          "label": "", "signals": {}, "title": "A quark burst", "reason": "loud reaction", "context": []},
         {"id": "m2", "start": 100.0, "end": 120.0, "score": 70, "found_score": 70, "found_by": "clipskitty",
          "label": "", "signals": {}, "title": "Round one", "reason": "fast speech", "context": []}]


@pytest.fixture
def trimmer(tmp_path, install_plugin):
    data_dir = tmp_path / "data"
    install_plugin(data_dir, TRIMMER)
    return data_dir


def _edit_run(data_dir, video, edits=None, *, config=None, crops=("track", "center", "letterbox"), **settings):
    from plugins import runner

    choice = {"id": TRIMMER_ID, "settings": {**({"edits": json.dumps(edits)} if edits is not None else {}),
                                             **settings}}
    return runner.answer_moments(choice, ["edit"], CLIPS, video=video, segments=SEGMENTS, language="en",
                                 config=config or _config(min_score=70), data_dir=data_dir, stage="edit",
                                 crops=crops)


def test_an_edit_run_has_no_min_score_and_has_crops(trimmer, video):
    from core import progress
    from plugins import runner

    events = []
    progress.set_handler(events.append)
    try:
        out = _edit_run(trimmer, video, {"m1": {"fade_out": 0.45, "crop": "letterbox"}, "m2": {"crop": "center"}},
                        crops=("track", "center"))
    finally:
        progress.set_handler(None)
    job = _seen(trimmer)["job"]
    assert job["steps"] == ["edit"]
    # Its clips are chosen already: the creator's minimum score isn't sent, the layouts they can use are.
    assert job["limits"] == {"max_clips": None, "min_duration": 10, "max_duration": 60, "crops": ["track", "center"]}
    assert [m["id"] for m in job["moments"]] == ["m1", "m2"]
    (folder,) = (trimmer / "plugins" / "runs").iterdir()
    assert re.fullmatch(r"vid123-\d{8}-\d{6}-edit", folder.name)
    # Read with host.read_edits: fitted to the clip and to the editor, with a line for each change.
    assert out == {"plugin": TRIMMER_ID, "version": "1.0.0", "name": "Trimmer", "steps": ["edit"],
                   "edits": {"m1": {"edit": {"fade_out": 0.5}}, "m2": {"edit": {"crop": "center"}}},
                   "ignored": ["changed: m1's fade_out 0.45 s to 0.5 s, the nearest the editor offers",
                               "ignored: m1's crop \"letterbox\": this job's clips don't use \"letterbox\""]}
    assert [(e["stage"], e["fraction"], e["plugin"]) for e in events] == [
        ("edit", 0.0, "Trimmer"), ("edit", 0.5, "Trimmer"), ("edit", 1.0, "Trimmer")]
    assert events[-1]["message"] == "Trimmer suggested edits for 2 clip(s)"
    # Without layouts given, it is told there are none.
    _edit_run(trimmer, video, {"m1": {"fade_in": 0.3}}, crops=None)
    assert _seen(trimmer)["job"]["limits"]["crops"] == []
    # An edit run is a run of its own: never asked beside another step.
    for steps in (["rate", "edit"], ["edit", "understand"], ["edit", "edit"]):
        with pytest.raises(ValueError, match="an edit run is asked to suggest edits and nothing else"):
            runner.answer_moments({"id": TRIMMER_ID}, steps, CLIPS, video=video, segments=SEGMENTS, language="en",
                                  config=_config(), data_dir=trimmer, stage="edit", crops=())


def test_an_edit_run_stops_at_its_limit(trimmer, video, monkeypatch):
    from plugins import runner

    asked = []

    def short(manifest, default=runner.DEFAULT_TIMEOUT_MINUTES):
        asked.append(default)
        return 1.5

    monkeypatch.setattr(runner, "timeout_seconds", short)
    with pytest.raises(runner.PluginError) as e:
        _edit_run(trimmer, video, mode="sleep")
    assert asked == [runner.MOMENT_TIMEOUT_MINUTES]  # a moment run's limit, unless its manifest sets one
    assert e.value.why == "It took longer than its 1 minute limit, so Clips Kitty stopped it."
    assert str(e.value) == "Trimmer took longer than its 1 minute limit, so Clips Kitty stopped it."


def test_an_edit_run_that_cannot_start_says_it_can_no_longer_suggest_edits(echo, video, install_plugin):
    from plugins import runner

    install_plugin(echo, TRIMMER, enabled=False)
    with pytest.raises(runner.PluginError) as e:
        runner.answer_moments({"id": "fixture-dev/echo"}, ["edit"], CLIPS, video=video, segments=SEGMENTS,
                              language="en", config=_config(), data_dir=echo, stage="edit", crops=())
    assert (e.value.code, e.value.why) == ("step", "It can no longer suggest edits.")
    assert str(e.value) == ("The pipeline Echo can't suggest edits for clips: its manifest needs moments in inputs "
                            "and edits in outputs")
    with pytest.raises(runner.PluginError) as e:
        _edit_run(echo, video, {"*": {"fade_in": 0.3}})
    assert (e.value.code, e.value.why) == ("off", "It's turned off in Marketplace › Installed.")
    assert not (echo / "plugins" / "runs").exists()  # nothing ran


# ---- dogfooding: Clips Kitty's own scorer behind the contract -------------------


def test_the_first_party_adapter_satisfies_the_contract(tmp_path, install_plugin, sample_transcript):
    data_dir = tmp_path / "data"
    install_plugin(data_dir, ADAPTER)
    from plugins import runner

    video = DownloadedVideo(video_id="sample", title="Sample stream", path=tmp_path / "none.mp4",
                            duration=sample_transcript[-1].end)
    found = runner.find_clips({"id": "clips-kitty-examples/transcript-highlights",
                               "settings": {"fake_model": True}},
                              video=video, segments=sample_transcript, language="en",
                              config=_config(), data_dir=data_dir)
    # The same moments examples/score_a_transcript.py --fake picks, now through a plugin process.
    from analysis.highlights import find_highlights
    from examples.fake_backend import FakeBackend

    direct, _ = find_highlights(sample_transcript, FakeBackend(), min_score=60, max_clips=3,
                                min_duration=10, max_duration=60)
    assert found and [(c.start, c.end, c.score, c.hook) for c in found] == \
        [(c.start, c.end, c.score, c.hook) for c in direct]
    assert all(c.source == "plugin:clips-kitty-examples/transcript-highlights@1.0.0" for c in found)


@pytest.mark.skipif(os.name == "nt", reason="a shell script stands in for a program; Windows runs .exe files")
def test_a_plugin_can_be_a_program_rather_than_a_python_script(tmp_path, install_plugin, video):
    """run.command can start a program shipped in the plugin's folder; it is
    started from that folder by its full path, never looked up on PATH."""
    import yaml

    folder = tmp_path / "program-plugin"
    (folder / "bin").mkdir(parents=True)
    script = folder / "bin" / "answer"
    script.write_text('#!/bin/sh\nprintf \'{"plugin_api": 1, "ranges": [{"start": 4, "end": 19, "score": 77}]}\' '
                      '> "$1/result.json"\n', encoding="utf-8")
    script.chmod(0o755)
    manifest = yaml.safe_load((ECHO / "clipskitty.yaml").read_text(encoding="utf-8"))
    manifest.update(id="fixture-dev/program", name="Program", run={"command": ["bin/answer"]}, settings={})
    (folder / "clipskitty.yaml").write_text(yaml.safe_dump(manifest), encoding="utf-8")
    data_dir = tmp_path / "data"
    install_plugin(data_dir, folder)
    clips = _run(data_dir, video, plugin="fixture-dev/program")
    assert [(c.start, c.end, c.score) for c in clips] == [(4.0, 19.0, 77)]
