"""The built-in voice: Kokoro, inside the engine, on the CPU.

kokoro-onnx (MIT) runs the Kokoro-82M model (Apache-2.0) on the ONNX Runtime
the app already ships; espeak-ng turns the text into sounds first. No second
program, no server, no network. The two model files are found through
core/binaries.py and checked against their SHA-256 (voice/kokoro_files.py)
before they are loaded.

Languages: only the ones measured to come out right through kokoro-onnx with
what the app bundles (docs/editor-suite/api/stepV1-voice.md has the numbers).
The voice pack also holds Japanese and Chinese voices; through espeak-ng they
are not understandable, and the dictionaries that fix that are a large new
dependency, so those voices are not offered and Chinese stays on Piper.

The same text never gives the same bytes twice by itself: the model draws
random numbers while it speaks. With a seed it does, on the same PC: a seeded
request gets a fresh model session started from that seed, with a fixed
number of threads.
"""

from __future__ import annotations

import importlib.util
import os
import threading
import zipfile
from pathlib import Path

from voice import kokoro_files, wav
from voice.base import VoiceError

ID = "kokoro"
SAMPLE_RATE = 24000

# App language -> the espeak-ng voice that reads the text.
LANGUAGES: dict[str, str] = {
    "en": "en-us",
    "es": "es",
    "fr": "fr-fr",
    "hi": "hi",
    "it": "it",
    "pt": "pt-br",
}
DEFAULT_VOICE: dict[str, str] = {
    "en": "af_heart",
    "es": "ef_dora",
    "fr": "ff_siwis",
    "hi": "hf_alpha",
    "it": "if_sara",
    "pt": "pf_dora",
}
# First letter of a voice's name -> (language, espeak-ng voice, country).
_FAMILY: dict[str, tuple[str, str, str]] = {
    "a": ("en", "en-us", "United States"),
    "b": ("en", "en-gb", "United Kingdom"),
    "e": ("es", "es", ""),
    "f": ("fr", "fr-fr", "France"),
    "h": ("hi", "hi", "India"),
    "i": ("it", "it", "Italy"),
    "p": ("pt", "pt-br", "Brazil"),
}
SPEED_MIN, SPEED_MAX = 0.5, 2.0
# A seeded session always runs on this many threads: the thread count changes
# the last bits of the sound, and a seed is a promise of the same bytes.
SEEDED_THREADS = 4

_lock = threading.Lock()          # one sentence at a time; a sentence already uses several cores
_loaded: tuple[str, object] | None = None
_names: tuple[tuple[str, int], tuple[str, ...]] | None = None


def installed() -> bool:
    """Whether the Python packages the voice needs are there (a checkout
    without them still runs everything else)."""
    try:
        return all(importlib.util.find_spec(m) is not None for m in ("kokoro_onnx", "onnxruntime", "numpy"))
    except (ImportError, ValueError):
        return False


def readiness() -> tuple[bool, str]:
    """(ready, why not) in plain words. Reads the two files' checksums the
    first time (about a second), then remembers them."""
    if not installed():
        import sys

        # An installed copy has no command line to send anyone to.
        how = "" if getattr(sys, "frozen", False) else " Run: pip install -r requirements.txt"
        return False, f"The built-in voice is not installed in this copy.{how}"
    folder, why = kokoro_files.check()
    return (folder is not None), why


def speaks(language: str) -> bool:
    return language in LANGUAGES


def _voice_names(folder: Path) -> tuple[str, ...]:
    """The voices in the voice pack, read from its listing (it is a zip of
    arrays), so listing them loads neither numpy nor the model."""
    global _names
    pack = folder / kokoro_files.VOICES_FILE
    key = (str(pack), pack.stat().st_mtime_ns)
    if _names is None or _names[0] != key:
        with zipfile.ZipFile(pack) as z:
            found = tuple(sorted(n[:-4] for n in z.namelist() if n.endswith(".npy")))
        _names = (key, found)
    return _names[1]


def _entry(name: str) -> dict | None:
    family = _FAMILY.get(name[:1])
    if family is None or len(name) < 4 or name[2] != "_":
        return None
    language, _espeak, country = family
    return {
        "id": f"{ID}:{name}",
        "voice": name,
        "name": name[3:].replace("_", " ").title(),
        "country": country,
        "quality": "",
        "language": language,
        "gender": {"f": "female", "m": "male"}.get(name[1], ""),
        "engine": ID,
        "commercial_ok": True,   # Apache-2.0 model, MIT runtime
    }


def voices(language: str | None = None) -> list[dict]:
    """The voices offered for `language` (every offered voice with None),
    the language's default first. Empty when the voice can't speak in this
    copy (its files or its Python package are not there): a voice that is
    listed is a voice that can be picked."""
    if not installed():
        return []
    folder, _why = kokoro_files.check()
    if folder is None:
        return []
    out = []
    for name in _voice_names(folder):
        entry = _entry(name)
        if entry is not None and (language is None or entry["language"] == language):
            out.append(entry)
    first = DEFAULT_VOICE.get(language or "")
    out.sort(key=lambda e: (e["voice"] != first, e["voice"]))
    return out


def default_voice(language: str) -> str | None:
    return DEFAULT_VOICE.get(language)


def _threads() -> int:
    return max(2, min(8, (os.cpu_count() or 4) // 2))


def _session(folder: Path, threads: int, seed: int | None = None):
    import onnxruntime as rt
    from kokoro_onnx import Kokoro

    if seed is not None:
        # The model's random numbers start from this when the session is
        # built; nothing else in the app reads ONNX Runtime's seed.
        rt.set_seed(int(seed) % (2 ** 31))
    options = rt.SessionOptions()
    options.intra_op_num_threads = threads
    options.inter_op_num_threads = 1
    session = rt.InferenceSession(str(folder / kokoro_files.MODEL_FILE), sess_options=options,
                                  providers=["CPUExecutionProvider"])
    return Kokoro.from_session(session, str(folder / kokoro_files.VOICES_FILE))


def _shared(folder: Path):
    global _loaded
    if _loaded is None or _loaded[0] != str(folder):
        _loaded = (str(folder), _session(folder, _threads()))
    return _loaded[1]


def unload() -> None:
    """Let go of the loaded model (about 350 MB of memory)."""
    global _loaded
    with _lock:
        _loaded = None


def speak(text: str, language: str, voice: str | None, out: Path, speed: float = 1.0,
          seed: int | None = None) -> int:
    """Speak `text` into `out` (16-bit mono WAV). Returns the sample rate."""
    if not speaks(language):
        raise VoiceError(f"The built-in voice does not speak this language ({language}).", "no_voice")
    ready, why = readiness()
    if not ready:
        raise VoiceError(why, "files_missing")
    folder, _ = kokoro_files.check()
    name = voice or DEFAULT_VOICE[language]
    if name not in _voice_names(folder) or _entry(name) is None:
        raise VoiceError("That built-in voice does not exist. Pick another voice.", "no_voice")
    family_language, espeak, _country = _FAMILY[name[:1]]
    if family_language != language:
        espeak = LANGUAGES[language]
    pace = max(SPEED_MIN, min(SPEED_MAX, float(speed)))
    if not text or not text.strip():
        raise VoiceError("There is nothing to say.", "failed")

    import numpy as np

    with _lock:
        try:
            engine = _session(folder, SEEDED_THREADS, seed) if seed is not None else _shared(folder)
            samples, rate = engine.create(text, voice=name, speed=pace, lang=espeak)
        except VoiceError:
            raise
        except Exception as e:  # the runtime's own errors carry nothing a person can act on
            raise VoiceError(f"The built-in voice could not say this ({e.__class__.__name__}).", "failed") from e
    if not len(samples) or not np.isfinite(samples).all() or float(np.max(np.abs(samples))) < 0.001:
        raise VoiceError("The built-in voice made no sound for this text.", "failed")
    wav.write_samples(Path(out), samples, int(rate))
    return int(rate)
