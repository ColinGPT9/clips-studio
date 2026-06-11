"""Ollama backend — serves Gemma, Llama, and any other model Ollama hosts."""

import requests

from llm.base import LLMBackend


class OllamaBackend(LLMBackend):
    def __init__(
        self,
        model: str,
        host: str = "http://localhost:11434",
        temperature: float = 0.4,
        num_ctx: int = 8192,
        timeout: int = 600,
    ):
        self.model = model
        self.host = host.rstrip("/")
        self.temperature = temperature
        # Ollama's default context is tiny (2-4K) and it silently truncates
        # longer prompts — fatal for transcript analysis. Set it explicitly.
        self.num_ctx = num_ctx
        self.timeout = timeout

    def generate(self, prompt: str, *, json_mode: bool = False) -> str:
        payload = {
            "model": self.model,
            "prompt": prompt,
            "stream": False,
            "options": {
                "temperature": self.temperature,
                "num_ctx": self.num_ctx,
                "num_predict": 1024,  # explicit output budget; defaults can starve JSON mid-object
            },
        }
        if json_mode:
            payload["format"] = "json"

        response = requests.post(
            f"{self.host}/api/generate", json=payload, timeout=self.timeout
        )
        response.raise_for_status()
        return response.json()["response"]

    @property
    def name(self) -> str:
        return f"ollama/{self.model}"
