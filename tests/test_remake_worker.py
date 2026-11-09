"""Make clips again in the worker (server/jobs.py, core/remake.py): the old
clips nobody used are replaced only after a run that finished and made clips,
and a job without `replace_untouched` (Process again) runs as it always did.

The worker loop really runs here, with process_video faked: it registers
clips the way core/pipeline.py _register_clip does, through the database it
is given.
"""

import json
import sys
import threading
import time
import types
from pathlib import Path

import pytest

jobs = pytest.importorskip("server.jobs")  # CI installs only the light dependencies

from core import progress, queue  # noqa: E402
from core.cancel import CancelledError  # noqa: E402
from core.state import StateDB, _now  # noqa: E402

VID = "abcdefghijk"
URL = f"https://www.youtube.com/watch?v={VID}"


def _register(db, start, end, render_opts="", folder=None):
    """What _register_clip does with a rendered clip, its file written into
    `folder` first under its whole seconds, as _render_files writes it."""
    path = ""
    if folder is not None:
        path = str(folder / f"clip_{int(start):05d}-{int(end):05d}.mp4")
        with open(path, "wb") as f:
            f.write(b"the run's render")
    if db.add_clip(VID, start, end, 80, "hook", path=path, status="queued", title="new",
                   render_opts=render_opts) is None:
        row = db.conn.execute("SELECT id FROM clips WHERE video_id = ? AND start_s = ? AND end_s = ?",
                              (VID, round(start, 2), round(end, 2))).fetchone()
        db.set_clip(row["id"], path=path, scores="{}")


@pytest.fixture
def world(tmp_path, monkeypatch):
    """A processed video with three clips: one nobody used, one edited, and
    one nobody used whose window the next run finds again."""
    pytest.importorskip("numpy")   # the worker's imports, as tests/test_job_model_check.py
    pytest.importorskip("cv2")     # core.pipeline, for the real D33 check below
    pytest.importorskip("PIL")
    pytest.importorskip("yaml")
    pytest.importorskip("yt_dlp")  # sources.dispatch reads the job's link
    from core import pipeline
    from main import BUNDLED_CONFIG, load_config

    config = load_config(BUNDLED_CONFIG)
    config["paths"]["data_dir"] = str(tmp_path)
    worker = jobs.Worker(config)
    worker.prefetch = types.SimpleNamespace(wait_for=lambda vid: None, maybe_start=lambda _db: None)
    monkeypatch.setattr(jobs.Worker, "_check_plan", staticmethod(lambda *a, **k: None))
    monkeypatch.setattr(jobs.Worker, "_check_local_model", staticmethod(lambda *a, **k: None))
    monkeypatch.setattr(progress, "_handler", progress._handler)  # put back after the run
    events: list = []
    monkeypatch.setattr(jobs.broadcaster, "publish", events.append)
    given: list = []
    plan: dict = {"run": lambda db: None}

    def process_video(url, cfg, db, force=False):
        given.append((url, db, force))
        plan["run"](db)
        return []

    # The real D33 check, which core/remake.py reads through core.pipeline.
    monkeypatch.setitem(sys.modules, "core.pipeline", types.SimpleNamespace(
        process_video=process_video, _edited=pipeline._edited))

    db = StateDB(tmp_path / "state.db")
    db.upsert_video(VID, title="A stream")
    db.set_video_status(VID, "done")
    db.set_outcome(VID, {"candidates": 3})
    clips = tmp_path / "clips"
    clips.mkdir()

    def clip(start, end, **fields):
        path = clips / f"clip_{int(start):05d}-{int(end):05d}.mp4"
        path.write_bytes(b"x")
        clip_id = db.add_clip(VID, start, end, 70, "hook", path=str(path), status="queued",
                              render_opts=json.dumps({"job_captions": True}))
        if fields:
            db.set_clip(clip_id, **fields)
        return clip_id

    old = {"unused": clip(10.0, 70.0), "edited": clip(200.0, 270.0, edited_at=_now()),
           "again": clip(100.0, 130.0)}

    def run(payload):
        job_id = db.add_job("process", json.dumps(payload), video_id=VID)
        queue.set_paused(db, False)
        thread = threading.Thread(target=worker.run, daemon=True)
        thread.start()
        deadline = time.time() + 30
        while db.get_job(job_id)["status"] in ("queued", "running") and time.time() < deadline:
            time.sleep(0.05)
        worker.stop()
        thread.join(timeout=10)
        return db.get_job(job_id)

    yield types.SimpleNamespace(db=db, old=old, run=run, plan=plan, given=given, events=events, clips=clips)
    db.close()


REMAKE = {"url": URL, "force": True, "replace_untouched": True, "captions": False}


def _ids(db):
    return {r["id"] for r in db.conn.execute("SELECT id FROM clips WHERE video_id = ?", (VID,))}


def test_a_remake_replaces_the_unused_clips_once_its_run_is_done(world, capsys):
    made_with = json.dumps({"job_captions": False})   # this run's captions are off

    def run(db):
        _register(db, 12.0, 40.0, made_with, world.clips)          # a new, shorter clip
        _register(db, 100.0, 130.0, made_with, world.clips)        # found again
        _register(db, 200.0, 270.0, made_with, world.clips)        # found again, and edited before

    world.plan["run"] = run
    job = world.run(REMAKE)

    assert job["status"] == "done"
    left = _ids(world.db)
    assert world.old["unused"] not in left
    assert {world.old["edited"], world.old["again"]} <= left and len(left) == 3
    assert not (world.clips / "clip_00010-00070.mp4").exists()
    # The unused one made again is what this run made it with, its new file
    # too; the edited one keeps its own.
    assert world.db.get_clip(world.old["again"])["render_opts"] == made_with
    assert (world.clips / "clip_00100-00130.mp4").read_bytes() == b"the run's render"
    assert (world.clips / "clip_00200-00270.mp4").read_bytes() == b"x"
    assert json.loads(world.db.get_clip(world.old["edited"])["render_opts"]) == {"job_captions": True}
    said = capsys.readouterr().out
    # The pipeline's own "Done (N clips)" counts the new clips only: the line
    # says which of the run's clips are new, so the two agree.
    assert ("Make clips again: this run made 3 clip(s): 1 new, 2 at the same start and end as a clip from "
            "before. Replaced 2 clip(s) from before that nobody had used (1 removed, 1 made again with the new "
            "settings). Kept 1: 1 edited." in said)
    # The job's own "done" comes after: the Library reads its clips again
    # then, and the count it carries is the clips left. It says what the run
    # did, as the job's row does for the Queue page.
    done = [e for e in world.events if e.get("type") == "job" and e.get("status") == "done"]
    assert done and done[-1]["clips"] == 3
    did = {"made": 3, "new": 1, "again": 1, "removed": 1, "replaced": 2, "kept": 1, "unchanged": 1}
    assert done[-1]["result"] == did
    assert json.loads(world.db.get_job(job["id"])["result"]) == did
    shown = queue.snapshot(world.db)["completed"]
    assert [j["result"] for j in shown if j["id"] == job["id"]] == [did]


def test_a_remake_that_made_no_clips_says_so_on_its_job(world, capsys):
    """Black-box S3: a video with no speech, made again with the AI picking
    its moments, makes no clips. Its clips from before are all kept, and the
    job says it made none, on its row (the Queue page) and in its "done"
    event (the Library): the clips still there would otherwise look as if
    nothing had run."""
    job = world.run(REMAKE)
    assert job["status"] == "done"
    assert _ids(world.db) == set(world.old.values())
    did = {"made": 0, "new": 0, "again": 0, "removed": 0, "replaced": 0, "kept": 1, "unchanged": 3}
    assert json.loads(job["result"]) == did
    done = [e for e in world.events if e.get("type") == "job" and e.get("status") == "done"]
    assert done[-1]["result"] == did
    assert ("Make clips again: this run made no clips, so the 3 clip(s) from before are all kept as they were"
            in capsys.readouterr().out)


def test_a_job_that_isnt_a_remake_has_no_result(world):
    """Process again (force, no replace_untouched): no result on its row, in
    its event or on its queue row, as before."""
    world.plan["run"] = lambda db: _register(db, 12.0, 40.0)
    job = world.run({"url": URL, "force": True})
    assert job["status"] == "done" and job["result"] == ""
    done = [e for e in world.events if e.get("type") == "job" and e.get("status") == "done"]
    assert done and "result" not in done[-1]
    shown = queue.snapshot(world.db)["completed"]
    assert [("result" in j) for j in shown if j["id"] == job["id"]] == [False]


def test_the_engine_puts_an_interrupted_runs_files_back_before_its_first_job(world, capsys):
    """The app closed in the middle of a Make clips again: the old clips'
    files are still under their put-aside names. As the engine starts,
    before the worker takes its first job (any job: a Process again here),
    every one is back where its row says, under its first name."""
    from core import in_use, remake

    firsts = {c: world.db.get_clip(c)["path"] for c in world.old.values()}
    left = remake.Run(world.db, VID, world.clips.parent)           # never finished: the app closed
    assert all(".clip-" in world.db.get_clip(c)["path"] for c in world.old.values())
    in_use.let_go_of_files(left)                                     # a new process holds nothing
    seen: dict = {}
    world.plan["run"] = lambda db: seen.update({c: db.get_clip(c)["path"] for c in world.old.values()})
    job = world.run({"url": URL, "force": True})
    assert job["status"] == "done"
    assert seen == firsts
    assert all(Path(p).read_bytes() == b"x" for p in firsts.values())
    assert "has its own video back" in capsys.readouterr().out
    assert world.db.get_flag(remake.JOURNAL + VID, "") == ""


@pytest.mark.parametrize("ending, status, said", [
    (RuntimeError("the transcript couldn't be read"), "failed", "this run failed"),
    (CancelledError("cancelled"), "cancelled", "this run was cancelled"),
])
def test_a_run_that_fails_or_is_cancelled_replaces_nothing(world, ending, status, said, capsys):
    made_with = json.dumps({"job_captions": False})

    def run(db):
        _register(db, 12.0, 40.0)                 # some clips made before it stopped,
        _register(db, 100.0, 130.0, made_with)    # one of them at an old clip's window
        raise ending

    world.plan["run"] = run
    job = world.run(REMAKE)

    assert job["status"] == status
    assert set(world.old.values()) <= _ids(world.db)
    assert (world.clips / "clip_00010-00070.mp4").exists()
    # The one whose window was made again keeps its own file and render
    # options: its file was out of the run's way, and the run's row write was held back.
    again = world.db.get_clip(world.old["again"])
    assert json.loads(again["render_opts"]) == {"job_captions": True}
    assert again["path"] == str(world.clips / "clip_00100-00130.mp4")
    assert (f"Make clips again: {said}, so nothing was replaced and the 3 clip(s) from before are all kept"
            in capsys.readouterr().out)


def test_a_cancel_the_run_never_looked_for_still_replaces_nothing(world, capsys):
    """POST /cancel answers at once, but a run looks for it only at its
    checks, the last one after each clip's render. A Cancel pressed after
    that (while the creator's learning or the story reels finish) used to go
    unseen, and the old clips were replaced. The worker looks once more
    first: the job ends cancelled and nothing is deleted."""
    from core import cancel

    def run(db):
        _register(db, 12.0, 40.0)        # the last clip made...
        cancel.request_cancel(VID)       # ...then Cancel is pressed (POST /cancel)

    world.plan["run"] = run
    try:
        job = world.run(REMAKE)
    finally:
        cancel.clear(VID)
    assert job["status"] == "cancelled"
    assert set(world.old.values()) <= _ids(world.db)
    assert (world.clips / "clip_00010-00070.mp4").exists()
    assert ("Make clips again: this run was cancelled, so nothing was replaced and the 3 clip(s) from "
            "before are all kept" in capsys.readouterr().out)


def test_a_cancel_pressed_as_the_job_starts_replaces_nothing(world, monkeypatch, capsys):
    """Pressed while the worker still checks the AI model: the run never
    starts, the job ends cancelled, and every clip is as it was."""
    from core import cancel

    before = {c: dict(world.db.get_clip(c)) for c in world.old.values()}
    monkeypatch.setattr(jobs.Worker, "_check_local_model",
                        staticmethod(lambda *a, **k: cancel.request_cancel(VID)))     # POST /cancel
    try:
        job = world.run(REMAKE)
    finally:
        cancel.clear(VID)
    assert job["status"] == "cancelled" and world.given == []
    assert {c: dict(world.db.get_clip(c)) for c in world.old.values()} == before
    assert (world.clips / "clip_00010-00070.mp4").exists()
    assert "this run was cancelled, so nothing was replaced" in capsys.readouterr().out


@pytest.mark.parametrize("cleared", ["before its clips", "after its last clip"])
def test_a_cancel_whose_flag_was_cleared_still_replaces_nothing(world, capsys, cleared):
    """core/pipeline.py clears the flag once its download step is done, so a
    Cancel pressed before then (while the source is found, converted or
    downloaded again) used to be forgotten: the run finished and the old
    clips were replaced. The worker counts Cancels, which nothing clears;
    the run's database asks again for one cleared before its next step."""
    from core import cancel

    def press_and_clear():
        cancel.request_cancel(VID)       # pressed while the source is found
        cancel.clear(VID)                # the pipeline's own fresh start

    def run(db):
        if cleared == "before its clips":
            press_and_clear()
        _register(db, 12.0, 40.0)        # the run goes on, as it did
        if cleared == "after its last clip":
            press_and_clear()

    world.plan["run"] = run
    try:
        job = world.run(REMAKE)
    finally:
        cancel.clear(VID)
    assert job["status"] == "cancelled"
    assert set(world.old.values()) <= _ids(world.db)
    assert (world.clips / "clip_00010-00070.mp4").exists()
    assert ("Make clips again: this run was cancelled, so nothing was replaced and the 3 clip(s) from "
            "before are all kept" in capsys.readouterr().out)


def test_a_late_cancel_of_a_plain_run_is_as_before(world):
    """Only a Make clips again looks again: Process again, like every plain
    run, ends as it always has."""
    from core import cancel

    def run(db):
        _register(db, 12.0, 40.0)
        cancel.request_cancel(VID)

    world.plan["run"] = run
    try:
        job = world.run({"url": URL, "force": True})
    finally:
        cancel.clear(VID)
    assert job["status"] == "done"
    assert set(world.old.values()) <= _ids(world.db) and len(_ids(world.db)) == 4


def test_a_remake_that_stops_at_a_check_says_nothing_was_replaced(world, monkeypatch, capsys):
    """Stopped before its run starts (no AI model, here): nothing is made,
    nothing is replaced, and its log says so too."""
    def no_model(*_a, **_k):
        raise RuntimeError("no AI model is installed")

    monkeypatch.setattr(jobs.Worker, "_check_local_model", staticmethod(no_model))
    job = world.run(REMAKE)

    assert job["status"] == "failed" and world.given == []
    assert _ids(world.db) == set(world.old.values())
    assert ("Make clips again: this run failed, so nothing was replaced and the 3 clip(s) from before "
            "are all kept" in capsys.readouterr().out)


def test_a_plain_job_that_fails_says_nothing_about_making_clips_again(world, capsys):
    def run(db):
        raise RuntimeError("the transcript couldn't be read")

    world.plan["run"] = run
    assert world.run({"url": URL, "force": True})["status"] == "failed"
    assert "Make clips again" not in capsys.readouterr().out
    assert set(world.old.values()) == _ids(world.db)


def test_a_run_that_made_no_clips_replaces_nothing(world, capsys):
    job = world.run(REMAKE)
    assert job["status"] == "done"
    assert _ids(world.db) == set(world.old.values())
    assert "made no clips" in capsys.readouterr().out


def test_a_run_given_a_job_without_the_flag_is_process_again_as_before(world, capsys):
    """Process again (force, no replace_untouched): the worker's own database,
    and every clip from before stays beside the new ones."""
    world.plan["run"] = lambda db: _register(db, 12.0, 40.0)
    job = world.run({"url": URL, "force": True, "captions": False})

    assert job["status"] == "done"
    ((url, db, force),) = world.given
    assert type(db) is StateDB and (url, force) == (URL, True)
    assert set(world.old.values()) <= _ids(world.db) and len(_ids(world.db)) == 4
    assert "Make clips again" not in capsys.readouterr().out


def test_a_remake_run_is_given_the_noting_database(world):
    world.run(REMAKE)
    ((url, db, force),) = world.given
    assert type(db) is not StateDB and db.conn is not None and (url, force) == (URL, True)


# ---- QC fix round 3 ---------------------------------------------------------------------


def _files(world) -> dict:
    return {p.name: p.read_bytes() for p in world.clips.iterdir()}


def test_a_remake_doesnt_start_while_one_of_its_clips_is_in_use(world, monkeypatch, capsys):
    """Queued while nothing was busy, it starts while a publish of one of its
    clips is under way: it waits a little (START_WAIT), then ends with
    nothing changed and says why, and the run never starts."""
    from core import in_use, remake

    monkeypatch.setattr(remake, "START_WAIT", 0.4)
    before = _files(world)
    with in_use.holding([world.old["again"]]):
        job = world.run(REMAKE)
    assert job["status"] == "failed" and job["error"].startswith(
        "Clips Kitty was still working with one of this video's clips")
    assert world.given == [] and _files(world) == before
    assert {c: world.db.get_clip(c)["path"] for c in world.old.values()} == {
        c: str(world.clips / name) for c, name in zip(world.old.values(), (
            "clip_00010-00070.mp4", "clip_00200-00270.mp4", "clip_00100-00130.mp4"))}
    assert "waiting for this video's clips to finish being published" in capsys.readouterr().out


def test_a_cancel_while_the_old_clips_are_being_replaced_ends_the_job_cancelled(world, monkeypatch, capsys):
    """Pressed after the worker's last look and before the first clip from
    before changes: finish() checks inside the lock, changes nothing, and
    the job ends cancelled."""
    from core import cancel, remake

    real = remake.classify

    def judged_then_cancelled(*args, **kwargs):
        out = real(*args, **kwargs)
        cancel.request_cancel(VID)              # POST /cancel, now
        return out

    monkeypatch.setattr(remake, "classify", judged_then_cancelled)
    world.plan["run"] = lambda db: _register(db, 12.0, 40.0, folder=world.clips)
    before = {c: dict(world.db.get_clip(c)) for c in world.old.values()}
    try:
        job = world.run(REMAKE)
    finally:
        cancel.clear(VID)
    assert job["status"] == "cancelled"
    assert {c: dict(world.db.get_clip(c)) for c in world.old.values()} == before
    assert (world.clips / "clip_00010-00070.mp4").read_bytes() == b"x"
    assert ("Make clips again: this run was cancelled, so nothing was replaced and the 3 clip(s) from before "
            "are all kept") in capsys.readouterr().out


def test_a_remake_in_which_a_render_failed_replaces_nothing(world, capsys):
    def run(db):
        db.set_outcome(VID, {"clips": 3, "candidates": 4})     # as process_video does before rendering
        _register(db, 12.0, 40.0, json.dumps({"job_captions": False}), world.clips)
        _register(db, 100.0, 130.0, json.dumps({"job_captions": False}), world.clips)

    world.plan["run"] = run
    before = {c: dict(world.db.get_clip(c)) for c in world.old.values()}
    job = world.run(REMAKE)
    assert job["status"] == "done"
    assert {c: dict(world.db.get_clip(c)) for c in world.old.values()} == before
    assert (world.clips / "clip_00100-00130.mp4").read_bytes() == b"x"
    assert len(_ids(world.db)) == 4                              # the new one is added
    assert "1 of the 3 clip(s) this run set out to make couldn't be made, so nothing was replaced" in (
        capsys.readouterr().out)


def test_a_remake_of_the_videos_own_copy_gets_it_back_if_its_run_deletes_it(world):
    """Queued as local:<id> (a file from this PC, or a linked video whose link
    is lost). core/pipeline.py deletes a copy FFprobe can't read in time; the
    job fails, and the copy is back."""
    copy = world.clips.parent / "downloads" / f"{VID}.mp4"
    copy.parent.mkdir()
    copy.write_bytes(b"the video itself")

    def run(db):
        copy.unlink()
        raise ValueError("This file did not finish importing, so its copy could not be read.")

    world.plan["run"] = run
    job = world.run({**REMAKE, "url": f"local:{VID}"})
    assert job["status"] == "failed"
    assert copy.read_bytes() == b"the video itself"
    assert sorted(p.name for p in copy.parent.iterdir()) == [copy.name]


def test_a_clip_exported_in_other_languages_is_marked_and_kept(tmp_path, monkeypatch):
    """The translate job's export stage (POST /translate, stage "export")
    writes the clip's videos in other languages into the person's folder and
    records nothing else: it marks the clip first (core/remake.py
    note_language_export), so Make clips again keeps it. Export all, the
    star and creator learning are left as they were."""
    pytest.importorskip("numpy")
    pytest.importorskip("cv2")
    pytest.importorskip("PIL")
    pytest.importorskip("yaml")
    import llm.registry
    import multilingual.publish
    from core import pipeline, remake
    from main import BUNDLED_CONFIG, load_config

    config = load_config(BUNDLED_CONFIG)
    config["paths"]["data_dir"] = str(tmp_path)
    db = StateDB(tmp_path / "state.db")
    db.upsert_video(VID, title="A stream")
    exported = db.add_clip(VID, 10.0, 40.0, 70, "h", path=str(tmp_path / "c.mp4"), status="queued", title="Mine")
    idle = db.add_clip(VID, 50.0, 80.0, 70, "h", status="queued")
    out = tmp_path / "My exports"
    out.mkdir()

    def publish(lines, languages, folder, stem, llm, **_kw):
        marked = db.get_flag(f"clip_language_export:{exported}")
        written = []
        for code in languages:
            path = Path(folder) / f"{stem}.{code}.mp4"
            path.write_bytes(b"translated" if marked else b"written before its mark")
            written.append(str(path))
        return written

    monkeypatch.setattr(multilingual.publish, "publish", publish)
    monkeypatch.setattr(multilingual.publish, "translate_only", lambda lines, languages, llm, **_kw: {
        code: {"lines": [{"start": 0.0, "end": 1.0, "text": "hola"}], "post": {}} for code in languages})
    monkeypatch.setattr(llm.registry, "create_backend", lambda cfg: object())
    monkeypatch.setattr(pipeline, "_with_usable_model", lambda cfg: cfg)
    monkeypatch.setattr(jobs.Worker, "_caption_lines_for",
                        lambda self, db, clip, data_dir: [{"start": 0.0, "end": 1.0, "text": "hi"}])
    worker = jobs.Worker(config)
    try:
        worker._translate_clips(db, {"clip_ids": [exported], "languages": ["es", "fr"], "stage": "export",
                                     "folder": str(out), "burn": True})
        assert {p.read_bytes() for p in out.iterdir()} == {b"translated"}
        judged = {c["id"]: c for c in remake.classify(db, VID)}
        assert judged[exported]["reasons"] == ["exported"] and judged[idle]["replace"] is True
        row = db.get_clip(exported)
        assert row["exported_at"] == "" and db.conn.execute("SELECT COUNT(*) FROM clip_feedback").fetchone()[0] == 0
        # The review pass (stage "translate") writes no files and marks nothing.
        worker._translate_clips(db, {"clip_ids": [idle], "languages": ["es"], "stage": "translate"})
        assert db.get_flag(f"clip_language_export:{idle}") == ""
    finally:
        db.close()
