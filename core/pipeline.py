"""Pipeline orchestration for a single video.

download -> transcribe -> analyze -> render (cut + track + vertical crop)

Every stage transition is committed to the state DB before the next stage
runs: a crash resumes at the failed stage, a 'done' video is never
reprocessed, and the clips table's UNIQUE constraint blocks duplicates.
"""

import json
import re
import threading
from pathlib import Path

from analysis.fusion import find_clips
from analysis.metadata import ClipMetadata, generate_metadata_batch
from core import cancel, progress
from core.binaries import ffprobe
from core.models import ClipCandidate, RenderedClip, Segment
from core.outcome import explain_no_clips, summarise_run
from core.paths import cached_source, discard
from core.state import StateDB
from llm.registry import create_backend
from transcription.transcriber import transcribe
from video.captions import build_captions
from video.cutter import cut_clip

# A latch, not a flag: _share_the_cpu() is called from worker threads, and an
# Event's set/is_set pair does the once-only check without a `global` rebind
# that static analysis reads as a write nobody consumes.
_CPU_SHARED = threading.Event()


def _render_failure_reason(err: Exception) -> str:
    """One sentence for a failed render, instead of FFmpeg's whole output.

    A memory failure arrives as pages of x264 and libav noise whose actual
    content is "malloc failed" somewhere in the middle. Printed once per clip
    across forty clips, the cause is completely buried.
    """
    text = str(err)
    lowered = text.lower()
    if "malloc of size" in lowered or "cannot allocate memory" in lowered:
        return (
            "ran out of memory while encoding. Close other applications, or "
            "lower video.parallel_renders in settings.yaml"
        )

    # Not a known signature: keep one line rather than a wall — but the LAST
    # meaningful one, not the first.
    #
    # This used to take line one, which for the error that matters most is
    # "ffmpeg cut failed:" — our own prefix from video/cutter.py. The 2000
    # characters of FFmpeg stderr underneath it were captured and then thrown
    # away one line later, so a user watching every clip fail saw a message
    # that named the stage and nothing about the cause. That is what made #84
    # impossible to diagnose from a bug report.
    #
    # FFmpeg puts the fatal error last, after the banner and stream dumps, so
    # reading from the end is what finds it.
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    lines = [ln for ln in lines if not ln.endswith(":")] or lines
    return (lines[-1] if lines else "unknown error")[:300]


def _share_the_cpu(workers: int) -> None:
    """Stop the render pool taking every core, so the machine stays usable.

    OpenCV defaults to one thread per core and torch to half, and neither
    knows how many clips are being rendered at once. On a 12-core machine with
    parallel_renders: 3 that is up to 36 OpenCV threads contending for 12
    cores. The result is not just "busy" — oversubscribed, the scheduler
    cannot hand the desktop a slice, and the machine becomes unusable while a
    job runs. Measured, a single frame's greyscale conversion pulled 9.4 cores
    for a quarter-second of work.

    Two cores are held back on purpose. Saturating the last one buys a few
    percent of throughput and costs the ability to use your computer, which is
    a bad trade for something that runs for an hour.

    Fewer threads is often FASTER here as well: 36 threads thrashing 12 cores
    lose time to context switching that 12 threads do not pay.

    Process-wide and set once, so it covers the podcast path and the renderer
    too. Not applied per call — the setting is global to the process, so doing
    it repeatedly from worker threads would just race.
    """
    if _CPU_SHARED.is_set():
        return
    _CPU_SHARED.set()

    import os

    import cv2

    cores = os.cpu_count() or 4
    per_worker = max(1, (cores - 2) // max(1, workers))
    cv2.setNumThreads(per_worker)
    try:
        import torch

        torch.set_num_threads(per_worker)
    except Exception:  # torch is optional at this point in the pipeline
        pass
    print(f"      CPU: {per_worker} thread(s) per render worker "
          f"({workers} workers, {cores} cores, 2 held back for the desktop)")


def online_transcription(config: dict) -> dict | None:
    """The `transcription` settings when they name a provider, else None.

    None keeps transcription on this PC with Whisper, the default, exactly as
    it has always run. A provider sends the audio to it on the user's own key.
    """
    settings = config.get("transcription") or {}
    if str(settings.get("backend") or "local") == "local":
        return None
    return {**settings, "data_dir": config["paths"]["data_dir"]}


def clip_direction(config: dict, llm, duration: float):
    """The person's direction for this job (analysis/intent.py), or None.

    None whenever no direction was given: nothing here runs and the video is
    scored exactly as it always has been."""
    focus = str((config.get("clips") or {}).get("focus") or "").strip()
    if not focus:
        return None
    from analysis import intent as clip_intent

    intent = clip_intent.parse(focus, llm, duration)
    if intent is not None:
        print(f"      Clip direction: {focus}")
        for line in intent.understood():
            print(f"        {line}")
        for text in intent.not_applied:
            print(f"        Not applied (a direction only adds weight): {text}")
    return intent


def _with_usable_model(llm_config: dict) -> dict:
    """Point the backend at a model that is actually installed.

    The setup check reports whichever model can really run rather than the one
    named in settings.yaml, because the two drift apart — setup downloads what
    it recommends for the hardware, which on a machine with no graphics card is
    not the shipped default. Loading the configured tag regardless would make
    that check a lie: green in setup, "model not found" on the first video.

    Returns the config untouched when nothing needs changing, including when
    Ollama cannot be reached — the preflight check is what reports that.

    A cloud model on the user's own key is used exactly as chosen: it is not
    "installed" anywhere, and swapping it for a local model would be the
    silent fallback the user never asked for.
    """
    from llm.manager import resolve_usable_model
    from llm.spec import is_local

    if not is_local(llm_config.get("backend") or ""):
        return llm_config

    spec = llm_config.get("backend") or ""
    configured = spec.split("/")[-1]
    usable = resolve_usable_model(
        llm_config.get("ollama_host", "http://localhost:11434"), configured
    )
    if not usable or usable == configured:
        return llm_config

    print(f"      AI model: '{configured}' is not installed — using '{usable}'")
    return {**llm_config, "backend": f"ollama/{usable}"}


def convert_slow_source(video, config: dict) -> None:
    """One up-front H.264 conversion for a source that decodes slowly.

    AV1, VP9 and H.265 (files added from the PC, old uploads, format
    fallbacks) are converted once, in place, so every later decode pass runs
    at hardware speed. It happens here, in the job, rather than while the file
    is being added: it takes minutes for a long recording, and this is where
    the window can show a stage for it (#122). Both paths call it, 9:16 and
    16:9. Failure-safe: ensure_h264_source keeps the original if it fails.
    """
    from video.encoding import SLOW_SOURCE_CODECS, ensure_h264_source, source_codec

    if source_codec(video.path) in SLOW_SOURCE_CODECS:
        progress.emit(stage="converting source to H.264", video_id=video.video_id)
        ensure_h264_source(video.path, config)


def process_video(url: str, config: dict, db: StateDB, force: bool = False) -> list[RenderedClip]:
    import time

    data_dir = Path(config["paths"]["data_dir"])
    started = time.monotonic()

    # Per-job, not per-process: the server is long-lived, so without this the
    # end-card tally would report every job it had ever run.
    from video import outro as _outro

    _outro.reset_tally()

    print(f"[1/4] Downloading: {url}")
    progress.emit(stage="download", message=url)
    video = _cached_or_download(url, data_dir, db, vertical=_vertical_live_requested(config))
    print(f"      {video.title} ({video.duration:.0f}s) -> {video.path}")
    progress.emit(stage="downloaded", video_id=video.video_id, title=video.title, duration=video.duration)

    convert_slow_source(video, config)

    # Vertical Live (core/modes.py): the source has to actually be a 9:16
    # video. Checked before any work, so a wrong toggle costs seconds and says
    # so, rather than an hour of processing the wrong way.
    from core import modes

    if modes.is_vertical_live(config):
        width, height = modes.probe_size(video.path)
        if modes.orientation(width, height) != "vertical":
            raise modes.NotVerticalError(width, height)
        print(f"      Vertical Live: {width}×{height}, keeping the stream's own layout "
              "(no face tracking or reframing)")
    elif modes.sport(config):
        # A match already filmed for 9:16 keeps its composition, exactly as a
        # Vertical Live does: the moments are still found the sport's way.
        width, height = modes.probe_size(video.path)
        if modes.orientation(width, height) == "vertical":
            print(f"      Sports: {width}×{height} is already vertical, keeping its composition")
            config = {**config, "clips": {**config["clips"], "vertical_live": True}}

    cancel.clear(video.video_id)  # fresh start; any stale flag from a prior run gone
    # Source length is stored too: the queue's time estimate scales its history
    # by it, so a long VOD isn't predicted to cost the same as a short upload.
    db.upsert_video(
        video.video_id,
        title=video.title,
        channel_name=video.channel,
        duration=video.duration,
    )
    # Where it came from, so the clips can point back to it. An uploaded file
    # records its original link (if the user gave one) at upload instead.
    from sources.dispatch import identify

    source_name, _ = identify(url)
    if source_name != "local":
        db.set_video_source(video.video_id, url, source_name)
    # The game(s) the platform says it shows, kept so a re-run from the cached
    # file still knows them (the gaming profile, analysis/gaming.py).
    if getattr(video, "games", None):
        db.set_video_games(video.video_id, video.games)
    # Creator intelligence: attach the video to its creator profile (created
    # on first sight of this channel). Failure-safe — never blocks processing.
    creator_id = None
    creator_ctx = None
    creator_prefs = None
    try:
        from creator import identity, learning, retrieval

        creator_id = identity.tag_video(db, video.video_id, video.channel)
        if creator_id is not None:
            # What we already know about this creator from PAST videos —
            # informs scoring (small capped callback bonus) and metadata.
            creator_ctx = retrieval.context_for(db, creator_id)
            if creator_ctx is not None:
                print(f"      Creator context loaded for {creator_ctx.creator_name}")
            # What the user KEEPS for this creator (exports/edits) — bounded
            # scoring-weight bias; None until there's enough feedback data.
            creator_prefs = learning.preferences(db, creator_id)
            # Branding: if the job didn't pick a watermark but THIS creator
            # has a default branding profile, apply it. Lets a clipper set
            # each creator's logo once and have every video auto-brand.
            if "watermark" not in config["clips"]:
                crow = db.conn.execute(
                    "SELECT default_branding_id FROM creators WHERE creator_id = ?", (creator_id,)
                ).fetchone()
                bid = crow["default_branding_id"] if crow else None
                if bid:
                    import json as _json

                    brow = db.get_branding(bid)
                    if brow:
                        # Rebind (don't mutate the possibly-shared config).
                        config = {**config, "clips": {**config["clips"],
                                  "watermark": _json.loads(brow["config"])}}
                        print(f"      Applying {creator_ctx.creator_name if creator_ctx else 'creator'}'s default branding")
            # Gaming / Reaction: the webcam and game area set for this creator
            # in the editor, so their next videos need no setup (gaming/run.py).
            if (modes.is_gaming(config) and config["clips"].get("gaming_remember")
                    and config["clips"].get("gaming_layout")):
                # Set up before processing with "remember for this creator".
                db.set_creator_gaming_layout(
                    creator_id, {k: v for k, v in config["clips"]["gaming_layout"].items() if k != "by"})
            if modes.is_gaming(config) and "gaming_layout" not in config["clips"]:
                saved = db.creator_gaming_layout(creator_id)
                if saved:
                    config = {**config, "clips": {**config["clips"], "gaming_layout": saved}}
    except Exception as e:
        print(f"      (creator tagging failed: {e})")
    # Shorts made before, not just "done": a Longform run marks a video done
    # too, and its Shorts are still to be made (#98).
    if db.shorts_made(video.video_id) and not force:
        print("      Already processed (Shorts made before). Use --force to redo.")
        return []
    db.set_video_status(video.video_id, "downloaded")
    cancel.check(video.video_id)

    # Audio/visual signal extraction needs no transcript, and it's FFmpeg +
    # numpy work while Whisper occupies the GPU compute — so it runs in the
    # background DURING transcription and the analysis stage gets it for free.
    # Best-effort: on any error, analysis recomputes and reports it properly.
    import threading

    signals_out: dict = {}

    def _extract_signals() -> None:
        try:
            from analysis.audio_features import extract_audio_features
            from analysis.visual_features import extract_visual_features

            audio_raw = extract_audio_features(video.path)
            visual_raw = extract_visual_features(video.path)
            signals_out["signals"] = (audio_raw, visual_raw)
        except cancel.CancelledError:
            return  # the video was cancelled: its decode stopped with it
        except Exception as e:
            print(f"      (background signal extraction failed, will retry in analysis: {e})")

    signals_thread = threading.Thread(
        target=_extract_signals, daemon=True, name="signals-prepass"
    )
    signals_thread.start()

    # Audience hype (chat replay speed / YouTube most-replayed) fetched in
    # the background too — pure network wait, free during transcription.
    # Optional signal: any failure just means no bonus.
    hype_out: dict = {}
    gaming_scoring = modes.gaming_scoring(config)
    # A match (the Sports toggle, sports/): scored for its moments with the
    # same evidence a gaming stream gets, plus the sport's own.
    sport_name = modes.sport(config)
    known_games = list(getattr(video, "games", None) or []) or db.video_games(video.video_id)

    def _fetch_hype() -> None:
        try:
            from analysis.hype import audience_signals

            curve, messages = audience_signals(url, video.video_id, video.duration)
            if curve is not None:
                hype_out["curve"] = curve
            # What chat said, for a gaming stream's reading of its reactions.
            if (gaming_scoring or sport_name) and messages:
                hype_out["messages"] = messages
        except Exception as e:
            print(f"      (audience hype fetch failed: {e})")
        if gaming_scoring and not known_games:
            # A file cached before the game was recorded: ask the platform once.
            from sources.dispatch import game_info

            hype_out["games"] = game_info(url)

    hype_thread = threading.Thread(target=_fetch_hype, daemon=True, name="hype-prepass")
    hype_thread.start()

    # A gaming stream's own sound (gunfire, a goal, a crash), listened for
    # while Whisper runs: a small model, minutes of work on a CPU for a long
    # VOD. Optional: without the model the stream is scored without it.
    sounds_out: dict = {}
    sounds_thread = None
    if gaming_scoring and not sport_name:
        def _listen() -> None:
            try:
                from analysis import game_audio, gaming, panns

                if not panns.available():
                    print("      (game sounds: the sound model isn't installed, scoring without it)")
                    return
                groups = gaming.knowledge().get("sound_groups") or {}
                sounds_out["heard"] = game_audio.listen(video.path, groups)
            except Exception as e:
                print(f"      (game sounds unavailable: {e})")

        sounds_thread = threading.Thread(target=_listen, daemon=True, name="game-sounds-prepass")
        sounds_thread.start()

    # A match's own reading (its sound, and soccer's score box), done while
    # Whisper runs.
    match = MatchReading(config, video) if sport_name else None

    print("[2/4] Transcribing...")
    progress.emit(stage="transcribe", video_id=video.video_id, title=video.title)
    # Content language: "auto" lets Whisper detect; a forced code fixes
    # bilingual streams (e.g. Hindi speech over English game audio) where
    # detection picks the wrong language and every caption burns wrong.
    forced_lang = (config.get("content_language") or "auto").lower()
    hint = None
    if sport_name:
        # The names a sport's video spells (basketball: its players, from its
        # title and description), for Whisper to listen for.
        hint = _listening_for(config, video, url)
        if hint:
            print(f"      Listening for: {hint[:120]}{'…' if len(hint) > 120 else ''}")
    segments = transcribe(
        video.path,
        video.video_id,
        data_dir / "transcripts",
        model_size=config["whisper"]["model"],
        device=config["whisper"]["device"],
        language=None if forced_lang == "auto" else forced_lang,
        online=online_transcription(config),
        **({"hotwords": hint} if hint else {}),
    )
    from transcription.transcriber import detected_language

    content_lang = forced_lang if forced_lang != "auto" else detected_language(
        video.video_id, data_dir / "transcripts"
    )
    if content_lang != "en":
        print(f"      Content language: {content_lang}")
    print(f"      {len(segments)} segments")
    db.set_video_status(video.video_id, "transcribed")

    cancel.check(video.video_id)
    print("[3/4] Multimodal analysis (transcript + audio + visual)...")
    progress.emit(stage="analyze", video_id=video.video_id)
    # Waited for the way Cancel can interrupt (#113): on a long video these
    # passes can still be running, and a plain join() didn't listen.
    cancel.wait(signals_thread, video_id=video.video_id)  # usually already done — transcription takes longer
    cancel.wait(hype_thread, 60, video.video_id)  # network fetch; hard cap so it never stalls
    if sounds_thread is not None:
        cancel.wait(sounds_thread, 900, video.video_id)  # done long before Whisper, bar a stuck decode
    llm = create_backend(_with_usable_model(config["llm"]))
    gaming_profile, sport_profile, chat, sounds = None, None, None, None
    if match is not None:
        sport_profile, chat, sounds = match.finish(hype_out)
    elif gaming_scoring:
        gaming_profile, chat, sounds = _gaming_scoring_inputs(
            config, video, db, known_games, hype_out, sounds_out.get("heard"))
    intent = clip_direction(config, llm, video.duration)
    candidates, rejections = find_clips(
        video.path, segments, llm, config,
        signals=signals_out.get("signals"),
        creator_context=creator_ctx,
        weight_bias=(creator_prefs or {}).get("weight_bias"),
        audience=hype_out.get("curve"),
        **({"measure_reaction": False} if not modes.measures_reaction(config) else {}),
        **({"gaming": gaming_profile, "chat": chat, "sounds": sounds}
           if gaming_profile is not None else {}),
        **({"sport": sport_profile, "chat": chat, "sounds": sounds}
           if sport_profile is not None else {}),
        **({"intent": intent} if intent is not None else {}),
    )
    for r in rejections:
        db.log_rejection(
            video.video_id,
            r.candidate.start, r.candidate.end, r.candidate.score, r.reason,
            kept_start=r.kept.start if r.kept else None,
            kept_end=r.kept.end if r.kept else None,
            subscores=r.candidate.subscores,
        )
    dup_count = sum(1 for r in rejections if r.reason not in ("below_min_score", "over_limit"))
    if dup_count:
        print(f"      Rejected {dup_count} duplicate/overlapping candidate(s) (logged)")
    db.set_video_status(video.video_id, "analyzed")

    outcome = summarise_run(candidates, rejections, config)
    if intent is not None:
        # What the direction was understood as, and what it couldn't find,
        # where the clip page shows it.
        outcome["intent"] = intent.report()
    if sport_profile is not None and getattr(sport_profile, "report_data", None):
        # What the match gave (goals found, the score read, replays grouped).
        outcome["sport"] = sport_profile.report_data
    db.set_outcome(video.video_id, outcome)

    if not candidates:
        # This is the single most common "bug" report: a finished run, no
        # error, and no clips. The reason is knowable -- it is right here in
        # the scores -- so record it where the UI can read it instead of only
        # printing it to a log nobody opens.
        print(f"      {explain_no_clips(outcome)}")
        db.set_video_status(video.video_id, "done")
        return []
    for c in candidates:
        s = c.subscores or {}
        breakdown = (
            f"text {s.get('text', '?')} | audio {s.get('audio', '?')} | "
            f"visual {s.get('visual', '?')} | reaction {s.get('reaction', '?')} | "
            f"engage {s.get('engagement', '?')} | {c.source}"
            + (f" | direction +{s['intent']}: {s.get('intent_why', '')}" if s.get("intent") else "")
            + (f" | kept as asked: {s['required']}" if s.get("required") else "")
        )
        print(f"      [{c.score:3d}] {c.start:7.1f}s - {c.end:7.1f}s  {c.hook}")
        print(f"            ({breakdown})")

    print("[4/4] Rendering clips...")
    rendered = []
    # This run's clips, the re-rendered ones too (a re-run registers nothing
    # new): a match's story reels are joined from them.
    made = []
    # Human-browsable layout: clips/<channel>/<video title> [id]/clip_*.mp4
    clip_dir = (
        data_dir / "clips"
        / _safe_name(video.channel, "unknown-channel")
        / f"{_safe_name(video.title, video.video_id)} [{video.video_id}]"
    )
    # Titles/descriptions/hashtags for ALL clips in a few batched LLM calls
    # (one call per clip made long streams crawl through analysis).
    print(f"      Writing titles & hashtags for {len(candidates)} clip(s) (batched)...")
    from video import post_style as _post_style

    post_style = _post_style.resolve(config["clips"].get("caption_style"))
    # A sport's own rules for its clips' titles (basketball: which player to name).
    title_rules = getattr(sport_profile, "title_rules", None) if sport_profile is not None else None
    metas = generate_metadata_batch(
        candidates, segments, video.title, llm,
        creator_context=(creator_ctx.summary if creator_ctx else ""),
        style=post_style,
        **({"rules": title_rules()} if title_rules is not None else {}),
    )
    # ...and its check of them against the game (basketball: who scored, who
    # leads): the ones that get it wrong are written again with these rules.
    check_titles = getattr(sport_profile, "check_titles", None) if sport_profile is not None else None
    if check_titles is not None:
        # The same post style, so a rewritten Highlights clip keeps its card lines.
        metas = check_titles(candidates, metas, lambda clips, rules: generate_metadata_batch(
            clips, segments, video.title, llm,
            creator_context=(creator_ctx.summary if creator_ctx else ""), rules=rules,
            style=post_style))

    # Hashtags the request insisted on (chat: "put #creatorname on all of
    # them"). Appended after generation rather than asked of the model: a
    # required tag that the LLM sometimes forgets is not required. Order keeps
    # the model's own tags first, and a tag it happened to pick anyway is not
    # repeated.
    required = config["clips"].get("required_hashtags") or []
    if required:
        from analysis.metadata import _clean_hashtags

        extra = _clean_hashtags(required)
        for meta in metas:
            have = {t.casefold() for t in meta.hashtags}
            meta.hashtags = meta.hashtags + [t for t in extra if t.casefold() not in have]
            # "titles AND descriptions must have it in them", so the tag goes
            # on the title too, not just the tag list. YouTube rejects a title
            # over 100 characters, so a tag that will not fit is left to the
            # description rather than costing the clip its upload.
            for tag in extra:
                if tag.casefold() in meta.title.casefold():
                    continue
                if len(meta.title) + len(tag) + 1 <= 100:
                    meta.title = f"{meta.title} {tag}"

    # Creator learning runs in the background WHILE clips render — renders
    # don't use Ollama, so this pass is wall-clock free. It extracts durable
    # facts/events for FUTURE videos and never touches this run's clips.
    knowledge_thread = None
    if creator_id is not None:

        def _learn() -> None:
            try:
                from core.state import StateDB as _DB
                from creator import extractor

                kdb = _DB(data_dir / "state.db")  # sqlite: own connection per thread
                try:
                    n = extractor.extract_and_store(
                        kdb, creator_id, video.video_id, segments, llm
                    )
                finally:
                    kdb.conn.close()
                if n:
                    print(f"      Learned {n} new fact(s)/event(s) about {video.channel}")
            except Exception as e:
                print(f"      (creator learning failed: {e})")

        knowledge_thread = threading.Thread(target=_learn, daemon=True, name="creator-learning")
        knowledge_thread.start()

    # Renders run in parallel: one clip's (GPU) tracking overlaps another's
    # (NVENC) encode. File work happens in worker threads; SQLite writes stay
    # on this thread — sqlite connections are not shareable across threads.
    from concurrent.futures import ThreadPoolExecutor, as_completed

    workers = max(1, int(config.get("video", {}).get("parallel_renders", 2)))
    if modes.needs_framing(config):
        # Only the tracked render pipes frames through OpenCV/torch; a
        # Vertical Live render is FFmpeg alone and needs neither loaded.
        _share_the_cpu(workers)
    done_count = 0
    # One cause usually breaks every clip in the same way. Printing FFmpeg's
    # full output forty times buries the one fact that matters, so identical
    # reasons are counted and reported once at the end.
    last_failure: str | None = None
    repeated_failures = 0
    # Gaming / Split-Screen: find the streamer's webcam once, from several of
    # the video's clips together, before any of them renders. Off -> None,
    # exactly the argument every render has always been given.
    gaming_opts = _gaming_prepare(video.path, candidates, clip_dir, config) if modes.is_gaming(config) else None

    def _clip_opts(meta) -> dict | None:
        """One clip's render options: the job's, plus its title card when the
        post style draws one (written for that style above)."""
        if post_style != _post_style.HIGHLIGHTS:
            return gaming_opts
        return {**(gaming_opts or {}),
                "headline": meta.headline or _post_style.headline_from_title(meta.title),
                "subline": meta.subline}

    def _finish(candidate, meta, get_result) -> None:
        nonlocal done_count, last_failure, repeated_failures
        done_count += 1
        progress.emit(
            stage="render", video_id=video.video_id, clip=done_count, total=len(candidates)
        )
        try:
            final_path, render_opts_json = get_result()
        except Exception as e:
            where = f"{candidate.start:.0f}s-{candidate.end:.0f}s"
            reason = _render_failure_reason(e)
            if reason == last_failure:
                repeated_failures += 1      # reported once, after the loop
            else:
                last_failure = reason
                repeated_failures = 0
                print(f"      Render failed for {where}: {reason}")
            return
        clip = _register_clip(db, video.video_id, candidate, final_path, meta,
                              render_opts_json, config)
        if clip:
            rendered.append(clip)
        made.append(clip or RenderedClip(source_video_id=video.video_id, candidate=candidate, path=final_path))

    # Remote rendering (Settings -> Advanced settings): None unless it is on
    # and not set to this computer, so this is the local loop as always.
    remote = _remote_renderer(config)
    if remote is None:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {
                pool.submit(
                    _render_files, video.path, candidate, segments, clip_dir, config,
                    _clip_opts(meta), content_lang,
                ): (candidate, meta)
                for candidate, meta in zip(candidates, metas)
            }
            for future in as_completed(futures):
                # Every render is submitted up front, so cancelling has to reach
                # the workers too — _render_files checks on entry, which lets the
                # not-yet-started ones fall straight through. This stops us
                # registering clips for a video the user has given up on.
                cancel.check_active()
                candidate, meta = futures[future]
                _finish(candidate, meta, future.result)
    else:
        for candidate, meta, get_result in remote.render_all(
            video.video_id, video.path, list(zip(candidates, metas)), segments, clip_dir, config,
            gaming_opts, content_lang, workers, opts_for=_clip_opts,
        ):
            _finish(candidate, meta, get_result)

    if repeated_failures:
        print(f"      ({repeated_failures} more clip(s) failed the same way)")

    if knowledge_thread is not None:
        knowledge_thread.join(timeout=600)  # normally finished during renders

    # A match's story reels, when asked for (sports/core/reels.py): joined
    # from the clips just rendered.
    if sport_profile is not None and made and (sport_profile.option or {}).get("reels"):
        rendered += _sport_reels(db, video.video_id, sport_profile, made, clip_dir, config, segments)

    elapsed = time.monotonic() - started
    db.set_process_seconds(video.video_id, elapsed)
    db.set_video_status(video.video_id, "done")
    progress.emit(
        stage="done", video_id=video.video_id, clips=len(rendered), seconds=round(elapsed, 1)
    )
    from video import outro as _outro

    if (_line := _outro.summary()):
        print(f"      {_line}")
    print(f"      Done in {elapsed / 60:.1f} min ({len(rendered)} clips)")
    return rendered


def _sport_reels(db: StateDB, video_id: str, profile, clips: list, clip_dir: Path, config: dict,
                 segments: list | None = None, start: float = 0.0, opts: dict | None = None) -> list:
    """The story reels a Sports job asked for, joined from this run's
    `clips` and registered as clips of their own: the recap, a reel per
    team, per player (the transcript's `segments` say which clips' commentary
    names a player typed in Teams or players). Longform's 16:9 reels pass
    its `start` nudge and its profile in `opts`. Failure-safe: a reel that
    can't be joined is skipped, and the clips stay as they are.

    A reel is known by what it is (its kind, its team or player, its
    format), not by its length: a re-run makes it again into its own row and
    file, and a reel never takes another clip's row because it is as long."""
    reel_clips = []
    try:
        from analysis.metadata import ClipMetadata
        from sports.core import reels
        from video import outro

        report = getattr(profile, "report_data", None) or {}

        def said(clip) -> str:
            lo, hi = clip.candidate.start, clip.candidate.end
            return " ".join(s.text for s in segments or [] if s.end > lo and s.start < hi)

        planned = reels.plan(clips, profile.option.get("reels"), report.get("score", ""),
                             profile.option.get("teams", ""), said if segments else None)
        labels = {"recap": "Match recap", "team": "Team reel", "player": "Player reel"}
        card = outro.enabled(config)
        before = _reel_rows(db, video_id)
        for reel in planned:
            label = labels[reel.kind]
            # Each clip's end card off (a clip whose card was skipped keeps
            # all of it), and one at the end of the reel, made the way a
            # clip's is: outro.finish writes the reel from the join.
            paths = [c.path for c in reel.parts]
            trims = [outro.DURATION if outro.has_outro(c.path, c.candidate.end - c.candidate.start) else 0.0
                     for c in reel.parts]
            out = clip_dir / reels.file_name(reel)
            joined = out.with_name(f"{out.stem}.pre-card.mp4") if card else out
            try:
                lengths = reels.join(paths, joined, trims)
            except Exception as e:
                if card:
                    discard(joined)
                print(f"      ({label.lower()} not made: {e})")
                continue
            if card:
                outro.finish(joined, out, config)
            scores = {"sport_reel": reel.kind, "sport_label": label, "sport_parts": len(reel.parts)}
            chapters = reels.chapters(reel.parts, lengths)
            row = before.get((reel.kind, reel.subject, (opts or {}).get("profile")))
            # Its own length, not the source's span: the card shows it.
            end = _free_end(db, video_id, start, start + sum(lengths), row["id"] if row else None)
            if row is not None:
                # Made again: its title is kept (it may have been edited),
                # its chapters are this join's.
                db.set_clip(row["id"], path=str(out), end_s=end, scores=json.dumps(scores), description=chapters)
                print(f"      {label} made again: {row['title']} ({len(reel.parts)} moments, {sum(lengths):.0f}s)")
                continue
            candidate = ClipCandidate(start=start, end=end, score=max(c.candidate.score for c in reel.parts),
                                      hook=reel.title, subscores=scores)
            meta = ClipMetadata(title=reel.title, description=chapters, hashtags=[])
            known = {**(opts or {}), "reel": reel.kind, **({"of": reel.subject} if reel.subject else {})}
            clip = _register_clip(db, video_id, candidate, out, meta, json.dumps(known), config)
            if clip:
                reel_clips.append(clip)
                print(f"      {label}: {reel.title} ({len(reel.parts)} moments, {sum(lengths):.0f}s)")
    except Exception as e:
        print(f"      (story reels not made: {e})")
    return reel_clips


def _reel_rows(db: StateDB, video_id: str) -> dict:
    """The video's story reels already made, by (kind, team or player,
    Longform profile or None)."""
    rows = {}
    for row in db.clips_for_video(video_id):
        try:
            opts = json.loads(row["render_opts"] or "{}")
        except ValueError:
            continue
        if isinstance(opts, dict) and opts.get("reel"):
            rows[(opts["reel"], opts.get("of", ""), opts.get("profile"))] = row
    return rows


def _free_end(db: StateDB, video_id: str, start: float, end: float, own: int | None = None) -> float:
    """`end`, or the next hundredth after it that no other clip of the video
    has with this start: a clip's window is what its row is known by."""
    end = round(end, 2)
    while True:
        row = db.conn.execute(
            "SELECT id FROM clips WHERE video_id = ? AND start_s = ? AND end_s = ?",
            (video_id, round(start, 2), end),
        ).fetchone()
        if row is None or row["id"] == own:
            return end
        end = round(end + 0.01, 2)


def _remote_renderer(config: dict):
    """Remote rendering's dispatcher when it is on, else None. Never lets a
    problem with it stop a video: that renders here instead."""
    try:
        from remote_render import dispatch

        return dispatch.renderer_for(config)
    except Exception as e:
        print(f"      (remote rendering unavailable, rendering here: {e})")
        return None


def _vertical_live_requested(config: dict) -> bool:
    from core import modes

    return modes.is_vertical_live(config)


def _gaming_prepare(source: Path, candidates: list, clip_dir: Path, config: dict) -> dict | None:
    """Gaming / Split-Screen's once-per-video webcam search (gaming/run.py),
    as the render options every clip gets. Fails closed: on any error the
    clips still render, each deciding for itself or falling back to the
    standard layout."""
    try:
        from gaming import run as gaming_run

        return {"gaming": gaming_run.prepare(source, candidates, config, clip_dir)}
    except Exception as e:
        print(f"      (Gaming webcam search failed, each clip decides for itself: {e})")
        return {"gaming": {}}


def _try_gaming_render(intermediate: Path, render_path: Path, opts: dict, config: dict,
                       ass_path: Path | None, vf_extra: str, normalize: bool) -> dict | None:
    """One clip in the gaming layout (gaming/run.py). None means the standard
    renderer takes it: a camera filling the frame, or any error at all."""
    try:
        from gaming import run as gaming_run

        g = opts.get("gaming")
        return gaming_run.render(intermediate, render_path, g if isinstance(g, dict) else {}, config,
                                 ass_path=ass_path, vf_extra=vf_extra, normalize=normalize)
    except Exception as e:
        print(f"      (Gaming layout failed, using the standard layout: {e})")
        return None


def _gaming_scoring_inputs(config: dict, video, db: StateDB, games: list, hype_out: dict,
                           heard: dict | None = None):
    """(the gaming profile, what chat's reactions mark, what the game's sound
    marks) for a gaming stream. Failure-safe: anything that goes wrong scores
    it as a gaming stream with whatever is known, never as nothing."""
    from analysis import gaming

    try:
        if not games and hype_out.get("games"):
            games = hype_out["games"]
            db.set_video_games(video.video_id, games)
        profile = gaming.profile_for(config, games, video.title)
    except Exception as e:
        print(f"      (gaming profile: {e}; scoring as a generic game)")
        profile = gaming.GamingProfile(weights=dict(gaming.STANDARD_WEIGHTS))
    chat = None
    if hype_out.get("messages"):
        try:
            from analysis.chat_moments import chat_signal

            k = gaming.knowledge()
            chat = chat_signal(hype_out["messages"], video.duration, k.get("chat_classes") or {},
                               float(k.get("chat_lag_seconds", 6)), k.get("chat_ignore") or [])
        except Exception as e:
            print(f"      (chat moments unavailable: {e})")
    sounds = None
    if heard:
        try:
            from analysis.game_audio import sound_signal

            k = gaming.knowledge()
            seconds = max(v.size for v in heard.values())
            # Weighted for the game played in each part of the stream.
            sounds = sound_signal(heard, k.get("sound_groups") or {}, profile.genre_track(seconds),
                                  k.get("genre_sounds") or {})
        except Exception as e:
            print(f"      (game sounds unavailable: {e})")
    return profile, chat, sounds


class MatchReading:
    """A Sports job's reading of the video beside transcription (sports/): the
    match's sound (the crowd and the whistle, from the sound model) and the
    sport's own pass (soccer: the score box), both started before Whisper and
    finished into what find_clips takes.

    The Shorts and the Longform paths share it, so a match is read the same
    way whichever output it is for. Each part is optional: whatever can't be
    read, the match is scored without."""

    def __init__(self, config: dict, video):
        from core import modes

        self.config, self.video = config, video
        self.name = modes.sport(config)
        self._heard: dict = {}
        self._read: dict = {}
        self._threads = [
            threading.Thread(target=self._listen, daemon=True, name="game-sounds-prepass"),
            threading.Thread(target=self._prepass, daemon=True, name="sport-prepass"),
        ]
        for thread in self._threads:
            thread.start()

    def _listen(self) -> None:
        try:
            import sports
            from analysis import game_audio, panns

            if not panns.available():
                print("      (match sounds: the sound model isn't installed, scoring without it)")
                return
            self._heard["heard"] = game_audio.listen(self.video.path, sports.sound_groups(self.name))
        except Exception as e:
            print(f"      (match sounds unavailable: {e})")

    def _prepass(self) -> None:
        try:
            import sports

            self._read.update(sports.prepass(self.config, self.video.path, self.video.duration))
        except Exception as e:
            print(f"      ({self.name}: reading the video failed: {e})")

    def finish(self, hype_out: dict | None = None):
        """(the sport's profile, what chat's reactions mark, what the match's
        sound marks), once both passes are done: long before Whisper, bar a
        stuck decode, or a sport whose own pass takes longer on a long video
        (sports.prepass_wait: basketball's scoreboard took 25 minutes on a
        79-minute game)."""
        import sports

        waits = (900, sports.prepass_wait(self.config, float(getattr(self.video, "duration", 0) or 0)))
        for thread, wait in zip(self._threads, waits):
            cancel.wait(thread, wait, self.video.video_id)
            if thread.is_alive():
                what = "the match's sound" if thread is self._threads[0] else "its reading of the video"
                print(f"      ({self.name}: {what} still running after {wait:.0f}s; going on without it)")
        return _sport_inputs(self.config, self.video, hype_out or {}, self._heard.get("heard"), self._read)


def _sport_inputs(config: dict, video, hype_out: dict, heard: dict | None, prepass: dict):
    """(the sport's profile, what chat's reactions mark, what the match's
    sound marks) for a job with a sport. The profile carries the crowd and
    the whistle as curves of their own and whatever the sport's prepass read
    (soccer: the scoreboard). Failure-safe: a match with none of it is still
    scored, as a match."""
    import sports
    from analysis import gaming

    profile = sports.profile_for(config, video)
    for key, value in (prepass or {}).items():
        setattr(profile, key, value)
    chat = None
    if hype_out.get("messages"):
        try:
            from analysis.chat_moments import chat_signal

            k = gaming.knowledge()
            chat = chat_signal(hype_out["messages"], video.duration, k.get("chat_classes") or {},
                               float(k.get("chat_lag_seconds", 6)), k.get("chat_ignore") or [])
        except Exception as e:
            print(f"      (chat moments unavailable: {e})")
    sounds = None
    profile.curves = {}
    if heard:
        try:
            from analysis.game_audio import sound_signal

            groups = sports.sound_groups(profile.name)
            seconds = max(v.size for v in heard.values())
            track = profile.genre_track(seconds)
            sounds = sound_signal(heard, groups, track, profile.sound_weights())
            # The crowd and the whistle (and a sport's own, like basketball's
            # buzzer) each as a curve of their own: the moments are typed by
            # which of them agree.
            for name in profile.sound_curves():
                if name in heard:
                    one = sound_signal({name: heard[name]}, groups, track, {profile.name: {name: 1.0}})
                    if one is not None:
                        profile.curves[name] = one.game
            # ...and how sure the sound model is that a crowd is cheering, as
            # it heard it: what dates a goal the scoreboard confirms.
            if "crowd" in heard:
                profile.curves["crowd_heard"] = heard["crowd"]
        except Exception as e:
            print(f"      (match sounds unavailable: {e})")
    board = getattr(profile, "board", None)
    if board is not None and board.box:
        teams, final = board.teams(), board.final()
        # "goal(s)", "basket(s)": the sport's own word for a score.
        scored = (profile.event_label(profile.scoring_types[0]).lower()
                  if getattr(profile, "scoring_types", None) else "goal")
        print(f"      Scoreboard: {len(board.changes)} {scored}(s) read"
              + (f", {teams[0]} v {teams[1]}" if teams else "")
              + (f", {final[0]}-{final[1]} at the end" if final else ""))
    resolve = getattr(profile, "resolve_footage", None)
    if resolve is not None and resolve(board) == "sideline":
        chosen = (profile.option or {}).get("footage") == "sideline"
        print("      Footage: club or phone" + ("" if chosen else " (no score box on screen)"))
    return profile, chat, sounds


def _listening_for(config: dict, video, url: str) -> str | None:
    """The names Whisper listens for in a sport's video (sports.hotwords). A
    sport that reads the video's description (basketball: its players, and
    who won) gets it asked for when the download was reused: a file already
    on disk comes back without one (_cached_or_download), and on an NBA game
    Whisper then listened for the two teams alone."""
    import sports

    if not getattr(video, "description", "") and sports.reads_description(config):
        from sources.dispatch import description

        video.description = description(url)
    return sports.hotwords(config, video)


def _cached_or_download(url: str, data_dir: Path, db: StateDB, vertical: bool = False):
    """Reprocessing must never depend on the platform being reachable: when
    the source file is already on disk, use it (with title/channel from the
    DB) instead of re-contacting YouTube/Twitch — which can rate-limit or
    bot-block repeat requests."""
    from core.models import DownloadedVideo
    from sources import dispatch

    source, video_id = dispatch.identify(url)
    # Vertical Live keeps its own copy (sources/vertical.py): the horizontal
    # file of the same video must never stand in for the vertical one. An
    # uploaded file is what it is, so it has only the one copy.
    if vertical and source != "local":
        from sources.vertical import SUFFIX

        cached = cached_source(data_dir / "downloads", f"{video_id}{SUFFIX}")
    else:
        cached = cached_source(data_dir / "downloads", video_id)
    if cached is None:
        return dispatch.download(url, data_dir / "downloads", vertical=vertical)

    if source == "local":
        # A copy whose import was stopped part-way has no index and cannot be
        # read. It used to pass as "already downloaded", and the job then died
        # in transcription with PyAV's InvalidDataError, which reads like a
        # codec problem and is not one (#122). New imports cannot leave one
        # behind; this is for copies that older versions already did.
        from video.encoding import readable_video

        if not readable_video(cached):
            discard(cached)
            raise ValueError(
                "This file did not finish importing, so its copy could not be read. "
                "Remove it from the queue and add the file again."
            )

    import subprocess

    # Cached files from before the H.264-only YouTube selector can be AV1 —
    # every analysis/render pass software-decodes those, which once made a
    # 25-min video slower than a 2-hour H.264 VOD. Swap for H.264 while the
    # platform is reachable; otherwise the slow cached copy still works.
    codec = subprocess.run(
        [ffprobe(), "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=codec_name", "-of", "csv=p=0", str(cached)],
        capture_output=True, text=True,
    ).stdout.strip()
    if codec in ("av1", "vp9") and source != "local":  # a file from the PC has nowhere to come from again
        print(f"      Cached source is {codec} (slow to decode) — re-downloading as H.264")
        try:
            fresh = dispatch.download(url, data_dir / "downloads", vertical=vertical)
            # The replacement usually lands as .mp4 while the slow copy was
            # .webm, and yt-dlp writes the new name rather than overwriting
            # the old one — so without this the video is on disk twice, at a
            # couple of GB each. Only the file we just replaced is removed.
            if fresh.path.resolve() != cached.resolve() and cached.exists():
                discard(cached)
                print(f"      Removed the superseded {cached.suffix} copy")
            return fresh
        except Exception:
            print("      Re-download failed — using the cached copy")

    row = db.conn.execute(
        "SELECT title, channel_name FROM videos WHERE video_id = ?", (video_id,)
    ).fetchone()
    title = (row["title"] if row and row["title"] else "") or ""
    channel = (row["channel_name"] if row else "") or ""

    # A cached file usually means a row written when it was downloaded. Not
    # always: the database can be reset, moved, or lost while downloads/
    # survives, and files get copied in by hand. Falling back to the ID there
    # is not just an ugly label — an empty channel attaches the video to no
    # creator, so catchphrase learning and preference history silently never
    # run on it. One metadata request is cheap next to re-fetching several GB.
    if not title or title == video_id or not channel:
        try:
            fetched_title, fetched_channel = dispatch.metadata(url)
            title = title if title and title != video_id else fetched_title
            channel = channel or fetched_channel
            if row and (title != (row["title"] or "") or channel != (row["channel_name"] or "")):
                db.conn.execute(
                    "UPDATE videos SET title = ?, channel_name = ? WHERE video_id = ?",
                    (title, channel, video_id),
                )
                db.conn.commit()
        except Exception as e:
            # Offline, rate-limited, or a local upload. The whole point of this
            # branch is that reprocessing works without the platform, so this
            # stays non-fatal and the ID remains the fallback.
            print(f"      (could not fetch title/channel: {e})")

    probe = subprocess.run(
        [ffprobe(), "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(cached)],
        capture_output=True, text=True,
    )
    duration = float(probe.stdout.strip() or 0)
    print("      Source already downloaded — skipping the download")
    return DownloadedVideo(
        video_id=video_id,
        title=title or video_id,
        path=cached,
        duration=duration,
        channel=channel,
    )


def _safe_name(name: str, fallback: str) -> str:
    """Make a name safe as a Windows folder: strip reserved characters,
    trailing dots/spaces, and overlong text."""
    cleaned = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "", name).strip().rstrip(". ")
    return cleaned[:60].strip() or fallback


def _render_files(
    source: Path,
    candidate: ClipCandidate,
    segments: list[Segment],
    clip_dir: Path,
    config: dict,
    render_opts: dict | None = None,
    content_language: str = "en",
) -> tuple[Path, str]:
    """Pure file work — cut, track, crop, captions, color. NO database access
    and NO LLM call, so it is safe to run in a worker thread. Returns the
    finished clip path and the persisted render-options JSON.

    render_opts (all optional, persisted per clip, set by the user or the AI
    edit assistant): captions, caption_style, caption_lines, crop, filter,
    adjust.
    """
    from video.filters import combined_chain

    # Rendering a clip is FFmpeg plus per-frame tracking — minutes each, and a
    # video can queue a dozen. Checking on entry means a cancelled job burns
    # through its remaining queue instead of rendering clips nobody will see.
    # Safe for the API's re-render paths too: this only fires when the video
    # the worker is actively processing has been cancelled.
    cancel.check_active()

    opts = render_opts or {}
    # Deterministic timestamp-based name: re-runs overwrite instead of piling up.
    stem = f"clip_{int(candidate.start):05d}-{int(candidate.end):05d}"
    final_path = clip_dir / f"{stem}.mp4"
    clip_dir.mkdir(parents=True, exist_ok=True)

    # The end card is part of PRODUCING the clip, not something applied to it
    # afterwards -- the same way CapCut exports its outro. So when it is on,
    # everything below renders to a scratch file and the final clip is written
    # once, by the concat in outro.finish(), already containing the card.
    #
    # This is not a style preference. A clip open in the app's preview holds a
    # Windows handle that forbids DELETING the file but permits WRITING it, so
    # every version that replaced a finished clip lost its card to whichever
    # clips you happened to be looking at -- one run managed 37 of 49 and spent
    # twelve minutes stalling on locks it could never win.
    from video import outro as _outro

    wants_card = _outro.enabled(config)
    render_path = clip_dir / f"{stem}.pre-card.mp4" if wants_card else final_path

    # Longform rendering profile (render_opts["profile"], set only by the
    # longform module): 16:9 1920x1080 output, no vertical crop/tracking.
    # Absent for every existing Shorts clip — their path is unchanged.
    landscape = bool(opts.get("profile"))
    # Loudness: on unless this clip opted out (see settings/editor).
    normalize = bool(opts.get("normalize_audio", True))
    # Podcast: opt-in, per video. Multi-cam/multi-person footage renders as a
    # steady full-frame letterbox instead of tracking a subject (see
    # video/podcast.py). Read from the job config or a persisted per-clip flag.
    # When false the tracking path below is entered exactly as before.
    podcast = bool(opts.get("podcast") or config["clips"].get("podcast"))
    # Vertical Live (core/modes.py): the source is already a finished 9:16
    # composition. One encode of the whole frame at 1080x1920: no tracking,
    # no crop, no layout change. Read from the job config or the per-clip flag,
    # so editor re-renders keep it.
    from core import modes

    vertical_live = not landscape and (modes.is_vertical_live(opts) or modes.is_vertical_live(config))
    # A match (the Sports toggle, sports/): framed by the ball and the play.
    # From the job or the clip's saved options, so re-renders keep it.
    sport_name = modes.sport(opts) or modes.sport(config)
    # Gaming / Split-Screen (gaming/): opt-in, per video or per clip. Tried
    # first inside the tracked branch; anything it declines or fails at goes
    # on to the standard layout below. Off -> never imported.
    gaming = (not landscape and not vertical_live and not podcast
              and (modes.is_gaming(opts) or modes.is_gaming(config)))
    gaming_kept = None
    canvas = (1920, 1080) if landscape else (1080, 1920)

    # Color: preset filter (per-clip wins over job/config default) + manual
    # brightness/saturation/contrast adjustments.
    filter_name = opts.get("filter") or config["clips"].get("filter") or "none"
    vf_extra = combined_chain(filter_name, opts.get("adjust"))
    if landscape:
        fit = (
            "scale=1920:1080:force_original_aspect_ratio=decrease:flags=lanczos,"
            "pad=1920:1080:(ow-iw)/2:(oh-ih)/2,setsar=1"
        )
        vf_extra = f"{fit},{vf_extra}" if vf_extra else fit
    elif vertical_live:
        # Nothing at all for a 1080x1920 source: the trim and captions need an
        # encode anyway, a rescale on top would only cost quality.
        fit = modes.fit_filter(*modes.probe_size(source))
        if fit:
            vf_extra = f"{fit},{vf_extra}" if vf_extra else fit

    # Manual edits from the Shorts editor (trim/cuts/mutes/volume/fades) —
    # non-destructive: stored in render_opts, applied fresh on every render.
    edit = None
    if opts.get("edit"):
        from video_editor.timeline import EditList

        edit = EditList.from_dict(opts["edit"], duration=candidate.duration)

    ass_path = None
    # Per-clip style wins; otherwise the job/config default chosen at generate time.
    caption_style = opts.get("caption_style") or config["clips"].get("caption_style")
    # Post style (video/post_style.py), chosen with the caption style. The
    # highlights look keeps the clip's framing and adds its captions and
    # title card on top; vertical clips only.
    from video import post_style as _post_style

    highlights = not landscape and _post_style.resolve(caption_style) == _post_style.HIGHLIGHTS
    # Where someone other than the main speaker talks, when the caption style
    # asks for them in a second colour (#126). Empty for every other clip.
    turns: list = []
    if config["clips"].get("captions", True) and opts.get("captions", True):
        lines = opts.get("caption_lines")  # user-corrected caption text, if any
        if _wants_second_speaker(config, opts):
            from video.captions import DEFAULT_STYLE, build_caption_lines, paint_turns, tag_lines, words_of

            turns = _speaker_turns(source, candidate, segments, config, opts)
            # What was heard, with what a person said in the editor laid over
            # it (speaker_edits): theirs is the last word on any stretch.
            said = paint_turns(turns, opts.get("speaker_edits"))
            # Lines end where the speaker changes; saved ones keep the user's
            # grouping and are marked with who says them. Before the cuts
            # below: the turns are timed on the clip as it was heard.
            if lines is not None:
                # Also with nobody else talking: a mark an earlier render left
                # on a saved line must not colour it now.
                lines = tag_lines(lines, said, words_of(segments, candidate))
            elif said:
                wpc = {**DEFAULT_STYLE, **(caption_style or {})}["words_per_caption"]
                lines = build_caption_lines(segments, candidate, wpc, said)
        if edit is not None and (edit.keep is not None or abs(edit.speed - 1) >= 0.01):
            # Sections were cut out and/or the clip was sped up: every
            # surviving caption shifts to its new time on the edited timeline.
            from video.captions import DEFAULT_STYLE, build_caption_lines
            from video_editor.captions import remap_lines

            if lines is None:
                wpc = {**DEFAULT_STYLE, **(caption_style or {})}["words_per_caption"]
                lines = build_caption_lines(segments, candidate, wpc)
            lines = remap_lines(lines, edit)
        ass_path = build_captions(
            segments, candidate, clip_dir / f"{stem}.ass",
            style=_post_style.caption_style_for(caption_style) if highlights else caption_style,
            lines=lines,
            canvas=canvas,
            language=content_language,
        )

    # Hook title (big text, top third, first few seconds) burns through the
    # same ASS/subtitles path as captions — correct at final resolution.
    if edit is not None and edit.hook:
        from video.captions import caption_font_for
        from video_editor.overlay import ensure_hook

        ass_path = ensure_hook(
            ass_path, clip_dir / f"{stem}.ass", edit.hook, canvas=canvas,
            font=caption_font_for(content_language, None) or "Arial Black",
        )

    # Watermark & branding (opts["watermark"], else the job/config default).
    # Text folds into the ASS burn now; the image overlay runs after the
    # final render. Absent -> no branding, path unchanged.
    from video_editor import watermark as _wm

    wm_cfg = opts["watermark"] if "watermark" in opts else config["clips"].get("watermark")
    wm_assets = Path(config["paths"]["data_dir"]) / "branding" / "assets"
    if wm_cfg and _wm.has_text(wm_cfg):
        ass_path = _wm.ensure_text(
            ass_path, clip_dir / f"{stem}.ass", wm_cfg, canvas, duration=candidate.duration
        )

    # Whisper's word timestamps often end a hair BEFORE the word is finished
    # being spoken, so a cut exactly at the last word's end clips its audio —
    # the caption shows the word but the voice cuts out. Pad the cut a beat
    # past the transcript end. Captions were already built above from the
    # unpadded window, so no extra words appear on screen.
    from dataclasses import replace

    padded = replace(candidate, end=candidate.end + 0.4)

    # Staging files for this render. Cleaned in a finally: a failed render, a
    # crash mid-tracking or a cancel used to leave its multi-hundred-MB
    # intermediate behind forever, and repeated testing quietly filled the
    # disk with them.
    scratch: list[Path] = []
    try:
        # Vertical Live takes the single-encode branch below, like longform:
        # the tracked path (intermediate cut, face tracking, TalkNet, layout
        # decisions, frames through Python) is never entered.
        if config["clips"].get("vertical", True) and not landscape and not vertical_live:
            # Cut a horizontal intermediate, track the subject, render 9:16.
            intermediate = clip_dir / f"{stem}.source.mp4"
            scratch.append(intermediate)
            cut_clip(source, padded, intermediate)

            if edit is not None:
                # Apply manual edits BEFORE tracking, so the tracker and
                # captions see the final (edited) timeline.
                from video_editor.export import apply_edits

                edited = clip_dir / f"{stem}.edited.mp4"
                scratch.append(edited)
                apply_edits(intermediate, edit, edited)
                discard(intermediate)
                intermediate = edited

            if gaming:
                gaming_kept = _try_gaming_render(intermediate, render_path, opts, config,
                                                 ass_path, vf_extra, normalize)
            if gaming_kept is not None:
                pass  # rendered in the gaming layout (split or game only)
            elif podcast:
                # Separate podcast path (video/podcast.py): tracked crop for a
                # single speaker, 50/50 split when two speakers can't share one
                # crop, tight-region letterbox only as a last resort — and cuts
                # SNAP instead of panning across the set. video/tracker.py is
                # never modified; the stream branch below is untouched.
                from video import podcast as podcast_mod

                tracking_cfg = config["tracking"]
                decision = podcast_mod.analyze(
                    intermediate,
                    model_name=tracking_cfg["detector"],
                    sample_fps=tracking_cfg["sample_fps"],
                )
                # The editor's Layout buttons still win on a podcast clip.
                crop_mode = opts.get("crop", "track")
                if crop_mode == "center":
                    decision = {"mode": "track", "path": [(0.0, 0.5)]}
                elif crop_mode == "letterbox":
                    decision = {"mode": "fit_blur", "region": None}
                podcast_mod.render_clip(
                    intermediate, render_path, decision, ass_path=ass_path,
                    vf_extra=vf_extra, normalize=normalize,
                )
            else:
                from video.cropper import render_vertical
                from video.tracker import compute_tracking  # lazy: imports torch

                crop_mode = opts.get("crop", "track")
                sport_framing = (_sport_framing(intermediate, config, sport_name)
                                 if sport_name and crop_mode in ("track", "bias_left", "bias_right")
                                 else None)
                if crop_mode == "center":
                    tracking = {"mode": "track", "path": [(0.0, 0.5)]}
                elif sport_framing is not None:
                    # A match (sports/): the ball and the play, not a face.
                    tracking = sport_framing
                    if crop_mode in ("bias_left", "bias_right"):
                        shift = -0.12 if crop_mode == "bias_left" else 0.12
                        tracking["path"] = [(t, x + shift) for t, x in tracking["path"]]
                else:
                    tracking_cfg = config["tracking"]
                    tracking = compute_tracking(
                        intermediate,
                        model_name=tracking_cfg["detector"],
                        sample_fps=tracking_cfg["sample_fps"],
                        force_fit_blur=(crop_mode == "letterbox"),
                    )
                    if crop_mode == "letterbox" and tracking["mode"] == "fit_blur":
                        # USER-forced letterbox means "show me the WHOLE frame" —
                        # reaction/gaming mixes need both the person and the
                        # content. Cropping tight to the detected person (the
                        # automatic letterbox behavior) threw away the game side.
                        tracking["region"] = None
                    if tracking["mode"] == "track" and crop_mode in ("bias_left", "bias_right"):
                        shift = -0.12 if crop_mode == "bias_left" else 0.12
                        tracking["path"] = [(t, x + shift) for t, x in tracking["path"]]
                render_vertical(
                    intermediate, tracking, render_path, ass_path=ass_path, vf_extra=vf_extra,
                    normalize=normalize,
                )
        else:
            if edit is not None:
                # Horizontal (or Vertical Live) output: cut plain first, then
                # apply edits and burn captions in the same pass (they land
                # AFTER the cuts).
                from video_editor.export import apply_edits

                plain = clip_dir / f"{stem}.plain.mp4"
                scratch.append(plain)
                cut_clip(source, padded, plain, vf_extra=vf_extra)
                apply_edits(plain, edit, render_path, ass_path=ass_path, normalize=normalize)
            else:
                cut_clip(source, padded, render_path, ass_path=ass_path, vf_extra=vf_extra,
                         normalize=normalize)
    finally:
        # NEVER raise from here. This runs in a `finally`, so an exception
        # would REPLACE whatever the render actually failed with — and the
        # caller treats any exception as a failed clip, so a clip that had
        # already been written would be thrown away too. That is issue #74:
        # a leaked decoder handle made these unlinks fail on Windows, and the
        # resulting WinError 32 buried the real error for every clip.
        for p in scratch:
            discard(p)

    if ass_path is not None:
        discard(ass_path)

    # Highlights title card: one overlay pass on the finished clip, like the
    # image watermark below (which then sits on top of it). The text is saved
    # with the clip (opts["headline"], opts["subline"]), so a re-render keeps
    # it and the editor can change it.
    if highlights and (opts.get("headline") or opts.get("subline")):
        # The editor's hook title is burned at the top in the pass above, and
        # a card laid over it there would hide it. A clip with a hook keeps
        # its card in the lower third instead.
        position = _post_style.card_position(caption_style)
        if position == "top" and edit is not None and edit.hook:
            position = "lower"
        _title_card(render_path, opts, position, clip_dir / f"{stem}.card.png", content_language)

    # Image watermark: one overlay pass on the finished clip (only when set).
    if wm_cfg and _wm.has_image(wm_cfg, wm_assets):
        _wm.apply_image(render_path, wm_cfg, canvas, wm_assets)

    # Writes final_path complete, with the card. Never raises and never loses
    # the clip: if the card cannot be made it still writes final_path without
    # one, because every step here WRITES the destination rather than
    # replacing it, which is what a held file permits.
    if wants_card:
        _outro.finish(render_path, final_path, config)

    # A clip the gaming layout handed to the standard renderer (the camera
    # filled the frame) is saved as what it got, so the editor shows that and
    # a re-render frames it the same way.
    if gaming and gaming_kept is None:
        opts = {k: v for k, v in opts.items() if k != "gaming"}
    # The other speaker's turns are the ones this file was burned with, or
    # none: never ones left over from an earlier render.
    opts = {k: v for k, v in opts.items() if k != "speaker_turns"}
    render_opts_json = json.dumps(
        {
            **opts,
            # For the editor: its caption preview colours from these, and its
            # lines break where they do.
            **({"speaker_turns": turns} if turns else {}),
            **({"caption_style": caption_style} if caption_style else {}),
            **({"filter": filter_name} if filter_name != "none" else {}),
            # Persist podcast (a video-level job flag) per clip, so an editor
            # re-render keeps the letterbox instead of falling back to tracking.
            **({"podcast": True} if podcast else {}),
            # Same for Vertical Live: a re-render must keep the whole frame,
            # never fall back to tracking. Always written when set, whatever
            # else the clip has (a job with no caption style or filter would
            # otherwise save nothing at all).
            **({"vertical_live": True} if vertical_live else {}),
            # Gaming: the webcam and layout this clip got, so a re-render
            # keeps them (and the editor can show and change them).
            **({"gaming": gaming_kept} if gaming_kept is not None else {}),
            # Persist the resolved branding so a later re-render reapplies it,
            # even when it came from the job/config default (not per-clip opts).
            **({"watermark": wm_cfg} if wm_cfg else {}),
            # The sport, so a re-render frames the ball again, not a face.
            **({"sport": sport_name} if sport_name else {}),
        }
    ) if (opts or caption_style or filter_name != "none" or wm_cfg or vertical_live or sport_name) else ""
    return final_path, render_opts_json


def _wants_second_speaker(config: dict, opts: dict) -> bool:
    """Whether a clip's captions burn the other speaker in a second colour:
    captions on, the option ticked in its caption style, and not the
    Highlights look, which has a colour of its own."""
    if not (config["clips"].get("captions", True) and opts.get("captions", True)):
        return False
    from video import post_style as _post_style

    style = opts.get("caption_style") or config["clips"].get("caption_style")
    if not opts.get("profile") and _post_style.resolve(style) == _post_style.HIGHLIGHTS:
        style = _post_style.caption_style_for(style)
    return bool((style or {}).get("second_speaker"))


def _speaker_turns(
    source: Path, candidate: ClipCandidate, segments: list[Segment], config: dict, opts: dict
) -> list:
    """Where in the clip someone other than the main speaker talks
    (analysis/voice_turns.py). Empty when it can't be told, whatever the
    reason: a clip never fails to render over the colour of its captions.

    Only imported here, so a clip without the option never loads the models.
    A piece of a video sent by another PC to render (remote_render/) can't be
    listened to for who its main speaker is: its turns came with the job."""
    from analysis import voice_turns

    if not voice_turns.in_library(source, Path(config["paths"]["data_dir"])):
        return list(opts.get("speaker_turns") or [])
    try:
        return voice_turns.turns_for(source, candidate, segments, config)
    except cancel.CancelledError:
        raise
    except Exception as e:
        print(f"      (second speaker's caption colour skipped: {e})")
        return []


def _title_card(clip: Path, opts: dict, position: str, png: Path, language: str) -> None:
    """Lay the highlights title card over a rendered clip, at `position`
    ("lower" or "top"). Never raises: a card that cannot be drawn leaves the
    clip as it was rendered."""
    from core import modes
    from video import post_style

    try:
        size = modes.probe_size(clip)
        if not all(size):
            size = (1080, 1920)
        card = post_style.render_card(
            str(opts.get("headline") or ""), str(opts.get("subline") or ""), size, png,
            position=position, language=language,
        )
        if card is None:
            # Its words were only hashtags, or nothing here can draw them: no
            # face has their script, or right-to-left text has no layout
            # engine to shape it. Said, so a missing card is not a mystery.
            print("      (Title card skipped: nothing on it could be drawn)")
        else:
            post_style.apply_card(clip, card)
    except Exception as e:
        print(f"      (Title card skipped: {e})")
    finally:
        discard(png)


def _sport_framing(clip_path: Path, config: dict, sport_name: str) -> dict | None:
    """The sport's own framing for a clip (sports/), or None to use the face
    tracker as always: a sport without framing, or a failure, never stops a
    clip from rendering."""
    try:
        import sports

        return sports.framing(sport_name, clip_path, config)
    except Exception as e:
        print(f"      ({sport_name} framing failed: {e}; framing it the usual way)")
        return None


def _register_clip(
    db: StateDB,
    video_id: str,
    candidate: ClipCandidate,
    final_path: Path,
    meta: ClipMetadata,
    render_opts_json: str,
    config: dict | None = None,
) -> RenderedClip | None:
    """DB write for one rendered clip. Main thread only (sqlite connections
    are not shareable across threads).

    The branded end card is appended HERE rather than in _render_files,
    because this is the one place every finished video passes through. The
    longform Highlights and Edited Stream modes build their output with
    longform.assemble and never call _render_files at all, so hooking the
    renderer silently left two of the four longform profiles with no end card.
    Hooking the funnel means a mode added later cannot miss it either.
    """
    clip_id = db.add_clip(
        video_id,
        candidate.start,
        candidate.end,
        candidate.score,
        candidate.hook,
        path=str(final_path),
        status="queued",  # awaiting a daily schedule slot
        title=meta.title,
        description=meta.description,
        hashtags=json.dumps(meta.hashtags),
        scores=json.dumps(candidate.subscores or {}),
        render_opts=render_opts_json,
    )
    if clip_id is None:
        # Same window already in the DB (re-run): point the existing row at
        # the fresh render and updated scores.
        row = db.conn.execute(
            "SELECT id FROM clips WHERE video_id = ? AND start_s = ? AND end_s = ?",
            (video_id, round(candidate.start, 2), round(candidate.end, 2)),
        ).fetchone()
        if row:
            from video import post_style as _post_style

            fresh = {"path": str(final_path), "scores": json.dumps(candidate.subscores or {})}
            rendered = json.loads(render_opts_json) if render_opts_json else {}
            existing = db.get_clip(row["id"])
            kept = json.loads(existing["render_opts"]) if existing and existing["render_opts"] else {}
            if rendered.get("speaker_turns") != kept.get("speaker_turns"):
                # The other speaker's turns: the row's must be the ones this
                # file was burned with, or the editor colours its preview by
                # a render that is gone.
                kept = {k: v for k, v in kept.items() if k != "speaker_turns"}
                if rendered.get("speaker_turns"):
                    kept["speaker_turns"] = rendered["speaker_turns"]
                fresh["render_opts"] = json.dumps(kept)
            if rendered.get("gaming"):
                # Gaming / Reaction: the row's split must be the one this file
                # was rendered with, or the editor starts from a stale one.
                kept = {**kept, "gaming": rendered["gaming"]}
                fresh["render_opts"] = json.dumps(kept)
            if _post_style.HIGHLIGHTS in (_post_style.resolve(kept.get("caption_style")),
                                          _post_style.resolve(rendered.get("caption_style"))):
                # Highlights, in this render or the saved one: the row's title
                # card must be the one this file was rendered with. The editor
                # and its re-renders start from the row, so a stale one drops
                # the new card on the next edit, or brings an old one back.
                fresh["render_opts"] = json.dumps(_with_card_of(kept, rendered))
            db.set_clip(row["id"], **fresh)
        print(f"      Re-rendered (kept existing metadata): {final_path.name}")
        return None

    print(f"      -> {final_path}  ({meta.title})")
    return RenderedClip(source_video_id=video_id, candidate=candidate, path=final_path)


def _with_card_of(kept: dict, rendered: dict) -> dict:
    """A clip's saved options with the highlights title card set to the one
    a fresh render was made with: its post style and card position, and its
    headline and subline (gone when that render drew no card). The rest of
    the saved caption style stays the clip's, as on any re-run."""
    out = dict(kept)
    new_style = rendered.get("caption_style") or {}
    style = dict(kept.get("caption_style") or new_style)
    for key in ("post_style", "card_position"):
        if key in new_style:
            style[key] = new_style[key]
        else:
            style.pop(key, None)
    if style:
        out["caption_style"] = style
    else:
        out.pop("caption_style", None)
    for key in ("headline", "subline"):
        if key in rendered:
            out[key] = rendered[key]
        else:
            out.pop(key, None)
    return out
