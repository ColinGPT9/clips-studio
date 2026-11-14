"""What a media file is (ffprobe), and whether the editor's <video> element
can play it as it is or needs a proxy.

What Chromium plays (the editor runs in Electron 31, Chromium 126, which
Electron builds with its proprietary codecs: H.264, AAC, MP3). The list is
from Chromium's own format support (media/base/supported_types.cc and
mime_util_internal.cc, as of Chromium 126) and the plan's rule, read from
the sources, not measured in the app; step 8 checks it by playing each kind.

| Container (ffprobe's format) | Video | Sound |
|---|---|---|
| MP4 / MOV / M4A (`mov,mp4,m4a,...`) | H.264 (8-bit 4:2:0), VP9, AV1 | AAC, MP3, Opus, FLAC |
| WebM / Matroska (`matroska,webm`) | VP8, VP9, AV1 | Opus, Vorbis |
| MP3, ADTS AAC, Ogg, WAV, FLAC (sound only) | none | MP3; AAC; Opus, Vorbis, FLAC; PCM; FLAC |

- H.264 in 10-bit or 4:2:2/4:4:4 (High 10, High 4:2:2) doesn't play in
  Chromium's software decoder: proxy.
- Matroska with H.264 or AAC (a common OBS recording) is given a proxy:
  Chromium 126 is not known to play Matroska beyond WebM's codecs. A guess
  on the safe side, until step 8 tries it.
- Bigger than 1080p (either side, after rotation) or more than 60 frames a
  second: proxy, so scrubbing stays smooth.

Standard library only.
"""

from __future__ import annotations

import json
import math
import subprocess
from pathlib import Path

MP4_FAMILY = "mov,mp4,m4a,3gp,3g2,mj2"
PLAYS = {
    "mp4": ({"h264", "vp9", "av1"}, {"aac", "mp3", "opus", "flac"}),
    "matroska": ({"vp8", "vp9", "av1"}, {"opus", "vorbis"}),
    "mp3": (set(), {"mp3"}),
    "aac": (set(), {"aac"}),
    "ogg": (set(), {"opus", "vorbis", "flac"}),
    "wav": (set(), {"pcm_u8", "pcm_s16le", "pcm_s24le", "pcm_s32le", "pcm_f32le"}),
    "flac": (set(), {"flac"}),
}
IMAGE_CODECS = {"png", "mjpeg", "webp", "gif", "bmp"}
IMAGE_FORMATS = {"image2", "png_pipe", "jpeg_pipe", "webp_pipe", "gif", "bmp_pipe", "gif_pipe"}
H264_PIXELS = {"yuv420p", "yuvj420p"}
MAX_SHORT, MAX_LONG, MAX_FPS = 1080, 1920, 60.5

# FFprobe and FFmpeg read a media file from this PC's disk and nothing
# else: no URL, pipe or other protocol a file could lead them to.
LOCAL_ONLY = ("-protocol_whitelist", "file")

# Formats that are only a list of other files or addresses (a playlist, a
# concat list, a session description): such a file is not media of its own,
# would make FFmpeg read whatever it names, and stops working when those go.
INDIRECT_FORMATS = frozenset({"hls", "applehttp", "concat", "ffconcat", "dash", "sdp", "rtp", "rtsp", "tee", "ffmetadata"})


class ProbeError(Exception):
    """The file can't be read as media, in plain words."""


def run_ffprobe(path, *, timeout: float = 60.0) -> dict:
    from core.binaries import ffprobe

    try:
        r = subprocess.run([ffprobe(), "-v", "error", *LOCAL_ONLY, "-show_format", "-show_streams", "-of", "json", str(path)],
                           capture_output=True, text=True, timeout=timeout, stdin=subprocess.DEVNULL)
    except subprocess.TimeoutExpired as e:
        raise ProbeError("Reading the file took too long.") from e
    except OSError as e:
        raise ProbeError(f"FFprobe couldn't be started ({e}).") from e
    try:
        data = json.loads(r.stdout or "{}")
    except ValueError:
        data = {}
    if r.returncode != 0 or not isinstance(data, dict) or not data.get("format"):
        raise ProbeError("This isn't a video, a sound or a picture Clips Kitty can read.")
    return data


def _number(value, default=0.0) -> float:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return default
    return out if math.isfinite(out) else default


def _fraction(text) -> dict | None:
    num, _, den = str(text or "").partition("/")
    try:
        n, d = int(num), int(den or 1)
    except ValueError:
        return None
    if n <= 0 or d <= 0:
        return None
    g = math.gcd(n, d)
    return {"num": n // g, "den": d // g}


def _rotation(stream: dict) -> int:
    """Degrees clockwise the picture is turned to be shown upright: 0, 90,
    180 or 270 (the old `rotate` tag, or the display matrix, whose rotation
    ffprobe gives counter-clockwise)."""
    tag = (stream.get("tags") or {}).get("rotate")
    if tag is not None:
        degrees = _number(tag)
    else:
        degrees = 0.0
        for side in stream.get("side_data_list") or []:
            if isinstance(side, dict) and "rotation" in side:
                degrees = -_number(side.get("rotation"))
                break
    return int(round(degrees / 90.0)) * 90 % 360


def _family(format_name: str) -> str:
    names = set(str(format_name or "").split(","))
    if format_name == MP4_FAMILY or {"mov", "mp4"} & names:
        return "mp4"
    if {"matroska", "webm"} & names:
        return "matroska"
    for name in ("mp3", "aac", "ogg", "wav", "flac"):
        if name in names:
            return name
    return str(format_name or "")


def describe(data: dict, *, size: int = 0) -> dict:
    """The facts the editor needs, from ffprobe's JSON."""
    fmt = data.get("format") or {}
    streams = [s for s in data.get("streams") or [] if isinstance(s, dict)]
    pictures = [s for s in streams if s.get("codec_type") == "video"]
    video = next((s for s in pictures if not (s.get("disposition") or {}).get("attached_pic")), None)
    audio = next((s for s in streams if s.get("codec_type") == "audio"), None)
    format_name = str(fmt.get("format_name") or "")
    if set(format_name.split(",")) & INDIRECT_FORMATS:
        raise ProbeError("This file is a list of other files (a playlist), not a video, a sound or a picture "
                         "of its own.")
    image = video is not None and (format_name in IMAGE_FORMATS or (
        video.get("codec_name") in IMAGE_CODECS and audio is None and _number(fmt.get("duration")) <= 0.05))
    if video is None and audio is None:
        raise ProbeError("This file has no picture and no sound Clips Kitty can read.")
    duration = _number(fmt.get("duration")) or _number((video or audio or {}).get("duration"))
    width = int(_number((video or {}).get("width")))
    height = int(_number((video or {}).get("height")))
    rotation = _rotation(video) if video else 0
    shown_w, shown_h = (height, width) if rotation in (90, 270) else (width, height)
    fps = None
    if video is not None and not image:
        fps = _fraction(video.get("avg_frame_rate")) or _fraction(video.get("r_frame_rate"))
    return {
        "kind": "image" if image else ("video" if video is not None else "audio"),
        "container": format_name,
        "size": int(_number(fmt.get("size"))) or int(size),
        "duration_us": 0 if image else int(round(duration * 1_000_000)),
        "start_us": int(round(_number(fmt.get("start_time")) * 1_000_000)),
        "width": width, "height": height,
        "display_width": shown_w, "display_height": shown_h,
        "rotation": rotation,
        "fps": fps,
        "fps_value": round(fps["num"] / fps["den"], 3) if fps else None,
        "video_codec": str(video.get("codec_name") or "") if video is not None else None,
        "pix_fmt": str(video.get("pix_fmt") or "") if video is not None else None,
        "audio_codec": str(audio.get("codec_name") or "") if audio is not None else None,
        "audio_channels": int(_number(audio.get("channels"))) if audio is not None else 0,
        "sample_rate": int(_number(audio.get("sample_rate"))) if audio is not None else 0,
        "has_video": video is not None and not image,
        "has_audio": audio is not None,
    }


def proxy_reasons(info: dict) -> list[str]:
    """Why the editor's <video> can't play the file as it is, in plain
    words; [] when it can. Pictures and sounds Chromium plays need nothing;
    a picture it doesn't is the export compiler's to convert (step 7)."""
    if info["kind"] == "image":
        return []
    family = _family(info.get("container", ""))
    videos, sounds = PLAYS.get(family, (set(), set()))
    reasons = []
    if family not in PLAYS:
        reasons.append(f"Chromium doesn't play the {info.get('container') or 'unknown'} format")
    vc, ac = info.get("video_codec"), info.get("audio_codec")
    if info.get("has_video"):
        if family in PLAYS and vc not in videos:
            reasons.append(f"Chromium doesn't play {vc or 'this'} video in {family.upper()}")
        elif vc == "h264" and info.get("pix_fmt") not in H264_PIXELS:
            reasons.append(f"Chromium doesn't play {info.get('pix_fmt') or 'this'} H.264 (only 8-bit 4:2:0)")
        short = min(info["display_width"], info["display_height"])
        long = max(info["display_width"], info["display_height"])
        if short > MAX_SHORT or long > MAX_LONG:
            reasons.append(f"bigger than 1080p ({info['display_width']}x{info['display_height']})")
        if (info.get("fps_value") or 0) > MAX_FPS:
            reasons.append(f"more than 60 frames a second ({info['fps_value']:g})")
    if info.get("has_audio") and family in PLAYS and ac not in sounds:
        reasons.append(f"Chromium doesn't play {ac or 'this'} sound in {family.upper()}")
    return reasons


def info(path) -> dict:
    """describe() for a file on disk. Raises ProbeError."""
    data = run_ffprobe(path)
    try:
        size = Path(path).stat().st_size
    except OSError:
        size = 0
    return describe(data, size=size)
