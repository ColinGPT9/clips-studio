"""Hardware-accelerated encoding selection.

NVENC (NVIDIA's hardware encoder) renders H.264 many times faster than
libx264 on CPU and leaves the CPU free for detection/analysis. Detection is
done once per process by actually test-encoding a frame — the encoder can be
listed by FFmpeg but still fail at runtime (driver/session limits), so a real
probe is the only trustworthy check.

Config: video.encoder in settings.yaml — "auto" (default), "nvenc", or "cpu".
"""

import subprocess

_nvenc_works: bool | None = None


def nvenc_available() -> bool:
    global _nvenc_works
    if _nvenc_works is None:
        try:
            probe = subprocess.run(
                [
                    "ffmpeg", "-v", "error",
                    "-f", "lavfi", "-i", "color=black:s=256x256:d=0.1",
                    "-c:v", "h264_nvenc", "-f", "null", "-",
                ],
                capture_output=True,
                timeout=30,
            )
            _nvenc_works = probe.returncode == 0
        except Exception:
            _nvenc_works = False
        if _nvenc_works:
            print("  Encoder: NVENC (GPU) available — using hardware encoding")
    return _nvenc_works


def video_encoder_args(config: dict | None = None) -> list[str]:
    """The `-c:v ...` argument block for FFmpeg output encoding."""
    mode = (config or {}).get("video", {}).get("encoder", "auto")
    if mode != "cpu" and (mode == "nvenc" or nvenc_available()):
        # p5 ≈ x264 medium quality at a fraction of the time; cq 23 visually
        # matches the crf 20 we used on libx264 for this content.
        return ["-c:v", "h264_nvenc", "-preset", "p5", "-rc", "vbr", "-cq", "23", "-b:v", "0"]
    return ["-c:v", "libx264", "-preset", "veryfast", "-crf", "20"]
