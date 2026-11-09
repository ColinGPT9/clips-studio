"""Make clips again keeps every clip from before safe while its run works
and as it ends (#190, core/remake.py; QC fix round 3).

- A render the run holds back (no row points at it until the run is over)
  counts as in use: Storage's clean-up and deleting a clip leave it, and a
  clip is given the run's file only if it is still there.
- Every clip from before is judged again, inside the write lock, right
  before it is made again in place, as before it is deleted; settling never
  deletes a file something may be reading.
- A run in which a render failed replaces nothing.
- A Cancel counts until the first clip from before changes.
- The job doesn't start while one of the video's clips is in use.
- A run of the video's own copy can't lose it.
- A clip exported in other languages is kept.

These run on CI's light install: the clips here have no render options, so
core.pipeline._edited (D33) is never imported, and the files are plain bytes.
"""

import json
import os
import threading
import time
import types
from pathlib import Path

import pytest

from core import cancel, housekeeping, in_use, remake
from core.state import StateDB, _now

VID = "abcdefghijk"
OWN = b"the clip's own file"
NEW = b"the run's render"


def _name(start, end) -> str:
    """The file name a run gives a clip (core/pipeline.py _render_files)."""
    return f"clip_{int(start):05d}-{int(end):05d}.mp4"


@pytest.fixture
def lib(tmp_path):
    """A data folder with one processed video; clip() adds a clip from an
    earlier run, its file named as a run names it, with an editor draft."""
    data = tmp_path / "data"
    db = StateDB(data / "state.db")
    db.upsert_video(VID, title="A stream", channel_name="someone")
    db.set_video_status(VID, "done")
    folder = data / "clips" / "someone" / f"A stream [{VID}]"
    folder.mkdir(parents=True)
    (data / "previews").mkdir()
    cancel.clear(VID)

    def clip(start, end, **fields):
        path = folder / _name(start, end)
        path.write_bytes(OWN)
        clip_id = db.add_clip(VID, start, end, 70, "hook", path=str(path), status="queued", title="Model title")
        if fields:
            db.set_clip(clip_id, **fields)
        (data / "previews" / f"clip_{clip_id}.mp4").write_bytes(b"draft")
        return clip_id

    yield types.SimpleNamespace(db=db, data=data, folder=folder, clip=clip, path=data / "state.db")
    db.conn.close()
    cancel.clear(VID)
    assert in_use.held() == set()


def _render(lib, run, start, end, render_opts=""):
    """One clip of the run, as core/pipeline.py makes it: its file under its
    whole seconds (_render_files), then registered (_register_clip: an
    insert, or the row of the same window pointed at the new file, which the
    run holds back)."""
    path = lib.folder / _name(start, end)
    path.write_bytes(NEW)
    if run.db.add_clip(VID, start, end, 90, "new hook", path=str(path), status="queued", title="Run's title",
                       render_opts=render_opts) is None:
        row = run.db.conn.execute("SELECT id FROM clips WHERE video_id = ? AND start_s = ? AND end_s = ?",
                                  (VID, round(start, 2), round(end, 2))).fetchone()
        run.db.set_clip(row["id"], path=str(path), scores='{"run": 1}')
    return path


def _plays(db, clip_id) -> bytes | None:
    row = db.get_clip(clip_id)
    path = Path(row["path"]) if row is not None and row["path"] else None
    return path.read_bytes() if path is not None and path.is_file() else None


def _other(lib) -> StateDB:
    """A request's own connection, as each API route opens one."""
    return StateDB(lib.path)


# ---- a render the run holds back counts as in use ------------------------------------


def test_storage_clean_up_while_the_run_works_leaves_the_render_it_holds_back(lib):
    """Settings > Storage > Free up deletes every clip file no row points at.
    The run's render of a moment found again has no row until the run is
    over, so it used to go, and the clip made again in place had no video."""
    unused = lib.clip(100.0, 130.0)
    run = remake.Run(lib.db, VID, lib.data)
    held_back = _render(lib, run, 100.0, 130.0)
    _render(lib, run, 300.0, 330.0)
    other = _other(lib)
    try:
        listed = [p.name for p in housekeeping.survey(other, lib.data)["_groups"]["orphan_clips"]]
        housekeeping.clean(other, lib.data)
    finally:
        other.close()
    assert listed == [] and held_back.read_bytes() == NEW
    out = run.finish()
    assert out["replaced"] == 1 and _plays(lib.db, unused) == NEW
    # Once the run is over nothing is held: what no clip plays is the clean-up's again.
    stray = lib.folder / "clip_00999-01000.mp4"
    stray.write_bytes(b"left over")
    other = _other(lib)
    try:
        assert [p.name for p in housekeeping.survey(other, lib.data)["_groups"]["orphan_clips"]] == [stray.name]
    finally:
        other.close()


def test_the_files_of_the_clips_from_before_are_held_until_the_run_is_over(lib):
    kept = lib.clip(10.0, 20.0, edited_at=_now())
    run = remake.Run(lib.db, VID, lib.data)
    aside = Path(lib.db.get_clip(kept)["path"])
    held_back = _render(lib, run, 10.0, 20.0)
    held = in_use.files_held()
    assert {in_use.file_key(aside), in_use.file_key(held_back)} <= held
    run.finish()
    assert in_use.file_key(aside) not in in_use.files_held()
    assert in_use.file_key(held_back) not in in_use.files_held()
    assert _plays(lib.db, kept) == OWN


@pytest.mark.parametrize("how", ["failed", "was cancelled"])
def test_a_run_that_stopped_holds_nothing_either(lib, how):
    lib.clip(10.0, 20.0)
    run = remake.Run(lib.db, VID, lib.data)
    held_back = _render(lib, run, 10.0, 20.0)
    run.stopped(how)
    assert in_use.file_key(held_back) not in in_use.files_held()


def test_deleting_a_new_clip_that_shares_the_held_back_render_leaves_it(lib):
    """A new clip with the same whole seconds as an old clip's moment found
    again (10.4-70.6 s beside 10.2-70.3 s) is written to the same file. No
    other row points at it, so deleting the new clip used to delete it."""
    unused = lib.clip(10.2, 70.3)
    run = remake.Run(lib.db, VID, lib.data)
    shared = _render(lib, run, 10.2, 70.3)
    _render(lib, run, 10.4, 70.6)
    new = lib.db.conn.execute("SELECT id FROM clips WHERE start_s = 10.4").fetchone()[0]
    other = _other(lib)
    try:
        remake.remove_clips(other, [new], lib.data)      # as DELETE /clips/{clip_id} does
        assert other.get_clip(new) is None
        assert remake.file_in_use(other, shared) is True     # no row points at it now: held
    finally:
        other.close()
    assert shared.read_bytes() == NEW
    run.finish()
    assert _plays(lib.db, unused) == NEW


def test_a_clip_whose_new_file_is_gone_keeps_its_own_and_the_log_says_so(lib, capsys):
    """Something deleted the run's render of its moment (by hand, say): the
    clip isn't given a file that isn't there. It keeps its own file and
    everything it had, as if the run hadn't made it again."""
    unused = lib.clip(100.0, 130.0)
    before = dict(lib.db.get_clip(unused))
    run = remake.Run(lib.db, VID, lib.data)
    _render(lib, run, 100.0, 130.0, render_opts=json.dumps({"job_captions": False})).unlink()
    _render(lib, run, 300.0, 330.0)
    out = run.finish()
    assert out["replaced"] == 0 and out["kept_because"] == {"new_file_gone": 1}
    assert dict(lib.db.get_clip(unused)) == before and _plays(lib.db, unused) == OWN
    said = capsys.readouterr().out
    assert "Kept 1: 1 whose new video was gone." in said
    assert "the new video of 1 clip(s) was gone by the end of the run, so they keep their own" in said


# ---- each clip judged again, inside the lock, before it is made again ---------------------


@pytest.mark.parametrize("use", ["retitled", "posted", "exported"])
def test_a_use_recorded_after_judging_keeps_a_clip_the_run_found_again(lib, monkeypatch, use):
    """A use that lands after finish() first judged the clips (a person's
    request, on its own connection) keeps a clip the run found again as it
    is, its file too, as it keeps one about to be deleted."""
    found_again, idle = lib.clip(100.0, 130.0), lib.clip(10.0, 40.0)
    before = dict(lib.db.get_clip(found_again))
    run = remake.Run(lib.db, VID, lib.data)
    _render(lib, run, 100.0, 130.0, render_opts=json.dumps({"job_captions": False}))
    _render(lib, run, 300.0, 330.0)
    real = remake.classify

    def judged_then_used(*args, **kwargs):
        out = real(*args, **kwargs)
        other = _other(lib)
        try:
            for clip_id in (found_again, idle):
                if use == "retitled":
                    other.set_clip(clip_id, title="Mine", edited_at=_now())
                elif use == "posted":
                    other.record_clip_publish(clip_id, "tiktok", {"provider": "woopsocial", "state": "published",
                                                                 "video_id": VID})
                else:
                    other.set_clip(clip_id, exported_at=_now())
        finally:
            other.close()
        return out

    monkeypatch.setattr(remake, "classify", judged_then_used)
    out = run.finish()
    assert out["replaced"] == 0 and out["removed"] == [] and out["kept"] == 2
    row = lib.db.get_clip(found_again)
    assert (row["render_opts"], row["score"], row["hook"]) == (before["render_opts"], 70, "hook")
    assert Path(row["path"]) == Path(before["path"]) and _plays(lib.db, found_again) == OWN


def test_a_clip_found_again_that_a_request_starts_holding_keeps_the_file_it_reads(lib, monkeypatch):
    """A publish that starts right after the clips were judged holds the clip
    (core/in_use.py), then reads its path (its file put aside): the clip
    keeps that file, under that name, and it stays on disk."""
    found_again = lib.clip(100.0, 130.0)
    run = remake.Run(lib.db, VID, lib.data)
    _render(lib, run, 100.0, 130.0)
    _render(lib, run, 300.0, 330.0)
    hold = in_use.holding([found_again])
    sending: dict = {}
    real = remake.classify

    def judged_then_held(*args, **kwargs):
        out = real(*args, **kwargs)
        hold.__enter__()
        sending["path"] = Path(lib.db.get_clip(found_again)["path"])
        return out

    monkeypatch.setattr(remake, "classify", judged_then_held)
    try:
        out = run.finish()
        assert out["kept_because"] == {"busy": 1}
        assert Path(lib.db.get_clip(found_again)["path"]) == sending["path"]
        assert sending["path"].read_bytes() == OWN
    finally:
        hold.__exit__(None, None, None)


def test_settling_keeps_an_old_file_something_started_reading(lib, monkeypatch):
    """A clip made again in place, then held by a request before the run is
    settled (it reads the clip's new path, but may have the old one): its old
    file isn't deleted while something may be reading. Storage's clean-up
    leaves it too, as a file under a put-aside name whose first name a clip
    plays (that may be the clip's own after the app was closed halfway); the
    video's next Make clips again puts the clip's file aside over it."""
    unused = lib.clip(100.0, 130.0)
    run = remake.Run(lib.db, VID, lib.data)
    aside = Path(lib.db.get_clip(unused)["path"])
    _render(lib, run, 100.0, 130.0)
    real = lib.db.set_clip
    hold = in_use.holding([unused])

    def set_clip(clip_id, **fields):
        real(clip_id, **fields)
        if clip_id == unused and "path" in fields:
            hold.__enter__()                # a request starts on it now

    monkeypatch.setattr(lib.db, "set_clip", set_clip)
    try:
        out = run.finish()
        assert out["replaced"] == 1 and _plays(lib.db, unused) == NEW
        assert aside.read_bytes() == OWN
    finally:
        hold.__exit__(None, None, None)
    other = _other(lib)
    try:
        assert aside not in housekeeping.survey(other, lib.data)["_groups"]["orphan_clips"]
    finally:
        other.close()
    remake.Run(lib.db, VID, lib.data).stopped("failed")
    assert _plays(lib.db, unused) == NEW
    assert sorted(p.name for p in lib.folder.iterdir()) == [_name(100.0, 130.0)]


# ---- a run in which a render failed replaces nothing ---------------------------------------


def test_a_run_in_which_a_render_failed_replaces_nothing(lib, capsys):
    """core/pipeline.py records how many clips it is about to render (its
    outcome's `clips`) before it renders, and registers each one it made. Two
    of three made: every clip from before stays as it was, the unused one
    found again too, and the new clip is added."""
    unused, again = lib.clip(10.0, 40.0), lib.clip(100.0, 130.0)
    before = {c: dict(lib.db.get_clip(c)) for c in (unused, again)}
    run = remake.Run(lib.db, VID, lib.data)
    run.db.set_outcome(VID, {"clips": 3, "candidates": 5})
    _render(lib, run, 100.0, 130.0, render_opts=json.dumps({"job_captions": False}))
    _render(lib, run, 300.0, 330.0)
    out = run.finish()
    assert out["renders_failed"] == 1 and out["removed"] == [] and out["replaced"] == 0
    assert {c: dict(lib.db.get_clip(c)) for c in before} == before
    assert _plays(lib.db, unused) == OWN and _plays(lib.db, again) == OWN
    assert lib.db.conn.execute("SELECT COUNT(*) FROM clips WHERE start_s = 300.0").fetchone()[0] == 1
    assert ("Make clips again: 1 of the 3 clip(s) this run set out to make couldn't be made, so nothing was "
            "replaced: the 2 clip(s) from before are all kept, and the 1 new clip(s) it made are added beside "
            "them") in capsys.readouterr().out


def test_a_run_that_made_every_clip_it_set_out_to_replaces(lib):
    unused = lib.clip(10.0, 40.0)
    run = remake.Run(lib.db, VID, lib.data)
    run.db.set_outcome(VID, {"clips": 2})
    _render(lib, run, 100.0, 130.0)
    _render(lib, run, 300.0, 330.0)
    assert run.renders_failed() == 0
    assert run.finish()["removed"] == [unused]


def test_only_the_runs_own_shorts_count_toward_what_it_set_out_to_make(lib):
    """Longform's record of a match, another video's outcome and a value
    that isn't a count say nothing about this run's renders."""
    lib.clip(10.0, 40.0)
    run = remake.Run(lib.db, VID, lib.data)
    run.db.upsert_video("other", title="Other")
    run.db.set_outcome("other", {"clips": 9})
    run.db.set_outcome(VID, {"sport": {}, "longform_only": True, "clips": 9})
    run.db.set_outcome(VID, {"clips": True})
    assert run.to_make is None
    run.db.set_outcome(VID, {"clips": 1})
    _render(lib, run, 0.0, 600.0, render_opts=json.dumps({"profile": "highlights"}))
    _render(lib, run, 0.0, 95.0, render_opts=json.dumps({"reel": "recap"}))
    assert run.renders_failed() == 1           # a Longform output or a reel isn't one of its clips
    run.stopped("failed")


def test_core_pipeline_says_how_many_it_will_render_before_it_renders():
    """What renders_failed() reads: process_video records its outcome, whose
    `clips` is how many candidates it kept (core/outcome.py summarise_run),
    through the database it was given, before its render loop."""
    from core.outcome import summarise_run

    candidates = [types.SimpleNamespace(score=80, subscores={}) for _ in range(3)]
    assert summarise_run(candidates, [], {"clips": {}})["clips"] == 3
    source = (Path(__file__).resolve().parent.parent / "core" / "pipeline.py").read_text(encoding="utf-8")
    body = source[source.index("def process_video("):source.index("def _sport_reels(")]
    recorded = body.index("db.set_outcome(video.video_id, outcome)")
    assert body.index("outcome = summarise_run(candidates, rejections, config)") < recorded
    assert recorded < body.index("_render_files, video.path, candidate")
    assert recorded < body.index("clip = _register_clip(db, video.video_id, candidate, final_path")


# ---- a Cancel while finish() works ----------------------------------------------------------


def test_a_cancel_before_the_first_change_changes_nothing_and_ends_the_job_cancelled(lib, monkeypatch, capsys):
    unused, again = lib.clip(10.0, 40.0), lib.clip(100.0, 130.0)
    before = {c: dict(lib.db.get_clip(c)) for c in (unused, again)}
    run = remake.Run(lib.db, VID, lib.data)
    _render(lib, run, 100.0, 130.0)
    _render(lib, run, 300.0, 330.0)
    real = remake.classify

    def judged_then_cancelled(*args, **kwargs):
        out = real(*args, **kwargs)
        cancel.request_cancel(VID)            # POST /cancel, while finish() works
        return out

    monkeypatch.setattr(remake, "classify", judged_then_cancelled)
    with pytest.raises(cancel.CancelledError):
        run.finish()
    assert {c: dict(lib.db.get_clip(c)) for c in before} == before
    assert _plays(lib.db, unused) == OWN and _plays(lib.db, again) == OWN
    assert ("Make clips again: this run was cancelled, so nothing was replaced and the 2 clip(s) from before "
            "are all kept") in capsys.readouterr().out
    run.stopped("was cancelled")              # the worker's handler: says nothing more
    assert capsys.readouterr().out == ""


def test_a_cancel_after_the_first_change_is_too_late(lib, monkeypatch):
    """Once a clip from before has changed, the rest change too, and the job
    ends done: a run half replaced would keep some clips and not others."""
    first, second = lib.clip(10.0, 40.0), lib.clip(50.0, 80.0)
    run = remake.Run(lib.db, VID, lib.data)
    _render(lib, run, 300.0, 330.0)
    real = lib.db.delete_clip

    def delete_clip(clip_id):
        path = real(clip_id)
        cancel.request_cancel(VID)            # pressed right after the first one went
        return path

    monkeypatch.setattr(lib.db, "delete_clip", delete_clip)
    out = run.finish()
    assert sorted(out["removed"]) == sorted([first, second])


def test_a_cancel_while_finish_works_with_nothing_to_change_ends_the_job_cancelled(lib, monkeypatch):
    kept = lib.clip(10.0, 40.0, edited_at=_now())
    run = remake.Run(lib.db, VID, lib.data)
    _render(lib, run, 300.0, 330.0)
    real = remake.classify

    def judged_then_cancelled(*args, **kwargs):
        out = real(*args, **kwargs)
        cancel.request_cancel(VID)
        return out

    monkeypatch.setattr(remake, "classify", judged_then_cancelled)
    with pytest.raises(cancel.CancelledError):
        run.finish()
    assert _plays(lib.db, kept) == OWN


# ---- the job doesn't start while one of the video's clips is in use ---------------------------


BUSY_ONE = {
    "held by a request": lambda lib, c: in_use.holding([c]),
    "on the editor lane": None,
    "a publish waiting": lambda lib, c: lib.db.add_publish_job(c, "{}", video_id=VID, start_s=10.0, end_s=40.0),
    "a publish under way, by its window": lambda lib, c: lib.db.conn.execute(
        "UPDATE publish_jobs SET status = 'running' WHERE id = ?",
        (lib.db.add_publish_job(999, "{}", video_id=VID, start_s=10.0, end_s=40.0),)),
}


@pytest.mark.parametrize("how", sorted(BUSY_ONE))
def test_the_job_ends_with_nothing_changed_while_a_clip_stays_in_use(lib, how):
    clip_id, other = lib.clip(10.0, 40.0), lib.clip(50.0, 80.0)
    before = {c: dict(lib.db.get_clip(c)) for c in (clip_id, other)}
    busy, hold = (lambda: ()), None
    if how == "on the editor lane":
        busy = lambda: {clip_id}                               # noqa: E731
    else:
        made = BUSY_ONE[how](lib, clip_id)
        lib.db.conn.commit()
        if how == "held by a request":
            hold = made
            hold.__enter__()
    assert remake.busy_clips(lib.db, VID, busy()) == {clip_id}
    try:
        with pytest.raises(RuntimeError, match="still working with one of this video's clips"):
            remake.Run(lib.db, VID, lib.data, busy=busy, wait=0.3)
    finally:
        if hold is not None:
            hold.__exit__(None, None, None)
    assert {c: dict(lib.db.get_clip(c)) for c in before} == before
    assert sorted(p.name for p in lib.folder.iterdir()) == [_name(10.0, 40.0), _name(50.0, 80.0)]


def test_the_job_waits_for_a_clip_in_use_and_starts_once_it_is_free(lib, capsys):
    clip_id = lib.clip(10.0, 40.0)
    hold = in_use.holding([clip_id])
    hold.__enter__()
    threading.Timer(0.4, lambda: hold.__exit__(None, None, None)).start()
    started = time.monotonic()
    run = remake.Run(lib.db, VID, lib.data, wait=10)
    assert 0.3 < time.monotonic() - started < 9
    assert Path(lib.db.get_clip(clip_id)["path"]).name == f"clip_00010-00040.clip-{clip_id}.mp4"
    assert "waiting for this video's clips to finish being published, exported or edited" in capsys.readouterr().out
    run.stopped("failed")


def test_a_cancel_while_the_job_waits_stops_it(lib, capsys):
    clip_id = lib.clip(10.0, 40.0)
    counted = cancel.times_requested(VID)
    threading.Timer(0.3, lambda: cancel.request_cancel(VID)).start()
    with in_use.holding([clip_id]):
        with pytest.raises(cancel.CancelledError):
            remake.Run(lib.db, VID, lib.data, cancels=counted, wait=10)
    assert _plays(lib.db, clip_id) == OWN and Path(lib.db.get_clip(clip_id)["path"]).name == _name(10.0, 40.0)
    assert "this run was cancelled, so nothing was replaced" in capsys.readouterr().out


def test_no_request_can_start_holding_a_clip_while_the_files_move(lib, monkeypatch):
    """The check and every rename happen inside one in_use.settled(): a
    request that starts then waits, and finds the clip's file aside."""
    clip_id = lib.clip(10.0, 40.0)
    seen: dict = {}
    real = remake._rename

    def rename(src, dst):
        if "thread" not in seen:
            def request():
                with in_use.holding([clip_id]):
                    seen["path"] = _other_path(lib, clip_id)
            seen["thread"] = threading.Thread(target=request, daemon=True)
            seen["thread"].start()
            time.sleep(0.3)
            seen["waited"] = seen["thread"].is_alive()
        real(src, dst)

    monkeypatch.setattr(remake, "_rename", rename)
    run = remake.Run(lib.db, VID, lib.data, wait=5)
    seen["thread"].join(10)
    assert seen["waited"] is True and Path(seen["path"]).name == f"clip_00010-00040.clip-{clip_id}.mp4"
    run.stopped("failed")


def _other_path(lib, clip_id) -> str:
    other = _other(lib)
    try:
        return other.get_clip(clip_id)["path"]
    finally:
        other.close()


# ---- the video's own copy --------------------------------------------------------------------


def _copy(lib, video_id=VID, ext=".mp4") -> Path:
    path = lib.data / "downloads" / f"{video_id}{ext}"
    path.parent.mkdir(exist_ok=True)
    path.write_bytes(b"the video itself")
    return path


def test_a_run_of_the_videos_own_copy_gets_it_back_if_the_run_deletes_it(lib, capsys):
    """core/pipeline.py deletes a copy (local:<id>) FFprobe can't read in time,
    and for a linked video whose link is lost, or a file from this PC, it
    can't be got again."""
    lib.clip(10.0, 40.0)
    copy = _copy(lib)
    run = remake.Run(lib.db, VID, lib.data, url=f"local:{VID}")
    second = copy.with_name(f"{VID}.remaking.mp4")
    assert second.read_bytes() == b"the video itself"
    other = _other(lib)
    try:
        assert second not in housekeeping.survey(other, lib.data)["_groups"]["orphan_downloads"]
    finally:
        other.close()
    copy.unlink()                                  # the run's first step
    run.stopped("failed")
    assert copy.read_bytes() == b"the video itself" and not second.exists()
    assert "this video's own copy was put back" in capsys.readouterr().out


def test_the_second_name_goes_once_the_run_is_over(lib):
    lib.clip(10.0, 40.0)
    copy = _copy(lib)
    run = remake.Run(lib.db, VID, lib.data, url=f"local:{VID}")
    _render(lib, run, 300.0, 330.0)
    run.finish()
    assert copy.read_bytes() == b"the video itself"
    assert sorted(p.name for p in copy.parent.iterdir()) == [copy.name]


def test_a_link_run_keeps_no_second_name(lib):
    copy = _copy(lib)
    run = remake.Run(lib.db, VID, lib.data, url=f"https://www.youtube.com/watch?v={VID}")
    assert run.source_kept is None and sorted(p.name for p in copy.parent.iterdir()) == [copy.name]
    run.stopped("failed")


def test_a_deleted_video_doesnt_get_its_copy_back(lib):
    copy = _copy(lib)
    run = remake.Run(lib.db, VID, lib.data, url=f"local:{VID}")
    copy.unlink()
    lib.db.conn.execute("DELETE FROM videos WHERE video_id = ?", (VID,))   # DELETE /videos/{id} meanwhile
    lib.db.conn.commit()
    run.stopped("was cancelled")
    assert list(copy.parent.iterdir()) == []


def test_a_second_name_a_run_the_app_closed_halfway_left_is_put_back(lib):
    copy = _copy(lib)
    left = copy.with_name(f"{VID}.remaking.mp4")
    copy.rename(left)
    run = remake.Run(lib.db, VID, lib.data, url=f"local:{VID}")
    assert copy.read_bytes() == b"the video itself"
    run.stopped("failed")
    assert sorted(p.name for p in copy.parent.iterdir()) == [copy.name]


def test_without_hard_links_an_unreadable_copy_stops_the_job_before_the_run(lib, monkeypatch):
    import video.encoding

    copy = _copy(lib)
    clip_id = lib.clip(10.0, 40.0)

    def no_links(*_a, **_k):
        raise OSError(1, "Incorrect function")       # an exFAT drive

    monkeypatch.setattr(os, "link", no_links)
    monkeypatch.setattr(video.encoding, "readable_video", lambda path: False)
    with pytest.raises(RuntimeError, match="couldn't read this video's own copy"):
        remake.Run(lib.db, VID, lib.data, url=f"local:{VID}")
    assert copy.read_bytes() == b"the video itself"
    assert Path(lib.db.get_clip(clip_id)["path"]).name == _name(10.0, 40.0)
    monkeypatch.setattr(video.encoding, "readable_video", lambda path: True)
    remake.Run(lib.db, VID, lib.data, url=f"local:{VID}").stopped("failed")
    assert copy.read_bytes() == b"the video itself"


# ---- a clip exported in other languages -------------------------------------------------------


def test_a_clip_exported_in_other_languages_is_kept(lib):
    exported, idle = lib.clip(10.0, 40.0), lib.clip(50.0, 80.0)
    remake.note_language_export(lib.db, lib.db.get_clip(exported), ["es", "fr"])
    remake.note_language_export(lib.db, lib.db.get_clip(exported), ["de"])
    mark = json.loads(lib.db.get_flag(f"clip_language_export:{exported}"))
    assert mark["languages"] == ["de", "es", "fr"] and mark["video_id"] == VID
    judged = {c["id"]: c for c in remake.classify(lib.db, VID)}
    assert judged[exported]["reasons"] == ["exported"] and judged[idle]["replace"] is True
    # Nothing else reads it: the star, Export all and creator learning are as they were.
    row = lib.db.get_clip(exported)
    assert row["exported_at"] == "" and row["edited_at"] == ""
    assert lib.db.conn.execute("SELECT COUNT(*) FROM clip_feedback").fetchone()[0] == 0
    run = remake.Run(lib.db, VID, lib.data)
    _render(lib, run, 300.0, 330.0)
    out = run.finish()
    assert out["removed"] == [idle] and out["kept_because"] == {"exported": 1}


def test_discarding_its_translated_text_doesnt_unmark_an_export(lib):
    exported = lib.clip(10.0, 40.0)
    lib.db.save_translation(exported, "es", "[]")
    remake.note_language_export(lib.db, lib.db.get_clip(exported), ["es"])
    lib.db.delete_translation(exported, "es")       # DELETE /clips/{id}/translations/es
    assert {c["id"]: c for c in remake.classify(lib.db, VID)}[exported]["reasons"] == ["exported"]


def test_an_unreadable_export_mark_still_keeps_its_clip(lib):
    exported = lib.clip(10.0, 40.0)
    lib.db.set_flag(f"clip_language_export:{exported}", "{not json")
    assert {c["id"]: c for c in remake.classify(lib.db, VID)}[exported]["reasons"] == ["exported"]


# ---- the app closed in the middle of a run (QC fix round 5) ----------------------------------
#
# tests/test_remake_crash.py kills a real process at every point; these show
# each piece on its own, in one process.


def _journal(lib) -> dict | None:
    raw = lib.db.get_flag(remake.JOURNAL + VID, "")
    return json.loads(raw) if raw else None


class _Closed(BaseException):
    """The app closed: nothing after it runs (no `except Exception` sees it)."""


def test_the_plan_is_saved_before_the_first_file_moves_and_goes_once_settled(lib, monkeypatch):
    kept, unused = lib.clip(10.0, 40.0, edited_at=_now()), lib.clip(50.0, 80.0)
    seen = []
    real = remake._rename

    def rename(src, dst):
        seen.append(_journal(lib))
        real(src, dst)

    monkeypatch.setattr(remake, "_rename", rename)
    run = remake.Run(lib.db, VID, lib.data)
    assert [e["state"] for e in seen[0]["entries"]] == ["planned", "planned"]
    assert sorted(i for e in seen[0]["entries"] for i in e["ids"]) == sorted([kept, unused])
    # Each file's step is saved in the commit that points its rows aside.
    assert [e["state"] for e in seen[1]["entries"]] == ["aside", "planned"]
    assert [e["state"] for e in _journal(lib)["entries"]] == ["aside", "aside"]
    _render(lib, run, 300.0, 330.0)
    run.finish()
    assert _journal(lib) is None


def test_a_row_left_on_its_put_aside_name_is_pointed_back_at_its_file(lib):
    """(a) The app closed as a file went back under its first name, before
    its row followed: the row says clip_...clip-<id>.mp4, which isn't there,
    and the file has its first name. Storage's clean-up leaves the file, and
    the next Make clips again points the row back at it and puts it aside as
    any file, so the run's render of the moment can't write over it."""
    kept = lib.clip(10.0, 40.0, edited_at=_now())
    first = lib.folder / _name(10.0, 40.0)
    lib.db.set_clip(kept, path=str(remake._aside_name(first, kept)))     # the row, as the app left it
    other = _other(lib)
    try:
        housekeeping.clean(other, lib.data)
    finally:
        other.close()
    assert first.read_bytes() == OWN
    run = remake.Run(lib.db, VID, lib.data)
    assert not first.exists() and _plays(lib.db, kept) == OWN            # put aside, and its row with it
    _render(lib, run, 10.0, 40.0)                                         # its moment, found again
    out = run.finish()
    assert out["kept_because"] == {"edited": 1}
    assert Path(lib.db.get_clip(kept)["path"]) == first and _plays(lib.db, kept) == OWN


def test_settling_deletes_no_file_the_run_did_not_write(lib):
    """(b) A kept clip whose file isn't under its put-aside name as the run
    ends, while a file the run didn't write is under its first name (put
    back by an app closed halfway, or by hand): that file is the clip's, and
    it plays it. The run's render of a clip whose file went missing is the
    run's: it goes, and that clip stays without a file."""
    kept, missing = lib.clip(10.0, 40.0, edited_at=_now()), lib.clip(50.0, 80.0, edited_at=_now())
    run = remake.Run(lib.db, VID, lib.data)
    os.replace(lib.db.get_clip(kept)["path"], lib.folder / _name(10.0, 40.0))     # not by the run
    Path(lib.db.get_clip(missing)["path"]).unlink()
    _render(lib, run, 50.0, 80.0)
    run.finish()
    assert _plays(lib.db, kept) == OWN
    assert _plays(lib.db, missing) is None and not (lib.folder / _name(50.0, 80.0)).exists()


@pytest.mark.parametrize("closed", ["before the file moves", "after the file moved"])
def test_a_file_goes_back_only_once_its_row_says_so(lib, monkeypatch, closed):
    """(c) Settling points a kept clip's row back at its first name, in one
    commit with that step in the journal ("back"), before the file follows.
    The app closed in between leaves the row on its first name and the file
    aside, which the start-up recovery finishes; closed after, the row
    already says where the file is."""
    kept = lib.clip(10.0, 40.0, edited_at=_now())
    run = remake.Run(lib.db, VID, lib.data)
    first = lib.folder / _name(10.0, 40.0)
    real = remake._put_back

    def put_back(aside, own):
        assert lib.db.get_clip(kept)["path"] == str(first)
        assert [e["state"] for e in _journal(lib)["entries"]] == ["back"]
        if closed == "after the file moved":
            assert real(aside, own)
        raise _Closed()

    monkeypatch.setattr(remake, "_put_back", put_back)
    with pytest.raises(_Closed):
        run.stopped("was cancelled")
    monkeypatch.setattr(remake, "_put_back", real)
    in_use.let_go_of_files(run)                                          # a new process holds nothing
    assert _plays(lib.db, kept) == (OWN if closed == "after the file moved" else None)
    assert remake.recover(lib.db, lib.data) == 1 and _journal(lib) is None
    assert Path(lib.db.get_clip(kept)["path"]) == first and _plays(lib.db, kept) == OWN


def test_storage_clean_up_never_takes_a_clips_file_under_its_other_name(lib):
    """(d) An app closed halfway can leave a clip's row on one name of its
    file and the file under the other. Storage's clean-up leaves both, and
    still takes a file no clip plays under either name."""
    on_first, on_aside = lib.clip(10.0, 40.0), lib.clip(50.0, 80.0)
    a = lib.folder / _name(10.0, 40.0)
    os.replace(a, remake._aside_name(a, on_first))                        # row: first name; file: aside
    b = lib.folder / _name(50.0, 80.0)
    lib.db.set_clip(on_aside, path=str(remake._aside_name(b, on_aside)))  # row: aside; file: first name
    stray = lib.folder / _name(900.0, 930.0)
    stray_aside = lib.folder / f"clip_00950-00980.clip-{on_first}.mp4"
    for path in (stray, stray_aside):
        path.write_bytes(b"left over")
    other = _other(lib)
    try:
        listed = {p.name for p in housekeeping.survey(other, lib.data)["_groups"]["orphan_clips"]}
        housekeeping.clean(other, lib.data)
    finally:
        other.close()
    assert listed == {stray.name, stray_aside.name}
    assert remake._aside_name(a, on_first).read_bytes() == OWN and b.read_bytes() == OWN


def test_storage_clean_up_leaves_the_second_name_of_a_known_videos_own_copy(lib):
    """The app closed after the run deleted the video's own copy: its second
    name is the only one left until the next start puts it back."""
    copy = _copy(lib)
    second = copy.with_name(f"{VID}.remaking.mp4")
    copy.rename(second)
    stranger = copy.with_name("zzzzzzzzzzz.remaking.mp4")
    stranger.write_bytes(b"another video's")
    other = _other(lib)
    try:
        listed = housekeeping.survey(other, lib.data)["_groups"]["orphan_downloads"]
    finally:
        other.close()
    assert listed == [stranger]


def test_the_start_up_recovery_finishes_an_interrupted_run(lib, capsys):
    """A run the app was closed in the middle of (never settled; a new
    process holds nothing), with its render over a kept clip's first name
    and the video's own copy deleted: recover() puts every file back where
    its row says, the copy too, and drops the journal. Again, it finds
    nothing to do."""
    kept, unused = lib.clip(10.0, 40.0, edited_at=_now()), lib.clip(50.0, 80.0)
    copy = _copy(lib)
    run = remake.Run(lib.db, VID, lib.data, url=f"local:{VID}")
    _render(lib, run, 10.0, 40.0)
    copy.unlink()
    in_use.let_go_of_files(run)
    assert remake.recover(lib.db, lib.data) == 1
    assert (_plays(lib.db, kept), _plays(lib.db, unused)) == (OWN, OWN)
    assert [Path(lib.db.get_clip(c)["path"]).name for c in (kept, unused)] == [_name(10.0, 40.0), _name(50.0, 80.0)]
    assert copy.read_bytes() == b"the video itself" and _journal(lib) is None
    assert "has its own video back" in capsys.readouterr().out
    assert remake.recover(lib.db, lib.data) == 0


def test_a_make_clips_again_finishes_its_videos_interrupted_run_before_it_starts(lib):
    kept = lib.clip(10.0, 40.0, edited_at=_now())
    left = remake.Run(lib.db, VID, lib.data)
    in_use.let_go_of_files(left)
    run = remake.Run(lib.db, VID, lib.data)
    entries = _journal(lib)["entries"]
    assert [(e["own"], e["state"]) for e in entries] == [(str(lib.folder / _name(10.0, 40.0)), "aside")]
    _render(lib, run, 300.0, 330.0)
    run.finish()
    assert Path(lib.db.get_clip(kept)["path"]).name == _name(10.0, 40.0) and _plays(lib.db, kept) == OWN


def test_an_unreadable_journal_moves_nothing(lib, capsys):
    kept = lib.clip(10.0, 40.0)
    run = remake.Run(lib.db, VID, lib.data)
    aside = Path(lib.db.get_clip(kept)["path"])
    in_use.let_go_of_files(run)
    lib.db.set_flag(remake.JOURNAL + VID, "{not json")
    assert remake.recover(lib.db, lib.data) == 0
    assert lib.db.get_flag(remake.JOURNAL + VID, "") == ""
    assert Path(lib.db.get_clip(kept)["path"]) == aside and aside.read_bytes() == OWN
    assert "couldn't be read" in capsys.readouterr().out
