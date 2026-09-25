"""One module per wire format. Each exposes the same four functions:

    generate(spec, key, model, prompt, *, json_mode, schema) -> str
    chat(spec, key, model, messages, tools) -> ChatTurn
    list_models(spec, key) -> list[ModelInfo]
    check_key(spec, key) -> str        # a short "it works" detail

and makes every request through llm/providers/http.send.
"""

from importlib import import_module


def adapter_for(spec):
    return import_module(f"llm.providers.adapters.{spec.adapter}")
