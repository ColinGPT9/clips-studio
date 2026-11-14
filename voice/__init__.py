"""The voice layer: one way to make speech, whichever voice does the talking.

Everything in Clips Kitty that speaks goes through here (dubbing, the local
narration script, the /voice routes the voice-over tool and the promo kit
use), so the person's choice in Settings → Voice holds everywhere:

- **Built-in** (the default): Kokoro, inside the engine, for the languages
  it speaks well, and Piper, as before, for the rest. Nothing to install,
  nothing downloaded for Kokoro, works offline.
- **Voicebox**, **VoiceStudio** or **another local voice app**: optional,
  the person's own copy, used only while it is open. Clips Kitty never
  ships, downloads or starts them, and only ever talks to this PC
  (voice/net.py).

    voice.speak(data_dir, "Hello.", language="en")   -> Spoken (a WAV file)
    voice.voices(data_dir, "es")                      -> the voices to choose from
    voice.status(data_dir)                            -> which engines are usable now
    voice.align.words(wav, text, ...)                 -> when each word is said

A voice id says which engine speaks it (voice/base.py); a Piper id is written
as it always was and always means Piper, so a dub with a chosen Piper voice
is exactly what it was before this layer.

Nothing here is imported by a plain clipping run.
"""

from __future__ import annotations

import hashlib
import secrets as _secrets
from dataclasses import dataclass
from pathlib import Path

from voice import settings as voice_settings
from voice.base import VoiceError, join, split

__all__ = [
    "Route",
    "Spoken",
    "VoiceError",
    "can_dub",
    "check_dub",
    "default_voice",
    "default_voice_problem",
    "dub_plan",
    "dub_speaker",
    "dubbing_available",
    "preview",
    "route",
    "speak",
    "status",
    "voices",
]

BUILTIN = "builtin"
OPTIONAL = ("voicebox", "voicestudio", "custom")
ENGINE_NAMES = {
    BUILTIN: "Built-in",
    "voicebox": "Voicebox",
    "voicestudio": "VoiceStudio",
    "custom": "Another local voice app",
}
# Which engines' voices belong to each choice in Settings → Voice.
_OWN = {BUILTIN: ("kokoro", "piper"), "voicebox": ("voicebox",), "voicestudio": ("voicestudio",),
        "custom": ("custom",)}
_LICENCE_NOTES = {
    "voicebox": "",
    "voicestudio": "VoiceStudio's usual voice may not be used in videos that earn money.",
    "custom": "Whether a voice may be used in videos that earn money is for you to check with the "
              "app it comes from.",
}


@dataclass
class Spoken:
    path: Path
    sample_rate: int
    duration: float
    engine: str          # kokoro, piper, voicebox, voicestudio or custom
    voice: str           # the voice's id, as voices() lists it
    language: str
    seed: int | None = None
    note: str = ""       # set when the built-in voice stood in for a closed voice app


@dataclass
class Route:
    """Which engine will speak, and with which of its voices."""

    engine: str          # kokoro, piper, voicebox, voicestudio or custom
    native: str | None   # the engine's own name for the voice; None = Piper's default
    note: str = ""

    @property
    def voice_id(self) -> str:
        return join(self.engine, self.native or "")


def _voices_dir(data_dir, voices_dir=None) -> Path:
    return Path(voices_dir) if voices_dir is not None else Path(data_dir or ".") / "voices"


def _optional_status(kind: str, s: dict) -> dict:
    """{"ready", "code", "message", "address", "version"} for one voice app."""
    from voice import net
    from voice.engines import openai_local, voicebox

    if kind == "voicebox":
        got = voicebox.status()
        try:
            got["address"] = voicebox.address()
        except net.NotLocal:
            got["address"] = ""
        got["code"] = "ok" if got["ready"] else (
            "wrong_app" if got["message"].startswith("Another program") else
            "not_local" if not got["address"] else "not_running")
        return got
    got = openai_local.status(kind, s)
    got["version"] = ""
    if got["ready"]:
        got["code"] = "ok"
    elif kind == "custom" and not str(s.get("custom_address") or "").strip():
        got["code"] = "no_address"
    else:
        got["code"] = "not_local" if not got["address"] else "not_running"
    return got


# ---- status -----------------------------------------------------------------


def _piper_languages() -> list[str]:
    from multilingual import dub

    return list(dub.VOICES)


def status(data_dir) -> dict:
    """Which engines can speak right now, and what is chosen.

    Looks at each optional voice app once (a connection attempt to this PC,
    half a second at most each, all at the same time)."""
    from concurrent.futures import ThreadPoolExecutor

    from voice.engines import kokoro, piper

    s = voice_settings.load(data_dir)
    k_ready, k_why = kokoro.readiness()
    p_ready = piper.installed()
    engines = [{
        "id": BUILTIN,
        "name": ENGINE_NAMES[BUILTIN],
        "optional": False,
        "ready": bool(k_ready or p_ready),
        "code": "ok" if k_ready else "files_missing",
        # Why Kokoro can't speak (the voice files are missing, with the
        # command that fetches them). Dubbing still works through Piper.
        "message": "" if k_ready else k_why,
        "address": "",
        "version": "",
        "languages": sorted(set(kokoro.LANGUAGES if k_ready else ()) | set(_piper_languages() if p_ready else ())),
        "kokoro": {"ready": k_ready, "message": "" if k_ready else k_why,
                   "languages": sorted(kokoro.LANGUAGES)},
        "piper": {"ready": p_ready, "languages": sorted(_piper_languages())},
        "licence_note": "",
    }]
    with ThreadPoolExecutor(max_workers=len(OPTIONAL)) as pool:
        found = list(pool.map(lambda kind: _optional_status(kind, s), OPTIONAL))
    for kind, got in zip(OPTIONAL, found):
        engines.append({
            "id": kind,
            "name": ENGINE_NAMES[kind],
            "optional": True,
            "ready": bool(got["ready"]),
            "code": got["code"],
            "message": got.get("message", ""),
            "address": got.get("address", ""),
            "version": got.get("version", ""),
            "languages": [],
            # For the engine's tooltip: who answers for a voice's licence.
            "licence_note": _LICENCE_NOTES[kind],
        })
    return {"engine": s["engine"], "engines": engines, "settings": s}


# ---- choosing who speaks ----------------------------------------------------


def _builtin_default(language: str) -> tuple[str, str | None]:
    from voice.engines import kokoro, piper

    if kokoro.speaks(language) and kokoro.readiness()[0]:
        return "kokoro", kokoro.default_voice(language)
    if piper.speaks(language) and piper.installed():
        return "piper", None
    if kokoro.speaks(language):
        raise VoiceError(kokoro.readiness()[1], "files_missing")
    raise VoiceError(f"There is no built-in voice for this language ({language}).", "no_voice")


def _builtin_default_id(language: str) -> str | None:
    from voice.engines import piper

    try:
        engine, native = _builtin_default(language)
    except VoiceError:
        return None
    return piper.default_voice(language) if engine == "piper" else join(engine, native or "")


def _stored_default(s: dict, language: str) -> str | None:
    """The person's default voice for a language, when it is one of the
    chosen engine's voices (a default picked for Voicebox is not used once
    Built-in is chosen again, and comes back with Voicebox)."""
    stored = s["voices"].get(language)
    if stored and split(stored)[0] in _OWN[s["engine"]] and not default_voice_problem(language, stored):
        # (A default whose engine does not speak the language can only come
        # from a file edited by hand, and is passed over: POST /voice/engine
        # refuses to save one.)
        return stored
    return None


def route(data_dir, language: str, voice_id: str | None = None) -> Route:
    """Who speaks `language`: the voice asked for, else the person's default
    for the language, else the chosen engine's own default.

    VoiceError when nothing can: the chosen voice app is closed and standing
    in is not allowed ("not_running"), its address is not this PC
    ("not_local"), the built-in voice's files are missing ("files_missing"),
    or there is no voice for the language ("no_voice").
    """
    from voice.engines import kokoro, openai_local, voicebox

    s = voice_settings.load(data_dir)
    chosen = voice_id or _stored_default(s, language)
    if chosen:
        engine, native = split(chosen)
    elif s["engine"] == BUILTIN:
        engine, native = _builtin_default(language)
    else:
        engine, native = s["engine"], None
    note = ""

    if engine in OPTIONAL:
        got = _optional_status(engine, s)
        name = ENGINE_NAMES[engine] if engine != "custom" else "Your voice app"
        if not got["ready"]:
            if not s["fallback_to_builtin"]:
                raise VoiceError(got.get("message") or f"{name} is not running.",
                                 "not_local" if got["code"] in ("not_local", "no_address") else "not_running")
            engine, native = _builtin_default(language)
            note = f"{name} is not running, so the built-in voice is used."
        elif not native:
            if engine == "voicebox":
                native = voicebox.default_voice(language)
            else:
                native = openai_local.default_voice(engine, s, language)
            if not native:
                raise VoiceError(f"{name} has no voice for this language. Pick a voice in Settings.",
                                 "no_voice")
    if engine == "kokoro":
        if not kokoro.speaks(language):
            raise VoiceError(f"The built-in voice does not speak this language ({language}).", "no_voice")
        ready, why = kokoro.readiness()
        if not ready:
            raise VoiceError(why, "files_missing")
        # "kokoro:" with no name is the language's usual voice, by its name.
        native = native or kokoro.default_voice(language)
    return Route(engine, native or None, note)


def default_voice(data_dir, language: str) -> str | None:
    """The id of the voice a request with no voice would use, or None."""
    from voice.engines import piper

    try:
        r = route(data_dir, language)
    except VoiceError:
        return None
    if r.engine == "piper" and not r.native:
        return piper.default_voice(language)
    return r.voice_id


def default_voice_problem(language: str, voice_id: str | None) -> str:
    """Why `voice_id` can't be saved as the default voice for `language`
    ("" when it can): a voice whose engine does not speak the language would
    leave every dub in it unspoken. Contacts nothing; a voice of VoiceStudio
    or another local voice app is taken as given, since only that app knows
    what its voices speak."""
    from multilingual.languages import english_name
    from voice.engines import kokoro, piper, voicebox

    engine, _native = split(voice_id)
    if engine == "kokoro" and not kokoro.speaks(language):
        return f"The built-in voice does not speak {english_name(language)}."
    if engine == "piper" and not piper.speaks(language):
        return f"There is no Piper voice for {english_name(language)}."
    if engine == "voicebox" and language not in voicebox.LANGUAGES:
        return f"Voicebox does not speak {english_name(language)}."
    return ""


# ---- speaking ---------------------------------------------------------------


def _say(r: Route, s: dict, text: str, language: str, out: Path, voices_dir: Path, speed: float,
         seed: int | None, length_scale: float | None) -> int:
    from voice.engines import kokoro, openai_local, piper, voicebox

    pace = (1.0 / float(length_scale)) if length_scale else float(speed)
    if r.engine == "piper":
        return piper.speak(text, language, r.native, out, voices_dir, speed=speed, length_scale=length_scale)
    if r.engine == "kokoro":
        return kokoro.speak(text, language, r.native, out, speed=pace, seed=seed)
    if r.engine == "voicebox":
        return voicebox.speak(text, language, r.native or "", out, seed=seed)
    return openai_local.speak(r.engine, s, text, language, r.native or "", out, speed=pace, seed=seed)


def speak(data_dir, text: str, language: str = "en", voice: str | None = None, seed: int | None = None,
          speed: float = 1.0, out: Path | None = None, voices_dir: Path | None = None) -> Spoken:
    """Speak `text` to a WAV file.

    `voice` is an id from voices(); None is the person's default. `speed` is
    1.0 for the voice's own pace, above 1 faster (Voicebox has no pace and
    ignores it). With a `seed` the built-in Kokoro voice gives the same bytes
    for the same request on the same PC; an optional voice app is handed the
    seed and may or may not honour it; Piper takes none. `out` defaults to a
    new file under the data folder's voice/spoken/ folder. VoiceError says in
    plain words why it could not.
    """
    from voice import wav
    from voice.engines import piper

    if not isinstance(text, str) or not text.strip():
        raise VoiceError("There is nothing to say.", "failed")
    r = route(data_dir, language, voice)
    s = voice_settings.load(data_dir)
    if out is None:
        out = Path(data_dir) / "voice" / "spoken" / f"{_secrets.token_hex(16)}.wav"
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    rate = _say(r, s, text, language, out, _voices_dir(data_dir, voices_dir), speed, seed, None)
    _rate, seconds = wav.info(out)
    voice_id = r.voice_id
    if r.engine == "piper":
        # The voice that spoke: an id Piper does not know is spoken by the
        # language's usual voice, and the answer says so.
        voice_id = piper.spoken_by(r.native, language)
    return Spoken(out, rate, seconds, r.engine, voice_id, language, seed, r.note)


# ---- listing voices ---------------------------------------------------------


def _builtin_voices(language: str, voices_dir: Path) -> list[dict]:
    from voice.engines import kokoro, piper

    out: list[dict] = []
    if kokoro.speaks(language):
        out += kokoro.voices(language)
    out += piper.voices(language, voices_dir)
    return out


def voices(data_dir, language: str, engine: str | None = None, voices_dir: Path | None = None) -> dict:
    """The voices to choose from for `language`, from the chosen engine (or
    from `engine`: builtin, voicebox, voicestudio or custom).

    {"engine", "voices": [...], "default": id or None, "message", "code"}.
    Each voice: id, name, country, quality, language, engine, and
    commercial_ok (True = cleared for videos that earn money, False = not,
    None = not known). When the chosen voice app is closed the list is empty
    and `message` says what to do, unless standing in is allowed, in which
    case the built-in voices are listed and `message` says so."""
    from voice.engines import openai_local, voicebox

    s = voice_settings.load(data_dir)
    engine = engine if engine in voice_settings.ENGINES else s["engine"]
    vdir = _voices_dir(data_dir, voices_dir)
    message, code = "", "ok"
    builtin_listed = engine == BUILTIN
    if engine == BUILTIN:
        found = _builtin_voices(language, vdir)
    else:
        try:
            found = voicebox.voices(language) if engine == "voicebox" else openai_local.voices(engine, s, language)
        except VoiceError as e:
            message, code, found = str(e), e.code, []
            if s["fallback_to_builtin"] and engine == s["engine"]:
                found = _builtin_voices(language, vdir)
                builtin_listed = True
                message = f"{message} The built-in voice is used until then."
    if not found and not message and engine in OPTIONAL:
        from multilingual.languages import english_name

        name = ENGINE_NAMES[engine] if engine != "custom" else "Your voice app"
        message, code = f"{name} has no voice for {english_name(language)}.", "no_voice"
    default = None
    if found:
        ids = {v["id"] for v in found}
        stored = s["voices"].get(language)
        if stored in ids:
            default = stored
        elif builtin_listed:
            default = _builtin_default_id(language)
        else:
            ready = [v for v in found if v.get("ready", True)]
            default = (ready or found)[0]["id"]
    return {"engine": engine, "voices": found, "default": default, "message": message, "code": code}


def can_dub(data_dir, language: str) -> bool:
    """Whether a dub in `language` is possible with the person's settings
    (without contacting anything)."""
    from voice.engines import kokoro, piper, voicebox

    if piper.installed() and piper.speaks(language):
        return True
    if kokoro.speaks(language) and kokoro.readiness()[0]:
        return True
    engine = voice_settings.load(data_dir)["engine"]
    if engine == "voicebox":
        return language in voicebox.LANGUAGES
    return engine in ("voicestudio", "custom")


def dubbing_available(data_dir) -> bool:
    from voice.engines import kokoro, piper

    if piper.installed() or kokoro.readiness()[0]:
        return True
    return voice_settings.load(data_dir)["engine"] in OPTIONAL


# ---- dubbing ----------------------------------------------------------------


class DubSpeaker:
    """One language's voice for a dub, when it is not a Piper voice.
    Dubbing (multilingual/dub.py) asks it for each sentence."""

    def __init__(self, data_dir, language: str, r: Route, voices_dir: Path):
        self.language = language
        self.route = r
        self.note = r.note
        self._settings = voice_settings.load(data_dir)
        self._voices_dir = voices_dir
        # Voicebox has no pace setting: speaking a sentence again to fit its
        # slot would only make the same take twice. Dubbing fits such a take
        # by changing the finished sound's tempo instead (dub._fit).
        self.paced = r.engine != "voicebox"
        # Why the last sentence that could not be spoken could not, in plain
        # words ("" while every sentence has been spoken). One such sentence
        # stops the language's dub (multilingual/dub.py).
        self.failed = ""

    def _name(self) -> str:
        if self.route.engine == "kokoro":
            return "The built-in voice"
        if self.route.engine == "custom":
            return "Your voice app"
        return ENGINE_NAMES.get(self.route.engine, "The voice")

    def say(self, text: str, out: Path, length_scale: float | None = None) -> bool:
        """Speak one sentence into `out`. False (and a printed reason) when
        it could not, as Piper's own speech step answers."""
        try:
            _say(self.route, self._settings, text, self.language, Path(out), self._voices_dir, 1.0, None,
                 length_scale)
        except VoiceError as e:
            print(f"      (speech failed: {e})")
            self.failed = str(e)
            return False
        if not (Path(out).exists() and Path(out).stat().st_size > 0):
            self.failed = f"{self._name()} made no sound file."
            return False
        from voice import wav

        if wav.is_silent(Path(out)):
            # A voice app that answers with a sound file that has no sound
            # in it. The built-in voice refuses its own silence already.
            self.failed = f"{self._name()} sent silence instead of speech."
            print(f"      (speech failed: {self.failed})")
            return False
        # Piper hands over every sentence at full level, and the dub's mix is
        # set for that. Kokoro (and a voice app) speak quieter, so their
        # sentences are brought to the same level before the mix.
        wav.to_full_level(Path(out))
        return True


def dub_plan(data_dir, language: str, voice_id: str | None = None,
             voices_dir: Path | None = None) -> tuple[DubSpeaker | None, str | None]:
    """Who speaks a dub in `language`: (speaker, Piper voice).

    A speaker of None means Piper, exactly as dubbing always did it (the
    caller keeps its own Piper path), with the Piper voice to use: the one
    asked for, else the person's default for the language (Settings → Voice),
    else None for Piper's usual voice. VoiceError when the person's choice
    can't speak right now."""
    r = route(data_dir, language, voice_id)
    if r.note:
        print(f"      ({r.note})")
    if r.engine == "piper":
        return None, r.native
    return DubSpeaker(data_dir, language, r, _voices_dir(data_dir, voices_dir)), None


def dub_speaker(data_dir, language: str, voice_id: str | None = None,
                voices_dir: Path | None = None) -> DubSpeaker | None:
    """dub_plan's speaker alone."""
    return dub_plan(data_dir, language, voice_id, voices_dir)[0]


def check_dub(data_dir, languages: list[str], voice_choice: dict | None = None) -> list[str]:
    """Before a dubbing job starts: is the person's voice app there?

    Raises VoiceError when a language would be spoken by a voice app that is
    not running (or an address that is not this PC) and standing in is not
    allowed, so the job stops and says why instead of dubbing in a voice
    nobody picked. The same when a built-in voice was picked that this copy
    can't speak (its files are missing): the job says so, it does not finish
    without the dub. Returns the notes to print when the built-in voice
    stands in. A language with no voice at all is not an error here: it is
    skipped later with its own line, as it always was."""
    notes: list[str] = []
    for language in languages:
        try:
            r = route(data_dir, language, (voice_choice or {}).get(language))
        except VoiceError as e:
            if e.code in ("not_running", "not_local"):
                raise
            if e.code == "files_missing" and can_dub(data_dir, language):
                # Another voice speaks this language, so the person was
                # offered a dub and picked this voice for it.
                raise
            continue
        if r.note and r.note not in notes:
            notes.append(r.note)
    return notes


# ---- hearing a voice --------------------------------------------------------


def preview(data_dir, language: str, voice: str | None = None, voices_dir: Path | None = None) -> Path:
    """A spoken sample of one voice, in the language being auditioned.

    A Piper voice's sample is the file it always was
    (previews/voice_<name>.wav, made once). A built-in Kokoro voice's is made
    once too. A voice from the person's own voice app is spoken fresh each
    time, since the voice behind an id can change there."""
    from multilingual.languages import sample_text

    sample = sample_text(language)
    if sample is None:
        raise VoiceError(f"There is no sample sentence for this language ({language}).", "no_voice")
    r = route(data_dir, language, voice)
    previews = Path(data_dir) / "previews"
    previews.mkdir(parents=True, exist_ok=True)
    vdir = _voices_dir(data_dir, voices_dir)
    if r.engine == "piper":
        from multilingual import dub
        from multilingual.voices import resolve

        if not dub.available():
            raise VoiceError("This copy has no Piper voices installed.", "no_voice")
        name = dub.ensure_voice(language, vdir, r.native)
        if name is None:
            raise VoiceError(f"There is no voice for this language ({language}).", "no_voice")
        _n, speaker = resolve(r.native, language)
        safe = f"{name}_{speaker}" if speaker is not None else name
        out = previews / f"voice_{safe}.wav"
        if not out.exists() and not dub._speak(sample, name, vdir, out, speaker=speaker):
            raise VoiceError("The voice could not make a sample.", "failed")
        return out
    # Never a file name built from the voice's id: a digest of it.
    digest = hashlib.sha256(f"{r.engine}\n{r.native}\n{language}".encode()).hexdigest()[:24]
    out = previews / f"voice_{r.engine}_{digest}.wav"
    if r.engine == "kokoro" and out.exists() and out.stat().st_size > 44:
        return out
    _say(r, voice_settings.load(data_dir), sample, language, out, vdir, 1.0, None, None)
    return out
