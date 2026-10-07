"""Loud moments, cut on scene changes: an example Clips Kitty pipeline.

What it does, and nothing more:

1. Measures the video's loudness every tenth of a second with FFmpeg's
   ebur128 filter, and averages it per second.
2. Marks the seconds that are `louder_by_db` above the video's median
   loudness, and joins neighbours into loud stretches.
3. Finds scene cuts with FFmpeg's scene-change score.
4. Turns each loud stretch into a clip: it starts at the last cut before the
   stretch (or `lead_in_seconds` before it when no cut is near), ends a moment
   after the stretch, and is kept between the job's shortest and longest clip.

It does not know what is on screen or what is said. Loud is not the same as
interesting, and a quiet highlight is missed. That is the honest limit of a
detector built on two FFmpeg filters, and it is why this is an example.

It imports only the Python standard library and the Clips Kitty SDK.
"""

from __future__ import annotations

import re
import statistics
import subprocess

from clipskitty_sdk import run

# How far before a loud stretch a scene cut may be and still start the clip.
CUT_SEARCH_SECONDS = 8.0
# Seconds kept after a loud stretch ends, so a clip doesn't stop mid-reaction.
TAIL_SECONDS = 2.0
# Quiet seconds allowed inside one loud stretch.
MERGE_GAP_SECONDS = 2.0

_PTS = re.compile(r"pts_time:\s*([0-9.]+)")
_MOMENTARY = re.compile(r"lavfi\.r128\.M=(-?[0-9.]+|-inf)")


def _ffmpeg(ffmpeg: str, video: str, filters: list[str]) -> str:
    """Run FFmpeg over the video with these filter options; return what it printed."""
    proc = subprocess.run([ffmpeg, "-hide_banner", "-nostats", "-i", video, *filters, "-f", "null", "-"],
                          capture_output=True, text=True, encoding="utf-8", errors="replace")
    if proc.returncode != 0:
        tail = "\n".join(proc.stderr.strip().splitlines()[-3:])
        raise RuntimeError(f"FFmpeg could not read the video: {tail}")
    return proc.stderr


def loudness_per_second(ffmpeg: str, video: str) -> list[float]:
    """Momentary loudness (LUFS) averaged per second; silence is -70."""
    out = _ffmpeg(ffmpeg, video, ["-vn", "-af", "ebur128=metadata=1,ametadata=mode=print:key=lavfi.r128.M"])
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
            buckets.setdefault(int(time), []).append(-70.0 if value == "-inf" else max(-70.0, float(value)))
    if not buckets:
        return []
    return [statistics.fmean(buckets[s]) if s in buckets else -70.0 for s in range(max(buckets) + 1)]


def scene_cuts(ffmpeg: str, video: str, threshold: float) -> list[float]:
    """Times (seconds) where the picture changes by more than `threshold` (0-1)."""
    out = _ffmpeg(ffmpeg, video, ["-an", "-vf", f"select='gt(scene,{threshold})',showinfo"])
    return sorted(float(m.group(1)) for line in out.splitlines() if "Parsed_showinfo" in line
                  for m in [_PTS.search(line)] if m)


def loud_stretches(levels: list[float], louder_by_db: float) -> list[tuple[int, int, float]]:
    """(first second, last second + 1, peak dB above the median) for each loud stretch."""
    if len(levels) < 3:
        return []
    median = statistics.median(levels)
    loud = [s for s, level in enumerate(levels) if level >= median + louder_by_db]
    stretches: list[list[int]] = []
    for s in loud:
        if stretches and s - stretches[-1][1] <= MERGE_GAP_SECONDS:
            stretches[-1][1] = s
        else:
            stretches.append([s, s])
    return [(a, b + 1, max(levels[a:b + 1]) - median) for a, b in stretches]


def to_range(stretch, cuts: list[float], duration: float, lead_in: float, shortest: float, longest: float):
    """A clip around a loud stretch, starting on a cut when one is close before it."""
    loud_start, loud_end, _ = stretch
    before = [c for c in cuts if loud_start - CUT_SEARCH_SECONDS <= c <= loud_start + 0.5]
    start = before[-1] if before else max(0.0, loud_start - lead_in)
    end = min(duration, loud_end + TAIL_SECONDS)
    if end - start > longest:
        end = start + longest
    if end - start < shortest:
        end = min(duration, start + shortest)
        start = max(0.0, end - shortest)
    return round(start, 2), round(end, 2), bool(before)


def main(job):
    if job.video is None or not job.tools.ffmpeg:
        job.fail("This pipeline needs the video and FFmpeg (permissions video.read and ffmpeg).")
    video, ffmpeg = str(job.video.path), job.tools.ffmpeg
    settings = job.settings
    job.progress(0.05, "Measuring loudness")
    levels = loudness_per_second(ffmpeg, video)
    duration = float(job.video.duration or len(levels))
    job.progress(0.5, "Looking for scene cuts")
    cuts = scene_cuts(ffmpeg, video, float(settings["scene_threshold"]))
    job.log(f"{len(cuts)} scene cut(s) found")
    job.progress(0.9, "Choosing moments")
    stretches = loud_stretches(levels, float(settings["louder_by_db"]))
    shortest = float(job.limits.min_duration or 10)
    longest = float(job.limits.max_duration or 60)
    for stretch in stretches:
        start, end, on_cut = to_range(stretch, cuts, duration, float(settings["lead_in_seconds"]), shortest, longest)
        if end - start < 1:
            continue
        louder = stretch[2]
        job.add_range(start, end, score=min(95.0, 50.0 + 4.0 * louder), label="loud",
                      title="A loud moment",
                      reason=f"{louder:.0f} dB louder than the video's usual level"
                             + (", starting on a scene cut" if on_cut else ""))
    if not stretches:
        job.finish(notes="Nothing was clearly louder than the rest of the video, so no moments were picked.")
    job.progress(1.0, f"{len(job.ranges)} loud moment(s)")


if __name__ == "__main__":
    run(main)
