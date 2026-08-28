"""FFmpeg clip extraction (accurate re-encoded cut, source aspect ratio).

Vertical 9:16 conversion is NOT done here — that's video/cropper.py,
driven by the tracker's crop path.
"""

import subprocess
from pathlib import Path

from core.binaries import ffmpeg
from core.models import ClipCandidate
from video.encoding import (
    CPU_ARGS,
    audio_filter_args,
    hwaccel_input_args,
    using_hardware_encoder,
    video_encoder_args,
)


def cut_clip(
    source: Path,
    candidate: ClipCandidate,
    output_path: Path,
    ass_path: Path | None = None,
    vf_extra: str = "",
    normalize: bool = False,
) -> Path:
    """normalize: loudness-normalise the audio. Only for a FINAL clip — this
    also cuts the staging file the tracked path crops from, and normalising
    that would just be undone (and doubled) by the real encode."""
    output_path.parent.mkdir(parents=True, exist_ok=True)

    # Absolute paths: cwd may be changed for the subtitles filter, which
    # would silently break relative input/output paths.
    cmd = [
        ffmpeg(), "-y",
        *hwaccel_input_args(),             # GPU decode when the codec allows
        "-ss", f"{candidate.start:.2f}",   # before -i: fast seek
        "-i", str(source.resolve()),
        "-t", f"{candidate.duration:.2f}",
        # Force CONSTANT frame rate. Twitch/Kick VODs are often variable frame
        # rate; the tracked crop rewrites video at a constant fps in OpenCV, so
        # without this the video duration drifts from the audio -> A/V desync.
        "-vsync", "cfr",
        *audio_filter_args(normalize),     # sync, and loudness for a final clip
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

    # A GPU encoder that passed its start-up probe can still refuse the actual
    # footage — h264_nvenc rejects 10-bit input, and the probe only ever sees a
    # synthetic 8-bit black frame. That failure is per-source, so it hits every
    # clip identically and a whole job finishes with nothing rendered.
    #
    # Retrying once on the CPU turns that into a slower job instead of an empty
    # one, whatever the cause: pixel format, driver, or a card the probe
    # flattered. Same shape as the CPU fallbacks in core/gpu.py and
    # transcription/transcriber.py.
    if result.returncode != 0 and using_hardware_encoder():
        print("      GPU encoder refused this clip — retrying on the CPU")
        retry = _swap_encoder(cmd, video_encoder_args(), CPU_ARGS)
        result = subprocess.run(retry, capture_output=True, text=True, cwd=workdir)

    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg cut failed:\n{result.stderr[-2000:]}")
    return output_path


def _swap_encoder(cmd: list[str], old: list[str], new: list[str]) -> list[str]:
    """Replace the encoder argument block in an already-built command.

    Rebuilding the whole command would mean duplicating every filter, path and
    subtitle decision made above, which is how the two paths drift apart.
    """
    i = cmd.index(old[0])
    return cmd[:i] + list(new) + cmd[i + len(old):]
