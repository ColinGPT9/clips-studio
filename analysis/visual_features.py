"""Visual activity signals — no neural nets in the global pass.

Global per-second features:
  motion     - mean absolute frame difference (activity/action proxy)
  scene_cut  - hard cuts (frame difference spiking far above its baseline)
  flash      - sudden brightness jumps (gameplay events, explosions, effects)

Decoding strategy matters here: OpenCV must decode *every* frame even when
sampling (H.264 frames depend on previous frames), which costs ~10 minutes
on a 30-minute video. Instead FFmpeg — multithreaded, SIMD — decodes and
emits only the sampled frames, already downscaled and grayscale, piped
straight into numpy. Same signals, ~20x faster.

Reaction scoring (YOLOv8 presence + zoom-in deltas) is computed per
candidate window in `reaction_for_window`, not globally — detector time is
only spent inside likely clips.
"""

import subprocess
from pathlib import Path

import cv2
import numpy as np

SAMPLE_FPS = 2.0
FRAME_W, FRAME_H = 160, 90  # analysis resolution; plenty for motion/cuts


def extract_visual_features(video_path: Path) -> dict[str, np.ndarray]:
    frames = _decode_sampled_gray(video_path)
    if frames.shape[0] < 2:
        return {}

    # Per-sample raw signals.
    diffs = np.abs(np.diff(frames.astype(np.float32), axis=0)).mean(axis=(1, 2))
    diffs = np.concatenate([[0.0], diffs])
    brightness = frames.mean(axis=(1, 2), dtype=np.float32)
    flash_raw = np.abs(np.concatenate([[0.0], np.diff(brightness)]))

    # A hard cut is a frame difference far above the local baseline AND
    # large in absolute terms (avoids flagging noise in static videos).
    baseline = _rolling_median(diffs, int(SAMPLE_FPS * 30))
    cuts_raw = (diffs > np.maximum(3.0 * baseline, 15.0)).astype(np.float32)

    # Bin per-sample arrays into per-second arrays.
    n_secs = int(frames.shape[0] / SAMPLE_FPS)
    if n_secs == 0:
        return {}

    def per_sec(x: np.ndarray, reducer) -> np.ndarray:
        k = int(SAMPLE_FPS)
        return reducer(x[: n_secs * k].reshape(n_secs, k), axis=1).astype(np.float32)

    return {
        "motion": per_sec(diffs, np.mean),
        "scene_cut": per_sec(cuts_raw, np.sum),
        "flash": per_sec(flash_raw, np.max),
    }


def _decode_sampled_gray(video_path: Path) -> np.ndarray:
    from video.encoding import hwaccel_input_args

    cmd = [
        "ffmpeg", "-v", "error",
        *hwaccel_input_args(),  # NVDEC decode: this pass is decode-bound
        "-i", str(video_path),
        "-vf", f"fps={SAMPLE_FPS},scale={FRAME_W}:{FRAME_H}",
        "-f", "rawvideo", "-pix_fmt", "gray", "-",
    ]
    result = subprocess.run(cmd, capture_output=True)
    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg visual decode failed:\n{result.stderr[-1000:].decode(errors='replace')}")
    buf = np.frombuffer(result.stdout, dtype=np.uint8)
    n = buf.size // (FRAME_W * FRAME_H)
    return buf[: n * FRAME_W * FRAME_H].reshape(n, FRAME_H, FRAME_W)


def _rolling_median(x: np.ndarray, window: int) -> np.ndarray:
    half = max(window // 2, 1)
    out = np.empty_like(x)
    for i in range(x.size):
        lo, hi = max(0, i - half), min(x.size, i + half + 1)
        out[i] = np.median(x[lo:hi])
    return out


def reaction_for_window(
    video_path: Path,
    start: float,
    end: float,
    audio_excitement: float,
    detector: str = "yolov8n.pt",
    sample_fps: float = 1.0,
) -> float:
    """Reaction proxy 0..1 for one candidate window: face/person presence x
    framing changes (zoom-ins / lean-ins) x synchronized audio excitement.

    Honest limitation: this is not facial-expression recognition (that needs
    a dedicated FER model, planned as a drop-in upgrade). It measures whether
    a human is on screen, being emphasized, while the audio is exciting —
    which correlates strongly with reaction moments in practice.
    """
    from video.tracker import _get_model  # reuse the cached YOLO instance

    model = _get_model(detector)
    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        return 0.0

    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    times = np.arange(start, end, 1.0 / sample_fps)
    presence, areas = [], []

    for t in times:
        cap.set(cv2.CAP_PROP_POS_FRAMES, int(t * fps))
        ok, frame = cap.read()
        if not ok:
            continue
        results = model.predict(frame, classes=[0], conf=0.4, verbose=False)
        best_area = 0.0
        for r in results:
            for b in r.boxes:
                x1, y1, x2, y2 = b.xyxy[0].tolist()
                best_area = max(best_area, (x2 - x1) * (y2 - y1) / (frame.shape[0] * frame.shape[1]))
        presence.append(1.0 if best_area > 0 else 0.0)
        areas.append(best_area)

    cap.release()
    if not areas:
        return 0.0

    presence_rate = float(np.mean(presence))
    # Emphasis: how much the subject's size changes (zoom-ins, lean-ins).
    area_delta = float(np.abs(np.diff(areas)).sum() / max(np.mean(areas), 1e-6)) if len(areas) > 1 else 0.0
    emphasis = min(area_delta / 3.0, 1.0)

    return presence_rate * (0.4 + 0.6 * emphasis) * (0.5 + 0.5 * audio_excitement)
