"""Multimodal fusion: combines transcript, audio, visual, and reaction
signals into one 0-100 clip score.

    final = w_text*text + w_visual*visual + w_reaction*reaction
          + w_audio*audio + w_engagement*engagement      (weights in config)

Candidates come from two pools:
  1. transcript pool — Gemma's picks (analysis/highlights.py), now informed
     by an EVENTS timeline built from the signals
  2. signal pool — windows where combined audio+visual excitement exceeds
     the configured percentile; their transcript text is scored by Gemma in
     one extra call so they compete on equal footing

After dedup, the top finalists go through a RERANK pass: Gemma orders them
against each other (relative judgment — far more reliable for small local
models than absolute 0-100 scoring, which clusters).
"""

import json
import re
from pathlib import Path

import numpy as np

from analysis import highlights
from analysis.audio_features import extract_audio_features
from core import progress
from analysis.visual_features import extract_visual_features, reaction_for_window
from core.models import ClipCandidate, Rejection, Segment
from llm.base import LLMBackend

RERANK_PROMPT_PATH = Path(__file__).resolve().parent.parent / "config" / "prompts" / "rerank.txt"


def find_clips(
    video_path: Path,
    segments: list[Segment],
    llm: LLMBackend,
    config: dict,
    signals: tuple[dict, dict] | None = None,
    creator_context=None,  # creator.retrieval.CreatorContext | None
    weight_bias: dict | None = None,  # per-channel multipliers from creator.learning
) -> tuple[list[ClipCandidate], list[Rejection]]:
    clips_cfg = config["clips"]
    analysis_cfg = config["analysis"]
    scoring_cfg = config.get("scoring", {})
    weights = scoring_cfg.get(
        "weights",
        {"text": 0.30, "visual": 0.20, "reaction": 0.20, "audio": 0.20, "engagement": 0.10},
    )
    if weight_bias:
        # Learned from the user's own keep/edit/export behavior for THIS
        # creator — bounded (max 20% shift per channel) and renormalized.
        from creator.learning import apply_bias

        weights = apply_bias(weights, weight_bias)
        print("  Using learned scoring preferences for this creator")

    # ---- 1. extract + normalize signals --------------------------------
    progress.emit(stage="signals")
    if signals is not None:
        # Precomputed by the pipeline in the background while Whisper was
        # transcribing — this stage costs nothing on that path.
        audio_raw, visual_raw = signals
        print("  Using audio/visual signals precomputed during transcription")
    else:
        print("  Extracting audio signals...")
        audio_raw = extract_audio_features(video_path)
        print("  Extracting visual signals...")
        visual_raw = extract_visual_features(video_path)

    audio_excitement = _combine([_pct(audio_raw[k]) for k in ("spike", "burst", "noisiness") if k in audio_raw])
    visual_activity = _combine([_pct(visual_raw[k]) for k in ("motion", "scene_cut", "flash") if k in visual_raw])
    combined = _combine([a for a in (audio_excitement, visual_activity) if a.size])

    events = _build_events(audio_excitement, visual_activity, visual_raw)
    if events:
        print(f"  {len(events)} notable audio/visual events detected")

    # ---- 2. candidate pools ---------------------------------------------
    candidates, _ = highlights.find_highlights(
        segments, llm,
        min_score=0,  # fusion owns thresholding now
        max_clips=999,
        min_duration=clips_cfg["min_duration"],
        max_duration=clips_cfg["max_duration"],
        max_overlap=1.1,  # fusion owns dedup too — keep all for scoring
        max_text_similarity=1.1,
        max_segment_reuse=1.1,
        chunk_seconds=analysis_cfg["chunk_seconds"],
        chunk_overlap_seconds=analysis_cfg["chunk_overlap_seconds"],
        long_video_threshold_seconds=analysis_cfg["long_video_threshold_seconds"],
        events=events,
    )

    # Candidate windows from signal peaks — detected PER MODALITY, not just
    # their mean. Averaging audio+visual hides content that is strong in only
    # ONE of them: a SILENT workout is visual-only, and the mean dilutes it
    # below the peak cutoff so it never becomes a candidate at all. Scanning
    # visual-alone and audio-alone peaks is what surfaces silent action (and
    # talk-free hype) for the scorer to judge. Dedup accumulates across all
    # three passes so the same moment isn't proposed twice.
    pk_pct = scoring_cfg.get("signal_peak_percentile", 90)
    peak_windows: list[tuple[float, float]] = []
    seen = list(candidates)
    for sig in (visual_activity, audio_excitement, combined):
        for win in _signal_peak_windows(
            sig, segments,
            percentile=pk_pct,
            min_duration=clips_cfg["min_duration"],
            max_duration=clips_cfg["max_duration"],
            existing=seen,
        ):
            peak_windows.append(win)
            seen.append(ClipCandidate(start=win[0], end=win[1], score=0))
    if peak_windows:
        print(f"  {len(peak_windows)} signal-peak window(s) found beyond transcript picks "
              f"(per-modality: visual/audio/combined)")
        signal_cands = highlights.score_windows(segments, llm, peak_windows, events=events)
        # Signal peaks are seeded tight around the hot moment — grow them to a
        # full ~25s clip on sentence boundaries so action moments aren't tiny.
        signal_cands = [
            highlights._fit_to_segments(
                c, segments, clips_cfg["min_duration"], clips_cfg["max_duration"], target_duration=25.0
            )
            for c in signal_cands
        ]
        candidates += signal_cands

    if not candidates:
        return [], []

    # ---- 3. fused scoring ------------------------------------------------
    for c in candidates:
        text = c.score / 100.0
        engagement = (c.engagement if c.engagement is not None else c.score) / 100.0
        audio = _window_mean(audio_excitement, c.start, c.end)
        visual = _window_mean(visual_activity, c.start, c.end)
        c.subscores = {
            "text": round(text * 100),
            "audio": round(audio * 100),
            "visual": round(visual * 100),
            "reaction": 50,  # placeholder until the per-window pass below
            "engagement": round(engagement * 100),
            "source": c.source,
        }

    # Reaction (YOLO per window) is the expensive signal — compute it only
    # for candidates that need it; the rest keep a neutral 0.5.
    speech = {id(c): _speech_ratio(c, segments) for c in candidates}
    top_k = max(scoring_cfg.get("reaction_top_k", 8), min(24, len(candidates) // 3))
    provisional = sorted(
        candidates, key=lambda c: _fuse(c, weights, 0.5, speech[id(c)]), reverse=True
    )
    react_set = list(provisional[:top_k])
    # ALSO measure every visually-active candidate (motion present). Reaction
    # — is a prominent person mid-action? — is the DECIDING signal for a
    # workout/action clip and the gate for the action bonus, but with a
    # neutral 0.5 those clips rank low and get pre-filtered out before it is
    # ever measured. That catch-22 is why workouts never surfaced: they were
    # rejected on a reaction score that was never taken.
    in_set = {id(c) for c in react_set}
    for c in provisional[top_k:]:
        if c.subscores.get("visual", 0) >= 40 and id(c) not in in_set:
            react_set.append(c)
            in_set.add(id(c))
    n_reactions = len(react_set)
    print(f"  Scoring reactions for {n_reactions} candidate(s) "
          f"(incl. silent-action clips)...")
    for ri, c in enumerate(react_set, 1):
        progress.emit(stage="reactions", current=ri, total=n_reactions)
        r = reaction_for_window(
            video_path, c.start, c.end,
            audio_excitement=_window_mean(audio_excitement, c.start, c.end),
            detector=config["tracking"]["detector"],
        )
        c.subscores["reaction"] = round(r * 100)

    ctx_cap = int(scoring_cfg.get("creator_context_max", 6))
    action_bonus = int(scoring_cfg.get("action_bonus", 10))
    n_context = 0
    n_action = 0
    for c in candidates:
        fused = round(100 * _fuse(c, weights, c.subscores["reaction"] / 100.0, speech[id(c)]))
        # Trending/drama moments (a creator/celebrity named, beef, controversy)
        # ride existing attention — give them a meaningful boost.
        if c.trending:
            fused = min(100, fused + 10)
            c.subscores["trending"] = True
        # Active-content archetype (workouts, sports, dance): a person
        # prominently MOVING performs well on social whether or not they're
        # talking — the value is the action, which the scorer under-credits
        # (casual workout narration grades as mediocre chatter), so it lands
        # right at the threshold. Surface more of it with a capped additive
        # nudge. Gated on on-screen person + motion — NOT on silence, since
        # creators often narrate while they work out — so a static talking
        # head is not promoted. Tunable via scoring.action_bonus.
        if (
            action_bonus > 0
            and c.subscores.get("reaction", 50) >= 55
            and c.subscores.get("visual", 0) >= 40
        ):
            fused = min(100, fused + action_bonus)
            c.subscores["action"] = action_bonus
            n_action += 1
        # Creator-context callback (open storyline, catchphrase, collaborator):
        # a small ADDITIVE-ONLY nudge, hard-capped, from deterministic matching
        # against learned knowledge. Zero when nothing is known — cannot
        # degrade scoring for creators without (or with bad) knowledge.
        if creator_context is not None:
            from creator.retrieval import context_bonus

            clip_text = " ".join(
                s.text for s in segments if s.end > c.start and s.start < c.end
            )
            b, reasons = context_bonus(clip_text, creator_context, cap=ctx_cap)
            if b:
                fused = min(100, fused + b)
                c.subscores["context"] = b
                c.subscores["context_why"] = "; ".join(reasons)
                n_context += 1
        c.score = fused
    if n_context:
        print(f"  Creator context boosted {n_context} candidate(s) (max +{ctx_cap})")
    if n_action:
        print(f"  Active-content boosted {n_action} candidate(s) (+{action_bonus})")

    # ---- 4. dedup + threshold (reusing the proven logic) ------------------
    # max_clips_per_video == 0 means automatic: keep EVERY unique clip that
    # passes the quality bar — the bar (min_score) decides, not a count.
    # A 2-hour stream SHOULD yield far more clips than a 20-minute video.
    max_clips = clips_cfg.get("max_clips_per_video", 0)
    selection_cap = max_clips if max_clips > 0 else len(candidates)
    finalists, rejections = highlights._select_unique(
        candidates, segments,
        min_score=clips_cfg["min_score"],
        max_clips=selection_cap,
        max_overlap=analysis_cfg["max_overlap"],
        max_text_similarity=analysis_cfg["max_text_similarity"],
        max_segment_reuse=analysis_cfg["max_segment_reuse"],
    )

    # ---- 5. rerank: relative judgment beats absolute scoring --------------
    # Batched: head-to-head comparison is only reliable for small groups, so
    # long videos with many finalists are reranked in rerank_pool-sized
    # batches of similar-scoring clips instead of being capped.
    batch_size = max(2, scoring_cfg.get("rerank_pool", 8))
    if len(finalists) > 1:
        finalists.sort(key=lambda c: c.score, reverse=True)
        reranked: list[ClipCandidate] = []
        for i in range(0, len(finalists), batch_size):
            batch = finalists[i : i + batch_size]
            reranked += _rerank(batch, segments, llm) if len(batch) > 1 else batch
        finalists = sorted(reranked, key=lambda c: c.score, reverse=True)

    kept = finalists[:max_clips] if max_clips > 0 else finalists
    rejections += [Rejection(c, "over_limit") for c in finalists[len(kept):]]
    return kept, rejections


def _fuse(c: ClipCandidate, weights: dict, reaction: float, speech_ratio: float = 1.0) -> float:
    """Weighted multimodal score 0..1 from a candidate's subscores.

    Content-adaptive: for low-speech clips (workouts, action, b-roll) the
    text/engagement weight — which the LLM scores near zero when nobody is
    talking — is shifted onto the channels that actually carry NON-VERBAL
    content: VISUAL and REACTION (what and who is on screen). It is NOT put
    on audio, because for a silent clip low audio-excitement is just silence
    (an artifact of percentile-ranking against a talkier part of the video),
    so weighting audio up would penalize the very content we want to surface.
    A big lift or a fast rep then scores on what it shows, not on empty
    dialogue. Total weight is conserved, so talky clips are unaffected.
    """
    s = c.subscores or {}
    talky = max(0.0, min(1.0, speech_ratio))

    w_text = weights["text"] * talky
    w_eng = weights["engagement"] * talky
    freed = (weights["text"] - w_text) + (weights["engagement"] - w_eng)
    carriers = weights["visual"] + weights["reaction"]
    boost = 1.0 + (freed / carriers if carriers > 0 else 0.0)

    return (
        w_text * s.get("text", 50) / 100.0
        + weights["visual"] * boost * s.get("visual", 50) / 100.0
        + weights["reaction"] * boost * reaction
        + weights["audio"] * s.get("audio", 50) / 100.0
        + w_eng * s.get("engagement", 50) / 100.0
    )


def _speech_ratio(c: ClipCandidate, segments: list[Segment]) -> float:
    """0 = silent, 1 = steady talking (~2 words/sec). Drives adaptive weights."""
    words = sum(
        len(sg.text.split())
        for sg in segments
        if sg.end > c.start and sg.start < c.end
    )
    dur = max(c.end - c.start, 1.0)
    return min(1.0, (words / dur) / 2.0)


# ---- signals ---------------------------------------------------------------


def _pct(x: np.ndarray) -> np.ndarray:
    """Percentile-rank normalization to 0..1 within this video."""
    if x.size == 0:
        return x
    order = x.argsort().argsort().astype(np.float32)
    return order / max(x.size - 1, 1)


def _combine(channels: list[np.ndarray]) -> np.ndarray:
    channels = [c for c in channels if c.size]
    if not channels:
        return np.zeros(0, dtype=np.float32)
    n = min(c.size for c in channels)
    return np.mean([c[:n] for c in channels], axis=0)


def _window_mean(signal: np.ndarray, start: float, end: float) -> float:
    if signal.size == 0:
        return 0.5  # no signal data -> neutral, not penalizing
    lo, hi = int(start), min(int(end) + 1, signal.size)
    if lo >= signal.size or hi <= lo:
        return 0.5
    return float(signal[lo:hi].mean())


def _build_events(
    audio_excitement: np.ndarray,
    visual_activity: np.ndarray,
    visual_raw: dict,
    threshold: float = 0.92,
    max_events: int = 120,
) -> list[tuple[float, str]]:
    """Compact (second, description) list of standout moments for prompts."""
    events = []
    scene_cut = visual_raw.get("scene_cut", np.zeros(0))
    n = max(audio_excitement.size, visual_activity.size)
    for sec in range(n):
        parts = []
        a = audio_excitement[sec] if sec < audio_excitement.size else 0
        v = visual_activity[sec] if sec < visual_activity.size else 0
        if a > threshold:
            parts.append("AUDIO spike (shouting/laughter/cheering likely)")
        if v > threshold:
            parts.append("high visual activity")
        if sec < scene_cut.size and scene_cut[sec] >= 2:
            parts.append("rapid scene cuts")
        if parts:
            events.append((float(sec), " + ".join(parts)))
    if len(events) > max_events:  # keep the most spread-out subset
        step = len(events) / max_events
        events = [events[int(i * step)] for i in range(max_events)]
    return events


def _signal_peak_windows(
    combined: np.ndarray,
    segments: list[Segment],
    percentile: float,
    min_duration: float,
    max_duration: float,
    existing: list[ClipCandidate],
) -> list[tuple[float, float]]:
    """Windows around signal peaks that no transcript candidate already covers."""
    if combined.size == 0:
        return []
    cutoff = np.percentile(combined, percentile)
    hot = combined >= cutoff

    # Merge consecutive/near-adjacent hot seconds into windows.
    windows: list[list[float]] = []
    for sec in np.flatnonzero(hot).astype(float):
        if windows and sec - windows[-1][1] <= 5:
            windows[-1][1] = sec
        else:
            windows.append([sec, sec])

    video_end = segments[-1].end if segments else float(combined.size)
    result = []
    for lo, hi in windows:
        # Pad to minimum duration around the peak, clamp into the video.
        pad = max(0.0, (min_duration - (hi - lo)) / 2)
        start, end = max(0.0, lo - pad), min(video_end, hi + pad + 1)
        if end - start < min_duration:
            continue
        end = min(end, start + max_duration)
        c = ClipCandidate(start=start, end=end, score=0)
        if any(c.overlap_ratio(e) > 0.3 for e in existing):
            continue  # the transcript pool already found this moment
        result.append((start, end))
    # Scale the window budget with video length: ~1 per 5 minutes, min 12.
    # Long streams (esp. low-speech workouts) need more action candidates.
    return result[: max(12, combined.size // 300)]


# ---- rerank ------------------------------------------------------------------


def _rerank(finalists: list[ClipCandidate], segments: list[Segment], llm: LLMBackend) -> list[ClipCandidate]:
    """One LLM call ordering the finalists best-first; blends rank into score."""
    template = RERANK_PROMPT_PATH.read_text(encoding="utf-8")
    lines = []
    for i, c in enumerate(finalists):
        text = highlights._clip_text(c, segments)[:300]
        s = c.subscores or {}
        lines.append(
            f'{i}: [{c.start:.0f}s-{c.end:.0f}s] audio={s.get("audio", "?")} visual={s.get("visual", "?")} '
            f'reaction={s.get("reaction", "?")} | "{text}"'
        )
    prompt = template.replace("{candidates}", "\n".join(lines)).replace("{count}", str(len(finalists)))

    try:
        raw = llm.generate(prompt, json_mode=True)
        order = _parse_order(raw, len(finalists))
    except Exception:
        order = None
    if order is None:
        print("  Rerank: unparseable LLM output, keeping fused order")
        return sorted(finalists, key=lambda c: c.score, reverse=True)

    # Rerank only REORDERS and gives a small upward nudge to favorites — it
    # must never lower a clip's score (that would retroactively push clips
    # below the quality bar they already passed). Scores only go up, by 0-6.
    n = len(finalists)
    for rank, idx in enumerate(order):
        bonus = round((n - 1 - rank) / max(n - 1, 1) * 6)  # top +6 ... worst +0
        finalists[idx].score = min(100, finalists[idx].score + bonus)
        finalists[idx].subscores["rerank_position"] = rank + 1
    return sorted(finalists, key=lambda c: c.score, reverse=True)


def _parse_order(raw: str, n: int) -> list[int] | None:
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw.strip())
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        return None
    try:
        data = json.loads(text[start : end + 1])
        order = [int(i) for i in data["order"]]
    except (json.JSONDecodeError, KeyError, TypeError, ValueError):
        return None
    if sorted(order) != list(range(n)):
        # Tolerate partial/duplicated lists: keep valid first occurrences, append missing.
        seen, cleaned = set(), []
        for i in order:
            if 0 <= i < n and i not in seen:
                cleaned.append(i)
                seen.add(i)
        cleaned += [i for i in range(n) if i not in seen]
        order = cleaned
    return order
