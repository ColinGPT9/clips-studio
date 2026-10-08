"""One plugin run for one video: the hand-over and the answer.

process_video calls find_clips() at its detection step when the job names a
pipeline plugin. Everything before (download, transcription) and after
(titles, rendering, captions, the library) is Clips Kitty's, unchanged.
answer_moments() is the other kind of run: a plugin chosen under Rate &
understand is handed the moments once they are found (plugins/steps.py) and
answers about each one. Its failures carry a `why` in the creator's words.

What the plugin receives depends on what its manifest asks for: the video
only with `video.read`, the transcript only with `transcript.read`, FFmpeg's
path only with `ffmpeg`, the local AI model only with `ollama`. That much is
enforced here, because the job folder simply does not contain the rest. What
the plugin does beyond it is not: it runs with the user's own rights, as any
program they install does.
"""

from __future__ import annotations

import logging
import os
import re
import shutil
import sys
import time
from pathlib import Path

from plugins import models as plugin_models
from plugins import store
from plugins._sdk import contract, host
from plugins._sdk import manifest as plugin_manifest

log = logging.getLogger(__name__)

# Job folders of earlier runs kept for a look when something went wrong.
KEEP_RUNS = 5
DEFAULT_TIMEOUT_MINUTES = 60
# A run that rates or understands the moments others found, without a
# run.timeout_minutes of its own. Several can follow one find run.
MOMENT_TIMEOUT_MINUTES = 10
MAX_TIMEOUT_MINUTES = 24 * 60
# After a run that ended without the plugin's own error line. The engine's log
# (the plugin's output and the exit code) goes with a bug report.
TRY_AGAIN = "Try again; if it happens again, send a bug report from Feedback (it includes the details)."


class PluginError(RuntimeError):
    """A plugin run that could not give an answer. The message is for the user
    in a find run. In a run that rates or understands moments it is for the
    log, and `why` says what happened in the creator's words, without the
    plugin's name (the line around it names it). `code` says which check
    stopped a run before it started: a ChoiceProblem's code, or "command",
    "python" or "model"."""

    def __init__(self, message: str = "", *, code: str | None = None, why: str = ""):
        super().__init__(message)
        self.code = code
        self.why = why


def _safe(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9_-]+", "_", text)[:60] or "video"


def _prune_runs(runs: Path) -> None:
    try:
        folders = sorted((p for p in runs.iterdir() if p.is_dir()), key=lambda p: p.stat().st_mtime)
    except OSError:
        return
    for old in folders[:-KEEP_RUNS]:
        shutil.rmtree(old, ignore_errors=True)


def settings_for(manifest: dict, chosen: dict | None) -> dict:
    """The job's settings for a plugin (host.job_settings), with its refusal
    as a PluginError for the user."""
    try:
        return host.job_settings(manifest, chosen)
    except ValueError as e:
        raise PluginError(str(e)) from e


def secret_name(plugin_id: str) -> str:
    return "plugin." + plugin_id.replace("/", ".")


def secrets_for(plugin: store.Installed, data_dir) -> dict:
    """The plugin's `secret` settings from Clips Kitty's secrets store."""
    declared = [n for n, s in (plugin.manifest.get("settings") or {}).items()
                if isinstance(s, dict) and s.get("type") == "secret"]
    if not declared:
        return {}
    from core import secrets

    stored = secrets.load(Path(data_dir), secret_name(plugin.id)) or {}
    return {name: stored[name] for name in declared if stored.get(name)}


def transcript_of(segments, language: str) -> dict:
    """The transcript in the contract's shape (transcript.json)."""
    return {
        "language": language or "",
        "segments": [
            {"start": float(s.start), "end": float(s.end), "text": s.text, "words": s.words}
            for s in segments
        ],
    }


def build_job(plugin: store.Installed, choice: dict, *, video, segments, language: str, config: dict,
              output_dir: Path, models: dict | None = None, steps=None, moments: list | None = None,
              min_score=None) -> tuple[dict, dict | None]:
    """job.json's content, and the transcript to write beside it (or None).
    host.build_job decides what the plugin's permissions let in.

    `steps` and `moments` go in only when given, and `limits.min_score` only
    when `min_score` is, so a plain finder's job is what it always was."""
    perms = set(plugin.manifest.get("permissions") or [])
    clips_cfg = config.get("clips") or {}
    limits = {"max_clips": int(clips_cfg.get("max_clips_per_video") or 0) or None,
              "min_duration": clips_cfg.get("min_duration"),
              "max_duration": clips_cfg.get("max_duration")}
    if min_score is not None:
        limits["min_score"] = min_score
    tools: dict = {}
    if "ffmpeg" in perms:
        from core.binaries import ffmpeg, ffprobe

        tools = {"ffmpeg": ffmpeg(), "ffprobe": ffprobe()}
    # The local model the user chose, when the AI runs on this PC; no model
    # when it runs at a cloud provider, whose key is never handed over.
    llm = config.get("llm") or {}
    provider, _, model = str(llm.get("backend") or "").partition("/")
    ollama = {"host": llm.get("ollama_host", "http://localhost:11434"),
              "model": model if provider == "ollama" else ""}
    try:
        job, transcript = host.build_job(
            plugin.manifest,
            settings=choice.get("settings"),
            video={"path": str(video.path), "id": video.video_id, "title": video.title,
                   "duration": float(video.duration or 0) or None, "games": list(video.games or [])},
            transcript=transcript_of(segments, language),
            limits=limits,
            focus=clips_cfg.get("focus") or None,
            ollama=ollama, models=models, output_dir=output_dir, steps=steps, moments=moments, **tools,
        )
    except ValueError as e:
        raise PluginError(str(e)) from e
    job["plugin"] = {"id": plugin.id, "version": plugin.version}  # as installed
    return job, transcript


def _score_for(rank: int, score) -> int:
    """A clip's score: the plugin's own, or from its order when it gave none
    (90 for the first, 5 less for each after, never under 50)."""
    if score is not None:
        return int(round(float(score)))
    return max(50, 90 - 5 * rank)


def to_candidates(plugin: store.Installed, ranges: list[dict], *, notes: bool = False) -> list:
    """The ranges of a find run as ClipCandidates.

    With `notes` (a run asked to find and understand, whose notes
    host.read_result has checked and cleaned), a range's `context` is kept as
    the clip's `plugin_notes`, with who said it. Every other clip gets the same
    four plugin subscores as always."""
    from core.models import ClipCandidate

    out = []
    for rank, r in enumerate(ranges):
        label = r.get("label") or ""
        subscores = {"plugin": plugin.id, "plugin_version": plugin.version,
                     "plugin_label": label, "plugin_why": r.get("reason") or ""}
        if notes and r.get("context"):
            subscores["plugin_notes"] = [{"plugin": plugin.id, "version": plugin.version, "name": plugin.name,
                                          "text": text} for text in r["context"]]
        out.append(ClipCandidate(
            start=float(r["start"]), end=float(r["end"]), score=_score_for(rank, r.get("score")),
            hook=r.get("title") or label, reason=r.get("reason") or "",
            source=f"plugin:{plugin.id}@{plugin.version}",
            subscores=subscores,
        ))
    return out


def timeout_seconds(manifest: dict, default: float = DEFAULT_TIMEOUT_MINUTES) -> float:
    """How long a run may take: the manifest's run.timeout_minutes, else
    `default` minutes (MOMENT_TIMEOUT_MINUTES for a run that rates or
    understands moments), never over MAX_TIMEOUT_MINUTES.

    The rule is the SDK's (host.timeout_seconds), so `python -m clipskitty_sdk
    run` stops a plugin when Clips Kitty would. The runner calls this name,
    so a caller can still replace it (scripts/check_compatibility.py caps it,
    and a test shortens it)."""
    return host.timeout_seconds(manifest, default, MAX_TIMEOUT_MINUTES)


def python_for(plugin, config: dict | None) -> str | None:
    """The Python a plugin's {python} runs with.

    The plugin's own (none yet), else the plugins.python setting (a
    developer's choice), else, in the installed app, the app's own Python:
    the engine itself, which runs the script when started with the plugin's
    environment (main.py, _clipskitty_script_host.py). Only a source checkout
    falls back to this interpreter or one on PATH, so the installed app never
    needs a Python of the creator's and never picks Windows' "python" shortcut
    to the Store."""
    own = getattr(plugin, "python", None) if plugin is not None else None
    setting = ((config or {}).get("plugins") or {}).get("python")
    if own or setting:
        return own or setting
    if getattr(sys, "frozen", False) and sys.executable:
        return sys.executable
    return host.find_python(None)


def _failure(plugin, outcome, reported: list[str]) -> str:
    """Why a run failed, for the user: the plugin's own last error line (its
    words for the user, as the contract says), else a plain sentence. What the
    process said and its exit code go to the log, never into the message."""
    if outcome.timed_out:
        minutes = round(timeout_seconds(plugin.manifest) / 60)
        return f"{plugin.name} took longer than its {minutes} minute limit, so Clips Kitty stopped it."
    if reported:
        return f"{plugin.name} failed: {reported[-1]}"
    if outcome.exit_code is None:
        log.warning("%s %s could not be started: %s", plugin.id, plugin.version, outcome.error)
        return f"Clips Kitty couldn't start {plugin.name}. {TRY_AGAIN}"
    log.warning("%s %s stopped with exit code %s: %s", plugin.id, plugin.version, outcome.exit_code,
                outcome.error)
    return f"{plugin.name} stopped before it finished. {TRY_AGAIN}"


def _prepare(choice: dict, *, data_dir, config: dict, step: str):
    """The plugin a job's choice names, checked before anything runs: installed,
    turned on, able to do `step`, a command to run, a Python for it, and every
    model it lists on this PC. Returns (plugin, command, python, model paths).
    Raises PluginError with a message for the user."""
    try:
        plugin = store.installed_choice(data_dir, store.clean_choice(choice), step=step)
    except ValueError as e:
        raise PluginError(str(e)[:1].upper() + str(e)[1:], code=getattr(e, "code", None)) from e
    run = plugin.manifest.get("run") or {}
    command = run.get("command")
    if not isinstance(command, list) or not command or not all(isinstance(p, str) for p in command):
        raise PluginError(f"{plugin.name} has no command to run in its manifest", code="command")
    python = python_for(plugin, config)
    if "{python}" in command and not python:
        # Only a source checkout gets here: the installed app runs it on its own Python.
        raise PluginError(f"{plugin.name} needs Python 3.10 or newer, and none was found on this PC. "
                          "Install Python, or set plugins.python in settings.yaml.", code="python")

    # Every model it lists must be here before it starts (an Ollama model
    # with Ollama not answering can't be checked, and is let through).
    ollama_host = (config.get("llm") or {}).get("ollama_host")
    try:
        model_paths, missing = plugin_models.for_job(data_dir, plugin.manifest, ollama_host=ollama_host)
    except plugin_models.ModelError as e:
        raise PluginError(f"{plugin.name} can't run: {e}", code="model") from e
    if missing:
        raise PluginError(f"{plugin.name} can't run. " + " ".join(missing), code="model")
    return plugin, command, python, model_paths


def _new_folder(data_dir, video_id: str, tag: str = "") -> Path:
    """A new job folder under runs/: <video id>-<date and time>, then
    -<tag> when given (a moment run's steps, such as understand-rate), and
    ~2, ~3 and so on when that name is taken. Not created yet."""
    runs = store.root(data_dir) / "runs"
    folder = runs / f"{_safe(video_id)}-{time.strftime('%Y%m%d-%H%M%S')}{'-' + tag if tag else ''}"
    n = 1
    while folder.exists():
        n += 1
        folder = folder.with_name(f"{folder.name.rsplit('~', 1)[0]}~{n}")
    return folder


def _execute(plugin: store.Installed, command: list, python: str | None, folder: Path, *, video, data_dir,
             stage: str, timeout: float, label_name: str | None = None, failure=None) -> None:
    """Run the plugin on its written job folder until it exits, is cancelled
    or runs out of time (`timeout`, in seconds).

    Its progress lines become `stage` events for the video, each with
    `plugin=label_name` when that is given, and its messages go to the job
    log. Afterwards only the newest KEEP_RUNS job folders are kept. Raises
    core.cancel.CancelledError when the job is cancelled, and PluginError
    when the run didn't end well: `failure(outcome, reported)` words it when
    given (a moment run), else _failure does, as for every find run."""
    from core import cancel, progress

    reported: list[str] = []
    named = {"plugin": label_name} if label_name else {}

    def on_event(event: dict) -> None:
        if event["type"] == "error" and event.get("message"):
            reported.append(event["message"])
        if event["type"] == "progress" and event.get("fraction") is not None:
            progress.emit(stage=stage, video_id=video.video_id, fraction=event["fraction"],
                          message=event.get("message", ""), **named)
        if event.get("message"):
            print(f"      [{plugin.id}] {event['message']}")

    from plugins._sdk import sdk_dir

    env = host.plugin_env(os.environ, job_folder=folder, secrets=secrets_for(plugin, data_dir), sdk_dir=sdk_dir())
    outcome = host.run_plugin(
        host.resolve_command(command, python or "", plugin.folder), cwd=plugin.folder, job_folder=folder, env=env,
        timeout=timeout, on_event=on_event,
        should_cancel=lambda: cancel.is_cancelled(video.video_id),
    )
    _prune_runs(store.root(data_dir) / "runs")
    if outcome.cancelled:
        raise cancel.CancelledError(video.video_id)
    if not outcome.ok:
        if failure is not None:
            raise failure(outcome, reported)
        raise PluginError(_failure(plugin, outcome, reported))


def find_clips(choice: dict, *, video, segments, language: str, config: dict, data_dir) -> list:
    """Ask the job's plugin for the video's moments, as ClipCandidates.

    A plugin that also says what happens in the moments it finds (outputs
    `ranges` and `context`) is asked to find and understand: its notes are
    checked and cleaned as they are read, and each clip keeps them as
    `plugin_notes`.

    Raises PluginError with a message for the user when the plugin can't run
    or gives no valid answer, and core.cancel.CancelledError when the job is
    cancelled while it runs.
    """
    from core import progress

    plugin, command, python, model_paths = _prepare(choice, data_dir=data_dir, config=config, step="find")
    # job.json says which steps the run is asked for only when the manifest
    # uses the step words, so a plain finder's job is what it always was.
    steps = plugin_manifest.find_steps(plugin.manifest) if plugin_manifest.uses_steps(plugin.manifest) else None
    folder = _new_folder(data_dir, video.video_id)
    job, transcript = build_job(plugin, choice, video=video, segments=segments, language=language,
                                config=config, output_dir=folder / "out", models=model_paths, steps=steps)
    host.write_job(folder, job, transcript)

    print(f"      Pipeline: {plugin.name} {plugin.version} ({plugin.id})")
    progress.emit(stage="analyze", video_id=video.video_id, fraction=0.0, message=f"Running {plugin.name}")
    _execute(plugin, command, python, folder, video=video, data_dir=data_dir, stage="analyze",
             timeout=timeout_seconds(plugin.manifest))
    try:
        result = host.read_result(folder, duration=video.duration,
                                  max_clips=job["limits"]["max_clips"], steps=steps)
    except contract.ContractError as e:
        raise PluginError(f"{plugin.name} gave an answer Clips Kitty can't use: {e}") from e
    if result.get("notes"):
        print(f"      [{plugin.id}] {result['notes']}")
    progress.emit(stage="analyze", video_id=video.video_id, fraction=1.0,
                  message=f"{plugin.name} found {len(result['ranges'])} moment(s)")
    return to_candidates(plugin, result["ranges"], notes="understand" in (steps or ()))


# ---- a run that rates or understands the moments others found -------------------

# Why a moment run didn't happen, in the creator's words (the video page
# shows "Clips Kitty made these clips without {name}. {why}"). None names the
# plugin, and none asks for a bug report about someone else's plugin.
_NOT_READY = {
    "missing": "It isn't installed any more.",
    "off": "It's turned off in Marketplace › Installed.",
    "incompatible": "It can't run on this version of Clips Kitty.",
    "command": "Its files are damaged. Install it again.",
    "python": "It needs Python, and none was found on this PC.",
    "model": "A model it needs isn't on this PC. Get it in Marketplace › Installed.",
}
# The plugin's own error line, cut to this many characters in the creator's sentence.
MAX_SAID = 300


def _sentence(text: str) -> str:
    """`text` ending in one full stop (or its own ! or ?), never two."""
    text = " ".join(str(text or "").split()).rstrip()
    return text if text.endswith((".", "!", "?", "…")) else text + "."


def _why_not_ready(e: PluginError, step: str) -> str:
    """Why a moment run couldn't start (_prepare), for the creator."""
    cause = e.__cause__
    if e.code == "blocked":
        reason = getattr(cause, "detail", "").strip().rstrip(".").strip()
        return _sentence(f"It was blocked: {reason}" if reason else "It was blocked")
    if e.code == "step":
        return f"It can no longer {step} moments."
    if e.code == "settings":
        detail = getattr(cause, "detail", "") or str(cause or "")
        return _sentence(f"A setting chosen for it no longer fits: {detail.strip().rstrip('.')}")
    # A choice that isn't even shaped like one (a hand-written settings.yaml).
    return _NOT_READY.get(e.code or "", "Clips Kitty couldn't read how it was chosen.")


def _moment_failure(plugin: store.Installed, outcome, reported: list[str], timeout: float) -> PluginError:
    """A moment run that didn't end well: the log's message, and its `why`."""
    if outcome.timed_out:
        minutes = max(1, round(timeout / 60))
        stopped = f"took longer than its {minutes} minute limit, so Clips Kitty stopped it."
        return PluginError(f"{plugin.name} {stopped}", why=f"It {stopped}")
    # The last error line it reported with words in it: a blank one (" ")
    # says nothing, and would read "It said: ." on the video page.
    said = next((line for line in (" ".join(r.split()) for r in reversed(reported)) if line), "")
    if said:
        return PluginError(f"{plugin.name} failed: {said}", why="It said: " + _sentence(said[:MAX_SAID]))
    if outcome.exit_code is None:
        return PluginError(f"Clips Kitty couldn't start {plugin.name}: {outcome.error}",
                           why="Clips Kitty couldn't start it.")
    return PluginError(f"{plugin.name} stopped with exit code {outcome.exit_code}: {outcome.error}",
                       why="It stopped before it finished.")


def _result_notes(folder: Path) -> str:
    """The `notes` of an answer read_answers has already checked."""
    import json

    try:
        notes = json.loads((folder / "result.json").read_text(encoding="utf-8")).get("notes")
    except (OSError, ValueError, AttributeError, RecursionError, MemoryError):
        return ""
    return notes if isinstance(notes, str) else ""


def answer_moments(choice, steps, moments: list[dict], *, video, segments, language: str, config: dict,
                   data_dir, stage: str) -> dict:
    """Ask a plugin chosen under Rate & understand about the moments found.

    `steps` is what the run is asked for (understand, rate or both) and
    `moments` the moments as job.json hands them over (plugins/steps.py
    builds them). The run goes like a find run: the same checks, Python,
    models, environment and secrets, with a job folder named after its steps,
    the job's own clip limit and the creator's minimum score, and progress
    reported as `stage` with the plugin's name. The answer is read with
    host.read_answers, the function the SDK's runner uses too.

    Returns {plugin, version, name, steps, answers, ignored}: the answers by
    moment id, and the log's lines for what was ignored. Raises PluginError
    with a `why` for the creator when it can't run or gives no usable answer,
    and core.cancel.CancelledError when the job is cancelled while it runs.
    """
    from core import progress

    asked = [step for step in ("understand", "rate") if step in (steps or ())]
    if not asked:
        raise ValueError("a moment run is asked to understand, to rate, or both")
    for step in asked:
        try:
            plugin, command, python, model_paths = _prepare(choice, data_dir=data_dir, config=config,
                                                            step=step)
        except PluginError as e:
            raise PluginError(str(e), code=e.code, why=_why_not_ready(e, step)) from e
    choice = store.clean_choice(choice)  # as _prepare found it
    folder = _new_folder(data_dir, video.video_id, "-".join(asked))
    min_score = int((config.get("clips") or {}).get("min_score", 0))
    try:
        job, transcript = build_job(plugin, choice, video=video, segments=segments, language=language,
                                    config=config, output_dir=folder / "out", models=model_paths,
                                    steps=asked, moments=moments, min_score=min_score)
    except PluginError as e:
        detail = str(e).rstrip(".")
        raise PluginError(str(e), code="settings",
                          why=_sentence(f"A setting chosen for it no longer fits: {detail}")) from e
    host.write_job(folder, job, transcript)

    timeout = timeout_seconds(plugin.manifest, default=MOMENT_TIMEOUT_MINUTES)
    progress.emit(stage=stage, video_id=video.video_id, fraction=0.0, message=f"Running {plugin.name}",
                  plugin=plugin.name)
    _execute(plugin, command, python, folder, video=video, data_dir=data_dir, stage=stage, timeout=timeout,
             label_name=plugin.name,
             failure=lambda outcome, reported: _moment_failure(plugin, outcome, reported, timeout))
    try:
        answers, ignored = host.read_answers(folder, steps=asked, ids=[m["id"] for m in moments])
    except contract.ContractError as e:
        raise PluginError(f"{plugin.name} gave an answer Clips Kitty can't use: {e}",
                          why="Clips Kitty couldn't use its answer.") from e
    except Exception as e:
        # Anything else reading it raised: still an answer that can't be used,
        # so this run is skipped and the job goes on without it.
        raise PluginError(f"{plugin.name} gave an answer Clips Kitty couldn't read: {type(e).__name__}: {e}",
                          why="Clips Kitty couldn't use its answer.") from e
    notes = _result_notes(folder)
    if notes:
        print(f"      [{plugin.id}] {notes}")
    for line in ignored:
        print(f"      [{plugin.id}] {line}")
    progress.emit(stage=stage, video_id=video.video_id, fraction=1.0,
                  message=f"{plugin.name} answered about {len(answers)} moment(s)", plugin=plugin.name)
    return {"plugin": plugin.id, "version": plugin.version, "name": plugin.name, "steps": asked,
            "answers": answers, "ignored": ignored}
