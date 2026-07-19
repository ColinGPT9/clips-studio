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
    idx = 0
    while len(smalls) < SAMPLES:
        ok = cap.grab()
        if not ok:
            break
        if idx % step == 0:
            ok, frame = cap.retrieve()
            if not ok:
                break
            smalls.append(
                cv2.cvtColor(cv2.resize(frame, (192, 108)), cv2.COLOR_BGR2GRAY).astype(np.float32)
            )
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

    content = _find_content_box(np.stack(smalls))
    cam_area = cam[2] * cam[3]
    content_area = content[2] * content[3]
    if cam_area <= 0 or content_area / cam_area < MIN_CONTENT_RATIO:
        return None  # no clear "big content + small cam" relationship

    # Side-by-side when the cam occupies its own vertical band with content
    # beside it; otherwise the cam is an overlay sitting on the content.
    cx, cy, cw, ch = cam
    side_by_side = (cx > 0.55 or cx + cw < 0.45) and ch > 0.5
    kind = "side_by_side" if side_by_side else "pip"

    # Confidence: how cam-like the pane is (small, static, well inside the
    # frame) — the composer's caller can demand more for AUTO routing than
    # for a user who explicitly asked for reaction mode.
    size_score = 1.0 - min(1.0, cam_area / CAM_MAX_AREA)
    conf = round(0.5 + 0.5 * size_score, 3)
    return ReactionLayout(cam_box=cam, content_box=content, kind=kind, confidence=conf)


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


def _find_content_box(frames: np.ndarray) -> tuple[float, float, float, float]:
    """The frame minus dead margins (flat, motionless borders).

    Interest = temporal activity + structure. Only margins that are dead on
    BOTH get trimmed, so a static-but-detailed region (a tweet, a paused
    video, a chat column) is always kept — losing real content is the
    failure this pipeline exists to prevent."""
    activity = frames.std(axis=0)
    structure = np.mean(
        [np.abs(cv2.Sobel(f, cv2.CV_32F, 1, 0, 3)) + np.abs(cv2.Sobel(f, cv2.CV_32F, 0, 1, 3))
         for f in frames],
        axis=0,
    )

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
