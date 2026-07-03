"""Vertical 9:16 rendering from a tracking result.

Two modes (see video/tracker.py for how they're chosen):

  track  - OpenCV follows the smoothed crop path frame by frame, then FFmpeg
           scales to 1080x1920, burns captions, and muxes the audio back in.
  split  - gameplay + facecam layout: webcam region stacked on top (35%),
           centered gameplay crop below (65%). Both regions are static, so
           this renders in ONE pure-FFmpeg pass with no per-frame Python.

No-stretch guarantee in both modes: every crop window keeps a fixed aspect
ratio and only ever gets uniformly scaled — distortion is impossible by
construction.
"""

import subprocess
from pathlib import Path

import cv2
import numpy as np

from video.encoding import video_encoder_args

CAM_H = 672    # webcam band height in the 1080x1920 split layout (35%)
GAME_H = 1248  # gameplay band height (65%)


def render_vertical(
    clip_path: Path,
    tracking: dict,
    output_path: Path,
    ass_path: Path | None = None,
    vf_extra: str = "",
) -> Path:
    """vf_extra: an optional FFmpeg filter fragment (color preset) applied
    after scaling and before captions, so captions stay unfiltered."""
    if tracking["mode"] == "split":
        return _render_split(clip_path, tracking["webcam_box"], output_path, ass_path, vf_extra)
    return _render_tracked(clip_path, tracking["path"], output_path, ass_path, vf_extra)


# ---- follow-the-subject mode -------------------------------------------------


def _render_tracked(
    clip_path: Path,
    crop_path: list[tuple[float, float]],
    output_path: Path,
    ass_path: Path | None,
    vf_extra: str = "",
) -> Path:
    cap = cv2.VideoCapture(str(clip_path))
    if not cap.isOpened():
        raise RuntimeError(f"OpenCV could not open {clip_path}")

    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    src_w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    src_h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))

    crop_w = int(src_h * 9 / 16)
    crop_w -= crop_w % 2  # even width required by H.264
    crop_w = min(crop_w, src_w)

    temp_path = output_path.parent / (output_path.stem + ".cropped.mp4")
    writer = cv2.VideoWriter(
        str(temp_path), cv2.VideoWriter_fourcc(*"mp4v"), fps, (crop_w, src_h)
    )
    if not writer.isOpened():
        cap.release()
        raise RuntimeError(f"OpenCV VideoWriter failed to open {temp_path} ({crop_w}x{src_h} @ {fps}fps)")

    frame_idx = 0
    while True:
        ok, frame = cap.read()
        if not ok:
            break
        t = frame_idx / fps
        center_x = _interpolate(crop_path, t) * src_w
        x0 = int(round(center_x - crop_w / 2))
        x0 = max(0, min(src_w - crop_w, x0))  # clamp window inside the frame
        # Column slices are non-contiguous views; VideoWriter needs contiguous data.
        writer.write(np.ascontiguousarray(frame[:, x0 : x0 + crop_w]))
        frame_idx += 1

    cap.release()
    writer.release()

    # Uniform scale to 1080x1920 + color filter + captions + mux audio.
    vf = "scale=1080:1920:flags=lanczos,setsar=1"
    if vf_extra:
        vf += f",{vf_extra}"
    if ass_path is not None:
        vf += f",subtitles={ass_path.name}"
    cmd = [
        "ffmpeg", "-y",
        "-i", str(temp_path.resolve()),
        "-i", str(clip_path.resolve()),
        "-map", "0:v:0", "-map", "1:a:0?",
        "-vf", vf,
        *video_encoder_args(),  # NVENC when available
        "-c:a", "aac", "-b:a", "128k",
        "-movflags", "+faststart",
        "-shortest",
        str(output_path.resolve()),
    ]
    _run_ffmpeg(cmd, ass_path)
    temp_path.unlink(missing_ok=True)
    return output_path


def _interpolate(path: list[tuple[float, float]], t: float) -> float:
    """Linear interpolation of center_x at time t over the crop path."""
    if t <= path[0][0]:
        return path[0][1]
    if t >= path[-1][0]:
        return path[-1][1]
    for (t0, x0), (t1, x1) in zip(path, path[1:]):
        if t0 <= t <= t1:
            if t1 == t0:
                return x0
            return x0 + (x1 - x0) * (t - t0) / (t1 - t0)
    return path[-1][1]


# ---- gameplay + facecam split mode --------------------------------------------


def _render_split(
    clip_path: Path,
    webcam_box: tuple[float, float, float, float],
    output_path: Path,
    ass_path: Path | None,
    vf_extra: str = "",
) -> Path:
    cap = cv2.VideoCapture(str(clip_path))
    src_w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    src_h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    cap.release()

    bx, by, bw, bh = webcam_box
    cam_x, cam_y = int(bx * src_w), int(by * src_h)
    cam_w, cam_h = int(bw * src_w), int(bh * src_h)
    cam_w -= cam_w % 2
    cam_h -= cam_h % 2

    # Gameplay band: center crop at exactly the band's aspect (1080:1248).
    game_w = int(src_h * 1080 / GAME_H)
    game_w -= game_w % 2
    game_w = min(game_w, src_w)
    game_x = (src_w - game_w) // 2

    # Webcam band: crop the overlay region, fill the 1080x672 band
    # (uniform scale up, then trim overflow — never stretch).
    filters = (
        f"[0:v]crop={cam_w}:{cam_h}:{cam_x}:{cam_y},"
        f"scale=1080:{CAM_H}:force_original_aspect_ratio=increase:flags=lanczos,"
        f"crop=1080:{CAM_H},setsar=1[cam];"
        f"[0:v]crop={game_w}:{src_h}:{game_x}:0,"
        f"scale=1080:{GAME_H}:flags=lanczos,setsar=1[game];"
        f"[cam][game]vstack=inputs=2[v]"
    )
    if vf_extra:
        filters += f";[v]{vf_extra}[v]"
    if ass_path is not None:
        filters += f";[v]subtitles={ass_path.name}[v]"

    cmd = [
        "ffmpeg", "-y",
        "-i", str(clip_path.resolve()),
        "-filter_complex", filters,
        "-map", "[v]", "-map", "0:a:0?",
        *video_encoder_args(),  # NVENC when available
        "-c:a", "aac", "-b:a", "128k",
        "-movflags", "+faststart",
        str(output_path.resolve()),
    ]
    _run_ffmpeg(cmd, ass_path)
    return output_path


def _run_ffmpeg(cmd: list[str], ass_path: Path | None) -> None:
    # cwd is the ass file's folder: the subtitles filter gets a bare filename,
    # avoiding fragile Windows path escaping inside filter args.
    workdir = ass_path.parent if ass_path is not None else None
    result = subprocess.run(cmd, capture_output=True, text=True, cwd=workdir)
    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg render failed:\n{result.stderr[-2000:]}")
