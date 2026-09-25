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

SITE = "https://openrouter.ai"
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
    pricing = entry.get("pricing") or {}
    lines = _token_lines(pricing)
    return ModelInfo(
        id=model_id,
        name=str(entry.get("name") or model_id),
        context=int(entry.get("context_length") or 0),
        json_schema="structured_outputs" in params,
        tools="tools" in params,
        note=note,
        vendor=model_id.split("/", 1)[0] if "/" in model_id else "",
        price=_price(pricing),
        pricing=lines,
        free=_is_free(pricing),
        page_url=f"{SITE}/{model_id}",
    )


# The fields OpenRouter prices a text model by, as it names them, and how each
# is shown. Only those it actually lists for a model, above zero, are shown.
_TOKEN_FIELDS = (
    ("prompt", "Input", 1_000_000, "per 1M tokens"),
    ("completion", "Output", 1_000_000, "per 1M tokens"),
    ("input_cache_read", "Cached input", 1_000_000, "per 1M tokens"),
    ("input_cache_write", "Cache write", 1_000_000, "per 1M tokens"),
    ("internal_reasoning", "Reasoning", 1_000_000, "per 1M tokens"),
    ("request", "Per request", 1, "per request"),
    ("image", "Image input", 1, "per image"),
    ("web_search", "Web search", 1, "per search"),
)


def _amount(pricing: dict, field: str) -> float | None:
    """A listed price in USD, or None: absent, unreadable, or negative ("varies")."""
    try:
        value = float(pricing[field])
    except (KeyError, TypeError, ValueError):
        return None
    return value if value >= 0 else None


def _token_lines(pricing: dict) -> tuple[tuple[str, float, str, bool], ...]:
    lines = []
    for field, label, scale, unit in _TOKEN_FIELDS:
        value = _amount(pricing, field)
        if value:
            lines.append((label, round(value * scale, 6), unit, False))
    return tuple(lines)


def _is_free(pricing: dict) -> bool:
    """Listed at zero for input and output, today. OpenRouter can change that."""
    return _amount(pricing, "prompt") == 0 and _amount(pricing, "completion") in (0, None) \
        and not any(_amount(pricing, f) for f, *_ in _TOKEN_FIELDS)


# ---- voice (transcription) models ------------------------------------------------

# OpenRouter lists a speech model's audio price with no unit. Every per-second
# price in the catalogue sits below this (whisper-1 is 0.0001 a second, which
# is OpenAI's own $0.006 a minute); larger figures (0.1, 0.36) only make sense
# per hour, and nothing in the data says so. So a per-hour estimate is shown
# only below this line, labelled as an estimate, and never above it.
PER_SECOND_CEILING = 1e-3


def stt_model_info(entry: dict) -> ModelInfo | None:
    """A transcription model from OpenRouter's public speech catalogue."""
    model_id = str(entry.get("id") or "")
    outputs = (entry.get("architecture") or {}).get("output_modalities") or []
    if not model_id or "transcription" not in outputs:
        return None
    pricing = entry.get("pricing") or {}
    prompt, completion = _amount(pricing, "prompt"), _amount(pricing, "completion")
    lines: list[tuple[str, float, str, bool]] = []
    price = None
    if completion:
        # Token-billed, like a text model: shown exactly as listed.
        lines = list(_token_lines({"prompt": pricing.get("prompt"), "completion": pricing.get("completion")}))
        price = _price(pricing)
    elif prompt:
        lines.append(("OpenRouter rate", prompt, "unit not stated by OpenRouter", False))
        if prompt < PER_SECOND_CEILING:
            lines.append(("Per hour of audio", round(prompt * 3600, 4), "estimate", True))
    return ModelInfo(
        id=model_id,
        name=str(entry.get("name") or model_id),
        vendor=model_id.split("/", 1)[0] if "/" in model_id else "",
        price=price,
        pricing=tuple(lines),
        free=prompt == 0 and not completion,
        verified=model_id in SPEC.stt["models"],
        page_url=f"{SITE}/{model_id}",
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
    # /models/user, as the website uses: only models this account can route
    # to, after its own privacy and provider settings.
    models_path="/models/user",
    model_filter=model_info,
    stt_filter=stt_model_info,
    key_check_path="/key",
    # The recommended cloud path: one key reaches many models and providers.
    tier=2,
    tagline="One API key for many AI models and providers.",
    # Whisper through OpenRouter, with word timings. Short parts: OpenRouter's
    # upstream providers stop after about a minute of processing a request.
    # "models" are the ones known to return the word timings captions need;
    # the full live list comes from "catalog", and any other model is checked
    # with a short test clip when the user picks it (transcription/cloud.py).
    stt={"format": "openrouter", "chunk_seconds": 180,
         "models": ["openai/whisper-large-v3-turbo", "openai/whisper-large-v3", "openai/whisper-1"],
         "catalog": "/models?output_modalities=transcription&limit=100"},
)
