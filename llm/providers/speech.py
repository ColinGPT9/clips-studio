"""The voice (transcription) models a provider offers, with their prices.

A provider with a live speech catalogue (OpenRouter: stt["catalog"]) is read
from it, prices and all. One without offers the models known to return word
timings (stt["models"]), which is what captions, filler cuts and the editor's
word tools need. Known-good models come first; any other one is checked with a
short test clip when the user picks it (transcription/cloud.check_model).
"""

from llm.providers.base import ModelInfo, ProviderSpec
from llm.providers.http import send


def list_stt_models(spec: ProviderSpec, key: str) -> list[ModelInfo]:
    known = list(spec.stt.get("models") or [])
    catalog = spec.stt.get("catalog")
    if not catalog or spec.stt_filter is None:
        return [ModelInfo(id=m, name=m, verified=True, page_url=spec.pricing_url) for m in known]
    data = send(spec, key, "GET", catalog)
    models = [m for m in (spec.stt_filter(e) for e in data.get("data") or [] if isinstance(e, dict)) if m]
    return sorted(models, key=lambda m: (not m.verified, m.name.lower()))
