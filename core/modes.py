"""Vertical Live: a livestream already composed as 9:16 during the broadcast.

A YouTube vertical live, the vertical feed of a Twitch Dual Format or
Streamlabs Dual Output stream, or a downloaded Instagram/TikTok/YouTube live
MP4. The streamer already placed the gameplay, webcam and chat on a 9:16
canvas, so there is nothing to reframe: Vertical Live keeps that composition
and skips everything that decides where to point the frame (face tracking,
TalkNet, the layout decisions), while everything that decides WHICH moments
matter runs exactly as it does for any other video.

It is the user's explicit choice, a toggle of its own: a video being tall is
not enough (a vertical upload may still want the standard treatment), so
nothing here switches it on by itself. This module is the one place that says
what it means; web/lib/vertical.ts mirrors the numbers (a test keeps the two
in step).
"""

import subprocess
from pathlib import Path

# The standard Clips Kitty vertical output.
TARGET = (1080, 1920)

# Width / height counted as 9:16. 9:16 is 0.5625; this takes the usual
# vertical sizes (720x1280, 1080x1920, 1440x2560, 2160x3840) and a slightly
# cropped canvas, and refuses anything clearly another shape.
VERTICAL_MIN = 0.50
VERTICAL_MAX = 0.60

MISMATCH = ("This video is not a vertical 9:16 source. Vertical Live mode expects a "
            "vertically composed video.")


class NotVerticalError(Exception):
    """A Vertical Live job whose source isn't 9:16. Stopped before any work,
    never processed the wrong way; the queue offers standard processing."""

    def __init__(self, width: int, height: int):
        super().__init__(f"{MISMATCH} This one is {width}×{height}.")
        self.width = width
        self.height = height


def is_vertical_live(config_or_opts: dict | None) -> bool:
    """True for a job config (its clips section) or a clip's render options
    that carry the toggle."""
    if not config_or_opts:
        return False
    if config_or_opts.get("vertical_live"):
        return True
    clips = config_or_opts.get("clips")
    return bool(isinstance(clips, dict) and clips.get("vertical_live"))


def is_gaming(config_or_opts: dict | None) -> bool:
    """Gaming / Split-Screen (the gaming/ package, docs/GAMING.md): the
    streamer's webcam over the game in a split, or the game filling the
    screen. A toggle of its own like Vertical Live, off unless asked for, and
    never combined with it, Podcast or Longform."""
    if not config_or_opts:
        return False
    if config_or_opts.get("gaming"):
        return True
    clips = config_or_opts.get("clips")
    return bool(isinstance(clips, dict) and clips.get("gaming"))


def gaming_scoring(config_or_opts: dict | None) -> bool:
    """Score this job as a gaming stream (analysis/gaming.py): in-game moments
    and the reactions to them count, even when little is said. Gaming /
    Reaction always does; "Gaming stream" asks for it with any other layout,
    such as Vertical Live. Off unless asked for."""
    if not config_or_opts:
        return False
    if is_gaming(config_or_opts) or config_or_opts.get("gaming_scoring"):
        return True
    clips = config_or_opts.get("clips")
    return bool(isinstance(clips, dict) and clips.get("gaming_scoring"))


GAMING_BY = ("user", "creator", "video", "clip")


def _unit_box(value) -> list | None:
    """A normalized [x, y, w, h] inside the frame, or None."""
    if not isinstance(value, (list, tuple)) or len(value) != 4:
        return None
    try:
        x, y, w, h = (float(v) for v in value)
    except (TypeError, ValueError):
        return None
    if not (0 <= x < 1 and 0 <= y < 1 and 0.02 <= w <= 1 and 0.02 <= h <= 1):
        return None
    # Trimmed to the frame when it runs off it (past float noise: a box drawn
    # to the edge adds up to 1.0000000000000002).
    return [x, y, 1 - x if x + w > 1 + 1e-6 else w, 1 - y if y + h > 1 + 1e-6 else h]


def clean_gaming(settings: dict | None) -> dict:
    """A clip's Gaming / Reaction settings (gaming/run.py documents each key)
    reduced to valid values, for anything that arrives from outside: the
    editor, the API. Raises ValueError on a box that isn't one."""
    if not isinstance(settings, dict):
        raise ValueError("gaming settings must be an object")
    from gaming.framing import LAYOUTS  # the layout table only; nothing that renders

    out: dict = {"by": settings.get("by") if settings.get("by") in GAMING_BY else "user"}
    for key in ("cam", "game_box", "ui_box", "cam2"):
        if settings.get(key) is None:
            if key == "cam" and "cam" in settings:
                out["cam"] = None          # "no webcam", said on purpose
            continue
        box = _unit_box(settings[key])
        if box is None:
            raise ValueError(f"{key} must be [x, y, width, height] within the frame")
        out[key] = box
    if settings.get("preset") in LAYOUTS["presets"]:
        out["preset"] = settings["preset"]
    if settings.get("safe") in LAYOUTS["safe_zones"]:
        out["safe"] = settings["safe"]
    if settings.get("order") in ("cam_top", "game_top"):
        out["order"] = settings["order"]
    if isinstance(settings.get("divider"), (int, float)) and not isinstance(settings["divider"], bool):
        out["divider"] = min(max(float(settings["divider"]), 0.0), 1.0)
    if settings.get("cam_position") in ("top", "bottom"):
        out["cam_position"] = settings["cam_position"]
    if settings.get("game_align") in ("left", "center", "right"):
        out["game_align"] = settings["game_align"]
    if settings.get("game_fit") in ("fit", "fill"):
        out["game_fit"] = settings["game_fit"]
    if isinstance(settings.get("panels"), list):
        # The stream's solid panels, as found (gaming/panels.py): a few boxes.
        panels = [_unit_box(b) for b in settings["panels"][:8]]
        out["panels"] = [b for b in panels if b is not None]
    if isinstance(settings.get("places"), dict):
        # Where the facecams and the Game UI were put on the Short (fractions
        # of its width and height).
        places = {k: _unit_box(v) for k, v in settings["places"].items() if k in ("cam", "cam2", "ui")}
        out["places"] = {k: v for k, v in places.items() if v is not None}
    return out


def needs_framing(config: dict) -> bool:
    """Whether a job's clips need framing decided (face tracking, TalkNet,
    layout). Only framing: importance analysis runs either way."""
    return not is_vertical_live(config)


def orientation(width: int, height: int) -> str:
    """"vertical" (9:16 within tolerance), "horizontal" (16:9 within the same
    tolerance) or "other"."""
    if width <= 0 or height <= 0:
        return "other"
    ratio = width / height
    if VERTICAL_MIN <= ratio <= VERTICAL_MAX:
        return "vertical"
    if VERTICAL_MIN <= 1 / ratio <= VERTICAL_MAX:
        return "horizontal"
    return "other"


def probe_size(path: Path) -> tuple[int, int]:
    """The video's displayed width and height, from ffprobe. (0, 0) when it
    can't be read, which orientation() calls "other"."""
    from core.binaries import ffprobe

    out = subprocess.run(
        [ffprobe(), "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=width,height:stream_side_data=rotation",
         "-of", "default=nw=1", str(path)],
        capture_output=True, text=True,
    ).stdout
    values: dict[str, str] = {}
    for line in out.splitlines():
        key, _, value = line.partition("=")
        values.setdefault(key.strip(), value.strip())
    try:
        width, height = int(values.get("width", 0)), int(values.get("height", 0))
    except ValueError:
        return 0, 0
    # A phone recording can be stored landscape with a rotation flag and shown
    # upright; what matters is how it is displayed.
    try:
        rotated = abs(int(float(values.get("rotation", 0)))) % 180 == 90
    except ValueError:
        rotated = False
    return (height, width) if rotated else (width, height)


def fit_filter(width: int, height: int) -> str:
    """The FFmpeg filter that brings a vertical source to TARGET without
    cropping it: nothing at all when it already is TARGET (no pointless
    rescale), otherwise scale to fit and pad the rest."""
    if (width, height) == TARGET:
        return ""
    w, h = TARGET
    return (f"scale={w}:{h}:force_original_aspect_ratio=decrease:flags=lanczos,"
            f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2,setsar=1")
