"""An LLMBackend that runs on the user's own key with a cloud provider."""

from llm.base import ChatTurn, LLMBackend
from llm.providers import keys
from llm.providers.adapters import adapter_for
from llm.providers.base import LLMError, ProviderSpec


class CloudBackend(LLMBackend):
    supports_schema = True

    def __init__(self, spec: ProviderSpec, model: str, data_dir):
        self.spec = spec
        self.model = model
        self.data_dir = data_dir
        self._adapter = adapter_for(spec)

    def _key(self) -> str:
        # Read at the moment of the request, never kept on the object: this
        # backend can be printed, logged or copied without carrying a secret.
        key = keys.load_key(self.data_dir, self.spec.id)
        if not key:
            raise LLMError("not_configured", f"No {self.spec.label} API key is saved. "
                                             "Add yours in Settings → AI.")
        return key

    def generate(self, prompt: str, *, json_mode: bool = False, schema: dict | None = None) -> str:
        return self._adapter.generate(self.spec, self._key(), self.model, prompt,
                                      json_mode=json_mode, schema=schema)

    def chat(self, messages: list[dict], tools: list[dict]) -> ChatTurn:
        return self._adapter.chat(self.spec, self._key(), self.model, messages, tools)

    @property
    def name(self) -> str:
        return f"{self.spec.id}/{self.model}"

    def __repr__(self) -> str:
        return f"CloudBackend({self.name})"
