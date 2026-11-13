"""Editor suite step 5: where projects are kept (video_editor/project/store.py
and the tables core/state.py makes): compare-and-swap on head, undo and redo,
snapshots, the revision cap, coalescing, a project following its clip, a clip
deleted under a project, another version's document, and the tables arriving
on a database that had none. Standard library only: runs on the CI light
install.
"""

import json
import sqlite3

import pytest
from project_helpers import ENV, project

from core import remake
from core.state import PROJECT_TABLES, StateDB
from video_editor.project import importer, migrate, model, ops, store

S = 1_000_000


def _new(db, clips: int = 2, **kwargs) -> dict:
    return store.create(db.conn, project(clips=clips), now=0, **kwargs)


def _rename(db, made: dict, n: int, now: float = 0, **kwargs) -> dict:
    return store.apply_ops(db.conn, made["id"], made["revision"],
                           [{"op": "set_project_props", "name": f"Name {n}"}], ENV, now=now, **kwargs)


def _rows(db, project_id: str) -> list[sqlite3.Row]:
    return db.conn.execute("SELECT * FROM project_revisions WHERE project_id = ? ORDER BY seq",
                           (project_id,)).fetchall()


def _text(db, project_id: str) -> str:
    return db.conn.execute("SELECT doc FROM projects WHERE id = ?", (project_id,)).fetchone()["doc"]


def _clip(db, start: float = 10.0, end: float = 40.0, video_id: str = "vid1", **kwargs) -> int:
    if db.conn.execute("SELECT 1 FROM videos WHERE video_id = ?", (video_id,)).fetchone() is None:
        db.upsert_video(video_id, title="A video")
    return db.add_clip(video_id, start, end, 80.0, "hook", "clip.mp4", **kwargs)


# ---- create, read, delete -----------------------------------------------------------------------


def test_a_new_project_is_at_revision_0_and_reads_back_the_same_bytes(db):
    doc = project(clips=3)
    made = store.create(db.conn, doc, now=0)
    assert made["created"] is True and made["revision"] == 0
    assert (made["can_undo"], made["can_redo"], made["read_only"]) == (False, False, False)
    got = store.get(db.conn, made["id"])
    assert model.to_bytes(got["document"]) == model.to_bytes(doc)
    assert _text(db, made["id"]) == model.canonical(doc)
    assert got["name"] == "Test" and got["clip_id"] is None
    rows = _rows(db, made["id"])
    assert [(r["seq"], r["snapshot"] is not None) for r in rows] == [(0, True)]


def test_only_a_valid_document_is_stored(db):
    broken = project()
    broken["tracks"] = []
    with pytest.raises(store.StoreError, match="not a project"):
        store.create(db.conn, broken)
    assert db.conn.execute("SELECT COUNT(*) FROM projects").fetchone()[0] == 0


def test_a_project_that_isnt_there(db):
    for call in (lambda: store.get(db.conn, "nope"),
                 lambda: store.apply_ops(db.conn, "nope", 0, [], ENV),
                 lambda: store.undo(db.conn, "nope", 0), lambda: store.redo(db.conn, "nope", 0),
                 lambda: store.revisions(db.conn, "nope"), lambda: store.document_at(db.conn, "nope", 0)):
        with pytest.raises(store.NotFound):
            call()
    assert store.delete(db.conn, "nope") is False


def test_delete_removes_the_project_its_history_and_its_exports(db):
    made, other = _new(db), _new(db)
    out = _rename(db, made, 1)
    store.add_export(db.conn, made["id"], out["revision"], path="out.mp4", preset={"codec": "h264"}, now=0)
    assert store.delete(db.conn, made["id"]) is True
    for table in ("project_revisions", "project_exports"):
        assert db.conn.execute(f"SELECT COUNT(*) FROM {table} WHERE project_id = ?", (made["id"],)).fetchone()[0] == 0
    assert store.get(db.conn, other["id"])["revision"] == 0 and len(_rows(db, other["id"])) == 1


# ---- compare-and-swap ---------------------------------------------------------------------------


def test_a_stale_edit_is_a_conflict_and_changes_nothing(db):
    made = _new(db)
    first = _rename(db, made, 1)
    assert first["revision"] == 1 and first["changed"] and first["can_undo"]
    before, rows = _text(db, made["id"]), len(_rows(db, made["id"]))
    for stale in (0, 2, -1, None, "1", 1.0, True):
        with pytest.raises(store.Conflict) as caught:
            store.apply_ops(db.conn, made["id"], stale, [{"op": "set_project_props", "name": "Late"}], ENV, now=5)
        assert caught.value.current == 1
        for step in (store.undo, store.redo):
            with pytest.raises(store.Conflict):
                step(db.conn, made["id"], stale)
    assert _text(db, made["id"]) == before and len(_rows(db, made["id"])) == rows
    assert store.get(db.conn, made["id"])["revision"] == 1


def test_two_connections_cant_both_change_the_same_revision(db, tmp_path):
    made = _new(db)
    other = StateDB(tmp_path / "state.db")
    try:
        mine = store.apply_ops(db.conn, made["id"], 0, [{"op": "set_project_props", "name": "Mine"}], ENV, now=0)
        with pytest.raises(store.Conflict) as caught:
            store.apply_ops(other.conn, made["id"], 0, [{"op": "set_project_props", "name": "Theirs"}], ENV, now=0)
        assert caught.value.current == mine["revision"] == 1
        theirs = store.apply_ops(other.conn, made["id"], 1, [{"op": "set_project_props", "name": "Theirs"}], ENV,
                                 now=50)
        assert theirs["revision"] == 2 and store.get(db.conn, made["id"])["name"] == "Theirs"
    finally:
        other.conn.close()


def test_a_refused_operation_changes_nothing_in_the_store(db):
    made = _new(db)
    before = _text(db, made["id"])
    with pytest.raises(ops.OpError, match="split_item"):
        store.apply_ops(db.conn, made["id"], 0, [{"op": "set_project_props", "name": "Fine"},
                                                 {"op": "split_item", "item_id": "nope", "at_us": 1}], ENV, now=0)
    assert _text(db, made["id"]) == before and len(_rows(db, made["id"])) == 1
    assert store.get(db.conn, made["id"])["revision"] == 0
    assert not db.conn.in_transaction


def test_a_change_that_fails_half_way_leaves_nothing_behind(db, monkeypatch):
    """The revision row and the project's row are one write: a failure
    between the two (the app closed, the disk full) leaves neither."""
    made = _new(db)
    out = _rename(db, made, 1)
    before, rows = _text(db, made["id"]), [dict(r) for r in _rows(db, made["id"])]

    def fails(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(store, "_save", fails)
    for call in (lambda: _rename(db, out, 2), lambda: store.undo(db.conn, made["id"], out["revision"]),
                 lambda: _volume(db, made["id"], out["revision"], 0.5, now=0.1)):
        with pytest.raises(OSError):
            call()
        assert not db.conn.in_transaction
        assert _text(db, made["id"]) == before and [dict(r) for r in _rows(db, made["id"])] == rows
    monkeypatch.undo()
    assert _rename(db, out, 2)["revision"] == 2


def test_a_history_row_that_cant_be_read_stops_undo_and_changes_nothing(db):
    made = _new(db)
    out = _rename(db, made, 1)
    db.conn.execute("UPDATE project_revisions SET inverse = '{not json' WHERE project_id = ? AND seq = 1", (made["id"],))
    db.conn.commit()
    before = _text(db, made["id"])
    with pytest.raises(store.Damaged, match="history"):
        store.undo(db.conn, made["id"], out["revision"])
    assert _text(db, made["id"]) == before and store.get(db.conn, made["id"])["revision"] == 1
    assert _rename(db, out, 2)["revision"] == 2            # the project itself still works


def test_a_dry_run_answers_and_saves_nothing(db):
    made = _new(db)
    before = _text(db, made["id"])
    out = store.apply_ops(db.conn, made["id"], 0, [{"op": "split_item", "item_id": "i4", "at_us": 2 * S}], ENV,
                          dry_run=True, now=0)
    assert out["dry_run"] and out["changed"] and out["revision"] == 0
    assert out["created"] == [{"kind": "item", "id": "i6"}] and out["inverse"][0]["op"] == "restore"
    assert len(out["document"]["tracks"][0]["items"]) == 3
    assert _text(db, made["id"]) == before and len(_rows(db, made["id"])) == 1
    with pytest.raises(ops.OpError):
        store.apply_ops(db.conn, made["id"], 0, [{"op": "nope"}], ENV, dry_run=True)
    with pytest.raises(store.Conflict):
        store.apply_ops(db.conn, made["id"], 3, [], ENV, dry_run=True)


def test_an_edit_that_changes_nothing_adds_no_revision(db):
    made = _new(db)
    out = store.apply_ops(db.conn, made["id"], 0, [{"op": "set_project_props", "name": "Test"}], ENV, now=0)
    assert out["changed"] is False and out["revision"] == 0 and len(_rows(db, made["id"])) == 1


# ---- undo, redo ---------------------------------------------------------------------------------


def test_undo_and_redo_move_the_head_forward_and_the_document_back_and_forth(db):
    made = _new(db)
    texts = [_text(db, made["id"])]
    out = made
    for n in range(1, 4):
        out = _rename(db, out, n, now=n * 100, label=f"  Rename   {n} ")
        texts.append(_text(db, made["id"]))
    assert out["revision"] == 3
    back = store.undo(db.conn, made["id"], 3, now=400)
    assert back["revision"] == 4 and back["label"] == "Rename 3" and back["can_undo"] and back["can_redo"]
    assert _text(db, made["id"]) == texts[2]
    back = store.undo(db.conn, made["id"], 4, now=401)
    assert back["revision"] == 5 and _text(db, made["id"]) == texts[1]
    again = store.redo(db.conn, made["id"], 5, now=402)
    assert again["revision"] == 6 and _text(db, made["id"]) == texts[2] and again["label"] == "Rename 2"
    listed = store.revisions(db.conn, made["id"])
    assert (listed["revision"], listed["cursor"], listed["tip"], listed["base"]) == (6, 2, 3, 0)
    assert [(r["seq"], r["current"], r["undone"], r["start"]) for r in listed["revisions"]] == [
        (3, False, True, False), (2, True, False, False), (1, False, False, False), (0, False, False, True)]
    assert listed["revisions"][0]["ops"] == [{"op": "set_project_props", "name": "Name 3"}]


def test_a_new_edit_after_an_undo_drops_the_redo_tail(db):
    made = _new(db)
    out = made
    for n in range(1, 5):
        out = _rename(db, out, n, now=n * 100)
    out = store.undo(db.conn, made["id"], out["revision"], now=500)
    out = store.undo(db.conn, made["id"], out["revision"], now=501)
    fresh = store.apply_ops(db.conn, made["id"], out["revision"], [{"op": "add_track", "kind": "audio"}], ENV, now=600)
    assert fresh["can_redo"] is False and fresh["can_undo"] is True
    assert [r["seq"] for r in _rows(db, made["id"])] == [0, 1, 2, 3]
    with pytest.raises(store.NothingToDo, match="nothing to redo"):
        store.redo(db.conn, made["id"], fresh["revision"])
    back = store.undo(db.conn, made["id"], fresh["revision"], now=700)
    assert back["document"]["name"] == "Name 2" and model.track(back["document"], "A3") is None


def test_nothing_to_undo_on_a_new_project(db):
    made = _new(db)
    with pytest.raises(store.NothingToDo, match="nothing to undo"):
        store.undo(db.conn, made["id"], 0)
    with pytest.raises(store.NothingToDo, match="nothing to redo"):
        store.redo(db.conn, made["id"], 0)
    assert store.get(db.conn, made["id"])["revision"] == 0


# ---- snapshots and the cap ----------------------------------------------------------------------


def test_every_25th_revision_holds_the_whole_document(db):
    made = _new(db)
    out, texts = made, [_text(db, made["id"])]
    for n in range(1, 61):
        out = _rename(db, out, n, now=n * 100)
        texts.append(_text(db, made["id"]))
    rows = _rows(db, made["id"])
    assert [r["seq"] for r in rows if r["snapshot"] is not None] == [0, 25, 50]
    assert rows[25]["snapshot"] == texts[25] and rows[50]["snapshot"] == texts[50]
    for seq in (0, 1, 24, 25, 26, 49, 50, 60):
        assert model.canonical(store.document_at(db.conn, made["id"], seq)) == texts[seq]
    with pytest.raises(store.NotFound):
        store.document_at(db.conn, made["id"], 61)


def test_at_most_500_revisions_the_oldest_folded_into_a_snapshot(db):
    made = _new(db, clips=0)
    out, texts = made, {0: _text(db, made["id"])}
    for n in range(1, 531):
        out = _rename(db, out, n, now=n * 100)
        texts[n] = _text(db, made["id"])
        assert len(_rows(db, made["id"])) <= store.MAX_REVISIONS
    rows = _rows(db, made["id"])
    listed = store.revisions(db.conn, made["id"], limit=1)
    assert (listed["base"], listed["cursor"], listed["tip"]) == (50, 530, 530)
    assert rows[0]["seq"] == 50 and rows[0]["snapshot"] == texts[50] and len(rows) == 481
    assert model.canonical(store.document_at(db.conn, made["id"], 50)) == texts[50]
    assert model.canonical(store.document_at(db.conn, made["id"], 77)) == texts[77]
    with pytest.raises(store.NotFound):
        store.document_at(db.conn, made["id"], 49)
    # Undo goes back to the fold and stops there.
    revision = out["revision"]
    for seq in range(529, 49, -1):
        back = store.undo(db.conn, made["id"], revision, now=99_999)
        revision = back["revision"]
        if seq in (529, 300, 51, 50):
            assert _text(db, made["id"]) == texts[seq]
    assert back["can_undo"] is False
    with pytest.raises(store.NothingToDo):
        store.undo(db.conn, made["id"], revision)


# ---- coalescing ---------------------------------------------------------------------------------


def _volume(db, project_id: str, revision: int, value: float, now: float, item_id: str = "i4") -> dict:
    return store.apply_ops(db.conn, project_id, revision,
                           [{"op": "set_item_props", "item_id": item_id, "volume": value}], ENV, now=now)


def test_quick_tweaks_of_one_property_are_one_undo_step(db):
    made = _new(db)
    start = _text(db, made["id"])
    out = _volume(db, made["id"], 0, 0.9, now=100.0)
    for n, value in enumerate((0.8, 0.7, 0.6, 0.5), start=1):
        out = _volume(db, made["id"], out["revision"], value, now=100.0 + n * 0.9)   # each within a second of the last
    assert out["revision"] == 5                                # the head moved with every tweak
    rows = _rows(db, made["id"])
    assert [r["seq"] for r in rows] == [0, 1]                  # and they are one revision
    assert json.loads(rows[1]["ops"]) == [{"op": "set_item_props", "item_id": "i4", "volume": 0.5}]
    assert out["document"]["tracks"][0]["items"][0]["volume"] == 0.5
    back = store.undo(db.conn, made["id"], 5, now=200)
    assert _text(db, made["id"]) == start and back["can_undo"] is False
    again = store.redo(db.conn, made["id"], back["revision"], now=201)
    assert again["document"]["tracks"][0]["items"][0]["volume"] == 0.5


def test_what_doesnt_coalesce(db):
    made = _new(db)
    out = _volume(db, made["id"], 0, 0.9, now=100.0)
    out = _volume(db, made["id"], out["revision"], 0.8, now=101.5)                  # too late
    assert len(_rows(db, made["id"])) == 3
    out = _volume(db, made["id"], out["revision"], 0.7, now=101.6, item_id="i5")    # another item
    assert len(_rows(db, made["id"])) == 4
    out = store.apply_ops(db.conn, made["id"], out["revision"],
                          [{"op": "set_item_props", "item_id": "i5", "speed": 2}], ENV, now=101.7)   # another property
    assert len(_rows(db, made["id"])) == 5
    out = store.apply_ops(db.conn, made["id"], out["revision"],
                          [{"op": "split_item", "item_id": "i4", "at_us": S}], ENV, now=101.8)        # not a tweak
    out = store.apply_ops(db.conn, made["id"], out["revision"],
                          [{"op": "split_item", "item_id": "i4", "at_us": 500_000}], ENV, now=101.9)
    assert len(_rows(db, made["id"])) == 7
    out = _volume(db, made["id"], out["revision"], 0.6, now=102.0)
    assert len(_rows(db, made["id"])) == 8
    out = _volume(db, made["id"], out["revision"], 0.5, now=101.0)                  # the clock went backwards
    assert len(_rows(db, made["id"])) == 9
    # An undo and a redo in between: the next tweak is a step of its own.
    out = store.undo(db.conn, made["id"], out["revision"], now=101.1)
    out = store.redo(db.conn, made["id"], out["revision"], now=101.2)
    out = _volume(db, made["id"], out["revision"], 0.4, now=101.3)
    assert len(_rows(db, made["id"])) == 10
    # After an undo, a tweak never joins the step before it: it replaces the redo tail.
    out = store.undo(db.conn, made["id"], out["revision"], now=101.4)
    out = _volume(db, made["id"], out["revision"], 0.3, now=101.5)
    assert len(_rows(db, made["id"])) == 10 and out["can_redo"] is False


def test_a_coalesced_25th_revision_keeps_its_snapshot_up_to_date(db):
    made = _new(db)
    out = made
    for n in range(1, 25):
        out = _rename(db, out, n, now=n * 100)
    out = _volume(db, made["id"], out["revision"], 0.9, now=5000.0)
    out = _volume(db, made["id"], out["revision"], 0.4, now=5000.5)
    row = _rows(db, made["id"])[25]
    assert row["seq"] == 25 and row["snapshot"] == _text(db, made["id"])
    out = _rename(db, out, 99, now=6000)
    assert model.canonical(store.document_at(db.conn, made["id"], 26)) == _text(db, made["id"])


# ---- a project and its clip ---------------------------------------------------------------------


def test_a_clip_has_one_project(db):
    clip_id = _clip(db)
    first = _new(db, clip_id=clip_id, video_id="vid1")
    second = _new(db, clip_id=clip_id, video_id="vid1")
    assert first["created"] is True and second["created"] is False and second["id"] == first["id"]
    assert store.for_clip(db.conn, clip_id) == first["id"] and store.for_clip(db.conn, clip_id + 1) is None
    assert db.conn.execute("SELECT COUNT(*) FROM projects").fetchone()[0] == 1


def test_a_project_follows_its_clip_through_a_re_render(db):
    old = _clip(db)
    made = _new(db, clip_id=old, video_id="vid1")
    bystander = _new(db)
    # What server/jobs.py _rerender_clip does: the row goes, a new one comes, then follow_clip_id.
    db.conn.execute("DELETE FROM clips WHERE id = ?", (old,))
    db.conn.commit()
    new = _clip(db, start=12.0, end=41.0)
    assert new != old
    db.follow_clip_id(old, new)
    assert store.get(db.conn, made["id"])["clip_id"] == new
    assert store.for_clip(db.conn, new) == made["id"] and store.for_clip(db.conn, old) is None
    assert store.get(db.conn, bystander["id"])["clip_id"] is None
    assert store.get(db.conn, made["id"])["revision"] == 0          # following is not a change to the project


def test_an_export_record_follows_its_clip_and_lets_go_of_a_deleted_one(db):
    old = _clip(db)
    made = _new(db, clip_id=old, video_id="vid1")
    export_id = store.add_export(db.conn, made["id"], 0, clip_id=old, status="done", now=0)
    db.conn.execute("DELETE FROM clips WHERE id = ?", (old,))
    db.conn.commit()
    new = _clip(db, start=12.0, end=41.0)
    db.follow_clip_id(old, new)
    assert [(e["id"], e["clip_id"]) for e in store.exports(db.conn, made["id"])] == [(export_id, new)]
    db.delete_clip(new)
    assert [e["clip_id"] for e in store.exports(db.conn, made["id"])] == [None]
    again = _clip(db, start=50.0, end=80.0)
    store.set_export(db.conn, export_id, clip_id=again, now=1)
    db.delete_video("vid1")
    assert [e["clip_id"] for e in store.exports(db.conn, made["id"])] == [None]


def test_a_clip_that_follows_onto_one_with_a_project_leaves_no_project_on_a_gone_id(db):
    """Never expected (a re-render's new row is new), but a clip has one
    project: the one already there stays, and the other keeps its document
    with no clip rather than the id of a clip that is gone."""
    old, new = _clip(db), _clip(db, start=50.0, end=80.0)
    left, kept = _new(db, clip_id=old, video_id="vid1"), _new(db, clip_id=new, video_id="vid1")
    db.conn.execute("DELETE FROM clips WHERE id = ?", (old,))
    db.conn.commit()
    db.follow_clip_id(old, new)
    assert store.for_clip(db.conn, new) == kept["id"] and store.for_clip(db.conn, old) is None
    assert store.get(db.conn, left["id"])["clip_id"] is None
    assert store.get(db.conn, left["id"])["document"] == left["document"]


def test_follow_clip_id_does_nothing_when_no_project_exists(db):
    old, new = _clip(db), _clip(db, start=50.0, end=80.0)
    db.follow_clip_id(old, new)
    assert db.conn.execute("SELECT COUNT(*) FROM projects").fetchone()[0] == 0
    assert db.conn.execute("SELECT COUNT(*) FROM project_revisions").fetchone()[0] == 0


def test_the_re_render_job_calls_follow_clip_id():
    """The wiring: server/jobs.py's re-render moves the dependent rows with
    StateDB.follow_clip_id, which now moves the project too."""
    from pathlib import Path

    source = (Path(__file__).resolve().parent.parent / "server" / "jobs.py").read_text(encoding="utf-8")
    assert 'db.follow_clip_id(clip["id"], new_row["id"])' in source


def test_a_clip_deleted_under_a_project_leaves_the_project_whole(db):
    clip_id = _clip(db)
    made = _new(db, clip_id=clip_id, video_id="vid1")
    out = _rename(db, made, 1)
    db.delete_clip(clip_id)
    got = store.get(db.conn, made["id"])
    assert got["clip_id"] is None and got["revision"] == 1 and got["name"] == "Name 1"
    assert store.for_clip(db.conn, clip_id) is None
    back = store.undo(db.conn, made["id"], out["revision"])
    assert back["document"]["name"] == "Test"
    # The clip's place can have a project again (a new clip made at the same window).
    again = _clip(db)
    assert _new(db, clip_id=again, video_id="vid1")["created"] is True


def test_a_video_deleted_under_its_projects_leaves_them_whole(db):
    first, second = _clip(db), _clip(db, start=50.0, end=80.0)
    other = _clip(db, video_id="vid2")
    made = [_new(db, clip_id=c, video_id="vid1") for c in (first, second)]
    kept = _new(db, clip_id=other, video_id="vid2")
    db.delete_video("vid1")
    for entry in made:
        got = store.get(db.conn, entry["id"])
        assert got["clip_id"] is None and got["video_id"] == "vid1"
        assert store.apply_ops(db.conn, entry["id"], 0, [{"op": "add_track", "kind": "text"}], ENV)["revision"] == 1
    assert store.get(db.conn, kept["id"])["clip_id"] == other


def test_make_clips_again_keeps_a_clip_that_has_a_project(db):
    with_project, plain = _clip(db), _clip(db, start=50.0, end=80.0)
    assert [c["replace"] for c in remake.classify(db, "vid1")] == [True, True]
    made = _new(db, clip_id=with_project, video_id="vid1")
    judged = {c["id"]: c for c in remake.classify(db, "vid1")}
    assert judged[with_project]["replace"] is False and judged[with_project]["reasons"] == ["edited"]
    assert judged[plain]["replace"] is True and judged[plain]["reasons"] == []
    assert remake.counts(list(judged.values()))["kept_because"] == {"edited": 1}
    store.delete(db.conn, made["id"])
    assert [c["replace"] for c in remake.classify(db, "vid1")] == [True, True]


def test_make_clips_again_reads_a_database_with_no_projects_table(db):
    clip_id = _clip(db)
    db.conn.execute("DROP TABLE projects")
    db.conn.commit()
    assert [c["id"] for c in remake.classify(db, "vid1") if c["replace"]] == [clip_id]


# ---- another version's document -----------------------------------------------------------------


def test_a_newer_versions_document_opens_read_only(db):
    made = _new(db)
    newer = project(clips=1)
    newer["schema"] = model.SCHEMA + 1
    newer["from_the_future"] = {"kept": True}
    text = model.canonical(newer)
    db.conn.execute("UPDATE projects SET doc = ?, doc_schema = ? WHERE id = ?", (text, model.SCHEMA + 1, made["id"]))
    db.conn.commit()
    got = store.get(db.conn, made["id"])
    assert got["read_only"] is True and (got["can_undo"], got["can_redo"]) == (False, False)
    assert got["document"]["from_the_future"] == {"kept": True}
    for call in (lambda: store.apply_ops(db.conn, made["id"], 0, [{"op": "add_track", "kind": "text"}], ENV),
                 lambda: store.apply_ops(db.conn, made["id"], 0, [], ENV, dry_run=True),
                 lambda: store.undo(db.conn, made["id"], 0), lambda: store.redo(db.conn, made["id"], 0)):
        with pytest.raises(store.ReadOnly, match="newer version of Clips Kitty"):
            call()
    assert _text(db, made["id"]) == text                        # never rewritten


def test_an_older_versions_document_is_brought_up_to_date_on_load(db, monkeypatch):
    made = _new(db)
    out = _rename(db, made, 1, now=100)
    old_text = _text(db, made["id"])

    def to_2(doc):
        doc["schema"] = 2
        doc["added_in_2"] = "default"
        return doc

    monkeypatch.setattr(model, "SCHEMA", 2)
    monkeypatch.setattr(migrate, "MIGRATIONS", {1: to_2})
    got = store.get(db.conn, made["id"])
    assert got["document"]["schema"] == 2 and got["document"]["added_in_2"] == "default"
    assert got["read_only"] is False and got["can_undo"] is False
    assert _text(db, made["id"]) == old_text                    # reading saves nothing
    with pytest.raises(store.NothingToDo):
        store.undo(db.conn, made["id"], out["revision"])
    changed = store.apply_ops(db.conn, made["id"], out["revision"], [{"op": "add_track", "kind": "text"}], ENV, now=200)
    saved = json.loads(_text(db, made["id"]))
    assert saved["schema"] == 2 and saved["added_in_2"] == "default" and model.track(saved, "T2") is not None
    listed = store.revisions(db.conn, made["id"])
    assert (listed["base"], listed["cursor"], listed["tip"]) == (1, 2, 2)      # history starts again at the update
    back = store.undo(db.conn, made["id"], changed["revision"], now=300)
    assert back["document"]["schema"] == 2 and model.track(back["document"], "T2") is None
    assert back["can_undo"] is False


def test_a_document_too_old_or_unreadable_is_said_plainly(db, monkeypatch):
    made = _new(db)
    db.conn.execute("UPDATE projects SET doc = '{not json' WHERE id = ?", (made["id"],))
    db.conn.commit()
    with pytest.raises(store.Damaged, match="can't be read"):
        store.get(db.conn, made["id"])
    with pytest.raises(store.Damaged):
        store.apply_ops(db.conn, made["id"], 0, [], ENV)
    other = _new(db)
    monkeypatch.setattr(model, "SCHEMA", 3)
    with pytest.raises(store.Damaged, match="can no longer open"):
        store.get(db.conn, other["id"])


def test_migrate_load_is_pure_and_keeps_unknown_fields():
    doc = project(clips=1)
    doc["mystery"] = {"a": [1, 2]}
    doc["tracks"][0]["items"][0]["mystery"] = 7
    before = model.canonical(doc)
    assert model.validate(doc) == []

    def to_2(d):
        d["schema"] = 2
        return d

    loaded, read_only = migrate.load(doc, migrations={1: to_2}, target=2)
    assert not read_only and loaded["schema"] == 2 and loaded["mystery"] == {"a": [1, 2]}
    assert loaded["tracks"][0]["items"][0]["mystery"] == 7 and model.canonical(doc) == before
    same, read_only = migrate.load(doc)
    assert same is doc and not read_only
    newer, read_only = migrate.load({**doc, "schema": 9})
    assert read_only and newer["schema"] == 9
    for bad in (None, {}, {"schema": 0}, {"schema": "1"}):
        with pytest.raises(migrate.MigrationError):
            migrate.load(bad)
    with pytest.raises(migrate.MigrationError, match="went wrong"):
        migrate.load(doc, migrations={1: lambda d: d}, target=2)
    # An operation keeps what it doesn't know.
    changed = ops.apply(doc, [{"op": "split_item", "item_id": "i4", "at_us": S}], ENV).document
    assert changed["mystery"] == {"a": [1, 2]} and changed["tracks"][0]["items"][0]["mystery"] == 7


# ---- the tables ---------------------------------------------------------------------------------


def test_the_tables_arrive_on_a_database_from_before_step_5(tmp_path):
    path = tmp_path / "old" / "state.db"
    old = StateDB(path)
    clip_id = _clip(old)
    for table in ("projects", "project_revisions", "media_assets", "project_exports"):
        old.conn.execute(f"DROP TABLE {table}")
    old.conn.commit()
    old.conn.close()
    reopened = StateDB(path)
    try:
        tables = {r[0] for r in reopened.conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
        assert {"projects", "project_revisions", "media_assets", "project_exports"} <= tables
        assert reopened.get_clip(clip_id) is not None             # what was there is as it was
        made = store.create(reopened.conn, project(clips=1), clip_id=clip_id, video_id="vid1", now=0)
        assert store.get(reopened.conn, made["id"])["clip_id"] == clip_id
    finally:
        reopened.conn.close()
    again = StateDB(path)                                         # and a second start changes nothing
    try:
        assert store.get(again.conn, made["id"])["revision"] == 0
    finally:
        again.conn.close()


def test_projects_clip_id_is_no_foreign_key():
    assert len(PROJECT_TABLES) == 6
    for statement in PROJECT_TABLES:
        assert "REFERENCES" not in statement.upper() and "FOREIGN KEY" not in statement.upper()


def test_a_plain_run_never_touches_the_project_tables(db):
    """Adding, changing and re-rendering clips with no project leaves every
    project table empty."""
    old = _clip(db, render_opts=json.dumps({"edit": {"speed": 1.25}}))
    db.set_clip(old, title="A title")
    db.mark_edited(old)
    db.conn.execute("DELETE FROM clips WHERE id = ?", (old,))
    db.conn.commit()
    new = _clip(db, start=11.0, end=40.0)
    db.follow_clip_id(old, new)
    db.delete_clip(new)
    db.delete_video("vid1")
    for table in ("projects", "project_revisions", "media_assets", "project_exports"):
        assert db.conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0


# ---- media assets and exports -------------------------------------------------------------------


def test_media_assets_are_kept_once_under_their_sha256(db, tmp_path):
    source = tmp_path / "My Sound.MP3"
    source.write_bytes(b"not really a sound" * 100)
    data_dir = tmp_path / "data"
    first = store.add_asset(db.conn, data_dir, source, kind="audio", name="My sound",
                            facts={"duration_us": 3 * S, "has_audio": True}, now=0)
    import hashlib

    sha = hashlib.sha256(source.read_bytes()).hexdigest()
    assert first["sha256"] == sha and first["ext"] == ".mp3" and first["size"] == 1800
    kept = store.asset_path(data_dir, sha, ".mp3")
    assert kept == data_dir / "media" / "assets" / sha[:2] / f"{sha}.mp3"
    assert kept.read_bytes() == source.read_bytes()
    copy = tmp_path / "copy.mp3"
    copy.write_bytes(source.read_bytes())
    second = store.add_asset(db.conn, data_dir, copy, kind="audio", now=5)
    assert second == first and db.conn.execute("SELECT COUNT(*) FROM media_assets").fetchone()[0] == 1
    assert [p.name for p in kept.parent.iterdir()] == [kept.name]
    assert store.asset(db.conn, "0" * 64) is None
    with pytest.raises(store.StoreError):
        store.add_asset(db.conn, data_dir, source, kind="document")
    for bad in ("../../etc/passwd", "ABC", sha + "/.."):
        with pytest.raises(store.StoreError):
            store.asset_path(data_dir, bad)


def test_exports_are_recorded_against_the_revision_they_were_made_from(db):
    made = _new(db)
    out = _rename(db, made, 1)
    export_id = store.add_export(db.conn, made["id"], out["revision"], preset={"codec": "h264"}, now=0)
    store.set_export(db.conn, export_id, status="done", path="projects/x-r1.mp4", clip_id=7, now=1)
    listed = store.exports(db.conn, made["id"])
    assert [(e["head"], e["status"], e["path"], e["clip_id"]) for e in listed] == [(1, "done", "projects/x-r1.mp4", 7)]
    assert json.loads(listed[0]["preset"]) == {"codec": "h264"}
    with pytest.raises(store.NotFound):
        store.add_export(db.conn, "nope", 0)


def test_an_imported_clip_is_stored_and_changed_like_any_project(db):
    clip_id = _clip(db, render_opts=json.dumps({"edit": {"keep": [[0.0, 10.0], [15.0, 30.0]]}, "crop": "center"}))
    row = db.get_clip(clip_id)
    doc = importer.import_clip(importer.clip_of_row(row), {"fps": {"num": 30, "den": 1}})
    made = store.create(db.conn, doc, clip_id=clip_id, video_id="vid1", now=0)
    out = store.apply_ops(db.conn, made["id"], 0, [{"op": "split_item", "item_id": "i2", "at_us": 4 * S}], ENV, now=1)
    assert [i["id"] for i in out["document"]["tracks"][0]["items"]] == ["i2", "i4", "i3"]
    assert store.undo(db.conn, made["id"], out["revision"])["document"] == doc


# ---- found in review (5-D15 onward) -------------------------------------------------------------


def _project_row(db, project_id: str) -> sqlite3.Row:
    return db.conn.execute("SELECT * FROM projects WHERE id = ?", (project_id,)).fetchone()


def test_tweaks_that_end_where_they_began_leave_no_undo_step(db):
    made = _new(db)
    pid = made["id"]
    start = _text(db, pid)
    out = _rename(db, made, 1, now=10)
    named = _text(db, pid)
    # A slider dragged out and back within the window.
    out = _volume(db, pid, out["revision"], 0.4, now=100.0)
    out = _volume(db, pid, out["revision"], 0.7, now=100.5)
    assert [r["seq"] for r in _rows(db, pid)] == [0, 1, 2]
    out = _volume(db, pid, out["revision"], 1.0, now=101.0)        # where it began
    assert out["changed"] is True and out["revision"] == 4           # the head moved with each tweak
    assert out["can_undo"] is True and out["can_redo"] is False
    assert _text(db, pid) == named
    assert [r["seq"] for r in _rows(db, pid)] == [0, 1]              # and no step is left for the drag
    row = _project_row(db, pid)
    assert (row["cursor"], row["tip"], row["base"]) == (1, 1, 0)
    # The next tweak, even a moment later, is a step of its own: it never joins the rename.
    out = _volume(db, pid, out["revision"], 0.5, now=101.2)
    assert [r["seq"] for r in _rows(db, pid)] == [0, 1, 2]
    back = store.undo(db.conn, pid, out["revision"], now=300)
    assert back["changed"] is True and _text(db, pid) == named
    back = store.undo(db.conn, pid, back["revision"], now=301)
    assert back["changed"] is True and _text(db, pid) == start and back["can_undo"] is False
    # A switch flipped twice: the same. Flipped three times: one step.
    out = store.redo(db.conn, pid, back["revision"], now=400)
    for n, mute in enumerate((True, False)):
        out = store.apply_ops(db.conn, pid, out["revision"],
                              [{"op": "set_track_props", "track_id": "A2", "mute": mute}], ENV, now=500 + n * 0.3)
    assert [r["seq"] for r in _rows(db, pid)] == [0, 1] and _text(db, pid) == named
    for n, mute in enumerate((True, False, True)):
        out = store.apply_ops(db.conn, pid, out["revision"],
                              [{"op": "set_track_props", "track_id": "A2", "mute": mute}], ENV, now=600 + n * 0.3)
    assert [r["seq"] for r in _rows(db, pid)] == [0, 1, 2]
    assert store.undo(db.conn, pid, out["revision"], now=700)["changed"] is True and _text(db, pid) == named


def test_a_drag_out_and_back_on_the_25th_revision_leaves_the_snapshots_right(db):
    made = _new(db)
    pid = made["id"]
    out = made
    for n in range(1, 25):
        out = _rename(db, out, n, now=n * 100)
    before = _text(db, pid)
    out = _volume(db, pid, out["revision"], 0.9, now=5000.0)        # revision 25, with its snapshot
    assert _rows(db, pid)[25]["snapshot"] is not None
    out = _volume(db, pid, out["revision"], 1.0, now=5000.5)        # and back: revision 25 is gone
    assert [r["seq"] for r in _rows(db, pid)] == list(range(25)) and _text(db, pid) == before
    out = _volume(db, pid, out["revision"], 0.3, now=6000.0)        # a new revision 25
    rows = _rows(db, pid)
    assert rows[25]["snapshot"] == _text(db, pid)
    assert model.canonical(store.document_at(db.conn, pid, 25)) == _text(db, pid)
    assert store.undo(db.conn, pid, out["revision"], now=7000)["changed"] and _text(db, pid) == before


def test_a_saved_row_holding_nan_is_damaged_never_a_crash(db):
    """JSON as Python reads it lets NaN and Infinity through. A row holding
    one (hand-edited, or damaged) could never be written back: it is
    Damaged, in plain words, and nothing is changed."""
    made = _new(db)
    pid = made["id"]
    out = _rename(db, made, 1, now=1)
    good = _text(db, pid)
    for constant in ("NaN", "Infinity", "-Infinity"):
        bad = good.replace('"master_volume":1.0', f'"master_volume":{constant}')
        assert bad != good and json.loads(bad)             # Python's own reader takes it
        db.conn.execute("UPDATE projects SET doc = ? WHERE id = ?", (bad, pid))
        db.conn.commit()
        for call in (lambda: store.get(db.conn, pid),
                     lambda: store.apply_ops(db.conn, pid, out["revision"], [{"op": "add_track", "kind": "text"}], ENV),
                     lambda: store.apply_ops(db.conn, pid, out["revision"], [{"op": "add_track", "kind": "text"}], ENV,
                                             dry_run=True),
                     lambda: store.undo(db.conn, pid, out["revision"])):
            with pytest.raises(store.Damaged, match="can't be read"):
                call()
        assert _text(db, pid) == bad and _project_row(db, pid)["head"] == out["revision"]
    db.conn.execute("UPDATE projects SET doc = ? WHERE id = ?", (good, pid))
    # The history's rows are read the same way.
    db.conn.execute("UPDATE project_revisions SET inverse = ?, ops = ? WHERE project_id = ? AND seq = 1",
                    ('[{"op":"restore","patch":["=",NaN]}]', '[{"op":"set_project_props","name":NaN}]', pid))
    db.conn.execute("UPDATE project_revisions SET snapshot = ? WHERE project_id = ? AND seq = 0",
                    (good.replace('"master_volume":1.0', '"master_volume":NaN'), pid))
    db.conn.commit()
    with pytest.raises(store.Damaged):
        store.undo(db.conn, pid, out["revision"])
    with pytest.raises(store.Damaged):
        store.document_at(db.conn, pid, 1)
    listed = store.revisions(db.conn, pid)["revisions"]
    assert [r["ops"] for r in listed] == [[], []]
    json.dumps(listed, allow_nan=False)
    assert _text(db, pid) == good and store.get(db.conn, pid)["revision"] == out["revision"]


def test_a_project_whose_clips_row_is_gone_reports_no_clip(db):
    """A re-render deletes the old clip's row before the new one exists
    (server/jobs.py). Stopped in between, the project's row still names the
    old id; the store never reports a clip that isn't there."""
    clip_id = _clip(db)
    made = store.create(db.conn, project(clips=1), clip_id=clip_id, video_id="vid1", now=0)
    assert made["clip_id"] == clip_id and store.get(db.conn, made["id"])["clip_id"] == clip_id
    db.conn.execute("DELETE FROM clips WHERE id = ?", (clip_id,))
    db.conn.commit()
    got = store.get(db.conn, made["id"])
    assert got["clip_id"] is None and got["video_id"] == "vid1"
    out = store.apply_ops(db.conn, made["id"], 0, [{"op": "add_track", "kind": "text"}], ENV, now=1)
    assert out["revision"] == 1
    # When the new row does arrive, the project follows it as before.
    new_id = _clip(db, 10.5, 40.0)
    assert new_id != clip_id
    db.follow_clip_id(clip_id, new_id)
    db.conn.commit()
    assert store.get(db.conn, made["id"])["clip_id"] == new_id


def test_a_label_with_half_a_character_is_saved_without_it(db):
    made = _new(db)
    out = _rename(db, made, 1, now=1, label="Renamed \ud83d  twice \U0001F389")
    assert out["revision"] == 1
    assert _rows(db, made["id"])[1]["label"] == "Renamed twice \U0001F389"
