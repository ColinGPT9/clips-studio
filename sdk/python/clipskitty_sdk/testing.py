# SPDX-License-Identifier: MIT
# Copyright (c) 2026 ColinGPT9. The Clips Kitty SDK; see sdk/python/LICENSE.
"""Testing a plugin: run it the way Clips Kitty would, from a test, and look
at what it answered.

    from pathlib import Path

    from clipskitty_sdk import testing

    PLUGIN = Path(__file__).resolve().parent.parent   # the folder holding clipskitty.yaml


    def test_it_finds_the_quark_burst(tmp_path):
        run = testing.run_plugin(PLUGIN, transcript=testing.sample_transcript(),
                                 duration=testing.SAMPLE_VIDEO_SECONDS, tmp_path=tmp_path)
        assert run.ok, run.error
        assert [m.label for m in run.moments] == ["words_said"]


    def test_it_sees_the_banner(tmp_path):
        video = testing.sample_video(tmp_path)        # skips the test without FFmpeg
        run = testing.run_plugin(PLUGIN, video=video, tmp_path=tmp_path)
        assert run.ok, run.error

`run_plugin` checks the manifest as Clips Kitty does, builds the job folder
with the function Clips Kitty builds it with (clipskitty_sdk.host.build_job),
starts the plugin's run.command (`{python}` is the Python running the
tests), and reads its answer with host.read_result or host.read_answers, the
functions Clips Kitty reads it with. It asks for the run the app would make,
as `python -m clipskitty_sdk run` does (clipskitty_sdk.devrun): a find run
for a plugin that finds moments, else a run that understands and rates.
`make_job` only writes the job folder, for a test that calls the plugin's
own functions on `read_job(folder)`.

sample_transcript() and SAMPLE_VIDEO_SECONDS need nothing, so a test of a
plugin that reads only the transcript runs on any PC, FFmpeg or not. The
sample video needs FFmpeg.

This module is for tests on a developer's PC, never for a plugin's own code
inside Clips Kitty. Standard library only; pytest is imported only when
sample_video() skips a test.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from . import devrun, host, samples
from .contract import MAX_RANGES, ContractError
from .job import RESULT_FILE, Moment, _handed
from .job import _number as _float
from .manifest import MANIFEST_FILE, uses_steps, validate_folder

# The sample's length, 40.0 seconds, and what is said in it (clipskitty_sdk.samples).
SAMPLE_VIDEO_SECONDS = samples.SAMPLE_VIDEO_SECONDS
sample_transcript = samples.sample_transcript
# What sample_video() says when there is no FFmpeg on PATH, outside pytest.
NO_FFMPEG = ("the sample video needs FFmpeg, and there is none on PATH. Install FFmpeg, or add the folder "
             f"holding it to PATH. An installed Clips Kitty has it, in {samples.INSTALLED_FFMPEG}.")
# What run_plugin() and make_job() say for a plugin that reads the video when none is given.
VIDEO_NEEDED = ("this plugin reads the video (video.read), so it needs one: pass video=, such as "
                "video=testing.sample_video(tmp_path)")


class SampleUnavailable(samples.SampleError):
    """sample_video() outside a pytest test, with no FFmpeg on PATH to make the sample."""


@dataclass
class PluginRun:
    """What one run of a plugin did, as Clips Kitty would take it.

    `ok` is True when the plugin finished and Clips Kitty would use its
    answer. Otherwise `error` says why, in words: the plugin's own last error
    line (the words creators see when it fails), else why the run stopped
    (its time limit, its exit code) or why Clips Kitty can't use its answer.

    In a find run, `moments` are its ranges as Clips Kitty takes them from
    result.json (host.read_result: fitted to the video's length, scored ones
    best first, cut to the clip limit), numbered m1, m2... in that order.
    Each `score` is the plugin's own, or None when it gave none; `notes` are
    its notes about the range, in a run asked to understand. In a run that
    understands or rates, `moments` are the moments it was handed, in their
    order, each with the `score` this run gave it (else the one it had) and
    this run's `notes`, and
    `answers` maps each answered moment's id to {score, reason, context}
    (host.read_answers).

    `notes` is the text it left for the job log (result.json's `notes`).
    `events` are its progress, log and error lines, parsed as Clips Kitty
    parses them, and `log` the messages of its log lines (standard error
    included) and any `ignored:` line from reading its answers. `steps` is
    what the run asked for, and `folder` the job folder, which is kept.
    """

    ok: bool = False
    error: str = ""
    moments: list = field(default_factory=list)
    answers: dict = field(default_factory=dict)
    notes: str = ""
    events: list = field(default_factory=list)
    log: list = field(default_factory=list)
    steps: tuple = ()
    folder: Path | None = None


# ---- the sample ---------------------------------------------------------------------


def _under_pytest() -> bool:
    """Whether a pytest test is running in this process."""
    return "pytest" in sys.modules and bool(os.environ.get("PYTEST_CURRENT_TEST"))


def sample_video(folder) -> Path:
    """Make the 40-second sample video (clipskitty_sdk.samples) in `folder`,
    as sample.mp4 with sample.transcript.json beside it, and return its path.

    It needs FFmpeg on PATH. Without it, a pytest test that calls this is
    skipped ("needs FFmpeg"); anywhere else it raises SampleUnavailable. A
    folder inside a plugin's folder is refused (ValueError), because Clips
    Kitty copies everything there on install: use pytest's tmp_path."""
    out = Path(folder) / "sample.mp4"
    if samples.plugin_folder_holding(out) is not None:
        raise ValueError("that is inside a plugin's folder, and Clips Kitty copies everything there on install. "
                         "Make the sample somewhere else, such as pytest's tmp_path")
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        if _under_pytest():
            import pytest

            pytest.skip("needs FFmpeg")
        raise SampleUnavailable(NO_FFMPEG)
    return samples.make_sample(out, ffmpeg)[0]


# ---- the job --------------------------------------------------------------------------


@dataclass
class _Prepared:
    """A run that passed every check: what host.build_job is given, less the job folder."""

    folder: Path
    manifest: dict
    steps: tuple
    duration: float | None
    build: dict


def _refuse(what: str, message: str) -> ContractError:
    return ContractError(what, [message])


def _steps_text(steps) -> str | None:
    if steps is None or isinstance(steps, str):
        return steps
    return ",".join(str(step) for step in steps)


def _transcript(transcript) -> dict:
    if transcript is None:
        return {"language": "", "segments": []}
    if isinstance(transcript, (str, os.PathLike)):
        return devrun.read_transcript(str(transcript))
    if isinstance(transcript, (dict, list)):
        return devrun.transcript_from(transcript)
    raise _refuse("transcript", "expected {language, segments}, a list of segments, or a transcript.json path")


def _moments(moments) -> list[dict]:
    """The moments to hand over, as job.json holds them (devrun.moments_from).
    A Moment (from an earlier run's `moments`) keeps who found it and its
    found score, and its notes from that run join its context."""
    if isinstance(moments, (str, os.PathLike)):
        try:
            return devrun.read_moments(str(moments))
        except devrun.Refused as e:
            raise _refuse("moments", str(e).removeprefix("--moments: ")) from None
    if not isinstance(moments, (list, tuple)):
        raise _refuse("moments", "expected a list of moments, or the path of a JSON file holding one")
    given = [{"start": m.start, "end": m.end, "score": m.score, "label": m.label, "title": m.title,
              "reason": m.reason, "context": [*m.context, *m.notes]} if isinstance(m, Moment) else m
             for m in moments]
    try:
        out = devrun.moments_from(given)
    except devrun.Refused as e:
        raise _refuse("moments", str(e)) from None
    for data, m in zip(out, moments):
        if isinstance(m, Moment):
            data["found_by"] = m.found_by or data["found_by"]
            if m.found_score is not None:
                data["found_score"] = m.found_score
    return out


def _games(games, duration) -> list[dict]:
    names = []
    shaped = []
    for game in games or ():
        if isinstance(game, str):
            names.append(game)
        elif isinstance(game, dict):
            shaped.append(dict(game))
        else:
            raise _refuse("games", "each game is a name, or a dict shaped as video.games holds it")
    return devrun.games_for(names, (), duration) + shaped


def _text_pairs(given, what: str) -> dict[str, str]:
    """{name: text} from a mapping of names to values, as `run`'s
    NAME=VALUE options give them."""
    if given is None:
        return {}
    if not isinstance(given, dict) or not all(isinstance(k, str) and k for k in given):
        raise _refuse(what, "expected a dict of names to values")
    return {k: str(v) for k, v in given.items()}


def _prepare(plugin, *, video, transcript, duration, settings, steps, moments, games, ollama_host=None,
             ollama_model=None, models=None) -> _Prepared:
    """Every check `python -m clipskitty_sdk run` makes before a job folder
    exists, as ContractError."""
    folder = Path(plugin).resolve()
    refusal = devrun.manifest_refusal(folder)
    if refusal:
        raise _refuse(MANIFEST_FILE, refusal)
    manifest, report = validate_folder(folder)
    if not report.ok:
        raise ContractError(MANIFEST_FILE, report.errors)
    perms = set(manifest.get("permissions") or [])
    try:
        asked = devrun.run_steps(manifest, _steps_text(steps))
    except devrun.Refused as e:
        raise _refuse("steps", str(e).removeprefix("--steps: ")) from None
    find_run = "find" in asked
    try:
        host.job_settings(manifest, settings)
    except ValueError as e:
        raise _refuse("settings", str(e)) from None
    try:
        where = devrun.models_for(manifest, _text_pairs(models, "models"))
    except devrun.Refused as e:
        raise _refuse("models", str(e).removeprefix("--model: ")) from None
    for name, value in (("ollama_host", ollama_host), ("ollama_model", ollama_model)):
        if value is not None and not isinstance(value, str):
            raise _refuse(name, "expected text")

    if video is not None:
        video = Path(video).resolve()
        if not video.is_file():
            raise _refuse("video", f"no such video: {video}")
    elif "video.read" in perms:
        raise _refuse("video", VIDEO_NEEDED)
    if duration is not None and not (_float(duration) and duration > 0):
        raise _refuse("duration", "must be a number of seconds above 0")
    said = _transcript(transcript)
    # As `run` does: the video's own length when FFprobe can read it, else `duration`,
    # else (with no video) the end of the transcript.
    length = devrun.probe_duration(shutil.which("ffprobe"), video) if video is not None else None
    if length is None:
        length = float(duration) if duration is not None else None
    if video is None and length is None:
        length = devrun.transcript_end(said)

    limits = {"max_clips": None, "min_duration": devrun.DEFAULT_MIN_DURATION,
              "max_duration": devrun.DEFAULT_MAX_DURATION}
    handed = None
    if not find_run:  # a run that understands or rates; a find run is never handed moments
        limits["min_score"] = devrun.DEFAULT_MIN_SCORE
        if moments is not None:
            handed = _moments(moments)
        else:
            span = length or devrun.transcript_end(said)
            if not span:
                raise _refuse("moments", "no video length for sample moments: pass duration, or moments")
            handed = devrun.sample_moments(span)
        handed = handed[:MAX_RANGES]  # as many as Clips Kitty hands over

    build = {
        "settings": settings,
        "video": None if video is None else {"path": str(video), "id": video.stem, "title": video.stem,
                                             "duration": length, "games": _games(games, length)},
        "transcript": said,
        "limits": limits,
        "focus": None,
        "ffmpeg": shutil.which("ffmpeg"),
        "ffprobe": shutil.which("ffprobe"),
        # As `run` hands them over: no model unless one is named, as when
        # Clips Kitty's AI runs at a cloud provider.
        "ollama": {"host": ollama_host or devrun.DEFAULT_OLLAMA_HOST, "model": ollama_model or ""},
        "models": where,
        "steps": list(asked) if not find_run or uses_steps(manifest) else None,
        "moments": handed,
    }
    return _Prepared(folder, manifest, asked, length, build)


def make_job(folder, plugin, *, video=None, transcript=None, duration=None, settings=None, steps=None,
             moments=None, games=(), ollama_host=None, ollama_model=None, models=None) -> Path:
    """Write the job folder Clips Kitty would make for the plugin in folder
    `plugin`, at `folder`, and return `folder`: `read_job(folder)` then
    gives the plugin's Job.

    `video` is a video file's path, and `transcript` a {language, segments}
    mapping (such as sample_transcript()), a list of segments or the path of
    a transcript.json. Only what the plugin's permissions cover goes in, as
    in the app. `duration` is the video's length when there is no video or
    FFprobe can't read it. `settings` are the creator's choices, by name.
    `steps` asks for `find`, or `understand`, `rate` or both (a list, or
    text with commas); without it the job is the run the app would make.
    `moments` are what a run that understands or rates is handed: Moments
    from an earlier run, or {start, end, score?, label?, title?, reason?,
    context?} mappings; without them it gets 5 sample moments spread through
    the video. `games` are names of games the video shows, for video.games.
    `ollama_model` is the creator's local model, for a plugin with the
    `ollama` permission (`run`'s --ollama-model): without it the job names
    none, as when Clips Kitty's AI runs at a cloud provider, and
    local_model refuses. `ollama_host` is its address (default
    http://localhost:11434). `models` maps a model the manifest lists to
    where it is on this PC (`run`'s --model NAME=PATH). Secret settings are
    never in the job folder: run_plugin's `secrets` hands them over; with
    make_job, set CLIPSKITTY_SECRET_<NAME> in the test's environment (pytest's
    monkeypatch.setenv) before calling job.secret().

    Raises ContractError when Clips Kitty would refuse the plugin's manifest,
    and for anything `python -m clipskitty_sdk run` refuses (a step the
    plugin doesn't offer, a setting it doesn't declare, no video for a plugin
    with video.read), before anything is written."""
    prepared = _prepare(plugin, video=video, transcript=transcript, duration=duration, settings=settings,
                        steps=steps, moments=moments, games=games, ollama_host=ollama_host,
                        ollama_model=ollama_model, models=models)
    folder = Path(folder).resolve()
    job, said = host.build_job(prepared.manifest, output_dir=folder / "out", **prepared.build)
    host.write_job(folder, job, said)
    return folder


# ---- running it --------------------------------------------------------------------------


def _result_notes(job_folder: Path) -> str:
    try:
        notes = json.loads((job_folder / RESULT_FILE).read_text(encoding="utf-8")).get("notes")
    except (OSError, ValueError, AttributeError):
        return ""
    return notes if isinstance(notes, str) else ""


def _found(i: int, r: dict, *, plugin_id: str, notes: bool) -> Moment:
    """A range of a find run's answer as a Moment."""
    score = _float(r.get("score"))
    m = Moment(id=f"m{i}", start=float(r["start"]), end=float(r["end"]), score=score, found_score=score,
               found_by=plugin_id, label=str(r.get("label") or ""), title=str(r.get("title") or ""),
               reason=str(r.get("reason") or ""))
    if notes:
        m._notes.extend(r.get("context") or [])
    return m


def run_plugin(plugin, *, video=None, transcript=None, duration=None, settings=None, steps=None, moments=None,
               tmp_path=None, timeout=None, games=(), ollama_host=None, ollama_model=None, models=None,
               secrets=None) -> PluginRun:
    """Run the plugin in folder `plugin` as Clips Kitty would, and return
    what it did as a PluginRun.

    The inputs are make_job's. `secrets` are the plugin's secret settings by
    name (`run`'s --secret NAME=VALUE), handed over in the environment as
    Clips Kitty hands them over, for job.secret(). The job folder is a new
    folder inside `tmp_path` (pass pytest's, which pytest cleans up), else
    in the system's temporary folder; either way it is kept, in
    `PluginRun.folder`. `timeout` is in seconds; without it the plugin may
    take as long as Clips Kitty would allow (run.timeout_minutes, else 60
    minutes to find and 10 to understand or rate).

    A plugin that fails, or gives an answer Clips Kitty can't use, gives a
    PluginRun with `ok` False and `error` saying why. A run that can't start
    raises ContractError, as make_job does, before any folder is made."""
    prepared = _prepare(plugin, video=video, transcript=transcript, duration=duration, settings=settings,
                        steps=steps, moments=moments, games=games, ollama_host=ollama_host,
                        ollama_model=ollama_model, models=models)
    secrets = _text_pairs(secrets, "secrets")
    if timeout is not None and not (_float(timeout) and timeout > 0):
        raise _refuse("timeout", "must be a number of seconds above 0")
    limit = float(timeout) if timeout is not None else devrun.time_limit(prepared.manifest, prepared.steps)
    if tmp_path is not None:
        Path(tmp_path).mkdir(parents=True, exist_ok=True)
    job_folder = Path(tempfile.mkdtemp(prefix="clipskitty-job-", dir=tmp_path)).resolve()
    job, said = host.build_job(prepared.manifest, output_dir=job_folder / "out", **prepared.build)

    events: list[dict] = []
    outcome = devrun.start(prepared.folder, prepared.manifest, job_folder, job, said,
                           python=host.find_python() or "python", base_env=os.environ, secrets=secrets,
                           timeout=limit, on_event=events.append)
    run = PluginRun(events=events, log=[e["message"] for e in events if e["type"] == "log"],
                    steps=prepared.steps, folder=job_folder)
    if not outcome.ok:
        reported = next((line for line in (e["message"] for e in reversed(events) if e["type"] == "error")
                         if line.strip()), "")
        if outcome.timed_out:
            run.error = (f"it ran past the {limit:g} second timeout" if timeout is not None
                         else f"it ran past its {limit / 60:g} minute limit, as Clips Kitty would stop it")
        else:
            run.error = reported or outcome.error
        return run

    try:
        if "find" in prepared.steps:
            result = host.read_result(job_folder, duration=prepared.duration, max_clips=job["limits"]["max_clips"],
                                      steps=job.get("steps"))
            notes = "understand" in prepared.steps
            run.moments = [_found(i, r, plugin_id=str(prepared.manifest.get("id", "")), notes=notes)
                           for i, r in enumerate(result["ranges"], 1)]
        else:
            handed = job["moments"]
            run.answers, ignored = host.read_answers(job_folder, steps=prepared.steps,
                                                     ids=[m["id"] for m in handed])
            run.log += ignored
            for data in handed:
                m = _handed(data)
                answer = run.answers.get(m.id, {})
                if "score" in answer:
                    m.score = answer["score"]
                m._notes.extend(answer.get("context", []))
                run.moments.append(m)
    except ContractError as e:
        run.error = f"it gave an answer Clips Kitty can't use: {e}"
        return run
    run.ok = True
    run.notes = _result_notes(job_folder)
    return run


__all__ = ["NO_FFMPEG", "SAMPLE_VIDEO_SECONDS", "VIDEO_NEEDED", "PluginRun", "SampleUnavailable", "make_job",
           "run_plugin", "sample_transcript", "sample_video"]
