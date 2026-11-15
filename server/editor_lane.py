"""The editor lane: a second background worker, for the editor's jobs.

The pipeline worker (server/jobs.py) works through one video at a time and
can be busy for an hour. An export of a finished clip must not wait behind
that, nor take the machine from it. So editor jobs run here: exports now
(video/export_presets.py), the new editor's previews in later steps.

- **One FFmpeg at a time, below normal priority.** One worker thread, and
  each FFmpeg starts at BELOW_NORMAL_PRIORITY_CLASS on Windows (niceness 10
  elsewhere), so the PC stays usable and a render beside it keeps most of
  the machine.
- **Independent of the processing queue.** It never waits for the queue,
  paused or not, and the queue never waits for it: nothing here touches the
  jobs table or the pipeline worker.
- **Cancellable.** Cancelling a waiting job drops it; cancelling the running
  one kills its FFmpeg and removes the partial file.
- **Nothing is ever half there.** An export is written beside where it goes
  under a temporary name (`<name>.exporting-<8 hex>.mp4`) and renamed only
  when it is finished. While a file is being written its path is listed in
  <data>/editor_lane/partials.json, so one a crash left behind is removed
  the next time the lane starts, with the 2-pass logs in editor_lane/job-*.

Jobs are kept in memory only. An export the app was closed in the middle of
is gone, partial file and all, rather than started again behind the user's
back at the next launch.

Progress goes out through the app's events (server/events.py) as
{"type": "export", "export": <the job, as public() shows it>}: when it is
queued, when it starts, about twice a second while it runs, and when it is
done, failed or cancelled.

**Tasks** (editor suite step 6): the new editor's media work (a proxy, the
waveform peaks, the thumbnail sheets, a large import) runs here too, one at a
time with the exports, in the order asked. A task is a function given a
TaskContext (run an FFmpeg, report progress, check for a cancel); it writes
its own files, under temporary names. Its events are {"type": "media_job",
"job": <public()>}. jobs(), get() and cancel() answer for exports only, so
GET /export/jobs and Make clips again see what they always did; tasks() and
find() are the tasks'.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import threading
import time
import uuid
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from core.paths import discard
from core.state import _now
from server.events import broadcaster
from video import export_presets

KEEP_FINISHED = 100     # finished jobs GET /export/jobs still shows
PROGRESS_EVERY = 0.5    # seconds between progress events for one job
PARTIAL_SUFFIX = re.compile(r"\.exporting-[0-9a-f]{8}\.mp4$")
STATUSES = ("queued", "running", "done", "failed", "cancelled")
# Tasks that are exports all the same (editor suite step 7): a project's
# export runs as a task (it is many FFmpeg commands, not one encode), and is
# listed, cancelled and announced with the other exports.
EXPORT_TASKS = ("project_export",)


def unique_target(folder: Path, stem: str) -> Path:
    """`stem`.mp4 in `folder`, or stem-2.mp4, stem-3.mp4 and so on: the names
    the export route gives copies (server/api.py _unique_path)."""
    target = folder / f"{stem}.mp4"
    i = 2
    while target.exists():
        target = folder / f"{stem}-{i}.mp4"
        i += 1
    return target


def _kill(proc) -> None:
    try:
        proc.kill()
    except (OSError, ProcessLookupError):
        pass  # it ended on its own


@dataclass
class LaneJob:
    id: int
    kind: str                    # export, or a task's kind (proxy, peaks, thumbs, import)
    clip_id: int | None
    source: Path
    folder: Path
    name: str
    preset: dict
    on_done: Callable[[dict], None] | None = None
    status: str = "queued"
    fraction: float = 0.0
    step: dict = field(default_factory=dict)
    result: dict | None = None
    error: str | None = None
    created_at: str = field(default_factory=_now)
    started_at: str | None = None
    finished_at: str | None = None
    cancel_requested: bool = False
    work: Callable[[TaskContext], dict] | None = None      # a task's function; None for an export
    key: str = ""                                          # what a task makes, so it is made once
    extra: dict = field(default_factory=dict)              # a project export's own fields (project_id, revision, branch)
    on_dropped: Callable[[dict], None] | None = None       # a project export cancelled before it started

    @property
    def is_export(self) -> bool:
        """An export of a clip (no `work`), or a task that is one (EXPORT_TASKS)."""
        return self.work is None or self.kind in EXPORT_TASKS

    def public(self) -> dict:
        """The job as the API and the events show it."""
        if self.work is not None and not self.is_export:
            return {
                "job_id": self.id,
                "kind": self.kind,
                "key": self.key,
                "name": self.name,
                "status": self.status,
                "cancelling": self.cancel_requested and self.status == "running",
                "fraction": round(self.fraction, 4),
                "result": dict(self.result) if self.result else None,
                "error": self.error,
                "created_at": self.created_at,
                "started_at": self.started_at,
                "finished_at": self.finished_at,
                # A project's draft (editor suite step 7): the stage it is
                # on, and its project and revision.
                **({"stage": self.step.get("stage")} if self.step.get("stage") else {}),
                **self.extra,
            }
        return {
            "export_id": self.id,
            "kind": self.kind,
            "clip_id": self.clip_id,
            "status": self.status,
            "cancelling": self.cancel_requested and self.status == "running",
            "fraction": round(self.fraction, 4),
            "pass": self.step.get("pass"),
            "passes": self.step.get("passes"),
            "encoder": self.step.get("encoder"),
            # Made again, smaller, because it came out over the size limit
            # (`fraction` is then near its end); how far along that attempt is.
            "smaller": bool(self.step.get("smaller")),
            "attempt_fraction": self.step.get("attempt_fraction"),
            "folder": str(self.folder),
            "name": self.name,
            "preset": dict(self.preset),
            "result": dict(self.result) if self.result else None,
            "error": self.error,
            "created_at": self.created_at,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            **({"stage": self.step.get("stage")} if self.work is not None else {}),
            **self.extra,
        }


class EditorLane(threading.Thread):
    """The worker. start() it once; submit_export() from any thread."""

    def __init__(self, data_dir, *, publish: Callable[[dict], None] | None = None,
                 encode: Callable[..., dict] | None = None, popen=None):
        super().__init__(daemon=True, name="editor-lane")
        self.data_dir = Path(data_dir)
        self.work_root = self.data_dir / "editor_lane"
        self._publish = publish or broadcaster.publish
        self._encode = encode or export_presets.encode
        self._popen = popen                      # None: subprocess.Popen
        self._cond = threading.Condition()
        self._jobs: dict[int, LaneJob] = {}
        self._queue: deque[int] = deque()
        self._next_id = 1
        self._current: LaneJob | None = None
        self._proc = None
        self._stopping = False
        self._partials_lock = threading.Lock()
        self._last_event: dict[int, float] = {}

    # ---- lifecycle ---------------------------------------------------------

    def start(self) -> None:
        self.cleanup()
        super().start()

    def stop(self) -> None:
        """Stop after killing whatever FFmpeg is running (its partial file is
        removed as it would be by a cancel)."""
        with self._cond:
            self._stopping = True
            if self._current is not None:
                self._current.cancel_requested = True
            proc = self._proc
            self._cond.notify_all()
        if proc is not None:
            _kill(proc)

    def run(self) -> None:
        while True:
            job = self._take(block=True)
            if job is None:
                return
            self._run(job)

    def run_pending(self) -> int:
        """Run every queued job now, in the calling thread, and return how
        many ran. For tests and scripts, with the thread not started."""
        ran = 0
        while (job := self._take(block=False)) is not None:
            self._run(job)
            ran += 1
        return ran

    # ---- the queue ---------------------------------------------------------

    def submit_export(self, *, source, folder, name: str, preset, clip_id: int | None = None,
                      on_done: Callable[[dict], None] | None = None) -> dict:
        """Queue an export of the finished clip `source` into `folder` as
        `name`.mp4 (or name-2.mp4...). `on_done` gets the finished job.
        Returns the job as public() shows it."""
        preset = export_presets.normalize(preset)
        if preset["kind"] == "original":
            raise export_presets.PresetError("Original is a copy; the export route makes it, not the lane.")
        with self._cond:
            job = LaneJob(id=self._next_id, kind="export", clip_id=clip_id, source=Path(source).resolve(),
                          folder=Path(folder).resolve(), name=name, preset=preset, on_done=on_done)
            self._next_id += 1
            self._jobs[job.id] = job
            self._queue.append(job.id)
            self._prune()
            self._cond.notify_all()
        self._emit(job)
        return self.get(job.id)

    def submit_project_export(self, work: Callable[[TaskContext], dict], *, folder, name: str, preset: dict,
                              clip_id: int | None = None, extra: dict | None = None,
                              on_done: Callable[[dict], None] | None = None,
                              on_dropped: Callable[[dict], None] | None = None) -> dict:
        """Queue a project's export (editor suite step 7): a task listed with
        the exports (jobs(), get(), cancel(), "export" events). `work(ctx)`
        makes and registers the file and returns the result. `folder` and
        `name` say where it goes; `extra` is added to what public() shows.
        `on_dropped` gets the job when it is cancelled before it started
        (work never ran, so it couldn't say so itself)."""
        with self._cond:
            job = LaneJob(id=self._next_id, kind="project_export", clip_id=clip_id, source=Path(),
                          folder=Path(folder), name=name, preset=dict(preset or {}), on_done=on_done,
                          work=work, key=f"project_export:{self._next_id}", extra=dict(extra or {}),
                          on_dropped=on_dropped)
            self._next_id += 1
            self._jobs[job.id] = job
            self._queue.append(job.id)
            self._prune()
            self._cond.notify_all()
        self._emit(job)
        return self.get(job.id)

    def submit_task(self, kind: str, key: str, work: Callable[[TaskContext], dict], *, name: str = "",
                    on_done: Callable[[dict], None] | None = None, extra: dict | None = None) -> dict:
        """Queue a task: `work(ctx)` runs on the lane and returns its result.
        When a task with the same `key` is waiting or running, that one is
        returned instead: a thing is made once."""
        with self._cond:
            for job in self._jobs.values():
                if job.work is not None and job.key == key and job.status in ("queued", "running"):
                    return job.public()
            job = LaneJob(id=self._next_id, kind=kind, clip_id=None, source=Path(), folder=Path(), name=name,
                          preset={}, on_done=on_done, work=work, key=key, extra=dict(extra or {}))
            self._next_id += 1
            self._jobs[job.id] = job
            self._queue.append(job.id)
            self._prune()
            self._cond.notify_all()
        self._emit(job)
        return self.get(job.id, kind=None)

    def tasks(self) -> list[dict]:
        """Every task the lane remembers, newest first."""
        with self._cond:
            return [self._jobs[i].public() for i in sorted(self._jobs, reverse=True)
                    if self._jobs[i].work and not self._jobs[i].is_export]

    def find(self, key: str) -> dict | None:
        """The newest task with this key, or None."""
        with self._cond:
            for i in sorted(self._jobs, reverse=True):
                job = self._jobs[i]
                if job.work is not None and not job.is_export and job.key == key:
                    return job.public()
        return None

    def cancel(self, job_id: int, *, wait: float = 5.0, kind: str | None = "export") -> dict | None:
        """Cancel a job: a waiting one is dropped, a running one's FFmpeg is
        killed and its partial file removed. Waits up to `wait` seconds for
        a running one to be finished with. None for an unknown id (or one
        that is not an export, unless `kind` is None)."""
        with self._cond:
            job = self._jobs.get(job_id)
            if job is None or not self._is(job, kind):
                return None
            proc = None
            if job.status == "queued":
                self._queue.remove(job_id)
                job.status, job.finished_at = "cancelled", _now()
            elif job.status == "running":
                job.cancel_requested = True
                proc = self._proc if self._current is job else None
            else:
                return job.public()
        if proc is not None:
            _kill(proc)
        if job.status == "cancelled":
            if job.on_dropped is not None and job.started_at is None:
                try:
                    job.on_dropped(job.public())
                except Exception as e:
                    print(f"Editor {job.kind} {job.id}: after-cancel step failed: {e}")
            self._emit(job)
        else:
            deadline = time.monotonic() + wait
            with self._cond:
                while job.status == "running" and time.monotonic() < deadline:
                    self._cond.wait(timeout=max(0.01, deadline - time.monotonic()))
        return self.get(job_id, kind=None)

    @staticmethod
    def _is(job: LaneJob, kind: str | None) -> bool:
        if kind is None:
            return True
        return job.is_export if kind == "export" else (job.work is not None and job.kind == kind)

    def get(self, job_id: int, kind: str | None = "export") -> dict | None:
        """A job as public() shows it: an export, unless `kind` says
        otherwise (None for any job)."""
        with self._cond:
            job = self._jobs.get(job_id)
            return job.public() if job and self._is(job, kind) else None

    def jobs(self) -> list[dict]:
        """Every export the lane remembers, newest first."""
        with self._cond:
            return [self._jobs[i].public() for i in sorted(self._jobs, reverse=True) if self._jobs[i].is_export]

    def _take(self, *, block: bool) -> LaneJob | None:
        with self._cond:
            while not self._queue:
                if self._stopping or not block:
                    return None
                self._cond.wait(timeout=2.0)
            if self._stopping:
                return None
            job = self._jobs[self._queue.popleft()]
            job.status, job.started_at = "running", _now()
            self._current = job
        return job

    def _prune(self) -> None:
        finished = sorted(i for i, j in self._jobs.items() if j.status not in ("queued", "running"))
        for i in finished[:-KEEP_FINISHED] if len(finished) > KEEP_FINISHED else []:
            del self._jobs[i]
            self._last_event.pop(i, None)

    # ---- running a job -----------------------------------------------------

    def _run(self, job: LaneJob) -> None:
        if job.work is not None:
            self._run_task(job)
            return
        self._emit(job)
        partial = job.folder / f"{job.name}.exporting-{uuid.uuid4().hex[:8]}.mp4"
        work = self.work_root / f"job-{job.id}"
        status, result, error = "failed", None, None
        try:
            self._check_cancel(job)
            job.folder.mkdir(parents=True, exist_ok=True)
            self._add_partial(partial)
            made = self._encode(job.source, partial, job.preset, workdir=work, run=self._runner(job),
                                on_progress=lambda fraction, step: self._progress(job, fraction, step))
            self._check_cancel(job)
            final = unique_target(job.folder, job.name)
            partial.replace(final)
            status, result = "done", {**made, "path": str(final), "file": final.name}
        except export_presets.Cancelled:
            status = "cancelled"
        except Exception as e:
            if job.cancel_requested:
                status = "cancelled"
            else:
                error = str(e).strip()[:500] or type(e).__name__
                print(f"Export {job.id} failed: {error}")
        finally:
            if discard(partial):
                self._remove_partial(partial)
            shutil.rmtree(work, ignore_errors=True)
        if status == "done" and job.on_done is not None:
            try:
                job.on_done({**job.public(), "status": "done", "result": result})
            except Exception as e:  # a bookkeeping failure must not lose the export
                print(f"Export {job.id}: after-export step failed: {e}")
        with self._cond:
            job.status, job.result, job.error, job.finished_at = status, result, error, _now()
            if status == "done":
                job.fraction = 1.0
            self._current = None
            self._prune()
            self._cond.notify_all()
        self._emit(job)

    def _run_task(self, job: LaneJob) -> None:
        self._emit(job)
        status, result, error = "failed", None, None
        try:
            self._check_cancel(job)
            result = job.work(TaskContext(self, job))
            status = "done"
        except export_presets.Cancelled:
            status = "cancelled"
        except Exception as e:
            if job.cancel_requested:
                status = "cancelled"
            else:
                error = str(e).strip()[:500] or type(e).__name__
                print(f"Editor {job.kind} {job.id} failed: {error}")
        finally:
            with self._cond:
                self._proc = None
        with self._cond:
            job.status, job.result, job.error, job.finished_at = status, result, error, _now()
            if status == "done":
                job.fraction = 1.0
            self._current = None
            self._prune()
            self._cond.notify_all()
        if job.on_done is not None:
            try:
                job.on_done(job.public())
            except Exception as e:
                print(f"Editor {job.kind} {job.id}: after-task step failed: {e}")
        self._emit(job)

    def _task_process(self, job: LaneJob, proc) -> None:
        """A task's own process (not through run_ffmpeg): cancel() kills it."""
        with self._cond:
            self._proc = proc
            late = job.cancel_requested
        if late:
            _kill(proc)

    def _check_cancel(self, job: LaneJob) -> None:
        if job.cancel_requested:
            raise export_presets.Cancelled()

    def _runner(self, job: LaneJob):
        """export_presets.encode's `run`: each FFmpeg at below-normal
        priority, killed by cancel()."""

        def run(argv, *, cwd=None, duration=0.0, on_progress=None):
            self._check_cancel(job)

            def started(proc) -> None:
                with self._cond:
                    self._proc = proc
                    late = job.cancel_requested
                if late:
                    _kill(proc)

            try:
                code, err = export_presets.run_ffmpeg(argv, cwd=cwd, duration=duration, on_progress=on_progress,
                                                      on_start=started, popen=self._popen)
            finally:
                with self._cond:
                    self._proc = None
            self._check_cancel(job)
            return code, err

        return run

    def _progress(self, job: LaneJob, fraction: float, step: dict) -> None:
        with self._cond:
            job.fraction = max(job.fraction, min(0.999, float(fraction)))
            job.step = dict(step)
        now = time.monotonic()
        if now - self._last_event.get(job.id, 0.0) >= PROGRESS_EVERY:
            self._last_event[job.id] = now
            self._emit(job)

    def _emit(self, job: LaneJob) -> None:
        with self._cond:
            if not job.is_export:
                event = {"type": "media_job", "job": job.public()}
            else:
                event = {"type": "export", "export": job.public()}
        try:
            self._publish(event)
        except Exception:
            pass  # no window listening is not the export's problem

    # ---- leftovers ---------------------------------------------------------

    @property
    def _partials_file(self) -> Path:
        return self.work_root / "partials.json"

    def _read_partials(self) -> list[str]:
        try:
            listed = json.loads(self._partials_file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return []
        return [str(p) for p in listed if isinstance(p, str)] if isinstance(listed, list) else []

    def _write_partials(self, paths: list[str]) -> None:
        self.work_root.mkdir(parents=True, exist_ok=True)
        tmp = self._partials_file.with_suffix(".tmp")
        tmp.write_text(json.dumps(paths, indent=1), encoding="utf-8")
        os.replace(tmp, self._partials_file)

    def _add_partial(self, path: Path) -> None:
        with self._partials_lock:
            listed = self._read_partials()
            if str(path) not in listed:
                self._write_partials([*listed, str(path)])

    def _remove_partial(self, path: Path) -> None:
        with self._partials_lock:
            listed = self._read_partials()
            if str(path) in listed:
                self._write_partials([p for p in listed if p != str(path)])

    def cleanup(self) -> int:
        """Remove what a lane stopped mid-job left: the partial exports listed
        in partials.json (only names the lane itself gives them) and the
        working folders. Returns how many partial files were removed."""
        removed = 0
        with self._partials_lock:
            kept = []
            for listed in self._read_partials():
                path = Path(listed)
                if not PARTIAL_SUFFIX.search(path.name):
                    continue  # not a name the lane writes: never delete it
                existed = path.exists()
                if discard(path):
                    removed += existed
                else:
                    kept.append(listed)   # still locked: try again next start
            if kept or self._partials_file.exists():
                self._write_partials(kept)
        if self.work_root.is_dir():
            for folder in self.work_root.glob("job-*"):
                shutil.rmtree(folder, ignore_errors=True)
        if removed:
            print(f"Editor lane: removed {removed} unfinished export(s) left by the last run")
        return removed


class TaskContext:
    """What a task is given: `run(argv, duration=...)` runs one FFmpeg at
    below-normal priority (its -progress output as progress, killed by a
    cancel), `started(proc)` makes a process the task started itself
    cancellable the same way, `progress(fraction)` reports, `check()` raises
    export_presets.Cancelled once a cancel was asked for."""

    def __init__(self, lane: EditorLane, job: LaneJob):
        self._lane, self.job = lane, job
        self._run = lane._runner(job)

    def run(self, argv: list[str], *, duration: float = 0.0, cwd=None,
            on_progress: Callable[[float], None] | None = None) -> tuple[int, str]:
        """`on_progress` takes the command's own fraction in place of
        progress() (a task of many commands scales it to its share)."""
        return self._run(argv, cwd=cwd, duration=duration, on_progress=on_progress or self.progress)

    def started(self, proc) -> None:
        self._lane._task_process(self.job, proc)

    def progress(self, fraction: float, step: dict | None = None) -> None:
        self._lane._progress(self.job, fraction, step or {})

    def check(self) -> None:
        self._lane._check_cancel(self.job)
