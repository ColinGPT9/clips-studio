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

import os
import subprocess
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from core.binaries import ffmpeg, ffprobe
from core.paths import discard

# Default voice per language; creators pick a different one in the UI.
# Languages absent from Piper's catalogue simply aren't in here.
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
    "zh": "zh_CN-huayan-medium",
    "vi": "vi_VN-vais1000-medium",
    "tr": "tr_TR-dfki-medium",
    "ur": "ur_PK-aegis_female-medium",
    "bn": "bn_BD-google-medium",
    "it": "it_IT-paola-medium",
}
# Filipino, Thai and Korean have no Piper voice: subtitles only, which the
# UI states rather than skipping them silently.

RATE_MIN, RATE_MAX = 0.72, 1.45   # speaking-rate range that still sounds human
DUCK = 0.12                        # original audio kept this loud underneath


def available() -> bool:
    """True when Piper can be imported.

    Piper IS bundled as of 1.1.3, so this is normally true in an installed
    copy. It stays a check rather than an assumption because a source checkout
    without the optional dependency should still run everything else, with
    dubbing reported unavailable rather than failing — server.api gates every
    dubbing route on this.

    Bundling it required dropping the two `sys.executable -m piper` calls that
    used to live below. In a frozen build sys.executable is api.exe, not
    python.exe, and a PyInstaller executable cannot run `-m module`: they
    worked in a checkout and would have failed silently in the shipped app,
    which is the same trap that hid the missing FFmpeg path from yt-dlp.
    Everything now goes through Piper's Python API in-process.
    """
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
        return _installed_name(voices_dir, name)
    print(f"      Downloading the {language} voice ({name})…")
    try:
        from piper.download_voices import download_voice

        download_voice(name, voices_dir)
    except Exception as e:
        print(f"      (voice download failed: {e})")
        return None
    if not (voices_dir / f"{name}.onnx").exists():
        print("      (voice download produced no model file)")
        return None
    return _installed_name(voices_dir, name)


def _installed_name(voices_dir: Path, name: str) -> str | None:
    """The voice's name as the FILESYSTEM spells it, not as the caller did.

    The returned string is identical to `name`, so piper is invoked exactly
    as before. What changes is where the string comes from: a directory
    listing rather than an API parameter. That makes the guarantee structural
    instead of a promise — the value can only be the name of a model file
    that really sits in this folder, so it cannot be a path leading somewhere
    else, and it cannot start with "-" and become a piper flag.

    resolve() already validates the shape, and this is the second half of the
    same job: shape checks say what a value looks like, this says the file
    exists. It is also the part a scanner can follow, since taint stops at
    the filesystem.
    """
    for model in voices_dir.glob("*.onnx"):
        if model.stem == name:
            return model.stem
    return None


def _duration(path: Path) -> float:
    r = subprocess.run(
        [ffprobe(), "-v", "error", "-show_entries", "format=duration",
         "-of", "csv=p=0", str(path)],
        capture_output=True, text=True,
    )
    try:
        return float(r.stdout.strip())
    except ValueError:
        return 0.0


# One loaded voice per model file. Loading builds an ONNX session and reads
# the model off disk, and the old process-per-utterance approach paid that on
# every line of a clip.
_voices: dict[str, object] = {}
# Synthesis is serialised on purpose. It is called from a ThreadPoolExecutor
# (see the lanes below), and espeak-ng phonemization keeps process-global
# state, so two lanes phonemizing at once can corrupt each other's output.
# Same reasoning as the YOLO inference lock in video/tracker.py: the GPU work
# was never the parallel part worth having, and a wrong result is worse than
# a slower one.
_speak_lock = threading.Lock()


def _load_voice(model: Path):
    """The PiperVoice for this model, loaded once and kept.

    espeak_data_dir is left at its default, which Piper derives from its own
    package directory — that resolves correctly both in a checkout and inside
    the frozen bundle, where the data ships alongside the module.
    """
    key = str(model)
    voice = _voices.get(key)
    if voice is None:
        from piper import PiperVoice

        voice = PiperVoice.load(model)
        _voices[key] = voice
    return voice


def _speak(text: str, voice: str, voices_dir: Path, out: Path, rate: float = 1.0,
           speaker: int | None = None) -> bool:
    """Synthesize one utterance to `out` as a wav.

    In-process rather than `python -m piper`: sys.executable is api.exe in a
    frozen build and cannot run `-m module`, so shelling out worked in a
    checkout and would have failed silently in the shipped app.
    """
    import wave

    from piper import SynthesisConfig

    model = voices_dir / f"{voice}.onnx"
    if not model.exists():
        print(f"      (voice model missing: {model.name})")
        return False

    config = SynthesisConfig(length_scale=float(rate))
    if speaker is not None:
        config.speaker_id = int(speaker)

    with _speak_lock:
        try:
            piper_voice = _load_voice(model)
            with wave.open(str(out), "wb") as wav:
                piper_voice.synthesize_wav(text, wav, syn_config=config)
        except Exception as e:
            # Unicode is the historical failure here: the old subprocess path
            # encoded stdin with the Windows locale codec and silently lost
            # every Hindi, Arabic, Chinese, Urdu, Bengali, Korean and Thai
            # utterance. In-process there is no encoding step to get wrong.
            print(f"      (speech failed: {e})")
            return False

    return out.exists() and out.stat().st_size > 0


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
    workers: int | None = None,
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

    def synth(job: tuple[int, tuple[str, float, float]]) -> tuple[Path, float] | None:
        i, (text, start, end) = job
        wav = work_dir / f"{language}_{i:03d}.wav"
        if not _speak(text, voice, voices_dir, wav, speaker=speaker):
            return None
        slot = max(0.4, end - start)
        spoken = _duration(wav)
        # Only re-synthesize when it genuinely misses the slot; Piper
        # stretching phonemes beats post-hoc speed changes.
        if spoken > 0 and not (0.9 <= spoken / slot <= 1.1):
            rate = max(RATE_MIN, min(RATE_MAX, slot / spoken))
            _speak(text, voice, voices_dir, wav, rate=rate, speaker=speaker)
        return (wav, start)

    # Each utterance is its own Piper PROCESS that reloads the voice model,
    # so a clip's worth of them is mostly startup cost sitting idle. They
    # share no state, so running a few at once is safe and roughly halves
    # the wall time. Capped low: Piper is CPU-bound and each worker holds
    # its own copy of the model.
    # `workers` lets the caller shrink the pool when it is dubbing several
    # languages at once, so the lanes share cores instead of fighting.
    lanes = workers or ((os.cpu_count() or 4) // 2)
    lanes = max(1, min(4, lanes, len(utterances)))
    with ThreadPoolExecutor(max_workers=lanes) as pool:
        done = list(pool.map(synth, enumerate(utterances)))  # map keeps order
    pieces: list[tuple[Path, float]] = [p for p in done if p is not None]
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
        ffmpeg(), "-y", "-v", "error", *inputs,
        "-filter_complex", ";".join(chains),
        "-map", "0:v", "-map", "[aout]",
        "-c:v", "copy", "-c:a", "aac", "-b:a", "160k",
        "-shortest", "-movflags", "+faststart",
        str(out_path.resolve()),
    ]
    # errors="replace": ffmpeg's stderr is bytes, and the locale codec has
    # undefined slots that would raise while merely reporting a failure.
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    for wav, _ in pieces:
        discard(wav)
    if r.returncode != 0:
        print(f"      (dub mix failed: {(r.stderr or '')[-200:]})")
        return None
    return out_path
