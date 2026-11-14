"""The person's voice choices (Settings → Voice), kept as one small JSON file
in the data folder.

Defaults live here, not in settings.yaml: an installed build never receives
new settings.yaml keys (the reasoning of remote_render/settings.py). With no
file, or with a file nobody can read, everything is the default: the
built-in voice, no optional voice app, no fallback.
"""

from __future__ import annotations

import copy
import json
import os
import threading
from pathlib import Path

FILE = "voice_settings.json"

# "builtin" is the voice that ships with the app (Kokoro, and Piper for the
# languages Kokoro does not speak). The others are apps the person installed
# themselves and has open.
ENGINES = ("builtin", "voicebox", "voicestudio", "custom")

DEFAULTS: dict = {
    "engine": "builtin",
    # {language code: voice id}: the voice used when nothing picks one.
    "voices": {},
    # "Another local voice app": its address on this PC, and the model name
    # its speech route is asked for (empty = "tts-1", which most accept).
    "custom_address": "",
    "custom_model": "",
    # When the chosen voice app is not running: False = the job stops and
    # says why; True = it uses the built-in voice instead, and says so.
    "fallback_to_builtin": False,
}

_lock = threading.Lock()


def _path(data_dir) -> Path:
    return Path(data_dir) / FILE


def _clean(value: dict) -> dict:
    out = copy.deepcopy(DEFAULTS)
    if not isinstance(value, dict):
        return out
    if value.get("engine") in ENGINES:
        out["engine"] = value["engine"]
    voices = value.get("voices")
    if isinstance(voices, dict):
        out["voices"] = {str(k)[:12]: str(v)[:300] for k, v in voices.items()
                         if isinstance(k, str) and isinstance(v, str) and v.strip()}
    for key in ("custom_address", "custom_model"):
        if isinstance(value.get(key), str):
            out[key] = value[key].strip()[:300]
    out["fallback_to_builtin"] = value.get("fallback_to_builtin") is True
    return out


def load(data_dir) -> dict:
    if data_dir is None:
        return copy.deepcopy(DEFAULTS)
    try:
        return _clean(json.loads(_path(data_dir).read_text(encoding="utf-8")))
    except (OSError, ValueError):
        return copy.deepcopy(DEFAULTS)


def save(data_dir, value: dict) -> dict:
    merged = _clean(value)
    path = _path(data_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    part = path.with_name(path.name + ".part")
    part.write_text(json.dumps(merged, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(part, path)
    return merged


def update(data_dir, **changes) -> dict:
    """Change some fields. `voices` is merged language by language; a
    language given as None or "" goes back to the engine's own default."""
    with _lock:
        current = load(data_dir)
        for key, value in changes.items():
            if value is None:
                continue
            if key == "voices" and isinstance(value, dict):
                for language, voice in value.items():
                    if voice:
                        current["voices"][str(language)] = str(voice)
                    else:
                        current["voices"].pop(str(language), None)
            elif key in current:
                current[key] = value
        return save(data_dir, current)
