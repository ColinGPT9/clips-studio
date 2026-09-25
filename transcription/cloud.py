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
would drift and word editing would vanish. Nothing falls back to local
Whisper; a failing provider fails the job, in plain words.
"""

import base64
import json
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

OVERLAP = 2.0          # seconds of audio shared by neighbouring chunks
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
    key = keys.load_key(online.get("data_dir") or "data", provider)
    if not key:
        raise LLMError("not_configured", f"No {spec.label} API key is saved. Add yours in Settings → AI.")
    model = str(online.get("model") or spec.stt["models"][0])
    length = float(spec.stt["chunk_seconds"])

    duration = _duration(video_path)
    starts = [i * length for i in range(max(1, int(-(-duration // length))))]
    print(f"  Transcribing online with {spec.label} ({model}), {len(starts)} part(s)...")

    words: list[dict] = []
    detected = ""
    with tempfile.TemporaryDirectory(prefix="ck-stt-") as tmp:
        for i, start in enumerate(starts):
            cancel.check_active()
            progress.emit(stage="transcribe", fraction=i / len(starts))
            clip_from = max(0.0, start - OVERLAP)
            audio = _extract(video_path, Path(tmp) / f"part{i}.mp3", clip_from, length + 2 * OVERLAP)
            part_words, part_language = _request(spec, key, model, audio, language)
            detected = detected or part_language
            # A word belongs to the part its start falls in; the overlap is
            # context for the model, so words at a boundary are not cut off.
            end = start + length if i < len(starts) - 1 else float("inf")
            for w in part_words:
                absolute = {**w, "start": round(w["start"] + clip_from, 2), "end": round(w["end"] + clip_from, 2)}
                if start <= absolute["start"] < end:
                    words.append(absolute)
    progress.emit(stage="transcribe", fraction=1.0)

    segments = group_words(words)
    lang = (language or normalize_language(detected) or "en").lower()
    transcript_dir.mkdir(parents=True, exist_ok=True)
    payload = {"video_id": video_id, "language": lang, "source": f"{provider}/{model}",
               "segments": [vars(s) for s in segments]}
    (transcript_dir / f"{video_id}.json").write_text(json.dumps(payload), encoding="utf-8")
    # The repetition guard runs in transcriber.transcribe(), on these the same
    # as on a local transcript; importing it here would make a cycle.
    print(f"      {len(segments)} segments ({lang})")
    return segments


# ---- one request per provider format -------------------------------------------


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
        form = {"model": model, "format": "true"}
        if language:
            form["language"] = language
        answer = send(spec, key, "POST", "/stt", data=form,
                      files={"file": (audio.name, data, "audio/mpeg")}, timeout=STT_TIMEOUT)
        words = [{"start": float(w["start"]), "end": float(w["end"]), "word": str(w.get("text") or "").strip()}
                 for w in answer.get("words") or [] if isinstance(w, dict) and str(w.get("text") or "").strip()]
        return words, str(answer.get("language") or "")
    raise LLMError("not_configured", f"{spec.label} transcription is not set up in this version.")


def _whisper_words(answer: dict) -> list[dict]:
    """Words from a Whisper verbose_json answer, with the segment text's
    punctuation put back: whisper-1 returns bare words ("there", not
    "there."), and captions and sentence grouping need the punctuation."""
    words = [{"start": float(w["start"]), "end": float(w["end"]), "word": str(w.get("word") or "").strip()}
             for w in answer.get("words") or [] if isinstance(w, dict) and str(w.get("word") or "").strip()]
    if not words:
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
    """"en" stays "en"; "english" (whisper-1) becomes "en"."""
    text = (value or "").strip().lower()
    if re.fullmatch(r"[a-z]{2,3}", text):
        return text
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
