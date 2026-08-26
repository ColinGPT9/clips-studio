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
    ("CPU / iGPU, no graphics card", "gemma3:4b",
     "runs on the processor. Slower, but it works — and it is one of the three tested"),
    ("Under 6 GB VRAM, or an older PC", "gemma4:e2b",
     "smallest edge build; made to run on modest hardware"),
    ("6 GB VRAM", "gemma4:e4b",
     "the larger edge build, a step up from e2b at the same job"),
    ("8 GB VRAM", "gemma:7b",
     "the model everything here was tested on, and the default in settings.yaml"),
    ("10-12 GB VRAM", "gemma3:12b",
     "big quality jump for scoring, and tested here"),
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
    if vram_gb >= 7:
        return {
            "model": "gemma:7b",
            "reason": f"{vram_gb:.0f} GB of VRAM fits the model this app was built "
                      "and tested against, which is the safest thing to start on.",
        }
    if vram_gb >= 6:
        return {
            "model": "gemma4:e4b",
            "reason": f"Sized for {vram_gb:.0f} GB of VRAM — the larger of the two "
                      "edge builds, made for cards this size.",
        }
    return {
        "model": "gemma4:e2b",
        "reason": f"Only {vram_gb:.0f} GB of VRAM, so this is the smallest edge "
                  "build. A larger model would spill out of the card and crawl.",
    }


def installed_models(host: str) -> list[dict]:
    """Models currently pulled in Ollama: [{"name", "size_gb"}]."""
    response = requests.get(f"{host.rstrip('/')}/api/tags", timeout=15)
    response.raise_for_status()
    return [
        {"name": m["name"], "size_gb": m.get("size", 0) / 1e9}
        for m in response.json().get("models", [])
    ]


def model_is_installed(tag: str, installed: list[str]) -> bool:
    """Whether `tag` names one of the models Ollama actually has.

    Ollama reports "gemma:7b"; a config may say "gemma:7b", "ollama/gemma:7b",
    or just the family. All three have to match the same model.
    """
    wanted = tag.split("/")[-1]
    return any(
        m == wanted or m.startswith(f"{wanted}:") or m.split(":")[0] == wanted
        for m in installed
    )


def resolve_usable_model(host: str, configured: str) -> str | None:
    """The model this app will really load, or None if there is not one.

    Selected, installed and recommended are three different things, and
    conflating them is what failed Store certification: setup downloaded the
    model it *recommended* while the health check demanded the model that was
    *configured*, and nothing reconciled the two. So this is the single answer
    to "what will actually run", and the check, the pipeline and the UI all ask
    it rather than each deciding for themselves.

    Any installed model counts. RECOMMENDATIONS is advice about what runs well
    on which hardware; treating it as a list of permitted models is exactly the
    bug, so it is used only to break ties.
    """
    try:
        installed = [m["name"] for m in installed_models(host)]
    except Exception:
        return None  # Ollama unreachable; the runtime check reports that

    if not installed:
        return None
    if configured and model_is_installed(configured, installed):
        return configured.split("/")[-1]

    # Nothing selected that can be used, so pick for them rather than stopping.
    # Preferring the recommendation order means the choice matches what the
    # Models page would have suggested, instead of whatever Ollama lists first.
    for _hardware, tag, _note in RECOMMENDATIONS:
        if model_is_installed(tag, installed):
            return tag
    return installed[0]


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
