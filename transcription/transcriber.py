"""Transcription via faster-whisper, fully local.

Transcripts are cached as JSON per video id so re-runs (e.g. while tuning
the LLM prompt) skip the expensive transcription step.
"""

import json
import os
from pathlib import Path

from core import progress
from core.models import Segment


def _add_gpu_dlls() -> None:
    """ctranslate2 (faster-whisper's engine) needs cuBLAS/cuDNN DLLs on
    Windows. The CUDA PyTorch wheels ship them — point the DLL search there
    so Whisper can run on the GPU without a separate CUDA toolkit install."""
    try:
        import torch

        lib = Path(torch.__file__).parent / "lib"
        if lib.exists():
            os.add_dll_directory(str(lib))
    except Exception:
        pass


def _load_model(model_size: str, device: str):
    # Imported lazily: loading faster-whisper/ctranslate2 takes seconds and
    # isn't needed when the transcript is cached.
    from faster_whisper import WhisperModel

    if device in ("auto", "cuda"):
        try:
            _add_gpu_dlls()
            if model_size == "auto":
                # large-v3-turbo: large-v3 accuracy with a 4-layer decoder —
                # several times faster than medium AND more accurate. Fall
                # back to medium if this faster-whisper can't load it.
                for name in ("large-v3-turbo", "medium"):
                    try:
                        model = WhisperModel(name, device="cuda", compute_type="float16")
                        print(f"  Whisper: GPU (CUDA) active, model '{name}'")
                        return model
                    except Exception as e:
                        turbo_err = e
                raise turbo_err
            model = WhisperModel(model_size, device="cuda", compute_type="float16")
            print(f"  Whisper: GPU (CUDA) active, model '{model_size}'")
            return model
        except Exception as e:
            if device == "cuda":
                raise  # user explicitly demanded GPU — don't silently downgrade
            print(f"  Whisper: GPU unavailable ({str(e)[:90]}) — using CPU")
    if model_size == "auto":
        model_size = "small"  # on CPU, medium is 3-5x slower — speed wins there
    return WhisperModel(model_size, device="cpu", compute_type="auto")


def transcribe(
    video_path: Path,
    video_id: str,
    transcript_dir: Path,
    model_size: str = "small",
    device: str = "auto",
    language: str | None = None,
) -> list[Segment]:
    """language: force a transcription language (ISO code like 'es');
    None = Whisper auto-detects. The detected/forced language is cached in
    the transcript JSON — read it back with detected_language()."""
    transcript_dir.mkdir(parents=True, exist_ok=True)
    cache_path = transcript_dir / f"{video_id}.json"

    if cache_path.exists():
        print(f"  Using cached transcript: {cache_path}")
        data = json.loads(cache_path.read_text(encoding="utf-8"))
        return [Segment(**seg) for seg in data["segments"]]

    print(f"  Loading whisper model '{model_size}' (device={device})...")
    model = _load_model(model_size, device)

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
    )

    segments = []
    last_emit = 0.0
    for seg in raw_segments:  # generator — transcription happens here
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


def detected_language(video_id: str, transcript_dir: Path) -> str:
    """ISO language code from the cached transcript ('en' when unknown)."""
    try:
        data = json.loads((transcript_dir / f"{video_id}.json").read_text(encoding="utf-8"))
        return (data.get("language") or "en").lower()
    except Exception:
        return "en"
