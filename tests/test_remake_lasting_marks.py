"""Make clips again keeps a clip a person used, whatever they do elsewhere in
the app afterwards (#190, QC fix round 6).

Some uses left their only lasting trace in clip_feedback, the creator's
learning, which the Creators page's "Wipe learned memory for this creator"
(DELETE /creators/{id}/memory) and "Delete this creator" (DELETE
/creators/{id}) erase while the clip stays:

- An edit applied (Apply in the timeline editor, a caption fix, the AI chat)
  whose render failed. The worker marks only the new row of a render that
  works, so the person's edit was left only in the failed job, for Retry.
  Make clips again then deleted the clip, and Retry failed with "No clip
  with id". The routes now mark the clip edited as they queue the render
  (StateDB.mark_edited), and a render job in any state counts too.
- An export whose star was taken off since (the star is exported_at). Every
  export now also marks first_exported_at (StateDB.mark_exported), which the
  star and erasing learning leave alone.

The routes run in-process (TestClient) with the real worker
(server/jobs.py), which runs the render jobs (the real _rerender_clip) and
the Make clips again job; only process_video, the render itself on a Retry,
the AI's answer and the editor lane's encode are stand-ins.
"""

import json
import shutil
import threading
import time
import types
from pathlib import Path

import pytest

from core.state import StateDB, _now

ROOT = Path(__file__).resolve().parent.parent
VID = "abcdefghijk"
URL = f"https://www.youtube.com/watch?v={VID}"
CUTS = {"keep": [[0.0, 20.0], [35.0, 60.0]]}
TYPED = "What I really said"


@pytest.fixture
def world(tmp_path, monkeypatch):
    """A processed video by a creator, with the real app's routes and the
    real worker, which `drain` runs until a job has ended. Its download is
    gone (`restore_source` puts it back), so a render fails as a person's
    does when "Delete downloads once published" or Storage took it."""
    for module in ("numpy", "cv2", "PIL", "yaml", "yt_dlp", "fastapi", "httpx"):
        pytest.importorskip(module)
    from fastapi.testclient import TestClient

    from core import pipeline, progress, queue
    from main import BUNDLED_CONFIG, load_config
    from plugins import session
    from server import jobs
    from server.api import create_app
    from server.events import broadcaster
    from video import encoding

    monkeypatch.delenv("CLIPS_KITTY_SESSION_SECRET", raising=False)
    settings = tmp_path / "settings.yaml"
    shutil.copy(ROOT / "config" / "settings.yaml", settings)
    config = load_config(BUNDLED_CONFIG)
    data = tmp_path / "data"
    config["paths"]["data_dir"] = str(data)
    config.setdefault("clips", {})["outro"] = False
    app = create_app(config, settings)
    events: list = []
    monkeypatch.setattr(broadcaster, "publish", events.append)
    monkeypatch.setattr(jobs.broadcaster, "publish", events.append)
    monkeypatch.setattr(jobs.Worker, "_check_plan", staticmethod(lambda *a, **k: None))
    monkeypatch.setattr(jobs.Worker, "_check_local_model", staticmethod(lambda *a, **k: None))
    monkeypatch.setattr(progress, "_handler", progress._handler)   # put back after the runs
    client = TestClient(app, base_url="http://127.0.0.1")

    db = StateDB(data / "state.db")
    db.upsert_video(VID, title="A stream", channel_name="someone")
    db.set_video_source(VID, URL, "youtube")
    db.set_video_status(VID, "done")
    db.set_outcome(VID, {"candidates": 3})
    creator = db.conn.execute("INSERT INTO creators (display_name, created_at) VALUES ('Someone', ?)",
                              (_now(),)).lastrowid
    db.conn.execute("UPDATE videos SET creator_id = ? WHERE video_id = ?", (creator, VID))
    db.conn.commit()
    folder = data / "clips" / "someone" / f"A stream [{VID}]"
    folder.mkdir(parents=True)
    (data / "previews").mkdir()

    def process_video(url, cfg, run_db, force=False):
        """A run that makes one new clip, as core/pipeline.py registers one."""
        run_db.set_outcome(VID, {"clips": 1})
        path = folder / "clip_00500-00530.mp4"
        path.write_bytes(b"NEW RENDER")
        run_db.add_clip(VID, 500.0, 530.0, 80, "new hook", path=str(path), status="queued", title="new",
                        description="", hashtags="[]", scores="{}",
                        render_opts=json.dumps({"job_captions": False}))
        return []

    def render_files(_source, candidate, _segments, clip_dir, _config, render_opts, _lang):
        """A render that works, once the download is back."""
        clip_dir.mkdir(parents=True, exist_ok=True)
        out = clip_dir / f"clip_{int(candidate.start):05d}-{int(candidate.end):05d}.mp4"
        out.write_bytes(b"THE PERSON'S EDIT")
        return out, json.dumps(render_opts)

    monkeypatch.setattr(pipeline, "process_video", process_video)
    monkeypatch.setattr(pipeline, "_render_files", render_files)

    # The editor lane's encode, and the encoders an export asks about
    # (as tests/test_export_route.py): nothing is encoded or probed.
    def encode(src, dst, preset, *, workdir, run, on_progress=None):
        Path(dst).write_bytes(Path(src).read_bytes() + b" smaller")
        return {"bytes": Path(dst).stat().st_size, "width": 720, "height": 1280}

    app.state.editor_lane._encode = encode
    monkeypatch.setattr(encoding, "_mode", "auto")
    monkeypatch.setattr(encoding, "encoder_for",
                        lambda codec, pref="auto": encoding.Encoder(codec, "cpu", "libx264")
                        if codec == "h264" and pref != "gpu" else None)

    def clip(start, end):
        path = folder / f"clip_{int(start):05d}-{int(end):05d}.mp4"
        path.write_bytes(f"OLD {start}-{end}".encode())
        clip_id = db.add_clip(VID, start, end, 70, "hook", path=str(path), status="queued", title="t",
                              description="d", hashtags="[]", render_opts=json.dumps({"job_captions": True}))
        (data / "previews" / f"clip_{clip_id}.mp4").write_bytes(b"draft")
        return clip_id

    def drain(job_id, timeout=60):
        """Run the real worker until this job has ended, then stop it."""
        queue.set_paused(db, False)
        worker = jobs.Worker(config)
        worker.prefetch = types.SimpleNamespace(wait_for=lambda vid: None, maybe_start=lambda _db: None)
        thread = threading.Thread(target=worker.run, daemon=True)
        thread.start()
        deadline = time.time() + timeout
        while db.get_job(job_id)["status"] in ("queued", "running") and time.time() < deadline:
            time.sleep(0.05)
        worker.stop()
        thread.join(timeout=15)
        return db.get_job(job_id)

    def remake():
        """Make clips again, as the dialog does: what it would keep, then the run."""
        preview = client.get(f"/videos/{VID}/remake").json()
        r = client.post(f"/videos/{VID}/remake", json=preview["options"])
        assert r.status_code == 200, r.text
        return preview, drain(r.json()["job_id"])

    def restore_source():
        (data / "downloads").mkdir(exist_ok=True)
        (data / "downloads" / f"{VID}.mp4").write_bytes(b"source")
        (data / "transcripts").mkdir(exist_ok=True)
        (data / "transcripts" / f"{VID}.json").write_text('{"segments": []}', encoding="utf-8")

    def ids():
        return {r[0] for r in db.conn.execute("SELECT id FROM clips WHERE video_id = ?", (VID,))}

    def at(start, end):
        return db.conn.execute("SELECT * FROM clips WHERE video_id = ? AND start_s = ? AND end_s = ?",
                               (VID, start, end)).fetchone()

    yield types.SimpleNamespace(
        client=client, app=app, db=db, data=data, creator=creator, clip=clip, drain=drain, remake=remake,
        restore_source=restore_source, ids=ids, at=at, events=events, monkeypatch=monkeypatch,
        session={"X-Clips-Kitty-Session": session.path(data).read_text(encoding="utf-8").strip()})
    db.close()


def _ai_answers(w, start):
    """The AI chat works out a new start, as interpret_edit answers."""
    from analysis import clip_edit
    from core import pipeline
    from llm import registry

    w.monkeypatch.setattr(clip_edit, "interpret_edit", lambda message, **_k: {
        "reply": "Done", "needs_render": True, "start": start, "end": None, "render_opts": {}})
    w.monkeypatch.setattr(pipeline, "_with_usable_model", lambda llm: llm)
    w.monkeypatch.setattr(registry, "create_backend", lambda _spec: None)


def _apply(w, how, clip_id) -> int:
    """A person's edit of the clip, through the route the app uses: the job's id."""
    if how == "apply":          # the timeline editor's Apply
        r = w.client.post(f"/clips/{clip_id}/render", json={"render_opts": {"edit": CUTS}})
    elif how == "captions":     # a caption fix: the typed lines are only in the job
        r = w.client.put(f"/clips/{clip_id}/captions",
                         json={"lines": [{"start": 0.0, "end": 2.0, "text": TYPED}]})
    else:                       # the AI chat: "start 5 seconds earlier"
        _ai_answers(w, 95.0)
        r = w.client.post(f"/clips/{clip_id}/ai-edit", json={"message": "start 5 seconds earlier"})
    assert r.status_code == 200, r.text
    return r.json()["job_id"]


def _applied(w, how):
    """The clip as the person's edit made it, once it has rendered."""
    if how == "ai":
        row = w.at(95.0, 160.0)
        return row is not None and w.at(100.0, 160.0) is None
    row = w.at(100.0, 160.0)
    opts = json.loads(row["render_opts"]) if row is not None else {}
    if how == "apply":
        return opts.get("edit") == CUTS
    return any(line.get("text") == TYPED for line in opts.get("caption_lines") or [])


def _wipe(w, how):
    r = w.client.delete(f"/creators/{w.creator}/memory" if how == "memory" else f"/creators/{w.creator}")
    assert r.status_code == 200, r.text
    assert w.db.conn.execute("SELECT COUNT(*) FROM clip_feedback").fetchone()[0] == 0


# ---- an edit whose render failed ------------------------------------------------------------


@pytest.mark.parametrize("wipe", ["memory", "creator"])
@pytest.mark.parametrize("how", ["apply", "captions", "ai"])
def test_a_failed_edit_keeps_its_clip_after_the_learning_is_erased_and_its_retry_applies(world, how, wipe):
    w = world
    untouched, edited = w.clip(10.0, 70.0), w.clip(100.0, 160.0)
    job_id = _apply(w, how, edited)
    job = w.drain(job_id)
    assert job["status"] == "failed" and "Source video missing" in job["error"]
    _wipe(w, wipe)

    preview, run = w.remake()
    reasons = {c["id"]: c["reasons"] for c in preview["clips"]}
    assert run["status"] == "done"
    assert w.ids() == {edited, w.at(500.0, 530.0)["id"]}, "the clip the person edited is kept"
    assert reasons[edited] == ["edited"] and reasons[untouched] == []
    assert w.db.get_clip(edited)["edited_at"], "marked as the render was queued"
    assert w.db.get_clip(edited)["path"] and Path(w.db.get_clip(edited)["path"]).read_bytes() == b"OLD 100.0-160.0"

    # The download is back: Retry on the Queue page applies the edit.
    w.restore_source()
    r = w.client.post(f"/jobs/{job_id}/retry")
    assert r.status_code == 200, r.text
    retried = w.drain(r.json()["job_id"])
    assert retried["status"] == "done", retried["error"]
    assert _applied(w, how)


@pytest.mark.parametrize("how", ["apply", "captions", "ai"])
def test_a_failed_edit_keeps_its_clip_after_the_queue_is_cleared_too(world, how):
    """The mark the route sets is what lasts: the failed job is gone too."""
    w = world
    untouched, edited = w.clip(10.0, 70.0), w.clip(100.0, 160.0)
    assert w.drain(_apply(w, how, edited))["status"] == "failed"
    _wipe(w, "memory")
    assert w.client.post("/queue/clear", json={"what": "failed"}).json()["deleted"] == 1
    _, run = w.remake()
    assert run["status"] == "done"
    assert edited in w.ids() and untouched not in w.ids()


def test_a_failed_render_named_by_its_job_alone_keeps_its_clip(world):
    """A render job from another version, which doesn't mark the clip: its
    row, in any state, counts as long as the Queue page keeps it."""
    w = world
    untouched, edited = w.clip(10.0, 70.0), w.clip(100.0, 160.0)
    job_id = w.db.add_job("render", json.dumps({"clip_id": edited, "render_opts": {"edit": CUTS}}))
    assert w.drain(job_id)["status"] == "failed"
    assert w.db.get_clip(edited)["edited_at"] == ""
    _, run = w.remake()
    assert run["status"] == "done"
    assert edited in w.ids() and untouched not in w.ids()


def test_the_mark_changes_nothing_a_screen_shows(world):
    """edited_at is in the clip's JSON, which no screen reads; the clip's
    words, star, file and fingerprint are as they were."""
    w = world
    clip_id = w.clip(100.0, 160.0)
    before = w.db.get_clip(clip_id)
    _apply(w, "apply", clip_id)
    after = w.db.get_clip(clip_id)
    changed = {k for k in before.keys() if before[k] != after[k]}
    assert changed == {"edited_at"}
    shown = {c["id"]: c for c in w.client.get(f"/videos/{VID}/clips").json()}[clip_id]
    assert shown["edited_at"] == after["edited_at"] and "first_exported_at" not in shown


# ---- an export whose star was taken off ------------------------------------------------------


def _export(w, how, clip_id, out):
    if how == "copy":            # Export, one clip (the MCP tool export_clip uses it too)
        r = w.client.post(f"/clips/{clip_id}/export", json={"folder": str(out)})
    elif how == "batch":         # Export all
        r = w.client.post("/export/batch", json={"clip_ids": [clip_id], "folder": str(out), "preset": "original"})
    else:                        # a smaller copy, made on the editor lane
        r = w.client.post(f"/clips/{clip_id}/export", headers=w.session,
                          json={"folder": str(out), "preset": {"kind": "custom", "resolution": "720p"}})
        assert r.status_code == 200, r.text
        assert w.app.state.editor_lane.run_pending() == 1
    assert r.status_code == 200, r.text


@pytest.mark.parametrize("how", ["copy", "batch", "lane"])
def test_an_export_keeps_its_clip_after_the_star_is_off_and_the_learning_erased(world, how):
    w = world
    untouched, exported = w.clip(10.0, 70.0), w.clip(100.0, 160.0)
    out = w.data.parent / "my exports"
    _export(w, how, exported, out)
    assert len(list(out.glob("*.mp4"))) == 1
    first = w.db.get_clip(exported)["first_exported_at"]
    assert first and w.db.get_clip(exported)["exported_at"]
    r = w.client.patch(f"/clips/{exported}", json={"exported": False})
    assert r.status_code == 200 and r.json()["exported_at"] == "" and "first_exported_at" not in r.json()
    _wipe(w, "memory")

    preview, run = w.remake()
    assert {c["id"]: c["reasons"] for c in preview["clips"]}[exported] == ["exported"]
    assert run["status"] == "done"
    assert exported in w.ids() and untouched not in w.ids()
    assert w.db.get_clip(exported)["first_exported_at"] == first


def test_exporting_again_keeps_the_first_time_and_starring_by_hand_is_no_export(world):
    w = world
    exported, starred = w.clip(10.0, 70.0), w.clip(100.0, 160.0)
    w.db.set_clip(exported, first_exported_at="2026-01-01T00:00:00")
    _export(w, "copy", exported, w.data.parent / "out")
    assert w.db.get_clip(exported)["first_exported_at"] == "2026-01-01T00:00:00"
    assert w.client.patch(f"/clips/{starred}", json={"exported": True}).json()["exported_at"]
    assert w.db.get_clip(starred)["first_exported_at"] == ""
    assert w.client.patch(f"/clips/{starred}", json={"exported": False}).json()["exported_at"] == ""
    _, run = w.remake()
    assert run["status"] == "done"
    assert exported in w.ids() and starred not in w.ids(), "a star taken back exported nothing"


def test_a_rerender_carries_the_export_mark_to_the_new_row(world):
    w = world
    clip_id = w.clip(100.0, 160.0)
    w.db.mark_exported(clip_id)
    first = w.db.get_clip(clip_id)["first_exported_at"]
    w.restore_source()
    r = w.client.post(f"/clips/{clip_id}/render", json={"start": 95.0})
    assert w.drain(r.json()["job_id"])["status"] == "done"
    assert w.at(95.0, 160.0)["first_exported_at"] == first


# ---- a render before a YouTube publish ---------------------------------------------------------


def test_a_render_before_a_youtube_publish_marks_its_clip(tmp_path, monkeypatch):
    pytest.importorskip("fastapi")
    pytest.importorskip("httpx")
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from server import youtube_api, youtube_service

    data = tmp_path / "data"
    db = StateDB(data / "state.db")
    db.upsert_video(VID, title="A stream")
    clip_id = db.add_clip(VID, 10.0, 40.0, 70, "hook", status="queued", title="t")
    monkeypatch.setattr(youtube_service, "is_enabled", lambda _d: True)
    app = FastAPI()
    worker = types.SimpleNamespace(notify=lambda: None)
    youtube_api.install(app, config={}, db=lambda: StateDB(data / "state.db"), data_dir=data,
                        worker=worker, publish_worker=worker)
    r = TestClient(app, base_url="http://127.0.0.1").post(
        f"/clips/{clip_id}/publish", json={"title": "Mine", "render_first": {"render_opts": {"edit": CUTS}}})
    assert r.status_code == 200, r.text
    assert db.get_clip(clip_id)["edited_at"]
    (job,) = db.conn.execute("SELECT payload FROM jobs WHERE type = 'render'").fetchall()
    assert json.loads(job[0])["clip_id"] == clip_id
    db.close()
