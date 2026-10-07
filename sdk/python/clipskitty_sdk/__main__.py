# SPDX-License-Identifier: MIT
# Copyright (c) 2026 ColinGPT9. The Clips Kitty SDK; see sdk/python/LICENSE.
"""Developer tools: check a plugin, and run it on a video the way Clips Kitty would.

    python -m clipskitty_sdk validate <plugin folder>
    python -m clipskitty_sdk run <plugin folder> [--video clip.mp4] [--transcript t.json]
                                 [--duration SECONDS] [--moments moments.json]
                                 [--steps find|understand,rate] [--min-score 55]
                                 [--set name=value ...] [--secret name=value ...]
    python -m clipskitty_sdk schema [--write]

`validate` runs the manifest checks the app, the plugin manager and the
registry run. `run` builds the same job folder the app builds
(clipskitty_sdk.host), starts the plugin's command from its manifest, shows its
progress, and checks its result.json with the same checks the app uses.

Without --steps, `run` asks for the run the app would make: a find run for a
plugin that finds moments, else a run that understands and rates the moments
it is given, as far as the plugin does each. --steps asks for `find`, or for
`understand`, `rate` or both. A run that understands or rates takes its
moments from --moments (a list of {start, end, score?, label?, title?,
reason?}, or a finder's result.json), or gets 5 sample moments spread
through the video. --video is needed for a find run and for a plugin with the
video.read permission.

Exit code 0 means the app would accept the plugin's answer, 1 that the plugin
failed or the app would refuse its answer, 2 that the run couldn't start.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from . import host
from .contract import MAX_RANGES, STEPS, ContractError
from .job import RESULT_FILE
from .manifest import SCHEMA_FILE, find_steps, offers, schema_text, step_problem, uses_steps, validate_folder

# The app's own defaults for a job's clip lengths and minimum score (config/settings.yaml).
DEFAULT_MIN_DURATION = 10.0
DEFAULT_MAX_DURATION = 60.0
DEFAULT_MIN_SCORE = 55.0
# A moment run's steps, in the order the app runs them.
MOMENT_STEPS = ("understand", "rate")
# A moment from --moments without a score, and each sample moment, gets this one.
DEFAULT_MOMENT_SCORE = 60
SAMPLE_MOMENTS = 5
SAMPLE_SECONDS = 20.0


class _Refused(Exception):
    """Why the run can't start: printed as `error: ...`, exit code 2."""


def _value(text: str):
    """A --set value: JSON when it parses (numbers, true, lists), else the text itself."""
    try:
        return json.loads(text)
    except ValueError:
        return text


def _pairs(items: list[str], what: str) -> dict:
    out = {}
    for item in items or []:
        name, sep, value = item.partition("=")
        if not sep or not name:
            raise SystemExit(f"{what} expects name=value, got {item!r}")
        out[name] = value
    return out


def _duration(ffprobe: str | None, video: Path) -> float | None:
    if not ffprobe:
        return None
    try:
        out = subprocess.run([ffprobe, "-v", "error", "-show_entries", "format=duration", "-of", "json", str(video)],
                             capture_output=True, text=True, timeout=60)
        return float(json.loads(out.stdout)["format"]["duration"])
    except (OSError, ValueError, KeyError, subprocess.SubprocessError):
        return None


def _transcript(path: str | None) -> dict:
    if not path:
        return {"language": "", "segments": []}
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if isinstance(data, list):  # a bare list of segments
        data = {"language": "", "segments": data}
    return {"language": data.get("language", ""), "segments": list(data.get("segments") or [])}


def _number(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _run_steps(manifest: dict, asked: str | None) -> tuple[str, ...]:
    """The steps the run asks the plugin for: the run the app would make,
    or the one --steps names when the plugin offers it."""
    name = manifest.get("name") or manifest.get("id")
    offered = offers(manifest)
    if asked is None:
        if "find" in offered:
            return find_steps(manifest)
        return tuple(step for step in MOMENT_STEPS if step in offered)
    chosen = [part.strip() for part in asked.split(",")]
    for step in chosen:
        if step not in STEPS:
            raise _Refused(f"--steps: unknown step '{step}'; expected find, understand or rate")
    if "find" in chosen and "rate" in chosen:
        raise _Refused("--steps: a run that finds moments isn't also asked to rate others' moments; "
                       "run them separately")
    if "find" in chosen:
        problem = step_problem(manifest, "find")
        if problem:
            raise _Refused(f"--steps: the pipeline {name} {problem}")
        if "understand" in chosen and "understand" not in find_steps(manifest):
            raise _Refused(f"--steps: the pipeline {name} doesn't say what happens in the moments it finds: "
                           "its manifest needs context in outputs")
        return find_steps(manifest)
    for step in MOMENT_STEPS:
        problem = step_problem(manifest, step) if step in chosen else None
        if problem:
            raise _Refused(f"--steps: the pipeline {name} {problem}")
    return tuple(step for step in MOMENT_STEPS if step in chosen)


def _moment(number: int, start: float, end: float, score: float, *, label="", title="", reason="",
            context=()) -> dict:
    """One moment as the app hands it over: found by Clips Kitty, its score as found."""
    return {"id": f"m{number}", "start": start, "end": end, "score": score, "found_score": score,
            "found_by": "clipskitty", "label": label, "signals": {}, "title": title, "reason": reason,
            "context": list(context)}


def _sample_moments(length: float) -> list[dict]:
    """SAMPLE_MOMENTS moments spread through the video, each scored DEFAULT_MOMENT_SCORE."""
    span = min(SAMPLE_SECONDS, length / (SAMPLE_MOMENTS + 1))
    out = []
    for k in range(1, SAMPLE_MOMENTS + 1):
        start = round(length * k / (SAMPLE_MOMENTS + 1), 2)
        out.append(_moment(k, start, round(start + span, 2), DEFAULT_MOMENT_SCORE))
    return out


def _read_moments(path: str) -> list[dict]:
    """--moments: a list of {start, end, score?, label?, title?, reason?}, or
    a finder's result.json, whose ranges are used (with their notes)."""
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except OSError as e:
        raise _Refused(f"--moments: can't read {path} ({e.strerror or e})") from e
    except ValueError as e:
        raise _Refused(f"--moments: {path} is not valid JSON ({e})") from e
    if isinstance(data, dict) and isinstance(data.get("ranges"), list):
        data = data["ranges"]
    if not isinstance(data, list):
        raise _Refused("--moments: expected a list of {start, end, score?, label?, title?, reason?}, "
                       "or a finder's result.json")
    out = []
    for i, item in enumerate(data):
        start, end = (item.get("start"), item.get("end")) if isinstance(item, dict) else (None, None)
        if not _number(start) or not _number(end) or not 0 <= start < end:
            raise _Refused(f"--moments: moment {i} needs a start and an end in seconds, the start first")
        score = item.get("score")
        if score is None:
            score = DEFAULT_MOMENT_SCORE
        elif not _number(score) or not 0 <= score <= 100:
            raise _Refused(f"--moments: moment {i}: score must be a number from 0 to 100, or left out")
        notes = item.get("context") if isinstance(item.get("context"), list) else []
        out.append(_moment(i + 1, float(start), float(end), score, label=str(item.get("label") or ""),
                           title=str(item.get("title") or ""), reason=str(item.get("reason") or ""),
                           context=[n for n in map(host.clean_note, notes) if n]))
    return out


def _transcript_end(transcript: dict) -> float | None:
    ends = [s.get("end") for s in transcript["segments"] if isinstance(s, dict) and _number(s.get("end"))]
    return float(max(ends)) if ends and max(ends) > 0 else None


def _g(score) -> str:
    return f"{float(score):g}"


def _print_report(report, out=None) -> None:
    out = out or sys.stdout  # looked up now: a default argument would keep the stream from import time
    for line in report.errors:
        print(f"error: {line}", file=out)
    for line in report.warnings:
        print(f"warning: {line}", file=out)


def cmd_validate(args) -> int:
    folder = Path(args.plugin).resolve()
    manifest, report = validate_folder(folder)
    _print_report(report)
    if report.ok:
        print(f"{manifest['id']} {manifest['version']}: valid"
              + (f", {len(report.warnings)} warning(s)" if report.warnings else ""))
        return 0
    print(f"{len(report.errors)} problem(s): Clips Kitty would refuse to install this plugin")
    return 1


def cmd_schema(args) -> int:
    if args.write:
        SCHEMA_FILE.parent.mkdir(parents=True, exist_ok=True)
        SCHEMA_FILE.write_text(schema_text(), encoding="utf-8")
        print(f"wrote {SCHEMA_FILE}")
    else:
        sys.stdout.write(schema_text())
    return 0


def cmd_run(args) -> int:
    folder = Path(args.plugin).resolve()
    manifest, report = validate_folder(folder)
    if not report.ok:
        _print_report(report, sys.stderr)
        print("error: fix the manifest first; Clips Kitty would refuse to install this plugin", file=sys.stderr)
        return 2
    command = manifest["run"]["command"]
    perms = set(manifest.get("permissions") or [])
    try:
        steps = _run_steps(manifest, args.steps)
    except _Refused as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    find_run = "find" in steps
    if args.video is None and (find_run or "video.read" in perms):
        print("error: --video is needed: a run that finds moments, or a plugin with the video.read permission, "
              "needs a video to run on", file=sys.stderr)
        return 2
    video = Path(args.video).resolve() if args.video is not None else None
    if video is not None and not video.is_file():
        print(f"error: no such video: {video}", file=sys.stderr)
        return 2
    if args.duration is not None and not (_number(args.duration) and args.duration > 0):
        print("error: --duration must be a number of seconds above 0", file=sys.stderr)
        return 2
    if not 0 <= args.min_score <= 100:
        print("error: --min-score must be a number from 0 to 100", file=sys.stderr)
        return 2
    ffmpeg = args.ffmpeg or shutil.which("ffmpeg")
    ffprobe = args.ffprobe or shutil.which("ffprobe")
    duration = _duration(ffprobe, video) if video is not None else None
    if duration is None:
        duration = args.duration
    if "transcript.read" in perms and not args.transcript:
        print("note: no --transcript given, so the plugin gets an empty one", file=sys.stderr)
    transcript = _transcript(args.transcript)

    limits = {"max_clips": args.max_clips or None, "min_duration": args.min_duration,
              "max_duration": args.max_duration}
    moments = None
    if find_run and args.moments:
        print("note: --moments is for a run that understands or rates moments, so this find run leaves it out",
              file=sys.stderr)
    if not find_run:
        limits["min_score"] = args.min_score
        try:
            if args.moments:
                moments = _read_moments(args.moments)
            else:
                length = duration or _transcript_end(transcript)
                if not length:
                    raise _Refused("no video length for sample moments: pass --duration, or --moments")
                moments = _sample_moments(length)
                print(f"note: no --moments given, so the plugin gets {SAMPLE_MOMENTS} sample moments spread "
                      "through the video", file=sys.stderr)
        except _Refused as e:
            print(f"error: {e}", file=sys.stderr)
            return 2
        if len(moments) > MAX_RANGES:
            print(f"note: only the first {MAX_RANGES} of {len(moments)} moments are handed over, as in the app",
                  file=sys.stderr)
            moments = moments[:MAX_RANGES]

    job_folder = Path(args.job_dir).resolve() if args.job_dir else Path(tempfile.mkdtemp(prefix="clipskitty-job-"))
    try:
        job, transcript = host.build_job(
            manifest,
            settings={k: _value(v) for k, v in _pairs(args.set, "--set").items()},
            video=None if video is None else {"path": str(video), "id": video.stem, "title": video.stem,
                                              "duration": duration, "games": []},
            transcript=transcript,
            limits=limits,
            focus=args.focus, ffmpeg=ffmpeg, ffprobe=ffprobe,
            ollama={"host": args.ollama_host, "model": args.ollama_model or ""},
            output_dir=job_folder / "out",
            steps=list(steps) if not find_run or uses_steps(manifest) else None,
            moments=moments,
        )
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    host.write_job(job_folder, job, transcript)
    print(f"job folder: {job_folder}")

    def on_event(event: dict) -> None:
        if event["type"] == "progress" and event.get("fraction") is not None:
            print(f"  {event['fraction']:4.0%}  {event.get('message', '')}")
        elif event.get("message"):
            print(f"  {event['type']}: {event['message']}")

    secrets = _pairs(args.secret, "--secret")
    env = host.plugin_env(os.environ, job_folder=job_folder, secrets=secrets)
    python = args.python or host.find_python()
    outcome = host.run_plugin(host.resolve_command(command, python or "python", folder), cwd=folder,
                              job_folder=job_folder, env=env, timeout=args.timeout, on_event=on_event)
    if outcome.timed_out:
        print(f"failed: the plugin ran past {args.timeout:g}s", file=sys.stderr)
        return 1
    if not outcome.ok:
        print(f"failed: {outcome.error}", file=sys.stderr)
        return 1
    if find_run:
        return _show_ranges(job_folder, job, duration)
    return _show_answers(job_folder, steps, moments)


def _show_ranges(job_folder: Path, job: dict, duration: float | None) -> int:
    """A find run's answer, as the app would take it."""
    try:
        result = host.read_result(job_folder, duration=duration, max_clips=job["limits"]["max_clips"],
                                  steps=job.get("steps"))
    except ContractError as e:
        print(f"Clips Kitty would refuse this answer: {e}", file=sys.stderr)
        return 1
    print(f"{len(result['ranges'])} moment(s), as Clips Kitty would take them:")
    for r in result["ranges"]:
        score = "  -" if r.get("score") is None else f"{r['score']:3.0f}"
        print(f"  {r['start']:8.1f}s  {r['end']:8.1f}s  score {score}  {r.get('title') or r.get('label') or ''}")
        for note in r.get("context") or []:
            print(f"       note: {note}")
    if result.get("notes"):
        print(f"notes: {result['notes']}")
    return 0


def _show_answers(job_folder: Path, steps: tuple[str, ...], moments: list[dict]) -> int:
    """A moment run's answers, as the app would use them."""
    try:
        answers, ignored = host.read_answers(job_folder, steps=steps, ids=[m["id"] for m in moments])
    except ContractError as e:
        print(f"Clips Kitty would refuse this answer: {e}", file=sys.stderr)
        return 1
    print(f"{' and '.join(steps).capitalize()}: {len(answers)} of {len(moments)} moment(s) answered, "
          "as Clips Kitty would use them:")
    for m in moments:
        answer = answers.get(m["id"], {})
        line = f"{m['id']:<4} {m['start']:.1f}s-{m['end']:.1f}s  score {_g(m['score'])}"
        if "score" in answer:
            line += f" -> {_g(answer['score'])}" + (f"  {answer['reason']}" if answer.get("reason") else "")
        print(line)
        for note in answer.get("context", []):
            print(f"       note: {note}")
    for line in ignored:
        print(line)
    notes = json.loads((job_folder / RESULT_FILE).read_text(encoding="utf-8")).get("notes")
    if notes:
        print(f"notes: {notes}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m clipskitty_sdk", description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)
    check = sub.add_parser("validate", help="check a plugin's manifest the way Clips Kitty and the registry do")
    check.add_argument("plugin", help="the plugin's folder (the one holding clipskitty.yaml)")
    schema = sub.add_parser("schema", help="print the manifest's JSON Schema, for editors")
    schema.add_argument("--write", action="store_true", help="write it to the SDK's schema/ folder (contributors)")
    run = sub.add_parser("run", help="run a plugin on a video the way Clips Kitty would")
    run.add_argument("plugin", help="the plugin's folder (the one holding clipskitty.yaml)")
    run.add_argument("--video", help="a video file to run it on (needed to find moments, and with video.read)")
    run.add_argument("--transcript", help="transcript.json ({language, segments}) to hand over")
    run.add_argument("--duration", type=float, metavar="SECONDS",
                     help="the video's length, when there is no --video or FFprobe can't read it")
    run.add_argument("--steps", metavar="find|understand,rate",
                     help="what to ask for: find, or understand and/or rate (default: the run the app would make)")
    run.add_argument("--moments", metavar="MOMENTS.JSON",
                     help="the moments to rate or understand: a list of {start, end, score?, label?, title?, "
                          "reason?}, or a finder's result.json (default: 5 sample moments)")
    run.add_argument("--min-score", type=float, default=DEFAULT_MIN_SCORE,
                     help="the creator's minimum score, handed to a run that understands or rates moments "
                          f"(default {DEFAULT_MIN_SCORE:g})")
    run.add_argument("--set", action="append", metavar="NAME=VALUE", help="a setting from the manifest")
    run.add_argument("--secret", action="append", metavar="NAME=VALUE", help="a secret setting")
    run.add_argument("--max-clips", type=int, default=0)
    run.add_argument("--min-duration", type=float, default=DEFAULT_MIN_DURATION)
    run.add_argument("--max-duration", type=float, default=DEFAULT_MAX_DURATION)
    run.add_argument("--focus", help="what the user asked the clips to be about")
    run.add_argument("--ollama-host", default="http://localhost:11434")
    run.add_argument("--ollama-model", help="the local model the app would hand over")
    run.add_argument("--python", help="the Python to run {python} commands with")
    run.add_argument("--ffmpeg")
    run.add_argument("--ffprobe")
    run.add_argument("--timeout", type=float, default=600.0, help="seconds (default 600)")
    run.add_argument("--job-dir", help="where to build the job folder (default: a new temporary folder)")
    args = parser.parse_args(argv)
    return {"validate": cmd_validate, "schema": cmd_schema, "run": cmd_run}[args.command](args)


if __name__ == "__main__":
    sys.exit(main())
