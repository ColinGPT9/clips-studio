"""Settings → Voice, and speech on request (editor suite step V1): the
routes over voice/ (the voice layer).

- GET  /voice           which voice engines can speak now, and what is chosen
- POST /voice/engine    choose the engine, default voices, the address of
                        another local voice app, and whether the built-in
                        voice may stand in for a closed voice app
- GET  /voice/voices    the voices to choose from for a language
- POST /voice/speak     text to a WAV, with word times when asked
- GET  /voice/preview   a spoken sample of one voice
- GET  /voice/audio/{file}   the WAV a speak or preview answer points at

Every route but the last is internal (server/api_stability.py) and guarded
like the project routes: the desktop app's session secret in the
X-Clips-Kitty-Session header, and an Origin that is the app's own
(server/projects_api.local_origin). They make this PC speak, and can point
the engine at a voice app on this PC, so a web page must not reach them.

The app's window may only play sound from this API (its content policy has
no blob: for media), and a player cannot send a header. So speak and preview
answer with a `url` under /voice/audio/ whose file name is a random 128-bit
token only that answer contains, and that one route takes no header. It
serves WAV files this module made and nothing else.

The optional voice apps are contacted from here, the Python engine, never
from the window, and only at this PC's own loopback address (voice/net.py).
"""

from __future__ import annotations

import re
import secrets
import threading
import time
from pathlib import Path

from fastapi import Depends, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field

from plugins import session
from server.projects_api import local_origin

# One request's text. About five minutes of speech; a longer script is
# spoken in parts (the voice-over tool does that by scene).
TEXT_MAX = 5000
KEEP_SECONDS = 24 * 3600        # a spoken file is kept this long, then removed
_TOKEN_FILE = re.compile(r"\A[0-9a-f]{32}\.wav\Z")
_LANGUAGE = re.compile(r"\A[a-z]{2,3}\Z")

_STATUS = {"not_running": 409, "files_missing": 409, "not_local": 400, "no_voice": 400,
           "refused": 502, "failed": 500}


class EngineIn(BaseModel):
    """Any of these; what is left out stays as it is."""

    model_config = ConfigDict(extra="forbid")

    engine: str | None = None                      # builtin, voicebox, voicestudio or custom
    voices: dict[str, str | None] | None = None    # {language: voice id}; null clears one
    custom_address: str | None = Field(default=None, max_length=300)
    custom_model: str | None = Field(default=None, max_length=120)
    fallback_to_builtin: bool | None = None


class SpeakIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str = Field(min_length=1, max_length=TEXT_MAX)
    language: str = "en"
    voice: str | None = Field(default=None, max_length=300)
    seed: int | None = Field(default=None, ge=0, le=2 ** 31 - 1)
    speed: float = Field(default=1.0, ge=0.5, le=2.0)
    words: bool = False       # also say when each word is spoken (slower: runs Whisper)


def install(app, *, config: dict, data_dir: Path, session_secret: str) -> None:
    data_dir = Path(data_dir)
    spoken_dir = data_dir / "voice" / "spoken"
    # token file name -> a preview's file elsewhere in the data folder
    served: dict[str, Path] = {}
    served_lock = threading.Lock()

    def guard(request: Request) -> None:
        if not session.matches(session_secret, request.headers.get(session.HEADER)):
            raise HTTPException(403, f"This needs the {session.HEADER} header. The desktop app sends it; a "
                                     f"script finds it in {session.path(data_dir)}.")
        if not local_origin(request.headers.get("origin"), request.headers.get("host")):
            raise HTTPException(403, "Voices can only be used from the Clips Kitty app on this PC.")

    guarded = [Depends(guard)]

    def refused(e) -> HTTPException:
        return HTTPException(_STATUS.get(e.code, 500), {"message": str(e), "code": e.code})

    def language_of(value: str) -> str:
        value = (value or "").strip().lower()
        if not _LANGUAGE.match(value):
            raise HTTPException(400, {"message": "language is a two-letter code like en or es.",
                                      "code": "no_voice"})
        return value

    def prune() -> None:
        """Remove spoken files older than a day. Never raises."""
        cutoff = time.time() - KEEP_SECONDS
        try:
            for item in spoken_dir.iterdir():
                if _TOKEN_FILE.match(item.name) and item.stat().st_mtime < cutoff:
                    item.unlink(missing_ok=True)
        except OSError:
            pass

    @app.get("/voice", dependencies=guarded)
    def voice_status():
        """The engines (Built-in, Voicebox, VoiceStudio, another local voice
        app), whether each can speak right now with a plain line when it
        can't, and the person's choices."""
        from voice import status

        return status(data_dir)

    @app.post("/voice/engine", dependencies=guarded)
    def choose_engine(body: EngineIn):
        """Save the person's voice choices; answers like GET /voice. Another
        local voice app's address must be an address on this PC."""
        from voice import net, status
        from voice import settings as voice_settings

        if body.engine is not None and body.engine not in voice_settings.ENGINES:
            raise HTTPException(400, {"message": f"engine is one of {', '.join(voice_settings.ENGINES)}.",
                                      "code": "failed"})
        changes = body.model_dump(exclude_unset=True)
        address = changes.get("custom_address")
        if address:
            try:
                changes["custom_address"] = net.local_base(address)
            except net.NotLocal as e:
                raise HTTPException(400, {"message": str(e), "code": "not_local"}) from None
        if "voices" in changes and changes["voices"] is not None:
            from voice import default_voice_problem

            for language, picked in changes["voices"].items():
                language_of(language)
                why = default_voice_problem(language, picked)
                if why:
                    raise HTTPException(400, {"message": why, "code": "no_voice"})
        voice_settings.update(data_dir, **changes)
        return status(data_dir)

    @app.get("/voice/voices", dependencies=guarded)
    def voice_list(language: str, engine: str | None = None):
        """The voices for a language from the chosen engine, or from
        `engine`. A closed voice app answers with an empty list and a plain
        `message`, not an error."""
        from voice import voices

        return voices(data_dir, language_of(language), engine)

    def audio_answer(path: Path, spoken=None) -> dict:
        from voice import wav

        name = path.name
        if path.parent != spoken_dir or not _TOKEN_FILE.match(name):
            name = f"{secrets.token_hex(16)}.wav"
            with served_lock:
                if len(served) > 500:
                    served.clear()
                served[name] = path
        rate, seconds = wav.info(path)
        out = {"id": name[:-4], "url": f"/voice/audio/{name}", "path": str(path),
               "sample_rate": rate, "duration": round(seconds, 3)}
        if spoken is not None:
            out.update({"engine": spoken.engine, "voice": spoken.voice, "language": spoken.language,
                        "seed": spoken.seed, "note": spoken.note})
        return out

    @app.post("/voice/speak", dependencies=guarded)
    def speak_text(body: SpeakIn):
        """Speak text with the person's voice (or `voice`) into a WAV kept
        for a day. `words: true` adds each word's start and end, from the
        app's own transcription of the finished speech."""
        from voice import VoiceError, speak

        language = language_of(body.language)
        if not body.text.strip():
            raise HTTPException(400, {"message": "There is nothing to say.", "code": "failed"})
        prune()
        try:
            spoken = speak(data_dir, body.text, language=language, voice=body.voice or None,
                           seed=body.seed, speed=body.speed)
        except VoiceError as e:
            raise refused(e) from None
        out = audio_answer(spoken.path, spoken)
        if body.words:
            from voice import align

            whisper = config.get("whisper") or {}
            try:
                out["words"] = align.words(spoken.path, body.text, data_dir=data_dir, language=language,
                                           model_size=str(whisper.get("model") or "auto"),
                                           device=str(whisper.get("device") or "auto"),
                                           # Always this PC's own Whisper, whatever the
                                           # app's transcription setting: the spoken
                                           # sound is never sent to an online service.
                                           online=None)
            except Exception as e:  # the speech is made; only its word times are missing
                out["words"] = None
                out["words_error"] = f"The word times could not be worked out ({e.__class__.__name__})."
        return out

    @app.get("/voice/preview", dependencies=guarded)
    def preview_voice(language: str, voice: str | None = None):
        """A short sample of one voice, spoken in `language`. Answers with
        where to play it from (`url`), not the sound itself."""
        from voice import VoiceError, preview

        try:
            path = preview(data_dir, language_of(language), voice or None)
        except VoiceError as e:
            raise refused(e) from None
        return audio_answer(path)

    @app.get("/voice/audio/{file}")
    def spoken_audio(file: str):
        """A WAV that POST /voice/speak or GET /voice/preview made, by the
        random name its answer gave. Anything else is 404."""
        if not _TOKEN_FILE.match(file):
            raise HTTPException(404, "No such sound.")
        with served_lock:
            path = served.get(file)
        if path is None:
            # Asked of the folder's own listing, never built from the name.
            try:
                path = next((p for p in spoken_dir.iterdir() if p.name == file), None)
            except OSError:
                path = None
        if path is None or not path.is_file():
            raise HTTPException(404, "No such sound.")
        return FileResponse(path, media_type="audio/wav", headers={"Cache-Control": "no-store"})
