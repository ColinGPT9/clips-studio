"""Editor suite step 5: the project routes (server/projects_api.py), through
FastAPI's TestClient on a temporary database. Nothing is started and nothing
listens on a port.

- Every route needs the session header and an Origin of this PC's own.
- A project is made from a clip (once) or empty, read, changed with
  operations, undone, redone, listed and deleted.
- A stale change answers 409 with the revision the project is at.
- A refused operation names itself and its field, and changes nothing.
"""

import json

import pytest

from core.state import StateDB

NOW = "2026-11-05T09:00:00"
S = 1_000_000


class Env:
    def __init__(self, client, db, session, data_dir):
        self.client, self.db, self.data_dir = client, db, data_dir
        self.ok = {"X-Clips-Kitty-Session": session}

    def post(self, path, body=None, **headers):
        return self.client.post(path, json=body if body is not None else {}, headers={**self.ok, **headers})

    def get(self, path, **headers):
        return self.client.get(path, headers={**self.ok, **headers})

    def ops(self, project_id, revision, *op_list, **more):
        return self.post(f"/projects/{project_id}/ops", {"expected_revision": revision, "ops": list(op_list), **more})

    def clip(self, start=60.0, end=90.0, render_opts=None) -> int:
        cur = self.db.conn.execute(
            "INSERT INTO clips (video_id, start_s, end_s, score, hook, path, title, render_opts, created_at)"
            " VALUES ('vid1', ?, ?, 80, 'h', 'clip.mp4', 'Clip one', ?, ?)",
            (start, end, json.dumps(render_opts) if render_opts is not None else "", NOW))
        self.db.conn.commit()
        return cur.lastrowid


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
    # What this PC can encode, answered here so nothing is probed.
    monkeypatch.setattr(encoding, "encoder_for",
                        lambda codec, pref="auto": encoding.Encoder(codec, "cpu", "libx264")
                        if codec == "h264" and pref != "gpu" else None)
    data_dir = tmp_path / "data"
    config = load_config(BUNDLED_CONFIG)
    config["paths"]["data_dir"] = str(data_dir)
    app = create_app(config, tmp_path / "settings.yaml")
    db = StateDB(data_dir / "state.db")
    db.conn.execute("INSERT INTO videos (video_id, title, status, duration, created_at, updated_at)"
                    " VALUES ('vid1', 'Friday stream', 'done', 3600, ?, ?)", (NOW, NOW))
    db.conn.commit()
    client = TestClient(app, base_url="http://127.0.0.1")
    yield Env(client, db, session.path(data_dir).read_text(encoding="utf-8").strip(), data_dir)
    db.conn.close()


def _empty(env, **body) -> dict:
    r = env.post("/projects", body)
    assert r.status_code == 200, r.text
    return r.json()


def _with_clips(env) -> dict:
    made = _empty(env)
    r = env.ops(made["id"], 0, {"op": "add_media", "video_id": "vid1"},
                {"op": "insert_clip", "media_id": "m1", "source_in_us": 10 * S, "source_out_us": 20 * S},
                {"op": "insert_clip", "media_id": "m1", "source_in_us": 40 * S, "source_out_us": 50 * S})
    assert r.status_code == 200, r.text
    return r.json()


# ---- who may ask --------------------------------------------------------------------------------


def _every_route(project_id: str, clip_id: int) -> list[tuple[str, str, dict | None]]:
    return [("POST", f"/clips/{clip_id}/project", {}), ("POST", "/projects", {}),
            ("GET", f"/projects/{project_id}", None),
            ("POST", f"/projects/{project_id}/ops", {"expected_revision": 0, "ops": [{"op": "add_track", "kind": "text"}]}),
            ("POST", f"/projects/{project_id}/undo", {"expected_revision": 0}),
            ("POST", f"/projects/{project_id}/redo", {"expected_revision": 0}),
            ("GET", f"/projects/{project_id}/revisions", None), ("DELETE", f"/projects/{project_id}", None)]


def test_every_route_needs_the_session_header(env):
    made, clip_id = _empty(env), env.clip()
    for headers in ({}, {"X-Clips-Kitty-Session": "not-the-secret-at-all"}, {"X-Clips-Kitty-Session": ""}):
        for method, path, body in _every_route(made["id"], clip_id):
            r = env.client.request(method, path, json=body, headers=headers)
            assert r.status_code == 403, (method, path, r.text)
            assert "X-Clips-Kitty-Session" in r.json()["detail"]
    assert env.get(f"/projects/{made['id']}").json()["revision"] == 0           # nothing was changed
    assert env.db.conn.execute("SELECT COUNT(*) FROM projects").fetchone()[0] == 1


@pytest.mark.parametrize("origin", ["https://example.com", "http://evil.test:8765", "http://127.0.0.1:3000",
                                    "http://localhost:8080", "https://127.0.0.1", "http://[::1]:8765",
                                    "http://127.0.0.1.evil.test", "http://localhost.evil.test:5173",
                                    "https://127.0.0.1@evil.test", "ftp://127.0.0.1", "garbage",
                                    "file:///C:/page.html", "chrome-extension://abc"])
def test_a_foreign_origin_is_refused_even_with_the_session(env, origin):
    made, clip_id = _empty(env), env.clip()
    for method, path, body in _every_route(made["id"], clip_id):
        r = env.client.request(method, path, json=body, headers={**env.ok, "Origin": origin})
        assert r.status_code == 403, (method, path, origin)
        assert "Clips Kitty app" in r.json()["detail"]
    assert env.get(f"/projects/{made['id']}").json()["revision"] == 0
    assert env.db.conn.execute("SELECT COUNT(*) FROM projects").fetchone()[0] == 1


@pytest.mark.parametrize("origin", ["http://localhost:5173", "http://127.0.0.1:5173", "http://127.0.0.1",
                                    "file://", "null"])
def test_the_apps_own_origins_may_ask(env, origin):
    """The dev server, the engine's own address (the test client's is
    http://127.0.0.1), and what a window that loaded a file sends."""
    made = _empty(env)
    assert env.get(f"/projects/{made['id']}", Origin=origin).status_code == 200
    r = env.post(f"/projects/{made['id']}/ops", {"expected_revision": 0, "ops": [{"op": "add_track", "kind": "text"}]},
                 Origin=origin)
    assert r.status_code == 200 and r.json()["revision"] == 1


def test_the_origin_rule():
    pytest.importorskip("fastapi")
    from server.projects_api import local_origin

    host = "127.0.0.1:8765"
    for mine in (None, "", "file://", "null", "http://LOCALHOST:5173", "http://127.0.0.1:5173",
                 "http://127.0.0.1:8765"):
        assert local_origin(mine, host), mine
    for theirs in ("http://example.com", "http://127.0.0.2", "file:///C:/x.html", "localhost:5173",
                   "http://localhost:99999999", "http://[bad", "chrome-extension://abc", "http://localhost",
                   "https://127.0.0.1:8765", "http://127.0.0.1:8766", "http://localhost:8765",
                   "http://user@127.0.0.1:8765", "https://localhost:5173"):
        assert not local_origin(theirs, host), theirs
    assert local_origin("http://localhost:8765", "localhost:8765")        # the engine asked for by its other name
    assert not local_origin("http://127.0.0.1:8765", None)                # no Host to compare with: refused
    assert not local_origin("http://127.0.0.1:8765", "")


# ---- making and reading -------------------------------------------------------------------------


def test_an_empty_project_in_each_canvas(env):
    sizes = {"9:16": (1080, 1920), "16:9": (1920, 1080), "1:1": (1080, 1080), "4:5": (1080, 1350)}
    for preset, (width, height) in sizes.items():
        made = _empty(env, canvas=preset, name=f"  My {preset} ")
        canvas = made["document"]["canvas"]
        assert (canvas["width"], canvas["height"]) == (width, height) and canvas["fps"] == {"num": 30, "den": 1}
        assert made["revision"] == 0 and made["created"] is True and made["clip_id"] is None
        assert made["name"] == f"My {preset}"
        assert [t["id"] for t in made["document"]["tracks"]] == ["V1", "T1", "A1", "A2"]
    custom = _empty(env, canvas={"width": 1280, "height": 720}, fps=29.97)
    assert custom["document"]["canvas"] == {"width": 1280, "height": 720, "fps": {"num": 30000, "den": 1001}}
    assert _empty(env)["document"]["canvas"]["height"] == 1920            # 9:16 unless said otherwise
    for bad in ({"canvas": "3:2"}, {"canvas": {"width": 1281, "height": 720}}, {"canvas": {"width": 8, "height": 720}},
                {"fps": 0}, {"fps": "fast"}, {"name": "x" * 121}):
        r = env.post("/projects", bad)
        assert r.status_code == 400, (bad, r.text)
    assert env.post("/projects", {"canvas": {"width": 1280}}).status_code == 422
    assert env.post("/projects", {"path": "C:/x"}).status_code == 422


def test_a_project_from_a_clip_once(env):
    opts = {"edit": {"keep": [[0.0, 10.0], [15.0, 30.0]], "hook": {"text": "Watch", "seconds": 3}}, "crop": "center"}
    clip_id = env.clip(render_opts=opts)
    r = env.post(f"/clips/{clip_id}/project")
    assert r.status_code == 200, r.text
    made = r.json()
    assert made["created"] is True and made["clip_id"] == clip_id and made["video_id"] == "vid1"
    assert made["name"] == "Clip one" and made["revision"] == 0
    doc = made["document"]
    assert [(i["source_in_us"], i["source_out_us"]) for i in doc["tracks"][0]["items"]] == [(60 * S, 70 * S),
                                                                                           (75 * S, 90 * S)]
    assert doc["media"]["m1"]["duration_us"] == 3600 * S
    again = env.post(f"/clips/{clip_id}/project").json()
    assert again["created"] is False and again["id"] == made["id"]
    assert env.db.conn.execute("SELECT COUNT(*) FROM projects").fetchone()[0] == 1
    # The clip itself is as it was.
    row = env.db.get_clip(clip_id)
    assert json.loads(row["render_opts"]) == opts and not row["edited_at"]
    from video_editor.project import projection

    assert projection.to_clip(doc) == {"branch": "A", "start": 60.0, "end": 90.0, "render_opts": opts}
    assert env.post("/clips/999999/project").status_code == 404
    assert env.post(f"/clips/{2**70}/project").status_code == 404
    plain = env.post(f"/clips/{env.clip(100.0, 130.0)}/project").json()         # render options never saved
    assert projection.to_clip(plain["document"])["render_opts"] == {}


def test_reading_a_project(env):
    made = _with_clips(env)
    got = env.get(f"/projects/{made['id']}").json()
    assert got["revision"] == 1 and got["can_undo"] is True and got["can_redo"] is False and got["read_only"] is False
    assert got["document"] == made["document"]
    assert env.get("/projects/nope").status_code == 404
    assert env.get("/projects/nope/revisions").status_code == 404


# ---- changing -----------------------------------------------------------------------------------


def test_ops_undo_redo_and_the_history(env):
    made = _with_clips(env)
    pid = made["id"]
    assert made["created"] == [{"kind": "media", "id": "m1"}, {"kind": "item", "id": "i2"}, {"kind": "item", "id": "i3"}]
    r = env.ops(pid, 1, {"op": "split_item", "item_id": "i2", "at_us": 4 * S}, label="Cut")
    out = r.json()
    assert r.status_code == 200 and out["revision"] == 2 and out["changed"] and not out["dry_run"]
    assert [i["id"] for i in out["document"]["tracks"][0]["items"]] == ["i2", "i4", "i3"]
    assert out["inverse"][0]["op"] == "restore" and out["warnings"] == []
    back = env.post(f"/projects/{pid}/undo", {"expected_revision": 2}).json()
    assert back["revision"] == 3 and back["label"] == "Cut" and back["can_redo"] is True
    assert back["document"] == made["document"]
    again = env.post(f"/projects/{pid}/redo", {"expected_revision": 3}).json()
    assert again["revision"] == 4 and again["document"] == out["document"]
    r = env.post(f"/projects/{pid}/redo", {"expected_revision": 4})
    assert r.status_code == 400 and "nothing to redo" in r.json()["detail"]
    listed = env.get(f"/projects/{pid}/revisions").json()
    assert listed["revision"] == 4 and [r["seq"] for r in listed["revisions"]] == [2, 1, 0]
    assert listed["revisions"][0]["label"] == "Cut" and listed["revisions"][0]["current"] is True
    assert listed["revisions"][0]["ops"] == [{"op": "split_item", "item_id": "i2", "at_us": 4 * S}]
    assert len(env.get(f"/projects/{pid}/revisions?limit=1").json()["revisions"]) == 1


def test_a_stale_change_answers_409_with_the_current_revision(env):
    made = _with_clips(env)
    pid = made["id"]
    before = env.get(f"/projects/{pid}").json()
    for call in (lambda: env.ops(pid, 0, {"op": "add_track", "kind": "text"}),
                 lambda: env.ops(pid, 7, {"op": "add_track", "kind": "text"}),
                 lambda: env.ops(pid, 0, {"op": "add_track", "kind": "text"}, dry_run=True),
                 lambda: env.post(f"/projects/{pid}/undo", {"expected_revision": 0}),
                 lambda: env.post(f"/projects/{pid}/redo", {"expected_revision": 2})):
        r = call()
        assert r.status_code == 409, r.text
        assert r.json()["detail"]["current_revision"] == 1
        assert "changed since" in r.json()["detail"]["message"]
    assert env.get(f"/projects/{pid}").json() == before
    assert env.ops(pid, 1, {"op": "add_track", "kind": "text"}).json()["revision"] == 2


def test_a_dry_run_saves_nothing(env):
    made = _with_clips(env)
    r = env.ops(made["id"], 1, {"op": "delete_items", "item_ids": ["i2"]}, dry_run=True)
    out = r.json()
    assert r.status_code == 200 and out["dry_run"] and out["changed"] and out["revision"] == 1
    assert len(out["document"]["tracks"][0]["items"]) == 1
    assert env.get(f"/projects/{made['id']}").json()["document"] == made["document"]
    assert len(env.get(f"/projects/{made['id']}/revisions").json()["revisions"]) == 2


def test_a_refused_operation_names_itself_and_changes_nothing(env):
    made = _with_clips(env)
    pid = made["id"]
    good = {"op": "set_project_props", "name": "Fine"}
    for bad, op, field in (
            ({"op": "split_item", "item_id": "i9", "at_us": 5}, "split_item", "item_id"),
            ({"op": "set_item_props", "item_id": "i2", "speed": 9}, "set_item_props", "speed"),
            ({"op": "add_media", "path": "C:/Users/someone/video.mp4"}, "add_media", "path"),
            ({"op": "add_media", "video_id": "../../etc/passwd"}, "add_media", "video_id"),
            ({"op": "add_media", "asset": "C:\\Windows\\win.ini"}, "add_media", "asset"),
            ({"op": "add_media", "library": "no-such-track"}, "add_media", "library"),
            ({"op": "restore", "patch": ["=", {}]}, "restore", None),
            ({"op": "format_disk"}, "format_disk", "op"),
            ("nonsense", "ops", "op")):
        r = env.ops(pid, 1, good, bad, good)
        assert r.status_code == 400, (bad, r.text)
        detail = r.json()["detail"]
        assert (detail["op"], detail["field"]) == (op, field), detail
        assert detail["message"]
    r = env.ops(pid, 1, *[good] * 501)
    assert r.status_code == 400 and "at most 500" in r.json()["detail"]["message"]
    for body in ({"ops": []}, {"expected_revision": 1}, {"expected_revision": "one", "ops": []},
                 {"expected_revision": 1, "ops": {}}, {"expected_revision": 1, "ops": [], "extra": 1}):
        assert env.post(f"/projects/{pid}/ops", body).status_code == 422
    got = env.get(f"/projects/{pid}").json()
    assert got["revision"] == 1 and got["document"] == made["document"]


def test_the_operations_ask_the_real_app(env):
    """Through the real environment (video_editor/project/env.py): a library
    video by its id, a free track by its id, the caption fonts, the filters
    and what this PC can encode."""
    pytest.importorskip("numpy")
    from video import audio_library, captions, filters

    made = _with_clips(env)
    pid = made["id"]
    tracks = audio_library.items()
    track = tracks[0] if tracks else None
    font = captions.FONTS[0]
    wanted = [{"op": "add_text", "content": "Hi", "start_us": 0, "duration_us": S, "font": font.lower()},
              {"op": "set_export_preset", "preset": {"kind": "custom", "resolution": "720p"}},
              {"op": "set_captions", "style": {"font": font, "font_size": 80}}]
    r = env.ops(pid, 1, *wanted)
    assert r.status_code == 200, r.text
    doc = r.json()["document"]
    assert doc["tracks"][1]["items"][0]["text"]["font"] == font
    assert doc["export"]["preset"]["resolution"] == "720p" and doc["captions"]["style"]["font"] == font
    name = next(n for n in ("vibrant", "mono", "warm", "cinematic") if filters.is_valid(n))
    assert env.ops(pid, 2, {"op": "set_item_props", "item_id": "i2", "color": {"filter": name}}).status_code == 200
    for bad, field in (({"op": "set_export_preset", "preset": {"kind": "custom", "codec": "av1"}}, "preset"),
                       ({"op": "set_item_props", "item_id": "i2", "color": {"filter": "no-such-filter"}}, "color"),
                       ({"op": "add_text", "content": "Hi", "start_us": 5 * S, "duration_us": S, "font": "Nope Sans"},
                        "font")):
        r = env.ops(pid, 3, bad)
        assert r.status_code == 400 and r.json()["detail"]["field"] == field, r.text
    if track is not None:
        r = env.ops(pid, 3, {"op": "add_media", "library": track["id"]})
        assert r.status_code == 200 and r.json()["document"]["media"]["m5"]["source"]["type"] == "library"


def test_a_newer_versions_project_opens_read_only(env):
    made = _with_clips(env)
    pid = made["id"]
    doc = made["document"]
    doc["schema"] = 2
    env.db.conn.execute("UPDATE projects SET doc = ?, doc_schema = 2 WHERE id = ?", (json.dumps(doc), pid))
    env.db.conn.commit()
    got = env.get(f"/projects/{pid}").json()
    assert got["read_only"] is True and got["can_undo"] is False and got["document"]["schema"] == 2
    for r in (env.ops(pid, 1, {"op": "add_track", "kind": "text"}),
              env.post(f"/projects/{pid}/undo", {"expected_revision": 1})):
        assert r.status_code == 423 and "newer version of Clips Kitty" in r.json()["detail"]


def test_delete(env):
    made = _with_clips(env)
    r = env.client.delete(f"/projects/{made['id']}", headers=env.ok)
    assert r.status_code == 200 and r.json() == {"deleted": made["id"]}
    assert env.get(f"/projects/{made['id']}").status_code == 404
    assert env.client.delete(f"/projects/{made['id']}", headers=env.ok).status_code == 404
    assert env.db.conn.execute("SELECT COUNT(*) FROM project_revisions").fetchone()[0] == 0


def test_a_clip_deleted_or_re_rendered_under_its_project(env):
    clip_id = env.clip()
    made = env.post(f"/clips/{clip_id}/project").json()
    # A re-render: a new row, and the project follows it.
    env.db.conn.execute("DELETE FROM clips WHERE id = ?", (clip_id,))
    env.db.conn.commit()
    new_id = env.clip(61.0, 90.0)
    env.db.follow_clip_id(clip_id, new_id)
    assert env.get(f"/projects/{made['id']}").json()["clip_id"] == new_id
    assert env.post(f"/clips/{new_id}/project").json()["id"] == made["id"]
    # The clip deleted (the Library's delete route): the project still opens and still changes.
    assert env.client.delete(f"/clips/{new_id}").status_code == 200
    got = env.get(f"/projects/{made['id']}").json()
    assert got["clip_id"] is None
    assert env.ops(made["id"], 0, {"op": "add_track", "kind": "text"}).json()["revision"] == 1


def _raw(env, path: str, body) -> object:
    """A body sent as Python writes JSON: NaN and Infinity as those words,
    half of a character as its escape. A script can send these; the app's
    own window can't."""
    return env.client.post(path, content=json.dumps(body).encode("ascii"),
                           headers={**env.ok, "content-type": "application/json"})


def test_expected_revision_is_a_whole_number_as_json_has_it(env):
    made = _with_clips(env)
    pid = made["id"]
    before = env.get(f"/projects/{pid}").json()
    assert before["revision"] == 1
    for bad in (True, "1", 1.0, None, [1]):
        for path, body in ((f"/projects/{pid}/ops", {"expected_revision": bad, "ops": [{"op": "add_track",
                                                                                         "kind": "text"}]}),
                           (f"/projects/{pid}/undo", {"expected_revision": bad}),
                           (f"/projects/{pid}/redo", {"expected_revision": bad})):
            assert env.post(path, body).status_code == 422, (bad, path)
    assert env.get(f"/projects/{pid}").json() == before
    assert env.ops(pid, 1, {"op": "add_track", "kind": "text"}).json()["revision"] == 2


def test_what_cant_be_saved_answers_400_never_500(env):
    made = _with_clips(env)
    pid = made["id"]
    before = env.get(f"/projects/{pid}").json()
    half, nan, inf = "\ud83d", float("nan"), float("inf")
    line = {"in_us": 0, "out_us": S, "text": "one"}
    for bad, field in (
            ({"op": "set_item_props", "item_id": "i2", "extra": {"note": "cut " + half}}, "extra"),
            ({"op": "set_item_props", "item_id": "i2", "extra": {"n": nan}}, "extra"),
            ({"op": "set_branding", "watermark": {"type": "text", "text": "@Me", half: 1}}, "watermark"),
            ({"op": "set_captions", "lines": [{**line, "words": [{"w": half}]}]}, "lines"),
            ({"op": "set_captions", "lines": [{**line, "w": inf}]}, "lines"),
            ({"op": "set_audio_mutes", "mutes": [{"in_us": 0, "out_us": S, "x": nan}]}, "mutes"),
            ({"op": "add_text", "content": "Hi " + half, "start_us": 0, "duration_us": S}, "content"),
            ({"op": "set_project_props", "name": "Name " + half}, "name"),
            ({"op": "set_canvas", "fps": inf}, "fps")):
        for dry_run in (False, True):
            r = _raw(env, f"/projects/{pid}/ops", {"expected_revision": 1, "ops": [bad], "dry_run": dry_run})
            assert r.status_code == 400, (bad, r.status_code, r.text)
            detail = r.json()["detail"]
            assert (detail["op"], detail["field"]) == (bad["op"], field), detail
    assert env.get(f"/projects/{pid}").json() == before
    # A new project with a frame rate that is no number.
    for fps in (inf, nan):
        r = _raw(env, "/projects", {"canvas": "9:16", "fps": fps})
        assert r.status_code == 400, r.text
    # A path can't be put in the document through a watermark.
    r = env.ops(pid, 1, {"op": "set_branding", "watermark": {"type": "image", "image": "D:\\videos\\secret.mp4"}})
    assert r.status_code == 400 and r.json()["detail"]["field"] == "watermark"
    assert env.get(f"/projects/{pid}").json() == before


def test_the_routes_are_internal(env):
    from server import api_stability as st

    served = {(m, p) for m, p, _ in st.app_routes(env.client.app)}
    mine = {("POST", "/clips/{clip_id}/project"), ("POST", "/projects"), ("GET", "/projects/{project_id}"),
            ("POST", "/projects/{project_id}/ops"), ("POST", "/projects/{project_id}/undo"),
            ("POST", "/projects/{project_id}/redo"), ("GET", "/projects/{project_id}/revisions"),
            ("DELETE", "/projects/{project_id}")}
    assert mine <= served
    assert {route for route in served if "project" in route[1]} == mine
    for route in mine:
        assert st.label(*route) == st.INTERNAL
