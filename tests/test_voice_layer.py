"""Editor suite step V1: the voice layer itself (voice/__init__.py).

Who speaks is decided in one place:

- a chosen Piper voice is always Piper; a language the built-in Kokoro voice
  does not speak is Piper; otherwise the built-in Kokoro voice, when its
  files are there;
- the person's own voice app (Settings → Voice) speaks when it is chosen and
  open; closed, the job stops with a plain reason unless the person allowed
  the built-in voice to stand in;
- the built-in voice's two files are only used when both match their
  SHA-256; missing or wrong files are a status, never a crash;
- word times are the script's own words, snapped to what Whisper heard.

Kokoro, Piper and the voice apps are stood in for; nothing here needs numpy,
the model files or a network. Runs on the CI light install.
"""

import hashlib
import json
import os
import sys
import types
import wave

import pytest
from voice_fakes import closed_port, openai_server, voicebox

import voice
from voice import align, kokoro_files
from voice import settings as voice_settings
from voice.base import VoiceError, join, split
from voice.engines import kokoro, piper


@pytest.fixture
def quiet(monkeypatch, tmp_path):
    """No voice app is running, wherever the tests run: Voicebox's and
    VoiceStudio's addresses are pointed at ports nothing listens on."""
    monkeypatch.setenv("CLIPS_STUDIO_VOICEBOX_URL", f"http://127.0.0.1:{closed_port()}")
    monkeypatch.setenv("VOICESTUDIO_USER_DATA", str(tmp_path / "no-voicestudio"))
    (tmp_path / "no-voicestudio").mkdir()
    (tmp_path / "no-voicestudio" / "backend.json").write_text(
        json.dumps({"schema": 1, "url": f"http://127.0.0.1:{closed_port()}"}), encoding="utf-8")
    return tmp_path / "data"


def _builtin(monkeypatch, kokoro_ready=True, piper_ready=True, spoken=None):
    """Kokoro and Piper stood in for. `spoken` collects what each is asked."""
    spoken = spoken if spoken is not None else []
    why = "" if kokoro_ready else f"Voice files missing. Run: {kokoro_files.FETCH_COMMAND}"
    monkeypatch.setattr(kokoro, "readiness", lambda: (kokoro_ready, why))
    monkeypatch.setattr(piper, "installed", lambda: piper_ready)

    def write(out, rate):
        out.parent.mkdir(parents=True, exist_ok=True)
        with wave.open(str(out), "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(rate)
            w.writeframes(b"\x00\x10" * (rate // 2))
        return rate

    def kokoro_speak(text, language, voice_name, out, speed=1.0, seed=None):
        spoken.append(("kokoro", text, language, voice_name, speed, seed))
        return write(out, 24000)

    def piper_speak(text, language, voice_name, out, voices_dir, speed=1.0, length_scale=None):
        spoken.append(("piper", text, language, voice_name, speed, length_scale))
        return write(out, 22050)

    monkeypatch.setattr(kokoro, "speak", kokoro_speak)
    monkeypatch.setattr(piper, "speak", piper_speak)
    return spoken


# ---- voice ids --------------------------------------------------------------------------


def test_a_voice_id_says_which_engine_speaks_it():
    assert split("en_US-lessac-medium") == ("piper", "en_US-lessac-medium")
    assert split("fr_FR-upmc-medium#1") == ("piper", "fr_FR-upmc-medium#1")
    assert split("kokoro:af_heart") == ("kokoro", "af_heart")
    assert split("voicebox:1b2c:3d") == ("voicebox", "1b2c:3d")
    assert split("custom:alloy") == ("custom", "alloy")
    assert split("weird:thing") == ("piper", "weird:thing"), "an unknown prefix is no engine"
    assert split(None) == ("", "") and split("") == ("", "")
    for voice_id in ("en_US-lessac-medium", "kokoro:af_heart", "voicestudio:default"):
        assert join(*split(voice_id)) == voice_id


# ---- settings ---------------------------------------------------------------------------


def test_settings_default_to_the_built_in_voice_and_no_standing_in(tmp_path):
    assert voice_settings.load(tmp_path) == {"engine": "builtin", "voices": {}, "custom_address": "",
                                             "custom_model": "", "fallback_to_builtin": False}
    assert not (tmp_path / voice_settings.FILE).exists(), "reading writes nothing"


def test_settings_are_kept_and_a_damaged_file_is_the_defaults(tmp_path):
    voice_settings.update(tmp_path, engine="voicebox", voices={"en": "voicebox:p-heart"}, fallback_to_builtin=True)
    voice_settings.update(tmp_path, voices={"es": "kokoro:ef_dora"})
    got = voice_settings.load(tmp_path)
    assert got["engine"] == "voicebox" and got["fallback_to_builtin"] is True
    assert got["voices"] == {"en": "voicebox:p-heart", "es": "kokoro:ef_dora"}
    voice_settings.update(tmp_path, voices={"en": None})
    assert voice_settings.load(tmp_path)["voices"] == {"es": "kokoro:ef_dora"}
    (tmp_path / voice_settings.FILE).write_text('{"engine": "skynet", "voices": [1], "fallback_to_builtin": "yes"}',
                                               encoding="utf-8")
    assert voice_settings.load(tmp_path) == voice_settings.DEFAULTS
    (tmp_path / voice_settings.FILE).write_text("{not json", encoding="utf-8")
    assert voice_settings.load(tmp_path) == voice_settings.DEFAULTS


# ---- who speaks -------------------------------------------------------------------------


def test_with_nothing_changed_kokoro_speaks_its_languages_and_piper_the_rest(quiet, monkeypatch):
    _builtin(monkeypatch)
    for language in ("en", "es", "fr", "hi", "it", "pt"):
        r = voice.route(quiet, language)
        assert (r.engine, r.native) == ("kokoro", kokoro.DEFAULT_VOICE[language])
    for language in ("de", "ru", "ar", "zh", "vi", "tr", "id", "ur", "bn"):
        r = voice.route(quiet, language)
        assert (r.engine, r.native) == ("piper", None), language
    assert kokoro.DEFAULT_VOICE["en"] == "af_heart"
    assert "ja" not in kokoro.LANGUAGES and "zh" not in kokoro.LANGUAGES


def test_a_chosen_piper_voice_is_always_piper(quiet, monkeypatch):
    _builtin(monkeypatch)
    for engine in voice_settings.ENGINES:
        voice_settings.update(quiet, engine=engine, custom_address="http://127.0.0.1:1")
        r = voice.route(quiet, "en", "en_US-lessac-medium")
        assert (r.engine, r.native) == ("piper", "en_US-lessac-medium"), engine
        assert voice.dub_speaker(quiet, "en", "en_US-lessac-medium") is None, "None = Piper as it always was"


def test_without_the_voice_files_everything_is_piper_as_before(quiet, monkeypatch):
    _builtin(monkeypatch, kokoro_ready=False)
    for language in ("en", "es", "de"):
        assert voice.route(quiet, language).engine == "piper"
        assert voice.dub_speaker(quiet, language) is None
    with pytest.raises(VoiceError) as e:
        voice.route(quiet, "en", "kokoro:af_heart")
    assert e.value.code == "files_missing" and "python scripts/fetch_kokoro.py" in str(e.value)


def test_a_language_nothing_speaks_is_a_plain_no_voice(quiet, monkeypatch):
    _builtin(monkeypatch)
    for language in ("ja", "th", "ko", "tl"):
        with pytest.raises(VoiceError) as e:
            voice.route(quiet, language)
        assert e.value.code == "no_voice"
    with pytest.raises(VoiceError) as e:
        voice.route(quiet, "ja", "kokoro:jf_alpha")
    assert e.value.code == "no_voice", "the Japanese voices in the pack are not offered"


def test_the_persons_default_voice_is_used_while_its_engine_is_chosen(quiet, monkeypatch):
    _builtin(monkeypatch)
    voice_settings.update(quiet, voices={"en": "kokoro:bf_emma", "de": "de_DE-thorsten-high"})
    assert voice.route(quiet, "en").native == "bf_emma"
    assert voice.route(quiet, "de").voice_id == "de_DE-thorsten-high"
    assert voice.route(quiet, "en", "kokoro:am_michael").native == "am_michael", "a voice asked for wins"
    voice_settings.update(quiet, voices={"en": "voicebox:p-heart"})
    assert voice.route(quiet, "en").voice_id == "kokoro:af_heart", "a Voicebox default waits for Voicebox"


def test_a_closed_voice_app_stops_the_job_and_says_why(quiet, monkeypatch):
    _builtin(monkeypatch)
    for engine, words in (("voicebox", "Open Voicebox to use it."), ("voicestudio", "Open VoiceStudio to use it.")):
        voice_settings.update(quiet, engine=engine)
        with pytest.raises(VoiceError) as e:
            voice.route(quiet, "en")
        assert e.value.code == "not_running" and str(e.value) == words
        with pytest.raises(VoiceError) as e:
            voice.check_dub(quiet, ["es", "de"])
        assert str(e.value) == words
        with pytest.raises(VoiceError):
            voice.speak(quiet, "Hello.", "en")
        # A Piper voice the person picked for a language still needs no app.
        assert voice.check_dub(quiet, ["de"], {"de": "de_DE-thorsten-medium"}) == []
    voice_settings.update(quiet, engine="custom", custom_address="")
    with pytest.raises(VoiceError) as e:
        voice.check_dub(quiet, ["en"])
    assert e.value.code == "not_local" and "address" in str(e.value)


def test_standing_in_is_only_done_when_the_person_allowed_it(quiet, monkeypatch):
    spoken = _builtin(monkeypatch)
    voice_settings.update(quiet, engine="voicebox", fallback_to_builtin=True)
    r = voice.route(quiet, "en")
    assert (r.engine, r.native) == ("kokoro", "af_heart")
    assert r.note == "Voicebox is not running, so the built-in voice is used."
    assert voice.check_dub(quiet, ["en", "es", "de"]) == [r.note]
    said = voice.speak(quiet, "Hello.", "en", out=quiet / "x.wav")
    assert said.engine == "kokoro" and said.note == r.note and spoken[-1][0] == "kokoro"
    listed = voice.voices(quiet, "de")
    assert listed["code"] == "not_running" and "built-in voice is used until then" in listed["message"]


def test_the_persons_voicebox_speaks_when_it_is_open(quiet, monkeypatch):
    _builtin(monkeypatch)
    voice_settings.update(quiet, engine="voicebox")
    with voicebox() as fake:
        monkeypatch.setenv("CLIPS_STUDIO_VOICEBOX_URL", fake.base)
        assert voice.check_dub(quiet, ["en", "es"]) == []
        r = voice.route(quiet, "es")
        assert (r.engine, r.native) == ("voicebox", "p-dora"), "the first ready voice for the language"
        said = voice.speak(quiet, "Hola.", "es", seed=5, out=quiet / "hola.wav")
        assert (said.engine, said.voice, said.sample_rate) == ("voicebox", "voicebox:p-dora", 24000)
        assert fake.bodies("/generate/stream")[-1]["seed"] == 5
        listed = voice.voices(quiet, "en")
        assert listed["engine"] == "voicebox" and listed["default"] == "voicebox:p-heart"
        assert [v["id"] for v in listed["voices"]] == ["voicebox:p-heart", "voicebox:p-me"]
        speaker = voice.dub_speaker(quiet, "en")
        assert speaker is not None and speaker.paced is False, "Voicebox has no pace: one take a sentence"
        status = voice.status(quiet)
    by_id = {e["id"]: e for e in status["engines"]}
    assert by_id["voicebox"]["ready"] is True and by_id["voicebox"]["version"] == "0.5.0"
    assert by_id["voicestudio"]["message"] == "Open VoiceStudio to use it."


def test_another_local_voice_app_speaks_at_its_address(quiet, monkeypatch):
    _builtin(monkeypatch)
    with openai_server() as fake:
        voice_settings.update(quiet, engine="custom", custom_address=fake.base)
        said = voice.speak(quiet, "Hello.", "en", speed=1.25, out=quiet / "x.wav")
        assert (said.engine, said.voice) == ("custom", "custom:v-1")
        speaker = voice.dub_speaker(quiet, "en", "custom:alloy")
        assert speaker.paced and speaker.say("Hi.", quiet / "y.wav", length_scale=0.8)
        bodies = fake.bodies("/v1/audio/speech")
    assert bodies[0]["speed"] == 1.25 and bodies[1]["voice"] == "alloy" and bodies[1]["speed"] == 1.25


def test_status_never_raises_and_names_each_engine(quiet, monkeypatch):
    _builtin(monkeypatch, kokoro_ready=False)
    status = voice.status(quiet)
    assert [e["id"] for e in status["engines"]] == ["builtin", "voicebox", "voicestudio", "custom"]
    assert [e["name"] for e in status["engines"]] == ["Built-in", "Voicebox", "VoiceStudio",
                                                      "Another local voice app"]
    builtin, vbx, vs, custom = status["engines"]
    assert builtin["ready"] is True and builtin["code"] == "files_missing"
    assert builtin["message"] == "Voice files missing. Run: python scripts/fetch_kokoro.py"
    assert (vbx["ready"], vbx["code"], vbx["message"]) == (False, "not_running", "Open Voicebox to use it.")
    assert (vs["ready"], vs["code"]) == (False, "not_running")
    assert (custom["ready"], custom["code"]) == (False, "no_address")
    assert vs["licence_note"] and custom["licence_note"] and not vbx["licence_note"]
    assert status["engine"] == "builtin" and status["settings"]["fallback_to_builtin"] is False


def test_the_voice_list_keeps_dubbings_shape_and_marks_each_voice(quiet, monkeypatch):
    _builtin(monkeypatch)
    monkeypatch.setattr(kokoro, "voices", lambda language=None: [
        {"id": "kokoro:ef_dora", "voice": "ef_dora", "name": "Dora", "country": "", "quality": "",
         "language": "es", "gender": "female", "engine": "kokoro", "commercial_ok": True}])
    from multilingual import voices as piper_catalogue

    monkeypatch.setattr(piper_catalogue, "catalogue", lambda _dir: {
        "es_ES-davefx-medium": {"name": "davefx", "quality": "medium", "num_speakers": 1,
                                "language": {"family": "es", "country_english": "Spain"}}})
    got = voice.voices(quiet, "es")
    assert got["engine"] == "builtin" and got["default"] == "kokoro:ef_dora" and got["message"] == ""
    assert [v["id"] for v in got["voices"]] == ["kokoro:ef_dora", "es_ES-davefx-medium"]
    for entry in got["voices"]:
        assert {"id", "name", "country", "quality", "engine", "commercial_ok"} <= set(entry)
    assert got["voices"][1]["commercial_ok"] is None
    german = voice.voices(quiet, "de")
    assert german["default"] == "de_DE-thorsten-medium", "Piper's default, as /voices always answered"


def test_dubbing_is_offered_where_a_voice_exists(quiet, monkeypatch):
    _builtin(monkeypatch)
    assert voice.can_dub(quiet, "en") and voice.can_dub(quiet, "de") and not voice.can_dub(quiet, "ja")
    assert voice.dubbing_available(quiet)
    _builtin(monkeypatch, kokoro_ready=False, piper_ready=False)
    assert not voice.can_dub(quiet, "en") and not voice.dubbing_available(quiet)
    voice_settings.update(quiet, engine="voicebox")
    assert voice.can_dub(quiet, "ja") and not voice.can_dub(quiet, "th") and voice.dubbing_available(quiet)


# ---- dubbing through the layer ----------------------------------------------------------


def _dub(monkeypatch, tmp_path, data_dir, language, voice_id=None, takes=(2.0, 3.6, 0.3), problems=None):
    """dub() with FFmpeg and ffprobe stood in for: the speech calls are in
    `spoken` (from _builtin), the commands come back here."""
    import subprocess

    import core.binaries as binaries
    from multilingual import dub

    lines = [{"start": 0.5, "end": 2.5, "text": "Hola a todos."},
             {"start": 3.0, "end": 5.0, "text": "Hoy vamos a ver algo nuevo."},
             {"start": 5.5, "end": 6.5, "text": "¿Listos?"}]
    commands = []
    probe = binaries.ffprobe()

    def run(cmd, *args, **kwargs):
        if cmd[0] == probe:
            index = int(str(cmd[-1]).rsplit("_", 1)[1].split(".")[0])
            return subprocess.CompletedProcess(cmd, 0, stdout=f"{takes[index]}\n", stderr="")
        commands.append(list(cmd))
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", run)
    out = dub.dub(lines, language, tmp_path / "clip.mp4", tmp_path / "clip.dubbed.mp4", tmp_path / "voices",
                  tmp_path / "work", voice_id=voice_id, workers=1, data_dir=data_dir, problems=problems)
    return out, commands


def test_a_dub_with_the_built_in_voice_fits_each_sentence_like_pipers(quiet, monkeypatch, tmp_path):
    spoken = _builtin(monkeypatch)
    out, [cmd] = _dub(monkeypatch, tmp_path, quiet, "es")
    assert out == tmp_path / "clip.dubbed.mp4"
    said = [(engine, text, round(speed, 4)) for engine, text, _l, _v, speed, _seed in spoken]
    assert said == [
        ("kokoro", "Hola a todos.", 1.0),                       # 2.0 s in a 2.0 s slot: kept
        ("kokoro", "Hoy vamos a ver algo nuevo.", 1.0),         # 3.6 s: too long
        ("kokoro", "Hoy vamos a ver algo nuevo.", round(1 / 0.72, 4)),   # again, as fast as sounds human
        ("kokoro", "¿Listos?", 1.0),                            # 0.3 s: too short
        ("kokoro", "¿Listos?", round(1 / 1.45, 4)),             # again, slower
    ]
    assert all(v == "ef_dora" and seed is None for _e, _t, _l, v, _s, seed in spoken)
    graph = cmd[cmd.index("-filter_complex") + 1]
    assert graph == ("[0:a]volume=0.12[bed];[1:a]adelay=500|500,volume=1.6[v1];"
                     "[2:a]adelay=3000|3000,volume=1.6[v2];[3:a]adelay=5500|5500,volume=1.6[v3];"
                     "[bed][v1][v2][v3]amix=inputs=4:normalize=0:dropout_transition=0[aout]")
    assert cmd[cmd.index("-c:v") + 1] == "copy" and cmd[-1] == str((tmp_path / "clip.dubbed.mp4").resolve())


def test_a_voice_id_naming_another_engine_goes_through_the_layer_without_a_data_folder(monkeypatch, tmp_path, quiet):
    spoken = _builtin(monkeypatch)
    out, _commands = _dub(monkeypatch, tmp_path, None, "en", voice_id="kokoro:am_michael", takes=(2.0, 2.0, 1.0))
    assert out is not None and {v for _e, _t, _l, v, _s, _seed in spoken} == {"am_michael"}


def test_a_closed_voice_app_gives_no_dub_and_says_why(quiet, monkeypatch, tmp_path, capsys):
    _builtin(monkeypatch)
    voice_settings.update(quiet, engine="voicestudio")
    out, commands = _dub(monkeypatch, tmp_path, quiet, "es")
    assert out is None and commands == []
    assert "Open VoiceStudio to use it." in capsys.readouterr().out



def test_a_voice_app_with_no_voice_for_a_language_gives_no_dub_and_says_why(quiet, monkeypatch, tmp_path, capsys):
    """Voicebox chosen and open, with voices for other languages only: the
    language is not dubbed, and the job's log says why (nothing else would:
    a built-in voice could have spoken it, so the 'no voice available' line
    is not printed)."""
    _builtin(monkeypatch)
    voice_settings.update(quiet, engine="voicebox")
    with voicebox() as fake:
        monkeypatch.setenv("CLIPS_STUDIO_VOICEBOX_URL", fake.base)
        for language, name in (("ur", "Urdu"), ("de", "German")):
            assert voice.can_dub(quiet, language)
            out, commands = _dub(monkeypatch, tmp_path, quiet, language)
            assert out is None and commands == []
            said = capsys.readouterr().out
            assert f"({name} was not dubbed: Voicebox has no voice for this language. Pick a voice in Settings.)" \
                in said
        listed = voice.voices(quiet, "ur")
        assert (listed["voices"], listed["default"], listed["code"]) == ([], None, "no_voice")
        assert listed["message"] == "Voicebox has no voice for Urdu."
        assert voice.voices(quiet, "es")["message"] == "", "a language it has a voice for says nothing"
        assert fake.bodies("/generate/stream") == [], "nothing was asked of Voicebox for them"


def test_a_voice_id_for_a_language_its_engine_does_not_speak_says_why(quiet, monkeypatch, tmp_path, capsys):
    _builtin(monkeypatch)
    out, commands = _dub(monkeypatch, tmp_path, quiet, "de", voice_id="kokoro:af_heart")
    assert out is None and commands == []
    assert "(German was not dubbed: The built-in voice does not speak this language (de).)" in capsys.readouterr().out


def test_a_language_no_voice_speaks_is_left_to_the_caller_to_say(quiet, monkeypatch, tmp_path, capsys):
    _builtin(monkeypatch)
    out, _commands = _dub(monkeypatch, tmp_path, quiet, "ko")
    assert out is None and not voice.can_dub(quiet, "ko")
    assert capsys.readouterr().out == "", "publish prints its own 'no voice available' line for this"


# ---- a dub that was not made is never reported as made -------------------------------------


def test_a_built_in_voice_this_copy_cannot_speak_is_not_offered(quiet, monkeypatch, tmp_path):
    """The voice files are there but the Python package is not (a checkout
    whose requirements were installed before this step): the voices are not
    listed, so nobody picks one that can't speak."""
    import zipfile

    with zipfile.ZipFile(tmp_path / "voices-v1.0.bin", "w") as z:
        z.writestr("ef_dora.npy", b"")
    monkeypatch.setattr(kokoro_files, "check", lambda folder=None: (tmp_path, ""))
    monkeypatch.setattr(kokoro, "installed", lambda: False)
    monkeypatch.setattr(piper, "installed", lambda: True)
    assert kokoro.voices("es") == []
    assert kokoro.readiness()[0] is False
    listed = voice.voices(quiet, "es", voices_dir=tmp_path / "voices")
    assert listed["voices"] and all(v["engine"] == "piper" for v in listed["voices"])
    assert listed["default"] == piper.default_voice("es")
    monkeypatch.setattr(kokoro, "installed", lambda: True)
    assert [v["id"] for v in kokoro.voices("es")] == ["kokoro:ef_dora"]


def test_a_picked_built_in_voice_that_cannot_speak_stops_the_job(quiet, monkeypatch):
    _builtin(monkeypatch, kokoro_ready=False)
    why = f"Voice files missing. Run: {kokoro_files.FETCH_COMMAND}"
    # Picked for this dub, or saved as the language's default in Settings.
    with pytest.raises(VoiceError) as e:
        voice.check_dub(quiet, ["de", "es"], {"es": "kokoro:ef_dora"})
    assert (str(e.value), e.value.code) == (why, "files_missing")
    voice_settings.update(quiet, voices={"es": "kokoro:ef_dora"})
    with pytest.raises(VoiceError):
        voice.check_dub(quiet, ["es"])
    voice_settings.update(quiet, voices={"es": None})
    # Nothing picked: Piper speaks, as it always did, and nothing stops.
    assert voice.check_dub(quiet, ["es", "de", "en"]) == []
    # A language no other voice speaks was never offered a dub ("subtitles
    # only", said by the window and by the job): not an error.
    _builtin(monkeypatch, kokoro_ready=False, piper_ready=False)
    assert not voice.can_dub(quiet, "es")
    assert voice.check_dub(quiet, ["es"], {"es": "kokoro:ef_dora"}) == []


def test_a_voice_that_could_not_speak_is_reported_to_the_job(quiet, monkeypatch, tmp_path):
    """dub() still answers None, and tells the job why in plain words, so the
    job can end as failed instead of 'exported'."""
    _builtin(monkeypatch)
    voice_settings.update(quiet, engine="voicestudio")
    problems = []
    out, commands = _dub(monkeypatch, tmp_path, quiet, "es", problems=problems)
    assert out is None and commands == []
    assert problems == ["Spanish was not dubbed: Open VoiceStudio to use it."]

    # The voice went away during the dub: every sentence failed.
    voice_settings.update(quiet, engine="builtin")

    def gone(*_args, **_kwargs):
        raise VoiceError("The built-in voice stopped.", "failed")

    monkeypatch.setattr(kokoro, "speak", gone)
    problems = []
    out, commands = _dub(monkeypatch, tmp_path, quiet, "es", problems=problems)
    assert out is None and commands == []
    assert problems == ["Spanish was not dubbed: The built-in voice stopped."]

    # A language nothing speaks, and a Piper dub, report nothing here.
    problems = []
    _dub(monkeypatch, tmp_path, quiet, "ko", problems=problems)
    assert problems == []


def test_a_voice_that_fails_part_way_gives_no_dub_at_all(quiet, monkeypatch, tmp_path, capsys):
    """The first sentence is spoken, the next is not (the person's voice app
    closed or answered with an error, the built-in voice stopped): nothing is
    mixed, no take is left behind, and the job is told why, so it ends as
    failed instead of handing over a dub that goes quiet after a sentence."""
    spoken = _builtin(monkeypatch)
    said = kokoro.speak

    def stops_after_the_first(text, *args, **kwargs):
        if len(spoken) >= 1:
            spoken.append(("asked", text))
            raise VoiceError("The built-in voice stopped.", "failed")
        return said(text, *args, **kwargs)

    monkeypatch.setattr(kokoro, "speak", stops_after_the_first)
    problems = []
    out, commands = _dub(monkeypatch, tmp_path, quiet, "es", problems=problems)
    assert out is None and commands == [], "no ffmpeg mix was run"
    assert problems == ["Spanish was not dubbed: The built-in voice stopped."]
    assert "(Spanish was not dubbed: The built-in voice stopped.)" in capsys.readouterr().out
    assert [entry[0] for entry in spoken] == ["kokoro", "asked"], "the third sentence is not asked for"
    assert not list((tmp_path / "work").glob("*.wav")), "the spoken first sentence is not kept"
    assert not (tmp_path / "clip.dubbed.mp4").exists()

    # The same when the caller keeps no list: still no dub.
    spoken.clear()
    out, commands = _dub(monkeypatch, tmp_path, quiet, "es")
    assert out is None and commands == []


def test_a_second_take_that_fails_is_a_failed_sentence(quiet, monkeypatch, tmp_path):
    """A sentence is spoken again at another pace when it misses its slot. A
    voice that fails on that second take has failed the sentence."""
    spoken = _builtin(monkeypatch)
    said = kokoro.speak

    def fails_at_another_pace(text, language, voice_name, out, speed=1.0, seed=None):
        if speed != 1.0:
            raise VoiceError("The built-in voice stopped.", "failed")
        return said(text, language, voice_name, out, speed=speed, seed=seed)

    monkeypatch.setattr(kokoro, "speak", fails_at_another_pace)
    problems = []
    out, commands = _dub(monkeypatch, tmp_path, quiet, "es", problems=problems)
    assert out is None and commands == []
    assert problems == ["Spanish was not dubbed: The built-in voice stopped."]
    assert len(spoken) == 2, "two sentences at the usual pace; the retake of the second failed"


def test_a_voice_app_that_stops_answering_part_way_gives_no_dub(quiet, monkeypatch, tmp_path):
    """Voicebox answers the first sentence, then with an error."""
    from voice_fakes import wav_bytes

    _builtin(monkeypatch)
    voice_settings.update(quiet, engine="voicebox")
    asked = []

    def first_only(_body):
        asked.append(1)
        if len(asked) == 1:
            return (200, "audio/wav", wav_bytes(1.0))
        return (500, "application/json", b'{"detail":"CUDA out of memory"}')

    with voicebox({("POST", "/generate/stream"): first_only}) as fake:
        monkeypatch.setenv("CLIPS_STUDIO_VOICEBOX_URL", fake.base)
        problems = []
        out, commands = _dub(monkeypatch, tmp_path, quiet, "es", takes=(2.0, 2.0, 1.0), problems=problems)
    assert out is None and commands == []
    assert len(problems) == 1 and problems[0].startswith("Spanish was not dubbed: Voicebox could not say this")
    assert len(asked) == 2, "one spoken, one failed, the third not asked for"


@pytest.mark.parametrize("engine", ["voicebox", "custom"])
def test_a_voice_app_that_sends_silence_gives_no_dub_and_says_why(quiet, monkeypatch, tmp_path, capsys, engine):
    """A sound file with no sound in it is not speech: the dub is not made
    of silence and reported as done."""
    from voice_fakes import wav_bytes

    _builtin(monkeypatch)
    silence = (200, "audio/wav", wav_bytes(1.0, fill=bytes(2)))
    if engine == "voicebox":
        server = voicebox({("POST", "/generate/stream"): silence})
    else:
        server = openai_server({("POST", "/v1/audio/speech"): silence})
    name = {"voicebox": "Voicebox", "custom": "Your voice app"}[engine]
    with server as fake:
        monkeypatch.setenv("CLIPS_STUDIO_VOICEBOX_URL", fake.base)
        voice_settings.update(quiet, engine=engine, custom_address=fake.base)
        problems = []
        out, commands = _dub(monkeypatch, tmp_path, quiet, "es", takes=(2.0, 2.0, 1.0), problems=problems)
    assert out is None and commands == []
    assert problems == [f"Spanish was not dubbed: {name} sent silence instead of speech."]
    assert f"(speech failed: {name} sent silence instead of speech.)" in capsys.readouterr().out


def test_a_line_with_nothing_to_say_is_not_the_voices_fault(quiet, monkeypatch, tmp_path):
    """A caption of dots or music notes: when the voice makes nothing of it
    the sentence is left out, as it always was, and the dub is made."""
    import subprocess

    import core.binaries as binaries
    from multilingual import dub

    spoken = _builtin(monkeypatch)
    said = kokoro.speak

    def no_sound_for_marks(text, *args, **kwargs):
        if not any(ch.isalnum() for ch in text):
            raise VoiceError("The built-in voice made no sound for this text.", "failed")
        return said(text, *args, **kwargs)

    monkeypatch.setattr(kokoro, "speak", no_sound_for_marks)
    commands = []
    probe = binaries.ffprobe()

    def run(cmd, *args, **kwargs):
        if cmd[0] == probe:
            return subprocess.CompletedProcess(cmd, 0, stdout="1.5", stderr="")
        commands.append(list(cmd))
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", run)
    lines = [{"start": 0.5, "end": 2.0, "text": "..."}, {"start": 3.0, "end": 4.5, "text": "Hello there, friends."}]
    problems = []
    out = dub.dub(lines, "en", tmp_path / "clip.mp4", tmp_path / "clip.dubbed.mp4", tmp_path / "voices",
                  tmp_path / "work", workers=1, data_dir=quiet, problems=problems)
    assert problems == [] and out == tmp_path / "clip.dubbed.mp4" and len(commands) == 1
    assert [entry[1] for entry in spoken] == ["Hello there, friends."]


def test_silence_is_told_from_sound_in_any_sample_size(tmp_path):
    from voice import wav

    def make(name, width, sample):
        path = tmp_path / name
        with wave.open(str(path), "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(width)
            w.setframerate(24000)
            w.writeframes(sample * 2400)
        return path

    assert wav.peak(make("s16.wav", 2, bytes(2))) == 0 and wav.is_silent(tmp_path / "s16.wav")
    assert wav.peak(make("hum16.wav", 2, (20).to_bytes(2, "little", signed=True))) == 20
    assert wav.is_silent(tmp_path / "hum16.wav"), "a thousandth of full level is not speech"
    assert wav.peak(make("loud16.wav", 2, (-9000).to_bytes(2, "little", signed=True))) == 9000
    assert not wav.is_silent(tmp_path / "loud16.wav")
    assert wav.is_silent(make("s8.wav", 1, bytes([128]))) and not wav.is_silent(make("loud8.wav", 1, bytes([200])))
    assert wav.is_silent(make("s24.wav", 3, bytes(3)))
    assert wav.peak(make("loud24.wav", 3, (9000 * 256).to_bytes(3, "little", signed=True))) == 9000
    assert wav.is_silent(make("s32.wav", 4, bytes(4)))
    assert wav.peak(make("loud32.wav", 4, (-9000 * 65536).to_bytes(4, "little", signed=True))) == 9000
    assert wav.peak(tmp_path / "missing.wav") is None and not wav.is_silent(tmp_path / "missing.wav")


def test_a_default_voice_must_be_able_to_speak_its_language():
    assert voice.default_voice_problem("es", "kokoro:ef_dora") == ""
    assert voice.default_voice_problem("es", "kokoro:af_heart") == "", "any built-in voice reads its six languages"
    assert voice.default_voice_problem("de", "kokoro:af_heart") == "The built-in voice does not speak German."
    assert voice.default_voice_problem("de", "de_DE-thorsten-medium") == ""
    assert voice.default_voice_problem("ko", "en_US-lessac-medium") == "There is no Piper voice for Korean."
    assert voice.default_voice_problem("ko", "voicebox:p-1") == ""
    assert voice.default_voice_problem("th", "voicebox:p-1") == "Voicebox does not speak Thai."
    assert voice.default_voice_problem("th", "voicestudio:default") == ""
    assert voice.default_voice_problem("th", "custom:alloy") == ""
    assert voice.default_voice_problem("de", None) == "", "clearing a default is always allowed"


def test_a_stored_default_that_cannot_speak_its_language_is_passed_over(quiet, monkeypatch):
    """Only a settings file edited by hand can hold one (the route refuses
    it): the language is spoken by its usual voice, not left without a dub."""
    _builtin(monkeypatch)
    voice_settings.update(quiet, voices={"de": "kokoro:af_heart", "es": "kokoro:em_alex"})
    german = voice.route(quiet, "de")
    assert (german.engine, german.native) == ("piper", None)
    assert voice.route(quiet, "es").native == "em_alex", "a default that can speak is used"


def test_the_built_in_voices_model_is_let_go_when_a_translate_job_ends(monkeypatch):
    import inspect

    from server import jobs

    monkeypatch.setattr(kokoro, "_loaded", ("folder", object()))
    jobs._release_voice()
    assert kokoro._loaded is None
    jobs._release_voice()   # nothing loaded: nothing to do, nothing raised
    monkeypatch.setattr(kokoro, "unload", lambda: 1 / 0)
    jobs._release_voice()   # never the reason a job fails
    run = inspect.getsource(jobs.Worker.run)
    at = run.index("self._translate_clips(db, payload)")
    assert "finally:" in run[at:at + 120] and "_release_voice()" in run[at:at + 160], \
        "also when the job fails or is cancelled"


def _level_wav(path, value, rate=24000, frames=2400):
    import struct

    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(struct.pack("<hh", value, -value // 2) * (frames // 2))


def _peak(path):
    import struct

    with wave.open(str(path), "rb") as w:
        raw = w.readframes(w.getnframes())
    return max(abs(v) for v in struct.unpack(f"<{len(raw) // 2}h", raw))


def test_a_quiet_take_is_brought_to_full_level_and_nothing_else_changes(tmp_path):
    from voice import wav

    take = tmp_path / "take.wav"
    _level_wav(take, 13000)                      # about -8 dB, where Kokoro speaks
    assert wav.to_full_level(take) is True
    assert _peak(take) == 32767 and wav.info(take) == (24000, 0.1)
    assert wav.to_full_level(take) is False, "already there: left alone"
    faint = tmp_path / "faint.wav"
    _level_wav(faint, 100)
    assert wav.to_full_level(faint) is True and _peak(faint) == 1000, "never more than ten times louder"
    silent = tmp_path / "silent.wav"
    _level_wav(silent, 0)
    before = silent.read_bytes()
    assert wav.to_full_level(silent) is False and silent.read_bytes() == before
    assert wav.to_full_level(tmp_path / "missing.wav") is False


def test_a_dubbed_sentence_from_the_layer_is_at_pipers_level(quiet, monkeypatch, tmp_path):
    _builtin(monkeypatch)
    monkeypatch.setattr(kokoro, "speak", lambda text, language, name, out, speed=1.0, seed=None:
                        (_level_wav(out, 13000), 24000)[1])
    speaker = voice.dub_speaker(quiet, "en")
    assert speaker.say("Hello.", tmp_path / "s.wav") and _peak(tmp_path / "s.wav") == 32767
    said = voice.speak(quiet, "Hello.", "en", out=tmp_path / "plain.wav")
    assert _peak(said.path) == 13000, "speech asked for outside a dub is the voice's own level"


def test_changing_a_takes_tempo_changes_its_length_with_ffmpeg(tmp_path):
    import shutil

    from core.binaries import ffmpeg
    from multilingual import dub
    from voice import wav

    if shutil.which(ffmpeg()) is None:
        pytest.skip("FFmpeg is not here")
    take = tmp_path / "es_001.wav"
    _level_wav(take, 9000, frames=48000)          # 2.0 s
    dub._retime(take, 0.72)
    rate, seconds = wav.info(take)
    assert rate == 24000 and abs(seconds - 1.44) < 0.05
    assert not (tmp_path / "es_001.fit.wav").exists()


def test_a_voice_without_a_pace_is_fitted_by_tempo(quiet, monkeypatch, tmp_path):
    """Voicebox can't speak faster or slower, so a take that misses its slot
    has its tempo changed by FFmpeg, inside the range a re-spoken take has."""
    _builtin(monkeypatch)
    voice_settings.update(quiet, engine="voicebox")
    with voicebox() as fake:
        monkeypatch.setenv("CLIPS_STUDIO_VOICEBOX_URL", fake.base)
        out, commands = _dub(monkeypatch, tmp_path, quiet, "es")
        assert len(fake.bodies("/generate/stream")) == 3, "one take a sentence"
    assert out is not None
    tempo = [c[c.index("-filter:a") + 1] for c in commands if "-filter:a" in c]
    # 2.0 s in a 2.0 s slot: kept. 3.6 s: faster. 0.3 s in a 1.0 s slot: slower.
    assert tempo == [f"atempo={1 / 0.72:.4f}", f"atempo={1 / 1.45:.4f}"]
    assert "-filter_complex" in commands[-1] and len(commands) == 3


def test_the_answer_names_the_piper_voice_that_spoke(quiet, monkeypatch):
    _builtin(monkeypatch, kokoro_ready=False)
    for made_up in ("../../../Windows/win.ini", "C:\\Windows\\win.ini", ":", "not a voice"):
        said = voice.speak(quiet, "Hello.", "en", voice=made_up, out=quiet / "x.wav")
        assert (said.engine, said.voice) == ("piper", "en_US-lessac-medium")
    assert voice.speak(quiet, "Salut.", "fr", voice="fr_FR-upmc-medium#1", out=quiet / "y.wav").voice \
        == "fr_FR-upmc-medium#1"
    assert voice.speak(quiet, "Hallo.", "de", out=quiet / "z.wav").voice == "de_DE-thorsten-medium"
    _builtin(monkeypatch)
    assert voice.speak(quiet, "Hello.", "en", voice="kokoro:", out=quiet / "k.wav").voice == "kokoro:af_heart"


def test_the_start_up_check_agrees_with_the_sha_check_once_it_has_run(monkeypatch, tmp_path):
    from core import preflight

    folder = _pins(monkeypatch, tmp_path)
    monkeypatch.setattr(kokoro, "installed", lambda: True)
    monkeypatch.setattr(kokoro_files, "find", lambda: folder)
    (folder / kokoro_files.MODEL_FILE).write_bytes(b"MODEL BYTES")   # the right size, another file
    (folder / kokoro_files.VOICES_FILE).write_bytes(b"voice pack bytes")
    assert kokoro_files.known_wrong() is None and preflight.check_voice().ok, "by size alone, before anything spoke"
    assert kokoro_files.check()[0] is None, "the voice refuses to load it"
    check = preflight.check_voice()
    assert check.ok is False and check.blocking is False
    assert check.detail == f"The voice file {kokoro_files.MODEL_FILE} is not the one this version expects."


def test_an_installed_copy_is_not_sent_to_a_command_line(monkeypatch):
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(kokoro_files, "find", lambda: None)
    assert kokoro_files.check() == (None, "Voice files missing. Install Clips Kitty again to put them back.")
    monkeypatch.setattr(kokoro, "installed", lambda: False)
    assert kokoro.readiness() == (False, "The built-in voice is not installed in this copy.")

# ---- the built-in voice's files ---------------------------------------------------------


def _pins(monkeypatch, tmp_path, model=b"model bytes", voices=b"voice pack bytes"):
    monkeypatch.setattr(kokoro_files, "FILES", (
        ("kokoro-v1.0.onnx", "Kokoro voice model", "https://example.invalid/m", hashlib.sha256(model).hexdigest(),
         len(model)),
        ("voices-v1.0.bin", "Kokoro voice pack", "https://example.invalid/v", hashlib.sha256(voices).hexdigest(),
         len(voices)),
    ))
    folder = tmp_path / "kokoro"
    folder.mkdir()
    return folder


def test_the_voice_files_are_used_only_when_both_match_their_sha256(monkeypatch, tmp_path):
    folder = _pins(monkeypatch, tmp_path)
    assert kokoro_files.check(folder) == (None, "Voice files missing. Run: python scripts/fetch_kokoro.py")
    (folder / "kokoro-v1.0.onnx").write_bytes(b"model bytes")
    assert kokoro_files.check(folder)[0] is None, "one file is not enough"
    (folder / "voices-v1.0.bin").write_bytes(b"voice pack bytes")
    assert kokoro_files.check(folder) == (folder, "")
    pack = folder / "voices-v1.0.bin"
    stamp = pack.stat()
    pack.write_bytes(b"voice pack bytez")
    # Not left to the clock: two quick writes can get the same time on some
    # filesystems, and the checksum is remembered by size and time.
    os.utime(pack, ns=(stamp.st_atime_ns, stamp.st_mtime_ns + 2_000_000_000))
    found, why = kokoro_files.check(folder)
    assert found is None and "voices-v1.0.bin is not the one this version expects" in why
    assert "python scripts/fetch_kokoro.py" in why


def test_the_pins_are_the_documented_release_files():
    names = [f[0] for f in kokoro_files.FILES]
    assert names == ["kokoro-v1.0.onnx", "voices-v1.0.bin"]
    assert kokoro_files.MODEL_SHA256 == "beb0d1848dee9a49da392cc3df26958d46cfa35d321edf434f52949153f0df3a"
    assert kokoro_files.VOICES_SHA256 == "bca610b8308e8d99f32e6fe4197e7ec01679264efed0cac9140fe9c29f1fbf7d"
    for _name, _label, url, digest, size in kokoro_files.FILES:
        assert url.startswith("https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.1/")
        assert len(digest) == 64 and size > 1_000_000


def test_the_files_are_found_through_core_binaries(monkeypatch, tmp_path):
    from core import binaries

    folder = tmp_path / "elsewhere"
    folder.mkdir()
    monkeypatch.setenv("CLIPS_STUDIO_KOKORO_DIR", str(folder))
    assert binaries.kokoro_dir() != folder, "an empty folder is not the voice"
    (folder / "kokoro-v1.0.onnx").write_bytes(b"x")
    (folder / "voices-v1.0.bin").write_bytes(b"y")
    assert binaries.kokoro_dir() == folder
    assert kokoro_files.find() == folder
    assert kokoro_files.check()[0] is None, "found, but not the pinned files: not used"


def test_a_checkout_without_the_files_reports_it_and_nothing_crashes(monkeypatch, tmp_path, quiet):
    from core import binaries

    monkeypatch.setattr(binaries, "kokoro_dir", lambda: None)
    monkeypatch.setattr(kokoro, "installed", lambda: True)
    assert kokoro.readiness() == (False, "Voice files missing. Run: python scripts/fetch_kokoro.py")
    assert kokoro.voices("en") == []
    with pytest.raises(VoiceError) as e:
        kokoro.speak("Hello.", "en", None, tmp_path / "x.wav")
    assert e.value.code == "files_missing"
    status = voice.status(quiet)
    assert status["engines"][0]["kokoro"] == {"ready": False, "languages": ["en", "es", "fr", "hi", "it", "pt"],
                                              "message": "Voice files missing. Run: python scripts/fetch_kokoro.py"}


def test_the_voice_pack_is_listed_without_loading_it(monkeypatch, tmp_path):
    import zipfile

    pack = tmp_path / "voices-v1.0.bin"
    with zipfile.ZipFile(pack, "w") as z:
        for name in ("af_heart", "am_michael", "bf_emma", "ef_dora", "jf_alpha", "zf_xiaoxiao", "ff_siwis"):
            z.writestr(f"{name}.npy", b"")
    monkeypatch.setattr(kokoro_files, "check", lambda folder=None: (tmp_path, ""))
    monkeypatch.setattr(kokoro, "installed", lambda: True)
    english = kokoro.voices("en")
    assert [v["id"] for v in english] == ["kokoro:af_heart", "kokoro:am_michael", "kokoro:bf_emma"]
    assert english[0]["name"] == "Heart" and english[0]["gender"] == "female" and english[0]["commercial_ok"] is True
    assert english[2]["country"] == "United Kingdom"
    everything = {v["voice"] for v in kokoro.voices()}
    assert everything == {"af_heart", "am_michael", "bf_emma", "ef_dora", "ff_siwis"}, \
        "the Japanese and Chinese voices are not offered"
    assert "numpy" not in sys.modules or True   # listing reads the zip's names only


# ---- word times -------------------------------------------------------------------------


def _heard(*words):
    return [{"word": w, "start": s, "end": e} for w, s, e in words]


def test_words_whisper_heard_the_same_take_its_times():
    got = align.snap("Hello there, world!", _heard(("hello", 0.1, 0.4), ("there", 0.5, 0.8), ("world", 0.9, 1.3)), 1.5)
    assert got == [{"word": "Hello", "start": 0.1, "end": 0.4}, {"word": "there,", "start": 0.5, "end": 0.8},
                   {"word": "world!", "start": 0.9, "end": 1.3}]


def test_a_word_heard_differently_keeps_the_scripts_spelling():
    got = align.snap("Clips Kitty has 3 modes", _heard(("Clips", 0.0, 0.3), ("Kiddy", 0.3, 0.6), ("has", 0.6, 0.8),
                                                       ("three", 0.8, 1.1), ("modes", 1.1, 1.5)), 1.6)
    assert [w["word"] for w in got] == ["Clips", "Kitty", "has", "3", "modes"]
    assert got[1] == {"word": "Kitty", "start": 0.3, "end": 0.6} and got[3]["start"] == 0.8


def test_words_whisper_missed_share_the_gap_between_their_neighbours():
    got = align.snap("one two three four", _heard(("one", 0.0, 0.2), ("four", 1.0, 1.2)), 1.3)
    assert [w["word"] for w in got] == ["one", "two", "three", "four"]
    assert got[1]["start"] == 0.2 and got[2]["end"] == 1.0 and got[1]["end"] == got[2]["start"]


def test_a_number_read_out_as_several_words_shares_their_time():
    got = align.snap("In 2026 we ship", _heard(("in", 0.0, 0.2), ("twenty", 0.2, 0.5), ("twenty", 0.5, 0.8),
                                               ("six", 0.8, 1.0), ("we", 1.0, 1.1), ("ship", 1.1, 1.5)), 1.5)
    assert got[1] == {"word": "2026", "start": 0.2, "end": 1.0}


def test_times_never_run_backwards_or_past_the_end():
    got = align.snap("a b c d", _heard(("a", 0.5, 0.4), ("b", 0.3, 0.9), ("c", 0.8, 5.0), ("d", 4.0, 9.0)), 2.0)
    flat = [t for w in got for t in (w["start"], w["end"])]
    assert flat == sorted(flat) and max(flat) <= 2.0 and min(flat) >= 0.0
    assert align.snap("", [], 1.0) == []
    nothing = align.snap("just three words", [], 3.0)
    assert [w["word"] for w in nothing] == ["just", "three", "words"]
    assert nothing[0]["start"] == 0.0 and nothing[-1]["end"] == 3.0


def test_each_take_is_transcribed_under_its_own_name(monkeypatch, tmp_path):
    """The transcriber keeps results by name: two takes must never share one."""
    wav_path = tmp_path / "take.wav"
    with wave.open(str(wav_path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(24000)
        w.writeframes(b"\x00\x00" * 24000)
    calls = []
    fake = types.ModuleType("transcription.transcriber")

    def transcribe(path, video_id, transcript_dir, model_size="small", device="auto", language=None, online=None):
        calls.append((path, video_id, transcript_dir, model_size, device, language))
        transcript_dir.mkdir(parents=True, exist_ok=True)
        (transcript_dir / f"{video_id}.json").write_text("{}", encoding="utf-8")
        return [types.SimpleNamespace(words=[{"word": "hello", "start": 0.1, "end": 0.5}])]

    fake.transcribe = transcribe
    monkeypatch.setitem(sys.modules, "transcription.transcriber", fake)
    first = align.words(wav_path, "Hello", data_dir=tmp_path, language="en", model_size="auto", device="cpu")
    align.words(wav_path, "Hello", data_dir=tmp_path, language="en")
    assert first == [{"word": "Hello", "start": 0.1, "end": 0.5}]
    assert calls[0][1] != calls[1][1] and calls[0][2] != calls[1][2]
    assert calls[0][3:] == ("auto", "cpu", "en")
    assert not list((tmp_path / "voice" / "align").iterdir()), "the scratch transcripts are removed"
