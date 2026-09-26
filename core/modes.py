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
