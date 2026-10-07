"""One plugin run for one video: the hand-over and the answer.

process_video calls find_clips() at its detection step when the job names a
pipeline plugin. Everything before (download, transcription) and after
(titles, rendering, captions, the library) is Clips Kitty's, unchanged.

What the plugin receives depends on what its manifest asks for: the video
only with `video.read`, the transcript only with `transcript.read`, FFmpeg's
path only with `ffmpeg`, the local AI model only with `ollama`. That much is
enforced here, because the job folder simply does not contain the rest. What
the plugin does beyond it is not: it runs with the user's own rights, as any
program they install does.
"""

from __future__ import annotations

import os
import re
import shutil
import sys
import time
from pathlib import Path

from plugins import models as plugin_models
from plugins import store
from plugins._sdk import contract, host

# Job folders of earlier runs kept for a look when something went wrong.
KEEP_RUNS = 5
DEFAULT_TIMEOUT_MINUTES = 60
MAX_TIMEOUT_MINUTES = 24 * 60


class PluginError(RuntimeError):
    """A plugin run that could not give an answer. The message is for the user."""


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
              output_dir: Path, models: dict | None = None) -> tuple[dict, dict | None]:
    """job.json's content, and the transcript to write beside it (or None).
    host.build_job decides what the plugin's permissions let in."""
    perms = set(plugin.manifest.get("permissions") or [])
    clips_cfg = config.get("clips") or {}
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
            limits={"max_clips": int(clips_cfg.get("max_clips_per_video") or 0) or None,
                    "min_duration": clips_cfg.get("min_duration"),
                    "max_duration": clips_cfg.get("max_duration")},
            focus=clips_cfg.get("focus") or None,
            ollama=ollama, models=models, output_dir=output_dir, **tools,
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


def to_candidates(plugin: store.Installed, ranges: list[dict]) -> list:
    from core.models import ClipCandidate

    out = []
    for rank, r in enumerate(ranges):
        label = r.get("label") or ""
        out.append(ClipCandidate(
            start=float(r["start"]), end=float(r["end"]), score=_score_for(rank, r.get("score")),
            hook=r.get("title") or label, reason=r.get("reason") or "",
            source=f"plugin:{plugin.id}@{plugin.version}",
            subscores={"plugin": plugin.id, "plugin_version": plugin.version,
                       "plugin_label": label, "plugin_why": r.get("reason") or ""},
        ))
    return out


def timeout_seconds(manifest: dict) -> float:
    minutes = (manifest.get("run") or {}).get("timeout_minutes") or DEFAULT_TIMEOUT_MINUTES
    try:
        minutes = float(minutes)
    except (TypeError, ValueError):
        minutes = DEFAULT_TIMEOUT_MINUTES
    return max(1.0, min(float(MAX_TIMEOUT_MINUTES), minutes)) * 60


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


def find_clips(choice: dict, *, video, segments, language: str, config: dict, data_dir) -> list:
    """Ask the job's plugin for the video's moments, as ClipCandidates.

    Raises PluginError with a message for the user when the plugin can't run
    or gives no valid answer, and core.cancel.CancelledError when the job is
    cancelled while it runs.
    """
    from core import cancel, progress

    try:
        plugin = store.installed_choice(data_dir, store.clean_choice(choice))
    except ValueError as e:
        raise PluginError(str(e)[:1].upper() + str(e)[1:]) from e
    run = plugin.manifest.get("run") or {}
    command = run.get("command")
    if not isinstance(command, list) or not command or not all(isinstance(p, str) for p in command):
        raise PluginError(f"{plugin.name} has no command to run in its manifest")
    python = python_for(plugin, config)
    if "{python}" in command and not python:
        raise PluginError(f"{plugin.name} needs Python 3.10 or newer, and none was found on this PC. "
                          "Install Python, or set plugins.python in settings.yaml.")

    # Every model it lists must be here before it starts (an Ollama model
    # with Ollama not answering can't be checked, and is let through).
    ollama_host = (config.get("llm") or {}).get("ollama_host")
    try:
        model_paths, missing = plugin_models.for_job(data_dir, plugin.manifest, ollama_host=ollama_host)
    except plugin_models.ModelError as e:
        raise PluginError(f"{plugin.name} can't run: {e}") from e
    if missing:
        raise PluginError(f"{plugin.name} can't run: " + "; ".join(missing))

    runs = store.root(data_dir) / "runs"
    folder = runs / f"{_safe(video.video_id)}-{time.strftime('%Y%m%d-%H%M%S')}"
    n = 1
    while folder.exists():
        n += 1
        folder = folder.with_name(f"{folder.name.rsplit('~', 1)[0]}~{n}")
    job, transcript = build_job(plugin, choice, video=video, segments=segments, language=language,
                                config=config, output_dir=folder / "out", models=model_paths)
    host.write_job(folder, job, transcript)

    print(f"      Pipeline: {plugin.name} {plugin.version} ({plugin.id})")
    progress.emit(stage="analyze", video_id=video.video_id, fraction=0.0, message=f"Running {plugin.name}")

    def on_event(event: dict) -> None:
        if event["type"] == "progress" and event.get("fraction") is not None:
            progress.emit(stage="analyze", video_id=video.video_id, fraction=event["fraction"],
                          message=event.get("message", ""))
        if event.get("message"):
            print(f"      [{plugin.id}] {event['message']}")

    from plugins._sdk import sdk_dir

    env = host.plugin_env(os.environ, job_folder=folder, secrets=secrets_for(plugin, data_dir), sdk_dir=sdk_dir())
    outcome = host.run_plugin(
        host.resolve_command(command, python or "", plugin.folder), cwd=plugin.folder, job_folder=folder, env=env,
        timeout=timeout_seconds(plugin.manifest), on_event=on_event,
        should_cancel=lambda: cancel.is_cancelled(video.video_id),
    )
    _prune_runs(runs)
    if outcome.cancelled:
        raise cancel.CancelledError(video.video_id)
    if not outcome.ok:
        raise PluginError(f"{plugin.name} failed: {outcome.error}")
    try:
        result = host.read_result(folder, duration=video.duration,
                                  max_clips=job["limits"]["max_clips"])
    except contract.ContractError as e:
        raise PluginError(f"{plugin.name} gave an answer Clips Kitty can't use: {e}") from e
    if result.get("notes"):
        print(f"      [{plugin.id}] {result['notes']}")
    progress.emit(stage="analyze", video_id=video.video_id, fraction=1.0,
                  message=f"{plugin.name} found {len(result['ranges'])} moment(s)")
    return to_candidates(plugin, result["ranges"])
