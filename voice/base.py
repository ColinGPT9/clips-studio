"""What every voice engine shares: the error a person reads, and voice ids.

A voice id says which engine speaks it, so a choice made once (in the
dubbing dialog, or as a default in Settings → Voice) keeps meaning the same
voice whatever engine is picked later:

    en_US-lessac-medium      a Piper voice, written as it always was
    fr_FR-upmc-medium#1      speaker 1 inside a Piper voice
    kokoro:af_heart          a built-in Kokoro voice
    voicebox:<profile id>    a voice in the person's own Voicebox
    voicestudio:<voice id>   a voice in the person's own VoiceStudio
    custom:<voice id>        a voice of another local voice app
"""

from __future__ import annotations

PREFIXES = ("kokoro", "voicebox", "voicestudio", "custom")


class VoiceError(RuntimeError):
    """Speech could not be made. The message is written for the person
    using the app, in plain words, and is shown as it is.

    `code` lets the UI tell the cases apart without reading the words:
    "not_running" (the chosen voice app is closed), "files_missing" (the
    built-in voice's files are not there), "not_local" (an address that is
    not this PC), "no_voice" (nothing can speak this language or voice),
    "refused" (the voice app answered with an error) and "failed"."""

    def __init__(self, message: str, code: str = "failed"):
        super().__init__(message)
        self.code = code


def split(voice_id: str | None) -> tuple[str, str]:
    """'kokoro:af_heart' -> ('kokoro', 'af_heart'); a Piper id (or anything
    with no known prefix) -> ('piper', the id unchanged); nothing -> ('', '')."""
    if not voice_id or not isinstance(voice_id, str):
        return "", ""
    head, sep, rest = voice_id.partition(":")
    if sep and head in PREFIXES:
        return head, rest
    return "piper", voice_id


def join(engine: str, native: str) -> str:
    return native if engine == "piper" else f"{engine}:{native}"
