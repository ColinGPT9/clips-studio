"""Each wire format, against a faked transport: what is sent, and what is read.

OpenRouter, xAI and Meta share chat completions (tests/test_llm_providers.py);
OpenAI, Anthropic and Gemini each have their own format, tested here, as are
the model lists every provider is filtered through.
"""

import pytest

from llm.providers import http, keys
from llm.providers.base import LLMError
from llm.providers.catalog import PROVIDERS
from llm.providers.cloud_backend import CloudBackend

KEYS = {
    "openai": "sk-proj-" + "Ab3" * 16,
    "anthropic": "sk-ant-api03-" + "Zz9" * 16,
    "gemini": "AIza" + "Sy" * 18,
    "xai": "xai-" + "Qr9" * 20,
    "meta": "mk-" + "M3ta" * 12,
}


class Response:
    def __init__(self, status=200, body=None):
        self.status_code = status
        self._body = body if body is not None else {}
        self.headers = {}
        self.text = str(self._body)

    def json(self):
        return self._body


@pytest.fixture
def wire(monkeypatch):
    calls = []

    def install(*answers):
        queue = list(answers)

        def transport(method, url, **kw):
            calls.append({"method": method, "url": url, **kw})
            return queue.pop(0) if len(queue) > 1 else queue[0]

        monkeypatch.setattr(http, "transport", transport)
        return calls

    monkeypatch.setattr(http, "sleep", lambda s: None)
    return install


def backend(tmp_path, provider, model):
    keys.save_key(tmp_path, provider, KEYS[provider])
    return CloudBackend(PROVIDERS[provider], model, tmp_path)


# ---- OpenAI (Responses API) --------------------------------------------------


def test_openai_asks_the_responses_api_for_strict_json_and_stores_nothing(wire, tmp_path):
    calls = wire(Response(200, {"output": [
        {"type": "reasoning", "id": "rs_1"},
        {"type": "message", "content": [{"type": "output_text", "text": '{"clips": []}'}]},
    ]}))
    b = backend(tmp_path, "openai", "gpt-5.2")
    assert b.generate("p", json_mode=True, schema={"type": "object"}) == '{"clips": []}'
    call = calls[0]
    assert call["url"] == "https://api.openai.com/v1/responses"
    assert call["headers"]["Authorization"] == f"Bearer {KEYS['openai']}"
    assert call["json"]["store"] is False
    assert call["json"]["text"]["format"]["type"] == "json_schema"
    assert call["json"]["text"]["format"]["strict"] is True


def test_openai_tool_turns_hand_the_models_own_items_back(wire, tmp_path):
    first = [{"type": "reasoning", "id": "rs_1", "encrypted_content": "x"},
             {"type": "function_call", "call_id": "call_9", "name": "queue_video", "arguments": '{"url": "u"}'}]
    calls = wire(Response(200, {"output": first}),
                 Response(200, {"output": [{"type": "message", "content": [{"type": "output_text", "text": "Queued."}]}]}))
    b = backend(tmp_path, "openai", "gpt-5.2")
    tools = [{"type": "function", "function": {"name": "queue_video", "parameters": {"type": "object"}}}]
    turn = b.chat([{"role": "system", "content": "sys"}, {"role": "user", "content": "clip u"}], tools)
    assert [(c.id, c.name, c.arguments) for c in turn.tool_calls] == [("call_9", "queue_video", {"url": "u"})]

    history = [
        {"role": "system", "content": "sys"}, {"role": "user", "content": "clip u"},
        {"role": "assistant", "content": turn.text, "tool_calls": [vars(c) for c in turn.tool_calls], "raw": turn.raw},
        {"role": "tool", "tool_call_id": "call_9", "name": "queue_video", "content": "ok"},
    ]
    assert b.chat(history, tools).text == "Queued."
    sent = calls[1]["json"]
    assert sent["instructions"] == "sys"
    assert sent["input"][1:3] == first  # reasoning and the call, verbatim
    assert sent["input"][3] == {"type": "function_call_output", "call_id": "call_9", "output": "ok"}
    assert sent["tools"][0]["name"] == "queue_video"


def test_openai_lists_only_models_that_write_text():
    spec = PROVIDERS["openai"]
    listed = [spec.model_filter({"id": i}) for i in
              ("gpt-5.2", "o4-mini", "gpt-4o-mini-transcribe", "text-embedding-3-large", "dall-e-3", "whisper-1")]
    assert [m.id for m in listed if m] == ["gpt-5.2", "o4-mini"]


def test_openai_out_of_credit_is_not_mistaken_for_a_rate_limit(wire, tmp_path):
    wire(Response(429, {"error": {"type": "insufficient_quota", "code": "credit_balance_exhausted",
                                  "message": "You exceeded your current quota"}}))
    with pytest.raises(LLMError) as err:
        backend(tmp_path, "openai", "gpt-5.2").generate("p")
    assert err.value.kind == "no_credits"


# ---- Anthropic (Messages API) -------------------------------------------------


def test_anthropic_uses_its_own_headers_and_structured_outputs(wire, tmp_path):
    calls = wire(Response(200, {"content": [{"type": "text", "text": '{"clips": []}'}], "stop_reason": "end_turn"}))
    b = backend(tmp_path, "anthropic", "claude-sonnet-5")
    assert b.generate("p", json_mode=True, schema={"type": "object"}) == '{"clips": []}'
    call = calls[0]
    assert call["url"] == "https://api.anthropic.com/v1/messages"
    assert call["headers"]["x-api-key"] == KEYS["anthropic"]
    assert call["headers"]["anthropic-version"] == "2023-06-01"
    assert "Authorization" not in call["headers"]
    assert call["json"]["output_config"] == {"format": {"type": "json_schema", "schema": {"type": "object"}}}
    assert call["json"]["max_tokens"] > 0


def test_anthropic_tool_results_follow_their_call_in_one_user_turn(wire, tmp_path):
    calls = wire(Response(200, {"content": [{"type": "text", "text": "Done."}], "stop_reason": "end_turn"}))
    b = backend(tmp_path, "anthropic", "claude-sonnet-5")
    raw = [{"type": "tool_use", "id": "toolu_1", "name": "a", "input": {}},
           {"type": "tool_use", "id": "toolu_2", "name": "b", "input": {}}]
    b.chat([
        {"role": "system", "content": "sys"}, {"role": "user", "content": "go"},
        {"role": "assistant", "content": "", "tool_calls": [], "raw": raw},
        {"role": "tool", "tool_call_id": "toolu_1", "name": "a", "content": "1"},
        {"role": "tool", "tool_call_id": "toolu_2", "name": "b", "content": "2"},
    ], [{"type": "function", "function": {"name": "a", "parameters": {"type": "object"}}}])
    sent = calls[0]["json"]
    assert sent["system"] == "sys"
    assert sent["messages"][1] == {"role": "assistant", "content": raw}
    assert [b["tool_use_id"] for b in sent["messages"][2]["content"]] == ["toolu_1", "toolu_2"]
    assert sent["tools"][0]["input_schema"] == {"type": "object"}


def test_anthropic_overloaded_is_a_provider_outage(wire, tmp_path):
    wire(Response(529, {"type": "error", "error": {"type": "overloaded_error", "message": "Overloaded"}}))
    with pytest.raises(LLMError) as err:
        backend(tmp_path, "anthropic", "claude-sonnet-5").generate("p")
    assert err.value.kind == "provider_down"


# ---- Gemini (generateContent) --------------------------------------------------


def test_gemini_sends_the_key_in_a_header_and_asks_for_json_by_schema(wire, tmp_path):
    calls = wire(Response(200, {"candidates": [{"content": {"parts": [{"text": '{"clips": []}'}]},
                                                "finishReason": "STOP"}]}))
    b = backend(tmp_path, "gemini", "gemini-3.8-flash")
    assert b.generate("p", json_mode=True, schema={"type": "object"}) == '{"clips": []}'
    call = calls[0]
    assert call["url"] == "https://generativelanguage.googleapis.com/v1beta/models/gemini-3.8-flash:generateContent"
    assert call["headers"]["x-goog-api-key"] == KEYS["gemini"]
    assert KEYS["gemini"] not in call["url"] and not call.get("params")
    assert call["json"]["generationConfig"] == {"responseMimeType": "application/json",
                                                 "responseJsonSchema": {"type": "object"}}


def test_gemini_tool_calls_and_results(wire, tmp_path):
    model_turn = {"role": "model", "parts": [{"functionCall": {"name": "queue_video", "args": {"url": "u"}},
                                              "thoughtSignature": "sig"}]}
    calls = wire(Response(200, {"candidates": [{"content": model_turn}]}),
                 Response(200, {"candidates": [{"content": {"parts": [{"text": "Queued."}]}}]}))
    b = backend(tmp_path, "gemini", "gemini-3.8-flash")
    tools = [{"type": "function", "function": {"name": "queue_video", "parameters": {
        "type": "object", "properties": {"url": {"type": "string"}}, "additionalProperties": False}}}]
    turn = b.chat([{"role": "user", "content": "clip u"}], tools)
    assert [(c.name, c.arguments) for c in turn.tool_calls] == [("queue_video", {"url": "u"})]
    assert "additionalProperties" not in calls[0]["json"]["tools"][0]["functionDeclarations"][0]["parameters"]

    b.chat([{"role": "user", "content": "clip u"},
            {"role": "assistant", "content": "", "tool_calls": [vars(c) for c in turn.tool_calls], "raw": turn.raw},
            {"role": "tool", "tool_call_id": "queue_video", "name": "queue_video", "content": "ok"}], tools)
    contents = calls[1]["json"]["contents"]
    assert contents[1]["parts"] == model_turn["parts"]  # thought signature handed back
    assert contents[2]["parts"][0]["functionResponse"]["name"] == "queue_video"


def test_gemini_lists_text_models_by_their_short_id():
    spec = PROVIDERS["gemini"]
    entries = [
        {"name": "models/gemini-3.8-flash", "displayName": "Gemini 3.8 Flash", "inputTokenLimit": 1048576,
         "supportedGenerationMethods": ["generateContent"]},
        {"name": "models/gemini-embedding-001", "supportedGenerationMethods": ["embedContent"]},
        {"name": "models/gemini-3.5-transcribe", "supportedGenerationMethods": ["generateContent"]},
    ]
    assert [m.id for m in map(spec.model_filter, entries) if m] == ["gemini-3.8-flash"]


def test_a_safety_block_is_said_plainly(wire, tmp_path):
    wire(Response(200, {"candidates": [{"content": {"parts": []}, "finishReason": "SAFETY"}]}))
    with pytest.raises(LLMError) as err:
        backend(tmp_path, "gemini", "gemini-3.8-flash").generate("p")
    assert err.value.kind == "rejected"


# ---- xAI and Meta (chat completions, catalogue entries only) ----------------------


def test_xai_and_meta_are_chat_completions_at_their_own_addresses(wire, tmp_path):
    calls = wire(Response(200, {"choices": [{"message": {"content": "{}"}}]}))
    backend(tmp_path, "xai", "grok-4.7").generate("p")
    backend(tmp_path, "meta", "muse-spark-1.3").generate("p")
    assert calls[0]["url"] == "https://api.x.ai/v1/chat/completions"
    assert calls[1]["url"] == "https://api.meta.ai/v1/chat/completions"
    assert "X-OpenRouter-Title" not in calls[0]["headers"]  # attribution is OpenRouter's alone


def test_a_blocked_xai_key_fails_the_check(wire):
    wire(Response(200, {"api_key_blocked": True}))
    spec = PROVIDERS["xai"]
    from llm.providers.adapters import adapter_for

    with pytest.raises(LLMError) as err:
        adapter_for(spec).check_key(spec, KEYS["xai"])
    assert err.value.kind == "invalid_key"


def test_meta_offers_muse_spark_and_warns_about_contributor_models():
    spec = PROVIDERS["meta"]
    listed = [spec.model_filter({"id": i}) for i in
              ("muse-spark-1.3", "muse-spark-1.3-contributor", "muse-image-1.0", "muse-voice-transcribe-1.0")]
    kept = [m for m in listed if m]
    assert [m.id for m in kept] == ["muse-spark-1.3", "muse-spark-1.3-contributor"]
    assert kept[0].note == "" and "improve" in kept[1].note


def test_the_catalogue_is_in_the_order_the_settings_list_it():
    assert list(PROVIDERS) == ["openrouter", "openai", "anthropic", "gemini", "xai", "meta",
                               "deepseek", "qwen"]


def test_openrouter_is_the_recommended_cloud_and_the_rest_are_direct():
    tiers = {spec.id: spec.tier for spec in PROVIDERS.values()}
    openrouter_tier = tiers.pop("openrouter")
    assert openrouter_tier == 2
    assert set(tiers.values()) == {3}  # every direct provider, all still offered
    assert PROVIDERS["openrouter"].public()["recommended"] is True
    assert not any(PROVIDERS[p].public()["recommended"] for p in tiers)
    assert all(spec.tagline for spec in PROVIDERS.values())
