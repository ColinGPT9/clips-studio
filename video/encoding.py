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


# Every platform loudness-normalises uploads to roughly -14 LUFS. Clips cut
# from different sources arrive all over the place — measured across this
# app's own output, a 13.5 dB spread from a quiet reaction VOD (-25 LUFS) to
# a loud vlog (-12) — so the quiet ones were being pushed up by the platform
# along with their noise floor, and the loud ones pulled down. Normalising
# here means a viewer scrolling between two clips doesn't reach for the
# volume, and nothing is left clipping (one clip peaked at +0.8 dBFS).
LOUDNESS_LUFS = -14.0
LOUDNESS_PEAK = -1.5   # dBTP of headroom, so lossy encoders don't clip
LOUDNORM = f"loudnorm=I={LOUDNESS_LUFS:g}:TP={LOUDNESS_PEAK:g}:LRA=11"
_SYNC = "aresample=async=1"  # keep audio aligned to a rewritten timeline


def audio_filter_args(normalize: bool = True) -> list[str]:
    """The `-af` block for a clip's FINAL audio encode.

    Single-pass loudnorm: a second analysis pass is more exact, but it
    doubles the work for a 30-second clip and the difference lands well
    inside what streaming normalisation would absorb anyway.

    normalize=False for intermediate files — normalising a staging file and
    then normalising the result again is wasted work, and stacking the
    dynamic pass twice can audibly pump.
    """
    if not normalize:
        return ["-af", _SYNC]
    return ["-af", f"{_SYNC},{LOUDNORM}"]


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


# Codecs that software-decode slowly enough to drag the whole pipeline
# (tracking + every clip render re-reads the source). H.264 stays the one
# codec everything downstream is fast and predictable with.
SLOW_SOURCE_CODECS = ("av1", "vp9", "hevc")


def source_codec(path) -> str:
    """codec_name of the first video stream ('' when unprobeable)."""
    try:
        r = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "v:0",
             "-show_entries", "stream=codec_name", "-of", "csv=p=0", str(path)],
            capture_output=True, text=True, timeout=30,
        )
        return r.stdout.strip()
    except Exception:
        return ""


def ensure_h264_source(path, config: dict | None = None) -> bool:
    """Transcode an AV1/VP9 source to H.264 IN PLACE (same filename) before
    the pipeline touches it. One decode + hardware-encode pass (a few
    minutes) beats software-decoding the same file dozens of times — the
    tracking pass plus EVERY clip render decode the source, which is how an
    AV1 25-min video once processed slower than a 2-hour H.264 VOD.
    Downloads prefer H.264 now, but local uploads and format fallbacks can
    still arrive in any codec, so every source is guarded here.
    Returns True if a transcode happened. Failure-safe: the original file
    is kept untouched unless the new one fully succeeds."""
    from pathlib import Path

    p = Path(path)
    codec = source_codec(p)
    if codec not in SLOW_SOURCE_CODECS:
        return False
    print(f"      Source is {codec} (slow to decode) — converting to H.264 once up front...")
    tmp = p.with_name(p.stem + ".h264.tmp.mp4")
    # -pix_fmt yuv420p: 10-bit sources (HEVC main10, HDR phone video) are
    # not accepted by h264_nvenc — normalize to 8-bit while we're here.
    base = ["ffmpeg", "-y", "-v", "error", *hwaccel_input_args(), "-i", str(p),
            *video_encoder_args(config), "-pix_fmt", "yuv420p"]
    # Copy audio when the container allows it; re-encode as the fallback.
    for audio in (["-c:a", "copy"], ["-c:a", "aac", "-b:a", "192k"]):
        try:
            r = subprocess.run([*base, *audio, "-movflags", "+faststart", str(tmp)],
                               capture_output=True, text=True)
            if r.returncode == 0 and tmp.exists() and tmp.stat().st_size > 0:
                tmp.replace(p)
                print("      Converted to H.264 — all later stages decode at full speed")
                return True
        except Exception:
            pass
    tmp.unlink(missing_ok=True)
    print("      (conversion failed — continuing with the original file)")
    return False
