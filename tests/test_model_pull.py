"""A model download counts as done only when it is (#148, #150).

Ollama ends a pull that worked with {"status": "success"}, and the model then
shows up in /api/tags. Before this, a stream that stopped short (Ollama quit
while it checked the download) was announced as done, and setup could then
select a model that wasn't there, so every job failed with a 404. Each
download's state is also kept for GET /models/pulls, so a window that missed
the last WebSocket event can still find out how it ended.
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
    """What requests.post(..., stream=True) gives back: Ollama's lines."""

    def __init__(self, lines, gate=None):
        self.lines = lines
        self.gate = gate

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def iter_lines(self):
        for i, line in enumerate(self.lines):
            if self.gate is not None and i == 2:
                self.gate.wait(5)  # hold the download part-way, as a real one is
            yield json.dumps(line).encode()


class Tags:
    def __init__(self, names):
        self.names = names

    def raise_for_status(self):
        pass

    def json(self):
        return {"models": [{"name": n, "size": 3_300_000_000} for n in self.names]}


class Ollama:
    """Stands in for Ollama: the lines a pull streams and what it then lists."""

    def __init__(self, lines, listed, gate=None):
        self.lines = lines
        self.listed = listed
        self.gate = gate
        self.pulls = []

    def post(self, url, json=None, **kw):
        assert url.endswith("/api/pull"), url
        self.pulls.append(json["model"])
        return Stream(self.lines, self.gate)

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

    def ollama(lines, listed, gate=None):
        import requests

        fake = Ollama(lines, listed, gate)
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
    # Ollama quit while it checked the download: the stream ends on the step
    # it was on, with every byte counted.
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


def _ui(tmp_path, body: str):
    """Run `body` (JavaScript, `m` is the module) and return what it logs as JSON."""
    import subprocess

    lib = ROOT / "ui" / "src" / "renderer" / "src" / "lib" / "modelPull.ts"
    # Copied out of ui/ so ui/package.json doesn't decide how Node loads it.
    (tmp_path / "modelPull.ts").write_text(lib.read_text(encoding="utf-8"), encoding="utf-8")
    script = f"const m = await import({json.dumps((tmp_path / 'modelPull.ts').as_uri())}); console.log(JSON.stringify({body}));"
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
