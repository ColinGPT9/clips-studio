"""The export routes with presets (server/api.py, step 1 of the editor suite).

- Original, the default, is the same copy as before: the same file, the same
  response, no session header needed.
- Any other preset becomes an editor-lane job, needs the session header, and
  marks the clip exported once the file is made.
- Bad values are refused before anything is queued.
- Settings → Export's default preset round-trips through the settings route.

The lane's encode is a fake here (real encodes are in
tests/test_export_presets_ffmpeg.py) and the lane thread never starts: the
tests run its queue with run_pending().
"""

import filecmp
import os
from pathlib import Path

import pytest

from core.state import StateDB
from video import encoding
from video import export_presets as ep

NOW = "2026-11-05T09:00:00"
P720 = {"kind": "custom", "resolution": "720p"}


def _seed(db: StateDB, media: Path) -> dict[str, int]:
    """One video with three clips; the third has lost its file."""
    db.conn.execute("INSERT INTO videos (video_id, title, status, created_at, updated_at)"
                    " VALUES ('vid1', 'Friday stream', 'done', ?, ?)", (NOW, NOW))
    ids = {}
    for i, (name, has_file) in enumerate((("a", True), ("b", True), ("gone", False))):
        path = media / f"{name}.mp4"
        if has_file:
            path.write_bytes(f"clip {name} ".encode() * 100)
        cur = db.conn.execute(
            "INSERT INTO clips (video_id, start_s, end_s, score, hook, path, title, created_at)"
            " VALUES ('vid1', ?, ?, 80, ?, ?, ?, ?)",
            (i * 60.0, i * 60.0 + 30, name, str(path), f"Clip {name}", NOW))
        ids[name] = cur.lastrowid
    db.conn.commit()
    return ids


class Env:
    def __init__(self, client, app, db, ids, root, session):
        self.client, self.app, self.db, self.ids, self.root = client, app, db, ids, root
        self.lane = app.state.editor_lane
        self.ok = {"X-Clips-Kitty-Session": session}
        self.out = root / "out"


@pytest.fixture
def env(tmp_path, monkeypatch):
    pytest.importorskip("fastapi")
    pytest.importorskip("httpx")
    pytest.importorskip("yaml")
    from fastapi.testclient import TestClient

    from main import BUNDLED_CONFIG, load_config
    from plugins import session
    from server.api import create_app

    monkeypatch.delenv("CLIPS_KITTY_SESSION_SECRET", raising=False)
    monkeypatch.setattr(encoding, "_mode", "auto")
    data_dir = tmp_path / "data"
    config = load_config(BUNDLED_CONFIG)
    config["paths"]["data_dir"] = str(data_dir)
    app = create_app(config, tmp_path / "settings.yaml")
    db = StateDB(data_dir / "state.db")
    ids = _seed(db, tmp_path)

    def encode(src, dst, preset, *, workdir, run, on_progress=None):
        Path(dst).write_bytes(Path(src).read_bytes() + b" at " + preset["resolution"].encode())
        return {"bytes": Path(dst).stat().st_size, "width": 720, "height": 1280}

    app.state.editor_lane._encode = encode
    # Exports ask which encoder this PC has only for AV1 and "gpu"; estimates
    # always ask. Answered here, so nothing is probed.
    monkeypatch.setattr(encoding, "encoder_for",
                        lambda codec, pref="auto": encoding.Encoder(codec, "cpu", "libx264")
                        if codec == "h264" and pref != "gpu" else None)
    client = TestClient(app, base_url="http://127.0.0.1")
    yield Env(client, app, db, ids, tmp_path, session.path(data_dir).read_text(encoding="utf-8").strip())
    db.conn.close()


def _clips(env) -> dict[int, dict]:
    return {c["id"]: c for c in env.client.get("/videos/vid1/clips").json()}


# ---- Original: the copy, as before -----------------------------------------------------------


@pytest.mark.parametrize("preset", ["absent", None, "original", {"kind": "original"},
                                    {"kind": "original", "resolution": "720p"}, "default"])
def test_original_is_the_copy_it_always_was(env, preset):
    body = {"folder": str(env.out)}
    if preset != "absent":
        body["preset"] = preset
    r = env.client.post(f"/clips/{env.ids['a']}/export", json=body)   # no session header
    assert r.status_code == 200
    assert set(r.json()) == {"exported"}, "the same answer as before presets"
    (copy,) = map(Path, r.json()["exported"])
    source = env.root / "a.mp4"
    assert copy == env.out / "clip-a.mp4"
    assert filecmp.cmp(source, copy, shallow=False), "byte for byte the clip"
    assert os.stat(copy).st_mtime == os.stat(source).st_mtime, "copied with shutil.copy2, as before"
    assert _clips(env)[env.ids["a"]]["exported_at"]
    assert env.lane.jobs() == []


def test_original_batch_is_unchanged(env):
    r = env.client.post("/export/batch", json={"clip_ids": [env.ids["a"], env.ids["b"], env.ids["gone"]],
                                               "folder": str(env.out), "preset": "original"})
    assert r.status_code == 200 and set(r.json()) == {"exported"} and len(r.json()["exported"]) == 2


# ---- a preset: a lane job ----------------------------------------------------------------------


def test_a_preset_becomes_a_lane_job(env):
    a = env.ids["a"]
    r = env.client.post(f"/clips/{a}/export", json={"folder": str(env.out), "preset": P720}, headers=env.ok)
    assert r.status_code == 200
    body = r.json()
    assert body["exported"] == [] and body["skipped"] == []
    (job,) = body["jobs"]
    assert (job["status"], job["clip_id"], job["preset"]["resolution"]) == ("queued", a, "720p")
    assert not env.out.exists() or not list(env.out.glob("*.mp4")), "nothing is written in the request"
    assert not _clips(env)[a]["exported_at"], "not exported until the file is made"

    assert env.lane.run_pending() == 1
    done = env.client.get(f"/export/jobs/{job['export_id']}").json()
    assert done["status"] == "done"
    made = Path(done["result"]["path"])
    assert made == env.out / "clip-a.mp4" and made.read_bytes().endswith(b" at 720p")
    assert _clips(env)[a]["exported_at"]
    assert env.client.get("/export/jobs").json()["jobs"][0]["export_id"] == job["export_id"]


def test_a_preset_needs_the_session(env):
    for headers in ({}, {"X-Clips-Kitty-Session": "not-the-secret-at-all"}):
        r = env.client.post(f"/clips/{env.ids['a']}/export", json={"folder": str(env.out), "preset": P720},
                            headers=headers)
        assert r.status_code == 403
        r = env.client.post("/export/batch", json={"clip_ids": [env.ids["a"]], "folder": str(env.out),
                                                   "preset": P720}, headers=headers)
        assert r.status_code == 403
    assert env.lane.jobs() == []


@pytest.mark.parametrize("preset", [
    {"kind": "custom", "resolution": "999p"}, {"kind": "custom", "codec": "vp9"},
    {"kind": "custom", "bar_color": "#000;drawtext=x"}, {"kind": "custom", "crf": "1 -f null"},
    {"kind": "custom", "fps": 120}, {"vf": "drawtext"}, "fancy", 7,
])
def test_bad_presets_are_refused(env, preset):
    r = env.client.post(f"/clips/{env.ids['a']}/export", json={"folder": str(env.out), "preset": preset},
                        headers=env.ok)
    assert r.status_code in (400, 422)
    assert env.lane.jobs() == []


def test_av1_or_a_graphics_card_this_pc_lacks_is_refused(env):
    for preset in ({"kind": "custom", "codec": "av1"}, {"kind": "custom", "encoder": "gpu"}):
        r = env.client.post(f"/clips/{env.ids['a']}/export", json={"folder": str(env.out), "preset": preset},
                            headers=env.ok)
        assert r.status_code == 400, preset
        r = env.client.patch("/settings", json={"export": preset}, headers=env.ok)
        assert r.status_code == 400, preset
    assert env.lane.jobs() == []


def test_a_batch_with_a_preset(env):
    ids = env.ids
    r = env.client.post("/export/batch", json={"clip_ids": [ids["a"], ids["b"], ids["gone"]], "folder": str(env.out),
                                               "preset": P720}, headers=env.ok)
    assert r.status_code == 200
    assert [j["clip_id"] for j in r.json()["jobs"]] == [ids["a"], ids["b"]]
    assert r.json()["skipped"] == [ids["gone"]]
    assert env.lane.run_pending() == 2
    assert sorted(p.name for p in env.out.iterdir()) == ["clip-a.mp4", "clip-b.mp4"]


def test_jobs_can_be_listed_and_cancelled(env):
    r = env.client.post(f"/clips/{env.ids['a']}/export", json={"folder": str(env.out), "preset": P720},
                        headers=env.ok)
    export_id = r.json()["jobs"][0]["export_id"]
    assert env.client.post(f"/export/jobs/{export_id}/cancel").status_code == 403
    assert env.client.get("/export/jobs/9999").status_code == 404
    assert env.client.post("/export/jobs/9999/cancel", headers=env.ok).status_code == 404
    r = env.client.post(f"/export/jobs/{export_id}/cancel", headers=env.ok)
    assert r.status_code == 200 and r.json()["status"] == "cancelled"
    assert env.lane.run_pending() == 0


# ---- Settings → Export ---------------------------------------------------------------------------


def test_the_default_preset_round_trips_through_settings(env):
    assert env.client.get("/settings").json()["export"]["kind"] == "original"
    r = env.client.patch("/settings", json={"export": {**P720, "max_mb": 95, "fps": "30"}}, headers=env.ok)
    assert r.status_code == 200
    assert (r.json()["export"]["resolution"], r.json()["export"]["max_mb"], r.json()["export"]["fps"]) == \
        ("720p", 95.0, 30)
    assert env.client.get("/settings").json()["export"]["resolution"] == "720p"
    assert env.client.get("/export/presets").json()["default"]["max_mb"] == 95.0
    r = env.client.post(f"/clips/{env.ids['a']}/export", json={"folder": str(env.out), "preset": "default"},
                        headers=env.ok)
    assert r.json()["jobs"][0]["preset"]["max_mb"] == 95.0
    # Back to Original: the Export button copies again, with no header needed.
    assert env.client.patch("/settings", json={"export": "original"}, headers=env.ok).status_code == 200
    r = env.client.post(f"/clips/{env.ids['b']}/export", json={"folder": str(env.out), "preset": "default"})
    assert set(r.json()) == {"exported"}


def test_export_settings_need_the_session_and_good_values(env):
    assert env.client.patch("/settings", json={"export": P720}).status_code == 403
    r = env.client.patch("/settings", json={"export": {"kind": "custom", "quality": "ultra"}}, headers=env.ok)
    assert r.status_code == 400 and "quality" in r.json()["detail"]
    assert env.client.get("/settings").json()["export"]["kind"] == "original"


def test_a_damaged_saved_preset_reads_as_original(env):
    env.db.set_flag("export_settings", "{not json")
    assert env.client.get("/settings").json()["export"]["kind"] == "original"
    env.db.set_flag("export_settings", '{"kind": "custom", "codec": "vp9"}')
    assert env.client.get("/export/presets").json()["default"]["kind"] == "original"


# ---- what the Settings card reads --------------------------------------------------------------


def test_the_presets_route_lists_every_choice(env):
    got = env.client.get("/export/presets").json()
    assert got["default"]["kind"] == "original" and got["original"]["kind"] == "original"
    assert got["custom"]["kind"] == "custom" and got["custom"]["quality"] == "balanced"
    assert got["choices"]["resolution"] == ["same", "240p", "360p", "480p", "540p", "720p", "1080p", "1440p",
                                            "2160p", "custom"]
    assert got["choices"]["aspect"] == ["same", "9:16", "16:9", "1:1", "4:5", "custom"]
    assert got["choices"]["fps"] == ["same", 24, 25, 30, 50, 60]
    assert got["limits"]["max_mb"] == [5, 4000] and got["limits"]["audio_kbps"] == [64, 320]


def test_the_codecs_route_lists_what_this_pc_encodes(env, monkeypatch):
    monkeypatch.setattr(encoding, "export_codecs",
                        lambda: {"hevc": {"cpu": "libx265", "gpu": None}, "h264": {"cpu": "libx264", "gpu": "h264_qsv"}})
    assert env.client.get("/export/codecs").json() == {"codecs": [
        {"codec": "h264", "cpu": "libx264", "gpu": "h264_qsv"}, {"codec": "hevc", "cpu": "libx265", "gpu": None}]}


def test_the_estimate_route(env, monkeypatch):
    r = env.client.post("/export/estimate", json={"preset": P720})
    est = r.json()
    assert r.status_code == 200 and est["kind"] == "range" and est["low"] < est["bytes"] < est["high"]
    assert (est["width"], est["height"], est["seconds"], est["available"]) == (720, 1280, 60.0, True)
    assert est["per_minute"]["bytes"] == est["bytes"]
    up = env.client.post("/export/estimate", json={"preset": {"kind": "custom", "resolution": "1440p"}}).json()
    assert up["upscale"] is True
    original = env.client.post("/export/estimate", json={}).json()
    assert original["kind"] == "range" and 48e6 <= original["per_minute"]["bytes"] <= 54e6
    target = env.client.post("/export/estimate",
                             json={"preset": {"kind": "custom", "max_mb": 100}, "seconds": 300}).json()
    assert target["kind"] == "close" and target["high"] <= 100 * ep.MB

    monkeypatch.setattr(ep, "probe", lambda path: ep.Source(1080, 1920, 30.0, 30.0, video_kbps=5000.0,
                                                            size=19_000_000))
    clip = env.client.post("/export/estimate", json={"clip_id": env.ids["a"]}).json()
    assert (clip["kind"], clip["bytes"], clip["seconds"]) == ("copy", 19_000_000, 30.0)
    clip = env.client.post("/export/estimate", json={"clip_id": env.ids["a"], "preset": P720}).json()
    assert clip["seconds"] == 30.0 and clip["fps"] == 30.0
    assert env.client.post("/export/estimate", json={"clip_id": 9999}).status_code == 404
    assert env.client.post("/export/estimate", json={"clip_id": env.ids["gone"]}).status_code == 400
    assert env.client.post("/export/estimate", json={"preset": {"codec": "vp9"}}).status_code == 400


def test_huge_json_numbers_never_crash_the_routes(env):
    """JSON allows integers no float can hold: each is clamped or refused, never a 500."""
    huge = 10**400
    r = env.client.post("/export/estimate",
                        json={"preset": {"kind": "custom", "resolution": "custom", "crf": huge, "width": huge}})
    assert r.status_code == 200 and r.json()["width"] == 4096
    # Parsed and clamped, then refused only for the missing session header, as any preset is.
    r = env.client.post(f"/clips/{env.ids['a']}/export",
                        json={"folder": str(env.out), "preset": {"kind": "custom", "width": huge}})
    assert r.status_code == 403
    assert env.client.post("/export/estimate", json={"clip_id": huge}).status_code == 404
    assert env.client.post("/export/estimate", json={"seconds": huge}).status_code in (200, 422)
    r = env.client.patch("/settings", json={"export": {"kind": "custom", "audio_kbps": huge}}, headers=env.ok)
    assert r.status_code == 200


@pytest.mark.parametrize("bad", [10**400, -10**400, 2**63, -2**63 - 1, 0])
def test_clip_ids_beyond_any_row_are_skipped_not_a_crash(env, bad):
    """With the session, a preset export reaches the clip lookup: an id SQLite
    can't hold is skipped like a missing clip, and nothing is queued for it."""
    custom = {"kind": "custom", "resolution": "720p"}
    r = env.client.post(f"/clips/{bad}/export", json={"folder": str(env.out), "preset": custom}, headers=env.ok)
    assert r.status_code == 200 and r.json()["jobs"] == [] and r.json()["skipped"] == [bad]
    r = env.client.post("/export/batch", json={"folder": str(env.out), "preset": custom,
                                               "clip_ids": [env.ids["a"], bad]}, headers=env.ok)
    assert r.status_code == 200 and r.json()["skipped"] == [bad]
    assert [job["clip_id"] for job in r.json()["jobs"]] == [env.ids["a"]]


def test_the_engine_applies_the_encoder_setting_at_start(tmp_path, monkeypatch):
    pytest.importorskip("fastapi")
    pytest.importorskip("yaml")
    from main import BUNDLED_CONFIG, load_config
    from server.api import create_app

    monkeypatch.setattr(encoding, "_mode", "auto")
    monkeypatch.setattr(encoding, "_selected", None)
    monkeypatch.setattr(encoding, "_refused", set())
    config = load_config(BUNDLED_CONFIG)
    config["paths"]["data_dir"] = str(tmp_path / "data")
    config["video"]["encoder"] = "cpu"
    create_app(config, tmp_path / "settings.yaml")
    assert encoding._mode == "cpu"
    assert encoding.video_encoder_args() == encoding.CPU_ARGS
