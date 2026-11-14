"""Piper behind the voice layer.

Piper is the voice dubbing has always used, and the only built-in one for
the languages Kokoro does not speak. Its code stays where it was
(multilingual/dub.py and multilingual/voices.py): this is a thin doorway to
it, so what Piper is asked for, and what it writes, is unchanged.
"""

from __future__ import annotations

from pathlib import Path

from voice.base import VoiceError

ID = "piper"


def installed() -> bool:
    from multilingual import dub

    return dub.available()


def speaks(language: str) -> bool:
    from multilingual import dub

    return dub.supported(language)


def default_voice(language: str) -> str | None:
    from multilingual.voices import DEFAULTS

    return DEFAULTS.get(language)


def spoken_by(voice: str | None, language: str) -> str:
    """The id of the Piper voice that speaks when `voice` is asked for: the
    voice itself, or the language's usual voice when the id is not one Piper
    could have (multilingual.voices.resolve falls back the same way)."""
    from multilingual.voices import resolve

    name, speaker = resolve(voice, language)
    return f"{name}#{speaker}" if name and speaker is not None else (name or "")


def voices(language: str, voices_dir: Path) -> list[dict]:
    """Piper's voices for a language, as multilingual/voices.py lists them,
    marked with their engine. Whether a Piper voice may be used in monetised
    videos differs voice by voice and is not recorded in its catalogue, so
    `commercial_ok` is None: not known."""
    from multilingual.voices import list_for

    if not installed():
        return []
    return [{**entry, "engine": ID, "language": language, "commercial_ok": None}
            for entry in list_for(language, Path(voices_dir))]


def speak(text: str, language: str, voice: str | None, out: Path, voices_dir: Path,
          speed: float = 1.0, length_scale: float | None = None) -> int:
    """Speak into `out` with a Piper voice (downloading the voice the first
    time, as dubbing always has). `length_scale` is Piper's own pace number
    (above 1 is slower) and wins over `speed` when given, so dubbing's timing
    fit hands over exactly the number it always did."""
    from multilingual import dub
    from multilingual.voices import resolve

    if not installed():
        raise VoiceError("This copy has no Piper voices installed.", "no_voice")
    name = dub.ensure_voice(language, Path(voices_dir), voice)
    if name is None:
        raise VoiceError(f"There is no voice for this language ({language}).", "no_voice")
    _name, speaker = resolve(voice, language)
    scale = float(length_scale) if length_scale is not None else 1.0 / max(0.25, min(4.0, float(speed)))
    if not dub._speak(text, name, Path(voices_dir), Path(out), rate=scale, speaker=speaker):
        raise VoiceError("The voice could not say this.", "failed")
    from voice import wav

    return wav.info(Path(out))[0]
