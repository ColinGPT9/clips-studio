"""Watermark & branding: burn a logo and/or text into a rendered clip.

Two mechanisms, matching how the rest of the app burns overlays:
  * TEXT  -> an ASS event (like captions and the hook title). It folds into
    the clip's existing subtitle burn at zero extra cost, scales to the
    canvas, and supports outline/shadow via libass. Works on every render
    path that burns an ASS file.
  * IMAGE -> a logo overlaid via FFmpeg (transparent PNG, aspect preserved,
    never stretched). Runs as one extra pass ONLY when an image is set;
    text-only branding costs nothing.

Config schema (stored in a branding profile or a clip's render_opts.watermark):
  {
    "type": "image" | "text" | "both",
    "text": "@YourChannel", "font": "Arial", "font_size": 42,
    "color": "#FFFFFF", "opacity": 0.85,
    "position": "bottom_right",   # + top_left/top_right/bottom_left/center
    "padding": 0.04,              # fraction of the SHORTER edge
    "scale": 0.18,               # image width as a fraction of the frame width
    "rotation": 0, "shadow": true,
    "image_asset": "<hash>.png"  # filename under the branding assets dir
  }

Positions are computed against the OUTPUT frame (1080x1920 or 1920x1080), so
one config scales correctly for both vertical Shorts and horizontal video.
"""

import subprocess
from pathlib import Path

from video.encoding import video_encoder_args

# ASS numpad alignment per named position (7 8 9 / 4 5 6 / 1 2 3).
_ALIGN = {
    "top_left": 7, "top_right": 9,
    "bottom_left": 1, "bottom_right": 3,
    "center": 5,
}
# overlay x:y expressions per position, `P` = padding px, W/H = frame, w/h = logo.
_OVERLAY_XY = {
    "top_left": ("{p}", "{p}"),
    "top_right": ("W-w-{p}", "{p}"),
    "bottom_left": ("{p}", "H-h-{p}"),
    "bottom_right": ("W-w-{p}", "H-h-{p}"),
    "center": ("(W-w)/2", "(H-h)/2"),
}


def has_text(cfg: dict) -> bool:
    return cfg.get("type") in ("text", "both") and bool(str(cfg.get("text", "")).strip())


def has_image(cfg: dict, asset_dir: Path) -> bool:
    if cfg.get("type") not in ("image", "both"):
        return False
    name = cfg.get("image_asset")
    return bool(name) and (asset_dir / name).exists()


def _ass_color(hex_rgb: str) -> str:
    h = str(hex_rgb).lstrip("#")
    if len(h) != 6:
        return "&H00FFFFFF"
    r, g, b = h[0:2], h[2:4], h[4:6]
    return f"&H00{b}{g}{r}".upper()


def _text_style(cfg: dict, canvas: tuple[int, int]) -> str:
    """A watermark ASS style, scaled to the canvas height."""
    s = canvas[1] / 1920
    size = max(12, round(int(cfg.get("font_size", 42)) * s))
    color = _ass_color(cfg.get("color", "#FFFFFF"))
    align = _ALIGN.get(cfg.get("position", "bottom_right"), 3)
    # alpha in ASS is inverted (00 opaque, FF transparent) and prefixes colour.
    alpha = max(0, min(255, round((1 - float(cfg.get("opacity", 0.85))) * 255)))
    primary = f"&H{alpha:02X}{color[4:]}"  # splice alpha onto the BBGGRR colour
    outline = 2 if cfg.get("shadow", True) else 1
    shadow = 2 if cfg.get("shadow", True) else 0
    margin = round(float(cfg.get("padding", 0.04)) * min(canvas))
    font = str(cfg.get("font", "Arial"))
    angle = -float(cfg.get("rotation", 0) or 0)  # ASS angle is counter-clockwise
    return (
        f"Style: Watermark,{font},{size},{primary},{primary},&H00000000,&H7F000000,"
        f"0,0,0,0,100,100,0,{angle},1,{outline},{shadow},{align},"
        f"{margin},{margin},{margin},1"
    )


def _minimal_ass(style: str, event: str, canvas: tuple[int, int]) -> str:
    return (
        "[Script Info]\nScriptType: v4.00+\n"
        f"PlayResX: {canvas[0]}\nPlayResY: {canvas[1]}\nWrapStyle: 0\n\n"
        "[V4+ Styles]\nFormat: Name, Fontname, Fontsize, PrimaryColour, "
        "SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, "
        "StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, "
        "Shadow, Alignment, MarginL, MarginR, MarginV, Encoding\n"
        f"{style}\n\n[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, "
        "MarginR, MarginV, Effect, Text\n" + event + "\n"
    )


def ensure_text(ass_path: Path | None, target: Path, cfg: dict, canvas: tuple[int, int]) -> Path:
    """Merge a whole-clip watermark text line into the clip's ASS file (or
    write a standalone one). Returns the ASS file to burn. Mirrors the hook
    overlay so it shares the caption burn."""
    text = str(cfg["text"]).replace("\\", "").replace("{", "").replace("}", "").strip()
    style = _text_style(cfg, canvas)
    # Layer 2 so branding sits above captions; 9:59:59 = "whole clip".
    event = f"Dialogue: 2,0:00:00.00,9:59:59.99,Watermark,,0,0,0,,{text}"
    if ass_path is not None and ass_path.exists():
        content = ass_path.read_text(encoding="utf-8")
        content = content.replace("\n[Events]", f"\n{style}\n\n[Events]", 1)
        content = content.rstrip("\n") + "\n" + event + "\n"
        ass_path.write_text(content, encoding="utf-8")
        return ass_path
    target.write_text(_minimal_ass(style, event, canvas), encoding="utf-8")
    return target


def apply_image(video_path: Path, cfg: dict, canvas: tuple[int, int], asset_dir: Path) -> None:
    """Overlay the logo onto the video IN PLACE (transparent PNG, aspect kept,
    positioned + scaled + faded per the config). One extra encode; runs only
    when an image watermark is set."""
    logo = asset_dir / cfg["image_asset"]
    w, h = canvas
    pad = round(float(cfg.get("padding", 0.04)) * min(w, h))
    logo_w = max(16, round(float(cfg.get("scale", 0.18)) * w))
    opacity = max(0.0, min(1.0, float(cfg.get("opacity", 0.85))))
    xexpr, yexpr = _OVERLAY_XY.get(cfg.get("position", "bottom_right"), _OVERLAY_XY["bottom_right"])
    xexpr, yexpr = xexpr.format(p=pad), yexpr.format(p=pad)

    logo_chain = f"[1:v]scale={logo_w}:-1:flags=lanczos,format=rgba,colorchannelmixer=aa={opacity:.3f}"
    rot = float(cfg.get("rotation", 0) or 0)
    if abs(rot) > 0.1:
        logo_chain += f",rotate={rot}*PI/180:ow=rotw({rot}*PI/180):oh=roth({rot}*PI/180):c=none"
    graph = f"{logo_chain}[wm];[0:v][wm]overlay={xexpr}:{yexpr}"

    tmp = video_path.with_suffix(".wm.mp4")
    cmd = [
        "ffmpeg", "-y",
        "-i", str(video_path.resolve()),
        "-i", str(logo.resolve()),
        "-filter_complex", graph,
        "-map", "0:a?",   # keep audio untouched
        *video_encoder_args(),
        "-c:a", "copy",
        "-movflags", "+faststart",
        str(tmp.resolve()),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        tmp.unlink(missing_ok=True)
        raise RuntimeError(f"watermark overlay failed:\n{result.stderr[-1500:]}")
    tmp.replace(video_path)
