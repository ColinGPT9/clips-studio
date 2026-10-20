"""A model download counts as done only when it is (#148, #150).

Ollama ends a pull that worked with {"status": "success"}, and the model then
shows up in /api/tags. Before this, a stream that stopped short (Ollama quit
while it checked the download) was announced as done, and setup could then
select a model that wasn't there, so every job failed with a 404. Each
download's state is also kept for GET /models/pulls, so a window that missed
the last WebSocket event can still find out how it ended, and llm.pulls says
which are running, for a job that needs the model being fetched.
"""

import json
import shutil
import threading
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
TAG = "gemma3:4b"

PROGRESS = [
    {"status": "pulling manifest"},
    {"status": "pulling 0123abcd", "digest": "sha256:0123abcd", "total": 3_300_000_000, "completed": 1_000_000_000},
    {"status": "pulling 0123abcd", "digest": "sha256:0123abcd", "total": 3_300_000_000, "completed": 3_300_000_000},
    {"status": "verifying sha256 digest"},
    {"status": "writing manifest"},
]
FINISHED = [*PROGRESS, {"status": "success"}]


class Stream:
    """What requests.post(..., stream=True) gives back: Ollama's lines, and
    then, if `cut` is given, the error requests raises when the stream is cut
    off instead of ending."""

    def __init__(self, lines, gate=None, cut=None):
        self.lines = lines
        self.gate = gate
        self.cut = cut

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def iter_lines(self):
        for i, line in enumerate(self.lines):
            if self.gate is not None and i == 2:
                self.gate.wait(5)  # hold the download part-way, as a real one is
            yield json.dumps(line).encode()
        if self.cut is not None:
            raise self.cut


class Tags:
    def __init__(self, names):
        self.names = names

    def raise_for_status(self):
        pass

    def json(self):
        return {"models": [{"name": n, "size": 3_300_000_000} for n in self.names]}


class Ollama:
    """Stands in for Ollama: the lines a pull streams and what it then lists."""

    def __init__(self, lines, listed, gate=None, cut=None):
        self.lines = lines
        self.listed = listed
        self.gate = gate
        self.cut = cut
        self.pulls = []

    def post(self, url, json=None, **kw):
        assert url.endswith("/api/pull"), url
        self.pulls.append(json["model"])
        if self.lines is None:
            raise self.cut  # never got as far as a stream
        return Stream(self.lines, self.gate, self.cut)

    def get(self, url, **kw):
        assert url.endswith("/api/tags"), url
        return Tags(self.listed)


@pytest.fixture
def api(tmp_path, monkeypatch):
    pytest.importorskip("fastapi")
    pytest.importorskip("httpx")
    pytest.importorskip("yaml")
    from fastapi.testclient import TestClient

    from main import BUNDLED_CONFIG, load_config
    from server import api as server_api
    from server.api import create_app

    events = []
    monkeypatch.setattr(server_api.broadcaster, "publish", events.append)
    settings = tmp_path / "settings.yaml"
    shutil.copy(ROOT / "config" / "settings.yaml", settings)
    config = load_config(BUNDLED_CONFIG)
    config["paths"]["data_dir"] = str(tmp_path / "data")
    config["llm"]["backend"] = "ollama/gemma:7b"
    app = create_app(config, settings)
    # No `with`: startup never runs, so no worker thread starts.
    client = TestClient(app, base_url="http://127.0.0.1")

    def ollama(lines, listed, gate=None, cut=None):
        import requests

        fake = Ollama(lines, listed, gate, cut)
        monkeypatch.setattr(requests, "post", fake.post)
        monkeypatch.setattr(requests, "get", fake.get)
        return fake

    return client, config, settings, events, ollama


def _pull(client, tag=TAG):
    return client.post("/models/pull", json={"tag": tag}).json()


def _finished(client, tag=TAG):
    """The download's state once it has ended, as GET /models/pulls has it."""
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        state = next((p for p in client.get("/models/pulls").json()["pulls"] if p["tag"] == tag), None)
        if state and state["done"]:
            return state
        time.sleep(0.01)
    raise AssertionError(f"{tag} never finished")


def _model_line(settings: Path) -> str:
    return next(line for line in settings.read_text(encoding="utf-8").splitlines() if line.startswith("model:"))


def test_a_finished_download_is_done_and_selected_when_nothing_else_runs(api):
    client, config, settings, events, ollama = api
    ollama(FINISHED, listed=[TAG])
    assert _pull(client) == {"started": TAG}
    state = _finished(client)
    assert state["status"] == "done" and state["error"] is None
    assert events[-1] == {"type": "model_pull", "tag": TAG, "status": "done"}
    # gemma:7b, the one selected, isn't installed, so the download is the choice.
    assert config["llm"]["backend"] == f"ollama/{TAG}"
    assert _model_line(settings).split()[1] == TAG


def test_a_finished_download_leaves_a_usable_choice_alone(api):
    client, config, settings, events, ollama = api
    before = _model_line(settings)
    ollama(FINISHED, listed=["gemma:7b", TAG])
    _pull(client)
    assert _finished(client)["status"] == "done"
    assert config["llm"]["backend"] == "ollama/gemma:7b"
    assert _model_line(settings) == before


def test_a_stream_that_stops_before_success_is_an_error(api):
    # The stream ends cleanly on the step it was on, with every byte counted.
    client, config, settings, events, ollama = api
    before = settings.read_text(encoding="utf-8")
    ollama(PROGRESS[:4], listed=[])
    _pull(client)
    state = _finished(client)
    assert state["status"] == "error"
    assert "didn't finish" in state["error"] and "verifying sha256 digest" in state["error"]
    assert events[-1]["status"] == "error" and events[-1]["error"] == state["error"]
    assert not any(e["status"] == "done" for e in events)
    assert config["llm"]["backend"] == "ollama/gemma:7b"
    assert settings.read_text(encoding="utf-8") == before


def test_a_stream_cut_off_part_way_says_to_try_again(api):
    # Ollama quit or crashed while it checked the download, or the connection
    # dropped: requests raises from iter_lines instead of the stream ending.
    import requests

    client, config, settings, events, ollama = api
    before = settings.read_text(encoding="utf-8")
    ollama(PROGRESS[:4], listed=[], cut=requests.exceptions.ChunkedEncodingError("Response ended prematurely"))
    _pull(client)
    state = _finished(client)
    assert state["status"] == "error"
    assert state["error"] == (
        "The download didn't finish (last step: verifying sha256 digest). Try again; it carries on where it stopped."
    )
    assert events[-1]["status"] == "error" and events[-1]["error"] == state["error"]
    assert not any(e["status"] == "done" for e in events)
    assert config["llm"]["backend"] == "ollama/gemma:7b"
    assert settings.read_text(encoding="utf-8") == before


def test_ollama_not_answering_at_all_says_so(api):
    import requests

    client, config, settings, events, ollama = api
    ollama(None, listed=[], cut=requests.exceptions.ConnectionError("connection refused"))
    _pull(client)
    state = _finished(client)
    assert state["status"] == "error"
    assert "(last step: connecting to Ollama)" in state["error"] and "carries on where it stopped" in state["error"]


def test_success_with_the_model_missing_from_ollama_is_an_error(api):
    client, config, settings, events, ollama = api
    before = settings.read_text(encoding="utf-8")
    ollama(FINISHED, listed=["llama3:8b"])
    _pull(client)
    state = _finished(client)
    assert state["status"] == "error" and TAG in state["error"]
    assert not any(e["status"] == "done" for e in events)
    assert config["llm"]["backend"] == "ollama/gemma:7b"
    assert settings.read_text(encoding="utf-8") == before


def test_an_error_line_from_ollama_is_an_error(api):
    client, config, settings, events, ollama = api
    ollama([{"status": "pulling manifest"}, {"error": "pull model manifest: file does not exist"}], listed=[])
    _pull(client, "gemma3:nosuchsize")
    state = _finished(client, "gemma3:nosuchsize")
    assert state["status"] == "error" and state["error"] == "pull model manifest: file does not exist"
    assert events[-1] == {
        "type": "model_pull", "tag": "gemma3:nosuchsize", "status": "error",
        "error": "pull model manifest: file does not exist",
    }
    assert config["llm"]["backend"] == "ollama/gemma:7b"


def test_a_tag_ollama_lists_in_full_counts_as_installed(api):
    client, config, settings, events, ollama = api
    tag = "hf.co/someone/some-model-GGUF:Q4_K_M"
    ollama(FINISHED, listed=["gemma:7b", tag])
    _pull(client, tag)
    assert _finished(client, tag)["status"] == "done"


def test_the_state_of_a_running_download_can_be_read_back(api):
    client, config, settings, events, ollama = api
    gate = threading.Event()
    ollama(FINISHED, listed=[TAG], gate=gate)
    try:
        _pull(client)
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            pulls = client.get("/models/pulls").json()["pulls"]
            if pulls and pulls[0]["completed"]:
                break
            time.sleep(0.01)
        assert pulls == [{
            "tag": TAG, "status": "pulling 0123abcd", "completed": 1_000_000_000,
            "total": 3_300_000_000, "error": None, "done": False,
        }]
    finally:
        gate.set()
    assert _finished(client) == {
        "tag": TAG, "status": "done", "completed": None, "total": None, "error": None, "done": True,
    }


def _until(condition, what):
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        if condition():
            return
        time.sleep(0.01)
    raise AssertionError(what)


@pytest.mark.parametrize("lines", [FINISHED, PROGRESS[:4]], ids=["success", "error"])
def test_a_job_can_tell_a_download_is_running(api, lines):
    # The job's model check asks llm.pulls, so a job queued while its model
    # downloads isn't failed for a model that is minutes away.
    from llm import pulls

    client, config, settings, events, ollama = api
    gate = threading.Event()
    ollama(lines, listed=[TAG], gate=gate)
    try:
        _pull(client)
        # From the moment the POST returns, not from when the thread gets going.
        assert TAG in pulls.running()
    finally:
        gate.set()
    _finished(client)
    _until(lambda: TAG not in pulls.running(), f"{TAG} still counted as downloading after it ended")


def test_asking_again_while_it_downloads_starts_nothing(api):
    client, config, settings, events, ollama = api
    gate = threading.Event()
    fake = ollama(FINISHED, listed=[TAG], gate=gate)
    try:
        assert _pull(client) == {"started": TAG}
        assert _pull(client) == {"started": TAG, "already": True}
    finally:
        gate.set()
    assert _finished(client)["status"] == "done"
    assert fake.pulls == [TAG]
    # Once it has ended, asking again is a new download (a retry after a failure).
    assert _pull(client) == {"started": TAG}
    _finished(client)
    assert fake.pulls == [TAG, TAG]


# ---- what the window shows (ui/src/renderer/src/lib/modelPull.ts, under Node) ----------


def _node() -> str:
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node isn't available")
    return node


MODEL_PULL = ROOT / "ui" / "src" / "renderer" / "src" / "lib" / "modelPull.ts"
STALE_PARTIALS = ROOT / "ui" / "src" / "main" / "stalePartials.ts"
# What the bundled Ollama writes for each model (stalePartials.ts reads only this).
MANIFEST_V2 = "application/vnd.docker.distribution.manifest.v2+json"


def _ui(tmp_path, body: str, lib: Path = MODEL_PULL):
    """Run `body` (JavaScript, `m` is the module) and return what it logs as JSON."""
    import subprocess

    # Copied out of ui/ so ui/package.json doesn't decide how Node loads it.
    (tmp_path / lib.name).write_text(lib.read_text(encoding="utf-8"), encoding="utf-8")
    script = f"const m = await import({json.dumps((tmp_path / lib.name).as_uri())}); console.log(JSON.stringify({body}));"
    r = subprocess.run([_node(), "--experimental-strip-types", "--no-warnings", "--input-type=module", "-e", script],
                       capture_output=True, text=True, encoding="utf-8", timeout=120)
    if r.returncode != 0 and "strip-types" in r.stderr:
        pytest.skip("this Node can't run TypeScript directly")
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


def test_the_percentage_never_reads_100_before_it_is_done(tmp_path):
    assert _ui(tmp_path, "[m.pullPercent(995, 1000), m.pullPercent(1000, 1000), m.pullPercent(456, 1000),"
                         " m.pullPercent(0, 1000), m.pullPercent(null, null)]") == [99, 99, 45, None, None]


def test_the_checking_steps_say_to_keep_the_app_open(tmp_path):
    out = _ui(tmp_path, "["
              "m.pullText({status: 'verifying sha256 digest', completed: null, total: null}),"
              "m.pullText({status: 'writing manifest', completed: null, total: null}),"
              "m.pullText({status: 'pulling 0123abcd', completed: 999, total: 1000}),"
              "m.pullText({status: 'pulling manifest', completed: null, total: null})]")
    assert out[0] == out[1] == "Checking the download… keep Clips Kitty open"
    assert out[2:] == ["Downloading 99%", "Starting…"]


def test_the_engines_record_fills_in_what_the_events_missed(tmp_path):
    running = {"tag": "a:1", "status": "pulling x", "completed": 5, "total": 10, "error": None, "done": False}
    failed = {**running, "tag": "b:1", "status": "error", "error": "disk full", "done": True}
    out = _ui(tmp_path, "["
              # A download shown at 50% that the engine says has finished leaves the list.
              f"m.mergePulls({{'a:1': {json.dumps(running)}}}, [{{...{json.dumps(running)}, status: 'done', done: true}}]),"
              # One that failed while the window wasn't listening shows its error.
              f"m.mergePulls({{'b:1': {{...{json.dumps(failed)}, status: 'pulling x', done: false, error: null}}}}, [{json.dumps(failed)}]),"
              # A failure on screen stays after the engine has forgotten it.
              f"m.mergePulls({{'b:1': {json.dumps(failed)}}}, []),"
              # Events: progress, then the error.
              "m.foldPullEvent(m.foldPullEvent({}, {type: 'model_pull', tag: 'c:1', status: 'pulling x', completed: 1, total: 4}),"
              " {type: 'model_pull', tag: 'c:1', status: 'error', error: 'gone'})]")
    assert out[0] == {}
    assert out[1] == {"b:1": failed}
    assert out[2] == {"b:1": failed}
    assert out[3] == {"c:1": {"tag": "c:1", "status": "error", "completed": None, "total": None, "error": "gone", "done": True}}


# ---- unfinished downloads kept for resuming (ui/src/main/stalePartials.ts, under Node) --


def test_downloads_untouched_for_a_week_are_cleared_before_ollama_starts(tmp_path):
    # OLLAMA_NOPRUNE keeps an unfinished download so a retry carries on, which
    # also kept the ones nobody retried, for good. A week untouched is given up.
    import os

    blobs = tmp_path / "models" / "blobs"
    blobs.mkdir(parents=True)
    week = 7 * 24 * 3600
    now = time.time()
    ages = {
        # Given up on: the data file and its progress record, both old.
        "sha256-aaaa-partial": week + 60,
        "sha256-aaaa-partial-0": week + 60,
        # Tried again yesterday: one recent piece keeps the whole download.
        "sha256-bbbb-partial": week + 60,
        "sha256-bbbb-partial-1": 24 * 3600,
        # Stopped an hour ago.
        "sha256-cccc-partial": 3600,
        # A finished layer, however old, is part of an installed model.
        "sha256-dddd": 10 * week,
        "sha256-eeee": 10 * week,
        # Whole files no model lists: what a quit during "verifying" leaves
        # (#150). Gone after a week; a recent one may still be resumed.
        "sha256-ffff": week + 60,
        "sha256-gggg": 3600,
    }
    for name, age in ages.items():
        (blobs / name).write_bytes(b"x")
        os.utime(blobs / name, (now - age, now - age))
    manifest = tmp_path / "models" / "manifests" / "registry.ollama.ai" / "library" / "gemma3" / "4b"
    manifest.parent.mkdir(parents=True)
    manifest.write_text(json.dumps({"schemaVersion": 2, "mediaType": MANIFEST_V2,
                                    "config": {"digest": "sha256:dddd"}, "layers": [{"digest": "sha256:eeee"}]}))
    dropped = _ui(tmp_path, f"m.dropStalePartials({json.dumps(str(tmp_path / 'models'))})", lib=STALE_PARTIALS)
    assert sorted(Path(p).name for p in dropped) == ["sha256-aaaa-partial", "sha256-aaaa-partial-0", "sha256-ffff"]
    assert sorted(p.name for p in blobs.iterdir()) == [
        "sha256-bbbb-partial", "sha256-bbbb-partial-1", "sha256-cccc-partial", "sha256-dddd", "sha256-eeee",
        "sha256-gggg",
    ]
    # A manifest that can't be read: no whole file is touched, since it might
    # be one of that model's.
    (blobs / "sha256-hhhh").write_bytes(b"x")
    os.utime(blobs / "sha256-hhhh", (now - 2 * week, now - 2 * week))
    (manifest.parent / "12b").write_text("{not json")
    assert _ui(tmp_path, f"m.dropStalePartials({json.dumps(str(tmp_path / 'models'))})", lib=STALE_PARTIALS) == []
    # No manifests folder at all: no model was ever installed, so an old whole
    # file belongs to nothing.
    lone = tmp_path / "lone" / "blobs"
    lone.mkdir(parents=True)
    (lone / "sha256-iiii").write_bytes(b"x")
    os.utime(lone / "sha256-iiii", (now - 2 * week, now - 2 * week))
    dropped = _ui(tmp_path, f"m.dropStalePartials({json.dumps(str(tmp_path / 'lone'))})", lib=STALE_PARTIALS)
    assert [Path(p).name for p in dropped] == ["sha256-iiii"]
    # No models folder yet (a first start): nothing to do, and no error.
    assert _ui(tmp_path, f"m.dropStalePartials({json.dumps(str(tmp_path / 'none'))})", lib=STALE_PARTIALS) == []


@pytest.mark.parametrize("layout", ["manifests-v2", "a manifest list", "another media type"])
def test_a_layout_other_than_the_bundled_ollamas_keeps_every_whole_file(tmp_path, layout):
    """Newer Ollama keeps manifests in manifests-v2/ (as blobs) and can list
    manifests in a manifest. Read as the bundled Ollama's layout, its models'
    files would look like orphans and be deleted under it: then no whole file
    goes, only the old partials."""
    import os

    models = tmp_path / "models"
    blobs = models / "blobs"
    blobs.mkdir(parents=True)
    old = time.time() - 30 * 24 * 3600
    for name in ("sha256-1111", "sha256-2222-partial"):
        (blobs / name).write_bytes(b"x")
        os.utime(blobs / name, (old, old))
    if layout == "manifests-v2":
        (models / "manifests-v2" / "registry.ollama.ai").mkdir(parents=True)
    else:
        body = ({"schemaVersion": 2, "mediaType": MANIFEST_V2, "manifests": [{"digest": "sha256:1111"}],
                 "config": {"digest": "sha256:3333"}} if layout == "a manifest list"
                else {"schemaVersion": 2, "mediaType": "application/vnd.oci.image.manifest.v1+json",
                      "config": {"digest": "sha256:3333"}, "layers": []})
        manifest = models / "manifests" / "registry.ollama.ai" / "library" / "x" / "1b"
        manifest.parent.mkdir(parents=True)
        manifest.write_text(json.dumps(body))
    dropped = _ui(tmp_path, f"m.dropStalePartials({json.dumps(str(models))})", lib=STALE_PARTIALS)
    assert [Path(p).name for p in dropped] == ["sha256-2222-partial"]
    assert (blobs / "sha256-1111").exists()


def test_the_cleanup_is_checked_again_when_the_bundled_ollama_changes():
    """dropStalePartials reads v0.32.6's manifest layout. A newer Ollama
    stores models differently; bumping it must come with a look at that
    file, or a real model's files could be taken for an abandoned download."""
    import importlib.util

    spec = importlib.util.spec_from_file_location("fetch_ollama", ROOT / "scripts" / "fetch_ollama.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.OLLAMA_VERSION == "v0.32.6", "check ui/src/main/stalePartials.ts against the new layout"
