"""Adding a video file from this computer, through the real API.

What the file brings into its job: the chosen options (Vertical Live among
them), `force` (so "Make clips again" on a file already clipped really runs
again), and its original link when the user gives one. And the shape check
the Generate bar asks before a file is added.
"""

import json
import subprocess

import pytest

from core.state import StateDB


def _ffmpeg_or_skip() -> str:
    from core.binaries import ffmpeg

    binary = ffmpeg()
    try:
        subprocess.run([binary, "-version"], capture_output=True, check=True)
    except (OSError, subprocess.CalledProcessError):
        pytest.skip("FFmpeg isn't available")
    return binary


@pytest.fixture
def api(tmp_path):
    pytest.importorskip("fastapi")
    pytest.importorskip("httpx")
    pytest.importorskip("numpy")
    from fastapi.testclient import TestClient

    from main import BUNDLED_CONFIG, load_config
    from server.api import create_app

    data_dir = tmp_path / "data"
    config = load_config(BUNDLED_CONFIG)
    config["paths"]["data_dir"] = str(data_dir)
    app = create_app(config, tmp_path / "settings.yaml")
    # No `with` block: startup never runs, so no worker thread picks the job up.
    return TestClient(app, base_url="http://127.0.0.1"), data_dir


def _video(tmp_path, size: str) -> str:
    binary = _ffmpeg_or_skip()
    path = tmp_path / f"live_{size}.mp4"
    subprocess.run([binary, "-v", "error", "-f", "lavfi", "-i", f"testsrc2=size={size}:rate=30",
                    "-f", "lavfi", "-i", "sine", "-t", "3", "-c:v", "libx264", "-pix_fmt", "yuv420p",
                    "-c:a", "aac", "-shortest", str(path)], check=True)
    return str(path)


def _job(data_dir, job_id: int) -> dict:
    db = StateDB(data_dir / "state.db")
    try:
        return json.loads(db.get_job(job_id)["payload"])
    finally:
        db.conn.close()


def test_the_generate_bar_can_ask_a_files_shape_first(api, tmp_path):
    client, _ = api
    tall = client.get("/videos/local/shape", params={"path": _video(tmp_path, "1080x1920")}).json()
    wide = client.get("/videos/local/shape", params={"path": _video(tmp_path, "1920x1080")}).json()
    assert tall == {"width": 1080, "height": 1920, "orientation": "vertical"}
    assert wide["orientation"] == "horizontal"


def test_a_vertical_live_file_keeps_its_switch_and_original_link(api, tmp_path):
    client, data_dir = api
    res = client.post("/videos/local", json={
        "path": _video(tmp_path, "1080x1920"), "vertical_live": True,
        "source_url": "https://www.tiktok.com/@someone/live"})
    assert res.status_code == 200, res.text
    payload = _job(data_dir, res.json()["job_id"])
    assert payload["vertical_live"] is True and payload["url"].startswith("local:")
    db = StateDB(data_dir / "state.db")
    try:
        row = db.conn.execute("SELECT source_url, source_platform FROM videos WHERE video_id = ?",
                              (res.json()["video_id"],)).fetchone()
    finally:
        db.conn.close()
    assert (row["source_url"], row["source_platform"]) == ("https://www.tiktok.com/@someone/live", "tiktok")


def test_make_clips_again_on_a_file_really_runs_again(api, tmp_path):
    client, data_dir = api
    res = client.post("/videos/local", json={"path": _video(tmp_path, "1920x1080"), "force": True})
    assert _job(data_dir, res.json()["job_id"])["force"] is True
