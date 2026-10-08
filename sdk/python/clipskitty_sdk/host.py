# SPDX-License-Identifier: MIT
# Copyright (c) 2026 ColinGPT9. The Clips Kitty SDK; see sdk/python/LICENSE.
"""Running a plugin: the job folder, the process and the result.

Clips Kitty's engine and `python -m clipskitty_sdk run` both run plugins
through these functions, so a plugin that works under the SDK's runner works
in the app. Standard library only.
"""

from __future__ import annotations

import json
import math
import os
import re
import shutil
import signal
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

from .contract import (
    DEFAULT_TITLE_OVERLAY_SECONDS,
    EDIT_FIELDS,
    FADE_CHOICES,
    HOOK_SECONDS_CHOICES,
    MAX_CONTEXT,
    MAX_EDIT_REASON,
    MAX_TITLE_OVERLAY,
    PLUGIN_API_VERSION,
    SPEED_CHOICES,
    ContractError,
    check_result,
    kept_length,
    merge_spans,
    nearest_choice,
    parse_line,
)
from .job import JOB_FILE, RESULT_FILE, SECRET_PREFIX, one_line
from .manifest import MAX_TIMEOUT_MINUTES, setting_value_problem

SDK_DIR = Path(__file__).resolve().parent.parent  # the folder holding clipskitty_sdk/

# The Python that installed Clips Kitty runs plugins on: its engine's own,
# frozen into the app (_clipskitty_script_host.py). This is the one place the
# version is written. plugins/sources.py and tests/test_plugin_manager.py
# already say "the app builds with 3.11"; scripts/build_installer.py refuses
# to build with another minor version, the SDK's lint parses plugins with this
# grammar, and CI's SDK (Windows) job tests on it.
APP_PYTHON = (3, 11)

# How long a run may take when the manifest's run.timeout_minutes says
# nothing: a run that finds moments, and one that understands or rates the
# moments others found (several can follow one find run). Never longer than
# MAX_TIMEOUT_MINUTES. Clips Kitty's runner uses the same numbers
# (plugins/runner.py; tests/test_plugin_runner.py checks they agree).
FIND_TIMEOUT_MINUTES = 60
MOMENT_TIMEOUT_MINUTES = 10

# Variables a plugin never inherits from Clips Kitty: its own configuration,
# and anything that looks like a credential. The plugin still runs as the
# user, so this keeps Clips Kitty from handing secrets over; it is not a wall.
_CREDENTIAL_WORDS = ("KEY", "TOKEN", "SECRET", "PASSWORD", "PASSWD", "CREDENTIAL", "COOKIE", "AUTH")
# Python start-up settings from the developer's own machine that would make a
# plugin behave differently here than on the Python inside the installed app,
# which ignores them all. PYTHONUTF8 is then set to 0 (plugin_env).
_PYTHON_STARTUP = frozenset({"PYTHONHOME", "PYTHONSTARTUP", "PYTHONINSPECT", "PYTHONUTF8"})


def plugin_env(base: dict, *, job_folder: Path, secrets: dict | None = None, sdk_dir: Path = SDK_DIR,
               python_path: list | None = None) -> dict:
    """The environment a plugin process starts with.

    CLIPSKITTY_SCRIPT_HOST=1 lets the installed app's engine run a {python}
    command itself (main.py, _clipskitty_script_host.py); an ordinary Python
    ignores it. UTF-8 mode is switched off (PYTHONUTF8=0): the app's Python
    never has it, so a plugin that opens text files must pass encoding=, and
    a developer's run here behaves the same way, even on a Python whose UTF-8
    mode is on by default (PEP 686). The frozen app ignores the variable."""
    env = {}
    for name, value in base.items():
        upper = name.upper()
        if upper.startswith(("CLIPS_", "CLIPSKITTY_")):
            continue
        if any(word in upper for word in _CREDENTIAL_WORDS):
            continue
        if upper in _PYTHON_STARTUP:
            continue
        env[name] = value
    env["CLIPSKITTY_JOB"] = str(job_folder)
    env["CLIPSKITTY_SCRIPT_HOST"] = "1"
    env["PYTHONPATH"] = os.pathsep.join([str(sdk_dir), *(str(p) for p in python_path or [])])
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUNBUFFERED"] = "1"
    env["PYTHONUTF8"] = "0"
    for name, value in (secrets or {}).items():
        if value:
            env[SECRET_PREFIX + name.upper().replace("-", "_")] = str(value)
    return env


def timeout_seconds(manifest: dict, default: float = FIND_TIMEOUT_MINUTES,
                    maximum: float = MAX_TIMEOUT_MINUTES) -> float:
    """How long a run may take, in seconds: the manifest's
    run.timeout_minutes, else `default` minutes (MOMENT_TIMEOUT_MINUTES for a
    run that rates or understands moments), at least a minute and never over
    `maximum` minutes. Clips Kitty's runner and `python -m clipskitty_sdk run`
    both use this rule."""
    run = (manifest or {}).get("run")
    minutes = (run.get("timeout_minutes") if isinstance(run, dict) else None) or default
    try:
        minutes = float(minutes)
    except (TypeError, ValueError):
        minutes = default
    return max(1.0, min(float(maximum), minutes)) * 60


def job_settings(manifest: dict, chosen: dict | None) -> dict:
    """The job's settings for a plugin: its manifest's defaults, then the user's.

    A setting the manifest does not declare, or a value that doesn't fit its
    declared type, is refused rather than passed on, and `secret` settings
    never travel this way: they reach the plugin in its environment
    (plugin_env), never in job.json. Raises ValueError.
    """
    declared = manifest.get("settings") or {}
    out = {}
    for name, spec in declared.items():
        if isinstance(spec, dict) and spec.get("type") != "secret" and "default" in spec:
            out[name] = spec["default"]
    for name, value in (chosen or {}).items():
        spec = declared.get(name)
        if not isinstance(spec, dict):
            raise ValueError(f"this pipeline has no setting called '{name}'")
        if spec.get("type") == "secret":
            raise ValueError(f"'{name}' is a secret: set it in the pipeline's settings, not in a job")
        problem = setting_value_problem(spec, value)
        if problem:
            raise ValueError(f"setting '{name}': {problem}")
        out[name] = value
    return out


# What was said in a moment: its title (the hook), why it was picked, and what
# earlier plugins said happens in it. Handed over only with transcript.read.
SAID_IN_A_MOMENT = ("title", "reason", "context")


def _unsaid(suggested) -> dict:
    """An earlier plugin's suggestion for a clip (a moment's `suggested`),
    without its text: its reason and hook title are plugin text that may
    come from what was said."""
    if not isinstance(suggested, dict):
        return suggested
    out = {key: value for key, value in suggested.items() if key != "reason"}
    if isinstance(out.get("edit"), dict):
        out["edit"] = {key: value for key, value in out["edit"].items() if key != "title_overlay"}
    return out


def _moment_for(moment: dict, *, said: bool) -> dict:
    """A copy of one moment for job.json, without what was said in it unless
    `said`: its title, reason and notes, and the reason and hook title of
    what earlier plugins suggested for it."""
    out = {key: (list(value) if isinstance(value, list) else dict(value) if isinstance(value, dict) else value)
           for key, value in moment.items()}
    if not said:
        for key in SAID_IN_A_MOMENT:
            out.pop(key, None)
        if isinstance(out.get("suggested"), list):
            out["suggested"] = [_unsaid(s) for s in out["suggested"]]
    return out


def build_job(manifest: dict, *, settings: dict | None = None, video: dict | None = None,
              transcript: dict | None = None, limits: dict | None = None, focus: str | None = None,
              ffmpeg: str | None = None, ffprobe: str | None = None, ollama: dict | None = None,
              models: dict | None = None, output_dir: Path, steps=None,
              moments: list | None = None) -> tuple[dict, dict | None]:
    """job.json's content, and the transcript to write beside it (or None).

    Only what the manifest's permissions cover goes in: the video with
    `video.read`, the transcript with `transcript.read`, FFmpeg's paths with
    `ffmpeg`, the local model's address with `ollama`. Everything else is left
    out, so a plugin cannot read it from the job folder. `models` is where the
    models its manifest lists are on this PC (the app's plugins/models.py);
    they are its own declarations, so no permission is needed for them.

    `steps` (what the run is asked for) and `moments` (the moments to rate or
    understand, or the clips to suggest edits for) go in only when given, so
    a plugin that uses neither gets the job.json it always did. A moment's
    title, reason and context come from what was said, so they go in only
    with `transcript.read`, and so do the reason and hook title of what
    earlier plugins suggested for it. An edit run's `limits` carry `crops`.
    """
    perms = set(manifest.get("permissions") or [])
    job: dict = {
        "plugin_api": PLUGIN_API_VERSION,
        "plugin": {"id": manifest.get("id", ""), "version": str(manifest.get("version", ""))},
    }
    if steps is not None:
        job["steps"] = [steps] if isinstance(steps, str) else [str(step) for step in steps]
    if moments is not None:
        job["moments"] = [_moment_for(m, said="transcript.read" in perms) for m in moments]
    job.update({
        "settings": job_settings(manifest, settings),
        "limits": {"max_clips": None, "min_duration": None, "max_duration": None, **(limits or {})},
        "focus": focus or None,
        "models": dict(models or {}),
        "tools": {},
        "output_dir": str(output_dir),
    })
    if "video.read" in perms and video:
        job["video"] = dict(video)
    if "ffmpeg" in perms:
        job["tools"]["ffmpeg"] = ffmpeg
        job["tools"]["ffprobe"] = ffprobe
    if "ollama" in perms and ollama is not None:
        job["tools"]["ollama"] = dict(ollama)
    return job, (transcript if "transcript.read" in perms else None)


def write_job(folder: Path, job: dict, transcript: dict | None = None) -> Path:
    """Write job.json (and transcript.json when given) into a fresh job folder."""
    folder.mkdir(parents=True, exist_ok=True)
    data = dict(job)
    data.setdefault("plugin_api", PLUGIN_API_VERSION)
    data.setdefault("output_dir", str(folder / "out"))
    Path(data["output_dir"]).mkdir(parents=True, exist_ok=True)
    if transcript is not None:
        path = folder / "transcript.json"
        path.write_text(json.dumps(transcript, ensure_ascii=False), encoding="utf-8")
        data["transcript"] = {**(data.get("transcript") or {}), "path": str(path),
                              "language": transcript.get("language", "")}
    (folder / JOB_FILE).write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    return folder / JOB_FILE


def resolve_command(command: list[str], python: str, folder: Path | None = None) -> list[str]:
    """The manifest's run.command with {python} replaced and, when `folder` is
    given, a program path made absolute inside the plugin's folder, so it is
    never looked up on PATH."""
    out = [python if part == "{python}" else part for part in command]
    if folder is not None and command and command[0] != "{python}":
        out[0] = str(Path(folder) / command[0])
    return out


def find_python(setting: str | None = None) -> str | None:
    """The Python a plugin runs with when it has no environment of its own:
    the configured one, else this interpreter when it is a real one (not a
    frozen app), else python or py on PATH."""
    if setting:
        return setting
    if not getattr(sys, "frozen", False) and sys.executable:
        return sys.executable
    return shutil.which("python") or shutil.which("py") or shutil.which("python3")


@dataclass
class RunOutcome:
    exit_code: int | None
    error: str = ""
    cancelled: bool = False
    timed_out: bool = False
    lines: list = field(default_factory=list)  # the last lines, for the log

    @property
    def ok(self) -> bool:
        return self.exit_code == 0 and not self.cancelled and not self.timed_out


def _kill_tree(proc: subprocess.Popen) -> None:
    if proc.poll() is not None:
        return
    try:
        if os.name == "nt":
            subprocess.run(["taskkill", "/T", "/F", "/PID", str(proc.pid)], capture_output=True, timeout=30)
        else:
            os.killpg(proc.pid, signal.SIGKILL)
    except Exception:
        proc.kill()


def run_plugin(command: list[str], *, cwd: Path, job_folder: Path, env: dict, timeout: float | None = None,
               on_event=None, should_cancel=None, keep_lines: int = 50) -> RunOutcome:
    """Start the plugin and follow it until it exits, is cancelled or times out.

    `on_event(event)` gets every progress, log and error line as parsed by
    contract.parse_line, standard error included (as log lines).
    `should_cancel()` is asked twice a second; True stops the process tree.
    """
    kwargs = {}
    if os.name == "nt":
        kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP | getattr(subprocess, "CREATE_NO_WINDOW", 0)
    else:
        kwargs["start_new_session"] = True
    try:
        proc = subprocess.Popen(
            [*command, str(job_folder)], cwd=str(cwd), env=env, stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8", errors="replace",
            **kwargs,
        )
    except OSError as e:
        return RunOutcome(None, error=f"could not start the plugin ({command[0]}): {e}")

    outcome = RunOutcome(None)
    last_error = []
    lock = threading.Lock()

    def follow(stream, is_err: bool):
        for line in stream:
            event = parse_line(line) if not is_err else {"type": "log", "message": line.rstrip("\r\n")}
            with lock:
                outcome.lines.append(event["message"])
                del outcome.lines[:-keep_lines]
                if event["type"] == "error":
                    last_error.append(event["message"])
            if on_event is not None:
                try:
                    on_event(event)
                except Exception:
                    pass  # a failing on_event callback must not stop the reader; the run's own lines are still kept

    readers = [threading.Thread(target=follow, args=(proc.stdout, False), daemon=True),
               threading.Thread(target=follow, args=(proc.stderr, True), daemon=True)]
    for t in readers:
        t.start()
    started = time.monotonic()
    while proc.poll() is None:
        if should_cancel is not None and should_cancel():
            outcome.cancelled = True
            _kill_tree(proc)
            break
        if timeout and time.monotonic() - started > timeout:
            outcome.timed_out = True
            _kill_tree(proc)
            break
        time.sleep(0.5)
    proc.wait()
    for t in readers:
        t.join(timeout=5)
    outcome.exit_code = proc.returncode
    if outcome.cancelled:
        outcome.error = "cancelled"
    elif outcome.timed_out:
        outcome.error = f"the plugin took longer than its {round((timeout or 0) / 60)} minute limit"
    elif proc.returncode != 0:
        with lock:
            tail = last_error[-1] if last_error else (outcome.lines[-1] if outcome.lines else "")
        outcome.error = tail or f"the plugin stopped with exit code {proc.returncode}"
    return outcome


_LINK = re.compile(r"(?:https?://|\bwww\.)\S+", re.IGNORECASE)


def _cleaned(text, limit: int) -> str:
    return " ".join(_LINK.sub(" ", one_line(text)).split())[:limit].rstrip()


def clean_note(text) -> str:
    """A note of what happens in a moment, as Clips Kitty keeps it and gives
    it to the AI that writes titles: control characters removed, links
    (http://, https://, www.) taken out, whitespace and newlines collapsed to
    single spaces, and cut to MAX_CONTEXT characters. "" when nothing is left."""
    return _cleaned(text, MAX_CONTEXT)


def _asked(steps) -> set:
    return {steps} if isinstance(steps, str) else {s for s in steps or () if isinstance(s, str)}


def _load_result(job_folder: Path, steps) -> dict:
    """result.json, checked for the steps the run was asked for (None: as a find run's)."""
    path = job_folder / RESULT_FILE
    if not path.exists():
        raise ContractError("result", ["the plugin exited without writing result.json"])
    if not path.is_file():
        # A folder can't be read, and a pipe or a device (a link to
        # /dev/zero) could keep the read going for ever.
        raise ContractError("result", ["result.json is not a file"])
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except ValueError as e:
        raise ContractError("result", [f"result.json is not valid JSON ({e})"]) from e
    except (OSError, RecursionError, MemoryError) as e:
        # Unreadable, nested too deep to parse, or too large: refused like any
        # other answer that can't be used, so a moment run is skipped.
        raise ContractError("result", [f"result.json could not be read ({e or type(e).__name__})"]) from e
    problems = check_result(data, steps=steps)
    if problems:
        raise ContractError("result", problems)
    return data


def read_result(job_folder: Path, *, duration: float | None = None, max_clips: int | None = None,
                steps=None) -> dict:
    """The plugin's result.json, checked, with its ranges fitted to the video.

    Ranges are clamped to [0, duration], those left shorter than a second are
    dropped, scored ranges are kept best first (unscored ones keep the
    plugin's order after them) and the list is cut to `max_clips`.

    `steps` is what the run was asked for (None: checked as a find run's
    answer, as always). In a find run asked to understand, each range's
    notes (`context`) go through clean_note; empty ones are dropped, and the
    key when none are left.
    """
    data = _load_result(job_folder, steps)
    notes_asked = steps is not None and {"find", "understand"} <= _asked(steps)
    fitted = []
    for r in data.get("ranges", []):
        start, end = max(0.0, float(r["start"])), float(r["end"])
        if duration is not None and math.isfinite(duration) and duration > 0:
            end = min(end, float(duration))
        if end - start < 1.0:
            continue
        r = {**r, "start": start, "end": end}
        if notes_asked and "context" in r:
            notes = [n for n in map(clean_note, r["context"] or []) if n]
            if notes:
                r["context"] = notes
            else:
                del r["context"]
        fitted.append(r)
    scored = sorted((r for r in fitted if r.get("score") is not None), key=lambda r: -r["score"])
    unscored = [r for r in fitted if r.get("score") is None]
    ranges = scored + unscored
    if max_clips:
        ranges = ranges[: int(max_clips)]
    return {**data, "ranges": ranges}


def read_answers(job_folder: Path, *, steps, ids) -> tuple[dict, list[str]]:
    """What a run asked to rate or understand moments answered, as Clips Kitty uses it.

    Returns the answers by moment id, and a line for the log for each thing
    ignored. An answer holds `score` (0-100) and `reason` when the run was
    asked to rate and gave a score, and `context`, the notes through
    clean_note with empty ones dropped, when it was asked to understand. A
    moment without a usable answer is left out and keeps what it had.
    Answers for moments not in `ids`, any ranges, and fields the run wasn't
    asked for are ignored. A missing or invalid result.json raises
    ContractError, as in read_result.
    """
    data = _load_result(job_folder, steps)
    asked = _asked(steps)
    rate, understand = "rate" in asked, "understand" in asked
    known = set(ids)
    answers: dict = {}
    ignored: list[str] = []
    unasked_scores = unasked_notes = unasked_edits = False
    given = data.get("moments")
    for a in given if isinstance(given, list) else []:
        if not isinstance(a, dict) or not isinstance(a.get("id"), str):
            continue  # only possible when neither step was asked, so the answers weren't checked
        if a["id"] not in known:
            ignored.append(f"ignored: an answer for {a['id']}, which isn't one of this run's moments")
            continue
        if a.get("edit") is not None and "edit" not in asked:
            unasked_edits = True
        answer: dict = {}
        if a.get("score") is not None or a.get("reason"):
            if not rate:
                unasked_scores = True
            elif a.get("score") is not None:
                answer["score"], answer["reason"] = float(a["score"]), str(a.get("reason") or "")
        if a.get("context"):
            if not understand:
                unasked_notes = True
            else:
                notes = [n for n in map(clean_note, a["context"]) if n]
                if notes:
                    answer["context"] = notes
        if answer:
            answers[a["id"]] = answer
    ranges = data.get("ranges") or []
    if ranges:
        ignored.append(f"ignored: {len(ranges)} range(s): this run was asked about moments, not to find new ones")
    if unasked_scores:
        ignored.append("ignored: scores, because this run wasn't asked to rate")
    if unasked_notes:
        ignored.append("ignored: notes, because this run wasn't asked to understand")
    if unasked_edits:
        ignored.append("ignored: edits, because this run wasn't asked to suggest edits")
    return answers, ignored


# Fields of an edit Clips Kitty never takes from a plugin that have a word of
# their own, with what to write instead. Every other field outside
# contract.EDIT_FIELDS is ignored with one line for the answer.
_EDIT_INSTEAD = {"keep": "write the spans to take out as cuts",
                 "hook": "write the hook title as title_overlay"}


def _fit_spans(mid: str, key: str, spans, window: tuple[float, float], lines: list[str]) -> list[list[float]]:
    """An edit's cuts or mutes clamped to the clip's window and joined where
    they overlap, each in seconds of the video to the millisecond. A span
    wholly outside the clip is dropped with a line."""
    start, end = window
    kept = []
    for a, b in spans:
        a, b = float(a), float(b)
        if b <= start or a >= end:
            lines.append(f"ignored: {mid}'s {key[:-1]} {a:.1f}-{b:.1f} s: it is outside the clip "
                         f"({start:.1f}-{end:.1f} s)")
            continue
        kept.append((max(a, start), min(b, end)))
    return [[round(a, 3), round(b, 3)] for a, b in merge_spans(kept)]


def _moved(given: float, fitted: float) -> bool:
    return abs(given - fitted) > 1e-9


def _fit_edit(mid: str, edit: dict, window: tuple[float, float], crops: tuple, floor: float,
              max_length: float | None, lines: list[str]) -> tuple[dict, str]:
    """One answer's edit as Clips Kitty keeps it, and its cleaned reason."""
    instead = [key for key in edit if key in _EDIT_INSTEAD]
    for key in instead:
        lines.append(f"ignored: {mid}'s {key}: {_EDIT_INSTEAD[key]}")
    others = [str(key) for key in edit if key not in EDIT_FIELDS and key not in _EDIT_INSTEAD]
    if others:
        lines.append(f"ignored: {', '.join(others)} in {mid}'s edit: Clips Kitty doesn't take them from a plugin")
    out: dict = {}
    start, end = window
    length = end - start
    cuts = _fit_spans(mid, "cuts", edit.get("cuts") or [], window, lines)
    if cuts:
        left = kept_length(start, end, cuts)
        if left < floor:
            lines.append(f"ignored: {mid}'s cuts: they would leave {left:.1f} s, under this job's {floor:g} s "
                         "shortest clip")
        else:
            out["cuts"], length = cuts, left
    mutes = _fit_spans(mid, "mutes", edit.get("mutes") or [], window, lines)
    if mutes:
        out["mutes"] = mutes
    if edit.get("volume") is not None:
        volume = round(float(edit["volume"]), 2)  # the editor's slider moves in whole percents
        if volume != 1:
            out["volume"] = volume
    for key in ("fade_in", "fade_out"):
        if edit.get(key) is not None:
            given = float(edit[key])
            fade = nearest_choice(given, FADE_CHOICES)
            if _moved(given, fade):
                lines.append(f"changed: {mid}'s {key} {given:g} s to {fade:g} s, the nearest the editor offers")
            if fade:
                out[key] = fade
    if edit.get("speed") is not None:
        given = float(edit["speed"])
        speed = nearest_choice(given, SPEED_CHOICES, toward=1)
        after = length / speed
        if speed > 1 and after < floor:
            lines.append(f"ignored: {mid}'s speed: at {speed:g}x the clip would be {after:.1f} s, under this job's "
                         f"{floor:g} s shortest clip")
        elif speed < 1 and max_length and after > max_length:
            lines.append(f"ignored: {mid}'s speed: at {speed:g}x the clip would be {after:.1f} s, over this job's "
                         f"{max_length:g} s longest clip")
        else:
            if _moved(given, speed):
                lines.append(f"changed: {mid}'s speed {given:g} to {speed:g}, the nearest the editor offers")
            if speed != 1:
                out["speed"] = speed
    overlay = edit.get("title_overlay")
    if overlay is not None:
        text = _cleaned(overlay["text"], MAX_TITLE_OVERLAY)
        if not text:
            lines.append(f"ignored: {mid}'s title_overlay: no text is left once web addresses and line breaks "
                         "are taken out")
        else:
            seconds = overlay.get("seconds")
            given = float(DEFAULT_TITLE_OVERLAY_SECONDS if seconds is None else seconds)
            fitted = nearest_choice(given, HOOK_SECONDS_CHOICES)
            if _moved(given, fitted):
                lines.append(f"changed: {mid}'s title_overlay seconds {given:g} s to {fitted:g} s, the nearest "
                             "the editor offers")
            out["title_overlay"] = {"text": text, "seconds": fitted}
    crop = edit.get("crop")
    if crop is not None:
        if crop in crops:
            out["crop"] = crop
        elif not crops:
            lines.append(f"ignored: {mid}'s crop \"{one_line(crop)}\": this job's clips don't use a layout")
        else:
            lines.append(f"ignored: {mid}'s crop \"{one_line(crop)}\": this job's clips don't use "
                         f"\"{one_line(crop)}\"")
    reason = _cleaned(edit.get("reason"), MAX_EDIT_REASON) if edit.get("reason") is not None else ""
    return out, reason


def read_edits(job_folder: Path, *, windows: dict, crops, min_length: float,
               max_length: float | None = None) -> tuple[dict, list[str]]:
    """What a run asked to suggest edits answered, as Clips Kitty keeps it.

    `windows` maps each clip's moment id to its (start, end) in seconds of
    the video; `crops` are the layouts this job's clips can use (the job's
    `limits.crops`); `min_length` is the job's shortest clip, at least 1 s;
    `max_length` its longest. Returns each clip's suggestion by moment id,
    {"edit": {...}, "reason": "..."} (no reason when it gave none), and a
    line for the log for each thing ignored or changed.

    The edit is fitted to the clip and to the timeline editor. Cuts and
    mutes are clamped to the clip and joined where they overlap; a span
    wholly outside the clip is dropped. Cuts that would leave less than
    `min_length` (pieces under contract.MIN_PIECE don't count) are ignored,
    and a speed that would take the clip under it, or past `max_length`.
    Volume is rounded to a whole percent. Fades, speed and the hook title's
    seconds are set to the nearest of the editor's choices (FADE_CHOICES,
    SPEED_CHOICES, HOOK_SECONDS_CHOICES), with a `changed:` line when one
    moves. The hook title is put on one line, without web addresses that
    start with http://, https:// or www., and cut to MAX_TITLE_OVERLAY
    characters. A crop not in `crops` is ignored. Values that change nothing
    (volume 1, fades 0, speed 1) are left out, and an edit left with nothing
    is ignored. Fields contract.EDIT_FIELDS doesn't name are ignored with a
    line, as are answers for other moments, ranges, scores and notes. A
    missing or invalid result.json raises ContractError, as in read_result.
    """
    data = _load_result(job_folder, ["edit"])
    floor = max(1.0, float(min_length or 0))
    crops = tuple(crops or ())
    edits: dict = {}
    lines: list[str] = []
    unasked_scores = unasked_notes = False
    given = data.get("moments")
    for a in given if isinstance(given, list) else []:
        mid = a["id"]  # checked: every answer is an object with an id
        if mid not in windows:
            lines.append(f"ignored: an answer for {mid}, which isn't one of this run's moments")
            continue
        unasked_scores = unasked_scores or a.get("score") is not None or bool(a.get("reason"))
        unasked_notes = unasked_notes or bool(a.get("context"))
        if a.get("edit") is None:
            continue
        start, end = windows[mid]
        fitted, reason = _fit_edit(mid, a["edit"], (float(start), float(end)), crops, floor, max_length, lines)
        if not fitted:
            lines.append(f"ignored: {mid}'s edit changes nothing")
            continue
        edits[mid] = {"edit": fitted, **({"reason": reason} if reason else {})}
    ranges = data.get("ranges") or []
    if ranges:
        lines.append(f"ignored: {len(ranges)} range(s): this run was asked about moments, not to find new ones")
    if unasked_scores:
        lines.append("ignored: scores, because this run wasn't asked to rate")
    if unasked_notes:
        lines.append("ignored: notes, because this run wasn't asked to understand")
    return edits, lines
