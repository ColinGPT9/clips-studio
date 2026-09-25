"""OpenRouter's live model lists and prices, as the settings card shows them.

Prices are read from OpenRouter's listing every time, never written down here:
these tests only check each listed field lands in the right place, with the
right unit, and that nothing is shown that OpenRouter did not list.
"""

import json

import pytest

from llm.providers import http, keys, openrouter
from llm.providers.catalog import PROVIDERS
from llm.providers.speech import list_stt_models

KEY = "sk-or-v1-" + "5eed5eed" * 8
ATTRIBUTION = openrouter.attribution_headers()

TEXT = {
    "id": "anthropic/claude-sonnet-5", "name": "Anthropic: Claude Sonnet 5", "context_length": 1_000_000,
    "architecture": {"output_modalities": ["text"]},
    "supported_parameters": ["structured_outputs", "response_format", "tools"],
    "pricing": {"prompt": "0.000003", "completion": "0.000015", "input_cache_read": "0.0000003",
                "input_cache_write": "0.00000375", "internal_reasoning": "0", "request": "0",
                "image": "0", "web_search": "0"},
}


def speech(model_id, **pricing):
    return {"id": model_id, "name": model_id, "architecture": {"output_modalities": ["transcription"]},
            "supported_parameters": [], "pricing": {k: str(v) for k, v in pricing.items()}}


# ---- text models ------------------------------------------------------------


def test_every_listed_text_price_lands_in_the_right_place_and_nothing_else():
    info = openrouter.model_info(TEXT).as_dict()
    assert info["price"] == {"input": 3.0, "output": 15.0}
    assert [(p["label"], p["amount"], p["unit"]) for p in info["pricing"]] == [
        ("Input", 3.0, "per 1M tokens"),
        ("Output", 15.0, "per 1M tokens"),
        ("Cached input", 0.3, "per 1M tokens"),
        ("Cache write", 3.75, "per 1M tokens"),
    ]  # the zero-priced fields are not shown
    assert info["free"] is False
    assert info["page_url"] == "https://openrouter.ai/anthropic/claude-sonnet-5"


def test_a_free_model_says_so_and_a_varying_price_is_not_a_price():
    free = openrouter.model_info({**TEXT, "id": "x/y:free", "pricing": {"prompt": "0", "completion": "0"}})
    assert free.free and free.pricing == ()
    varies = openrouter.model_info({**TEXT, "id": "openrouter/auto", "pricing": {"prompt": "-1", "completion": "-1"}})
    assert varies.price is None and varies.pricing == () and not varies.free


# ---- voice models -------------------------------------------------------------


def test_a_token_billed_voice_model_is_shown_in_tokens():
    info = openrouter.stt_model_info(speech("google/gemini-3.5-transcribe", prompt=0.000002, completion=0.000012))
    assert [(label, amount, unit) for label, amount, unit, _estimate in info.pricing] == [
        ("Input", 2.0, "per 1M tokens"), ("Output", 12.0, "per 1M tokens")]


def test_an_audio_price_is_shown_exactly_and_estimated_only_when_the_unit_is_clear():
    whisper = openrouter.stt_model_info(speech("openai/whisper-1", prompt=0.0001))
    assert whisper.pricing == (("OpenRouter rate", 0.0001, "unit not stated by OpenRouter", False),
                               ("Per hour of audio", 0.36, "estimate", True))  # OpenAI's own $0.006/min
    assert whisper.verified  # returns word timings: no test clip needed
    mai = openrouter.stt_model_info(speech("microsoft/mai-transcribe-1.5", prompt=0.36))
    assert mai.pricing == (("OpenRouter rate", 0.36, "unit not stated by OpenRouter", False),)  # no guess
    assert not mai.verified


def test_only_transcription_models_are_voice_models():
    assert openrouter.stt_model_info(TEXT) is None


# ---- retrieval, with the attribution ------------------------------------------------


class Response:
    def __init__(self, body):
        self.status_code = 200
        self._body = body
        self.headers = {}
        self.text = json.dumps(body)

    def json(self):
        return self._body


@pytest.fixture
def served(monkeypatch):
    calls = []

    def transport(method, url, **kw):
        calls.append({"url": url, **kw})
        if "output_modalities=transcription" in url:
            return Response({"data": [speech("mistralai/voxtral-mini-transcribe", prompt=0.00005),
                                      speech("openai/whisper-1", prompt=0.0001)]})
        return Response({"data": [TEXT]})

    monkeypatch.setattr(http, "transport", transport)
    return calls


def test_text_models_come_from_the_accounts_own_list(served):
    from llm.providers.adapters import adapter_for

    spec = PROVIDERS["openrouter"]
    models = adapter_for(spec).list_models(spec, KEY)
    assert [m.id for m in models] == ["anthropic/claude-sonnet-5"]
    assert served[0]["url"] == "https://openrouter.ai/api/v1/models/user"
    assert all(served[0]["headers"][k] == v for k, v in ATTRIBUTION.items())


def test_voice_models_come_from_the_live_speech_catalogue_known_good_first(served):
    models = list_stt_models(PROVIDERS["openrouter"], KEY)
    assert [m.id for m in models] == ["openai/whisper-1", "mistralai/voxtral-mini-transcribe"]
    assert "output_modalities=transcription" in served[0]["url"]
    assert all(served[0]["headers"][k] == v for k, v in ATTRIBUTION.items())


def test_a_provider_without_a_catalogue_offers_its_known_voice_model():
    models = list_stt_models(PROVIDERS["openai"], "sk-proj-" + "x" * 30)
    assert [(m.id, m.verified) for m in models] == [("whisper-1", True)]


# ---- the API: refresh, the check, persistence, requests -------------------------------

fastapi = pytest.importorskip("fastapi")
pytest.importorskip("httpx")


class Env:
    def __init__(self, tmp_path, monkeypatch, words=True):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        from core.state import StateDB
        from server import ai_api

        self.data = tmp_path / "data"
        self.data.mkdir()
        self.settings = tmp_path / "settings.yaml"
        self.settings.write_text("model: gemma:7b\nchannel: \n", encoding="utf-8")
        self.config = {"llm": {"backend": "ollama/gemma:7b", "data_dir": str(self.data)}}
        self.calls = []
        self.words = words
        monkeypatch.setattr(http, "transport", self.transport)
        monkeypatch.setattr(http, "sleep", lambda s: None)
        app = FastAPI()
        ai_api.install(app, config=self.config, db=lambda: StateDB(tmp_path / "state.db"),
                       data_dir=self.data, settings_path=self.settings)
        self.client = TestClient(app, base_url="http://127.0.0.1")
        keys.save_key(self.data, "openrouter", KEY)

    def transport(self, method, url, **kw):
        self.calls.append({"method": method, "url": url, **kw})
        if url.endswith("/audio/transcriptions"):
            words = [{"word": "Clips", "start": 0.2, "end": 0.5}] if self.words else []
            return Response({"language": "en", "segments": [{"text": "Clips"}], "words": words})
        if "output_modalities=transcription" in url:
            return Response({"data": [speech("mistralai/voxtral-mini-transcribe", prompt=0.00005)]})
        if url.endswith("/chat/completions"):
            return Response({"choices": [{"message": {"content": "{}"}}]})
        return Response({"data": [TEXT]})


def test_models_and_prices_refresh_together_and_are_dated(tmp_path, monkeypatch):
    env = Env(tmp_path, monkeypatch)
    first = env.client.get("/ai/providers/openrouter/models").json()
    again = env.client.get("/ai/providers/openrouter/models").json()
    assert first["fetched_at"] == again["fetched_at"] and len(env.calls) == 1  # cached
    fresh = env.client.get("/ai/providers/openrouter/models?refresh=true").json()
    assert fresh["fetched_at"] >= first["fetched_at"] and len(env.calls) == 2
    voice = env.client.get("/ai/providers/openrouter/models?kind=stt").json()
    assert voice["kind"] == "stt" and voice["models"][0]["id"] == "mistralai/voxtral-mini-transcribe"


def test_a_new_key_means_a_fresh_list(tmp_path, monkeypatch):
    env = Env(tmp_path, monkeypatch)
    env.client.get("/ai/providers/openrouter/models")
    env.client.put("/ai/providers/openrouter/key", json={"api_key": KEY})  # checks /key, then forgets the list
    before = len(env.calls)
    env.client.get("/ai/providers/openrouter/models")
    assert len(env.calls) == before + 1


def test_a_known_voice_model_is_chosen_without_a_test_clip(tmp_path, monkeypatch):
    env = Env(tmp_path, monkeypatch)
    got = env.client.post("/ai/providers/openrouter/stt-check", json={"model": "openai/whisper-1"}).json()
    assert got["ok"] and got["transcription"] == {"backend": "openrouter", "model": "openai/whisper-1"}
    assert env.calls == []


def test_another_voice_model_is_chosen_only_if_it_returns_word_timings(tmp_path, monkeypatch):
    env = Env(tmp_path, monkeypatch, words=False)
    refused = env.client.post("/ai/providers/openrouter/stt-check",
                              json={"model": "mistralai/voxtral-mini-transcribe"}).json()
    assert refused["ok"] is False and "word timings" in refused["message"]
    assert refused["transcription"] == {"backend": "local", "model": ""}  # nothing changed
    sent = env.calls[-1]
    assert sent["url"].endswith("/audio/transcriptions")
    assert sent["json"]["model"] == "mistralai/voxtral-mini-transcribe"  # the model actually asked for
    assert all(sent["headers"][k] == v for k, v in ATTRIBUTION.items())

    env.words = True
    chosen = env.client.post("/ai/providers/openrouter/stt-check",
                             json={"model": "mistralai/voxtral-mini-transcribe"}).json()
    assert chosen["ok"] and chosen["transcription"]["model"] == "mistralai/voxtral-mini-transcribe"


def test_choices_survive_a_restart_and_are_the_models_sent(tmp_path, monkeypatch):
    import main
    from llm.registry import create_backend

    env = Env(tmp_path, monkeypatch)
    env.client.post("/ai/activate", json={"provider": "openrouter", "model": "anthropic/claude-sonnet-5"})
    env.client.post("/ai/providers/openrouter/stt-check", json={"model": "openai/whisper-1"})

    reloaded = main.load_config(env.settings)  # what the next start reads
    assert reloaded["llm"]["backend"] == "openrouter/anthropic/claude-sonnet-5"
    assert reloaded["transcription"] == {"backend": "openrouter", "model": "openai/whisper-1"}

    backend = create_backend({**reloaded["llm"], "data_dir": str(env.data)})
    backend.generate("p", json_mode=True)
    assert env.calls[-1]["json"]["model"] == "anthropic/claude-sonnet-5"
