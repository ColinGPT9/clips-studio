"""The editor lane (server/editor_lane.py): export jobs one at a time, in
order, cancellable, at below-normal priority, never half there.

Light tier. FFmpeg is stood in for by a few lines of Python started as a real
process, so killing it, its exit and its priority are the real thing; the
lane's encode is a fake that runs that process the way video/export_presets.py
runs FFmpeg (through the lane's runner).
"""

import ast
import os
import re
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from server import editor_lane
from server.editor_lane import EditorLane
from video import export_presets

PRESET = {"kind": "custom", "resolution": "720p"}

# A stand-in for FFmpeg: writes its output, reports progress the way
# -progress pipe:1 does, then holds for argv[2] seconds.
FAKE_FFMPEG = """
import sys, time
with open(sys.argv[1], "wb") as f:
    f.write(b"x" * 1000)
print("out_time_us=500000", flush=True)
end = time.time() + float(sys.argv[2])
while time.time() < end:
    time.sleep(0.05)
print("out_time_us=1000000", flush=True)
print("progress=end", flush=True)
"""


def wait_for(check, timeout: float = 30.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if check():
            return
        time.sleep(0.02)
    pytest.fail("timed out waiting")


class Popens:
    """subprocess.Popen, recording each process it starts and its arguments."""

    def __init__(self):
        self.procs: list[subprocess.Popen] = []
        self.kwargs: list[dict] = []

    def __call__(self, *args, **kwargs):
        proc = subprocess.Popen(*args, **kwargs)
        self.procs.append(proc)
        self.kwargs.append(kwargs)
        return proc


def fake_encode(*, hold: float = 0.0, fail: str | None = None, log: list | None = None, active: list | None = None):
    """What video/export_presets.encode does, minus FFmpeg: runs one process
    through the lane's runner, which writes the export."""
    lock = threading.Lock()

    def encode(src, dst, preset, *, workdir, run, on_progress=None, **_kwargs):
        Path(workdir).mkdir(parents=True, exist_ok=True)
        (Path(workdir) / "export2pass-0.log").write_text("a 2-pass log")
        if active is not None:
            with lock:
                active.append(1)
                assert len(active) == 1, "two exports ran at once"
        if log is not None:
            log.append(Path(src).name)
        try:
            code, err = run([sys.executable, "-c", FAKE_FFMPEG, str(dst), str(hold)], cwd=workdir, duration=1.0,
                            on_progress=lambda f: on_progress(f, {"pass": 1, "passes": 1, "encoder": "fake"}))
        finally:
            if active is not None:
                with lock:
                    active.pop()
        if fail:
            raise export_presets.ExportError(fail)
        if code != 0:
            raise export_presets.ExportError(err or f"exit {code}")
        return {"bytes": Path(dst).stat().st_size, "width": 720, "height": 1280}

    return encode


@pytest.fixture
def clip(tmp_path) -> Path:
    path = tmp_path / "clips" / "clip_00010-00020.mp4"
    path.parent.mkdir(parents=True)
    path.write_bytes(b"a finished clip")
    return path


def test_jobs_run_one_at_a_time_in_the_order_they_came(tmp_path, clip):
    log, active = [], []
    sources = []
    for i in range(3):
        source = clip.with_name(f"c{i}.mp4")
        source.write_bytes(b"clip")
        sources.append(source)
    lane = EditorLane(tmp_path / "data", publish=lambda e: None, encode=fake_encode(hold=0.2, log=log, active=active))
    lane.start()
    try:
        ids = [lane.submit_export(source=s, folder=tmp_path / "out", name=f"clip{i}", preset=PRESET)["export_id"]
               for i, s in enumerate(sources)]
        wait_for(lambda: all(lane.get(i)["status"] == "done" for i in ids))
    finally:
        lane.stop()
        lane.join(10)
    assert log == ["c0.mp4", "c1.mp4", "c2.mp4"]
    assert sorted(p.name for p in (tmp_path / "out").iterdir()) == ["clip0.mp4", "clip1.mp4", "clip2.mp4"]
    assert [j["export_id"] for j in lane.jobs()] == sorted(ids, reverse=True)


def test_a_finished_export_takes_its_name_only_when_it_is_done(tmp_path, clip):
    out = tmp_path / "out"
    out.mkdir()
    (out / "clip.mp4").write_bytes(b"an older export")
    written, done = [], []
    lane = EditorLane(tmp_path / "data", publish=lambda e: None)

    def encode(src, dst, preset, *, workdir, run, on_progress=None):
        written.append(Path(dst).name)
        assert lane._read_partials() == [str(dst)], "listed while it is written"
        Path(dst).write_bytes(b"made")
        return {"bytes": 4}

    lane._encode = encode
    job = lane.submit_export(source=clip, folder=out, name="clip", preset=PRESET, clip_id=7, on_done=done.append)
    assert job["status"] == "queued" and job["preset"]["resolution"] == "720p"
    assert lane.run_pending() == 1
    final = lane.get(job["export_id"])
    assert final["status"] == "done" and final["fraction"] == 1.0
    assert final["result"]["file"] == "clip-2.mp4" and final["result"]["path"] == str(out / "clip-2.mp4")
    assert (out / "clip-2.mp4").read_bytes() == b"made"
    assert (out / "clip.mp4").read_bytes() == b"an older export"
    assert re.fullmatch(r"clip\.exporting-[0-9a-f]{8}\.mp4", written[0])
    assert not list(out.glob("*.exporting-*")) and lane._read_partials() == []
    assert done and done[0]["clip_id"] == 7 and done[0]["status"] == "done"
    assert not (lane.work_root / f"job-{job['export_id']}").exists()


def test_cancelling_a_waiting_export_drops_it(tmp_path, clip):
    ran = []

    def encode(src, dst, preset, *, workdir, run, on_progress=None):
        ran.append(Path(dst).name)
        Path(dst).write_bytes(b"x")
        return {"bytes": 1}

    lane = EditorLane(tmp_path / "data", publish=lambda e: None, encode=encode)
    a = lane.submit_export(source=clip, folder=tmp_path / "out", name="a", preset=PRESET)
    b = lane.submit_export(source=clip, folder=tmp_path / "out", name="b", preset=PRESET)
    assert lane.cancel(b["export_id"])["status"] == "cancelled"
    assert lane.run_pending() == 1
    assert len(ran) == 1 and ran[0].startswith("a.")
    assert lane.get(a["export_id"])["status"] == "done"
    assert lane.cancel(a["export_id"])["status"] == "done", "a finished export stays finished"
    assert lane.cancel(999) is None


def test_cancelling_the_running_export_kills_ffmpeg_and_removes_the_partial(tmp_path, clip):
    popens, events = Popens(), []
    out = tmp_path / "out"
    lane = EditorLane(tmp_path / "data", publish=events.append, encode=fake_encode(hold=60), popen=popens)
    lane.start()
    try:
        job = lane.submit_export(source=clip, folder=out, name="clip", preset=PRESET)
        wait_for(lambda: popens.procs and list(out.glob("*.exporting-*.mp4")))
        began = time.monotonic()
        got = lane.cancel(job["export_id"])
        assert got["status"] == "cancelled"
        assert time.monotonic() - began < 10
    finally:
        lane.stop()
        lane.join(10)
    assert popens.procs[0].poll() is not None, "the FFmpeg stand-in was killed"
    assert not out.exists() or not list(out.iterdir()), "no partial file and no export"
    assert lane._read_partials() == []
    assert events[-1]["export"]["status"] == "cancelled"


def test_a_failed_export_says_why_and_leaves_nothing(tmp_path, clip):
    out = tmp_path / "out"
    lane = EditorLane(tmp_path / "data", publish=lambda e: None,
                      encode=fake_encode(fail="Error while opening encoder"))
    job = lane.submit_export(source=clip, folder=out, name="clip", preset=PRESET)
    lane.run_pending()
    final = lane.get(job["export_id"])
    assert final["status"] == "failed" and final["error"] == "Error while opening encoder"
    assert final["result"] is None
    assert not list(out.iterdir()) and lane._read_partials() == []


def test_progress_goes_out_as_export_events(tmp_path, clip):
    events = []
    lane = EditorLane(tmp_path / "data", publish=events.append, encode=fake_encode())
    lane.submit_export(source=clip, folder=tmp_path / "out", name="clip", preset=PRESET, clip_id=3)
    lane.run_pending()
    assert all(e["type"] == "export" and e["export"]["clip_id"] == 3 for e in events)
    statuses = [e["export"]["status"] for e in events]
    assert statuses[:2] == ["queued", "running"] and statuses[-1] == "done"
    assert "running" in statuses[2:-1] or len(statuses) == 3
    fractions = [e["export"]["fraction"] for e in events]
    assert fractions == sorted(fractions) and fractions[-1] == 1.0
    assert events[-1]["export"]["result"]["path"].endswith("clip.mp4")


def test_a_job_made_again_smaller_says_so(tmp_path, clip):
    """export_presets.encode marks the steps of a file made again because it
    came out over the size limit: the job shows "smaller", and how far along
    that attempt is, while `fraction` keeps going up."""
    events, during = [], []
    lane = EditorLane(tmp_path / "data", publish=events.append)

    def encode(src, dst, preset, *, workdir, run, on_progress=None):
        on_progress(0.9, {"pass": 1, "passes": 1, "encoder": "libx264", "smaller": False, "attempt_fraction": 0.9})
        during.append(lane.jobs()[0])
        on_progress(0.999, {"pass": 1, "passes": 2, "encoder": "libx264", "smaller": True, "attempt_fraction": 0.25})
        during.append(lane.jobs()[0])
        Path(dst).write_bytes(b"made")
        return {"bytes": 4, "retried": True}

    lane._encode = encode
    queued = lane.submit_export(source=clip, folder=tmp_path / "out", name="clip", preset=PRESET)
    assert (queued["smaller"], queued["attempt_fraction"]) == (False, None)
    lane.run_pending()
    assert [(j["smaller"], j["attempt_fraction"], j["pass"], j["passes"]) for j in during] == \
        [(False, 0.9, 1, 1), (True, 0.25, 1, 2)]
    assert during[1]["fraction"] >= during[0]["fraction"]
    assert events[-1]["export"]["status"] == "done"


def test_original_never_goes_to_the_lane(tmp_path, clip):
    lane = EditorLane(tmp_path / "data", publish=lambda e: None)
    with pytest.raises(export_presets.PresetError):
        lane.submit_export(source=clip, folder=tmp_path, name="clip", preset=None)
    with pytest.raises(export_presets.PresetError):
        lane.submit_export(source=clip, folder=tmp_path, name="clip", preset={"resolution": "999p"})
    assert lane.jobs() == []


def test_leftovers_of_a_stopped_lane_are_removed_at_start(tmp_path):
    out = tmp_path / "out"
    out.mkdir()
    partial = out / "clip.exporting-0123abcd.mp4"
    partial.write_bytes(b"half an export")
    finished, stranger = out / "clip.mp4", out / "notes.txt"
    finished.write_bytes(b"a finished export")
    stranger.write_text("not ours")
    lane = EditorLane(tmp_path / "data", publish=lambda e: None)
    (lane.work_root / "job-3").mkdir(parents=True)
    (lane.work_root / "job-3" / "export2pass-0.log").write_text("x")
    # Only names the lane gives partial files are ever deleted, whatever the list says.
    lane._write_partials([str(partial), str(finished), str(stranger)])
    assert lane.cleanup() == 1
    assert not partial.exists() and finished.exists() and stranger.exists()
    assert not (lane.work_root / "job-3").exists()
    assert lane._read_partials() == []
    assert lane.cleanup() == 0


def test_ffmpeg_runs_below_normal_priority(tmp_path):
    report = (
        "import os, sys, time\n"
        "time.sleep(0.3)\n"
        "if os.name == 'nt':\n"
        "    import ctypes\n"
        "    k = ctypes.windll.kernel32\n"
        "    k.GetCurrentProcess.restype = ctypes.c_void_p\n"
        "    k.GetPriorityClass.argtypes = [ctypes.c_void_p]\n"
        "    value = k.GetPriorityClass(k.GetCurrentProcess())\n"
        "else:\n"
        "    value = os.getpriority(os.PRIO_PROCESS, 0)\n"
        "print('priority=%d' % value, file=sys.stderr, flush=True)\n"
    )
    code, err = export_presets.run_ffmpeg([sys.executable, "-c", report], cwd=tmp_path)
    assert code == 0, err
    value = int(re.search(r"priority=(-?\d+)", err).group(1))
    if os.name == "nt":
        assert value == 0x4000, "BELOW_NORMAL_PRIORITY_CLASS"
    else:
        assert value >= 10


def test_progress_reaches_the_caller_from_ffmpegs_progress_lines(tmp_path):
    seen = []
    code, _ = export_presets.run_ffmpeg([sys.executable, "-c", FAKE_FFMPEG, str(tmp_path / "x.mp4"), "0"],
                                        duration=2.0, on_progress=seen.append)
    assert code == 0 and seen == [0.25, 0.5]


def test_the_lane_never_waits_for_the_processing_queue():
    """It neither reads the queue's pause nor claims from the jobs table, and
    the pipeline worker never imports it."""
    tree = ast.parse(Path(editor_lane.__file__).read_text(encoding="utf-8"))
    imported = {alias.name for node in ast.walk(tree) if isinstance(node, ast.Import) for alias in node.names}
    imported |= {node.module or "" for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)}
    assert not imported & {"core.queue", "server.jobs", "core.pipeline"}
    called = {node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)}
    assert not called & {"is_paused", "claim_next_job", "recover_interrupted_jobs"}
    jobs_source = (Path(editor_lane.__file__).parent / "jobs.py").read_text(encoding="utf-8")
    assert "editor_lane" not in jobs_source
