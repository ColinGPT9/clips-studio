"""LLM-based highlight detection with strict duplicate prevention.

Scoring methodology follows SamurAIGPT/AI-Youtube-Shorts-Generator:
virality framework, 0-100 scores, long videos chunked with overlap.

Duplicate prevention is three independent checks, applied highest-score-first:
  1. timestamp overlap   — reject if >40% of the shorter clip overlaps a kept clip
  2. transcript similarity — reject if the spoken text is >70% similar to a kept clip
  3. segment reuse       — reject if >40% of the candidate's transcript segments
                           are already claimed by kept clips
Every rejection carries a reason so the pipeline can log it for auditing.

All robustness (JSON parsing, timestamp validation, duration enforcement)
lives here in deterministic Python, NOT in the LLM backend — so swapping
models never changes this module.
"""

import json
import re
from difflib import SequenceMatcher
from pathlib import Path

from core import progress
from core.models import ClipCandidate, Rejection, Segment
from llm.base import LLMBackend

PROMPT_PATH = Path(__file__).resolve().parent.parent / "config" / "prompts" / "score_clips.txt"
WINDOWS_PROMPT_PATH = Path(__file__).resolve().parent.parent / "config" / "prompts" / "score_windows.txt"


def find_highlights(
    segments: list[Segment],
    llm: LLMBackend,
    *,
    min_score: int = 60,
    max_clips: int = 3,
    min_duration: float = 10.0,
    max_duration: float = 60.0,
    max_overlap: float = 0.4,
    max_text_similarity: float = 0.7,
    max_segment_reuse: float = 0.4,
    chunk_seconds: float = 1200.0,
    chunk_overlap_seconds: float = 60.0,
    long_video_threshold_seconds: float = 1800.0,
    events: list[tuple[float, str]] | None = None,
) -> tuple[list[ClipCandidate], list[Rejection]]:
    """Returns (selected clips, rejected candidates with reasons).
    `events` is an optional multimodal timeline [(second, description)] shown
    to the model alongside each chunk's transcript."""
    if not segments:
        return [], []

    prompt_template = PROMPT_PATH.read_text(encoding="utf-8")
    video_end = segments[-1].end

    chunks = _chunk_segments(segments, chunk_seconds, chunk_overlap_seconds, long_video_threshold_seconds)
    print(f"  Analyzing {len(chunks)} chunk(s) with {llm.name}...")

    candidates: list[ClipCandidate] = []
    for i, chunk in enumerate(chunks, 1):
        progress.emit(stage="analyze", current=i, total=len(chunks))
        transcript_text = "\n".join(f"[{s.start:.1f} - {s.end:.1f}] {s.text}" for s in chunk)
        prompt = prompt_template.replace("{transcript}", transcript_text)
        prompt = prompt.replace("{events}", _events_block(events, chunk[0].start, chunk[-1].end))
        prompt = prompt.replace("{min_duration}", str(int(min_duration)))
        prompt = prompt.replace("{max_duration}", str(int(max_duration)))
        raw = _generate_with_retry(llm, prompt)
        parsed = _parse_clips_json(raw)
        if parsed is None:
            snippet = " ".join(raw.split())[:150]
            print(f"  Chunk {i}/{len(chunks)}: unparseable LLM output, skipping chunk (got: {snippet!r})")
            continue
        print(f"  Chunk {i}/{len(chunks)}: {len(parsed)} candidate(s)")
        candidates.extend(parsed)

    candidates = [c for c in candidates if _valid_range(c, video_end)]
    candidates = [_fit_to_segments(c, segments, min_duration, max_duration) for c in candidates]
    candidates = [c for c in candidates if c.duration >= min_duration - 1]

    return _select_unique(
        candidates,
        segments,
        min_score=min_score,
        max_clips=max_clips,
        max_overlap=max_overlap,
        max_text_similarity=max_text_similarity,
        max_segment_reuse=max_segment_reuse,
    )


def score_windows(
    segments: list[Segment],
    llm: LLMBackend,
    windows: list[tuple[float, float]],
    events: list[tuple[float, str]] | None = None,
) -> list[ClipCandidate]:
    """Score specific time windows (signal peaks fusion found) in one LLM
    call, so signal candidates get real text/engagement scores and grounded
    hooks instead of placeholders."""
    if not windows:
        return []
    template = WINDOWS_PROMPT_PATH.read_text(encoding="utf-8")

    blocks = []
    for i, (start, end) in enumerate(windows):
        text = " ".join(s.text for s in segments if s.end > start and s.start < end) or "(no speech)"
        ev = _events_block(events, start, end)
        blocks.append(f"WINDOW {i} [{start:.1f}s - {end:.1f}s]:\n{text}\n{ev}".strip())
    prompt = template.replace("{windows}", "\n\n".join(blocks))

    raw = _generate_with_retry(llm, prompt)
    parsed = _parse_clips_json(raw)
    if parsed is None:
        # Model failed — keep the windows anyway with neutral text scores;
        # their audio/visual signals still let strong moments compete.
        return [
            ClipCandidate(start=s, end=e, score=50, hook="High-energy moment", source="signal")
            for s, e in windows
        ]

    results = []
    by_index = {i: c for i, c in enumerate(parsed)}
    for i, (start, end) in enumerate(windows):
        c = by_index.get(i)
        if c is not None:
            # Trust the model's score/hook but keep OUR window timestamps —
            # these came from the signals, not from the model.
            results.append(ClipCandidate(start=start, end=end, score=c.score, hook=c.hook,
                                         reason=c.reason, source="signal", engagement=c.engagement))
        else:
            results.append(ClipCandidate(start=start, end=end, score=50,
                                         hook="High-energy moment", source="signal"))
    return results


def _events_block(events: list[tuple[float, str]] | None, start: float, end: float) -> str:
    if not events:
        return ""
    lines = [f"[{sec:.0f}s] {desc}" for sec, desc in events if start <= sec <= end]
    if not lines:
        return ""
    return "AUDIO/VISUAL EVENTS (from signal analysis):\n" + "\n".join(lines)


# ---- duplicate prevention ------------------------------------------------


def _select_unique(
    candidates: list[ClipCandidate],
    segments: list[Segment],
    *,
    min_score: int,
    max_clips: int,
    max_overlap: float,
    max_text_similarity: float,
    max_segment_reuse: float,
) -> tuple[list[ClipCandidate], list[Rejection]]:
    kept: list[ClipCandidate] = []
    rejections: list[Rejection] = []
    claimed_segments: set[int] = set()

    for c in sorted(candidates, key=lambda c: c.score, reverse=True):
        if c.score < min_score:
            rejections.append(Rejection(c, "below_min_score"))
            continue
        if len(kept) >= max_clips:
            rejections.append(Rejection(c, "over_limit"))
            continue

        rejection = _check_against_kept(
            c, kept, segments, claimed_segments,
            max_overlap, max_text_similarity, max_segment_reuse,
        )
        if rejection:
            rejections.append(rejection)
            continue

        kept.append(c)
        claimed_segments |= _covered_segments(c, segments)

    return kept, rejections


def _check_against_kept(
    c: ClipCandidate,
    kept: list[ClipCandidate],
    segments: list[Segment],
    claimed_segments: set[int],
    max_overlap: float,
    max_text_similarity: float,
    max_segment_reuse: float,
) -> Rejection | None:
    for k in kept:
        if c.overlap_ratio(k) > max_overlap:
            return Rejection(c, "timestamp_overlap", kept=k)
        if _text_similarity(c, k, segments) > max_text_similarity:
            return Rejection(c, "transcript_similarity", kept=k)

    covered = _covered_segments(c, segments)
    if covered:
        reuse = len(covered & claimed_segments) / len(covered)
        if reuse > max_segment_reuse:
            return Rejection(c, "segment_reuse")
    return None


def _covered_segments(c: ClipCandidate, segments: list[Segment]) -> set[int]:
    """Indices of transcript segments that fall inside the clip window."""
    return {
        i for i, s in enumerate(segments)
        if s.end > c.start and s.start < c.end
    }


def _clip_text(c: ClipCandidate, segments: list[Segment]) -> str:
    return " ".join(s.text for s in segments if s.end > c.start and s.start < c.end)


def _text_similarity(a: ClipCandidate, b: ClipCandidate, segments: list[Segment]) -> float:
    """Content-level duplicate check: catches the model re-proposing the same
    moment with shifted boundaries (low timestamp overlap, same words)."""
    text_a, text_b = _clip_text(a, segments), _clip_text(b, segments)
    if not text_a or not text_b:
        return 0.0
    return SequenceMatcher(None, text_a, text_b).ratio()


# ---- chunking --------------------------------------------------------------


def _chunk_segments(
    segments: list[Segment],
    chunk_seconds: float,
    overlap_seconds: float,
    long_threshold: float,
) -> list[list[Segment]]:
    total = segments[-1].end
    if total <= long_threshold:
        return [segments]

    chunks = []
    start = 0.0
    while start < total:
        end = start + chunk_seconds
        chunk = [s for s in segments if s.end > start and s.start < end]
        if chunk:
            chunks.append(chunk)
        start = end - overlap_seconds
    return chunks


# ---- LLM I/O robustness -----------------------------------------------------


def _generate_with_retry(llm: LLMBackend, prompt: str) -> str:
    raw = llm.generate(prompt, json_mode=True)
    if _parse_clips_json(raw) is not None:
        return raw
    # One retry with an explicit reminder — local models sometimes wrap
    # JSON in prose or markdown fences on the first attempt.
    retry_prompt = prompt + "\n\nIMPORTANT: Respond with ONLY the JSON object. No markdown, no explanation."
    return llm.generate(retry_prompt, json_mode=True)


def _parse_clips_json(raw: str) -> list[ClipCandidate] | None:
    """Tolerant parse: strips code fences, finds the outermost JSON object.
    Returns None if nothing usable, [] if the model validly found no clips."""
    text = raw.strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text)

    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        return None
    try:
        data = json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return None

    clips_raw = data.get("clips")
    if not isinstance(clips_raw, list):
        return None

    clips = []
    for item in clips_raw:
        try:
            engagement = item.get("engagement")
            clips.append(
                ClipCandidate(
                    start=float(item["start"]),
                    end=float(item["end"]),
                    score=max(0, min(100, int(item["score"]))),
                    hook=str(item.get("hook", "")),
                    reason=str(item.get("reason", "")),
                    engagement=max(0, min(100, int(engagement))) if engagement is not None else None,
                    trending=bool(item.get("trending", False)),
                )
            )
        except (KeyError, TypeError, ValueError):
            continue  # drop malformed entries, keep the rest
    return clips


# ---- candidate normalization ------------------------------------------------


def _valid_range(c: ClipCandidate, video_end: float) -> bool:
    return 0 <= c.start < c.end <= video_end + 5  # small slack for rounding


def _fit_to_segments(
    c: ClipCandidate,
    segments: list[Segment],
    min_duration: float,
    max_duration: float,
    target_duration: float | None = None,
) -> ClipCandidate:
    """Fit a candidate to whole-sentence boundaries with a NATURAL length.

    Snaps outward to full sentences, then grows (preferring forward, to finish
    the thought) toward target_duration, capped at max_duration. The target
    defaults to the candidate's own length (so an LLM pick that was already a
    good 30s stays ~30s) but never less than a sensible clip length — this is
    what stops everything collapsing to the bare minimum. Landing on sentence
    edges gives varied natural lengths across the 10-60s range.
    """
    if not segments:
        return c
    idxs = [i for i, s in enumerate(segments) if s.end > c.start and s.start < c.end]
    if not idxs:  # candidate fell between segments — anchor to the nearest one
        idxs = [min(range(len(segments)), key=lambda i: abs(segments[i].start - c.start))]
    lo, hi = idxs[0], idxs[-1]

    def dur() -> float:
        return segments[hi].end - segments[lo].start

    # Aim for the candidate's own span, but at least a real clip length (18s),
    # so short LLM picks and tiny signal peaks grow into watchable clips.
    target = target_duration if target_duration is not None else max(c.end - c.start, 18.0)
    target = max(min_duration, min(target, max_duration))

    while dur() < target:
        grew = False
        if hi + 1 < len(segments) and (segments[hi + 1].end - segments[lo].start) <= max_duration:
            hi += 1
            grew = True
        elif lo > 0 and (segments[hi].end - segments[lo - 1].start) <= max_duration:
            lo -= 1
            grew = True
        if not grew:
            break
    # Trim whole trailing sentences if somehow over the cap.
    while dur() > max_duration and hi > lo:
        hi -= 1

    c.start = round(segments[lo].start, 2)
    c.end = round(segments[hi].end, 2)
    return c
