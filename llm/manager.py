"""Model management helpers for the CLI.

Lets users see what's installed in Ollama, what their hardware can run,
and switch the active model with one command — without editing YAML by hand.
"""

import re
from pathlib import Path

import requests

# Rough VRAM guide for Ollama default (4-bit) quantizations.
#
# The first four rows are the defaults recommend_for() hands out, and they stay
# on the Gemma 3 line because that is what has actually been run against real
# streams here. The rows after them are alternatives a user can choose.
# Something newer is not automatically something better, and the machine that
# finds out should not be a stranger's on first launch.
#
# Licence matters more than usual for this audience: people clipping their own
# streams are usually monetising them. Everything listed is free to run
# locally, but the terms differ — Qwen, Mistral and Granite are Apache-2.0,
# Phi-4 is MIT, Llama and Gemma carry their own terms that permit commercial
# use with conditions. Deliberately absent: Cohere's Aya Expanse and
# Command-R7B, which are excellent multilingually and licensed CC-BY-NC, so
# they cannot be used for anything anyone earns from.
RECOMMENDATIONS = [
    ("CPU only / iGPU",   "gemma3:4b",   "fast, surprisingly capable"),
    ("6-8 GB VRAM",       "gemma3:4b",   "fully GPU-accelerated"),
    ("10-12 GB VRAM",     "gemma3:12b",  "big quality jump for scoring"),
    ("16-24 GB VRAM",     "gemma3:27b",  "best local Gemma"),
    ("Low RAM / edge",    "gemma4:e2b",  "Gemma 4 edge build, smallest that still scores"),
    ("Newer Gemma",       "gemma4:e4b / gemma4:12b", "untested here — compare before switching"),
    ("Multilingual",      "qwen3:8b / qwen3:14b", "Apache-2.0; set as llm.translation_model"),
    ("No extra terms",    "mistral-nemo:12b / phi4:14b", "Apache-2.0 and MIT respectively"),
    ("Alternatives",      "llama3.1:8b / qwen2.5:14b", "swap freely, same one-line change"),
]


def recommend_for(vram_gb: float | None) -> dict:
    """The model to suggest for this machine, from the table above.

    Lives here so there is ONE answer. The setup wizard used to decide this
    for itself in TypeScript and disagreed with the table on the same page —
    a 12 GB card was told gemma3:12b by the Models page and gemma:7b by the
    wizard, in the same app.

    A model that doesn't fit in VRAM spills into system RAM and crawls, which
    reads as broken rather than slow, so the sizes here are deliberately
    conservative.
    """
    if not vram_gb or vram_gb <= 0:
        return {
            "model": "gemma3:4b",
            "reason": "No graphics card detected, so this is the small model — "
                      "it runs on the processor. Clipping works, it just takes longer.",
        }
    if vram_gb >= 16:
        return {
            "model": "gemma3:27b",
            "reason": f"{vram_gb:.0f} GB of VRAM fits the largest model, "
                      "which picks and titles clips best.",
        }
    if vram_gb >= 10:
        return {
            "model": "gemma3:12b",
            "reason": f"Sized for {vram_gb:.0f} GB of VRAM — a big quality jump "
                      "over the smaller models for choosing clips.",
        }
    return {
        "model": "gemma3:4b",
        "reason": f"Sized for {vram_gb:.0f} GB of VRAM. A larger model would spill "
                  "out of the graphics card and crawl.",
    }


def installed_models(host: str) -> list[dict]:
    """Models currently pulled in Ollama: [{"name", "size_gb"}]."""
    response = requests.get(f"{host.rstrip('/')}/api/tags", timeout=15)
    response.raise_for_status()
    return [
        {"name": m["name"], "size_gb": m.get("size", 0) / 1e9}
        for m in response.json().get("models", [])
    ]


def switch_model(settings_path: Path, model_tag: str) -> str:
    """Rewrite the `model:` line in the quick-setup block at the top of
    settings.yaml (preserves all user comments). Returns the new spec."""
    text = settings_path.read_text(encoding="utf-8")
    new_text, n = re.subn(
        r"(?m)^(model:\s*)\S+", rf"\g<1>{model_tag}", text, count=1
    )
    if n == 0:
        raise RuntimeError(f"No 'model:' line found in {settings_path}")
    settings_path.write_text(new_text, encoding="utf-8")
    return model_tag if "/" in model_tag else f"ollama/{model_tag}"
