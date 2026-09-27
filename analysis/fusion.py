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
import time
from pathlib import Path

import numpy as np

from analysis import highlights
from analysis.audio_features import extract_audio_features
from analysis.visual_features import extract_visual_features, reaction_for_window
from core import cancel, progress
from core.models import ClipCandidate, Rejection, Segment
from llm.base import LLMBackend, generate_json

# The shape rerank.txt asks for; held to it on a cloud model (llm.base.generate_json).
ORDER_SCHEMA = {
    "type": "object",
    "properties": {"order": {"type": "array", "items": {"type": "integer"}}},
    "required": ["order"],
    "additionalProperties": False,
}

RERANK_PROMPT_PATH = Path(__file__).resolve().parent.parent / "config" / "prompts" / "rerank.txt"


def find_clips(
    video_path: Path,
    segments: list[Segment],
    llm: LLMBackend,
    config: dict,
    signals: tuple[dict, dict] | None = None,
    creator_context=None,  # creator.retrieval.CreatorContext | None
    weight_bias: dict | None = None,  # per-channel multipliers from creator.learning
    audience: "np.ndarray | None" = None,  # analysis.hype curve (chat/heatmap), 0..1
    measure_reaction: bool = True,  # False for Gaming / Split-Screen, see below
    gaming=None,  # analysis.gaming.GamingProfile: score as a gaming stream
    chat=None,  # analysis.chat_moments.ChatSignal: what chat's reactions mark
    sounds=None,  # analysis.game_audio.GameSounds: what the game's own sound marks
) -> tuple[list[ClipCandidate], list[Rejection]]:
    clips_cfg = config["clips"]
    analysis_cfg = config["analysis"]
    scoring_cfg = config.get("scoring", {})
    weights = scoring_cfg.get(
        "weights",
        {"text": 0.30, "visual": 0.20, "reaction": 0.20, "audio": 0.20, "engagement": 0.10},
    )
    if gaming is not None:
        # A gaming stream is judged on what happens in the game (the "game"
        # channel below) more than on the words: analysis/gaming.py.
        weights = dict(gaming.weights)
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

    # ---- the gaming profile's evidence that something happened in the game --
    # Only for a gaming stream; standard scoring never builds any of it.
    game_curve = np.zeros(0, dtype=np.float32)
    voice = np.zeros(0, dtype=np.float32)
    chat_curve = np.zeros(0, dtype=np.float32)
    game_sound = np.zeros(0, dtype=np.float32)
    people_sound = np.zeros(0, dtype=np.float32)
    screen = None
    guidance = ""
    events_title = None
    if gaming is not None:
        voice, voice_events = _voice_jump(audio_raw, segments)
        sources = [(voice, 0.6)]
        game_events = list(voice_events)
        if chat is not None:
            chat_curve = chat.curve
            sources.append((chat_curve, 0.9))
            game_events += list(chat.events)
        if sounds is not None:
            # The game's sound (gunfire, a goal, a crash) is the game saying
            # so; a scream or laughter is mostly the people playing it.
            game_sound, people_sound = sounds.game, sounds.people
            sources += [(game_sound, 0.7), (people_sound, 0.5)]
            game_events += list(sounds.events)
        game_curve = _soft_or(sources)
        if scoring_cfg.get("read_screen", True) and game_curve.size:
            # What the game writes on screen in those moments: an event's
            # banner, or a menu chat reacted to.
            screen = _read_screen(video_path, game_curve, segments, clips_cfg, gaming)
            if screen is not None:
                game_events += list(screen.events)
                game_events += [(s0, f"ON SCREEN: a menu, queue or settings screen ({words})")
                                for s0, _e0, words in screen.menus]
        events = sorted(events + game_events, key=lambda ev: ev[0])
        guidance = gaming.guidance("clips")
        # Per chunk when the stream changes game part way through.
        if len({str(g.get("name") or "") for g in gaming.games}) > 1:
            guidance = lambda start, end: gaming.guidance("clips", start, end)  # noqa: E731
        events_title = "GAME / CHAT / AUDIO EVENTS (from signal analysis):"
        print(f"  Gaming: scoring as a {gaming.spec.get('label', 'game')} stream"
              + (f" ({gaming.game})" if gaming.game else "")
              + f": {len(chat.events) if chat is not None else 0} chat moment(s), "
              f"{len(sounds.events) if sounds is not None else 0} game sound(s), "
              f"{len(voice_events)} voice jump(s)")

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
        **({"guidance": guidance, "events_title": events_title} if gaming is not None else {}),
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
    # Audience hype (chat density / most-replayed) is a first-class peak
    # source: a moment chat exploded over is a candidate even if the
    # transcript and A/V signals missed it.
    peak_signals = [visual_activity, audio_excitement, combined]
    if audience is not None and audience.size:
        peak_signals.append(audience)
    if gaming is not None and game_curve.size:
        # Each in-game moment is a candidate of its own, from a few seconds
        # before it (the setup) to the reaction, even with nothing said: a
        # quiet streamer's best play has no transcript line to be found by.
        # First, so the moment's own window (with its setup) is the one kept.
        for win in _event_windows(game_curve, segments, clips_cfg["min_duration"],
                                  clips_cfg["max_duration"], existing=seen):
            peak_windows.append(win)
            seen.append(ClipCandidate(start=win[0], end=win[1], score=0))
    for sig in peak_signals:
        for win in _signal_peak_windows(
            sig, segments,
            percentile=pk_pct,
            min_duration=clips_cfg["min_duration"],
            max_duration=clips_cfg["max_duration"],
            existing=seen,
            **({"video_end": max(segments[-1].end if segments else 0.0, float(sig.size))}
               if gaming is not None else {}),
        ):
            peak_windows.append(win)
            seen.append(ClipCandidate(start=win[0], end=win[1], score=0))
    if peak_windows:
        print(f"  {len(peak_windows)} signal-peak window(s) found beyond transcript picks "
              f"(per-modality: visual/audio/combined)")
        signal_cands = highlights.score_windows(
            segments, llm, peak_windows, events=events,
            **({"guidance": gaming.guidance("windows"), "events_title": events_title,
                "labels": {i: "game here: " + gaming.game_at(s0, e0)[0]
                           for i, (s0, e0) in enumerate(peak_windows) if gaming.game_at(s0, e0)[0]}}
               if gaming is not None else {}),
        )
        # Signal peaks are seeded tight around the hot moment — grow them to a
        # full ~25s clip on sentence boundaries so action moments aren't tiny.
        if gaming is None:
            signal_cands = [
                highlights._fit_to_segments(
                    c, segments, clips_cfg["min_duration"], clips_cfg["max_duration"], target_duration=25.0
                )
                for c in signal_cands
            ]
        else:
            # One game moment makes a 15-35 s clip that starts before the play.
            # Snapping to sentences would move it to wherever the streamer
            # next spoke (often after the play), so it is only extended to
            # finish a line it would cut off.
            end_of_video = max(segments[-1].end if segments else 0.0, float(audio_excitement.size))
            signal_cands = [
                _frame_game_window(c, segments, clips_cfg["min_duration"], clips_cfg["max_duration"],
                                   end_of_video)
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
        if gaming is not None:
            # How strongly something happened in the game in this window: its
            # strongest moment counts most, so one big play isn't averaged away.
            c.subscores["game"] = (round(_window_peak(game_curve, c.start, c.end) * 100)
                                   if game_curve.size else 50)

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
    if measure_reaction:
        print(f"  Scoring reactions for {n_reactions} candidate(s) "
              f"(incl. silent-action clips)...")
    else:
        # Gaming / Split-Screen (gaming/): "a person on screen, being
        # emphasised" reads a game's characters as people and a top-down
        # game as nobody at all, which is how top-down games once got no
        # clips. The streamer's reaction is in their voice, which the audio
        # and text channels already score, so every candidate keeps the
        # neutral 50 rather than being judged on the game's characters.
        react_set, n_reactions = [], 0
        print("  Gaming: reactions left neutral (the game's characters are not the streamer)")
    for ri, c in enumerate(react_set, 1):
        # Each pass decodes video around the candidate and runs the detector,
        # so this loop is minutes of work with no natural stopping point.
        cancel.check_active()
        progress.emit(stage="reactions", current=ri, total=n_reactions)
        r = reaction_for_window(
            video_path, c.start, c.end,
            audio_excitement=_window_mean(audio_excitement, c.start, c.end),
            detector=config["tracking"]["detector"],
        )
        c.subscores["reaction"] = round(r * 100)
        # Distinguishes a MEASURED zero -- nothing person-shaped on screen --
        # from the neutral 50 placeholder above, which only means this
        # candidate never got the expensive pass. Without the marker the two
        # are indistinguishable downstream, and "we found no people" would be
        # claimed about windows nobody ever looked at.
        c.subscores["reaction_measured"] = 1

    ctx_cap = int(scoring_cfg.get("creator_context_max", 6))
    action_bonus = int(scoring_cfg.get("action_bonus", 10))
    audience_bonus = int(scoring_cfg.get("audience_bonus", 8))
    game_bonus = int(scoring_cfg.get("game_bonus", 8))
    menu_penalty = int(scoring_cfg.get("menu_penalty", 12))
    n_context = 0
    n_action = 0
    n_hype = 0
    n_game = 0
    n_menu = 0
    for c in candidates:
        fused = round(100 * _fuse(c, weights, c.subscores["reaction"] / 100.0, speech[id(c)]))
        if gaming is not None:
            # What marked this moment, for the clip's score breakdown, and a
            # bonus when independent witnesses agree it happened: chat (the
            # audience), the game's own sound, and the streamer (a shout, a
            # scream, laughing). Loudness alone only backs chat up: gunfire
            # is loud, so it would always agree with the game's sound.
            hits = set()
            if chat_curve.size and _window_max(chat_curve, c.start, c.end) >= 0.5:
                hits.add("chat")
            if game_sound.size and _window_max(game_sound, c.start, c.end) >= 0.5:
                hits.add("game")
            if ((voice.size and _window_max(voice, c.start, c.end) >= 0.5)
                    or (people_sound.size and _window_max(people_sound, c.start, c.end) >= 0.5)):
                hits.add("streamer")
            if _window_max(audio_excitement, c.start, c.end) >= 0.92:
                hits.add("loud")
            if screen is not None and any(c.start - 1 <= sec <= c.end for sec, _d in screen.events):
                hits.add("screen")
            # A menu, a queue or a settings screen isn't a moment, whatever
            # chat made of it.
            menu_here = [words for s0, e0, words in (screen.menus if screen is not None else [])
                         if min(c.end, e0) - max(c.start, s0) >= 0.5 * (c.end - c.start)]
            why, kinds = [], set()
            if menu_here:
                why.append(f"ON SCREEN: a menu, queue or settings screen ({menu_here[0]})")
                kinds.add("ON SCREEN")
            for sec, desc in events:
                kind = desc.split(":")[0]
                if (c.start - 1 <= sec <= c.end
                        and kind in ("CHAT", "STREAMER", "GAME SOUND", "SOUND", "ON SCREEN")
                        and kind not in kinds):
                    why.append(desc)
                    kinds.add(kind)
            if why:
                c.subscores["game_why"] = "; ".join(why[:3])
            on_menu = bool(menu_here)
            if on_menu and menu_penalty > 0:
                fused = max(0, fused - menu_penalty)
                c.subscores["menu"] = -menu_penalty
                n_menu += 1
            agree = (len(hits & {"chat", "game", "streamer", "screen"}) >= 2
                     or {"chat", "loud"} <= hits)
            if game_bonus > 0 and agree and not on_menu:
                fused = min(100, fused + game_bonus)
                c.subscores["game_bonus"] = game_bonus
                n_game += 1
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
        # Audience hype (chat replay density / YouTube most-replayed): a
        # small additive bonus only for windows near the TOP of the curve.
        # Deliberately capped below the content channels — the transcript/
        # visual context stays dominant, and gifted-sub message storms are
        # already neutralized upstream by unique-chatter counting.
        if audience is not None and audience_bonus > 0:
            h = _window_mean(audience, c.start, c.end)
            if h >= 0.85:
                b = round(audience_bonus * (h - 0.85) / 0.15)
                if b:
                    fused = min(100, fused + b)
                    c.subscores["hype"] = b
                    n_hype += 1
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
    if n_hype:
        print(f"  Audience hype boosted {n_hype} candidate(s) (max +{audience_bonus})")
    if n_game:
        print(f"  Game moments boosted {n_game} candidate(s) (+{game_bonus})")
    if n_menu:
        print(f"  Menus and queues marked down: {n_menu} candidate(s) (-{menu_penalty})")

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
        # One LLM call per batch, and on a CPU-only machine each one is slow.
        # Reported per batch because this loop used to run silently: the bar
        # reached the end of "analyze" and then sat there, which is
        # indistinguishable from a hang.
        n_batches = (len(finalists) + batch_size - 1) // batch_size
        print(f"  Ranking {len(finalists)} finalist(s) in {n_batches} batch(es)...")
        for bi, i in enumerate(range(0, len(finalists), batch_size), 1):
            cancel.check_active()  # one more LLM call per batch
            progress.emit(stage="ranking", current=bi, total=n_batches)
            batch = finalists[i : i + batch_size]
            reranked += (_rerank(batch, segments, llm, gaming) if len(batch) > 1 else batch)
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
    if "game" in weights:
        # The gaming profile: a quiet stretch's freed weight goes to what
        # happened in the game and to the sound, not to who is on screen: a
        # quiet streamer's big play is carried by the play itself.
        carriers = weights["game"] + weights["audio"]
        boost = 1.0 + (freed / carriers if carriers > 0 else 0.0)
        return (
            w_text * s.get("text", 50) / 100.0
            + weights["visual"] * s.get("visual", 50) / 100.0
            + weights["reaction"] * reaction
            + weights["audio"] * boost * s.get("audio", 50) / 100.0
            + weights["game"] * boost * s.get("game", 50) / 100.0
            + w_eng * s.get("engagement", 50) / 100.0
        )
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


def _window_max(signal: np.ndarray, start: float, end: float) -> float:
    if signal.size == 0:
        return 0.0
    lo, hi = int(start), min(int(end) + 1, signal.size)
    if lo >= signal.size or hi <= lo:
        return 0.0
    return float(signal[lo:hi].max())


def _window_peak(signal: np.ndarray, start: float, end: float) -> float:
    """A window's strongest second counts most: 0.6 x peak + 0.4 x mean, so a
    short burst isn't averaged away over a 25 s clip."""
    if signal.size == 0:
        return 0.5
    return 0.6 * _window_max(signal, start, end) + 0.4 * _window_mean(signal, start, end)


def _soft_or(sources: list[tuple[np.ndarray, float]]) -> np.ndarray:
    """Evidence from several independent sources, each 0..1 per second with
    a trust weight: 1 - product(1 - w*s). Agreement raises it; a missing
    source (an empty array) is simply absent, never a penalty."""
    sources = [(s, w) for s, w in sources if s.size]
    if not sources:
        return np.zeros(0, dtype=np.float32)
    n = max(s.size for s, _ in sources)
    miss = np.ones(n, dtype=np.float32)
    for sig, w in sources:
        x = np.zeros(n, dtype=np.float32)
        x[: sig.size] = np.clip(sig, 0.0, 1.0)
        miss *= 1.0 - w * x
    return (1.0 - miss).astype(np.float32)


def _voice_jump(audio_raw: dict, segments: list[Segment], max_events: int = 60) -> tuple[np.ndarray, list]:
    """Seconds where the streamer suddenly gets loud while talking: a shout, a
    laugh, a scream (loudness against the last 30 s, only where words are
    being said). The cheap stand-in for seeing their face react. Returns
    (0..1 per second, events)."""
    spike = np.asarray(audio_raw.get("spike", np.zeros(0)), dtype=np.float32)
    n = spike.size
    if n == 0:
        return np.zeros(0, dtype=np.float32), []
    talking = np.zeros(n, dtype=bool)
    for sg in segments:
        words = sg.words or [{"start": sg.start, "end": sg.end}]
        for w in words:
            lo, hi = int(w.get("start", sg.start)), int(w.get("end", sg.end)) + 1
            talking[max(0, lo):min(n, hi)] = True
    # 1.8x the recent level starts to count; 4x is a full shout.
    jump = np.clip((spike - 1.8) / 2.2, 0.0, 1.0) * talking
    events = []
    sec = 0
    while sec < n:
        if jump[sec] < 0.6:
            sec += 1
            continue
        end = sec
        while end + 1 < n and jump[end + 1] >= 0.6:
            end += 1
        peak = float(spike[sec:end + 1].max())
        events.append((float(sec), f"STREAMER: sudden shout or laugh ({peak:.1f}x louder than usual)", peak))
        sec = end + 1
    events.sort(key=lambda ev: -ev[2])
    events = sorted(((t, d) for t, d, _p in events[:max_events]), key=lambda ev: ev[0])
    return jump.astype(np.float32), events


def _event_windows(
    game: np.ndarray,
    segments: list[Segment],
    min_duration: float,
    max_duration: float,
    existing: list[ClipCandidate],
    threshold: float = 0.5,
    before: float = 4.0,
    after: float = 18.0,
) -> list[tuple[float, float]]:
    """A window around each in-game moment the game curve marks: from a few
    seconds before it (the setup) to about 18 s after (the play and the
    reaction), within the clip limits; one per moment, none that an existing
    candidate already covers."""
    if game.size == 0:
        return []
    # The signals cover the whole video; a quiet stream's transcript can end
    # long before it does.
    video_end = max(segments[-1].end if segments else 0.0, float(game.size))
    hot = np.flatnonzero(game >= threshold)
    groups: list[list[int]] = []
    for sec in hot:
        if groups and sec - groups[-1][1] <= 3:
            groups[-1][1] = int(sec)
        else:
            groups.append([int(sec), int(sec)])
    # The strongest moments first, so the budget goes to them.
    groups.sort(key=lambda g: -float(game[g[0]:g[1] + 1].max()))
    out: list[tuple[float, float]] = []
    taken = list(existing)
    for lo, hi in groups:
        start = max(0.0, lo - before)
        end = min(video_end, max(hi + 6.0, lo + after))
        if end - start < min_duration:
            end = min(video_end, start + min_duration)
        end = min(end, start + max_duration)
        if end - start < min_duration - 1:
            continue
        c = ClipCandidate(start=start, end=end, score=0)
        if any(c.overlap_ratio(e) > 0.3 for e in taken):
            continue
        out.append((start, end))
        taken.append(c)
        if len(out) >= max(12, game.size // 240):
            break
    return sorted(out)


def _read_screen(video_path, game_curve: np.ndarray, segments: list[Segment], clips_cfg: dict, gaming):
    """What the game writes on screen in the game-moment windows, strongest
    first (analysis/game_text.py); None when the OCR isn't installed."""
    from analysis import game_text

    if not game_text.available():
        return None
    from analysis.gaming import knowledge

    windows = _event_windows(game_curve, segments, clips_cfg["min_duration"], clips_cfg["max_duration"],
                             existing=[])
    windows.sort(key=lambda w: -float(game_curve[int(w[0]):int(w[1]) + 1].max()))
    t0 = time.monotonic()
    try:
        screen = game_text.read_screen(Path(video_path), windows, lambda s, e: gaming.game_at(s, e)[1],
                                       knowledge().get("screen_text") or {})
    except Exception as e:
        print(f"  (on-screen text unavailable: {e})")
        return None
    print(f"  On-screen text: read {screen.frames} frame(s) in {time.monotonic() - t0:.0f}s, "
          f"{len(screen.events)} event(s), {len(screen.menus)} menu screen(s)")
    return screen


def _frame_game_window(c: ClipCandidate, segments: list[Segment], min_duration: float,
                       max_duration: float, video_end: float) -> ClipCandidate:
    """A game moment's window as a 15-35 s clip (within the user's limits):
    grown after the play if short, never moved off its start, and extended
    only to finish a spoken line it would cut off."""
    lo = max(min_duration, 15.0)
    hi = max(lo, min(max_duration, 35.0))
    start = max(0.0, c.start)
    end = min(max(c.end, start + lo), start + hi, video_end)
    for sg in segments:
        if sg.start < end < sg.end and sg.end - start <= hi:
            end = min(sg.end, video_end)
    c.start, c.end = start, end
    return c


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
    video_end: float | None = None,
) -> list[tuple[float, float]]:
    """Windows around signal peaks that no transcript candidate already covers.
    `video_end`: where the video ends when the transcript stops before it (the
    gaming profile passes the signals' length); by default the transcript's end."""
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

    if video_end is None:
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


def _rerank(finalists: list[ClipCandidate], segments: list[Segment], llm: LLMBackend,
            gaming=None) -> list[ClipCandidate]:
    """One LLM call ordering the finalists best-first; blends rank into score."""
    template = RERANK_PROMPT_PATH.read_text(encoding="utf-8")
    lines = []
    for i, c in enumerate(finalists):
        text = highlights._clip_text(c, segments)[:300]
        s = c.subscores or {}
        if gaming is None:
            signals = (f'audio={s.get("audio", "?")} visual={s.get("visual", "?")} '
                       f'reaction={s.get("reaction", "?")}')
        else:
            # In a game stream the game moment is what the clips are compared on.
            signals = (f'game={s.get("game", "?")} audio={s.get("audio", "?")} '
                       f'visual={s.get("visual", "?")}')
            if s.get("game_why"):
                signals += f' [{s["game_why"]}]'
        lines.append(f'{i}: [{c.start:.0f}s-{c.end:.0f}s] {signals} | "{text}"')
    prompt = template.replace("{candidates}", "\n".join(lines)).replace("{count}", str(len(finalists)))
    guidance = ""
    if gaming is not None:
        guidance = (gaming.guidance("rerank") + "\n- game (0-100) is how strongly chat, the "
                    "streamer's voice and the game itself mark an in-game moment in that clip.")
    prompt = prompt.replace("{mode_guidance}", highlights._guidance_block(guidance))

    try:
        raw = generate_json(llm, prompt, ORDER_SCHEMA)
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
