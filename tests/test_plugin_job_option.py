"""The `pipeline` job option: a job that names a plugin pipeline.

Through the API it is checked like every other option (its shape, that the
plugin is installed and on, and the modes it can't share a job with). In
process_video it replaces only the detection step, and a job without it
reaches find_clips exactly as before.
"""

import json
import shutil
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
ECHO = ROOT / "tests" / "fixtures" / "plugins" / "echo"
URL = "https://www.youtube.com/watch?v=aB3dEfGhIjK"


@pytest.fixture
def api(tmp_path, install_plugin):
    pytest.importorskip("fastapi")
    pytest.importorskip("httpx")
    pytest.importorskip("numpy")
    pytest.importorskip("yt_dlp")  # sources.dispatch imports the YouTube source
    from fastapi.testclient import TestClient

    from main import BUNDLED_CONFIG, load_config
    from server.api import create_app

    settings = tmp_path / "settings.yaml"
    shutil.copy(ROOT / "config" / "settings.yaml", settings)
    config = load_config(BUNDLED_CONFIG)
    data_dir = tmp_path / "data"
    config["paths"]["data_dir"] = str(data_dir)
    app = create_app(config, settings)
    # No `with`: startup never runs, so no worker thread starts.
    return TestClient(app, base_url="http://127.0.0.1"), data_dir, install_plugin


def _payload(client, job_id) -> dict:
    return json.loads(client.get(f"/jobs/{job_id}").json()["payload"])


# ---- through the API ------------------------------------------------------------


def test_a_job_can_name_an_installed_pipeline(api):
    client, data_dir, install = api
    install(data_dir, ECHO)
    r = client.post("/jobs", json={"url": URL, "pipeline": {"id": "fixture-dev/echo", "version": "1.0.0",
                                                            "settings": {"mode": "ok"}}})
    assert r.status_code == 200, r.text
    assert _payload(client, r.json()["job_id"])["pipeline"] == {
        "id": "fixture-dev/echo", "version": "1.0.0", "settings": {"mode": "ok"}}


def test_a_bare_id_is_enough(api):
    client, data_dir, install = api
    install(data_dir, ECHO)
    job_id = client.post("/jobs", json={"url": URL, "pipeline": "fixture-dev/echo"}).json()["job_id"]
    assert _payload(client, job_id)["pipeline"] == {"id": "fixture-dev/echo"}


def test_a_pipeline_that_is_not_installed_or_is_off_is_refused_at_once(api):
    client, data_dir, install = api
    r = client.post("/jobs", json={"url": URL, "pipeline": {"id": "fixture-dev/echo"}})
    assert r.status_code == 400 and r.json()["detail"] == "pipeline: the pipeline fixture-dev/echo isn't installed"
    install(data_dir, ECHO, enabled=False)
    r = client.post("/jobs", json={"url": URL, "pipeline": {"id": "fixture-dev/echo"}})
    assert r.status_code == 400 and "turned off" in r.json()["detail"]
    install(data_dir, ECHO)
    r = client.post("/jobs", json={"url": URL, "pipeline": {"id": "fixture-dev/echo", "version": "9.9.9"}})
    assert r.status_code == 400 and "fixture-dev/echo 9.9.9 isn't installed" in r.json()["detail"]


def test_settings_that_do_not_fit_the_manifest_are_refused_at_once(api):
    client, data_dir, install = api
    install(data_dir, ECHO)
    for settings, fragment in (({"mode": "explode"}, "setting 'mode': 'explode' is not one of"),
                               ({"colour": "red"}, "no setting called 'colour'"),
                               ({"api_key": "abc"}, "'api_key' is a secret")):
        r = client.post("/jobs", json={"url": URL, "pipeline": {"id": "fixture-dev/echo", "settings": settings}})
        assert r.status_code == 400 and fragment in r.json()["detail"], r.text


@pytest.mark.parametrize("pipeline, fragment", [
    ({"id": "Not An Id"}, "publisher/name"),
    ({"id": "a/b", "colour": "red"}, "unknown fields: colour"),
    ({"id": "a/b", "version": "latest"}, "a version like"),
    ({"id": "a/b", "settings": ["x"]}, "settings must be an object"),
])
def test_a_badly_shaped_choice_is_refused(api, pipeline, fragment):
    client, _, _ = api
    r = client.post("/jobs", json={"url": URL, "pipeline": pipeline})
    assert r.status_code == 400 and fragment in r.json()["detail"], r.text


def test_a_choice_that_is_neither_an_id_nor_an_object_fails_validation(api):
    client, _, _ = api
    assert client.post("/jobs", json={"url": URL, "pipeline": ["a/b"]}).status_code == 422


@pytest.mark.parametrize("other", [{"sport": {"name": "soccer"}}, {"gaming_scoring": True},
                                   {"longform": {"mode": "highlights"}}])
def test_modes_that_pick_moments_their_own_way_cannot_share_the_job(api, other):
    client, data_dir, install = api
    install(data_dir, ECHO)
    r = client.post("/jobs", json={"url": URL, "pipeline": "fixture-dev/echo", **other})
    assert r.status_code == 400 and "can't be combined with Sports, Gaming scoring or Longform" in r.json()["detail"]


def test_layouts_can_share_the_job(api):
    client, data_dir, install = api
    install(data_dir, ECHO)
    for layout in ({"gaming": True}, {"vertical_live": True}, {"podcast": True}):
        r = client.post("/jobs", json={"url": URL, "force": True, "pipeline": "fixture-dev/echo", **layout})
        assert r.status_code == 200, (layout, r.text)


def test_a_batch_skips_a_row_with_a_bad_pipeline(api):
    client, data_dir, install = api
    install(data_dir, ECHO)
    body = client.post("/jobs/batch", json={"items": [
        {"url": "https://www.twitch.tv/videos/111111111", "pipeline": "fixture-dev/echo"},
        {"url": "https://www.twitch.tv/videos/222222222", "pipeline": "fixture-dev/missing"}]}).json()
    assert [c["video_id"] for c in body["created"]] == ["tw_111111111"]
    assert body["skipped"][0]["reason"] == "bad_option" and "isn't installed" in body["skipped"][0]["detail"]


def test_a_queued_job_can_gain_and_drop_a_pipeline(api):
    client, data_dir, install = api
    install(data_dir, ECHO)
    job_id = client.post("/jobs", json={"url": URL}).json()["job_id"]
    assert client.patch(f"/jobs/{job_id}", json={"pipeline": "fixture-dev/echo"}).status_code == 200
    assert _payload(client, job_id)["pipeline"] == {"id": "fixture-dev/echo"}
    assert client.patch(f"/jobs/{job_id}", json={"clear": ["pipeline"]}).status_code == 200
    assert "pipeline" not in _payload(client, job_id)


def test_a_watch_with_a_pipeline_beside_longform_keeps_longform():
    pytest.importorskip("yaml")
    pytest.importorskip("fastapi")  # server.automation mounts routes
    from server import automation

    watch = {"options": json.dumps({"pipeline": {"id": "fixture-dev/echo"}, "preset": "highlights"})}
    payload = automation.job_payload(watch, URL)
    assert payload.get("longform") and "pipeline" not in payload
    watch = {"options": json.dumps({"pipeline": {"id": "fixture-dev/echo"}, "gaming": True})}
    payload = automation.job_payload(watch, URL)
    assert payload["pipeline"] == {"id": "fixture-dev/echo"} and payload["gaming"] is True


# ---- in process_video ------------------------------------------------------------


class _Stop(Exception):
    """Stops a run where the test has seen what it needs."""


@pytest.fixture
def pipeline_run(monkeypatch, tmp_path, db):
    """process_video with download, transcription and analysis stubbed out,
    recording which detector it called and with what."""
    pytest.importorskip("numpy")
    pytest.importorskip("cv2")
    pytest.importorskip("PIL")  # the end card (video/outro.py)
    import core.pipeline as pipeline
    from core.models import DownloadedVideo, Segment
    from main import BUNDLED_CONFIG, load_config

    source = tmp_path / "stream.mp4"
    source.write_bytes(b"not really a video")
    video = DownloadedVideo(video_id="vid001", title="Stream", path=source, duration=600.0)
    segments = [Segment(0.0, 5.0, "hello there")]
    monkeypatch.setattr(pipeline, "_cached_or_download", lambda *_a, **_k: video)
    monkeypatch.setattr(pipeline, "transcribe", lambda *_a, **_k: segments)
    monkeypatch.setattr("transcription.transcriber.detected_language", lambda *_a, **_k: "en")
    monkeypatch.setattr("video.encoding.source_codec", lambda _p: "h264")
    monkeypatch.setattr("analysis.audio_features.extract_audio_features", lambda _p: {})
    monkeypatch.setattr("analysis.visual_features.extract_visual_features", lambda _p: {})
    monkeypatch.setattr("analysis.hype.audience_signals", lambda *_a, **_k: (None, None))
    monkeypatch.setattr(pipeline, "_with_usable_model", lambda cfg: cfg)
    monkeypatch.setattr(pipeline, "create_backend", lambda cfg: object())
    calls: dict = {}

    def find_clips(*args, **kwargs):
        calls["find_clips"] = (args, kwargs)
        raise _Stop

    def plugin_find_clips(choice, **kwargs):
        calls["plugin"] = (choice, kwargs)
        return []

    def gaming_inputs(*_a, **_k):
        calls["gaming_inputs"] = True
        return None, None, None

    monkeypatch.setattr(pipeline, "find_clips", find_clips)
    monkeypatch.setattr(pipeline, "clip_direction", lambda *_a, **_k: None)
    monkeypatch.setattr(pipeline, "_gaming_scoring_inputs", gaming_inputs)
    monkeypatch.setattr("plugins.runner.find_clips", plugin_find_clips)

    def run(**clips):
        config = load_config(BUNDLED_CONFIG)
        config["paths"]["data_dir"] = str(tmp_path / "data")
        config["clips"].update({"captions": False, **clips})
        calls.clear()
        try:
            out = pipeline.process_video("local:stream", config, db, force=True)
        except _Stop:
            out = None
        return out, dict(calls)

    return run


def test_a_job_without_a_pipeline_reaches_find_clips_as_before(pipeline_run):
    out, calls = pipeline_run()
    assert out is None and "plugin" not in calls
    args, _kwargs = calls["find_clips"]
    assert [s.text for s in args[1]] == ["hello there"]


def test_a_gaming_job_without_a_pipeline_still_gets_gaming_scoring(pipeline_run):
    _out, calls = pipeline_run(gaming=True)
    assert calls.get("gaming_inputs") is True and "find_clips" in calls


def test_a_job_with_a_pipeline_asks_the_plugin_instead(pipeline_run, tmp_path):
    out, calls = pipeline_run(pipeline={"id": "fixture-dev/echo"})
    assert out == [] and "find_clips" not in calls
    choice, kwargs = calls["plugin"]
    assert choice == {"id": "fixture-dev/echo"}
    assert kwargs["video"].video_id == "vid001" and kwargs["language"] == "en"
    assert [s.text for s in kwargs["segments"]] == ["hello there"]
    assert Path(kwargs["data_dir"]) == tmp_path / "data"


def test_a_gaming_layout_beside_a_pipeline_skips_gaming_scoring(pipeline_run):
    _out, calls = pipeline_run(gaming=True, pipeline={"id": "fixture-dev/echo"})
    assert "plugin" in calls and "gaming_inputs" not in calls and "find_clips" not in calls


# ---- remote rendering -------------------------------------------------------------


def test_a_pipeline_choice_never_travels_to_a_render_pc():
    from remote_render import protocol

    plain = {"clips": {"vertical": True, "captions": True}, "tracking": {}, "video": {}}
    with_plugin = {**plain, "clips": {**plain["clips"], "pipeline": {"id": "fixture-dev/echo",
                                                                     "settings": {"ranges": "1-9"}}}}
    assert protocol.render_config(with_plugin) == protocol.render_config(plain)
    assert "pipeline" in with_plugin["clips"]  # the job's own config is left as it was
    assert protocol.job_id("v", 1, 9, None, with_plugin) == protocol.job_id("v", 1, 9, None, plain)
