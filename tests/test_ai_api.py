"""Settings → AI over HTTP: local first, keys checked before they are kept,
and a saved key never comes back out of any route."""

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi import FastAPI
from fastapi.testclient import TestClient

from core.state import StateDB
from llm.providers import http, keys
from server import ai_api

KEY = "sk-or-v1-" + "f00dfeed" * 8
SETTINGS = "# quick setup\nmodel: gemma:7b\nchannel: \n"


class Response:
    def __init__(self, status=200, body=None):
        self.status_code = status
        self._body = body if body is not None else {}
        self.headers = {}
        self.text = str(self._body)

    def json(self):
        return self._body


MODELS = {"data": [
    {"id": "meta/muse-spark-1.3", "name": "Meta: Muse Spark 1.3", "context_length": 1048576,
     "architecture": {"output_modalities": ["text"]},
     "supported_parameters": ["response_format", "structured_outputs", "tools"]},
    {"id": "some/image-model", "architecture": {"output_modalities": ["image"]},
     "supported_parameters": ["response_format"]},
    {"id": "some/no-json-model", "architecture": {"output_modalities": ["text"]},
     "supported_parameters": ["temperature"]},
]}


class Env:
    def __init__(self, tmp_path, monkeypatch):
        self.data = tmp_path / "data"
        self.data.mkdir()
        self.settings = tmp_path / "settings.yaml"
        self.settings.write_text(SETTINGS, encoding="utf-8")
        self.config = {"llm": {"backend": "ollama/gemma:7b", "data_dir": str(self.data),
                               "ollama_host": "http://127.0.0.1:1"}}
        self.key_ok = True
        self.calls = []
        monkeypatch.setattr(http, "transport", self.transport)
        monkeypatch.setattr(http, "sleep", lambda s: None)
        app = FastAPI()
        ai_api.install(app, config=self.config, db=lambda: StateDB(tmp_path / "state.db"),
                       data_dir=self.data, settings_path=self.settings)
        self.client = TestClient(app, base_url="http://127.0.0.1")

    def transport(self, method, url, **kw):
        self.calls.append(url)
        if url.endswith("/key"):
            return Response(200, {"data": {}}) if self.key_ok else Response(401, {"error": {"code": 401, "message": "bad key"}})
        if url.endswith("/models"):
            return Response(200, MODELS)
        raise AssertionError(url)


@pytest.fixture
def env(tmp_path, monkeypatch):
    return Env(tmp_path, monkeypatch)


def test_local_is_listed_first_and_is_the_default(env):
    body = env.client.get("/ai").json()
    assert body["active"] == {"provider": "ollama", "model": "gemma:7b", "local": True}
    assert body["providers"][0]["id"] == "ollama" and body["providers"][0]["local"] is True
    assert body["providers"][1]["id"] == "openrouter"


def test_a_key_is_checked_before_it_is_kept_and_never_returned(env):
    env.key_ok = False
    bad = env.client.put("/ai/providers/openrouter/key", json={"api_key": KEY})
    assert bad.status_code == 400 and KEY not in bad.text
    assert not keys.has_key(env.data, "openrouter")

    env.key_ok = True
    good = env.client.put("/ai/providers/openrouter/key", json={"api_key": KEY})
    assert good.status_code == 200
    assert keys.load_key(env.data, "openrouter") == KEY
    row = next(p for p in good.json()["providers"] if p["id"] == "openrouter")
    assert row["has_key"] is True and row["key_tail"] == KEY[-4:]

    for path in ("/ai", "/ai/providers/openrouter/models"):
        assert KEY not in env.client.get(path).text
    assert KEY not in env.client.post("/ai/providers/openrouter/test", json={"model": "x"}).text


def test_only_models_that_can_do_the_job_are_offered(env):
    keys.save_key(env.data, "openrouter", KEY)
    models = env.client.get("/ai/providers/openrouter/models").json()["models"]
    assert [m["id"] for m in models] == ["meta/muse-spark-1.3"]
    assert models[0]["json_schema"] and models[0]["tools"]


def test_test_connection_reports_a_missing_model_without_spending_tokens(env):
    keys.save_key(env.data, "openrouter", KEY)
    ok = env.client.post("/ai/providers/openrouter/test", json={"model": "meta/muse-spark-1.3"}).json()
    missing = env.client.post("/ai/providers/openrouter/test", json={"model": "gone/model"}).json()
    assert ok["ok"] is True and missing == {**missing, "ok": False, "kind": "model_unavailable"}
    assert not any(u.endswith("/chat/completions") for u in env.calls)


def test_choosing_a_cloud_model_needs_a_key_and_switching_back_restores_local(env):
    refused = env.client.post("/ai/activate", json={"provider": "openrouter", "model": "meta/muse-spark-1.3"})
    assert refused.status_code == 400

    keys.save_key(env.data, "openrouter", KEY)
    env.client.post("/ai/activate", json={"provider": "openrouter", "model": "meta/muse-spark-1.3"})
    assert env.config["llm"]["backend"] == "openrouter/meta/muse-spark-1.3"
    assert "model: openrouter/meta/muse-spark-1.3" in env.settings.read_text(encoding="utf-8")
    assert KEY not in env.settings.read_text(encoding="utf-8")

    back = env.client.post("/ai/activate", json={"provider": "ollama"}).json()
    assert back["active"] == {"provider": "ollama", "model": "gemma:7b", "local": True}
    assert env.config["llm"]["backend"] == "ollama/gemma:7b"


def test_transcription_is_local_until_a_keyed_provider_is_chosen(env):
    assert env.client.get("/ai").json()["transcription"] == {"backend": "local", "model": ""}
    assert env.client.post("/ai/transcription", json={"backend": "openrouter"}).status_code == 400  # no key yet
    assert env.client.post("/ai/transcription", json={"backend": "anthropic"}).status_code == 400  # no audio

    keys.save_key(env.data, "openrouter", KEY)
    chosen = env.client.post("/ai/transcription", json={"backend": "openrouter"}).json()["transcription"]
    assert chosen == {"backend": "openrouter", "model": "openai/whisper-large-v3-turbo"}
    text = env.settings.read_text(encoding="utf-8")
    assert "transcription:\n  backend: openrouter" in text and "model: gemma:7b" in text  # added, nothing lost

    env.client.post("/ai/transcription", json={"backend": "local"})
    assert env.config["transcription"] == {"backend": "local", "model": ""}
    assert env.settings.read_text(encoding="utf-8").count("transcription:") == 1  # replaced, not repeated


def test_removing_a_key_forgets_it(env):
    keys.save_key(env.data, "openrouter", KEY)
    env.client.delete("/ai/providers/openrouter/key")
    assert not keys.has_key(env.data, "openrouter")
