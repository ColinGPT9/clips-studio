# SPDX-License-Identifier: MIT
# Copyright (c) 2026 ColinGPT9. The Clips Kitty SDK; see sdk/python/LICENSE.
"""Developer tools: check a plugin, and run it on a video the way Clips Kitty would.

    python -m clipskitty_sdk new FOLDER --template NAME [--publisher NAME] [--name "Display name"]
                                 [--game SLUG] [--author NAME] [--license SPDX]
    python -m clipskitty_sdk new --list
    python -m clipskitty_sdk validate <plugin folder>
    python -m clipskitty_sdk run <plugin folder> [--sample | --video clip.mp4] [--transcript t.json]
                                 [--duration SECONDS] [--moments moments.json]
                                 [--steps find | understand rate] [--min-score 55]
                                 [--set name=value ...] [--secret name=value ...]
                                 [--model name=path ...] [--game NAME ...] [--game-hint TAGS]
    python -m clipskitty_sdk sample OUT.mp4
    python -m clipskitty_sdk frame VIDEO --at SECONDS [--region "0.30,0.10,0.40,0.10"] [--out FILE.png]
    python -m clipskitty_sdk install <plugin folder> [--watch] [--yes] [--data-dir DIR]
                                     [--api http://127.0.0.1:8765]
    python -m clipskitty_sdk listing <plugin folder> --section gaming/generic [--alias WORDS ...]
                                     [--to EXISTING.yaml] [--out FILE]
    python -m clipskitty_sdk schema [--write]
    python -m clipskitty_sdk --version

Installed with pip, `clipskitty-sdk` is the same command. On Windows, run it
as `py -m clipskitty_sdk` (the hints it prints name the Python that runs it).

`new` writes a new plugin folder from a template (clipskitty_sdk.scaffold):
its manifest, code, README, tests and GitHub workflow, ready for
`run --sample`. On a terminal it asks for your GitHub name and the plugin's
name when the options don't give them.

`validate` runs the manifest checks the app, the plugin manager and the
registry run, shows the line in clipskitty.yaml each problem is on, and warns
about code in the plugin that would fail on Clips Kitty's own Python
(clipskitty_sdk.lint). `run` does the same checks, then builds the same job
folder the app builds (clipskitty_sdk.host), starts the plugin's command from
its manifest, shows its progress, and checks its result.json with the same
checks the app uses (clipskitty_sdk.devrun).

Without --steps, `run` asks for the run the app would make: a find run for a
plugin that finds moments, else a run that understands and rates the moments
it is given, as far as the plugin does each. --steps asks for `find`, or for
`understand`, `rate` or both (as separate words or with commas). A run that
understands or rates takes its moments from --moments (a list of {start,
end, score?, label?, title?, reason?}, or a finder's result.json), or gets 5
sample moments spread through the video. --video is needed only for a plugin
with the video.read permission; a run without one is fitted to --duration,
else to the end of --transcript. --sample hands over a 40-second test video
and its transcript instead, made in the job folder (clipskitty_sdk.samples);
a plugin that never touches the video gets the transcript even without
FFmpeg. --set values follow the setting's type. The run may take as long as
Clips Kitty would allow (run.timeout_minutes, else 60 minutes to find and 10
to understand or rate) unless --timeout says otherwise.

`sample` writes that test video and transcript somewhere else, and `frame`
writes one frame of a video as a PNG, with a box drawn around --region and
the region in pixels, to measure where something shows on screen. Neither
writes inside a plugin's folder: Clips Kitty copies everything there.

`install` puts the plugin into the Clips Kitty running on this PC, as
Marketplace › Browse › For developers does (clipskitty_sdk.installer): it
shows Clips Kitty's install screen as text and asks first. --yes skips the
question only when nothing is new, and --watch reinstalls on each save until
a save adds something. It talks only to Clips Kitty on this PC, never
through a proxy.

`listing` writes the file that lists a finished plugin in Awesome Clips
Kitty, the catalog the Marketplace reads (clipskitty_sdk.listing), from its
folder's git repository: the folder must be committed and pushed, and the
id's publisher must own the GitHub repository. --to adds this version to a
listing that exists. It runs only git commands that read, and fetches
nothing; the pull request, with the catalog's index rebuilt, is yours to open.

Exit code 0 means the app would accept the plugin's answer, 1 that the plugin
failed or the app would refuse its answer, 2 that the run couldn't start.
For `install`: 0 installed, 1 not installed, 2 it couldn't get as far as
Clips Kitty's plan. For `listing`: 0 written, 2 refused.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import tempfile
import threading
from pathlib import Path

from . import __version__, devrun, host, installer, listing, samples, scaffold
from ._hints import python_command
from .contract import MAX_RANGES, PLUGIN_API_VERSION, ContractError, _number
from .job import RESULT_FILE
from .manifest import SCHEMA_FILE, schema_text, uses_steps, validate_folder


def _pairs(items: list[str], what: str) -> dict:
    out = {}
    for item in items or []:
        name, sep, value = item.partition("=")
        if not sep or not name:
            raise SystemExit(f"{what} expects name=value, got {item!r}")
        out[name] = value
    return out


def _g(score) -> str:
    return f"{float(score):g}"


def _checked(folder: Path, out) -> tuple[dict | None, object, int]:
    """Read and check the plugin's manifest and code, printing what was
    found to `out`: (manifest, report, warnings printed). The manifest is
    None and the report too when the folder has no manifest or this PC has
    no PyYAML, which is printed as an error."""
    refusal = devrun.manifest_refusal(folder)
    if refusal:
        print(f"error: {refusal}", file=out)
        return None, None, 0
    manifest, report = validate_folder(folder)
    lines = devrun.report_lines(folder, report, manifest)
    for line in lines:
        print(line, file=out)
    return manifest, report, sum(1 for line in lines if line.startswith("warning: "))


def cmd_validate(args) -> int:
    folder = Path(args.plugin).resolve()
    manifest, report, warnings = _checked(folder, sys.stdout)
    if report is None:
        return 2
    if report.ok:
        print(f"{manifest['id']} {manifest['version']}: valid" + (f", {warnings} warning(s)" if warnings else ""))
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


def _is_terminal(stream) -> bool:
    try:
        return bool(stream.isatty())
    except (AttributeError, ValueError, OSError):
        return False


class _Progress:
    """The plugin's progress and log lines as `run` shows them. On a terminal
    the progress line is rewritten in place; elsewhere (a pipe, a file, a
    test) each one is a line of its own."""

    def __init__(self, out):
        self.out = out
        self.live = _is_terminal(out)
        self.width = 0
        self.lock = threading.Lock()  # stdout and stderr are read on two threads

    def __call__(self, event: dict) -> None:
        with self.lock:
            if event["type"] == "progress" and event.get("fraction") is not None:
                text = f"  {event['fraction']:4.0%}  {event.get('message', '')}"
                if self.live:
                    self.out.write("\r" + text.ljust(self.width))
                    self.out.flush()
                    self.width = len(text)
                    return
                print(text, file=self.out)
            elif event.get("message"):
                self._end_line()
                print(f"  {event['type']}: {event['message']}", file=self.out)

    def _end_line(self) -> None:
        if self.width:
            self.out.write("\n")
            self.out.flush()
            self.width = 0

    def done(self) -> None:
        with self.lock:
            self._end_line()


def cmd_run(args) -> int:
    folder = Path(args.plugin).resolve()
    manifest, report, _ = _checked(folder, sys.stderr)
    if report is None:
        return 2
    if not report.ok:
        print("error: fix the manifest first; Clips Kitty would refuse to install this plugin", file=sys.stderr)
        return 2
    perms = set(manifest.get("permissions") or [])
    ffmpeg = args.ffmpeg or shutil.which("ffmpeg")
    ffprobe = args.ffprobe or shutil.which("ffprobe")
    try:
        steps = devrun.run_steps(manifest, ",".join(args.steps) if args.steps else None)
        find_run = "find" in steps
        sample_video = args.sample and devrun.sample_video_wanted(
            manifest, ffmpeg, {"--video": args.video, "--transcript": args.transcript, "--duration": args.duration})
        if args.video is None and not args.sample and "video.read" in perms:
            raise devrun.Refused("--video is needed: a plugin with the video.read permission needs a video to "
                                 "run on, or try it with --sample")
        video = Path(args.video).resolve() if args.video is not None else None
        if video is not None and not video.is_file():
            raise devrun.Refused(f"no such video: {video}")
        if args.duration is not None and not (_number(args.duration) and args.duration > 0):
            raise devrun.Refused("--duration must be a number of seconds above 0")
        if not 0 <= args.min_score <= 100:
            raise devrun.Refused("--min-score must be a number from 0 to 100")
        if args.timeout is not None and not (_number(args.timeout) and args.timeout > 0):
            raise devrun.Refused("--timeout must be a number of seconds above 0")
        settings = devrun.typed_settings(manifest, _pairs(args.set, "--set"))
        try:
            host.job_settings(manifest, settings)  # refused here, before the job folder is made
        except ValueError as e:
            raise devrun.Refused(str(e)) from e
        models = devrun.models_for(manifest, _pairs(args.model, "--model"))
        secrets = _pairs(args.secret, "--secret")
    except devrun.Refused as e:
        print(f"error: {e}", file=sys.stderr)
        return 2

    if args.sample:
        print(f"note: --sample: {samples.SAMPLE_NOTE if sample_video else devrun.SAMPLE_WITHOUT_VIDEO}",
              file=sys.stderr)
    if "ffmpeg" in perms and not ffmpeg:
        print("warning: this plugin asks for ffmpeg, but FFmpeg isn't on PATH. Install FFmpeg, or pass --ffmpeg "
              "and --ffprobe.", file=sys.stderr)
    if "ollama" in perms and not args.ollama_model:
        print(f"note: {devrun.NO_OLLAMA_MODEL}", file=sys.stderr)
    if args.sample:  # the sample video is made in the job folder, below
        duration, transcript = samples.SAMPLE_VIDEO_SECONDS, samples.sample_transcript()
    else:
        duration = devrun.probe_duration(ffprobe, video) if video is not None else None
        if duration is None:
            duration = args.duration
        if "transcript.read" in perms and not args.transcript:
            print("note: no --transcript given, so the plugin gets an empty one", file=sys.stderr)
        transcript = devrun.read_transcript(args.transcript)
        if video is None and duration is None:
            duration = devrun.transcript_end(transcript)  # what the answer is fitted to

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
                moments = devrun.read_moments(args.moments)
            else:
                length = duration or devrun.transcript_end(transcript)
                if not length:
                    raise devrun.Refused("no video length for sample moments: pass --duration, or --moments")
                moments = devrun.sample_moments(length)
                print(f"note: no --moments given, so the plugin gets {devrun.SAMPLE_MOMENTS} sample moments spread "
                      "through the video", file=sys.stderr)
        except devrun.Refused as e:
            print(f"error: {e}", file=sys.stderr)
            return 2
        if len(moments) > MAX_RANGES:
            print(f"note: only the first {MAX_RANGES} of {len(moments)} moments are handed over, as in the app",
                  file=sys.stderr)
            moments = moments[:MAX_RANGES]

    # Every check has passed: only now is the job folder made.
    job_folder = Path(args.job_dir).resolve() if args.job_dir else Path(tempfile.mkdtemp(prefix="clipskitty-job-"))
    if sample_video:
        try:
            video = samples.make_sample(job_folder / "sample.mp4", ffmpeg)[0].resolve()
        except samples.SampleError as e:
            print(f"error: {e}", file=sys.stderr)
            if not args.job_dir:
                shutil.rmtree(job_folder, ignore_errors=True)
            return 2
    job, transcript = host.build_job(
        manifest,
        settings=settings,
        video=None if video is None else {"path": str(video), "id": video.stem, "title": video.stem,
                                          "duration": duration,
                                          "games": devrun.games_for(args.game, args.game_hint, duration)},
        transcript=transcript,
        limits=limits,
        focus=args.focus, ffmpeg=ffmpeg, ffprobe=ffprobe,
        ollama={"host": args.ollama_host, "model": args.ollama_model or ""},
        models=models,
        output_dir=job_folder / "out",
        steps=list(steps) if not find_run or uses_steps(manifest) else None,
        moments=moments,
    )
    print(f"job folder: {job_folder}")

    timeout = args.timeout if args.timeout is not None else devrun.time_limit(manifest, steps)
    progress = _Progress(sys.stdout)
    python = args.python or host.find_python()
    outcome = devrun.start(folder, manifest, job_folder, job, transcript, python=python or "python",
                           base_env=os.environ, secrets=secrets, timeout=timeout,
                           on_event=progress)
    progress.done()
    if outcome.timed_out:
        if args.timeout is None:
            print(f"failed: the plugin ran past its {timeout / 60:g} minute limit, as Clips Kitty would stop it",
                  file=sys.stderr)
        else:
            print(f"failed: the plugin ran past the {timeout:g} second limit --timeout set", file=sys.stderr)
        return 1
    if not outcome.ok:
        print(f"failed: {outcome.error}", file=sys.stderr)
        return 1
    if find_run:
        code = _show_ranges(job_folder, job, duration)
        if code == 0:
            for line in devrun.label_warnings(manifest, job_folder):
                print(f"warning: {line}", file=sys.stderr)
        return code
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
        if r.get("reason"):
            print(f"       why: {r['reason']}")
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


def _inside_plugin(what: str, example: str) -> str:
    return (f"error: that is inside a plugin's folder, and Clips Kitty copies everything there on install. "
            f"Write the {what} somewhere else, for example: {example}")


def cmd_sample(args) -> int:
    out = Path(args.out)
    plugin = samples.plugin_folder_holding(out)
    if plugin is not None:
        example = samples.beside_plugin(plugin, "sample.mp4")
        print(_inside_plugin("sample", f"{python_command()} sample {example}"), file=sys.stderr)
        return 2
    ffmpeg = args.ffmpeg or shutil.which("ffmpeg")
    if not ffmpeg:
        print("error: sample needs FFmpeg to make the test video. Install FFmpeg, or pass --ffmpeg. An installed "
              f"Clips Kitty has it, in {samples.INSTALLED_FFMPEG}.", file=sys.stderr)
        return 2
    try:
        video, transcript = samples.make_sample(out, ffmpeg)
    except samples.SampleError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    print(f"wrote {video} and {transcript}: {samples.SAMPLE_NOTE}")
    return 0


def cmd_frame(args) -> int:
    region = None
    if args.region:
        text = ",".join(args.region)  # PowerShell hands an unquoted 0.30,0.10,... over as several words
        try:
            region = samples.parse_region(text)
        except ValueError as e:
            print(f"error: --region: {e}", file=sys.stderr)
            return 2
    if not (_number(args.at) and args.at >= 0):
        print("error: --at must be a number of seconds, 0 or more", file=sys.stderr)
        return 2
    out = Path(args.out) if args.out else Path(f"frame-{args.at:g}s.png")
    plugin = samples.plugin_folder_holding(out)
    if plugin is not None:
        print(_inside_plugin("frame", f"--out {samples.beside_plugin(plugin, 'frame.png')}"), file=sys.stderr)
        return 2
    video = Path(args.video)
    if not video.is_file():
        print(f"error: no such video: {video}", file=sys.stderr)
        return 2
    ffmpeg = args.ffmpeg or shutil.which("ffmpeg")
    if not ffmpeg:
        print("error: frame needs FFmpeg to read the video. Install FFmpeg, or pass --ffmpeg. An installed Clips "
              f"Kitty has it, in {samples.INSTALLED_FFMPEG}.", file=sys.stderr)
        return 2
    try:
        width, height, box = samples.write_frame(ffmpeg, video, args.at, out, region)
    except samples.SampleError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    print(f"wrote {out}: the frame at {args.at:g} s of this {width}x{height} video")
    if box is not None:
        x, y, w, h = box
        print(f'region "{samples.region_text(text)}" is x={x} y={y} w={w} h={h} on this {width}x{height} video')
    return 0


def _ask(question: str) -> str:
    try:
        return input(question).strip()
    except EOFError:
        return ""


def _shown(path: str) -> str:
    """A path as it goes into a command to copy: quoted when it holds a space."""
    return f'"{path}"' if any(ch.isspace() for ch in path) else path


def cmd_new(args) -> int:
    if args.list:
        sys.stdout.write(scaffold.list_text())
        return 0
    if not args.folder or not args.template:
        print(f"error: new needs a folder and a template: {python_command()} new FOLDER --template NAME "
              f"(templates: {', '.join(scaffold.TEMPLATES)}; new --list says what each does)", file=sys.stderr)
        return 2
    asking = _is_terminal(sys.stdin) and _is_terminal(sys.stdout)  # never ask a script or a test
    try:
        scaffold.template_files(args.template)
        scaffold.check_folder(Path(args.folder))
        publisher = args.publisher
        if publisher is None:
            publisher = (_ask(scaffold.ASK_PUBLISHER) if asking else "") or scaffold.DEFAULT_PUBLISHER
        scaffold.check_publisher(publisher)
        name = args.name
        if name is None and asking:
            name = _ask(scaffold.ASK_NAME)
        scaffold.make(args.folder, args.template, publisher=publisher, name=name or None, game=args.game,
                      author=args.author, license=args.license)
    except scaffold.NewRefused as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    print(f"Made {args.folder} from the {args.template} template.")
    note = scaffold.license_note(args.license)
    if note:
        print(f"note: {note}", file=sys.stderr)
    print(f"Next: {python_command()} run {_shown(args.folder)} --sample")
    print("Then change clipskitty.yaml, src/main.py and README.md for your game:")
    print(scaffold.GUIDE)
    return 0


def cmd_install(args) -> int:
    return installer.run(args.plugin, api=args.api, data_dir=args.data_dir, yes=args.yes, watch=args.watch)


def cmd_listing(args) -> int:
    return listing.run(args.plugin, section=args.section, aliases=args.alias or (), to=args.to, out=args.out)


def version_line() -> str:
    """What --version prints: the SDK's version and the plugin contract's."""
    return f"clipskitty-sdk {__version__} (plugin contract {PLUGIN_API_VERSION})"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m clipskitty_sdk", description=__doc__.split("\n\n")[0])
    parser.add_argument("--version", action="version", version=version_line(),
                        help="print the SDK's version and the plugin contract it follows")
    sub = parser.add_subparsers(dest="command", required=True)
    new = sub.add_parser("new", help="start a new plugin from a template")
    new.add_argument("folder", nargs="?", help="the new plugin's folder (new, or empty); its name becomes the "
                     "plugin's name in its id")
    new.add_argument("--template", metavar="NAME", help=f"one of: {', '.join(scaffold.TEMPLATES)}")
    new.add_argument("--list", action="store_true", help="list the templates and what each does")
    new.add_argument("--publisher", metavar="NAME",
                     help=f"your GitHub name, in lower case: the id's first part (default: {scaffold.DEFAULT_PUBLISHER})")
    new.add_argument("--name", metavar="DISPLAY NAME", help="the plugin's name as people see it "
                     "(default: from the folder's name)")
    new.add_argument("--game", metavar="SLUG", default=scaffold.DEFAULT_GAME,
                     help=f"the game it is for, in lower case with hyphens (default: {scaffold.DEFAULT_GAME}, "
                          "a made-up game)")
    new.add_argument("--author", metavar="NAME", help="who holds the copyright (default: the publisher)")
    new.add_argument("--license", metavar="SPDX", default=scaffold.DEFAULT_LICENSE,
                     help=f"the plugin's licence, as an SPDX id (default: {scaffold.DEFAULT_LICENSE}, whose text "
                          "is written as LICENSE)")
    check = sub.add_parser("validate", help="check a plugin's manifest the way Clips Kitty and the registry do")
    check.add_argument("plugin", help="the plugin's folder (the one holding clipskitty.yaml)")
    schema = sub.add_parser("schema", help="print the manifest's JSON Schema, for editors")
    schema.add_argument("--write", action="store_true", help="write it to the SDK's schema/ folder (contributors)")
    run = sub.add_parser("run", help="run a plugin on a video the way Clips Kitty would")
    run.add_argument("plugin", help="the plugin's folder (the one holding clipskitty.yaml)")
    run.add_argument("--video", help="a video file to run it on (needed with video.read)")
    run.add_argument("--sample", action="store_true",
                     help="run it on a 40-second test video and its transcript, made in the job folder with FFmpeg")
    run.add_argument("--transcript", help="transcript.json ({language, segments}) to hand over")
    run.add_argument("--duration", type=float, metavar="SECONDS",
                     help="the video's length, when there is no --video or FFprobe can't read it")
    run.add_argument("--steps", nargs="+", metavar="STEP",
                     help="what to ask for: find, or understand and/or rate, as separate words or with commas "
                          "(default: the run the app would make)")
    run.add_argument("--moments", metavar="MOMENTS.JSON",
                     help="the moments to rate or understand: a list of {start, end, score?, label?, title?, "
                          "reason?}, or a finder's result.json (default: 5 sample moments)")
    run.add_argument("--min-score", type=float, default=devrun.DEFAULT_MIN_SCORE,
                     help="the creator's minimum score, handed to a run that understands or rates moments "
                          f"(default {devrun.DEFAULT_MIN_SCORE:g})")
    run.add_argument("--set", action="append", metavar="NAME=VALUE",
                     help="a setting from the manifest; the value is read as the setting's type")
    run.add_argument("--secret", action="append", metavar="NAME=VALUE", help="a secret setting")
    run.add_argument("--model", action="append", metavar="NAME=PATH",
                     help="where a model the manifest lists is on this PC (its listed files are inside PATH)")
    run.add_argument("--game", action="append", metavar="NAME",
                     help="a game the video shows, for the whole video (video.games)")
    run.add_argument("--game-hint", action="append", metavar="TAGS",
                     help="the video's tags, as YouTube hands them over when it only says Gaming (video.games)")
    run.add_argument("--max-clips", type=int, default=0)
    run.add_argument("--min-duration", type=float, default=devrun.DEFAULT_MIN_DURATION)
    run.add_argument("--max-duration", type=float, default=devrun.DEFAULT_MAX_DURATION)
    run.add_argument("--focus", help="what the user asked the clips to be about")
    run.add_argument("--ollama-host", default=devrun.DEFAULT_OLLAMA_HOST)
    run.add_argument("--ollama-model", help="the local model the app would hand over")
    run.add_argument("--python", help="the Python to run {python} commands with")
    run.add_argument("--ffmpeg")
    run.add_argument("--ffprobe")
    run.add_argument("--timeout", type=float, metavar="SECONDS",
                     help="stop the plugin after this long (default: what Clips Kitty allows: "
                          "run.timeout_minutes, else 60 minutes to find and 10 to understand or rate)")
    run.add_argument("--job-dir", help="where to build the job folder (default: a new temporary folder)")
    sample = sub.add_parser("sample", help="write a 40-second test video and its transcript, made with FFmpeg")
    sample.add_argument("out", metavar="OUT.mp4",
                        help="where to write the video; its transcript goes beside it, as OUT.transcript.json")
    sample.add_argument("--ffmpeg")
    frame = sub.add_parser("frame", help="write one frame of a video as a PNG, to measure where things show")
    frame.add_argument("video", help="the video")
    frame.add_argument("--at", type=float, required=True, metavar="SECONDS", help="the frame's time")
    frame.add_argument("--region", nargs="+", metavar="LEFT,TOP,WIDTH,HEIGHT",
                       help='a box to draw, as fractions of the frame from 0 to 1, such as "0.30,0.10,0.40,0.10" '
                            "(quote it); prints it in pixels")
    frame.add_argument("--out", metavar="FILE.png", help="where to write it (default: frame-SECONDSs.png here)")
    frame.add_argument("--ffmpeg")
    inst = sub.add_parser("install", help="install a plugin you're writing into the Clips Kitty running on this PC")
    inst.add_argument("plugin", help="the plugin's folder (the one holding clipskitty.yaml)")
    inst.add_argument("--watch", action="store_true",
                      help="after installing, reinstall it each time a file in the folder is saved, until a save "
                           "adds something new or Ctrl+C")
    inst.add_argument("--yes", action="store_true",
                      help="install without asking, only when nothing is new: the same plugin is installed and the "
                           "update adds no permission, network host, data sent, change to where it runs, or step")
    inst.add_argument("--data-dir", metavar="DIR",
                      help="Clips Kitty's data folder, the one holding plugins/session.secret (default: the one "
                           "Clips Kitty names, else the installed app's)")
    inst.add_argument("--api", default=installer.DEFAULT_API, metavar="URL",
                      help=f"where Clips Kitty's API is, on this PC only (default: {installer.DEFAULT_API})")
    lst = sub.add_parser("listing", help="write the file that lists your plugin in the Marketplace's catalog "
                         "(it only reads git: it never pushes or fetches)")
    lst.add_argument("plugin", help="the plugin's folder (the one holding clipskitty.yaml), committed and pushed")
    lst.add_argument("--section", metavar="SECTION",
                     help="the catalog section, such as gaming/generic (awesome-clips-kitty/registry/sections.yaml)")
    lst.add_argument("--alias", action="extend", nargs="+", metavar="WORDS",
                     help="a word or short phrase people may search for (quote one with spaces); up to "
                          f"{listing.MAX_ALIASES}")
    lst.add_argument("--to", metavar="EXISTING.yaml",
                     help="add this version to the listing you already have, instead of writing a new one")
    lst.add_argument("--out", metavar="FILE",
                     help="where to write it (default: <name>.yaml in this folder, or the --to file)")
    args = parser.parse_args(argv)
    return {"new": cmd_new, "validate": cmd_validate, "schema": cmd_schema, "run": cmd_run, "sample": cmd_sample,
            "frame": cmd_frame, "install": cmd_install, "listing": cmd_listing}[args.command](args)


if __name__ == "__main__":
    sys.exit(main())
