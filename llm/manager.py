"""Model management helpers for the CLI.

Lets users see what's installed in Ollama, what their hardware can run,
and switch the active model with one command — without editing YAML by hand.
"""

import re
from pathlib import Path

import requests

# Rough VRAM guide for Ollama default (4-bit) quantizations.
RECOMMENDATIONS = [
    ("CPU only / iGPU",   "gemma3:4b",   "fast, surprisingly capable"),
    ("6-8 GB VRAM",       "gemma3:4b",   "fully GPU-accelerated"),
    ("10-12 GB VRAM",     "gemma3:12b",  "big quality jump for scoring"),
    ("16-24 GB VRAM",     "gemma3:27b",  "best local Gemma"),
    ("Alternatives",      "llama3.1:8b / qwen2.5:14b", "swap freely, same one-line change"),
]


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
