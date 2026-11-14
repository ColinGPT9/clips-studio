"""Shared by the editor suite step 6 tests (video_editor/media/,
server/media_api.py): small generated media, made with FFmpeg when it is
there, and a project row that names media.

Standard library only; the FFmpeg ones skip the test without FFmpeg.
"""

from __future__ import annotations

import json
import subprocess
import uuid
from pathlib import Path

import pytest

from core import binaries

NOW = "2026-11-14T09:00:00"


def need_ffmpeg() -> None:
    if binaries.missing():
        pytest.skip("needs FFmpeg and ffprobe")


def _run(argv) -> None:
    Path(argv[-1]).parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(argv, check=True, capture_output=True)


def make_video(path: Path, *, seconds: float = 4, size: str = "640x360", rate: int = 30, keyint: int | None = None,
               audio: bool = True, codec: str = "libx264", extra: tuple = ()) -> Path:
    """A test picture with a moving counter (testsrc2), and a 440 Hz tone."""
    need_ffmpeg()
    argv = [binaries.ffmpeg(), "-y", "-v", "error", "-f", "lavfi", "-i",
            f"testsrc2=size={size}:rate={rate}:duration={seconds}"]
    if audio:
        argv += ["-f", "lavfi", "-i", f"sine=frequency=440:duration={seconds}:sample_rate=48000"]
    argv += ["-c:v", codec, "-pix_fmt", "yuv420p"]
    if codec == "libx264":
        argv += ["-preset", "ultrafast"]
    if keyint:
        argv += ["-g", str(keyint), "-keyint_min", str(keyint), "-sc_threshold", "0"]
    if audio:
        argv += ["-c:a", "aac", "-shortest"]
    _run([*argv, *extra, str(path)])
    return path


def make_tone_and_silence(path: Path, *, tone_s: float = 1.0, silence_s: float = 1.0, amplitude: float = 0.5,
                          video: bool = False) -> Path:
    """`tone_s` seconds of a 200 Hz tone at `amplitude` (of full scale),
    then `silence_s` seconds of silence, as WAV (or with a picture, MP4)."""
    need_ffmpeg()
    total = tone_s + silence_s
    expr = f"if(lt(t\\,{tone_s})\\,{amplitude}*sin(2*PI*200*t)\\,0)"
    argv = [binaries.ffmpeg(), "-y", "-v", "error", "-f", "lavfi", "-i",
            f"aevalsrc={expr}:sample_rate=48000:duration={total}"]
    if video:
        argv += ["-f", "lavfi", "-i", f"color=c=black:size=160x90:rate=10:duration={total}",
                 "-map", "1:v", "-map", "0:a", "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
                 "-c:a", "pcm_s16le", "-f", "mov"]
    else:
        argv += ["-c:a", "pcm_s16le"]
    _run([*argv, str(path)])
    return path


def make_picture(path: Path, size: str = "320x240") -> Path:
    need_ffmpeg()
    _run([binaries.ffmpeg(), "-y", "-v", "error", "-f", "lavfi", "-i", f"testsrc2=size={size}:duration=1",
          "-frames:v", "1", str(path)])
    return path


def add_project(conn, media: dict, *, video_id: str = "", project_id: str | None = None) -> str:
    """A project row whose document names `media` ({media id: source}),
    written as the store writes documents (plain JSON)."""
    project_id = project_id or uuid.uuid4().hex
    doc = {"schema": 1, "name": "", "seq": 1, "media": {
        key: {"id": key, "kind": kind, "source": source}
        for key, (kind, source) in media.items()}, "tracks": []}
    conn.execute("INSERT INTO projects (id, video_id, doc, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
                 (project_id, video_id, json.dumps(doc, separators=(",", ":"), sort_keys=True), NOW, NOW))
    conn.commit()
    return project_id


def add_video(conn, video_id: str, title: str = "A stream") -> None:
    conn.execute("INSERT INTO videos (video_id, title, status, created_at, updated_at) VALUES (?, ?, 'done', ?, ?)",
                 (video_id, title, NOW, NOW))
    conn.commit()
