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
) -> tuple[list[ClipCandidate], list[Rejection]]:
    clips_cfg = config["clips"]
    analysis_cfg = config["analysis"]
    scoring_cfg = config.get("scoring", {})
    weights = scoring_cfg.get(
        "weights",
        {"text": 0.30, "visual": 0.20, "reaction": 0.20, "audio": 0.20, "engagement": 0.10},
    )

    # ---- 1. extract + normalize signals --------------------------------
    progress.emit(stage="signals")
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

    peak_windows = _signal_peak_windows(
        combined, segments,
        percentile=scoring_cfg.get("signal_peak_percentile", 90),
        min_duration=clips_cfg["min_duration"],
        max_duration=clips_cfg["max_duration"],
        existing=candidates,
    )
    if peak_windows:
        print(f"  {len(peak_windows)} signal-peak window(s) found beyond transcript picks")
        candidates += highlights.score_windows(segments, llm, peak_windows, events=events)

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
    # for the strongest candidates; the rest keep a neutral 0.5.
    # Scale the (YOLO-expensive) reaction pass with candidate volume so long
    # streams don't leave most finalists on a neutral reaction score.
    top_k = max(scoring_cfg.get("reaction_top_k", 8), min(24, len(candidates) // 3))
    provisional = sorted(candidates, key=lambda c: _fuse(c, weights, reaction=0.5), reverse=True)
    n_reactions = min(top_k, len(provisional))
    print(f"  Scoring reactions for top {n_reactions} candidate(s)...")
    for ri, c in enumerate(provisional[:top_k], 1):
        progress.emit(stage="reactions", current=ri, total=n_reactions)
        r = reaction_for_window(
            video_path, c.start, c.end,
            audio_excitement=_window_mean(audio_excitement, c.start, c.end),
            detector=config["tracking"]["detector"],
        )
        c.subscores["reaction"] = round(r * 100)

    for c in candidates:
        c.score = round(100 * _fuse(c, weights, reaction=c.subscores["reaction"] / 100.0))

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


def _fuse(c: ClipCandidate, weights: dict, reaction: float) -> float:
    """Weighted multimodal score 0..1 from a candidate's subscores."""
    s = c.subscores or {}
    return (
        weights["text"] * s.get("text", 50) / 100.0
        + weights["visual"] * s.get("visual", 50) / 100.0
        + weights["reaction"] * reaction
        + weights["audio"] * s.get("audio", 50) / 100.0
        + weights["engagement"] * s.get("engagement", 50) / 100.0
    )


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
    # Scale the window budget with video length: ~1 per 10 minutes, min 10.
    return result[: max(10, combined.size // 600)]


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

    n = len(finalists)
    for rank, idx in enumerate(order):
        bonus = (n - 1 - rank) / max(n - 1, 1)  # best=1.0 ... worst=0.0
        finalists[idx].score = round(finalists[idx].score * 0.85 + bonus * 15)
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
