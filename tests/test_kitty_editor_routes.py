"""Editor suite step K2: Clips Kitty Editor's routes (server/kitty_editor_api.py),
through FastAPI's TestClient on a temporary data folder. Nothing listens on
a port and no editor runs: its automation is answered by the stand-in in
tests/fakes/fake_kitty_editor.py, and the clip's framed render is stood in
for (a small file), so no FFmpeg is needed.

- Every route needs the session header and an Origin of this PC's own.
- GET /editor/status says in plain words when the editor is missing, is
  another program, or can't run on this system.
- A project is opened or made for a clip, a whole video or a picked file;
  listed, opened again, deleted. A client's path is checked like the other
  local-file routes check it.
- An export the editor finished is taken in by its file name only.
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
PROBED = {"video": True, "width": 1080, "height": 1920, "fps_num": 30, "fps_den": 1, "seconds": 45.4}


class Env:
    def __init__(self, client, db, session, data_dir, tmp_path):
        self.client, self.db, self.data_dir, self.tmp = client, db, data_dir, tmp_path
        self.ok = {"X-Clips-Kitty-Session": session}
        self.rendered: list[dict] = []

    def post(self, path, body=None, **headers):
        return self.client.post(path, json=body if body is not None else {}, headers={**self.ok, **headers})

    def get(self, path, **headers):
        return self.client.get(path, headers={**self.ok, **headers})

    def delete(self, path):
        return self.client.delete(path, headers=self.ok)

    def clip(self, render_opts=None, name="clip_00010-00055.mp4", start=10.0, end=55.0, title="Clip one") -> int:
        folder = self.data_dir / "clips" / "channel" / "Friday stream [vid1]"
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / name
        path.write_bytes(b"the clip as the AI made it")
        cur = self.db.conn.execute(
            "INSERT INTO clips (video_id, start_s, end_s, score, hook, path, title, render_opts, created_at)"
            " VALUES ('vid1', ?, ?, 80, 'h', ?, ?, ?, ?)",
            (start, end, str(path), title, json.dumps(render_opts) if render_opts is not None else "", NOW))
        self.db.conn.commit()
        return cur.lastrowid

    def row(self, clip_id):
        return self.db.conn.execute("SELECT * FROM clips WHERE id = ?", (clip_id,)).fetchone()

    def ready(self, project_id: str, want="ready") -> dict:
        """The project once its build has ended."""
        until = time.monotonic() + 30
        while time.monotonic() < until:
            project = self.get(f"/editor/projects/{project_id}").json()
            if project["state"] != "building":
                assert project["state"] == want, (project["state"], project["message"])
                return project
            time.sleep(0.02)
        raise AssertionError("the project was never built")

    def open(self, body, want="ready") -> dict:
        r = self.post("/editor/projects/open", body)
        assert r.status_code == 200, r.text
        return self.ready(r.json()["project"]["id"], want)

    def export(self, project, name="My clip.mp4", content=b"exported by the editor") -> Path:
        path = Path(project["export_dir"]) / name
        path.write_bytes(content)
        return path


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
    monkeypatch.setenv("FAKE_KITTY_EDITOR_SECONDS", "45.4")
    monkeypatch.setattr(kitty_editor_api, "supported", lambda: True)
    monkeypatch.setattr(automation, "version_of", lambda exe, timeout=5.0: "0.2.11")
    monkeypatch.setattr(automation, "command", lambda exe: [sys.executable, str(FAKE)])
    monkeypatch.setattr(build, "probe", lambda path: dict(PROBED))
    monkeypatch.setattr(registry, "probe_seconds", lambda path: 12.5)

    data_dir = tmp_path / "data"
    config = load_config(BUNDLED_CONFIG)
    config["paths"]["data_dir"] = str(data_dir)
    app = create_app(config, tmp_path / "settings.yaml")
    db = StateDB(data_dir / "state.db")
    db.conn.execute("INSERT INTO videos (video_id, title, status, duration, created_at, updated_at)"
                    " VALUES ('vid1', 'Friday stream', 'done', 3600, ?, ?)", (NOW, NOW))
    db.conn.commit()
    downloads = data_dir / "downloads"
    downloads.mkdir(parents=True, exist_ok=True)
    (downloads / "vid1.mp4").write_bytes(b"not read: the render is stood in for")
    client = TestClient(app, base_url="http://127.0.0.1")
    made = Env(client, db, session.path(data_dir).read_text(encoding="utf-8").strip(), data_dir, tmp_path)

    def render_base(row, config, data_dir, work_dir, progress=None):
        made.rendered.append({"clip_id": row["id"], "work_dir": str(work_dir)})
        if not (Path(data_dir) / "downloads" / f"{row['video_id']}.mp4").exists():
            return None
        work_dir.mkdir(parents=True, exist_ok=True)
        out = work_dir / "clip_00010-00055.mp4"
        out.write_bytes(b"the whole window, framed, no captions")
        return out

    monkeypatch.setattr(build, "render_base", render_base)
    yield made
    db.conn.close()


def saved(project) -> dict:
    return json.loads((Path(project["project_dir"]) / "concat.json").read_text(encoding="utf-8"))


# ---- the guard ----------------------------------------------------------------------------------

ROUTES = [("get", "/editor/status"), ("get", "/editor/launch"), ("get", "/editor/projects"),
          ("post", "/editor/projects/open"), ("get", "/editor/projects/" + "0" * 32),
          ("post", "/editor/projects/" + "0" * 32 + "/open"), ("delete", "/editor/projects/" + "0" * 32),
          ("post", "/editor/projects/" + "0" * 32 + "/exports")]


@pytest.mark.parametrize("method, path", ROUTES)
def test_every_route_needs_the_session_header_and_the_apps_own_origin(env, method, path):
    call = getattr(env.client, method)
    assert call(path).status_code == 403                                             # no header
    assert call(path, headers={"X-Clips-Kitty-Session": "wrong"}).status_code == 403
    for origin in ("https://example.com", "http://127.0.0.1:9999", "http://localhost:3000"):
        assert call(path, headers={**env.ok, "Origin": origin}).status_code == 403, origin
    for origin in ("null", "file://", "http://localhost:5173"):                      # the app's own windows
        assert call(path, headers={**env.ok, "Origin": origin}).status_code != 403, origin


def test_the_routes_are_internal_and_documented():
    pytest.importorskip("fastapi")
    from server import api_stability

    assert not any(path.startswith("/editor") for _method, path in api_stability.ROUTES)      # promised to nobody
    for method, path in (("GET", "/editor/status"), ("POST", "/editor/projects/open"),
                         ("POST", "/editor/projects/{project_id}/exports")):
        assert api_stability.label(method, path) == api_stability.INTERNAL
    reference = (Path(__file__).resolve().parent.parent / "docs" / "developers" / "api-reference.md").read_text(
        encoding="utf-8")
    for path in ("/editor/status", "/editor/launch", "/editor/projects/open", "/editor/projects/{project_id}/exports"):
        assert path in reference, path


def test_the_served_editor_routes_are_exactly_these_and_all_internal(env):
    from server import api_stability as st

    served = {(m, p) for m, p, _ in st.app_routes(env.client.app)}
    mine = {("GET", "/editor/status"), ("GET", "/editor/launch"), ("GET", "/editor/projects"),
            ("POST", "/editor/projects/open"), ("GET", "/editor/projects/{project_id}"),
            ("POST", "/editor/projects/{project_id}/open"), ("DELETE", "/editor/projects/{project_id}"),
            ("POST", "/editor/projects/{project_id}/exports")}
    assert {route for route in served if route[1].startswith("/editor/")} == mine
    for route in mine:
        assert st.label(*route) == st.INTERNAL


# ---- where the editor is ------------------------------------------------------------------------


def test_status_says_in_plain_words_when_the_editor_is_missing(env, monkeypatch, tmp_path):
    from kitty_editor import automation
    from server import kitty_editor_api

    assert env.get("/editor/status").json() == {"found": True, "version": "0.2.11", "message": "", "reason": ""}
    launch = env.get("/editor/launch").json()
    assert Path(launch["exe"]).name == EXE_NAME and Path(launch["cwd"]) == Path(launch["exe"]).parent

    monkeypatch.setattr(kitty_editor_api, "supported", lambda: False)
    assert env.get("/editor/status").json() == {
        "found": False, "version": "", "message": "The editor runs on Windows only.", "reason": "windows_only"}
    monkeypatch.setattr(kitty_editor_api, "supported", lambda: True)

    monkeypatch.setenv("CLIPS_KITTY_EDITOR_DIR", str(tmp_path / "nothing here"))
    status = env.get("/editor/status").json()
    assert status == {"found": False, "version": "", "reason": "missing",
                      "message": "The editor is not installed with this copy of Clips Kitty."}
    r = env.get("/editor/launch")
    assert r.status_code == 503 and r.json()["detail"] == {"message": status["message"], "reason": "missing"}
    r = env.post("/editor/projects/open", {"kind": "clip", "clip_id": env.clip()})
    assert r.status_code == 503 and r.json()["detail"]["reason"] == "missing"
    assert env.get("/editor/projects").json() == {"projects": []}                    # nothing was made

    other = tmp_path / "another program"
    other.mkdir()
    (other / EXE_NAME).write_bytes(b"")
    monkeypatch.setenv("CLIPS_KITTY_EDITOR_DIR", str(other))
    monkeypatch.setattr(automation, "version_of", lambda exe, timeout=5.0: None)
    assert env.get("/editor/status").json() == {
        "found": False, "version": "", "reason": "not_ours",
        "message": "The editor found on this PC is not Clips Kitty Editor."}


def test_the_version_is_asked_once_per_engine_start(env, monkeypatch):
    from kitty_editor import automation

    asked = []
    monkeypatch.setattr(automation, "version_of", lambda exe, timeout=5.0: asked.append(exe) or "9.9")
    for _ in range(3):
        assert env.get("/editor/status").json()["version"] == "9.9"
    env.get("/editor/launch")
    assert len(asked) == 1


# ---- a clip's project ---------------------------------------------------------------------------


def test_a_clip_is_opened_as_a_project_and_the_same_one_every_time_after(env):
    opts = {"caption_lines": [{"start": 0.5, "end": 3.0, "text": "first line"},
                              {"start": 12.0, "end": 14.0, "text": "cut away"},
                              {"start": 16.0, "end": 22.0, "text": "after the cut"}],
            "caption_style": {"highlight": True},
            "edit": {"keep": [[0.0, 10.0], [15.0, 40.0]], "hook": {"text": "Wait for it", "seconds": 3}}}
    clip_id = env.clip(opts, title="My clip: part 1?")
    r = env.post("/editor/projects/open", {"kind": "clip", "clip_id": clip_id})
    assert r.status_code == 200, r.text
    assert r.json()["created"] is True and r.json()["project"]["state"] == "building"
    project = env.ready(r.json()["project"]["id"])

    home = env.data_dir / "editor" / "projects" / project["id"]
    assert project["kind"] == "clip" and project["name"] == "My clip part 1"         # a Windows folder name
    assert Path(project["project_dir"]) == home / "p" / "My clip part 1"
    assert Path(project["export_dir"]) == home / "out" and project["export_name"] == "My clip part 1"
    assert project["canvas"] == {"width": 1080, "height": 1920, "fps_num": 30, "fps_den": 1}
    assert project["shape"] == "9:16" and project["progress"] == 1.0
    assert project["notes"] == ["framing_baked", "captions_plain"]
    clip_file = env.row(clip_id)["path"]
    assert project["clip"]["id"] == clip_id and project["clip"]["file"] == clip_file
    assert project["clip"]["file_stamp"] == [os.stat(clip_file).st_size, os.stat(clip_file).st_mtime_ns]
    assert (project["clip_changed"], project["clip_gone"], project["media_missing"]) == (False, False, False)
    # The video is the framed, caption-free render of the whole window, kept in the project.
    # Named for people (K-D20): the clip's title, never "base.mp4" or an id.
    assert Path(project["video_file"]) == home / "media" / "My clip part 1.mp4"
    assert Path(project["video_file"]).read_bytes() == b"the whole window, framed, no captions"
    assert [Path(m["path"]).name for m in saved(project)["media"]] == ["My clip part 1.mp4"]
    assert not (home / "work").exists() and (home / "kitty.json").is_file()
    doc = saved(project)
    pieces = [(c["start"], c["duration"], c["sourceStart"]) for c in doc["clips"] if c["kind"] == "video"]
    assert pieces == [(0.0, 10.0, 0.0), (10.0, 25.0, 15.0)]
    texts = {c["text"]: c["start"] for c in doc["clips"] if c["kind"] == "text"}
    assert texts == {"first line": 0.5, "after the cut": 11.0, "Wait for it": 0.0}
    assert not (Path(project["project_dir"]) / "concat.lock").exists()

    again = env.post("/editor/projects/open", {"kind": "clip", "clip_id": clip_id}).json()
    assert again["created"] is False and again["project"]["id"] == project["id"]
    assert len(env.rendered) == 1                                                    # never rendered again
    fresh = env.post("/editor/projects/open", {"kind": "clip", "clip_id": clip_id, "fresh": True}).json()
    assert fresh["created"] is True and fresh["project"]["id"] != project["id"]
    env.ready(fresh["project"]["id"])
    listed = [p["id"] for p in env.get("/editor/projects").json()["projects"]]
    assert set(listed) == {project["id"], fresh["project"]["id"]}                    # the old one stays
    newest = env.post("/editor/projects/open", {"kind": "clip", "clip_id": clip_id}).json()
    assert newest["project"]["id"] == fresh["project"]["id"]


def test_a_clip_changed_or_deleted_after_its_project_is_said_and_the_project_still_opens(env):
    clip_id = env.clip()
    project = env.open({"kind": "clip", "clip_id": clip_id})
    Path(env.row(clip_id)["path"]).write_bytes(b"Quick edit rendered it again, longer")
    assert env.get(f"/editor/projects/{project['id']}").json()["clip_changed"] is True
    again = env.post("/editor/projects/open", {"kind": "clip", "clip_id": clip_id}).json()
    assert again["created"] is False and again["project"]["clip_changed"] is True

    env.db.conn.execute("DELETE FROM clips WHERE id = ?", (clip_id,))
    env.db.conn.commit()
    gone = env.get(f"/editor/projects/{project['id']}").json()
    assert gone["clip_gone"] is True and gone["clip_changed"] is False and gone["media_missing"] is False
    r = env.post(f"/editor/projects/{project['id']}/open", {})
    assert r.status_code == 200 and r.json()["state"] == "ready" and r.json()["recovered"] == []


def test_a_render_that_gives_the_clip_a_new_id_is_followed(env):
    """Quick edit's Apply re-renders a clip under a new id (server/jobs.py):
    the project then says "changed", not "deleted"."""
    import inspect

    from kitty_editor import registry
    from server import jobs

    clip_id = env.clip()
    project = env.open({"kind": "clip", "clip_id": clip_id})
    assert "follow_clip_id(data_dir, clip[\"id\"], new_row[\"id\"])" in inspect.getsource(
        jobs.Worker._rerender_clip_now)
    newer = Path(env.row(clip_id)["path"]).with_name("clip_00012-00050.mp4")
    newer.write_bytes(b"rendered again")
    env.db.conn.execute("UPDATE clips SET id = 4242, path = ? WHERE id = ?", (str(newer), clip_id))
    env.db.conn.commit()
    registry.follow_clip_id(env.data_dir, clip_id, 4242)
    after = env.get(f"/editor/projects/{project['id']}").json()
    assert (after["clip"]["id"], after["clip_changed"], after["clip_gone"]) == (4242, True, False)


def test_when_the_original_video_is_gone_the_clips_finished_file_is_used(env):
    (env.data_dir / "downloads" / "vid1.mp4").unlink()
    clip_id = env.clip({"caption_lines": [{"start": 0, "end": 2, "text": "burned in"}],
                        "edit": {"keep": [[0, 5]]}})
    project = env.open({"kind": "clip", "clip_id": clip_id})
    assert project["notes"] == ["source_missing"]
    home = env.data_dir / "editor" / "projects" / project["id"]
    assert (home / "media" / "Clip one.mp4").read_bytes() == b"the clip as the AI made it"
    doc = saved(project)
    assert [(c["kind"], c["start"], c["duration"]) for c in doc["clips"]] == [("video", 0.0, 45.4)]


def test_a_clip_that_is_already_an_editor_export_opens_from_its_finished_file(env):
    clip_id = env.clip({"edit": {"keep": [[0, 5]]}})
    exports = env.data_dir / "clips" / "projects"
    exports.mkdir(parents=True)
    exported = exports / f"{'0' * 32}-1.mp4"
    exported.write_bytes(b"made in the editor before")
    env.db.set_clip(clip_id, path=str(exported))
    project = env.open({"kind": "clip", "clip_id": clip_id})
    assert project["notes"] == ["from_export"] and env.rendered == []                # never re-made from the window
    home = env.data_dir / "editor" / "projects" / project["id"]
    assert (home / "media" / "Clip one.mp4").read_bytes() == b"made in the editor before"


def test_a_build_that_fails_says_so_plainly_and_opening_again_builds_again(env, monkeypatch):
    clip_id = env.clip()
    monkeypatch.setenv("FAKE_KITTY_EDITOR_FAIL", "media_import")
    failed = env.open({"kind": "clip", "clip_id": clip_id}, want="failed")
    assert failed["message"] == "The editor could not make this project." and "media_import" not in failed["message"]
    monkeypatch.delenv("FAKE_KITTY_EDITOR_FAIL")
    r = env.post("/editor/projects/open", {"kind": "clip", "clip_id": clip_id})
    assert r.status_code == 200 and r.json()["created"] is False
    assert r.json()["project"]["id"] == failed["id"] and r.json()["project"]["state"] == "building"
    rebuilt = env.ready(failed["id"])                                                # the same folder
    assert rebuilt["message"] == "" and saved(rebuilt)["clips"]


def test_a_clip_whose_files_are_all_gone_fails_in_plain_words(env):
    (env.data_dir / "downloads" / "vid1.mp4").unlink()
    clip_id = env.clip()
    Path(env.row(clip_id)["path"]).unlink()
    failed = env.open({"kind": "clip", "clip_id": clip_id}, want="failed")
    assert failed["message"] == "This clip's video file is gone, so it can't be opened in the editor."


def test_a_project_left_half_made_by_a_stopped_engine_is_made_again(env):
    from kitty_editor import registry

    clip_id = env.clip()
    project = env.open({"kind": "clip", "clip_id": clip_id})
    registry.update(env.data_dir, project["id"], lambda m: m.update(state="building", progress=0.4))
    stale = env.get(f"/editor/projects/{project['id']}").json()
    assert stale["state"] == "failed" and stale["message"]                           # not "building" for ever
    r = env.post(f"/editor/projects/{project['id']}/open", {})
    assert r.status_code == 200
    assert env.ready(project["id"])["state"] == "ready"


@pytest.mark.parametrize("body, status", [
    ({"kind": "clip"}, 400), ({"kind": "clip", "clip_id": 999}, 404), ({"kind": "clip", "clip_id": 2**70}, 404),
    ({"kind": "clip", "clip_id": 1, "shape": "9:16"}, 400), ({"kind": "clip", "clip_id": 1, "path": "C:/x.mp4"}, 400),
    ({"kind": "movie"}, 400), ({"kind": "video"}, 400), ({"kind": "video", "video_id": "nope"}, 404),
    ({"kind": "video", "video_id": "vid1", "shape": "3:2"}, 400),
    ({"kind": "video", "video_id": "vid1", "clip_id": 1}, 400),
    ({"kind": "file", "shape": "16:9"}, 400), ({"kind": "clip", "clip_id": 1, "surprise": True}, 422), ({}, 422),
])
def test_a_bad_request_to_open_is_refused(env, body, status):
    assert env.clip() == 1
    r = env.post("/editor/projects/open", body)
    assert r.status_code == status, r.text
    assert env.get("/editor/projects").json() == {"projects": []}


# ---- a whole video and a file from the PC -------------------------------------------------------


def test_a_whole_video_is_opened_in_the_shape_chosen(env):
    r = env.post("/editor/projects/open", {"kind": "video", "video_id": "vid1"})
    assert r.status_code == 200 and r.json()["created"] is True
    project = env.ready(r.json()["project"]["id"])
    source = (env.data_dir / "downloads" / "vid1.mp4").resolve()
    assert project["kind"] == "video" and project["name"] == "Friday stream" and project["shape"] == "16:9"
    assert project["canvas"] == {"width": 1920, "height": 1080, "fps_num": 30, "fps_den": 1}
    assert project["media"] == [str(source)] and project["clip"] is None and project["notes"] == []
    doc = saved(project)
    assert [(c["start"], c["framing"]) for c in doc["clips"]] == [(0.0, "fit")]
    media = env.data_dir / "editor" / "projects" / project["id"] / "media"
    linked = Path(project["video_file"]) == media / "Friday stream.mp4"
    if linked:      # K-D20: the video's title in the editor, a hard link to the Library's download (no copy)
        assert Path(doc["media"][0]["path"]) == media / "Friday stream.mp4"
        assert os.path.samefile(project["video_file"], source)
        assert [f.name for f in media.iterdir()] == ["Friday stream.mp4"]
    else:           # a drive that can't link: the download in place, never copied
        assert Path(doc["media"][0]["path"]) == source
        assert not list(media.iterdir())

    same = env.post("/editor/projects/open", {"kind": "video", "video_id": "vid1", "shape": "16:9"}).json()
    assert same["created"] is False and same["project"]["id"] == project["id"]
    tall = env.open({"kind": "video", "video_id": "vid1", "shape": "9:16"})
    assert tall["id"] != project["id"] and (tall["canvas"]["width"], tall["canvas"]["height"]) == (1080, 1920)
    for shape, size in (("1:1", (1080, 1080)), ("4:5", (1080, 1350))):
        made = env.open({"kind": "video", "video_id": "vid1", "shape": shape})
        assert (made["canvas"]["width"], made["canvas"]["height"]) == size

    source.unlink()
    # With the link, the project still has its video when the Library's download goes.
    assert env.get(f"/editor/projects/{project['id']}").json()["media_missing"] is (not linked)
    r = env.post("/editor/projects/open", {"kind": "video", "video_id": "vid1", "shape": "16:9"})
    assert r.status_code == 404 and "isn't in Clips Kitty any more" in r.json()["detail"]


def test_a_file_from_the_pc_is_opened_in_place_with_no_ai_run(env):
    picked = env.tmp / "my videos" / "Holiday: day 1.mp4"
    picked.parent.mkdir()
    picked.write_bytes(b"a video")
    jobs_before = env.db.conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
    project = env.open({"kind": "file", "path": str(picked), "shape": "1:1"})
    assert project["kind"] == "file" and project["name"] == "Holiday day 1"
    assert Path(project["source_file"]) == picked.resolve() and project["media"] == [project["source_file"]]
    assert (project["canvas"]["width"], project["canvas"]["height"]) == (1080, 1080)
    assert env.db.conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == jobs_before      # no AI run
    assert env.db.conn.execute("SELECT COUNT(*) FROM videos").fetchone()[0] == 1              # no Library entry
    again = env.post("/editor/projects/open", {"kind": "file", "path": str(picked), "shape": "1:1"}).json()
    assert again["created"] is False and again["project"]["id"] == project["id"]


def test_a_path_from_a_client_is_checked_like_the_other_local_file_routes(env, monkeypatch):
    """As tests/test_input_safety.py: a folder, a network share, a file of
    another kind and a file that is not there are refused before anything
    is made; so is a file with no picture in it."""
    from kitty_editor import build

    folder = env.tmp / "a folder.mp4"
    folder.mkdir()
    text = env.tmp / "notes.txt"
    text.write_text("not a video", encoding="utf-8")
    database = env.data_dir / "state.db"
    for path in (str(folder), "\\\\server\\share\\video.mp4", "//server/share/video.mp4", str(text), str(database),
                 str(env.tmp / "missing.mp4"), "", "video.mp4\x00.txt"):
        r = env.post("/editor/projects/open", {"kind": "file", "path": path})
        assert r.status_code == 400, (path, r.text)
        assert r.json()["detail"] == "not a video file this app can open"
    sound = env.tmp / "song.mp4"
    sound.write_bytes(b"sound only")
    monkeypatch.setattr(build, "probe", lambda path: {**PROBED, "video": False})
    r = env.post("/editor/projects/open", {"kind": "file", "path": str(sound)})
    assert r.status_code == 400 and r.json()["detail"] == "that file doesn't look like a video"
    assert env.get("/editor/projects").json() == {"projects": []}


# ---- listed, opened again, deleted --------------------------------------------------------------


def test_projects_are_listed_last_opened_first_read_and_deleted(env):
    first = env.open({"kind": "clip", "clip_id": env.clip()})
    second = env.open({"kind": "video", "video_id": "vid1"})
    time.sleep(0.05)
    r = env.post(f"/editor/projects/{first['id']}/open", {})
    assert r.status_code == 200 and r.json()["last_opened_at"] > first["last_opened_at"]
    assert [p["id"] for p in env.get("/editor/projects").json()["projects"]] == [first["id"], second["id"]]

    for bad in ("nope", "0" * 32, "A" * 32, "..%2F..%2Fstate.db"):
        assert env.get(f"/editor/projects/{bad}").status_code == 404
        assert env.post(f"/editor/projects/{bad}/open", {}).status_code == 404
        assert env.delete(f"/editor/projects/{bad}").status_code == 404
        assert env.post(f"/editor/projects/{bad}/exports", {"file": "x.mp4"}).status_code == 404

    assert env.delete(f"/editor/projects/{second['id']}").json() == {"deleted": True}
    assert not (env.data_dir / "editor" / "projects" / second["id"]).exists()
    assert (env.data_dir / "downloads" / "vid1.mp4").is_file()                      # the video itself stays
    assert [p["id"] for p in env.get("/editor/projects").json()["projects"]] == [first["id"]]


def test_a_project_the_editor_has_open_is_not_deleted(env, monkeypatch):
    from kitty_editor import registry

    project = env.open({"kind": "video", "video_id": "vid1"})

    def held(data_dir, project_id):
        raise registry.InUse(project_id)

    monkeypatch.setattr(registry, "delete", held)
    r = env.delete(f"/editor/projects/{project['id']}")
    assert r.status_code == 409 and r.json()["detail"]["reason"] == "open_in_editor"
    assert env.get(f"/editor/projects/{project['id']}").status_code == 200


def test_a_project_being_made_is_not_opened_or_deleted(env, monkeypatch):
    import threading

    from kitty_editor import build

    go = threading.Event()
    real = build.build_whole

    def slow(*args, **kwargs):
        go.wait(20)
        return real(*args, **kwargs)

    monkeypatch.setattr(build, "build_whole", slow)
    r = env.post("/editor/projects/open", {"kind": "video", "video_id": "vid1"})
    project_id = r.json()["project"]["id"]
    try:
        for call in (env.post(f"/editor/projects/{project_id}/open", {}), env.delete(f"/editor/projects/{project_id}")):
            assert call.status_code == 409 and call.json()["detail"]["reason"] == "building"
        same = env.post("/editor/projects/open", {"kind": "video", "video_id": "vid1"}).json()
        assert same["created"] is False and same["project"]["state"] == "building"   # never a second build
    finally:
        go.set()
    assert env.ready(project_id)["state"] == "ready"


def test_a_damaged_project_is_listed_as_failed(env):
    project = env.open({"kind": "video", "video_id": "vid1"})
    (env.data_dir / "editor" / "projects" / project["id"] / "kitty.json").write_text("{", encoding="utf-8")
    listed = env.get("/editor/projects").json()["projects"]
    assert [(p["id"], p["state"]) for p in listed] == [(project["id"], "failed")]
    assert env.delete(f"/editor/projects/{project['id']}").status_code == 200


# ---- exports come back --------------------------------------------------------------------------


def test_a_clips_export_is_taken_in_by_its_name_and_the_library_is_told(env):
    clip_id = env.clip()
    old = Path(env.row(clip_id)["path"])
    preview = env.data_dir / "previews" / f"clip_{clip_id}.mp4"
    preview.parent.mkdir(parents=True, exist_ok=True)
    preview.write_bytes(b"Quick edit's draft")
    project = env.open({"kind": "clip", "clip_id": clip_id})
    env.export(project, "Clip one.mp4")
    r = env.post(f"/editor/projects/{project['id']}/exports", {"file": "Clip one.mp4", "export_folder": None})
    assert r.status_code == 200, r.text
    target = env.data_dir / "clips" / "projects" / f"{project['id']}-1.mp4"
    assert r.json() == {"result": "replaced_clip", "clip_id": clip_id, "path": str(target),
                        "folder": str(target.parent), "file": target.name, "seconds": 12.5, "again": False}
    assert env.row(clip_id)["path"] == str(target) and not old.exists() and not preview.exists()

    clips = env.client.get("/videos/vid1/clips").json()
    assert clips[0]["editor_export"] == {"seconds": 12.5, "project_id": project["id"]}
    assert clips[0]["start_s"] == 10.0 and clips[0]["end_s"] == 55.0                 # still the AI's window
    after = env.get(f"/editor/projects/{project['id']}").json()
    assert after["clip_changed"] is False and after["exports"][0]["path"] == str(target)

    again = env.post(f"/editor/projects/{project['id']}/exports", {"file": "Clip one.mp4"})
    assert again.status_code == 200 and again.json()["again"] is True


def test_a_clip_the_editor_never_exported_has_no_new_key(env):
    env.clip()
    clips = env.client.get("/videos/vid1/clips").json()
    assert len(clips) == 1 and "editor_export" not in clips[0]


def test_an_export_of_a_clip_changed_meanwhile_is_kept_beside_it(env):
    clip_id = env.clip()
    project = env.open({"kind": "clip", "clip_id": clip_id})
    Path(env.row(clip_id)["path"]).write_bytes(b"Quick edit rendered it again, longer")
    env.export(project)
    r = env.post(f"/editor/projects/{project['id']}/exports", {"file": "My clip.mp4"})
    assert r.status_code == 200 and r.json()["result"] == "kept_beside" and r.json()["reason"] == "clip_changed"
    assert Path(r.json()["path"]).parent == env.data_dir / "clips" / "projects"
    assert "editor_export" not in env.client.get("/videos/vid1/clips").json()[0]


def test_a_whole_videos_export_goes_to_the_export_folder(env):
    from datetime import date

    project = env.open({"kind": "video", "video_id": "vid1"})
    folder = env.tmp / "exports"
    env.export(project, "Friday stream.mp4")
    r = env.post(f"/editor/projects/{project['id']}/exports",
                 {"file": "Friday stream.mp4", "export_folder": str(folder)})
    assert r.status_code == 200, r.text
    assert r.json()["result"] == "saved_to_folder"
    assert r.json()["file"] == f"Friday stream - {date.today().isoformat()}.mp4"
    # The Export sheet starts with the name the file will have (UX round).
    assert project["export_name"] == f"Friday stream - {date.today().isoformat()}"
    env.export(project, project["export_name"] + ".mp4")
    r = env.post(f"/editor/projects/{project['id']}/exports",
                 {"file": project["export_name"] + ".mp4", "export_folder": str(folder)})
    assert r.status_code == 200 and r.json()["file"] == f"Friday stream - {date.today().isoformat()}-2.mp4", r.text
    assert (folder / r.json()["file"]).read_bytes() == b"exported by the editor"

    env.export(project, "Friday stream.mp4")
    for bad in ("\\\\server\\share", "relative"):
        r = env.post(f"/editor/projects/{project['id']}/exports",
                     {"file": "Friday stream.mp4", "export_folder": bad})
        assert r.status_code == 400, r.text
    assert (Path(project["export_dir"]) / "Friday stream.mp4").is_file()            # nothing moved


def test_an_export_is_named_by_a_plain_file_name_in_the_projects_own_out_folder(env):
    project = env.open({"kind": "video", "video_id": "vid1"})
    other = env.open({"kind": "video", "video_id": "vid1", "shape": "9:16"})
    env.export(other, "theirs.mp4")
    for name in ("../kitty.json", "..\\..\\state.db", f"../../{other['id']}/out/theirs.mp4", "C:\\x.mp4",
                 "sub/x.mp4", "x.txt", ""):
        r = env.post(f"/editor/projects/{project['id']}/exports", {"file": name})
        assert r.status_code == 400, (name, r.text)
    for name in ("never made.mp4", "theirs.mp4"):                                    # another project's file
        assert env.post(f"/editor/projects/{project['id']}/exports", {"file": name}).status_code == 404
    assert env.post(f"/editor/projects/{project['id']}/exports", {"file": "x.mp4", "path": "C:/"}).status_code == 422
    assert (Path(other["export_dir"]) / "theirs.mp4").is_file()


def test_a_report_while_the_same_project_is_being_taken_in_is_told_busy(env, monkeypatch):
    """After waiting LOCK_WAIT seconds for the project's lock (K-D17)."""
    from kitty_editor import intake

    monkeypatch.setattr(intake, "LOCK_WAIT", 0.2)
    project = env.open({"kind": "video", "video_id": "vid1"})
    env.export(project, "x.mp4")
    with intake._project_lock(project["id"]):
        r = env.post(f"/editor/projects/{project['id']}/exports", {"file": "x.mp4"})
    assert r.status_code == 409 and r.json()["detail"]["reason"] == "busy"


def test_opening_a_project_takes_in_what_a_crash_left_behind(env):
    clip_id = env.clip()
    project = env.open({"kind": "clip", "clip_id": clip_id})
    left = env.export(project, "Clip one.mp4")
    os.utime(left, (1_000_000, 1_000_000))
    r = env.post(f"/editor/projects/{project['id']}/open", {})
    assert r.status_code == 200
    assert [a["result"] for a in r.json()["recovered"]] == ["replaced_clip"]
    assert Path(env.row(clip_id)["path"]).parent == env.data_dir / "clips" / "projects"
    assert env.post(f"/editor/projects/{project['id']}/open", {}).json()["recovered"] == []


def test_a_failed_take_in_changes_nothing_and_answers_in_plain_words(env, monkeypatch):
    clip_id = env.clip()
    old = env.row(clip_id)["path"]
    project = env.open({"kind": "clip", "clip_id": clip_id})
    made = env.export(project)

    def broken(self, clip_id, **fields):
        raise RuntimeError("the database is locked")

    monkeypatch.setattr(StateDB, "set_clip", broken)
    r = env.post(f"/editor/projects/{project['id']}/exports", {"file": "My clip.mp4"})
    assert r.status_code == 500 and r.json()["detail"] == {
        "message": "The export could not be saved as this clip's video."}
    assert env.row(clip_id)["path"] == old and Path(old).is_file() and made.is_file()


def test_an_export_named_with_dots_comes_back(env):
    """K-D6: any name the editor's export sheet writes is taken in, dots and
    all ("Wait... what happened.mp4", ".intro.mp4", a .webm); only the
    editor's own unfinished work files are refused."""
    clip_id = env.clip(title="Wait... what happened?")
    project = env.open({"kind": "clip", "clip_id": clip_id})
    assert project["export_name"] == "Wait. what happened"
    env.export(project, "Wait... what happened.mp4", content=b"typed with the dots")
    r = env.post(f"/editor/projects/{project['id']}/exports", {"file": "Wait... what happened.mp4"})
    assert r.status_code == 200 and r.json()["result"] == "replaced_clip", r.text
    assert Path(env.row(clip_id)["path"]).read_bytes() == b"typed with the dots"

    video = env.open({"kind": "video", "video_id": "vid1"})
    folder = env.tmp / "exports"
    # A long name with dots, as long as this tmp folder lets out/<name> stay
    # under Windows' 260-character path limit (K-D19): up to 120 characters.
    room = max(16, min(120, 255 - len(str(Path(video["export_dir"]))) - 1))
    long_name = "Wait... " + "x" * (room - len("Wait... ") - len(".mkv")) + ".mkv"
    for name in (".intro.mp4", "cut.final.webm", "Wait... what.mov", long_name):
        env.export(video, name)
        r = env.post(f"/editor/projects/{video['id']}/exports", {"file": name, "export_folder": str(folder)})
        assert r.status_code == 200 and r.json()["result"] == "saved_to_folder", (name, r.text)
        assert Path(r.json()["path"]).suffix == Path(name).suffix
    for work in (".Friday stream.concat-video.mp4", ".Friday stream.concat-muxed.mp4", "....mp4", ". .mp4"):
        env.export(video, work)
        r = env.post(f"/editor/projects/{video['id']}/exports", {"file": work})
        assert r.status_code == 400, (work, r.text)


def test_an_export_that_is_not_a_video_never_replaces_the_clip(env, monkeypatch):
    """K-D7: an empty, broken or sound-only export changes nothing, now or
    when the project is opened again; it stays in out/."""
    from kitty_editor import registry

    monkeypatch.setattr(registry, "probe_seconds", lambda path: 0.0)       # ffprobe finds no picture
    clip_id = env.clip()
    old = env.row(clip_id)["path"]
    project = env.open({"kind": "clip", "clip_id": clip_id})
    made = env.export(project, "My clip.mp4", content=b"")
    r = env.post(f"/editor/projects/{project['id']}/exports", {"file": "My clip.mp4"})
    assert r.status_code == 500 and r.json()["detail"] == {"message": "The export isn't a video Clips Kitty can play."}
    assert env.row(clip_id)["path"] == old and Path(old).is_file() and made.is_file()
    os.utime(made, (1_000_000, 1_000_000))
    assert env.post(f"/editor/projects/{project['id']}/open", {}).json()["recovered"] == []
    assert env.row(clip_id)["path"] == old and Path(old).is_file() and made.is_file()
    assert not (env.data_dir / "clips" / "projects").exists() or not any((env.data_dir / "clips" / "projects").iterdir())


def test_a_whole_video_export_recovered_on_reopen_goes_to_the_export_folder(env):
    """K-D10: the app sends the export folder from Settings when it opens a
    project again, so a recovered export lands there, not in Downloads."""
    project = env.open({"kind": "video", "video_id": "vid1"})
    left = env.export(project, "Friday stream.mp4")
    os.utime(left, (1_000_000, 1_000_000))
    folder = env.tmp / "chosen"
    r = env.post(f"/editor/projects/{project['id']}/open", {"export_folder": str(folder)})
    assert r.status_code == 200, r.text
    [recovered] = r.json()["recovered"]
    assert recovered["result"] == "saved_to_folder" and Path(recovered["path"]).parent == folder.resolve()
    assert env.post(f"/editor/projects/{project['id']}/open", {"export_folder": 7}).status_code == 422


# ---- the clip's video in the project ------------------------------------------------------------


def test_the_clips_video_is_rendered_without_captions_edits_watermark_or_end_card(tmp_path, monkeypatch):
    """The real render_base: what it asks the render for. The clip's own
    options, less captions, less its edit block, less the watermark; the end
    card off; the whole window."""
    pytest.importorskip("numpy")      # core.pipeline imports the analysis stack
    pytest.importorskip("yaml")
    from core import pipeline
    from kitty_editor import build
    from main import BUNDLED_CONFIG, load_config

    data_dir = tmp_path / "data"
    (data_dir / "downloads").mkdir(parents=True)
    (data_dir / "downloads" / "vid1.mp4").write_bytes(b"x")
    config = load_config(BUNDLED_CONFIG)
    config["clips"]["outro"] = True
    asked = {}

    def render(source, candidate, segments, clip_dir, cfg, opts, *rest):
        asked.update(source=source, start=candidate.start, end=candidate.end, opts=opts, outro=cfg["clips"]["outro"])
        return clip_dir / "clip.mp4", ""

    monkeypatch.setattr(pipeline, "_render_files", render)
    row = {"id": 1, "video_id": "vid1", "start_s": 10.0, "end_s": 55.0, "score": 80, "hook": "h",
           "render_opts": json.dumps({"crop": "center", "filter": "warm", "captions": True,
                                      "caption_lines": [{"start": 0, "end": 1, "text": "x"}],
                                      "caption_style": {"color": "#FFE600"},
                                      "edit": {"keep": [[0, 5]], "hook": {"text": "h"}, "music": {"path": "m.mp3"}},
                                      "watermark": {"type": "text", "text": "@me"}})}
    out = build.render_base(row, config, data_dir, tmp_path / "work")
    assert out == tmp_path / "work" / "clip.mp4"
    assert (asked["start"], asked["end"]) == (10.0, 55.0) and asked["outro"] is False
    assert asked["opts"]["captions"] is False and asked["opts"]["watermark"] is None
    assert "edit" not in asked["opts"] and "caption_lines" not in asked["opts"]
    assert asked["opts"]["crop"] == "center" and asked["opts"]["filter"] == "warm"   # the AI's framing and colour
    assert config["clips"]["outro"] is True                                           # the app's own setting untouched
    (data_dir / "downloads" / "vid1.mp4").unlink()
    assert build.render_base(row, config, data_dir, tmp_path / "work") is None


# ---- K2 fix round (K-D15 to K-D23) --------------------------------------------------------------


def _second_row(env, path) -> int:
    """Clip B: another AI window with the same whole-second start and end,
    so the same clip_<start>-<end>.mp4 (core.remake.file_in_use)."""
    cur = env.db.conn.execute(
        "INSERT INTO clips (video_id, start_s, end_s, score, hook, path, title, render_opts, created_at)"
        " VALUES ('vid1', 10.7, 55.4, 70, 'h', ?, 'Clip two', '', ?)", (str(path), NOW))
    env.db.conn.commit()
    return cur.lastrowid


def test_an_export_never_deletes_a_file_another_clip_still_plays(env):
    """K-D16: taking an editor export in as clip A's video re-points A and
    keeps the file clip B still plays (as DELETE /clips/{id} keeps it)."""
    a = env.clip(start=10.2, end=55.1)
    shared = Path(env.row(a)["path"])
    b = _second_row(env, shared)
    project = env.open({"kind": "clip", "clip_id": a})
    env.export(project, "A.mp4", b"edited")
    r = env.post(f"/editor/projects/{project['id']}/exports", {"file": "A.mp4"})
    assert r.status_code == 200 and r.json()["result"] == "replaced_clip", r.text
    assert Path(env.row(a)["path"]).read_bytes() == b"edited"
    assert env.row(b)["path"] == str(shared) and shared.read_bytes() == b"the clip as the AI made it"

    # Recovered on a reopen, the same: B's export, with A back on the shared file.
    env.db.set_clip(a, path=str(shared))
    other = env.open({"kind": "clip", "clip_id": b})
    left = env.export(other, "B.mp4", b"edited too")
    os.utime(left, (1_000_000, 1_000_000))
    r = env.post(f"/editor/projects/{other['id']}/open", {})
    assert [x["result"] for x in r.json()["recovered"]] == ["replaced_clip"]
    assert Path(env.row(b)["path"]).read_bytes() == b"edited too"
    assert shared.is_file() and env.row(a)["path"] == str(shared)

    # Played by no other clip: the old file goes, as before.
    c = env.clip(name="clip_00100-00130.mp4", start=100.0, end=130.0)
    alone = Path(env.row(c)["path"])
    third = env.open({"kind": "clip", "clip_id": c})
    env.export(third, "C.mp4", b"edited three")
    assert env.post(f"/editor/projects/{third['id']}/exports", {"file": "C.mp4"}).status_code == 200
    assert not alone.exists()


def test_a_file_a_run_holds_is_never_deleted_by_an_export(env):
    """K-D16: a Make clips again run holds files (core/in_use.py); an
    export's take-in keeps those too."""
    from core import in_use

    clip_id = env.clip()
    old = Path(env.row(clip_id)["path"])
    project = env.open({"kind": "clip", "clip_id": clip_id})
    env.export(project, "A.mp4", b"edited")
    holder = object()
    in_use.hold_files(holder, [old])
    try:
        r = env.post(f"/editor/projects/{project['id']}/exports", {"file": "A.mp4"})
    finally:
        in_use.let_go_of_files(holder)
    assert r.status_code == 200 and r.json()["result"] == "replaced_clip", r.text
    assert old.is_file()


def test_a_report_while_a_reopen_takes_the_export_in_is_answered_as_taken(env, monkeypatch):
    """K-D17: the app reports an export while a reopen's recover() is taking
    the same file in: the report waits and is answered with what recover
    did ("again"), never busy or failed, and the file is taken in once."""
    import threading

    from kitty_editor import intake, registry

    clip_id = env.clip()
    project = env.open({"kind": "clip", "clip_id": clip_id})
    left = env.export(project, "Clip one.mp4", b"exported by the editor")
    os.utime(left, (1_000_000, 1_000_000))
    entered = threading.Event()

    def slow_probe(path):
        entered.set()
        time.sleep(0.6)
        return 12.5

    monkeypatch.setattr(registry, "probe_seconds", slow_probe)
    answers = {}
    reopen = threading.Thread(target=lambda: answers.setdefault(
        "reopen", env.post(f"/editor/projects/{project['id']}/open", {})))
    reopen.start()
    assert entered.wait(10)
    r = env.post(f"/editor/projects/{project['id']}/exports", {"file": "Clip one.mp4"})
    reopen.join(20)
    assert r.status_code == 200, r.text
    assert r.json()["result"] == "replaced_clip" and r.json()["again"] is True
    assert [x["result"] for x in answers["reopen"].json()["recovered"]] == ["replaced_clip"]
    taken = sorted(f.name for f in (env.data_dir / "clips" / "projects").iterdir())
    assert taken == [f"{project['id']}-1.mp4"]                                   # once
    assert len(env.get(f"/editor/projects/{project['id']}").json()["exports"]) == 1
    assert intake.LOCK_WAIT >= 30                                                 # the route's real wait


def test_deleting_a_whole_video_project_keeps_the_librarys_download(env):
    """K-D20: the project's video is a link to the download (or the download
    itself); deleting the project never removes the Library's file."""
    source = env.data_dir / "downloads" / "vid1.mp4"
    project = env.open({"kind": "video", "video_id": "vid1", "shape": "9:16"})
    assert Path(project["video_file"]).name in ("Friday stream.mp4", "vid1.mp4")
    assert env.delete(f"/editor/projects/{project['id']}").json() == {"deleted": True}
    assert source.read_bytes() == b"not read: the render is stood in for"


def test_a_clip_titled_like_the_projects_other_media_keeps_its_own_name(env):
    clip_id = env.clip({"edit": {"keep": [[0, 5]]}}, title="Music")
    project = env.open({"kind": "clip", "clip_id": clip_id})
    assert Path(project["video_file"]).name == "Music video.mp4"


def test_a_project_made_before_its_video_was_named_still_opens(env):
    """A clip project made before K-D20 plays media/base.mp4 and has no
    video_file: it is not shown as missing, and is when base.mp4 goes."""
    from kitty_editor import registry

    project = env.open({"kind": "clip", "clip_id": env.clip()})
    named = Path(project["video_file"])
    base = named.with_name("base.mp4")
    named.rename(base)
    registry.update(env.data_dir, project["id"], lambda m: m.pop("video_file"))
    assert env.get(f"/editor/projects/{project['id']}").json()["media_missing"] is False
    base.unlink()
    assert env.get(f"/editor/projects/{project['id']}").json()["media_missing"] is True


# ---- the first open's speed (K-D24 to K-D26) ----------------------------------------------------


def test_a_clips_video_is_made_once_and_a_new_project_of_the_clip_reuses_it(env):
    clip_id = env.clip({"crop": "track"})
    first = env.open({"kind": "clip", "clip_id": clip_id})
    fresh = env.post("/editor/projects/open", {"kind": "clip", "clip_id": clip_id, "fresh": True}).json()
    second = env.ready(fresh["project"]["id"])
    assert len(env.rendered) == 1                                                    # made once
    assert second["base_key"] == first["base_key"] and second["video_file"] != first["video_file"]
    assert Path(second["video_file"]).read_bytes() == Path(first["video_file"]).read_bytes()
    # Its own copy, never a link to the other project's: deleting one leaves the other whole.
    assert env.delete(f"/editor/projects/{first['id']}").status_code == 200
    assert Path(second["video_file"]).is_file()


def test_the_clips_video_is_made_again_when_its_window_framing_or_file_changed(env):
    clip_id = env.clip({"crop": "track", "caption_style": {"color": "#FFFFFF"}})

    def fresh() -> dict:
        r = env.post("/editor/projects/open", {"kind": "clip", "clip_id": clip_id, "fresh": True})
        return env.ready(r.json()["project"]["id"])

    env.open({"kind": "clip", "clip_id": clip_id})
    env.db.conn.execute("UPDATE clips SET render_opts = ? WHERE id = ?",
                        (json.dumps({"crop": "track", "caption_style": {"color": "#FFE600"}}), clip_id))
    env.db.conn.commit()
    fresh()
    assert len(env.rendered) == 1                                   # the captions' colour: the same video
    env.db.conn.execute("UPDATE clips SET render_opts = ? WHERE id = ?", (json.dumps({"crop": "center"}), clip_id))
    env.db.conn.commit()
    fresh()
    assert len(env.rendered) == 2                                   # the framing
    Path(env.row(clip_id)["path"]).write_bytes(b"Quick edit rendered it again")
    fresh()
    assert len(env.rendered) == 3                                   # the clip's file
    env.db.conn.execute("UPDATE clips SET end_s = 56.0 WHERE id = ?", (clip_id,))
    env.db.conn.commit()
    fresh()
    assert len(env.rendered) == 4                                   # the window


def test_a_failed_render_is_reported_as_failed_and_made_next_time(env, monkeypatch):
    from kitty_editor import build

    working = build.render_base

    def fails(row, config, data_dir, work_dir, progress=None):
        raise RuntimeError("ffmpeg base render failed: no space left")

    monkeypatch.setattr(build, "render_base", fails)
    clip_id = env.clip()
    failed = env.open({"kind": "clip", "clip_id": clip_id}, want="failed")
    assert failed["message"] == "This project could not be made." and "ffmpeg" not in failed["message"]
    assert not failed.get("base_key") and failed["progress"] < 1.0
    monkeypatch.setattr(build, "render_base", working)
    r = env.post("/editor/projects/open", {"kind": "clip", "clip_id": clip_id})
    assert r.json()["project"]["id"] == failed["id"]
    assert env.ready(failed["id"])["base_key"] and len(env.rendered) == 1


def test_a_build_that_failed_after_the_clips_video_was_made_reuses_it(env, monkeypatch):
    clip_id = env.clip()
    monkeypatch.setenv("FAKE_KITTY_EDITOR_FAIL", "media_import")
    failed = env.open({"kind": "clip", "clip_id": clip_id}, want="failed")
    assert failed["base_key"] and Path(failed["video_file"]).is_file()
    monkeypatch.delenv("FAKE_KITTY_EDITOR_FAIL")
    env.post("/editor/projects/open", {"kind": "clip", "clip_id": clip_id})
    rebuilt = env.ready(failed["id"])
    assert len(env.rendered) == 1 and rebuilt["video_file"] == failed["video_file"]


def test_the_bar_follows_the_render_never_back_and_never_whole_before_it_is_ready(env, monkeypatch):
    from kitty_editor import build, registry

    seen = []

    def render(row, config, data_dir, work_dir, progress=None):
        pid = next(p["id"] for p in registry.list_all(data_dir) if p["state"] == "building")
        for value in (0.5, 0.2, 1.7):
            time.sleep(0.55)                    # said at most twice a second
            progress(value)
            seen.append(registry.load(data_dir, pid)["progress"])
        work_dir.mkdir(parents=True, exist_ok=True)
        (work_dir / "b.mp4").write_bytes(b"framed")
        return work_dir / "b.mp4"

    monkeypatch.setattr(build, "render_base", render)
    project = env.open({"kind": "clip", "clip_id": env.clip()})
    assert seen == [0.425, 0.425, 0.8]          # 5% + 75% of the render's own share; never back
    assert project["progress"] == 1.0
