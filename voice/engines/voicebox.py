"""The person's own Voicebox, when it is open.

Voicebox (github.com/jamiepine/voicebox, MIT) is a separate app the person
installs and runs themselves; Clips Kitty neither ships nor starts it. While
its window is open (or its "keep server running" setting is on) it listens
on 127.0.0.1:17493 with no login. Four routes are used and nothing else:

    GET  /                  is this Voicebox, and which version
    GET  /profiles          the voices the person made there
    GET  /models/status     which of its models are downloaded
    POST /generate/stream   speak: answers with the WAV, saves nothing there

Only voices that already exist are listed; a profile is never created,
changed or deleted (that would write into the person's Voicebox). A voice
whose model Voicebox has not downloaded says so: this never asks Voicebox to
download anything. Voices on its "tada" engine are left out: that model's
licence (Llama 3.2 Community) is not one Clips Kitty offers.
"""

from __future__ import annotations

import os
import threading
import time
from pathlib import Path

from voice import net, wav
from voice.base import VoiceError

ID = "voicebox"
NAME = "Voicebox"
DEFAULT_ADDRESS = "http://127.0.0.1:17493"
LEFT_OUT = ("tada",)

# The languages Voicebox's speech route accepts (its request model, v0.5.0).
LANGUAGES = frozenset("zh en ja ko de fr ru pt es it he ar da el fi hi ms nl no pl sv sw tr".split())
# Its engines whose models have an open licence that allows commercial use
# (checked at each model's page on 2026-11-12: Kokoro-82M, Qwen3-TTS and
# LuxTTS Apache-2.0, Chatterbox MIT).
_CLEARED = frozenset({"kokoro", "qwen", "qwen_custom_voice", "luxtts", "chatterbox", "chatterbox_turbo"})
# Engine -> the start of its model's name in GET /models/status.
_MODEL_PREFIX = {"kokoro": "kokoro", "qwen": "qwen-tts", "qwen_custom_voice": "qwen-custom-voice",
                 "luxtts": "luxtts", "chatterbox": "chatterbox-tts", "chatterbox_turbo": "chatterbox-turbo"}

CLOSED = "Open Voicebox to use it."

_cache_lock = threading.Lock()
_profiles_cache: dict[str, tuple[float, list[dict]]] = {}


def address() -> str:
    """Voicebox's address: always this PC, port 17493. CLIPS_STUDIO_VOICEBOX_URL
    names another loopback port (a test, or a Voicebox started by hand)."""
    return net.local_base(os.environ.get("CLIPS_STUDIO_VOICEBOX_URL") or DEFAULT_ADDRESS)


def status(base: str | None = None) -> dict:
    """{"ready", "message", "version"}: whether Voicebox answers right now."""
    try:
        base = base or address()
    except net.NotLocal as e:
        return {"ready": False, "message": str(e), "version": ""}
    if not net.listening(base):
        return {"ready": False, "message": CLOSED, "version": ""}
    try:
        root = net.get_json(base, "/", timeout=3.0)
    except net.Unreachable:
        return {"ready": False, "message": CLOSED, "version": ""}
    except net.BadAnswer:
        root = None
    if not isinstance(root, dict) or "voicebox" not in str(root.get("message", "")).lower():
        return {"ready": False, "version": "",
                "message": "Another program is using Voicebox's address on this PC, so Voicebox can't be reached."}
    return {"ready": True, "message": "", "version": str(root.get("version") or "")[:40]}


def _engine_of(profile: dict) -> str:
    engine = profile.get("default_engine") or profile.get("preset_engine") or ""
    return str(engine).strip().lower()


def _profiles(base: str, fresh: bool = False) -> list[dict]:
    """The person's voices, kept for a few seconds so speaking a clip's
    worth of sentences does not ask for the list each time."""
    now = time.monotonic()
    with _cache_lock:
        held = _profiles_cache.get(base)
        if held and not fresh and now - held[0] < 20:
            return held[1]
    try:
        data = net.get_json(base, "/profiles", timeout=10.0)
    except net.Unreachable:
        raise VoiceError(CLOSED, "not_running") from None
    except net.BadAnswer as e:
        if e.status == 404:
            raise VoiceError("This version of Voicebox can't be used from Clips Kitty. Update Voicebox.",
                             "refused") from None
        raise VoiceError(f"Voicebox could not list its voices: {e}", "refused") from None
    found = [p for p in data if isinstance(p, dict) and isinstance(p.get("id"), str) and p["id"]] \
        if isinstance(data, list) else []
    found = [p for p in found if _engine_of(p) not in LEFT_OUT]
    with _cache_lock:
        _profiles_cache[base] = (now, found)
    return found


def _downloaded(base: str) -> dict[str, bool] | None:
    """Model name -> downloaded, or None when Voicebox does not say."""
    try:
        data = net.get_json(base, "/models/status", timeout=10.0)
    except (net.Unreachable, net.BadAnswer):
        return None
    models = data.get("models") if isinstance(data, dict) else None
    if not isinstance(models, list):
        return None
    return {str(m.get("model_name")): bool(m.get("downloaded")) for m in models if isinstance(m, dict)}


def _model_ready(engine: str, downloaded: dict[str, bool] | None) -> bool | None:
    prefix = _MODEL_PREFIX.get(engine)
    if downloaded is None or prefix is None:
        return None
    hits = [ok for name, ok in downloaded.items() if name.startswith(prefix)]
    return any(hits) if hits else None


def _base(base: str | None) -> str:
    """The address to ask, or a plain refusal when the developer's address
    override names somewhere that is not this PC."""
    try:
        return base or address()
    except net.NotLocal as e:
        raise VoiceError(str(e), "not_local") from None


def voices(language: str | None = None, base: str | None = None) -> list[dict]:
    """The person's Voicebox voices that can speak `language`: the ones made
    for that language, and every cloned voice (a cloned voice speaks other
    languages too). The language's own voices come first. None for a
    language Voicebox does not take at all."""
    base = _base(base)
    if not net.listening(base):
        raise VoiceError(CLOSED, "not_running")
    if language and language not in LANGUAGES:
        return []
    downloaded = _downloaded(base)
    out = []
    for p in _profiles(base, fresh=True):
        own = str(p.get("language") or "").lower()
        preset = str(p.get("voice_type") or "cloned").lower() == "preset"
        if language and own != language and preset:
            continue
        engine = _engine_of(p)
        ready = _model_ready(engine, downloaded)
        out.append({
            "id": f"{ID}:{p['id']}",
            "voice": p["id"],
            "name": str(p.get("name") or p["id"])[:80],
            "country": "",
            "quality": "",
            "language": own,
            "engine": ID,
            "model": engine,
            # False = Voicebox has not downloaded this voice's model yet.
            "ready": ready is not False,
            "commercial_ok": True if engine in _CLEARED else None,
        })
    out.sort(key=lambda e: (bool(language) and e["language"] != language, e["name"].lower()))
    return out


def default_voice(language: str, base: str | None = None) -> str | None:
    found = voices(language, base)
    for entry in found:
        if entry["ready"] and entry["language"] == language:
            return entry["voice"]
    for entry in found:
        if entry["ready"]:
            return entry["voice"]
    return None


def speak(text: str, language: str, voice: str, out: Path, seed: int | None = None,
          base: str | None = None, timeout: float = 600.0) -> int:
    """Speak with one of the person's Voicebox voices. Returns the sample
    rate. Voicebox has no pace setting, so there is none here."""
    base = _base(base)
    if not net.listening(base):
        raise VoiceError(CLOSED, "not_running")
    if language not in LANGUAGES:
        raise VoiceError(f"Voicebox does not speak this language ({language}).", "no_voice")
    profile = next((p for p in _profiles(base) if p["id"] == voice), None)
    if profile is None:
        profile = next((p for p in _profiles(base, fresh=True) if p["id"] == voice), None)
    if profile is None:
        raise VoiceError("That voice is no longer in Voicebox. Pick another voice.", "no_voice")
    body: dict = {"profile_id": profile["id"], "text": text, "language": language}
    engine = _engine_of(profile)
    if engine:
        body["engine"] = engine
    if seed is not None:
        body["seed"] = int(seed) % (2 ** 31)
    try:
        got, _kind = net.request(base, "/generate/stream", body=body, timeout=timeout,
                                 limit=net.MAX_AUDIO_BYTES)
    except net.Unreachable:
        raise VoiceError(CLOSED, "not_running") from None
    except net.BadAnswer as e:
        if e.status == 404:
            raise VoiceError("This version of Voicebox can't be used from Clips Kitty. Update Voicebox.",
                             "refused") from None
        if e.status == 400:
            # What Voicebox answers when the voice's model is not downloaded.
            raise VoiceError(f"Voicebox could not use this voice: {e} Open Voicebox and try the voice "
                             f"there first.", "refused") from None
        raise VoiceError(f"Voicebox could not say this: {e}", "refused") from None
    try:
        rate, _seconds = wav.save_answers([got], Path(out))
    except wav.NotAudio as e:
        raise VoiceError(f"Voicebox did not send speech back ({e}).", "refused") from None
    return rate
