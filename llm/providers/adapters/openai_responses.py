"""OpenAI's Responses API (/v1/responses), which OpenAI recommends for new work.

Nothing is stored at OpenAI (`store: false`). In a tool conversation the
model's own output items are handed back verbatim on the next turn, reasoning
included (as encrypted content), which is what the API needs when it is not
keeping the conversation itself.
"""

import json

from llm.base import ChatTurn, ToolCall
from llm.providers.adapters.chat_completions import _arguments, check_key_generic
from llm.providers.base import LLMError, ModelInfo, ProviderSpec
from llm.providers.http import send

_working_format: dict[tuple[str, str], int] = {}


def generate(spec: ProviderSpec, key: str, model: str, prompt: str, *,
             json_mode: bool = False, schema: dict | None = None) -> str:
    formats: list[dict | None] = []
    if schema:
        formats.append({"type": "json_schema", "name": "clips_kitty", "schema": schema, "strict": True})
    if json_mode or schema:
        formats.append({"type": "json_object"})
    formats.append(None)
    start = min(_working_format.get((spec.id, model), 0), len(formats) - 1)
    last: LLMError | None = None
    for index in range(start, len(formats)):
        body: dict = {"model": model, "input": prompt, "store": False}
        if formats[index]:
            body["text"] = {"format": formats[index]}
        try:
            data = send(spec, key, "POST", "/responses", json_body=body)
        except LLMError as e:
            if formats[index] is None or e.kind not in ("rejected", "model_unavailable"):
                raise
            last = e
            continue
        _working_format[(spec.id, model)] = index
        text = _output_text(data)
        if not text.strip():
            raise _empty(spec, data)
        return text
    raise last or LLMError("rejected", f"{spec.label} refused every way of asking for this answer.")


def chat(spec: ProviderSpec, key: str, model: str, messages: list[dict], tools: list[dict]) -> ChatTurn:
    body: dict = {
        "model": model,
        "input": _items(messages),
        "store": False,
        "include": ["reasoning.encrypted_content"],
    }
    instructions = "\n\n".join(str(m.get("content") or "") for m in messages if m.get("role") == "system")
    if instructions:
        body["instructions"] = instructions
    if tools:
        body["tools"] = [
            {"type": "function", "name": fn["name"], "description": fn.get("description", ""),
             "parameters": fn.get("parameters") or {"type": "object", "properties": {}}, "strict": False}
            for fn in (t.get("function") or {} for t in tools)
        ]
    data = send(spec, key, "POST", "/responses", json_body=body)
    output = data.get("output") or []
    calls = [
        ToolCall(id=str(item.get("call_id") or item.get("id") or ""), name=str(item.get("name") or ""),
                 arguments=_arguments(item.get("arguments")))
        for item in output if isinstance(item, dict) and item.get("type") == "function_call"
    ]
    return ChatTurn(text=_output_text(data), tool_calls=calls, raw=output)


def list_models(spec: ProviderSpec, key: str) -> list[ModelInfo]:
    data = send(spec, key, "GET", spec.models_path)
    models = [m for m in (spec.model_filter(e) for e in data.get("data") or [] if isinstance(e, dict)) if m]
    return sorted(models, key=lambda m: m.name.lower())


def check_key(spec: ProviderSpec, key: str) -> str:
    return check_key_generic(spec, key, list_models)


def _items(messages: list[dict]) -> list[dict]:
    items: list[dict] = []
    for m in messages:
        role = m.get("role")
        if role == "system":
            continue  # sent as `instructions`
        if role == "assistant":
            if isinstance(m.get("raw"), list):
                items.extend(m["raw"])  # the model's own items, reasoning and calls included
                continue
            if m.get("content"):
                items.append({"role": "assistant", "content": str(m["content"])})
            for call in m.get("tool_calls") or []:
                items.append({"type": "function_call", "call_id": call["id"], "name": call["name"],
                              "arguments": json.dumps(call.get("arguments") or {})})
        elif role == "tool":
            items.append({"type": "function_call_output", "call_id": m.get("tool_call_id", ""),
                          "output": str(m.get("content") or "")})
        else:
            items.append({"role": "user", "content": str(m.get("content") or "")})
    return items


def _output_text(data: dict) -> str:
    parts = []
    for item in data.get("output") or []:
        if isinstance(item, dict) and item.get("type") == "message":
            for piece in item.get("content") or []:
                if isinstance(piece, dict) and piece.get("type") == "output_text":
                    parts.append(str(piece.get("text") or ""))
    return "".join(parts)


def _empty(spec: ProviderSpec, data: dict) -> LLMError:
    reason = (data.get("incomplete_details") or {}).get("reason")
    if reason == "max_output_tokens":
        return LLMError("bad_response", f"The {spec.label} model ran out of room before answering. "
                                        "A model with a larger output limit will do better.")
    return LLMError("bad_response", f"{spec.label} sent back an empty answer.")
