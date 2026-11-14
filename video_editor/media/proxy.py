"""The proxy: a copy the editor's <video> plays and scrubs smoothly.

540p (the shorter side 540, never larger than the source), H.264 8-bit
4:2:0, a keyframe every half second (so a seek decodes at most half a
second), at most 60 frames a second, AAC stereo, MP4 with the index first
(+faststart: the app's rule for files only it reads, video/encoding.py).
FFmpeg's default turns the picture upright, so a rotated phone video's
proxy is already the right way up and carries no rotation. A sound with no
picture gets an AAC proxy (.m4a). Made on the CPU (libx264, veryfast,
CRF 26): the same everywhere, and the graphics card stays free for exports.

Standard library only.
"""

from __future__ import annotations

from video_editor.media import probe

SHORT_SIDE = 540
GOP_SECONDS = 0.5
MAX_FPS = 60


def name_for(info: dict) -> str:
    return "proxy.mp4" if info.get("has_video") else "proxy.m4a"


def size_for(info: dict) -> tuple[int, int]:
    """The proxy's width and height: the source as shown (rotation applied),
    its shorter side brought to 540 (or kept, when smaller), both even."""
    w, h = int(info.get("display_width") or 0), int(info.get("display_height") or 0)
    if w <= 0 or h <= 0:
        return 960, 540
    short = min(w, h)
    scale = min(1.0, SHORT_SIDE / short)
    return max(2, int(round(w * scale / 2)) * 2), max(2, int(round(h * scale / 2)) * 2)


def command(ffmpeg: str, source, target, info: dict) -> list[str]:
    argv = [ffmpeg, "-hide_banner", "-nostdin", "-y", "-v", "error", "-progress", "pipe:1", *probe.LOCAL_ONLY, "-i", str(source)]
    if info.get("has_video"):
        w, h = size_for(info)
        fps = info.get("fps_value") or 30.0
        chain = [f"scale={w}:{h}", "format=yuv420p", "setsar=1"]
        if fps > MAX_FPS + 0.5:
            chain.insert(0, f"fps={MAX_FPS}")
            fps = MAX_FPS
        gop = max(1, int(round(fps * GOP_SECONDS)))
        argv += ["-map", "0:v:0", "-map", "0:a:0?", "-sn", "-dn", "-vf", ",".join(chain),
                 "-c:v", "libx264", "-preset", "veryfast", "-crf", "26", "-profile:v", "high",
                 "-g", str(gop), "-keyint_min", str(gop), "-sc_threshold", "0",
                 "-force_key_frames", f"expr:gte(t,n_forced*{GOP_SECONDS})"]
    else:
        argv += ["-map", "0:a:0", "-vn", "-sn", "-dn"]
    if info.get("has_audio"):
        argv += ["-c:a", "aac", "-b:a", "128k", "-ac", "2"]
    return [*argv, "-movflags", "+faststart", "-f", "mp4", str(target)]
