# SPDX-License-Identifier: MIT
# Copyright (c) 2026 ColinGPT9. The Clips Kitty SDK; see sdk/python/LICENSE.
"""What a plugin's own code uses: read the job, report progress, answer.

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

A plugin that rates the moments others found, or says what happens in them,
gets those moments in `job.moments` and answers about each one:

    def main(job):
        for m in job.moments:                     # found by Clips Kitty or a pipeline
            said = job.text(m).lower()            # what is said during the moment
            if "quark burst" in said:
                job.rate(m, min(100, m.score + 15), reason="the caster called a big play")
                job.understand(m, "The caster calls a quark burst here")

`job.settings` holds the plugin's settings. A setting with no value (not
declared, or declared without a default and left empty by the creator)
raises SettingMissing; `job.settings.get(name, default)` never does.

A plugin that suggests edits gets the clips Clips Kitty is about to make in
`job.moments` too, in a run of its own, and suggests an edit for each one it
wants to change. Times are seconds of the video:

    def main(job):
        for m in job.moments:                     # the clips Clips Kitty will make
            s = job.suggest_edit(m)
            s.trim(m.start + 2).fade(fade_out=0.5).title_overlay("Triple bloom!")
            s.reason("Starts on the action")

Clips Kitty keeps the suggestion for the creator, who can use it in the
editor; it changes no clip by itself. `m.suggested` holds what edit plugins
before this one suggested for the clip.

`job.steps` is what this run is asked for (find, understand, rate, edit) and
`job.wants(step)` says whether it is asked for one, so one `main()` can serve
every way the plugin is used. In a run that wasn't asked to understand,
`understand()` is logged and keeps nothing, and so is `rate()` on a moment
handed over in a run that wasn't asked to rate, and `suggest_edit()` in a
run that wasn't asked to edit. `add_range()` returns a Moment too: `rate()`
sets its score in any run, and `understand()` adds its notes in a run asked
to understand.
"""

from __future__ import annotations

import json
import os
import sys
import sysconfig
import traceback
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path

from ._hints import python_command
from .contract import (
    DEFAULT_TITLE_OVERLAY_SECONDS,
    EDIT_FIELDS,
    EDIT_NUMBERS,
    FADE_CHOICES,
    HOOK_SECONDS_CHOICES,
    MAX_CONTEXT,
    MAX_CONTEXT_ITEMS,
    MAX_CROP,
    MAX_EDIT_REASON,
    MAX_EDIT_SPANS,
    MAX_LABEL,
    MAX_NOTES,
    MAX_REASON,
    MAX_TITLE,
    MAX_TITLE_OVERLAY,
    PLUGIN_API_VERSION,
    SPEED_CHOICES,
    STEPS,
    TITLE_OVERLAY_SECONDS,
    ContractError,
    check_job,
    check_result,
    error_line,
    kept_length,
    log_line,
    merge_spans,
    nearest_choice,
    progress_line,
)
from .contract import _number as _finite

JOB_FILE = "job.json"
RESULT_FILE = "result.json"
SECRET_PREFIX = "CLIPSKITTY_SECRET_"

# The error line for a slip in the plugin's own code (a missing key, a wrong
# type): creators see "{name} failed: " and this. The details go to the log.
MISTAKE = "it stopped on a mistake in its own code. Ask its developer to fix it."
# Exceptions that mean the plugin's code has a mistake, rather than a problem
# it reports in words of its own (those keep their message).
PROGRAMMING_ERRORS = (LookupError, AttributeError, TypeError, NameError, ArithmeticError)
# The error line for a plugin that imports a part of the SDK this Clips Kitty
# doesn't have: it was made with a newer SDK. Clips Kitty's script host says
# the same for an import at the top of the plugin's file
# (_clipskitty_script_host.py NEWER_VERSION; tests/test_script_host.py checks
# they agree); run() says it for one inside main().
NEWER_VERSION = ("This pipeline needs a newer version of Clips Kitty. Update Clips Kitty, or ask the pipeline's "
                 "developer which version it needs.")


def needs_newer_sdk(error: BaseException) -> bool:
    """Whether `error` is an import of a module or name the SDK doesn't
    have: `import clipskitty_sdk.media`, `from clipskitty_sdk import media`
    or `from clipskitty_sdk.media import Region`, on an older SDK."""
    name = getattr(error, "name", None) or ""
    return isinstance(error, ImportError) and (name == "clipskitty_sdk" or name.startswith("clipskitty_sdk."))


class SettingMissing(KeyError):
    """job.settings[name] for a setting with no value: not declared in
    clipskitty.yaml, or declared without a default and left empty by the
    creator, or a `secret` setting, which is never in job.settings (it is
    read with job.secret()). str() is the plain line a creator sees; `hint`
    is the developer's, which run() puts in the log."""

    def __init__(self, name):
        super().__init__(name)
        self.name = name
        # A secret the creator set is handed over in the environment, not in job.settings.
        self.is_secret = isinstance(name, str) and bool(os.environ.get(SECRET_PREFIX + name.upper().replace("-", "_")))
        if self.is_secret:
            self.hint = (f"{name!r} is a secret setting, and secrets are never in job.settings: read it with "
                         f"job.secret({name!r})")
        else:
            self.hint = (f"no setting called {name!r} in job.settings: declare it under settings in clipskitty.yaml "
                         f"with a default, or use job.settings.get({name!r}, <default>). A secret setting is read "
                         f"with job.secret({name!r})")

    def __str__(self) -> str:  # KeyError's own would put the sentence in quotes
        if self.is_secret:  # the creator did set it: the mistake is in the plugin's code
            return MISTAKE
        return f"the setting {self.name} has no value: choose one in the pipeline's settings, or ask its developer"


class Settings(dict):
    """job.settings: a dict whose missing keys raise SettingMissing."""

    def __missing__(self, key):
        raise SettingMissing(key)


def _shown(value) -> str:
    """`value` as an error message shows it: its repr, cut to 40 characters."""
    try:
        text = repr(value)
    except ValueError:  # an int with more digits than Python will print
        text = f"a {type(value).__name__} too long to show"
    return text if len(text) <= 40 else text[:37] + "..."


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
    min_score: float | None = None  # the creator's minimum score, in a run that rates or understands moments
    # The layouts this job's clips can use, in a run that suggests edits
    # (contract.CROPS, or none); () in every other run.
    crops: tuple[str, ...] = ()


@dataclass
class Model:
    """One model the manifest lists, on this PC. `path` is the folder (Hugging
    Face), the file (url), the name:tag (Ollama) or the app's own path
    (bundled); None when Clips Kitty couldn't tell (Ollama not answering).
    `files` maps each listed file to its full path."""

    path: Path | None
    revision: str = ""
    files: dict = field(default_factory=dict)
    source: str = ""
    id: str = ""


@dataclass
class Tools:
    ffmpeg: str | None = None
    ffprobe: str | None = None
    ollama: dict | None = None


def _number(value) -> float | None:
    """`value` as a float when it is a finite number (not a bool), else None."""
    return float(value) if _finite(value) else None


def one_line(text) -> str:
    """`text` on one line: control characters removed, whitespace and newlines
    collapsed to single spaces, nothing at either end."""
    text = "" if text is None else str(text)
    kept = "".join(ch for ch in text if ch.isspace() or unicodedata.category(ch) != "Cc")
    return " ".join(kept.split())


@dataclass(frozen=True)
class Suggested:
    """What an edit plugin that ran before this one in the same job suggested
    for a clip (Moment.suggested), as Clips Kitty keeps it: read-only. `by`
    is that plugin's id and `name` its name; `edit` holds the fitted fields
    (contract.EDIT_FIELDS but `reason`), in seconds of the video. Without
    transcript.read, `reason` is empty and `edit` has no title_overlay: they
    are another plugin's text, which may come from what was said."""

    by: str
    name: str = ""
    edit: dict = field(default_factory=dict)
    reason: str = ""


def _suggested(data) -> tuple[Suggested, ...]:
    """A handed clip's `suggested`, skipping entries without a plugin id."""
    if not isinstance(data, list):
        return ()
    return tuple(Suggested(by=s["by"], name=str(s.get("name") or ""),
                           edit=dict(s["edit"]) if isinstance(s.get("edit"), dict) else {},
                           reason=str(s.get("reason") or ""))
                 for s in data if isinstance(s, dict) and isinstance(s.get("by"), str))


@dataclass
class Moment:
    """One moment of the video: handed over in `job.moments`, or a range of
    the plugin's own from `job.add_range()`.

    A handed moment's `id` is the one Clips Kitty gave it (m1, m2, ... in its
    order); the plugin's own ranges are r1, r2, ... in the order added.
    `score` is its current score, after any plugin that rated it before this
    one, and `found_score` the score it was found with (for an own range, the
    score given to add_range, if any). `found_by` is "clipskitty" or the id of
    the plugin that found it. `title`, `reason` and `context` (what earlier
    plugins said happens in it) come from what was said, so they are empty
    without transcript.read. `signals` holds Clips Kitty's own subscores for
    it: text, audio, visual, engagement, game, and reaction when it was
    measured. `context` is never changed; `notes` are the ones this run
    added with job.understand(). In a run that suggests edits, `suggested`
    holds what the edit plugins before this one suggested for the clip.
    """

    id: str
    start: float
    end: float
    score: float | None = None
    found_score: float | None = None
    found_by: str = ""
    label: str = ""
    title: str = ""
    reason: str = ""
    context: tuple[str, ...] = ()
    signals: dict = field(default_factory=dict)
    suggested: tuple[Suggested, ...] = ()
    _notes: list = field(default_factory=list, init=False, repr=False, compare=False)

    @property
    def notes(self) -> tuple[str, ...]:
        """This run's own notes about the moment, from job.understand()."""
        return tuple(self._notes)

    @property
    def duration(self) -> float:
        return self.end - self.start


def _handed(data) -> Moment | None:
    """A moment from job.json, or None when it lacks an id, a start or an end.
    Keys this SDK doesn't know are ignored, so a later 1.x can add some."""
    if not isinstance(data, dict) or not isinstance(data.get("id"), str):
        return None
    start, end = _number(data.get("start")), _number(data.get("end"))
    if start is None or end is None:
        return None
    context, signals = data.get("context"), data.get("signals")
    return Moment(
        id=data["id"], start=start, end=end, score=_number(data.get("score")),
        found_score=_number(data.get("found_score")), found_by=str(data.get("found_by") or ""),
        label=str(data.get("label") or ""), title=str(data.get("title") or ""),
        reason=str(data.get("reason") or ""),
        context=tuple(n for n in context if isinstance(n, str)) if isinstance(context, list) else (),
        signals=dict(signals) if isinstance(signals, dict) else {},
        suggested=_suggested(data.get("suggested")),
    )


class EditSuggestion:
    """The edit a plugin suggests for one clip: `job.suggest_edit(m)`.

    Every method returns the suggestion, so calls chain:

        job.suggest_edit(m).cut(815.0, 821.5).fade(fade_out=0.5).reason("Cuts the wait")

    Times are seconds of the video, as `m.start` and `m.end` are. A value
    the contract refuses raises ContractError at the call. What depends on
    the job or the editor is logged once instead: a span wholly outside the
    clip (not kept), a crop this job's clips don't use (kept; Clips Kitty
    ignores it), and a fade, speed or hook title length that Clips Kitty
    will set to the nearest of the timeline editor's choices. Clips Kitty
    fits the rest when it reads the answer (host.read_edits).
    """

    def __init__(self, job: Job, moment: Moment, *, kept: bool):
        self._job = job
        self._m = moment
        self._kept = kept  # False: a call Clips Kitty didn't ask for, logged and never written
        self._cuts: list[tuple[float, float]] = []
        self._mutes: list[tuple[float, float]] = []
        self._values: dict = {}

    def _refuse(self, message: str) -> ContractError:
        return ContractError("suggest_edit", [message])

    def _number(self, what: str, value, low: float, high: float) -> float:
        number = _number(value)
        if number is None or not low <= number <= high:
            raise self._refuse(f"{what} must be a number from {low:g} to {high:g} (got {_shown(value)})")
        return number

    def _span(self, what: str, start, end) -> tuple[float, float]:
        a, b = _number(start), _number(end)
        if a is None or b is None:
            raise self._refuse(f"{what} needs numbers of seconds of the video (got {_shown(start)} to {_shown(end)})")
        if a < 0:
            raise self._refuse(f"{what} needs a start of 0 or more, in seconds of the video (got {_shown(start)})")
        if a >= b:
            raise self._refuse(f"{what} needs start < end, in seconds of the video "
                               f"(got {_shown(start)} to {_shown(end)})")
        return a, b

    def _add(self, what: str, spans: list, start, end) -> None:
        a, b = self._span(what, start, end)
        m = self._m
        if b <= m.start or a >= m.end:
            self._job._log_once(f"{m.id}: {what} {a:.1f}-{b:.1f} s is outside the clip "
                                f"({m.start:.1f}-{m.end:.1f} s), so it isn't kept")
            return
        merged = merge_spans([*spans, (a, b)])
        if len(merged) > MAX_EDIT_SPANS:
            raise self._refuse(f"at most {MAX_EDIT_SPANS} {what}s for one clip")
        spans[:] = merged

    def _nearest(self, what: str, given: float, fitted: float, unit: str = "") -> None:
        if abs(given - fitted) > 1e-9:
            self._job._log_once(f"{self._m.id}: {what} {given:g}{unit} will be {fitted:g}{unit}, "
                                "the nearest the editor offers")

    def cut(self, start: float, end: float) -> EditSuggestion:
        """Take out [start, end] of the clip. Cuts add up; overlapping ones join."""
        self._add("cut", self._cuts, start, end)
        return self

    def trim(self, start: float | None = None, end: float | None = None) -> EditSuggestion:
        """Keep the clip from `start` to `end`: a cut at each end. Either may be left out."""
        m = self._m
        a = m.start if start is None else _number(start)
        b = m.end if end is None else _number(end)
        if a is None or b is None:
            raise self._refuse(f"trim needs numbers of seconds of the video (got {_shown(start)} to {_shown(end)})")
        if a >= b:
            raise self._refuse(f"trim needs start < end, in seconds of the video (got {_shown(start)} to "
                               f"{_shown(end)})")
        if a > m.start:
            self.cut(m.start, min(a, m.end))
        if b < m.end:
            self.cut(max(b, m.start), m.end)
        return self

    def keep_only(self, *spans) -> EditSuggestion:
        """Keep only these (start, end) spans of the clip: the rest is cut."""
        if not spans:
            raise self._refuse("keep_only needs at least one (start, end) span to keep")
        kept = []
        for span in spans:
            if not isinstance(span, (list, tuple)) or len(span) != 2:
                raise self._refuse(f"keep_only needs (start, end) spans (got {_shown(span)})")
            kept.append(self._span("keep_only", *span))
        m = self._m
        pos = m.start
        for a, b in merge_spans(kept):
            if a >= m.end:
                break
            if a > pos:
                self.cut(pos, a)
            pos = max(pos, b)
        if pos < m.end:
            self.cut(pos, m.end)
        return self

    def mute(self, start: float, end: float) -> EditSuggestion:
        """Silence [start, end]; the picture stays. Mutes add up; overlapping ones join."""
        self._add("mute", self._mutes, start, end)
        return self

    def volume(self, level: float) -> EditSuggestion:
        """The whole clip's loudness: 1 as it is, 0 silent, up to 2."""
        self._values["volume"] = self._number("volume", level, *EDIT_NUMBERS["volume"])
        return self

    def fade(self, fade_in: float | None = None, fade_out: float | None = None) -> EditSuggestion:
        """Fade in and out over this many seconds, up to 3 each. Clips Kitty
        uses the nearest of the editor's choices, 0, 0.3, 0.5 or 1 s."""
        for key, value in (("fade_in", fade_in), ("fade_out", fade_out)):
            if value is not None:
                number = self._number(key, value, *EDIT_NUMBERS[key])
                self._nearest(key, number, nearest_choice(number, FADE_CHOICES), " s")
                self._values[key] = number
        return self

    def speed(self, factor: float) -> EditSuggestion:
        """Play the whole clip this much faster, 0.5 to 3. Clips Kitty uses
        the nearest of the editor's choices, 0.75, 1, 1.25, 1.5 or 2."""
        number = self._number("speed", factor, *EDIT_NUMBERS["speed"])
        self._nearest("speed", number, nearest_choice(number, SPEED_CHOICES, toward=1))
        self._values["speed"] = number
        return self

    def title_overlay(self, text: str, seconds: float = DEFAULT_TITLE_OVERLAY_SECONDS) -> EditSuggestion:
        """The editor's Hook title: big text at the top for the clip's first
        `seconds` (1 to 10; Clips Kitty uses the nearest of 2, 3, 5 or 8).
        The text is put on one line and cut to MAX_TITLE_OVERLAY characters;
        Clips Kitty takes out web addresses."""
        line = one_line(text)[:MAX_TITLE_OVERLAY].rstrip()
        if not line:
            raise self._refuse(f"title_overlay needs text (got {_shown(text)})")
        number = self._number("title_overlay seconds", seconds, *TITLE_OVERLAY_SECONDS)
        self._nearest("title_overlay seconds", number, nearest_choice(number, HOOK_SECONDS_CHOICES), " s")
        self._values["title_overlay"] = {"text": line, "seconds": number}
        return self

    def crop(self, mode: str) -> EditSuggestion:
        """The clip's layout: one of job.limits.crops (contract.CROPS in a
        job whose clips can use one). Another is kept, and Clips Kitty
        ignores it."""
        if not isinstance(mode, str) or not mode or len(mode) > MAX_CROP:
            raise self._refuse(f"crop must be text of 1 to {MAX_CROP} characters (got {_shown(mode)})")
        crops = self._job.limits.crops
        if mode not in crops:
            named = ", ".join(crops[:-1]) + f" or {crops[-1]}" if len(crops) > 1 else "".join(crops)
            why = f"this job's clips use {named}" if crops else "this job's clips don't use a layout"
            self._job._log_once(f"{self._m.id}: crop {mode!r} will be ignored: {why}")
        self._values["crop"] = mode
        return self

    def reason(self, text: str) -> EditSuggestion:
        """Why, in one line the creator sees (cut to MAX_EDIT_REASON characters)."""
        line = one_line(text)[:MAX_EDIT_REASON].rstrip()
        if line:
            self._values["reason"] = line
        else:
            self._values.pop("reason", None)
        return self

    @property
    def cuts(self) -> tuple[tuple[float, float], ...]:
        """The cuts set so far, joined where they overlap."""
        return tuple(self._cuts)

    @property
    def mutes(self) -> tuple[tuple[float, float], ...]:
        """The mutes set so far, joined where they overlap."""
        return tuple(self._mutes)

    @property
    def length(self) -> float:
        """Seconds of the clip left after the cuts and the speed, as Clips
        Kitty measures it: pieces shorter than contract.MIN_PIECE don't
        count, and the speed is the nearest of the editor's choices."""
        m = self._m
        return kept_length(m.start, m.end, self._cuts) / self._speed()

    def _speed(self) -> float:
        speed = self._values.get("speed")
        return 1.0 if speed is None else nearest_choice(speed, SPEED_CHOICES, toward=1)

    def _data(self) -> dict:
        """The edit as result.json holds it: what the plugin set, in EDIT_FIELDS order."""
        data: dict = {}
        if self._cuts:
            data["cuts"] = [[a, b] for a, b in self._cuts]
        if self._mutes:
            data["mutes"] = [[a, b] for a, b in self._mutes]
        data.update(self._values)
        return {key: data[key] for key in EDIT_FIELDS if key in data}

    def _length_lines(self, floor: float, longest) -> list[str]:
        """What Clips Kitty will ignore because of the clip's length, as
        host.read_edits decides it."""
        m = self._m
        out = []
        length = m.end - m.start
        if self._cuts:
            left = kept_length(m.start, m.end, self._cuts)
            if left < floor:
                out.append(f"{m.id}: cuts leave {left:.1f} s, under this job's {floor:g} s shortest clip: "
                           "Clips Kitty will ignore the cuts")
            else:
                length = left
        speed = self._speed()
        if speed > 1 and length / speed < floor:
            out.append(f"{m.id}: cuts and speed leave {length / speed:.1f} s, under this job's {floor:g} s "
                       "shortest clip: Clips Kitty will ignore the speed")
        elif speed < 1 and longest and length / speed > longest:
            out.append(f"{m.id}: speed makes the clip {length / speed:.1f} s, over this job's {longest:g} s "
                       "longest clip: Clips Kitty will ignore the speed")
        return out


class Job:
    """One job: what Clips Kitty handed over, and the answer being returned."""

    def __init__(self, folder: Path, data: dict, out=None):
        self.folder = Path(folder)
        self.data = data
        self._out = out
        self._ranges: list[dict] = []
        self._finished = False
        steps = data.get("steps")
        # What this run is asked for; a job.json without steps is a find run.
        self.steps: tuple[str, ...] = (tuple(s for s in steps if isinstance(s, str)) if isinstance(steps, list)
                                       else ("find",))
        handed = data.get("moments")
        self._handed: list[Moment] = [m for m in map(_handed, handed if isinstance(handed, list) else []) if m]
        self._by_id: dict[str, Moment] = {m.id: m for m in self._handed}
        self._own: dict[str, tuple[Moment, dict]] = {}  # r1, r2, ...: the Moment and its range
        self._rated: dict[str, tuple[float, str]] = {}  # a handed moment's id: (score, reason)
        self._edits: dict[str, EditSuggestion] = {}  # a handed moment's id: its suggested edit
        self._own_edits: dict[str, EditSuggestion] = {}  # suggest_edit() on an own range: never written
        self._said: list[tuple[float, float, str]] | None = None  # the transcript, once read
        self._logged: set[str] = set()
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
        self.settings: Settings = Settings(data.get("settings") or {})
        limits = data.get("limits") or {}
        crops = limits.get("crops")
        self.limits = Limits(limits.get("max_clips"), limits.get("min_duration"), limits.get("max_duration"),
                             limits.get("min_score"),
                             tuple(c for c in crops if isinstance(c, str)) if isinstance(crops, list) else ())
        self.focus: str | None = data.get("focus") or None
        self.models: dict[str, Model] = {
            name: Model(Path(m["path"]) if m.get("path") else None, m.get("revision", ""),
                        dict(m.get("files") or {}), m.get("source", ""), m.get("id", ""))
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

    # ---- what this run is asked for --------------------------------------------

    def wants(self, step: str) -> bool:
        """Whether this run is asked to `find` moments, `understand` them (say
        what happens in them), `rate` them, or suggest edits for the clips
        made from them (`edit`). A step name this SDK doesn't know is never
        wanted."""
        return step in STEPS and step in self.steps

    @property
    def moments(self) -> list[Moment]:
        """The moments handed over to rate or understand, or the clips to
        suggest edits for, in Clips Kitty's order. Empty in a find run."""
        return list(self._handed)

    def text(self, m: Moment) -> str:
        """What is said during moment `m`: the transcript's segments that
        overlap it, joined with spaces. The transcript is read once."""
        if self.transcript is None:
            raise ContractError("text", [("this job has no transcript: add transcript to inputs and "
                                          "transcript.read to permissions")])
        if self._said is None:
            said = []
            for s in self.transcript.segments():
                start, end = (_number(s.get("start")), _number(s.get("end"))) if isinstance(s, dict) else (None, None)
                words = str(s.get("text") or "").strip() if start is not None and end is not None else ""
                if words:
                    said.append((start, end, words))
            self._said = said
        return " ".join(words for start, end, words in self._said if start < m.end and end > m.start)

    # ---- the answer ----------------------------------------------------------

    def _mine(self, m, what: str) -> tuple[Moment, dict | None]:
        """`m` as this job handed it out, with its range when it is one of the
        plugin's own (None for a handed moment)."""
        if isinstance(m, Moment):
            if self._by_id.get(m.id) is m:
                return m, None
            own = self._own.get(m.id)
            if own is not None and own[0] is m:
                return own
        name = m.id if isinstance(m, Moment) else repr(m)
        raise ContractError(what, [f"{name} is not a moment of this job"])

    def _log_once(self, message: str) -> None:
        if message not in self._logged:
            self._logged.add(message)
            self.log(message)

    def understand(self, m: Moment, text: str) -> None:
        """Say what happens in moment `m`, for its title: one note of this run.

        The note is put on one line (control characters removed, whitespace
        collapsed) and cut to MAX_CONTEXT characters; an empty note is
        ignored. A moment takes at most MAX_CONTEXT_ITEMS notes from one run;
        `m.context`, what earlier plugins said, is never changed or counted.
        In a run that wasn't asked to understand, the note is logged as
        ignored and nothing is kept.
        """
        m, _ = self._mine(m, "understand")
        if not self.wants("understand"):
            self._log_once("understand ignored: this job didn't ask for notes")
            return
        note = one_line(text)[:MAX_CONTEXT].rstrip()
        if not note:
            return
        if len(m._notes) >= MAX_CONTEXT_ITEMS:
            raise ContractError("understand", [f"at most {MAX_CONTEXT_ITEMS} notes for one moment"])
        m._notes.append(note)

    def rate(self, m: Moment, score: float, reason: str = "") -> None:
        """Give moment `m` a score from 0 to 100, with why (cut to MAX_REASON
        characters). Rating it again replaces the earlier rating; `m.score`
        follows.

        A moment that was handed over needs a run asked to rate: otherwise
        the rating is logged as ignored and nothing is kept. On a range of
        the plugin's own it sets that range's score and reason, exactly as
        add_range(score=, reason=) would, in any run.
        """
        m, own = self._mine(m, "rate")
        value = _number(score)
        if value is None or not 0 <= value <= 100:
            raise ContractError("rate", [f"score must be a number from 0 to 100 (got {_shown(score)})"])
        why = ("" if reason is None else str(reason))[:MAX_REASON]
        if own is not None:
            own["score"], own["reason"] = value, why
            m.score, m.reason = value, why
            return
        if not self.wants("rate"):
            self._log_once("rate ignored: this job didn't ask for ratings")
            return
        self._rated[m.id] = (value, why)
        m.score = value

    def suggest_edit(self, m: Moment) -> EditSuggestion:
        """The edit this run suggests for clip `m`, one of job.moments: the
        same EditSuggestion on every call for `m`, so calls add up.

        In a run that wasn't asked to suggest edits, the call is logged as
        ignored and nothing is kept, and so is a call on one of the plugin's
        own ranges: Clips Kitty asks for edits on the clips it hands over.
        The returned suggestion still checks its calls, so one function can
        serve every run.
        """
        m, own = self._mine(m, "suggest_edit")
        if own is not None:
            self._log_once(f"suggest_edit ignored on {m.id}: Clips Kitty asks for edits on the clips it hands "
                           "over (job.moments)")
            return self._own_edits.setdefault(m.id, EditSuggestion(self, m, kept=False))
        if not self.wants("edit"):
            self._log_once("suggest_edit ignored: this job didn't ask for edits")
        if m.id not in self._edits:
            self._edits[m.id] = EditSuggestion(self, m, kept=self.wants("edit"))
        return self._edits[m.id]

    def add_range(self, start: float, end: float, *, score: float | None = None, label: str = "",
                  title: str = "", reason: str = "") -> Moment:
        """One moment of the source video, in seconds, to become a clip.

        `score` (0-100) is optional: without one, Clips Kitty keeps the order
        the ranges were added in. `label` is the kind of moment ("goal",
        "team_wipe"), `title` a short headline, `reason` why it was picked;
        Clips Kitty writes the clip's own title and captions either way.
        Returns the range as a Moment (r1, r2, ...), for rate() and understand().
        """
        entry = {"start": float(start), "end": float(end), "label": str(label)[:MAX_LABEL],
                 "title": str(title)[:MAX_TITLE], "reason": str(reason)[:MAX_REASON]}
        if score is not None:
            entry["score"] = float(score)
        problems = check_result({"plugin_api": PLUGIN_API_VERSION, "ranges": [entry]})
        if problems:
            problems = [p.replace("ranges[0]", "range") for p in problems]
            raise ContractError("add_range", [f"{p} (got {_shown(score)})" if "score must be" in p else p
                                              for p in problems])
        limits = self.limits
        length = entry["end"] - entry["start"]
        if limits.max_duration and length > limits.max_duration:
            self.log(f"range {start:.1f}-{end:.1f}s is longer than this job's {limits.max_duration}s limit")
        if limits.min_duration and length < limits.min_duration:
            self.log(f"range {start:.1f}-{end:.1f}s is shorter than this job's {limits.min_duration}s minimum")
        self._ranges.append(entry)
        moment = Moment(id=f"r{len(self._own) + 1}", start=entry["start"], end=entry["end"],
                        score=entry.get("score"), found_score=entry.get("score"), found_by=self.plugin_id,
                        label=entry["label"], title=entry["title"], reason=entry["reason"])
        self._own[moment.id] = (moment, entry)
        return moment

    @property
    def ranges(self) -> list[dict]:
        return list(self._ranges)

    def _answers(self) -> list[dict]:
        """One answer for each handed moment this run rated, noted or
        suggested an edit for, in order."""
        out = []
        for m in self._handed:
            answer: dict = {"id": m.id}
            if m.id in self._rated:
                answer["score"], answer["reason"] = self._rated[m.id]
            if m._notes:
                answer["context"] = list(m._notes)
            suggestion = self._edits.get(m.id)
            if suggestion is not None and suggestion._kept and suggestion._data():
                answer["edit"] = suggestion._data()
            if len(answer) > 1:
                out.append(answer)
        return out

    def finish(self, notes: str = "") -> Path:
        """Write result.json. Call once, at the end; the process should then exit with 0.

        It holds the ranges added (each with its own notes, in a run asked to
        understand), the answers about the moments handed over (with the
        edit suggested for each, in a run asked to edit), and `notes` for the
        job log. It is checked the way Clips Kitty checks it, for the steps
        this run was asked for. In a run asked to edit, a clip whose cuts or
        speed leave less than the job's shortest clip (at least 1 s) gets a
        log line saying what Clips Kitty will ignore.
        """
        if self.wants("edit"):
            floor = max(1.0, _number(self.limits.min_duration) or 0.0)
            for suggestion in self._edits.values():
                for line in suggestion._length_lines(floor, _number(self.limits.max_duration)):
                    self._log_once(line)
        ranges = self._ranges
        noted = {id(entry): m.notes for m, entry in self._own.values() if m._notes}
        if noted and self.wants("understand"):
            ranges = [{**entry, "context": list(noted[id(entry)])} if id(entry) in noted else entry
                      for entry in self._ranges]
        result: dict = {"plugin_api": PLUGIN_API_VERSION, "ranges": ranges}
        answers = self._answers()
        if answers:
            result["moments"] = answers
        if notes:
            result["notes"] = str(notes)[:MAX_NOTES]
        problems = check_result(result, steps=self.steps)
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


def _job_folder(folder) -> str | os.PathLike | None:
    """Where the job folder is: `folder`, else the first command-line
    argument, else the CLIPSKITTY_JOB environment variable."""
    return folder or (sys.argv[1] if len(sys.argv) > 1 else None) or os.environ.get("CLIPSKITTY_JOB")


def no_job_folder_text() -> str:
    """What run() prints when the plugin was started without a job folder,
    as when a developer runs `python src/main.py`."""
    return ("This is a Clips Kitty plugin: Clips Kitty starts it with a job folder.\n"
            f"To try it, run: {python_command()} run <the plugin's folder> --sample\n"
            "(no job folder: pass it as the first argument or set CLIPSKITTY_JOB)\n")


def read_job(folder: str | os.PathLike | None = None, out=None) -> Job:
    """The job Clips Kitty prepared: from `folder`, else the first command-line
    argument, else the CLIPSKITTY_JOB environment variable."""
    where = _job_folder(folder)
    if not where:
        raise ContractError("job", ["no job folder: pass it as the first argument or set CLIPSKITTY_JOB"])
    path = Path(where)
    data = json.loads((path / JOB_FILE).read_text(encoding="utf-8"))
    problems = check_job(data)
    if problems:
        raise ContractError("job.json", problems)
    return Job(path, data, out=out)


def _where_it_stopped(error: BaseException) -> str:
    """Where in the plugin's own code `error` was raised, as " (src/main.py
    line 12)": the innermost frame in the plugin's folder (the working
    folder, where Clips Kitty starts it), else the innermost one outside the
    SDK and the standard library. "" when there is none."""
    frames = traceback.extract_tb(error.__traceback__)
    sdk = Path(__file__).resolve().parent
    here = Path.cwd().resolve()
    stdlib = {Path(p).resolve() for p in (sysconfig.get_paths().get("stdlib"),
                                          sysconfig.get_paths().get("platstdlib")) if p}

    def under(path: Path, folder: Path) -> bool:
        return path == folder or folder in path.parents

    outside = None
    for frame in reversed(frames):
        if frame.filename.startswith("<"):
            continue
        path = Path(frame.filename).resolve()
        if under(path, sdk) or any(under(path, lib) for lib in stdlib):
            continue
        if under(path, here):
            return f" ({path.relative_to(here).as_posix()} line {frame.lineno})"
        outside = outside or f" ({frame.filename} line {frame.lineno})"
    return outside or ""


def run(main, folder: str | os.PathLike | None = None) -> None:
    """Read the job, call `main(job)`, and finish it, reporting any error.

    An exception inside `main` becomes an error line (the traceback goes to
    the log), and the process exits with 1, so the job fails with words
    rather than a stack trace. The error line is the exception's message,
    except for a slip in the plugin's own code (a KeyError, TypeError and the
    like), which gets MISTAKE, with the exception and where it happened in a
    log line; a setting with no value (SettingMissing), which gets its
    plain line, with the developer's hint in a log line; and an import of a
    part of the SDK this Clips Kitty doesn't have (needs_newer_sdk), which
    gets NEWER_VERSION.

    Started without a job folder (a developer running `python src/main.py`),
    it says how to try the plugin, on standard error, and exits with 2.
    """
    if not _job_folder(folder):
        sys.stderr.write(no_job_folder_text())
        sys.stderr.flush()
        raise SystemExit(2)
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
        if isinstance(e, SettingMissing):
            job.log(e.hint)
            job.fail(str(e))
        if needs_newer_sdk(e):
            job.log(f"{type(e).__name__}: {e}{_where_it_stopped(e)}: "
                    "the clipskitty_sdk it runs with doesn't have it")
            job.fail(NEWER_VERSION)
        if isinstance(e, PROGRAMMING_ERRORS):
            job.log(f"{type(e).__name__}: {e}{_where_it_stopped(e)}")
            job.fail(MISTAKE)
        job.fail(str(e) or type(e).__name__)
