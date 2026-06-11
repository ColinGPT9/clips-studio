"""Pipeline orchestration for a single video.

download -> transcribe -> analyze -> render (cut + track + vertical crop)

Every stage transition is committed to the state DB before the next stage
runs: a crash resumes at the failed stage, a 'done' video is never
reprocessed, and the clips table's UNIQUE constraint blocks duplicates.
"""

from pathlib import Path

import json

from analysis.highlights import find_highlights
from analysis.metadata import generate_metadata
from core.models import ClipCandidate, RenderedClip, Segment
from core.state import StateDB
from llm.base import LLMBackend
from llm.registry import create_backend
from sources import youtube
from transcription.transcriber import transcribe
from video.captions import build_captions
from video.cutter import cut_clip


def process_video(url: str, config: dict, db: StateDB, force: bool = False) -> list[RenderedClip]:
    data_dir = Path(config["paths"]["data_dir"])
    clips_cfg = config["clips"]
    analysis_cfg = config["analysis"]

    print(f"[1/4] Downloading: {url}")
    video = youtube.download(url, data_dir / "downloads")
    print(f"      {video.title} ({video.duration:.0f}s) -> {video.path}")

    db.upsert_video(video.video_id, title=video.title)
    if db.video_status(video.video_id) == "done" and not force:
        print("      Already processed (status: done). Use --force to redo.")
        return []
    db.set_video_status(video.video_id, "downloaded")

    print("[2/4] Transcribing...")
    segments = transcribe(
        video.path,
        video.video_id,
        data_dir / "transcripts",
        model_size=config["whisper"]["model"],
        device=config["whisper"]["device"],
    )
    print(f"      {len(segments)} segments")
    db.set_video_status(video.video_id, "transcribed")

    print("[3/4] Finding highlights...")
    llm = create_backend(config["llm"])
    candidates, rejections = find_highlights(
        segments,
        llm,
        min_score=clips_cfg["min_score"],
        max_clips=clips_cfg["max_clips_per_video"],
        min_duration=clips_cfg["min_duration"],
        max_duration=clips_cfg["max_duration"],
        max_overlap=analysis_cfg["max_overlap"],
        max_text_similarity=analysis_cfg["max_text_similarity"],
        max_segment_reuse=analysis_cfg["max_segment_reuse"],
        chunk_seconds=analysis_cfg["chunk_seconds"],
        chunk_overlap_seconds=analysis_cfg["chunk_overlap_seconds"],
        long_video_threshold_seconds=analysis_cfg["long_video_threshold_seconds"],
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
        print(f"      [{c.score:3d}] {c.start:7.1f}s - {c.end:7.1f}s  {c.hook}")

    print("[4/4] Rendering clips...")
    rendered = []
    clip_dir = data_dir / "clips" / video.video_id
    for candidate in candidates:
        clip = _render_one(
            video.path, video.video_id, video.title, candidate, segments, clip_dir, config, db, llm
        )
        if clip:
            rendered.append(clip)

    db.set_video_status(video.video_id, "done")
    return rendered


def _render_one(
    source: Path,
    video_id: str,
    video_title: str,
    candidate: ClipCandidate,
    segments: list[Segment],
    clip_dir: Path,
    config: dict,
    db: StateDB,
    llm: LLMBackend,
) -> RenderedClip | None:
    # Deterministic timestamp-based name: re-runs overwrite instead of piling up.
    stem = f"clip_{int(candidate.start):05d}-{int(candidate.end):05d}"
    final_path = clip_dir / f"{stem}.mp4"
    clip_dir.mkdir(parents=True, exist_ok=True)

    ass_path = None
    if config["clips"].get("captions", True):
        ass_path = build_captions(segments, candidate, clip_dir / f"{stem}.ass")

    if config["clips"].get("vertical", True):
        # Cut a horizontal intermediate, track the subject, render true 9:16.
        intermediate = clip_dir / f"{stem}.source.mp4"
        cut_clip(source, candidate, intermediate)

        from video.cropper import render_vertical
        from video.tracker import compute_crop_path  # lazy: imports torch

        tracking_cfg = config["tracking"]
        crop_path = compute_crop_path(
            intermediate,
            model_name=tracking_cfg["detector"],
            sample_fps=tracking_cfg["sample_fps"],
        )
        render_vertical(intermediate, crop_path, final_path, ass_path=ass_path)
        intermediate.unlink(missing_ok=True)
    else:
        cut_clip(source, candidate, final_path, ass_path=ass_path)

    if ass_path is not None:
        ass_path.unlink(missing_ok=True)

    meta = generate_metadata(candidate, segments, video_title, llm)

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
    )
    if clip_id is None:
        print(f"      Skipped (already in DB): {final_path.name}")
        return None

    print(f"      -> {final_path}")
    print(f"         Title: {meta.title}")
    return RenderedClip(source_video_id=video_id, candidate=candidate, path=final_path)
