"""The FFmpeg render for a gaming layout. One encode, no frames through Python.

Every layout is the same few steps (gaming/layout.py makes the Plan): each
element is cut from the source, made the size of its region, and laid on the
1080x1920 canvas in order.

- cover:   scaled to fill its region (the plan already cut it to that shape).
- contain: shown whole, as big as fits, at the region's anchor (top, bottom
           or centre), on a blurred copy of itself that fills the rest of that
           region only: the bars are the picture's own colours, never black.
- shift:   a camera picture moved down within its region so the streamer's
           head clears the platform's top bar (gaming/framing.py), with the
           same blur above it.
- circle:  a round alpha mask, for the Circle facecam layout.

video/cropper.py is not modified; this is its own graph, with the same
encoder, audio and captions handling as the standard renderer.
"""

import subprocess
from pathlib import Path

from core.binaries import ffmpeg
from gaming.layout import OUT_H, OUT_W, Element, Plan
from video.encoding import audio_filter_args, video_encoder_args


def _blur_fill(label: str, w: int, h: int) -> str:
    """The picture on [label] blown up to cover w x h, then downscaled hard,
    blurred and scaled back: a wash of its colours (the standard Letterbox
    layout's recipe, video/cropper.py _render_fit_blur)."""
    return (f"[{label}]scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h},"
            f"scale={max(2, w // 8)}:{max(2, h // 8)},gblur=sigma=12,scale={w}:{h}:flags=bilinear,setsar=1")


def _element(i: int, e: Element) -> tuple[str, str]:
    """(filter chain, output label) making element `i` the size of its region."""
    name = f"e{i}"
    sx, sy, sw, sh = e.src
    _dx, _dy, dw, dh = e.dest
    cut = f"[0:v]crop={sw}:{sh}:{sx}:{sy}"
    if e.fit == "blur":
        return f"{cut}[{name}s];{_blur_fill(name + 's', dw, dh)}[{name}]", name
    if e.fit == "contain" or e.shift:
        if e.fit == "contain":
            fg = f"scale={dw}:{dh}:force_original_aspect_ratio=decrease:force_divisible_by=2:flags=lanczos"
            y = {"top": "0", "bottom": "H-h"}.get(e.anchor, "(H-h)/2")
            at = f"(W-w)/2:{y}"
        else:
            fg = f"scale={dw}:{dh}:flags=lanczos"
            at = f"0:{e.shift}"
        chain = (f"{cut},split=2[{name}b][{name}f];"
                 f"{_blur_fill(name + 'b', dw, dh)}[{name}bg];"
                 f"[{name}f]{fg},setsar=1[{name}fg];"
                 f"[{name}bg][{name}fg]overlay={at}[{name}]")
    else:
        chain = f"{cut},scale={dw}:{dh}:flags=lanczos,setsar=1[{name}]"
    if e.shape == "circle":
        chain += (f";[{name}]format=yuva444p,geq=lum='lum(X,Y)':cb='cb(X,Y)':cr='cr(X,Y)':"
                  f"a='if(lte(hypot(X-W/2,Y-H/2),W/2),255,0)'[{name}c]")
        return chain, f"{name}c"
    return chain, name


def filter_graph(p: Plan, vf_extra: str = "", ass_name: str | None = None) -> str:
    """The -filter_complex for one plan; the output pad is [v]."""
    parts = [f"[0:v]crop=2:2:0:0,scale={OUT_W}:{OUT_H},setsar=1,"
             f"drawbox=x=0:y=0:w={OUT_W}:h={OUT_H}:color=black:t=fill[base]"]
    below = "base"
    for i, e in enumerate(p.elements):
        chain, label = _element(i, e)
        parts.append(chain)
        out = f"l{i}"
        parts.append(f"[{below}][{label}]overlay={e.dest[0]}:{e.dest[1]}[{out}]")
        below = out
    parts.append(f"[{below}]format=yuv420p[v]")
    graph = ";".join(parts)
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
