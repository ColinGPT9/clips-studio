# SPDX-License-Identifier: MIT
# Copyright (c) 2026 ColinGPT9. The Clips Kitty SDK; see sdk/python/LICENSE.
"""Turning what media reads into moments: colours and brightness in a frame,
spikes in a list of numbers, and the times they join into.

    from clipskitty_sdk import media, signals

    def main(job):
        loud = signals.spikes(media.loudness(job), louder_by=6.0)       # seconds
        for start, end in signals.merge(signals.stretches(loud)):
            job.add_range(*signals.around(job, start, end), label="loud")

It needs nothing of its own: it works on the numbers and frames that media
(or the plugin's own code) gives it, and on the job's limits. Standard
library only.
"""

from __future__ import annotations

import re
import statistics

_HEX = re.compile(r"#?([0-9a-fA-F]{6})")


def _colour(colour) -> tuple[int, int, int]:
    """(red, green, blue) from "e0303a", "#e0303a" or (224, 48, 58)."""
    if isinstance(colour, str):
        m = _HEX.fullmatch(colour.strip())
        if not m:
            raise ValueError(f'a colour is 6 hex digits, such as "e0303a" (red, green, blue), not {colour!r}')
        value = int(m.group(1), 16)
        return value >> 16, (value >> 8) & 0xFF, value & 0xFF
    try:
        red, green, blue = (int(c) for c in colour)
    except (TypeError, ValueError):
        raise ValueError(f"a colour is 6 hex digits or (red, green, blue), not {colour!r}") from None
    if not all(0 <= c <= 255 for c in (red, green, blue)):
        raise ValueError(f"each part of a colour is from 0 to 255, not {colour!r}")
    return red, green, blue


def colour_share(frame, colour="e0303a", tolerance: int = 60) -> float:
    """The share of the frame's pixels, from 0 to 1, that are `colour`: each
    of red, green and blue within `tolerance` (0 to 255) of it. `colour` is
    6 hex digits ("e0303a") or (red, green, blue)."""
    red, green, blue = _colour(colour)
    rgb, near = frame.rgb, int(tolerance)
    count = len(rgb) // 3
    if not count:
        return 0.0
    hits = sum(1 for i in range(0, count * 3, 3)
               if abs(rgb[i] - red) <= near and abs(rgb[i + 1] - green) <= near and abs(rgb[i + 2] - blue) <= near)
    return hits / count


def brightness(frame) -> float:
    """How bright the frame is on average, from 0 (black) to 255 (white),
    weighting red, green and blue as the eye does (0.299, 0.587, 0.114)."""
    rgb = frame.rgb
    count = len(rgb) // 3
    if not count:
        return 0.0
    return (0.299 * sum(rgb[0:count * 3:3]) + 0.587 * sum(rgb[1:count * 3:3]) + 0.114 * sum(rgb[2:count * 3:3])) / count


def difference(a, b) -> float:
    """How different two frames of the same size are, from 0 (the same) to
    255: the average difference of their red, green and blue values."""
    if (a.width, a.height) != (b.width, b.height) or len(a.rgb) != len(b.rgb):
        raise ValueError(f"the frames differ in size ({a.width}x{a.height} and {b.width}x{b.height})")
    if not a.rgb:
        return 0.0
    return sum(abs(x - y) for x, y in zip(a.rgb, b.rgb)) / len(a.rgb)


def spikes(values, louder_by: float = 6.0, window: int = 30) -> list[int]:
    """The positions in `values` that are at least `louder_by` above the
    median of the `window` values around them (a rolling median, so a video
    that gets louder or brighter over time is compared with its own
    surroundings). For media.loudness(), a position is a second, and
    louder_by is in dB."""
    values = [float(v) for v in values]
    size = max(1, int(window))
    half = size // 2
    out = []
    for i, value in enumerate(values):
        first = max(0, min(i - half, len(values) - size))
        around = values[first:first + size]
        if value >= statistics.median(around) + louder_by:
            out.append(i)
    return out


def stretches(times, gap: float = 1.0, min_length: float = 0.5) -> list[tuple[float, float]]:
    """Times joined into stretches: (first, last) for each run of times no
    more than `gap` apart, kept when last - first is at least `min_length`.
    For the times of frames where a banner shows, that is when it shows;
    with min_length 0, a time on its own is a stretch too."""
    ordered = sorted(float(t) for t in times)
    runs: list[list[float]] = []
    for t in ordered:
        if runs and t - runs[-1][1] <= gap + 1e-9:
            runs[-1][1] = t
        else:
            runs.append([t, t])
    return [(first, last) for first, last in runs if last - first >= min_length - 1e-9]


def merge(ranges, gap: float = 2.0) -> list[tuple[float, float]]:
    """(start, end) ranges joined where they overlap or are no more than
    `gap` seconds apart, in order. Each range may carry more items after
    its start and end; only those two are kept."""
    ordered = sorted((float(r[0]), float(r[1])) for r in ranges)
    out: list[list[float]] = []
    for start, end in ordered:
        if out and start - out[-1][1] <= gap + 1e-9:
            out[-1][1] = max(out[-1][1], end)
        else:
            out.append([start, end])
    return [(start, end) for start, end in out]


def around(job, start: float, end: float, lead: float = 6.0, tail: float = 3.0) -> tuple[float, float]:
    """A moment around `start` to `end`: from `lead` seconds before it to
    `tail` seconds after, inside the video (its length, when the job knows
    it), then fitted to the job's limits: cut to its longest clip, keeping
    the start, or made as long as its shortest. Seconds, rounded to 0.01."""
    duration = getattr(job.video, "duration", None) if job.video is not None else None
    duration = float(duration) if isinstance(duration, (int, float)) and duration > 0 else None
    first = max(0.0, float(start) - float(lead))
    last = float(end) + float(tail)
    if duration is not None:
        last = min(duration, last)
    longest, shortest = job.limits.max_duration, job.limits.min_duration
    if longest and last - first > longest:
        last = first + float(longest)
    if shortest and last - first < shortest:
        last = first + float(shortest)
        if duration is not None and last > duration:
            last = duration
            first = max(0.0, last - float(shortest))
    return round(first, 2), round(last, 2)


__all__ = ["around", "brightness", "colour_share", "difference", "merge", "spikes", "stretches"]
