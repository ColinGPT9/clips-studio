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


# ---- #122: an H.265 file failed at transcription with InvalidDataError ------
#
# It was never the codec. A file that isn't H.264 was converted while it was
# being added, straight to its final name, which takes minutes for a long
# recording. Stopped part-way (the app closed, Generate pressed again), it left
# a half-written copy that both the import and the job took for the real thing.

def _h265(tmp_path) -> str:
    binary = _ffmpeg_or_skip()
    path = tmp_path / "recording_h265.mp4"
    made = subprocess.run(
        [binary, "-v", "error", "-f", "lavfi", "-i", "testsrc2=size=1280x720:rate=60",
         "-f", "lavfi", "-i", "sine", "-t", "3", "-c:v", "libx265", "-pix_fmt", "yuv420p10le",
         "-c:a", "aac", "-shortest", str(path)], capture_output=True)
    if made.returncode != 0:
        pytest.skip("this FFmpeg cannot encode H.265")
    return str(path)


def _half_written(path) -> None:
    """What FFmpeg leaves when it is stopped while writing an MP4: the file
    type, then media data, and no index, because the index is written last."""
    import os

    ftyp = (24).to_bytes(4, "big") + b"ftyp" + b"isom" + (512).to_bytes(4, "big") + b"isomiso2"
    free = (8).to_bytes(4, "big") + b"free"
    path.write_bytes(ftyp + free + (0).to_bytes(4, "big") + b"mdat" + os.urandom(200_000))


def test_an_h265_file_is_copied_in_not_converted(api, tmp_path):
    from video.encoding import readable_video, source_codec

    client, data_dir = api
    res = client.post("/videos/local", json={"path": _h265(tmp_path)})
    assert res.status_code == 200, res.text
    copy = data_dir / "downloads" / f"{res.json()['video_id']}.mp4"
    # Still H.265: the minutes-long conversion belongs to the job, in view.
    assert source_codec(copy) == "hevc" and readable_video(copy)
    assert [p.name for p in copy.parent.iterdir()] == [copy.name]   # no temporary file left


def test_a_half_written_copy_is_imported_again(api, tmp_path):
    from video.encoding import readable_video

    client, data_dir = api
    path = _h265(tmp_path)
    first = client.post("/videos/local", json={"path": path}).json()
    copy = data_dir / "downloads" / f"{first['video_id']}.mp4"
    _half_written(copy)
    assert not readable_video(copy)

    again = client.post("/videos/local", json={"path": path, "force": True})

    assert again.status_code == 200, again.text
    assert readable_video(copy)


def test_a_job_never_runs_on_a_half_written_copy(api, tmp_path):
    from core.pipeline import _cached_or_download

    _, data_dir = api
    copy = data_dir / "downloads" / "local_0123456789ab.mp4"
    copy.parent.mkdir(parents=True, exist_ok=True)
    _half_written(copy)
    db = StateDB(data_dir / "state.db")
    try:
        with pytest.raises(ValueError, match="did not finish importing"):
            _cached_or_download("local:local_0123456789ab", data_dir, db)
    finally:
        db.conn.close()
    assert not copy.exists()   # so adding the file again starts clean


def test_a_slow_source_is_converted_in_the_job(api, tmp_path):
    from types import SimpleNamespace

    from core.pipeline import convert_slow_source
    from main import BUNDLED_CONFIG, load_config
    from video.encoding import source_codec

    path = _h265(tmp_path)
    convert_slow_source(SimpleNamespace(path=path, video_id="local_x"), load_config(BUNDLED_CONFIG))
    assert source_codec(path) == "h264"
