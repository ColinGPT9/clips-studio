"""Downtime detection for Edited Streams.

Works on the per-second signal arrays the analysis stage already extracts
(audio loudness, visual motion) — no new probing of the video.

Removed:
  * silence: sustained near-silent audio. Twitch/Kick DMCA-muted music
    sections arrive ALREADY silenced in the VOD and look exactly like this —
    they're removed as ordinary silence, never treated as errors.
  * AFK/waiting/loading: sustained low motion + low audio together (a
    static "starting soon" screen is both).

Kept: everything else, in chronological order, with breathing room around
speech so words are never clipped.
"""

import numpy as np

MIN_SILENCE = 4.0     # seconds of silence before it's worth cutting
MIN_AFK = 20.0        # seconds of static+quiet before it counts as downtime
PAD = 0.75            # seconds kept on each side of every cut
MIN_KEEP = 2.0        # kept islands shorter than this get absorbed into the cut


def detect_keep_ranges(
    loudness: np.ndarray,
    motion: np.ndarray | None,
    duration: float,
) -> list[tuple[float, float]]:
    """Chronological (start, end) ranges to KEEP, in seconds."""
    n = int(min(len(loudness), duration))
    if n < 10:
        return [(0.0, duration)]
    loud = np.asarray(loudness[:n], dtype=np.float32)

    # Silence floor: relative to the stream's typical loudness, with an
    # absolute near-zero fallback so fully-muted (DMCA) sections are always
    # caught even on quiet streams.
    audible = loud[loud > 1e-5]
    typical = float(np.median(audible)) if audible.size else 0.0
    floor = max(typical * 0.06, 1e-4)
    quiet = loud < floor

    remove = _sustained(quiet, int(MIN_SILENCE))

    if motion is not None and len(motion) >= 10:
        m = np.asarray(motion[: len(loud)], dtype=np.float32)
        if m.size < loud.size:
            m = np.pad(m, (0, loud.size - m.size))
        still = m < max(float(np.median(m)) * 0.15, 1e-6)
        quiet_ish = loud < typical * 0.25
        remove |= _sustained(still & quiet_ish, int(MIN_AFK))

    # Boolean per-second mask -> padded keep ranges.
    keep: list[tuple[float, float]] = []
    for a, b in _runs(~remove):
        start = max(0.0, a - PAD) if a > 0 else 0.0
        end = min(duration, b + PAD)
        if keep and start <= keep[-1][1]:
            keep[-1] = (keep[-1][0], end)
        else:
            keep.append((start, end))
    keep = [(a, b) for a, b in keep if b - a >= MIN_KEEP]
    return keep or [(0.0, duration)]


def _sustained(mask: np.ndarray, min_len: int) -> np.ndarray:
    """Only runs of True at least min_len long survive."""
    out = np.zeros_like(mask)
    for a, b in _runs(mask):
        if b - a >= min_len:
            out[a:b] = True
    return out


def _runs(mask: np.ndarray) -> list[tuple[int, int]]:
    """[start, end) index pairs of consecutive True runs."""
    idx = np.flatnonzero(np.diff(np.concatenate(([0], mask.view(np.int8), [0]))))
    return list(zip(idx[::2], idx[1::2]))
