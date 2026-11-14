"""Thumbnail sheets for the timeline: small frames at a regular spacing,
tiled COLUMNS x ROWS to a JPEG sheet, and an index.

One FFmpeg pass. With `-skip_frame nokey` the decoder decodes keyframes
only, and a `select` keeps the first keyframe of each `interval` (the first
at or after 0 s, 15 s, 30 s...), so each tile is a real frame and the index
gives its real time (read from `showinfo`). A source whose keyframes
are further apart than the spacing (fewer than half the tiles expected) is
done again decoding every frame: slower, but the spacing holds.

The index (index.json; GET /media/{id}/thumbs adds each sheet's address):

    {"version": 1, "interval_us": 15000000, "keyframes_only": true,
     "tile": {"width": 160, "height": 90}, "columns": 10, "rows": 10,
     "count": 480, "times_us": [0, 15015000, ...],
     "sheets": [{"index": 0, "file": "sheet-001.jpg", "width": 1600, "height": 900, "first": 0, "count": 100}]}

Tile i is on sheet i // 100, column (i % 100) % 10, row (i % 100) // 10, at
x = column x tile width, y = row x tile height. Its time is times_us[i],
counted from the start of the source (its first timestamp is 0).

Standard library only.
"""

from __future__ import annotations

import math
import re
import subprocess
import threading
from pathlib import Path

from video_editor.media import probe

TILE_HEIGHT = 90
COLUMNS, ROWS = 10, 10
MAX_TILES = 600
SPACINGS = (1, 2, 5, 10, 15, 30, 60, 120, 300, 600)
_PTS = re.compile(r"\bn:\s*(\d+)\s+pts:\s*(-?\d+)\s+pts_time:\s*(-?[0-9.]+)")


def spacing(duration_s: float) -> int:
    """Seconds between tiles: the smallest of SPACINGS that keeps a source
    to MAX_TILES tiles."""
    for s in SPACINGS:
        if duration_s / s <= MAX_TILES:
            return s
    return SPACINGS[-1]


def tile_size(display_w: int, display_h: int) -> tuple[int, int]:
    if display_w <= 0 or display_h <= 0:
        return 160, TILE_HEIGHT
    w = int(round(TILE_HEIGHT * display_w / display_h / 2.0)) * 2
    return max(32, min(320, w)), TILE_HEIGHT


def command(ffmpeg: str, source, out_pattern, *, interval: float, tile: tuple[int, int],
            keyframes_only: bool) -> list[str]:
    w, h = tile
    chain = ",".join((
        f"select='isnan(prev_selected_t)+gt(floor(t/{interval:g})\\,floor(prev_selected_t/{interval:g}))'",
        f"scale={w}:{h}:force_original_aspect_ratio=decrease",
        f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2",
        "setsar=1",
        "showinfo",
        f"tile={COLUMNS}x{ROWS}",
    ))
    argv = [ffmpeg, "-hide_banner", "-nostdin", "-v", "info"]
    if keyframes_only:
        argv += ["-skip_frame", "nokey"]
    return [*argv, *probe.LOCAL_ONLY, "-i", str(source), "-map", "0:v:0", "-an", "-sn", "-dn", "-vf", chain, "-fps_mode", "vfr", "-q:v", "5", "-f", "image2", str(out_pattern)]


def parse_times(stderr_lines) -> list[float]:
    """Each kept frame's time (seconds), from showinfo's lines."""
    times = []
    for line in stderr_lines:
        if "showinfo" not in line:
            continue
        m = _PTS.search(line)
        if m:
            times.append(float(m.group(3)))
    return times


def _run(argv, *, popen, started, on_progress, duration_s) -> tuple[int, list[float], list[str]]:
    from video.export_presets import low_priority

    proc = popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                 **low_priority())
    if started is not None:
        started(proc)
    times: list[float] = []
    tail: list[str] = []

    def read() -> None:
        for raw in iter(proc.stderr.readline, b""):
            line = raw.decode("utf-8", "replace")
            if "showinfo" in line:
                m = _PTS.search(line)
                if m:
                    times.append(float(m.group(3)))
                    if on_progress is not None and duration_s > 0:
                        on_progress(min(0.999, times[-1] / duration_s))
                    continue
            tail.append(line.rstrip())
            del tail[:-20]

    reader = threading.Thread(target=read, daemon=True)
    reader.start()
    try:
        code = proc.wait()
    finally:
        reader.join(timeout=10)
        try:
            proc.stderr.close()
        except Exception:
            pass
    return code, times, tail


def make(source, folder: Path, *, info: dict, popen=None, started=None, on_progress=None,
         check_cancel=None) -> dict:
    """Write the sheets into `folder` (a temporary folder the caller renames)
    and return the index. Raises RuntimeError when FFmpeg fails."""
    from core.binaries import ffmpeg

    popen = popen or subprocess.Popen
    folder.mkdir(parents=True, exist_ok=True)
    duration_s = max(0.0, info.get("duration_us", 0) / 1e6)
    interval = spacing(duration_s)
    tile = tile_size(info.get("display_width", 0), info.get("display_height", 0))
    image = info.get("kind") == "image"
    expected = 1 if image else math.floor(duration_s / interval) + 1
    start = (info.get("start_us") or 0) / 1e6
    attempts = (False,) if image else (True, False)
    for keyframes_only in attempts:
        for old in folder.glob("sheet-*.jpg"):
            old.unlink()
        argv = command(ffmpeg(), source, folder / "sheet-%03d.jpg", interval=interval, tile=tile,
                       keyframes_only=keyframes_only)
        code, times, tail = _run(argv, popen=popen, started=started, on_progress=on_progress,
                                 duration_s=duration_s)
        if check_cancel is not None:
            check_cancel()
        if code != 0:
            raise RuntimeError("FFmpeg couldn't make the thumbnails: " + (tail[-1] if tail else f"exit code {code}"))
        if not keyframes_only or len(times) * 2 >= expected:
            break
    sheets = sorted(folder.glob("sheet-*.jpg"))
    per_sheet = COLUMNS * ROWS
    count = len(times)
    if len(sheets) != -(-count // per_sheet):
        raise RuntimeError(f"FFmpeg made {len(sheets)} sheet(s) for {count} thumbnails")
    return {
        "version": 1,
        "interval_us": interval * 1_000_000,
        "keyframes_only": keyframes_only,
        "tile": {"width": tile[0], "height": tile[1]},
        "columns": COLUMNS, "rows": ROWS,
        "count": count,
        "times_us": [max(0, int(round((t - start) * 1_000_000))) for t in times],
        "sheets": [{"index": i, "file": p.name, "width": tile[0] * COLUMNS, "height": tile[1] * ROWS,
                    "first": i * per_sheet, "count": min(per_sheet, count - i * per_sheet)}
                   for i, p in enumerate(sheets)],
    }
