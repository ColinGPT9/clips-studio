"""Pipeline orchestration for a single video.

download -> transcribe -> analyze -> render (cut + track + vertical crop)

Every stage transition is committed to the state DB before the next stage
runs: a crash resumes at the failed stage, a 'done' video is never
reprocessed, and the clips table's UNIQUE constraint blocks duplicates.
"""

from pathlib import Path

import json
import re

from analysis.fusion import find_clips
from analysis.metadata import ClipMetadata, generate_metadata_batch
from core import progress
from core.models import ClipCandidate, RenderedClip, Segment
from core.state import StateDB
from llm.base import LLMBackend
from llm.registry import create_backend
from sources import youtube
from transcription.transcriber import transcribe
from video.captions import build_captions
from video.cutter import cut_clip


def process_video(url: str, config: dict, db: StateDB, force: bool = False) -> list[RenderedClip]:
    import time

    from core import cancel

    data_dir = Path(config["paths"]["data_dir"])
    started = time.monotonic()

    print(f"[1/4] Downloading: {url}")
    progress.emit(stage="download", message=url)
    video = _cached_or_download(url, data_dir, db)
    print(f"      {video.title} ({video.duration:.0f}s) -> {video.path}")
    progress.emit(stage="downloaded", video_id=video.video_id, title=video.title, duration=video.duration)

    # Slow-decode sources (AV1/VP9/HEVC — old local uploads, format
    # fallbacks) get ONE up-front H.264 conversion so every later decode
    # pass runs at hardware speed. New uploads convert at import instead.
    from video.encoding import SLOW_SOURCE_CODECS, ensure_h264_source, source_codec

    if source_codec(video.path) in SLOW_SOURCE_CODECS:
        progress.emit(stage="converting source to H.264", video_id=video.video_id)
        ensure_h264_source(video.path, config)

    cancel.clear(video.video_id)  # fresh start; any stale flag from a prior run gone
    db.upsert_video(video.video_id, title=video.title, channel_name=video.channel)
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
            # Reaction layout drawn once for this creator applies to every
            # video of theirs (reaction pipeline only; ignored otherwise).
            lrow = db.conn.execute(
                "SELECT reaction_layout FROM creators WHERE creator_id = ?", (creator_id,)
            ).fetchone()
            if lrow and lrow["reaction_layout"]:
                import json as _json

                config = {**config, "clips": {**config["clips"],
                          "reaction_regions": _json.loads(lrow["reaction_layout"])}}
                print("      Using this creator's saved reaction layout")
    except Exception as e:
        print(f"      (creator tagging failed: {e})")
    if db.video_status(video.video_id) == "done" and not force:
        print("      Already processed (status: done). Use --force to redo.")
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

    def _fetch_hype() -> None:
        try:
            from analysis.hype import audience_curve

            curve = audience_curve(url, video.video_id, video.duration)
            if curve is not None:
                hype_out["curve"] = curve
        except Exception as e:
            print(f"      (audience hype fetch failed: {e})")

    hype_thread = threading.Thread(target=_fetch_hype, daemon=True, name="hype-prepass")
    hype_thread.start()

    print("[2/4] Transcribing...")
    progress.emit(stage="transcribe", video_id=video.video_id, title=video.title)
    # Content language: "auto" lets Whisper detect; a forced code fixes
    # bilingual streams (e.g. Hindi speech over English game audio) where
    # detection picks the wrong language and every caption burns wrong.
    forced_lang = (config.get("content_language") or "auto").lower()
    segments = transcribe(
        video.path,
        video.video_id,
        data_dir / "transcripts",
        model_size=config["whisper"]["model"],
        device=config["whisper"]["device"],
        language=None if forced_lang == "auto" else forced_lang,
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
    signals_thread.join()  # usually already done — transcription takes longer
    hype_thread.join(timeout=60)  # network fetch; hard cap so it never stalls
    llm = create_backend(config["llm"])
    candidates, rejections = find_clips(
        video.path, segments, llm, config,
        signals=signals_out.get("signals"),
        creator_context=creator_ctx,
        weight_bias=(creator_prefs or {}).get("weight_bias"),
        audience=hype_out.get("curve"),
    )
    for r in rejections:
        db.log_rejection(
            video.video_id,
            r.candidate.start, r.candidate.end, r.candidate.score, r.reason,
            kept_start=r.kept.start if r.kept else None,
            kept_end=r.kept.end if r.kept else None,
        )
    dup_count = sum(1 for r in rejections if r.reason not in ("below_min_score", "over_limit"))
    if dup_count:
        print(f"      Rejected {dup_count} duplicate/overlapping candidate(s) (logged)")
    db.set_video_status(video.video_id, "analyzed")

    if not candidates:
        print("      No clips passed the score threshold. Try lowering clips.min_score in config/settings.yaml.")
        db.set_video_status(video.video_id, "done")
        return []
    for c in candidates:
        s = c.subscores or {}
        breakdown = (
            f"text {s.get('text', '?')} | audio {s.get('audio', '?')} | "
            f"visual {s.get('visual', '?')} | reaction {s.get('reaction', '?')} | "
            f"engage {s.get('engagement', '?')} | {c.source}"
        )
        print(f"      [{c.score:3d}] {c.start:7.1f}s - {c.end:7.1f}s  {c.hook}")
        print(f"            ({breakdown})")

    print("[4/4] Rendering clips...")
    rendered = []
    # Human-browsable layout: clips/<channel>/<video title> [id]/clip_*.mp4
    clip_dir = (
        data_dir / "clips"
        / _safe_name(video.channel, "unknown-channel")
        / f"{_safe_name(video.title, video.video_id)} [{video.video_id}]"
    )
    # Titles/descriptions/hashtags for ALL clips in a few batched LLM calls
    # (one call per clip made long streams crawl through analysis).
    print(f"      Writing titles & hashtags for {len(candidates)} clip(s) (batched)...")
    metas = generate_metadata_batch(
        candidates, segments, video.title, llm,
        creator_context=(creator_ctx.summary if creator_ctx else ""),
    )

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
    done_count = 0
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {
            pool.submit(
                _render_files, video.path, candidate, segments, clip_dir, config, None,
                content_lang,
            ): (candidate, meta)
            for candidate, meta in zip(candidates, metas)
        }
        for future in as_completed(futures):
            candidate, meta = futures[future]
            done_count += 1
            progress.emit(
                stage="render", video_id=video.video_id, clip=done_count, total=len(candidates)
            )
            try:
                final_path, render_opts_json = future.result()
            except Exception as e:
                print(f"      Render failed for {candidate.start:.0f}s-{candidate.end:.0f}s: {e}")
                continue
            clip = _register_clip(db, video.video_id, candidate, final_path, meta, render_opts_json)
            if clip:
                rendered.append(clip)

    if knowledge_thread is not None:
        knowledge_thread.join(timeout=600)  # normally finished during renders

    elapsed = time.monotonic() - started
    db.set_process_seconds(video.video_id, elapsed)
    db.set_video_status(video.video_id, "done")
    progress.emit(
        stage="done", video_id=video.video_id, clips=len(rendered), seconds=round(elapsed, 1)
    )
    print(f"      Done in {elapsed / 60:.1f} min ({len(rendered)} clips)")
    return rendered


def _cached_or_download(url: str, data_dir: Path, db: StateDB):
    """Reprocessing must never depend on the platform being reachable: when
    the source file is already on disk, use it (with title/channel from the
    DB) instead of re-contacting YouTube/Twitch — which can rate-limit or
    bot-block repeat requests."""
    from core.models import DownloadedVideo
    from sources import dispatch

    _, video_id = dispatch.identify(url)
    cached = data_dir / "downloads" / f"{video_id}.mp4" if video_id else None
    if not (cached and cached.exists()):
        return dispatch.download(url, data_dir / "downloads")

    import subprocess

    # Cached files from before the H.264-only YouTube selector can be AV1 —
    # every analysis/render pass software-decodes those, which once made a
    # 25-min video slower than a 2-hour H.264 VOD. Swap for H.264 while the
    # platform is reachable; otherwise the slow cached copy still works.
    codec = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=codec_name", "-of", "csv=p=0", str(cached)],
        capture_output=True, text=True,
    ).stdout.strip()
    if codec in ("av1", "vp9"):
        print(f"      Cached source is {codec} (slow to decode) — re-downloading as H.264")
        try:
            return dispatch.download(url, data_dir / "downloads")
        except Exception:
            print("      Re-download failed — using the cached copy")

    row = db.conn.execute(
        "SELECT title, channel_name FROM videos WHERE video_id = ?", (video_id,)
    ).fetchone()

    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(cached)],
        capture_output=True, text=True,
    )
    duration = float(probe.stdout.strip() or 0)
    print("      Source already downloaded — skipping YouTube")
    return DownloadedVideo(
        video_id=video_id,
        title=(row["title"] if row and row["title"] else video_id),
        path=cached,
        duration=duration,
        channel=(row["channel_name"] if row else "") or "",
    )


def _safe_name(name: str, fallback: str) -> str:
    """Make a name safe as a Windows folder: strip reserved characters,
    trailing dots/spaces, and overlong text."""
    cleaned = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "", name).strip().rstrip(". ")
    return cleaned[:60].strip() or fallback


def _try_reaction_render(
    intermediate: Path,
    final_path: Path,
    ass_path: Path | None,
    vf_extra: str,
    cam_pos: str,
    crop_mode: str,
    opts: dict,
    config: dict,
) -> bool:
    """Render this clip with the REACTION pipeline (creator + reacted
    content both visible), or return False to leave it to the standard one.

    Routing is EXPLICIT — there is no auto-detection, because misrouting
    ordinary content is worse than asking:
      crop == 'reaction'  -> this clip (editor's Layout row)
      reaction == always  -> this job (Dashboard's Reaction control)
      otherwise           -> False, standard pipeline (the default)

    Fails closed on every path: no layout, low confidence, or any exception
    hands the clip back to the standard renderer, which has not changed."""
    regions = opts.get("reaction_regions") or config["clips"].get("reaction_regions")
    mode = str(opts.get("reaction") or config["clips"].get("reaction") or "off").lower()
    # Hand-drawn regions ARE the instruction to use this pipeline.
    forced = crop_mode == "reaction" or mode == "always" or bool(regions)
    if not forced:
        return False
    try:
        from reaction.compose import render_reaction
        from reaction.layout import ReactionLayout, adapt_cam_box, analyze

        if regions:
            # The user drew these on a real frame — no detection, no guessing.
            # Only the cam box is re-checked, in case they moved their webcam.
            layout = ReactionLayout(
                cam_box=adapt_cam_box(
                    intermediate, tuple(regions["cam"]), config["tracking"]["detector"]
                ),
                content_box=tuple(regions["content"]),
                kind="manual",
                confidence=1.0,
            )
        else:
            layout = analyze(
            intermediate,
            model_name=config["tracking"]["detector"],
            cam_corner=str(
                opts.get("cam_corner") or config["clips"].get("cam_corner") or "auto"
            ),
            content_side=str(
                opts.get("content_side") or config["clips"].get("content_side") or "auto"
            ),
            )
        if layout is None:
            if not forced:
                return False  # AUTO: not a reaction clip — standard pipeline
            # Explicitly asked for: full frame on a blurred backdrop still
            # shows the creator AND the content, just uncomposed.
            from video.cropper import render_vertical

            print("      Reaction: no two-region layout found — full-frame letterbox")
            render_vertical(
                intermediate, {"mode": "fit_blur", "region": None},
                final_path, ass_path=ass_path, vf_extra=vf_extra,
            )
            return True
        if not forced and layout.confidence < 0.6:
            return False  # AUTO stays conservative
        print(f"      Reaction layout — {layout.describe()}")
        render_reaction(
            intermediate, layout, final_path, ass_path=ass_path,
            vf_extra=vf_extra, cam_position=cam_pos,
        )
        return True
    except Exception as e:  # noqa: BLE001 — isolation: never lose a clip
        print(f"      (reaction pipeline failed, using the standard one: {e})")
        return False


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

    opts = render_opts or {}
    # Deterministic timestamp-based name: re-runs overwrite instead of piling up.
    stem = f"clip_{int(candidate.start):05d}-{int(candidate.end):05d}"
    final_path = clip_dir / f"{stem}.mp4"
    clip_dir.mkdir(parents=True, exist_ok=True)

    # Longform rendering profile (render_opts["profile"], set only by the
    # longform module): 16:9 1920x1080 output, no vertical crop/tracking.
    # Absent for every existing Shorts clip — their path is unchanged.
    landscape = bool(opts.get("profile"))
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

    # Manual edits from the Shorts editor (trim/cuts/mutes/volume/fades) —
    # non-destructive: stored in render_opts, applied fresh on every render.
    edit = None
    if opts.get("edit"):
        from video_editor.timeline import EditList

        edit = EditList.from_dict(opts["edit"], duration=candidate.duration)

    ass_path = None
    # Per-clip style wins; otherwise the job/config default chosen at generate time.
    caption_style = opts.get("caption_style") or config["clips"].get("caption_style")
    if config["clips"].get("captions", True) and opts.get("captions", True):
        lines = opts.get("caption_lines")  # user-corrected caption text, if any
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
            style=caption_style,
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

    if config["clips"].get("vertical", True) and not landscape:
        # Cut a horizontal intermediate, track the subject, render true 9:16.
        intermediate = clip_dir / f"{stem}.source.mp4"
        cut_clip(source, padded, intermediate)

        if edit is not None:
            # Apply manual edits BEFORE tracking, so the tracker and captions
            # see the final (edited) timeline.
            from video_editor.export import apply_edits

            edited = clip_dir / f"{stem}.edited.mp4"
            apply_edits(intermediate, edit, edited)
            intermediate.unlink(missing_ok=True)
            intermediate = edited

        from video.cropper import render_vertical
        from video.tracker import compute_tracking  # lazy: imports torch

        crop_mode = opts.get("crop", "track")
        # Facecam band position — per-clip editor choice wins over the job
        # default from the Generate bar (same precedence as filters/styles).
        cam_pos = (
            opts.get("split_position")
            or config["clips"].get("split_position")
            or "top"
        )
        # ---- reaction pipeline (isolated — see reaction/__init__.py) ------
        # Returns False on any doubt or failure, and the untouched standard
        # path below runs instead: talking-head and IRL clips are unaffected.
        if not _try_reaction_render(
            intermediate, final_path, ass_path, vf_extra, cam_pos, crop_mode, opts, config
        ):
            if crop_mode == "center":
                tracking = {"mode": "track", "path": [(0.0, 0.5)]}
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
                    # automatic letterbox behavior) threw away the other side.
                    tracking["region"] = None
                if tracking["mode"] == "track" and crop_mode in ("bias_left", "bias_right"):
                    shift = -0.12 if crop_mode == "bias_left" else 0.12
                    tracking["path"] = [(t, x + shift) for t, x in tracking["path"]]
            render_vertical(
                intermediate, tracking, final_path, ass_path=ass_path,
                vf_extra=vf_extra, cam_position=cam_pos,
            )
        intermediate.unlink(missing_ok=True)
    else:
        if edit is not None:
            # Horizontal output: cut plain first, then apply edits and burn
            # captions in the same pass (they must land AFTER the cuts).
            from video_editor.export import apply_edits

            plain = clip_dir / f"{stem}.plain.mp4"
            cut_clip(source, padded, plain, vf_extra=vf_extra)
            apply_edits(plain, edit, final_path, ass_path=ass_path)
            plain.unlink(missing_ok=True)
        else:
            cut_clip(source, padded, final_path, ass_path=ass_path, vf_extra=vf_extra)

    if ass_path is not None:
        ass_path.unlink(missing_ok=True)

    # Image watermark: one overlay pass on the finished clip (only when set).
    if wm_cfg and _wm.has_image(wm_cfg, wm_assets):
        _wm.apply_image(final_path, wm_cfg, canvas, wm_assets)

    render_opts_json = json.dumps(
        {
            **opts,
            **({"caption_style": caption_style} if caption_style else {}),
            **({"filter": filter_name} if filter_name != "none" else {}),
            # Persist the resolved branding so a later re-render reapplies it,
            # even when it came from the job/config default (not per-clip opts).
            **({"watermark": wm_cfg} if wm_cfg else {}),
        }
    ) if (opts or caption_style or filter_name != "none" or wm_cfg) else ""
    return final_path, render_opts_json


def _register_clip(
    db: StateDB,
    video_id: str,
    candidate: ClipCandidate,
    final_path: Path,
    meta: ClipMetadata,
    render_opts_json: str,
) -> RenderedClip | None:
    """DB write for one rendered clip. Main thread only (sqlite connections
    are not shareable across threads)."""
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
            db.set_clip(row["id"], path=str(final_path), scores=json.dumps(candidate.subscores or {}))
        print(f"      Re-rendered (kept existing metadata): {final_path.name}")
        return None

    print(f"      -> {final_path}  ({meta.title})")
    return RenderedClip(source_video_id=video_id, candidate=candidate, path=final_path)
