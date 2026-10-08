# SPDX-License-Identifier: MIT
# Copyright (c) 2026 ColinGPT9. The Clips Kitty SDK; see sdk/python/LICENSE.
"""Test media for developing a plugin, and a frame grab to measure a screen.

`python -m clipskitty_sdk sample OUT.mp4`, `run --sample` and `frame` use
these. The sample video is made only with FFmpeg's own generators (`lavfi`
colour and sine sources, `concat`, `drawbox`, the built-in `mpeg4` and `aac`
encoders), so it needs no fonts, no download and no other library:

- 40 seconds of 640x360 video at 25 frames a second: four 10-second colour
  scenes (dark blue, light grey, green, light yellow), so there are scene
  cuts at 10, 20 and 30 s;
- a red banner (colour e0303a) across the top, at region
  "0.30,0.10,0.40,0.10", and a small white square at the top right, at about
  "0.92,0.05,0.05,0.09", both shown from 22 to 27 s;
- a quiet 220 Hz tone throughout, and a loud 880 Hz one from 22 to 27 s.

sample_transcript() is what is said in it: "quark burst" from 21 to 26 s.
Quarkbloom Arena is a made-up game. Standard library only.
"""

from __future__ import annotations

import contextlib
import json
import math
import os
import re
import struct
import subprocess
import zlib
from pathlib import Path

SAMPLE_VIDEO_SECONDS = 40.0
SAMPLE_WIDTH, SAMPLE_HEIGHT = 640, 360
# Where the sample's banner is and when it shows, and when it is loud.
BANNER_REGION = "0.30,0.10,0.40,0.10"
BANNER_COLOUR = "e0303a"
BANNER_SECONDS = (22.0, 27.0)
LOUD_SECONDS = (22.0, 27.0)
SCENE_CUTS = (10.0, 20.0, 30.0)
# What `run --sample` says it hands over.
SAMPLE_NOTE = ('a 40-second test video with a red banner at the top and a loud sound from 22 to 27 s, '
               'and a transcript that says "quark burst" there')
# Where an installed Clips Kitty keeps its FFmpeg (ui/electron-builder.yml
# puts the frozen engine in resources/backend; clips-studio.spec puts FFmpeg
# in its _internal/ffmpeg folder).
INSTALLED_FFMPEG = r"resources\backend\_internal\ffmpeg inside the folder it was installed to"
MANIFEST_FILE = "clipskitty.yaml"

# Alternating dark and light scenes: similar dark colours gave missing cuts.
_SCENES = ("0x1e3a5f", "0xd0d0d0", "0x2f7f1a", "0xf0e0a0")
_FILTERS = (
    "[0][1][2][3]concat=n=4:v=1:a=0,"
    "drawbox=x=192:y=36:w=256:h=36:color=0xe0303a@1:t=fill:enable='between(t,22,27)',"
    "drawbox=x=588:y=18:w=32:h=32:color=white@1:t=fill:enable='between(t,22,27)'[v];"
    "[4]volume=0.05[q];"
    "[5]volume='if(between(t,22,27),0.8,0)':eval=frame[l];"
    "[q][l]amix=inputs=2:normalize=0[a]"
)
_SAID = (
    (8.0, 12.0, "round one, here we go"),
    (21.0, 26.0, "what a quark burst"),
    (34.5, 39.0, "just waiting for the respawn timer"),
)
# The box `frame` draws around a region: magenta, rarely a game's own colour.
_BOX_COLOUR = (255, 0, 255)


class SampleError(Exception):
    """FFmpeg couldn't make the sample or read the frame; the message says why."""


# ---- the sample -----------------------------------------------------------------------


def sample_transcript() -> dict:
    """What is said in the sample video, in transcript.json's shape: three
    segments, each with its words spread evenly over it, in the shape Clips
    Kitty's transcripts give word timings ({"start", "end", "word"}).

        8.0-12.0 s   round one, here we go
        21.0-26.0 s  what a quark burst
        34.5-39.0 s  just waiting for the respawn timer

    A new copy each time, so a test may change it."""
    segments = []
    for start, end, text in _SAID:
        words = text.split()
        step = (end - start) / len(words)
        segments.append({"start": start, "end": end, "text": text, "words": [
            {"start": round(start + i * step, 2), "end": round(start + (i + 1) * step, 2), "word": word}
            for i, word in enumerate(words)]})
    return {"language": "en", "segments": segments}


def transcript_path(video: Path) -> Path:
    """Where the transcript of a sample at `video` goes: sample.mp4 ->
    sample.transcript.json, beside it."""
    video = Path(video)
    return video.with_name(f"{video.stem}.transcript.json")


def sample_command(ffmpeg: str, out: Path) -> list[str]:
    """The FFmpeg command that writes the sample video to `out`, as MP4."""
    command = [ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin", "-y"]
    for colour in _SCENES:
        command += ["-f", "lavfi", "-i", f"color=c={colour}:s={SAMPLE_WIDTH}x{SAMPLE_HEIGHT}:r=25:d=10"]
    for pitch in (220, 880):
        command += ["-f", "lavfi", "-i", f"sine=f={pitch}:sample_rate=44100:d={SAMPLE_VIDEO_SECONDS:g}"]
    return [*command, "-filter_complex", _FILTERS, "-map", "[v]", "-map", "[a]",
            "-c:v", "mpeg4", "-q:v", "5", "-c:a", "aac", "-b:a", "64k",
            "-t", f"{SAMPLE_VIDEO_SECONDS:g}", "-movflags", "+faststart", "-f", "mp4", str(out)]


def _tail(text: str) -> str:
    lines = [line.strip() for line in (text or "").strip().splitlines() if line.strip()]
    return lines[-1] if lines else "no message"


_IS_A_FOLDER = "{out} is a folder: name the {what} file to write, not a folder"


def _why(e: OSError) -> str:
    return e.strerror or str(e)


def _folder_for(out: Path, what: str) -> None:
    """Make the folder `out` is written in. Raises SampleError, in plain
    words, when `out` is a folder itself, a file is in the way of its
    folder, or the folder can't be made."""
    if out.is_dir():
        raise SampleError(_IS_A_FOLDER.format(out=out, what=what))
    for where in (out.parent, *out.parent.parents):
        if where.exists():
            if not where.is_dir():
                raise SampleError(f"couldn't write {out}: {where} is a file, not a folder")
            break
    try:
        out.parent.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        raise SampleError(f"couldn't write {out}: {_why(e)}") from e


def make_sample(out: Path, ffmpeg: str) -> tuple[Path, Path]:
    """Write the sample video to `out` and its transcript beside it
    (transcript_path). Returns both paths. The video is written under a
    temporary name first, so a failed run leaves no half-written file.
    Raises SampleError, also when `out` can't be written."""
    out = Path(out)
    _folder_for(out, "video")
    part = out.with_name(out.name + ".part")
    try:
        done = subprocess.run(sample_command(ffmpeg, part), capture_output=True, text=True, encoding="utf-8",
                              errors="replace", timeout=300)
    except (OSError, subprocess.SubprocessError) as e:
        part.unlink(missing_ok=True)
        raise SampleError(f"FFmpeg couldn't make the sample video ({e})") from e
    if done.returncode != 0 or not part.is_file():
        part.unlink(missing_ok=True)
        raise SampleError(f"FFmpeg couldn't make the sample video: {_tail(done.stderr)}")
    transcript = transcript_path(out)
    try:
        os.replace(part, out)
    except OSError as e:
        with contextlib.suppress(OSError):
            part.unlink(missing_ok=True)
        raise SampleError(f"couldn't write {out}: {_why(e)}") from e
    try:
        transcript.write_text(json.dumps(sample_transcript(), indent=1), encoding="utf-8")
    except OSError as e:
        raise SampleError(f"couldn't write {transcript}: {_why(e)}") from e
    return out, transcript


def plugin_folder_holding(path: Path) -> Path | None:
    """The plugin folder (one holding clipskitty.yaml) that `path` would be
    written into, at or above its folder, or None. Clips Kitty copies
    everything in a plugin's folder when it installs it, so test media
    doesn't belong there."""
    folder = Path(path).resolve().parent
    for where in (folder, *folder.parents):
        if (where / MANIFEST_FILE).is_file():
            return where
    return None


def beside_plugin(plugin: Path, name: str) -> str:
    """Where to write `name` instead of inside the plugin folder `plugin`
    (as plugin_folder_holding gives it): in the folder that holds the
    plugin's folder. Written from the current folder when that is in there
    (../sample.mp4 from the plugin's own folder, ../../sample.mp4 from its
    src folder), else in full, and in double quotes when it has a space, so
    it can be typed as it is."""
    target = Path(plugin).parent / name
    try:
        Path.cwd().resolve().relative_to(target.parent)
        shown = os.path.relpath(target)
    except ValueError:  # not in there, or on another drive
        shown = str(target)
    return f'"{shown}"' if any(c.isspace() for c in shown) else shown


# ---- regions ----------------------------------------------------------------------------

_SEPARATORS = re.compile(r"[\s,]+")
_REGION_SHAPE = ('a region is four numbers from 0 to 1, as fractions of the frame: left, top, width and '
                 f'height, such as "{BANNER_REGION}"')


def _region_parts(text: str) -> list[str]:
    return [part for part in _SEPARATORS.split(str(text).strip()) if part]


def region_text(text: str) -> str:
    """A region as written, with commas between its numbers and no spaces,
    so it can be pasted into a command as one quoted argument."""
    return ",".join(_region_parts(text))


def parse_region(text: str) -> tuple[float, float, float, float]:
    """(left, top, width, height) as fractions of the frame, from text such
    as "0.30,0.10,0.40,0.10". The numbers may be separated by commas, spaces
    or both. Raises ValueError with a sentence saying what is wrong."""
    parts = _region_parts(text)
    try:
        values = tuple(float(part) for part in parts)
    except ValueError:
        values = ()
    if len(values) != 4 or not all(math.isfinite(v) and 0 <= v <= 1 for v in values):
        raise ValueError(_REGION_SHAPE)
    left, top, width, height = values
    if width <= 0 or height <= 0:
        raise ValueError(f'the width and height of "{region_text(text)}" must be above 0')
    if left + width > 1 + 1e-9 or top + height > 1 + 1e-9:
        raise ValueError(f'"{region_text(text)}" goes past the edge of the frame: left + width and '
                         "top + height must be 1 or less")
    return values


def region_pixels(region, width: int, height: int) -> tuple[int, int, int, int]:
    """A region (fractions, as parse_region gives) in pixels on a
    `width` x `height` frame: (x, y, w, h), each rounded, at least 1 pixel
    wide and high, and inside the frame."""
    left, top, w, h = region
    x, y = min(width - 1, round(left * width)), min(height - 1, round(top * height))
    return x, y, max(1, min(width - x, round(w * width))), max(1, min(height - y, round(h * height)))


# ---- a frame ------------------------------------------------------------------------------

_PPM_HEADER = re.compile(rb"P6\s+(\d+)\s+(\d+)\s+(\d+)\s")


def grab_frame(ffmpeg: str, video: Path, at: float) -> tuple[int, int, bytearray]:
    """The video's frame at `at` seconds: (width, height, RGB bytes, 3 per
    pixel, row by row). Raises SampleError."""
    # rgb24, as media.frames asks for: without it a 10-bit (HDR) video gives
    # a 16-bit picture.
    command = [ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin", "-ss", f"{float(at):g}",
               "-i", str(video), "-frames:v", "1", "-an", "-pix_fmt", "rgb24", "-f", "image2pipe", "-c:v", "ppm",
               "-"]
    try:
        done = subprocess.run(command, capture_output=True, timeout=120)
    except (OSError, subprocess.SubprocessError) as e:
        raise SampleError(f"FFmpeg couldn't read {video} ({e})") from e
    if done.returncode != 0:
        raise SampleError(f"FFmpeg couldn't read {video}: {_tail(done.stderr.decode('utf-8', 'replace'))}")
    header = _PPM_HEADER.match(done.stdout)
    if not header:
        raise SampleError(f"there is no frame at {float(at):g} s in {video}: is the video that long?")
    width, height, top = (int(n) for n in header.groups())
    rgb = bytearray(done.stdout[header.end():header.end() + width * height * 3])
    if top != 255 or len(rgb) != width * height * 3:
        raise SampleError(f"FFmpeg gave an unexpected picture for the frame at {float(at):g} s in {video}")
    return width, height, rgb


def _draw_box(rgb: bytearray, width: int, height: int, box: tuple[int, int, int, int]) -> None:
    """A magenta box around the region `box` (x, y, w, h), outside it, so
    what the region holds stays visible."""
    x, y, w, h = box
    thick = max(2, height // 180)
    left, right = max(0, x - thick), min(width, x + w + thick)

    def paint(row: int, first: int, last: int) -> None:  # columns first..last-1 of one row
        if first < last:
            rgb[(row * width + first) * 3:(row * width + last) * 3] = bytes(_BOX_COLOUR) * (last - first)

    for row in range(max(0, y - thick), min(height, y + h + thick)):
        if row < y or row >= y + h:
            paint(row, left, right)
        else:
            paint(row, left, min(x, width))
            paint(row, min(x + w, width), right)


def png_bytes(width: int, height: int, rgb: bytes) -> bytes:
    """An 8-bit RGB PNG of the picture, made with zlib alone."""
    row = width * 3
    raw = b"".join(b"\x00" + bytes(rgb[y * row:(y + 1) * row]) for y in range(height))

    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)

    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw, 6)) + chunk(b"IEND", b""))


def write_frame(ffmpeg: str, video: Path, at: float, out: Path,
                region=None) -> tuple[int, int, tuple[int, int, int, int] | None]:
    """Write the video's frame at `at` seconds to `out` as a PNG, with a box
    drawn around `region` (fractions, as parse_region gives) when one is
    given. Returns (width, height, the region in pixels or None). Raises
    SampleError, also when `out` can't be written."""
    out = Path(out)
    if out.is_dir():  # said before reading the video, not after
        raise SampleError(_IS_A_FOLDER.format(out=out, what="picture"))
    width, height, rgb = grab_frame(ffmpeg, video, at)
    box = region_pixels(region, width, height) if region is not None else None
    if box is not None:
        _draw_box(rgb, width, height, box)
    _folder_for(out, "picture")
    try:
        out.write_bytes(png_bytes(width, height, rgb))
    except OSError as e:
        raise SampleError(f"couldn't write {out}: {_why(e)}") from e
    return width, height, box


__all__ = ["BANNER_COLOUR", "BANNER_REGION", "BANNER_SECONDS", "INSTALLED_FFMPEG", "LOUD_SECONDS", "SAMPLE_HEIGHT",
           "SAMPLE_NOTE", "SAMPLE_VIDEO_SECONDS", "SAMPLE_WIDTH", "SCENE_CUTS", "SampleError", "beside_plugin",
           "grab_frame", "make_sample", "parse_region", "plugin_folder_holding", "png_bytes", "region_pixels",
           "region_text", "sample_command", "sample_transcript", "transcript_path", "write_frame"]
