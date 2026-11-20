"""Editor suite step K2, E5: nothing of the person's is lost silently.

A clip whose file is an export of Clips Kitty Editor (told by the file being
in data/clips/projects/, with or without its project) keeps its window and
render options as the AI made them. Anything that re-makes the clip from
that window would replace, or leave out, the editor's changes, so:

- Quick edit's Apply, the chat edit, the caption save and "Apply edits &
  upload" ask first (409, reason project_export) unless told to replace;
- the queue's render job asks again when it runs;
- Export in other languages asks before it makes its separate files, and its
  job asks again when it runs;
- a forced run of the video and story reels made again keep the export;
- Make clips again counts the clip as edited; Storage never takes the export.

Ported from the hand-built editor's tests (tests/test_step8_routes.py and
tests/test_step8_engine.py at commit b43d905: "A clip whose file is a
project export", "Everything made from a clip's own window"), rewritten for
the file rule: there is no project_exports table here.

Through FastAPI's TestClient on a temporary data folder; the editor's
automation is the stand-in in tests/fakes/fake_kitty_editor.py and no render
runs.
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

import pytest

from core.state import StateDB

FAKE = Path(__file__).resolve().parent / "fakes" / "fake_kitty_editor.py"
NOW = "2026-11-18T09:00:00"
EXE_NAME = "clips-kitty-editor.exe" if os.name == "nt" else "clips-kitty-editor"


class Env:
    def __init__(self, client, db, session, data_dir):
        self.client, self.db, self.data_dir = client, db, data_dir
        self.ok = {"X-Clips-Kitty-Session": session}

    def post(self, path, body=None, **headers):
        return self.client.post(path, json=body if body is not None else {}, headers={**self.ok, **headers})

    def get(self, path, **headers):
        return self.client.get(path, headers={**self.ok, **headers})

    def clip(self, name="clip_00001-00004.mp4", start=1.0, end=4.0) -> int:
        folder = self.data_dir / "clips" / "unknown-channel" / "Friday stream [vid1]"
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / name
        path.write_bytes(b"the clip as it was")
        cur = self.db.conn.execute(
            "INSERT INTO clips (video_id, start_s, end_s, score, hook, path, title, render_opts, created_at)"
            " VALUES ('vid1', ?, ?, 80, 'h', ?, 'Clip one', '', ?)", (start, end, str(path), NOW))
        self.db.conn.commit()
        return cur.lastrowid

    def clip_row(self, clip_id):
        return self.db.conn.execute("SELECT * FROM clips WHERE id = ?", (clip_id,)).fetchone()

    def project(self, clip_id: int) -> dict:
        """The clip's editor project, built."""
        r = self.post("/editor/projects/open", {"kind": "clip", "clip_id": clip_id})
        assert r.status_code == 200, r.text
        project_id = r.json()["project"]["id"]
        until = time.monotonic() + 30
        while time.monotonic() < until:
            project = self.get(f"/editor/projects/{project_id}").json()
            if project["state"] != "building":
                assert project["state"] == "ready", project["message"]
                return project
            time.sleep(0.02)
        raise AssertionError("the project was never built")

    def exported(self, clip_id: int | None = None) -> tuple[int, dict, Path]:
        """A clip whose file is now the editor's export: (clip id, project, the export)."""
        clip_id = clip_id if clip_id is not None else self.clip()
        project = self.project(clip_id)
        (Path(project["export_dir"]) / "Clip one.mp4").write_bytes(b"exported by the editor")
        r = self.post(f"/editor/projects/{project['id']}/exports", {"file": "Clip one.mp4"})
        assert r.status_code == 200 and r.json()["result"] == "replaced_clip", r.text
        return clip_id, project, Path(r.json()["path"])

    def delete_project(self, project) -> None:
        r = self.client.delete(f"/editor/projects/{project['id']}", headers=self.ok)
        assert r.status_code == 200, r.text


@pytest.fixture
def env(tmp_path, monkeypatch):
    pytest.importorskip("fastapi")
    pytest.importorskip("httpx")
    pytest.importorskip("yaml")
    from fastapi.testclient import TestClient

    from kitty_editor import automation, build, registry
    from main import BUNDLED_CONFIG, load_config
    from plugins import session
    from server import kitty_editor_api
    from server.api import create_app

    monkeypatch.delenv("CLIPS_KITTY_SESSION_SECRET", raising=False)
    editor = tmp_path / "editor"
    editor.mkdir()
    (editor / EXE_NAME).write_bytes(b"")
    monkeypatch.setenv("CLIPS_KITTY_EDITOR_DIR", str(editor))
    monkeypatch.setattr(kitty_editor_api, "supported", lambda: True)
    monkeypatch.setattr(automation, "version_of", lambda exe, timeout=5.0: "0.2.11")
    monkeypatch.setattr(automation, "command", lambda exe: [sys.executable, str(FAKE)])
    monkeypatch.setattr(build, "probe", lambda path: {"video": True, "width": 1080, "height": 1920,
                                                      "fps_num": 30, "fps_den": 1, "seconds": 3.4})
    monkeypatch.setattr(registry, "probe_seconds", lambda path: 3.4)

    def render_base(row, config, data_dir, work_dir, progress=None):
        work_dir.mkdir(parents=True, exist_ok=True)
        out = work_dir / "base.mp4"
        out.write_bytes(b"the whole window, framed")
        return out

    monkeypatch.setattr(build, "render_base", render_base)
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
    yield Env(client, db, session.path(data_dir).read_text(encoding="utf-8").strip(), data_dir)
    db.conn.close()


def render_jobs(env) -> int:
    return env.db.conn.execute("SELECT COUNT(*) FROM jobs WHERE type = 'render'").fetchone()[0]


def translate_jobs(env) -> list[dict]:
    return [json.loads(r[0]) for r in env.db.conn.execute(
        "SELECT payload FROM jobs WHERE type = 'translate' ORDER BY id").fetchall()]


# ---- a render that would replace the export -----------------------------------------------------


def test_quick_edit_asks_before_rendering_over_an_editor_export(env):
    clip_id = env.clip()
    plain = env.post(f"/clips/{clip_id}/render", {})
    assert plain.status_code == 200                       # a clip with no export renders as before
    clip_id, project, exported = env.exported(clip_id)
    assert exported.parent == env.data_dir / "clips" / "projects"
    before = render_jobs(env)

    r = env.post(f"/clips/{clip_id}/render", {"start": 1.0, "end": 4.0})
    assert r.status_code == 409
    detail = r.json()["detail"]
    assert detail["reason"] == "project_export" and detail["project_id"] == project["id"]
    assert detail["message"] == ("This clip was changed in the editor. Applying changes here starts again from "
                                 "the clip as it was before, so those changes would be lost.")
    r = env.post(f"/clips/{clip_id}/ai-edit", {"message": "make it shorter"})       # before any AI is asked
    assert r.status_code == 409 and r.json()["detail"]["reason"] == "project_export"
    assert render_jobs(env) == before and exported.is_file()
    r = env.post(f"/clips/{clip_id}/render", {"start": 1.0, "end": 4.0, "replace_project_edits": True})
    assert r.status_code == 200 and r.json()["job_id"]


def test_a_deleted_projects_export_is_still_asked_about(env, monkeypatch):
    """The project whose export is the clip's file is deleted. Every route
    that renders the clip from its own settings still asks, with no project
    to open (project_id null); "Apply anyway" works as before. Make clips
    again still keeps the clip, and Storage never offers the export."""
    from core import housekeeping, remake
    from server import youtube_service

    monkeypatch.setattr(youtube_service, "is_enabled", lambda _d: True)
    clip_id, project, exported = env.exported()
    env.delete_project(project)
    assert exported.is_file()                              # an export taken in is never deleted with its project
    before = render_jobs(env)

    r = env.post(f"/clips/{clip_id}/render", {"start": 1.0, "end": 4.0})
    assert r.status_code == 409, r.text
    detail = r.json()["detail"]
    assert detail["reason"] == "project_export" and detail["project_id"] is None
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
    judged = {c["id"]: c for c in remake.classify(env.db, "vid1", data_dir=env.data_dir)}
    assert judged[clip_id]["replace"] is False            # Make clips again keeps it
    found = housekeeping.survey(env.db, env.data_dir)
    offered = [Path(f).resolve() for files in found["_groups"].values() for f in files]
    assert exported.resolve() not in offered

    r = env.post(f"/clips/{clip_id}/render", {"start": 1.0, "end": 4.0, "replace_project_edits": True})
    assert r.status_code == 200 and r.json()["job_id"]


def test_a_render_job_never_goes_over_an_export_it_was_not_asked_about(env):
    """A render job queued before the export landed, or retried after it,
    never asked. The worker asks again under the clip's lock and the job
    fails with a plain sentence; one the person chose "Apply anyway" for
    carries replace_project_edits and goes ahead."""
    import inspect

    from server import jobs

    clip_id = env.clip()
    queued_before = env.post(f"/clips/{clip_id}/render", {"start": 1.0, "end": 4.0})
    assert queued_before.status_code == 200
    payload = json.loads(env.db.conn.execute("SELECT payload FROM jobs WHERE id = ?",
                                             (queued_before.json()["job_id"],)).fetchone()[0])
    assert "replace_project_edits" not in payload             # a plain Apply's job is as before
    clip_id, project, exported = env.exported(clip_id)
    with pytest.raises(ValueError, match="nothing was applied"):
        jobs.Worker._rerender_clip(None, env.db, payload, keep_project_export=True)
    env.delete_project(project)
    with pytest.raises(ValueError, match="nothing was applied"):
        jobs.Worker._rerender_clip(None, env.db, payload, keep_project_export=True)
    assert exported.is_file() and env.clip_row(clip_id)["path"] == str(exported)
    assert not jobs.clip_lock(clip_id).locked()                # the lock is let go when it refuses
    # The queue's render jobs ask; every other caller of the re-render is as before.
    assert 'keep_project_export=not payload.get("replace_project_edits")' in inspect.getsource(jobs.Worker.run)

    chosen = env.post(f"/clips/{clip_id}/render", {"start": 1.0, "end": 4.0, "replace_project_edits": True})
    assert chosen.status_code == 200
    payload = json.loads(env.db.conn.execute("SELECT payload FROM jobs WHERE id = ?",
                                             (chosen.json()["job_id"],)).fetchone()[0])
    assert payload["replace_project_edits"] is True


def test_a_re_render_and_a_take_in_of_one_clip_never_run_at_once(env):
    """Both hold the clip's lock (server.jobs.clip_lock)."""
    import inspect

    from kitty_editor import intake
    from server import jobs

    assert jobs.clip_lock(7) is jobs.clip_lock("7") and jobs.clip_lock(7) is not jobs.clip_lock(8)
    assert "with clip_lock(payload[\"clip_id\"]):" in inspect.getsource(jobs.Worker._rerender_clip)
    assert "from server.jobs import clip_lock" in inspect.getsource(intake._take_in)
    assert "with clip_lock(clip[\"id\"]):" in inspect.getsource(intake._take_in)


def test_apply_edits_and_upload_and_the_caption_save_ask_first_too(env, monkeypatch):
    """Every route that renders the clip from its own settings asks first:
    "Apply edits & upload" (YouTube's render_first) and PUT
    /clips/{id}/captions, as Apply and the chat edit do."""
    from server import youtube_service

    monkeypatch.setattr(youtube_service, "is_enabled", lambda _d: True)
    clip_id, project, exported = env.exported()
    before = render_jobs(env)

    publish = {"title": "Mine", "render_first": {"render_opts": {"caption_style": {}}}}
    r = env.post(f"/clips/{clip_id}/publish", publish)
    assert r.status_code == 409, r.text
    assert r.json()["detail"]["reason"] == "project_export" and r.json()["detail"]["project_id"] == project["id"]
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
    payloads = [json.loads(r[0]) for r in env.db.conn.execute(
        "SELECT payload FROM jobs WHERE type = 'render' ORDER BY id DESC LIMIT 2").fetchall()]
    assert all(p["replace_project_edits"] is True for p in payloads)


def test_a_clip_with_no_editor_export_publishes_and_saves_captions_as_before(env, monkeypatch):
    from server import youtube_service

    monkeypatch.setattr(youtube_service, "is_enabled", lambda _d: True)
    clip_id = env.clip()
    r = env.post(f"/clips/{clip_id}/publish", {"title": "Mine", "render_first": {"end": 3.0}})
    assert r.status_code == 200, r.text
    r = env.client.put(f"/clips/{clip_id}/captions", json={"lines": []}, headers=env.ok)
    assert r.status_code == 200
    payloads = [json.loads(r[0]) for r in env.db.conn.execute("SELECT payload FROM jobs WHERE type = 'render'")]
    assert payloads and all("replace_project_edits" not in p for p in payloads)       # the jobs are as before


def test_a_publish_that_renders_nothing_is_never_asked(env, monkeypatch):
    """Everything that only reads the clip's file uses the export."""
    from server import youtube_service

    monkeypatch.setattr(youtube_service, "is_enabled", lambda _d: True)
    clip_id, project, exported = env.exported()
    r = env.post(f"/clips/{clip_id}/publish", {"title": "Mine"})
    assert r.status_code == 200, r.text
    assert render_jobs(env) == 0


# ---- separate files made from the clip's own window ---------------------------------------------


def test_export_in_other_languages_asks_first_for_an_editor_export(env, tmp_path):
    """Burned subtitles, a dub and subtitle files are made from the clip's
    own window and caption lines. For a clip whose file is an editor export,
    POST /translate asks first (409, reason project_export, separate_file)
    and queues nothing; "Make it anyway" (without_project_edits) queues the
    job as before, with the flag. Text only (the review stage, the post
    text) never asks."""
    clip_id, project, exported = env.exported()
    folder = tmp_path / "out"
    base = {"clip_ids": [clip_id], "languages": ["es"], "stage": "export", "folder": str(folder)}
    for asked in ({"burn": True}, {"dub": True}, {"subtitles": True}, {"burn": True, "dub": True}):
        r = env.post("/translate", {**base, **asked})
        assert r.status_code == 409, (asked, r.text)
        detail = r.json()["detail"]
        assert detail["reason"] == "project_export" and detail["separate_file"] is True
        assert detail["clip_ids"] == [clip_id] and detail["count"] == 1 and detail["total"] == 1
        assert detail["project_id"] == project["id"]
        assert "without the editor's changes" in detail["message"]
    assert translate_jobs(env) == []
    for text_only in ({**base, "post_text": True}, {**base, "stage": "translate", "burn": True}):
        assert env.post("/translate", text_only).status_code == 200
    assert all("without_project_edits" not in p for p in translate_jobs(env))
    r = env.post("/translate", {**base, "burn": True, "without_project_edits": True})
    assert r.status_code == 200, r.text
    assert translate_jobs(env)[-1]["without_project_edits"] is True


def test_a_batch_is_asked_about_once_and_a_plain_clip_never(env, tmp_path):
    """A batch names every clip it affects, once (count of total); a batch
    of clips the editor never exported queues exactly the job it did before
    (no new key in its payload). A deleted project's export is still asked
    about, with nothing to open."""
    plain = env.clip("clip_00005-00007.mp4", 5.0, 7.0)
    base = {"languages": ["es"], "stage": "export", "folder": str(tmp_path / "out"), "burn": True}
    r = env.post("/translate", {**base, "clip_ids": [plain]})
    assert r.status_code == 200, r.text
    assert set(translate_jobs(env)[-1]) == {"clip_ids", "languages", "stage", "folder", "include_video", "burn",
                                            "dub", "subtitles", "post_text", "voices", "style"}

    clip_id, project, exported = env.exported()
    r = env.post("/translate", {**base, "clip_ids": [plain, clip_id]})
    assert r.status_code == 409
    detail = r.json()["detail"]
    assert detail["clip_ids"] == [clip_id] and detail["count"] == 1 and detail["total"] == 2
    assert detail["project_id"] == project["id"]

    env.delete_project(project)
    r = env.post("/translate", {**base, "clip_ids": [clip_id, plain]})
    assert r.status_code == 409
    assert r.json()["detail"]["project_id"] is None and r.json()["detail"]["clip_ids"] == [clip_id]


def test_a_translate_job_never_makes_files_it_was_not_asked_about(env, tmp_path, monkeypatch):
    """A translate job queued before the clip's export landed asks again
    when it runs: it fails with a plain sentence before anything is made,
    and before any AI is set up. One the person chose "Make it anyway" for
    goes on as before."""
    from llm import registry
    from server import jobs

    clip_id = env.clip()
    folder = tmp_path / "out"
    r = env.post("/translate", {"clip_ids": [clip_id], "languages": ["es"], "stage": "export",
                                "folder": str(folder), "burn": True})
    assert r.status_code == 200
    payload = translate_jobs(env)[-1]
    env.exported(clip_id)

    class Worker:
        def __init__(self):
            self.config = {"paths": {"data_dir": str(env.data_dir)}, "llm": {"backend": "ollama/none"}}

    def no_ai(_cfg):
        raise AssertionError("no AI is set up for a job that stops")

    monkeypatch.setattr(registry, "create_backend", no_ai)
    with pytest.raises(RuntimeError, match="changed in the editor"):
        jobs.Worker._translate_clips(Worker(), env.db, payload)
    assert not folder.exists()
    with pytest.raises(AssertionError, match="no AI"):     # past the question: on as before
        jobs.Worker._translate_clips(Worker(), env.db, {**payload, "without_project_edits": True})


# ---- runs that only happen to find the clip's window again --------------------------------------


def test_a_run_that_makes_the_window_again_keeps_the_export(env, tmp_path):
    """A forced run (Process anyway, a retry) that finds a clip's window
    again re-points the clip at its fresh render. Over an editor export it
    keeps the clip on the export instead; every other clip is re-pointed
    exactly as before."""
    pytest.importorskip("numpy")      # core.pipeline imports the analysis stack
    from analysis.metadata import ClipMetadata
    from core import pipeline
    from core.models import ClipCandidate

    plain = env.clip("clip_00005-00007.mp4", 5.0, 7.0)
    clip_id, project, exported = env.exported()
    fresh = tmp_path / "fresh.mp4"
    fresh.write_bytes(b"made again")
    meta = ClipMetadata(title="t", description="", hashtags=[])
    for start, end in ((1.0, 4.0), (5.0, 7.0)):
        out = pipeline._register_clip(env.db, "vid1", ClipCandidate(start=start, end=end, score=80), fresh, meta,
                                      "{}", None)
        assert out is None
    assert env.clip_row(clip_id)["path"] == str(exported) and exported.is_file()
    assert env.clip_row(plain)["path"] == str(fresh)
    assert fresh.is_file()      # left for the run's story reels; Storage frees it later

    env.delete_project(project)   # with no project left, the file alone still says so
    assert pipeline._register_clip(env.db, "vid1", ClipCandidate(start=1.0, end=4.0, score=80), fresh, meta,
                                   "{}", None) is None
    assert env.clip_row(clip_id)["path"] == str(exported)


def test_a_story_reel_made_again_keeps_its_export():
    import inspect

    pytest.importorskip("numpy")      # core.pipeline imports the analysis stack
    from core import pipeline

    reels = inspect.getsource(pipeline._sport_reels)
    assert reels.index('row is not None and _keeps_project_export(db, row["id"])') < \
        reels.index('db.set_clip(row["id"], path=str(out)')
    assert pipeline._keeps_project_export(object(), 1) is False


def test_the_export_guard_uses_only_the_database(env):
    """core/pipeline.py tells an editor export through StateDB alone: it
    never loads the editor's package (tests/test_editor_import_guard.py)."""
    import inspect

    from core.state import StateDB as Database

    source = inspect.getsource(Database.project_export_of)
    assert "kitty_editor" not in source.replace("kitty_editor/intake.py", "").replace(
        "kitty_editor.registry.project_of_export", "")
    clip_id, project, exported = env.exported()
    assert env.db.project_export_of(clip_id) == ""
    assert env.db.project_export_clips([clip_id]) == {clip_id: ""}


def test_a_clip_opened_in_the_editor_but_never_exported_is_kept_by_make_clips_again(env):
    """K-D23: a clip with a project in Clips Kitty Editor counts as edited
    (kept) in Make clips again, exported or not, as the step-5 projects
    table does; a clip with none is judged as before. The project's id
    follows the clip through a re-render (registry.follow_clip_id)."""
    from core import remake
    from kitty_editor import registry

    opened = env.clip()
    plain = env.clip(name="clip_00005-00008.mp4", start=5.0, end=8.0)
    before = {c["id"]: c for c in remake.classify(env.db, "vid1", data_dir=env.data_dir)}
    assert "edited" not in before[opened]["reasons"]
    project = env.project(opened)
    judged = {c["id"]: c for c in remake.classify(env.db, "vid1", data_dir=env.data_dir)}
    assert "edited" in judged[opened]["reasons"] and judged[opened]["replace"] is False
    assert judged[plain]["reasons"] == before[plain]["reasons"]                   # nothing else changes
    assert remake.classify(env.db, "vid1") == list(before.values())               # no data folder: as before

    registry.follow_clip_id(env.data_dir, opened, 999999)                         # its project is another clip's now
    judged = {c["id"]: c for c in remake.classify(env.db, "vid1", data_dir=env.data_dir)}
    assert "edited" not in judged[opened]["reasons"]
    registry.follow_clip_id(env.data_dir, 999999, opened)
    (registry.root(env.data_dir) / project["id"] / "kitty.json").write_text("{broken", encoding="utf-8")
    judged = {c["id"]: c for c in remake.classify(env.db, "vid1", data_dir=env.data_dir)}
    assert judged[opened]["reasons"] == before[opened]["reasons"]                 # an unreadable record names nothing
