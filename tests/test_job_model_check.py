"""A job on a local AI with no model at all ends before anything is downloaded.

It used to download the video, transcribe it, and only then fail, with "404
Client Error: Not Found for url: http://127.0.0.1:11435/api/generate" (#149).
The worker now asks Ollama what it has first. Everything that worked before
still runs: a missing choice with another model installed (the pipeline
switches to it), Ollama not answering (reported where it always was), a cloud
model, a plugin pipeline, whose AI only writes titles, a model still
downloading, and a video whose Shorts were made before.
"""

import json
import sys
import threading
import time
import types

import pytest
import requests

jobs = pytest.importorskip("server.jobs")  # CI installs only the light dependencies

from llm import pulls  # noqa: E402
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


class _Videos:
    """The one thing the check reads from the database: whether this video's
    Shorts were made before."""

    def __init__(self, made: bool):
        self.made = made

    def shorts_made(self, _vid):
        return self.made


def _check(backend="ollama/gemma:7b", made=False, **payload):
    jobs.Worker._check_local_model(_Videos(made), "plain", {"backend": backend, "ollama_host": HOST}, payload)


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


def test_a_model_still_downloading_lets_the_job_go_ahead(monkeypatch):
    """Setup tells a new user to carry on while the recommended model
    downloads, and Ollama lists nothing until it's done. The first video
    queued meanwhile used to run, and find the model by the time it needed
    it; it must not fail at once naming a model nobody is downloading."""
    _ollama_has(monkeypatch, [])
    pulls.started("gemma3:4b")
    try:
        _check()
    finally:
        pulls.finished("gemma3:4b")
    with pytest.raises(LLMError):
        _check()  # the download over, and still nothing to run on


def test_shorts_made_before_need_no_model(monkeypatch):
    """process_video ends such a job as done before the AI is needed. Asked
    again (force), or for its 16:9 version, the AI is needed after all."""
    asked = _ollama_has(monkeypatch, [])
    _check(made=True)
    assert asked == []
    with pytest.raises(LLMError):
        _check(made=True, force=True)
    with pytest.raises(LLMError):
        _check(made=True, longform={"shorts": False})


def _run_worker(tmp_path, monkeypatch, videos, installed, made=()):
    """Queue a process job for each of `videos` and run the worker until none
    is left, with Ollama listing `installed` and the Shorts of `made` already
    made. Returns the finished jobs, the videos process_video was started on,
    and every call to the prefetcher, in order."""
    # The worker's pipeline imports numpy, which CI's light install doesn't have.
    pytest.importorskip("numpy")
    from core import progress, queue
    from core.state import StateDB
    from main import BUNDLED_CONFIG, load_config

    _ollama_has(monkeypatch, installed)
    config = load_config(BUNDLED_CONFIG)
    config["paths"]["data_dir"] = str(tmp_path)
    config["llm"].update(backend="ollama/gemma:7b", ollama_host=HOST)
    worker = jobs.Worker(config)
    fetched: list = []
    worker.prefetch = types.SimpleNamespace(
        wait_for=lambda vid: fetched.append(("wait_for", vid)),
        maybe_start=lambda _db: fetched.append(("maybe_start",)))
    started: list = []
    monkeypatch.setitem(sys.modules, "core.pipeline", types.SimpleNamespace(
        process_video=lambda url, cfg, db, force=False: started.append(url) or []))
    monkeypatch.setattr(progress, "_handler", progress._handler)  # put back after the run
    db = StateDB(tmp_path / "state.db")
    try:
        for vid in made:
            db.upsert_video(vid, title="A video")
            db.set_video_status(vid, "done")
            db.set_outcome(vid, {"candidates": 0})
        ids = [db.add_job("process", json.dumps({"url": f"local:{vid}"}), video_id=vid) for vid in videos]
        queue.set_paused(db, False)
        thread = threading.Thread(target=worker.run, daemon=True)
        thread.start()
        deadline = time.time() + 30
        while (any(db.get_job(i)["status"] in ("queued", "running") for i in ids)
               and time.time() < deadline):
            time.sleep(0.05)
        worker.stop()
        thread.join(timeout=10)
        finished = [db.get_job(i) for i in ids]
    finally:
        db.close()
    return finished, started, fetched


def test_the_job_fails_before_the_download_with_the_message(tmp_path, monkeypatch):
    (job,), started, _fetched = _run_worker(tmp_path, monkeypatch, ["plain"], [])
    assert started == []  # nothing downloaded, nothing transcribed
    assert job["status"] == "failed"
    assert job["error"] == MISSING


def test_a_failed_check_never_starts_the_next_download(tmp_path, monkeypatch):
    """The next queued video's download used to start before the check, so
    the next job waited out the whole download only to fail the same way: a
    batch with no model downloaded every video in it."""
    finished, started, fetched = _run_worker(tmp_path, monkeypatch, ["one", "two"], [])
    assert [j["status"] for j in finished] == ["failed", "failed"]
    assert all(j["error"] == MISSING for j in finished)
    assert started == []
    assert fetched == []  # not waited on, and the next one never started


def test_a_job_that_passes_still_starts_the_next_download(tmp_path, monkeypatch):
    """With a model, the prefetch runs as it always did: this video's own is
    settled first, then the next one's starts beside this job's work."""
    finished, started, fetched = _run_worker(tmp_path, monkeypatch, ["one", "two"], ["gemma:7b"])
    assert [j["status"] for j in finished] == ["done", "done"]
    assert started == ["local:one", "local:two"]
    assert fetched == [("wait_for", "one"), ("maybe_start",), ("wait_for", "two"), ("maybe_start",)]


def test_a_video_made_before_still_finishes_as_done_without_a_model(tmp_path, monkeypatch):
    """Queued again without force, it ends at process_video's "Already
    processed" as it did before the check, though no model is installed."""
    (job,), started, _fetched = _run_worker(tmp_path, monkeypatch, ["plain"], [], made=["plain"])
    assert job["status"] == "done"
    assert started == ["local:plain"]


def test_an_edited_stream_needs_no_model(monkeypatch):
    """Edited Stream cuts silence and AFK stretches with a fixed title: it
    finished with no model installed before the check, and still does."""
    _ollama_has(monkeypatch, [])
    _check(longform={"mode": "edited_stream"})
    # Its "also make 9:16 Shorts" does need one ...
    with pytest.raises(LLMError):
        _check(longform={"mode": "edited_stream", "shorts": True})
    # ... unless those Shorts were made before and aren't asked for again.
    _check(made=True, longform={"mode": "edited_stream", "shorts": True})
    with pytest.raises(LLMError):
        _check(made=True, force=True, longform={"mode": "edited_stream", "shorts": True})
