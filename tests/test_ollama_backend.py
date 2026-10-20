"""What Clips Kitty sends to Ollama.

The models setup installs have been run against real streams with one exact
request. Reasoning models (DeepSeek-R1, gpt-oss, Nemotron 3) need extra fields,
or they spend the answer's token budget thinking and the chunk comes back empty.
These tests pin both halves: the extra fields reach reasoning models, and
nothing changes for the models that already work.
"""

import re
from pathlib import Path

import pytest
import requests

from llm.manager import RECOMMENDATIONS, missing_model
from llm.ollama_backend import OllamaBackend
from llm.providers.base import LLMError


class _Response:
    status_code = 200

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


@pytest.mark.parametrize("tag", [tag for _hardware, tag, _note in RECOMMENDATIONS
                                 if not tag.startswith("gemma4")])
def test_the_models_setup_installs_are_left_alone(monkeypatch, tag):
    """The setup models that cannot think get today's request, without even an
    extra lookup."""
    calls = _fake_ollama(monkeypatch, capabilities=["completion", "thinking"])
    OllamaBackend(tag).generate("p", json_mode=True)
    assert _sent(calls) == _todays_request(tag, True)
    assert not any(url.endswith("/api/show") for url, _ in calls)


@pytest.mark.parametrize("tag", ["gemma4:e2b", "gemma4:e4b"])
def test_setups_gemma_4_answers_without_thinking(monkeypatch, tag):
    """Left thinking, gemma4:e2b and e4b ran into JSON mode and gave empty or
    cut-off answers: three of four chunks of a real stream scored nothing."""
    calls = _fake_ollama(monkeypatch, capabilities=["completion", "vision", "tools", "thinking"])
    OllamaBackend(tag).generate("p", json_mode=True)
    sent = _sent(calls)
    assert sent["think"] is False
    # Held to JSON mode it repeated itself until Ollama aborted it (a 500).
    assert "format" not in sent
    assert sent["options"]["num_predict"] == 1024


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


# ---- looking at frames (a gaming stream's candidates) ----------------------------------


def test_a_vision_model_is_shown_the_frames_and_thinking_is_off(monkeypatch):
    calls = _fake_ollama(monkeypatch, capabilities=("completion", "vision", "thinking"))
    llm = OllamaBackend("gemma4:e4b")
    assert llm.sees_images()
    llm.look("what happens?", [b"\xff\xd8one", b"\xff\xd8two"])
    body = _sent(calls)
    assert body["images"] == ["/9hvbmU=", "/9h0d28="]          # base64 of each JPEG, in order
    assert body["format"] == "json" and body["think"] is False  # JSON mode blanks a thinking model


def test_a_text_model_cannot_look_and_a_plain_vision_model_is_sent_no_think(monkeypatch):
    _fake_ollama(monkeypatch, capabilities=("completion",))
    assert not OllamaBackend("gemma:7b").sees_images()
    calls = _fake_ollama(monkeypatch, capabilities=("completion", "vision"))
    OllamaBackend("gemma3:4b").look("p", [b"x"])
    assert "think" not in _sent(calls)


# ---- a request Ollama fails ------------------------------------------------------------


class _Failed(_Response):
    def __init__(self, status, error):
        super().__init__({"error": error})
        self.status_code = status

    def raise_for_status(self):
        raise requests.HTTPError(f"{self.status_code} Server Error")


CUT_OFF = "prediction aborted, token repeat limit reached"


def _answers(monkeypatch, *replies):
    """Ollama answers each /api/generate with the next of `replies`."""
    sent, left = [], list(replies)
    monkeypatch.setattr(requests, "post", lambda url, json=None, timeout=None: (
        sent.append(json) or left.pop(0)) if url.endswith("/api/generate") else _Response({"capabilities": []}))
    monkeypatch.setattr("llm.ollama_backend.time.sleep", lambda _s: None)
    return sent


def test_an_answer_ollama_cuts_off_is_asked_again_without_json_mode(monkeypatch, capsys):
    """On an NBA game gemma3:4b, held to JSON mode, repeated itself until Ollama
    cut it off with a 500, and the job ended there: five runs in a row, six to
    eight minutes in. Without `format` (gemma4's cure), the prompt still asks
    for the JSON."""
    sent = _answers(monkeypatch, _Failed(500, CUT_OFF), _Response({"response": '{"clips": []}'}))
    assert OllamaBackend("gemma3:4b").generate("p", json_mode=True) == '{"clips": []}'
    assert sent[0] == _todays_request("gemma3:4b", True)
    assert sent[1] == _todays_request("gemma3:4b", False)
    assert f"Ollama answered 500: {CUT_OFF}; asking again without JSON mode" in capsys.readouterr().out


def test_an_answer_cut_off_every_time_is_one_the_caller_cant_read(monkeypatch, capsys):
    """Every caller gets past an answer it can't read (a chunk skipped, a batch
    of windows left at a neutral score); the job goes on."""
    sent = _answers(monkeypatch, *[_Failed(500, CUT_OFF)] * 3)
    assert OllamaBackend("gemma3:4b").generate("p", json_mode=True) == ""
    assert len(sent) == 3
    assert "going on without it" in capsys.readouterr().out


def test_another_server_error_every_time_still_ends_the_job(monkeypatch):
    sent = _answers(monkeypatch, *[_Failed(500, "llama runner process has terminated")] * 3)
    with pytest.raises(requests.HTTPError):
        OllamaBackend("gemma3:4b").generate("p", json_mode=True)
    assert len(sent) == 3


def test_a_model_ollama_doesnt_have_ends_the_job_in_plain_words(monkeypatch, capsys):
    """#149: the queue said "404 Client Error: Not Found for url:
    http://127.0.0.1:11435/api/generate", after the download and the
    transcription. A model that was never downloaded won't appear on a second
    try, so it is asked once, and the job says what to do about it."""
    sent = _answers(monkeypatch, _Failed(404, "model 'gemma9' not found, try pulling it first"))
    with pytest.raises(LLMError) as e:
        OllamaBackend("gemma9").generate("p")
    assert len(sent) == 1
    assert e.value.kind == "model_unavailable"
    assert e.value.message == ("The AI model gemma9 isn't downloaded. "
                               "Download it on the Models page, or choose a model you have.")
    assert "model 'gemma9' not found" in capsys.readouterr().out  # the log keeps Ollama's own words


def test_any_other_refusal_ends_the_job_at_once_with_what_ollama_said(monkeypatch):
    """Not "400 Client Error" and an address: what Ollama said was wrong."""
    sent = _answers(monkeypatch, _Failed(400, "invalid options: num_ctx"))
    with pytest.raises(LLMError) as e:
        OllamaBackend("gemma3:4b").generate("p", json_mode=True)
    assert len(sent) == 1
    assert e.value.kind == "rejected"
    assert e.value.message == "Ollama refused the request (400): invalid options: num_ctx"


def test_the_queue_row_offers_the_models_page_for_a_missing_model(tmp_path):
    """The queue row reads the job's error to offer Open Models, and the cloud
    suggestion beside it (QueueItem.tsx). Reworded on one side only, both
    would quietly disappear.

    A plugin's own model that isn't here also "isn't downloaded yet", and the
    plugin's job fails with that sentence. Neither the Models page nor a cloud
    key gets a plugin its model, so the row offers neither for it."""
    from plugins import models as plugin_models

    row = (Path(__file__).resolve().parent.parent / "ui" / "src" / "renderer" / "src"
           / "components" / "queue" / "QueueItem.tsx").read_text(encoding="utf-8")
    pattern = re.search(r"^const MODEL_NOT_DOWNLOADED = /(.+)/i$", row, re.M).group(1)
    for model in ("gemma9", ""):
        assert re.search(pattern, missing_model(model).message, re.I)
    assert not re.search(pattern, "404 Client Error: Not Found for url: http://127.0.0.1:11435/api/generate", re.I)

    plugin = {"models": [
        {"name": "weights", "source": "url", "id": "https://example.com/w.onnx", "sha256": "c" * 64,
         "size_bytes": 52_428_800},
        {"name": "helper", "source": "ollama", "id": "example-helper:7b"}]}
    _handed, missing = plugin_models.for_job(tmp_path, plugin, ollama_host="http://localhost:11434",
                                             fetch_json=lambda url: {"models": []})
    assert len(missing) == 2 and all("isn't downloaded" in m for m in missing)  # both of its sentences
    for message in missing:
        assert not re.search(pattern, message, re.I), message


def test_a_look_at_frames_ollama_cuts_off_is_asked_again_too(monkeypatch):
    sent = _answers(monkeypatch, _Failed(500, CUT_OFF), _Response({"response": '{"shot": "crowd"}'}))
    assert OllamaBackend("gemma3:4b").look("p", [b"x"]) == '{"shot": "crowd"}'
    assert sent[0]["format"] == "json" and "format" not in sent[1] and sent[1]["images"] == sent[0]["images"]
