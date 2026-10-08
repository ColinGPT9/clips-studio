# SPDX-License-Identifier: MIT
# Copyright (c) 2026 ColinGPT9. The Clips Kitty SDK; see sdk/python/LICENSE.
"""The core of `python -m clipskitty_sdk run`: what a developer's run of a
plugin hands it, built the way Clips Kitty builds it (clipskitty_sdk.host),
and what it answered.

The command line (__main__.py) reads the options and prints; this module does
the work, so other tools can run a plugin the same way. Each check raises
Refused with the sentence the command prints as `error: ...`. Standard
library only.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path, PurePosixPath

from . import host, samples
from .contract import CROPS, PLANNED_STEPS, STEPS, _number, kept_length
from .job import RESULT_FILE
from .lint import lint_folder
from .manifest import MANIFEST_FILE, find_steps, has_yaml, line_marks, offers, step_problem

# The app's own defaults for a job's clip lengths and minimum score (config/settings.yaml).
DEFAULT_MIN_DURATION = 10.0
DEFAULT_MAX_DURATION = 60.0
DEFAULT_MIN_SCORE = 55.0
# Where a plugin with the ollama permission is told the local model answers, as in the app's settings.
DEFAULT_OLLAMA_HOST = "http://localhost:11434"
# What `run` says for a plugin with the ollama permission and no --ollama-model.
NO_OLLAMA_MODEL = ("no --ollama-model given, so the plugin is told there is no local model, as when Clips Kitty's "
                   "AI runs at a cloud provider. To try it with one, run Ollama on this PC and pass --ollama-model "
                   "with the name of a model you have in it")
# A moment run's steps, in the order the app runs them.
MOMENT_STEPS = ("understand", "rate")
# An edit run: always alone, after the clips are chosen.
EDIT_STEPS = ("edit",)
# What `run` adds for a plugin that also suggests edits, when it makes another run.
ALSO_EDITS = "it also suggests edits: run again with --steps edit"
# The kinds of clip --layout names. Only standard vertical clips can use a
# layout (contract.CROPS); every other kind gets none, as in the app.
LAYOUTS = ("standard", "podcast", "sports", "gaming", "vertical-live", "whole-frame")
DEFAULT_LAYOUT = "standard"
# A moment from --moments without a score, and each sample moment, gets this one.
DEFAULT_MOMENT_SCORE = 60
SAMPLE_MOMENTS = 5
SAMPLE_SECONDS = 20.0
# What run --sample says when it can't make the test video.
SAMPLE_NEEDS_FFMPEG = ("--sample needs FFmpeg to make the test video. Install FFmpeg, or pass --ffmpeg and "
                       f"--ffprobe. An installed Clips Kitty has both, in {samples.INSTALLED_FFMPEG}.")
SAMPLE_WITHOUT_VIDEO = (f"no FFmpeg here, so this run gets the sample transcript and a "
                        f"{samples.SAMPLE_VIDEO_SECONDS:g}-second length but no video; this plugin doesn't read "
                        "the video, so that is all it needs")
# The width of "warning: ", so a hint lines up under the line it belongs to.
_INDENT = " " * len("warning: ")


class Refused(Exception):
    """Why the run can't start: printed as `error: ...`, exit code 2."""


# ---- the manifest and the code -----------------------------------------------------


def manifest_refusal(folder: Path) -> str | None:
    """Why a plugin folder's manifest can't be read at all, or None: no
    clipskitty.yaml there, or no PyYAML on this PC (which is no fault of
    the plugin's)."""
    if not (Path(folder) / MANIFEST_FILE).is_file():
        return f"no {MANIFEST_FILE} in {folder}: is this the plugin's folder?"
    if not has_yaml():
        return ("reading clipskitty.yaml needs PyYAML, which isn't installed with this Python: "
                'pip install "clipskitty-sdk[yaml]". This is about your PC, not your plugin.')
    return None


def _marks(folder: Path) -> dict[str, int]:
    try:
        return line_marks((Path(folder) / MANIFEST_FILE).read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError):
        return {}


def _mark(line: str, marks: dict[str, int]) -> str:
    """` (clipskitty.yaml line N)` for a report line "path: message": the
    line of that field, or of the nearest field holding it."""
    where = line.split(": ", 1)[0] if ": " in line else ""
    while where:
        if where in marks:
            return f" ({MANIFEST_FILE} line {marks[where]})"
        cut = max(where.rfind("."), where.rfind("["))
        where = where[:cut] if cut > 0 else ""
    return ""


def report_lines(folder: Path, report, manifest: dict | None) -> list[str]:
    """What `validate` and `run` print for a plugin folder: the manifest's
    errors and warnings, each with its line in clipskitty.yaml when PyYAML
    can tell, a "did you mean" under a misspelt field, then the lint's
    warnings about the code (clipskitty_sdk.lint)."""
    marks = _marks(folder)
    lines = [f"error: {e}{_mark(e, marks)}" for e in report.errors]
    for w in report.warnings:
        lines.append(f"warning: {w}{_mark(w, marks)}")
        where = w.split(": ", 1)[0]
        if where in report.hints:
            lines.append(f"{_INDENT}did you mean {report.hints[where]}?")
    if manifest is not None:
        lines += [f"warning: {w}" for w in lint_folder(folder, manifest)]
    return lines


# ---- what the run asks for ---------------------------------------------------------


def run_steps(manifest: dict, asked: str | None) -> tuple[str, ...]:
    """The steps the run asks the plugin for: the run the app would make,
    or the one `asked` (comma-separated step names) names when the plugin
    offers it. A plugin that only suggests edits gets an edit run; one that
    also finds, understands or rates gets that run, and an edit run only
    when asked for (`edit`, which is always a run of its own)."""
    name = manifest.get("name") or manifest.get("id")
    offered = offers(manifest)
    if asked is None:
        if "find" in offered:
            return find_steps(manifest)
        moment_steps = tuple(step for step in MOMENT_STEPS if step in offered)
        return EDIT_STEPS if not moment_steps and "edit" in offered else moment_steps
    chosen = [part.strip() for part in asked.split(",")]
    for step in chosen:
        if step in PLANNED_STEPS:
            raise Refused(f"--steps: {step} is planned, not part of plugin contract 1 yet; "
                          "use find, understand, rate or edit")
        if step not in STEPS:
            raise Refused(f"--steps: unknown step '{step}'; expected find, understand, rate or edit")
    if "edit" in chosen:
        if set(chosen) != {"edit"}:
            raise Refused("--steps: edit is a run of its own, after the clips are chosen: run it separately")
        problem = step_problem(manifest, "edit")
        if problem:
            raise Refused(f"--steps: the pipeline {name} {problem}")
        return EDIT_STEPS
    if "find" in chosen and "rate" in chosen:
        raise Refused("--steps: a run that finds moments isn't also asked to rate others' moments; "
                      "run them separately")
    if "find" in chosen:
        problem = step_problem(manifest, "find")
        if problem:
            raise Refused(f"--steps: the pipeline {name} {problem}")
        if "understand" in chosen and "understand" not in find_steps(manifest):
            raise Refused(f"--steps: the pipeline {name} doesn't say what happens in the moments it finds: "
                          "its manifest needs context in outputs")
        return find_steps(manifest)
    for step in MOMENT_STEPS:
        problem = step_problem(manifest, step) if step in chosen else None
        if problem:
            raise Refused(f"--steps: the pipeline {name} {problem}")
    return tuple(step for step in MOMENT_STEPS if step in chosen)


def touches_video(manifest: dict) -> bool:
    """Whether a plugin reads the video or runs FFmpeg (the video.read or
    ffmpeg permission). One that does neither, such as a plugin that works
    on the transcript, never sees the video file."""
    return bool({"video.read", "ffmpeg"} & set(manifest.get("permissions") or []))


def sample_video_wanted(manifest: dict, ffmpeg: str | None, given: dict) -> bool:
    """For run --sample: True when the run gets the sample video (made with
    `ffmpeg`), False when there is no FFmpeg here and the plugin never
    touches the video, so the sample transcript and length are all it needs.
    `given` maps each option --sample replaces (--video, --transcript,
    --duration) to its value; one that is set is refused, and so is a
    plugin that needs the video when there is no FFmpeg."""
    for option, value in given.items():
        if value is not None:
            raise Refused(f"use --sample or {option}, not both")
    if ffmpeg:
        return True
    if touches_video(manifest):
        raise Refused(SAMPLE_NEEDS_FFMPEG)
    return False


def also_edits(manifest: dict, steps) -> bool:
    """Whether to say ALSO_EDITS: the plugin suggests edits too, and this run isn't its edit run."""
    return "edit" in offers(manifest) and tuple(steps) != EDIT_STEPS


def crops_for(layout: str | None) -> tuple[str, ...]:
    """limits.crops for --layout, as the app fills it: every layout
    (contract.CROPS) for standard vertical clips, the default, and none for
    any other kind of clip."""
    return CROPS if (layout or DEFAULT_LAYOUT) == DEFAULT_LAYOUT else ()


def time_limit(manifest: dict, steps) -> float:
    """How long Clips Kitty would let this run take, in seconds."""
    default = host.FIND_TIMEOUT_MINUTES if "find" in steps else host.MOMENT_TIMEOUT_MINUTES
    return host.timeout_seconds(manifest, default)


# ---- settings, models and games ------------------------------------------------------


def setting_value(spec: dict, text: str):
    """A --set value as the setting's declared type takes it: text for a
    `string` (and a `secret`), JSON for `integer`, `number` and `boolean`
    (the text itself when it isn't JSON, which the type check then refuses),
    and for a `choice` the text when it is one of the options as written,
    else JSON."""
    kind = spec.get("type") if isinstance(spec, dict) else None
    if kind in ("string", "secret"):
        return text
    if kind == "choice" and text in [str(o) for o in spec.get("options") or []]:
        return text
    try:
        return json.loads(text)
    except ValueError:
        return text


def typed_settings(manifest: dict, given: dict[str, str]) -> dict:
    """The --set values, each as its setting's type takes it. A name the
    manifest doesn't declare is refused with the names it does."""
    declared = manifest.get("settings") if isinstance(manifest.get("settings"), dict) else {}
    out = {}
    for name, text in given.items():
        spec = declared.get(name)
        if not isinstance(spec, dict):
            settable = [n for n, s in declared.items() if isinstance(s, dict) and s.get("type") != "secret"]
            if settable:
                known = f"its settings are {', '.join(settable)}"
            elif declared:
                known = "its only settings are secrets: pass them with --secret"
            else:
                known = "it has no settings"
            raise Refused(f"--set: this pipeline has no setting called '{name}'; {known}")
        out[name] = setting_value(spec, text)
    return out


def _url_file(address: str) -> str:
    return PurePosixPath(address.split("#")[0].split("?")[0].rstrip("/")).name or "model"


def models_for(manifest: dict, given: dict[str, str]) -> dict:
    """job.json's `models` for --model NAME=PATH: each where the developer
    has it, with the files its manifest entry lists (as Clips Kitty's model
    store hands them over)."""
    listed = {m.get("name"): m for m in manifest.get("models") or [] if isinstance(m, dict)}
    out = {}
    for name, path in given.items():
        model = listed.get(name)
        if model is None:
            names = ", ".join(str(n) for n in listed) if listed else "none"
            raise Refused(f"--model: the manifest lists no model called '{name}'; it lists: {names}")
        source = model.get("source", "")
        if source == "url":
            files = {_url_file(str(model.get("id") or "")): str(path)}
        else:
            files = {str(f): str(Path(path) / f) for f in model.get("files") or [] if isinstance(f, str)}
        out[name] = {"source": source, "id": model.get("id", ""), "path": str(path),
                     "revision": str(model.get("revision") or model.get("sha256") or ""), "files": files}
    return out


def games_for(names, hints, duration: float | None) -> list[dict]:
    """video.games for --game NAME (a game shown for the whole video, as
    Twitch and Kick name it) and --game-hint TAGS (YouTube's shape: no name,
    the video's tags as a hint)."""
    end = float(duration) if duration else 0.0
    games = [{"name": str(n), "start": 0.0, "end": end} for n in names or []]
    games += [{"name": "", "start": 0.0, "end": end, "hint": str(h)} for h in hints or []]
    return games


# ---- the video, the transcript and the moments -----------------------------------------


def probe_duration(ffprobe: str | None, video: Path) -> float | None:
    if not ffprobe:
        return None
    try:
        out = subprocess.run([ffprobe, "-v", "error", "-show_entries", "format=duration", "-of", "json", str(video)],
                             capture_output=True, text=True, timeout=60)
        return float(json.loads(out.stdout)["format"]["duration"])
    except (OSError, ValueError, KeyError, subprocess.SubprocessError):
        return None


def read_transcript(path: str | None) -> dict:
    if not path:
        return {"language": "", "segments": []}
    return transcript_from(json.loads(Path(path).read_text(encoding="utf-8")))


def transcript_from(data) -> dict:
    """A transcript as job.json's transcript.json holds it, from
    {language, segments} or a bare list of segments."""
    if isinstance(data, list):  # a bare list of segments
        data = {"language": "", "segments": data}
    return {"language": data.get("language", ""), "segments": list(data.get("segments") or [])}


def transcript_end(transcript: dict) -> float | None:
    ends = [s.get("end") for s in transcript["segments"] if isinstance(s, dict) and _number(s.get("end"))]
    return float(max(ends)) if ends and max(ends) > 0 else None


def moment(number: int, start: float, end: float, score: float, *, label="", title="", reason="",
           context=(), suggested=()) -> dict:
    """One moment as the app hands it over: found by Clips Kitty, its score
    as found, with what earlier edit plugins suggested for it when there is
    anything."""
    out = {"id": f"m{number}", "start": start, "end": end, "score": score, "found_score": score,
           "found_by": "clipskitty", "label": label, "signals": {}, "title": title, "reason": reason,
           "context": list(context)}
    if suggested:
        out["suggested"] = [dict(s) for s in suggested]
    return out


def _suggested_from(i: int, value) -> list[dict]:
    """A --moments item's `suggested`: what earlier edit plugins suggested
    for the clip, as job.json holds it ({by, name, edit, reason?})."""
    if value is None:
        return []
    shape = f"moment {i}: suggested must be a list of {{by, name, edit, reason?}}, by a plugin's id"
    if not isinstance(value, list):
        raise Refused(shape)
    out = []
    for s in value:
        if not (isinstance(s, dict) and isinstance(s.get("by"), str) and isinstance(s.get("edit"), dict)):
            raise Refused(shape)
        entry = {"by": s["by"], "name": str(s.get("name") or s["by"]), "edit": dict(s["edit"])}
        if s.get("reason"):
            entry["reason"] = str(s["reason"])
        out.append(entry)
    return out


def sample_moments(length: float) -> list[dict]:
    """SAMPLE_MOMENTS moments spread through the video, each scored DEFAULT_MOMENT_SCORE."""
    span = min(SAMPLE_SECONDS, length / (SAMPLE_MOMENTS + 1))
    out = []
    for k in range(1, SAMPLE_MOMENTS + 1):
        start = round(length * k / (SAMPLE_MOMENTS + 1), 2)
        out.append(moment(k, start, round(start + span, 2), DEFAULT_MOMENT_SCORE))
    return out


def read_moments(path: str) -> list[dict]:
    """--moments: a list of {start, end, score?, label?, title?, reason?,
    suggested?}, or a finder's result.json, whose ranges are used (with
    their notes)."""
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except OSError as e:
        raise Refused(f"--moments: can't read {path} ({e.strerror or e})") from e
    except ValueError as e:
        raise Refused(f"--moments: {path} is not valid JSON ({e})") from e
    try:
        return moments_from(data)
    except Refused as e:
        raise Refused(f"--moments: {e}") from None


def moments_from(data) -> list[dict]:
    """The moments a run that understands or rates is handed, or the clips
    an edit run is handed, as job.json holds them, from a list of {start,
    end, score?, label?, title?, reason?, context?, suggested?} or a
    finder's result.json (its ranges). Ids m1, m2... are filled in, a
    missing score becomes DEFAULT_MOMENT_SCORE, and found_by is clipskitty.
    `suggested`, to try a plugin after another edit plugin, is a list of
    {by, name, edit, reason?}. Raises Refused with what is wrong."""
    if isinstance(data, dict) and isinstance(data.get("ranges"), list):
        data = data["ranges"]
    if not isinstance(data, list):
        raise Refused("expected a list of {start, end, score?, label?, title?, reason?}, or a finder's result.json")
    out = []
    for i, item in enumerate(data):
        start, end = (item.get("start"), item.get("end")) if isinstance(item, dict) else (None, None)
        if not _number(start) or not _number(end) or not 0 <= start < end:
            raise Refused(f"moment {i} needs a start and an end in seconds, the start first")
        score = item.get("score")
        if score is None:
            score = DEFAULT_MOMENT_SCORE
        elif not _number(score) or not 0 <= score <= 100:
            raise Refused(f"moment {i}: score must be a number from 0 to 100, or left out")
        notes = item.get("context") if isinstance(item.get("context"), list) else []
        out.append(moment(i + 1, float(start), float(end), score, label=str(item.get("label") or ""),
                          title=str(item.get("title") or ""), reason=str(item.get("reason") or ""),
                          context=[n for n in map(host.clean_note, notes) if n],
                          suggested=_suggested_from(i, item.get("suggested"))))
    return out


# ---- running it, and what it answered ------------------------------------------------


def start(folder: Path, manifest: dict, job_folder: Path, job: dict, transcript: dict | None, *, python: str,
          base_env: dict, secrets: dict | None = None, timeout: float | None = None,
          on_event=None) -> host.RunOutcome:
    """Write the job folder and run the plugin in it, as Clips Kitty does."""
    host.write_job(job_folder, job, transcript)
    env = host.plugin_env(base_env, job_folder=job_folder, secrets=secrets)
    return host.run_plugin(host.resolve_command(manifest["run"]["command"], python, folder), cwd=folder,
                           job_folder=job_folder, env=env, timeout=timeout, on_event=on_event)


def label_warnings(manifest: dict, job_folder: Path) -> list[str]:
    """For a plugin whose manifest declares `events`: a line for each label
    its ranges use that isn't one of them (the Marketplace lists only those).
    Ranges are named r1, r2... in the order the plugin added them."""
    events = manifest.get("events")
    if not isinstance(events, list) or not events:
        return []
    try:
        data = json.loads((Path(job_folder) / RESULT_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    ranges = data.get("ranges") if isinstance(data, dict) else None
    out, seen = [], set()
    for i, r in enumerate(ranges if isinstance(ranges, list) else []):
        label = r.get("label") if isinstance(r, dict) else None
        if not isinstance(label, str) or not label or label in events or label in seen:
            continue
        seen.add(label)
        out.append(f"r{i + 1}'s label '{label}' isn't in events in {MANIFEST_FILE} "
                   f"({', '.join(map(str, events))}); the Marketplace lists only those")
    return out


def edit_parts(edit: dict) -> list[str]:
    """A fitted edit (host.read_edits) in words, one part per field."""
    parts = [f"cut {a:.1f}-{b:.1f}" for a, b in edit.get("cuts", [])]
    parts += [f"mute {a:.1f}-{b:.1f}" for a, b in edit.get("mutes", [])]
    if "volume" in edit:
        parts.append(f"volume {round(edit['volume'] * 100)}%")
    for key, words in (("fade_in", "fade in"), ("fade_out", "fade out")):
        if key in edit:
            parts.append(f"{words} {edit[key]:g} s")
    if "speed" in edit:
        parts.append(f"speed {edit['speed']:g}x")
    if "title_overlay" in edit:
        parts.append(f"hook title \"{edit['title_overlay']['text']}\" ({edit['title_overlay']['seconds']:g} s)")
    if "crop" in edit:
        parts.append(f"layout {edit['crop']}")
    return parts


def edit_lines(moments: list[dict], edits: dict) -> list[str]:
    """What `run` prints for an edit run: a line for each clip, with its
    length before and after the suggestion and what it suggests, then the
    reason under it."""
    out = []
    for m in moments:
        head = f"{m['id']:<4} {m['start']:.1f}s-{m['end']:.1f}s"
        entry = edits.get(m["id"])
        if not entry:
            out.append(f"{head}  no suggestion")
            continue
        edit = entry["edit"]
        before = m["end"] - m["start"]
        after = kept_length(m["start"], m["end"], edit.get("cuts", [])) / edit.get("speed", 1)
        size = f"{before:.1f} s" + (f" -> {after:.1f} s" if round(after, 1) != round(before, 1) else "")
        # Plain ASCII between the parts: a console on another code page
        # (932 or 874 on Windows) can't print every character.
        out.append(f"{head}  {size}  {', '.join(edit_parts(edit))}")
        if entry.get("reason"):
            out.append(f"     {entry['reason']}")
    return out


__all__ = ["ALSO_EDITS", "DEFAULT_LAYOUT", "DEFAULT_MAX_DURATION", "DEFAULT_MIN_DURATION", "DEFAULT_MIN_SCORE",
           "DEFAULT_OLLAMA_HOST", "EDIT_STEPS", "LAYOUTS", "NO_OLLAMA_MODEL", "SAMPLE_NEEDS_FFMPEG",
           "SAMPLE_WITHOUT_VIDEO", "Refused", "also_edits", "crops_for", "edit_lines", "edit_parts", "games_for",
           "label_warnings", "manifest_refusal", "models_for", "moments_from", "probe_duration", "read_moments",
           "read_transcript", "report_lines", "run_steps", "sample_moments", "sample_video_wanted",
           "setting_value", "start", "time_limit", "touches_video", "transcript_end", "transcript_from",
           "typed_settings"]
