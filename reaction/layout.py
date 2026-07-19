"""Layout analysis for reaction videos: find the two regions that matter.

    cam_box     — the creator's webcam pane
    content_box — what they are reacting to

Both are returned normalized (x, y, w, h in 0..1) for the composer to lay
out. Detection is deliberately conservative: when it can't identify a
credible two-region layout it returns None, and the caller renders the clip
with the standard full-frame letterbox instead — which still shows creator
AND content, just uncomposed. Guessing wrong is worse than not guessing.

How the regions are found
-------------------------
cam: person detections that are SMALL and STATIC across the clip. A webcam
pane doesn't wander; a talking-head subject fills the frame and moves, so
that content can't produce a cam box at all (and therefore never routes
here by accident).

content: the frame minus dead margins. An "interest" map combines temporal
activity (pixels that change = playing video) with structure (edge density
= text, UI, imagery). Margins that are flat on BOTH — pillarbox bars, plain
desktop wallpaper, empty gutters — get trimmed. Anything with text or
motion is kept, because cutting real content is the failure we're avoiding.

Reuses video.tracker's model loader and detector read-only; nothing there
is modified.
"""

from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

SAMPLES = 24            # frames analyzed across the clip
CAM_MIN_AREA = 0.004    # smaller than this is a bystander/artifact, not a cam
CAM_MAX_AREA = 0.30     # a webcam pane never fills a third of the frame
CAM_MOVE_TOL = 0.05     # normalized center drift tolerated for a "static" pane
CAM_PRESENCE = 0.5      # must be present in at least half the samples
MIN_CONTENT_RATIO = 2.0  # content must be clearly bigger than the cam


@dataclass
class ReactionLayout:
    """Normalized (x, y, w, h) boxes plus a description of what was found."""

    cam_box: tuple[float, float, float, float]
    content_box: tuple[float, float, float, float]
    kind: str          # 'pip' | 'side_by_side'
    confidence: float  # 0..1 — the caller may require a minimum

    def describe(self) -> str:
        cx, cy, cw, ch = self.cam_box
        return (
            f"{self.kind}: cam {cw * 100:.0f}x{ch * 100:.0f}% at "
            f"({cx * 100:.0f}%, {cy * 100:.0f}%), confidence {self.confidence:.2f}"
        )


def analyze(
    clip_path: Path,
    model_name: str = "yolov8n-pose.pt",
    min_confidence: float = 0.4,
) -> ReactionLayout | None:
    """Detect a reaction layout, or None when this isn't one (or analysis
    fails). Never raises — an unusable clip just isn't a reaction clip."""
    try:
        return _analyze(clip_path, model_name, min_confidence)
    except Exception as e:  # noqa: BLE001 - isolation is the whole point
        print(f"      (reaction layout analysis failed: {e})")
        return None


def _analyze(clip_path: Path, model_name: str, min_confidence: float):
    from video.tracker import _detect, _get_model  # read-only reuse

    cap = cv2.VideoCapture(str(clip_path))
    if not cap.isOpened():
        return None
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT)) or 0
    src_w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    src_h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    if src_w <= 0 or src_h <= 0:
        cap.release()
        return None

    model = _get_model(model_name)
    step = max(1, total // SAMPLES) if total else 30
    boxes: list[tuple[float, float, float, float]] = []  # normalized x1,y1,x2,y2
    smalls: list[np.ndarray] = []                        # downscaled grayscale
    colours: list[np.ndarray] = []                       # downscaled BGR
    gx_acc = gy_acc = None                               # accumulated edges
    grad_n = 0
    idx = 0
    while len(smalls) < SAMPLES:
        ok = cap.grab()
        if not ok:
            break
        if idx % step == 0:
            ok, frame = cap.retrieve()
            if not ok:
                break
            small = cv2.resize(frame, (192, 108))
            colours.append(small)
            smalls.append(cv2.cvtColor(small, cv2.COLOR_BGR2GRAY).astype(np.float32))
            # Border map at a resolution that can actually localize an edge.
            g = cv2.cvtColor(cv2.resize(frame, (480, 270)), cv2.COLOR_BGR2GRAY).astype(np.float32)
            sx = np.abs(cv2.Sobel(g, cv2.CV_32F, 1, 0, ksize=3))
            sy = np.abs(cv2.Sobel(g, cv2.CV_32F, 0, 1, ksize=3))
            gx_acc = sx if gx_acc is None else gx_acc + sx
            gy_acc = sy if gy_acc is None else gy_acc + sy
            grad_n += 1
            for x1, y1, x2, y2, _conf, _head in _detect(model, frame, min_confidence):
                boxes.append((x1 / src_w, y1 / src_h, x2 / src_w, y2 / src_h))
        idx += 1
    cap.release()
    if len(smalls) < 3:
        return None

    n_frames = len(smalls)
    cam = _find_cam_box(boxes, n_frames)
    if cam is None:
        return None

    if grad_n:
        cam = _snap_cam_box((gx_acc / grad_n, gy_acc / grad_n), cam)

    stack = np.stack(smalls)
    activity, structure = _maps(stack)
    content = _find_content_box(activity, structure)
    # The cam pane gets its own band, so the content pane must NOT contain it
    # too — otherwise the creator appears twice in the same clip.
    content = _content_excluding_cam(content, cam, activity, structure)
    cam_area = cam[2] * cam[3]
    content_area = content[2] * content[3]
    if cam_area <= 0 or content_area / cam_area < MIN_CONTENT_RATIO:
        return None  # no clear "big content + small cam" relationship

    cx, _cy, cw, ch = cam
    side_by_side = (cx > 0.55 or cx + cw < 0.45) and ch > 0.5
    kind = "side_by_side" if side_by_side else "pip"

    # ---- is this a reaction LAYOUT, or just a small person in frame? ----
    # Three discriminators separate them, because a wide talking-head shot
    # (someone sitting still at a desk) also yields a small static person:
    #
    #  1. Colour environment — the strongest tell. A webcam (skin, room
    #     lighting) and the content being reacted to (screen colours, its
    #     own white balance) are different colour worlds; a person and the
    #     room behind them are the same one.
    #  2. Anchoring — a webcam pane is mounted against a corner/edge. A
    #     person in a room sits in the middle of it, body running off the
    #     bottom edge only.
    #  3. Second-region evidence — outside a reaction cam there is SCREEN
    #     content: text, UI, video, all high in detail and/or motion. A
    #     room background is comparatively flat and still.
    conf = confidence(
        cam,
        _second_region_evidence(activity, structure, cam),
        _colour_dissimilarity(colours, cam),
    )
    return ReactionLayout(cam_box=cam, content_box=content, kind=kind, confidence=conf)


def confidence(
    cam: tuple[float, float, float, float], evidence: float, colour: float
) -> float:
    """0..1 that this really is a reaction layout. The caller auto-routes
    only above 0.6; an explicit user choice ignores this entirely.

    colour carries the most weight because it is the most reliable tell: a
    webcam pane (skin tones, room lighting) and the content being reacted
    to (screen colours, its own white balance) come from different
    environments, while a person in a wide talking-head shot shares one
    environment with everything around them."""
    cx, cy, cw, ch = cam
    edges = sum((cx < 0.08, cy < 0.08, cx + cw > 0.92, cy + ch > 0.92))
    size_score = 1.0 - min(1.0, (cw * ch) / CAM_MAX_AREA)
    corner = 1.0 if edges >= 2 else (0.5 if edges == 1 else 0.0)
    # Floor, not a ramp: hue histograms are noisy on low-saturation frames,
    # so modest dissimilarity (~0.3) is what ONE environment already looks
    # like. Only a clear separation counts as two colour worlds.
    colour_score = max(0.0, min(1.0, (colour - 0.35) / 0.35))
    conf = (
        0.35 * corner
        + 0.35 * colour_score
        + 0.20 * min(1.0, evidence / 0.9)
        + 0.10 * size_score
    )
    if edges < 2:
        # Not corner/edge-anchored -> a person in a room, not a mounted
        # webcam pane. Capped below the auto threshold, never auto-routed.
        conf = min(conf, 0.5)
    return round(conf, 3)


def _snap_cam_box(
    grads: tuple[np.ndarray, np.ndarray], cam: tuple[float, float, float, float]
) -> tuple[float, float, float, float]:
    """Tighten the padded person box onto the webcam pane's real borders.

    Works on edge maps ACCUMULATED across the clip: the pane's border is the
    only straight line that never moves, so averaging makes it stand out
    while the content behind it washes out. Each side snaps to its dominant
    persistent line, anchors to the frame when the pane already touches it,
    or keeps the padded guess when no line is confident. Without this the
    band shows a strip of whatever sits beside the camera."""
    gx, gy = grads
    h, w = gx.shape[:2]
    cx, cy, cw, ch = cam
    px0, py0, px1, py1 = cx * w, cy * h, (cx + cw) * w, (cy + ch) * h
    # The person must stay inside whatever we snap to: keep a safety margin
    # (headroom especially — a line inside the pane must never cut the head).
    keep_x0, keep_y0 = px0 + 0.15 * (px1 - px0), py0 + 0.20 * (py1 - py0)
    keep_x1, keep_y1 = px1 - 0.15 * (px1 - px0), py1 - 0.10 * (py1 - py0)
    reach_x, reach_y = int(0.20 * w), int(0.20 * h)

    def edge(profile: np.ndarray, lo: float, hi: float) -> int | None:
        lo_i, hi_i = max(0, int(lo)), min(len(profile), int(hi))
        if hi_i - lo_i < 3:
            return None
        seg = profile[lo_i:hi_i]
        peak = int(np.argmax(seg))
        baseline = float(np.median(profile)) + 1e-6
        return lo_i + peak if seg[peak] > 3.0 * baseline else None

    col = gx[max(0, int(keep_y0)):max(1, int(keep_y1)), :].sum(axis=0)
    row = gy[:, max(0, int(keep_x0)):max(1, int(keep_x1))].sum(axis=1)

    left = 0 if cx < 0.02 else (edge(col, keep_x0 - reach_x, keep_x0) or int(px0))
    right = w if cx + cw > 0.98 else (edge(col, keep_x1, keep_x1 + reach_x) or int(px1))
    top = 0 if cy < 0.02 else (edge(row, keep_y0 - reach_y, keep_y0) or int(py0))
    bottom = h if cy + ch > 0.98 else (edge(row, keep_y1, keep_y1 + reach_y) or int(py1))

    left, top = min(left, int(keep_x0)), min(top, int(keep_y0))
    right, bottom = max(right, int(keep_x1)), max(bottom, int(keep_y1))
    if (right - left) * (bottom - top) > CAM_MAX_AREA * 1.5 * w * h:
        return cam  # snapped onto something far too big — distrust it
    return (round(left / w, 4), round(top / h, 4),
            round((right - left) / w, 4), round((bottom - top) / h, 4))


def _content_excluding_cam(
    content: tuple[float, float, float, float],
    cam: tuple[float, float, float, float],
    activity: np.ndarray,
    structure: np.ndarray,
) -> tuple[float, float, float, float]:
    """Cut the cam pane out of the content box so the creator isn't shown
    TWICE (once in their band, again inside the content).

    The cam is normally corner/edge-mounted, so removing it leaves four
    candidate rectangles; the winner is the one preserving the most actual
    content — scored on the interest map, not on area, so a big empty
    region never beats a smaller busy one. If every option would throw away
    most of the content, the box is left alone (an occasional duplicate
    beats losing what the clip is about)."""
    x0, y0, w, h = content
    x1, y1 = x0 + w, y0 + h
    cx0, cy0, cw, chh = cam
    cx1, cy1 = cx0 + cw, cy0 + chh
    if cx1 <= x0 or cx0 >= x1 or cy1 <= y0 or cy0 >= y1:
        return content  # no overlap already

    def interest_of(rect: tuple[float, float, float, float]) -> float:
        rx, ry, rw, rh = rect
        if rw <= 0.02 or rh <= 0.02:
            return 0.0
        mh, mw = activity.shape
        a = activity[int(ry * mh):max(1, int((ry + rh) * mh)),
                     int(rx * mw):max(1, int((rx + rw) * mw))]
        s = structure[int(ry * mh):max(1, int((ry + rh) * mh)),
                      int(rx * mw):max(1, int((rx + rw) * mw))]
        if a.size == 0:
            return 0.0
        # Total (not mean): preserving MORE content should win.
        return float(a.sum() / (activity.sum() + 1e-6) + s.sum() / (structure.sum() + 1e-6))

    candidates = [
        (x0, y0, max(0.0, cx0 - x0), h),                     # left of the cam
        (min(x1, cx1), y0, max(0.0, x1 - cx1), h),           # right of the cam
        (x0, y0, w, max(0.0, cy0 - y0)),                     # above the cam
        (x0, min(y1, cy1), w, max(0.0, y1 - cy1)),           # below the cam
    ]
    whole = interest_of(content)
    best = max(candidates, key=interest_of)
    if interest_of(best) < 0.45 * whole:
        return content  # every cut loses too much — keep it whole
    return (round(best[0], 4), round(best[1], 4), round(best[2], 4), round(best[3], 4))


def _colour_dissimilarity(
    frames_bgr: list[np.ndarray], cam: tuple[float, float, float, float]
) -> float:
    """How differently the cam pane and everything around it are COLOURED.

    0 = same colour world (one room — a talking-head wide shot), 1 = totally
    different (a webcam against screen content — a reaction layout).
    Bhattacharyya distance between hue/saturation histograms, averaged over
    the sampled frames.

    Known blind spot: a creator reacting to their OWN footage shares the
    environment, so this drops toward 0. The corner-anchoring and
    screen-detail tests still carry those clips, and the editor's per-clip
    Reaction option is the manual override."""
    if not frames_bgr:
        return 0.0
    h, w = frames_bgr[0].shape[:2]
    cx, cy, cw, ch = cam
    x0, x1 = int(cx * w), min(w, int((cx + cw) * w))
    y0, y1 = int(cy * h), min(h, int((cy + ch) * h))
    inside = np.zeros((h, w), np.uint8)
    inside[y0:y1, x0:x1] = 255
    outside = cv2.bitwise_not(inside)
    if int(inside.sum()) < 255 * 16 or int(outside.sum()) < 255 * 16:
        return 0.0

    dists = []
    for bgr in frames_bgr[:12]:
        hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)
        hi = cv2.calcHist([hsv], [0, 1], inside, [24, 8], [0, 180, 0, 256])
        ho = cv2.calcHist([hsv], [0, 1], outside, [24, 8], [0, 180, 0, 256])
        cv2.normalize(hi, hi)
        cv2.normalize(ho, ho)
        dists.append(float(cv2.compareHist(hi, ho, cv2.HISTCMP_BHATTACHARYYA)))
    return float(np.median(dists)) if dists else 0.0


def _second_region_evidence(
    activity: np.ndarray, structure: np.ndarray, cam: tuple[float, float, float, float]
) -> float:
    """How much the area OUTSIDE the cam looks like content rather than a
    room: detail and motion outside, relative to inside the cam pane."""
    h, w = activity.shape
    cx, cy, cw, ch = cam
    x0, x1 = int(cx * w), min(w, int((cx + cw) * w))
    y0, y1 = int(cy * h), min(h, int((cy + ch) * h))
    mask = np.ones((h, w), bool)
    mask[y0:y1, x0:x1] = False
    if mask.sum() < 16 or (~mask).sum() < 16:
        return 0.0
    eps = 1e-6
    str_ratio = float(structure[mask].mean()) / (float(structure[~mask].mean()) + eps)
    act_ratio = float(activity[mask].mean()) / (float(activity[~mask].mean()) + eps)
    return max(str_ratio, act_ratio)


def _find_cam_box(
    boxes: list[tuple[float, float, float, float]], n_frames: int
) -> tuple[float, float, float, float] | None:
    """The small, static person pane — a webcam. None when the person is big
    or roams (talking-head / IRL content, which must not route here)."""
    cands = [b for b in boxes if CAM_MIN_AREA <= (b[2] - b[0]) * (b[3] - b[1]) <= CAM_MAX_AREA]
    if len(cands) < max(3, CAM_PRESENCE * n_frames):
        return None

    # Cluster by center: the modal 10% grid cell, then everything near it.
    centers = [((b[0] + b[2]) / 2, (b[1] + b[3]) / 2) for b in cands]
    cells: dict[tuple[int, int], int] = {}
    for cx, cy in centers:
        key = (int(cx * 10), int(cy * 10))
        cells[key] = cells.get(key, 0) + 1
    best_cell = max(cells, key=lambda k: cells[k])
    anchor = ((best_cell[0] + 0.5) / 10, (best_cell[1] + 0.5) / 10)
    cluster = [
        b
        for b, (cx, cy) in zip(cands, centers)
        if abs(cx - anchor[0]) < 0.10 and abs(cy - anchor[1]) < 0.10
    ]
    if len(cluster) < max(3, CAM_PRESENCE * n_frames):
        return None

    arr = np.array(cluster)
    cxs = (arr[:, 0] + arr[:, 2]) / 2
    cys = (arr[:, 1] + arr[:, 3]) / 2
    if float(cxs.std()) > CAM_MOVE_TOL or float(cys.std()) > CAM_MOVE_TOL:
        return None  # roams -> a moving subject, not a mounted webcam pane

    x1, y1, x2, y2 = np.median(arr, axis=0)
    # Pad to the pane around the person: generous headroom (webcam framing
    # puts the head near the top), modest elsewhere.
    pw, ph = (x2 - x1) * 0.30, (y2 - y1) * 0.30
    x = max(0.0, float(x1 - pw))
    y = max(0.0, float(y1 - ph * 1.6))
    w = min(1.0 - x, float(x2 - x1) + 2 * pw)
    h = min(1.0 - y, float(y2 - y1) + ph * 2.6)
    return (round(x, 4), round(y, 4), round(w, 4), round(h, 4))


def _maps(frames: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """(temporal activity, structural detail) maps for the sampled frames."""
    activity = frames.std(axis=0)
    structure = np.mean(
        [np.abs(cv2.Sobel(f, cv2.CV_32F, 1, 0, 3)) + np.abs(cv2.Sobel(f, cv2.CV_32F, 0, 1, 3))
         for f in frames],
        axis=0,
    )
    return activity, structure


def _find_content_box(
    activity: np.ndarray, structure: np.ndarray
) -> tuple[float, float, float, float]:
    """The frame minus dead margins (flat, motionless borders).

    Interest = temporal activity + structure. Only margins that are dead on
    BOTH get trimmed, so a static-but-detailed region (a tweet, a paused
    video, a chat column) is always kept — losing real content is the
    failure this pipeline exists to prevent."""

    def norm(a: np.ndarray) -> np.ndarray:
        peak = float(a.max())
        return a / peak if peak > 1e-6 else np.zeros_like(a)

    interest = norm(activity) + norm(structure)
    h, w = interest.shape
    cols = interest.mean(axis=0)
    rows = interest.mean(axis=1)

    def trim(profile: np.ndarray, limit: float) -> tuple[int, int]:
        """Dead run at each end, capped so trimming can never run away."""
        peak = float(profile.max())
        if peak <= 1e-6:
            return 0, len(profile)
        dead = 0.12 * peak
        lo, hi = 0, len(profile)
        max_trim = int(limit * len(profile))
        while lo < max_trim and profile[lo] < dead:
            lo += 1
        while hi > len(profile) - max_trim and profile[hi - 1] < dead:
            hi -= 1
        return lo, hi

    x0, x1 = trim(cols, 0.35)
    y0, y1 = trim(rows, 0.35)
    if x1 - x0 < 0.3 * w or y1 - y0 < 0.3 * h:  # implausible trim -> full frame
        return (0.0, 0.0, 1.0, 1.0)
    return (round(x0 / w, 4), round(y0 / h, 4),
            round((x1 - x0) / w, 4), round((y1 - y0) / h, 4))
