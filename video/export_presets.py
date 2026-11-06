"""Export presets: what an exported clip comes out as (#177, #203).

Exporting used to be a plain copy of the finished clip, and the preset
"Original" still is: the same file, byte for byte, and the default, so
nothing changes for anyone who never picks a preset. Any other preset is one
more encode of the finished clip (after its end card), run on the editor lane
(server/editor_lane.py):

- size: 240p to 2160p, counted on the SHORT side (720p is 720x1280 for 9:16,
  1280x720 for 16:9, 720x720 for 1:1, 720x900 for 4:5), or a custom width x
  height. Every size is even, as 4:2:0 video needs.
- shape: the clip's own, 9:16, 16:9, 1:1, 4:5 or a custom ratio, filled by
  cropping, a blurred copy of the picture behind it, or plain colour bars.
- quality: Smallest file, Balanced, High, or a custom CRF/CQ or bitrate.
- codec: H.264 or HEVC, on the CPU or the graphics card, and AV1 only on a
  graphics card that encodes it. video/encoding.py encoder_for() probes what
  this PC can do, without touching the encoder renders use.
- frame rate: the clip's own, 24, 25, 30, 50 or 60, never above the clip's.
- sound: AAC at 64-320 kbps, 48 kHz.
- an optional "keep under N MB" (5-4000): the bitrate is worked out from the
  clip's length, and the file is checked afterwards, with one retry at 0.9x
  the bitrate when it came out over.

Every value comes from an allow-list or is a clamped number (normalize()), and
the FFmpeg arguments are built only from the normalised values and the two
file paths, which are always absolute. Nothing a request sends reaches FFmpeg
as text. Standard library only, so it runs on CI's light install.
"""

from __future__ import annotations

import json
import math
import os
import re
import subprocess
import threading
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, replace
from pathlib import Path

from core.binaries import ffmpeg, ffprobe
from video import encoding

# "Keep under N MB" counts decimal megabytes. A file under N of these is also
# under N binary megabytes, whichever one a platform's limit means.
MB = 1_000_000

RESOLUTIONS = {"240p": 240, "360p": 360, "480p": 480, "540p": 540, "720p": 720,
               "1080p": 1080, "1440p": 1440, "2160p": 2160}
CODEC_NAMES = {"h264": "H.264", "hevc": "HEVC", "av1": "AV1"}  # for messages
ASPECTS = {"9:16": (9, 16), "16:9": (16, 9), "1:1": (1, 1), "4:5": (4, 5)}
FILLS = ("crop", "blur", "bars")
QUALITIES = ("smallest", "balanced", "high", "custom")
RATES = ("crf", "bitrate")
CODECS = tuple(encoding.EXPORT_ENCODERS)          # h264, hevc, av1
ENCODER_PREFS = ("auto", "cpu", "gpu")
FPS_CHOICES = (24, 25, 30, 50, 60)
AUDIO_CHOICES = (64, 96, 128, 160, 192, 256, 320)  # offered; any whole number in range is taken
BAR_COLOURS = {"black": "#000000", "white": "#FFFFFF"}
_HEX_COLOUR = re.compile(r"#[0-9A-Fa-f]{6}")
_NUMBER_TEXT = re.compile(r"-?\d{1,7}(\.\d{1,4})?")

# (lowest, highest) for every number a preset holds. Out-of-range numbers are
# clamped into these, not refused.
LIMITS: dict[str, tuple[float, float]] = {
    "width": (128, 4096),
    "height": (128, 4096),
    "aspect_w": (1, 32),
    "aspect_h": (1, 32),
    "crf": (1, 51),
    "bitrate_kbps": (100, 150_000),
    "audio_kbps": (64, 320),
    "max_mb": (5, 4000),
}
MAX_SIDE = 4096   # H.264 and the graphics-card encoders stop here

# A whole preset. Every key is always present once normalised; `kind` says
# whether the rest is used ("original" is the copy).
CUSTOM_DEFAULTS: dict = {
    "kind": "custom",
    "resolution": "same",   # same | 240p ... 2160p | custom
    "width": 1080,          # custom size only
    "height": 1920,
    "aspect": "same",       # same | 9:16 | 16:9 | 1:1 | 4:5 | custom
    "aspect_w": 9,          # custom shape only
    "aspect_h": 16,
    "fill": "crop",         # crop | blur | bars, when the shape changes
    "bar_color": "#000000",  # bars only
    "quality": "balanced",  # smallest | balanced | high | custom
    "rate": "crf",          # custom quality only: crf | bitrate
    "crf": 23,              # custom CRF (CQ on NVIDIA, ICQ on Intel)
    "bitrate_kbps": 6000,   # custom bitrate
    "codec": "h264",        # h264 | hevc | av1
    "encoder": "auto",      # auto | cpu | gpu
    "fps": "same",          # same | 24 | 25 | 30 | 50 | 60
    "audio_kbps": 128,
    "max_mb": None,         # None, or keep the file under this many MB
}
ORIGINAL: dict = {**CUSTOM_DEFAULTS, "kind": "original"}

# ---- what each quality level means -------------------------------------------------
#
# Each level is an x264 CRF (lower is better). Every other encoder runs at
# the value that looks about the same on its own scale: CRF_OFFSET is added.
LEVEL_CRF = {"smallest": 27, "balanced": 23, "high": 18}
CRF_OFFSET = {
    ("cpu", "h264"): 0,     # libx264, preset medium
    ("cpu", "hevc"): 5,     # libx265: CRF 28 looks like x264's 23
    ("nvenc", "h264"): 2,
    ("nvenc", "hevc"): 5,
    ("nvenc", "av1"): 13,   # an estimate: not measured, no AV1 card was at hand
    ("qsv", "h264"): 1,
    ("qsv", "hevc"): 4,
}
CQ_RANGE = {("nvenc", "av1"): (1, 63)}  # everything else takes 1-51
# AMD, Apple and Intel's AV1 have no constant-quality mode that works the same
# across driver generations (video/encoding.py says the same of AMD), so they
# run at a bitrate worked out from the size estimate below instead.

# ---- the size estimate -------------------------------------------------------------
#
# Step 0's baseline (docs/editor-suite/STATUS.md): the app's own 1080x1920
# 60 fps clips, x264 CRF 20 veryfast, came to 48-54 MB a minute, about
# 6,600 kbps of video. Other sizes and rates scale from that: the bitrate
# grows with pixels^0.75 and frame rate^0.7 (bigger pictures and higher rates
# cost less per pixel), and halves every 6 CRF. Checked on 6 real finished
# clips (30 and 60 fps, 16-43 s) at Smallest/720p, Balanced and High on the
# CPU: each came out at 0.85-1.10 times the estimate. Re-encoding a finished
# clip at x264's medium preset saves nothing on the veryfast figure, so the
# CPU's factor is 1. The other encoders' factors are estimates, not measured.
REF_KBPS = 6600.0
REF_PIXELS = 1080 * 1920
REF_FPS = 60.0
ENCODER_GAIN = {"cpu": 1.0, "nvenc": 1.0, "qsv": 1.05, "amf": 1.1, "videotoolbox": 1.1}  # bits vs x264 veryfast
CODEC_GAIN = {"h264": 1.0, "hevc": 0.65, "av1": 0.55}
# How far a CRF export of real footage strays from the estimate: with the
# clip in hand (its own bitrate says how busy the picture is), and without.
SPREAD_KNOWN = (0.8, 1.25)
SPREAD_UNKNOWN = (0.55, 1.6)

# ---- the size target ---------------------------------------------------------------
SIZE_HEADROOM = 0.95      # aim 5% under: the container and the encoder's swings
MIN_VIDEO_KBPS = 100      # the floor: below this there is nothing left to watch
RETRY_FACTOR = 0.9        # the one retry when a file came out over
# Bitrate caps on the graphics-card encoders, as multiples of the bitrate:
# a size target stays close to its average; a chosen bitrate may peak more.
CAPS = {"target": (1.2, 1.0), "bitrate": (1.5, 2.0)}  # (maxrate, bufsize)

# A clip WoopSocial would refuse is copied smaller at its own size if this many
# bits per pixel are left at the size that fits, and at 720p otherwise.
SAME_SIZE_MIN_BPP = 0.03


class PresetError(ValueError):
    """A preset that can't be used. The message says why, for a person."""


class ExportError(RuntimeError):
    """An export that could not be made. The message says why, for a person."""


class Cancelled(Exception):
    """The export was stopped (server/editor_lane.py)."""


# ---- normalising ---------------------------------------------------------------------


def _choice(key: str, value, allowed) -> object:
    if isinstance(value, bool) or value not in allowed:
        raise PresetError(f"{key} can't be {_shown(value)}. It is one of: "
                          + ", ".join(str(a) for a in allowed) + ".")
    return value


def _shown(value) -> str:
    text = repr(value)
    return text if len(text) <= 40 else text[:37] + "..."


def _number(key: str, value) -> float:
    """A finite number from JSON (or a plain numeric string), clamped into LIMITS."""
    if isinstance(value, str) and _NUMBER_TEXT.fullmatch(value.strip()):
        value = float(value)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise PresetError(f"{key} must be a number, not {_shown(value)}.")
    low, high = LIMITS[key]
    if isinstance(value, int):
        # Clamped while still an int: JSON allows integers too big for a
        # float, and float() or math.isfinite() on one raises OverflowError.
        return float(min(high, max(low, value)))
    if not math.isfinite(value):
        raise PresetError(f"{key} must be a number, not {_shown(value)}.")
    return min(high, max(low, value))


def _even(x: float) -> int:
    return max(2, int(round(x / 2.0)) * 2)


def normalize(raw) -> dict:
    """A preset with every key present and every value checked.

    None and "original" are the copy. Anything else must be an object whose
    keys are CUSTOM_DEFAULTS' and whose values are allowed. Choices that
    aren't allowed are refused (PresetError); numbers are clamped into
    LIMITS and sizes rounded to even. The result is safe to build FFmpeg
    arguments from.
    """
    if raw is None or raw == "original":
        return dict(ORIGINAL)
    if not isinstance(raw, dict):
        raise PresetError("A preset is an object of export settings, or \"original\".")
    unknown = sorted(str(k) for k in raw if k not in CUSTOM_DEFAULTS)
    if unknown:
        raise PresetError(f"{_shown(unknown[0])} is not an export setting.")
    given = {**CUSTOM_DEFAULTS, **raw}
    out: dict = {}
    out["kind"] = _choice("kind", given["kind"], ("original", "custom"))
    out["resolution"] = _choice("resolution", given["resolution"], ("same", *RESOLUTIONS, "custom"))
    out["width"] = _even(_number("width", given["width"]))
    out["height"] = _even(_number("height", given["height"]))
    out["aspect"] = _choice("aspect", given["aspect"], ("same", *ASPECTS, "custom"))
    out["aspect_w"] = int(round(_number("aspect_w", given["aspect_w"])))
    out["aspect_h"] = int(round(_number("aspect_h", given["aspect_h"])))
    out["fill"] = _choice("fill", given["fill"], FILLS)
    colour = given["bar_color"]
    if isinstance(colour, str) and colour.lower() in BAR_COLOURS:
        colour = BAR_COLOURS[colour.lower()]
    if not (isinstance(colour, str) and _HEX_COLOUR.fullmatch(colour)):
        raise PresetError(f"bar_color must be black, white or #RRGGBB, not {_shown(colour)}.")
    out["bar_color"] = colour.upper()
    out["quality"] = _choice("quality", given["quality"], QUALITIES)
    out["rate"] = _choice("rate", given["rate"], RATES)
    out["crf"] = int(round(_number("crf", given["crf"])))
    out["bitrate_kbps"] = int(round(_number("bitrate_kbps", given["bitrate_kbps"])))
    out["codec"] = _choice("codec", given["codec"], CODECS)
    out["encoder"] = _choice("encoder", given["encoder"], ENCODER_PREFS)
    fps = given["fps"]
    if isinstance(fps, str) and fps.strip() in {str(f) for f in FPS_CHOICES}:
        fps = int(fps.strip())
    elif isinstance(fps, float) and fps.is_integer():
        fps = int(fps)
    out["fps"] = _choice("fps", fps, ("same", *FPS_CHOICES))
    out["audio_kbps"] = int(round(_number("audio_kbps", given["audio_kbps"])))
    if given["max_mb"] is None or given["max_mb"] == "":
        out["max_mb"] = None
    else:
        out["max_mb"] = round(_number("max_mb", given["max_mb"]), 1)
    return out


def is_original(preset) -> bool:
    return normalize(preset)["kind"] == "original"


def choices() -> dict:
    """Every allowed value, for the Settings page."""
    return {
        "kind": ["original", "custom"],
        "resolution": ["same", *RESOLUTIONS, "custom"],
        "aspect": ["same", *ASPECTS, "custom"],
        "fill": list(FILLS),
        "bar_color": list(BAR_COLOURS),
        "quality": list(QUALITIES),
        "rate": list(RATES),
        "codec": list(CODECS),
        "encoder": list(ENCODER_PREFS),
        "fps": ["same", *FPS_CHOICES],
        "audio_kbps": list(AUDIO_CHOICES),
    }


# ---- the clip being exported -----------------------------------------------------------


@dataclass(frozen=True)
class Source:
    """What the finished clip is: read with probe()."""

    width: int
    height: int
    fps: float
    duration: float
    has_audio: bool = True
    video_kbps: float | None = None   # its own bitrate: how busy the picture is
    size: int | None = None


# What an estimate assumes with no clip in hand: the app's own 9:16 clip at 60 fps.
REFERENCE = Source(width=1080, height=1920, fps=60.0, duration=60.0)


def _rate(text) -> float:
    try:
        num, _, den = str(text).partition("/")
        value = float(num) / float(den or 1)
        return value if math.isfinite(value) and value > 0 else 0.0
    except (ValueError, ZeroDivisionError):
        return 0.0


def probe(path) -> Source:
    """Read a clip's size, frame rate, length, sound and bitrate with ffprobe."""
    try:
        r = subprocess.run(
            [ffprobe(), "-v", "error", "-show_entries",
             "format=duration,size,bit_rate:stream=codec_type,width,height,r_frame_rate,avg_frame_rate,bit_rate",
             "-of", "json", str(path)],
            capture_output=True, text=True, timeout=60)
        data = json.loads(r.stdout or "{}")
    except (OSError, subprocess.SubprocessError, ValueError) as e:
        raise ExportError(f"The clip's video can't be read ({e}).") from e
    streams = data.get("streams") or []
    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    fmt = data.get("format") or {}
    if not video:
        raise ExportError("The clip has no picture to export.")
    try:
        duration = float(fmt.get("duration") or 0)
    except (TypeError, ValueError):
        duration = 0.0
    fps = _rate(video.get("avg_frame_rate")) or _rate(video.get("r_frame_rate"))
    try:
        kbps = float(video.get("bit_rate") or 0) / 1000 or None
    except (TypeError, ValueError):
        kbps = None
    try:
        size = int(fmt.get("size") or 0) or None
    except (TypeError, ValueError):
        size = None
    width, height = int(video.get("width") or 0), int(video.get("height") or 0)
    if width <= 0 or height <= 0 or duration <= 0:
        raise ExportError("The clip's size or length can't be read.")
    return Source(width=width, height=height, fps=fps or 30.0, duration=duration,
                  has_audio=any(s.get("codec_type") == "audio" for s in streams),
                  video_kbps=kbps, size=size)


# ---- the picture -----------------------------------------------------------------------


def output_size(src_w: int, src_h: int, preset: dict) -> tuple[int, int]:
    """The exported picture's width and height. Always even, at most MAX_SIDE."""
    if preset["resolution"] == "custom":
        return preset["width"], preset["height"]
    if preset["aspect"] == "same":
        rw, rh = src_w, src_h
    elif preset["aspect"] == "custom":
        rw, rh = preset["aspect_w"], preset["aspect_h"]
    else:
        rw, rh = ASPECTS[preset["aspect"]]
    short = min(src_w, src_h) if preset["resolution"] == "same" else RESOLUTIONS[preset["resolution"]]
    w, h = (short, short * rh / rw) if rw <= rh else (short * rw / rh, short)
    longest = max(w, h)
    if longest > MAX_SIDE:
        w, h = w * MAX_SIDE / longest, h * MAX_SIDE / longest
    return _even(w), _even(h)


def _same_shape(src_w: int, src_h: int, out_w: int, out_h: int) -> bool:
    """The same aspect ratio, allowing for rounding to even sizes."""
    return abs(src_w / src_h - out_w / out_h) <= 0.005 * (out_w / out_h)


def content_scale(src_w: int, src_h: int, out_w: int, out_h: int, fill: str) -> float:
    """How much the clip's picture is enlarged (above 1) or shrunk: a crop
    covers the frame, bars and blur fit the picture inside it."""
    sx, sy = out_w / src_w, out_h / src_h
    if _same_shape(src_w, src_h, out_w, out_h):
        return sx
    return max(sx, sy) if fill == "crop" else min(sx, sy)


def is_upscale(src_w: int, src_h: int, out_w: int, out_h: int, fill: str) -> bool:
    """Whether the export is bigger than the clip, so it can only look softer
    than its size suggests (until native 4K masters, Phase 4)."""
    return content_scale(src_w, src_h, out_w, out_h, fill) > 1.01


def _ntsc(fps: float) -> bool:
    return any(abs(fps - n * 1000 / 1001) < 0.01 for n in (24, 30, 60))


def output_fps(src_fps: float, choice) -> tuple[float, str | None]:
    """The frame rate out, and the fps filter's value (None keeps the clip's).
    Never above the clip's own rate. A 29.97 or 59.94 clip asked for 30 or 60
    keeps the 1000/1001 rates, so nothing judders."""
    if choice == "same" or src_fps <= 0:
        return src_fps, None
    ntsc = _ntsc(src_fps) and choice in (24, 30, 60)
    wanted = choice * 1000 / 1001 if ntsc else float(choice)
    if wanted > src_fps * 1.001 or abs(wanted - src_fps) <= src_fps * 0.001:
        return src_fps, None
    return wanted, (f"{choice * 1000}/1001" if ntsc else str(choice))


def video_filter(src_w: int, src_h: int, out_w: int, out_h: int, fill: str, bar_color: str,
                 fps_filter: str | None) -> str | None:
    """The -vf graph that turns the clip into the export's picture, or None."""
    parts = [f"fps={fps_filter}"] if fps_filter else []
    if (out_w, out_h) != (src_w, src_h):
        cover = f"scale={out_w}:{out_h}:force_original_aspect_ratio=increase"
        fit = f"scale={out_w}:{out_h}:force_original_aspect_ratio=decrease:force_divisible_by=2:flags=lanczos"
        if _same_shape(src_w, src_h, out_w, out_h):
            parts.append(f"scale={out_w}:{out_h}:flags=lanczos")
        elif fill == "crop":
            parts.append(f"{cover}:flags=lanczos,crop={out_w}:{out_h}")
        elif fill == "bars":
            parts.append(f"{fit},pad={out_w}:{out_h}:(ow-iw)/2:(oh-ih)/2:color=0x{bar_color.lstrip('#')}")
        else:
            # The clip again behind itself, covering the frame and washed out
            # the way video/cropper.py's fit_blur does it: shrunk hard,
            # blurred and blown back up, so only its colours are left.
            wash = f"{cover},crop={out_w}:{out_h},scale={_even(out_w / 8)}:{_even(out_h / 8)}," \
                   f"gblur=sigma=12,scale={out_w}:{out_h}:flags=bilinear"
            parts.append(f"split=2[bg][fg];[bg]{wash}[wash];[fg]{fit}[pic];[wash][pic]overlay=(W-w)/2:(H-h)/2")
        parts.append("setsar=1")
    return ",".join(parts) or None


# ---- the bitrate -----------------------------------------------------------------------


def model_kbps(width: int, height: int, fps: float, crf: float, codec: str, family: str,
               busy: float = 1.0) -> float:
    """The video bitrate a CRF encode is expected to come to (see REF_KBPS).
    `busy` scales it for how much is going on in the picture."""
    kbps = REF_KBPS * (width * height / REF_PIXELS) ** 0.75 * (max(fps, 1.0) / REF_FPS) ** 0.7
    kbps *= 2 ** ((20 - crf) / 6)
    return kbps * ENCODER_GAIN.get(family, 1.0) * CODEC_GAIN.get(codec, 1.0) * busy


def busyness(source: Source) -> float | None:
    """How busy the clip's picture is, from its own bitrate: 1 for a clip at
    the baseline's rate. Its clips were all made at about CRF 20 (CPU) or
    CQ 23 (NVIDIA), so their bitrate says how hard the footage is to encode.
    None when it can't be told."""
    if not source.video_kbps:
        return None
    expected = REF_KBPS * (source.width * source.height / REF_PIXELS) ** 0.75 * \
        (max(source.fps, 1.0) / REF_FPS) ** 0.7
    return min(3.0, max(0.3, source.video_kbps / expected))


def target_video_kbps(max_bytes: float, duration: float, audio_kbps: float) -> int:
    """The video bitrate that keeps `duration` seconds under `max_bytes`:
    0.95 x 8 x bytes / seconds, less the sound, never below MIN_VIDEO_KBPS."""
    return max(MIN_VIDEO_KBPS, int(_target_kbps_raw(max_bytes, duration, audio_kbps)))


def _target_kbps_raw(max_bytes: float, duration: float, audio_kbps: float) -> float:
    return SIZE_HEADROOM * 8 * max_bytes / max(duration, 0.1) / 1000 - audio_kbps


def _level_crf(preset: dict) -> float:
    """The x264 CRF the preset's quality means."""
    return float(preset["crf"]) if preset["quality"] == "custom" else float(LEVEL_CRF[preset["quality"]])


# ---- the plan --------------------------------------------------------------------------


@dataclass(frozen=True)
class Plan:
    """Everything an export's encode is built from: normalised values only."""

    source: Source
    encoder: encoding.Encoder
    width: int
    height: int
    fps: float
    fps_filter: str | None
    vf: str | None
    mode: str                 # what was asked for: quality | bitrate | target
    rate: str                 # how it is encoded: crf | kbps
    crf: int | None           # CRF / CQ / ICQ, on the encoder's own scale
    video_kbps: int | None
    audio_kbps: int
    passes: int
    max_bytes: int | None
    upscale: bool
    target_reachable: bool | None
    busy: float | None


def make_plan(source: Source, preset: dict, encoder: encoding.Encoder, *, max_bytes: int | None = None) -> Plan:
    """How `preset` encodes `source` on `encoder`. `max_bytes` sets a size
    target in bytes in place of the preset's max_mb, for the app's own
    callers (smaller_copy), so it isn't held to max_mb's limits."""
    preset = normalize(preset)
    if preset["kind"] == "original":
        raise PresetError("Original is a copy of the clip, not an encode.")
    if max_bytes is not None:
        preset = {**preset, "max_mb": max(1.0, max_bytes) / MB}
    width, height = output_size(source.width, source.height, preset)
    fps, fps_filter = output_fps(source.fps, preset["fps"])
    vf = video_filter(source.width, source.height, width, height, preset["fill"], preset["bar_color"], fps_filter)
    busy = busyness(source)
    family, codec = encoder.family, encoder.codec
    crf_capable = (family, codec) in CRF_OFFSET
    audio = preset["audio_kbps"] if source.has_audio else 0
    level = _level_crf(preset)

    def quality_kbps(spread: float = 1.0) -> int:
        return int(model_kbps(width, height, fps, level, codec, family, busy or 1.0) * spread)

    mode = "quality"
    rate, crf, kbps, reachable = "crf", None, None, None
    if preset["max_mb"] is not None:
        mode = "target"
        max_bytes = int(max_bytes if max_bytes is not None else preset["max_mb"] * MB)
        raw = _target_kbps_raw(max_bytes, source.duration, audio)
        reachable = raw >= MIN_VIDEO_KBPS
        # Never more than the quality asked for needs: a 20-second clip
        # "under 100 MB" is not made 95 MB.
        if preset["quality"] == "custom" and preset["rate"] == "bitrate":
            ceiling = preset["bitrate_kbps"]
        else:
            ceiling = quality_kbps(SPREAD_KNOWN[1] if busy else SPREAD_UNKNOWN[1])
        rate, kbps = "kbps", max(MIN_VIDEO_KBPS, min(int(raw), ceiling))
    elif preset["quality"] == "custom" and preset["rate"] == "bitrate":
        mode, rate, kbps = "bitrate", "kbps", preset["bitrate_kbps"]
    elif crf_capable:
        low, high = CQ_RANGE.get((family, codec), (1, 51))
        value = preset["crf"] if preset["quality"] == "custom" else LEVEL_CRF[preset["quality"]] + CRF_OFFSET[
            (family, codec)]
        crf = int(min(high, max(low, value)))
    else:
        rate, kbps = "kbps", max(MIN_VIDEO_KBPS, quality_kbps())
    passes = 2 if rate == "kbps" and not encoder.hardware and mode in ("bitrate", "target") else 1
    return Plan(source=source, encoder=encoder, width=width, height=height, fps=fps, fps_filter=fps_filter,
                vf=vf, mode=mode, rate=rate, crf=crf, video_kbps=kbps, audio_kbps=audio, passes=passes,
                max_bytes=max_bytes,
                upscale=is_upscale(source.width, source.height, width, height, preset["fill"]),
                target_reachable=reachable, busy=busy)


def _video_args(plan: Plan, pass_no: int | None, passlog: str) -> list[str]:
    """The -c:v block, rate control included.

    Constant quality: -crf on the CPU, -cq on NVIDIA (VBR with no bitrate,
    as renders run it), -global_quality on Intel. A bitrate: 2-pass on the
    CPU (`pass_no`), and on a graphics card one pass of VBR held under
    -maxrate with a -bufsize window (CAPS); Apple's encoder takes no bufsize.
    """
    enc = plan.encoder
    family = enc.family
    args = list(encoding.EXPORT_BASE_ARGS[enc.name])
    if plan.rate == "crf":
        if family == "cpu":
            return [*args, "-crf", str(plan.crf)]
        if family == "nvenc":
            return [*args, "-rc", "vbr", "-cq", str(plan.crf), "-b:v", "0"]
        return [*args, "-global_quality", str(plan.crf)]          # qsv
    kbps = int(plan.video_kbps)
    if family == "cpu":
        if pass_no is not None and enc.name == "libx265":
            i = args.index("-x265-params")
            args[i + 1] = f"{args[i + 1]}:pass={pass_no}:stats={passlog}.x265"
        elif pass_no is not None:
            args += ["-pass", str(pass_no), "-passlogfile", passlog]
        return [*args, "-b:v", f"{kbps}k"]
    maxrate, bufsize = CAPS["target" if plan.mode == "target" else "bitrate"]
    if family == "nvenc":
        args += ["-rc", "vbr"]
    args += ["-b:v", f"{kbps}k", "-maxrate", f"{round(kbps * maxrate)}k"]
    if family != "videotoolbox":
        args += ["-bufsize", f"{round(kbps * bufsize)}k"]
    return args


PASSLOG = "export2pass"   # the 2-pass log's name, in the export's working folder


def commands(plan: Plan, src, dst, *, passlog: str = PASSLOG) -> list[list[str]]:
    """The FFmpeg command lines that make the export: one, or two for a
    CPU encode at a bitrate (2-pass). Run them in a working folder of their
    own: the 2-pass log is written there under `passlog`.

    The final pass writes an MP4 the way every finished file in the app is
    written (video/encoding.py final_movflags: +faststart and the first frame
    at 0). Progress is reported on stdout (-progress pipe:1)."""
    src, dst = Path(src), Path(dst)
    if not (src.is_absolute() and dst.is_absolute()):
        # A relative path could read as an FFmpeg protocol ("tcp:..."); an
        # absolute one is always a file.
        raise ExportError("Export paths must be absolute.")
    head = [ffmpeg(), "-y", "-v", "error", "-nostdin", "-progress", "pipe:1", "-nostats", "-i", str(src)]
    picture = (["-vf", plan.vf] if plan.vf else []) + ["-pix_fmt", "yuv420p"]
    tag = ["-tag:v", "hvc1"] if plan.encoder.codec == "hevc" else []
    sound = (["-c:a", "aac", "-b:a", f"{plan.audio_kbps}k", "-ar", "48000"] if plan.source.has_audio
             else ["-an"])
    out = [*encoding.final_movflags(), "-f", "mp4", str(dst)]
    if plan.passes == 1:
        return [[*head, "-map", "0:v:0", "-map", "0:a:0?", *picture, *_video_args(plan, None, passlog),
                 *tag, *sound, *out]]
    first = [*head, "-map", "0:v:0", *picture, *_video_args(plan, 1, passlog), "-an", "-f", "null", "-"]
    second = [*head, "-map", "0:v:0", "-map", "0:a:0?", *picture, *_video_args(plan, 2, passlog),
              *tag, *sound, *out]
    return [first, second]


# ---- the estimate ----------------------------------------------------------------------


def estimate(duration: float, preset, *, source: Source | None = None,
             encoder: encoding.Encoder | None = None) -> dict:
    """What an export with `preset` comes to for `duration` seconds.

    A range for the quality levels and a custom CRF, because how much a CRF
    encode needs depends on the footage; a close figure for a bitrate or a
    size target. Without a clip (`source`), it assumes the app's own 1080x1920
    60 fps clip (REFERENCE). Without an encoder, the CPU's."""
    preset = normalize(preset)
    known = source is not None
    source = replace(source or REFERENCE, duration=max(0.1, float(duration)))
    if preset["kind"] == "original":
        if known and source.size:
            size = int(source.size)
            return _sized("copy", size, size, size, duration, source.width, source.height, source.fps,
                          preset, None, upscale=False)
        # The clip as the app makes it: CRF 20 (ENCODER_GAIN's 1.0 is x264
        # veryfast) and 128 kbps of AAC.
        typical = (model_kbps(source.width, source.height, source.fps, 20, "h264", "nvenc") + 128) \
            * 125 * source.duration
        return _sized("range", typical * SPREAD_UNKNOWN[0], typical, typical * SPREAD_UNKNOWN[1], duration,
                      source.width, source.height, source.fps, preset, None, upscale=False)
    plan = make_plan(source, preset, encoder or assumed_encoder(preset["codec"]))
    encoder = plan.encoder
    audio_bytes = plan.audio_kbps * 125 * source.duration
    if plan.rate == "kbps":
        close = (plan.video_kbps * 125 * source.duration + audio_bytes) * 1.01
        low, high = close * 0.93, close * 1.05
        if plan.max_bytes and plan.target_reachable:
            high = min(high, plan.max_bytes)
            close = min(close, plan.max_bytes)
        kind = "close"
    else:
        crf = float(plan.crf) - CRF_OFFSET.get((encoder.family, encoder.codec), 0)
        video = model_kbps(plan.width, plan.height, plan.fps, crf, encoder.codec, encoder.family,
                           plan.busy or 1.0) * 125 * source.duration
        spread = SPREAD_KNOWN if plan.busy else SPREAD_UNKNOWN
        close = video + audio_bytes
        low, high = video * spread[0] + audio_bytes, video * spread[1] + audio_bytes
        kind = "range"
    out = _sized(kind, low, close, high, duration, plan.width, plan.height, plan.fps, preset, encoder,
                 upscale=plan.upscale)
    out.update({"mode": plan.mode, "video_kbps": plan.video_kbps, "audio_kbps": plan.audio_kbps,
                "crf": plan.crf, "passes": plan.passes,
                "target_bytes": plan.max_bytes, "target_reachable": plan.target_reachable})
    return out


def _sized(kind, low, typical, high, duration, width, height, fps, preset, encoder, *, upscale) -> dict:
    per_minute = 60.0 / max(0.1, float(duration))
    return {
        "kind": kind,
        "bytes": int(typical), "low": int(low), "high": int(high),
        "per_minute": {"bytes": int(typical * per_minute), "low": int(low * per_minute),
                       "high": int(high * per_minute)},
        "seconds": round(float(duration), 3),
        "width": width, "height": height, "fps": round(fps, 3),
        "codec": "copy" if preset["kind"] == "original" else preset["codec"],
        "encoder": encoder.name if encoder else None,
        "hardware": bool(encoder and encoder.hardware),
        "upscale": upscale,
        "mode": "copy" if preset["kind"] == "original" else None,
        "video_kbps": None, "audio_kbps": None, "crf": None, "passes": 0,
        "target_bytes": None, "target_reachable": None,
    }


# ---- running FFmpeg --------------------------------------------------------------------

# A runner takes a command line and runs it to the end:
#   run(argv, cwd=..., duration=..., on_progress=...) -> (returncode, the end of stderr)
Runner = Callable[..., tuple[int, str]]


def progress_seconds(line: str) -> float | None:
    """How far an FFmpeg -progress line says it is, in seconds, or None."""
    key, _, value = line.strip().partition("=")
    try:
        if key in ("out_time_us", "out_time_ms"):   # both are microseconds, in every FFmpeg
            return max(0.0, int(value) / 1_000_000)
        if key == "out_time":
            h, m, s = value.split(":")
            return max(0.0, int(h) * 3600 + int(m) * 60 + float(s))
    except ValueError:
        return None
    return None


def low_priority() -> dict:
    """Popen arguments that start a process below normal priority on Windows."""
    flag = getattr(subprocess, "BELOW_NORMAL_PRIORITY_CLASS", 0)
    return {"creationflags": flag} if os.name == "nt" and flag else {}


def run_ffmpeg(argv: list[str], *, cwd=None, duration: float = 0.0,
               on_progress: Callable[[float], None] | None = None,
               on_start: Callable[[subprocess.Popen], None] | None = None,
               popen=None) -> tuple[int, str]:
    """Run one FFmpeg command at below-normal priority, reporting progress
    from its -progress output as a fraction of `duration`. `on_start` gets
    the process, so a caller can kill it to cancel. Returns the exit code and
    the last lines of stderr."""
    popen = popen or subprocess.Popen
    proc = popen(argv, cwd=None if cwd is None else str(cwd), stdin=subprocess.DEVNULL,
                 stdout=subprocess.PIPE, stderr=subprocess.PIPE, **low_priority())
    if os.name != "nt":
        try:
            os.setpriority(os.PRIO_PROCESS, proc.pid, 10)
        except (AttributeError, OSError):
            pass
    if on_start is not None:
        on_start(proc)
    tail: deque[str] = deque(maxlen=30)

    def drain() -> None:
        for raw in iter(proc.stderr.readline, b""):
            tail.append(raw.decode("utf-8", "replace").rstrip())

    reader = threading.Thread(target=drain, daemon=True, name="ffmpeg-stderr")
    reader.start()
    try:
        for raw in iter(proc.stdout.readline, b""):
            seconds = progress_seconds(raw.decode("utf-8", "replace"))
            if seconds is not None and on_progress is not None and duration > 0:
                on_progress(min(1.0, seconds / duration))
    finally:
        code = proc.wait()
        reader.join(timeout=5)
        for stream in (proc.stdout, proc.stderr):
            try:
                stream.close()
            except Exception:
                pass
    return code, "\n".join(line for line in tail if line)


# ---- the export --------------------------------------------------------------------------


def assumed_encoder(codec: str) -> encoding.Encoder:
    """The encoder an estimate assumes when none was probed: the CPU's, or
    NVIDIA's for AV1, which has none on the CPU. Probes nothing."""
    families = encoding.EXPORT_ENCODERS[codec]
    family = "cpu" if "cpu" in families else encoding.HARDWARE_FAMILIES[0]
    return encoding.Encoder(codec, family, families[family])


def pick_encoder(preset: dict) -> encoding.Encoder:
    """The encoder for `preset`: its codec on its choice of CPU or GPU, or
    the CPU when no graphics card can (H.264 and HEVC). AV1 needs one."""
    chosen = encoding.encoder_for(preset["codec"], preset["encoder"])
    if chosen is None and preset["codec"] != "av1":
        chosen = encoding.encoder_for(preset["codec"], "cpu")
    if chosen is None:
        raise ExportError("This PC can't make AV1 videos: that needs a graphics card that encodes AV1."
                          if preset["codec"] == "av1" else
                          f"This PC can't encode {CODEC_NAMES[preset['codec']]} videos.")
    return chosen


def encode(src, dst, preset, *, workdir, run: Runner | None = None, encoder: encoding.Encoder | None = None,
           source: Source | None = None, max_bytes: int | None = None,
           on_progress: Callable[[float, dict], None] | None = None) -> dict:
    """Make the export `dst` from the finished clip `src`.

    `run` runs each FFmpeg command (run_ffmpeg by default; the editor lane
    passes one it can cancel, which raises Cancelled). A graphics-card encode
    that fails is made again on the CPU (H.264 and HEVC), the way a render is
    (video/cutter.py). With a size target, the file is checked afterwards
    and, if it came out over, made once more at 0.9x the bitrate; a 2-pass
    encode reuses its first pass for that. Returns what was made: its size,
    picture, codec and encoder, and whether it is over a target anyway."""
    preset = normalize(preset)
    src, dst, workdir = Path(src).resolve(), Path(dst).resolve(), Path(workdir).resolve()
    workdir.mkdir(parents=True, exist_ok=True)
    run = run or run_ffmpeg
    source = source or probe(src)
    plan = make_plan(source, preset, encoder or pick_encoder(preset), max_bytes=max_bytes)
    fell_back = retried = False
    done = [0.0]

    def report(fraction: float, step: dict) -> None:
        done[0] = max(done[0], min(0.999, fraction))
        if on_progress is not None:
            on_progress(done[0], step)

    def attempt(p: Plan, *, from_pass: int = 1, span: tuple[float, float] = (0.0, 1.0)) -> None:
        every = commands(p, src, dst, passlog=PASSLOG)
        todo = every[from_pass - 1:]
        lo, hi = span
        for i, argv in enumerate(todo):
            step = {"pass": from_pass + i, "passes": len(every), "encoder": p.encoder.name}

            def progress(f: float, i=i, step=step) -> None:
                report(lo + (hi - lo) * (i + f) / len(todo), step)

            code, err = run(argv, cwd=workdir, duration=source.duration, on_progress=progress)
            if code != 0:
                lines = err.strip().splitlines()
                raise ExportError(lines[-1] if lines else f"FFmpeg stopped (exit code {code}).")

    try:
        attempt(plan)
    except ExportError:
        if not plan.encoder.hardware:
            raise
        cpu = encoding.encoder_for(preset["codec"], "cpu")
        if cpu is None:
            raise
        print(f"      Export: {plan.encoder.name} failed; making it on the CPU instead")
        plan, fell_back = make_plan(source, preset, cpu, max_bytes=max_bytes), True
        attempt(plan, span=(done[0], 1.0))
    size = dst.stat().st_size if dst.exists() else 0
    if size <= 0:
        raise ExportError("FFmpeg finished without writing the export.")
    if plan.max_bytes and size > plan.max_bytes and plan.video_kbps:
        lower = max(MIN_VIDEO_KBPS, int(plan.video_kbps * RETRY_FACTOR))
        if lower < plan.video_kbps:
            print(f"      Export: {size / MB:.1f} MB is over {plan.max_bytes / MB:g} MB; once more at {lower} kbps")
            plan, retried = replace(plan, video_kbps=lower), True
            attempt(plan, from_pass=plan.passes, span=(done[0], 1.0))
            size = dst.stat().st_size
    report(1.0, {"pass": plan.passes, "passes": plan.passes, "encoder": plan.encoder.name})
    return {
        "bytes": size,
        "width": plan.width, "height": plan.height, "fps": round(plan.fps, 3),
        "codec": plan.encoder.codec, "encoder": plan.encoder.name, "hardware": plan.encoder.hardware,
        "duration": round(source.duration, 3),
        "mode": plan.mode, "video_kbps": plan.video_kbps, "crf": plan.crf, "audio_kbps": plan.audio_kbps,
        "upscale": plan.upscale,
        "target_bytes": plan.max_bytes,
        "over_target": bool(plan.max_bytes and size > plan.max_bytes),
        "retried": retried, "fell_back_to_cpu": fell_back,
    }


# ---- a smaller copy for an upload limit (publish/woopsocial.py) ---------------------------


def smaller_copy_preset(source: Source, max_bytes: int, audio_kbps: int = 128) -> dict:
    """The preset that copies `source` under `max_bytes` (pass the bytes to
    encode() as `max_bytes`): H.264 at the clip's own size when that leaves
    enough bits per pixel (SAME_SIZE_MIN_BPP), and 720p otherwise. The frame
    rate and shape stay the clip's."""
    kbps = target_video_kbps(max_bytes, source.duration, audio_kbps)
    bpp = kbps * 1000 / (source.width * source.height * max(source.fps, 1.0))
    keep = bpp >= SAME_SIZE_MIN_BPP or min(source.width, source.height) <= 720
    return normalize({"kind": "custom", "resolution": "same" if keep else "720p", "aspect": "same",
                      "quality": "high", "codec": "h264", "encoder": "auto", "fps": "same",
                      "audio_kbps": audio_kbps})


def smaller_copy(src, dst, max_bytes: int, *, workdir, run: Runner | None = None) -> dict:
    """Write a copy of `src` to `dst` that is under `max_bytes` if it can be
    (smaller_copy_preset). Returns encode()'s result."""
    source = probe(src)
    return encode(src, dst, smaller_copy_preset(source, max_bytes), workdir=workdir, run=run, source=source,
                  max_bytes=max_bytes)
