"""FFmpeg clip extraction (accurate re-encoded cut, source aspect ratio).

Vertical 9:16 conversion is NOT done here — that's video/cropper.py,
driven by the tracker's crop path.
"""

import subprocess
from pathlib import Path

from core.models import ClipCandidate
from video.encoding import hwaccel_input_args, video_encoder_args


def cut_clip(
    source: Path,
    candidate: ClipCandidate,
    output_path: Path,
    ass_path: Path | None = None,
    vf_extra: str = "",
) -> Path:
    output_path.parent.mkdir(parents=True, exist_ok=True)

    # Absolute paths: cwd may be changed for the subtitles filter, which
    # would silently break relative input/output paths.
    cmd = [
        "ffmpeg", "-y",
        *hwaccel_input_args(),             # GPU decode when the codec allows
        "-ss", f"{candidate.start:.2f}",   # before -i: fast seek
        "-i", str(source.resolve()),
        "-t", f"{candidate.duration:.2f}",
        *video_encoder_args(),  # NVENC when available, libx264 otherwise
        "-c:a", "aac", "-b:a", "128k",
        "-movflags", "+faststart",
    ]
    vf_parts = [p for p in (vf_extra,) if p]
    if ass_path is not None:
        # Bare filename + cwd avoids Windows path escaping in filter args.
        vf_parts.append(f"subtitles={ass_path.name}")
    if vf_parts:
        cmd.extend(["-vf", ",".join(vf_parts)])
    cmd.append(str(output_path.resolve()))
    workdir = ass_path.parent if ass_path is not None else None
    result = subprocess.run(cmd, capture_output=True, text=True, cwd=workdir)
    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg cut failed:\n{result.stderr[-2000:]}")
    return output_path
