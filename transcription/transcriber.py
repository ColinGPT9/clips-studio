"""Transcription via faster-whisper, fully local.

Transcripts are cached as JSON per video id so re-runs (e.g. while tuning
the LLM prompt) skip the expensive transcription step.
"""

import json
import os
from pathlib import Path

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
            model = WhisperModel(model_size, device="cuda", compute_type="float16")
            print("  Whisper: GPU (CUDA) active")
            return model
        except Exception as e:
            if device == "cuda":
                raise  # user explicitly demanded GPU — don't silently downgrade
            print(f"  Whisper: GPU unavailable ({str(e)[:90]}) — using CPU")
    return WhisperModel(model_size, device="cpu", compute_type="auto")


def transcribe(
    video_path: Path,
    video_id: str,
    transcript_dir: Path,
    model_size: str = "small",
    device: str = "auto",
) -> list[Segment]:
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
        vad_filter=True,
        beam_size=5,
        word_timestamps=True,  # word-level timing powers the synced captions
    )

    segments = []
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
