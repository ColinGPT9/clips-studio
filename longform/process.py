"""Longform pipeline orchestration.

Thin glue over the SAME stage functions the Shorts pipeline uses — nothing
is duplicated, and nothing in the Shorts path is modified. Differences:
different clip duration bounds per profile, a landscape rendering profile
(render_opts["profile"]), and output under Longform/<mode>/ inside the
video's clip folder.
"""

import copy
import json
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import replace
from pathlib import Path

from core import cancel, progress
from core.state import StateDB
from longform.profiles import PROFILES

# Longform picks moments with the SAME analysis as Shorts, so it often
# chooses identical windows. Clips are unique per (video, start, end) —
# nudge longform timestamps imperceptibly so a longform clip can never
# collide with (and hijack) an existing Short's row.
_NUDGE = 0.011


def process_longform(url: str, config: dict, db: StateDB, options: dict) -> None:
    from analysis.fusion import find_clips
    from analysis.metadata import generate_metadata_batch
    from core.pipeline import _cached_or_download, _register_clip, _render_files, _safe_name
    from llm.registry import create_backend
    from transcription.transcriber import transcribe

    mode = str(options.get("mode") or "short_clips")
    profile = PROFILES.get(mode)
    if profile is None:
        raise ValueError(f"unknown longform mode {mode!r}")
    if not profile.get("ready"):
        raise ValueError(f"Longform {profile['label']} isn't available yet — coming in an update")

    data_dir = Path(config["paths"]["data_dir"])
    started = time.monotonic()

    print(f"[1/4] Longform ({profile['label']}): {url}")
    progress.emit(stage="download", message=url)
    video = _cached_or_download(url, data_dir, db)
    print(f"      {video.title} ({video.duration:.0f}s)")
    progress.emit(stage="downloaded", video_id=video.video_id, title=video.title, duration=video.duration)

    cancel.clear(video.video_id)
    db.upsert_video(video.video_id, title=video.title, channel_name=video.channel)
    try:
        from creator import identity

        identity.tag_video(db, video.video_id, video.channel)
    except Exception:
        pass
    db.set_video_status(video.video_id, "downloaded")
    cancel.check(video.video_id)

    if mode == "edited_stream":
        _edited_stream(video, config, db, data_dir, profile, started)
        return

    print("[2/4] Transcribing...")
    progress.emit(stage="transcribe", video_id=video.video_id, title=video.title)
    forced_lang = (config.get("content_language") or "auto").lower()
    segments = transcribe(
        video.path, video.video_id, data_dir / "transcripts",
        model_size=config["whisper"]["model"], device=config["whisper"]["device"],
        language=None if forced_lang == "auto" else forced_lang,
    )
    db.set_video_status(video.video_id, "transcribed")
    cancel.check(video.video_id)

    print(f"[3/4] Analysis (same scoring as Shorts, {profile['min_duration']}-{profile['max_duration']}s windows)...")
    progress.emit(stage="analyze", video_id=video.video_id)
    cfg = copy.deepcopy(config)
    cfg["clips"]["min_duration"] = profile["min_duration"]
    cfg["clips"]["max_duration"] = profile["max_duration"]
    llm = create_backend(config["llm"])
    candidates, rejections = find_clips(video.path, segments, llm, cfg)
    for r in rejections:
        db.log_rejection(
            video.video_id,
            r.candidate.start, r.candidate.end, r.candidate.score, r.reason,
            kept_start=r.kept.start if r.kept else None,
            kept_end=r.kept.end if r.kept else None,
        )
    db.set_video_status(video.video_id, "analyzed")
    if not candidates:
        print("      No clips passed the score threshold.")
        db.set_video_status(video.video_id, "done")
        return
    cancel.check(video.video_id)

    if mode == "highlights":
        _highlights(video, config, db, data_dir, profile, candidates, options, started)
        return

    print(f"[4/4] Rendering {len(candidates)} landscape clip(s) (1920x1080)...")
    metas = generate_metadata_batch(candidates, segments, video.title, llm)
    candidates = [replace(c, start=c.start + _NUDGE, end=c.end + _NUDGE) for c in candidates]

    clip_dir = (
        data_dir / "clips"
        / _safe_name(video.channel, "unknown-channel")
        / f"{_safe_name(video.title, video.video_id)} [{video.video_id}]"
        / profile["subdir"]
    )
    render_opts = {"profile": mode}
    from transcription.transcriber import detected_language

    content_lang = forced_lang if forced_lang != "auto" else detected_language(
        video.video_id, data_dir / "transcripts"
    )
    workers = max(1, int(config.get("video", {}).get("parallel_renders", 2)))
    done_count = 0
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {
            pool.submit(
                _render_files, video.path, c, segments, clip_dir, config,
                dict(render_opts), content_lang,
            ): (c, m)
            for c, m in zip(candidates, metas)
        }
        for future in as_completed(futures):
            candidate, meta = futures[future]
            done_count += 1
            progress.emit(stage="render", video_id=video.video_id, clip=done_count, total=len(candidates))
            try:
                final_path, render_opts_json = future.result()
            except Exception as e:
                print(f"      Render failed for {candidate.start:.0f}s-{candidate.end:.0f}s: {e}")
                continue
            _register_clip(db, video.video_id, candidate, final_path, meta, render_opts_json)

    elapsed = time.monotonic() - started
    db.set_process_seconds(video.video_id, elapsed)
    db.set_video_status(video.video_id, "done")
    progress.emit(stage="done", video_id=video.video_id, clips=done_count, seconds=round(elapsed, 1))
    print(f"      Longform done in {elapsed / 60:.1f} min ({done_count} clips)")


def _highlights(
    video, config: dict, db: StateDB, data_dir: Path,
    profile: dict, candidates, options: dict, started: float,
) -> None:
    """Highlights: the stream's best moments assembled into one well-paced
    video. QUALITY decides the length — at least 8 minutes (YouTube
    mid-roll ad eligibility), up to 20 when the material earns it, never
    padded with filler to hit a number."""
    from analysis.metadata import ClipMetadata
    from core.models import ClipCandidate
    from core.pipeline import _register_clip, _safe_name
    from longform.assemble import assemble
    from longform.highlight_select import FLOOR, select_highlights

    keep = select_highlights(candidates, video.duration)
    if not keep:
        print("      Not enough scored moments for a highlight video.")
        db.set_video_status(video.video_id, "done")
        return
    total = sum(b - a for a, b in keep)
    if total < FLOOR:
        print(
            f"      Only {total / 60:.1f} min of highlight material in this"
            f" video (under the 8 min ad floor) — using everything there is."
        )
    print(
        f"[4/4] Assembling {total / 60:.1f} min highlight video"
        f" ({len(keep)} moments, chronological)..."
    )

    clip_dir = (
        data_dir / "clips"
        / _safe_name(video.channel, "unknown-channel")
        / f"{_safe_name(video.title, video.video_id)} [{video.video_id}]"
        / profile["subdir"]
    )
    out = clip_dir / "highlights.mp4"
    assemble(
        video.path, keep, out, video.video_id,
        on_progress=lambda i, n: progress.emit(
            stage="render", video_id=video.video_id, clip=i, total=n
        ),
    )

    top = max(candidates, key=lambda c: c.score)
    candidate = ClipCandidate(
        start=0.0, end=round(total, 2), score=top.score, hook="Highlights"
    )
    meta = ClipMetadata(
        title=f"{video.title} — highlights",
        description=(
            f"The best {total / 60:.0f} minutes of the stream —"
            f" {len(keep)} moments, in order."
        ),
        hashtags=[],
    )
    _register_clip(db, video.video_id, candidate, out, meta, json.dumps({"profile": "highlights"}))

    elapsed = time.monotonic() - started
    db.set_process_seconds(video.video_id, elapsed)
    db.set_video_status(video.video_id, "done")
    progress.emit(stage="done", video_id=video.video_id, clips=1, seconds=round(elapsed, 1))
    print(f"      Highlights done in {elapsed / 60:.1f} min -> {out.name}")


def _edited_stream(video, config: dict, db: StateDB, data_dir: Path, profile: dict, started: float) -> None:
    """Edited Stream: the full VOD, chronological, with dead silence
    (including Twitch/Kick DMCA-muted music sections, which arrive already
    silent), AFK sections and waiting/loading screens cut out."""
    from analysis.audio_features import extract_audio_features
    from analysis.visual_features import extract_visual_features
    from analysis.metadata import ClipMetadata
    from core.models import ClipCandidate
    from core.pipeline import _register_clip, _safe_name
    from longform.assemble import assemble
    from longform.downtime import detect_keep_ranges

    print("[2/3] Detecting downtime (silence, DMCA-muted music, AFK, waiting screens)...")
    progress.emit(stage="signals", video_id=video.video_id)
    audio_raw = extract_audio_features(video.path)
    visual_raw = extract_visual_features(video.path)
    keep = detect_keep_ranges(
        audio_raw.get("loudness"), visual_raw.get("motion"), video.duration
    )
    kept_s = sum(b - a for a, b in keep)
    removed_s = max(0.0, video.duration - kept_s)
    print(
        f"      Keeping {kept_s / 60:.1f} min of {video.duration / 60:.1f}"
        f" ({removed_s / 60:.1f} min of downtime removed, {len(keep)} sections)"
    )
    db.set_video_status(video.video_id, "analyzed")
    cancel.check(video.video_id)

    print(f"[3/3] Assembling edited stream ({len(keep)} segments)...")
    clip_dir = (
        data_dir / "clips"
        / _safe_name(video.channel, "unknown-channel")
        / f"{_safe_name(video.title, video.video_id)} [{video.video_id}]"
        / profile["subdir"]
    )
    out = clip_dir / f"edited_{int(video.duration):06d}.mp4"
    assemble(
        video.path, keep, out, video.video_id,
        on_progress=lambda i, n: progress.emit(
            stage="render", video_id=video.video_id, clip=i, total=n
        ),
    )

    candidate = ClipCandidate(start=0.0, end=round(video.duration, 2), score=0, hook="Edited stream")
    meta = ClipMetadata(
        title=f"{video.title} — edited stream",
        description=f"Full stream with {removed_s / 60:.0f} minutes of downtime removed.",
        hashtags=[],
    )
    _register_clip(db, video.video_id, candidate, out, meta, json.dumps({"profile": "edited_stream"}))

    elapsed = time.monotonic() - started
    db.set_process_seconds(video.video_id, elapsed)
    db.set_video_status(video.video_id, "done")
    progress.emit(stage="done", video_id=video.video_id, clips=1, seconds=round(elapsed, 1))
    print(f"      Edited stream done in {elapsed / 60:.1f} min -> {out.name}")
