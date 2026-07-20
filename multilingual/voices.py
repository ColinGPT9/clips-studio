"""The catalogue of local dubbing voices, so creators can pick one.

Piper ships several voices per language (nine for Spanish, seven for
French) and some contain multiple speakers. The catalogue has no gender
field, so nothing here guesses one — it lists every voice with its speaker
name, country and quality and lets the creator LISTEN. A woman speaking on
screen shouldn't be dubbed by whichever voice happened to be first in the
list.

The catalogue is fetched once and cached on disk; if the network is
unavailable the built-in defaults still work.
"""

import json
import time
import urllib.request
from pathlib import Path

CATALOGUE_URL = "https://huggingface.co/rhasspy/piper-voices/resolve/main/voices.json"
CACHE_HOURS = 24 * 14

# Sensible starting voice per language when the creator hasn't chosen one.
DEFAULTS: dict[str, str] = {
    "en": "en_US-lessac-medium",
    "es": "es_ES-davefx-medium",
    "pt": "pt_BR-cadu-medium",
    "fr": "fr_FR-siwis-medium",
    "de": "de_DE-thorsten-medium",
    "hi": "hi_IN-pratham-medium",
    "id": "id_ID-news_tts-medium",
    "ru": "ru_RU-denis-medium",
    "ar": "ar_JO-kareem-medium",
    "zh": "zh_CN-huayan-medium",
    "vi": "vi_VN-vais1000-medium",
    "tr": "tr_TR-dfki-medium",
    "ur": "ur_PK-aegis_female-medium",
    "bn": "bn_BD-google-medium",
    "it": "it_IT-paola-medium",
}

_QUALITY_RANK = {"x_low": 0, "low": 1, "medium": 2, "high": 3}


def _cache_path(voices_dir: Path) -> Path:
    return voices_dir / "catalogue.json"


def catalogue(voices_dir: Path) -> dict:
    """Piper's voice list, cached on disk. {} when unavailable offline."""
    cache = _cache_path(voices_dir)
    try:
        if cache.exists() and (time.time() - cache.stat().st_mtime) < CACHE_HOURS * 3600:
            return json.loads(cache.read_text(encoding="utf-8"))
    except Exception:
        pass
    try:
        data = json.load(urllib.request.urlopen(CATALOGUE_URL, timeout=60))
        voices_dir.mkdir(parents=True, exist_ok=True)
        cache.write_text(json.dumps(data), encoding="utf-8")
        return data
    except Exception as e:
        print(f"      (voice catalogue unavailable: {e})")
        try:
            return json.loads(cache.read_text(encoding="utf-8")) if cache.exists() else {}
        except Exception:
            return {}


def list_for(language: str, voices_dir: Path) -> list[dict]:
    """Every voice a creator can choose for this language, best first.

    Multi-speaker voices are expanded into one entry per speaker, because
    that is often where the male/female alternative lives."""
    data = catalogue(voices_dir)
    out: list[dict] = []
    for key, v in data.items():
        lang = v.get("language", {})
        if lang.get("family") != language:
            continue
        n = int(v.get("num_speakers") or 1)
        base = {
            "voice": key,
            "name": v.get("name", key),
            "country": lang.get("country_english", ""),
            "quality": v.get("quality", ""),
            "rank": _QUALITY_RANK.get(v.get("quality", ""), 0),
        }
        if n <= 1:
            out.append({**base, "id": key, "speaker": None})
        else:
            # Cap it: some multi-speaker sets have >100 voices, which is a
            # menu nobody will read.
            for i in range(min(n, 8)):
                out.append({**base, "id": f"{key}#{i}", "speaker": i,
                            "name": f"{base['name']} #{i + 1}"})
    out.sort(key=lambda e: (-e["rank"], e["id"]))
    if not out and language in DEFAULTS:  # offline: at least offer the default
        out = [{"id": DEFAULTS[language], "voice": DEFAULTS[language],
                "name": DEFAULTS[language].split("-")[1], "country": "",
                "quality": "medium", "rank": 2, "speaker": None}]
    return out


def resolve(voice_id: str | None, language: str) -> tuple[str, int | None]:
    """'fr_FR-upmc-medium#1' -> ('fr_FR-upmc-medium', 1)."""
    chosen = voice_id or DEFAULTS.get(language, "")
    if "#" in chosen:
        name, _, idx = chosen.partition("#")
        try:
            return name, int(idx)
        except ValueError:
            return name, None
    return chosen, None
