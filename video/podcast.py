"""Podcast clips — a separate, opt-in path for multi-cam, multi-person footage.

v3, rebuilt after v2 failed on real podcast footage, then refined (v4) so
each shot punches in on ONE person instead of averaging two toward the
middle. v2 made one decision for the whole clip, but a podcast is a sequence
of SHOTS: it hard-cuts between camera angles, and every angle shows people at
different positions and sizes. Deciding globally meant the same person
counted as two "speakers" (one track per angle — v2 split-screened a guy
with himself), letterbox regions mixed positions from different shots
(quarter-faces), and smoothing panned across cuts hunting for the face.

So this works the way the podcast itself is edited — SHOT BY SHOT:

  1. Camera cuts are detected first (frame differencing).
  2. Within each shot, the framing is ONE static crop — podcast guests sit
     still, so a fixed face-centered crop is rock steady and is on the face
     from the shot's first frame. No drifting onto faces after a cut.
  3. The crop punches in TIGHT on ONE person — whoever is talking in that
     shot (mouth motion), falling back to the most prominent person when
     nobody clearly talks. It centers on that person's FACE positions only
     (never a body-center guess, never an average of two people), so the
     face lands in frame instead of the crop hovering near the middle.
  4. At each cut the crop SNAPS to the new shot's framing. Nothing pans.

Why one person, not two: earlier versions averaged two nearby people into a
single wide crop, which pulled every shot back toward center and showed both
faces small — the "everyone tiny" look. A podcast is cut so that when the
other person speaks, the camera cuts to them; so the right move per shot is
to frame the one who matters and let the edit's own cuts handle the rest.

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

# Shared framing components — cut detection and "commit to one subject" live
# in video/framing.py now, because the stream tracker needs them too.
from video.framing import is_cut, pick_focus, refine_cuts, small_gray

# Read-only reuse of the tracker's detection machinery. Importing these
# changes nothing about how the stream path behaves.
from video.tracker import (
    _assign,
    _detect,
    _face_box,
    _get_model,
    _update_speaking,
    head_box,
    mouth_region,
)

_MIN_SHOT = 0.5       # ignore "shots" shorter than this (flash/transition frames)
_MIN_AREA = 0.03      # a subject must be at least this fraction of the frame
_TALK_FLOOR = 0.004   # mouth-motion below this means nobody is visibly talking
_TALK_MARGIN = 1.4    # the talker must beat the runner-up by this factor


def _talknet_focus(clip_path: Path, cands: list, video_fps: float):
    """The candidate TalkNet says is speaking most of this shot, or None.

    None means "no opinion" — one candidate, no audio, no model — and the
    caller falls back to mouth motion. It never guesses.

    Scored per shot rather than once per clip because podcast resets identity
    at every cut, so a track id means nothing outside the shot it came from.
    Shots holding a single person skip this entirely, which is most of them in
    a normally-edited podcast.
    """
    if len(cands) < 2:
        return None
    from video.tracker import score_faces

    seen = {tid: st["boxes"] for tid, st in cands if st.get("boxes")}
    if len(seen) < 2:
        return None
    span = max(t for boxes in seen.values() for t, _ in boxes)
    try:
        scored = score_faces(clip_path, seen, span, video_fps)
    except Exception as e:
        print(f"      (podcast speaker detection failed, using motion: {e})")
        return None
    if scored is None:
        return None
    candidates, scores, _ = scored
    # Total speaking evidence across the shot; -inf marks "not on screen",
    # which must not count as quiet.
    best, best_score = None, None
    for tid in candidates:
        v = scores[tid]
        v = v[np.isfinite(v)]
        if v.size == 0:
            continue
        total = float(v.sum())
        if best_score is None or total > best_score:
            best, best_score = tid, total
    if best is None:
        return None
    return next((kv for kv in cands if kv[0] == best), None)


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

    shots: list[dict] = []          # finished shots: {"t0_idx","t1_idx","x","cy"}
    prev_small = None               # downscaled gray of the previous SAMPLE
    shot_start_idx: int | None = None  # frame index the current shot began at
    shot_tracks: dict = {}          # per-shot identity tracking (reset at cuts)
    shot_stats: dict[int, dict] = {}  # tid -> per-subject accumulators (below)
    cut_regions: list[tuple[int, int]] = []  # (prev_sample, cut_sample) to refine
    last_sample_idx = 0
    w = h = None
    frame_idx = 0

    def close_shot(end_idx: int) -> None:
        """Pick this shot's one subject and freeze a tight, face-centered crop."""
        if shot_start_idx is None or (end_idx - shot_start_idx) / video_fps < _MIN_SHOT:
            return
        # A subject counts only if it was seen as a face/head (not just legs
        # at the frame edge) and is prominent enough in THIS shot.
        cands = [
            (tid, s) for tid, s in shot_stats.items()
            if s["face_xs"] and float(np.median(s["areas"])) >= _MIN_AREA
        ]
        if not cands:
            shots.append({"t0_idx": shot_start_idx, "t1_idx": end_idx, "x": None, "cy": None})
            return
        # Talk rate = mouth motion per face sighting. The shared chooser picks
        # the clear talker, else the most prominent face — always exactly ONE
        # subject, never an average of two.
        # Who is actually SPEAKING, when there is a choice to make.
        #
        # pick_focus falls through to max(candidates, key=prominence) — the
        # BIGGEST person — whenever mouth motion fails to beat the runner-up
        # by _TALK_MARGIN. It nearly always fails: measured on real footage
        # the speaker and the listener separate by 7% while their on-screen
        # sizes differ by 45%. On a shot holding a 6'3" man and a 5'5" woman
        # that is not a tiebreak, it is a rule that she never wins.
        #
        # TalkNet answers the actual question. It only runs where the shot
        # holds two or more people, because with one there is nobody to
        # choose between, and mouth motion remains the fallback for a shot it
        # cannot answer — no audio, no model.
        chosen = _talknet_focus(clip_path, cands, video_fps)
        if chosen is None:
            chosen = pick_focus(
                cands,
                talk_rate=lambda kv: kv[1]["speak"] / max(len(kv[1]["face_xs"]), 1),
                prominence=lambda kv: float(np.median(kv[1]["areas"])),
                talk_floor=_TALK_FLOOR,
                talk_margin=_TALK_MARGIN,
            )
        s = chosen[1]
        # Center on the FACE positions only — a robust median, so a stray
        # body-center sample or a moment of mis-detection can't drag the crop.
        shots.append({
            "t0_idx": shot_start_idx, "t1_idx": end_idx,
            "x": float(np.median(s["face_xs"])),
            "cy": float(np.median(s["face_ys"])) if s["face_ys"] else None,
        })

    while True:
        ok = cap.grab()
        if not ok:
            break
        # Only sample frames are decoded and analysed. Cut detection compares
        # consecutive SAMPLES (coarse); the exact cut frame is recovered after
        # the pass by refining the short span around each coarse cut. This
        # keeps the frame-exact snap while decoding ~1/frame_step of the video
        # instead of every frame — the podcast pass is back to sampling speed.
        if frame_idx % frame_step != 0:
            frame_idx += 1
            continue
        ok, frame = cap.retrieve()
        if not ok:
            break
        t = frame_idx / video_fps
        h, w = frame.shape[:2]

        # ---- coarse cut detection between consecutive samples ------------
        small = small_gray(frame)
        if is_cut(prev_small, small):
            # A camera cut fell between the previous sample and this one; mark
            # the span for exact-frame refinement, and start a new shot here.
            cut_regions.append((last_sample_idx, frame_idx))
            close_shot(frame_idx)
            shot_start_idx = None
            shot_tracks = {}
            shot_stats = {}
        prev_small = small
        if shot_start_idx is None:
            shot_start_idx = frame_idx

        # ---- per-shot people tracking (identities never cross a cut) ------
        for tid in _assign(shot_tracks, _detect(model, frame, 0.4), t):
            tr = shot_tracks[tid]
            x1, y1, x2, y2, conf = tr.box[:5]
            # face_xs/face_ys hold ONLY real face/head sightings — the crop
            # centers on these. areas track prominence regardless.
            st = shot_stats.setdefault(
                tid, {"face_xs": [], "face_ys": [], "areas": [], "speak": 0.0,
                      "boxes": []}
            )
            head = tr.box[5] if len(tr.box) > 5 else None
            # Haar only when pose found nothing. This used to run every sample
            # for every person and then have its answer thrown away whenever
            # pose had the head — 78% of detections, and 81% of the time this
            # function spent.
            face = head_box(head) if head is not None else _face_box(frame, tr.box)
            # Motion needs a face-SHAPED region; the box above is square. See
            # mouth_region() for why feeding the wrong one moves the framing.
            mouth = mouth_region(head) if head is not None else face
            if face is not None:
                fx1, fy1, fx2, fy2 = face
                st["face_xs"].append(((fx1 + fx2) / 2) / w)
                st["face_ys"].append(((fy1 + fy2) / 2) / h)
                st["boxes"].append((t, face))   # for TalkNet, see close_shot
            st["areas"].append((x2 - x1) * (y2 - y1) / (w * h))
            if mouth is not None:
                _update_speaking(tr, frame, mouth)
                st["speak"] += tr.speak
            else:
                tr.speak *= 0.9
        last_sample_idx = frame_idx
        frame_idx += 1
    cap.release()
    close_shot(frame_idx)  # frame_idx is now the total frame count = clip end

    if not shots or w is None:
        return {"mode": "track", "path": [(0.0, 0.5)]}

    # Pin every coarse cut to its exact frame (decodes only the few frames
    # around each cut, not the whole clip).
    exact = refine_cuts(clip_path, cut_regions)

    def boundary_time(idx: int, *, ending: bool) -> float:
        """Frame index -> time. At a cut, the shot ENDING there stops one frame
        before the exact cut frame and the shot STARTING there begins on it, so
        the snap lands in that one-frame gap — instant on the new scene's first
        frame, never a ramp."""
        fc = exact.get(idx)
        if fc is None:
            return idx / video_fps        # clip start/end, not a cut
        return (fc - 1) / video_fps if ending else fc / video_fps

    # ---- the path: constant inside each shot, snapping at each cut --------
    # Two points per shot make the renderer's interpolation flat within a shot.
    # A shot with nobody detected holds the previous framing.
    path: list[tuple[float, float]] = []
    x_prev = 0.5
    last = len(shots) - 1
    for i, s in enumerate(shots):
        x = s["x"] if s["x"] is not None else x_prev
        t0 = s["t0_idx"] / video_fps if i == 0 else boundary_time(s["t0_idx"], ending=False)
        t1 = s["t1_idx"] / video_fps if i == last else boundary_time(s["t1_idx"], ending=True)
        path.append((t0, float(x)))
        path.append((t1, float(x)))
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
