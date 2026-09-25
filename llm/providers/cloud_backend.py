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

    def _keyed(self) -> tuple[ProviderSpec, str]:
        # Read at the moment of the request, never kept on the object: this
        # backend can be printed, logged or copied without carrying a secret.
        # The spec comes back at the address of the key's region, if it has one.
        spec, key = keys.resolve(self.data_dir, self.spec)
        if not key:
            raise LLMError("not_configured", f"No {self.spec.label} API key is saved. "
                                             "Add yours in Settings → AI.")
        return spec, key

    def generate(self, prompt: str, *, json_mode: bool = False, schema: dict | None = None) -> str:
        spec, key = self._keyed()
        return self._adapter.generate(spec, key, self.model, prompt,
                                      json_mode=json_mode, schema=schema)

    def chat(self, messages: list[dict], tools: list[dict]) -> ChatTurn:
        spec, key = self._keyed()
        return self._adapter.chat(spec, key, self.model, messages, tools)

    @property
    def name(self) -> str:
        return f"{self.spec.id}/{self.model}"

    def __repr__(self) -> str:
        return f"CloudBackend({self.name})"
