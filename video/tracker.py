"""YOLOv8 + OpenCV subject tracking.

Input:  a clip video file.
Output: a crop path — [(time_seconds, center_x_normalized)] keyframes telling
        the cropper where the 9:16 window should sit horizontally.

Fully decoupled from clip selection: this module knows nothing about
transcripts, scores, or uploads. Detection details:

- Frames are sampled at ~5 fps; subjects don't move meaningfully in 200 ms,
  and this keeps tracking fast even on CPU.
- YOLOv8n detects persons (COCO class 0).
- The "primary subject" per frame is chosen by confidence x box area x
  persistence (IoU with the previous frame's choice), which keeps the lock
  on the streamer when other people or characters enter the frame.
- The center-x is smoothed with an EMA plus a dead-zone so the crop window
  glides instead of twitching.
- Zero detections in the whole clip (gameplay, slides) -> static center crop.
"""

from pathlib import Path

import cv2

_model = None  # loaded once per process; YOLO init is expensive


def _get_model(model_name: str):
    global _model
    if _model is None:
        from ultralytics import YOLO  # lazy: heavy import, pulls in torch

        _model = YOLO(model_name)
    return _model


def compute_crop_path(
    clip_path: Path,
    model_name: str = "yolov8n.pt",
    sample_fps: float = 5.0,
    smoothing: float = 0.3,     # EMA alpha: lower = smoother, slower to follow
    dead_zone: float = 0.05,    # ignore subject moves smaller than 5% of width
    min_confidence: float = 0.4,
) -> list[tuple[float, float]]:
    """Returns [(t, center_x)] with t in seconds from clip start and
    center_x normalized 0..1. Always returns at least one point."""
    cap = cv2.VideoCapture(str(clip_path))
    if not cap.isOpened():
        raise RuntimeError(f"OpenCV could not open {clip_path}")

    video_fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    frame_step = max(1, round(video_fps / sample_fps))
    model = _get_model(model_name)

    path: list[tuple[float, float]] = []
    prev_box = None      # (x1, y1, x2, y2) of last chosen subject
    smoothed_x = None    # EMA state
    frame_idx = 0

    while True:
        ok, frame = cap.read()
        if not ok:
            break
        if frame_idx % frame_step != 0:
            frame_idx += 1
            continue

        t = frame_idx / video_fps
        width = frame.shape[1]

        box = _primary_subject(model, frame, prev_box, min_confidence)
        if box is not None:
            prev_box = box
            raw_x = ((box[0] + box[2]) / 2) / width
            if smoothed_x is None:
                smoothed_x = raw_x
            elif abs(raw_x - smoothed_x) > dead_zone:
                smoothed_x = smoothed_x + smoothing * (raw_x - smoothed_x)
            path.append((t, smoothed_x))

        frame_idx += 1

    cap.release()

    if not path:
        return [(0.0, 0.5)]  # nothing detected anywhere -> static center crop
    return path


def _primary_subject(model, frame, prev_box, min_confidence):
    """Pick one person box: confidence x area x persistence with last pick."""
    results = model.predict(frame, classes=[0], conf=min_confidence, verbose=False)
    best, best_score = None, 0.0
    frame_area = frame.shape[0] * frame.shape[1]

    for r in results:
        for b in r.boxes:
            x1, y1, x2, y2 = b.xyxy[0].tolist()
            conf = float(b.conf[0])
            area = (x2 - x1) * (y2 - y1) / frame_area
            persistence = 0.5 + 0.5 * _iou((x1, y1, x2, y2), prev_box) if prev_box else 1.0
            score = conf * area * persistence
            if score > best_score:
                best, best_score = (x1, y1, x2, y2), score
    return best


def _iou(a, b) -> float:
    ix1, iy1 = max(a[0], b[0]), max(a[1], b[1])
    ix2, iy2 = min(a[2], b[2]), min(a[3], b[3])
    inter = max(0.0, ix2 - ix1) * max(0.0, iy2 - iy1)
    if inter == 0:
        return 0.0
    area_a = (a[2] - a[0]) * (a[3] - a[1])
    area_b = (b[2] - b[0]) * (b[3] - b[1])
    return inter / (area_a + area_b - inter)
