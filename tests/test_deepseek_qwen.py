"""DeepSeek and Qwen, the direct providers added under Advanced.

Both speak OpenAI-compatible chat completions, so they are catalogue entries
and nothing more. Qwen's keys only work in the Alibaba Cloud region they were
made in: a new key is tried region by region, and the one that accepts it is
kept with the key and used for every request after.
"""

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi import FastAPI
from fastapi.testclient import TestClient

from core.state import StateDB
from llm.providers import http, keys
from llm.providers.catalog import PROVIDERS
from llm.providers.cloud_backend import CloudBackend
from server import ai_api

KEY = "sk-" + "0a1b2c3d" * 4
INTL = "https://dashscope-intl.aliyuncs.com/compatible-mode/v1"
US = "https://dashscope-us.aliyuncs.com/compatible-mode/v1"

QWEN_MODELS = {"object": "list", "data": [
    {"id": "qwen-plus", "object": "model"},
    {"id": "qwen3.8-max", "object": "model"},
    {"id": "qwq-plus", "object": "model"},
    {"id": "qwen-vl-max", "object": "model"},
    {"id": "qwen3-asr-flash", "object": "model"},
    {"id": "qwen-tts", "object": "model"},
    {"id": "text-embedding-v4", "object": "model"},
    {"id": "qwen-mt-plus", "object": "model"},
    {"id": "deepseek-r1", "object": "model"},
]}

DEEPSEEK_MODELS = {"object": "list", "data": [
    {"id": "deepseek-flash", "name": "DeepSeek Flash", "context_window": 1048576,
     "input_modalities": ["text"], "output_modalities": ["text"]},
    {"id": "deepseek-v4-pro", "name": "DeepSeek V4 Pro", "context_window": 1048576,
     "output_modalities": ["text"]},
    {"id": "deepseek-image-exp", "output_modalities": ["image"]},
]}

ANSWER = {"choices": [{"message": {"content": '{"clips": []}'}, "finish_reason": "stop"}]}


class Response:
    def __init__(self, status=200, body=None):
        self.status_code = status
        self._body = body if body is not None else {}
        self.headers = {}
        self.text = str(self._body)

    def json(self):
        return self._body


class Env:
    def __init__(self, tmp_path, monkeypatch):
        self.data = tmp_path / "data"
        self.data.mkdir()
        self.settings = tmp_path / "settings.yaml"
        self.settings.write_text("model: gemma:7b\n", encoding="utf-8")
        self.config = {"llm": {"backend": "ollama/gemma:7b", "data_dir": str(self.data),
                               "ollama_host": "http://127.0.0.1:1"}}
        self.accepts = {US}  # the region hosts that know the key
        self.calls: list[str] = []
        monkeypatch.setattr(http, "transport", self.transport)
        monkeypatch.setattr(http, "sleep", lambda s: None)
        app = FastAPI()
        ai_api.install(app, config=self.config, db=lambda: StateDB(tmp_path / "state.db"),
                       data_dir=self.data, settings_path=self.settings)
        self.client = TestClient(app, base_url="http://127.0.0.1")

    def transport(self, method, url, **kw):
        self.calls.append(url)
        if url.startswith("https://api.deepseek.com"):
            if url.endswith("/models"):
                return Response(200, DEEPSEEK_MODELS)
            return Response(200, ANSWER)
        base = url.split("/compatible-mode/v1")[0] + "/compatible-mode/v1"
        if base not in self.accepts:
            return Response(401, {"error": {"code": "invalid_api_key", "message": "Incorrect API key provided."}})
        if url.endswith("/models"):
            return Response(200, QWEN_MODELS)
        if url.endswith("/chat/completions"):
            return Response(200, ANSWER)
        raise AssertionError(url)


@pytest.fixture
def env(tmp_path, monkeypatch):
    return Env(tmp_path, monkeypatch)


def test_both_are_direct_providers_under_advanced():
    for provider_id in ("deepseek", "qwen"):
        spec = PROVIDERS[provider_id]
        assert spec.tier == 3 and spec.adapter == "chat_completions"
        assert spec.key_url.startswith("https://") and spec.pricing_url.startswith("https://")


def test_deepseek_offers_its_text_models_with_their_context(env):
    env.client.put("/ai/providers/deepseek/key", json={"api_key": KEY})
    models = env.client.get("/ai/providers/deepseek/models").json()["models"]
    assert [(m["id"], m["name"], m["context"]) for m in models] == [
        ("deepseek-flash", "DeepSeek Flash", 1048576),
        ("deepseek-v4-pro", "DeepSeek V4 Pro", 1048576),
    ]
    assert all(u.startswith("https://api.deepseek.com/") for u in env.calls)


def test_qwen_offers_only_its_text_chat_models(env):
    env.client.put("/ai/providers/qwen/key", json={"api_key": KEY})
    models = env.client.get("/ai/providers/qwen/models").json()["models"]
    assert [m["id"] for m in models] == ["qwen-plus", "qwen3.8-max", "qwq-plus"]


def test_a_qwen_key_is_kept_with_the_region_that_accepted_it(env):
    saved = env.client.put("/ai/providers/qwen/key", json={"api_key": KEY})
    assert saved.status_code == 200
    assert "US (Virginia) region" in saved.json()["message"]
    assert env.calls[0].startswith(INTL)  # Singapore is tried first
    row = next(p for p in saved.json()["providers"] if p["id"] == "qwen")
    assert row["key_region"] == "US (Virginia)" and KEY not in saved.text
    assert keys.load_region(env.data, "qwen") == "us"

    env.calls.clear()
    env.client.get("/ai/providers/qwen/models?refresh=true")
    CloudBackend(PROVIDERS["qwen"], "qwen-plus", env.data).generate("pick clips", json_mode=True)
    assert env.calls and all(u.startswith(US) for u in env.calls)


def test_a_qwen_key_no_region_accepts_is_not_kept(env):
    env.accepts = set()
    refused = env.client.put("/ai/providers/qwen/key", json={"api_key": KEY})
    assert refused.status_code == 400
    detail = refused.json()["detail"]
    assert "Singapore" in detail and "China (Hong Kong)" in detail and KEY not in refused.text
    assert not keys.has_key(env.data, "qwen")


def test_a_key_saved_without_a_region_uses_the_first_one(env):
    env.accepts = {INTL}
    keys.save_key(env.data, "qwen", KEY)
    spec, key = keys.resolve(env.data, PROVIDERS["qwen"])
    assert spec.base_url == INTL and key == KEY
    assert keys.load_region(env.data, "deepseek") == ""
    assert PROVIDERS["deepseek"].in_region("us") is PROVIDERS["deepseek"]


def test_choosing_either_needs_its_key(env):
    for provider_id, model in (("deepseek", "deepseek-flash"), ("qwen", "qwen-plus")):
        refused = env.client.post("/ai/activate", json={"provider": provider_id, "model": model})
        assert refused.status_code == 400
        env.client.put(f"/ai/providers/{provider_id}/key", json={"api_key": KEY})
        chosen = env.client.post("/ai/activate", json={"provider": provider_id, "model": model}).json()
        assert chosen["active"] == {"provider": provider_id, "model": model, "local": False}
    assert "model: qwen/qwen-plus" in env.settings.read_text(encoding="utf-8")
