"""LLM-based highlight detection with strict duplicate prevention.

Scoring asks the model for 0-100 viral potential against an explicit
framework (hooks, emotional peaks, opinion bombs, revelations, conflict,
quotable lines, payoff structure), with long videos chunked and overlapped
so a moment on a chunk boundary is never lost.

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

from core import cancel, progress
from core.models import ClipCandidate, Rejection, Segment
from llm.base import LLMBackend, generate_json

PROMPT_PATH = Path(__file__).resolve().parent.parent / "config" / "prompts" / "score_clips.txt"
WINDOWS_PROMPT_PATH = Path(__file__).resolve().parent.parent / "config" / "prompts" / "score_windows.txt"


# ---- clip selection (scoring.selection) --------------------------------------
#
# Two settings, read with defaults (config/settings.yaml doesn't list them):
#
#   scoring.selection: 2 (the default) or 1. Selection 1 chooses clips as they were
#     chosen before selection 2; `selection: 1`, or a value it doesn't know, brings it back.
#     Selection 2 changes four things on a standard run. A run scored as
#     gaming (core.modes.gaming_scoring: Gaming / Reaction, or Vertical Live's
#     Gaming / reaction choice) and a Sports match keep selection 1 whatever
#     this says; any other run is a standard one, a gaming VOD on the standard
#     layout included (analysis/fusion.py decides). The four:
#       - the signal-peak and clip-direction windows are scored WINDOW_BATCH at
#         a time, each answer matched to its window by time, and a window left
#         at the neutral 50 is reported. One request for 36-84 windows ran past
#         a local model's output budget, so every window fell back to 50, and
#         a partial answer matched by position put hooks on the wrong clips;
#       - each signal pass's window budget goes to its strongest peaks, not to
#         its earliest, so a long video's late peaks can become candidates;
#       - only a transcript pick scored at or above min_score hides a signal
#         peak it overlaps;
#       - a clip's start never moves past the line it was picked on, or the
#         line its hook quotes; a start that would come in mid-sentence goes
#         back to the line the sentence starts on, when that is at most
#         SENTENCE_REACH_SECONDS earlier and within max_duration; and a signal
#         window gets up to LEAD_IN_SECONDS of complete sentences before it
#         (_fit_to_segments). End rules are unchanged.
#   scoring.longform_selection: the same choice for Longform's 16:9 clips, which
#     keep 1 until this says 2, whatever scoring.selection is (longform/process.py).
WINDOW_BATCH = 8
LEAD_IN_SECONDS = 6.0
# A line runs on from the one before it when that one has no sentence end and
# the pause between them is at most SENTENCE_PAUSE_SECONDS; a longer pause
# ends the sentence, full stop or not (going back over it would put that
# silence in the clip's first seconds). A start on a line that runs on goes
# back to the line its sentence starts on, at most SENTENCE_REACH_SECONDS. On
# a talking-head stream that reached 8 of the 11 picks whose kept line came in
# mid-sentence; the other 3 sentences ran on over 4-8 lines without a sentence
# end (8.5-16 s).
#
# A sentence end, for this rule (_ends_sentence_for_start), is one the end
# rules read (_ends_sentence: . ! ? and the ellipsis, Chinese and Japanese
# full-width ones, also inside closing quotes or brackets) or a mark they
# don't read that a language in multilingual/languages.py ends sentences with
# (_MORE_SENTENCE_ENDS): the danda and double danda (Hindi, Bengali), the
# Arabic full stop (Urdu) and question mark (Arabic, Urdu), and the full-width
# and half-width full stops (Chinese, Japanese). A transcript with none of
# them at all (speech recognition that left the punctuation out, or Thai,
# written without any) can't show where a sentence starts, so there the rule
# moves no start.
SENTENCE_REACH_SECONDS = 8.0
SENTENCE_PAUSE_SECONDS = 2.0
NEUTRAL_HOOK = "High-energy moment"  # a window's hook when the model gave it none


def selection_version(config) -> int:
    """scoring.selection from a run's settings: 2 when absent or 2; anything else (1, a blank) is 1."""
    try:
        version = ((config or {}).get("scoring") or {}).get("selection", 2)
        return 2 if not isinstance(version, bool) and float(version) == 2 else 1
    except (AttributeError, TypeError, ValueError):
        return 1


def window_options(selection: int) -> dict:
    """score_windows' keywords for a standard run's windows: none for
    selection 1 (one request for them all, as always); for selection 2,
    WINDOW_BATCH at a time, matched by time, with a warning for any window
    left at the neutral 50."""
    return {"batch": WINDOW_BATCH, "warn": True} if selection == 2 else {}


def start_rules(selection: int, c: ClipCandidate, lead_in: float = 0.0) -> dict:
    """_fit_to_segments' keywords for candidate `c`: none for selection 1; for
    selection 2, its start keeps the line it was picked on and the line its
    hook quotes, goes back to the start of its sentence when it would come in
    mid-sentence (up to SENTENCE_REACH_SECONDS), and up to `lead_in` seconds
    of setup may come before it."""
    if selection != 2:
        return {}
    hook = c.hook if c.hook and c.hook != NEUTRAL_HOOK else ""
    return {"keep_own_start": True, "hook": hook, "lead_in": lead_in,
            "sentence_reach": SENTENCE_REACH_SECONDS}


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
    guidance="",  # str, or (start, end) -> str for the part of the video a chunk covers
    events_title: str | None = None,
    selection: int = 1,  # scoring.selection; 2: start_rules for the picks (analysis/fusion.py says)
) -> tuple[list[ClipCandidate], list[Rejection]]:
    """Returns (selected clips, rejected candidates with reasons).
    `events` is an optional multimodal timeline [(second, description)] shown
    to the model alongside each chunk's transcript. `guidance` is extra
    instruction for one kind of video (the gaming profile's, analysis/gaming.py);
    empty, the prompt is exactly the standard one. `selection` 2 fits each
    pick with start_rules; 1, the default, fits them as always."""
    if not segments:
        return [], []

    prompt_template = PROMPT_PATH.read_text(encoding="utf-8")
    video_end = segments[-1].end

    chunks = _chunk_segments(segments, chunk_seconds, chunk_overlap_seconds, long_video_threshold_seconds)
    print(f"  Analyzing {len(chunks)} chunk(s) with {llm.name}...")

    candidates: list[ClipCandidate] = []
    for i, chunk in enumerate(chunks, 1):
        # One LLM call per chunk, and a 12b model on a long stream makes each
        # of those minutes long. Checking here bounds how long Cancel takes to
        # land at roughly one chunk instead of the entire analysis stage.
        cancel.check_active()
        progress.emit(stage="analyze", current=i, total=len(chunks))
        transcript_text = "\n".join(f"[{s.start:.1f} - {s.end:.1f}] {s.text}" for s in chunk)
        prompt = prompt_template.replace("{transcript}", transcript_text)
        # Guidance can depend on where in the video the chunk is (a stream that
        # changes game part way through).
        chunk_guidance = guidance(chunk[0].start, chunk[-1].end) if callable(guidance) else guidance
        prompt = prompt.replace("{mode_guidance}", _guidance_block(chunk_guidance))
        prompt = prompt.replace("{events}", _events_block(events, chunk[0].start, chunk[-1].end, events_title))
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
    candidates = [_fit_to_segments(c, segments, min_duration, max_duration, **start_rules(selection, c))
                  for c in candidates]
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
    guidance: str = "",
    events_title: str | None = None,
    labels: dict | None = None,  # window index -> a note shown with it (the game there)
    batch: int = 0,
    warn: bool = False,
) -> list[ClipCandidate]:
    """Score specific time windows (signal peaks fusion found) in one LLM
    call, so signal candidates get real text/engagement scores and grounded
    hooks instead of placeholders.

    `batch` (the gaming profile, and selection 2's windows): at most this many
    windows a call, each answer matched to its window by time. One call for 45
    windows ran past the model's output budget, and a cut-off answer left
    every window at a neutral 50.

    `warn` (selection 2): say so when a window is left at the neutral 50, so
    a fallback is never silent."""
    if not windows:
        return []
    # One call for all the windows, but on CPU that single call can take
    # minutes. Say it is happening rather than going quiet mid-analyze.
    print(f"  Scoring {len(windows)} signal-peak window(s) with the model...")
    if batch and len(windows) > batch:
        results: list[ClipCandidate] = []
        total = (len(windows) + batch - 1) // batch
        for b, i in enumerate(range(0, len(windows), batch), 1):
            progress.emit(stage="analyze", current=b, total=total)
            part = windows[i:i + batch]
            part_labels = {j - i: labels[j] for j in range(i, i + len(part)) if labels and labels.get(j)}
            results += _score_window_batch(segments, llm, part, events, guidance, events_title,
                                           part_labels, by_time=True, warn=warn)
        return results
    progress.emit(stage="analyze", current=1, total=1)
    return _score_window_batch(segments, llm, windows, events, guidance, events_title, labels,
                               by_time=bool(batch), warn=warn)


def _score_window_batch(segments, llm, windows, events, guidance, events_title, labels,
                        by_time: bool, *, warn: bool = False) -> list[ClipCandidate]:
    template = WINDOWS_PROMPT_PATH.read_text(encoding="utf-8")

    blocks = []
    for i, (start, end) in enumerate(windows):
        text = " ".join(s.text for s in segments if s.end > start and s.start < end) or "(no speech)"
        ev = _events_block(events, start, end, events_title)
        note = f" ({labels[i]})" if labels and labels.get(i) else ""
        blocks.append(f"WINDOW {i} [{start:.1f}s - {end:.1f}s]{note}:\n{text}\n{ev}".strip())
    prompt = template.replace("{windows}", "\n\n".join(blocks))
    prompt = prompt.replace("{mode_guidance}", _guidance_block(guidance))

    raw = _generate_with_retry(llm, prompt, WINDOWS_SCHEMA)
    parsed = _parse_clips_json(raw)
    if warn and parsed is None:
        # Fusion hands the windows over signal by signal, not in time order.
        span = f"{min(s for s, _e in windows):.0f}-{max(e for _s, e in windows):.0f}s"
        print(f"  Warning: the model's answer for {len(windows)} window(s) ({span}) couldn't be read; "
              "they stay at a neutral 50")
    if parsed is None:
        # Model failed — keep the windows anyway with neutral text scores;
        # their audio/visual signals still let strong moments compete.
        return [
            ClipCandidate(start=s, end=e, score=50, hook="High-energy moment", source="signal")
            for s, e in windows
        ]

    results = []
    by_index = dict(enumerate(parsed))
    if by_time:
        # The model's own start for each window, when it copied one close
        # enough; its order only when it answered every window.
        by_index = {}
        for i, (start, _end) in enumerate(windows):
            near = [c for c in parsed if abs(c.start - start) <= 2.0]
            if near:
                by_index[i] = near[0]
            elif len(parsed) == len(windows):
                by_index[i] = parsed[i]
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
    if warn:
        missed = [w for i, w in enumerate(windows) if by_index.get(i) is None]
        if missed:
            print(f"  Warning: the model didn't answer {len(missed)} of {len(windows)} window(s) "
                  f"({', '.join(f'{s:.0f}s' for s, _e in missed)}); they stay at a neutral 50")
    return results


def _events_block(events: list[tuple[float, str]] | None, start: float, end: float,
                  title: str | None = None) -> str:
    if not events:
        return ""
    lines = [f"[{sec:.0f}s] {desc}" for sec, desc in events if start <= sec <= end]
    if not lines:
        return ""
    return (title or "AUDIO/VISUAL EVENTS (from signal analysis):") + "\n" + "\n".join(lines)


def _guidance_block(guidance: str) -> str:
    """The text a prompt's {mode_guidance} becomes: nothing for standard
    scoring (so its prompt is unchanged), else a paragraph of its own."""
    return f"\n\n{guidance.strip()}" if guidance and guidance.strip() else ""


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
    priority=None,  # order to choose in, best first; the score when not given
    distinct=None,  # (candidate, kept) -> True when they show two different moments
) -> tuple[list[ClipCandidate], list[Rejection]]:
    """`distinct`: two candidates it says show two different moments are
    never one for the words they share, only for overlapping in time
    (basketball: one play a clip, and a game winner 20 s after the tying
    three shares its commentator's sentence). Without it, as always."""
    kept: list[ClipCandidate] = []
    rejections: list[Rejection] = []
    claimed_segments: set[int] = set()

    for c in sorted(candidates, key=priority or (lambda c: c.score), reverse=True):
        if c.score < min_score:
            rejections.append(Rejection(c, "below_min_score"))
            continue
        if len(kept) >= max_clips:
            rejections.append(Rejection(c, "over_limit"))
            continue

        rejection = _check_against_kept(
            c, kept, segments, claimed_segments,
            max_overlap, max_text_similarity, max_segment_reuse, distinct,
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
    distinct=None,
) -> Rejection | None:
    for k in kept:
        if c.overlap_ratio(k) > max_overlap:
            return Rejection(c, "timestamp_overlap", kept=k)
        if distinct is not None and distinct(c, k):
            continue
        if _text_similarity(c, k, segments) > max_text_similarity:
            return Rejection(c, "transcript_similarity", kept=k)

    covered = _covered_segments(c, segments)
    if covered:
        if distinct is not None:
            claimed_segments = set().union(*(_covered_segments(k, segments) for k in kept if not distinct(c, k)))
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


# The shape score_clips.txt and score_windows.txt already ask for. A cloud
# model on the user's key is held to it; Ollama is sent exactly what it always
# was (see llm.base.generate_json). _parse_clips_json still checks every item.
_CLIP = {
    "type": "object",
    "properties": {
        "start": {"type": "number"}, "end": {"type": "number"},
        "score": {"type": "number"}, "engagement": {"type": "number"},
        "trending": {"type": "boolean"},
        "hook": {"type": "string"}, "reason": {"type": "string"},
    },
    "required": ["start", "end", "score", "engagement", "trending", "hook", "reason"],
    "additionalProperties": False,
}
CLIPS_SCHEMA = {
    "type": "object",
    "properties": {"clips": {"type": "array", "items": _CLIP}},
    "required": ["clips"],
    "additionalProperties": False,
}
# The signal windows' prompt (score_windows.txt) never asks for "trending".
# Held to CLIPS_SCHEMA anyway, a cloud model (Gemma 4 on OpenRouter) stopped
# at `"trending": ` and wrote nothing but spaces to its output limit, twice
# a batch: minutes each, and every window fell back to a neutral 50.
_WINDOW = {**_CLIP, "properties": {k: v for k, v in _CLIP["properties"].items() if k != "trending"},
           "required": [k for k in _CLIP["required"] if k != "trending"]}
WINDOWS_SCHEMA = {**CLIPS_SCHEMA, "properties": {"clips": {"type": "array", "items": _WINDOW}}}


def _generate_with_retry(llm: LLMBackend, prompt: str, schema: dict = CLIPS_SCHEMA) -> str:
    raw = generate_json(llm, prompt, schema)
    if _parse_clips_json(raw) is not None:
        return raw
    # One retry with an explicit reminder — local models sometimes wrap
    # JSON in prose or markdown fences on the first attempt.
    retry_prompt = prompt + "\n\nIMPORTANT: Respond with ONLY the JSON object. No markdown, no explanation."
    return generate_json(llm, retry_prompt, schema)


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
        if not isinstance(item, dict):
            continue  # a string or a number where an entry belongs: malformed too
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


# A finished thought, as far as the transcript shows it. Closing quotes and
# brackets count — Whisper writes 〈he said "stop."〉 with the stop inside.
# Chinese transcripts use full-width punctuation and their own closing marks.
_SENTENCE_END = (".", "!", "?", "…", "。", "！", "？")


def _ends_sentence(seg) -> bool:
    text = (getattr(seg, "text", "") or "").strip().rstrip("\"')]»”’」』）】》〉")
    return text.endswith(_SENTENCE_END)


def _fit_to_segments(
    c: ClipCandidate,
    segments: list[Segment],
    min_duration: float,
    max_duration: float,
    target_duration: float | None = None,
    *,
    keep_own_start: bool = False,
    hook: str = "",
    lead_in: float = 0.0,
    sentence_reach: float = 0.0,
) -> ClipCandidate:
    """Fit a candidate to whole-sentence boundaries with a NATURAL length.

    Snaps outward to full sentences, then grows (preferring forward, to finish
    the thought) toward target_duration, capped at max_duration. The target
    defaults to the candidate's own length (so an LLM pick that was already a
    good 30s stays ~30s) but never less than a sensible clip length — this is
    what stops everything collapsing to the bare minimum. Landing on sentence
    edges gives varied natural lengths across the 10-60s range.

    Selection 2's keywords (start_rules gives them; at their defaults nothing
    below changes). The move to a fresh thought at the start skips segments
    that follow one without a full stop, and Whisper often leaves the line
    before a pause without one, so the move could skip the line the clip was
    picked for:
      keep_own_start: the move never passes the segment the candidate starts in;
      hook: nor the segment its hook is quoted from, when it quotes one;
      sentence_reach: a start on a segment that runs on from the one before it
        (no sentence end there, a pause of at most SENTENCE_PAUSE_SECONDS), as
        the segments above often do, goes back to the segment its sentence
        starts on, when that is at most this many seconds earlier and the clip
        stays within max_duration; otherwise the start stays where it is, as
        it does in a transcript with no sentence end at all;
      lead_in: then up to this many seconds of complete sentences before the
        candidate's start come first (a signal window's setup), within
        max_duration.
    The end is chosen exactly as without them.
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

    # Land the END on a finished thought. Whisper splits segments on PAUSES,
    # not grammar, so a segment boundary is often mid-sentence — that is how
    # clips ended on "...manufactured in a fighter jet hangar in" and simply
    # stopped. Reach forward to the next sentence end if the cap allows,
    # otherwise fall back to the last one that still leaves a usable clip.
    # Transcripts with no punctuation at all (fast casual speech) match
    # neither and are left exactly as they were.
    if not _ends_sentence(segments[hi]):
        j = hi
        while (
            j + 1 < len(segments)
            and not _ends_sentence(segments[j])
            and (segments[j + 1].end - segments[lo].start) <= max_duration
        ):
            j += 1
        if _ends_sentence(segments[j]):
            hi = j
        else:
            k = hi
            while k > lo and not _ends_sentence(segments[k]):
                k -= 1
            if k > lo and (segments[k].end - segments[lo].start) >= min_duration:
                hi = k

    # Selection 2: the furthest in the move below may take the start.
    limit = None
    if keep_own_start or hook:
        limit = _start_limit(segments, idxs[0] if keep_own_start else None, lo, hi, hook)

    # Start on a fresh thought too, but only by moving INWARD: growing
    # backwards would pull in unrelated talk just to find a full stop.
    if lo > 0 and not _ends_sentence(segments[lo - 1]):
        k = lo
        while k < hi and not _ends_sentence(segments[k - 1]):
            k += 1
        if k < hi and (segments[hi].end - segments[k].start) >= min_duration:
            lo = k

    if limit is not None and lo > limit:
        lo = limit
    if sentence_reach > 0:
        lo = _sentence_start(segments, lo, hi, sentence_reach, max_duration)
    if lead_in > 0:
        lo = _lead_in_start(segments, lo, hi, c.start, lead_in, max_duration)

    start = segments[lo].start
    end = segments[hi].end

    # Everything above moves in whole segments, which silently assumes the
    # transcript offers a boundary near where one is needed. Twice it does
    # not, and both failures shipped real clips from a gym stream:
    #
    #   TOO SHORT — "Yo." runs 9.35-9.87 and the next segment is at 74.26.
    #   Growing forward would make a 66s clip (over the cap) and there is
    #   nothing behind it, so the loop gives up holding 0.52 seconds.
    #
    #   TOO LONG — Whisper emits one 153s segment for a long unpunctuated
    #   stretch. With lo == hi the trailing-trim loop cannot run (it needs
    #   hi > lo), so the whole segment ships as one clip.
    #
    # Sentence boundaries are a preference. The duration range is a promise.
    # When they conflict, the clock wins: a clip landing mid-sentence is a
    # blemish, while half a second is not a clip and 153 seconds is not a
    # short. Both fall back to clock time, anchored on the candidate — it
    # marks where the actual moment was, which a 153s segment does not.
    if end - start > max_duration:
        focus = min(max(c.start, start), end)
        start = max(start, focus - max_duration * 0.2)  # a little lead-in
        end = min(end, start + max_duration)

    if end - start < min_duration:
        video_end = max(segments[-1].end, c.end)
        # Prefer growing forward — an action beat plays out after its peak.
        # Pull the start back only when there is not enough room ahead.
        end = min(video_end, start + min_duration)
        if end - start < min_duration:
            start = max(0.0, end - min_duration)

    c.start = round(start, 2)
    c.end = round(end, 2)
    return c


# ---- selection 2's start rules (_fit_to_segments' keywords) -------------------

# A hook's words, with each Chinese or Japanese character a word of its own:
# written without spaces, a whole clause is otherwise a single \w+ "word".
_CJK = "\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff"
_HOOK_WORD = re.compile(rf"[{_CJK}]|[^\W{_CJK}]+")


def _hook_words(text: str) -> list[str]:
    return _HOOK_WORD.findall((text or "").casefold())


def _start_limit(segments: list[Segment], own: int | None, lo: int, hi: int, hook: str) -> int | None:
    """The furthest segment the start may move in to: `own` (the one the
    candidate starts in), or the one of segments[lo..hi] its hook is quoted
    from if that comes first. None when neither applies."""
    quoted = _hook_segment(segments, hook, lo, hi) if hook else None
    limits = [i for i in (own, quoted) if i is not None]
    return min(limits) if limits else None


def _hook_segment(segments: list[Segment], hook: str, lo: int, hi: int) -> int | None:
    """The first of segments[lo..hi] the hook quotes: where a run of its words
    (at least 3, and at least a third of them) is said in a row. None when it
    quotes none of them: a paraphrase, or too short to tell."""
    words = _hook_words(hook)
    need = max(3, -(-len(words) // 3))
    if len(words) < need:
        return None
    said = [(w, i) for i in range(lo, hi + 1) for w in _hook_words(segments[i].text)]
    for p in range(len(said) - need + 1):
        for q in range(len(words) - need + 1):
            if all(said[p + n][0] == words[q + n] for n in range(need)):
                return said[p][1]
    return None


# The sentence ends the start rule reads besides _ends_sentence's: the ones the
# languages in multilingual/languages.py end sentences with that it doesn't
# read. _ends_sentence keeps its own: the end rules, and selection 1, use it.
_MORE_SENTENCE_ENDS = (
    "\N{DEVANAGARI DANDA}",  # Hindi, Bengali
    "\N{DEVANAGARI DOUBLE DANDA}",  # Hindi, Bengali
    "\N{ARABIC FULL STOP}",  # Urdu
    "\N{ARABIC QUESTION MARK}",  # Arabic, Urdu
    "\N{FULLWIDTH FULL STOP}",  # Chinese, Japanese
    "\N{HALFWIDTH IDEOGRAPHIC FULL STOP}",  # Japanese
)


def _ends_sentence_for_start(seg) -> bool:
    """seg ends a sentence, as the start rule reads it (see SENTENCE_REACH_SECONDS):
    _ends_sentence, or one of _MORE_SENTENCE_ENDS, also inside the closing
    quotes and brackets _ends_sentence looks past."""
    if _ends_sentence(seg):
        return True
    text = (getattr(seg, "text", "") or "").strip().rstrip("\"')]»”’」』）】》〉")
    return text.endswith(_MORE_SENTENCE_ENDS)


def _runs_on(segments: list[Segment], j: int) -> bool:
    """segments[j] carries on the sentence of the segment before it: that one
    has no sentence end (_ends_sentence_for_start), and segments[j] follows it
    within SENTENCE_PAUSE_SECONDS."""
    return (j > 0 and not _ends_sentence_for_start(segments[j - 1])
            and segments[j].start - segments[j - 1].end <= SENTENCE_PAUSE_SECONDS)


def _sentence_start(segments: list[Segment], lo: int, hi: int, reach: float, max_duration: float) -> int:
    """Where the sentence segments[lo] is part of starts: back over each
    segment that runs on from the one before it (_runs_on). That segment when
    it is at most `reach` seconds before segments[lo] and segments[it..hi]
    stays within max_duration; otherwise `lo`, as when segments[lo] starts its
    sentence itself. Never a segment part of the way back: a start there would
    still come in mid-sentence. Also `lo` when no segment of the transcript
    ends a sentence: unpunctuated, every line runs on from the one before it
    unless a long pause comes between, and where a sentence starts can't be
    told."""
    j = lo
    while _runs_on(segments, j):
        j -= 1
        too_far = segments[lo].start - segments[j].start > reach
        if too_far or segments[hi].end - segments[j].start > max_duration:
            return lo
    if j < lo and not any(_ends_sentence_for_start(s) for s in segments):
        return lo
    return j


def _lead_in_start(segments: list[Segment], lo: int, hi: int, start: float, lead_in: float,
                   max_duration: float) -> int:
    """The earliest segment before `lo` that begins a complete sentence (the
    first one, or one after a sentence end) no more than `lead_in` seconds
    before `start`, keeping segments[it..hi] within max_duration; `lo` when
    there is none."""
    best = lo
    j = lo - 1
    while j >= 0 and segments[j].start >= start - lead_in:
        if segments[hi].end - segments[j].start > max_duration:
            break
        if j == 0 or _ends_sentence(segments[j - 1]):
            best = j
        j -= 1
    return best
