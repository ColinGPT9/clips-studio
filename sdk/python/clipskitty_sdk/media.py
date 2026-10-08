# SPDX-License-Identifier: MIT
# Copyright (c) 2026 ColinGPT9. The Clips Kitty SDK; see sdk/python/LICENSE.
"""Reading the video with Clips Kitty's FFmpeg: its size, a part of the
screen frame by frame, one frame as a JPEG, its loudness and its scene cuts.

    from clipskitty_sdk import media, signals

    BANNER = media.Region.parse("0.30,0.10,0.40,0.10")   # left, top, width, height (0 to 1)

    def main(job):
        seen = [f.t for f in media.frames(job, fps=4, region=BANNER)
                if signals.colour_share(f, "e0303a") > 0.5]

Every function takes the job and uses the FFmpeg and FFprobe Clips Kitty
hands over (`job.tools`), so the plugin needs `ffmpeg` in its permissions,
and `video.read` for the video itself. Without them, the error says which
one to add. FFmpeg's own problems raise MediaError with its last line.

loudness() and scene_cuts() are adapted from the MIT example pipeline in
examples/pipelines/scene-cut-highlights/src/main.py (its
loudness_per_second() and scene_cuts()); the rest is written for the SDK.
Standard library only.
"""

from __future__ import annotations

import json
import re
import statistics
import subprocess
import tempfile
from collections.abc import Iterator
from dataclasses import dataclass

from . import samples

NEEDS_FFMPEG = "This pipeline needs FFmpeg: add ffmpeg to permissions in clipskitty.yaml"
NEEDS_VIDEO = "This pipeline needs the video: add video.read to permissions in clipskitty.yaml"
# The plugin asks for ffmpeg, but no FFmpeg (or FFprobe) was found for this
# run. The installed app always has both; a developer's PC may not.
NOT_FOUND = "Clips Kitty couldn't find {tool} on this PC, which this pipeline needs to read the video"


class MediaError(RuntimeError):
    """The video couldn't be read: a permission is missing, or FFmpeg failed.
    The message says which, in words a creator can read."""


@dataclass(frozen=True)
class Region:
    """A part of the screen, as fractions of the frame from 0 to 1: `x` and
    `y` are its left and top edges, `w` and `h` its width and height. The
    same region fits any size of video. `python -m clipskitty_sdk frame
    VIDEO --at SECONDS --region "..."` shows one on a frame."""

    x: float
    y: float
    w: float
    h: float

    def __post_init__(self):
        try:
            values = [float(v) for v in (self.x, self.y, self.w, self.h)]
        except (TypeError, ValueError):
            values = None
        # The same checks as `frame --region` (samples.parse_region); ValueError says what is wrong.
        samples.parse_region(",".join(map(repr, values)) if values else "")
        for name, value in zip("xywh", values):
            object.__setattr__(self, name, value)

    @classmethod
    def parse(cls, text: str) -> Region:
        """A region from text such as "0.30,0.10,0.40,0.10" (left, top,
        width, height), with commas, spaces or both between the numbers.
        Raises ValueError with a sentence saying what is wrong."""
        return cls(*samples.parse_region(text))

    def pixels(self, width: int, height: int) -> tuple[int, int, int, int]:
        """The region on a `width` x `height` frame, in whole pixels: (x, y,
        w, h), at least 1 pixel wide and high, and inside the frame."""
        return samples.region_pixels((self.x, self.y, self.w, self.h), width, height)

    def __str__(self) -> str:
        return ",".join(f"{v:g}" for v in (self.x, self.y, self.w, self.h))


@dataclass(frozen=True)
class VideoInfo:
    """The video as its frames are read: `width` and `height` in pixels
    (after any rotation the file asks for), `fps` frames a second, and
    `duration` in seconds (None when FFprobe can't tell)."""

    width: int
    height: int
    fps: float
    duration: float | None


@dataclass(frozen=True)
class Frame:
    """One frame, or one region of it: `t` seconds into the video, `width` x
    `height` pixels, and `rgb` with 3 bytes a pixel (red, green, blue), row
    by row from the top left."""

    t: float
    width: int
    height: int
    rgb: bytes

    def pixel(self, x: int, y: int) -> tuple[int, int, int]:
        """The (red, green, blue) of the pixel `x` across and `y` down."""
        i = (y * self.width + x) * 3
        return self.rgb[i], self.rgb[i + 1], self.rgb[i + 2]


# ---- what the job hands over --------------------------------------------------------


def _tool(job, name: str) -> str:
    """The path of FFmpeg or FFprobe for this job, or MediaError saying why there is none."""
    tools = job.data.get("tools") if isinstance(getattr(job, "data", None), dict) else None
    if not isinstance(tools, dict) or "ffmpeg" not in tools:  # Clips Kitty hands it over only with the permission
        raise MediaError(NEEDS_FFMPEG)
    path = getattr(job.tools, name, None)
    if not path:
        raise MediaError(NOT_FOUND.format(tool="FFmpeg" if name == "ffmpeg" else "FFprobe"))
    return str(path)


def _video(job) -> str:
    if job.video is None:
        raise MediaError(NEEDS_VIDEO)
    return str(job.video.path)


def _last_line(text: str) -> str:
    lines = [line.strip() for line in (text or "").strip().splitlines() if line.strip()]
    return lines[-1] if lines else "no message"


def _run(job, args: list[str]) -> str:
    """Run FFmpeg over the video with these options, writing nothing; return
    what it printed on standard error (where filters print what they find)."""
    ffmpeg, video = _tool(job, "ffmpeg"), _video(job)
    try:
        done = subprocess.run([ffmpeg, "-hide_banner", "-nostats", "-nostdin", "-i", video, *args, "-f", "null", "-"],
                              capture_output=True, text=True, encoding="utf-8", errors="replace")
    except OSError as e:
        raise MediaError(f"FFmpeg couldn't start ({e})") from e
    if done.returncode != 0:
        raise MediaError(f"FFmpeg couldn't read the video: {_last_line(done.stderr)}")
    return done.stderr


def _rate(text) -> float | None:
    """A frame rate as FFprobe writes it ("30000/1001"), or None."""
    top, _, bottom = str(text or "").partition("/")
    try:
        value = float(top) / float(bottom or 1)
    except (ValueError, ZeroDivisionError):
        return None
    return value if value > 0 else None


def _turn(stream: dict) -> float:
    """How far the file says to turn the picture, in degrees (0 when it says nothing)."""
    said = [side.get("rotation") for side in stream.get("side_data_list") or []
            if isinstance(side, dict) and "rotation" in side]
    said.append((stream.get("tags") or {}).get("rotate"))
    for value in said:
        try:
            turn = float(value or 0)
        except (TypeError, ValueError):
            continue
        if turn:
            return turn
    return 0.0


# ---- the video ------------------------------------------------------------------------


def probe(job) -> VideoInfo:
    """The video's size, frame rate and length, from FFprobe. A video the
    file says to show turned by 90 degrees (as phones record) is measured
    turned, the way FFmpeg reads its frames."""
    ffprobe, video = _tool(job, "ffprobe"), _video(job)
    entries = ("format=duration:stream=width,height,avg_frame_rate,r_frame_rate,duration"
               ":stream_side_data=rotation:stream_tags=rotate")
    command = [ffprobe, "-v", "error", "-select_streams", "v:0", "-print_format", "json", "-show_entries", entries,
               video]
    try:
        done = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", errors="replace")
    except OSError as e:
        raise MediaError(f"FFprobe couldn't start ({e})") from e
    if done.returncode != 0:
        raise MediaError(f"FFprobe couldn't read the video: {_last_line(done.stderr)}")
    try:
        data = json.loads(done.stdout or "{}")
    except ValueError:
        data = {}
    streams = data.get("streams") or []
    stream = streams[0] if streams and isinstance(streams[0], dict) else {}
    width, height = stream.get("width"), stream.get("height")
    if not (isinstance(width, int) and isinstance(height, int) and width > 0 and height > 0):
        raise MediaError("FFprobe found no picture in the video")
    if round(_turn(stream)) % 180 == 90:
        width, height = height, width
    fps = _rate(stream.get("avg_frame_rate")) or _rate(stream.get("r_frame_rate")) or 0.0
    duration = None
    for value in ((data.get("format") or {}).get("duration"), stream.get("duration"),
                  getattr(job.video, "duration", None)):
        try:
            duration = float(value)
        except (TypeError, ValueError):
            continue
        if duration > 0:
            break
        duration = None
    return VideoInfo(width, height, fps, duration)


def _region(value) -> Region | None:
    if value is None or isinstance(value, Region):
        return value
    if isinstance(value, str):
        return Region.parse(value)
    return Region(*value)


def frames(job, fps: float = 4, region=None, size: tuple[int, int] | None = (32, 8)) -> Iterator[Frame]:
    """The video's frames, `fps` a second from the start (frame n is at n /
    fps seconds), one Frame at a time.

    `region` (a Region, or text such as "0.30,0.10,0.40,0.10") keeps only
    that part of each frame. `size` shrinks what is kept to that many pixels
    (width, height), averaging the pixels it joins, which makes each frame
    quick to check: a banner or an icon is still clear at 32x8. With size
    None, frames keep their full size, which is much slower to check.

    FFmpeg reads the video once, as the frames are used; stopping early
    stops it. A video FFmpeg can't read raises MediaError."""
    ffmpeg, video = _tool(job, "ffmpeg"), _video(job)
    rate = float(fps)
    if not rate > 0:
        raise ValueError("fps must be above 0")
    area = _region(region)
    filters = [f"fps={rate:g}", "format=rgb24"]
    info = probe(job)
    width, height = info.width, info.height
    if area is not None:
        x, y, width, height = area.pixels(info.width, info.height)
        filters.append(f"crop={width}:{height}:{x}:{y}")
    if size is not None:
        width, height = (int(n) for n in size)
        if width < 1 or height < 1:
            raise ValueError("size must be at least 1 pixel wide and high")
        filters.append(f"scale={width}:{height}:flags=area")
    command = [ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin", "-i", video, "-an",
               "-vf", ",".join(filters), "-f", "rawvideo", "-pix_fmt", "rgb24", "-"]
    return _read_frames(command, rate, width, height)


def _read_frames(command: list[str], rate: float, width: int, height: int) -> Iterator[Frame]:
    one = width * height * 3
    with tempfile.TemporaryFile() as errors:  # a file, so a chatty FFmpeg can never fill a pipe and stall
        try:
            proc = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=errors, stdin=subprocess.DEVNULL)
        except OSError as e:
            raise MediaError(f"FFmpeg couldn't start ({e})") from e
        finished = False
        try:
            n = 0
            while True:
                data = proc.stdout.read(one)
                if len(data) < one:
                    break
                yield Frame(round(n / rate, 6), width, height, data)
                n += 1
            finished = True
        finally:
            if not finished and proc.poll() is None:  # stopped early: FFmpeg isn't needed any more
                proc.kill()
            proc.stdout.close()
            code = proc.wait()
        if code != 0:
            errors.seek(0)
            raise MediaError(f"FFmpeg couldn't read the video: {_last_line(errors.read().decode('utf-8', 'replace'))}")


def jpeg(job, t: float, max_side: int = 896) -> bytes:
    """The frame at `t` seconds as a JPEG, shrunk so neither side is over
    `max_side` pixels (never enlarged): for local_model.ask(images=...).
    A time past the end of the video raises MediaError."""
    ffmpeg, video = _tool(job, "ffmpeg"), _video(job)
    side = int(max_side)
    if side < 1:
        raise ValueError("max_side must be at least 1")
    command = [ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin", "-ss", f"{float(t):g}", "-i", video,
               "-frames:v", "1", "-an",
               "-vf", f"scale=w='min(iw,{side})':h='min(ih,{side})':force_original_aspect_ratio=decrease",
               "-q:v", "3", "-f", "image2pipe", "-c:v", "mjpeg", "-"]
    try:
        done = subprocess.run(command, capture_output=True)
    except OSError as e:
        raise MediaError(f"FFmpeg couldn't start ({e})") from e
    if done.returncode != 0:
        raise MediaError(f"FFmpeg couldn't read the video: {_last_line(done.stderr.decode('utf-8', 'replace'))}")
    if not done.stdout.startswith(b"\xff\xd8"):
        raise MediaError(f"there is no frame at {float(t):g} s in the video: is it that long?")
    return done.stdout


# ---- sound and cuts (adapted from examples/pipelines/scene-cut-highlights, MIT) ------------

_PTS = re.compile(r"pts_time:\s*([0-9.]+)")
_MOMENTARY = re.compile(r"lavfi\.r128\.M=(-?[0-9.]+|-inf)")
SILENCE = -70.0  # what loudness() gives a silent second, in LUFS


def loudness(job) -> list[float]:
    """How loud the video is in each second: its momentary loudness (LUFS,
    from FFmpeg's ebur128 filter) averaged per second, so item n is second
    n. A silent second is SILENCE (-70). Empty for a video with no sound."""
    out = _run(job, ["-vn", "-af", "ebur128=metadata=1,ametadata=mode=print:key=lavfi.r128.M"])
    buckets: dict[int, list[float]] = {}
    time = None
    for line in out.splitlines():
        m = _PTS.search(line)
        if m:
            time = float(m.group(1))
            continue
        m = _MOMENTARY.search(line)
        if m and time is not None:
            value = m.group(1)
            buckets.setdefault(int(time), []).append(SILENCE if value == "-inf" else max(SILENCE, float(value)))
    if not buckets:
        return []
    return [statistics.fmean(buckets[s]) if s in buckets else SILENCE for s in range(max(buckets) + 1)]


def scene_cuts(job, threshold: float = 0.3) -> list[float]:
    """The times (seconds) where the picture changes by more than
    `threshold` (0 to 1; FFmpeg's scene-change score), in order."""
    out = _run(job, ["-an", "-vf", f"select='gt(scene,{float(threshold):g})',showinfo"])
    return sorted(float(m.group(1)) for line in out.splitlines() if "Parsed_showinfo" in line
                  for m in [_PTS.search(line)] if m)


__all__ = ["NEEDS_FFMPEG", "NEEDS_VIDEO", "SILENCE", "Frame", "MediaError", "Region", "VideoInfo", "frames", "jpeg",
           "loudness", "probe", "scene_cuts"]
