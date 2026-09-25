"""Every cloud provider the app can use, in the order the UI lists them.

Local (Ollama and Whisper on this PC) is not in here: it is the default, and
always listed first, ahead of all of these.

To add a provider that speaks OpenAI-compatible chat completions, add one
ProviderSpec to _ORDER, the way xAI and Meta are below. The API, the settings
card and the pipeline all read PROVIDERS; nothing else needs to change. A
provider with its own wire format adds one file in adapters/ as well.

Llama is not a separate entry: Meta's own API serves Muse Spark, and Llama 4
is reached through OpenRouter (meta-llama/...), on the same OpenRouter key.
"""

from llm.providers import anthropic, gemini, openai, openrouter
from llm.providers.base import ModelInfo, ProviderSpec


def _xai_model(entry: dict) -> ModelInfo | None:
    model_id = str(entry.get("id") or "")
    if not model_id or "text" not in (entry.get("output_modalities") or ["text"]):
        return None
    return ModelInfo(id=model_id, name=model_id, json_schema=True, tools=True)


def _xai_key_ok(body: dict) -> bool:
    return not any(body.get(flag) for flag in ("api_key_blocked", "api_key_disabled", "team_blocked"))


def _meta_model(entry: dict) -> ModelInfo | None:
    model_id = str(entry.get("id") or "")
    if not model_id.startswith("muse-spark"):
        return None  # image, voice and segmentation models cannot do this job
    note = ("Cheaper, but Meta may use what you send to improve its products."
            if "contributor" in model_id else "")
    return ModelInfo(id=model_id, name=model_id, json_schema=True, tools=True, note=note)


XAI = ProviderSpec(
    id="xai",
    label="xAI Grok",
    adapter="chat_completions",
    base_url="https://api.x.ai/v1",
    key_label="xAI API key",
    key_url="https://console.x.ai/",
    pricing_url="https://docs.x.ai/docs/models",
    privacy="Transcripts and prompts (and audio, if it transcribes) are sent to xAI with your key.",
    models_path="/language-models",
    model_filter=_xai_model,
    key_check_path="/api-key",
    key_check_ok=_xai_key_ok,
    stt={"format": "xai", "models": ["grok-voice-transcribe-2.0"], "chunk_seconds": 1200},
)

META = ProviderSpec(
    id="meta",
    label="Meta (Muse Spark)",
    adapter="chat_completions",
    base_url="https://api.meta.ai/v1",
    key_label="Meta Model API key",
    key_url="https://dev.meta.ai/",
    pricing_url="https://dev.meta.ai/docs/pricing-rate-limits",
    privacy="Transcripts and prompts are sent to Meta with your key.",
    model_filter=_meta_model,
)

_ORDER: tuple[ProviderSpec, ...] = (
    openrouter.SPEC,
    openai.SPEC,
    gemini.SPEC,
    anthropic.SPEC,
    XAI,
    META,
)

PROVIDERS: dict[str, ProviderSpec] = {spec.id: spec for spec in _ORDER}


def get(provider_id: str) -> ProviderSpec | None:
    return PROVIDERS.get(provider_id)
