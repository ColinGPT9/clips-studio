"""YOLOv8 + OpenCV subject tracking (v2).

Input:  a clip video file.
Output: a tracking result dict the cropper renders from:

  {"mode": "track", "path": [(t, center_x), ...]}
      Follow-the-subject mode: a smoothed horizontal crop path
      (center_x normalized 0..1).

  {"mode": "split", "webcam_box": (x, y, w, h)}
      Gameplay + facecam layout detected (all values normalized 0..1):
      render the webcam region stacked on top of a centered gameplay crop.
      Both regions are static, so this mode cannot jitter at all.

v2 upgrades over v1:
  - Identity tracking: detections are chained into tracks by IoU, so the
    system follows *people*, not per-frame boxes.
  - Target hysteresis: the camera switches subjects only when a challenger
    clearly dominates for >= 1.5s — no ping-ponging mid-conversation.
  - Two-person framing: when exactly two subjects persist close together,
    the crop frames their midpoint.
  - Pan-speed clamp: the window can never move faster than max_pan_speed
    (fraction of frame width per second) — kills whip-pans on detector noise.
  - Facecam layout detection for gameplay streams.

Fully decoupled from clip selection: this module knows nothing about
transcripts, scores, or uploads.
"""

from dataclasses import dataclass, field
from pathlib import Path

import cv2
import numpy as np

import threading

_model = None  # loaded once per process; YOLO init is expensive
# The single YOLO instance is shared across parallel render threads, and
# ultralytics inference is NOT thread-safe on one model. This serializes the
# GPU inference (the GPU runs one at a time anyway) while FFmpeg encodes —
# which release the GIL in subprocesses — still overlap.
_infer_lock = threading.Lock()
# OpenCV CascadeClassifier.detectMultiScale mutates internal scale state and
# crashes when one instance is used from two threads at once. Give each
# render thread its own cascades (cheap to construct) — full parallelism, safe.
_thread_local = threading.local()


def _get_model(model_name: str):
    global _model
    with _infer_lock:
        if _model is None:
            import torch
            from ultralytics import YOLO  # lazy: heavy import, pulls in torch

            _model = YOLO(model_name)
            if torch.cuda.is_available():
                _model.to("cuda")  # explicit: detection runs on the GPU
    return _model


def _get_cascades():
    # Per-thread instances: detectMultiScale is not thread-safe on a shared one.
    cascades = getattr(_thread_local, "cascades", None)
    if cascades is None:
        frontal = cv2.CascadeClassifier(
            cv2.data.haarcascades + "haarcascade_frontalface_default.xml"
        )
        profile = cv2.CascadeClassifier(
            cv2.data.haarcascades + "haarcascade_profileface.xml"
        )
        cascades = (frontal, profile)
        _thread_local.cascades = cascades
    return cascades


def _face_box(frame, box) -> tuple[int, int, int, int] | None:
    """Find the face inside a person box. In close-ups the person box centers
    on the torso, which can sit far from the face — the face box drives both
    framing and the talking detector.

    Detection order: frontal face -> left profile -> right profile (the
    profile cascade only knows one side, so the mirrored image covers the
    other). Returns the face box in absolute pixels, or None when no face is
    visible at all (e.g. subject facing away) — the caller then falls back
    to the person-box center, which is the best anyone can do without a face.
    """
    x1, y1, x2, y2 = (int(v) for v in box[:4])
    rx, ry = max(0, x1), max(0, y1)
    head_h = max((y2 - y1) // 2, 40)
    roi = frame[ry : y1 + head_h, rx:x2]
    if roi.size == 0:
        return None
    gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
    frontal, profile = _get_cascades()

    faces = frontal.detectMultiScale(gray, 1.15, 4, minSize=(36, 36))
    if len(faces) == 0:
        faces = profile.detectMultiScale(gray, 1.15, 4, minSize=(36, 36))
    if len(faces) == 0:
        flipped = profile.detectMultiScale(cv2.flip(gray, 1), 1.15, 4, minSize=(36, 36))
        if len(flipped) > 0:
            fx, fy, fw, fh = max(flipped, key=lambda f: f[2] * f[3])
            fx = gray.shape[1] - (fx + fw)  # mirror x back to the original
            faces = [(fx, fy, fw, fh)]
    if len(faces) == 0:
        return None
    fx, fy, fw, fh = max(faces, key=lambda f: f[2] * f[3])  # largest face
    return (rx + fx, ry + fy, rx + fx + fw, ry + fy + fh)


def _update_speaking(tr: "_Track", frame, face: tuple[int, int, int, int]) -> None:
    """Talking proxy: motion energy in the mouth region (lower part of the
    face box) between consecutive samples. A talking mouth changes shape
    constantly; a listening one doesn't. Comparing tracks RELATIVELY makes
    this robust to global camera motion, which inflates everyone equally."""
    fx1, fy1, fx2, fy2 = face
    my1 = fy1 + int((fy2 - fy1) * 0.55)
    mouth = frame[my1:fy2, fx1:fx2]
    if mouth.size == 0:
        return
    mouth = cv2.cvtColor(mouth, cv2.COLOR_BGR2GRAY)
    mouth = cv2.resize(mouth, (48, 24)).astype(np.float32) / 255.0
    if tr.prev_mouth is not None:
        motion = float(np.abs(mouth - tr.prev_mouth).mean())
        tr.speak = 0.65 * tr.speak + 0.35 * motion
    tr.prev_mouth = mouth


@dataclass
class _Track:
    box: tuple                     # last (x1, y1, x2, y2, conf, head) in pixels
    last_t: float
    dominance: float = 0.0         # EMA of confidence x area
    speak: float = 0.0             # EMA of mouth-region motion (talking proxy)
    prev_mouth: object = None      # last mouth crop (np array) for motion diff
    face_rate: float = 0.0         # EMA of "was a face detected this sample?"
    face_offset: float = 0.0       # EMA of (face cx - body cx), normalized
    face_w: float = 0.0            # EMA of face box width, normalized
    head_rate: float = 0.0         # EMA of "pose head keypoints seen this sample?"
    head_offset: float = 0.0       # EMA of (head cx - body cx), normalized
    head_cys: list = field(default_factory=list)  # normalized head center-y history
    n_seen: int = 0
    centers: list = field(default_factory=list)   # normalized cx history
    areas: list = field(default_factory=list)     # area fraction history
    norm_boxes: list = field(default_factory=list)  # normalized (x1, y1, x2, y2)


def compute_tracking(
    clip_path: Path,
    model_name: str = "yolov8n-pose.pt",
    sample_fps: float = 8.0,
    smoothing: float = 0.45,
    dead_zone: float = 0.03,
    max_pan_speed: float = 0.30,   # max window movement, frame-widths/second
    min_confidence: float = 0.4,
    switch_margin: float = 1.5,    # challenger must dominate by this factor...
    switch_seconds: float = 1.5,   # ...for this long before the camera switches
    fit_blur_fraction: float = 0.5,   # letterbox only when MOST of the clip
                                      # genuinely can't fit a 9:16 crop
) -> dict:
    cap = cv2.VideoCapture(str(clip_path))
    if not cap.isOpened():
        raise RuntimeError(f"OpenCV could not open {clip_path}")

    video_fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    frame_step = max(1, round(video_fps / sample_fps))
    dt = frame_step / video_fps
    model = _get_model(model_name)

    tracks: dict[int, _Track] = {}
    active_id: int | None = None
    challenger_id: int | None = None
    challenger_since = 0.0

    path: list[tuple[float, float]] = []
    smoothed_x: float | None = None
    n_samples = 0
    wide_boxes: list[tuple[float, float, float, float]] = []  # subject bbox when a
    #                                             9:16 crop can't hold it (normalized)
    frame_idx = 0

    while True:
        ok = cap.grab()
        if not ok:
            break
        if frame_idx % frame_step != 0:
            frame_idx += 1
            continue
        ok, frame = cap.retrieve()
        if not ok:
            break

        t = frame_idx / video_fps
        h, w = frame.shape[:2]
        n_samples += 1

        visible = _assign(tracks, _detect(model, frame, min_confidence), t)
        for tid in visible:
            tr = tracks[tid]
            x1, y1, x2, y2, conf = tr.box[:5]
            area_frac = (x2 - x1) * (y2 - y1) / (w * h)
            tr.dominance = 0.7 * tr.dominance + 0.3 * (conf * area_frac)
            tr.n_seen += 1
            # Anti-jitter center: the body box is always stable; the HEAD
            # refines it as a SMOOTHED OFFSET from the body center. The head
            # position comes from pose keypoints (nose/eyes/ears) — these
            # work even when the face is wet, tilted, turned, or too small
            # for the face detector (pool/beach/action shots), so the crop
            # keeps priority on the head over the body. The Haar face box is
            # the fallback signal and still drives the talking detector.
            body_cx = ((x1 + x2) / 2) / w
            head = tr.box[5] if len(tr.box) > 5 else None
            if head is not None:
                head_cx, head_cy, head_w = head
                tr.head_rate = 0.8 * tr.head_rate + 0.2
                tr.head_offset = 0.7 * tr.head_offset + 0.3 * (head_cx / w - body_cx)
                tr.face_w = 0.7 * tr.face_w + 0.3 * (head_w / w)
                tr.head_cys.append(head_cy / h)
            else:
                tr.head_rate = 0.8 * tr.head_rate
            face = _face_box(frame, tr.box)
            if face is not None:
                fx1, fy1, fx2, fy2 = face
                face_cx = ((fx1 + fx2) / 2) / w
                tr.face_rate = 0.8 * tr.face_rate + 0.2
                tr.face_offset = 0.7 * tr.face_offset + 0.3 * (face_cx - body_cx)
                if head is None:
                    tr.face_w = 0.7 * tr.face_w + 0.3 * ((fx2 - fx1) / w)
                    tr.head_cys.append(((fy1 + fy2) / 2) / h)
                _update_speaking(tr, frame, face)
            else:
                tr.face_rate = 0.8 * tr.face_rate  # detection getting unreliable
                tr.speak *= 0.9  # no visible face: talking evidence fades
            # Framing priority: pose head keypoints > face box > body center.
            if tr.head_rate > 0.3:
                refine = tr.head_offset
            elif tr.face_rate > 0.45:
                refine = tr.face_offset
            else:
                refine = 0.0
            tr.centers.append(body_cx + refine)
            tr.areas.append(area_frac)
            tr.norm_boxes.append((x1 / w, y1 / h, x2 / w, y2 / h))

        if visible:
            # ---- choose the target, with hysteresis ----------------------
            # Who to follow = size/confidence dominance x WHO IS TALKING.
            # Mouth-region motion is the talking proxy, so in group shots the
            # camera prefers the speaker, not just the biggest person.
            max_speak = max((tracks[tid].speak for tid in visible), default=0.0)

            def _score(tid: int) -> float:
                tr = tracks[tid]
                if max_speak < 0.004:  # nobody visibly talking: size decides
                    return tr.dominance
                return tr.dominance * (0.35 + 0.65 * (tr.speak / max_speak))

            top = max(visible, key=_score)
            active_gone = (
                active_id is None
                or active_id not in tracks
                or (active_id not in visible and t - tracks[active_id].last_t > 1.0)
            )
            if active_gone:
                active_id, challenger_id = top, None
            elif top != active_id and _score(top) > switch_margin * _score(active_id):
                if challenger_id != top:
                    challenger_id, challenger_since = top, t
                elif t - challenger_since >= switch_seconds:
                    active_id, challenger_id = top, None  # sustained takeover
            else:
                challenger_id = None

            crop_frac = (h * 9 / 16) / w  # crop width as fraction of frame width
            raw_x = _target_x(tracks, visible, active_id, crop_frac)

            # ---- when is a plain 9:16 crop NOT enough? -------------------
            # A single upright person is ALWAYS fine as a normal crop — we just
            # center on their face/torso, even if their shoulders are wider
            # than the narrow 9:16 window (that's normal for a talking-head
            # Short). The letterbox is only for cases a vertical crop genuinely
            # can't hold:
            #   * TWO+ real people spread wider than the crop, or
            #   * a SINGLE person lying down (box wider than tall).
            # Minor/background/low-confidence detections are ignored so a
            # motorcycle or a bystander never forces it.
            if active_id in tracks:
                active_dom = tracks[active_id].dominance
                subjects = [
                    tracks[tid].box
                    for tid in visible
                    # A co-subject must be a NEAR-EQUAL of the main subject —
                    # prominent, persistent through the clip, and confidently
                    # a person. Swimmers/bystanders drifting through a pool
                    # shot must never drag a centered creator into letterbox.
                    if (tracks[tid].box[2] - tracks[tid].box[0])
                    * (tracks[tid].box[3] - tracks[tid].box[1])
                    / (w * h)
                    > 0.06
                    and (
                        tid == active_id
                        or (
                            tracks[tid].dominance >= 0.75 * active_dom
                            and tracks[tid].n_seen >= max(8, 0.3 * n_samples)
                        )
                    )
                    and tracks[tid].box[4] >= 0.6  # confidently a person
                ]
                is_wide = False
                if len(subjects) >= 2:
                    x_left = min(b[0] for b in subjects) / w
                    x_right = max(b[2] for b in subjects) / w
                    is_wide = (x_right - x_left) > crop_frac * 1.3  # can't fit both
                elif len(subjects) == 1:
                    b = subjects[0]
                    bw, bh = b[2] - b[0], b[3] - b[1]
                    # Letterbox a SINGLE person only when they are clearly lying
                    # FLAT (box 2x+ wider than tall) — a genuinely horizontal
                    # head-to-toe pose a vertical crop would cut. Seated,
                    # reclined, arms-out, or close-up people (box near square or
                    # taller) are always a normal crop centered on the face.
                    is_wide = bw > bh * 2.0 and bw / w > crop_frac * 1.4
                if is_wide:
                    wide_boxes.append((
                        min(b[0] for b in subjects) / w,
                        min(b[1] for b in subjects) / h,
                        max(b[2] for b in subjects) / w,
                        max(b[3] for b in subjects) / h,
                    ))

            # ---- smoothing chain: dead-zone -> EMA -> pan-speed clamp ----
            if smoothed_x is None:
                smoothed_x = raw_x
            elif abs(raw_x - smoothed_x) > dead_zone:
                step = smoothing * (raw_x - smoothed_x)
                max_step = max_pan_speed * dt
                smoothed_x += float(np.clip(step, -max_step, max_step))

            # ---- head-containment clamp ----------------------------------
            # Guarantee the active subject's head stays inside the crop window
            # even when it moves faster than the pan clamp — prevents the
            # side-of-face cut-off. Overrides smoothing only when necessary.
            if active_id in tracks and (
                tracks[active_id].head_rate > 0.3 or tracks[active_id].face_rate > 0.45
            ):
                atr = tracks[active_id]
                x1, _, x2, _ = atr.box[:4]
                off = atr.head_offset if atr.head_rate > 0.3 else atr.face_offset
                face_cx = (x1 + x2) / 2 / w + off
                half = min(atr.face_w / 2 + 0.02, crop_frac / 2)  # keep face + margin in window
                lo, hi = face_cx - half, face_cx + half
                if lo < smoothed_x - crop_frac / 2:
                    smoothed_x = lo + crop_frac / 2
                elif hi > smoothed_x + crop_frac / 2:
                    smoothed_x = hi - crop_frac / 2

            path.append((t, float(smoothed_x)))

        frame_idx += 1

    cap.release()

    # Blurred-letterbox only when the subject genuinely won't fit a 9:16 crop
    # for a meaningful part of the clip. Crop TIGHTLY to the subject's bounding
    # box (both axes, padded) so they fill the frame and there's minimal dead
    # space — not the full frame height with the person small in the middle.
    if n_samples > 0 and len(wide_boxes) / n_samples > fit_blur_fraction:
        def _pct(vals: list[float], p: float) -> float:
            vals = sorted(vals)
            return vals[min(len(vals) - 1, max(0, int(p * len(vals))))]

        pad_x, pad_y = 0.04, 0.06
        x0 = max(0.0, _pct([b[0] for b in wide_boxes], 0.10) - pad_x)
        y0 = max(0.0, _pct([b[1] for b in wide_boxes], 0.10) - pad_y)
        x1 = min(1.0, _pct([b[2] for b in wide_boxes], 0.90) + pad_x)
        y1 = min(1.0, _pct([b[3] for b in wide_boxes], 0.90) + pad_y)
        return {
            "mode": "fit_blur",
            "region": (round(x0, 4), round(y0, 4), round(x1, 4), round(y1, 4)),
        }

    layout = _detect_facecam_layout(tracks, active_id, n_samples)
    if layout is not None:
        return layout
    if not path:
        return {"mode": "track", "path": [(0.0, 0.5)]}  # nothing detected: center
    # Where the subject's face sits vertically (median, normalized 0..1).
    # The renderer uses this to keep faces out of the zone TikTok/Instagram
    # cover with their own UI at the top of the screen.
    face_y = None
    if active_id in tracks and tracks[active_id].head_cys:
        face_y = round(float(np.median(tracks[active_id].head_cys)), 4)
    return {"mode": "track", "path": path, "face_y": face_y}


# ---- detection + identity assignment ----------------------------------------


def _detect(model, frame, min_confidence) -> list[tuple]:
    """Person detections as (x1, y1, x2, y2, conf, head). With a pose model,
    head is (head_cx_px, head_cy_px, head_w_px) from the nose/eye/ear keypoints — the most
    reliable "where is the head" signal there is (needs no visible face). With
    a plain detection model, head is None and the Haar face box fills in."""
    with _infer_lock:
        results = model.predict(frame, classes=[0], conf=min_confidence, verbose=False)
    out = []
    for r in results:
        kp = getattr(r, "keypoints", None)
        kxy = kp.xy.tolist() if kp is not None and kp.xy is not None else None
        kconf = kp.conf.tolist() if kp is not None and kp.conf is not None else None
        for i, b in enumerate(r.boxes):
            x1, y1, x2, y2 = b.xyxy[0].tolist()
            head = None
            if kxy is not None and kconf is not None and i < len(kxy):
                # COCO keypoints 0-4: nose, eyes, ears.
                pts = [kxy[i][j] for j in range(5) if kconf[i][j] > 0.5]
                if pts:
                    head_cx = sum(p[0] for p in pts) / len(pts)
                    head_cy = sum(p[1] for p in pts) / len(pts)
                    xs = [p[0] for p in pts]
                    spread = max(xs) - min(xs)
                    head_w = max(spread * 1.6, (y2 - y1) * 0.12)
                    head = (head_cx, head_cy, head_w)
            out.append((x1, y1, x2, y2, float(b.conf[0]), head))
    return out


def _assign(tracks: dict, detections: list, t: float) -> list[int]:
    """Greedy IoU matching of detections to live tracks; unmatched detections
    start new tracks. Returns the track ids visible in this frame."""
    pairs = []
    for tid, tr in tracks.items():
        if t - tr.last_t > 2.0:
            continue  # stale track — don't revive identities after long gaps
        for i, d in enumerate(detections):
            iou = _iou(tr.box[:4], d[:4])
            if iou >= 0.25:
                pairs.append((iou, tid, i))

    visible, used_t, used_d = [], set(), set()
    for _, tid, i in sorted(pairs, key=lambda p: p[0], reverse=True):
        if tid in used_t or i in used_d:
            continue
        tracks[tid].box = detections[i]
        tracks[tid].last_t = t
        visible.append(tid)
        used_t.add(tid)
        used_d.add(i)

    for i, d in enumerate(detections):
        if i not in used_d:
            tid = max(tracks, default=-1) + 1
            tracks[tid] = _Track(box=d, last_t=t)
            visible.append(tid)
    return visible


def _iou(a, b) -> float:
    ix1, iy1 = max(a[0], b[0]), max(a[1], b[1])
    ix2, iy2 = min(a[2], b[2]), min(a[3], b[3])
    inter = max(0.0, ix2 - ix1) * max(0.0, iy2 - iy1)
    if inter == 0:
        return 0.0
    area_a = (a[2] - a[0]) * (a[3] - a[1])
    area_b = (b[2] - b[0]) * (b[3] - b[1])
    return inter / (area_a + area_b - inter)


# ---- framing decisions --------------------------------------------------------


def _target_x(tracks: dict, visible: list[int], active_id: int, crop_frac: float) -> float:
    """Center on the active subject — or on the midpoint when exactly two
    persistent subjects fit inside the crop window together."""
    strong = [
        tid for tid in visible
        if tracks[tid].n_seen >= 8
        and tracks[tid].dominance > 0.2 * max(tracks[active_id].dominance, 1e-9)
    ]
    if len(strong) == 2:
        xa, xb = tracks[strong[0]].centers[-1], tracks[strong[1]].centers[-1]
        if abs(xa - xb) < crop_frac * 0.7:  # both fit in the 9:16 window
            return (xa + xb) / 2
    return tracks[active_id].centers[-1]


def _detect_facecam_layout(tracks: dict, active_id: int | None, n_samples: int) -> dict | None:
    """Gameplay + facecam streams: the streamer's face sits inside a small,
    static webcam overlay. If the dominant subject barely moves, is small,
    and is present in >=70% of samples -> stacked split layout."""
    if active_id is None or active_id not in tracks or n_samples == 0:
        return None
    tr = tracks[active_id]
    if tr.n_seen < 10 or tr.n_seen < 0.7 * n_samples:
        return None

    centers = np.array(tr.centers)
    if centers.std() > 0.025 or float(np.mean(tr.areas)) > 0.12:
        return None

    # Median normalized face box, padded 35% to capture the webcam frame.
    boxes = np.array(tr.norm_boxes)
    x1, y1, x2, y2 = np.median(boxes, axis=0)
    pw, ph = (x2 - x1) * 0.35, (y2 - y1) * 0.35
    x = max(0.0, float(x1 - pw))
    y = max(0.0, float(y1 - ph))
    bw = min(1.0 - x, float(x2 - x1 + 2 * pw))
    bh = min(1.0 - y, float(y2 - y1 + 2 * ph))
    return {"mode": "split", "webcam_box": (x, y, bw, bh)}
