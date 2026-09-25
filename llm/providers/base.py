"""What describes a cloud provider, and the one error type they all raise."""

from collections.abc import Callable
from dataclasses import dataclass, field


class LLMError(Exception):
    """A provider failure, already in words a creator can act on.

    `message` never contains the key, and is safe to show, log and store as a
    job error. `kind` lets callers and tests tell the cases apart.
    """

    KINDS = (
        "not_configured",     # no key saved for the chosen provider
        "invalid_key",        # the provider refused the key
        "no_credits",         # out of credit, or over a spending limit
        "rate_limited",       # too many requests for now
        "model_unavailable",  # the model is not offered to this key
        "provider_down",      # 5xx, overloaded, timed out
        "network",            # could not reach the provider at all
        "rejected",           # the provider refused this particular request
        "bad_response",       # an answer that could not be read
    )

    def __init__(self, kind: str, message: str):
        super().__init__(message)
        self.kind = kind if kind in self.KINDS else "rejected"
        self.message = message


@dataclass(frozen=True)
class ModelInfo:
    id: str
    name: str = ""
    context: int = 0
    json_schema: bool = False  # can hold its answer to a JSON schema
    tools: bool = False        # can call tools (the assistant needs this)
    note: str = ""             # a warning worth showing beside it

    def as_dict(self) -> dict:
        return {
            "id": self.id, "name": self.name or self.id, "context": self.context,
            "json_schema": self.json_schema, "tools": self.tools, "note": self.note,
        }


def _no_headers() -> dict:
    return {}


def _keep(body: dict) -> dict:
    return body


def _any_model(entry: dict) -> ModelInfo | None:
    model_id = str(entry.get("id") or "")
    return ModelInfo(id=model_id, name=str(entry.get("name") or model_id)) if model_id else None


@dataclass(frozen=True)
class ProviderSpec:
    """Everything the app needs to know about one provider.

    `adapter` names the wire format (a module in adapters/). Most providers
    speak OpenAI-compatible chat completions, so adding one of those is just
    another ProviderSpec with no new code.
    """

    id: str
    label: str
    adapter: str
    base_url: str
    key_label: str
    key_url: str        # where the user gets their own key
    pricing_url: str    # the provider's own pricing: what the user will pay
    privacy: str        # what leaves the PC, said plainly
    auth: str = "bearer"            # bearer | x-api-key | x-goog-api-key
    extra_headers: Callable[[], dict] = _no_headers
    body_extras: Callable[[dict], dict] = _keep
    models_path: str = "/models"
    model_filter: Callable[[dict], ModelInfo | None] = _any_model
    key_check_path: str = ""        # a cheap GET proving the key; "" lists models
    stt: dict = field(default_factory=dict)  # online transcription, when offered

    def public(self) -> dict:
        """What the UI is told about this provider. Never anything secret."""
        return {
            "id": self.id,
            "label": self.label,
            "local": False,
            "key_label": self.key_label,
            "key_url": self.key_url,
            "pricing_url": self.pricing_url,
            "privacy": self.privacy,
            "stt": bool(self.stt),
        }
