"""Propose cuts that tighten a clip: dead air and filler words.

Retention on short-form is decided in the first seconds, and a clip that
opens with two seconds of "uhh..." has spent them. This finds the pauses
and the filler words and returns the ranges worth dropping.

It PROPOSES only. The ranges go into the editor's normal edit list, drawn
on the timeline like any hand-made cut, so they can be reviewed, adjusted
or undone before anything is rendered — cutting someone's video silently
on a guess is not a trade worth making.

Silence comes from FFmpeg's silencedetect rather than from gaps in the
transcript: a gap between words is not necessarily quiet, and cutting
music, laughter or a held reaction would be worse than leaving the pause.
"""

import re
import subprocess
from pathlib import Path

# Sounds with no meaning to lose. Deliberately short: "like", "so" and
# "you know" are frequently load-bearing in speech, and cutting them
# mangles sentences.
FILLERS = {"um", "uh", "umm", "uhh", "uhm", "erm", "ah", "eh", "hmm", "mm", "mmm"}

MIN_GAP = 0.55       # a pause shorter than this is natural speech rhythm
KEEP_BREATH = 0.15   # leave this much of the pause, so cuts don't clip breath
MIN_CUT = 0.20       # not worth a cut below this
MIN_KEEP = 0.30      # never leave an unwatchable sliver behind


def _detect_silence(path: Path, noise_db: int = -32, min_gap: float = MIN_GAP) -> list[list[float]]:
    """Quiet stretches in the audio, as [start, end] seconds."""
    cmd = [
        "ffmpeg", "-hide_banner", "-nostats", "-i", str(path.resolve()),
        "-af", f"silencedetect=noise={noise_db}dB:d={min_gap:g}", "-f", "null", "-",
    ]
    try:
        r = subprocess.run(
            cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=300
        )
    except Exception:
        return []
    out: list[list[float]] = []
    start = None
    for m in re.finditer(r"silence_(start|end):\s*(-?[\d.]+)", r.stderr or ""):
        kind, value = m.group(1), float(m.group(2))
        if kind == "start":
            start = value
        elif start is not None:
            out.append([max(0.0, start), value])
            start = None
    if start is not None:  # silence running to the end of the clip
        out.append([max(0.0, start), float("inf")])
    return out


def _filler_ranges(words: list[dict]) -> list[list[float]]:
    """Spans of standalone filler sounds, as [start, end] seconds."""
    out = []
    for w in words or []:
        token = re.sub(r"[^\w']", "", str(w.get("word", ""))).lower()
        if token in FILLERS:
            out.append([float(w["start"]), float(w["end"])])
    return out


def _merge(ranges: list[list[float]], duration: float) -> list[list[float]]:
    clean = sorted(
        [max(0.0, a), min(duration, b)] for a, b in ranges if b > a
    )
    merged: list[list[float]] = []
    for a, b in clean:
        if merged and a <= merged[-1][1] + 0.05:
            merged[-1][1] = max(merged[-1][1], b)
        else:
            merged.append([a, b])
    return merged


def propose(
    clip_path: Path,
    words: list[dict],
    duration: float,
    drop_silence: bool = True,
    drop_fillers: bool = True,
) -> dict:
    """Keep-ranges with the dead air and filler words taken out.

    Returns {keep, removed_seconds, cuts, new_duration}; `keep` is exactly
    the shape render_opts["edit"]["keep"] expects."""
    cuts: list[list[float]] = []

    if drop_silence:
        for a, b in _detect_silence(clip_path):
            b = min(b, duration)
            # Leave a breath at each end so speech doesn't start abruptly.
            a2, b2 = a + KEEP_BREATH, b - KEEP_BREATH
            # A pause at the very start or end can go entirely — there is no
            # speech on the outer side to protect.
            if a <= 0.05:
                a2 = 0.0
            if b >= duration - 0.05:
                b2 = duration
            if b2 - a2 >= MIN_CUT:
                cuts.append([a2, b2])

    if drop_fillers:
        cuts.extend(_filler_ranges(words))

    cuts = _merge(cuts, duration)

    keep: list[list[float]] = []
    cursor = 0.0
    for a, b in cuts:
        if a - cursor >= MIN_KEEP:
            keep.append([round(cursor, 2), round(a, 2)])
        cursor = max(cursor, b)
    if duration - cursor >= MIN_KEEP:
        keep.append([round(cursor, 2), round(duration, 2)])

    if not keep:  # everything looked droppable — clearly wrong, change nothing
        return {"keep": [[0.0, round(duration, 2)]], "removed_seconds": 0.0,
                "cuts": 0, "new_duration": round(duration, 2)}

    kept = sum(b - a for a, b in keep)
    return {
        "keep": keep,
        "removed_seconds": round(duration - kept, 2),
        "cuts": len(cuts),
        "new_duration": round(kept, 2),
    }
