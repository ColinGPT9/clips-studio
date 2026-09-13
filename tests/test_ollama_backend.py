"""What Clips Kitty sends to Ollama.

The models setup installs have been run against real streams with one exact
request. Reasoning models (DeepSeek-R1, gpt-oss, Nemotron 3) need extra fields,
or they spend the answer's token budget thinking and the chunk comes back empty.
These tests pin both halves: the extra fields reach reasoning models, and
nothing changes for the models that already work.
"""

import pytest
import requests

from llm.manager import RECOMMENDATIONS
from llm.ollama_backend import OllamaBackend


class _Response:
    def __init__(self, body):
        self._body = body

    def raise_for_status(self):
        pass

    def json(self):
        return self._body


def _fake_ollama(monkeypatch, capabilities=("completion",), show_fails=False):
    """Record every request, and answer /api/show with `capabilities`."""
    calls = []

    def post(url, json=None, timeout=None):
        calls.append((url, json))
        if url.endswith("/api/show"):
            if show_fails:
                raise requests.ConnectionError("nothing listening")
            return _Response({"capabilities": list(capabilities)})
        return _Response({"response": "{}"})

    monkeypatch.setattr(requests, "post", post)
    return calls


def _sent(calls):
    """The body of the last /api/generate request."""
    return [body for url, body in calls if url.endswith("/api/generate")][-1]


def _todays_request(model, json_mode):
    """Exactly what every model was sent before reasoning models were handled."""
    body = {
        "model": model,
        "prompt": "p",
        "stream": False,
        "options": {"temperature": 0.4, "num_ctx": 8192, "num_predict": 1024},
    }
    if json_mode:
        body["format"] = "json"
    return body


@pytest.mark.parametrize("json_mode", [True, False])
def test_a_model_that_cannot_think_gets_todays_request(monkeypatch, json_mode):
    calls = _fake_ollama(monkeypatch, capabilities=["completion", "tools"])
    OllamaBackend("llama3.1:8b").generate("p", json_mode=json_mode)
    assert _sent(calls) == _todays_request("llama3.1:8b", json_mode)


@pytest.mark.parametrize("tag", [tag for _hardware, tag, _note in RECOMMENDATIONS])
def test_the_models_setup_installs_are_left_alone(monkeypatch, tag):
    """gemma4:e2b and e4b can think, and they still get today's request, without
    even an extra lookup."""
    calls = _fake_ollama(monkeypatch, capabilities=["completion", "thinking"])
    OllamaBackend(tag).generate("p", json_mode=True)
    assert _sent(calls) == _todays_request(tag, True)
    assert not any(url.endswith("/api/show") for url, _ in calls)


def test_a_reasoning_model_answers_without_thinking(monkeypatch):
    calls = _fake_ollama(monkeypatch, capabilities=["completion", "tools", "thinking"])
    OllamaBackend("deepseek-r1:8b").generate("p", json_mode=True)
    sent = _sent(calls)
    assert sent["think"] is False
    assert sent["format"] == "json"
    assert sent["options"]["num_predict"] == 1024


@pytest.mark.parametrize("tag, level", [("gpt-oss:20b", "medium"), ("nemotron-3-nano:4b", True)])
def test_models_that_must_think_keep_reasoning_and_drop_format(monkeypatch, tag, level):
    """Without reasoning these answer an empty clip list. With `format` while
    thinking, Ollama's /api/generate returns an empty answer."""
    calls = _fake_ollama(monkeypatch, capabilities=["completion", "tools", "thinking"])
    OllamaBackend(tag).generate("p", json_mode=True)
    sent = _sent(calls)
    assert sent["think"] == level
    assert "format" not in sent
    assert sent["options"]["num_predict"] == 6144


def test_if_ollama_cannot_describe_the_model_the_request_is_unchanged(monkeypatch):
    calls = _fake_ollama(monkeypatch, show_fails=True)
    OllamaBackend("deepseek-r1:8b").generate("p", json_mode=True)
    assert _sent(calls) == _todays_request("deepseek-r1:8b", True)


def test_capabilities_are_asked_once_per_backend(monkeypatch):
    calls = _fake_ollama(monkeypatch, capabilities=["completion", "thinking"])
    backend = OllamaBackend("nemotron-3-nano:4b")
    for _ in range(3):
        backend.generate("p", json_mode=True)
    assert sum(url.endswith("/api/show") for url, _ in calls) == 1
