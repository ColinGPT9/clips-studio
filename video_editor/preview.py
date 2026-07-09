"""Draft preview: a fast, low-res render of the clip WITH every pending edit
applied — cuts, mutes, muted-word captions, speed, hook title, music — so the
editor can show the real result in seconds, before the full-quality Apply.

Fast because it skips the expensive parts: 540x960 instead of 1080x1920,
ultrafast CPU x264, and a CENTER crop instead of subject tracking. Framing
may therefore differ slightly from the final render; everything else is
exactly what Apply will produce.
"""

import subprocess
from pathlib import Path

from core.models import ClipCandidate, Segment
from video.captions import DEFAULT_STYLE, build_caption_lines, build_captions
from video_editor.captions import remap_lines
from video_editor.export import apply_edits
from video_editor.overlay import ensure_hook
from video_editor.timeline import EditList


def render_draft(
    source: Path,
    start: float,
    end: float,
    edit_dict: dict | None,
    caption_lines: list[dict] | None,
    caption_style: dict | None,
    captions_enabled: bool,
    segments: list[Segment],
    out_path: Path,
) -> Path:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    duration = end - start
    candidate = ClipCandidate(start=start, end=end, score=0)
    edit = EditList.from_dict(edit_dict, duration=duration) or EditList(duration=duration)

    # Pass 1: fast low-res 9:16 center-crop cut of the clip window.
    rough = out_path.parent / (out_path.stem + ".rough.mp4")
    r = subprocess.run(
        [
            "ffmpeg", "-y",
            "-ss", f"{start:.2f}", "-i", str(source.resolve()),
            "-t", f"{duration + 0.4:.2f}",
            "-vf", "crop=ih*9/16:ih,scale=540:960,setsar=1",
            "-c:v", "libx264", "-preset", "ultrafast", "-crf", "27",
            "-c:a", "aac", "-b:a", "96k",
            "-fps_mode", "cfr", "-af", "aresample=async=1",
            str(rough.resolve()),
        ],
        capture_output=True, text=True,
    )
    if r.returncode != 0:
        raise RuntimeError(f"draft cut failed:\n{r.stderr[-1000:]}")

    # Captions exactly as Apply would burn them (remapped over cuts/speed).
    ass_path = None
    if captions_enabled:
        lines = caption_lines
        if edit.keep is not None or abs(edit.speed - 1) >= 0.01:
            if lines is None:
                wpc = {**DEFAULT_STYLE, **(caption_style or {})}["words_per_caption"]
                lines = build_caption_lines(segments, candidate, wpc)
            lines = remap_lines(lines, edit)
        ass_path = build_captions(
            segments, candidate, out_path.parent / (out_path.stem + ".ass"),
            style=caption_style, lines=lines,
        )
    if edit.hook:
        ass_path = ensure_hook(ass_path, out_path.parent / (out_path.stem + ".ass"), edit.hook)

    apply_edits(rough, edit, out_path, ass_path=ass_path)
    rough.unlink(missing_ok=True)
    if ass_path is not None:
        ass_path.unlink(missing_ok=True)
    return out_path
