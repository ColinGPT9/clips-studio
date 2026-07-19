"""Subtitle file writers: SRT and WebVTT.

Plain text formats every platform accepts — YouTube takes one caption file
per language and shows viewers their own, which is the whole point of this
stage. Times are clip-relative, exactly as the caption lines already are.
"""

from pathlib import Path


def _srt_time(t: float) -> str:
    t = max(0.0, float(t))
    h, rem = divmod(t, 3600)
    m, s = divmod(rem, 60)
    return f"{int(h):02d}:{int(m):02d}:{int(s):02d},{int(round((s % 1) * 1000)):03d}"


def _vtt_time(t: float) -> str:
    return _srt_time(t).replace(",", ".")


def write_srt(lines: list[dict], path: Path) -> Path:
    blocks = []
    n = 0
    for line in lines:
        text = str(line.get("text", "")).strip()
        start, end = float(line["start"]), float(line["end"])
        if not text or end <= start:
            continue
        n += 1
        blocks.append(f"{n}\n{_srt_time(start)} --> {_srt_time(end)}\n{text}\n")
    path.write_text("\n".join(blocks), encoding="utf-8")
    return path


def write_vtt(lines: list[dict], path: Path) -> Path:
    blocks = ["WEBVTT\n"]
    for line in lines:
        text = str(line.get("text", "")).strip()
        start, end = float(line["start"]), float(line["end"])
        if not text or end <= start:
            continue
        blocks.append(f"{_vtt_time(start)} --> {_vtt_time(end)}\n{text}\n")
    path.write_text("\n".join(blocks), encoding="utf-8")
    return path
