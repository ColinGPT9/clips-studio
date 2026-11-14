"""Any voice app on this PC that speaks OpenAI's speech route.

Many local voice apps answer `POST /v1/audio/speech` the way OpenAI's API
does. One connector covers them all, in two forms:

- **VoiceStudio** (github.com/debpalash/VoiceStudio), by name. While it runs
  it writes where it listens into `backend.json` in its own settings folder;
  that address is used when the file is there and names this PC, and
  127.0.0.1:3900 otherwise. A record that points anywhere else is ignored.
- **Another local voice app**, at an address the person types. It must be an
  address on this PC.

Clips Kitty neither ships nor starts either. Only the standard route is used
(plus `GET /v1/audio/voices`, where a server has it, to list voices): always
asking for a WAV, at most 4,096 characters a request (longer text is split
at sentence ends and the pieces joined), with a seed when one is given.
"""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

from voice import net, wav
from voice.base import VoiceError

VOICESTUDIO = "voicestudio"
CUSTOM = "custom"
NAMES = {VOICESTUDIO: "VoiceStudio", CUSTOM: "Another local voice app"}
VOICESTUDIO_DEFAULT = "http://127.0.0.1:3900"
MAX_INPUT = 4096
DEFAULT_MODEL = "tts-1"
# The voice names OpenAI's own route takes; offered when a server has no
# list of its own. Most compatible servers accept them.
STANDARD_VOICES = ("alloy", "echo", "fable", "onyx", "nova", "shimmer")
SPEED_MIN, SPEED_MAX = 0.25, 4.0

CLOSED = {VOICESTUDIO: "Open VoiceStudio to use it.",
          CUSTOM: "Open your voice app to use it."}
NO_ADDRESS = "Enter your voice app's address first, like http://127.0.0.1:8880."


def voicestudio_folder() -> Path:
    """Where VoiceStudio keeps its settings (its own rule, per system).
    VOICESTUDIO_USER_DATA is VoiceStudio's own switch for another folder."""
    override = os.environ.get("VOICESTUDIO_USER_DATA")
    if override:
        return Path(override).expanduser()
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "VoiceStudio"
    if sys.platform == "win32":
        return Path(os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming") / "VoiceStudio"
    return Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "VoiceStudio"


def voicestudio_address(folder: Path | None = None) -> str:
    """The address in VoiceStudio's backend.json when it names this PC, else
    its usual one. The file is only read; a record marked remote, with a
    shape this does not know, or pointing off this PC is ignored."""
    try:
        record = json.loads(((folder or voicestudio_folder()) / "backend.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return VOICESTUDIO_DEFAULT
    if not isinstance(record, dict) or record.get("remote") or not isinstance(record.get("url"), str):
        return VOICESTUDIO_DEFAULT
    try:
        return net.local_base(record["url"])
    except net.NotLocal:
        return VOICESTUDIO_DEFAULT


def address(kind: str, settings: dict) -> str:
    """The loopback address for this engine. NotLocal (with the plain
    message) when the custom address is missing or not on this PC."""
    if kind == VOICESTUDIO:
        return voicestudio_address()
    custom = str(settings.get("custom_address") or "").strip()
    if not custom:
        raise net.NotLocal(NO_ADDRESS)
    return net.local_base(custom)


def status(kind: str, settings: dict) -> dict:
    """{"ready", "message", "address"}: whether the app answers right now.
    Any HTTP answer counts: a server that has the speech route need not have
    a page at "/"."""
    try:
        base = address(kind, settings)
    except net.NotLocal as e:
        return {"ready": False, "message": str(e), "address": ""}
    if not net.listening(base):
        return {"ready": False, "message": CLOSED[kind], "address": base}
    return {"ready": True, "message": "", "address": base}


def _listed(data) -> list[dict] | None:
    """Voices out of whatever GET /v1/audio/voices answered: a list or
    {"voices": [...]} or {"data": [...]}, of names or of objects."""
    if isinstance(data, dict):
        data = data.get("voices") if isinstance(data.get("voices"), list) else data.get("data")
    if not isinstance(data, list):
        return None
    out = []
    for item in data[:500]:
        if isinstance(item, str) and item.strip():
            out.append({"voice": item.strip(), "name": item.strip(), "language": "", "type": ""})
        elif isinstance(item, dict):
            vid = item.get("id") or item.get("voice_id") or item.get("name")
            if isinstance(vid, str) and vid.strip():
                out.append({"voice": vid.strip(), "name": str(item.get("name") or vid).strip()[:80],
                            "language": str(item.get("language") or "").lower()[:12],
                            "type": str(item.get("type") or "")})
    return out


def voices(kind: str, settings: dict, language: str | None = None) -> list[dict]:
    """The voices the app offers. A local voice app's voices are not tied to
    one language in a way this can read, so all are listed for every
    language, the ones marked for `language` first."""
    try:
        base = address(kind, settings)
    except net.NotLocal as e:
        raise VoiceError(str(e), "not_local") from None
    if not net.listening(base):
        raise VoiceError(CLOSED[kind], "not_running")
    try:
        found = _listed(net.get_json(base, "/v1/audio/voices", timeout=10.0))
    except net.Unreachable:
        raise VoiceError(CLOSED[kind], "not_running") from None
    except net.BadAnswer:
        found = None   # no list on this server: the standard names below
    if kind == VOICESTUDIO and found is not None:
        # VoiceStudio lists OpenAI's thirteen names as stand-ins that all mean
        # "the current voice"; one entry says that better than thirteen.
        found = [{"voice": "default", "name": "VoiceStudio's current voice", "language": "", "type": ""}] + \
                [v for v in found if v["type"] != "openai_alias"]
    if not found:
        found = [{"voice": v, "name": v.title(), "language": "", "type": ""} for v in STANDARD_VOICES]
        if kind == VOICESTUDIO:
            found.insert(0, {"voice": "default", "name": "VoiceStudio's current voice", "language": "", "type": ""})
    out = [{
        "id": f"{kind}:{v['voice']}",
        "voice": v["voice"],
        "name": v["name"],
        "country": "",
        "quality": "",
        "language": v["language"],
        "engine": kind,
        # VoiceStudio speaks through whichever model the person set up
        # there; its default (OmniVoice) is licensed for non-commercial use
        # and VoiceStudio marks none of its models as cleared for commercial
        # use, so its voices carry the "not for monetised videos" mark. For
        # another app nothing is known: None, and the engine's note says the
        # voice's licence is the person's to check.
        "commercial_ok": False if kind == VOICESTUDIO else None,
    } for v in found]
    if language:
        out.sort(key=lambda e: 0 if e["language"] == language else (1 if not e["language"] else 2))
    return out


def default_voice(kind: str, settings: dict, language: str) -> str | None:
    found = voices(kind, settings, language)
    return found[0]["voice"] if found else None


_SENTENCE_END = re.compile(r"(?<=[.!?。！？…\n])\s*")


def split_text(text: str, limit: int = MAX_INPUT) -> list[str]:
    """`text` in pieces of at most `limit` characters, cut at sentence ends
    where there are any, else at a space, else mid-word. Nothing is dropped
    but the white space at a cut."""
    text = text.strip()
    pieces: list[str] = []
    current = ""
    for sentence in (s for s in _SENTENCE_END.split(text) if s and s.strip()):
        while len(sentence) > limit:
            if current:
                pieces.append(current)
                current = ""
            cut = sentence.rfind(" ", 0, limit + 1)
            cut = cut if cut > limit // 2 else limit
            pieces.append(sentence[:cut].strip())
            sentence = sentence[cut:].strip()
        if not sentence:
            continue
        joined = f"{current} {sentence}" if current else sentence
        if len(joined) <= limit:
            current = joined
        else:
            pieces.append(current)
            current = sentence
    if current:
        pieces.append(current)
    return [p for p in pieces if p]


def speak(kind: str, settings: dict, text: str, language: str, voice: str, out: Path,
          speed: float = 1.0, seed: int | None = None, timeout: float = 600.0) -> int:
    """Speak through the app's `POST /v1/audio/speech`. Returns the sample rate."""
    try:
        base = address(kind, settings)
    except net.NotLocal as e:
        raise VoiceError(str(e), "not_local") from None
    if not net.listening(base):
        raise VoiceError(CLOSED[kind], "not_running")
    name = NAMES[kind] if kind == VOICESTUDIO else "Your voice app"
    model = str(settings.get("custom_model") or "").strip() if kind == CUSTOM else ""
    answers: list[bytes] = []
    for piece in split_text(text):
        body: dict = {
            "model": model or DEFAULT_MODEL,
            "input": piece,
            "voice": voice or "default",
            "response_format": "wav",
            "speed": max(SPEED_MIN, min(SPEED_MAX, float(speed))),
        }
        if seed is not None:
            body["seed"] = int(seed) % (2 ** 31)
        if kind == VOICESTUDIO and language:
            body["language"] = language    # VoiceStudio's own extra field
        try:
            got, _kind = net.request(base, "/v1/audio/speech", body=body, timeout=timeout,
                                     limit=net.MAX_AUDIO_BYTES)
        except net.Unreachable:
            raise VoiceError(CLOSED[kind], "not_running") from None
        except net.BadAnswer as e:
            if e.status == 404:
                raise VoiceError(f"{name} has no speech route Clips Kitty can use "
                                 f"(POST /v1/audio/speech).", "refused") from None
            raise VoiceError(f"{name} could not say this: {e}", "refused") from None
        answers.append(got)
    if not answers:
        raise VoiceError("There is nothing to say.", "failed")
    try:
        rate, _seconds = wav.save_answers(answers, Path(out))
    except wav.NotAudio as e:
        raise VoiceError(f"{name} did not send speech back ({e}).", "refused") from None
    return rate
