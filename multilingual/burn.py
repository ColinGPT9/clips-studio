"""Burn translated captions into a copy of the clip, one file per language.

TikTok, Reels and Shorts don't read subtitle files — text has to be in the
picture. So each language gets its own video.

The clip already on disk usually has the ORIGINAL captions burned in, and
painting a second set on top would stack two languages. So a caption-free
base is rendered from the source ONCE (same framing, edits, colour and
watermark as the original clip), and each language is then a single cheap
burn onto that base. Five languages cost one re-render plus five subtitle
passes, not five re-renders.

Read-only reuse of the render path; nothing here changes it.
"""

import subprocess
from pathlib import Path

from multilingual.languages import english_name


def _canvas_of(path: Path) -> tuple[int, int]:
    try:
        r = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "v:0",
             "-show_entries", "stream=width,height", "-of", "csv=p=0", str(path)],
            capture_output=True, text=True, timeout=30,
        )
        w, h = (int(v) for v in r.stdout.strip().split(",")[:2])
        return (w, h)
    except Exception:
        return (1080, 1920)


def clean_base(clip_row, config: dict, data_dir: Path, work_dir: Path) -> Path | None:
    """The clip re-rendered WITHOUT captions, so translated ones can be
    burned on cleanly. Returns None when the source is gone (then the
    caller falls back to burning onto the existing clip)."""
    import json

    from core.models import ClipCandidate, Segment
    from core.pipeline import _render_files

    source = data_dir / "downloads" / f"{clip_row['video_id']}.mp4"
    if not source.exists():
        return None
    opts = json.loads(clip_row["render_opts"]) if clip_row["render_opts"] else {}
    opts = {**opts, "captions": False}
    opts.pop("caption_lines", None)

    tpath = data_dir / "transcripts" / f"{clip_row['video_id']}.json"
    segments = []
    if tpath.exists():
        data = json.loads(tpath.read_text(encoding="utf-8"))
        segments = [Segment(**s) for s in data["segments"]]
    candidate = ClipCandidate(
        start=clip_row["start_s"], end=clip_row["end_s"],
        score=clip_row["score"] or 0, hook=clip_row["hook"] or "",
    )
    work_dir.mkdir(parents=True, exist_ok=True)
    rendered, _ = _render_files(source, candidate, segments, work_dir, config, opts)
    return rendered


def burn(
    base_video: Path,
    lines: list[dict],
    language: str,
    out_path: Path,
    caption_style: dict | None,
    config: dict,
) -> Path | None:
    """One language: translated captions burned into `base_video`."""
    from video.captions import build_captions
    from video.encoding import video_encoder_args

    from core.models import ClipCandidate

    canvas = _canvas_of(base_video)
    ass_path = out_path.with_suffix(f".{language}.ass")
    built = build_captions(
        [], ClipCandidate(start=0, end=1, score=0), ass_path,
        style=caption_style, lines=lines, canvas=canvas,
        language=language,  # picks a font that has the script's glyphs
    )
    if built is None:
        return None
    cmd = [
        "ffmpeg", "-y", "-v", "error",
        "-i", str(base_video.resolve()),
        "-vf", f"subtitles={built.name}",
        "-c:a", "copy",
        *video_encoder_args(config),
        "-movflags", "+faststart",
        str(out_path.resolve()),
    ]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, cwd=built.parent)
        if r.returncode != 0:
            print(f"      ({english_name(language)} burn failed: {r.stderr[-200:]})")
            return None
        return out_path
    finally:
        built.unlink(missing_ok=True)
