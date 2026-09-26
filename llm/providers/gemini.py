"""Google Gemini, on the user's own API key (sent in a header, never a URL)."""

from llm.providers.base import ModelInfo, ProviderSpec

_NOT_TEXT = ("embedding", "tts", "image", "transcribe", "live", "aqa", "veo", "imagen", "native-audio")


def model_info(entry: dict) -> ModelInfo | None:
    name = str(entry.get("name") or "")
    model_id = name.removeprefix("models/")
    methods = entry.get("supportedGenerationMethods") or []
    if not model_id.startswith("gemini") or "generateContent" not in methods \
            or any(word in model_id for word in _NOT_TEXT):
        return None
    return ModelInfo(
        id=model_id,
        name=str(entry.get("displayName") or model_id),
        context=int(entry.get("inputTokenLimit") or 0),
        json_schema=True,
        tools=True,
    )


SPEC = ProviderSpec(
    id="gemini",
    label="Google Gemini",
    adapter="gemini_generate",
    base_url="https://generativelanguage.googleapis.com/v1beta",
    key_label="Gemini API key",
    key_url="https://aistudio.google.com/apikey",
    pricing_url="https://ai.google.dev/gemini-api/docs/pricing",
    privacy="Transcripts and prompts are sent to Google with your key. On Google's free tier, "
            "Google may use them to improve its products.",
    auth="x-goog-api-key",
    model_filter=model_info,
    tagline="Direct Google Gemini API.",
    plan_note="Google AI Pro and Ultra plans can't be used here: Google only allows them in its "
              "own apps. They do include monthly Google Cloud credits, which can pay for Gemini "
              "API use on your key.",
    plan_note_url="https://ai.google.dev/gemini-api/docs/google-ai-plans",
)
