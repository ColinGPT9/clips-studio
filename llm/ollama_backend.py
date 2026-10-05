"""Ollama backend — serves Gemma, Llama, and any other model Ollama hosts."""

import base64
import time

import requests

from llm.base import LLMBackend
from llm.manager import RECOMMENDATIONS

# The models setup installs that cannot think, run against real streams with the
# request below exactly as it is. Handling reasoning models must not change what
# these are sent, so they are excluded by name rather than by what Ollama reports.
#
# Gemma 4 (e2b, e4b) used to be excluded too, as working as it was. It did not:
# on Ollama 0.34 its thinking ran into JSON mode and the answer came back empty
# or cut off mid-object. On a real 13 minute stream, three of four chunks gave
# nothing and scoring took ten minutes for one candidate; with thinking off, as
# every other Gemma 4 size already was, the same chunks gave 8 in about a minute.
_SETUP_MODELS = frozenset(tag for _hardware, tag, _note in RECOMMENDATIONS
                          if not tag.startswith("gemma4"))

# Reasoning models that have to keep thinking to do the job, and at what level.
# Both were run on a real stream transcript: with reasoning off (or gpt-oss at
# "low") they answered an empty clip list in a handful of tokens, on a stretch
# where gemma:7b found ten clips.
_THINK_TO_ANSWER = {"gpt-oss": "medium", "nemotron": True}

# Reasoning models that answer without thinking, but not in JSON mode. Gemma 4
# held to `format` repeats itself until Ollama aborts the request ("prediction
# aborted, token repeat limit reached", a 500 that fails the job): three runs of
# three on a real stream. Without it, the same chunks gave 16-17 candidates in
# about 50 s every time. The prompts already ask for the JSON, and every caller
# finds it in plain text.
_ANSWER_WITHOUT_FORMAT = ("gemma4",)

# gemma3:4b held to `format` sometimes does the same: on an NBA game (Ollama
# 0.35.1) that 500 ended six runs in a day, the last five in a row, six to eight
# minutes in, reading the transcript's chunks or scoring windows. A request
# Ollama answers with a server error is asked again, without `format` from the
# second try, and an answer Ollama still cuts off comes back empty, as an
# answer that can't be read, which every caller already gets past.
_TRIES = 3
_PAUSE = 2.0        # seconds before the second try, twice that before the third
_CUT_OFF = "prediction aborted"     # Ollama's 500 for an answer that repeats itself


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
        return self._answer(payload)

    def sees_images(self) -> bool:
        """Gemma 3 and Gemma 4 can (Ollama says "vision"); gemma:7b can't."""
        return "vision" in self._capabilities()

    def look(self, prompt: str, images: list[bytes]) -> str:
        """The frames go to the local model with the prompt: nothing leaves
        the PC."""
        payload = {
            "model": self.model,
            "prompt": prompt,
            "images": [base64.b64encode(img).decode("ascii") for img in images],
            "stream": False,
            "format": "json",
            "options": {"temperature": 0.2, "num_ctx": self.num_ctx, "num_predict": 512},
        }
        # Gemma 4 can think, and a thinking model in JSON mode answers
        # nothing at all (see _limit_reasoning); this question doesn't need it.
        if "thinking" in self._capabilities():
            payload["think"] = False
        return self._answer(payload)

    def _answer(self, payload: dict) -> str:
        """The model's answer to `payload`. A request Ollama answers with a
        server error is asked again, _TRIES times in all and without `format`
        from the second (the prompts ask for JSON themselves). An answer Ollama
        still cuts off for repeating itself is "": one the caller can't read.
        Any other error ends the job, as it always has."""
        for attempt in range(1, _TRIES + 1):
            response = requests.post(f"{self.host}/api/generate", json=payload, timeout=self.timeout)
            if response.status_code < 500:
                break
            said = _error(response)
            if attempt == _TRIES:
                if _CUT_OFF in said:
                    print(f"      (Ollama cut the answer off {_TRIES} times ({said}); going on without it)")
                    return ""
                break
            print(f"      (Ollama answered {response.status_code}: {said}; asking again"
                  + (" without JSON mode)" if "format" in payload else ")"))
            payload = {k: v for k, v in payload.items() if k != "format"}
            time.sleep(_PAUSE * attempt)
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
            if self.model.startswith(_ANSWER_WITHOUT_FORMAT):
                payload.pop("format", None)
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


def _error(response) -> str:
    """What Ollama said went wrong ({"error": "..."}), or the start of its reply."""
    try:
        said = response.json().get("error")
    except Exception:
        said = None
    return str(said or getattr(response, "text", "") or "no reason given")[:200]
