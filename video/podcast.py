"""Podcast clips — a separate, opt-in path for multi-cam, multi-person footage.

v2, rebuilt from creator feedback on v1 (which letterboxed everything):

  1. FACE TRACKING FIRST. One real speaker on screen — even with someone
     else's legs or shoulder in frame — renders as a normal vertical tracked
     crop. A person with no visible head/face is never treated as a subject,
     which is exactly what separates "Brad talking" from "Sara's legs".
  2. CUTS SNAP. Podcasts hard-cut between camera angles. When the target
     position jumps like a cut, the crop jumps with it instead of panning
     across the set — that pan was most of the v1-era jitter.
  3. SPLIT WHEN NEEDED. Two real speakers who can't share one 9:16 crop get
     a 50/50 stacked split (each speaker cropped to their own band). If they
     DO fit together, one steady crop frames both — only cropped when needed.
  4. LETTERBOX LAST. Only for 3+ speakers (or as a user override), and it is
     the tight subject-region letterbox (subjects fill the frame), never the
     whole 16:9 frame shrunken into 9:16.

Isolation: this module is only imported when the Podcast toggle set the flag.
It REUSES the tracker's detection helpers read-only (imports); it never
modifies video/tracker.py, and nothing in the stream path imports this file —
with the toggle off, normal clips run the exact same code as before.
"""

from pathlib import Path

import cv2
import numpy as np

# Read-only reuse of the tracker's detection machinery (YOLO pose model,
# identity assignment, face box, talking proxy). Importing these changes
# nothing about how the stream path behaves.
from video.tracker import _assign, _detect, _face_box, _get_model, _update_speaking

# Podcast-path smoothing. Snap distinguishes a camera CUT from movement:
# panning across a cut is what looked jittery.
_EMA = 0.35
_PAN_SPEED = 0.25   # frame-widths/second while genuinely following someone
_SNAP_JUMP = 0.25   # a target jump bigger than this is a cut: jump, don't pan
_DEAD_ZONE = 0.02
_BAND_ASPECT = 1080 / 960  # each split band is 1080x960


def analyze(
    clip_path: Path,
    model_name: str = "yolov8n-pose.pt",
    sample_fps: float = 8.0,
) -> dict:
    """Watch the clip and decide its podcast layout.

    Returns one of:
      {"mode": "track", "path": [...], "face_y": ...}   tracked vertical crop
      {"mode": "podcast_split", "top": box, "bottom": box}  50/50 stacked
      {"mode": "fit_blur", "region": (x0,y0,x1,y1)}     tight-region letterbox
    (track / fit_blur render through the existing renderer unchanged.)
    """
    cap = cv2.VideoCapture(str(clip_path))
    if not cap.isOpened():
        raise RuntimeError(f"OpenCV could not open {clip_path}")
    video_fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    frame_step = max(1, round(video_fps / sample_fps))
    dt = frame_step / video_fps
    model = _get_model(model_name)

    tracks: dict = {}
    samples: list[tuple[float, dict]] = []  # (t, {tid: center_x}) per sample
    head_seen: dict[int, int] = {}          # sightings WITH a head/face
    speak_sum: dict[int, float] = {}
    w = h = None
    n_samples = 0
    frame_idx = 0

    while True:
        ok = cap.grab()
        if not ok:
            break
        if frame_idx % frame_step:
            frame_idx += 1
            continue
        ok, frame = cap.retrieve()
        if not ok:
            break
        t = frame_idx / video_fps
        h, w = frame.shape[:2]
        n_samples += 1

        seen_now: dict[int, float] = {}
        for tid in _assign(tracks, _detect(model, frame, 0.4), t):
            tr = tracks[tid]
            x1, y1, x2, y2, conf = tr.box[:5]
            area = (x2 - x1) * (y2 - y1) / (w * h)
            tr.dominance = 0.7 * tr.dominance + 0.3 * (conf * area)
            tr.n_seen += 1
            body_cx = ((x1 + x2) / 2) / w
            head = tr.box[5] if len(tr.box) > 5 else None
            got_head = False
            if head is not None:
                head_cx, head_cy, head_w = head
                tr.head_rate = 0.8 * tr.head_rate + 0.2
                tr.head_offset = 0.7 * tr.head_offset + 0.3 * (head_cx / w - body_cx)
                tr.face_w = 0.7 * tr.face_w + 0.3 * (head_w / w)
                tr.head_cys.append(head_cy / h)
                got_head = True
            else:
                tr.head_rate = 0.8 * tr.head_rate
            face = _face_box(frame, tr.box)
            if face is not None:
                fx1, fy1, fx2, fy2 = face
                tr.face_rate = 0.8 * tr.face_rate + 0.2
                tr.face_offset = 0.7 * tr.face_offset + 0.3 * (((fx1 + fx2) / 2) / w - body_cx)
                if head is None:
                    tr.head_cys.append(((fy1 + fy2) / 2) / h)
                _update_speaking(tr, frame, face)
                got_head = True
            else:
                tr.face_rate = 0.8 * tr.face_rate
                tr.speak *= 0.9
            refine = (
                tr.head_offset if tr.head_rate > 0.3
                else (tr.face_offset if tr.face_rate > 0.45 else 0.0)
            )
            cx = body_cx + refine
            tr.centers.append(cx)
            tr.areas.append(area)
            tr.norm_boxes.append((x1 / w, y1 / h, x2 / w, y2 / h))
            head_seen[tid] = head_seen.get(tid, 0) + (1 if got_head else 0)
            speak_sum[tid] = speak_sum.get(tid, 0.0) + tr.speak
            seen_now[tid] = cx
        samples.append((t, seen_now))
        frame_idx += 1
    cap.release()

    if not n_samples or w is None:
        return {"mode": "track", "path": [(0.0, 0.5)]}
    crop_frac = (h * 9 / 16) / w

    # ---- who is a real SPEAKER? ---------------------------------------
    # Head required: a pair of legs or a shoulder edge-of-frame has no head
    # keypoints and no face, so it can never become a subject — the person
    # talking does. Presence is lenient (25%) because a cut-based podcast
    # shows each person only on their own camera's shots.
    subjects = [
        tid for tid, tr in tracks.items()
        if tr.n_seen >= max(8, 0.25 * n_samples)
        and head_seen.get(tid, 0) >= 0.25 * tr.n_seen
        and float(np.median(tr.areas)) >= 0.03
    ]

    if not subjects:
        print("      Podcast layout: no clear speaker — steady center crop")
        return {"mode": "track", "path": [(0.0, 0.5)]}

    if len(subjects) > 2:
        # Last resort: tight letterbox around ALL speakers (subjects fill the
        # frame — not the whole 16:9 shot shrunken down).
        boxes = np.array([
            np.median(np.array(tracks[tid].norm_boxes), axis=0) for tid in subjects
        ])
        region = (
            round(max(0.0, float(boxes[:, 0].min()) - 0.04), 4),
            round(max(0.0, float(boxes[:, 1].min()) - 0.06), 4),
            round(min(1.0, float(boxes[:, 2].max()) + 0.04), 4),
            round(min(1.0, float(boxes[:, 3].max()) + 0.06), 4),
        )
        print(f"      Podcast layout: {len(subjects)} speakers — letterbox (tight region)")
        return {"mode": "fit_blur", "region": region}

    if len(subjects) == 2:
        a, b = subjects
        both = sum(1 for _, seen in samples if a in seen and b in seen)
        either = sum(1 for _, seen in samples if a in seen or b in seen)
        covis = both / either if either else 0.0
        if covis >= 0.5:
            seps = [abs(seen[a] - seen[b]) for _, seen in samples if a in seen and b in seen]
            if seps and float(np.median(seps)) >= crop_frac * 0.7:
                # Two speakers who share the shot but can't share one crop:
                # split — each gets their own band, only cropped because needed.
                top, bottom = sorted(
                    (a, b), key=lambda tid: -speak_sum.get(tid, 0.0) / max(tracks[tid].n_seen, 1)
                )
                print("      Podcast layout: 2 speakers, split screen (50/50)")
                return {
                    "mode": "podcast_split",
                    "top": _median_box(tracks[top]),
                    "bottom": _median_box(tracks[bottom]),
                }
            # They fit together: one steady crop framing both (midpoint path).
        # covis < 0.5: a cut-based two-cam podcast — follow whoever is on
        # screen, snapping across cuts (built by the path loop below).

    # ---- the tracked path: single speaker / midpoint pair / cut-following
    path: list[tuple[float, float]] = []
    sx: float | None = None
    for t, seen in samples:
        vis = [tid for tid in subjects if tid in seen]
        if vis:
            if len(vis) >= 2:
                spread = max(seen[v] for v in vis) - min(seen[v] for v in vis)
                if spread < crop_frac * 0.7:
                    target = sum(seen[v] for v in vis) / len(vis)   # frame both
                else:
                    target = seen[max(vis, key=lambda v: tracks[v].dominance)]
            else:
                target = seen[vis[0]]
            if sx is None or abs(target - sx) > _SNAP_JUMP:
                sx = target  # camera cut (or first sighting): jump, don't pan
            elif abs(target - sx) > _DEAD_ZONE:
                step = _EMA * (target - sx)
                sx += float(np.clip(step, -_PAN_SPEED * dt, _PAN_SPEED * dt))
        if sx is not None:
            path.append((t, float(sx)))

    if not path:
        return {"mode": "track", "path": [(0.0, 0.5)]}
    main = max(subjects, key=lambda tid: tracks[tid].n_seen)
    face_y = (
        round(float(np.median(tracks[main].head_cys)), 4)
        if tracks[main].head_cys else None
    )
    label = "1 speaker, tracked crop" if len(subjects) == 1 else "2 speakers, shared/cut-aware crop"
    print(f"      Podcast layout: {label}")
    return {"mode": "track", "path": path, "face_y": face_y}


def _median_box(tr) -> tuple[float, float, float, float]:
    """A track's typical position: median normalized box over the clip."""
    bx = np.median(np.array(tr.norm_boxes), axis=0)
    return tuple(round(float(v), 4) for v in bx)


def render_clip(
    intermediate: Path,
    output_path: Path,
    decision: dict,
    ass_path: Path | None = None,
    vf_extra: str = "",
    normalize: bool = True,
) -> None:
    """Render by the analyzer's decision. track/fit_blur reuse the existing
    renderer untouched; only the stacked split is rendered here."""
    if decision.get("mode") == "podcast_split":
        _render_stacked(intermediate, decision, output_path, ass_path, vf_extra, normalize)
        return
    from video.cropper import render_vertical

    render_vertical(
        intermediate, decision, output_path,
        ass_path=ass_path, vf_extra=vf_extra, normalize=normalize,
    )


def _band_rect(box, W: int, H: int) -> tuple[int, int, int, int]:
    """A 9:8 (1080x960) crop rect around one speaker, in source pixels.

    Sized from the person's box height with headroom, clamped inside the
    frame. Exact band aspect, so the scale to 1080x960 never distorts."""
    x1, y1, x2, y2 = box
    ch = min(float(H), max((y2 - y1) * H * 1.15, 0.4 * H))
    cw = ch * _BAND_ASPECT
    if cw > W:
        cw = float(W)
        ch = cw / _BAND_ASPECT
    cx = ((x1 + x2) / 2) * W
    y0 = min(max(y1 * H - 0.06 * ch, 0.0), H - ch)   # slight headroom above
    x0 = min(max(cx - cw / 2, 0.0), W - cw)
    # Even dimensions for the encoder.
    return (int(x0) // 2 * 2, int(y0) // 2 * 2, int(cw) // 2 * 2, int(ch) // 2 * 2)


def _render_stacked(
    clip_path: Path,
    decision: dict,
    output_path: Path,
    ass_path: Path | None,
    vf_extra: str,
    normalize: bool,
) -> None:
    """Two speakers stacked 50/50 (1080x960 each). Both crops are STATIC, so
    this layout cannot jitter at all — the steadiest thing a podcast can be."""
    from video.cropper import _run_ffmpeg
    from video.encoding import audio_filter_args, video_encoder_args

    cap = cv2.VideoCapture(str(clip_path))
    W = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    H = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    cap.release()

    tx, ty, tw, th = _band_rect(decision["top"], W, H)
    bx, by, bw, bh = _band_rect(decision["bottom"], W, H)
    extra = f",{vf_extra}" if vf_extra else ""
    sub = f",subtitles={ass_path.name}" if ass_path is not None else ""
    filters = (
        f"[0:v]split=2[t0][b0];"
        f"[t0]crop={tw}:{th}:{tx}:{ty},scale=1080:960:flags=lanczos[t];"
        f"[b0]crop={bw}:{bh}:{bx}:{by},scale=1080:960:flags=lanczos[b];"
        f"[t][b]vstack=inputs=2,setsar=1{extra}{sub}[v]"
    )
    cmd = [
        "ffmpeg", "-y",
        "-i", str(clip_path.resolve()),
        "-filter_complex", filters,
        "-map", "[v]", "-map", "0:a:0?",
        *video_encoder_args(),
        "-pix_fmt", "yuv420p",   # NVENC must not drift off 4:2:0 (see cropper)
        "-c:a", "aac", "-b:a", "128k",
        *audio_filter_args(normalize),
        "-fps_mode", "cfr",
        "-movflags", "+faststart",
        "-shortest",
        str(output_path.resolve()),
    ]
    _run_ffmpeg(cmd, ass_path)
