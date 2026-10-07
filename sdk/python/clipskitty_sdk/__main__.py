# SPDX-License-Identifier: MIT
# Copyright (c) 2026 ColinGPT9. The Clips Kitty SDK; see sdk/python/LICENSE.
"""Developer tools: check a plugin, and run it on a video the way Clips Kitty would.

    python -m clipskitty_sdk validate <plugin folder>
    python -m clipskitty_sdk run <plugin folder> --video clip.mp4 [--transcript t.json]
                                 [--set name=value ...] [--secret name=value ...]
    python -m clipskitty_sdk schema [--write]

`validate` runs the manifest checks the app, the plugin manager and the
registry run. `run` builds the same job folder the app builds
(clipskitty_sdk.host), starts the plugin's command from its manifest, shows its
progress, and checks its result.json with the same checks the app uses. Exit
code 0 means the app would accept the plugin, or its answer.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from . import host
from .contract import ContractError
from .manifest import SCHEMA_FILE, schema_text, validate_folder

# The app's own defaults for a job's clip lengths (config/settings.yaml).
DEFAULT_MIN_DURATION = 10.0
DEFAULT_MAX_DURATION = 60.0


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


def _print_report(report, out=sys.stdout) -> None:
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
    video = Path(args.video).resolve()
    if not video.is_file():
        print(f"error: no such video: {video}", file=sys.stderr)
        return 2
    ffmpeg = args.ffmpeg or shutil.which("ffmpeg")
    ffprobe = args.ffprobe or shutil.which("ffprobe")
    duration = _duration(ffprobe, video)
    perms = set(manifest.get("permissions") or [])
    if "transcript.read" in perms and not args.transcript:
        print("note: no --transcript given, so the plugin gets an empty one", file=sys.stderr)

    job_folder = Path(args.job_dir).resolve() if args.job_dir else Path(tempfile.mkdtemp(prefix="clipskitty-job-"))
    try:
        job, transcript = host.build_job(
            manifest,
            settings={k: _value(v) for k, v in _pairs(args.set, "--set").items()},
            video={"path": str(video), "id": video.stem, "title": video.stem, "duration": duration, "games": []},
            transcript=_transcript(args.transcript),
            limits={"max_clips": args.max_clips or None, "min_duration": args.min_duration,
                    "max_duration": args.max_duration},
            focus=args.focus, ffmpeg=ffmpeg, ffprobe=ffprobe,
            ollama={"host": args.ollama_host, "model": args.ollama_model or ""},
            output_dir=job_folder / "out",
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
    try:
        result = host.read_result(job_folder, duration=duration, max_clips=job["limits"]["max_clips"])
    except ContractError as e:
        print(f"Clips Kitty would refuse this answer: {e}", file=sys.stderr)
        return 1
    print(f"{len(result['ranges'])} moment(s), as Clips Kitty would take them:")
    for r in result["ranges"]:
        score = "  -" if r.get("score") is None else f"{r['score']:3.0f}"
        print(f"  {r['start']:8.1f}s  {r['end']:8.1f}s  score {score}  {r.get('title') or r.get('label') or ''}")
    if result.get("notes"):
        print(f"notes: {result['notes']}")
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
    run.add_argument("--video", required=True, help="a video file to run it on")
    run.add_argument("--transcript", help="transcript.json ({language, segments}) to hand over")
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
