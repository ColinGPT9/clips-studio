"""Vertical 9:16 rendering from a tracking result.

Three modes (see video/tracker.py for how they're chosen):

  track    - OpenCV follows the smoothed crop path frame by frame, then FFmpeg
             scales to 1080x1920, burns captions, and muxes the audio back in.
  split    - gameplay + facecam layout: webcam region stacked on top (35%),
             centered gameplay crop below (65%). Both regions static, so this
             renders in ONE pure-FFmpeg pass with no per-frame Python.
  fit_blur - subjects too spread out to crop without cutting someone off: show
             the full width fit into 1080 wide, centered, with the empty top
             and bottom filled by a blurred, zoomed copy of the video.

No-stretch guarantee in every mode: content keeps its aspect ratio and only
ever gets uniformly scaled — distortion is impossible by construction.
"""

import subprocess
import threading
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
    cam_position: str = "top",
) -> Path:
    """vf_extra: an optional FFmpeg filter fragment (color preset) applied
    after scaling and before captions, so captions stay unfiltered.
    cam_position: 'top' | 'bottom' — which band the facecam occupies in the
    split (gameplay + webcam) layout. Ignored by the other modes."""
    if tracking["mode"] == "split":
        return _render_split(
            clip_path, tracking["webcam_box"], output_path, ass_path, vf_extra,
            cam_position=cam_position,
        )
    if tracking["mode"] == "fit_blur":
        return _render_fit_blur(clip_path, tracking.get("region"), output_path, ass_path, vf_extra)
    return _render_tracked(
        clip_path, tracking["path"], output_path, ass_path, vf_extra,
        face_y=tracking.get("face_y"),
    )


def _render_fit_blur(
    clip_path: Path,
    region: tuple[float, float, float, float] | None,
    output_path: Path,
    ass_path: Path | None,
    vf_extra: str = "",
) -> Path:
    """The subject's bounding region (normalized x0,y0,x1,y1) shown at FULL
    output width on a heavily blurred backdrop — blurred bands land on the top
    and bottom only, never at the sides, and nobody is cropped out. Nothing is
    stretched: the region keeps its own aspect ratio. Pure FFmpeg.
    """
    x0, y0, x1, y1 = (region or (0.0, 0.0, 1.0, 1.0))
    x0 = max(0.0, min(0.85, x0))
    x1 = max(x0 + 0.15, min(1.0, x1))
    y0 = max(0.0, min(0.85, y0))
    y1 = max(y0 + 0.15, min(1.0, y1))
    rw, rh, rx, ry = x1 - x0, y1 - y0, x0, y0
    crop_region = f"crop=iw*{rw:.4f}:ih*{rh:.4f}:iw*{rx:.4f}:ih*{ry:.4f}"

    # Background: the same crop, blown up to COVER 1080x1920, then destroyed:
    # downscaled hard + blurred + upscaled = an unrecognizable color wash.
    bg = (
        f"{crop_region},scale=1080:1920:force_original_aspect_ratio=increase,"
        f"crop=1080:1920,scale=135:240,gblur=sigma=12,scale=1080:1920:flags=bilinear"
    )
    # Foreground: full width; height follows the region's own aspect ratio
    # (capped at the screen so an unusually tall region can never overflow).
    fg = (
        f"{crop_region},scale=1080:-2:flags=lanczos,crop=w=1080:h=min(ih\\,1920)"
        + (f",{vf_extra}" if vf_extra else "")
    )
    filters = f"[0:v]split=2[a][b];[a]{bg}[bg];[b]{fg}[fg];[bg][fg]overlay=0:(H-h)/2,setsar=1[v]"
    if ass_path is not None:
        filters += f";[v]subtitles={ass_path.name}[v2]"
        vout = "[v2]"
    else:
        vout = "[v]"

    cmd = [
        "ffmpeg", "-y",
        # No -hwaccel here: GPU decode feeding this split/overlay/blur graph can
        # drift the video timing off the audio. CPU decode keeps A/V locked.
        "-i", str(clip_path.resolve()),
        "-filter_complex", filters,
        "-map", vout, "-map", "0:a:0?",
        *video_encoder_args(),
        "-c:a", "aac", "-b:a", "128k",
        "-af", "aresample=async=1",  # keep audio aligned to the video timeline
        "-fps_mode", "cfr",          # constant output frame rate
        "-movflags", "+faststart",
        "-shortest",
        str(output_path.resolve()),
    ]
    _run_ffmpeg(cmd, ass_path)
    return output_path


# ---- follow-the-subject mode -------------------------------------------------


def _render_tracked(
    clip_path: Path,
    crop_path: list[tuple[float, float]],
    output_path: Path,
    ass_path: Path | None,
    vf_extra: str = "",
    face_y: float | None = None,
) -> Path:
    cap = cv2.VideoCapture(str(clip_path))
    if not cap.isOpened():
        raise RuntimeError(f"OpenCV could not open {clip_path}")

    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    src_w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    src_h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))

    # Face in the top quarter of a full-height crop would sit under the
    # TikTok/Instagram tab bar. For those clips, crop a WIDER 4:5 window
    # instead: shown at full output width it's shorter than the screen, so it
    # can start below the tab area — blurred bands land on top/bottom ONLY
    # (never at the sides), and the wider view shows more of the scene.
    top_safe = face_y is not None and face_y < 0.25
    crop_w = int(src_h * (4 / 5 if top_safe else 9 / 16))
    crop_w -= crop_w % 2  # even width required by H.264
    crop_w = min(crop_w, src_w)

    def produce(write) -> None:
        """Decode, follow the subject, and hand each cropped frame to ffmpeg.

        Frames go straight down the pipe — no intermediate file. Writing an
        mp4v staging file here cost a whole CPU encode and threw away detail
        the NVENC pass then could not get back."""
        frame_idx = 0
        try:
            while True:
                ok, frame = cap.read()
                if not ok:
                    break
                t = frame_idx / fps
                center_x = _interpolate(crop_path, t) * src_w
                x0 = int(round(center_x - crop_w / 2))
                x0 = max(0, min(src_w - crop_w, x0))  # clamp inside the frame
                # Column slices are non-contiguous views; the pipe needs bytes.
                write(np.ascontiguousarray(frame[:, x0 : x0 + crop_w]).tobytes())
                frame_idx += 1
        finally:
            cap.release()

    # Input 0 is the raw cropped video on stdin; input 1 stays the original
    # clip, which is still where the audio comes from.
    pipe_in = [
        "-f", "rawvideo", "-pix_fmt", "bgr24",   # OpenCV hands us BGR
        "-s", f"{crop_w}x{src_h}", "-r", f"{fps}",
        "-i", "pipe:0",
    ]

    # Platform-safe letterbox: the wider 4:5 crop at FULL output width (1080)
    # is shorter than the screen, so it starts just below the tab area with
    # heavily blurred bands above and below it — never at the sides — and the
    # face lands toward the center of the screen. Nothing is stretched.
    if top_safe:
        top = int(0.115 * 1920)            # content starts under the FYP tabs
        fg = "scale=1080:-2:flags=lanczos" + (f",{vf_extra}" if vf_extra else "")
        filters = (
            f"[0:v]split=2[a][b];"
            # Downscale hard + blur + upscale: an unrecognizable color wash.
            f"[a]scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,"
            f"scale=135:240,gblur=sigma=12,scale=1080:1920:flags=bilinear[bg];"
            f"[b]{fg}[fg];"
            f"[bg][fg]overlay=0:{top},setsar=1[v]"
        )
        if ass_path is not None:
            filters += f";[v]subtitles={ass_path.name}[v]"
        cmd = [
            "ffmpeg", "-y",
            *pipe_in,                         # cropped frames on stdin
            "-i", str(clip_path.resolve()),   # source of the audio
            "-filter_complex", filters,
            "-map", "[v]", "-map", "1:a:0?",
            *video_encoder_args(),
            "-c:a", "aac", "-b:a", "128k",
            "-af", "aresample=async=1",
            "-fps_mode", "cfr",
            "-movflags", "+faststart",
            "-shortest",
            str(output_path.resolve()),
        ]
        _run_ffmpeg_piped(cmd, ass_path, produce)
        return output_path

    # Uniform scale to 1080x1920 + color filter + captions + mux audio.
    vf = "scale=1080:1920:flags=lanczos,setsar=1"
    if vf_extra:
        vf += f",{vf_extra}"
    if ass_path is not None:
        vf += f",subtitles={ass_path.name}"
    cmd = [
        "ffmpeg", "-y",
        *pipe_in,                         # cropped frames on stdin
        "-i", str(clip_path.resolve()),   # source of the audio
        "-map", "0:v:0", "-map", "1:a:0?",
        "-vf", vf,
        *video_encoder_args(),  # NVENC when available
        "-c:a", "aac", "-b:a", "128k",
        "-af", "aresample=async=1",       # align audio to the cropped video
        "-fps_mode", "cfr",
        "-movflags", "+faststart",
        "-shortest",
        str(output_path.resolve()),
    ]
    _run_ffmpeg_piped(cmd, ass_path, produce)
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
    cam_position: str = "top",
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
    # (uniform scale up, then trim overflow — never stretch). The vstack
    # order decides which band is on top — user-switchable per clip.
    stack = "[game][cam]" if cam_position == "bottom" else "[cam][game]"
    filters = (
        f"[0:v]crop={cam_w}:{cam_h}:{cam_x}:{cam_y},"
        f"scale=1080:{CAM_H}:force_original_aspect_ratio=increase:flags=lanczos,"
        f"crop=1080:{CAM_H},setsar=1[cam];"
        f"[0:v]crop={game_w}:{src_h}:{game_x}:0,"
        f"scale=1080:{GAME_H}:flags=lanczos,setsar=1[game];"
        f"{stack}vstack=inputs=2[v]"
    )
    if vf_extra:
        filters += f";[v]{vf_extra}[v]"
    if ass_path is not None:
        filters += f";[v]subtitles={ass_path.name}[v]"

    cmd = [
        "ffmpeg", "-y",
        # CPU decode for the filter graph — keeps A/V locked (see _render_fit_blur).
        "-i", str(clip_path.resolve()),
        "-filter_complex", filters,
        "-map", "[v]", "-map", "0:a:0?",
        *video_encoder_args(),  # NVENC when available
        "-c:a", "aac", "-b:a", "128k",
        "-af", "aresample=async=1",
        "-fps_mode", "cfr",
        "-movflags", "+faststart",
        "-shortest",
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


def _run_ffmpeg_piped(cmd: list[str], ass_path: Path | None, produce) -> None:
    """Run ffmpeg with frames fed to its stdin by `produce(write)`.

    The tracked crop used to stage its frames in an mp4v file written by
    OpenCV, which ffmpeg then re-decoded and re-encoded — two encodes, the
    first on the CPU and lossy, so the real encode started from degraded
    frames. Feeding raw frames straight in removes that pass entirely.
    """
    workdir = ass_path.parent if ass_path is not None else None
    proc = subprocess.Popen(
        cmd, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, cwd=workdir
    )
    assert proc.stdin is not None and proc.stderr is not None

    # ffmpeg chatters on stderr while it encodes. If nobody empties that pipe
    # it fills, ffmpeg blocks writing to it, and we block writing frames to
    # stdin — a deadlock that turned a 45-second render into fifteen minutes.
    # subprocess.run() drains both for you; feeding stdin ourselves means we
    # have to. A reader thread does it.
    tail: list[bytes] = []

    def drain() -> None:
        for line in proc.stderr:  # type: ignore[union-attr]
            tail.append(line)
            del tail[:-40]  # keep only the last lines, for the error message

    reader = threading.Thread(target=drain, daemon=True)
    reader.start()

    try:
        produce(proc.stdin.write)
    except (BrokenPipeError, OSError):
        pass  # ffmpeg died early; its stderr below is the real error
    finally:
        try:
            proc.stdin.close()
        except (BrokenPipeError, OSError):
            pass
    proc.wait()
    reader.join(timeout=10)
    if proc.returncode != 0:
        msg = b"".join(tail).decode("utf-8", "replace")[-2000:]
        raise RuntimeError(f"ffmpeg render failed:\n{msg}")
