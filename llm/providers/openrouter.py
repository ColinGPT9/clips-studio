"""OpenRouter: one key, many providers' models, billed to the user's account.

Every request OpenRouter gets from this app carries the app's attribution
headers, which is how its usage is credited to Clips Kitty in OpenRouter's app
rankings. They are the app's identity, not a user setting, and they are built
here and nowhere else: SPEC.extra_headers hands them to llm/providers/http.py,
which puts them on every request (chat, models, key check, transcription and
every retry). `video-gen` is the category in OpenRouter's Creative section
that fits; a bare "creative" is not a category and would be dropped.

The website (web/lib/openrouter.ts) sends its own set, with its own origin and
the title "Clips Kitty Web", from the visitor's browser.
"""

from llm.providers.base import ModelInfo, ProviderSpec

APP_URL = "https://colingpt9.github.io/clips-studio/"
APP_TITLE = "Clips Kitty"
APP_CATEGORY = "video-gen"


def attribution_headers() -> dict:
    return {
        "HTTP-Referer": APP_URL,
        "X-OpenRouter-Title": APP_TITLE,
        "X-OpenRouter-Categories": APP_CATEGORY,
    }


def _route_to_capable_providers(body: dict) -> dict:
    """With a response format set, only route to providers that honour it
    (OpenRouter's own advice for structured output); otherwise the request
    could land on one that ignores the format and returns prose."""
    if "response_format" in body:
        body = {**body, "provider": {**body.get("provider", {}), "require_parameters": True}}
    return body


def model_info(entry: dict) -> ModelInfo | None:
    """A model worth offering: text out, and able to answer in JSON."""
    model_id = str(entry.get("id") or "")
    arch = entry.get("architecture") or {}
    outputs = arch.get("output_modalities") or []
    if not model_id or (outputs and "text" not in outputs):
        return None
    params = set(entry.get("supported_parameters") or [])
    if not params & {"response_format", "structured_outputs"}:
        return None
    note = ""
    if model_id.endswith(":free"):
        note = "Free, with a small daily request limit; a long video can use it up."
    elif "contributor" in model_id:
        note = "Cheaper, but the provider may use what you send to improve its products."
    return ModelInfo(
        id=model_id,
        name=str(entry.get("name") or model_id),
        context=int(entry.get("context_length") or 0),
        json_schema="structured_outputs" in params,
        tools="tools" in params,
        note=note,
        vendor=model_id.split("/", 1)[0] if "/" in model_id else "",
        price=_price(entry.get("pricing") or {}),
    )


def _price(pricing: dict) -> tuple[float, float] | None:
    """OpenRouter lists USD per token as strings; shown per million tokens.
    A negative price means "varies" (a router model), which is not a price."""
    try:
        per_token = float(pricing["prompt"]), float(pricing["completion"])
    except (KeyError, TypeError, ValueError):
        return None
    if min(per_token) < 0:
        return None
    return round(per_token[0] * 1_000_000, 4), round(per_token[1] * 1_000_000, 4)


SPEC = ProviderSpec(
    id="openrouter",
    label="OpenRouter",
    adapter="chat_completions",
    base_url="https://openrouter.ai/api/v1",
    key_label="OpenRouter API key",
    key_url="https://openrouter.ai/keys",
    pricing_url="https://openrouter.ai/models",
    privacy="Transcripts and prompts (and audio, if it transcribes) go to OpenRouter, and on to the "
            "model's provider, with your key.",
    extra_headers=attribution_headers,
    body_extras=_route_to_capable_providers,
    model_filter=model_info,
    key_check_path="/key",
    # The recommended cloud path: one key reaches many models and providers.
    tier=2,
    tagline="One API key for many AI models and providers.",
    # Whisper through OpenRouter, with word timings. Short parts: OpenRouter's
    # upstream providers stop after about a minute of processing a request.
    stt={"format": "openrouter", "chunk_seconds": 180,
         "models": ["openai/whisper-large-v3-turbo", "openai/whisper-large-v3", "openai/whisper-1"]},
)
