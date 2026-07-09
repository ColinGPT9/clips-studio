"""Chronological assembly: keep-ranges -> one 1920x1080 video.

A 3-hour VOD can produce hundreds of keep-ranges; a single FFmpeg filter
graph that size is fragile. Instead each range is cut (GPU-encoded with
identical parameters) and the pieces are joined losslessly with the concat
demuxer — same result, scales safely, and cancellation can land between
segments.
"""

import subprocess
from pathlib import Path
from typing import Callable

from core import cancel
from video.encoding import video_encoder_args

_FIT = (
    "scale=1920:1080:force_original_aspect_ratio=decrease:flags=lanczos,"
    "pad=1920:1080:(ow-iw)/2:(oh-ih)/2,setsar=1"
)


def assemble(
    source: Path,
    keep: list[tuple[float, float]],
    output_path: Path,
    video_id: str,
    on_progress: Callable[[int, int], None] | None = None,
) -> Path:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    segdir = output_path.parent / (output_path.stem + ".parts")
    segdir.mkdir(exist_ok=True)
    try:
        parts: list[Path] = []
        for i, (a, b) in enumerate(keep):
            cancel.check(video_id)
            seg = segdir / f"seg_{i:05d}.mp4"
            cmd = [
                "ffmpeg", "-y",
                "-ss", f"{a:.2f}", "-i", str(source.resolve()),
                "-t", f"{b - a:.2f}",
                "-vf", _FIT,
                *video_encoder_args(),
                "-c:a", "aac", "-b:a", "160k", "-ar", "48000", "-ac", "2",
                "-af", "aresample=async=1",
                "-fps_mode", "cfr",
                str(seg.resolve()),
            ]
            r = subprocess.run(cmd, capture_output=True, text=True)
            if r.returncode != 0:
                raise RuntimeError(f"segment {i} cut failed:\n{r.stderr[-800:]}")
            parts.append(seg)
            if on_progress:
                on_progress(i + 1, len(keep))

        # Identical codec parameters on every part -> lossless concat join.
        listfile = segdir / "concat.txt"
        listfile.write_text(
            "".join(f"file '{p.resolve().as_posix()}'\n" for p in parts), encoding="utf-8"
        )
        r = subprocess.run(
            [
                "ffmpeg", "-y", "-f", "concat", "-safe", "0",
                "-i", str(listfile.resolve()),
                "-c", "copy", "-movflags", "+faststart",
                str(output_path.resolve()),
            ],
            capture_output=True, text=True,
        )
        if r.returncode != 0:
            raise RuntimeError(f"concat join failed:\n{r.stderr[-800:]}")
        return output_path
    finally:
        for f in segdir.glob("*"):
            f.unlink(missing_ok=True)
        segdir.rmdir()
