"""Podcast clips — a separate, opt-in path for multi-cam, multi-person footage.

v3, rebuilt after v2 failed on real podcast footage. v2 made one decision
for the whole clip, but a podcast is a sequence of SHOTS: it hard-cuts
between camera angles, and every angle shows people at different positions
and sizes. Deciding globally meant the same person counted as two "speakers"
(one track per angle — v2 split-screened a guy with himself), letterbox
regions mixed positions from different shots (quarter-faces), and smoothing
panned across cuts hunting for the face.

So v3 works the way the podcast itself is edited — SHOT BY SHOT:

  1. Camera cuts are detected first (frame differencing).
  2. Within each shot, the framing is ONE static crop — podcast guests sit
     still, so a fixed face-centered crop is rock steady and is on the face
     from the shot's first frame. No drifting onto faces after a cut.
  3. The crop centers on WHO IS TALKING in that shot (mouth motion), falling
     back to the most prominent person when nobody clearly talks. A person
     with no visible head (someone's legs at frame edge) never qualifies.
  4. At each cut the crop SNAPS to the new shot's framing. Nothing pans.

No split screens and no automatic letterbox — both produced bad results on
real footage. The editor's manual Layout override (Center/Letterbox) still
works per clip.

Isolation: imported only when the Podcast toggle set the flag; reuses the
tracker's detection helpers read-only; video/tracker.py is never modified,
and with the toggle off normal clips run exactly the code they ran before.
"""

from pathlib import Path

import cv2
import numpy as np

# Read-only reuse of the tracker's detection machinery. Importing these
# changes nothing about how the stream path behaves.
from video.tracker import _assign, _detect, _face_box, _get_model, _update_speaking

_CUT_DIFF = 25.0      # mean abs gray diff (0..255) between samples = a camera cut
_MIN_SHOT = 0.5       # ignore "shots" shorter than this (flash/transition frames)
_MIN_AREA = 0.03      # a subject must be at least this fraction of the frame
_TALK_FLOOR = 0.004   # mouth-motion below this means nobody is visibly talking
_TALK_MARGIN = 1.4    # the talker must beat the runner-up by this factor


def analyze(
    clip_path: Path,
    model_name: str = "yolov8n-pose.pt",
    sample_fps: float = 8.0,
) -> dict:
    """Shot-by-shot framing for a podcast clip.

    Returns {"mode": "track", "path": [...], "face_y": ...} where the path is
    a STEP function: constant inside each shot, jumping exactly at the cuts.
    Rendered by the existing renderer unchanged."""
    cap = cv2.VideoCapture(str(clip_path))
    if not cap.isOpened():
        raise RuntimeError(f"OpenCV could not open {clip_path}")
    video_fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    frame_step = max(1, round(video_fps / sample_fps))
    model = _get_model(model_name)

    shots: list[dict] = []          # finished shots: {"t0","t1","x","cy"}
    prev_small = None               # downscaled gray of the previous sample
    shot_t0: float | None = None
    shot_tracks: dict = {}          # per-shot identity tracking (reset at cuts)
    shot_stats: dict[int, dict] = {}  # tid -> {"cxs","cys","areas","speak","head"}
    last_t = 0.0
    w = h = None
    frame_idx = 0

    def close_shot(t_end: float) -> None:
        """Pick this shot's focus and freeze its framing."""
        if shot_t0 is None or t_end - shot_t0 < _MIN_SHOT:
            return
        cands = [
            (tid, s) for tid, s in shot_stats.items()
            # A subject needs a head/face sighting (legs never qualify) and
            # real prominence in THIS shot.
            if s["head"] > 0 and s["cxs"] and float(np.median(s["areas"])) >= _MIN_AREA
        ]
        if not cands:
            shots.append({"t0": shot_t0, "t1": t_end, "x": None, "cy": None})
            return
        # Who talks in this shot? Mouth motion, requiring a clear winner.
        speaks = {tid: s["speak"] / max(len(s["cxs"]), 1) for tid, s in cands}
        ordered = sorted(speaks.items(), key=lambda kv: -kv[1])
        talker = None
        if ordered[0][1] > _TALK_FLOOR and (
            len(ordered) == 1 or ordered[0][1] > _TALK_MARGIN * ordered[1][1]
        ):
            talker = ordered[0][0]
        if talker is not None:
            focus = [talker]
        else:
            # Nobody clearly talking: the podcast's own camera usually frames
            # the person who matters largest — take the most prominent, or
            # both when two similar people sit close enough to share a crop.
            by_area = sorted(cands, key=lambda kv: -float(np.median(kv[1]["areas"])))
            focus = [by_area[0][0]]
            if len(by_area) >= 2 and w is not None:
                crop_frac = (h * 9 / 16) / w
                xa = float(np.median(by_area[0][1]["cxs"]))
                xb = float(np.median(by_area[1][1]["cxs"]))
                a0, a1 = (float(np.median(s["areas"])) for _, s in by_area[:2])
                if a1 >= 0.6 * a0 and abs(xa - xb) < crop_frac * 0.7:
                    focus.append(by_area[1][0])
        xs = [float(np.median(shot_stats[tid]["cxs"])) for tid in focus]
        cys = [float(np.median(shot_stats[tid]["cys"])) for tid in focus if shot_stats[tid]["cys"]]
        shots.append({
            "t0": shot_t0, "t1": t_end,
            "x": sum(xs) / len(xs),
            "cy": (sum(cys) / len(cys)) if cys else None,
        })

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

        # ---- cut detection: a big frame-to-frame change is a camera cut ----
        small = cv2.cvtColor(cv2.resize(frame, (48, 27)), cv2.COLOR_BGR2GRAY).astype(np.float32)
        if prev_small is not None and float(np.abs(small - prev_small).mean()) > _CUT_DIFF:
            close_shot(t)
            shot_t0 = None
            shot_tracks = {}
            shot_stats = {}
        prev_small = small
        if shot_t0 is None:
            shot_t0 = t

        # ---- per-shot people tracking (identities never cross a cut) ------
        for tid in _assign(shot_tracks, _detect(model, frame, 0.4), t):
            tr = shot_tracks[tid]
            x1, y1, x2, y2, conf = tr.box[:5]
            st = shot_stats.setdefault(
                tid, {"cxs": [], "cys": [], "areas": [], "speak": 0.0, "head": 0}
            )
            body_cx = ((x1 + x2) / 2) / w
            head = tr.box[5] if len(tr.box) > 5 else None
            face = _face_box(frame, tr.box)
            if head is not None:
                st["cxs"].append(head[0] / w)
                st["cys"].append(head[1] / h)
                st["head"] += 1
            elif face is not None:
                fx1, fy1, fx2, fy2 = face
                st["cxs"].append(((fx1 + fx2) / 2) / w)
                st["cys"].append(((fy1 + fy2) / 2) / h)
                st["head"] += 1
            else:
                st["cxs"].append(body_cx)
            st["areas"].append((x2 - x1) * (y2 - y1) / (w * h))
            if face is not None:
                _update_speaking(tr, frame, face)
                st["speak"] += tr.speak
            else:
                tr.speak *= 0.9
        last_t = t
        frame_idx += 1
    cap.release()
    close_shot(last_t + 1.0 / sample_fps)

    if not shots or w is None:
        return {"mode": "track", "path": [(0.0, 0.5)]}

    # ---- the path: constant inside each shot, snapping at each cut --------
    # Two points per shot make the renderer's interpolation flat within the
    # shot; the jump between shots spans one sample (~0.1s) — a clean snap.
    # A shot with nobody detected holds the previous framing.
    path: list[tuple[float, float]] = []
    x_prev = 0.5
    for s in shots:
        x = s["x"] if s["x"] is not None else x_prev
        path.append((s["t0"], float(x)))
        path.append((s["t1"], float(x)))
        x_prev = x
    n_cuts = len(shots) - 1
    print(f"      Podcast layout: {len(shots)} shot(s), {n_cuts} cut(s) — "
          f"static face-centered framing per shot")

    cys = [s["cy"] for s in shots if s["cy"] is not None]
    face_y = round(float(np.median(cys)), 4) if cys else None
    return {"mode": "track", "path": path, "face_y": face_y}


def render_clip(
    intermediate: Path,
    output_path: Path,
    decision: dict,
    ass_path: Path | None = None,
    vf_extra: str = "",
    normalize: bool = True,
) -> None:
    """Render by the analyzer's decision through the existing renderer —
    nothing podcast-specific left at render time."""
    from video.cropper import render_vertical

    render_vertical(
        intermediate, decision, output_path,
        ass_path=ass_path, vf_extra=vf_extra, normalize=normalize,
    )
