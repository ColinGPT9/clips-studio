"""OpenAI, on the user's own API key (an API key, not a ChatGPT subscription)."""

from llm.providers.base import ModelInfo, ProviderSpec

# /v1/models carries no capability flags, so models that cannot write the
# answer (speech, images, embeddings, moderation, realtime) go by their names.
_NOT_TEXT = ("audio", "realtime", "transcribe", "tts", "whisper", "image", "dall-e", "embedding",
             "moderation", "search", "computer-use", "codex", "sora", "babbage", "davinci")
_TEXT_FAMILIES = ("gpt-", "o1", "o3", "o4", "chatgpt-")


def model_info(entry: dict) -> ModelInfo | None:
    model_id = str(entry.get("id") or "")
    if not model_id.startswith(_TEXT_FAMILIES) or any(word in model_id for word in _NOT_TEXT):
        return None
    return ModelInfo(id=model_id, name=model_id, json_schema=True, tools=True)


SPEC = ProviderSpec(
    id="openai",
    label="OpenAI",
    adapter="openai_responses",
    base_url="https://api.openai.com/v1",
    key_label="OpenAI API key",
    key_url="https://platform.openai.com/api-keys",
    pricing_url="https://developers.openai.com/api/docs/pricing",
    privacy="Transcripts and prompts (and audio, if it transcribes) are sent to OpenAI with your key. "
            "Nothing is stored there by this app's requests.",
    model_filter=model_info,
    # whisper-1 is the OpenAI model that returns word timings; the newer
    # gpt-*-transcribe models do not, and captions need them.
    stt={"format": "openai", "models": ["whisper-1"], "chunk_seconds": 600},
)
