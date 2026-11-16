"""Editor suite step 7: a project's export (server/compose_api.py), through
FastAPI's TestClient on a temporary data folder. Nothing listens on a port;
the editor lane is not started, and the test runs its queue
(EditorLane.run_pending) when it wants the job done.

- The routes are protected like the project routes: the session header and
  an Origin of this PC's own.
- The branch: today's re-render (A) for a project that still projects to the
  clip's render options with the clip's own preset; the compiler (B) for
  everything else.
- An export is a job with the exports (GET /export/jobs), its row in
  project_exports follows it, and the file lands in data/clips/projects/.
- A clip's file is replaced only once the new one is complete and
  registered. A failed export (the source deleted mid-export) and a
  cancelled one leave the clip, the project and every file as they were.
- Branch A's file is the one Apply makes (the same FFmpeg, in this test).
"""

from __future__ import annotations

import json
import subprocess
import threading
from pathlib import Path

import pytest

from core.state import StateDB

NOW = "2026-11-14T09:00:00"


def _ffmpeg_or_skip() -> str:
    from core.binaries import ffmpeg

    try:
        subprocess.run([ffmpeg(), "-version"], capture_output=True, check=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        pytest.skip("FFmpeg isn't installed")
    return ffmpeg()


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

    def clip(self, start=1.0, end=4.0, render_opts=None, path=None) -> int:
        cur = self.db.conn.execute(
            "INSERT INTO clips (video_id, start_s, end_s, score, hook, path, title, render_opts, created_at)"
            " VALUES ('vid1', ?, ?, 80, 'h', ?, 'Clip one', ?, ?)",
            (start, end, str(path or ""), json.dumps(render_opts) if render_opts is not None else "", NOW))
        self.db.conn.commit()
        return cur.lastrowid

    def project_of(self, clip_id) -> dict:
        r = self.post(f"/clips/{clip_id}/project")
        assert r.status_code == 200, r.text
        return r.json()

    def export(self, project_id, **body):
        return self.post(f"/projects/{project_id}/export", body)

    def row(self, project_export_id) -> dict:
        return dict(self.db.conn.execute("SELECT * FROM project_exports WHERE id = ?",
                                         (project_export_id,)).fetchone())

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
    # What this PC can encode, answered here so nothing is probed: H.264 on
    # the CPU, the renders' encoder the CPU's.
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
    client = TestClient(app, base_url="http://127.0.0.1")
    yield Env(client, app, db, session.path(data_dir).read_text(encoding="utf-8").strip(), data_dir)
    db.conn.close()


@pytest.fixture
def media(env):
    """The video's download (8 s, 640x360, 30 fps, a tone) and its words."""
    binary = _ffmpeg_or_skip()
    downloads = env.data_dir / "downloads"
    downloads.mkdir(parents=True, exist_ok=True)
    source = downloads / "vid1.mp4"
    subprocess.run([binary, "-hide_banner", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=s=640x360:r=30:d=8",
                    "-f", "lavfi", "-i", "sine=f=440:r=48000:d=8", "-c:v", "libx264", "-preset", "ultrafast",
                    "-g", "30", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(source)],
                   check=True, timeout=120)
    transcripts = env.data_dir / "transcripts"
    transcripts.mkdir(parents=True, exist_ok=True)
    words = [{"start": 1.2, "end": 1.6, "word": "hello"}, {"start": 2.0, "end": 2.5, "word": "there"}]
    (transcripts / "vid1.json").write_text(json.dumps({"segments": [
        {"start": 1.2, "end": 2.5, "text": "hello there", "words": words}]}), encoding="utf-8")
    return source


def clip_file(env, name="clip_00001-00004.mp4", data=b"the clip as it was") -> Path:
    folder = env.data_dir / "clips" / "unknown-channel" / "Friday stream [vid1]"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / name
    path.write_bytes(data)
    return path


def manual_project(env) -> tuple[str, int]:
    """An empty 360x640 project with two cuts of the video."""
    r = env.post("/projects", {"name": "Mine", "canvas": {"width": 360, "height": 640}, "fps": 30})
    assert r.status_code == 200, r.text
    p = r.json()
    out = env.ops(p["id"], p["revision"], {"op": "add_media", "video_id": "vid1"})
    out = env.ops(p["id"], out["revision"],
                  {"op": "insert_clip", "media_id": "m1", "source_in_us": 1_000_000, "source_out_us": 2_000_000},
                  {"op": "insert_clip", "media_id": "m1", "source_in_us": 4_000_000, "source_out_us": 5_000_000,
                   "speed": 2.0})
    return p["id"], out["revision"]


def files_under(folder: Path) -> dict:
    return {p.relative_to(folder).as_posix(): p.stat().st_size for p in folder.rglob("*") if p.is_file()}


def partials(folder: Path) -> list[str]:
    return sorted(p.name for p in folder.rglob("*") if ".part" in p.name or ".exporting-" in p.name)


# ---- protection and answers ------------------------------------------------------------------------


def test_the_export_routes_need_the_session_and_an_origin_of_this_pc(env):
    pid, _rev = manual_project_no_media(env)
    for method, path in (("post", f"/projects/{pid}/export"), ("get", f"/projects/{pid}/exports")):
        call = getattr(env.client, method)
        kwargs = {"json": {}} if method == "post" else {}
        assert call(path, **kwargs).status_code == 403
        assert call(path, headers={**env.ok, "Origin": "https://example.com"}, **kwargs).status_code == 403
        assert call(path, headers={**env.ok, "Origin": "http://127.0.0.1:9999"}, **kwargs).status_code == 403


def manual_project_no_media(env) -> tuple[str, int]:
    r = env.post("/projects", {"name": "Empty"})
    assert r.status_code == 200, r.text
    return r.json()["id"], r.json()["revision"]


def test_unknown_project_stale_revision_bad_preset_and_empty_project(env):
    assert env.export("0123456789abcdef").status_code == 404
    pid, rev = manual_project_no_media(env)
    r = env.export(pid, revision=rev + 5)
    assert r.status_code == 409 and r.json()["detail"]["current_revision"] == rev
    assert env.export(pid, preset={"codec": "nope"}).status_code == 400
    assert env.export(pid, preset={"kind": "custom", "codec": "av1"}).status_code == 400
    r = env.export(pid)
    assert r.status_code == 400 and "nothing on its main video track" in r.json()["detail"]
    assert env.get(f"/projects/{pid}/exports").json() == {"exports": []}
    assert env.lane.jobs() == []


def test_a_missing_source_is_refused_before_anything_is_queued(env):
    pid, _rev = manual_project_no_media(env)
    (env.data_dir / "downloads").mkdir(parents=True, exist_ok=True)
    (env.data_dir / "downloads" / "vid1.mp4").write_bytes(b"x")
    out = env.ops(pid, _rev, {"op": "add_media", "video_id": "vid1"})
    out = env.ops(pid, out["revision"], {"op": "insert_clip", "media_id": "m1", "source_in_us": 0,
                                         "source_out_us": 1_000_000})
    (env.data_dir / "downloads" / "vid1.mp4").unlink()
    r = env.export(pid)
    assert r.status_code == 404 and "missing" in r.json()["detail"]
    assert env.lane.jobs() == [] and env.get(f"/projects/{pid}/exports").json()["exports"] == []


def test_the_branch_follows_the_projection_and_the_preset(env):
    """Queued, not run: an unchanged clip project with the clip's own preset
    is Branch A; another preset, a change today's render can't make, or a
    project not made from a clip is Branch B, with the reasons."""
    (env.data_dir / "downloads").mkdir(parents=True, exist_ok=True)
    (env.data_dir / "downloads" / "vid1.mp4").write_bytes(b"x")
    cid = env.clip(render_opts={"crop": "center"}, path=clip_file(env))
    p = env.project_of(cid)
    r = env.export(p["id"])
    assert r.status_code == 200, r.text
    a = r.json()
    assert a["branch"] == "A" and a["export"]["kind"] == "project_export" and a["export"]["branch"] == "A"
    assert a["export"]["clip_id"] == cid and a["export"]["status"] == "queued"
    r = env.export(p["id"], preset={"kind": "custom", "resolution": "720p", "codec": "h264"})
    assert r.json()["branch"] == "B" and r.json()["reasons"]
    out = env.ops(p["id"], p["revision"], {"op": "set_canvas", "preset": "1:1"})
    assert env.export(p["id"]).json()["branch"] == "B"
    rows = env.get(f"/projects/{p['id']}/exports").json()["exports"]
    assert [r["branch"] for r in rows] == ["B", "B", "A"] and {r["status"] for r in rows} == {"queued"}
    assert rows[0]["head"] == out["revision"] and rows[2]["head"] == p["revision"]
    assert [j["export_id"] for j in env.client.get("/export/jobs").json()["jobs"]] == \
        [r["job_id"] for r in rows]


def test_cancelling_a_queued_export_records_it(env):
    (env.data_dir / "downloads").mkdir(parents=True, exist_ok=True)
    (env.data_dir / "downloads" / "vid1.mp4").write_bytes(b"x")
    cid = env.clip(render_opts={"crop": "center"}, path=clip_file(env))
    a = env.export(env.project_of(cid)["id"]).json()
    r = env.client.post(f"/export/jobs/{a['export']['export_id']}/cancel", headers=env.ok)
    assert r.status_code == 200 and r.json()["status"] == "cancelled"
    assert env.row(a["project_export_id"])["status"] == "cancelled"


def test_an_export_the_app_was_closed_in_the_middle_of_reads_interrupted(env):
    pid, rev = manual_project_no_media(env)
    from video_editor.project import store

    eid = store.add_export(env.db.conn, pid, rev, status="running", branch="B", job_id=999)
    assert env.get(f"/projects/{pid}/exports").json()["exports"][0]["status"] == "interrupted"
    assert env.row(eid)["status"] == "interrupted"


# ---- Branch B, made --------------------------------------------------------------------------------


def test_a_manual_project_exports_to_the_projects_folder(env, media):
    pid, rev = manual_project(env)
    r = env.export(pid)
    assert r.status_code == 200 and r.json()["branch"] == "B", r.text
    assert env.lane.run_pending() == 1
    job = env.lane.get(r.json()["export"]["export_id"])
    assert job["status"] == "done", job
    target = env.data_dir / "clips" / "projects" / f"{pid}-r{rev}.mp4"
    assert job["result"]["path"] == str(target) and target.is_file()
    row = env.row(r.json()["project_export_id"])
    assert row["status"] == "done" and row["branch"] == "B" and row["path"] == str(target)
    assert row["bytes"] == target.stat().st_size and abs(row["duration_us"] - 1_500_000) <= 34_000
    assert row["clip_id"] is None and row["job_id"] == job["export_id"]
    assert partials(env.data_dir) == [] and not list((env.data_dir / "editor_lane").glob("job-*"))
    # The same revision again: a name of its own, the first kept.
    env.export(pid)
    env.lane.run_pending()
    assert (env.data_dir / "clips" / "projects" / f"{pid}-r{rev}-2.mp4").is_file() and target.is_file()


def test_storage_clean_up_keeps_a_manual_projects_export(env, media):
    """Fix 7-D11: a manual project's export has no clip row; Storage's
    clean-up took it for an orphan clip and deleted the finished video.
    Nor is it freed once its project (and so its export row) is gone."""
    from core import housekeeping

    pid, _rev = manual_project(env)
    r = env.export(pid)
    env.lane.run_pending()
    exported = Path(env.row(r.json()["project_export_id"])["path"])
    assert exported.is_file()
    stray = clip_file(env, name="clip_00009-00010.mp4")        # a real orphan clip, for contrast
    found = housekeeping.survey(env.db, env.data_dir)
    assert [Path(p) for p in found["_groups"]["orphan_clips"]] == [stray]
    c = env.post("/storage/cleanup")
    assert c.status_code == 200, c.text
    assert exported.is_file() and not stray.exists()
    assert env.client.delete(f"/projects/{pid}", headers=env.ok).status_code == 200
    env.post("/storage/cleanup")
    assert exported.is_file()


def _clip_project_for_b(env) -> tuple[int, Path, dict]:
    """A clip with a file of its own, and its project changed so that only
    the compiler can make it (a 1:1 canvas, framed by the document)."""
    old = clip_file(env)
    cid = env.clip(start=1.0, end=3.0, render_opts={"crop": "center", "captions": False}, path=old)
    p = env.project_of(cid)
    items = p["document"]["tracks"][0]["items"]
    out = env.ops(p["id"], p["revision"], {"op": "set_canvas", "width": 360, "height": 360},
                  *({"op": "set_item_props", "item_id": i["id"], "framing": {"fit": "fill"}} for i in items))
    return cid, old, {**p, "revision": out["revision"]}


def test_a_clip_projects_export_replaces_the_clips_file_once_it_is_made(env, media):
    cid, old, p = _clip_project_for_b(env)
    r = env.export(p["id"])
    assert r.json()["branch"] == "B", r.text
    env.lane.run_pending()
    job = env.lane.get(r.json()["export"]["export_id"])
    assert job["status"] == "done", job
    row = env.clip_row(cid)
    target = env.data_dir / "clips" / "projects" / f"{p['id']}-r{p['revision']}.mp4"
    assert row is not None and row["path"] == str(target) and target.is_file()
    assert row["edited_at"] and not old.exists()
    assert env.row(r.json()["project_export_id"])["clip_id"] == cid
    assert env.get(f"/projects/{p['id']}").json()["clip_id"] == cid


def test_a_failed_export_leaves_the_clip_the_project_and_every_file_as_they_were(env, media, monkeypatch):
    """The source is deleted once the export has started (its media were
    found and the plan made): FFmpeg fails, and nothing has changed."""
    from video_editor import compose

    cid, old, p = _clip_project_for_b(env)
    before_files = files_under(env.data_dir)
    before_doc = env.get(f"/projects/{p['id']}").json()
    original = compose._run_ffmpeg_stage

    def first_the_source_goes(stage, task, report, outcome):
        if media.exists():
            media.unlink()
        return original(stage, task, report, outcome)

    monkeypatch.setattr(compose, "_run_ffmpeg_stage", first_the_source_goes)
    r = env.export(p["id"])
    env.lane.run_pending()
    job = env.lane.get(r.json()["export"]["export_id"])
    assert job["status"] == "failed" and job["error"], job
    row = env.row(r.json()["project_export_id"])
    assert row["status"] == "failed" and row["error"] and row["path"] == ""
    assert env.clip_row(cid)["path"] == str(old) and old.read_bytes() == b"the clip as it was"
    assert env.get(f"/projects/{p['id']}").json() == before_doc
    after = files_under(env.data_dir)
    expected = {k: v for k, v in before_files.items() if k != "downloads/vid1.mp4"}
    assert {k: v for k, v in after.items() if k != "state.db" and not k.startswith("state.db")} == \
        {k: v for k, v in expected.items() if k != "state.db" and not k.startswith("state.db")}
    assert partials(env.data_dir) == []
    projects = env.data_dir / "clips" / "projects"
    assert not projects.exists() or not list(projects.iterdir())


def test_a_cancelled_export_leaves_the_clip_the_project_and_every_file_as_they_were(env, media, monkeypatch):
    """Cancelled while its last FFmpeg runs: the FFmpeg is killed, and
    nothing has changed. The final stage is a stand-in FFmpeg that writes
    its output at real time for up to a minute (fix 7-D2): it is still
    running when the cancel comes, however fast the PC is."""
    import dataclasses

    from video_editor import compose

    cid, old, p = _clip_project_for_b(env)
    before_files = files_under(env.data_dir)
    before_doc = env.get(f"/projects/{p['id']}").json()
    original = compose._run_ffmpeg_stage
    lane = env.lane
    seen = {}

    def cancel_while_final_runs(stage, task, report, outcome):
        if stage.id == "final":
            temp = stage.temps[stage.writes[0]]
            stage = dataclasses.replace(stage, fallback=None, retry=None, commands=[[
                stage.commands[0][0], "-hide_banner", "-v", "error", "-y", "-re", "-f", "lavfi", "-i",
                "color=c=black:s=64x64:r=10", "-t", "60", "-c:v", "libx264", "-preset", "ultrafast", temp]])

            def cancel():
                for _ in range(2000):
                    if lane._proc is not None:
                        break
                    threading.Event().wait(0.005)
                seen["killed"] = lane._proc is not None
                lane.cancel(seen["job"], wait=0)

            threading.Thread(target=cancel, daemon=True).start()
        return original(stage, task, report, outcome)

    monkeypatch.setattr(compose, "_run_ffmpeg_stage", cancel_while_final_runs)
    r = env.export(p["id"])
    seen["job"] = r.json()["export"]["export_id"]
    env.lane.run_pending()
    job = env.lane.get(seen["job"])
    assert job["status"] == "cancelled", job
    assert seen["killed"], "the final FFmpeg had ended before the cancel"
    assert env.row(r.json()["project_export_id"])["status"] == "cancelled"
    assert env.clip_row(cid)["path"] == str(old) and old.read_bytes() == b"the clip as it was"
    assert env.get(f"/projects/{p['id']}").json() == before_doc
    after = files_under(env.data_dir)
    assert {k: v for k, v in after.items() if not k.startswith("state.db")} == \
        {k: v for k, v in before_files.items() if not k.startswith("state.db")}
    assert partials(env.data_dir) == []


# ---- Branch A ----------------------------------------------------------------------------------------


def test_branch_a_makes_the_file_apply_makes(env, media):
    """An unchanged clip project exported with the clip's own preset is
    today's re-render: the same file Apply makes from the same clip (the
    same FFmpeg, in this test), and the clip replaced as Apply replaces it."""
    pytest.importorskip("numpy")
    pytest.importorskip("cv2")
    from server.jobs import Worker

    cid = env.clip(start=1.0, end=3.0, render_opts={"crop": "center", "captions": False},
                   path=clip_file(env, "clip_00001-00003.mp4"))
    p = env.project_of(cid)
    r = env.export(p["id"])
    assert r.json()["branch"] == "A", r.text
    env.lane.run_pending()
    job = env.lane.get(r.json()["export"]["export_id"])
    assert job["status"] == "done", job
    new_id = job["result"]["clip_id"]
    exported = Path(env.clip_row(new_id)["path"])
    assert exported.is_file() and env.row(r.json()["project_export_id"])["clip_id"] == new_id
    assert env.get(f"/projects/{p['id']}").json()["clip_id"] == new_id
    kept = exported.read_bytes()
    worker = Worker(_config(env))
    applied_id = Worker._rerender_clip(worker, env.db, {"clip_id": new_id})
    applied = Path(env.clip_row(applied_id)["path"])
    assert applied.read_bytes() == kept


def test_an_apply_racing_a_branch_b_export_keeps_the_clips_new_file(env, media, monkeypatch):
    """Fix 7-D1 (from the round-2 review's test_r2_race.py). Today's Apply has
    re-rendered the clip's file in place and not registered it yet, when a
    Branch B export of the same clip reaches its register step. The export
    waits for the clip's lock, finds the clip replaced, and leaves it alone:
    Apply's new clip keeps its file, and the export stays a project export."""
    pytest.importorskip("numpy")
    pytest.importorskip("cv2")
    from server import jobs
    from server.jobs import Worker

    cid, old, p = _clip_project_for_b(env)
    worker = Worker(_config(env))
    cid = Worker._rerender_clip(worker, env.db, {"clip_id": cid})   # a real render, so the next is in place
    p = env.get(f"/projects/{p['id']}").json()
    r = env.export(p["id"])
    assert r.status_code == 200 and r.json()["branch"] == "B", r.text
    export_id = r.json()["export"]["export_id"]
    original_lock = jobs.clip_lock
    waiting = threading.Event()
    seen = {}

    def lock_seen(clip_id):
        waiting.set()
        return original_lock(clip_id)

    def export_now(final_path):
        seen["apply_file"] = Path(final_path)
        monkeypatch.setattr(jobs, "clip_lock", lock_seen)
        runner = threading.Thread(target=env.lane.run_pending, daemon=True)
        runner.start()
        assert waiting.wait(120), "the export never reached its register step"
        seen["runner"] = runner

    new_id = Worker._rerender_clip(worker, env.db, {"clip_id": cid}, before_register=export_now)
    seen["runner"].join(120)
    assert not seen["runner"].is_alive()
    assert env.lane.get(export_id)["status"] == "done"
    row = env.clip_row(new_id)
    assert Path(row["path"]).is_file() and Path(row["path"]) == seen["apply_file"]
    target = env.data_dir / "clips" / "projects" / f"{p['id']}-r{p['revision']}.mp4"
    assert target.is_file()
    assert env.row(r.json()["project_export_id"])["path"] == str(target)


def test_a_branch_b_export_leaves_a_clip_whose_file_changed_meanwhile(env, media, monkeypatch):
    """Fix 7-D1: the clip's file is rewritten while the export runs (the row
    unchanged). The export does not repoint the clip or delete that file."""
    from video_editor import compose

    cid, old, p = _clip_project_for_b(env)
    original = compose._run_ffmpeg_stage

    def the_clip_changes(stage, task, report, outcome):
        if stage.id == "final":
            old.write_bytes(b"a newer render of the clip")
        return original(stage, task, report, outcome)

    monkeypatch.setattr(compose, "_run_ffmpeg_stage", the_clip_changes)
    r = env.export(p["id"])
    env.lane.run_pending()
    assert env.lane.get(r.json()["export"]["export_id"])["status"] == "done"
    assert env.clip_row(cid)["path"] == str(old) and old.read_bytes() == b"a newer render of the clip"
    target = env.data_dir / "clips" / "projects" / f"{p['id']}-r{p['revision']}.mp4"
    assert target.is_file()


def _config(env) -> dict:
    from main import BUNDLED_CONFIG, load_config

    config = load_config(BUNDLED_CONFIG)
    config["paths"]["data_dir"] = str(env.data_dir)
    config["clips"]["outro"] = False
    return config


# ---- the draft and the still (part C) ----------------------------------------------------------------


def test_the_draft_and_still_routes_are_protected(env):
    """The draft is made through a changing route (the session header and an
    Origin of this PC's own); its file and the stills are read routes: the
    header or the project's token in the address, and never a foreign
    Origin."""
    from server.compose_api import project_token

    pid, _rev = manual_project_no_media(env)
    assert env.client.post(f"/projects/{pid}/draft", json={}).status_code == 403
    assert env.client.post(f"/projects/{pid}/draft", json={},
                           headers={**env.ok, "Origin": "https://example.com"}).status_code == 403
    for path in (f"/projects/{pid}/draft", f"/projects/{pid}/still?t_us=0"):
        assert env.client.get(path).status_code == 403
        sep = "&" if "?" in path else "?"
        assert env.client.get(f"{path}{sep}t={'0' * 32}").status_code == 403
        assert env.client.get(path, headers={**env.ok, "Origin": "https://example.com"}).status_code == 403
        token = project_token(env.ok["X-Clips-Kitty-Session"], pid)
        assert env.client.get(f"{path}{sep}t={token}", headers={"Origin": "https://example.com"}).status_code == 403
        # The token of another project is not this one's.
        other = project_token(env.ok["X-Clips-Kitty-Session"], "0123456789abcdef")
        assert env.client.get(f"{path}{sep}t={other}").status_code == 403


def test_draft_and_still_refuse_as_the_export_does(env):
    assert env.post("/projects/0123456789abcdef/draft").status_code == 404
    assert env.get("/projects/0123456789abcdef/still?t_us=0").status_code == 404
    pid, rev = manual_project_no_media(env)
    r = env.post(f"/projects/{pid}/draft", {"revision": rev + 3})
    assert r.status_code == 409 and r.json()["detail"]["current_revision"] == rev
    assert env.get(f"/projects/{pid}/still?t_us=0&rev={rev + 3}").status_code == 409
    assert env.post(f"/projects/{pid}/draft").status_code == 400
    assert env.get(f"/projects/{pid}/still?t_us=0").status_code == 400
    assert env.get(f"/projects/{pid}/draft").status_code == 404
    assert env.lane.jobs() == [] and env.lane.tasks() == []


def test_a_draft_is_made_on_the_lane_kept_per_revision_and_replaced(env, media):
    from server.compose_api import preview_folder, project_token

    pid, rev = manual_project(env)
    events = []
    env.lane._publish = events.append
    r = env.post(f"/projects/{pid}/draft", {"revision": rev})
    assert r.status_code == 200, r.text
    a = r.json()
    assert a["draft"] is None and a["job"]["kind"] == "project_draft" and a["job"]["project_id"] == pid
    assert a["job"]["revision"] == rev and a["job"]["status"] == "queued"
    # Asked again while it waits: the same job, not a second one.
    assert env.post(f"/projects/{pid}/draft").json()["job"]["job_id"] == a["job"]["job_id"]
    env.lane.run_pending()
    made = list((preview_folder(env.data_dir, pid) / "draft").glob(f"r{rev}-*.mp4"))
    assert len(made) == 1, made
    key = made[0].stem.split("-", 1)[1]           # r<rev>-<facts_key>: fix 7-D6
    done = env.lane.find(f"project_draft:{pid}:r{rev}:r{rev}-{key}")
    assert done["status"] == "done", done
    assert any(e["type"] == "media_job" and e["job"].get("stage") for e in events)
    kept = preview_folder(env.data_dir, pid) / "draft" / f"r{rev}-{key}.mp4"
    assert kept.is_file() and not (env.data_dir / "clips" / "projects").exists()
    assert partials(env.data_dir) == [] and not list((env.data_dir / "editor_lane").glob("job-*"))
    # Made already: answered at once, no job.
    b = env.post(f"/projects/{pid}/draft").json()
    assert b["job"] is None and b["draft"]["revision"] == rev and b["draft"]["bytes"] == kept.stat().st_size
    token = project_token(env.ok["X-Clips-Kitty-Session"], pid)
    got = env.client.get(f"/projects/{pid}/draft?t={token}")
    assert got.status_code == 200 and got.content == kept.read_bytes()
    assert env.client.get(f"/projects/{pid}/draft?t={token}", headers={"Range": "bytes=0-9"}).status_code == 206
    # A new revision's draft replaces it.
    first = env.get(f"/projects/{pid}").json()["document"]["tracks"][0]["items"][0]["id"]
    out = env.ops(pid, rev, {"op": "set_item_props", "item_id": first, "volume": 0.5})
    env.post(f"/projects/{pid}/draft")
    env.lane.run_pending()
    names = sorted(p.name for p in kept.parent.glob("r*.mp4"))
    assert names == [f"r{out['revision']}-{key}.mp4"]
    assert env.get(f"/projects/{pid}/draft?rev={rev}").status_code == 409


def test_a_still_is_a_jpeg_of_that_moment_kept_by_revision_and_time(env, media):
    from server.compose_api import preview_folder

    pid, rev = manual_project(env)
    r = env.get(f"/projects/{pid}/still?t_us=500000")
    assert r.status_code == 200, r.text
    assert r.headers["content-type"] == "image/jpeg" and r.content[:2] == b"\xff\xd8"
    assert r.headers["x-clips-kitty-still-time"] == "500000"
    assert "x-clips-kitty-still-note" not in r.headers
    kept = list((preview_folder(env.data_dir, pid) / "stills").glob(f"r{rev}-*-500000.jpg"))
    assert len(kept) == 1 and kept[0].read_bytes() == r.content
    # A time inside the same frame is the same still, from the cache.
    again = env.get(f"/projects/{pid}/still?t_us=510000")
    assert again.content == r.content and again.headers["x-clips-kitty-still-time"] == "500000"
    # Past the end: the last frame.
    last = env.get(f"/projects/{pid}/still?t_us=99000000")
    assert last.status_code == 200 and int(last.headers["x-clips-kitty-still-time"]) < 1_500_000
    assert not list((env.data_dir / "editor_lane").glob("still-*")) and partials(env.data_dir) == []
    # Fix 7-D6: with captions from the video's transcript, the transcript
    # made again (no new revision) is a new still, not the kept one.
    out = env.ops(pid, rev, {"op": "set_captions", "enabled": True, "media_id": "m1"})
    stills = preview_folder(env.data_dir, pid) / "stills"
    assert env.get(f"/projects/{pid}/still?t_us=500000").status_code == 200
    assert len(list(stills.glob(f"r{out['revision']}-*-500000.jpg"))) == 1
    transcript = env.data_dir / "transcripts" / "vid1.json"
    transcript.write_text(transcript.read_text(encoding="utf-8") + " ", encoding="utf-8")
    assert env.get(f"/projects/{pid}/still?t_us=500000").status_code == 200
    assert len(list(stills.glob(f"r{out['revision']}-*-500000.jpg"))) == 2


# ---- the end pad covers the whole program (fix 8-D10) ------------------------------------------------


def _mean_rgb(binary: str, path: Path, t: float) -> tuple[int, int, int]:
    out = subprocess.run([binary, "-v", "error", "-ss", f"{t:.3f}", "-i", str(path), "-frames:v", "1",
                          "-vf", "scale=1:1:flags=area", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
                         capture_output=True, check=True, timeout=60).stdout
    return out[0], out[1], out[2]


def test_the_end_pad_keeps_what_was_laid_to_the_end(env, media, monkeypatch):
    """Fix 8-D10 (the round-3 review's test_t2_pad_overlay.py). An unchanged
    clip's export runs Apply's 0.4 s past the end the timeline shows (ED34).
    A video laid over the whole program stays on screen through the pad,
    holding its last frame when its source has no more; text laid to the
    end runs to the file's end; a picture that ends earlier keeps its end."""
    pytest.importorskip("numpy")
    pytest.importorskip("cv2")
    from video_editor import compose
    from video_editor.project import store

    binary = _ffmpeg_or_skip()
    cid = env.clip(start=1.0, end=4.0, render_opts={"crop": "center", "captions": False},
                   path=clip_file(env, "clip_00001-00004.mp4"))
    p = env.project_of(cid)
    doc = p["document"]
    w, h = doc["canvas"]["width"], doc["canvas"]["height"]
    end = model_end(doc)
    assert end == 3_000_000
    green, red = env.data_dir / "green.mp4", env.data_dir / "red.png"
    # Exactly the program's length: past it, the source has no more frames.
    subprocess.run([binary, "-v", "error", "-y", "-f", "lavfi", "-i", f"color=c=lime:s={w}x{h}:r=30:d=3",
                    "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", str(green)],
                   check=True, timeout=120)
    subprocess.run([binary, "-v", "error", "-y", "-f", "lavfi", "-i", f"color=c=red:s={w}x{h}", "-frames:v", "1",
                    str(red)], check=True, timeout=60)
    video = store.add_asset(env.db.conn, env.data_dir, green, kind="video", name="Green",
                            facts={"duration_us": 3_000_000, "width": w, "height": h,
                                   "fps": {"num": 30, "den": 1}, "has_audio": False})
    picture = store.add_asset(env.db.conn, env.data_dir, red, kind="image", name="Red",
                              facts={"duration_us": 0, "width": w, "height": h, "has_audio": False})
    out = env.ops(p["id"], p["revision"], {"op": "add_media", "asset": video["sha256"]},
                  {"op": "add_media", "asset": picture["sha256"]}, {"op": "add_track", "kind": "video"},
                  {"op": "add_track", "kind": "video"}, {"op": "add_track", "kind": "text"})
    kinds = {m["kind"]: k for k, m in out["document"]["media"].items() if k != doc["context"]["media_id"]}
    lower, upper = [t["id"] for t in out["document"]["tracks"] if t["kind"] == "video" and t["id"] != "V1"]
    out = env.ops(p["id"], out["revision"],
                  {"op": "insert_clip", "track_id": lower, "media_id": kinds["video"], "source_in_us": 0,
                   "source_out_us": end, "start_us": 0},
                  {"op": "insert_clip", "track_id": upper, "media_id": kinds["image"], "source_in_us": 0,
                   "source_out_us": 2_000_000, "start_us": 0},
                  {"op": "add_text", "content": "Follow for more", "start_us": 0, "duration_us": end})
    burned = []
    real_build_ass = compose.build_ass

    def seen_ass(*args, **kwargs):
        text, fonts = real_build_ass(*args, **kwargs)
        burned.append(text)
        return text, fonts

    monkeypatch.setattr(compose, "build_ass", seen_ass)
    ex = env.export(p["id"], revision=out["revision"])
    assert ex.status_code == 200 and ex.json()["branch"] == "B", ex.text
    env.lane.run_pending()
    job = env.lane.get(ex.json()["export"]["export_id"])
    assert job["status"] == "done", job
    path = Path(job["result"]["path"]) if job["result"].get("path") else Path(env.clip_row(cid)["path"])
    assert abs(job["result"]["duration_us"] - 3_400_000) <= 40_000           # Apply's length (ED34)
    r_, g, b = _mean_rgb(binary, path, 0.5)
    assert r_ > 200 and g < 60 and b < 60, "the picture is on top until 2 s"
    for t in (2.5, 3.1, 3.3):                    # the picture ended at 2 s; the video held through the pad
        r_, g, b = _mean_rgb(binary, path, t)
        assert g > 200 and r_ < 60 and b < 60, f"at {t} s the video laid over the whole program is gone"
    assert burned and "0:00:03.40,Hook,,0,0,0,,Follow for more" in burned[-1]


def model_end(doc: dict) -> int:
    items = next(t for t in doc["tracks"] if t["id"] == "V1")["items"]
    return items[-1]["start_us"] + items[-1]["duration_us"]
