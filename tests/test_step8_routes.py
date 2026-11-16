"""Editor suite step 8, part A: the routes the new editor calls, through
FastAPI's TestClient on a temporary data folder. Nothing listens on a port;
the editor lane is not started, and the test runs its queue
(EditorLane.run_pending) when it wants a job done. The renders are stood in
for (compose.run_plan writes a small file), so no FFmpeg is needed except
where a test says so.

- GET /projects: the Projects list; a project whose clip was deleted is
  labelled and still opens.
- POST /projects with `media`: a project made by hand from a library video
  or an imported file in one call, the whole video on V1, framed fit and
  centred.
- A clip whose file is a project export: today's editor's Apply and chat
  edit ask first (409) instead of rendering the old window over it.
- GET /projects/{id}/exports says whether each file is still there; an
  export a later one replaced is marked "replaced"; an export's row names
  its file before the file moves there.
- Stills: one at a time per project, a newer request stops an older one.
- A queued waveform job survives "Clear editor cache"; deleting a video
  stops its editor tasks.
- CORS lets the dev server read the still's two headers; the packaged
  window's requests (no Origin, measured with Electron 31) are let in.
"""

from __future__ import annotations

import threading
from pathlib import Path

import pytest

from core.state import StateDB

NOW = "2026-11-14T09:00:00"


class Env:
    def __init__(self, client, app, db, session, data_dir):
        self.client, self.app, self.db, self.data_dir = client, app, db, data_dir
        self.ok = {"X-Clips-Kitty-Session": session}
        self.lane = app.state.editor_lane

    def post(self, path, body=None, **headers):
        return self.client.post(path, json=body if body is not None else {}, headers={**self.ok, **headers})

    def get(self, path, **headers):
        return self.client.get(path, headers={**self.ok, **headers})

    def ops(self, project_id, revision, *op_list):
        r = self.post(f"/projects/{project_id}/ops", {"expected_revision": revision, "ops": list(op_list)})
        assert r.status_code == 200, r.text
        return r.json()

    def clip(self, path: Path, start=1.0, end=4.0) -> int:
        cur = self.db.conn.execute(
            "INSERT INTO clips (video_id, start_s, end_s, score, hook, path, title, render_opts, created_at)"
            " VALUES ('vid1', ?, ?, 80, 'h', ?, 'Clip one', '', ?)", (start, end, str(path), NOW))
        self.db.conn.commit()
        return cur.lastrowid

    def clip_row(self, clip_id):
        return self.db.conn.execute("SELECT * FROM clips WHERE id = ?", (clip_id,)).fetchone()


@pytest.fixture
def env(tmp_path, monkeypatch):
    pytest.importorskip("fastapi")
    pytest.importorskip("httpx")
    pytest.importorskip("yaml")
    from fastapi.testclient import TestClient

    from main import BUNDLED_CONFIG, load_config
    from plugins import session
    from server.api import create_app
    from video import encoding

    monkeypatch.delenv("CLIPS_KITTY_SESSION_SECRET", raising=False)
    monkeypatch.setattr(encoding, "_mode", "auto")
    monkeypatch.setattr(encoding, "encoder_for",
                        lambda codec, pref="auto": encoding.Encoder(codec, "cpu", "libx264")
                        if codec == "h264" and pref != "gpu" else None)
    monkeypatch.setattr(encoding, "_selected", ("cpu", encoding.CPU_ARGS))
    data_dir = tmp_path / "data"
    config = load_config(BUNDLED_CONFIG)
    config["paths"]["data_dir"] = str(data_dir)
    config["clips"]["outro"] = False
    app = create_app(config, tmp_path / "settings.yaml")
    db = StateDB(data_dir / "state.db")
    db.conn.execute("INSERT INTO videos (video_id, title, status, duration, created_at, updated_at)"
                    " VALUES ('vid1', 'Friday stream', 'done', 8, ?, ?)", (NOW, NOW))
    db.conn.commit()
    downloads = data_dir / "downloads"
    downloads.mkdir(parents=True, exist_ok=True)
    (downloads / "vid1.mp4").write_bytes(b"not read: the renders are stood in for")
    client = TestClient(app, base_url="http://127.0.0.1")
    yield Env(client, app, db, session.path(data_dir).read_text(encoding="utf-8").strip(), data_dir)
    db.conn.close()


@pytest.fixture
def no_render(monkeypatch):
    """compose.run_plan writes a small file where the plan's result goes;
    FFmpeg's version is not asked."""
    from video_editor import compose

    calls = []

    def run(plan, task, *, config=None):
        calls.append(plan)
        task.check()
        result = Path(plan.result)
        result.parent.mkdir(parents=True, exist_ok=True)
        result.write_bytes(b"made by " + plan.purpose.encode())
        return {"path": str(result), "bytes": result.stat().st_size, "retried": False, "fell_back_to_cpu": False}

    monkeypatch.setattr(compose, "run_plan", run)
    monkeypatch.setattr(compose, "ffmpeg_version", lambda binary: (8, 1))
    return calls


def clip_file(env, name="clip_00001-00004.mp4") -> Path:
    folder = env.data_dir / "clips" / "unknown-channel" / "Friday stream [vid1]"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / name
    path.write_bytes(b"the clip as it was")
    return path


def clip_project(env) -> tuple[int, dict]:
    clip_id = env.clip(clip_file(env))
    r = env.post(f"/clips/{clip_id}/project")
    assert r.status_code == 200, r.text
    return clip_id, r.json()


def changed(env, project: dict) -> int:
    """A change today's render can't make (the canvas): Branch B. The new revision."""
    out = env.ops(project["id"], project["revision"], {"op": "set_canvas", "preset": "1:1"})
    return out["revision"]


def export_now(env, project_id: str) -> dict:
    r = env.post(f"/projects/{project_id}/export", {})
    assert r.status_code == 200, r.text
    assert r.json()["branch"] == "B"
    env.lane.run_pending()
    return r.json()


def exports(env, project_id: str) -> list[dict]:
    r = env.get(f"/projects/{project_id}/exports")
    assert r.status_code == 200, r.text
    return r.json()["exports"]


# ---- the projects list ------------------------------------------------------------------------


def test_the_projects_list_and_a_deleted_clips_project(env):
    assert env.client.get("/projects").status_code == 403
    assert env.get("/projects").json() == {"projects": []}
    clip_id, from_clip = clip_project(env)
    made = env.post("/projects", {"name": "By hand", "canvas": "4:5"}).json()
    listed = env.get("/projects").json()["projects"]
    assert [p["id"] for p in listed] == [made["id"], from_clip["id"]] or \
        {p["id"] for p in listed} == {made["id"], from_clip["id"]}
    hand = next(p for p in listed if p["id"] == made["id"])
    assert hand["origin"] == "manual" and hand["canvas"]["width"] == 1080 and hand["canvas"]["height"] == 1350
    assert hand["duration_us"] == 0 and hand["name"] == "By hand"
    clipped = next(p for p in listed if p["id"] == from_clip["id"])
    assert clipped["origin"] == "clip" and clipped["clip_id"] == clip_id and not clipped["clip_deleted"]
    assert clipped["duration_us"] == 3_000_000

    assert env.client.delete(f"/clips/{clip_id}", headers=env.ok).status_code == 200
    gone = next(p for p in env.get("/projects").json()["projects"] if p["id"] == from_clip["id"])
    assert gone["clip_deleted"] and gone["clip_id"] is None
    assert env.get(f"/projects/{from_clip['id']}").status_code == 200        # it still opens
    assert env.get("/projects?limit=0").status_code == 400


@pytest.mark.parametrize("origin, status", [(None, 200), ("null", 200), ("file://", 200),
                                            ("http://localhost:5173", 200), ("http://example.com", 403)])
def test_the_packaged_windows_requests_are_let_in(env, origin, status):
    """Electron 31's window loaded from a file sends no Origin at all
    (measured, step 8): let in, as `null` and `file://` are."""
    headers = {} if origin is None else {"Origin": origin}
    assert env.get("/projects", **headers).status_code == status


def test_the_dev_server_can_read_the_stills_headers(env):
    r = env.client.get("/health", headers={"Origin": "http://localhost:5173"})
    exposed = r.headers.get("access-control-expose-headers", "").lower()
    assert "x-clips-kitty-still-time" in exposed and "x-clips-kitty-still-note" in exposed


# ---- a project made by hand in one call -----------------------------------------------------------


def test_a_whole_video_project_in_one_call(env):
    r = env.post("/projects", {"canvas": "16:9", "media": "video:vid1"})
    assert r.status_code == 200, r.text
    project = r.json()
    doc = project["document"]
    assert project["created"] and project["revision"] == 0 and not project["can_undo"]
    assert project["video_id"] == "vid1" and doc["name"] == "Friday stream"
    assert (doc["canvas"]["width"], doc["canvas"]["height"]) == (1920, 1080)
    (item,) = next(t for t in doc["tracks"] if t["id"] == "V1")["items"]
    assert item["source_in_us"] == 0 and item["source_out_us"] == 8_000_000
    assert item["framing"] == {"fit": "fit", "align": "center"}
    assert doc["media"][item["media_id"]]["source"] == {"type": "video", "video_id": "vid1"}
    assert "context" not in doc or (doc.get("context") or {}).get("origin") != "clip"

    bare = env.post("/projects", {"name": "Mine", "media": "video:vid1", "insert": False}).json()
    assert bare["document"]["name"] == "Mine"
    assert next(t for t in bare["document"]["tracks"] if t["id"] == "V1")["items"] == []
    assert next(iter(bare["document"]["media"].values()))["source"]["video_id"] == "vid1"


def test_an_imported_file_project_takes_its_frame_rate(env, tmp_path):
    from video_editor.project import store

    source = tmp_path / "holiday.mp4"
    source.write_bytes(b"an imported video")
    row = store.add_asset(env.db.conn, env.data_dir, source, kind="video", name="Holiday",
                          facts={"duration_us": 3_000_000, "width": 1920, "height": 1080,
                                 "fps": {"num": 25, "den": 1}, "has_audio": True})
    r = env.post("/projects", {"canvas": "9:16", "media": f"asset:{row['sha256']}"})
    assert r.status_code == 200, r.text
    doc = r.json()["document"]
    assert doc["canvas"]["fps"] == {"num": 25, "den": 1} and doc["name"] == "Holiday"
    (item,) = next(t for t in doc["tracks"] if t["id"] == "V1")["items"]
    assert item["duration_us"] == 3_000_000
    fixed = env.post("/projects", {"media": f"asset:{row['sha256']}", "fps": 30}).json()["document"]
    assert fixed["canvas"]["fps"] == {"num": 30, "den": 1}


@pytest.mark.parametrize("media", ["file:C:/x.mp4", "video:", "clip:1", "video:nope", "asset:" + "0" * 64])
def test_a_project_from_media_that_isnt_there_is_refused(env, media):
    r = env.post("/projects", {"media": media})
    assert r.status_code == 400
    assert env.get("/projects").json()["projects"] == []


# ---- a clip whose file is a project export --------------------------------------------------------


def test_todays_editor_asks_before_rendering_over_a_project_export(env, no_render):
    clip_id, project = clip_project(env)
    plain = env.post(f"/clips/{clip_id}/render", {})
    assert plain.status_code == 200                       # a clip with no export renders as before
    changed(env, project)
    export_now(env, project["id"])
    row = env.clip_row(clip_id)
    assert Path(row["path"]).parent == env.data_dir / "clips" / "projects"
    listed = next(p for p in env.get("/projects").json()["projects"] if p["id"] == project["id"])
    assert listed["clip_file_is_export"]

    r = env.post(f"/clips/{clip_id}/render", {"start": 1.0, "end": 4.0})
    assert r.status_code == 409
    detail = r.json()["detail"]
    assert detail["reason"] == "project_export" and detail["project_id"] == project["id"]
    assert "new editor" in detail["message"]
    r = env.post(f"/clips/{clip_id}/ai-edit", {"message": "make it shorter"})
    assert r.status_code == 409 and r.json()["detail"]["reason"] == "project_export"
    r = env.post(f"/clips/{clip_id}/render", {"start": 1.0, "end": 4.0, "replace_project_edits": True})
    assert r.status_code == 200 and r.json()["job_id"]


def render_jobs(env) -> int:
    return env.db.conn.execute("SELECT COUNT(*) FROM jobs WHERE type = 'render'").fetchone()[0]


def test_a_deleted_projects_export_is_still_asked_about(env, no_render, monkeypatch):
    """8-D9: the project whose export is the clip's file is deleted from the
    Projects list (its project_exports rows go with it). Every route that
    renders the clip from its own settings still asks, with no project to
    open (project_id null); "Apply anyway" works as before. Make clips
    again still keeps the clip, and Storage never offers the export."""
    from core import housekeeping, remake
    from server import youtube_service

    monkeypatch.setattr(youtube_service, "is_enabled", lambda _d: True)
    clip_id, project = clip_project(env)
    changed(env, project)
    export_now(env, project["id"])
    exported = Path(env.clip_row(clip_id)["path"])
    assert exported.parent == env.data_dir / "clips" / "projects" and exported.is_file()
    r = env.client.delete(f"/projects/{project['id']}", headers=env.ok)
    assert r.status_code == 200, r.text
    assert env.db.conn.execute("SELECT COUNT(*) FROM project_exports").fetchone()[0] == 0
    before = render_jobs(env)

    r = env.post(f"/clips/{clip_id}/render", {"start": 1.0, "end": 4.0})
    assert r.status_code == 409, r.text
    detail = r.json()["detail"]
    assert detail["reason"] == "project_export" and detail["project_id"] is None
    assert "deleted" in detail["message"] and "Open it in the new editor" not in detail["message"]
    r = env.post(f"/clips/{clip_id}/ai-edit", {"message": "make it shorter"})
    assert r.status_code == 409 and r.json()["detail"]["project_id"] is None
    lines = {"lines": [{"start": 0, "end": 1, "text": "hi"}]}
    r = env.client.put(f"/clips/{clip_id}/captions", json=lines, headers=env.ok)
    assert r.status_code == 409 and r.json()["detail"]["reason"] == "project_export"
    publish = {"title": "Mine", "render_first": {"render_opts": {"caption_style": {}}}}
    r = env.post(f"/clips/{clip_id}/publish", publish)
    assert r.status_code == 409 and r.json()["detail"]["reason"] == "project_export"
    assert render_jobs(env) == before and exported.is_file()      # nothing queued, the export stays

    row = env.clip_row(clip_id)
    assert "edited" in remake._reasons(row, remake._evidence(env.db, "vid1", env.data_dir), set())
    # Storage's survey: the export is the clip's file, and its folder is
    # never swept, so no group offers it.
    groups = housekeeping.survey(env.db, env.data_dir)
    offered = [Path(f).resolve() for files in groups.values() if isinstance(files, list) for f in files
               if isinstance(f, (str, Path))]
    assert exported.resolve() not in offered

    r = env.post(f"/clips/{clip_id}/render", {"start": 1.0, "end": 4.0, "replace_project_edits": True})
    assert r.status_code == 200 and r.json()["job_id"]


def test_a_render_job_never_goes_over_an_export_it_was_not_asked_about(env, no_render):
    """8-D9: a render job queued before the export landed, or retried after
    it, never asked. The worker asks again under the clip's lock and the job
    fails with a plain sentence; one the person chose "Apply anyway" for
    carries replace_project_edits and goes ahead."""
    import inspect
    import json

    from server import jobs

    clip_id, project = clip_project(env)
    queued_before = env.post(f"/clips/{clip_id}/render", {"start": 1.0, "end": 4.0})
    assert queued_before.status_code == 200
    payload = json.loads(env.db.conn.execute("SELECT payload FROM jobs WHERE id = ?",
                                             (queued_before.json()["job_id"],)).fetchone()[0])
    assert "replace_project_edits" not in payload             # a plain Apply's job is as before
    changed(env, project)
    export_now(env, project["id"])
    exported = Path(env.clip_row(clip_id)["path"])
    with pytest.raises(ValueError, match="nothing was applied"):
        jobs.Worker._rerender_clip(None, env.db, payload, keep_project_export=True)
    env.client.delete(f"/projects/{project['id']}", headers=env.ok)
    with pytest.raises(ValueError, match="nothing was applied"):
        jobs.Worker._rerender_clip(None, env.db, payload, keep_project_export=True)
    assert exported.is_file() and env.clip_row(clip_id)["path"] == str(exported)
    # The queue's render jobs ask; Branch A and others calling it directly don't.
    assert 'keep_project_export=not payload.get("replace_project_edits")' in inspect.getsource(jobs.Worker.run)

    chosen = env.post(f"/clips/{clip_id}/render", {"start": 1.0, "end": 4.0, "replace_project_edits": True})
    assert chosen.status_code == 200
    payload = json.loads(env.db.conn.execute("SELECT payload FROM jobs WHERE id = ?",
                                             (chosen.json()["job_id"],)).fetchone()[0])
    assert payload["replace_project_edits"] is True


def test_deleting_a_project_cancels_its_waiting_export(env, no_render):
    """8-D9: a project deleted while its export waits on the lane: the
    export is cancelled, the clip and its file stay as they were, and
    nothing is left in clips/projects/."""
    clip_id, project = clip_project(env)
    changed(env, project)
    r = env.post(f"/projects/{project['id']}/export", {})
    assert r.status_code == 200 and r.json()["branch"] == "B", r.text
    old = env.clip_row(clip_id)["path"]
    d = env.client.delete(f"/projects/{project['id']}", headers=env.ok)
    assert d.status_code == 200, d.text
    assert env.lane.get(r.json()["export"]["export_id"])["status"] == "cancelled"
    env.lane.run_pending()
    assert env.clip_row(clip_id)["path"] == old and Path(old).read_bytes() == b"the clip as it was"
    folder = env.data_dir / "clips" / "projects"
    assert not folder.exists() or not any(folder.iterdir())
    assert env.post(f"/clips/{clip_id}/render", {"start": 1.0, "end": 4.0}).status_code == 200


def test_deleting_a_project_cancels_its_running_export(env, monkeypatch):
    """8-D9: deleted while its export runs: the delete stops it first, and
    the clip stays as it was."""
    from video_editor import compose

    started, stop = threading.Event(), threading.Event()

    def run(plan, task, *, config=None):
        started.set()
        while not stop.wait(0.01):
            task.check()            # raises once the delete cancels it
        raise AssertionError("the export was not cancelled")

    monkeypatch.setattr(compose, "run_plan", run)
    monkeypatch.setattr(compose, "ffmpeg_version", lambda binary: (8, 1))
    clip_id, project = clip_project(env)
    changed(env, project)
    r = env.post(f"/projects/{project['id']}/export", {})
    assert r.status_code == 200 and r.json()["branch"] == "B", r.text
    old = env.clip_row(clip_id)["path"]
    worker = threading.Thread(target=env.lane.run_pending)
    worker.start()
    try:
        assert started.wait(30)
        d = env.client.delete(f"/projects/{project['id']}", headers=env.ok)
        assert d.status_code == 200, d.text
    finally:
        stop.set()
        worker.join(30)
    assert env.lane.get(r.json()["export"]["export_id"])["status"] == "cancelled"
    assert env.clip_row(clip_id)["path"] == old and Path(old).read_bytes() == b"the clip as it was"
    assert env.post(f"/clips/{clip_id}/render", {"start": 1.0, "end": 4.0}).status_code == 200


def test_apply_edits_and_upload_and_the_caption_save_ask_first_too(env, no_render, monkeypatch):
    """8-D1, 8-D2: every route that renders the clip from its own settings
    asks first: "Apply edits & upload" (YouTube's render_first) and PUT
    /clips/{id}/captions, as Apply and the chat edit do."""
    from server import youtube_service

    monkeypatch.setattr(youtube_service, "is_enabled", lambda _d: True)
    clip_id, project = clip_project(env)
    changed(env, project)
    export_now(env, project["id"])
    before = render_jobs(env)

    publish = {"title": "Mine", "render_first": {"render_opts": {"caption_style": {}}}}
    r = env.post(f"/clips/{clip_id}/publish", publish)
    assert r.status_code == 409, r.text
    assert r.json()["detail"]["reason"] == "project_export"
    lines = {"lines": [{"start": 0, "end": 1, "text": "hi"}]}
    r = env.client.put(f"/clips/{clip_id}/captions", json=lines, headers=env.ok)
    assert r.status_code == 409 and r.json()["detail"]["reason"] == "project_export"
    assert render_jobs(env) == before                    # nothing was queued

    r = env.client.put(f"/clips/{clip_id}/captions", json={**lines, "replace_project_edits": True},
                       headers=env.ok)
    assert r.status_code == 200 and r.json()["job_id"]
    publish["render_first"]["replace_project_edits"] = True
    r = env.post(f"/clips/{clip_id}/publish", publish)
    assert r.status_code == 200, r.text
    assert render_jobs(env) == before + 2


def test_a_clip_with_no_project_export_publishes_and_saves_captions_as_before(env, no_render, monkeypatch):
    from server import youtube_service

    monkeypatch.setattr(youtube_service, "is_enabled", lambda _d: True)
    clip_id = env.clip(clip_file(env))
    r = env.post(f"/clips/{clip_id}/publish", {"title": "Mine", "render_first": {"end": 3.0}})
    assert r.status_code == 200, r.text
    r = env.client.put(f"/clips/{clip_id}/captions", json={"lines": []}, headers=env.ok)
    assert r.status_code == 200


def v1_window(project: dict) -> tuple[int, int]:
    items = next(t for t in project["document"]["tracks"] if t["id"] == "V1")["items"]
    return items[0]["source_in_us"], items[-1]["source_out_us"]


def test_a_clip_changed_in_todays_editor_after_its_project_was_made(env, no_render):
    """8-D6: the project of a clip changed since in today's editor never
    quietly puts the clip back as it was."""
    clip_id, project = clip_project(env)
    assert project["clip_changed_since"] is False and v1_window(project) == (1_000_000, 4_000_000)

    # Today's Apply moved the end (as its render saves the clip). The
    # project was never changed: it follows the clip by itself, one step
    # Undo takes back.
    env.db.set_clip(clip_id, end_s=2.5)
    env.db.conn.commit()
    again = env.post(f"/clips/{clip_id}/project").json()
    assert again["id"] == project["id"] and again["started_again"] and not again["clip_changed_since"]
    assert v1_window(again) == (1_000_000, 2_500_000) and again["can_undo"]
    same = env.post(f"/clips/{clip_id}/project").json()
    assert not same["started_again"] and same["revision"] == again["revision"]

    # Now the project has a change of its own, and the clip changes again:
    # the project opens as it is, and says so.
    changed(env, again)
    env.db.set_clip(clip_id, end_s=3.0)
    env.db.conn.commit()
    kept = env.post(f"/clips/{clip_id}/project").json()
    assert kept["clip_changed_since"] and not kept["started_again"]
    assert v1_window(kept) == (1_000_000, 2_500_000)

    # Exporting it asks first.
    r = env.post(f"/projects/{project['id']}/export", {})
    assert r.status_code == 409 and r.json()["detail"]["reason"] == "clip_changed"
    assert exports(env, project["id"]) == []

    # "Start again from the clip as it is now".
    r = env.post(f"/clips/{clip_id}/project", {"start_again": True})
    assert r.status_code == 400                                   # needs the revision
    r = env.post(f"/clips/{clip_id}/project", {"start_again": True, "expected_revision": 0})
    assert r.status_code == 409                                   # a stale one changes nothing
    fresh = env.post(f"/clips/{clip_id}/project",
                     {"start_again": True, "expected_revision": kept["revision"]}).json()
    assert v1_window(fresh) == (1_000_000, 3_000_000) and not fresh["clip_changed_since"]
    undone = env.post(f"/projects/{project['id']}/undo", {"expected_revision": fresh["revision"]}).json()
    assert v1_window(undone) == (1_000_000, 2_500_000)            # Undo brings the project back
    redone = env.post(f"/projects/{project['id']}/redo", {"expected_revision": undone["revision"]}).json()
    assert env.post(f"/projects/{project['id']}/export", {"revision": redone["revision"]}).status_code == 200


def test_keeping_the_project_over_a_changed_clip_is_the_persons_choice(env, no_render):
    clip_id, project = clip_project(env)
    changed(env, project)
    env.db.set_clip(clip_id, end_s=2.0)
    env.db.conn.commit()
    assert env.post(f"/projects/{project['id']}/export", {}).status_code == 409
    r = env.post(f"/projects/{project['id']}/export", {"replace_clip_changes": True})
    assert r.status_code == 200, r.text
    env.lane.run_pending()
    assert Path(env.clip_row(clip_id)["path"]).parent == env.data_dir / "clips" / "projects"
    # Exported over the clip as it is now: asked once, not again. Renaming
    # the clip is no change to what the project shows.
    env.db.set_clip(clip_id, title="A new name")
    env.db.conn.commit()
    again = env.post(f"/clips/{clip_id}/project").json()
    assert not again["clip_changed_since"] and not again["started_again"]
    assert env.post(f"/projects/{project['id']}/export", {}).status_code == 200
    # Apply anyway in today's editor after that: the project has changes of
    # its own, so it is never started again by itself; it asks.
    env.lane.run_pending()
    env.db.set_clip(clip_id, end_s=3.0)
    env.db.conn.commit()
    asked = env.post(f"/clips/{clip_id}/project").json()
    assert asked["clip_changed_since"] and not asked["started_again"]


@pytest.fixture
def stub_rerender(monkeypatch):
    """Today's re-render (Branch A's encode) stood in for: it saves the
    window and options it was given on the clip's row, as the real one
    does, and leaves the clip's file where it is."""
    import json

    from server.jobs import Worker

    calls = []

    def rerender(owner, d, payload, before_register=None, while_waiting=None):
        calls.append(payload)
        d.set_clip(int(payload["clip_id"]), start_s=round(payload["start"], 2), end_s=round(payload["end"], 2),
                   render_opts=json.dumps(payload["render_opts"], sort_keys=True))
        d.conn.commit()
        return int(payload["clip_id"])

    monkeypatch.setattr(Worker, "_rerender_clip", rerender)
    return calls


def branch_a_export(env, project: dict) -> tuple[dict, int]:
    """A change today's render can make (a colour filter), then an export
    of that revision through Branch A, queued and not yet run."""
    item = next(t for t in project["document"]["tracks"] if t["id"] == "V1")["items"][0]
    out = env.ops(project["id"], project["revision"],
                  {"op": "set_item_props", "item_id": item["id"], "color": {"filter": "bw", "adjust": {}}})
    r = env.post(f"/projects/{project['id']}/export", {"revision": out["revision"], "preset": "original"})
    assert r.status_code == 200, r.text
    assert r.json()["branch"] == "A", r.json()
    return out, r.json()["export"]["export_id"]


def test_edits_made_during_a_branch_a_export_are_never_dropped(env, no_render, stub_rerender):
    """8-D12: the export records the revision it rendered, not the head when
    it ended. A cut made while the export waited is a change of the
    project's own, so after today's Apply the person is asked; the project
    is never started again by itself."""
    clip_id, project = clip_project(env)
    out, export_id = branch_a_export(env, project)
    item = next(t for t in out["document"]["tracks"] if t["id"] == "V1")["items"][0]
    # While the export waits, the person cuts off the first second.
    out = env.ops(project["id"], out["revision"], {"op": "split_item", "item_id": item["id"], "at_us": 1_000_000})
    first = next(t for t in out["document"]["tracks"] if t["id"] == "V1")["items"][0]["id"]
    out = env.ops(project["id"], out["revision"], {"op": "delete_items", "item_ids": [first]})
    mine = v1_window(out)
    env.lane.run_pending()
    assert env.lane.get(export_id)["status"] == "done" and stub_rerender
    # Today's Apply: a new end.
    env.db.set_clip(clip_id, end_s=3.5)
    env.db.conn.commit()
    again = env.post(f"/clips/{clip_id}/project").json()
    assert again["clip_changed_since"] and not again["started_again"]
    assert again["revision"] == out["revision"] and v1_window(again) == mine


def test_an_unchanged_branch_a_export_still_follows_the_clip_and_keeps_its_name(env, no_render, stub_rerender):
    """With no edit after the exported revision, today's Apply still
    starts the project again by itself (the editor says so), and the
    project's own name, which is not part of the clip, stays."""
    clip_id, project = clip_project(env)
    named = env.ops(project["id"], project["revision"], {"op": "set_project_props", "name": "My best moment"})
    out, export_id = branch_a_export(env, {**project, "revision": named["revision"], "document": named["document"]})
    env.lane.run_pending()
    assert env.lane.get(export_id)["status"] == "done"
    env.db.set_clip(clip_id, end_s=3.5)
    env.db.conn.commit()
    again = env.post(f"/clips/{clip_id}/project").json()
    assert again["started_again"] and not again["clip_changed_since"]
    assert again["document"]["name"] == "My best moment" and again["name"] == "My best moment"
    assert v1_window(again)[1] == 3_500_000
    # The person's own "Start again" keeps the name too.
    changed(env, again)
    env.db.set_clip(clip_id, end_s=3.0)
    env.db.conn.commit()
    kept = env.post(f"/clips/{clip_id}/project").json()
    assert kept["clip_changed_since"]
    fresh = env.post(f"/clips/{clip_id}/project",
                     {"start_again": True, "expected_revision": kept["revision"]}).json()
    assert fresh["started_again"] and fresh["document"]["name"] == "My best moment"


def test_a_manual_project_never_asks_about_a_clip(env, no_render):
    project = env.post("/projects", {"canvas": "9:16", "media": "video:vid1"}).json()
    assert env.post(f"/projects/{project['id']}/export", {}).status_code == 200


# ---- export rows ------------------------------------------------------------------------------


def test_an_export_whose_file_was_written_again_in_place_is_not_offered(env, no_render):
    """8-D4: a done row whose path now holds another file (today's Apply
    writes a clip's file in place, and that is a Branch A export's file)."""
    project = env.post("/projects", {"canvas": "9:16", "media": "video:vid1"}).json()
    export_now(env, project["id"])
    (row,) = exports(env, project["id"])
    assert row["status"] == "done" and row["url"]
    Path(row["path"]).write_bytes(b"another render, written over the same name")
    (row,) = exports(env, project["id"])
    assert row["status"] == "replaced" and row["url"] is None
    assert env.get(f"/projects/{project['id']}/exports/{row['id']}/file").status_code == 404


def test_replaced_exports_are_found_by_the_file_not_the_text_of_its_path(env):
    from video_editor.project import store

    project = env.post("/projects", {"canvas": "9:16"}).json()
    folder = env.data_dir / "clips" / "projects"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / "a.mp4"
    export_id = store.add_export(env.db.conn, project["id"], 0, path=str(path), status="done")
    assert store.mark_replaced(env.db.conn, [str(folder / "." / "a.mp4")]) == 1
    assert store.exports(env.db.conn, project["id"])[0]["status"] == "replaced"
    assert store.same_path(str(path), str(folder / "x" / ".." / "a.mp4"))
    assert not store.same_path(str(path), str(folder / "b.mp4"))
    assert export_id


def test_stopping_an_export_needs_the_apps_own_window(env):
    """8-D9: POST /export/jobs/{id}/cancel asks for the session and an
    Origin of this PC's own, as the project routes do."""
    assert env.client.post("/export/jobs/999/cancel").status_code == 403
    r = env.post("/export/jobs/999/cancel", Origin="http://example.com")
    assert r.status_code == 403
    assert env.post("/export/jobs/999/cancel").status_code == 404           # the app's own: no such job
    assert env.post("/export/jobs/999/cancel", Origin="null").status_code == 404


def test_an_export_replaced_by_a_later_one_is_never_offered(env, no_render):
    clip_id, project = clip_project(env)
    changed(env, project)
    first = export_now(env, project["id"])
    (row,) = exports(env, project["id"])
    assert row["status"] == "done" and row["file_exists"]
    first_path = Path(row["path"])
    assert Path(env.clip_row(clip_id)["path"]) == first_path

    export_now(env, project["id"])
    rows = {r["id"]: r for r in exports(env, project["id"])}
    old = rows[first["project_export_id"]]
    assert old["status"] == "replaced" and not old["file_exists"] and not first_path.exists()
    newest = max(rows.values(), key=lambda r: r["id"])
    assert newest["status"] == "done" and newest["file_exists"]
    assert Path(env.clip_row(clip_id)["path"]) == Path(newest["path"])


def _day_of(name: str) -> str:
    import re

    found = re.search(r" - (\d{4}-\d{2}-\d{2})(?:-\d+)?\.mp4$", name)
    assert found, name
    return found.group(1)


def test_a_project_with_no_clip_exports_to_the_persons_folder(env, no_render, tmp_path):
    """Step 8's UX fix: a project made by hand goes to the export folder
    from Settings, named after the project and the day (the day read from
    the answer, never the clock); it plays and is listed as before."""
    from server.compose_api import export_stem

    mine = tmp_path / "My exports"
    project = env.post("/projects", {"canvas": "9:16", "media": "video:vid1"}).json()
    r = env.post(f"/projects/{project['id']}/export", {"folder": str(mine)})
    assert r.status_code == 200, r.text
    env.lane.run_pending()
    (row,) = exports(env, project["id"])
    path = Path(row["path"])
    assert path.parent == mine and path.is_file()
    assert path.name == export_stem(project["document"]["name"], _day_of(path.name)) + ".mp4"
    assert row["status"] == "done" and row["file_exists"] and row["url"]
    assert env.client.get(row["url"]).content == path.read_bytes()
    job = env.get(f"/export/jobs/{r.json()['export']['export_id']}").json()
    assert Path(job["result"]["folder"]) == mine and job["result"]["file"] == path.name
    # A second export the same day is a copy beside it, never over it.
    env.post(f"/projects/{project['id']}/export", {"folder": str(mine)})
    env.lane.run_pending()
    names = sorted(Path(x["path"]).name for x in exports(env, project["id"]))
    assert len(set(names)) == 2 and all(Path(mine / n).is_file() for n in names)
    folder = env.data_dir / "clips" / "projects"
    assert not folder.exists() or not any(folder.iterdir())


@pytest.mark.parametrize("folder", [None, "", "relative/exports"])
def test_with_no_usable_folder_a_project_export_stays_where_it_was(env, no_render, folder):
    project = env.post("/projects", {"canvas": "9:16", "media": "video:vid1"}).json()
    body = {} if folder is None else {"folder": folder}
    assert env.post(f"/projects/{project['id']}/export", body).status_code == 200
    env.lane.run_pending()
    (row,) = exports(env, project["id"])
    path = Path(row["path"])
    assert path.parent == env.data_dir / "clips" / "projects"
    assert path.name == f"{project['id']}-r{project['revision']}.mp4"


def test_a_clips_project_ignores_the_folder_and_replaces_the_clip(env, no_render, tmp_path):
    """ED28 is kept: a clip's project replaces the clip's file in the app's
    own folder, whatever folder is sent."""
    mine = tmp_path / "My exports"
    clip_id, project = clip_project(env)
    revision = changed(env, project)
    assert env.post(f"/projects/{project['id']}/export", {"folder": str(mine)}).status_code == 200
    env.lane.run_pending()
    (row,) = exports(env, project["id"])
    path = Path(row["path"])
    assert path.parent == env.data_dir / "clips" / "projects" and path.name == f"{project['id']}-r{revision}.mp4"
    assert Path(env.clip_row(clip_id)["path"]) == path
    assert not mine.exists()


def test_a_project_whose_clip_was_deleted_exports_to_the_persons_folder(env, no_render, tmp_path):
    mine = tmp_path / "My exports"
    clip_id, project = clip_project(env)
    assert env.client.delete(f"/clips/{clip_id}", headers=env.ok).status_code == 200
    assert env.post(f"/projects/{project['id']}/export", {"folder": str(mine)}).status_code == 200
    env.lane.run_pending()
    (row,) = exports(env, project["id"])
    path = Path(row["path"])
    assert path.parent == mine and path.name.startswith(project["document"]["name"]) and row["file_exists"]


def test_export_file_names_are_safe_on_windows():
    pytest.importorskip("fastapi")
    from server.compose_api import export_stem

    day = "2026-11-15"
    assert export_stem("own_clip_45s", day) == "own_clip_45s - 2026-11-15"
    assert export_stem("own_clip_45s.mp4", day) == "own_clip_45s - 2026-11-15"     # an imported file's name
    assert export_stem("Trip.MOV", day) == "Trip - 2026-11-15"
    assert export_stem("notes.mp4.txt", day) == "notes.mp4.txt - 2026-11-15"
    assert export_stem('a<b>c:d"e/f\\g|h?i*j', day) == "abcdefghij - 2026-11-15"
    assert export_stem("  ends with dots...  ", day) == "ends with dots - 2026-11-15"
    assert export_stem("", day) == "Project - 2026-11-15"
    assert export_stem("???", day) == "Project - 2026-11-15"
    assert export_stem("CON", day) == "CON project - 2026-11-15"
    assert export_stem("nul.txt", day) == "nul.txt project - 2026-11-15"
    assert export_stem("tab\tand\nline", day) == "tabandline - 2026-11-15"
    assert len(export_stem("x" * 300, day)) == 80 + len(" - 2026-11-15")


def test_a_gone_file_is_shown_as_gone(env, no_render):
    r = env.post("/projects", {"canvas": "9:16", "media": "video:vid1"})
    project = r.json()
    export_now(env, project["id"])
    (row,) = exports(env, project["id"])
    assert row["file_exists"] and row["clip_id"] is None
    Path(row["path"]).unlink()
    (row,) = exports(env, project["id"])
    assert row["status"] == "done" and not row["file_exists"]


def test_a_finished_export_plays_from_its_row_and_nothing_else_does(env, no_render):
    """Part C: the export dialog plays a finished export through its row's
    `url` (the project's token), never by a path; a row whose file is gone,
    another project's row, or a row not done has no url and no file."""
    project = env.post("/projects", {"canvas": "9:16", "media": "video:vid1"}).json()
    other = env.post("/projects", {"canvas": "16:9", "media": "video:vid1"}).json()
    export_now(env, project["id"])
    (row,) = exports(env, project["id"])
    assert row["url"].startswith(f"/projects/{project['id']}/exports/{row['id']}/file?t=")
    played = env.client.get(row["url"])                     # the token alone, as a <video> asks
    assert played.status_code == 200 and played.content == Path(row["path"]).read_bytes()
    ranged = env.client.get(row["url"], headers={"Range": "bytes=0-3"})
    assert ranged.status_code == 206 and ranged.content == Path(row["path"]).read_bytes()[:4]
    bare = f"/projects/{project['id']}/exports/{row['id']}/file"
    assert env.client.get(bare).status_code == 403             # no token, no header
    assert env.client.get(bare, headers={**env.ok, "Origin": "https://example.com"}).status_code == 403
    assert env.get(f"/projects/{other['id']}/exports/{row['id']}/file").status_code == 404
    assert env.get(f"/projects/{project['id']}/exports/{row['id'] + 99}/file").status_code == 404
    env.db.conn.execute("UPDATE project_exports SET status = 'failed' WHERE id = ?", (row["id"],))
    env.db.conn.commit()
    assert env.get(bare).status_code == 404
    assert exports(env, project["id"])[0]["url"] is None
    env.db.conn.execute("UPDATE project_exports SET status = 'done' WHERE id = ?", (row["id"],))
    env.db.conn.commit()
    Path(row["path"]).unlink()
    assert env.get(bare).status_code == 404
    assert exports(env, project["id"])[0]["url"] is None


def test_an_export_row_naming_a_file_that_is_no_video_is_never_served(env, no_render, tmp_path):
    project = env.post("/projects", {"canvas": "9:16", "media": "video:vid1"}).json()
    export_now(env, project["id"])
    (row,) = exports(env, project["id"])
    secret = tmp_path / "state.db.txt"
    secret.write_text("not a video", encoding="utf-8")
    env.db.conn.execute("UPDATE project_exports SET path = ? WHERE id = ?", (str(secret), row["id"]))
    env.db.conn.commit()
    assert env.get(f"/projects/{project['id']}/exports/{row['id']}/file").status_code == 404


def test_an_exports_row_names_its_file_before_the_file_moves(env, no_render, monkeypatch):
    clip_id, project = clip_project(env)
    changed(env, project)

    def broken(self, clip_id, **fields):
        raise RuntimeError("the app stopped here")

    monkeypatch.setattr(StateDB, "set_clip", broken)
    r = env.post(f"/projects/{project['id']}/export", {})
    env.lane.run_pending()
    (row,) = exports(env, project["id"])
    assert row["status"] == "failed" and row["path"] and Path(row["path"]).is_file()
    assert Path(row["path"]).parent == env.data_dir / "clips" / "projects"
    assert r.json()["project_export_id"] == row["id"]


# ---- stills: one at a time ----------------------------------------------------------------------


def test_a_newer_still_stops_the_one_being_made(env, monkeypatch):
    from video import export_presets
    from video_editor import compose

    monkeypatch.setattr(compose, "ffmpeg_version", lambda binary: (8, 1))
    project = env.post("/projects", {"canvas": "9:16", "media": "video:vid1"}).json()
    slots = env.app.state.still_slots
    seen = []

    def run(plan, task, *, config=None):
        seen.append(plan.purpose)
        if len(seen) == 1:
            slots.ticket(project["id"])          # a newer request arrives while this one runs
            assert task.cancelled                # ... and stops it
            raise export_presets.Cancelled()
        result = Path(plan.result)
        result.parent.mkdir(parents=True, exist_ok=True)
        result.write_bytes(b"\xff\xd8\xff\xd9")
        return {"path": str(result), "bytes": 4, "retried": False, "fell_back_to_cpu": False}

    monkeypatch.setattr(compose, "run_plan", run)
    url = f"/projects/{project['id']}/still?t_us=1000000"
    r = env.get(url)
    assert r.status_code == 409 and r.json()["detail"]["superseded"]
    r = env.get(url)
    assert r.status_code == 200 and r.content == b"\xff\xd8\xff\xd9"
    assert seen == ["still", "still"]


def test_still_slots_run_one_at_a_time_and_older_waiting_ones_give_up():
    pytest.importorskip("fastapi")
    from server.compose_api import StillSlots

    slots = StillSlots()

    class Task:
        cancelled = False

        def cancel(self):
            self.cancelled = True

    slot, first = slots.ticket("p")
    task = Task()
    slots.running(slot, task)
    _same, second = slots.ticket("p")
    assert task.cancelled and not slots.current(slot, first) and slots.current(slot, second)
    slots.done(slot, task)
    other, ticket = slots.ticket("q")
    assert other is not slot and slots.current(other, ticket)

    running = []
    peak = []

    def request(n):
        s, mine = slots.ticket("r")
        with s["lock"]:
            if not slots.current(s, mine):
                return
            running.append(n)
            peak.append(len(running))
            running.remove(n)

    threads = [threading.Thread(target=request, args=(n,)) for n in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert max(peak) == 1


# ---- editor tasks: Clear while one waits, a deleted video ------------------------------------------


def test_a_waveform_job_queued_before_clear_still_works(env):
    from media_helpers import make_video

    source = env.data_dir / "downloads" / "vid1.mp4"
    source.unlink()
    make_video(source, seconds=2)
    r = env.get("/media/video:vid1/peaks?format=json")
    assert r.status_code == 202 and r.json()["state"] == "queued"
    assert env.post("/media/cache/clear").status_code == 200
    env.lane.run_pending()
    r = env.get("/media/video:vid1/peaks?format=json")
    assert r.status_code == 200, r.text
    assert r.json()["duration_us"] > 1_500_000


def test_deleting_a_video_stops_its_editor_tasks(env):
    from video_editor.media import cache

    folder = cache.root(env.data_dir) / ("e" * 64)
    folder.mkdir(parents=True)
    cache.write_json(folder / cache.SOURCE, {"ref": "video:vid1", "path": "x", "size": 1, "mtime_ns": 1})
    other = cache.root(env.data_dir) / ("f" * 64)
    other.mkdir(parents=True)
    cache.write_json(other / cache.SOURCE, {"ref": "video:vid2", "path": "y", "size": 1, "mtime_ns": 1})
    mine = env.lane.submit_task("peaks", f"peaks.bin:{folder.name}", lambda ctx: {}, name="peaks.bin")
    theirs = env.lane.submit_task("thumbs", f"thumbs:{other.name}", lambda ctx: {}, name="thumbs")
    assert env.client.delete("/videos/vid1", headers=env.ok).status_code == 200
    assert env.lane.get(mine["job_id"], kind=None)["status"] == "cancelled"
    assert env.lane.get(theirs["job_id"], kind=None)["status"] == "queued"


def test_the_route_list_is_documented():
    """Every route the editor calls is in the contract (docs/editor-suite/api/step8-editor.md)."""
    doc = (Path(__file__).resolve().parent.parent / "docs" / "editor-suite" / "api" / "step8-editor.md")
    text = doc.read_text(encoding="utf-8")
    for route in ("GET /projects", "POST /projects", "POST /clips/{clip_id}/project", "GET /projects/{id}",
                  "POST /projects/{id}/ops", "POST /projects/{id}/undo", "POST /projects/{id}/redo",
                  "GET /media/{id}/info", "/media/source/{id}", "GET /media/{id}/peaks",
                  "GET /media/{id}/thumbs", "POST /media/import", "GET /projects/{id}/still",
                  "POST /projects/{id}/draft", "GET /projects/{id}/draft", "POST /projects/{id}/export",
                  "GET /projects/{id}/exports", "GET /projects/{id}/exports/{row}/file",
                  "POST /export/jobs/{export_id}/cancel", "GET /export/presets",
                  "POST /export/estimate"):
        assert route in text, route


# ---- 8-D13: anything made from a clip's own window asks first -----------------------------------


def translate_jobs(env) -> list[dict]:
    import json

    return [json.loads(r[0]) for r in env.db.conn.execute(
        "SELECT payload FROM jobs WHERE type = 'translate' ORDER BY id").fetchall()]


def test_export_in_other_languages_asks_first_for_a_project_export(env, no_render, tmp_path):
    """8-D13: burned subtitles, a dub and subtitle files are made from the
    clip's own window and caption lines. For a clip whose file is a
    new-editor export, POST /translate asks first (409, reason
    project_export, separate_file) and queues nothing; "Make it anyway"
    (without_project_edits) queues the job as before, with the flag. Text
    only (the review stage, the post text) never asks."""
    clip_id, project = clip_project(env)
    changed(env, project)
    export_now(env, project["id"])
    folder = tmp_path / "out"
    base = {"clip_ids": [clip_id], "languages": ["es"], "stage": "export", "folder": str(folder)}
    for asked in ({"burn": True}, {"dub": True}, {"subtitles": True}, {"burn": True, "dub": True}):
        r = env.post("/translate", {**base, **asked})
        assert r.status_code == 409, (asked, r.text)
        detail = r.json()["detail"]
        assert detail["reason"] == "project_export" and detail["separate_file"] is True
        assert detail["clip_ids"] == [clip_id] and detail["count"] == 1 and detail["total"] == 1
        assert detail["project_id"] == project["id"]
        assert "without the new editor's changes" in detail["message"]
    assert translate_jobs(env) == []
    for text_only in ({**base, "post_text": True}, {**base, "stage": "translate", "burn": True}):
        assert env.post("/translate", text_only).status_code == 200
    assert all("without_project_edits" not in p for p in translate_jobs(env))
    r = env.post("/translate", {**base, "burn": True, "without_project_edits": True})
    assert r.status_code == 200, r.text
    assert translate_jobs(env)[-1]["without_project_edits"] is True


def test_a_batch_is_asked_about_once_and_a_plain_clip_never(env, no_render, tmp_path):
    """A batch names every clip it affects, once (count of total); a batch
    of clips the new editor never exported queues exactly the job it did
    before (no new key in its payload). A deleted project's export is still
    asked about, with nothing to open."""
    plain = env.clip(clip_file(env, "clip_00005-00007.mp4"), 5.0, 7.0)
    base = {"languages": ["es"], "stage": "export", "folder": str(tmp_path / "out"), "burn": True}
    r = env.post("/translate", {**base, "clip_ids": [plain]})
    assert r.status_code == 200, r.text
    assert set(translate_jobs(env)[-1]) == {"clip_ids", "languages", "stage", "folder", "include_video", "burn",
                                            "dub", "subtitles", "post_text", "voices", "style"}

    clip_id, project = clip_project(env)
    changed(env, project)
    export_now(env, project["id"])
    r = env.post("/translate", {**base, "clip_ids": [plain, clip_id]})
    assert r.status_code == 409
    detail = r.json()["detail"]
    assert detail["clip_ids"] == [clip_id] and detail["count"] == 1 and detail["total"] == 2
    assert detail["project_id"] == project["id"]

    assert env.client.delete(f"/projects/{project['id']}", headers=env.ok).status_code == 200
    r = env.post("/translate", {**base, "clip_ids": [clip_id, plain]})
    assert r.status_code == 409
    assert r.json()["detail"]["project_id"] is None and r.json()["detail"]["clip_ids"] == [clip_id]


def test_a_translate_job_never_makes_files_it_was_not_asked_about(env, no_render, tmp_path, monkeypatch):
    """A translate job queued before the clip's export landed asks again
    when it runs: it fails with a plain sentence before anything is made,
    and before any AI is set up. One the person chose "Make it anyway" for
    goes on as before."""
    from llm import registry
    from server import jobs

    clip_id, project = clip_project(env)
    folder = tmp_path / "out"
    r = env.post("/translate", {"clip_ids": [clip_id], "languages": ["es"], "stage": "export",
                                "folder": str(folder), "burn": True})
    assert r.status_code == 200
    payload = translate_jobs(env)[-1]
    changed(env, project)
    export_now(env, project["id"])

    class Worker:
        def __init__(self):
            self.config = {"paths": {"data_dir": str(env.data_dir)}, "llm": {"backend": "ollama/none"}}

    def no_ai(_cfg):
        raise AssertionError("no AI is set up for a job that stops")

    monkeypatch.setattr(registry, "create_backend", no_ai)
    with pytest.raises(RuntimeError, match="changed in the new editor"):
        jobs.Worker._translate_clips(Worker(), env.db, payload)
    assert not folder.exists()
    with pytest.raises(AssertionError, match="no AI"):     # past the question: on as before
        jobs.Worker._translate_clips(Worker(), env.db, {**payload, "without_project_edits": True})


def test_a_run_that_makes_the_window_again_keeps_the_export(env, no_render, tmp_path):
    """A forced run (Process anyway, a retry) that finds a clip's window
    again re-points the clip at its fresh render. Over a new-editor export
    it keeps the clip on the export instead (8-D13); every other clip is
    re-pointed exactly as before."""
    from analysis.metadata import ClipMetadata
    from core import pipeline
    from core.models import ClipCandidate

    plain = env.clip(clip_file(env, "clip_00005-00007.mp4"), 5.0, 7.0)
    clip_id, project = clip_project(env)
    changed(env, project)
    export_now(env, project["id"])
    exported = env.clip_row(clip_id)["path"]
    fresh = tmp_path / "fresh.mp4"
    fresh.write_bytes(b"made again")
    meta = ClipMetadata(title="t", description="", hashtags=[])
    for start, end in ((1.0, 4.0), (5.0, 7.0)):
        out = pipeline._register_clip(env.db, "vid1", ClipCandidate(start=start, end=end, score=80), fresh, meta,
                                      "{}", None)
        assert out is None
    assert env.clip_row(clip_id)["path"] == exported and Path(exported).is_file()
    assert env.clip_row(plain)["path"] == str(fresh)
    assert fresh.is_file()      # left for the run's story reels; Storage frees it later


def test_a_story_reel_made_again_keeps_its_export():
    import inspect

    pytest.importorskip("numpy")      # core.pipeline imports the analysis stack
    from core import pipeline

    reels = inspect.getsource(pipeline._sport_reels)
    assert reels.index('row is not None and _keeps_project_export(db, row["id"])') < \
        reels.index('db.set_clip(row["id"], path=str(out)')
    assert pipeline._keeps_project_export(object(), 1) is False
