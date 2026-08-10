"""Model management helpers for the CLI.

Lets users see what's installed in Ollama, what their hardware can run,
and switch the active model with one command — without editing YAML by hand.
"""

import re
from pathlib import Path

import requests

# Rough VRAM guide for Ollama default (4-bit) quantizations.
#
# ONE axis: what the machine can hold. Every row answers "how much VRAM do you
# have", and recommend_for() hands out exactly these. An earlier version
# appended rows like "Newer Gemma" and "Multilingual" here, which put
# non-hardware values under a "Your hardware" heading and left the note column
# carrying licences, warnings and hardware advice at once. Anything that is not
# a hardware tier belongs in OTHER_MODELS below.
#
# Stays on the Gemma 3 line because that is what has been run against real
# streams here. Newer is not automatically better, and a stranger's first
# launch is the wrong place to discover otherwise.
# One row per tier, no model listed twice. gemma3:4b previously appeared under
# both "CPU only / iGPU" and "6-8 GB VRAM", which reads as a mistake even
# though it is the same correct answer to both.
#
# The Gemma 4 edge builds belong here, not off to one side. e2b and e4b are
# built to run on ordinary local machines, which is precisely this audience —
# treating them as exotic would be wrong.
RECOMMENDATIONS = [
    ("Low-power or older PC", "gemma4:e2b",
     "smallest edge build; made to run on modest hardware"),
    ("CPU / iGPU / up to 8 GB VRAM", "gemma3:4b",
     "fits anywhere. gemma4:e4b is an equally good pick at this size"),
    ("10-12 GB VRAM", "gemma3:12b",
     "big quality jump for scoring. gemma4:12b is the newer equivalent"),
    ("16-24 GB VRAM", "gemma3:27b", "best local Gemma"),
]

# NOTE ON TESTING, true of both tables: only gemma:7b, gemma3:4b and gemma3:12b
# have been run against real streams. Everything else is listed because it is a
# sensible size for the hardware and is free to use commercially, not because
# clip quality has been measured with it. They will work — the app talks to all
# of them identically through Ollama — but nobody has checked whether they pick
# better or worse moments.
#
# The other axis: chosen for a reason other than how much VRAM you have.
#
# Licence is called out because this audience monetises its clips. Everything
# here is free to run locally, but the terms differ — Qwen and Mistral are
# Apache-2.0, Phi-4 is MIT, Llama and Gemma carry their own terms that permit
# commercial use with conditions. Deliberately absent: Cohere's Aya Expanse and
# Command-R7B, excellent multilingually and licensed CC-BY-NC, so they cannot
# be used for anything anyone earns from.
OTHER_MODELS = [
    ("Translation / multilingual", "qwen3:8b / qwen3:14b",
     "strongest multilingual here; set as llm.translation_model. Apache-2.0"),
    ("Permissive licence", "mistral-nemo:12b / phi4:14b",
     "Apache-2.0 and MIT, no additional terms"),
    ("Older, still solid", "llama3.1:8b / qwen2.5:14b",
     "swap freely, same one-line change"),
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
