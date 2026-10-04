"""What a render job is, what a worker can do, and whether the two match.

A job is data, never a command: the worker turns it into the same
core.pipeline._render_files call the main PC would have made. It carries
the render settings and nothing else from the config: no AI keys, no
publishing credentials, no tokens (see render_config).
"""

import hashlib
import json
import platform
import socket

PROTOCOL = 1

# The config sections a render reads (core/pipeline._render_files and what it
# calls: captions, tracking, the encoder, the end card). Nothing else leaves
# the main PC.
CONFIG_SECTIONS = ("clips", "tracking", "video")


def render_config(config: dict) -> dict:
    """The allowlisted part of the config a worker renders with."""
    return {k: json.loads(json.dumps(config.get(k) or {})) for k in CONFIG_SECTIONS}


def job_id(video_id: str, start: float, end: float, render_opts: dict | None, config: dict) -> str:
    """Stable for the same clip rendered the same way: a retry, a reconnect or
    a second submission is recognised as the same job, not rendered twice."""
    key = json.dumps({"v": video_id, "s": round(float(start), 3), "e": round(float(end), 3),
                      "o": render_opts or {}, "c": render_config(config), "p": PROTOCOL},
                     sort_keys=True, default=str)
    return hashlib.sha256(key.encode("utf-8")).hexdigest()[:32]


def needs_framing(render_cfg: dict, render_opts: dict | None) -> bool:
    """Whether the render tracks faces or lays out a split (it needs the
    tracking models), or is a straight cut of the whole frame (any worker)."""
    from core import modes

    opts = render_opts or {}
    if opts.get("profile") or modes.is_vertical_live(opts) or modes.is_vertical_live(render_cfg.get("clips")):
        return False
    return bool((render_cfg.get("clips") or {}).get("vertical", True))


def app_version() -> str:
    try:
        from server.feedback import _app_version

        return str(_app_version().get("app", "?"))
    except Exception:
        return "?"


def _ffmpeg_version() -> str:
    import subprocess

    from core.binaries import ffmpeg

    try:
        out = subprocess.run([ffmpeg(), "-hide_banner", "-version"], capture_output=True, text=True,
                             timeout=20).stdout
        return (out.splitlines() or [""])[0].replace("ffmpeg version ", "")[:60]
    except Exception:
        return ""


def _encoders() -> list[str]:
    """The H.264 encoders that actually work here, proven with the exact
    arguments the renderer uses (video/encoding.py test-encodes a frame), not
    guessed from the GPU's name. HEVC and AV1 on NVIDIA are listed for
    information; clips are H.264."""
    from video import encoding

    found = [name for name, args in encoding._CANDIDATES.items() if encoding._probe(args)]
    found.append("cpu")
    for extra in ("hevc_nvenc", "av1_nvenc"):
        if encoding._probe(["-c:v", extra]):
            found.append(extra)
    return found


def _gpu() -> dict:
    try:
        import pynvml

        pynvml.nvmlInit()
        h = pynvml.nvmlDeviceGetHandleByIndex(0)
        name = pynvml.nvmlDeviceGetName(h)
        mem = pynvml.nvmlDeviceGetMemoryInfo(h)
        util = pynvml.nvmlDeviceGetUtilizationRates(h)
        return {"name": name.decode() if isinstance(name, bytes) else str(name),
                "vram_total_gb": round(mem.total / 1e9, 1), "vram_used_gb": round(mem.used / 1e9, 1),
                "utilisation": int(util.gpu)}
    except Exception:
        return {}


def _framing_models() -> bool:
    """Whether this install can track faces (YOLO pose + TalkNet present)."""
    try:
        from video import asd

        return bool(asd.available())
    except Exception:
        return False


def capabilities(full: bool = True) -> dict:
    """What this machine offers as a render worker. `full` probes the
    encoders and FFmpeg (seconds); the heartbeat sends only the cheap,
    changing part."""
    import psutil

    caps = {
        "protocol": PROTOCOL,
        "version": app_version(),
        "name": socket.gethostname(),
        "os": f"{platform.system()} {platform.release()}",
        "cpu": platform.processor() or platform.machine(),
        "cores": psutil.cpu_count(logical=True) or 0,
        "ram_gb": round(psutil.virtual_memory().total / 1e9, 1),
        "cpu_percent": psutil.cpu_percent(interval=None),
        "gpu": _gpu(),
    }
    if full:
        caps["encoders"] = _encoders()
        caps["ffmpeg"] = _ffmpeg_version()
        caps["framing"] = _framing_models()
    return caps


def compatible(job_needs_framing: bool, caps: dict) -> tuple[bool, str]:
    """(can this worker render the job, and if not why)."""
    if int(caps.get("protocol") or 0) != PROTOCOL:
        return False, "worker update required"
    if job_needs_framing and not caps.get("framing"):
        return False, "no face-tracking models on this worker"
    if not caps.get("encoders"):
        return False, "no working video encoder"
    return True, ""
