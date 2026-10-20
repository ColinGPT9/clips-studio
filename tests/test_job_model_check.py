"""A job on a local AI with no model at all ends before anything is downloaded.

It used to download the video, transcribe it, and only then fail, with "404
Client Error: Not Found for url: http://127.0.0.1:11435/api/generate" (#149).
The worker now asks Ollama what it has first. Everything that worked before
still runs: a missing choice with another model installed (the pipeline
switches to it), Ollama not answering (reported where it always was), a cloud
model, and a plugin pipeline, whose AI only writes titles.
"""

import json
import sys
import threading
import time
import types

import pytest
import requests

jobs = pytest.importorskip("server.jobs")  # CI installs only the light dependencies

from llm.providers.base import LLMError  # noqa: E402

HOST = "http://127.0.0.1:11435"
MISSING = ("The AI model gemma:7b isn't downloaded. "
           "Download it on the Models page, or choose a model you have.")


def _ollama_has(monkeypatch, names):
    """Ollama lists exactly `names`, or isn't answering when None. Returns
    every (host, timeout) it was asked with."""
    asked = []

    def installed_models(host, timeout=15):
        asked.append((host, timeout))
        if names is None:
            raise requests.ConnectionError("nothing listening")
        return [{"name": n, "size_gb": 3.0} for n in names]

    monkeypatch.setattr("llm.manager.installed_models", installed_models)
    return asked


def _check(backend="ollama/gemma:7b", **payload):
    jobs.Worker._check_local_model({"backend": backend, "ollama_host": HOST}, payload)


def test_no_model_at_all_ends_the_job_in_plain_words(monkeypatch):
    asked = _ollama_has(monkeypatch, [])
    with pytest.raises(LLMError) as e:
        _check()
    assert e.value.kind == "model_unavailable"
    assert e.value.message == MISSING
    # Asked once, of the configured Ollama, without waiting long on it.
    assert len(asked) == 1 and asked[0][0] == HOST and asked[0][1] <= 5


@pytest.mark.parametrize("installed", [["gemma:7b"], ["gemma3:4b"], ["qwen3:8b"]])
def test_any_installed_model_lets_the_job_run(monkeypatch, installed):
    """The configured one, or another the pipeline switches to: either way
    the job runs as it did before."""
    _ollama_has(monkeypatch, installed)
    _check()


def test_ollama_not_answering_is_left_where_it_was_reported(monkeypatch):
    _ollama_has(monkeypatch, None)
    _check()


def test_a_cloud_model_is_never_asked_about(monkeypatch):
    asked = _ollama_has(monkeypatch, [])
    _check("openrouter/vendor/some-model")
    assert asked == []


def test_a_plugin_pipeline_runs_without_a_model_as_it_always_did(monkeypatch):
    """Its moments come from the plugin, and titles fall back without the AI.
    Its 16:9 pass finds moments with the AI, so that one is checked."""
    asked = _ollama_has(monkeypatch, [])
    _check(pipeline={"id": "fixture-dev/finder"})
    assert asked == []
    with pytest.raises(LLMError):
        _check(pipeline={"id": "fixture-dev/finder"}, longform={"shorts": False})


def test_the_job_fails_before_the_download_with_the_message(tmp_path, monkeypatch):
    from core import progress, queue
    from core.state import StateDB
    from main import BUNDLED_CONFIG, load_config

    _ollama_has(monkeypatch, [])
    config = load_config(BUNDLED_CONFIG)
    config["paths"]["data_dir"] = str(tmp_path)
    config["llm"].update(backend="ollama/gemma:7b", ollama_host=HOST)
    worker = jobs.Worker(config)
    worker.prefetch = types.SimpleNamespace(wait_for=lambda _vid: None, maybe_start=lambda _db: None)
    started: list = []
    monkeypatch.setitem(sys.modules, "core.pipeline", types.SimpleNamespace(
        process_video=lambda url, cfg, db, force=False: started.append(url)))
    monkeypatch.setattr(progress, "_handler", progress._handler)  # put back after the run
    db = StateDB(tmp_path / "state.db")
    try:
        job_id = db.add_job("process", json.dumps({"url": "local:plain"}), video_id="plain")
        queue.set_paused(db, False)
        thread = threading.Thread(target=worker.run, daemon=True)
        thread.start()
        deadline = time.time() + 30
        while db.get_job(job_id)["status"] in ("queued", "running") and time.time() < deadline:
            time.sleep(0.05)
        worker.stop()
        thread.join(timeout=10)
        job = db.get_job(job_id)
    finally:
        db.close()
    assert started == []  # nothing downloaded, nothing transcribed
    assert job["status"] == "failed"
    assert job["error"] == MISSING
