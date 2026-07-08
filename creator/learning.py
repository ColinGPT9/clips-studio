"""Preference learning: what does this creator's user actually keep?

Positive signals from clip_feedback (exports are the strongest "this one was
worth posting"; caption/timestamp edits mean the user invested in the clip).
We compare the subscore profile of those clips against the creator's library
average: channels that run consistently hotter in kept clips get a bounded
up-weight in fusion.

Guardrails (this must never destabilize scoring):
  * inert below MIN_SIGNALS positive events — no data, no opinion;
  * each weight can shift at most MAX_SHIFT (20%) from its configured value;
  * weights are renormalized, so the total influence budget never changes;
  * derived from the user's OWN actions only — never from the LLM.
"""

import json

from core.state import StateDB

MIN_SIGNALS = 8      # positive feedback events required before any bias
MAX_SHIFT = 0.20     # a weight may move at most this fraction of itself
_ACTION_WEIGHT = {"exported": 2.0, "captions_edited": 1.0, "timestamps_adjusted": 1.0}
_CHANNELS = ("text", "audio", "visual", "reaction", "engagement")


def preferences(db: StateDB, creator_id: int) -> dict | None:
    """{'weight_bias': {channel: multiplier}, 'preferred_duration': float|None,
    'signals': n} or None when there isn't enough data to say anything."""
    rows = db.conn.execute(
        "SELECT action, clip_meta FROM clip_feedback WHERE creator_id = ?", (creator_id,)
    ).fetchall()

    kept_profiles: list[tuple[float, dict]] = []
    durations: list[float] = []
    for r in rows:
        w = _ACTION_WEIGHT.get(r["action"])
        if w is None:
            continue
        try:
            meta = json.loads(r["clip_meta"] or "{}")
        except json.JSONDecodeError:
            continue
        scores = meta.get("scores") or {}
        profile = {ch: scores[ch] for ch in _CHANNELS if isinstance(scores.get(ch), (int, float))}
        if profile:
            kept_profiles.append((w, profile))
        if isinstance(meta.get("duration"), (int, float)) and r["action"] == "exported":
            durations.append(float(meta["duration"]))

    n_signals = sum(w for w, _ in kept_profiles)
    if n_signals < MIN_SIGNALS:
        return None

    # Library baseline: average subscores over ALL of this creator's clips.
    lib = db.conn.execute(
        "SELECT cl.scores FROM clips cl JOIN videos v ON v.video_id = cl.video_id"
        " WHERE v.creator_id = ? AND cl.scores != ''",
        (creator_id,),
    ).fetchall()
    baseline: dict[str, list[float]] = {ch: [] for ch in _CHANNELS}
    for r in lib:
        try:
            scores = json.loads(r["scores"])
        except json.JSONDecodeError:
            continue
        for ch in _CHANNELS:
            if isinstance(scores.get(ch), (int, float)):
                baseline[ch].append(float(scores[ch]))
    base_mean = {ch: (sum(v) / len(v)) for ch, v in baseline.items() if v}
    if not base_mean:
        return None

    # Weighted mean subscores of KEPT clips vs the baseline -> multiplier per
    # channel, clamped to [1 - MAX_SHIFT, 1 + MAX_SHIFT].
    bias: dict[str, float] = {}
    for ch in _CHANNELS:
        pairs = [(w, p[ch]) for w, p in kept_profiles if ch in p]
        if not pairs or ch not in base_mean or base_mean[ch] <= 0:
            bias[ch] = 1.0
            continue
        kept_mean = sum(w * v for w, v in pairs) / sum(w for w, _ in pairs)
        ratio = kept_mean / base_mean[ch]
        bias[ch] = max(1 - MAX_SHIFT, min(1 + MAX_SHIFT, ratio))

    preferred = sorted(durations)[len(durations) // 2] if len(durations) >= 5 else None
    return {"weight_bias": bias, "preferred_duration": preferred, "signals": int(n_signals)}


def apply_bias(weights: dict, bias: dict | None) -> dict:
    """Fusion weights nudged toward what this creator's user keeps, then
    renormalized so they still sum to the same total."""
    if not bias:
        return weights
    shifted = {ch: w * bias.get(ch, 1.0) for ch, w in weights.items()}
    total_before = sum(weights.values())
    total_after = sum(shifted.values())
    if total_after <= 0:
        return weights
    return {ch: w * total_before / total_after for ch, w in shifted.items()}
