"""What a plugin's own code uses: read the job, report progress, return moments.

    from clipskitty_sdk import read_job

    job = read_job()                      # the job folder Clips Kitty made
    for i, (start, end) in enumerate(find_moments(job.video.path)):
        job.progress(i / 10, "Looking for moments")
        job.add_range(start, end, score=80, label="goal", reason="the crowd erupts")
    job.finish()                          # writes result.json

Or let `run(main)` do the reading, finishing and error reporting:

    from clipskitty_sdk import run

    def main(job):
        ...

    run(main)
"""

from __future__ import annotations

import json
import os
import sys
import traceback
from dataclasses import dataclass, field
from pathlib import Path

from .contract import (
    MAX_LABEL,
    MAX_NOTES,
    MAX_REASON,
    MAX_TITLE,
    PLUGIN_API_VERSION,
    ContractError,
    check_job,
    check_result,
    error_line,
    log_line,
    progress_line,
)

JOB_FILE = "job.json"
RESULT_FILE = "result.json"
SECRET_PREFIX = "CLIPSKITTY_SECRET_"


@dataclass
class Video:
    path: Path
    id: str = ""
    title: str = ""
    duration: float | None = None
    games: list = field(default_factory=list)


@dataclass
class Transcript:
    path: Path
    language: str = ""

    def segments(self) -> list[dict]:
        """The transcript's segments: [{"start", "end", "text", "words"}], seconds."""
        data = json.loads(self.path.read_text(encoding="utf-8"))
        return list(data.get("segments", []))


@dataclass
class Limits:
    max_clips: int | None = None
    min_duration: float | None = None
    max_duration: float | None = None


@dataclass
class Model:
    path: Path
    revision: str = ""
    files: dict = field(default_factory=dict)


@dataclass
class Tools:
    ffmpeg: str | None = None
    ffprobe: str | None = None
    ollama: dict | None = None


class Job:
    """One job: what Clips Kitty handed over, and the moments being returned."""

    def __init__(self, folder: Path, data: dict, out=None):
        self.folder = Path(folder)
        self.data = data
        self._out = out
        self._ranges: list[dict] = []
        self._finished = False
        plugin = data.get("plugin") or {}
        self.plugin_id: str = plugin.get("id", "")
        self.plugin_version: str = plugin.get("version", "")
        video = data.get("video")
        self.video: Video | None = None
        if video:
            self.video = Video(Path(video["path"]), video.get("id", ""), video.get("title", ""),
                               video.get("duration"), list(video.get("games") or []))
        transcript = data.get("transcript")
        self.transcript: Transcript | None = (
            Transcript(Path(transcript["path"]), transcript.get("language", "")) if transcript else None
        )
        self.settings: dict = dict(data.get("settings") or {})
        limits = data.get("limits") or {}
        self.limits = Limits(limits.get("max_clips"), limits.get("min_duration"), limits.get("max_duration"))
        self.focus: str | None = data.get("focus") or None
        self.models: dict[str, Model] = {
            name: Model(Path(m["path"]), m.get("revision", ""), dict(m.get("files") or {}))
            for name, m in (data.get("models") or {}).items()
        }
        tools = data.get("tools") or {}
        self.tools = Tools(tools.get("ffmpeg"), tools.get("ffprobe"), tools.get("ollama"))
        self.output_dir = Path(data.get("output_dir") or self.folder / "out")

    # ---- talking to Clips Kitty while running --------------------------------

    def _say(self, line: str) -> None:
        out = self._out or sys.stdout
        out.write(line + "\n")
        out.flush()

    def progress(self, fraction: float, message: str = "") -> None:
        """How far along the plugin is, 0 to 1, with a short message for the user."""
        self._say(progress_line(fraction, message))

    def log(self, message: str) -> None:
        """A line for the job's log (shown when a job fails, and in the log view)."""
        self._say(log_line(message))

    def secret(self, name: str) -> str | None:
        """A `secret` setting from the plugin's manifest (an API key, a licence key).

        Clips Kitty keeps these in its own encrypted store and hands them over
        in the environment, never in job.json, so they are never on disk here.
        """
        return os.environ.get(SECRET_PREFIX + name.upper().replace("-", "_")) or None

    # ---- the answer ----------------------------------------------------------

    def add_range(self, start: float, end: float, *, score: float | None = None, label: str = "",
                  title: str = "", reason: str = "") -> None:
        """One moment of the source video, in seconds, to become a clip.

        `score` (0-100) is optional: without one, Clips Kitty keeps the order
        the ranges were added in. `label` is the kind of moment ("goal",
        "team_wipe"), `title` a short headline, `reason` why it was picked;
        Clips Kitty writes the clip's own title and captions either way.
        """
        entry = {"start": float(start), "end": float(end), "label": str(label)[:MAX_LABEL],
                 "title": str(title)[:MAX_TITLE], "reason": str(reason)[:MAX_REASON]}
        if score is not None:
            entry["score"] = float(score)
        problems = check_result({"plugin_api": PLUGIN_API_VERSION, "ranges": [entry]})
        if problems:
            raise ContractError("add_range", [p.replace("ranges[0]", "range") for p in problems])
        limits = self.limits
        length = entry["end"] - entry["start"]
        if limits.max_duration and length > limits.max_duration:
            self.log(f"range {start:.1f}-{end:.1f}s is longer than this job's {limits.max_duration}s limit")
        if limits.min_duration and length < limits.min_duration:
            self.log(f"range {start:.1f}-{end:.1f}s is shorter than this job's {limits.min_duration}s minimum")
        self._ranges.append(entry)

    @property
    def ranges(self) -> list[dict]:
        return list(self._ranges)

    def finish(self, notes: str = "") -> Path:
        """Write result.json. Call once, at the end; the process should then exit with 0."""
        result = {"plugin_api": PLUGIN_API_VERSION, "ranges": self._ranges}
        if notes:
            result["notes"] = str(notes)[:MAX_NOTES]
        problems = check_result(result)
        if problems:
            raise ContractError("result", problems)
        target = self.folder / RESULT_FILE
        partial = target.with_suffix(".json.partial")
        partial.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
        os.replace(partial, target)
        self._finished = True
        return target

    def fail(self, message: str, code: int = 1):
        """Stop with a message the user will see on the failed job."""
        self._say(error_line(message))
        raise SystemExit(code)


def read_job(folder: str | os.PathLike | None = None, out=None) -> Job:
    """The job Clips Kitty prepared: from `folder`, else the first command-line
    argument, else the CLIPSKITTY_JOB environment variable."""
    where = folder or (sys.argv[1] if len(sys.argv) > 1 else None) or os.environ.get("CLIPSKITTY_JOB")
    if not where:
        raise ContractError("job", ["no job folder: pass it as the first argument or set CLIPSKITTY_JOB"])
    path = Path(where)
    data = json.loads((path / JOB_FILE).read_text(encoding="utf-8"))
    problems = check_job(data)
    if problems:
        raise ContractError("job.json", problems)
    return Job(path, data, out=out)


def run(main, folder: str | os.PathLike | None = None) -> None:
    """Read the job, call `main(job)`, and finish it, reporting any error.

    An exception inside `main` becomes an error line with its message (the
    traceback goes to the log), and the process exits with 1, so the job fails
    with words rather than a stack trace.
    """
    job = read_job(folder)
    try:
        main(job)
        if not job._finished:
            job.finish()
    except SystemExit:
        raise
    except Exception as e:  # report it the way the contract asks, then stop
        for line in traceback.format_exc().splitlines():
            job.log(line)
        job.fail(str(e) or type(e).__name__)
