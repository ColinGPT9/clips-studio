"""The `pipeline` job option: a job that names a plugin pipeline.

Through the API it is checked like every other option (its shape, that the
plugin is installed and on, and the modes it can't share a job with). In
process_video it replaces only the detection step, and a job without it
reaches find_clips exactly as before.

The `rate` and `understand` options (plugins/steps.py) run after the
moments are found and before the titles are written; a job without them
never imports plugins.steps and hands its finder the same config as before.
"""

import json
import shutil
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
ECHO = ROOT / "tests" / "fixtures" / "plugins" / "echo"
STEPPER = ROOT / "tests" / "fixtures" / "plugins" / "stepper"
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


# ---- Rate & understand through the API ---------------------------------------------------

STEPPER_ID = "fixture-dev/stepper"
GRADER_ID = "example-dev/quarkbloom-grader"
LONGFORM_REFUSED = ("Rate & understand can't be combined with Longform: Longform picks and writes its clips "
                    "its own way. Turn one of them off.")


def _vod(n: int) -> str:
    """A different placeholder link for each job, so none joins an earlier one."""
    return f"https://www.twitch.tv/videos/{100000000 + n}"


def _grader(tmp_path, install, data_dir):
    """Installs the manifest fixture of a plugin that finds, understands and
    rates (Quarkbloom Arena, a made-up game), so it can be named as a job's
    pipeline and to rate or understand."""
    folder = tmp_path / "grader"
    folder.mkdir()
    shutil.copy(ROOT / "tests" / "fixtures" / "plugins" / "manifests" / "valid" / "finds-understands-rates.yaml",
                folder / "clipskitty.yaml")
    install(data_dir, folder)


def _refused(client, body, *, path="/jobs", method="post") -> str:
    r = getattr(client, method)(path, json=body)
    assert r.status_code == 400, (body, r.status_code, r.text)
    return r.json()["detail"]


def test_rate_and_understand_are_checked_like_pipeline(api):
    client, data_dir, install = api
    install(data_dir, STEPPER)
    r = client.post("/jobs", json={"url": _vod(1), "rate": {"id": STEPPER_ID, "version": "1.0.0",
                                                           "settings": {"scores": "*=+5"}},
                                   "understand": STEPPER_ID})
    assert r.status_code == 200, r.text
    payload = _payload(client, r.json()["job_id"])
    # A single choice or a bare id is a list of one; what is stored is the cleaned list.
    assert payload["rate"] == [{"id": STEPPER_ID, "version": "1.0.0", "settings": {"scores": "*=+5"}}]
    assert payload["understand"] == [{"id": STEPPER_ID}]
    r = client.post("/jobs", json={"url": _vod(2), "rate": [], "understand": None})
    assert r.status_code == 200 and not {"rate", "understand"} & set(_payload(client, r.json()["job_id"]))
    # Not installed, turned off, settings that don't fit: refused at once, in the pipeline's words.
    assert _refused(client, {"url": _vod(3), "rate": [{"id": STEPPER_ID, "settings": {"mode": "explode"}}]}) \
        .startswith("rate[0]: setting 'mode': 'explode' is not one of")
    assert "no setting called 'colour'" in _refused(
        client, {"url": _vod(3), "understand": [{"id": STEPPER_ID, "settings": {"colour": "red"}}]})
    install(data_dir, STEPPER, enabled=False)
    assert _refused(client, {"url": _vod(3), "understand": STEPPER_ID}) == (
        "understand[0]: the pipeline Stepper is turned off; turn it on in Marketplace › Installed first")
    # Neither a list, an object nor an id fails validation, as a pipeline that is a list does.
    assert client.post("/jobs", json={"url": _vod(3), "rate": 5}).status_code == 422


def test_a_finder_cant_be_named_to_rate_and_a_rater_cant_be_the_pipeline(api):
    client, data_dir, install = api
    install(data_dir, ECHO)
    install(data_dir, STEPPER)
    assert _refused(client, {"url": _vod(1), "rate": "fixture-dev/echo"}) == (
        "rate[0]: the pipeline Echo can't rate moments others found: its manifest needs moments in inputs "
        "and ratings in outputs")
    assert _refused(client, {"url": _vod(1), "understand": "fixture-dev/echo"}) == (
        "understand[0]: the pipeline Echo can't understand moments others found: its manifest needs moments "
        "in inputs and context in outputs")
    assert _refused(client, {"url": _vod(1), "pipeline": STEPPER_ID}) == (
        "pipeline: the pipeline Stepper doesn't find moments: it rates or understands moments others found. "
        "Choose it under Rate & understand instead")
    # Each where it belongs is fine.
    r = client.post("/jobs", json={"url": _vod(1), "pipeline": "fixture-dev/echo", "rate": STEPPER_ID})
    assert r.status_code == 200, r.text


def test_existing_pipeline_messages_are_unchanged(api):
    client, data_dir, install = api
    for pipeline, detail in (
        ({"id": "fixture-dev/echo"}, "pipeline: the pipeline fixture-dev/echo isn't installed"),
        ({"id": "fixture-dev/echo", "version": "9.9.9"}, "pipeline: the pipeline fixture-dev/echo 9.9.9 isn't installed"),
        ({"id": "Not An Id"}, "pipeline: pipeline.id must look like publisher/name (lower case, digits and hyphens)"),
        ({"id": "a/b", "colour": "red"}, "pipeline: pipeline has unknown fields: colour"),
        ({"id": "a/b", "version": "latest"}, "pipeline: pipeline.version must be a version like 1.2.0"),
        ({"id": "a/b", "settings": ["x"]}, "pipeline: pipeline.settings must be an object"),
        ({"id": "a/b", "settings": {"x": "y" * 16_001}}, "pipeline: pipeline.settings is too large"),
    ):
        # The pipeline is checked first, so a step beside it changes nothing in its message.
        assert _refused(client, {"url": URL, "pipeline": pipeline}) == detail
        assert _refused(client, {"url": URL, "pipeline": pipeline, "rate": STEPPER_ID}) == detail
    install(data_dir, ECHO, enabled=False)
    assert _refused(client, {"url": URL, "pipeline": "fixture-dev/echo"}) == (
        "pipeline: the pipeline Echo is turned off; turn it on in Marketplace › Installed first")
    install(data_dir, ECHO)
    install(data_dir, STEPPER)
    for other in ({"sport": {"name": "soccer"}}, {"gaming_scoring": True}, {"longform": {"mode": "highlights"}}):
        for steps in ({}, {"rate": STEPPER_ID}):
            assert _refused(client, {"url": URL, "pipeline": "fixture-dev/echo", **other, **steps}) == (
                "A plugin pipeline can't be combined with Sports, Gaming scoring or Longform: each picks the "
                "moments its own way. Turn one of them off.")


def test_step_messages_name_their_field(api):
    from plugins import steps

    client, data_dir, install = api
    install(data_dir, STEPPER)
    for body, detail in (
        ({"rate": [STEPPER_ID, "Not An Id"]},
         "rate[1].id must look like publisher/name (lower case, digits and hyphens)"),
        ({"rate": [{"id": "a/b", "x": 1}]}, "rate[0] has unknown fields: x"),
        ({"understand": ["a/b", "a/c", {"id": "a/d", "settings": {"x": "y" * 16_001}}]},
         "understand[2].settings is too large"),
        ({"understand": [{"id": "a/b", "version": "latest"}]}, "understand[0].version must be a version like 1.2.0"),
        ({"rate": ["fixture-dev/missing"]}, "rate[0]: the pipeline fixture-dev/missing isn't installed"),
        ({"rate": [STEPPER_ID, {"id": "fixture-dev/missing", "version": "2.0.0"}]},
         "rate[1]: the pipeline fixture-dev/missing 2.0.0 isn't installed"),
        ({"understand": [{"id": STEPPER_ID, "version": "9.9.9"}]},
         "understand[0]: the pipeline fixture-dev/stepper 9.9.9 isn't installed"),
    ):
        assert _refused(client, {"url": URL, **body}) == detail
    with pytest.raises(ValueError) as e:
        steps.clean("rate", 5)
    assert str(e.value) == 'rate must be a plugin or a list of plugins: [{"id": "publisher/name"}]'


def test_more_than_three_or_a_duplicate_is_refused(api):
    from server.api import JobIn, _process_options

    client, data_dir, install = api
    install(data_dir, STEPPER)
    assert _refused(client, {"url": URL, "rate": ["a/a", "a/b", "a/c", "a/d"]}) == (
        "rate: at most 3 plugins for one step")
    assert _refused(client, {"url": URL, "understand": [STEPPER_ID, {"id": STEPPER_ID, "settings": {"notes": "*=x"}}]}) \
        == "understand: fixture-dev/stepper is listed twice"
    # Three different ones are a fine shape (here without checking what is installed).
    payload = _process_options(JobIn(url=URL, rate=["a/a", "a/b", "a/c"], understand=["a/a"]))
    assert payload["rate"] == [{"id": "a/a"}, {"id": "a/b"}, {"id": "a/c"}] and payload["understand"] == [{"id": "a/a"}]


def test_naming_the_jobs_pipeline_again_is_refused(api, tmp_path):
    client, data_dir, install = api
    _grader(tmp_path, install, data_dir)
    for field in ("rate", "understand"):
        assert _refused(client, {"url": _vod(1), "pipeline": GRADER_ID, field: [GRADER_ID]}) == (
            f"{field}: {GRADER_ID} is this job's pipeline, so it already scores and describes the moments it finds")
    # Another version or other settings are still the same plugin.
    assert _refused(client, {"url": _vod(1), "pipeline": {"id": GRADER_ID, "version": "1.0.0"},
                             "rate": {"id": GRADER_ID, "settings": {}}}).startswith(f"rate: {GRADER_ID} is this job's")
    # A PATCH is judged on the payload it merges into, either way round.
    job_id = client.post("/jobs", json={"url": _vod(2), "rate": GRADER_ID}).json()["job_id"]
    assert _refused(client, {"pipeline": GRADER_ID}, path=f"/jobs/{job_id}", method="patch").startswith(
        f"rate: {GRADER_ID} is this job's pipeline")
    assert "pipeline" not in _payload(client, job_id)
    job_id = client.post("/jobs", json={"url": _vod(3), "pipeline": GRADER_ID}).json()["job_id"]
    assert _refused(client, {"understand": GRADER_ID}, path=f"/jobs/{job_id}", method="patch").startswith(
        f"understand: {GRADER_ID} is this job's pipeline")
    assert "understand" not in _payload(client, job_id)
    # Clearing the pipeline in the same PATCH makes room for it.
    r = client.patch(f"/jobs/{job_id}", json={"understand": GRADER_ID, "clear": ["pipeline"]})
    assert r.status_code == 200, r.text
    assert _payload(client, job_id)["understand"] == [{"id": GRADER_ID}]


def test_steps_go_with_sports_gaming_scoring_and_a_pipeline_but_not_longform(api):
    client, data_dir, install = api
    install(data_dir, ECHO)
    install(data_dir, STEPPER)
    steps = {"rate": [STEPPER_ID], "understand": [STEPPER_ID]}
    for n, other in enumerate(({"sport": {"name": "soccer"}}, {"gaming_scoring": True},
                               {"pipeline": "fixture-dev/echo"}, {"gaming": True}, {"gaming": True, "gaming_scoring": True},
                               {"vertical_live": True}, {"podcast": True},
                               {"focus": "quark bursts", "min_score": 70, "max_clips": 3}), 1):
        r = client.post("/jobs", json={"url": _vod(n), **other, **steps})
        assert r.status_code == 200 and r.json()["job_id"], (other, r.text)
        payload = _payload(client, r.json()["job_id"])
        assert payload["rate"] == payload["understand"] == [{"id": STEPPER_ID}], other
        assert client.delete(f"/jobs/{r.json()['job_id']}").status_code == 200  # the queue holds five
    for longform in ({"mode": "highlights"}, {"mode": "short_clips", "shorts": True}):
        for field in ("rate", "understand"):
            assert _refused(client, {"url": _vod(20), "longform": longform, field: STEPPER_ID}) == LONGFORM_REFUSED
    # Judged on the merged payload: a queued job with a rater can't gain Longform.
    job_id = client.post("/jobs", json={"url": _vod(21), "rate": STEPPER_ID}).json()["job_id"]
    assert _refused(client, {"longform": {"mode": "highlights"}}, path=f"/jobs/{job_id}", method="patch") \
        == LONGFORM_REFUSED
    r = client.patch(f"/jobs/{job_id}", json={"longform": {"mode": "highlights"}, "clear": ["rate"]})
    assert r.status_code == 200, r.text


def test_patch_adds_and_clears_rate(api):
    client, data_dir, install = api
    install(data_dir, STEPPER)
    job_id = client.post("/jobs", json={"url": URL}).json()["job_id"]
    assert client.patch(f"/jobs/{job_id}", json={"rate": STEPPER_ID}).status_code == 200
    assert _payload(client, job_id)["rate"] == [{"id": STEPPER_ID}]
    chosen = {"id": STEPPER_ID, "settings": {"notes": "*=This happens in the final round"}}
    assert client.patch(f"/jobs/{job_id}", json={"understand": [chosen], "max_clips": 3}).status_code == 200
    payload = _payload(client, job_id)
    assert payload["rate"] == [{"id": STEPPER_ID}] and payload["understand"] == [chosen]
    assert client.patch(f"/jobs/{job_id}", json={"clear": ["rate"]}).status_code == 200
    payload = _payload(client, job_id)
    assert "rate" not in payload and payload["understand"] == [chosen]
    assert client.patch(f"/jobs/{job_id}", json={"clear": ["understand"]}).status_code == 200
    assert not {"rate", "understand"} & set(_payload(client, job_id))
    # A bad one leaves the job as it was.
    assert _refused(client, {"rate": "fixture-dev/missing"}, path=f"/jobs/{job_id}", method="patch") == (
        "rate[0]: the pipeline fixture-dev/missing isn't installed")
    assert "rate" not in _payload(client, job_id)


def test_a_batch_row_with_bad_steps_is_skipped_as_bad_option(api):
    client, data_dir, install = api
    install(data_dir, STEPPER)
    body = client.post("/jobs/batch", json={"items": [
        {"url": _vod(1), "rate": STEPPER_ID},
        {"url": _vod(2), "understand": ["fixture-dev/missing"]},
        {"url": _vod(3), "rate": [STEPPER_ID, STEPPER_ID]}]}).json()
    assert [c["video_id"] for c in body["created"]] == ["tw_100000001"]
    assert [(s["reason"], s["detail"]) for s in body["skipped"]] == [
        ("bad_option", "understand[0]: the pipeline fixture-dev/missing isn't installed"),
        ("bad_option", "rate: fixture-dev/stepper is listed twice")]


def test_the_worker_copies_steps_only_when_given(tmp_path, monkeypatch):
    import threading
    import time
    import types

    from core import progress, queue
    from core.state import StateDB
    from main import BUNDLED_CONFIG, load_config
    from server import jobs

    config = load_config(BUNDLED_CONFIG)
    config["paths"]["data_dir"] = str(tmp_path)
    worker = jobs.Worker(config)
    worker.prefetch = types.SimpleNamespace(wait_for=lambda _vid: None, maybe_start=lambda _db: None)
    seen: list = []

    def process_video(url, cfg, db, force=False):
        seen.append((url, cfg["clips"]))

    monkeypatch.setitem(sys.modules, "core.pipeline", types.SimpleNamespace(process_video=process_video))
    monkeypatch.setattr(progress, "_handler", progress._handler)  # put back after the run
    rate = [{"id": STEPPER_ID, "settings": {"scores": "*=+5"}}, {"id": "fixture-dev/other"}]
    understand = [{"id": STEPPER_ID}]
    db = StateDB(tmp_path / "state.db")
    db.add_job("process", json.dumps({"url": "local:plain"}), video_id="plain")
    db.add_job("process", json.dumps({"url": "local:steps", "rate": rate, "understand": understand}),
               video_id="steps")
    db.add_job("process", json.dumps({"url": "local:rater", "rate": rate}), video_id="rater")
    # One choice not in a list, as a payload written by hand could hold it.
    db.add_job("process", json.dumps({"url": "local:one", "understand": understand[0]}), video_id="one")
    queue.set_paused(db, False)
    db.close()
    thread = threading.Thread(target=worker.run, daemon=True)
    thread.start()
    deadline = time.time() + 30
    while len(seen) < 4 and time.time() < deadline:
        time.sleep(0.05)
    worker.stop()
    thread.join(timeout=10)
    got = dict(seen)
    assert set(got) == {"local:plain", "local:steps", "local:rater", "local:one"}
    assert not {"rate", "understand"} & set(got["local:plain"])
    assert got["local:steps"]["rate"] == rate and got["local:steps"]["understand"] == understand
    assert got["local:rater"]["rate"] == rate and "understand" not in got["local:rater"]
    assert got["local:one"]["understand"] == understand and "rate" not in got["local:one"]
    assert not {"rate", "understand"} & set(worker.config["clips"])  # each job gets its own copy


# ---- in process_video ------------------------------------------------------------


class _Stop(Exception):
    """Stops a run where the test has seen what it needs."""


@pytest.fixture
def pipeline_run(monkeypatch, tmp_path, db):
    """process_video with download, transcription and analysis stubbed out,
    recording which detector it called and with what, the config it was
    given (`config`) and the order things ran in (`order`). Given `found`,
    the detector finds those moments and the run stops where the titles
    are written (`titles`: the candidates they were asked for); without it,
    the run stops at the detector. `run.calls` is the record while it runs."""
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
    found: list = []

    def find_clips(*args, **kwargs):
        calls["find_clips"] = (args, kwargs)
        calls["order"].append("find_clips")
        if not found:
            raise _Stop
        return list(found), []

    def plugin_find_clips(choice, **kwargs):
        calls["plugin"] = (choice, kwargs)
        calls["order"].append("plugin")
        return list(found)

    def generate_metadata_batch(candidates, *_a, **_k):
        calls["titles"] = list(candidates)
        calls["order"].append("titles")
        raise _Stop

    def gaming_inputs(*_a, **_k):
        calls["gaming_inputs"] = True
        return None, None, None

    monkeypatch.setattr(pipeline, "find_clips", find_clips)
    monkeypatch.setattr(pipeline, "clip_direction", lambda *_a, **_k: None)
    monkeypatch.setattr(pipeline, "_gaming_scoring_inputs", gaming_inputs)
    monkeypatch.setattr("plugins.runner.find_clips", plugin_find_clips)
    monkeypatch.setattr(pipeline, "generate_metadata_batch", generate_metadata_batch)

    def run(found_moments=None, **clips):
        config = load_config(BUNDLED_CONFIG)
        config["paths"]["data_dir"] = str(tmp_path / "data")
        config["clips"].update({"captions": False, **clips})
        calls.clear()
        calls.update(config=config, order=[])
        found[:] = found_moments or []
        try:
            out = pipeline.process_video("local:stream", config, db, force=True)
        except _Stop:
            out = None
        return out, dict(calls)

    run.calls = calls
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


# ---- Rate & understand in process_video -----------------------------------------------


def _moments():
    from core.models import ClipCandidate

    return [ClipCandidate(start=10.0, end=30.0, score=80, hook="a quark burst"),
            ClipCandidate(start=100.0, end=120.0, score=70, hook="round one")]


def test_a_job_without_steps_never_imports_plugins_steps(pipeline_run, monkeypatch):
    import plugins

    monkeypatch.setitem(sys.modules, "plugins.steps", None)  # importing it now fails
    monkeypatch.delattr(plugins, "steps", raising=False)
    for clips in ({}, {"gaming": True}, {"sport": {"name": "soccer"}}, {"pipeline": {"id": "fixture-dev/echo"}}):
        _out, calls = pipeline_run(_moments(), **clips)
        assert [c.start for c in calls.get("titles", [])] == [10.0, 100.0], clips


def test_no_steps_hands_find_clips_the_same_config_object(pipeline_run):
    _out, calls = pipeline_run()
    assert calls["find_clips"][0][3] is calls["config"]
    _out, calls = pipeline_run(pipeline={"id": "fixture-dev/echo"})
    assert calls["plugin"][1]["config"] is calls["config"]
    # Understanders alone, or a rater without a clip limit, change nothing either.
    for steps in ({"understand": [{"id": "fixture-dev/stepper"}], "max_clips_per_video": 3},
                  {"rate": [{"id": "fixture-dev/stepper"}], "max_clips_per_video": 0}):
        _out, calls = pipeline_run(**steps)
        assert calls["find_clips"][0][3] is calls["config"], steps


def test_a_rater_and_a_limit_give_the_finder_three_times_the_limit(pipeline_run):
    _out, calls = pipeline_run(rate=[{"id": "fixture-dev/stepper"}], max_clips_per_video=3)
    found_with = calls["find_clips"][0][3]
    assert found_with is not calls["config"] and found_with["clips"]["max_clips_per_video"] == 9
    assert calls["config"]["clips"]["max_clips_per_video"] == 3  # the job's own limit, for the cut after rating
    _out, calls = pipeline_run(pipeline={"id": "fixture-dev/echo"}, rate=[{"id": "fixture-dev/stepper"}],
                               max_clips_per_video=3)
    assert calls["plugin"][1]["config"]["clips"]["max_clips_per_video"] == 9


def test_steps_run_after_finding_and_before_titles(pipeline_run, monkeypatch, db, tmp_path):
    from core.models import Rejection
    from plugins import steps

    report = [{"plugin": "fixture-dev/stepper", "version": "1.0.0", "name": "Stepper", "steps": ["rate"],
               "ok": True, "given": 2, "noted": 0, "rated": 2, "set_aside": 1}]
    seen = {}

    def after_finding(candidates, rejections, **kwargs):
        pipeline_run.calls["order"].append("steps")
        seen.update(candidates=list(candidates), kwargs=kwargs)
        candidates[1].score = 90
        return [candidates[1]], [*rejections, Rejection(candidates[0], "below_min_score")], report

    monkeypatch.setattr(steps, "after_finding", after_finding)
    found = _moments()
    _out, calls = pipeline_run(found, rate=[{"id": "fixture-dev/stepper"}])
    assert calls["order"] == ["find_clips", "steps", "titles"]
    assert seen["candidates"] == found and calls["titles"] == [found[1]]
    kwargs = seen["kwargs"]
    assert kwargs["config"] is calls["config"] and kwargs["video"].video_id == "vid001"
    assert kwargs["language"] == "en" and [s.text for s in kwargs["segments"]] == ["hello there"]
    assert Path(kwargs["data_dir"]) == tmp_path / "data"
    outcome = db.get_outcome("vid001")
    assert outcome["steps"] == report and outcome["clips"] == 1
    assert outcome["rejected"] == {"below_min_score": 1}
    logged = db.conn.execute("SELECT start_s, reason FROM rejections WHERE video_id = 'vid001'").fetchall()
    assert [tuple(r) for r in logged] == [(10.0, "below_min_score")]


# ---- remote rendering -------------------------------------------------------------


def test_a_pipeline_choice_never_travels_to_a_render_pc():
    from remote_render import protocol

    plain = {"clips": {"vertical": True, "captions": True}, "tracking": {}, "video": {}}
    with_plugin = {**plain, "clips": {**plain["clips"], "pipeline": {"id": "fixture-dev/echo",
                                                                     "settings": {"ranges": "1-9"}}}}
    assert protocol.render_config(with_plugin) == protocol.render_config(plain)
    assert "pipeline" in with_plugin["clips"]  # the job's own config is left as it was
    assert protocol.job_id("v", 1, 9, None, with_plugin) == protocol.job_id("v", 1, 9, None, plain)


def test_steps_never_travel_to_a_render_pc():
    from remote_render import protocol

    plain = {"clips": {"vertical": True, "captions": True}, "tracking": {}, "video": {}}
    with_steps = {**plain, "clips": {**plain["clips"],
                                     "rate": [{"id": "fixture-dev/stepper", "settings": {"scores": "*=+5"}}],
                                     "understand": [{"id": "fixture-dev/stepper"}]}}
    assert protocol.render_config(with_steps) == protocol.render_config(plain)
    assert "rate" in with_steps["clips"] and "understand" in with_steps["clips"]  # left as it was
    assert protocol.job_id("v", 1, 9, None, with_steps) == protocol.job_id("v", 1, 9, None, plain)


# ---- a forced re-run of a rated clip ---------------------------------------------------


def _video_row(db):
    db.conn.execute("INSERT INTO videos (video_id, title, status, created_at, updated_at)"
                    " VALUES ('v', 'A Quarkbloom Arena match', 'done', 'x', 'x')")
    db.conn.commit()


def _register(db, tmp_path, candidate, title):
    from analysis.metadata import ClipMetadata
    from core.pipeline import _register_clip

    _register_clip(db, "v", candidate, tmp_path / f"{title}.mp4",
                   ClipMetadata(title=title, description="", hashtags=[]), "")
    return db.conn.execute("SELECT score, title, scores, path FROM clips WHERE video_id = 'v' AND start_s = ?",
                           (round(candidate.start, 2),)).fetchone()


def _rated(start, found, score):
    from core.models import ClipCandidate

    return ClipCandidate(start=start, end=start + 30, score=score, hook="h", subscores={
        "text": found, "found_score": found, "plugin_ratings": [
            {"plugin": "fixture-dev/stepper", "version": "1.0.0", "name": "Stepper", "score": score,
             "reason": "a big play"}]})


def test_a_forced_rerun_updates_a_rated_clips_score_but_keeps_its_title(db, tmp_path):
    pytest.importorskip("PIL")  # video.post_style draws title cards
    from core.models import ClipCandidate

    _video_row(db)
    _register(db, tmp_path, ClipCandidate(start=10.0, end=40.0, score=70, hook="h", subscores={"text": 70}), "first")
    row = _register(db, tmp_path, _rated(10.0, 70, 90), "second")
    assert (row["score"], row["title"]) == (90, "first")
    assert json.loads(row["scores"])["plugin_ratings"][0]["score"] == 90 and row["path"].endswith("second.mp4")
    # A clip never rated, before or now, keeps its score column as it always did.
    _register(db, tmp_path, ClipCandidate(start=100.0, end=130.0, score=60, hook="h"), "plain")
    row = _register(db, tmp_path, ClipCandidate(start=100.0, end=130.0, score=75, hook="h"), "plain again")
    assert (row["score"], row["title"]) == (60, "plain")


@pytest.mark.parametrize("rater", ["turned off", "removed", "failed"])
def test_a_forced_rerun_without_the_rater_puts_back_the_unrated_score(db, tmp_path, install_plugin, rater):
    pytest.importorskip("PIL")
    pytest.importorskip("yaml")
    from core.models import ClipCandidate, DownloadedVideo, Segment
    from plugins import steps

    _video_row(db)
    row = _register(db, tmp_path, _rated(10.0, 70, 90), "first")
    assert row["score"] == 90

    data_dir = tmp_path / "data"
    if rater != "removed":
        install_plugin(data_dir, STEPPER, enabled=rater != "turned off")
    choice = {"id": "fixture-dev/stepper", "settings": {"scores": "*=90", **({"mode": "fail"} if rater == "failed"
                                                                            else {})}}
    config = {"clips": {"min_score": 55, "min_duration": 10, "max_duration": 60, "max_clips_per_video": 0,
                        "rate": [choice]}, "llm": {"backend": "ollama/local"}}
    source = tmp_path / "match.mp4"
    source.write_bytes(b"not really a video")
    video = DownloadedVideo(video_id="v", title="A Quarkbloom Arena match", path=source, duration=600.0)
    found = [ClipCandidate(start=10.0, end=40.0, score=70, hook="h", subscores={"text": 70})]
    kept, _rejections, report = steps.after_finding(found, [], video=video, segments=[Segment(0.0, 5.0, "go")],
                                                    language="en", config=config, data_dir=data_dir)
    assert report[0]["ok"] is False and "plugin_ratings" not in kept[0].subscores
    row = _register(db, tmp_path, kept[0], "second")
    assert (row["score"], row["title"]) == (70, "first")
    assert "plugin_ratings" not in json.loads(row["scores"])
