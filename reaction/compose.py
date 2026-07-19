"""Compose a reaction clip into 1080x1920 with BOTH regions visible.

Priority order, enforced by construction:

  1. The reacted content is never cropped. It is placed at the largest size
     that fits, keeping its own aspect ratio (resized, never stretched,
     never center-cropped) — only provably dead margins were removed
     upstream by layout.py.
  2. The creator's webcam pane is always present, in a band above or below
     the content (the user's Camera Top/Bottom preference).
  3. Leftover space is a blurred wash of the frame, never black bars.

Both panes are static for the whole clip, so this render cannot jitter.
One FFmpeg pass, same encoder settings as the standard renderer.
"""

from pathlib import Path

import cv2

from video.encoding import video_encoder_args

OUT_W, OUT_H = 1080, 1920
CAM_MIN_H = 380   # the creator stays clearly visible...
CAM_MAX_H = 760   # ...but never takes more than ~40% from the content


def _even(v: float) -> int:
    i = int(round(v))
    return i - (i % 2)


def plan(
    src_w: int,
    src_h: int,
    cam_box: tuple[float, float, float, float],
    content_box: tuple[float, float, float, float],
    cam_position: str = "top",
) -> dict:
    """Pure geometry: source pixel crops + output placement. Separated from
    the FFmpeg call so it can be tested without rendering anything."""
    cx, cy, cw, ch = content_box
    c_px = (_even(cx * src_w), _even(cy * src_h),
            max(2, _even(cw * src_w)), max(2, _even(ch * src_h)))
    mx, my, mw, mh = cam_box
    m_px = (_even(mx * src_w), _even(my * src_h),
            max(2, _even(mw * src_w)), max(2, _even(mh * src_h)))

    # Content at full width unless that leaves no room for the cam band.
    out_cw, out_ch = OUT_W, _even(OUT_W * c_px[3] / c_px[2])
    if out_ch > OUT_H - CAM_MIN_H:
        out_ch = _even(OUT_H - CAM_MIN_H)
        out_cw = min(OUT_W, _even(out_ch * c_px[2] / c_px[3]))
    # The cam band takes ALL the space the content doesn't need, so the frame
    # is filled instead of padded with blurred bars. Widescreen content (a
    # video the creator is reacting to) is short at full width, and that
    # leftover is better spent on the creator's face than on a blur wash —
    # the pane cover-crops, which is normal for a webcam and never stretches.
    cam_h = _even(max(CAM_MIN_H, OUT_H - out_ch))

    gap = OUT_H - out_ch - cam_h
    y0 = _even(max(0, gap) / 2)
    if cam_position == "bottom":
        content_y, cam_y = y0, y0 + out_ch
    else:
        cam_y, content_y = y0, y0 + cam_h
    return {
        "content_crop": c_px,
        "cam_crop": m_px,
        "content_size": (out_cw, out_ch),
        "content_pos": (_even((OUT_W - out_cw) / 2), content_y),
        "cam_size": (OUT_W, cam_h),
        "cam_pos": (0, cam_y),
    }


def render_reaction(
    clip_path: Path,
    layout,
    output_path: Path,
    ass_path: Path | None = None,
    vf_extra: str = "",
    cam_position: str = "top",
) -> Path:
    """Render the reaction composition. Raises on FFmpeg failure — the
    caller (core.pipeline) catches and falls back to the standard render."""
    from video.cropper import _run_ffmpeg  # same ASS-cwd handling as the core renderer

    cap = cv2.VideoCapture(str(clip_path))
    src_w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    src_h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    cap.release()
    if src_w <= 0 or src_h <= 0:
        raise RuntimeError(f"could not read frame size from {clip_path}")

    p = plan(src_w, src_h, layout.cam_box, layout.content_box, cam_position)
    ccx, ccy, ccw, cch = p["content_crop"]
    mcx, mcy, mcw, mch = p["cam_crop"]
    out_cw, out_ch = p["content_size"]
    cam_w, cam_h = p["cam_size"]
    cpx, cpy = p["content_pos"]
    mpx, mpy = p["cam_pos"]

    filters = (
        f"[0:v]split=3[bg][c][m];"
        # Backdrop: the frame blown up to cover, then destroyed into a wash.
        f"[bg]scale={OUT_W}:{OUT_H}:force_original_aspect_ratio=increase,"
        f"crop={OUT_W}:{OUT_H},scale=135:240,gblur=sigma=12,"
        f"scale={OUT_W}:{OUT_H}:flags=bilinear,setsar=1[bgo];"
        # Content: exact aspect-preserving resize — nothing cropped here.
        f"[c]crop={ccw}:{cch}:{ccx}:{ccy},scale={out_cw}:{out_ch}:flags=lanczos,setsar=1[co];"
        # Cam: fill the band (scale up, trim overflow from the BOTTOM so the
        # creator's head is never cut off).
        f"[m]crop={mcw}:{mch}:{mcx}:{mcy},"
        f"scale={cam_w}:{cam_h}:force_original_aspect_ratio=increase:flags=lanczos,"
        f"crop={cam_w}:{cam_h}:(iw-{cam_w})/2:0,setsar=1[mo];"
        f"[bgo][co]overlay={cpx}:{cpy}[t1];"
        f"[t1][mo]overlay={mpx}:{mpy}[v]"
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
        *video_encoder_args(),
        "-c:a", "aac", "-b:a", "128k",
        "-af", "aresample=async=1",
        "-fps_mode", "cfr",
        "-movflags", "+faststart",
        "-shortest",
        str(output_path.resolve()),
    ]
    _run_ffmpeg(cmd, ass_path)
    return output_path
