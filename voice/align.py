"""When each word of a script is said in the speech made from it.

No voice engine here reports word times (Kokoro's runtime can place sounds,
not words; Voicebox and the OpenAI-style route send only the audio). The app
already has a tool that does: its Whisper transcription. So the finished WAV
is transcribed like any video's sound, and what Whisper heard is snapped
onto the script's own words: the captions then show the words as written,
at the moments they are spoken.

    words(wav, text, data_dir=..., language="en") -> [{"word", "start", "end"}, ...]

`snap` is the pure half (no Whisper, no numpy), so it is tested everywhere.
"""

from __future__ import annotations

import shutil
import unicodedata
import uuid
from difflib import SequenceMatcher
from pathlib import Path


def _key(word: str) -> str:
    """A word as it is compared: letters and digits only, case folded."""
    text = unicodedata.normalize("NFKC", word).casefold()
    return "".join(c for c in text if unicodedata.category(c)[0] in "LNM")


def _spread(words: list[str], start: float, end: float) -> list[tuple[float, float]]:
    """`words` laid across start..end, each given time by its length."""
    if not words:
        return []
    end = max(end, start)
    weights = [max(1, len(_key(w))) for w in words]
    total = float(sum(weights))
    out, at = [], start
    for weight in weights:
        span = (end - start) * weight / total
        out.append((at, at + span))
        at += span
    return out


def snap(script: str, heard: list[dict], duration: float) -> list[dict]:
    """The script's words with times taken from the words Whisper heard.

    `heard` is [{"word", "start", "end"}, ...] in order. Words Whisper heard
    the same (ignoring case and punctuation) take its times. Where it heard
    something else (a number read out, a name spelled differently, a word it
    missed), the script's words there share the time between their
    neighbours. Every script word comes back, in order, and times never run
    backwards or past `duration`."""
    words = script.split()
    if not words:
        return []
    duration = max(0.0, float(duration or 0.0))
    heard = [h for h in heard
             if isinstance(h, dict) and isinstance(h.get("start"), (int, float))
             and isinstance(h.get("end"), (int, float)) and _key(str(h.get("word", "")))]
    if not heard:
        return [{"word": w, "start": round(a, 3), "end": round(b, 3)}
                for w, (a, b) in zip(words, _spread(words, 0.0, duration))]

    a_keys = [_key(w) for w in words]
    b_keys = [_key(str(h["word"])) for h in heard]
    times: list[tuple[float, float] | None] = [None] * len(words)
    matcher = SequenceMatcher(None, a_keys, b_keys, autojunk=False)
    for tag, a0, a1, b0, b1 in matcher.get_opcodes():
        if tag == "equal" or (tag == "replace" and a1 - a0 == b1 - b0):
            for i, j in zip(range(a0, a1), range(b0, b1)):
                times[i] = (float(heard[j]["start"]), float(heard[j]["end"]))
        elif tag == "replace":
            # A different number of words (a number read out as three words):
            # they share the stretch Whisper heard in their place.
            spread = _spread(words[a0:a1], float(heard[b0]["start"]), float(heard[b1 - 1]["end"]))
            for i, span in zip(range(a0, a1), spread):
                times[i] = span
    # Words Whisper has nothing for: the gap between their timed neighbours.
    i = 0
    while i < len(words):
        if times[i] is not None:
            i += 1
            continue
        j = i
        while j < len(words) and times[j] is None:
            j += 1
        before = times[i - 1][1] if i > 0 else 0.0
        after = times[j][0] if j < len(words) else max(duration, before)
        for k, span in zip(range(i, j), _spread(words[i:j], before, max(after, before))):
            times[k] = span
        i = j

    out, last = [], 0.0
    limit = duration if duration > 0 else float("inf")
    for word, (start, end) in zip(words, times):
        start = min(max(start, last), limit)
        end = min(max(end, start), limit)
        out.append({"word": word, "start": round(start, 3), "end": round(end, 3)})
        last = end
    return out


def words(wav_path: Path, text: str, *, data_dir, language: str | None = None, model_size: str = "auto",
          device: str = "auto", online: dict | None = None) -> list[dict]:
    """Word times for speech made from `text`, through the app's own
    transcription (transcription/transcriber.py), snapped to the script.

    Each call transcribes under a name of its own in a scratch folder that is
    removed afterwards: the transcriber keeps its results by name, and two
    takes of the same script must never share one. `model_size` and `device`
    are the app's Whisper settings (config["whisper"]). `online` is the
    transcriber's online-service setting; POST /voice/speak always leaves it
    None, so spoken sound is timed on this PC and sent nowhere."""
    from core import progress
    from transcription.transcriber import transcribe
    from voice import wav as wav_files

    wav_path = Path(wav_path)
    _rate, duration = wav_files.info(wav_path)
    scratch = Path(data_dir) / "voice" / "align" / uuid.uuid4().hex
    # Whisper's progress reports would otherwise be shown as the transcription
    # of whatever video is being clipped at this moment.
    before = getattr(progress._local, "tags", None)
    progress.set_thread_tags(stage="voice")
    try:
        segments = transcribe(wav_path, f"voice_{uuid.uuid4().hex}", scratch, model_size=model_size,
                              device=device, language=language, online=online)
    finally:
        progress.set_thread_tags(**(before or {}))
        shutil.rmtree(scratch, ignore_errors=True)
    heard = [w for seg in segments for w in (seg.words or [])]
    return snap(text, heard, duration)
