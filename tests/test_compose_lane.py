"""Editor suite step 7, what the export compiler adds around it (standard
library only, so these run on the CI light install):

- a project's export is a lane task listed, cancelled and announced with
  the exports (server/editor_lane.py);
- the tracking of an AI-framed stretch is kept in the media cache, as the
  "tracking" product the cap and Clear count (video_editor/media/cache.py);
- project_exports gains branch, duration, size and job (core/state.py), on a
  new database and on one made before;
- a fitted picture's pad is "blur" or a colour (video_editor/project/ops.py).
"""

from __future__ import annotations

import sqlite3

import pytest
from project_helpers import ENV

from core.state import StateDB
from server.editor_lane import EditorLane
from video_editor import compose
from video_editor.media import cache
from video_editor.project import model, ops, store


def _lane(tmp_path):
    events: list[dict] = []
    return EditorLane(tmp_path, publish=events.append), events


def test_a_project_export_is_listed_and_announced_with_the_exports(tmp_path):
    lane, events = _lane(tmp_path)
    seen = {}

    def work(ctx):
        ctx.progress(0.5, {"stage": "final"})
        seen["fraction"] = lane.get(ctx.job.id)["fraction"]
        return {"path": "x.mp4"}

    job = lane.submit_project_export(work, folder=tmp_path, name="p-r3", preset={"kind": "original"}, clip_id=7,
                                     extra={"project_id": "p", "revision": 3, "branch": "B"})
    assert job["kind"] == "project_export" and job["export_id"] == 1 and job["project_id"] == "p"
    assert [j["export_id"] for j in lane.jobs()] == [1] and lane.tasks() == []
    assert lane.run_pending() == 1
    done = lane.get(1)
    assert done["status"] == "done" and done["result"] == {"path": "x.mp4"} and done["stage"] == "final"
    assert seen["fraction"] == 0.5
    assert {e["type"] for e in events} == {"export"}
    assert events[-1]["export"]["status"] == "done" and events[-1]["export"]["branch"] == "B"


def test_cancelling_a_waiting_project_export_tells_it(tmp_path):
    lane, _events = _lane(tmp_path)
    dropped = []
    lane.submit_project_export(lambda ctx: {}, folder=tmp_path, name="p", preset={}, on_dropped=dropped.append)
    assert lane.cancel(1)["status"] == "cancelled"
    assert [d["export_id"] for d in dropped] == [1]
    assert lane.run_pending() == 0


def test_a_task_is_still_a_task_and_an_export_an_export(tmp_path):
    lane, events = _lane(tmp_path)
    lane.submit_task("peaks", "peaks:abc", lambda ctx: {"ok": 1})
    lane.submit_project_export(lambda ctx: {}, folder=tmp_path, name="p", preset={})
    assert [t["job_id"] for t in lane.tasks()] == [1] and [j["export_id"] for j in lane.jobs()] == [2]
    assert lane.get(1) is None and lane.get(1, kind=None)["kind"] == "peaks"
    assert lane.find("peaks:abc")["job_id"] == 1


# ---- the tracking cache --------------------------------------------------------------------------


PARAMS = {"tracking_key": {"v": 1, "media": "m1", "fps": {"num": 30, "den": 1}, "speed": 1.0,
                           "spans": [[0, 1_000_000, 1_000_000]], "crop": "track"}}
CONFIG = {"tracking": {"detector": "yolov8n-pose.pt", "sample_fps": 8.0}}


def test_tracking_is_kept_in_the_media_cache_and_used_again(tmp_path):
    folder = tmp_path / "media_cache" / ("a" * 64)
    folder.mkdir(parents=True)
    calls = []

    def compute(path, **kwargs):
        calls.append(kwargs)
        return {"mode": "track", "path": [(0.0, 0.25), (1.0, 0.75)], "face_y": 0.4}

    track = compose.cached_tracker({**PARAMS, "tracking_cache": str(folder)}, CONFIG, compute)
    first = track("clip.mp4", model_name="m", force_fit_blur=False)
    again = track("clip.mp4", model_name="m", force_fit_blur=False)
    assert len(calls) == 1 and first == again and again["path"] == [(0.0, 0.25), (1.0, 0.75)]
    assert (folder / "tracking" / "index.json").is_file()
    assert len(list((folder / "tracking").glob("*.json"))) == 2
    track("clip.mp4", model_name="m", force_fit_blur=True)        # a forced letterbox is tracked apart
    assert len(calls) == 2
    other = compose.cached_tracker({**PARAMS, "tracking_cache": str(folder)},
                                   {"tracking": {**CONFIG["tracking"], "sample_fps": 4.0}}, compute)
    other("clip.mp4", force_fit_blur=False)
    assert len(calls) == 3


def test_without_a_cache_folder_tracking_is_computed_as_ever(tmp_path):
    calls = []
    track = compose.cached_tracker({**PARAMS, "tracking_cache": None}, CONFIG,
                                   lambda p, **k: calls.append(p) or {"mode": "track", "path": []})
    track("a.mp4")
    track("a.mp4")
    assert calls == ["a.mp4", "a.mp4"]


def test_a_cancel_made_while_tracking_stops_before_the_croppers_render(tmp_path):
    """Fix 7-D3: the frame stage checks the task once the tracking is known
    (computed or from the cache), so the cropper's render never starts
    after a cancel. The tracking computed is still kept."""
    folder = tmp_path / "media_cache" / ("c" * 64)
    folder.mkdir(parents=True)
    task = compose.LocalTask()

    def compute(path, **kwargs):
        task.cancel()             # the person cancels while it tracks
        return {"mode": "track", "path": [(0.0, 0.5)]}

    from video import export_presets

    track = compose.cached_tracker({**PARAMS, "tracking_cache": str(folder)}, CONFIG, compute, check=task.check)
    with pytest.raises(export_presets.Cancelled):
        track("clip.mp4", force_fit_blur=False)
    assert len(list((folder / "tracking").glob("*.json"))) == 2
    with pytest.raises(export_presets.Cancelled):          # from the cache too
        track("clip.mp4", force_fit_blur=False)


def test_the_tracking_product_counts_for_the_cap_and_clear(tmp_path):
    assert "tracking" in cache.PRODUCTS
    data = tmp_path
    folder = cache.root(data) / ("b" * 64)
    (folder / "tracking").mkdir(parents=True)
    (folder / "tracking" / "k.json").write_bytes(b"x" * 5000)
    (folder / "tracking" / "index.json").write_text("{}")
    assert folder / "tracking" in cache.products(data)
    cache.enforce_cap(data, 10)
    assert not (folder / "tracking").exists()
    (folder / "tracking").mkdir()
    (folder / "tracking" / "k.json").write_bytes(b"x")
    assert cache.clear(data)["files_removed"] >= 1 and not folder.exists()


# ---- project_exports ------------------------------------------------------------------------------


def test_project_exports_keeps_branch_length_size_and_job(tmp_path):
    db = StateDB(tmp_path / "state.db")
    try:
        created = store.create(db.conn, model.new_project(name="P"))
        eid = store.add_export(db.conn, created["id"], created["revision"], branch="B", job_id=4,
                               preset={"kind": "custom"})
        store.set_export(db.conn, eid, status="done", path="x.mp4", bytes=1234, duration_us=5_000_000,
                         nonsense="ignored")
        row = store.exports(db.conn, created["id"])[0]
        assert (row["branch"], row["job_id"], row["bytes"], row["duration_us"], row["status"]) == \
            ("B", 4, 1234, 5_000_000, "done")
    finally:
        db.close()


def test_a_database_made_before_gets_the_new_columns(tmp_path):
    path = tmp_path / "state.db"
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE project_exports (id INTEGER PRIMARY KEY AUTOINCREMENT, project_id TEXT NOT NULL, "
                 "head INTEGER NOT NULL DEFAULT 0, clip_id INTEGER, path TEXT NOT NULL DEFAULT '', "
                 "preset TEXT NOT NULL DEFAULT '', status TEXT NOT NULL DEFAULT '', error TEXT NOT NULL DEFAULT '', "
                 "created_at TEXT NOT NULL DEFAULT '', updated_at TEXT NOT NULL DEFAULT '')")
    conn.execute("INSERT INTO project_exports (project_id, head, status) VALUES ('p', 2, 'done')")
    conn.commit()
    conn.close()
    db = StateDB(path)
    try:
        cols = {r["name"] for r in db.conn.execute("PRAGMA table_info(project_exports)")}
        assert {"branch", "duration_us", "bytes", "job_id"} <= cols
        row = db.conn.execute("SELECT * FROM project_exports").fetchone()
        assert (row["branch"], row["bytes"], row["job_id"]) == ("", 0, None)
    finally:
        db.close()


# ---- the pad of a fitted picture -----------------------------------------------------------------


def _doc():
    doc = model.new_project(name="P")
    doc = ops.apply(doc, [{"op": "add_media", "video_id": "video-a"}], ENV).document
    return ops.apply(doc, [{"op": "insert_clip", "media_id": "m1", "source_in_us": 0,
                            "source_out_us": 1_000_000}], ENV).document


@pytest.mark.parametrize("pad,kept", [("blur", "blur"), ("#00ff00", "#00FF00")])
def test_a_fitted_picture_pads_with_blur_or_a_colour(pad, kept):
    doc = _doc()
    item = model.track(doc, "V1")["items"][0]
    out = ops.apply(doc, [{"op": "set_item_props", "item_id": item["id"],
                           "framing": {"fit": "fit", "pad": pad}}], ENV).document
    assert model.track(out, "V1")["items"][0]["framing"] == {"fit": "fit", "pad": kept}


@pytest.mark.parametrize("pad", ["red", "#12345", 3, None])
def test_any_other_pad_is_refused(pad):
    doc = _doc()
    item = model.track(doc, "V1")["items"][0]
    with pytest.raises(ops.OpError, match="pad"):
        ops.apply(doc, [{"op": "set_item_props", "item_id": item["id"], "framing": {"fit": "fit", "pad": pad}}], ENV)
