"""Anthropic (Claude), on the user's own API key."""

from llm.providers.base import ModelInfo, ProviderSpec

API_VERSION = "2023-06-01"


def _headers() -> dict:
    return {"anthropic-version": API_VERSION}


def model_info(entry: dict) -> ModelInfo | None:
    model_id = str(entry.get("id") or "")
    if not model_id.startswith("claude"):
        return None
    structured = ((entry.get("capabilities") or {}).get("structured_outputs") or {}).get("supported")
    return ModelInfo(
        id=model_id,
        name=str(entry.get("display_name") or model_id),
        context=int(entry.get("max_input_tokens") or 0),
        json_schema=bool(structured),
        tools=True,  # every current Claude model can call tools
    )


SPEC = ProviderSpec(
    id="anthropic",
    label="Anthropic Claude",
    adapter="anthropic_messages",
    base_url="https://api.anthropic.com/v1",
    key_label="Anthropic API key",
    key_url="https://console.anthropic.com/settings/keys",
    pricing_url="https://www.anthropic.com/pricing",
    privacy="Transcripts and prompts are sent to Anthropic with your key.",
    auth="x-api-key",
    extra_headers=_headers,
    model_filter=model_info,
    tagline="Direct Anthropic API.",
    plan_note="Claude Pro and Max plans can't be used here: Anthropic only allows them in its "
              "own apps. This uses a Claude API key, billed separately.",
    plan_note_url="https://code.claude.com/docs/en/legal-and-compliance",
)
