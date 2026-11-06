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
    kind: str                    # export (previews come in later steps)
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

    def public(self) -> dict:
        """The job as the API and the events show it."""
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
            "folder": str(self.folder),
            "name": self.name,
            "preset": dict(self.preset),
            "result": dict(self.result) if self.result else None,
            "error": self.error,
            "created_at": self.created_at,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
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

    def cancel(self, job_id: int, *, wait: float = 5.0) -> dict | None:
        """Cancel a job: a waiting one is dropped, a running one's FFmpeg is
        killed and its partial file removed. Waits up to `wait` seconds for
        a running one to be finished with. None for an unknown id."""
        with self._cond:
            job = self._jobs.get(job_id)
            if job is None:
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
            self._emit(job)
        else:
            deadline = time.monotonic() + wait
            with self._cond:
                while job.status == "running" and time.monotonic() < deadline:
                    self._cond.wait(timeout=max(0.01, deadline - time.monotonic()))
        return self.get(job_id)

    def get(self, job_id: int) -> dict | None:
        with self._cond:
            job = self._jobs.get(job_id)
            return job.public() if job else None

    def jobs(self) -> list[dict]:
        """Every job the lane remembers, newest first."""
        with self._cond:
            return [self._jobs[i].public() for i in sorted(self._jobs, reverse=True)]

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
