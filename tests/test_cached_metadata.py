"""A cached download still gets its title and channel (issue #64).

A file already in downloads/ skips the download, and with it the metadata the
download would have brought. When the database has no row for it, or only a
placeholder, the title and channel are fetched once and written back, so the
video is not recorded under its raw ID with no creator.
"""

from unittest.mock import MagicMock, patch

import pytest

pytest.importorskip("numpy", reason="core.pipeline imports numpy, which CI does not install")

from core.models import DownloadedVideo
from core.pipeline import _cached_or_download


@pytest.fixture
def downloads_dir(tmp_path):
    d = tmp_path / "downloads"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _mock_ffprobe_success(cmd, **kwargs):
    result = MagicMock()
    if any("stream=codec_name" in str(arg) for arg in cmd):
        result.stdout = "h264\n"
    elif any("format=duration" in str(arg) for arg in cmd):
        result.stdout = "120.5\n"
    else:
        result.stdout = ""
    return result


def test_cached_source_with_existing_db_metadata(db, downloads_dir):
    video_id = "dQw4w9WgXc1"  # exactly 11 chars
    file_path = downloads_dir / f"{video_id}.mp4"
    file_path.write_text("fake video data")

    db.upsert_video(
        video_id=video_id,
        title="Stored Title",
        channel_name="Stored Channel",
        duration=120.0,
    )

    url = f"https://www.youtube.com/watch?v={video_id}"

    with patch("subprocess.run", side_effect=_mock_ffprobe_success), \
         patch("sources.dispatch.metadata") as mock_metadata:
        video = _cached_or_download(url, downloads_dir.parent, db)
        assert isinstance(video, DownloadedVideo)
        assert video.video_id == video_id
        assert video.title == "Stored Title"
        assert video.channel == "Stored Channel"
        # Since metadata was in DB, dispatch.metadata should NOT be called
        mock_metadata.assert_not_called()


def test_cached_source_recovers_metadata_when_db_row_missing(db, downloads_dir):
    video_id = "dQw4w9WgXc2"  # exactly 11 chars
    file_path = downloads_dir / f"{video_id}.mp4"
    file_path.write_text("fake video data")

    url = f"https://www.youtube.com/watch?v={video_id}"

    with patch("subprocess.run", side_effect=_mock_ffprobe_success), \
         patch("sources.dispatch.metadata", return_value=("Fetched Title", "Fetched Channel")) as mock_metadata:
        video = _cached_or_download(url, downloads_dir.parent, db)
        assert video.video_id == video_id
        assert video.title == "Fetched Title"
        assert video.channel == "Fetched Channel"
        mock_metadata.assert_called_once_with(url)


def test_cached_source_recovers_metadata_and_updates_sparse_db_row(db, downloads_dir):
    video_id = "dQw4w9WgXc3"  # exactly 11 chars
    file_path = downloads_dir / f"{video_id}.mp4"
    file_path.write_text("fake video data")

    # DB row exists but has default/empty title and channel
    db.upsert_video(
        video_id=video_id,
        title=video_id,  # Raw ID title
        channel_name="",
        duration=120.0,
    )

    url = f"https://www.youtube.com/watch?v={video_id}"

    with patch("subprocess.run", side_effect=_mock_ffprobe_success), \
         patch("sources.dispatch.metadata", return_value=("Recovered Title", "Recovered Channel")):
        video = _cached_or_download(url, downloads_dir.parent, db)
        assert video.title == "Recovered Title"
        assert video.channel == "Recovered Channel"

        # Check that DB row was updated with the recovered values
        row = db.conn.execute("SELECT title, channel_name FROM videos WHERE video_id = ?", (video_id,)).fetchone()
        assert row["title"] == "Recovered Title"
        assert row["channel_name"] == "Recovered Channel"


def test_cached_source_falls_back_gracefully_when_metadata_fetch_fails(db, downloads_dir):
    video_id = "dQw4w9WgXc4"  # exactly 11 chars
    file_path = downloads_dir / f"{video_id}.mp4"
    file_path.write_text("fake video data")

    url = f"https://www.youtube.com/watch?v={video_id}"

    with patch("subprocess.run", side_effect=_mock_ffprobe_success), \
         patch("sources.dispatch.metadata", side_effect=Exception("Network error")):
        video = _cached_or_download(url, downloads_dir.parent, db)
        assert video.video_id == video_id
        assert video.title == video_id
        assert video.channel == ""
