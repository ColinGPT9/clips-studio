"""The exported mark on clips, shown as a star in the Clip Editor.

Copying a clip out marks it exported, the star sets or clears the mark by hand,
and Export all relies on it to skip clips that are already done. A database
from before the mark existed gets it from the export history it already kept
in clip_feedback.
"""

import sqlite3
from pathlib import Path

import pytest

from core.state import StateDB

NOW = "2026-09-15T09:00:00"


def _seed(db: StateDB, media: Path) -> dict[str, int]:
    """One video with three clips; the third has lost its file."""
    db.conn.execute(
        "INSERT INTO videos (video_id, title, status, created_at, updated_at)"
        " VALUES ('vid1', 'Friday stream', 'done', ?, ?)",
        (NOW, NOW),
    )
    ids = {}
    for i, (name, has_file) in enumerate((("a", True), ("b", True), ("gone", False))):
        path = media / f"{name}.mp4"
        if has_file:
            path.write_bytes(b"not really a video")
        cur = db.conn.execute(
            "INSERT INTO clips (video_id, start_s, end_s, score, hook, path, title, created_at)"
            " VALUES ('vid1', ?, ?, 80, ?, ?, ?, ?)",
            (i * 60.0, i * 60.0 + 30, name, str(path), f"Clip {name}", NOW),
        )
        ids[name] = cur.lastrowid
    db.conn.commit()
    return ids


def test_old_database_gets_exported_marks_from_its_export_history(tmp_path):
    if sqlite3.sqlite_version_info < (3, 35, 0):
        pytest.skip("needs DROP COLUMN to build a pre-migration database")
    path = tmp_path / "state.db"
    db = StateDB(path)
    ids = _seed(db, tmp_path)
    db.conn.executemany(
        "INSERT INTO clip_feedback (clip_id, action, created_at) VALUES (?, ?, ?)",
        [
            (ids["a"], "exported", "2026-08-01T10:00:00"),
            (ids["a"], "exported", "2026-09-01T10:00:00"),  # the latest export wins
            (ids["b"], "captions_edited", "2026-09-02T10:00:00"),  # not an export
        ],
    )
    db.conn.commit()
    db.conn.close()

    raw = sqlite3.connect(path)
    raw.execute("ALTER TABLE clips DROP COLUMN exported_at")
    raw.commit()
    raw.close()

    db = StateDB(path)
    rows = {r["id"]: r for r in db.conn.execute("SELECT id, exported_at FROM clips")}
    db.conn.close()
    assert rows[ids["a"]]["exported_at"] == "2026-09-01T10:00:00"
    assert rows[ids["b"]]["exported_at"] == ""
    assert rows[ids["gone"]]["exported_at"] == ""


@pytest.fixture
def api_env(tmp_path):
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
    ids = _seed(db, tmp_path)
    # No `with` block, so startup never runs and no worker thread starts. The
    # host has to be one TrustedHostMiddleware accepts.
    client = TestClient(app, base_url="http://127.0.0.1")
    yield client, db, ids, tmp_path
    db.conn.close()


def _clips(client) -> dict[int, dict]:
    return {c["id"]: c for c in client.get("/videos/vid1/clips").json()}


def test_export_marks_the_clips_it_copied_and_no_others(api_env):
    client, _, ids, tmp_path = api_env
    out = tmp_path / "out"
    r = client.post(
        "/export/batch",
        json={"clip_ids": [ids["a"], ids["b"], ids["gone"]], "folder": str(out)},
    )
    assert r.status_code == 200
    assert len(r.json()["exported"]) == 2
    assert len(list(out.glob("*.mp4"))) == 2

    clips = _clips(client)
    assert clips[ids["a"]]["exported_at"]
    assert clips[ids["b"]]["exported_at"]
    assert clips[ids["gone"]]["exported_at"] == ""  # nothing was copied for it


def test_star_sets_and_clears_the_mark_and_keeps_the_first_time(api_env):
    client, db, ids, _ = api_env
    db.set_clip(ids["a"], exported_at="2026-01-01T00:00:00")

    r = client.patch(f"/clips/{ids['a']}", json={"exported": True, "title": "Renamed"})
    assert r.status_code == 200
    assert r.json()["exported_at"] == "2026-01-01T00:00:00"
    assert r.json()["title"] == "Renamed"

    r = client.patch(f"/clips/{ids['b']}", json={"exported": True})
    assert r.json()["exported_at"]
    r = client.patch(f"/clips/{ids['b']}", json={"exported": False})
    assert r.json()["exported_at"] == ""

    # Only a real export is logged: a star says nothing about what this
    # creator's viewers like.
    assert db.conn.execute("SELECT COUNT(*) FROM clip_feedback").fetchone()[0] == 0
