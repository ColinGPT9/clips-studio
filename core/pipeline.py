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
from analysis.metadata import generate_metadata
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
    data_dir = Path(config["paths"]["data_dir"])

    print(f"[1/4] Downloading: {url}")
    progress.emit(stage="download", message=url)
    video = _cached_or_download(url, data_dir, db)
    print(f"      {video.title} ({video.duration:.0f}s) -> {video.path}")
    progress.emit(stage="downloaded", video_id=video.video_id, title=video.title, duration=video.duration)

    db.upsert_video(video.video_id, title=video.title, channel_name=video.channel)
    if db.video_status(video.video_id) == "done" and not force:
        print("      Already processed (status: done). Use --force to redo.")
        return []
    db.set_video_status(video.video_id, "downloaded")

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

    print("[3/4] Multimodal analysis (transcript + audio + visual)...")
    progress.emit(stage="analyze", video_id=video.video_id)
    llm = create_backend(config["llm"])
    candidates, rejections = find_clips(video.path, segments, llm, config)
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
    for i, candidate in enumerate(candidates, 1):
        progress.emit(stage="render", video_id=video.video_id, clip=i, total=len(candidates))
        clip = _render_one(
            video.path, video.video_id, video.title, candidate, segments, clip_dir, config, db, llm
        )
        if clip:
            rendered.append(clip)

    db.set_video_status(video.video_id, "done")
    progress.emit(stage="done", video_id=video.video_id, clips=len(rendered))
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
    render_opts: dict | None = None,
) -> RenderedClip | None:
    """render_opts (all optional, persisted per clip, set by the user or the
    AI edit assistant):
      captions: bool            - burn captions at all
      caption_style: dict       - see video/captions.DEFAULT_STYLE
      crop: "track" | "center" | "bias_left" | "bias_right"
    """
    opts = render_opts or {}
    # Deterministic timestamp-based name: re-runs overwrite instead of piling up.
    stem = f"clip_{int(candidate.start):05d}-{int(candidate.end):05d}"
    final_path = clip_dir / f"{stem}.mp4"
    clip_dir.mkdir(parents=True, exist_ok=True)

    ass_path = None
    # Per-clip style wins; otherwise the job/config default chosen at generate time.
    caption_style = opts.get("caption_style") or config["clips"].get("caption_style")
    if config["clips"].get("captions", True) and opts.get("captions", True):
        ass_path = build_captions(
            segments, candidate, clip_dir / f"{stem}.ass",
            style=caption_style,
            lines=opts.get("caption_lines"),  # user-corrected caption text, if any
        )

    if config["clips"].get("vertical", True):
        # Cut a horizontal intermediate, track the subject, render true 9:16.
        intermediate = clip_dir / f"{stem}.source.mp4"
        cut_clip(source, candidate, intermediate)

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
            )
            if tracking["mode"] == "track" and crop_mode in ("bias_left", "bias_right"):
                shift = -0.12 if crop_mode == "bias_left" else 0.12
                tracking["path"] = [(t, x + shift) for t, x in tracking["path"]]
        if tracking["mode"] == "split":
            print("         Facecam layout detected -> stacked webcam + gameplay render")
        render_vertical(intermediate, tracking, final_path, ass_path=ass_path)
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
        scores=json.dumps(candidate.subscores or {}),
        # Persist the effective options (including a generate-time caption
        # style) so re-renders and AI edits keep them.
        render_opts=json.dumps(
            {**opts, **({"caption_style": caption_style} if caption_style else {})}
        ) if (opts or caption_style) else "",
    )
    if clip_id is None:
        # Same window already in the DB (re-run): the file was just re-rendered,
        # so point the existing row at the fresh render and updated scores.
        row = db.conn.execute(
            "SELECT id FROM clips WHERE video_id = ? AND start_s = ? AND end_s = ?",
            (video_id, round(candidate.start, 2), round(candidate.end, 2)),
        ).fetchone()
        if row:
            db.set_clip(row["id"], path=str(final_path), scores=json.dumps(candidate.subscores or {}))
        print(f"      Re-rendered (kept existing metadata): {final_path.name}")
        return None

    print(f"      -> {final_path}")
    print(f"         Title: {meta.title}")
    return RenderedClip(source_video_id=video_id, candidate=candidate, path=final_path)
