"""The editor lane's tasks (server/editor_lane.py, editor suite step 6): the
new editor's media work beside the exports, one at a time, in order,
cancellable, at below-normal priority, made once.

- A task with the key of one waiting or running is not queued again.
- Exports and tasks share the one worker: never two at once.
- GET /export/jobs and Make clips again see exports only (jobs(), get(),
  cancel()); tasks have their own events.
- Cancelling a running task kills its FFmpeg (a process it started itself
  too).

Light tier: FFmpeg is stood in for by a few lines of Python.
"""

import subprocess
import sys
import time

from test_editor_lane import PRESET, Popens, fake_encode, wait_for

from server.editor_lane import EditorLane

HOLD = "import time\nend = time.time() + 60\nwhile time.time() < end:\n    time.sleep(0.05)\n"


def test_tasks_run_in_order_once_and_apart_from_exports(tmp_path):
    events, ran = [], []
    lane = EditorLane(tmp_path / "data", publish=events.append, encode=fake_encode(log=ran))
    clip = tmp_path / "clip.mp4"
    clip.write_bytes(b"clip")
    first = lane.submit_task("peaks", "peaks.bin:aaa", lambda ctx: ran.append("peaks") or {"ok": 1}, name="peaks")
    again = lane.submit_task("peaks", "peaks.bin:aaa", lambda ctx: ran.append("twice") or {}, name="peaks")
    export = lane.submit_export(source=clip, folder=tmp_path / "out", name="clip", preset=PRESET)
    lane.submit_task("thumbs", "thumbs:aaa", lambda ctx: ran.append("thumbs") or {})
    assert again["job_id"] == first["job_id"]
    assert [j["export_id"] for j in lane.jobs()] == [export["export_id"]]          # exports only
    assert lane.get(first["job_id"]) is None and lane.get(first["job_id"], kind="peaks")["status"] == "queued"
    assert lane.cancel(first["job_id"]) is None                                       # not through the export route
    assert lane.run_pending() == 3
    assert ran == ["peaks", "clip.mp4", "thumbs"]
    done = lane.find("peaks.bin:aaa")
    assert done["status"] == "done" and done["result"] == {"ok": 1} and done["fraction"] == 1.0
    assert [t["kind"] for t in lane.tasks()] == ["thumbs", "peaks"]
    kinds = {e["type"] for e in events}
    assert kinds == {"media_job", "export"}
    assert all("export_id" in e["export"] for e in events if e["type"] == "export")
    assert all("job_id" in e["job"] for e in events if e["type"] == "media_job")
    # Done, so the same key may be asked for again (the cache says whether it's needed).
    assert lane.submit_task("peaks", "peaks.bin:aaa", lambda ctx: {})["job_id"] != first["job_id"]


def test_a_failed_task_says_why(tmp_path):
    lane = EditorLane(tmp_path / "data", publish=lambda e: None)

    def broken(ctx):
        raise RuntimeError("FFmpeg couldn't read the sound")

    job = lane.submit_task("peaks", "k", broken)
    lane.run_pending()
    got = lane.get(job["job_id"], kind=None)
    assert got["status"] == "failed" and got["error"] == "FFmpeg couldn't read the sound"


def test_cancelling_a_running_task_kills_its_ffmpeg(tmp_path):
    popens = Popens()
    lane = EditorLane(tmp_path / "data", publish=lambda e: None, popen=popens)
    started = []

    def work(ctx):
        code, _err = ctx.run([sys.executable, "-c", HOLD], duration=1.0)
        return {"code": code}

    def own_process(ctx):
        proc = subprocess.Popen([sys.executable, "-c", HOLD])
        started.append(proc)
        ctx.started(proc)
        proc.wait()
        ctx.check()
        return {}

    lane.start()
    try:
        job = lane.submit_task("proxy", "proxy:x", work)
        wait_for(lambda: popens.procs)
        began = time.monotonic()
        assert lane.cancel(job["job_id"], kind=None)["status"] == "cancelled"
        assert time.monotonic() - began < 10
        assert popens.procs[0].poll() is not None
        second = lane.submit_task("peaks", "peaks:x", own_process)
        wait_for(lambda: started)
        assert lane.cancel(second["job_id"], kind="peaks")["status"] == "cancelled"
        assert started[0].poll() is not None
    finally:
        lane.stop()
        lane.join(10)
