"""Audio excitement signals, fully local (FFmpeg decode -> numpy).

Produces per-second feature arrays over the whole video:
  loudness  - RMS energy per second
  spike     - how loud this second is vs. the rolling 30s median
              (shouts, cheers, hype moments)
  burst     - density of sudden energy onsets within the second
              (laughter, applause, rapid-fire excitement)
  noisiness - zero-crossing rate (broadband/noisy audio like laughter and
              crowd noise scores high; clean speech scores low)

All arrays are raw values here; fusion.py normalizes them to per-video
percentiles. No ML models involved — this is signal processing, which is
exactly why it's fast (~seconds for a 30-minute video).
"""

import subprocess
from pathlib import Path

import numpy as np

SAMPLE_RATE = 16000
FRAME = SAMPLE_RATE // 20  # 50 ms analysis frames


def extract_audio_features(video_path: Path) -> dict[str, np.ndarray]:
    """Returns {feature_name: array indexed by second}. Empty dict if the
    video has no audio stream."""
    pcm = _decode_mono_pcm(video_path)
    if pcm.size < SAMPLE_RATE:
        return {}

    # 50 ms frame energies are the working unit for everything below.
    n_frames = pcm.size // FRAME
    frames = pcm[: n_frames * FRAME].reshape(n_frames, FRAME).astype(np.float32)
    frame_rms = np.sqrt((frames**2).mean(axis=1))

    frames_per_sec = SAMPLE_RATE // FRAME
    n_secs = n_frames // frames_per_sec
    if n_secs == 0:
        return {}

    per_sec = frame_rms[: n_secs * frames_per_sec].reshape(n_secs, frames_per_sec)
    loudness = per_sec.mean(axis=1)

    # Spike: this second vs. rolling 30s median loudness (clamped ratio).
    spike = loudness / np.maximum(_rolling_median(loudness, 30), 1e-6)
    spike = np.clip(spike, 0, 5)

    # Burst: count of frame-level onsets (energy jumping >2x over the
    # previous frame) per second — laughter and applause are onset-dense.
    onsets = frame_rms[1:] > 2.0 * np.maximum(frame_rms[:-1], 1e-6)
    onsets = np.concatenate([[False], onsets])
    burst = (
        onsets[: n_secs * frames_per_sec]
        .reshape(n_secs, frames_per_sec)
        .sum(axis=1)
        .astype(np.float32)
    )

    # Noisiness: zero-crossing rate per second.
    signs = np.signbit(frames)
    frame_zcr = (signs[:, 1:] != signs[:, :-1]).mean(axis=1)
    noisiness = (
        frame_zcr[: n_secs * frames_per_sec].reshape(n_secs, frames_per_sec).mean(axis=1)
    )
    # Only count noisiness when something is actually audible.
    audible = loudness > max(loudness.mean() * 0.3, 1e-6)
    noisiness = noisiness * audible

    return {
        "loudness": loudness,
        "spike": spike,
        "burst": burst,
        "noisiness": noisiness.astype(np.float32),
    }


def _decode_mono_pcm(video_path: Path) -> np.ndarray:
    cmd = [
        "ffmpeg", "-v", "error",
        "-i", str(video_path),
        "-vn", "-ac", "1", "-ar", str(SAMPLE_RATE),
        "-f", "s16le", "-",
    ]
    result = subprocess.run(cmd, capture_output=True)
    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg audio decode failed:\n{result.stderr[-1000:].decode(errors='replace')}")
    return np.frombuffer(result.stdout, dtype=np.int16).astype(np.float32) / 32768.0


def _rolling_median(x: np.ndarray, window: int) -> np.ndarray:
    """Median of a centered window at each position (edges shrink)."""
    half = window // 2
    out = np.empty_like(x)
    for i in range(x.size):
        lo, hi = max(0, i - half), min(x.size, i + half + 1)
        out[i] = np.median(x[lo:hi])
    return out
