"""Anthropic's Messages API (/v1/messages) for Claude.

JSON comes from structured outputs (`output_config.format`): current Claude
models refuse a pre-filled answer, so asking for JSON in the prompt alone is
the fallback, not the plan. `max_tokens` is required by this API.
"""

from llm.base import ChatTurn, ToolCall
from llm.providers.adapters.chat_completions import check_key_generic
from llm.providers.base import LLMError, ModelInfo, ProviderSpec
from llm.providers.http import send

GENERATE_MAX_TOKENS = 8192
CHAT_MAX_TOKENS = 4096

_working_format: dict[tuple[str, str], int] = {}


def generate(spec: ProviderSpec, key: str, model: str, prompt: str, *,
             json_mode: bool = False, schema: dict | None = None) -> str:
    formats: list[dict | None] = [{"type": "json_schema", "schema": schema}] if schema else []
    formats.append(None)
    start = min(_working_format.get((spec.id, model), 0), len(formats) - 1)
    last: LLMError | None = None
    for index in range(start, len(formats)):
        body: dict = {"model": model, "max_tokens": GENERATE_MAX_TOKENS,
                      "messages": [{"role": "user", "content": prompt}]}
        if formats[index]:
            body["output_config"] = {"format": formats[index]}
        try:
            data = send(spec, key, "POST", "/messages", json_body=body)
        except LLMError as e:
            if formats[index] is None or e.kind not in ("rejected", "model_unavailable"):
                raise
            last = e
            continue
        _working_format[(spec.id, model)] = index
        text = _text(data)
        if not text.strip():
            if data.get("stop_reason") == "max_tokens":
                raise LLMError("bad_response", f"The {spec.label} model ran out of room before answering.")
            raise LLMError("bad_response", f"{spec.label} sent back an empty answer.")
        return text
    raise last or LLMError("rejected", f"{spec.label} refused every way of asking for this answer.")


def chat(spec: ProviderSpec, key: str, model: str, messages: list[dict], tools: list[dict]) -> ChatTurn:
    body: dict = {"model": model, "max_tokens": CHAT_MAX_TOKENS, "messages": _messages(messages)}
    system = "\n\n".join(str(m.get("content") or "") for m in messages if m.get("role") == "system")
    if system:
        body["system"] = system
    if tools:
        body["tools"] = [
            {"name": fn["name"], "description": fn.get("description", ""),
             "input_schema": fn.get("parameters") or {"type": "object", "properties": {}}}
            for fn in (t.get("function") or {} for t in tools)
        ]
    data = send(spec, key, "POST", "/messages", json_body=body)
    content = data.get("content") or []
    calls = [
        ToolCall(id=str(block.get("id") or ""), name=str(block.get("name") or ""),
                 arguments=block.get("input") if isinstance(block.get("input"), dict) else {})
        for block in content if isinstance(block, dict) and block.get("type") == "tool_use"
    ]
    return ChatTurn(text=_text(data), tool_calls=calls, raw=content)


def list_models(spec: ProviderSpec, key: str) -> list[ModelInfo]:
    data = send(spec, key, "GET", spec.models_path, params={"limit": 1000})
    models = [m for m in (spec.model_filter(e) for e in data.get("data") or [] if isinstance(e, dict)) if m]
    return sorted(models, key=lambda m: m.name.lower())


def check_key(spec: ProviderSpec, key: str) -> str:
    return check_key_generic(spec, key, list_models)


def _messages(messages: list[dict]) -> list[dict]:
    """Claude wants every tool result straight after the call, all of one
    turn's results in a single user message, results first in its content."""
    out: list[dict] = []
    for m in messages:
        role = m.get("role")
        if role == "system":
            continue
        if role == "assistant":
            if isinstance(m.get("raw"), list):
                out.append({"role": "assistant", "content": m["raw"]})
                continue
            blocks: list[dict] = []
            if m.get("content"):
                blocks.append({"type": "text", "text": str(m["content"])})
            for call in m.get("tool_calls") or []:
                blocks.append({"type": "tool_use", "id": call["id"], "name": call["name"],
                               "input": call.get("arguments") or {}})
            out.append({"role": "assistant", "content": blocks or ""})
        elif role == "tool":
            result = {"type": "tool_result", "tool_use_id": m.get("tool_call_id", ""),
                      "content": str(m.get("content") or "")}
            if out and out[-1]["role"] == "user" and isinstance(out[-1]["content"], list) \
                    and all(b.get("type") == "tool_result" for b in out[-1]["content"]):
                out[-1]["content"].append(result)
            else:
                out.append({"role": "user", "content": [result]})
        else:
            out.append({"role": "user", "content": str(m.get("content") or "")})
    return out


def _text(data: dict) -> str:
    return "".join(str(b.get("text") or "") for b in data.get("content") or []
                   if isinstance(b, dict) and b.get("type") == "text")
