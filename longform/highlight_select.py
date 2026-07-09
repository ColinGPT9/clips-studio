"""Highlight selection: scored candidates -> a budgeted, well-paced cut list.

Not a plain concatenation of top clips. Three editorial rules:
  * CHRONOLOGICAL order — the highlight tells the stream's story in
    sequence, never jumping backwards.
  * SPREAD — the stream is split into sections and no section may dominate
    the budget, so one hot 10 minutes can't crowd out the rest of the show.
  * QUALITY-FIRST fill — within those constraints, the highest-scored
    moments win; every candidate already starts/ends on sentence
    boundaries via the existing fitting, so cuts land on natural speech.
"""

from core.models import ClipCandidate

SECTIONS = 8          # spread buckets across the stream
SECTION_SHARE = 0.22  # max budget share from one bucket (a hot streak that
                      # straddles a bucket boundary can reach ~2x this — still
                      # under half the video, which keeps the cut balanced)
OVERSHOOT = 1.08      # allow the last moment to run slightly past target
MERGE_GAP = 2.0       # selected moments closer than this merge into one cut


def select_highlights(
    candidates: list[ClipCandidate],
    target_seconds: float,
    stream_duration: float,
) -> list[tuple[float, float]]:
    """Chronological (start, end) keep-ranges totalling ~target_seconds."""
    if not candidates:
        return []
    section_len = max(1.0, stream_duration / SECTIONS)
    section_cap = target_seconds * SECTION_SHARE
    section_used = [0.0] * SECTIONS

    chosen: list[ClipCandidate] = []
    total = 0.0
    for c in sorted(candidates, key=lambda c: c.score, reverse=True):
        length = c.end - c.start
        if total + length > target_seconds * OVERSHOOT:
            continue
        sec = min(SECTIONS - 1, int(c.start / section_len))
        if section_used[sec] + length > section_cap:
            continue  # this part of the stream already has its share
        chosen.append(c)
        section_used[sec] += length
        total += length
        if total >= target_seconds:
            break

    # Story order + merge near-adjacent picks (a 1s gap would be a jump cut).
    chosen.sort(key=lambda c: c.start)
    ranges: list[tuple[float, float]] = []
    for c in chosen:
        if ranges and c.start - ranges[-1][1] < MERGE_GAP:
            ranges[-1] = (ranges[-1][0], max(ranges[-1][1], c.end))
        else:
            ranges.append((c.start, c.end))
    return ranges
