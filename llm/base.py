"""LLM backend interface.

Everything outside llm/ talks to this interface only. Swapping Gemma for
Llama, or a local model for a cloud one on the user's own key, is a config
change: no other module changes.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict


@dataclass
class ChatTurn:
    """One reply in a tool-using conversation: its text, the tools it asked
    for, and the provider's own record of the turn (`raw`), which some
    providers need handed back verbatim on the next request."""

    text: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    raw: object = None


class LLMBackend(ABC):
    # Whether generate() accepts a JSON schema to hold the answer to. Only a
    # backend that can enforce one says so; everything else is called exactly
    # as it always was (see generate_json below).
    supports_schema = False

    @abstractmethod
    def generate(self, prompt: str, *, json_mode: bool = False) -> str:
        """Run one completion. With json_mode=True the backend should ask the
        model for JSON output, but callers must still parse defensively —
        local models do not guarantee valid JSON."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Identifier for logging, e.g. 'ollama/gemma:7b'."""

    def chat(self, messages: list[dict], tools: list[dict]) -> ChatTurn:
        """One turn of a conversation that can call tools.

        `messages` are {"role": "system"|"user"|"assistant"|"tool", ...}: an
        assistant turn may carry "tool_calls" [{id, name, arguments}] and
        "raw", a tool result carries "tool_call_id" and "name". `tools` are
        {"type": "function", "function": {name, description, parameters}}.
        """
        raise NotImplementedError(f"{self.name} does not run tool conversations")


def generate_json(llm: LLMBackend, prompt: str, schema: dict) -> str:
    """Ask for JSON, holding the answer to `schema` where the backend can.

    The schema is only a guarantee on top of the prompt, which already asks
    for the same shape, and the caller still parses defensively. A backend
    that does not take one (Ollama, and every test fake) gets the call it has
    always had, so local output does not change.
    """
    if getattr(llm, "supports_schema", False):
        return llm.generate(prompt, json_mode=True, schema=schema)
    return llm.generate(prompt, json_mode=True)
