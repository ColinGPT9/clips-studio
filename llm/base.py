"""LLM backend interface.

Everything outside llm/ talks to this interface only. Swapping Gemma for
Llama (or a future cloud model) means adding one backend file and changing
one line of config — no other module changes.
"""

from abc import ABC, abstractmethod


class LLMBackend(ABC):
    @abstractmethod
    def generate(self, prompt: str, *, json_mode: bool = False) -> str:
        """Run one completion. With json_mode=True the backend should ask the
        model for JSON output, but callers must still parse defensively —
        local models do not guarantee valid JSON."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Identifier for logging, e.g. 'ollama/gemma:7b'."""
