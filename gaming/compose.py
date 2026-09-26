"""The FFmpeg render for a gaming layout. One encode, no frames through Python.

split: webcam band and game band, each 1080x960, stacked in the order the
       plan says. The webcam band is cover-cropped from its box (scaled up
       uniformly, overflow trimmed, never stretched).
fill:  the game alone, 1080x1920.

The game, in either, is shown one of two ways (Plan.game_fit):
- fit:  whole, letterboxed, on a blurred copy of itself (the standard
        Letterbox layout's blur, video/cropper.py _render_fit_blur), so the
        bars are the game's own colours, never black;
- fill: the crop scaled to fill (the plan cuts it at the band's aspect).

video/cropper.py is not modified; this is its own graph, with the same
encoder, audio and captions handling as the standard renderer.
"""

import subprocess
from pathlib import Path

from core.binaries import ffmpeg
from gaming.layout import BAND_H, OUT_H, OUT_W, Plan
from video.encoding import audio_filter_args, video_encoder_args


def _fit_on_blur(region: tuple, w: int, h: int, name: str) -> str:
    """[0:v] cropped to `region` and shown whole in w x h, centred on a
    blurred, zoomed-in copy of itself. Output pad [name]."""
    gx, gy, gw, gh = region
    return (
        f"[0:v]crop={gw}:{gh}:{gx}:{gy},split=2[{name}_b][{name}_f];"
        # Background: the same picture blown up to COVER the space, then
        # downscaled hard, blurred and scaled back: a wash of its colours.
        f"[{name}_b]scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h},"
        f"scale={w // 8}:{h // 8},gblur=sigma=12,scale={w}:{h}:flags=bilinear,setsar=1[{name}_bg];"
        # Foreground: the whole picture, as big as fits, never stretched.
        f"[{name}_f]scale={w}:{h}:force_original_aspect_ratio=decrease:force_divisible_by=2:flags=lanczos,"
        f"setsar=1[{name}_fg];"
        f"[{name}_bg][{name}_fg]overlay=(W-w)/2:(H-h)/2[{name}]"
    )


def _zoom(region: tuple, w: int, h: int, name: str) -> str:
    gx, gy, gw, gh = region
    return f"[0:v]crop={gw}:{gh}:{gx}:{gy},scale={w}:{h}:flags=lanczos,setsar=1[{name}]"


def filter_graph(p: Plan, vf_extra: str = "", ass_name: str | None = None) -> str:
    """The -filter_complex for one plan; the output pad is [v]."""
    game = _fit_on_blur if p.game_fit == "fit" else _zoom
    if p.kind == "fill":
        graph = game(p.game, OUT_W, OUT_H, "v")
    else:
        cx, cy, cw, ch = p.cam
        stack = "[game][cam]" if p.cam_position == "bottom" else "[cam][game]"
        graph = (
            f"[0:v]crop={cw}:{ch}:{cx}:{cy},"
            f"scale={OUT_W}:{BAND_H}:force_original_aspect_ratio=increase:flags=lanczos,"
            f"crop={OUT_W}:{BAND_H},setsar=1[cam];"
            f"{game(p.game, OUT_W, BAND_H, 'game')};"
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
