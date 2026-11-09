"""Make clips again leaves every clip from before its own file (#190,
core/remake.py), and a Cancel pressed at any time stops it.

A run writes each clip's file under a name made from its whole seconds
(core/pipeline.py _render_files: clip_00010-00020.mp4), and points a row it
finds again at the new file (_register_clip). So as a Make clips again job
starts, its video's clip files are put out of the run's way (Run.set_aside),
what the run would write on their rows is held back, and once it is over:

- a clip nobody used that the run made again takes the run's new file;
- one nobody used that it didn't make again goes, with its file;
- every other one keeps its own file and row, back under its first name
  unless a new clip took that name; the run's render of its moment goes;
- after a run that failed or was cancelled, every one is as it was.

The first tests run on CI's light install, with plain files standing in for
renders. The `rendering` ones render for real under tests/render_harness.py
(FFmpeg, the tracker and the detectors faked), and the real pipeline's
Cancel tests need the engine: those skip without it.
"""

import json
import types
from pathlib import Path

import pytest

from core import cancel, in_use, remake
from core.state import StateDB, _now

ROOT = Path(__file__).resolve().parent.parent
VID = "abcdefghijk"
URL = f"https://www.youtube.com/watch?v={VID}"


def _name(start, end) -> str:
    """The file name a run gives a clip (core/pipeline.py _render_files)."""
    return f"clip_{int(start):05d}-{int(end):05d}.mp4"


def _said(start, end) -> bytes:
    return f"clip {start}-{end} from before".encode()


@pytest.fixture
def lib(tmp_path):
    """A data folder with one processed video. clip() adds a clip from an
    earlier run, its file named as a run names it and holding its own
    words, with an editor draft."""
    data = tmp_path / "data"
    db = StateDB(data / "state.db")
    db.upsert_video(VID, title="A stream", channel_name="someone")
    db.set_video_status(VID, "done")
    folder = data / "clips" / "someone" / f"A stream [{VID}]"
    folder.mkdir(parents=True)
    (data / "previews").mkdir()
    cancel.clear(VID)

    def clip(start, end, name=None, render_opts="", **fields):
        path = folder / (name or _name(start, end))
        path.write_bytes(_said(start, end))
        clip_id = db.add_clip(VID, start, end, 70, "hook", path=str(path), status="queued", title="Model title",
                              render_opts=render_opts)
        if fields:
            db.set_clip(clip_id, **fields)
        (data / "previews" / f"clip_{clip_id}.mp4").write_bytes(b"draft")
        return clip_id

    yield types.SimpleNamespace(db=db, data=data, folder=folder, clip=clip)
    db.conn.close()
    cancel.clear(VID)
    assert in_use.held() == set()


def _render(lib, run, start, end, content=b"the run's render", render_opts=""):
    """One clip of the run, as core/pipeline.py makes it: its file written
    under its whole seconds (_render_files), then registered (_register_clip:
    an insert, or the row of the same window pointed at the new file)."""
    path = lib.folder / _name(start, end)
    path.write_bytes(content)
    if run.db.add_clip(VID, start, end, 90, "new hook", path=str(path), status="queued", title="Run's title",
                       render_opts=render_opts) is None:
        row = run.db.conn.execute("SELECT id FROM clips WHERE video_id = ? AND start_s = ? AND end_s = ?",
                                  (VID, round(start, 2), round(end, 2))).fetchone()
        run.db.set_clip(row["id"], path=str(path), scores='{"run": 1}')
    return path


def _row(db, clip_id) -> dict | None:
    row = db.get_clip(clip_id)
    return None if row is None else dict(row)


def _files(lib) -> list[str]:
    return sorted(p.name for p in lib.folder.iterdir())


# ---- the files, as the job starts ----------------------------------------------------


def test_as_the_job_starts_each_clips_file_is_put_out_of_the_runs_way(lib):
    a, b = lib.clip(10.0, 20.0), lib.clip(30.0, 45.5)
    remake.Run(lib.db, VID, lib.data)
    for clip_id, (start, end) in ((a, (10.0, 20.0)), (b, (30.0, 45.5))):
        path = Path(lib.db.get_clip(clip_id)["path"])
        assert path.parent == lib.folder
        assert path.name == f"clip_{int(start):05d}-{int(end):05d}.clip-{clip_id}.mp4"
        assert path.read_bytes() == _said(start, end)
        assert not (lib.folder / _name(start, end)).exists()       # where the run writes


def test_a_file_something_may_be_reading_is_copied_and_left_where_it_was(lib):
    """An export on the lane may have read its path already: its file is
    copied aside, and the export still finds it under its first name."""
    exporting, idle = lib.clip(10.0, 20.0), lib.clip(30.0, 40.0)
    run = remake.Run(lib.db, VID, lib.data, busy=lambda: {exporting})
    aside = Path(lib.db.get_clip(exporting)["path"])
    assert aside.name == f"clip_00010-00020.clip-{exporting}.mp4" and aside.read_bytes() == _said(10.0, 20.0)
    assert (lib.folder / _name(10.0, 20.0)).read_bytes() == _said(10.0, 20.0)
    assert not (lib.folder / _name(30.0, 40.0)).exists()           # nobody reads that one: moved
    _render(lib, run, 100.0, 130.0)
    out = run.finish()
    # Still exporting: kept, on its copy, and the export's file is left alone.
    assert out["kept_because"] == {"busy": 1} and out["removed"] == [idle]
    assert lib.db.get_clip(exporting)["path"] == str(aside)
    assert (lib.folder / _name(10.0, 20.0)).exists()


def test_a_request_or_a_youtube_publish_under_way_finds_its_file(lib):
    held, publishing = lib.clip(10.0, 20.0), lib.clip(30.0, 40.0)
    lib.db.add_publish_job(publishing, "{}", video_id=VID, start_s=30.0, end_s=40.0)
    with in_use.holding([held]):
        remake.Run(lib.db, VID, lib.data)
    assert (lib.folder / _name(10.0, 20.0)).read_bytes() == _said(10.0, 20.0)
    assert (lib.folder / _name(30.0, 40.0)).read_bytes() == _said(30.0, 40.0)


def test_a_file_windows_wont_rename_is_copied(lib, monkeypatch):
    """A player showing the clip holds its file open, and Windows then
    refuses to rename it."""
    playing = lib.clip(10.0, 20.0)
    real = remake._rename

    def held_open(src, dst):
        if Path(src).name == _name(10.0, 20.0):
            raise PermissionError(13, "The process cannot access the file because it is being used")
        real(src, dst)

    monkeypatch.setattr(remake, "_rename", held_open)
    remake.Run(lib.db, VID, lib.data)
    aside = Path(lib.db.get_clip(playing)["path"])
    assert aside != lib.folder / _name(10.0, 20.0)
    assert aside.read_bytes() == (lib.folder / _name(10.0, 20.0)).read_bytes() == _said(10.0, 20.0)


def test_when_a_file_cant_be_put_aside_the_job_stops_and_nothing_changes(lib, monkeypatch):
    first, second = lib.clip(10.0, 20.0), lib.clip(30.0, 40.0)
    before = {c: _row(lib.db, c) for c in (first, second)}
    real = remake._rename

    def held_open(src, dst):
        if Path(src).name == _name(30.0, 40.0):
            raise PermissionError(13, "in use")
        real(src, dst)

    def disk_full(*_a, **_k):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(remake, "_rename", held_open)
    monkeypatch.setattr(remake.shutil, "copy2", disk_full)
    with pytest.raises(RuntimeError, match="couldn't be kept safe"):
        remake.Run(lib.db, VID, lib.data)
    assert {c: _row(lib.db, c) for c in before} == before
    assert _files(lib) == [_name(10.0, 20.0), _name(30.0, 40.0)]
    assert (lib.folder / _name(10.0, 20.0)).read_bytes() == _said(10.0, 20.0)


def test_a_file_outside_the_data_folder_stays_where_it_is(lib, tmp_path):
    clip_id = lib.clip(10.0, 20.0, edited_at=_now())
    elsewhere = tmp_path / "Videos" / "mine.mp4"
    elsewhere.parent.mkdir()
    elsewhere.write_bytes(b"mine")
    lib.db.set_clip(clip_id, path=str(elsewhere))
    run = remake.Run(lib.db, VID, lib.data)
    assert lib.db.get_clip(clip_id)["path"] == str(elsewhere)
    _render(lib, run, 50.0, 60.0)
    run.finish()
    assert lib.db.get_clip(clip_id)["path"] == str(elsewhere) and elsewhere.read_bytes() == b"mine"


# ---- once the run has finished ---------------------------------------------------------


KEPT = {
    "published": lambda db, c: db.record_upload(c, "yt1"),
    "edited": lambda db, c: db.set_clip(c, title="My own title", edited_at=_now()),
    "scheduled": lambda db, c: db.set_clip(c, status="scheduled", scheduled_for="2026-11-09"),
    "exported": lambda db, c: db.set_clip(c, exported_at=_now()),
    "translated": lambda db, c: db.save_translation(c, "es", "[]", edited=True),
}


@pytest.mark.parametrize("kind", sorted(KEPT))
def test_a_kept_clip_the_run_finds_again_keeps_its_own_file_and_row(lib, kind):
    kept = lib.clip(10.0, 20.0)
    KEPT[kind](lib.db, kept)
    before = _row(lib.db, kept)
    run = remake.Run(lib.db, VID, lib.data)
    _render(lib, run, 10.0, 20.0)                # the same moment, with the new settings
    assert _row(lib.db, kept)["scores"] == before["scores"]      # nothing written on it meanwhile
    out = run.finish()
    assert out["made"] == 1 and out["kept"] == 1 and out["removed"] == [] and out["replaced"] == 0
    assert _row(lib.db, kept) == before          # its row, its first name included
    assert Path(before["path"]).read_bytes() == _said(10.0, 20.0)
    assert _files(lib) == [_name(10.0, 20.0)]    # the run's render of it went


def test_an_unused_clip_the_run_finds_again_takes_the_new_file(lib):
    unused = lib.clip(10.0, 20.0)
    run = remake.Run(lib.db, VID, lib.data)
    _render(lib, run, 10.0, 20.0, render_opts=json.dumps({"job_captions": False}))
    out = run.finish()
    row = lib.db.get_clip(unused)
    assert out["replaced"] == 1 and out["removed"] == []
    assert row["path"] == str(lib.folder / _name(10.0, 20.0))
    assert Path(row["path"]).read_bytes() == b"the run's render"
    assert json.loads(row["render_opts"]) == {"job_captions": False} and row["scores"] == '{"run": 1}'
    assert (row["score"], row["hook"], row["title"]) == (90, "new hook", "Model title")
    assert _files(lib) == [_name(10.0, 20.0)]    # its old file went


def test_an_unused_clip_the_run_doesnt_find_again_goes_with_its_file(lib):
    unused = lib.clip(10.0, 20.0)
    run = remake.Run(lib.db, VID, lib.data)
    _render(lib, run, 100.0, 130.0)
    out = run.finish()
    assert out["removed"] == [unused] and lib.db.get_clip(unused) is None
    assert _files(lib) == [_name(100.0, 130.0)]
    assert not (lib.data / "previews" / f"clip_{unused}.mp4").exists()


def test_a_new_moment_with_the_same_whole_seconds_leaves_a_kept_clip_its_file(lib):
    """The run's 10.4-20.6 s clip is written as clip_00010-00020.mp4, the
    kept 10.0-20.0 s clip's first name: the new clip keeps that name, and the
    kept one its own file under the name it was put aside under. Deleting the
    new near-duplicate leaves the kept clip its file."""
    kept = lib.clip(10.0, 20.0, edited_at=_now())
    run = remake.Run(lib.db, VID, lib.data)
    _render(lib, run, 10.4, 20.6)
    run.finish()
    new = lib.db.conn.execute("SELECT id FROM clips WHERE video_id = ? AND id != ?", (VID, kept)).fetchone()[0]
    kept_path, new_path = Path(lib.db.get_clip(kept)["path"]), Path(lib.db.get_clip(new)["path"])
    assert kept_path.name == f"clip_00010-00020.clip-{kept}.mp4" and kept_path.read_bytes() == _said(10.0, 20.0)
    assert new_path == lib.folder / _name(10.0, 20.0) and new_path.read_bytes() == b"the run's render"
    remake.remove_clips(lib.db, [new], lib.data)
    assert kept_path.read_bytes() == _said(10.0, 20.0)


def test_a_story_reel_from_before_keeps_its_file_and_its_words(lib):
    """A Sports run joins a reel of the same kind again over its file
    (core/pipeline.py _sport_reels), and writes its new chapters on its row."""
    reel = lib.clip(0.0, 95.0, name="reel_recap.mp4", render_opts=json.dumps({"reel": "recap"}),
                    description="0:00 Goal 18'")
    before = _row(lib.db, reel)
    run = remake.Run(lib.db, VID, lib.data)
    _render(lib, run, 100.0, 130.0)
    (lib.folder / "reel_recap.mp4").write_bytes(b"the run's reel")
    run.db.set_clip(reel, path=str(lib.folder / "reel_recap.mp4"), end_s=88.0, scores="{}",
                    description="0:00 Goal 21'")
    out = run.finish()
    assert out["kept_because"] == {"story_reel": 1}
    assert _row(lib.db, reel) == before and Path(before["path"]).read_bytes() == _said(0.0, 95.0)


def test_a_clip_whose_file_was_missing_gets_no_file_from_the_run(lib):
    kept = lib.clip(10.0, 20.0, edited_at=_now())
    first = Path(lib.db.get_clip(kept)["path"])
    first.unlink()
    before = _row(lib.db, kept)
    run = remake.Run(lib.db, VID, lib.data)
    _render(lib, run, 10.0, 20.0)
    run.finish()
    assert _row(lib.db, kept) == before and not first.exists()


def test_two_clips_on_one_file_are_put_aside_together(lib):
    """Two clips of the same whole seconds share a file (an earlier forced
    run wrote the second over the first's)."""
    a = lib.clip(10.0, 20.0, edited_at=_now())
    b = lib.clip(10.4, 20.6, edited_at=_now())
    run = remake.Run(lib.db, VID, lib.data)
    assert lib.db.get_clip(a)["path"] == lib.db.get_clip(b)["path"]
    assert Path(lib.db.get_clip(a)["path"]).name == f"clip_00010-00020.clip-{a}.mp4"
    _render(lib, run, 50.0, 60.0)
    run.finish()
    assert lib.db.get_clip(a)["path"] == lib.db.get_clip(b)["path"] == str(lib.folder / _name(10.0, 20.0))
    assert (lib.folder / _name(10.0, 20.0)).read_bytes() == _said(10.4, 20.6)


def test_a_file_a_run_the_app_closed_halfway_put_aside_goes_back(lib):
    kept = lib.clip(10.0, 20.0, edited_at=_now())
    first = Path(lib.db.get_clip(kept)["path"])
    aside = first.with_name(f"clip_00010-00020.clip-{kept}.mp4")
    first.rename(aside)
    lib.db.set_clip(kept, path=str(aside))          # where that run left it
    run = remake.Run(lib.db, VID, lib.data)
    assert lib.db.get_clip(kept)["path"] == str(aside)
    _render(lib, run, 10.0, 20.0)
    run.finish()
    assert lib.db.get_clip(kept)["path"] == str(first) and first.read_bytes() == _said(10.0, 20.0)
    assert _files(lib) == [_name(10.0, 20.0)]


def test_a_file_that_cant_go_back_stays_aside_with_its_rows(lib, monkeypatch):
    """Its first name held open (a player showing the run's render there):
    the kept clip keeps playing its own file, under the name it was put
    aside under."""
    kept = lib.clip(10.0, 20.0, edited_at=_now())
    run = remake.Run(lib.db, VID, lib.data)
    aside = Path(lib.db.get_clip(kept)["path"])
    _render(lib, run, 10.0, 20.0)
    real = remake._rename

    def held_open(src, dst):
        if Path(dst).name == _name(10.0, 20.0):
            raise PermissionError(13, "in use")
        real(src, dst)

    monkeypatch.setattr(remake, "_rename", held_open)
    run.finish()
    assert lib.db.get_clip(kept)["path"] == str(aside) and aside.read_bytes() == _said(10.0, 20.0)


def test_a_row_that_cant_be_saved_while_putting_a_file_aside_gets_the_file_back(lib):
    """The job stops before its run, and every clip is as it was."""
    first, second = lib.clip(10.0, 20.0), lib.clip(30.0, 40.0)
    before = {c: _row(lib.db, c) for c in (first, second)}
    lib.db.conn.execute(f"CREATE TRIGGER no_save BEFORE UPDATE OF path ON clips WHEN NEW.id = {second} "
                        "BEGIN SELECT RAISE(ABORT, 'disk I/O error'); END")
    lib.db.conn.commit()
    with pytest.raises(Exception, match="disk I/O error"):
        remake.Run(lib.db, VID, lib.data)
    lib.db.conn.execute("DROP TRIGGER no_save")
    lib.db.conn.commit()
    assert {c: _row(lib.db, c) for c in before} == before
    assert _files(lib) == [_name(10.0, 20.0), _name(30.0, 40.0)]
    assert (lib.folder / _name(30.0, 40.0)).read_bytes() == _said(30.0, 40.0)


def test_a_copy_goes_back_under_its_first_name_once_nothing_reads_it(lib):
    published = lib.clip(10.0, 20.0)
    with in_use.holding([published]):                # a publish under way as the job starts
        run = remake.Run(lib.db, VID, lib.data)
        lib.db.record_upload(published, "yt1")       # what it records once it's done
    _render(lib, run, 10.0, 20.0)
    run.finish()
    assert lib.db.get_clip(published)["path"] == str(lib.folder / _name(10.0, 20.0))
    assert (lib.folder / _name(10.0, 20.0)).read_bytes() == _said(10.0, 20.0)
    assert _files(lib) == [_name(10.0, 20.0)]


# ---- a run that stopped ----------------------------------------------------------------


@pytest.mark.parametrize("how", ["failed", "was cancelled"])
def test_after_a_run_that_stopped_every_clip_from_before_is_as_it_was(lib, how):
    kept, unused, again = lib.clip(10.0, 20.0, edited_at=_now()), lib.clip(30.0, 40.0), lib.clip(50.0, 60.0)
    before = {c: _row(lib.db, c) for c in (kept, unused, again)}
    run = remake.Run(lib.db, VID, lib.data)
    _render(lib, run, 10.0, 20.0)       # a kept clip's moment found again,
    _render(lib, run, 50.0, 60.0)       # an unused one's,
    _render(lib, run, 70.0, 80.0)       # and a new one
    run.stopped(how)
    assert {c: _row(lib.db, c) for c in before} == before
    for row in before.values():
        assert Path(row["path"]).read_bytes() == _said(row["start_s"], row["end_s"])
    new = lib.db.conn.execute("SELECT path FROM clips WHERE video_id = ? AND start_s = 70.0", (VID,)).fetchone()
    assert Path(new["path"]).read_bytes() == b"the run's render"       # what it made is kept, as always
    assert _files(lib) == sorted(_name(s, e) for s, e in ((10.0, 20.0), (30.0, 40.0), (50.0, 60.0), (70.0, 80.0)))


# ---- a Cancel -------------------------------------------------------------------------


def test_each_cancel_is_counted_and_clearing_the_flag_leaves_the_count():
    before = cancel.times_requested(VID)
    cancel.request_cancel(VID)
    cancel.clear(VID)
    assert cancel.times_requested(VID) == before + 1 and not cancel.is_cancelled(VID)
    assert cancel.times_requested(None) == 0 and cancel.times_requested("") == 0


def test_a_cancel_since_the_job_started_counts_even_once_its_flag_is_cleared(lib):
    cancel.request_cancel(VID)        # a Cancel of an earlier job of this video...
    cancel.clear(VID)                 # ...whose flag the worker's fresh start cleared
    run = remake.Run(lib.db, VID, lib.data)
    assert not run.cancelled()
    run.check_cancelled()
    cancel.request_cancel(VID)        # POST /cancel
    cancel.clear(VID)                 # core/pipeline.py's own fresh start
    assert run.cancelled()
    with pytest.raises(cancel.CancelledError):
        run.check_cancelled()


def test_a_cancel_from_before_the_worker_counted_counts(lib):
    """The worker reads the count before its own fresh start and gives it
    to the run: a Cancel between the two counts too."""
    counted = cancel.times_requested(VID)
    cancel.request_cancel(VID)
    cancel.clear(VID)
    assert remake.Run(lib.db, VID, lib.data, cancels=counted).cancelled()


def test_the_run_asks_again_for_a_cancel_its_own_start_cleared(lib):
    """So it stops at its next check, not at its end."""
    run = remake.Run(lib.db, VID, lib.data)
    cancel.request_cancel(VID)
    cancel.clear(VID)
    run.db.upsert_video(VID, title="A stream")      # what core/pipeline.py does right after
    assert cancel.is_cancelled(VID)
    with pytest.raises(cancel.CancelledError):
        cancel.check(VID)


def test_a_run_nobody_cancelled_is_never_cancelled(lib):
    run = remake.Run(lib.db, VID, lib.data)
    run.db.upsert_video(VID, title="A stream")
    _render(lib, run, 10.0, 20.0)
    assert not run.cancelled() and not cancel.is_cancelled(VID)


# ---- the real pipeline -------------------------------------------------------------------


def _engine():
    pytest.importorskip("numpy")
    pytest.importorskip("cv2")
    pytest.importorskip("PIL")


def _config(data: Path) -> dict:
    pytest.importorskip("yaml")
    pytest.importorskip("yt_dlp")       # sources.dispatch reads the link
    from main import BUNDLED_CONFIG, load_config

    config = load_config(BUNDLED_CONFIG)
    config["paths"]["data_dir"] = str(data)
    return config


def test_a_make_clips_again_run_stops_for_a_cancel_pressed_while_its_source_is_found(lib, monkeypatch):
    """core/pipeline.py clears the flag after its download step; given the
    run's database, its next check still stops it."""
    _engine()
    config = _config(lib.data)
    from core import pipeline
    from core.models import DownloadedVideo

    source = lib.data / "downloads" / f"{VID}.mp4"
    source.parent.mkdir()
    source.write_bytes(b"x")
    video = DownloadedVideo(video_id=VID, title="A stream", path=source, duration=600.0, channel="someone")

    def found(url, data_dir, db, vertical=False):
        cancel.request_cancel(VID)                  # pressed now
        return video

    monkeypatch.setattr(pipeline, "_cached_or_download", found)
    monkeypatch.setattr(pipeline, "convert_slow_source", lambda video, config: None)
    run = remake.Run(lib.db, VID, lib.data)
    with pytest.raises(cancel.CancelledError):
        pipeline.process_video(URL, config, run.db, force=True)


def test_a_cancel_the_av1_re_download_swallowed_still_stops_the_run(lib, monkeypatch):
    """A copy cached as AV1 is downloaded again, and _cached_or_download
    carries on with the cached copy when that download stops, a Cancel too."""
    _engine()
    config = _config(lib.data)
    import subprocess

    from core import pipeline
    from sources import dispatch

    cached = lib.data / "downloads" / f"{VID}.webm"
    cached.parent.mkdir()
    cached.write_bytes(b"x")
    real = subprocess.run

    def probe(argv, *a, **k):
        if "stream=codec_name" in argv:
            return types.SimpleNamespace(stdout="av1\n", stderr="", returncode=0)
        if "format=duration" in argv:
            return types.SimpleNamespace(stdout="600.0\n", stderr="", returncode=0)
        return real(argv, *a, **k)

    def download(url, folder, vertical=False):
        cancel.request_cancel(VID)                  # pressed mid-download...
        raise cancel.CancelledError(VID)            # ...as the download's progress hook stops it

    monkeypatch.setattr(subprocess, "run", probe)
    monkeypatch.setattr(dispatch, "download", download)
    monkeypatch.setattr(pipeline, "convert_slow_source", lambda video, config: None)
    run = remake.Run(lib.db, VID, lib.data)
    with pytest.raises(cancel.CancelledError):
        pipeline.process_video(URL, config, run.db, force=True)


# ---- rendered for real ---------------------------------------------------------------------


def _tracking(spec: dict) -> dict:
    out = dict(spec)
    for key in ("path", "zoom"):
        if key in out:
            out[key] = [tuple(point) for point in out[key]]
    for key in ("region", "webcam_box", "rows"):
        if out.get(key) is not None:
            out[key] = tuple(out[key])
    return out


@pytest.fixture
def rendering(monkeypatch, tmp_path):
    """One processed video whose clips are rendered for real
    (core/pipeline.py _render_files and _register_clip), with FFmpeg, the
    tracker and the detectors faked: each file is a descriptor, so a test can
    tell whose file it is. Its earlier run had captions on; this one has them
    off."""
    _engine()
    from render_harness import RenderHarness

    from analysis.metadata import ClipMetadata
    from core import pipeline
    from core.models import ClipCandidate, Segment
    from gaming import detect, panels
    from gaming import run as gaming_run  # noqa: F401  imported before any fake goes in
    from video import outro, tracker

    shared = json.loads((ROOT / "tests" / "fixtures" / "render_opts" / "_shared.json").read_text(encoding="utf-8"))
    h = RenderHarness(monkeypatch, tmp_path / "h", "cpu", (1080, 1920))
    monkeypatch.setattr(outro, "_tally", {"added": 0, "skipped": 0})
    h.fake(tracker, "compute_tracking", _tracking(shared["tracking"]))
    for name in ("sample_tracks", "head_in_box"):
        h.refuse(detect, name, "a detector would run")
    for name in ("speaking_scores", "clip_cam", "find_cam", "stills"):
        h.refuse(detect, name, "TalkNet or a detector would run")
    h.refuse(panels, "panels_in_video", "the chat-panel search would read the video")
    source = h.source(**shared["source"])
    segments = [Segment(**s) for s in shared["segments"]]
    data = h.root / "data"
    db = StateDB(data / "state.db")
    db.upsert_video(VID, title="A stream", channel_name="someone", duration=600.0)
    db.set_video_status(VID, "done")
    # Where process_video and a person's re-render both put this video's clips.
    clip_dir = (data / "clips" / pipeline._safe_name("someone", "unknown-channel")
                / f"{pipeline._safe_name('A stream', VID)} [{VID}]")
    clip_dir.mkdir(parents=True)
    (data / "previews").mkdir()
    base = json.loads(json.dumps(shared["config"]))
    base["paths"] = {"data_dir": str(data)}
    config = {**base, "clips": {**base["clips"], "captions": False}}

    def clip(start, end, made_by, render_opts="", **fields):
        path = clip_dir / _name(start, end)
        h.media(path, made_by=made_by, duration=round(end - start + 0.4, 6))
        clip_id = db.add_clip(VID, start, end, 70, "hook", path=str(path), status="queued", title="Model title",
                              render_opts=json.dumps(render_opts) if isinstance(render_opts, dict) else render_opts)
        if fields:
            db.set_clip(clip_id, **fields)
        return clip_id

    def render(run, start, end):
        """One clip of the run, as process_video makes it: with the edits a
        person saved on the clip of that window (D33), rendered, registered."""
        candidate = ClipCandidate(start=start, end=end, score=90, hook="new hook")
        choices = pipeline._creator_choices(run.db, VID, config, None)
        opts = pipeline._with_choices(None, choices.get(pipeline._window(candidate)))
        final, opts_json = pipeline._render_files(source, candidate, segments, clip_dir, config, opts, "en")
        pipeline._register_clip(run.db, VID, candidate, final,
                                ClipMetadata(title="Run's title", description="", hashtags=[]), opts_json, config)
        return final, opts_json

    def whose(clip_id):
        return (h.info(Path(db.get_clip(clip_id)["path"])) or {}).get("made_by")

    yield types.SimpleNamespace(db=db, data=data, clip_dir=clip_dir, clip=clip, render=render, whose=whose, h=h)
    db.conn.close()


def _hand_cut(db, clip_id):
    # Cut in the timeline editor and applied: made again "with my edits" (D33).
    db.set_clip(clip_id, render_opts=json.dumps({"edit": {"keep": [[0.0, 3.0], [5.0, 10.0]]}}), edited_at=_now())


RENDERED_KEPT = {
    "published": lambda db, c: db.record_publish(c, {"youtube_id": "yt1", "title": "My post", "video_id": VID,
                                                      "start_s": 10.0, "end_s": 20.0}),
    "retitled": lambda db, c: db.set_clip(c, title="My own title", edited_at=_now()),
    "cut_by_hand": _hand_cut,
    "scheduled": lambda db, c: db.set_clip(c, status="scheduled", scheduled_for="2026-11-09"),
}


@pytest.mark.parametrize("kind", sorted(RENDERED_KEPT))
def test_rendered_for_real_a_kept_clip_found_again_keeps_its_own_file(rendering, kind):
    kept = rendering.clip(10.0, 20.0, "the person's (captions on)")
    RENDERED_KEPT[kind](rendering.db, kept)
    before = _row(rendering.db, kept)
    run = remake.Run(rendering.db, VID, rendering.data)
    final, _ = rendering.render(run, 10.0, 20.0)
    assert final == Path(before["path"])               # the run wrote its first name
    out = run.finish()
    assert out["kept"] == 1 and out["removed"] == []
    assert _row(rendering.db, kept) == before
    assert rendering.whose(kept) == "the person's (captions on)"


def test_rendered_for_real_an_unused_clip_found_again_plays_the_new_render(rendering):
    unused = rendering.clip(10.0, 20.0, "the earlier run's", render_opts={"job_captions": True})
    run = remake.Run(rendering.db, VID, rendering.data)
    final, opts_json = rendering.render(run, 10.0, 20.0)
    run.finish()
    row = rendering.db.get_clip(unused)
    assert Path(row["path"]) == final and rendering.h.info(final) is not None
    assert rendering.whose(unused) is None                # the run's render, not the old file
    assert row["render_opts"] == opts_json and json.loads(opts_json)["job_captions"] is False
    assert not [p.name for p in rendering.clip_dir.iterdir() if ".clip-" in p.name]   # its old file went


def test_rendered_for_real_a_new_moment_with_the_same_whole_seconds_leaves_the_kept_clip_its_file(rendering):
    kept = rendering.clip(10.0, 20.0, "the person's cut")
    _hand_cut(rendering.db, kept)
    run = remake.Run(rendering.db, VID, rendering.data)
    final, _ = rendering.render(run, 10.4, 20.6)
    run.finish()
    new = rendering.db.conn.execute("SELECT id FROM clips WHERE id != ?", (kept,)).fetchone()[0]
    assert rendering.db.get_clip(new)["path"] == str(final)
    assert rendering.whose(kept) == "the person's cut"
    assert rendering.db.get_clip(kept)["path"] != str(final)
    remake.remove_clips(rendering.db, [new], rendering.data)
    assert rendering.whose(kept) == "the person's cut"


def test_rendered_for_real_a_run_that_fails_leaves_every_file_as_it_was(rendering, capsys):
    published = rendering.clip(10.0, 20.0, "the published one")
    RENDERED_KEPT["published"](rendering.db, published)
    unused = rendering.clip(30.0, 40.0, "the unused one")
    before = {c: _row(rendering.db, c) for c in (published, unused)}
    run = remake.Run(rendering.db, VID, rendering.data)
    rendering.render(run, 10.0, 20.0)
    rendering.render(run, 30.0, 40.0)
    run.stopped("failed")
    assert "nothing was replaced" in capsys.readouterr().out
    assert {c: _row(rendering.db, c) for c in before} == before
    assert (rendering.whose(published), rendering.whose(unused)) == ("the published one", "the unused one")


# ---- clips a real run makes are clips nobody used, until someone changes them ----------------


@pytest.fixture
def real_run(monkeypatch, tmp_path):
    """process_video from finding to the clip rows (core/pipeline.py
    _register_clip, the path every clip a run makes takes), with download,
    transcription, analysis, the title writer and the render stubbed out, as
    tests/test_plugin_edit_rerun.py runs it. `run(db, starts)` makes a 30 s
    clip at each start, through the database it is given."""
    _engine()
    import core.pipeline as pipeline
    from analysis.metadata import ClipMetadata
    from core.models import ClipCandidate, DownloadedVideo

    config = _config(tmp_path / "data")
    config["clips"].update({"captions": False, "min_duration": 10, "max_duration": 60, "min_score": 55,
                            "max_clips_per_video": 0})
    source = tmp_path / "stream.mp4"
    source.write_bytes(b"not really a video")
    video = DownloadedVideo(video_id=VID, title="A stream", path=source, duration=900.0, channel="someone")
    monkeypatch.setattr(pipeline, "_cached_or_download", lambda *_a, **_k: video)
    monkeypatch.setattr(pipeline, "transcribe", lambda *_a, **_k: [])
    monkeypatch.setattr("transcription.transcriber.detected_language", lambda *_a, **_k: "en")
    monkeypatch.setattr("video.encoding.source_codec", lambda _p: "h264")
    monkeypatch.setattr("analysis.audio_features.extract_audio_features", lambda _p: {})
    monkeypatch.setattr("analysis.visual_features.extract_visual_features", lambda _p: {})
    monkeypatch.setattr("analysis.hype.audience_signals", lambda *_a, **_k: (None, None))
    monkeypatch.setattr(pipeline, "_with_usable_model", lambda cfg: cfg)
    monkeypatch.setattr(pipeline, "create_backend", lambda cfg: object())
    monkeypatch.setattr(pipeline, "clip_direction", lambda *_a, **_k: None)
    monkeypatch.setattr(pipeline, "_share_the_cpu", lambda workers: None)
    monkeypatch.setattr(pipeline, "_gaming_prepare", lambda *_a, **_k: None)
    monkeypatch.setattr(pipeline, "_gaming_scoring_inputs", lambda *_a, **_k: (None, None, None))
    monkeypatch.setattr(pipeline, "_listening_for", lambda *_a, **_k: None)
    monkeypatch.setattr("core.modes.probe_size", lambda _p: (1920, 1080))
    found: list = []
    monkeypatch.setattr(pipeline, "find_clips", lambda *_a, **_k: (list(found), []))

    def titles(candidates, *_a, **_k):
        return [ClipMetadata(title=f"Clip at {c.start:g}", description=f"What happens at {c.start:g}",
                             hashtags=["#stream", f"#at{c.start:g}"]) for c in candidates]

    def render(source, candidate, segments, clip_dir, config, render_opts=None, content_language="en"):
        clip_dir.mkdir(parents=True, exist_ok=True)
        out = clip_dir / _name(candidate.start, candidate.end)
        out.write_bytes(f"the run's render of {candidate.start:g}".encode())
        return out, json.dumps(render_opts or {})

    monkeypatch.setattr(pipeline, "generate_metadata_batch", titles)
    monkeypatch.setattr(pipeline, "_render_files", render)

    def run(db, starts):
        found[:] = [ClipCandidate(start=float(s), end=float(s) + 30.0, score=90 - i, hook=f"Moment at {s}",
                                  reason="loud reaction") for i, s in enumerate(starts)]
        db.set_outcome(VID, {"candidates": len(starts)})
        pipeline.process_video(f"local:{VID}", config, db, force=True)
        return {round(float(r["start_s"])): int(r["id"]) for r in db.conn.execute(
            "SELECT id, start_s FROM clips WHERE video_id = ?", (VID,)).fetchall()}

    cancel.clear(VID)
    db = StateDB(tmp_path / "data" / "state.db")
    db.upsert_video(VID, title="A stream", channel_name="someone")
    yield types.SimpleNamespace(db=db, run=run, data=tmp_path / "data")
    db.conn.close()
    cancel.clear(VID)


def test_clips_a_real_run_makes_are_nobody_used_and_words_changed_in_any_version_keep_them(real_run, capsys):
    """Every clip a run makes keeps the fingerprint of the words and window
    the run gave it (core/state.py as_made), so it counts as nobody used it.
    A title, description or hashtags changed afterwards keep it, whichever
    version of Clips Kitty changed them: the released app's PATCH
    /clips/{id} only wrote the new words (set_clip), with no edited_at. A
    clip an older version made has no fingerprint at all: kept as from
    before. The next Make clips again, through the real pipeline too,
    deletes only the clip nobody touched."""
    db = real_run.db
    ids = real_run.run(db, [10, 100, 200, 300, 400])            # a first run, on the worker's own database
    judged = {c["id"]: c for c in remake.classify(db, VID)}
    assert all(judged[c]["replace"] and judged[c]["reasons"] == [] for c in ids.values()), judged
    assert db.get_clip(ids[10])["title"] == "Clip at 10"          # the run's own words
    # What the released app's PATCH /clips/{id} did: the words, and nothing else.
    db.set_clip(ids[100], title="My own title")
    db.set_clip(ids[200], description="My own words")
    db.set_clip(ids[300], hashtags=json.dumps(["#mine"]))
    # The same words saved again change nothing.
    db.set_clip(ids[400], title="Clip at 400", hashtags=json.dumps(["#stream", "#at400"]))
    # A clip an older version made: its add_clip wrote no fingerprint.
    db.conn.execute("INSERT INTO clips (video_id, start_s, end_s, score, hook, path, status, title, description, "
                    "hashtags, scores, render_opts, created_at) VALUES (?, 700.0, 730.0, 80, 'h', '', 'queued', "
                    "'Its title', '', '[]', '', '', 'x')", (VID,))
    db.conn.commit()
    older = db.conn.execute("SELECT id FROM clips WHERE start_s = 700.0").fetchone()[0]
    judged = {c["id"]: c["reasons"] for c in remake.classify(db, VID)}
    assert judged == {ids[10]: [], ids[100]: ["edited"], ids[200]: ["edited"], ids[300]: ["edited"],
                      ids[400]: [], older: ["older"]}

    run = remake.Run(db, VID, real_run.data)
    made = real_run.run(run.db, [400, 500])                       # a Make clips again: 400 found again, 500 new
    capsys.readouterr()
    out = run.finish()
    left = {c["id"]: c["reasons"] for c in remake.classify(db, VID)}
    assert ids[10] not in left and out["removed"] == [ids[10]]
    assert {ids[100], ids[200], ids[300], older} <= set(left)
    assert left[made[500]] == [] and left[ids[400]] == []         # the run's clips: nobody used them yet
    assert db.get_clip(ids[100])["title"] == "My own title"
