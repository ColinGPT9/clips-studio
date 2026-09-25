"""Maps a config string like 'ollama/gemma:7b' to a backend instance.

Local Ollama is the default and is handled exactly as it always was. Any
other provider is a cloud one on the user's own key, listed in
llm/providers/catalog.py; adding one there is all it takes to make it
selectable here.
"""

from llm.base import LLMBackend
from llm.ollama_backend import OllamaBackend


def create_backend(llm_config: dict) -> LLMBackend:
    spec = llm_config["backend"]
    provider, _, model = spec.partition("/")

    if provider == "ollama":
        if not model:
            raise ValueError(f"No model in LLM backend spec '{spec}' (expected e.g. 'ollama/gemma:7b')")
        return OllamaBackend(
            model=model,
            host=llm_config.get("ollama_host", "http://localhost:11434"),
            temperature=llm_config.get("temperature", 0.4),
            num_ctx=llm_config.get("num_ctx", 8192),
        )

    from llm.providers.catalog import get

    cloud = get(provider)
    if cloud is not None:
        if not model:
            raise ValueError(f"No model in LLM backend spec '{spec}' (expected e.g. '{provider}/<model>')")
        data_dir = llm_config.get("data_dir")
        if not data_dir:
            raise ValueError("No data directory to read the API key from (llm.data_dir)")
        from llm.providers.cloud_backend import CloudBackend

        return CloudBackend(cloud, model, data_dir)

    raise ValueError(f"Unknown LLM provider '{provider}' in backend spec '{spec}'")
