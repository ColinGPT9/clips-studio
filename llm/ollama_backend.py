"""Ollama backend — serves Gemma, Llama, and any other model Ollama hosts."""

import requests

from llm.base import LLMBackend
from llm.manager import RECOMMENDATIONS

# The models setup installs, which have been run against real streams with the
# request below exactly as it is. Handling reasoning models must not change what
# these are sent, so they are excluded by name rather than by what Ollama
# reports: gemma4:e2b and e4b can think, and they work as they are.
_SETUP_MODELS = frozenset(tag for _hardware, tag, _note in RECOMMENDATIONS)

# Reasoning models that have to keep thinking to do the job, and at what level.
# Both were run on a real stream transcript: with reasoning off (or gpt-oss at
# "low") they answered an empty clip list in a handful of tokens, on a stretch
# where gemma:7b found ten clips.
_THINK_TO_ANSWER = {"gpt-oss": "medium", "nemotron": True}


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
        self._capabilities_cache: list[str] | None = None

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
        self._limit_reasoning(payload)

        response = requests.post(
            f"{self.host}/api/generate", json=payload, timeout=self.timeout
        )
        response.raise_for_status()
        return response.json()["response"]

    def _limit_reasoning(self, payload: dict) -> None:
        """Give a reasoning model a request it can actually answer.

        Ollama turns thinking on by default for models that support it, and on
        /api/generate that fails two ways. The reasoning counts against
        num_predict, so a long think leaves the answer cut off or empty. And a
        `format` constraint is applied while the model is still thinking, which
        returns an empty answer outright (ollama/ollama#11691; the fix, #14288,
        is not released). Either way the chunk is dropped with no error.

        So most reasoning models (DeepSeek-R1) answer without thinking, which
        keeps JSON mode working. The ones in _THINK_TO_ANSWER give up without
        reasoning, so they keep it, get room for it, and lose `format`: every
        caller already finds the JSON object in free text. A model that cannot
        think, and a model Ollama cannot describe, is sent exactly what it was
        always sent.
        """
        if self.model in _SETUP_MODELS or "thinking" not in self._capabilities():
            return
        level = next(
            (lvl for prefix, lvl in _THINK_TO_ANSWER.items() if self.model.startswith(prefix)),
            None,
        )
        if level is None:
            payload["think"] = False
            return
        payload["think"] = level
        payload["options"]["num_predict"] = 6144  # gpt-oss used ~3,300 on one chunk
        payload.pop("format", None)

    def _capabilities(self) -> list[str]:
        """What Ollama says this model can do, asked once per backend.

        A failed lookup is not remembered and reads as "nothing special", so an
        unreachable Ollama leaves the request unchanged.
        """
        if self._capabilities_cache is None:
            try:
                response = requests.post(
                    f"{self.host}/api/show", json={"model": self.model}, timeout=15
                )
                response.raise_for_status()
                self._capabilities_cache = list(response.json().get("capabilities") or [])
            except Exception:
                return []
        return self._capabilities_cache

    @property
    def name(self) -> str:
        return f"ollama/{self.model}"
