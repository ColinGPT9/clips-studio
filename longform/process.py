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

    print("[2/4] Transcribing...")
    progress.emit(stage="transcribe", video_id=video.video_id, title=video.title)
    segments = transcribe(
        video.path, video.video_id, data_dir / "transcripts",
        model_size=config["whisper"]["model"], device=config["whisper"]["device"],
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
    workers = max(1, int(config.get("video", {}).get("parallel_renders", 2)))
    done_count = 0
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {
            pool.submit(_render_files, video.path, c, segments, clip_dir, config, dict(render_opts)): (c, m)
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
