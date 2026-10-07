"""The plugin contract, version 1: what goes into a plugin and what comes out.

Clips Kitty hands a plugin a job folder with `job.json` in it, starts the
plugin's command, reads progress lines from its standard output, and reads
`result.json` back when it exits with 0. This module defines those files and
lines and checks them. It imports only the standard library, and Clips Kitty
uses the very same checks, so what passes here passes in the app.

`job.json` (written by Clips Kitty):

    {"plugin_api": 1,
     "plugin": {"id": "example-dev/example-pipeline", "version": "1.0.0"},
     "video": {"path": "...", "id": "...", "title": "...", "duration": 812.5, "games": []},
     "transcript": {"path": ".../transcript.json", "language": "en"},
     "settings": {...},
     "limits": {"max_clips": 5, "min_duration": 10, "max_duration": 60},
     "focus": "what the user asked the clips to be about, or null",
     "models": {"name": {"source": "huggingface", "id": "...", "path": "...", "revision": "...",
                         "files": {"model.onnx": "..."}}},
     "tools": {"ffmpeg": "...", "ffprobe": "...", "ollama": {"host": "...", "model": "..."}},
     "output_dir": "..."}

`video`, `transcript`, `tools.ffmpeg`/`tools.ffprobe` and `tools.ollama` are
present only when the plugin's manifest asks for the permission that covers
them (video.read, transcript.read, ffmpeg, ollama). `models` has an entry for
each model the manifest lists; the run doesn't start until each is on the PC.

`transcript.json`: {"language": "en", "segments": [{"start", "end", "text",
"words": [{"start", "end", "word"}] or null}]}, times in seconds.

`result.json` (written by the plugin, usually through Job.finish()):

    {"plugin_api": 1,
     "ranges": [{"start": 12.0, "end": 41.5, "score": 87, "label": "goal",
                 "title": "...", "reason": "..."}],
     "notes": "optional text shown in the job log"}

`score` (0-100) is optional; without one Clips Kitty keeps the plugin's order.

Progress lines on standard output, one JSON object per line:

    {"type": "progress", "fraction": 0.4, "message": "Reading the kill feed"}
    {"type": "log", "message": "..."}
    {"type": "error", "message": "what went wrong, in words a user understands"}

Any other line is kept in the job log as it is.
"""

from __future__ import annotations

import json
import math

PLUGIN_API_VERSION = 1
SUPPORTED_PLUGIN_APIS = (1,)

# Lengths past which text is cut, so a runaway plugin can't flood the library.
MAX_LABEL = 64
MAX_TITLE = 200
MAX_REASON = 500
MAX_NOTES = 4000
MAX_RANGES = 200


class ContractError(ValueError):
    """A job or result that does not follow the contract. `errors` lists every problem found."""

    def __init__(self, what: str, errors: list[str]):
        self.errors = list(errors)
        super().__init__(f"{what}: " + "; ".join(self.errors))


def _number(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _text(value, limit: int) -> bool:
    return isinstance(value, str) and len(value) <= limit


def check_job(data) -> list[str]:
    """Every problem with a job.json mapping; empty when it is valid."""
    if not isinstance(data, dict):
        return ["job.json must be a JSON object"]
    errors = []
    if data.get("plugin_api") not in SUPPORTED_PLUGIN_APIS:
        errors.append(f"plugin_api {data.get('plugin_api')!r} is not one this SDK knows ({SUPPORTED_PLUGIN_APIS})")
    plugin = data.get("plugin")
    if not isinstance(plugin, dict) or not isinstance(plugin.get("id"), str):
        errors.append("plugin.id is missing")
    video = data.get("video")
    if video is not None:
        if not isinstance(video, dict) or not isinstance(video.get("path"), str):
            errors.append("video.path is missing")
        elif video.get("duration") is not None and not _number(video["duration"]):
            errors.append("video.duration must be a number of seconds")
    transcript = data.get("transcript")
    if transcript is not None and (not isinstance(transcript, dict) or not isinstance(transcript.get("path"), str)):
        errors.append("transcript.path is missing")
    for key in ("settings", "limits", "models", "tools"):
        if not isinstance(data.get(key, {}), dict):
            errors.append(f"{key} must be an object")
    if not isinstance(data.get("output_dir"), str):
        errors.append("output_dir is missing")
    return errors


def check_result(data) -> list[str]:
    """Every problem with a result.json mapping; empty when it is valid."""
    if not isinstance(data, dict):
        return ["result.json must be a JSON object"]
    errors = []
    if data.get("plugin_api") not in SUPPORTED_PLUGIN_APIS:
        errors.append(f"plugin_api must be one of {SUPPORTED_PLUGIN_APIS}, not {data.get('plugin_api')!r}")
    ranges = data.get("ranges", [])
    if not isinstance(ranges, list):
        return [*errors, "ranges must be a list"]
    if len(ranges) > MAX_RANGES:
        errors.append(f"{len(ranges)} ranges is more than the {MAX_RANGES} a job can take")
    for i, r in enumerate(ranges):
        where = f"ranges[{i}]"
        if not isinstance(r, dict):
            errors.append(f"{where} must be an object")
            continue
        start, end = r.get("start"), r.get("end")
        if not _number(start) or not _number(end):
            errors.append(f"{where}: start and end must be numbers of seconds")
        elif start < 0 or end <= start:
            errors.append(f"{where}: needs 0 <= start < end (got {start} to {end})")
        score = r.get("score")
        if score is not None and (not _number(score) or not 0 <= score <= 100):
            errors.append(f"{where}: score must be a number from 0 to 100, or left out")
        for key, limit in (("label", MAX_LABEL), ("title", MAX_TITLE), ("reason", MAX_REASON)):
            if r.get(key) is not None and not _text(r[key], limit):
                errors.append(f"{where}: {key} must be text of at most {limit} characters")
    if data.get("clips"):
        errors.append("clips: returning finished clip files is planned, not part of plugin API 1 yet; return ranges")
    if data.get("notes") is not None and not _text(data["notes"], MAX_NOTES):
        errors.append(f"notes must be text of at most {MAX_NOTES} characters")
    return errors


def progress_line(fraction: float, message: str = "") -> str:
    """The standard-output line that reports progress (fraction 0 to 1)."""
    f = 0.0 if not _number(fraction) else min(1.0, max(0.0, float(fraction)))
    return json.dumps({"type": "progress", "fraction": round(f, 4), "message": str(message)}, ensure_ascii=False)


def log_line(message: str) -> str:
    return json.dumps({"type": "log", "message": str(message)}, ensure_ascii=False)


def error_line(message: str) -> str:
    return json.dumps({"type": "error", "message": str(message)}, ensure_ascii=False)


def parse_line(line: str) -> dict:
    """One line of a plugin's output as an event: progress, log or error.

    Anything that is not one of the three is a log line with the text as it
    was, so a stray print() never breaks a run.
    """
    text = line.rstrip("\r\n")
    try:
        event = json.loads(text)
    except ValueError:
        return {"type": "log", "message": text}
    if not isinstance(event, dict) or event.get("type") not in ("progress", "log", "error"):
        return {"type": "log", "message": text}
    if event["type"] == "progress":
        f = event.get("fraction")
        event["fraction"] = min(1.0, max(0.0, float(f))) if _number(f) else None
    event["message"] = str(event.get("message", ""))
    return event

