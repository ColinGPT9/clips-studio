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


def _deepseek_model(entry: dict) -> ModelInfo | None:
    model_id = str(entry.get("id") or "")
    if not model_id or "text" not in (entry.get("output_modalities") or ["text"]):
        return None
    # JSON mode is json_object only (no strict schema); the adapter falls back to it.
    return ModelInfo(id=model_id, name=str(entry.get("name") or model_id),
                     context=int(entry.get("context_window") or 0), tools=True)


# Model Studio lists every model the key can reach, including image, speech,
# vision and embedding ones; only the text chat models can pick clips.
_QWEN_NOT_TEXT = ("vl", "audio", "asr", "tts", "omni", "image", "embedding", "rerank",
                  "ocr", "realtime", "livetranslate", "wan", "mt-")


def _qwen_model(entry: dict) -> ModelInfo | None:
    model_id = str(entry.get("id") or "")
    lowered = model_id.lower()
    if not lowered.startswith(("qwen", "qwq")):
        return None  # Model Studio also resells other makers' models; Qwen is what this entry is for
    if any(part in lowered for part in _QWEN_NOT_TEXT):
        return None
    return ModelInfo(id=model_id, name=model_id, tools=True)


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
    tagline="Direct xAI API.",
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
    tagline="Direct Meta Model API (Muse Spark).",
)

DEEPSEEK = ProviderSpec(
    id="deepseek",
    label="DeepSeek",
    adapter="chat_completions",
    base_url="https://api.deepseek.com",
    key_label="DeepSeek API key",
    key_url="https://platform.deepseek.com/api_keys",
    pricing_url="https://api-docs.deepseek.com/quick_start/pricing",
    privacy="Transcripts and prompts are sent to DeepSeek, a company based in China, with your key.",
    model_filter=_deepseek_model,
    tagline="Direct DeepSeek API.",
)

# Alibaba Cloud Model Studio. A key only works in the region it was made in,
# so a new key is tried in each of these until one accepts it. Keys from the
# Frankfurt and Tokyo regions need a workspace-specific address and aren't
# supported yet.
QWEN = ProviderSpec(
    id="qwen",
    label="Qwen (Alibaba Cloud)",
    adapter="chat_completions",
    base_url="https://dashscope-intl.aliyuncs.com/compatible-mode/v1",
    key_label="Alibaba Cloud Model Studio API key",
    key_url="https://www.alibabacloud.com/help/en/model-studio/get-api-key",
    pricing_url="https://www.alibabacloud.com/help/en/model-studio/model-pricing",
    privacy="Transcripts and prompts are sent to Alibaba Cloud Model Studio, in the region your key "
            "belongs to, with your key.",
    model_filter=_qwen_model,
    regions=(
        ("intl", "Singapore", "https://dashscope-intl.aliyuncs.com/compatible-mode/v1"),
        ("us", "US (Virginia)", "https://dashscope-us.aliyuncs.com/compatible-mode/v1"),
        ("cn", "China (Beijing)", "https://dashscope.aliyuncs.com/compatible-mode/v1"),
        ("hk", "China (Hong Kong)", "https://cn-hongkong.dashscope.aliyuncs.com/compatible-mode/v1"),
    ),
    tagline="Direct Qwen API, through Alibaba Cloud Model Studio.",
)

# OpenRouter first: the recommended cloud path (tier 2). The direct provider
# APIs follow (tier 3), always available, offered as the advanced option.
_ORDER: tuple[ProviderSpec, ...] = (
    openrouter.SPEC,
    openai.SPEC,
    anthropic.SPEC,
    gemini.SPEC,
    XAI,
    META,
    DEEPSEEK,
    QWEN,
)

PROVIDERS: dict[str, ProviderSpec] = {spec.id: spec for spec in _ORDER}


def get(provider_id: str) -> ProviderSpec | None:
    return PROVIDERS.get(provider_id)
