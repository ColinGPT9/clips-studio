"""Running a plugin pipeline for one video (plugins/runner.py).

The runner hands a plugin process a job folder and turns its answer into the
ClipCandidates the rest of process_video renders. These tests run real child
processes: tests/fixtures/plugins/echo, which does whatever its `mode` setting
says, and the first-party adapter in examples/pipelines/transcript-highlights.
"""

import json
import shutil
import threading
from pathlib import Path

import pytest

from core.models import DownloadedVideo, Segment

ROOT = Path(__file__).resolve().parent.parent
ECHO = ROOT / "tests" / "fixtures" / "plugins" / "echo"
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
