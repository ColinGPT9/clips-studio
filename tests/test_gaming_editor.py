"""Gaming / Reaction in the editor: the settings a person sets for a clip, the
frame they draw on, and the layout remembered for the creator. Light imports
only, so this runs in CI."""

import json
import subprocess

import pytest

from core import modes
from core.state import StateDB

NOW = "2026-09-26T09:00:00"


# ---- what the editor may send ----------------------------------------------------------


def test_a_drawn_layout_is_kept_as_drawn():
    got = modes.clean_gaming({"cam": [0.0, 0.66, 0.25, 0.34], "game_box": [0.17, 0, 0.83, 0.8],
                              "cam_position": "bottom", "game_align": "right", "by": "user"})
    assert got == {"by": "user", "cam": [0.0, 0.66, 0.25, 0.34], "game_box": [0.17, 0.0, 0.83, 0.8],
                   "cam_position": "bottom", "game_align": "right"}


def test_no_webcam_said_on_purpose_is_kept():
    assert modes.clean_gaming({"cam": None, "by": "user"}) == {"by": "user", "cam": None}


def test_a_box_outside_the_frame_is_refused():
    for box in ([1.2, 0, 0.2, 0.2], [0, 0, 0.001, 0.5], ["a", 0, 0.2, 0.2], [0, 0, 0.2]):
        with pytest.raises(ValueError):
            modes.clean_gaming({"cam": box})


def test_a_box_running_off_the_edge_is_trimmed_to_it():
    assert modes.clean_gaming({"cam": [0.8, 0.5, 0.4, 0.6]})["cam"] == [0.8, 0.5, pytest.approx(0.2),
                                                                        pytest.approx(0.5)]


def test_unknown_values_fall_back():
    got = modes.clean_gaming({"by": "hacker", "cam_position": "left", "game_align": "up"})
    assert got == {"by": "user"}


# ---- the API ---------------------------------------------------------------------------


@pytest.fixture
def env(tmp_path):
    pytest.importorskip("fastapi")
    pytest.importorskip("httpx")
    from fastapi.testclient import TestClient

    from main import BUNDLED_CONFIG, load_config
    from server.api import create_app

    data_dir = tmp_path / "data"
    config = load_config(BUNDLED_CONFIG)
    config["paths"]["data_dir"] = str(data_dir)
    app = create_app(config, tmp_path / "settings.yaml")
    db = StateDB(data_dir / "state.db")
    db.conn.execute("INSERT INTO creators (display_name, created_at) VALUES ('A streamer', ?)", (NOW,))
    creator_id = db.conn.execute("SELECT creator_id FROM creators").fetchone()[0]
    for vid, creator in (("vid1", creator_id), ("vid2", None)):
        db.conn.execute(
            "INSERT INTO videos (video_id, title, status, creator_id, created_at, updated_at)"
            " VALUES (?, 'A stream', 'done', ?, ?, ?)", (vid, creator, NOW, NOW))
    clips = {}
    for vid in ("vid1", "vid2"):
        cur = db.conn.execute(
            "INSERT INTO clips (video_id, start_s, end_s, score, hook, path, title, created_at)"
            " VALUES (?, 2, 6, 80, 'h', '', 'Clip', ?)", (vid, NOW))
        clips[vid] = cur.lastrowid
    db.conn.commit()
    client = TestClient(app, base_url="http://127.0.0.1")
    yield client, db, clips, data_dir, creator_id
    db.conn.close()


LAYOUT = {"cam": [0.0, 0.66, 0.25, 0.34], "game_box": [0.17, 0.0, 0.83, 0.8], "cam_position": "top",
          "by": "user"}


def test_a_layout_is_remembered_for_the_clips_creator(env):
    client, db, clips, _data, creator_id = env
    r = client.put(f"/clips/{clips['vid1']}/creator-gaming-layout", json={"layout": LAYOUT})
    assert r.status_code == 200
    saved = db.creator_gaming_layout(creator_id)
    assert saved["cam"] == LAYOUT["cam"] and "by" not in saved        # "by" is per clip, not per creator
    assert client.get(f"/clips/{clips['vid1']}/creator-gaming-layout").json()["layout"] == saved
    assert client.put(f"/clips/{clips['vid1']}/creator-gaming-layout", json={"layout": None}).status_code == 200
    assert db.creator_gaming_layout(creator_id) is None


def test_a_video_with_no_creator_says_so(env):
    client, _db, clips, _data, _creator = env
    r = client.put(f"/clips/{clips['vid2']}/creator-gaming-layout", json={"layout": LAYOUT})
    assert r.status_code == 409 and "creator" in r.json()["detail"]


def test_a_bad_box_is_refused_before_anything_is_saved(env):
    client, db, clips, _data, creator_id = env
    r = client.put(f"/clips/{clips['vid1']}/creator-gaming-layout", json={"layout": {"cam": [2, 2, 2, 2]}})
    assert r.status_code == 400 and db.creator_gaming_layout(creator_id) is None
    r = client.post(f"/clips/{clips['vid1']}/render", json={"render_opts": {"gaming": {"cam": [2, 2, 2, 2]}}})
    assert r.status_code == 400


def test_the_editors_rerender_carries_the_cleaned_layout(env):
    client, db, clips, _data, _creator = env
    r = client.post(f"/clips/{clips['vid1']}/render",
                    json={"render_opts": {"gaming": {**LAYOUT, "extra": 1}, "crop": "track"}})
    assert r.status_code == 200
    payload = json.loads(db.get_job(r.json()["job_id"])["payload"])
    assert payload["render_opts"]["gaming"]["cam"] == LAYOUT["cam"]
    assert "extra" not in payload["render_opts"]["gaming"]
    r = client.post(f"/clips/{clips['vid1']}/render", json={"render_opts": {"gaming": None, "crop": "center"}})
    assert json.loads(db.get_job(r.json()["job_id"])["payload"])["render_opts"]["gaming"] is None


def test_the_source_frame_to_draw_on(env):
    from core.binaries import ffmpeg

    client, _db, clips, data_dir, _creator = env
    try:
        subprocess.run([ffmpeg(), "-version"], capture_output=True, check=True)
    except (OSError, subprocess.CalledProcessError):
        pytest.skip("FFmpeg isn't available")
    (data_dir / "downloads").mkdir(parents=True, exist_ok=True)
    subprocess.run([ffmpeg(), "-v", "error", "-f", "lavfi", "-i", "testsrc2=size=640x360:rate=30:d=8",
                    "-pix_fmt", "yuv420p", str(data_dir / "downloads" / "vid1.mp4")], check=True)
    r = client.get(f"/clips/{clips['vid1']}/source-frame?at=0.25")
    assert r.status_code == 200 and r.headers["content-type"] == "image/jpeg" and r.content[:2] == b"\xff\xd8"
    assert client.get(f"/clips/{clips['vid2']}/source-frame").status_code == 404   # no source on disk


# ---- set up before processing ------------------------------------------------------------


def test_a_split_set_up_before_processing_travels_with_the_job(env):
    from server import api as api_mod

    body = api_mod.JobIn(url="https://www.youtube.com/watch?v=abcdefghijk", gaming=True, gaming_remember=True,
                         gaming_layout={"cam": [0.0, 0.66, 0.25, 0.34], "game_fit": "fill", "by": "video"})
    payload = api_mod._process_options(body)
    assert payload["gaming_layout"] == {"by": "user", "cam": [0.0, 0.66, 0.25, 0.34], "game_fit": "fill"}
    assert payload["gaming_remember"] is True
    # "Find it automatically" is no cam key at all, not "no webcam".
    auto = api_mod._process_options(api_mod.JobIn(url="https://youtu.be/x", gaming=True,
                                                  gaming_layout={"cam_position": "bottom"}))
    assert "cam" not in auto["gaming_layout"]


def test_frames_of_a_video_not_processed_yet(env):
    from core.binaries import ffmpeg

    client, _db, _clips, data_dir, _creator = env
    try:
        subprocess.run([ffmpeg(), "-version"], capture_output=True, check=True)
    except (OSError, subprocess.CalledProcessError):
        pytest.skip("FFmpeg isn't available")
    video = data_dir.parent / "my stream.mp4"
    subprocess.run([ffmpeg(), "-v", "error", "-f", "lavfi", "-i", "testsrc2=size=640x360:rate=30:d=10",
                    "-pix_fmt", "yuv420p", str(video)], check=True)
    r = client.get("/sources/frame", params={"path": str(video), "at": 0.7})
    assert r.status_code == 200 and r.content[:2] == b"\xff\xd8"
    assert client.get("/sources/frame", params={"path": str(data_dir / "state.db")}).status_code == 400
    assert client.get("/sources/frame").status_code == 400


def test_only_links_to_the_platforms_it_clips_from_are_opened(env):
    client = env[0]
    for url in ("http://127.0.0.1:8765/health", "file:///C:/Windows/win.ini", "https://example.com/v.mp4"):
        r = client.get("/sources/frame", params={"url": url})
        assert r.status_code == 422, url
