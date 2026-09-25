"""Every cloud provider the app can use, in the order the UI lists them.

Local (Ollama and Whisper on this PC) is not in here: it is the default, and
always listed first, ahead of all of these.

To add a provider that speaks OpenAI-compatible chat completions, add one
ProviderSpec to this tuple. The API, the settings card and the pipeline all
read PROVIDERS; nothing else needs to change.
"""

from llm.providers import openrouter
from llm.providers.base import ProviderSpec

_ORDER: tuple[ProviderSpec, ...] = (
    openrouter.SPEC,
)

PROVIDERS: dict[str, ProviderSpec] = {spec.id: spec for spec in _ORDER}


def get(provider_id: str) -> ProviderSpec | None:
    return PROVIDERS.get(provider_id)
