"""Editor suite step V1: the voice routes (server/voice_api.py) and the two
older dubbing routes that now go through the voice layer, through FastAPI's
TestClient on a temporary data folder. Nothing listens on the engine's port.

- GET /voice, POST /voice/engine, GET /voice/voices, POST /voice/speak and
  GET /voice/preview need the session header and an Origin of this PC's own.
- A closed voice app is a plain message with a code, never a crash.
- An address that is not this PC is refused when it is typed, and saved
  nowhere.
- A spoken file is played from /voice/audio/<random name>, and that route
  serves nothing else.
- GET /voices and GET /voices/preview (the dubbing dialog) keep their shape.

Kokoro and Piper are stood in for; the voice apps are fake local servers.
"""

import json
import wave

import pytest
from voice_fakes import closed_port, openai_server, voicebox

GUARDED = [
    ("GET", "/voice", None),
    ("POST", "/voice/engine", {"engine": "builtin"}),
    ("GET", "/voice/voices?language=en", None),
    ("POST", "/voice/speak", {"text": "Hello."}),
    ("GET", "/voice/preview?language=en", None),
]


class Env:
    def __init__(self, client, session, data_dir, spoken):
        self.client, self.data_dir, self.spoken = client, data_dir, spoken
        self.ok = {"X-Clips-Kitty-Session": session}

    def get(self, path, **headers):
        return self.client.get(path, headers={**self.ok, **headers})

    def post(self, path, body=None, **headers):
        return self.client.post(path, json=body if body is not None else {}, headers={**self.ok, **headers})


@pytest.fixture
def env(tmp_path, monkeypatch):
    pytest.importorskip("fastapi")
    pytest.importorskip("httpx")
    pytest.importorskip("yaml")
    from fastapi.testclient import TestClient

    from main import BUNDLED_CONFIG, load_config
    from plugins import session
    from server.api import create_app
    from voice.engines import kokoro, piper

    monkeypatch.delenv("CLIPS_KITTY_SESSION_SECRET", raising=False)
    # No voice app is running, wherever the tests run.
    monkeypatch.setenv("CLIPS_STUDIO_VOICEBOX_URL", f"http://127.0.0.1:{closed_port()}")
    studio = tmp_path / "no-voicestudio"
    studio.mkdir()
    (studio / "backend.json").write_text(
        json.dumps({"schema": 1, "url": f"http://127.0.0.1:{closed_port()}"}), encoding="utf-8")
    monkeypatch.setenv("VOICESTUDIO_USER_DATA", str(studio))

    spoken: list = []

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

    monkeypatch.setattr(kokoro, "readiness", lambda: (True, ""))
    monkeypatch.setattr(kokoro, "voices", lambda language=None: [
        {"id": "kokoro:af_heart", "voice": "af_heart", "name": "Heart", "country": "United States",
         "quality": "", "language": "en", "gender": "female", "engine": "kokoro", "commercial_ok": True},
    ] if language in (None, "en") else [])
    monkeypatch.setattr(kokoro, "speak", kokoro_speak)
    monkeypatch.setattr(piper, "installed", lambda: True)
    monkeypatch.setattr(piper, "voices", lambda language, voices_dir: [])
    monkeypatch.setattr(piper, "speak", piper_speak)

    data_dir = tmp_path / "data"
    config = load_config(BUNDLED_CONFIG)
    config["paths"]["data_dir"] = str(data_dir)
    app = create_app(config, tmp_path / "settings.yaml")
    client = TestClient(app, base_url="http://127.0.0.1")
    return Env(client, session.path(data_dir).read_text(encoding="utf-8").strip(), data_dir, spoken)


# ---- who may ask ------------------------------------------------------------------------


def test_every_voice_route_needs_the_session(env):
    for method, path, body in GUARDED:
        r = env.client.request(method, path, json=body)
        assert r.status_code == 403, (method, path, r.text)
        r = env.client.request(method, path, json=body, headers={"X-Clips-Kitty-Session": "wrong"})
        assert r.status_code == 403, (method, path, r.text)
    assert env.spoken == []


@pytest.mark.parametrize("origin", ["https://example.com", "http://evil.test:8765", "http://127.0.0.1:3000"])
def test_a_web_page_is_refused_even_with_the_session(env, origin):
    for method, path, body in GUARDED:
        r = env.client.request(method, path, json=body, headers={**env.ok, "Origin": origin})
        assert r.status_code == 403, (method, path, origin)
    assert env.spoken == []


def test_the_apps_own_window_may_ask(env):
    for origin in ("http://localhost:5173", "null", "file://"):
        assert env.get("/voice", Origin=origin).status_code == 200


def test_the_voice_routes_are_internal():
    from server import api_stability as st

    for method, path, _body in GUARDED:
        assert st.label(method, path.split("?")[0]) == st.INTERNAL
    assert st.label("GET", "/voice/audio/{file}") == st.INTERNAL


# ---- GET /voice and POST /voice/engine --------------------------------------------------


def test_status_names_the_four_engines_and_the_choice(env):
    got = env.get("/voice").json()
    assert got["engine"] == "builtin"
    assert [e["id"] for e in got["engines"]] == ["builtin", "voicebox", "voicestudio", "custom"]
    by_id = {e["id"]: e for e in got["engines"]}
    assert by_id["builtin"]["ready"] and by_id["builtin"]["code"] == "ok" and not by_id["builtin"]["optional"]
    assert by_id["voicebox"] == {**by_id["voicebox"], "ready": False, "code": "not_running",
                                 "message": "Open Voicebox to use it."}
    assert by_id["voicestudio"]["message"] == "Open VoiceStudio to use it."
    assert by_id["custom"]["code"] == "no_address"
    assert by_id["custom"]["licence_note"] and not by_id["voicebox"]["licence_note"]
    assert got["settings"] == {"engine": "builtin", "voices": {}, "custom_address": "", "custom_model": "",
                               "fallback_to_builtin": False}
    for engine in got["engines"]:
        assert set(engine) >= {"id", "name", "optional", "ready", "code", "message", "address", "version",
                               "languages", "licence_note"}


def test_choices_are_saved_and_what_is_left_out_stays(env):
    r = env.post("/voice/engine", {"engine": "voicebox", "voices": {"en": "kokoro:af_heart"}})
    assert r.status_code == 200, r.text
    assert r.json()["engine"] == "voicebox"
    r = env.post("/voice/engine", {"fallback_to_builtin": True})
    settings = r.json()["settings"]
    assert settings["engine"] == "voicebox" and settings["fallback_to_builtin"] is True
    assert settings["voices"] == {"en": "kokoro:af_heart"}
    # null clears one language's default.
    settings = env.post("/voice/engine", {"voices": {"en": None}}).json()["settings"]
    assert settings["voices"] == {}
    assert json.loads((env.data_dir / "voice_settings.json").read_text(encoding="utf-8"))["engine"] == "voicebox"


@pytest.mark.parametrize("body", [{"engine": "elevenlabs"}, {"unknown": 1}, {"voices": {"english!": "x"}}])
def test_a_choice_nobody_offers_is_refused(env, body):
    assert env.post("/voice/engine", body).status_code in (400, 422)
    assert not (env.data_dir / "voice_settings.json").exists()


@pytest.mark.parametrize("language,picked,why", [
    ("de", "kokoro:af_heart", "The built-in voice does not speak German."),
    ("ko", "en_US-lessac-medium", "There is no Piper voice for Korean."),
    ("th", "voicebox:p-1", "Voicebox does not speak Thai."),
])
def test_a_default_voice_that_cannot_speak_its_language_is_refused_and_not_saved(env, language, picked, why):
    r = env.post("/voice/engine", {"engine": "voicebox", "voices": {"en": "kokoro:af_heart", language: picked}})
    assert r.status_code == 400, r.text
    assert r.json()["detail"] == {"message": why, "code": "no_voice"}
    assert not (env.data_dir / "voice_settings.json").exists(), "nothing of the request is saved"
    # One that can is saved, and clearing one is always allowed.
    assert env.post("/voice/engine", {"voices": {"de": "de_DE-thorsten-medium", "ko": None}}).status_code == 200


@pytest.mark.parametrize("address", ["http://192.168.1.20:8880", "https://127.0.0.1:8880", "http://example.com",
                                     "http://0.0.0.0:8880", "http://user@127.0.0.1:8880"])
def test_an_address_that_is_not_this_pc_is_refused_and_not_saved(env, address):
    r = env.post("/voice/engine", {"engine": "custom", "custom_address": address})
    assert r.status_code == 400, r.text
    detail = r.json()["detail"]
    assert detail["code"] == "not_local" and "on this PC" in detail["message"]
    assert not (env.data_dir / "voice_settings.json").exists()


def test_an_address_on_this_pc_is_saved_in_one_form(env):
    with openai_server() as fake:
        r = env.post("/voice/engine", {"engine": "custom", "custom_address": f"localhost:{fake.port}/v1/"})
        assert r.status_code == 200, r.text
        got = r.json()
        assert got["settings"]["custom_address"] == fake.base
        custom = next(e for e in got["engines"] if e["id"] == "custom")
        assert custom["ready"] and custom["address"] == fake.base


# ---- GET /voice/voices ------------------------------------------------------------------


def test_the_built_in_voices_are_listed_with_the_default(env):
    got = env.get("/voice/voices?language=en").json()
    assert got["engine"] == "builtin" and got["code"] == "ok" and got["message"] == ""
    assert [v["id"] for v in got["voices"]] == ["kokoro:af_heart"]
    assert got["default"] == "kokoro:af_heart"
    assert got["voices"][0]["commercial_ok"] is True


def test_a_closed_voice_app_lists_nothing_and_says_what_to_do(env):
    env.post("/voice/engine", {"engine": "voicebox"})
    r = env.get("/voice/voices?language=en")
    assert r.status_code == 200
    assert r.json() == {"engine": "voicebox", "voices": [], "default": None,
                        "message": "Open Voicebox to use it.", "code": "not_running"}
    # Another engine's list can be asked for without choosing it.
    assert env.get("/voice/voices?language=en&engine=builtin").json()["voices"]


def test_voicestudios_voices_carry_the_not_for_monetised_videos_mark(env, monkeypatch, tmp_path):
    with openai_server() as fake:
        studio = tmp_path / "voicestudio"
        studio.mkdir()
        (studio / "backend.json").write_text(json.dumps({"schema": 1, "url": fake.base}), encoding="utf-8")
        monkeypatch.setenv("VOICESTUDIO_USER_DATA", str(studio))
        env.post("/voice/engine", {"engine": "voicestudio"})
        got = env.get("/voice/voices?language=es").json()
    assert got["voices"] and all(v["commercial_ok"] is False for v in got["voices"])


def test_another_apps_voices_carry_no_mark(env):
    with openai_server() as fake:
        env.post("/voice/engine", {"engine": "custom", "custom_address": fake.base})
        got = env.get("/voice/voices?language=en").json()
    assert got["voices"] and all(v["commercial_ok"] is None for v in got["voices"])


@pytest.mark.parametrize("language", ["", "english", "e", "../x", "EN US"])
def test_a_language_that_is_no_code_is_refused(env, language):
    assert env.get("/voice/voices", ).status_code == 422
    assert env.client.get("/voice/voices", params={"language": language}, headers=env.ok).status_code == 400


# ---- POST /voice/speak and the audio route ----------------------------------------------


def test_text_is_spoken_to_a_wav_the_window_can_play(env):
    r = env.post("/voice/speak", {"text": "Hello there.", "seed": 7, "speed": 1.1})
    assert r.status_code == 200, r.text
    got = r.json()
    assert env.spoken == [("kokoro", "Hello there.", "en", "af_heart", 1.1, 7)]
    assert got["engine"] == "kokoro" and got["voice"] == "kokoro:af_heart" and got["language"] == "en"
    assert got["seed"] == 7 and got["note"] == "" and got["sample_rate"] == 24000 and got["duration"] == 0.5
    assert got["url"] == f"/voice/audio/{got['id']}.wav" and len(got["id"]) == 32
    assert "words" not in got
    # The player sends no header.
    played = env.client.get(got["url"])
    assert played.status_code == 200 and played.headers["content-type"] == "audio/wav"
    assert played.content[:4] == b"RIFF"


def test_the_audio_route_serves_only_what_was_spoken(env):
    (env.data_dir / "voice" / "spoken").mkdir(parents=True, exist_ok=True)
    (env.data_dir / "voice" / "spoken" / "notes.txt").write_text("x", encoding="utf-8")
    for name in ("notes.txt", "..%2F..%2Fstate.db", "0" * 32 + ".wav", "voice_settings.json",
                 "%2e%2e%5cstate.db"):
        assert env.client.get(f"/voice/audio/{name}").status_code == 404, name


def test_word_times_come_with_the_speech_when_asked(env, monkeypatch):
    from voice import align

    asked = {}

    def words(path, text, **settings):
        asked.update(settings, text=text)
        return [{"word": "Hello", "start": 0.0, "end": 0.2}, {"word": "there.", "start": 0.2, "end": 0.5}]

    monkeypatch.setattr(align, "words", words)
    got = env.post("/voice/speak", {"text": "Hello there.", "words": True}).json()
    assert [w["word"] for w in got["words"]] == ["Hello", "there."]
    assert asked["text"] == "Hello there." and asked["language"] == "en"


def test_word_times_never_use_an_online_transcription_service(tmp_path, monkeypatch):
    """Even with online transcription turned on for clipping: the spoken
    sound stays on this PC."""
    pytest.importorskip("fastapi")
    pytest.importorskip("httpx")
    pytest.importorskip("yaml")
    from fastapi.testclient import TestClient

    from main import BUNDLED_CONFIG, load_config
    from plugins import session
    from server.api import create_app
    from voice import align
    from voice.engines import kokoro

    monkeypatch.delenv("CLIPS_KITTY_SESSION_SECRET", raising=False)
    monkeypatch.setattr(kokoro, "readiness", lambda: (True, ""))

    def speak(text, language, voice_name, out, speed=1.0, seed=None):
        out.parent.mkdir(parents=True, exist_ok=True)
        with wave.open(str(out), "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(24000)
            w.writeframes(b"\x00\x10" * 12000)
        return 24000

    monkeypatch.setattr(kokoro, "speak", speak)
    asked = {}

    def words(path, text, **settings):
        asked.update(settings)
        return []

    monkeypatch.setattr(align, "words", words)
    data_dir = tmp_path / "data"
    config = load_config(BUNDLED_CONFIG)
    config["paths"]["data_dir"] = str(data_dir)
    config["transcription"] = {"backend": "groq", "model": "whisper-large-v3"}
    client = TestClient(create_app(config, tmp_path / "settings.yaml"), base_url="http://127.0.0.1")
    secret = session.path(data_dir).read_text(encoding="utf-8").strip()
    r = client.post("/voice/speak", json={"text": "Hello there.", "words": True},
                    headers={"X-Clips-Kitty-Session": secret})
    assert r.status_code == 200, r.text
    assert "online" in asked and asked["online"] is None


def test_speech_is_kept_when_only_its_word_times_fail(env, monkeypatch):
    from voice import align

    def words(*_a, **_k):
        raise RuntimeError("no whisper here")

    monkeypatch.setattr(align, "words", words)
    r = env.post("/voice/speak", {"text": "Hello there.", "words": True})
    assert r.status_code == 200
    got = r.json()
    assert got["words"] is None and "RuntimeError" in got["words_error"]
    assert env.client.get(got["url"]).status_code == 200


def test_a_closed_voice_app_stops_speech_and_says_why(env):
    env.post("/voice/engine", {"engine": "voicebox"})
    r = env.post("/voice/speak", {"text": "Hello."})
    assert r.status_code == 409
    assert r.json()["detail"] == {"message": "Open Voicebox to use it.", "code": "not_running"}
    assert env.spoken == []
    # Allowed to stand in: the built-in voice speaks, and the answer says so.
    env.post("/voice/engine", {"fallback_to_builtin": True})
    got = env.post("/voice/speak", {"text": "Hello."}).json()
    assert got["engine"] == "kokoro" and "Voicebox is not running" in got["note"]


def test_the_persons_voicebox_speaks_when_it_is_open(env, monkeypatch):
    with voicebox() as fake:
        monkeypatch.setenv("CLIPS_STUDIO_VOICEBOX_URL", fake.base)
        env.post("/voice/engine", {"engine": "voicebox"})
        got = env.post("/voice/speak", {"text": "Hello.", "voice": "voicebox:p-heart", "seed": 3})
        assert got.status_code == 200, got.text
        assert got.json()["engine"] == "voicebox" and got.json()["voice"] == "voicebox:p-heart"
        assert fake.bodies("/generate/stream") == [
            {"profile_id": "p-heart", "text": "Hello.", "language": "en", "engine": "kokoro", "seed": 3}]
        # Nothing was written into the person's Voicebox.
        assert {m for m, _p in fake.seen()} <= {"GET", "POST"}
        assert {p for m, p in fake.seen() if m == "POST"} == {"/generate/stream"}


@pytest.mark.parametrize("body", [{"text": ""}, {"text": "   "}, {"text": "x" * 5001}, {"text": "Hi", "speed": 9},
                                  {"text": "Hi", "seed": -1}, {"text": "Hi", "language": "english"},
                                  {"text": "Hi", "extra": True}])
def test_a_request_that_makes_no_sense_is_refused_before_anything_speaks(env, body):
    assert env.post("/voice/speak", body).status_code in (400, 422)
    assert env.spoken == []


def test_a_language_with_no_voice_is_a_plain_400(env, monkeypatch):
    from voice.engines import piper

    monkeypatch.setattr(piper, "speaks", lambda language: False)
    r = env.post("/voice/speak", {"text": "Sawasdee.", "language": "th"})
    assert r.status_code == 400 and r.json()["detail"]["code"] == "no_voice"


def test_missing_voice_files_are_said_with_the_command(env, monkeypatch):
    from voice import kokoro_files
    from voice.engines import kokoro, piper

    monkeypatch.setattr(kokoro, "readiness", lambda: (False, f"Voice files missing. Run: {kokoro_files.FETCH_COMMAND}"))
    monkeypatch.setattr(piper, "installed", lambda: False)
    builtin = env.get("/voice").json()["engines"][0]
    assert builtin["code"] == "files_missing" and not builtin["ready"]
    assert builtin["message"] == "Voice files missing. Run: python scripts/fetch_kokoro.py"
    r = env.post("/voice/speak", {"text": "Hello."})
    assert r.status_code == 409 and r.json()["detail"]["code"] == "files_missing"
    assert "python scripts/fetch_kokoro.py" in r.json()["detail"]["message"]


# ---- GET /voice/preview -----------------------------------------------------------------


def test_a_voice_can_be_heard_in_the_language_asked_for(env):
    from multilingual.languages import sample_text

    r = env.get("/voice/preview?language=en&voice=kokoro:af_heart")
    assert r.status_code == 200, r.text
    got = r.json()
    assert env.spoken == [("kokoro", sample_text("en"), "en", "af_heart", 1.0, None)]
    assert env.client.get(got["url"]).content[:4] == b"RIFF"
    # A built-in voice's sample is made once.
    env.get("/voice/preview?language=en&voice=kokoro:af_heart")
    assert len(env.spoken) == 1
    # Its file's name comes from a digest, never from the voice's id.
    names = [p.name for p in (env.data_dir / "previews").iterdir()]
    assert len(names) == 1 and names[0].startswith("voice_kokoro_") and "af_heart" not in names[0]


def test_hearing_a_closed_voice_app_says_to_open_it(env):
    r = env.get("/voice/preview?language=en&voice=voicebox:p-heart")
    assert r.status_code == 409 and r.json()["detail"]["message"] == "Open Voicebox to use it."


# ---- the dubbing dialog's older routes --------------------------------------------------


def test_the_dubbing_voice_list_keeps_its_shape(env):
    got = env.client.get("/voices?language=en").json()
    assert set(got) == {"voices", "default", "engine", "message"}
    assert got["default"] == "kokoro:af_heart" and got["engine"] == "builtin" and got["message"] == ""
    assert set(got["voices"][0]) >= {"id", "name", "country", "quality"}


def test_the_dubbing_sample_is_still_the_sound_itself(env):
    r = env.client.get("/voices/preview?language=en&voice=kokoro:af_heart")
    assert r.status_code == 200 and r.headers["content-type"] == "audio/wav"
    assert r.content[:4] == b"RIFF"
    assert env.client.get("/voices/preview?language=xx").status_code == 400


def test_the_older_sample_route_only_reaches_the_voice_app_that_is_chosen(env, monkeypatch):
    """GET /voices/preview has no session guard, so a voice of a voice app
    the person did not choose is refused without contacting that app."""
    with voicebox() as fake:
        monkeypatch.setenv("CLIPS_STUDIO_VOICEBOX_URL", fake.base)
        for other in ("voicebox:p-heart", "custom:alloy", "voicestudio:v-1"):
            r = env.client.get(f"/voices/preview?language=en&voice={other}")
            assert r.status_code == 400
            assert r.json()["detail"] == "That voice belongs to a voice app that is not chosen in Settings."
        assert fake.requests == []
        assert env.post("/voice/engine", {"engine": "voicebox"}).status_code == 200
        fake.requests.clear()
        r = env.client.get("/voices/preview?language=en&voice=voicebox:p-heart")
        assert r.status_code == 200 and r.content[:4] == b"RIFF"
        assert ("POST", "/generate/stream") in fake.seen()


def test_languages_say_which_can_be_dubbed(env):
    got = env.client.get("/languages").json()
    assert got["dubbing_available"] is True
    by_code = {entry["code"]: entry["can_dub"] for entry in got["languages"]}
    assert by_code["en"] is True and by_code["de"] is True
