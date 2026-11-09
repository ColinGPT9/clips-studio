"""The child process tests/test_remake_crash.py kills in the middle of a Make
clips again, and the new process that starts after it.

    python remake_crash_child.py run <data> <scenario> <kill_at>
    python remake_crash_child.py restart <data> [<kill_at>]

`run` makes a library (a video, its own copy, five clips with files of their
own) and runs a Make clips again on it (`scenario`: "finish", a run that
finishes and replaces; "stopped", one that deletes the video's own copy and
fails). Every file operation core/remake.py makes (rename, copy, hard link,
delete), every file the run writes and every commit on the worker's
connection is a point; the process ends with os._exit right after point
`kill_at`, as Windows ends the engine when the app is closed (taskkill /F):
no `finally`, no `except`, nothing after it. With `kill_at` 0 it runs to the
end and prints how many points there were.

`restart` is the next start: Storage's clean-up first (the app's routes can
answer while the worker starts), then the engine's start-up recovery (the
call server/jobs.py makes before the worker takes a job), then the next Make
clips again and Storage's clean-up again. With `kill_at`, the recovery itself
is killed after that point, and nothing else runs. It prints what it found
as JSON.

Standard library and core/ only: no clip has render options to read.
"""

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core import housekeeping, remake  # noqa: E402
from core.state import StateDB, _now  # noqa: E402

VID = "abcdefghijk"
KILLED = 3            # the exit code of a process ended at its point

# The clips from before: (name, start, end, kept, how it is used).
CLIPS = (
    ("edited", 10.0, 70.0, True),      # a person changed it; the run doesn't find it again
    ("published", 100.0, 130.0, True),  # published; the run finds its window again
    ("found_again", 200.0, 230.0, False),  # nobody used it; the run makes it again in place
    ("not_found", 300.0, 330.0, False),    # nobody used it; deleted
    ("scheduled", 400.2, 430.3, True),     # scheduled; a new clip of the run takes its first name
)
# The run's renders: two windows found again, a new one with the scheduled
# clip's whole seconds, and a new one.
RENDERS = ((100.0, 130.0), (200.0, 230.0), (400.4, 430.6), (500.0, 530.0))


class Points:
    """Counts each point and ends the process right after `kill_at`."""

    def __init__(self, kill_at: int):
        self.kill_at, self.n, self.names = kill_at, 0, []

    def tick(self, what: str) -> None:
        self.n += 1
        self.names.append(what)
        if self.n == self.kill_at:
            sys.stdout.write(json.dumps({"killed_after": what, "point": self.n}) + "\n")
            sys.stdout.flush()
            os._exit(KILLED)


class _Conn:
    """The worker's connection, with every commit a point."""

    def __init__(self, real, points: Points):
        self._real, self._points = real, points

    def commit(self):
        self._real.commit()
        self._points.tick("commit")

    def __getattr__(self, name):
        return getattr(self._real, name)


def _counted(fn, points: Points, what: str):
    def call(*args, **kwargs):
        out = fn(*args, **kwargs)
        points.tick(what)
        return out

    return call


def instrument(db, points: Points) -> None:
    remake._rename = _counted(remake._rename, points, "rename")
    remake._copy = _counted(remake._copy, points, "copy")
    remake.discard = _counted(remake.discard, points, "delete")
    os.link = _counted(os.link, points, "link")
    db.conn = _Conn(db.conn, points)


def folder(data: Path) -> Path:
    return data / "clips" / "someone" / f"A stream [{VID}]"


def name(start: float, end: float) -> str:
    return f"clip_{int(start):05d}-{int(end):05d}.mp4"


def own_content(clip: str) -> bytes:
    return f"{clip}: the clip's own video".encode() * 20


def render_content(start: float, end: float) -> bytes:
    return f"the run's render of {start}-{end}".encode() * 20


COPY = b"the video's own copy" * 20


def setup(data: Path) -> dict:
    db = StateDB(data / "state.db")
    db.upsert_video(VID, title="A stream")
    db.set_video_status(VID, "done")
    folder(data).mkdir(parents=True)
    (data / "previews").mkdir()
    (data / "downloads").mkdir()
    (data / "downloads" / f"{VID}.mp4").write_bytes(COPY)
    ids = {}
    for clip, start, end, _kept in CLIPS:
        path = folder(data) / name(start, end)
        path.write_bytes(own_content(clip))
        ids[clip] = db.add_clip(VID, start, end, 70, "hook", path=str(path), status="queued", title=clip)
        (data / "previews" / f"clip_{ids[clip]}.mp4").write_bytes(b"draft")
    db.set_clip(ids["edited"], title="My own title", edited_at=_now())
    db.record_upload(ids["published"], "yt1")
    db.set_clip(ids["published"], status="queued")
    db.set_clip(ids["scheduled"], status="scheduled", scheduled_for="2026-11-20")
    expect = {str(ids[clip]): {"clip": clip, "kept": kept, "window": [start, end]}
              for clip, start, end, kept in CLIPS}
    (data / "expect.json").write_text(json.dumps(expect), encoding="utf-8")
    db.close()
    return expect


def render(run, data: Path, start: float, end: float, points: Points | None = None) -> None:
    """What core/pipeline.py does for one rendered window, through the run's
    database: its file under its whole seconds, then registered."""
    path = folder(data) / name(start, end)
    path.write_bytes(render_content(start, end))
    if points is not None:
        points.tick("render written")
    if run.db.add_clip(VID, start, end, 90, "new hook", path=str(path), status="queued", title="new",
                       description="", hashtags="[]", scores="{}", render_opts="") is None:
        row = run.db.conn.execute("SELECT id FROM clips WHERE video_id = ? AND start_s = ? AND end_s = ?",
                                  (VID, round(start, 2), round(end, 2))).fetchone()
        run.db.set_clip(row["id"], path=str(path), scores="{}")


def run(data: Path, scenario: str, kill_at: int) -> None:
    setup(data)
    db = StateDB(data / "state.db")
    points = Points(kill_at)
    instrument(db, points)
    job = remake.Run(db, VID, data, cancels=0, url=f"local:{VID}")
    if scenario == "finish":
        job.db.set_outcome(VID, {"clips": len(RENDERS)})
        for start, end in RENDERS:
            render(job, data, start, end, points)
        job.finish()
    else:
        render(job, data, 200.0, 230.0, points)
        (data / "downloads" / f"{VID}.mp4").unlink()     # the run's first step deleted the copy
        points.tick("the run deleted the copy")
        job.stopped("failed")
    print(json.dumps({"points": points.n, "names": points.names}))


def _plays(db, clip_id: int) -> bytes | None:
    row = db.get_clip(clip_id)
    if row is None:
        return None
    path = Path(row["path"]) if row["path"] else None
    return path.read_bytes() if path is not None and path.is_file() else b""


def _on_disk(data: Path) -> list[bytes]:
    return [p.read_bytes() for p in folder(data).iterdir() if p.is_file()]


def check(db, data: Path, expect: dict, rows_settled: bool) -> list[str]:
    """What is wrong, in words: a kept clip deleted or not playing its own
    video, a clip from before playing another one, or (`rows_settled`) any
    clip whose row points at no file."""
    wrong = []
    on_disk = _on_disk(data)
    for clip_id, want in expect.items():
        clip, kept = want["clip"], want["kept"]
        plays = _plays(db, int(clip_id))
        mine = own_content(clip)
        if plays is None:
            if kept:
                wrong.append(f"{clip}: kept, but its row was deleted")
            continue
        if kept and mine not in on_disk:
            wrong.append(f"{clip}: its own video is gone from the folder")
        if not rows_settled:
            continue
        if kept and plays != mine:
            wrong.append(f"{clip}: plays {plays[:30]!r}, not its own video")
        if not kept and plays not in (mine, render_content(*want["window"])):
            wrong.append(f"{clip}: plays {plays[:30]!r}")
    if rows_settled:
        for row in db.conn.execute("SELECT id, path FROM clips WHERE video_id = ?", (VID,)).fetchall():
            if not row["path"] or not Path(row["path"]).is_file():
                wrong.append(f"clip {row['id']}: its row points at no file ({Path(row['path']).name})")
    return wrong


def restart(data: Path, kill_at: int = 0) -> None:
    expect = json.loads((data / "expect.json").read_text(encoding="utf-8"))
    db = StateDB(data / "state.db")
    out: dict = {}
    if kill_at:
        points = Points(kill_at)
        instrument(db, points)
        remake.recover(db, data)
        print(json.dumps({"recovered_whole": True, "points": points.n}))
        return
    housekeeping.clean(db, data)
    out["cleanup_before_recovery"] = check(db, data, expect, rows_settled=False)
    out["recovered"] = remake.recover(db, data)
    out["after_recovery"] = check(db, data, expect, rows_settled=True)
    out["journal_left"] = db.conn.execute(
        "SELECT COUNT(*) FROM app_state WHERE key GLOB ?", (remake.JOURNAL + "*",)).fetchone()[0]
    copy = data / "downloads" / f"{VID}.mp4"
    out["copy"] = copy.is_file() and copy.read_bytes() == COPY
    out["downloads"] = sorted(p.name for p in (data / "downloads").iterdir())
    job = remake.Run(db, VID, data, cancels=0, url=f"local:{VID}")
    job.db.set_outcome(VID, {"clips": 1})
    render(job, data, 600.0, 630.0)
    out["next_remake"] = job.finish().get("kept_because")
    out["after_next_remake"] = check(db, data, expect, rows_settled=True)
    housekeeping.clean(db, data)
    out["after_cleanup"] = check(db, data, expect, rows_settled=True)
    db.close()
    print(json.dumps(out))


if __name__ == "__main__":
    what, where = sys.argv[1], Path(sys.argv[2])
    if what == "run":
        run(where, sys.argv[3], int(sys.argv[4]))
    else:
        restart(where, int(sys.argv[3]) if len(sys.argv) > 3 else 0)
