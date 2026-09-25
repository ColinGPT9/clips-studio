"""Google Gemini through generateContent.

Google's newer Interactions API is the one it recommends for new projects,
but its REST response shape is not published in enough detail to rely on
without an SDK; generateContent is fully supported and documented to the
field. Switching later is this one file.

JSON is held to a schema with responseMimeType + responseJsonSchema. In a
tool conversation the model's own content is handed back verbatim, which
carries the thought signatures Gemini needs to continue.
"""

from llm.base import ChatTurn, ToolCall
from llm.providers.adapters.chat_completions import check_key_generic
from llm.providers.base import LLMError, ModelInfo, ProviderSpec
from llm.providers.http import send

_working_format: dict[tuple[str, str], int] = {}

# The OpenAPI subset Gemini accepts for function parameters.
_SCHEMA_KEYS = {"type", "description", "properties", "items", "required", "enum", "format", "nullable"}


def generate(spec: ProviderSpec, key: str, model: str, prompt: str, *,
             json_mode: bool = False, schema: dict | None = None) -> str:
    configs: list[dict | None] = []
    if schema:
        configs.append({"responseMimeType": "application/json", "responseJsonSchema": schema})
    if json_mode or schema:
        configs.append({"responseMimeType": "application/json"})
    configs.append(None)
    start = min(_working_format.get((spec.id, model), 0), len(configs) - 1)
    last: LLMError | None = None
    for index in range(start, len(configs)):
        body: dict = {"contents": [{"role": "user", "parts": [{"text": prompt}]}]}
        if configs[index]:
            body["generationConfig"] = configs[index]
        try:
            data = send(spec, key, "POST", f"/models/{model}:generateContent", json_body=body)
        except LLMError as e:
            if configs[index] is None or e.kind not in ("rejected", "model_unavailable"):
                raise
            last = e
            continue
        _working_format[(spec.id, model)] = index
        return _text(spec, data, required=True)
    raise last or LLMError("rejected", f"{spec.label} refused every way of asking for this answer.")


def chat(spec: ProviderSpec, key: str, model: str, messages: list[dict], tools: list[dict]) -> ChatTurn:
    body: dict = {"contents": _contents(messages)}
    system = "\n\n".join(str(m.get("content") or "") for m in messages if m.get("role") == "system")
    if system:
        body["systemInstruction"] = {"parts": [{"text": system}]}
    if tools:
        body["tools"] = [{"functionDeclarations": [
            {"name": fn["name"], "description": fn.get("description", ""),
             "parameters": _clean_schema(fn.get("parameters") or {"type": "object", "properties": {}})}
            for fn in (t.get("function") or {} for t in tools)
        ]}]
    data = send(spec, key, "POST", f"/models/{model}:generateContent", json_body=body)
    content = _candidate(spec, data).get("content") or {}
    calls = []
    for part in content.get("parts") or []:
        call = part.get("functionCall") if isinstance(part, dict) else None
        if call:
            calls.append(ToolCall(id=str(call.get("id") or call.get("name") or ""),
                                  name=str(call.get("name") or ""),
                                  arguments=call.get("args") if isinstance(call.get("args"), dict) else {}))
    return ChatTurn(text=_text(spec, data), tool_calls=calls, raw=content)


def list_models(spec: ProviderSpec, key: str) -> list[ModelInfo]:
    data = send(spec, key, "GET", spec.models_path, params={"pageSize": 1000})
    models = [m for m in (spec.model_filter(e) for e in data.get("models") or [] if isinstance(e, dict)) if m]
    return sorted(models, key=lambda m: m.name.lower())


def check_key(spec: ProviderSpec, key: str) -> str:
    return check_key_generic(spec, key, list_models)


def _contents(messages: list[dict]) -> list[dict]:
    out: list[dict] = []
    for m in messages:
        role = m.get("role")
        if role == "system":
            continue
        if role == "assistant":
            if isinstance(m.get("raw"), dict) and m["raw"].get("parts"):
                out.append({"role": "model", "parts": m["raw"]["parts"]})
                continue
            parts: list[dict] = []
            if m.get("content"):
                parts.append({"text": str(m["content"])})
            for call in m.get("tool_calls") or []:
                parts.append({"functionCall": {"name": call["name"], "args": call.get("arguments") or {}}})
            out.append({"role": "model", "parts": parts or [{"text": ""}]})
        elif role == "tool":
            part = {"functionResponse": {"name": m.get("name") or m.get("tool_call_id", ""),
                                         "response": {"result": str(m.get("content") or "")}}}
            if out and out[-1]["role"] == "user" and all("functionResponse" in p for p in out[-1]["parts"]):
                out[-1]["parts"].append(part)
            else:
                out.append({"role": "user", "parts": [part]})
        else:
            out.append({"role": "user", "parts": [{"text": str(m.get("content") or "")}]})
    return out


def _clean_schema(schema):
    """Keep what Gemini's function-parameter schema understands."""
    if isinstance(schema, dict):
        cleaned = {k: _clean_schema(v) for k, v in schema.items() if k in _SCHEMA_KEYS}
        if "properties" in cleaned and isinstance(schema.get("properties"), dict):
            cleaned["properties"] = {name: _clean_schema(s) for name, s in schema["properties"].items()}
        return cleaned
    if isinstance(schema, list):
        return [_clean_schema(s) for s in schema]
    return schema


def _candidate(spec: ProviderSpec, data: dict) -> dict:
    blocked = (data.get("promptFeedback") or {}).get("blockReason")
    if blocked:
        raise LLMError("rejected", f"{spec.label} blocked the request ({blocked}).")
    candidates = data.get("candidates") or []
    if not candidates or not isinstance(candidates[0], dict):
        raise LLMError("bad_response", f"{spec.label} sent back an answer Clips Kitty couldn't read.")
    return candidates[0]


def _text(spec: ProviderSpec, data: dict, *, required: bool = False) -> str:
    candidate = _candidate(spec, data)
    parts = (candidate.get("content") or {}).get("parts") or []
    text = "".join(str(p.get("text") or "") for p in parts if isinstance(p, dict) and not p.get("thought"))
    if required and not text.strip():
        reason = candidate.get("finishReason") or ""
        if reason == "MAX_TOKENS":
            raise LLMError("bad_response", f"The {spec.label} model ran out of room before answering.")
        if reason in ("SAFETY", "PROHIBITED_CONTENT", "BLOCKLIST", "RECITATION"):
            raise LLMError("rejected", f"{spec.label} would not answer this ({reason.lower()}).")
        raise LLMError("bad_response", f"{spec.label} sent back an empty answer.")
    return text
