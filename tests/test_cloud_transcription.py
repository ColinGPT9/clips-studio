"""Online transcription on the user's own key, against faked audio and a
faked provider: it must come back exactly as a local transcript would."""

import json

import pytest

from core.models import Segment
from llm.providers import http, keys
from llm.providers.base import LLMError
from transcription import cloud

KEY = "sk-or-v1-" + "0badc0de" * 8


class Response:
    def __init__(self, body):
        self.status_code = 200
        self._body = body
        self.headers = {}
        self.text = ""

    def json(self):
        return self._body


@pytest.fixture
def audio(monkeypatch, tmp_path):
    """No ffmpeg: a 400 s "video" whose parts are empty files."""
    monkeypatch.setattr(cloud, "_duration", lambda path: 400.0)

    def extract(video, out, start, seconds):
        out.write_bytes(b"mp3")
        return out

    monkeypatch.setattr(cloud, "_extract", extract)
    monkeypatch.setattr(cloud, "_speech_in", lambda audio: None)   # no voice detector: every word kept
    checks = []
    monkeypatch.setattr(cloud.cancel, "check_active", lambda: checks.append(1))
    monkeypatch.setattr(http, "sleep", lambda s: None)
    return checks


def test_parts_are_placed_in_video_time_and_the_overlap_is_not_doubled(audio, monkeypatch, tmp_path):
    keys.save_key(tmp_path, "openrouter", KEY)
    calls = []
    # Three parts of 180 s; each answer is in the part's own time, from 2 s
    # before its start (the overlap), so the word at 1.0 s repeats the last
    # word of the part before.
    answers = [
        {"language": "english", "segments": [{"text": "Hello there."}],
         "words": [{"word": "Hello", "start": 0.5, "end": 0.9}, {"word": "there", "start": 0.9, "end": 1.4}]},
        {"language": "english", "segments": [{"text": "again. Welcome back"}],
         "words": [{"word": "again", "start": 1.0, "end": 1.5}, {"word": "Welcome", "start": 3.0, "end": 3.4},
                   {"word": "back", "start": 3.4, "end": 3.8}]},
        {"language": "english", "segments": [], "words": [{"word": "Bye!", "start": 5.0, "end": 5.5}]},
    ]

    def transport(method, url, **kw):
        calls.append({"url": url, **kw})
        return Response(answers[len(calls) - 1])

    monkeypatch.setattr(http, "transport", transport)
    online = {"backend": "openrouter", "model": "openai/whisper-large-v3-turbo", "data_dir": str(tmp_path)}
    segments = cloud.transcribe(tmp_path / "v.mp4", "vid00000001", tmp_path / "t", online)

    words = [w for s in segments for w in s.words]
    # Part 2 starts at 180 s but its audio from 178 s: "again" at 1.0 s is 179 s,
    # before the part's own start, so it belongs to part 1 and is dropped here.
    assert [(w["word"], w["start"]) for w in words] == [
        ("Hello", 0.5), ("there.", 0.9), ("Welcome", 181.0), ("back", 181.4), ("Bye!", 363.0)]
    assert len(audio) == 3  # cancel checked before every part
    assert all(c["headers"]["X-OpenRouter-Categories"] == "video-gen" for c in calls)
    assert calls[0]["json"]["timestamp_granularities"] == ["segment", "word"]
    assert KEY not in json.dumps(calls[0]["json"])

    saved = json.loads((tmp_path / "t" / "vid00000001.json").read_text(encoding="utf-8"))
    assert saved["language"] == "en" and saved["source"] == "openrouter/openai/whisper-large-v3-turbo"
    reloaded = [Segment(**s) for s in saved["segments"]]  # what every loader in the app does
    assert [s.text for s in reloaded] == ["Hello there.", "Welcome back", "Bye!"]


def test_words_become_whisper_like_segments():
    words = [{"word": w, "start": s, "end": s + 0.3} for w, s in
             [("So", 0.0), ("that", 0.3), ("happened.", 0.6), ("Then", 1.0), ("we", 1.3), ("left", 3.0)]]
    segments = cloud.group_words(words)
    assert [s.text for s in segments] == ["So that happened.", "Then we", "left"]
    assert segments[0].words[0] == {"start": 0.0, "end": 0.3, "word": "So"}


def test_a_long_run_without_pauses_is_split():
    words = [{"word": "go", "start": i * 0.5, "end": i * 0.5 + 0.4} for i in range(60)]
    assert all(s.end - s.start <= cloud.MAX_SEGMENT for s in cloud.group_words(words))


def test_whisper_words_get_their_punctuation_back():
    answer = {"segments": [{"text": " Wait, what? No way."}],
              "words": [{"word": w, "start": i, "end": i + 0.5} for i, w in enumerate(["Wait", "what", "No", "way"])]}
    assert [w["word"] for w in cloud._whisper_words(answer)] == ["Wait,", "what?", "No", "way."]


def test_no_word_timings_is_refused_rather_than_drifting_captions():
    with pytest.raises(LLMError):
        cloud._whisper_words({"text": "hi", "segments": [{"text": "hi"}]})
    with pytest.raises(LLMError):
        cloud._whisper_words({"text": "", "segments": [{"text": " hi"}]})


def test_a_part_where_nothing_is_said_is_not_a_failure(audio, monkeypatch, tmp_path):
    """Measured: a stream with 10 minutes of music before anyone spoke. Its
    first parts came back with no words and no text, which stopped the job
    as "no word timings" whatever the model."""
    keys.save_key(tmp_path, "openrouter", KEY)
    answers = [
        {"language": "english", "text": "", "segments": [], "words": []},
        {"language": "english", "text": " ", "words": None},
        {"language": "english", "segments": [{"text": "Okay, hello."}],
         "words": [{"word": "Okay", "start": 5.0, "end": 5.4}, {"word": "hello", "start": 5.4, "end": 5.9}]},
    ]
    calls = []

    def transport(method, url, **kw):
        calls.append(1)
        return Response(answers[len(calls) - 1])

    monkeypatch.setattr(http, "transport", transport)
    online = {"backend": "openrouter", "model": "openai/whisper-large-v3-turbo", "data_dir": str(tmp_path)}
    segments = cloud.transcribe(tmp_path / "v.mp4", "vid00000002", tmp_path / "t", online)
    assert [(w["word"], w["start"]) for s in segments for w in s.words] == [("Okay,", 363.0), ("hello.", 363.4)]


def test_words_made_up_where_nobody_speaks_are_dropped_and_silent_parts_not_sent(audio, monkeypatch, tmp_path):
    """Measured: "Thank you." 640 times in one two-hour stream, in every quiet
    stretch. The voice detector local Whisper uses runs on each part first."""
    keys.save_key(tmp_path, "openrouter", KEY)
    speech = iter([[], [(10.0, 14.0)], [(4.0, 7.0)]])     # part 1: nobody speaks
    monkeypatch.setattr(cloud, "_speech_in", lambda audio: next(speech))
    answers = [
        {"segments": [{"text": "Thank you. So this is it."}],
         "words": [{"word": "Thank", "start": 2.0, "end": 2.3}, {"word": "you.", "start": 2.3, "end": 2.6},
                   {"word": "So", "start": 10.5, "end": 10.7}, {"word": "this", "start": 10.7, "end": 11.0},
                   {"word": "is", "start": 11.0, "end": 11.2}, {"word": "it.", "start": 11.2, "end": 11.6}]},
        {"segments": [{"text": "Right."}], "words": [{"word": "Right.", "start": 5.0, "end": 5.4}]},
    ]
    calls = []

    def transport(method, url, **kw):
        calls.append(1)
        return Response(answers[len(calls) - 1])

    monkeypatch.setattr(http, "transport", transport)
    online = {"backend": "openrouter", "model": "openai/whisper-large-v3-turbo", "data_dir": str(tmp_path)}
    segments = cloud.transcribe(tmp_path / "v.mp4", "vid00000004", tmp_path / "t", online)
    assert len(calls) == 2                                # the silent part wasn't sent
    assert [w["word"] for s in segments for w in s.words] == ["So", "this", "is", "it.", "Right."]


def test_a_word_counts_as_spoken_close_to_detected_speech():
    speech = [(10.0, 14.0)]
    assert cloud._spoken({"start": 14.1, "end": 14.5}, speech)          # within the pad
    assert not cloud._spoken({"start": 15.0, "end": 15.4}, speech)
    assert not cloud._spoken({"start": 2.0, "end": 2.3}, speech)


def test_a_video_where_nothing_is_said_has_an_empty_transcript(audio, monkeypatch, tmp_path):
    keys.save_key(tmp_path, "openrouter", KEY)
    monkeypatch.setattr(http, "transport", lambda *a, **k: Response({"text": "", "segments": [], "words": []}))
    online = {"backend": "openrouter", "model": "openai/whisper-large-v3-turbo", "data_dir": str(tmp_path)}
    assert cloud.transcribe(tmp_path / "v.mp4", "vid00000003", tmp_path / "t", online) == []


@pytest.mark.parametrize("value,code", [
    ("english", "en"), ("en", "en"), ("Spanish", "es"), ("", ""),
    ("es-mx", "es"), ("fil", "tl"), ("tagalog", "tl"),
])
def test_languages_become_codes(value, code):
    assert cloud.normalize_language(value) == code


def test_xai_sends_the_configured_language_with_format(audio, monkeypatch, tmp_path):
    """format=true is what xAI rejects unless language is in the same form."""
    keys.save_key(tmp_path, "xai", "xai-" + "Qr9" * 20)
    monkeypatch.setattr(cloud, "_duration", lambda path: 30.0)
    calls = []

    def transport(method, url, **kw):
        calls.append(kw)
        return Response({"language": "id", "words": [{"text": "Halo.", "start": 0.2, "end": 0.6}]})

    monkeypatch.setattr(http, "transport", transport)
    online = {"backend": "xai", "data_dir": str(tmp_path)}
    cloud.transcribe(tmp_path / "v.mp4", "idvid", tmp_path / "t", online, language="id")
    form = calls[0]["data"]
    assert form == {"model": "grok-voice-transcribe-2.0", "language": "id", "format": "true"}
    name, payload, mime = calls[0]["files"]["file"]
    assert name.endswith(".mp3") and payload == b"mp3" and mime == "audio/mpeg"
    saved = json.loads((tmp_path / "t" / "idvid.json").read_text(encoding="utf-8"))
    assert saved["language"] == "id"

    calls.clear()
    cloud.transcribe(tmp_path / "v.mp4", "tlvid", tmp_path / "t", online, language="tl")
    assert calls[0]["data"]["language"] == "fil" and calls[0]["data"]["format"] == "true"
    assert json.loads((tmp_path / "t" / "tlvid.json").read_text(encoding="utf-8"))["language"] == "tl"


def test_xai_auto_detect_omits_format_then_reuses_the_detected_language(audio, monkeypatch, tmp_path):
    keys.save_key(tmp_path, "xai", "xai-" + "Qr9" * 20)
    monkeypatch.setattr(cloud, "_duration", lambda path: 1300.0)  # two 1200 s parts
    calls = []
    answers = [
        {"language": "es-mx", "words": [{"text": "Hola", "start": 0.2, "end": 0.5}]},
        {"language": "es-mx", "words": [{"text": "otra", "start": 1.0, "end": 1.4}]},
    ]

    def transport(method, url, **kw):
        calls.append(kw)
        return Response(answers[len(calls) - 1])

    monkeypatch.setattr(http, "transport", transport)
    online = {"backend": "xai", "data_dir": str(tmp_path)}
    for language in (None, "auto"):
        calls.clear()
        cloud.transcribe(tmp_path / "v.mp4", f"autovid-{language}", tmp_path / "t", online, language=language)
        assert set(calls[0]["data"]) == {"model"}
        assert calls[1]["data"]["language"] == "es" and calls[1]["data"]["format"] == "true"
        assert calls[0]["files"]["file"][2] == calls[1]["files"]["file"][2] == "audio/mpeg"
        saved = json.loads((tmp_path / "t" / f"autovid-{language}.json").read_text(encoding="utf-8"))
        assert saved["language"] == "es"


def test_xai_text_without_word_timings_is_refused():
    with pytest.raises(LLMError) as err:
        cloud._xai_words({"text": "hello", "words": [{"text": "hello"}]})
    assert err.value.kind == "bad_response"
    assert cloud._xai_words({"text": "", "words": []}) == []
    kept = cloud._xai_words({"words": [{"text": "Hi", "start": 0.0, "end": 0.2}, "nope"]})
    assert kept == [{"start": 0.0, "end": 0.2, "word": "Hi"}]


def test_openai_and_xai_requests(audio, monkeypatch, tmp_path):
    keys.save_key(tmp_path, "openai", "sk-proj-" + "Ab3" * 16)
    keys.save_key(tmp_path, "xai", "xai-" + "Qr9" * 20)
    monkeypatch.setattr(cloud, "_duration", lambda path: 30.0)
    calls = []

    def transport(method, url, **kw):
        calls.append({"url": url, **kw})
        if url.endswith("/stt"):
            return Response({"language": "en", "words": [{"text": "Hi.", "start": 0.2, "end": 0.5}]})
        return Response({"language": "english", "segments": [{"text": "Hi."}],
                         "words": [{"word": "Hi", "start": 0.2, "end": 0.5}]})

    monkeypatch.setattr(http, "transport", transport)
    cloud.transcribe(tmp_path / "v.mp4", "a", tmp_path / "t", {"backend": "openai", "data_dir": str(tmp_path)})
    cloud.transcribe(tmp_path / "v.mp4", "b", tmp_path / "t", {"backend": "xai", "data_dir": str(tmp_path)})
    openai_call, xai_call = calls
    assert openai_call["url"] == "https://api.openai.com/v1/audio/transcriptions"
    assert ("timestamp_granularities[]", "word") in openai_call["data"]
    assert ("model", "whisper-1") in openai_call["data"]
    assert "file" in openai_call["files"]
    assert xai_call["url"] == "https://api.x.ai/v1/stt" and xai_call["data"]["model"] == "grok-voice-transcribe-2.0"
    # No content language: format=true is illegal without one, so it stays off.
    assert "language" not in xai_call["data"] and "format" not in xai_call["data"]
    assert xai_call["files"]["file"][2] == "audio/mpeg"


def test_no_key_means_nothing_is_sent(audio, monkeypatch, tmp_path):
    monkeypatch.setattr(http, "transport", lambda *a, **k: pytest.fail("sent without a key"))
    with pytest.raises(LLMError) as err:
        cloud.transcribe(tmp_path / "v.mp4", "a", tmp_path / "t", {"backend": "openrouter", "data_dir": str(tmp_path)})
    assert err.value.kind == "not_configured"


def test_a_provider_that_cannot_transcribe_is_refused(tmp_path):
    with pytest.raises(LLMError):
        cloud.transcribe(tmp_path / "v.mp4", "a", tmp_path / "t", {"backend": "anthropic", "data_dir": str(tmp_path)})


def test_local_stays_local_and_online_goes_online(monkeypatch, tmp_path):
    pytest.importorskip("numpy", reason="core.pipeline imports numpy, which CI does not install")
    from core.pipeline import online_transcription
    from transcription import transcriber

    config = {"paths": {"data_dir": str(tmp_path)}}
    assert online_transcription(config) is None
    assert online_transcription({**config, "transcription": {"backend": "local"}}) is None
    online = online_transcription({**config, "transcription": {"backend": "openai", "model": "whisper-1"}})
    assert online == {"backend": "openai", "model": "whisper-1", "data_dir": str(tmp_path)}

    sent = []
    monkeypatch.setattr(cloud, "transcribe", lambda *a, **k: sent.append(a) or [])
    transcriber.transcribe(tmp_path / "v.mp4", "x", tmp_path / "t", online=online)
    assert len(sent) == 1
