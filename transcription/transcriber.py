"""Transcription via faster-whisper, fully local.

Transcripts are cached as JSON per video id so re-runs (e.g. while tuning
the LLM prompt) skip the expensive transcription step.
"""

import ctypes
import json
import os
import re
import sys
from pathlib import Path

from core import cancel, progress
from core.binaries import whisper_model
from core.models import Segment

# faster-whisper's engine, CTranslate2, is built against CUDA 12 and loads
# cuBLAS 12 by name the first time it runs on the GPU, not when the model
# loads. It brings its own cuDNN, but not cuBLAS. PyTorch moved to CUDA 13
# (for the RTX 50-series), so its folder has cublas64_13.dll, not this: it
# comes from NVIDIA's nvidia-cublas-cu12 wheel (requirements.txt, bundled in
# the app) or a CUDA 12 toolkit. Measured (issue #111): without either, every
# job on an NVIDIA PC failed at "Transcribing"; a CUDA 12 toolkit on the
# developer's PATH had hidden it.
_CUBLAS12 = ("cublasLt64_12.dll", "cublas64_12.dll")   # the first is the second's dependency
_GPU_LIBRARY_ERROR = re.compile(r"cublas|cudnn|cuda", re.IGNORECASE)
_NO_CUBLAS12 = ("GPU transcription needs NVIDIA's cuBLAS 12 (cublas64_12.dll), which isn't on this PC. "
                "Reinstalling Clips Kitty brings it back (from source: pip install -r requirements.txt).")


def _cublas12_dirs() -> list[Path]:
    """Where cuBLAS 12 may be, the app's own copy first."""
    dirs: list[Path] = []
    if getattr(sys, "frozen", False):
        # Where the installed app's build puts it (clips-studio.spec), found
        # even if the namespace package doesn't import in the frozen app.
        dirs.append(Path(getattr(sys, "_MEIPASS", "")) / "nvidia" / "cublas" / "bin")
    try:
        import nvidia.cublas

        dirs += [Path(p) / "bin" for p in nvidia.cublas.__path__]
    except Exception:
        pass  # no cuBLAS wheel installed: the other places are still looked at
    if os.environ.get("CUDA_PATH"):
        dirs.append(Path(os.environ["CUDA_PATH"]) / "bin")
    dirs += [Path(p) for p in os.environ.get("PATH", "").split(os.pathsep) if p]
    try:
        import torch

        dirs.append(Path(torch.__file__).parent / "lib")   # a CUDA 12 PyTorch had them
    except Exception:
        pass  # no PyTorch here: one place fewer to look
    return dirs


def _cuda12_blas(dirs: list[Path] | None = None, load=None) -> Path | None:
    """The folder cuBLAS 12 was loaded from, or None when it isn't here.

    Loaded by full path, and its folder put first on PATH: CTranslate2's own
    later load asks for it by name, which finds a copy already loaded, and
    os.add_dll_directory alone does not reach that load. Not Windows: the
    system's loader finds the libraries as it always has."""
    if os.name != "nt":
        return Path(".")
    load = load or ctypes.WinDLL
    for folder in (_cublas12_dirs() if dirs is None else dirs):
        if not all((folder / name).is_file() for name in _CUBLAS12):
            continue
        try:
            for name in _CUBLAS12:
                load(str(folder / name))
        except OSError as e:
            print(f"  Whisper: couldn't load cuBLAS 12 from {folder} ({e})")
            continue
        os.environ["PATH"] = str(folder) + os.pathsep + os.environ.get("PATH", "")
        try:
            os.add_dll_directory(str(folder))
        except (OSError, AttributeError):
            pass  # the libraries are loaded and the folder is on PATH already
        return folder
    return None


def _on_gpu(model) -> bool:
    """Whether a loaded Whisper model runs on the GPU (CTranslate2 says)."""
    return getattr(getattr(model, "model", None), "device", "cpu") == "cuda"


def _load_model(model_size: str, device: str):
    # Imported lazily: loading faster-whisper/ctranslate2 takes seconds and
    # isn't needed when the transcript is cached.
    from faster_whisper import WhisperModel

    # Every name handed to WhisperModel goes through here first. A bare size
    # name means "fetch it from Hugging Face", which in an installed copy is a
    # silent multi-gigabyte download in the middle of someone's first video;
    # whisper_model() swaps in the bundled weights when they are present.
    def load(name: str, **kwargs):
        return WhisperModel(whisper_model(name), **kwargs)

    if device in ("auto", "cuda") and _cuda12_blas() is None:
        if device == "cuda":
            raise RuntimeError(_NO_CUBLAS12)   # the GPU was asked for by name
        print(f"  Whisper: {_NO_CUBLAS12} Transcribing on the CPU, which is slower.")
    elif device in ("auto", "cuda"):
        try:
            if model_size == "auto":
                # large-v3-turbo: large-v3 accuracy with a 4-layer decoder —
                # several times faster than medium AND more accurate. Falls
                # back to small, which is the other bundled size: falling back
                # to a size that isn't shipped would trade a load failure for
                # a silent download, which is the worse of the two.
                for name in ("large-v3-turbo", "small"):
                    try:
                        model = load(name, device="cuda", compute_type="float16")
                        print(f"  Whisper: GPU (CUDA) active, model '{name}'")
                        return model
                    except Exception as e:
                        turbo_err = e
                raise turbo_err
            model = load(model_size, device="cuda", compute_type="float16")
            print(f"  Whisper: GPU (CUDA) active, model '{model_size}'")
            return model
        except Exception as e:
            if device == "cuda":
                raise  # user explicitly demanded GPU — don't silently downgrade
            print(f"  Whisper: GPU unavailable ({str(e)[:90]}) — using CPU")
    if model_size == "auto":
        model_size = "small"  # on CPU, medium is 3-5x slower — speed wins there
    return load(model_size, device="cpu", compute_type="auto")


def _run(model, video_path: Path, language: str | None, hotwords: str | None = None):
    """(segments, info) for one pass of Whisper over the video. hotwords:
    names to listen for (sports.hotwords), else Whisper as always."""
    raw_segments, info = model.transcribe(
        str(video_path),
        # None = auto-detect; a forced code fixes bilingual streams where
        # the opening audio (e.g. English game sound) misleads detection.
        language=language,
        vad_filter=True,
        # Greedy decoding: ~2.4x faster than beam 5 with near-identical output
        # (verified on real footage) — the turbo model's accuracy headroom
        # more than covers the difference, and on 2-3h streams this saves
        # many minutes.
        beam_size=1,
        # Don't feed the previous window's text back in: on long streams with
        # music/noise this is what causes repeated-sentence hallucination
        # loops, and dropping it is a little faster too.
        condition_on_previous_text=False,
        word_timestamps=True,  # word-level timing powers the synced captions
        # Unlike an initial prompt, which the line above drops after the
        # first 30 s, hotwords go with every window.
        **({"hotwords": hotwords} if hotwords else {}),
    )

    segments = []
    last_emit = 0.0
    for seg in raw_segments:  # generator — transcription happens here
        # Transcribing a three-hour stream is a single call lasting many
        # minutes. Without this, pressing Cancel set a flag that nothing read
        # until the whole thing finished, so the app sat there saying
        # "cancelling" while it kept working. Whisper hands back a segment at
        # a time, which makes this the finest-grained place to stop.
        cancel.check_active()
        words = [
            {"start": round(w.start, 2), "end": round(w.end, 2), "word": w.word.strip()}
            for w in (seg.words or [])
        ]
        segments.append(
            Segment(
                start=round(seg.start, 2),
                end=round(seg.end, 2),
                text=seg.text.strip(),
                words=words or None,
            )
        )
        print(f"\r  Transcribed up to {seg.end:7.1f}s", end="", flush=True)
        # Throttled percent updates for the UI's progress bar.
        if info.duration and seg.end - last_emit >= max(5.0, info.duration * 0.02):
            progress.emit(stage="transcribe", fraction=min(1.0, seg.end / info.duration))
            last_emit = seg.end
    print()
    return segments, info


def transcribe(
    video_path: Path,
    video_id: str,
    transcript_dir: Path,
    model_size: str = "small",
    device: str = "auto",
    language: str | None = None,
    online: dict | None = None,
    hotwords: str | None = None,
) -> list[Segment]:
    """language: force a transcription language (ISO code like 'es');
    None = Whisper auto-detects. The detected/forced language is cached in
    the transcript JSON — read it back with detected_language().

    online: the `transcription` settings. Local Whisper unless its backend
    names a provider, in which case the audio goes to that provider on the
    user's own key (transcription/cloud.py) and comes back in the same shape.

    hotwords: names local Whisper is told to listen for, so it spells them as
    given when it hears them (a basketball video's players, sports.hotwords).
    None for every other job, which is transcribed as always."""
    transcript_dir.mkdir(parents=True, exist_ok=True)
    cache_path = transcript_dir / f"{video_id}.json"

    if cache_path.exists():
        print(f"  Using cached transcript: {cache_path}")
        data = json.loads(cache_path.read_text(encoding="utf-8"))
        segments = [Segment(**seg) for seg in data["segments"]]
        # Also repair transcripts cached before the loop guard existed, so a
        # reprocess fixes them without paying for transcription again. The
        # file itself is left as the raw record of what Whisper returned.
        _collapse_repetition_loops(segments)
        return segments

    if online and str(online.get("backend") or "local") != "local":
        from transcription import cloud

        segments = cloud.transcribe(video_path, video_id, transcript_dir, online, language=language)
        _collapse_repetition_loops(segments)
        return segments

    print(f"  Loading whisper model '{model_size}' (device={device})...")
    model = _load_model(model_size, device)

    try:
        segments, info = _run(model, video_path, language, hotwords)
    except RuntimeError as e:
        # The GPU's libraries are only loaded at the first encode, after the
        # model has loaded, so a GPU that loads can still fail here. The job
        # goes on, on the CPU, rather than failing (a cancel is not a
        # RuntimeError, and still stops it).
        if device == "cuda" or not _on_gpu(model) or not _GPU_LIBRARY_ERROR.search(str(e)):
            raise
        print(f"\n  Whisper: the GPU failed ({str(e)[:120]}); transcribing again on the CPU")
        segments, info = _run(_load_model(model_size, "cpu"), video_path, language, hotwords)

    looped = _collapse_repetition_loops(segments)
    if looped:
        print(f"  Collapsed {looped} Whisper repetition loop(s) (music/noise)")

    cache_path.write_text(
        json.dumps(
            {
                "video_id": video_id,
                "language": info.language,
                "segments": [vars(s) for s in segments],
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return segments


# A repetition loop is a long segment, a lot of words, and almost no
# vocabulary. All three must hold: sparse speech (a gym stream saying little
# over a minute) has a normal unique-word ratio, and a genuine chant is short
# because the pauses in it become segment boundaries.
_LOOP_MIN_SECONDS = 25.0
_LOOP_MIN_WORDS = 40
_LOOP_MAX_UNIQUE_RATIO = 0.15


def _collapse_repetition_loops(segments: list[Segment]) -> int:
    """Collapse Whisper repetition loops in place; returns how many were hit.

    On music, crowd noise, or long near-silence, Whisper can lock into
    emitting one phrase over and over inside a SINGLE segment. A real case
    from a music video: one 184-second "segment" of 165 words with 5 unique
    ones ("We are ready." x40). Nothing downstream can tell that from speech
    — it reached scoring, titles, and creator knowledge as if the creator had
    said it, and produced clips whose hook was the looped phrase.

    The fix keeps the first instance of each distinct sentence and trims the
    duplicated word timings, so captions read correctly and the rest of the
    span is treated as what it actually is: not speech.
    """
    collapsed = 0
    for seg in segments:
        words = seg.words or []
        duration = seg.end - seg.start
        if duration < _LOOP_MIN_SECONDS or len(words) < _LOOP_MIN_WORDS:
            continue
        tokens = [w["word"].strip(" .,!?").lower() for w in words if w.get("word")]
        if not tokens or len(set(tokens)) / len(tokens) > _LOOP_MAX_UNIQUE_RATIO:
            continue

        # Keep each distinct sentence once, in the order first said.
        seen: set[str] = set()
        kept: list[str] = []
        for sentence in re.split(r"(?<=[.!?])\s+", seg.text.strip()):
            key = re.sub(r"[^a-z0-9 ]", "", sentence.lower()).strip()
            if key and key not in seen:
                seen.add(key)
                kept.append(sentence.strip())
        # A loop usually gets cut off mid-phrase, leaving a stub ("… We're
        # ready. We") that isn't a sentence and reads like a typo.
        if len(kept) > 1 and len(kept[-1].split()) < 2:
            kept.pop()
        if not kept:
            continue

        seg.text = " ".join(kept)
        # Trim word timings to match, and end the segment at the last word we
        # kept — the rest of the span was music or noise, not speech.
        keep_n = min(len(words), max(1, len(seg.text.split())))
        seg.words = words[:keep_n]
        seg.end = round(max(seg.words[-1]["end"], seg.start + 0.5), 2)
        collapsed += 1
    return collapsed


def detected_language(video_id: str, transcript_dir: Path) -> str:
    """ISO language code from the cached transcript ('en' when unknown)."""
    try:
        data = json.loads((transcript_dir / f"{video_id}.json").read_text(encoding="utf-8"))
        return (data.get("language") or "en").lower()
    except Exception:
        return "en"
