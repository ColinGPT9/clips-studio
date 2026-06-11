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

from core.models import ClipCandidate, Rejection, Segment
from llm.base import LLMBackend

PROMPT_PATH = Path(__file__).resolve().parent.parent / "config" / "prompts" / "score_clips.txt"


def find_highlights(
    segments: list[Segment],
    llm: LLMBackend,
    *,
    min_score: int = 60,
    max_clips: int = 3,
    min_duration: float = 15.0,
    max_duration: float = 59.0,
    max_overlap: float = 0.4,
    max_text_similarity: float = 0.7,
    max_segment_reuse: float = 0.4,
    chunk_seconds: float = 1200.0,
    chunk_overlap_seconds: float = 60.0,
    long_video_threshold_seconds: float = 1800.0,
) -> tuple[list[ClipCandidate], list[Rejection]]:
    """Returns (selected clips, rejected candidates with reasons)."""
    if not segments:
        return [], []

    prompt_template = PROMPT_PATH.read_text(encoding="utf-8")
    video_end = segments[-1].end

    chunks = _chunk_segments(segments, chunk_seconds, chunk_overlap_seconds, long_video_threshold_seconds)
    print(f"  Analyzing {len(chunks)} chunk(s) with {llm.name}...")

    candidates: list[ClipCandidate] = []
    for i, chunk in enumerate(chunks, 1):
        transcript_text = "\n".join(f"[{s.start:.1f} - {s.end:.1f}] {s.text}" for s in chunk)
        prompt = prompt_template.replace("{transcript}", transcript_text)
        raw = _generate_with_retry(llm, prompt)
        parsed = _parse_clips_json(raw)
        if parsed is None:
            snippet = " ".join(raw.split())[:150]
            print(f"  Chunk {i}/{len(chunks)}: unparseable LLM output, skipping chunk (got: {snippet!r})")
            continue
        print(f"  Chunk {i}/{len(chunks)}: {len(parsed)} candidate(s)")
        candidates.extend(parsed)

    candidates = [c for c in candidates if _valid_range(c, video_end)]
    candidates = [_snap_to_segments(c, segments) for c in candidates]
    candidates = [_enforce_duration(c, min_duration, max_duration, video_end) for c in candidates]
    candidates = [c for c in candidates if c.duration >= min_duration]

    return _select_unique(
        candidates,
        segments,
        min_score=min_score,
        max_clips=max_clips,
        max_overlap=max_overlap,
        max_text_similarity=max_text_similarity,
        max_segment_reuse=max_segment_reuse,
    )


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
            clips.append(
                ClipCandidate(
                    start=float(item["start"]),
                    end=float(item["end"]),
                    score=max(0, min(100, int(item["score"]))),
                    hook=str(item.get("hook", "")),
                    reason=str(item.get("reason", "")),
                )
            )
        except (KeyError, TypeError, ValueError):
            continue  # drop malformed entries, keep the rest
    return clips


# ---- candidate normalization ------------------------------------------------


def _valid_range(c: ClipCandidate, video_end: float) -> bool:
    return 0 <= c.start < c.end <= video_end + 5  # small slack for rounding


def _snap_to_segments(c: ClipCandidate, segments: list[Segment]) -> ClipCandidate:
    """Snap start/end to the nearest segment boundary so clips begin and end
    on sentence edges instead of mid-word."""
    c.start = min((s.start for s in segments), key=lambda t: abs(t - c.start))
    c.end = min((s.end for s in segments), key=lambda t: abs(t - c.end))
    return c


def _enforce_duration(
    c: ClipCandidate, min_duration: float, max_duration: float, video_end: float
) -> ClipCandidate:
    if c.duration > max_duration:
        c.end = c.start + max_duration
    elif c.duration < min_duration:
        # Extend symmetrically, clamped to the video bounds.
        deficit = min_duration - c.duration
        c.start = max(0.0, c.start - deficit / 2)
        c.end = min(video_end, c.start + min_duration)
    return c
