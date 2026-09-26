"""The FFmpeg render for a gaming layout. One encode, no frames through Python.

split: webcam band and game band, each 1080x960, stacked in the order the
       plan says. The webcam band is cover-cropped from its box (scaled up
       uniformly, overflow trimmed, never stretched); the game band is cut at
       exactly the band's aspect, so it only scales.
fill:  the game's 9:16 crop scaled to 1080x1920.

video/cropper.py is not modified; this is its own graph, with the same
encoder, audio and captions handling as the standard renderer.
"""

import subprocess
from pathlib import Path

from core.binaries import ffmpeg
from gaming.layout import BAND_H, OUT_H, OUT_W, Plan
from video.encoding import audio_filter_args, video_encoder_args


def filter_graph(p: Plan, vf_extra: str = "", ass_name: str | None = None) -> str:
    """The -filter_complex for one plan; the output pad is [v]."""
    gx, gy, gw, gh = p.game
    if p.kind == "fill":
        graph = f"[0:v]crop={gw}:{gh}:{gx}:{gy},scale={OUT_W}:{OUT_H}:flags=lanczos,setsar=1[v]"
    else:
        cx, cy, cw, ch = p.cam
        stack = "[game][cam]" if p.cam_position == "bottom" else "[cam][game]"
        graph = (
            f"[0:v]crop={cw}:{ch}:{cx}:{cy},"
            f"scale={OUT_W}:{BAND_H}:force_original_aspect_ratio=increase:flags=lanczos,"
            f"crop={OUT_W}:{BAND_H},setsar=1[cam];"
            f"[0:v]crop={gw}:{gh}:{gx}:{gy},scale={OUT_W}:{BAND_H}:flags=lanczos,setsar=1[game];"
            f"{stack}vstack=inputs=2[v]"
        )
    if vf_extra:
        graph += f";[v]{vf_extra}[v]"
    if ass_name:
        graph += f";[v]subtitles={ass_name}[v]"
    return graph


def render(clip_path: Path, output_path: Path, p: Plan, ass_path: Path | None = None,
           vf_extra: str = "", normalize: bool = True) -> Path:
    cmd = [
        ffmpeg(), "-y",
        "-i", str(clip_path.resolve()),
        "-filter_complex", filter_graph(p, vf_extra, ass_path.name if ass_path is not None else None),
        "-map", "[v]", "-map", "0:a:0?",
        *video_encoder_args(),
        "-c:a", "aac", "-b:a", "128k",
        *audio_filter_args(normalize),
        "-fps_mode", "cfr",
        "-movflags", "+faststart",
        "-shortest",
        str(output_path.resolve()),
    ]
    # cwd is the captions' folder so the subtitles filter gets a bare name,
    # the same way the standard renderer avoids Windows path escaping.
    result = subprocess.run(cmd, capture_output=True, text=True,
                            cwd=ass_path.parent if ass_path is not None else None)
    if result.returncode != 0:
        raise RuntimeError(f"gaming render failed:\n{result.stderr[-2000:]}")
    return output_path
