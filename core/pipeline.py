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

    print("[2/4] Transcribing...")
    progress.emit(stage="transcribe", video_id=video.video_id, title=video.title)
    segments = transcribe(
        video.path,
        video.video_id,
        data_dir / "transcripts",
        model_size=config["whisper"]["model"],
        device=config["whisper"]["device"],
    )
    print(f"      {len(segments)} segments")
    db.set_video_status(video.video_id, "transcribed")

    cancel.check(video.video_id)
    print("[3/4] Multimodal analysis (transcript + audio + visual)...")
    progress.emit(stage="analyze", video_id=video.video_id)
    signals_thread.join()  # usually already done — transcription takes longer
    llm = create_backend(config["llm"])
    candidates, rejections = find_clips(
        video.path, segments, llm, config,
        signals=signals_out.get("signals"),
        creator_context=creator_ctx,
        weight_bias=(creator_prefs or {}).get("weight_bias"),
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
            pool.submit(_render_files, video.path, candidate, segments, clip_dir, config, None): (
                candidate,
                meta,
            )
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

    row = db.conn.execute(
        "SELECT title, channel_name FROM videos WHERE video_id = ?", (video_id,)
    ).fetchone()
    import subprocess

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


def _render_files(
    source: Path,
    candidate: ClipCandidate,
    segments: list[Segment],
    clip_dir: Path,
    config: dict,
    render_opts: dict | None = None,
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
        )

    # Hook title (big text, top third, first few seconds) burns through the
    # same ASS/subtitles path as captions — correct at final resolution.
    if edit is not None and edit.hook:
        from video_editor.overlay import ensure_hook

        ass_path = ensure_hook(ass_path, clip_dir / f"{stem}.ass", edit.hook, canvas=canvas)

    # Watermark & branding (opts["watermark"], else the job/config default).
    # Text folds into the ASS burn now; the image overlay runs after the
    # final render. Absent -> no branding, path unchanged.
    from video_editor import watermark as _wm

    wm_cfg = opts["watermark"] if "watermark" in opts else config["clips"].get("watermark")
    wm_assets = Path(config["paths"]["data_dir"]) / "branding" / "assets"
    if wm_cfg and _wm.has_text(wm_cfg):
        ass_path = _wm.ensure_text(ass_path, clip_dir / f"{stem}.ass", wm_cfg, canvas)

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
        if crop_mode == "center":
            tracking = {"mode": "track", "path": [(0.0, 0.5)]}
        else:
            tracking_cfg = config["tracking"]
            tracking = compute_tracking(
                intermediate,
                model_name=tracking_cfg["detector"],
                sample_fps=tracking_cfg["sample_fps"],
                # User-forced letterbox: detection still runs so the frame is
                # cropped TIGHT to the subject (person large), exactly like
                # the automatic letterbox — just without its trigger.
                force_fit_blur=(crop_mode == "letterbox"),
            )
            if tracking["mode"] == "track" and crop_mode in ("bias_left", "bias_right"):
                shift = -0.12 if crop_mode == "bias_left" else 0.12
                tracking["path"] = [(t, x + shift) for t, x in tracking["path"]]
        render_vertical(intermediate, tracking, final_path, ass_path=ass_path, vf_extra=vf_extra)
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
