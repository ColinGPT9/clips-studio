"""Editor suite step V1: the optional voice apps, against stand-in servers.

Voicebox, VoiceStudio and "another local voice app" are the person's own
programs. The connectors (voice/engines/voicebox.py, openai_local.py) reach
them through voice/net.py, which only ever talks to this PC. Here each is
run against a fake server on a loopback port (tests/voice_fakes.py):

- only loopback addresses are contacted; everything else is refused before a
  socket is opened, redirects are refused, and no Origin header is sent;
- Voicebox: its four routes and nothing else, existing voices only, the
  "tada" engine left out, plain messages when it is closed, too old, has not
  downloaded a model, or is not Voicebox at all;
- the OpenAI-style route: always asks for WAV, splits text over 4,096
  characters, passes a seed, reads errors in OpenAI's shape, refuses an
  answer that is not sound;
- VoiceStudio's backend.json is used only when it names this PC.

Standard library only (one test needs FFmpeg and skips without it).
"""

import io
import json
import shutil
import socket
import wave

import pytest
from voice_fakes import closed_port, json_answer, openai_server, voicebox, wav_bytes

from voice import net
from voice.base import VoiceError
from voice.engines import openai_local
from voice.engines import voicebox as vb

# ---- loopback only ----------------------------------------------------------------------


@pytest.mark.parametrize("address, base", [
    ("127.0.0.1:3900", "http://127.0.0.1:3900"),
    ("http://127.0.0.1:3900/", "http://127.0.0.1:3900"),
    ("http://localhost:8880/v1", "http://127.0.0.1:8880"),
    ("HTTP://LOCALHOST:8880", "http://127.0.0.1:8880"),
    ("http://127.8.9.10:5000", "http://127.8.9.10:5000"),
    ("http://[::1]:8880", "http://[::1]:8880"),
    ("http://127.0.0.1", "http://127.0.0.1:80"),
])
def test_an_address_on_this_pc_is_accepted(address, base):
    assert net.local_base(address) == base


@pytest.mark.parametrize("address", [
    "", "   ",
    "http://192.168.1.20:3900",            # the LAN
    "http://10.0.0.5:3900",
    "http://0.0.0.0:3900",                 # every interface
    "http://example.com:3900",             # a name
    "http://localhost.example.com:3900",
    "http://my-pc:3900",
    "http://[::]:3900",
    "http://[fe80::1]:3900",
    "http://[2001:db8::1]:3900",
    "http://[::ffff:192.168.1.20]:3900",   # an IPv4 address written as IPv6
    "https://127.0.0.1:3900",              # not plain http
    "ftp://127.0.0.1:3900",
    "file:///C:/x",
    "http://user:pw@127.0.0.1:3900",       # a user name
    "http://127.0.0.1@example.com:3900",
    "http://127.0.0.1:99999",
    "http://127.0.0.1:0",
    "http://127.0.0.1:abc",
    "http://127.1:3900",                   # short forms a browser would expand
    "http://2130706433:3900",
    "http://0x7f.0.0.1:3900",
    "http://127.0.0.1\\@example.com",
    "http://127.0.0.1:3900\n.example.com",
])
def test_anything_that_is_not_this_pc_is_refused_in_plain_words(address):
    with pytest.raises(net.NotLocal) as e:
        net.local_base(address)
    assert "Only a voice app on this PC" in str(e.value)


def test_a_refused_address_is_never_connected_to(monkeypatch):
    def no_socket(*_a, **_k):
        raise AssertionError("a socket was opened for an address that is not this PC")

    monkeypatch.setattr(socket, "socket", no_socket)
    monkeypatch.setattr(socket, "create_connection", no_socket)
    for call in (lambda: net.request("http://192.168.1.20:3900", "/"),
                 lambda: net.listening("http://example.com:80"),
                 lambda: net.get_json("https://127.0.0.1:1", "/")):
        with pytest.raises(net.NotLocal):
            call()


def test_requests_carry_no_origin_and_ignore_the_systems_proxy(monkeypatch):
    # A proxy that would swallow the request if it were used.
    monkeypatch.setenv("HTTP_PROXY", f"http://127.0.0.1:{closed_port()}")
    monkeypatch.setenv("http_proxy", f"http://127.0.0.1:{closed_port()}")
    monkeypatch.delenv("NO_PROXY", raising=False)
    monkeypatch.delenv("no_proxy", raising=False)
    with voicebox() as fake:
        assert net.get_json(fake.base, "/")["version"] == "0.5.0"
        net.request(fake.base, "/generate/stream", body={"text": "hi"})
    for r in fake.requests:
        assert "origin" not in r["headers"] and "referer" not in r["headers"]
        assert "cookie" not in r["headers"] and "authorization" not in r["headers"]
    assert fake.requests[1]["headers"]["content-type"] == "application/json"


@pytest.mark.parametrize("status", [301, 302, 307, 308])
def test_a_redirect_is_refused_not_followed(status):
    with voicebox() as elsewhere:
        target = elsewhere.base + "/"
        with voicebox({("GET", "/"): (status, "text/plain", b"", {"Location": target}),
                       ("POST", "/generate/stream"): (status, "text/plain", b"", {"Location": target})}) as fake:
            with pytest.raises(net.BadAnswer):
                net.get_json(fake.base, "/")
            with pytest.raises(net.BadAnswer):
                net.request(fake.base, "/generate/stream", body={"text": "hi"})
        assert elsewhere.requests == [], "the redirect was followed"


def test_nothing_listening_is_unreachable_not_a_crash():
    base = f"http://127.0.0.1:{closed_port()}"
    assert net.listening(base) is False
    with pytest.raises(net.Unreachable):
        net.request(base, "/", timeout=3)


def test_an_answer_far_too_big_is_cut_off():
    with voicebox({("GET", "/"): (200, "application/json", b"x" * 5000)}) as fake:
        with pytest.raises(net.BadAnswer):
            net.request(fake.base, "/", limit=1000)


# ---- Voicebox ---------------------------------------------------------------------------


def test_voicebox_is_recognised_and_its_voices_listed():
    with voicebox() as fake:
        assert vb.status(fake.base) == {"ready": True, "message": "", "version": "0.5.0"}
        found = vb.voices("en", fake.base)
    assert [v["id"] for v in found] == ["voicebox:p-heart", "voicebox:p-me"], \
        "English voices and cloned voices, never the tada one, never another language's preset"
    heart, mine = found
    assert heart["commercial_ok"] is True and heart["ready"] is True and heart["model"] == "kokoro"
    assert mine["ready"] is False, "Voicebox has not downloaded Chatterbox"
    assert set(fake.seen()) == {("GET", "/"), ("GET", "/profiles"), ("GET", "/models/status")}


def test_a_cloned_voice_is_offered_for_other_languages_too():
    with voicebox() as fake:
        found = vb.voices("es", fake.base)
    assert [v["id"] for v in found] == ["voicebox:p-dora", "voicebox:p-me"]


def test_voicebox_speaks_through_the_stream_route_and_changes_nothing_there(tmp_path):
    with voicebox() as fake:
        out = tmp_path / "say.wav"
        assert vb.speak("Hello there.", "en", "p-heart", out, seed=1234, base=fake.base) == 24000
        with wave.open(str(out)) as w:
            assert w.getframerate() == 24000 and w.getnframes() == 6000
        [body] = fake.bodies("/generate/stream")
    assert body == {"profile_id": "p-heart", "text": "Hello there.", "language": "en", "engine": "kokoro",
                    "seed": 1234}
    assert set(fake.seen()) <= {("GET", "/profiles"), ("POST", "/generate/stream")}
    assert all(method == "GET" or path == "/generate/stream" for method, path in fake.seen()), \
        "nothing was created, changed or deleted in Voicebox"


def test_a_tada_voice_cannot_be_spoken_even_when_named(tmp_path):
    with voicebox() as fake:
        with pytest.raises(VoiceError) as e:
            vb.speak("Hello.", "en", "p-tada", tmp_path / "x.wav", base=fake.base)
        assert e.value.code == "no_voice"
        assert ("POST", "/generate/stream") not in fake.seen()


def test_voicebox_closed_says_to_open_it(tmp_path):
    base = f"http://127.0.0.1:{closed_port()}"
    assert vb.status(base) == {"ready": False, "message": "Open Voicebox to use it.", "version": ""}
    for call in (lambda: vb.voices("en", base), lambda: vb.speak("Hi.", "en", "p", tmp_path / "x.wav", base=base)):
        with pytest.raises(VoiceError) as e:
            call()
        assert e.value.code == "not_running" and str(e.value) == "Open Voicebox to use it."


def test_another_program_on_voiceboxs_port_is_not_taken_for_voicebox():
    for root in (json_answer({"hello": "world"}), (200, "text/html", b"<html>router</html>"),
                 (404, "text/plain", b"nope")):
        with voicebox({("GET", "/"): root}) as fake:
            got = vb.status(fake.base)
        assert got["ready"] is False and got["message"].startswith("Another program is using Voicebox's address")


def test_a_voicebox_without_the_stream_route_says_to_update(tmp_path):
    with voicebox({("POST", "/generate/stream"): None}) as fake:
        with pytest.raises(VoiceError) as e:
            vb.speak("Hello.", "en", "p-heart", tmp_path / "x.wav", base=fake.base)
    assert e.value.code == "refused" and "Update Voicebox" in str(e.value)


def test_a_model_voicebox_has_not_downloaded_is_said_plainly_and_never_downloaded(tmp_path):
    missing = json_answer({"detail": "Model chatterbox-tts is not downloaded yet."}, 400)
    with voicebox({("POST", "/generate/stream"): missing}) as fake:
        with pytest.raises(VoiceError) as e:
            vb.speak("Hello.", "en", "p-me", tmp_path / "x.wav", base=fake.base)
        assert "not downloaded yet" in str(e.value) and "Open Voicebox" in str(e.value)
        assert not any("download" in path or path == "/generate" for _m, path in fake.seen())
    assert not (tmp_path / "x.wav").exists()


def test_a_web_page_instead_of_speech_is_refused(tmp_path):
    with voicebox({("POST", "/generate/stream"): (200, "audio/wav", b"<html>Sign in</html>")}) as fake:
        with pytest.raises(VoiceError) as e:
            vb.speak("Hello.", "en", "p-heart", tmp_path / "x.wav", base=fake.base)
    assert e.value.code == "refused" and not (tmp_path / "x.wav").exists()


def test_a_language_voicebox_does_not_take_is_refused_before_asking(tmp_path):
    with voicebox() as fake:
        with pytest.raises(VoiceError) as e:
            vb.speak("Halo.", "id", "p-me", tmp_path / "x.wav", base=fake.base)
        assert e.value.code == "no_voice" and ("POST", "/generate/stream") not in fake.seen()


def test_voiceboxs_address_is_always_this_pc(monkeypatch):
    monkeypatch.delenv("CLIPS_STUDIO_VOICEBOX_URL", raising=False)
    assert vb.address() == "http://127.0.0.1:17493"
    monkeypatch.setenv("CLIPS_STUDIO_VOICEBOX_URL", "http://127.0.0.1:17611")
    assert vb.address() == "http://127.0.0.1:17611"
    monkeypatch.setenv("CLIPS_STUDIO_VOICEBOX_URL", "http://192.168.1.20:17493")
    with pytest.raises(net.NotLocal):
        vb.address()
    assert vb.status()["ready"] is False


# ---- the OpenAI-style route -------------------------------------------------------------


def test_an_address_override_that_is_not_this_pc_is_a_plain_refusal(monkeypatch, tmp_path):
    monkeypatch.setenv("CLIPS_STUDIO_VOICEBOX_URL", "http://192.168.1.20:17493")
    monkeypatch.setattr(socket, "create_connection", lambda *a, **k: pytest.fail("something was contacted"))
    for ask in (lambda: vb.voices("en"), lambda: vb.default_voice("en"),
                lambda: vb.speak("Hello.", "en", "p-heart", tmp_path / "x.wav")):
        with pytest.raises(VoiceError) as refused:
            ask()
        assert refused.value.code == "not_local"
    assert not (tmp_path / "x.wav").exists()


def _custom(fake) -> dict:
    return {"custom_address": fake.base, "custom_model": ""}


def test_speech_is_always_asked_for_as_wav_with_the_seed(tmp_path):
    with openai_server() as fake:
        out = tmp_path / "say.wav"
        rate = openai_local.speak("custom", _custom(fake), "Hello there.", "en", "v-1", out, speed=1.2, seed=7)
        [body] = fake.bodies("/v1/audio/speech")
    assert rate == 24000 and out.stat().st_size > 44
    assert body == {"model": "tts-1", "input": "Hello there.", "voice": "v-1", "response_format": "wav",
                    "speed": 1.2, "seed": 7}


def test_no_seed_is_sent_unless_one_is_given_and_the_model_name_can_be_set(tmp_path):
    with openai_server() as fake:
        openai_local.speak("custom", {"custom_address": fake.base, "custom_model": "kokoro"}, "Hi.", "en",
                           "af_heart", tmp_path / "x.wav")
        [body] = fake.bodies("/v1/audio/speech")
    assert "seed" not in body and body["model"] == "kokoro" and "language" not in body


def test_text_over_4096_characters_is_split_at_sentences_and_joined(tmp_path):
    sentence = "This sentence is exactly forty characters. "      # 43 with its space
    text = (sentence * 96).strip()                                 # 4,127 characters
    assert len(text) > 4096
    with openai_server() as fake:
        out = tmp_path / "long.wav"
        openai_local.speak("custom", _custom(fake), text, "en", "v-1", out)
        bodies = fake.bodies("/v1/audio/speech")
    assert len(bodies) == 2
    assert all(len(b["input"]) <= 4096 for b in bodies)
    assert all(b["input"].endswith(".") for b in bodies), "cut at a sentence end"
    assert " ".join(b["input"] for b in bodies) == text, "nothing lost, nothing said twice"
    with wave.open(str(out)) as w:
        assert w.getnframes() == 2 * 6000, "the two answers joined into one file"


def test_a_4097_character_word_is_still_split_under_the_limit():
    for text in ("a" * 4097, ("word " * 900).strip(), "x" * 4096, "Short."):
        pieces = openai_local.split_text(text)
        assert all(0 < len(p) <= 4096 for p in pieces)
        assert "".join(pieces).replace(" ", "") == text.replace(" ", "")
    assert len(openai_local.split_text("a" * 4097)) == 2
    assert openai_local.split_text("x" * 4096) == ["x" * 4096]


def test_an_error_in_openais_shape_is_shown_in_its_own_words(tmp_path):
    error = json_answer({"error": {"message": "Voice 'nobody' was not found.", "type": "invalid_request_error",
                                   "param": "voice", "code": "voice_not_found"}}, 400)
    with openai_server({("POST", "/v1/audio/speech"): error}) as fake:
        with pytest.raises(VoiceError) as e:
            openai_local.speak("custom", _custom(fake), "Hi.", "en", "nobody", tmp_path / "x.wav")
    assert e.value.code == "refused" and "Voice 'nobody' was not found." in str(e.value)


def test_a_server_without_the_speech_route_is_said_plainly(tmp_path):
    with openai_server({("POST", "/v1/audio/speech"): None}) as fake:
        with pytest.raises(VoiceError) as e:
            openai_local.speak("custom", _custom(fake), "Hi.", "en", "v-1", tmp_path / "x.wav")
    assert "POST /v1/audio/speech" in str(e.value)


def test_an_answer_that_is_not_sound_is_refused(tmp_path):
    for body in (b"<html>hello</html>", b"", b'{"ok": true}', b"RIFF\x00\x00\x00\x00WAVEjunk"):
        with openai_server({("POST", "/v1/audio/speech"): (200, "audio/wav", body)}) as fake:
            with pytest.raises(VoiceError) as e:
                openai_local.speak("custom", _custom(fake), "Hi.", "en", "v-1", tmp_path / "x.wav")
        assert e.value.code == "refused", body
        assert not (tmp_path / "x.wav").exists()


def test_mp3_sent_although_wav_was_asked_for_is_converted(tmp_path):
    """OpenAI's own default is mp3, and some servers ignore response_format."""
    import subprocess

    from core.binaries import ffmpeg

    if shutil.which(ffmpeg()) is None:
        pytest.skip("FFmpeg is not installed here")
    made = subprocess.run([ffmpeg(), "-v", "error", "-f", "lavfi", "-i", "sine=frequency=330:duration=0.5",
                           "-f", "mp3", "pipe:1"], capture_output=True)
    if made.returncode != 0 or not made.stdout:
        pytest.skip("this FFmpeg cannot write mp3")
    with openai_server({("POST", "/v1/audio/speech"): (200, "audio/mpeg", made.stdout)}) as fake:
        out = tmp_path / "x.wav"
        openai_local.speak("custom", _custom(fake), "Hi.", "en", "v-1", out)
    with wave.open(str(out)) as w:
        assert w.getsampwidth() == 2 and 0.3 < w.getnframes() / w.getframerate() < 0.8


def test_a_streamed_wav_with_no_real_length_is_read():
    data = bytearray(wav_bytes(0.1))
    data[4:8] = b"\xff\xff\xff\xff"
    data[40:44] = b"\xff\xff\xff\xff"
    from voice import wav

    frames, channels, width, rate = wav._read_pcm(bytes(data))
    assert (channels, width, rate) == (1, 2, 24000) and len(frames) == 4800


def test_voices_come_from_the_servers_list_or_the_standard_names():
    with openai_server() as fake:
        found = openai_local.voices("custom", _custom(fake), "es")
    assert [v["id"] for v in found] == ["custom:v-2", "custom:alloy", "custom:v-1"], "Spanish first"
    assert all(v["commercial_ok"] is None for v in found), "a licence nobody can identify: no mark"
    with openai_server({("GET", "/v1/audio/voices"): None}) as fake:
        found = openai_local.voices("custom", _custom(fake), "en")
    assert [v["voice"] for v in found] == list(openai_local.STANDARD_VOICES)
    with openai_server({("GET", "/v1/audio/voices"): json_answer({"voices": ["af_heart", "bf_emma"]})}) as fake:
        assert [v["id"] for v in openai_local.voices("custom", _custom(fake))] == ["custom:af_heart",
                                                                                 "custom:bf_emma"]


def test_a_custom_address_must_be_this_pc(tmp_path):
    for address in ("http://192.168.1.20:8880", "https://api.openai.com", "http://example.com"):
        settings = {"custom_address": address, "custom_model": ""}
        assert openai_local.status("custom", settings) == {"ready": False, "message": net.NOT_LOCAL, "address": ""}
        with pytest.raises(VoiceError) as e:
            openai_local.speak("custom", settings, "Hi.", "en", "v", tmp_path / "x.wav")
        assert e.value.code == "not_local"
    assert openai_local.status("custom", {"custom_address": ""})["message"].startswith("Enter your voice app")


# ---- VoiceStudio ------------------------------------------------------------------------


def _backend_json(folder, record) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "backend.json").write_text(record if isinstance(record, str) else json.dumps(record),
                                         encoding="utf-8")


def test_voicestudios_address_is_read_from_its_backend_json(tmp_path, monkeypatch):
    monkeypatch.setenv("VOICESTUDIO_USER_DATA", str(tmp_path / "vs"))
    assert openai_local.voicestudio_address() == "http://127.0.0.1:3900", "no file: its usual address"
    _backend_json(tmp_path / "vs", {"schema": 1, "url": "http://127.0.0.1:4900", "stage": "ready"})
    assert openai_local.voicestudio_address() == "http://127.0.0.1:4900"
    _backend_json(tmp_path / "vs", {"schema": 1, "url": "http://localhost:5900/"})
    assert openai_local.voicestudio_address() == "http://127.0.0.1:5900"


@pytest.mark.parametrize("record", [
    {"schema": 1, "url": "http://192.168.1.20:3900"},               # the LAN
    {"schema": 1, "url": "https://voice.example.com"},              # the internet
    {"schema": 1, "url": "http://127.0.0.1:4900", "remote": True},  # marked remote
    {"schema": 1, "url": 4900},
    {"schema": 1},
    ["http://127.0.0.1:4900"],
    "not json at all",
])
def test_a_backend_json_pointing_anywhere_else_is_ignored(record, tmp_path, monkeypatch):
    monkeypatch.setenv("VOICESTUDIO_USER_DATA", str(tmp_path / "vs"))
    _backend_json(tmp_path / "vs", record)
    assert openai_local.voicestudio_address() == "http://127.0.0.1:3900"


def test_voicestudio_speaks_at_the_address_its_file_names(tmp_path, monkeypatch):
    monkeypatch.setenv("VOICESTUDIO_USER_DATA", str(tmp_path / "vs"))
    with openai_server() as fake:
        _backend_json(tmp_path / "vs", {"schema": 1, "url": fake.base})
        assert openai_local.status("voicestudio", {}) == {"ready": True, "message": "", "address": fake.base}
        found = openai_local.voices("voicestudio", {}, "en")
        openai_local.speak("voicestudio", {}, "Hello.", "en", "default", tmp_path / "x.wav", seed=3)
        [body] = fake.bodies("/v1/audio/speech")
    assert [v["id"] for v in found] == ["voicestudio:v-1", "voicestudio:default", "voicestudio:v-2"]
    assert all(v["commercial_ok"] is False for v in found), "VoiceStudio's voices carry the mark"
    assert body == {"model": "tts-1", "input": "Hello.", "voice": "default", "response_format": "wav",
                    "speed": 1.0, "seed": 3, "language": "en"}
    assert set(fake.seen()) == {("GET", "/v1/audio/voices"), ("POST", "/v1/audio/speech")}


def test_voicestudio_closed_says_to_open_it(tmp_path, monkeypatch):
    monkeypatch.setenv("VOICESTUDIO_USER_DATA", str(tmp_path / "vs"))
    _backend_json(tmp_path / "vs", {"schema": 1, "url": f"http://127.0.0.1:{closed_port()}"})
    got = openai_local.status("voicestudio", {})
    assert got["ready"] is False and got["message"] == "Open VoiceStudio to use it."
    with pytest.raises(VoiceError) as e:
        openai_local.speak("voicestudio", {}, "Hi.", "en", "default", tmp_path / "x.wav")
    assert e.value.code == "not_running"


def test_two_answers_in_different_formats_are_not_glued_together(tmp_path):
    from voice import wav

    with pytest.raises(wav.NotAudio):
        wav.save_answers([wav_bytes(0.1, rate=24000), wav_bytes(0.1, rate=22050)], tmp_path / "x.wav")
    rate, seconds = wav.save_answers([wav_bytes(0.1), wav_bytes(0.2)], tmp_path / "ok.wav")
    assert rate == 24000 and abs(seconds - 0.3) < 0.001
    assert io.BytesIO((tmp_path / "ok.wav").read_bytes()).read(4) == b"RIFF"
