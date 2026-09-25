"""Cloud AI on the user's own key: the promises that matter.

- Local stays the default and is called exactly as before.
- Every OpenRouter request carries the app's attribution headers.
- The key goes in a header, never into a message, and nothing runs without it.
- A failing provider is reported in plain words, never swapped for another.

No test here touches the network: llm/providers/http.transport is faked.
"""

from pathlib import Path

import pytest

from llm.base import LLMBackend, generate_json
from llm.providers import http, keys, openrouter
from llm.providers.base import LLMError
from llm.providers.catalog import PROVIDERS
from llm.providers.cloud_backend import CloudBackend
from llm.registry import create_backend
from llm.spec import is_local, parse_spec

KEY = "sk-or-v1-" + "a1b2c3d4" * 8
ATTRIBUTION = {
    "HTTP-Referer": "https://colingpt9.github.io/clips-studio/",
    "X-OpenRouter-Title": "Clips Kitty",
    "X-OpenRouter-Categories": "video-gen",
}


class Response:
    def __init__(self, status=200, body=None, headers=None):
        self.status_code = status
        self._body = body if body is not None else {}
        self.headers = headers or {}
        self.text = str(self._body)

    def json(self):
        return self._body


class Transport:
    """Answers by path, in order when a path has a list of answers."""

    def __init__(self, answers):
        self.answers = answers
        self.calls = []

    def __call__(self, method, url, **kw):
        self.calls.append({"method": method, "url": url, **kw})
        for path, answer in self.answers.items():
            if url.endswith(path):
                if isinstance(answer, list):
                    return answer.pop(0) if len(answer) > 1 else answer[0]
                return answer
        raise AssertionError(f"unexpected request {method} {url}")


def reply(text):
    return Response(200, {"choices": [{"message": {"content": text}, "finish_reason": "stop"}]})


@pytest.fixture
def fake(monkeypatch):
    def install(answers):
        transport = Transport(answers)
        monkeypatch.setattr(http, "transport", transport)
        return transport

    waits = []
    monkeypatch.setattr(http, "sleep", waits.append)
    install.waits = waits
    return install


@pytest.fixture
def backend(tmp_path):
    keys.save_key(tmp_path, "openrouter", KEY)
    return CloudBackend(PROVIDERS["openrouter"], "meta/muse-spark-1.3", tmp_path)


# ---- specs and the registry --------------------------------------------------


def test_a_spec_keeps_the_models_own_slashes():
    assert parse_spec("openrouter/meta/muse-spark-1.3") == ("openrouter", "meta/muse-spark-1.3")
    assert parse_spec("ollama/gemma:7b") == ("ollama", "gemma:7b")
    assert parse_spec("gemma:7b") == ("ollama", "gemma:7b")
    assert is_local("") and is_local("gemma:7b") and not is_local("openrouter/x/y")


def test_local_stays_ollama_and_cloud_needs_a_known_provider(tmp_path):
    from llm.ollama_backend import OllamaBackend

    assert isinstance(create_backend({"backend": "ollama/gemma:7b"}), OllamaBackend)
    cloud = create_backend({"backend": "openrouter/meta/muse-spark-1.3", "data_dir": str(tmp_path)})
    assert isinstance(cloud, CloudBackend) and cloud.name == "openrouter/meta/muse-spark-1.3"
    with pytest.raises(ValueError):
        create_backend({"backend": "nobody/model", "data_dir": str(tmp_path)})
    with pytest.raises(ValueError):
        create_backend({"backend": "openrouter/meta/muse-spark-1.3"})  # nowhere to read a key from


def test_a_backend_without_schemas_is_called_exactly_as_before():
    seen = []

    class Local(LLMBackend):
        name = "local"

        def generate(self, prompt, *, json_mode=False):
            seen.append((prompt, json_mode))
            return "{}"

    generate_json(Local(), "p", {"type": "object"})
    assert seen == [("p", True)]


# ---- OpenRouter attribution --------------------------------------------------


def test_every_openrouter_request_carries_the_attribution(fake, backend):
    transport = fake({
        "/chat/completions": [Response(503, {"error": {"message": "busy"}}), reply('{"clips": []}')],
        "/models": Response(200, {"data": []}),
        "/key": Response(200, {"data": {"label": "x"}}),
    })
    backend.generate("p", json_mode=True, schema={"type": "object"})  # includes a retry
    backend.chat([{"role": "user", "content": "hi"}], [])
    spec = PROVIDERS["openrouter"]
    openrouter_adapter = backend._adapter
    openrouter_adapter.list_models(spec, KEY)
    openrouter_adapter.check_key(spec, KEY)

    assert len(transport.calls) == 5
    for call in transport.calls:
        for header, value in ATTRIBUTION.items():
            assert call["headers"].get(header) == value, (call["url"], header)
        assert KEY not in call["url"]
        assert call["headers"]["Authorization"] == f"Bearer {KEY}"


def test_openrouter_models_carry_their_maker_and_price_per_million():
    entry = {"id": "anthropic/claude-sonnet-5", "name": "Anthropic: Claude Sonnet 5", "context_length": 200000,
             "architecture": {"output_modalities": ["text"]},
             "supported_parameters": ["structured_outputs", "tools"],
             "pricing": {"prompt": "0.000003", "completion": "0.000015"}}
    info = openrouter.model_info(entry)
    assert (info.vendor, info.price, info.context) == ("anthropic", (3.0, 15.0), 200000)
    assert info.as_dict()["price"] == {"input": 3.0, "output": 15.0}
    routed = openrouter.model_info({**entry, "id": "openrouter/auto", "pricing": {"prompt": "-1", "completion": "-1"}})
    assert routed.price is None  # "varies" is not a price


def test_the_attribution_is_the_apps_identity_and_the_right_category():
    assert openrouter.attribution_headers() == ATTRIBUTION
    assert PROVIDERS["openrouter"].extra_headers() == ATTRIBUTION


def test_nothing_else_in_the_engine_talks_to_openrouter():
    root = Path(__file__).resolve().parent.parent
    skip = {"tests", "web", "build", "release", "node_modules", "vendor", ".venv", "venv"}
    offenders = []
    for path in root.rglob("*.py"):
        parts = set(path.relative_to(root).parts)
        if parts & skip or ("providers" in path.parts and "llm" in path.parts):
            continue
        if "openrouter.ai" in path.read_text(encoding="utf-8", errors="ignore"):
            offenders.append(str(path.relative_to(root)))
    assert offenders == []


# ---- requests and answers ----------------------------------------------------


def test_a_schema_asks_for_strict_json_on_capable_providers_only(fake, backend):
    transport = fake({"/chat/completions": reply('{"clips": []}')})
    assert backend.generate("p", json_mode=True, schema={"type": "object"}) == '{"clips": []}'
    body = transport.calls[0]["json"]
    assert body["response_format"]["type"] == "json_schema"
    assert body["response_format"]["json_schema"]["strict"] is True
    assert body["provider"] == {"require_parameters": True}
    assert body["messages"] == [{"role": "user", "content": "p"}]


def test_a_model_that_refuses_a_schema_is_asked_for_plain_json_and_remembered(fake, tmp_path):
    keys.save_key(tmp_path, "openrouter", KEY)
    b = CloudBackend(PROVIDERS["openrouter"], "some/other-model", tmp_path)
    transport = fake({"/chat/completions": [
        Response(400, {"error": {"message": "response_format json_schema is not supported"}}),
        reply('{"clips": []}'),
        reply('{"clips": []}'),
    ]})
    b.generate("p", json_mode=True, schema={"type": "object"})
    b.generate("p", json_mode=True, schema={"type": "object"})
    formats = [c["json"].get("response_format", {}).get("type") for c in transport.calls]
    assert formats == ["json_schema", "json_object", "json_object"]


def test_tool_calls_come_back_as_calls(fake, backend):
    fake({"/chat/completions": Response(200, {"choices": [{"message": {
        "content": None,
        "tool_calls": [{"id": "call_1", "type": "function",
                        "function": {"name": "queue_video", "arguments": '{"url": "u"}'}}],
    }}]})})
    turn = backend.chat([{"role": "user", "content": "clip u"}],
                        [{"type": "function", "function": {"name": "queue_video", "parameters": {}}}])
    assert [(c.id, c.name, c.arguments) for c in turn.tool_calls] == [("call_1", "queue_video", {"url": "u"})]


# ---- failures ------------------------------------------------------------------


@pytest.mark.parametrize("status,body,kind", [
    (401, {"error": {"code": 401, "message": "No auth credentials found"}}, "invalid_key"),
    (402, {"error": {"code": 402, "message": "Insufficient credits"}}, "no_credits"),
    (404, {"error": {"message": "No endpoints found"}}, "model_unavailable"),
    (400, {"error": {"message": "bad"}}, "rejected"),
])
def test_failures_are_named_in_plain_words(fake, backend, status, body, kind):
    fake({"/chat/completions": Response(status, body)})
    with pytest.raises(LLMError) as err:
        backend.generate("p")
    assert err.value.kind == kind
    assert KEY not in err.value.message and "OpenRouter" in err.value.message


def test_a_busy_provider_is_retried_honouring_retry_after_then_reported(fake, backend):
    fake({"/chat/completions": Response(429, {"error": {"message": "slow down"}}, {"Retry-After": "7"})})
    with pytest.raises(LLMError) as err:
        backend.generate("p")
    assert err.value.kind == "rate_limited"
    assert fake.waits == [7.0, 7.0]  # three tries, two waits


def test_an_error_inside_a_200_is_still_an_error(fake, backend):
    fake({"/chat/completions": Response(200, {"error": {"code": 402, "message": "out of credit"}})})
    with pytest.raises(LLMError) as err:
        backend.generate("p")
    assert err.value.kind == "no_credits"


def test_a_network_failure_says_so(fake, backend, monkeypatch):
    import requests

    def down(*a, **k):
        raise requests.ConnectionError(f"failed to reach https://openrouter.ai?key={KEY}")

    monkeypatch.setattr(http, "transport", down)
    with pytest.raises(LLMError) as err:
        backend.generate("p")
    assert err.value.kind == "network" and KEY not in err.value.message


def test_without_a_key_nothing_is_sent(fake, tmp_path):
    transport = fake({})
    b = CloudBackend(PROVIDERS["openrouter"], "meta/muse-spark-1.3", tmp_path)
    with pytest.raises(LLMError) as err:
        b.generate("p")
    assert err.value.kind == "not_configured"
    assert transport.calls == []


def test_the_backend_never_carries_the_key(backend):
    assert KEY not in repr(backend) and KEY not in str(vars(backend))


# ---- nothing falls back ------------------------------------------------------


def test_a_cloud_model_is_never_swapped_for_a_local_one(monkeypatch):
    pytest.importorskip("numpy", reason="core.pipeline imports numpy, which CI does not install")
    from core import pipeline

    def no_ollama_lookup(*a, **k):
        raise AssertionError("a cloud model must not be checked against Ollama")

    monkeypatch.setattr("llm.manager.installed_models", no_ollama_lookup)
    cfg = {"backend": "openrouter/meta/muse-spark-1.3", "ollama_host": "http://x"}
    assert pipeline._with_usable_model(cfg) is cfg


def test_preflight_with_a_cloud_model_checks_the_key_not_ollama(tmp_path, monkeypatch):
    import requests

    from core import preflight

    def no_ollama(*a, **k):
        raise AssertionError("Ollama is not needed with a cloud model")

    monkeypatch.setattr(requests, "get", no_ollama)
    cfg = {"llm": {"backend": "openrouter/meta/muse-spark-1.3", "data_dir": str(tmp_path)},
           "paths": {"data_dir": str(tmp_path)}}
    missing = preflight.check_cloud_ai("openrouter", "meta/muse-spark-1.3", tmp_path)
    assert not missing.ok and missing.blocking and "API key" in missing.fix
    keys.save_key(tmp_path, "openrouter", KEY)
    names = {c.name: c for c in preflight.run(cfg).checks}
    assert "ollama" not in names and names["ai"].ok


# ---- keys stay secret ----------------------------------------------------------


def test_no_provider_key_ships_with_the_app():
    """Bring your own key means there is no key of ours anywhere to find."""
    import re
    import subprocess

    root = Path(__file__).resolve().parent.parent
    tracked = subprocess.run(["git", "ls-files"], cwd=root, capture_output=True, text=True).stdout.split()
    if not tracked:
        pytest.skip("not a git checkout")
    key_shaped = re.compile(r"\b(sk-(?:or-v1-|proj-|ant-)?[A-Za-z0-9_\-]{24,}|xai-[A-Za-z0-9]{24,}|AIza[0-9A-Za-z_\-]{30,})")
    found = []
    for name in tracked:
        if name.startswith(("tests/", "web/", "whop-app/")) or name.endswith((".lock", "-lock.json", ".png", ".jpg", ".ico", ".svg")):
            continue
        path = root / name
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        found += [f"{name}: {m[:12]}…" for m in key_shaped.findall(text)]
    assert found == []


@pytest.mark.parametrize("key", [
    "sk-or-v1-" + "0123456789abcdef" * 4,
    "sk-proj-" + "Ab3_-" * 12,
    "sk-ant-api03-" + "Xy7_-" * 12,
    "xai-" + "Qr9" * 20,
    "AIza" + "Sy" * 18,
    "M3ta" + "k9Zq" * 12,
])
def test_every_providers_key_format_is_scrubbed(key):
    from core.scrub import scrub_secrets
    from server.feedback import redact

    line = f"request failed with key {key} (Authorization: Bearer {key})"
    assert key not in scrub_secrets(line)
    assert key not in redact(line)


def test_a_bug_report_names_the_cloud_model_and_nothing_else(monkeypatch):
    pytest.importorskip("requests")
    from server import feedback

    def no_ollama(*a, **k):
        raise AssertionError("not asked about a cloud model")

    monkeypatch.setattr(feedback.requests, "get", no_ollama)
    info = feedback._model_info({"llm": {"backend": "openrouter/meta/muse-spark-1.3"}})
    assert info == {"model": "meta/muse-spark-1.3", "backend": "openrouter", "local": False}
