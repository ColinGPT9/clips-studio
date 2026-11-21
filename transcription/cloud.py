"""Transcription online, on the user's own API key.

Local Whisper (transcription/transcriber.py) is the default and this does not
touch it. This is the opt-in for a PC too slow to transcribe long videos: the
audio is sent, in chunks, to the provider the user chose, with their key.

The result has to be indistinguishable from a local transcript to everything
downstream, so it is written to the same cache file in the same shape:
{"video_id", "language", "segments": [{"start", "end", "text", "words"}]},
with words as {"start", "end", "word"} and punctuation on the words, in
absolute seconds from the start of the video. Captions, filler cuts, the
editor's word tools, sentence snapping and translation all read that.

Only providers that return word timings are offered: without them captions
would drift and word editing would vanish. A part where nothing is said (a
stream's music-only intro: measured, 10 minutes of one) has no words and
that's fine; a part with text but no word timings is the model's limit.

Whisper makes words up where nobody speaks: "Thank you." 640 times in one
two-hour stream, "谢谢大家" and the like, one in every quiet stretch. Local
Whisper never shows them because faster-whisper runs a voice detector
(Silero) first. The same detector runs here, on each part before it is
sent: a part where nobody speaks isn't sent at all (and isn't paid for),
and a word the model returns outside the speech it found is dropped.
Nothing falls back to local Whisper; a failing provider fails the job, in
plain words.
"""

import base64
import json
import logging
import re
import subprocess
import tempfile
from pathlib import Path

from core import cancel, progress
from core.binaries import ffmpeg, ffprobe
from core.models import Segment
from llm.providers import keys
from llm.providers.base import LLMError
from llm.providers.catalog import get
from llm.providers.http import send

log = logging.getLogger(__name__)

# A three-second spoken phrase, for checking a voice model the app has not
# seen return word timings before (check_model).
PROBE = Path(__file__).resolve().parent / "assets" / "probe.mp3"

OVERLAP = 2.0          # seconds of audio shared by neighbouring chunks
SPEECH_PAD = 0.2       # a word counts as spoken this close to detected speech
PAUSE = 0.6            # a gap this long between words starts a new segment
MAX_SEGMENT = 15.0     # and no segment runs longer than this
STT_TIMEOUT = 300
_SENTENCE_END = re.compile(r"[.!?…。！？]['\")\]]*$")


def transcribe(video_path: Path, video_id: str, transcript_dir: Path, online: dict,
               language: str | None = None) -> list[Segment]:
    provider = str(online.get("backend") or "")
    spec = get(provider)
    if spec is None or not spec.stt:
        raise LLMError("not_configured", f"'{provider}' can't transcribe. Choose another in Settings → AI.")
    spec, key = keys.resolve(online.get("data_dir") or "data", spec)
    if not key:
        raise LLMError("not_configured", f"No {spec.label} API key is saved. Add yours in Settings → AI.")
    model = str(online.get("model") or spec.stt["models"][0])
    length = float(spec.stt["chunk_seconds"])

    duration = _duration(video_path)
    starts = [i * length for i in range(max(1, int(-(-duration // length))))]
    print(f"  Transcribing online with {spec.label} ({model}), {len(starts)} part(s)...")
    # xAI rejects format=true unless language is set. Auto-detect leaves it
    # off for the first part that is actually sent; a language the model
    # reports is reused for the parts after that, so a configured language
    # is never replaced with English.
    if spec.stt.get("format") == "xai" and _xai_language(language) is None:
        print("      xAI: no content language is set, so the spoken language is detected")
        log.info("xAI speech-to-text language is auto; format stays off until a language is known")

    words: list[dict] = []
    detected = ""
    silent = made_up = 0
    with tempfile.TemporaryDirectory(prefix="ck-stt-") as tmp:
        for i, start in enumerate(starts):
            cancel.check_active()
            progress.emit(stage="transcribe", fraction=i / len(starts))
            clip_from = max(0.0, start - OVERLAP)
            audio = _extract(video_path, Path(tmp) / f"part{i}.mp3", clip_from, length + 2 * OVERLAP)
            speech = _speech_in(audio)
            if speech == []:
                silent += 1
                continue
            request_language = language
            if spec.stt.get("format") == "xai" and _xai_language(language) is None:
                request_language = _xai_language(detected)
            part_words, part_language = _request(spec, key, model, audio, request_language)
            if speech is not None:
                kept = [w for w in part_words if _spoken(w, speech)]
                made_up += len(part_words) - len(kept)
                part_words = kept
            detected = detected or part_language
            # A word belongs to the part its start falls in; the overlap is
            # context for the model, so words at a boundary are not cut off.
            end = start + length if i < len(starts) - 1 else float("inf")
            for w in part_words:
                absolute = {**w, "start": round(w["start"] + clip_from, 2), "end": round(w["end"] + clip_from, 2)}
                if start <= absolute["start"] < end:
                    words.append(absolute)
    progress.emit(stage="transcribe", fraction=1.0)
    if silent or made_up:
        print(f"      Nobody speaks in {silent} part(s), not sent; {made_up} word(s) outside speech dropped")

    segments = group_words(words)
    # "auto" is the settings value for detect-per-video. It is not a language
    # code, so the transcript keeps what the provider reported instead.
    forced = (language or "").strip()
    if forced.lower() in _AUTO_LANGUAGE:
        forced = ""
    lang = (forced or normalize_language(detected) or "en").lower()
    transcript_dir.mkdir(parents=True, exist_ok=True)
    payload = {"video_id": video_id, "language": lang, "source": f"{provider}/{model}",
               "segments": [vars(s) for s in segments]}
    (transcript_dir / f"{video_id}.json").write_text(json.dumps(payload), encoding="utf-8")
    # The repetition guard runs in transcriber.transcribe(), on these the same
    # as on a local transcript; importing it here would make a cycle.
    print(f"      {len(segments)} segments ({lang})")
    return segments


def check_model(spec, key: str, model: str) -> tuple[bool, str]:
    """Does this voice model return the word timings captions need?

    One real request with the short test clip, through exactly the code a job
    uses, so a yes here means a yes in a job. Only ever run because the user
    picked the model; it costs a tiny fraction of a cent on their key.
    """
    try:
        words, _language = _request(spec, key, model, PROBE, "en")
    except LLMError as e:
        return False, e.message
    timed = [w for w in words if w["end"] > w["start"]]
    if not timed:
        return False, (f"{model} didn't return word timings, which captions and word editing "
                       "need. Choose another voice model.")
    return True, f"{model} works: it returned word timings for the test clip."


# ---- where someone speaks --------------------------------------------------------


def _speech_in(audio: Path) -> list[tuple[float, float]] | None:
    """Where someone speaks in a part, in seconds from its start, by the voice
    detector local Whisper uses (Silero, through faster-whisper, with its
    defaults). None when it can't run: then every word is kept, as before."""
    try:
        from faster_whisper.audio import decode_audio
        from faster_whisper.vad import VadOptions, get_speech_timestamps
    except ImportError:
        return None
    try:
        samples = decode_audio(str(audio), sampling_rate=16000)
        found = get_speech_timestamps(samples, VadOptions(), sampling_rate=16000)
    except Exception as e:  # a part it can't read is sent as before
        print(f"      (couldn't check a part for speech: {e})")
        return None
    return [(t["start"] / 16000, t["end"] / 16000) for t in found]


def _spoken(word: dict, speech: list[tuple[float, float]]) -> bool:
    return any(word["start"] < end + SPEECH_PAD and word["end"] > start - SPEECH_PAD for start, end in speech)


# ---- one request per provider format -------------------------------------------


# "auto" / missing means the model should detect the language. "und" is the
# ISO code for undetermined, which xAI does not accept as a formatting language.
_AUTO_LANGUAGE = {"", "auto", "none", "und", "unknown"}
# The app's content language for Filipino is tl; xAI's speech API spells it fil.
_XAI_LANGUAGE_CODES = {"tl": "fil"}
_APP_LANGUAGE_CODES = {"fil": "tl"}


def _xai_language(language: str | None) -> str | None:
    """Language code for xAI /stt, or None when this part should be detected.

    A configured content language is sent as xAI spells it. Auto-detect
    (None, "", "auto") stays unset: format=true is a 400 without a code,
    and English is not invented in its place.
    """
    text = (language or "").strip().lower()
    if text in _AUTO_LANGUAGE:
        return None
    code = normalize_language(text)
    if not code:
        log.warning("xAI speech-to-text ignored an unrecognised language %r", language)
        return None
    return _XAI_LANGUAGE_CODES.get(code, code)


def _request(spec, key: str, model: str, audio: Path, language: str | None) -> tuple[list[dict], str]:
    fmt = spec.stt["format"]
    data = audio.read_bytes()
    if fmt == "openrouter":
        body = {"model": model, "response_format": "verbose_json",
                "timestamp_granularities": ["segment", "word"],
                "input_audio": {"data": base64.b64encode(data).decode("ascii"), "format": "mp3"}}
        if language:
            body["language"] = language
        answer = send(spec, key, "POST", "/audio/transcriptions", json_body=body, timeout=STT_TIMEOUT)
        return _whisper_words(answer), str(answer.get("language") or "")
    if fmt == "openai":
        form = [("model", model), ("response_format", "verbose_json"),
                ("timestamp_granularities[]", "word"), ("timestamp_granularities[]", "segment")]
        if language:
            form.append(("language", language))
        answer = send(spec, key, "POST", "/audio/transcriptions", data=form,
                      files={"file": (audio.name, data, "audio/mpeg")}, timeout=STT_TIMEOUT)
        return _whisper_words(answer), str(answer.get("language") or "")
    if fmt == "xai":
        # format=true asks for written numbers and currency. xAI requires
        # language whenever it is on, and word timings come back either way.
        # The file field stays last: requests encodes `data` before `files`.
        spoken = _xai_language(language)
        form = {"model": model}
        if spoken:
            form["language"] = spoken
            form["format"] = "true"
        try:
            answer = send(spec, key, "POST", "/stt", data=form,
                          files={"file": (audio.name, data, "audio/mpeg")}, timeout=STT_TIMEOUT)
        except LLMError as e:
            log.warning("xAI speech-to-text failed (%s): %s", e.kind, e.message)
            raise
        return _xai_words(answer), str(answer.get("language") or "")
    raise LLMError("not_configured", f"{spec.label} transcription is not set up in this version.")


def _xai_words(answer: dict) -> list[dict]:
    """Timed words from an xAI /stt answer. Text with no timings is refused,
    the same as a Whisper answer captions could not place."""
    if not isinstance(answer, dict):
        log.warning("xAI speech-to-text returned %s, not an object", type(answer).__name__)
        raise LLMError("bad_response", "xAI Grok returned a transcript this app couldn't read.")
    words: list[dict] = []
    skipped = 0
    for w in answer.get("words") or []:
        if not isinstance(w, dict):
            skipped += 1
            continue
        text = str(w.get("text") or "").strip()
        if not text:
            continue
        try:
            start, end = float(w["start"]), float(w["end"])
        except (KeyError, TypeError, ValueError):
            skipped += 1
            continue
        words.append({"start": start, "end": end, "word": text})
    if skipped:
        log.warning("xAI speech-to-text dropped %s word(s) without timings", skipped)
        print(f"      (xAI dropped {skipped} word(s) without timings)")
    said = str(answer.get("text") or "").strip()
    if said and not words:
        raise LLMError("bad_response", "The transcription came back without word timings, which "
                                       "captions need. Choose a different transcription model.")
    return words


def _whisper_words(answer: dict) -> list[dict]:
    """Words from a Whisper verbose_json answer, with the segment text's
    punctuation put back: whisper-1 returns bare words ("there", not
    "there."), and captions and sentence grouping need the punctuation.
    Empty for a part where nothing is said; text without word timings is
    refused."""
    words = [{"start": float(w["start"]), "end": float(w["end"]), "word": str(w.get("word") or "").strip()}
             for w in answer.get("words") or [] if isinstance(w, dict) and str(w.get("word") or "").strip()]
    if not words:
        segments = [s for s in answer.get("segments") or [] if isinstance(s, dict)]
        said = str(answer.get("text") or "").strip() or any(str(s.get("text") or "").strip() for s in segments)
        if not said:
            return []   # nothing said in this part: music, a "starting soon" screen
        raise LLMError("bad_response", "The transcription came back without word timings, which "
                                       "captions need. Choose a different transcription model.")
    return _punctuate(words, answer.get("segments") or [])


def _bare(token: str) -> str:
    return re.sub(r"[^\w]", "", token.lower())


def _punctuate(words: list[dict], segments: list[dict]) -> list[dict]:
    tokens = [t for s in segments if isinstance(s, dict) for t in str(s.get("text") or "").split()]
    j = 0
    for w in words:
        if re.search(r"[^\w\s]", w["word"]):
            continue  # already punctuated
        for k in range(j, min(j + 4, len(tokens))):  # a short look-ahead keeps a miss from derailing it
            if _bare(tokens[k]) == _bare(w["word"]):
                w["word"] = tokens[k]
                j = k + 1
                break
    return words


# ---- shaping --------------------------------------------------------------------


def group_words(words: list[dict]) -> list[Segment]:
    """Whisper-like segments from timed words: a new one at a pause, after the
    end of a sentence, or once one has run MAX_SEGMENT seconds."""
    segments: list[Segment] = []
    current: list[dict] = []
    for w in sorted(words, key=lambda x: x["start"]):
        if current and (w["start"] - current[-1]["end"] >= PAUSE
                        or _SENTENCE_END.search(current[-1]["word"])
                        or w["end"] - current[0]["start"] > MAX_SEGMENT):
            segments.append(_segment(current))
            current = []
        current.append(w)
    if current:
        segments.append(_segment(current))
    return segments


def _segment(words: list[dict]) -> Segment:
    return Segment(
        start=round(words[0]["start"], 2),
        end=round(words[-1]["end"], 2),
        text=" ".join(w["word"] for w in words).strip(),
        words=[{"start": w["start"], "end": w["end"], "word": w["word"]} for w in words],
    )


def normalize_language(value: str) -> str:
    """"en" stays "en"; "english" (whisper-1) becomes "en"; "es-mx" becomes "es"."""
    text = (value or "").strip().lower().replace("_", "-")
    primary = text.split("-", 1)[0]
    if re.fullmatch(r"[a-z]{2,3}", primary):
        return _APP_LANGUAGE_CODES.get(primary, primary)
    from multilingual.languages import LANGUAGES

    names = {english.split(" ")[0].lower(): code for code, (english, _native, _prompt) in LANGUAGES.items()}
    names.update({"mandarin": "zh", "tagalog": "tl", "bangla": "bn"})
    return names.get(text.split(" ")[0], "")


# ---- audio ------------------------------------------------------------------------


def _duration(video_path: Path) -> float:
    out = subprocess.run(
        [ffprobe(), "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(video_path)],
        capture_output=True, text=True,
    ).stdout.strip()
    try:
        return max(1.0, float(out))
    except ValueError:
        return 1.0


def _extract(video_path: Path, out: Path, start: float, seconds: float) -> Path:
    """Mono 16 kHz speech-quality MP3: about 2.4 MB per ten minutes, inside
    every provider's upload limit, and a format they all accept."""
    result = subprocess.run(
        [ffmpeg(), "-v", "error", "-y", "-ss", f"{start:.2f}", "-t", f"{seconds:.2f}",
         "-i", str(video_path), "-vn", "-ac", "1", "-ar", "16000", "-c:a", "libmp3lame", "-b:a", "32k", str(out)],
        capture_output=True, text=True,
    )
    if result.returncode != 0 or not out.exists():
        raise RuntimeError(f"Could not extract audio for transcription: {result.stderr.strip()[:200]}")
    return out
