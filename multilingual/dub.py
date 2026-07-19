"""AI dubbing: speak the translated captions over the clip.

Local and free, like everything else here — Piper runs on the CPU, its
voices are ~60 MB each, and nothing is downloaded until a language is
actually dubbed. Piper is an OPTIONAL dependency: without it the rest of
multilingual publishing works exactly the same and the UI simply doesn't
offer dubbing.

Timing is the hard part of dubbing, not the voice. Translated speech runs
longer than English, so each utterance is synthesized, measured, and (only
when it misses its slot) re-synthesized at an adjusted speaking rate —
Piper stretches phonemes properly, which sounds better than speeding up
audio after the fact. Anything that still doesn't fit keeps its natural
pace rather than turning into chipmunk speech; a slight overlap into a
pause is less jarring.

The original audio stays underneath at low volume so music and room tone
survive. That also means the creator's own voice is faintly audible —
removing it properly needs a source-separation model, which is a heavier
dependency than this stage is worth.
"""

import subprocess
import sys
from pathlib import Path

# One voice per language. Japanese is absent from Piper's catalogue, so it
# gets subtitles only — stated rather than silently skipped.
VOICES: dict[str, str] = {
    "en": "en_US-lessac-medium",
    "es": "es_ES-davefx-medium",
    "pt": "pt_BR-cadu-medium",
    "fr": "fr_FR-siwis-medium",
    "de": "de_DE-thorsten-medium",
    "hi": "hi_IN-pratham-medium",
    "id": "id_ID-news_tts-medium",
    "ru": "ru_RU-denis-medium",
    "ar": "ar_JO-kareem-medium",
}

RATE_MIN, RATE_MAX = 0.72, 1.45   # speaking-rate range that still sounds human
DUCK = 0.12                        # original audio kept this loud underneath


def available() -> bool:
    """True when the optional Piper dependency is installed."""
    try:
        import piper  # noqa: F401

        return True
    except Exception:
        return False


def supported(language: str) -> bool:
    return language in VOICES


def ensure_voice(language: str, voices_dir: Path, voice_id: str | None = None) -> str | None:
    """Download the chosen voice (or this language's default) if needed."""
    from multilingual.voices import resolve

    name, _speaker = resolve(voice_id, language)
    if not name:
        return None
    voices_dir.mkdir(parents=True, exist_ok=True)
    if (voices_dir / f"{name}.onnx").exists():
        return name
    print(f"      Downloading the {language} voice ({name})…")
    r = subprocess.run(
        [sys.executable, "-m", "piper.download_voices", name, "--data-dir", str(voices_dir)],
        capture_output=True, text=True, timeout=900,
    )
    if r.returncode != 0 or not (voices_dir / f"{name}.onnx").exists():
        print(f"      (voice download failed: {(r.stderr or '')[-200:]})")
        return None
    return name


def _duration(path: Path) -> float:
    r = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "csv=p=0", str(path)],
        capture_output=True, text=True,
    )
    try:
        return float(r.stdout.strip())
    except ValueError:
        return 0.0


def _speak(text: str, voice: str, voices_dir: Path, out: Path, rate: float = 1.0,
           speaker: int | None = None) -> bool:
    cmd = [
        sys.executable, "-m", "piper", "-m", voice, "--data-dir", str(voices_dir),
        "-f", str(out), "--length-scale", f"{rate:.3f}",
    ]
    if speaker is not None:
        cmd += ["-s", str(speaker)]
    r = subprocess.run(cmd, input=text, text=True, capture_output=True, timeout=300)
    return r.returncode == 0 and out.exists()


def _utterances(lines: list[dict]) -> list[tuple[str, float, float]]:
    """Caption lines regrouped into spoken sentences with their time span."""
    from multilingual.translate import _group_sentences

    out = []
    for text, idx in _group_sentences(lines):
        if not text.strip() or not idx:
            continue
        out.append((text.strip(), float(lines[idx[0]]["start"]), float(lines[idx[-1]]["end"])))
    return out


def dub(
    lines: list[dict],
    language: str,
    base_video: Path,
    out_path: Path,
    voices_dir: Path,
    work_dir: Path,
    voice_id: str | None = None,
) -> Path | None:
    """A copy of `base_video` speaking `lines` in `language`, or None.

    voice_id picks WHICH voice ("es_MX-claude-high", or "fr_FR-upmc-medium#1"
    for a specific speaker inside a multi-speaker voice)."""
    from multilingual.voices import resolve

    if not available() or not supported(language):
        return None
    voice = ensure_voice(language, voices_dir, voice_id)
    if voice is None:
        return None
    _name, speaker = resolve(voice_id, language)
    utterances = _utterances(lines)
    if not utterances:
        return None

    work_dir.mkdir(parents=True, exist_ok=True)
    pieces: list[tuple[Path, float]] = []
    for i, (text, start, end) in enumerate(utterances):
        wav = work_dir / f"{language}_{i:03d}.wav"
        if not _speak(text, voice, voices_dir, wav, speaker=speaker):
            continue
        slot = max(0.4, end - start)
        spoken = _duration(wav)
        # Only re-synthesize when it genuinely misses the slot; Piper
        # stretching phonemes beats post-hoc speed changes.
        if spoken > 0 and not (0.9 <= spoken / slot <= 1.1):
            rate = max(RATE_MIN, min(RATE_MAX, slot / spoken))
            _speak(text, voice, voices_dir, wav, rate=rate, speaker=speaker)
        pieces.append((wav, start))
    if not pieces:
        return None

    # Original audio ducked underneath, each utterance delayed to its slot.
    inputs: list[str] = ["-i", str(base_video.resolve())]
    for wav, _ in pieces:
        inputs += ["-i", str(wav.resolve())]
    chains = [f"[0:a]volume={DUCK}[bed]"]
    labels = ["[bed]"]
    for n, (_wav, start) in enumerate(pieces, start=1):
        ms = int(start * 1000)
        chains.append(f"[{n}:a]adelay={ms}|{ms},volume=1.6[v{n}]")
        labels.append(f"[v{n}]")
    chains.append(
        f"{''.join(labels)}amix=inputs={len(labels)}:normalize=0:dropout_transition=0[aout]"
    )
    cmd = [
        "ffmpeg", "-y", "-v", "error", *inputs,
        "-filter_complex", ";".join(chains),
        "-map", "0:v", "-map", "[aout]",
        "-c:v", "copy", "-c:a", "aac", "-b:a", "160k",
        "-shortest", "-movflags", "+faststart",
        str(out_path.resolve()),
    ]
    r = subprocess.run(cmd, capture_output=True, text=True)
    for wav, _ in pieces:
        wav.unlink(missing_ok=True)
    if r.returncode != 0:
        print(f"      (dub mix failed: {(r.stderr or '')[-200:]})")
        return None
    return out_path
