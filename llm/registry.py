"""Maps a config string like 'ollama/gemma:7b' to a backend instance.

To add a new provider later (llama.cpp, Gemini, Anthropic, ...), add one
backend file implementing LLMBackend and one branch here.
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

    raise ValueError(f"Unknown LLM provider '{provider}' in backend spec '{spec}'")
