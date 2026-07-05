"""Hardware-accelerated encoding selection — NVIDIA, AMD, and Intel.

Hardware encoders render H.264 many times faster than libx264 on CPU and
leave the CPU free for detection/analysis. Preference order:

  NVENC (NVIDIA) -> AMF (AMD) -> QSV (Intel) -> libx264 (CPU)

Detection is done once per process by actually test-encoding a frame WITH
THE EXACT ARGUMENTS we render with — an encoder can be listed by FFmpeg but
still fail at runtime (missing hardware, driver/session limits, unsupported
flags on older drivers). A failed probe just means the next candidate is
tried, so a wrong flag on some AMD driver degrades to CPU encoding instead
of breaking renders.

Config: video.encoder in settings.yaml — "auto" (default) or force one of
"nvenc" / "amf" / "qsv" / "cpu".
"""

import subprocess

CPU_ARGS = ["-c:v", "libx264", "-preset", "veryfast", "-crf", "20"]

# Bitrate-led settings for the hardware encoders: constant-quality flags
# vary wildly between driver generations (especially AMF), while plain
# bitrate control works everywhere. 8 Mbps looks clean for 1080x1920 Shorts.
_CANDIDATES: dict[str, list[str]] = {
    "nvenc": ["-c:v", "h264_nvenc", "-preset", "p5", "-rc", "vbr", "-cq", "23", "-b:v", "0"],
    "amf": ["-c:v", "h264_amf", "-quality", "quality", "-b:v", "8M", "-maxrate", "12M"],
    "qsv": ["-c:v", "h264_qsv", "-global_quality", "23", "-preset", "medium"],
}

_selected: tuple[str, list[str]] | None = None  # cached (name, args)


def _probe(args: list[str]) -> bool:
    """Encode a tiny test clip with the candidate's real argument set."""
    try:
        result = subprocess.run(
            [
                "ffmpeg", "-v", "error",
                "-f", "lavfi", "-i", "color=black:s=256x256:d=0.1",
                *args,
                "-f", "null", "-",
            ],
            capture_output=True,
            timeout=30,
        )
        return result.returncode == 0
    except Exception:
        return False


def _select(mode: str) -> tuple[str, list[str]]:
    if mode == "cpu":
        return "cpu", CPU_ARGS
    if mode in _CANDIDATES:  # user forced a specific hardware encoder
        if _probe(_CANDIDATES[mode]):
            return mode, _CANDIDATES[mode]
        print(f"  Encoder: forced '{mode}' failed its probe — falling back to CPU")
        return "cpu", CPU_ARGS

    # auto: first hardware encoder that actually works on this machine
    for name, args in _CANDIDATES.items():
        if _probe(args):
            vendor = {"nvenc": "NVIDIA NVENC", "amf": "AMD AMF", "qsv": "Intel QSV"}[name]
            print(f"  Encoder: {vendor} (GPU) available — using hardware encoding")
            return name, args
    return "cpu", CPU_ARGS


def hwaccel_input_args() -> list[str]:
    """Hardware DECODE flags, placed before -i. 'auto' picks NVDEC/D3D11VA/
    QSV when the input codec supports it and silently falls back to software
    when it doesn't — so this is safe on every input we feed FFmpeg."""
    return ["-hwaccel", "auto"]


def video_encoder_args(config: dict | None = None) -> list[str]:
    """The `-c:v ...` argument block for FFmpeg output encoding."""
    global _selected
    mode = (config or {}).get("video", {}).get("encoder", "auto")
    if _selected is None or (mode != "auto" and _selected[0] != mode):
        _selected = _select(mode)
    return _selected[1]
