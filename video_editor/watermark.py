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

# "moving" (TikTok-style anti-crop): the watermark rests at one side edge-
# centre, then SLIDES across to the other — never top/bottom (platform UI
# covers those). The slide catches the eye; the travel makes it hard to crop.
MOVE_DWELL = 3.4   # seconds resting at a side
MOVE_SLIDE = 0.6   # seconds to slide across
MOVE_PERIOD = MOVE_DWELL + MOVE_SLIDE


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


def _ass_t(seconds: float) -> str:
    h = int(seconds // 3600)
    m = int(seconds % 3600 // 60)
    s = seconds % 60
    return f"{h}:{m:02d}:{s:05.2f}"


def _text_events(text: str, cfg: dict, duration: float, canvas: tuple[int, int]) -> str:
    """One whole-clip event, or hops between the side edge-centres ('moving'),
    or a fixed dragged point ('custom' with x,y as frame fractions)."""
    pos = cfg.get("position")
    if pos == "custom":
        cx = round(max(0.0, min(1.0, float(cfg.get("x", 0.5)))) * canvas[0])
        cy = round(max(0.0, min(1.0, float(cfg.get("y", 0.5)))) * canvas[1])
        return f"Dialogue: 2,0:00:00.00,9:59:59.99,Watermark,,0,0,0,,{{\\an5\\pos({cx},{cy})}}{text}"
    if pos != "moving":
        # Layer 2 so branding sits above captions; 9:59:59 = "whole clip".
        return f"Dialogue: 2,0:00:00.00,9:59:59.99,Watermark,,0,0,0,,{text}"
    # Slide between the side edge-centres. Each event holds at its start side
    # for MOVE_DWELL then \move()s across in MOVE_SLIDE; sides alternate.
    dur = max(MOVE_PERIOD, duration or 60.0)
    cw, ch = canvas
    rx, lx, my = round(cw * 0.82), round(cw * 0.18), round(ch * 0.5)
    d_ms, s_ms = round(MOVE_DWELL * 1000), round(MOVE_PERIOD * 1000)
    events, t, i = [], 0.0, 0
    while t < dur:
        end = min(t + MOVE_PERIOD, dur)
        a, b = (rx, lx) if i % 2 == 0 else (lx, rx)  # from -> to
        tag = f"{{\\an5\\move({a},{my},{b},{my},{d_ms},{s_ms})}}"
        events.append(f"Dialogue: 2,{_ass_t(t)},{_ass_t(end)},Watermark,,0,0,0,,{tag}{text}")
        t, i = end, i + 1
    return "\n".join(events)


def ensure_text(
    ass_path: Path | None,
    target: Path,
    cfg: dict,
    canvas: tuple[int, int],
    duration: float = 0.0,
) -> Path:
    """Merge the watermark text into the clip's ASS file (or write a
    standalone one). Static by default; 'moving' position hops it around the
    edges (TikTok-style, anti-crop). Returns the ASS file to burn."""
    text = str(cfg["text"]).replace("\\", "").replace("{", "").replace("}", "").strip()
    style = _text_style(cfg, canvas)
    event = _text_events(text, cfg, duration, canvas)
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
    if cfg.get("position") == "custom":
        # Dragged point: x,y are frame fractions for the logo's CENTRE.
        fx = max(0.0, min(1.0, float(cfg.get("x", 0.5))))
        fy = max(0.0, min(1.0, float(cfg.get("y", 0.5))))
        xexpr, yexpr = f"W*{fx:.4f}-w/2", f"H*{fy:.4f}-h/2"
    elif cfg.get("position") == "moving":
        # Rest at a side, then slide across to the other (anti-crop). x is a
        # piecewise function of t over one full L<->R cycle.
        rx, lx = f"(W-w-{pad})", f"{pad}"
        d, s, p = MOVE_DWELL, MOVE_SLIDE, MOVE_PERIOD
        c = 2 * p
        ph = f"mod(t,{c:g})"
        rl = f"(({ph}-{d:g})/{s:g})"           # right->left progress 0..1
        lr = f"(({ph}-{p + d:g})/{s:g})"       # left->right progress 0..1
        xexpr = (
            f"if(lt({ph},{d:g}),{rx},"
            f"if(lt({ph},{p:g}),{rx}+({lx}-{rx})*{rl},"
            f"if(lt({ph},{p + d:g}),{lx},"
            f"{lx}+({rx}-{lx})*{lr})))"
        )
        yexpr = "(H-h)/2"
    else:
        xexpr, yexpr = _OVERLAY_XY.get(cfg.get("position", "bottom_right"), _OVERLAY_XY["bottom_right"])
        xexpr, yexpr = xexpr.format(p=pad), yexpr.format(p=pad)

    logo_chain = f"[1:v]scale={logo_w}:-1:flags=lanczos,format=rgba,colorchannelmixer=aa={opacity:.3f}"
    rot = float(cfg.get("rotation", 0) or 0)
    if abs(rot) > 0.1:
        logo_chain += f",rotate={rot}*PI/180:ow=rotw({rot}*PI/180):oh=roth({rot}*PI/180):c=none"
    # Commas inside the overlay x/y EXPRESSION (mod/if/…) must be escaped so
    # the filtergraph parser doesn't read them as filter separators.
    xe, ye = xexpr.replace(",", "\\,"), yexpr.replace(",", "\\,")
    graph = f"{logo_chain}[wm];[0:v][wm]overlay={xe}:{ye}"

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
