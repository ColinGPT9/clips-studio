"""Vertical Live downloads: the vertical version, or a clear no.

A vertical live's recording is portrait-only (a YouTube vertical live, or the
"-vert" broadcast Streamlabs Dual Output makes) or one of two shapes of a
dual-format stream. So a Vertical Live download asks for portrait formats
only, keeps its own file, and refuses a video with no vertical version
instead of quietly taking the horizontal one. The standard download is
unchanged.
"""

from typing import ClassVar
from unittest.mock import MagicMock, patch

import pytest

pytest.importorskip("yt_dlp")

from sources import dispatch, kick, twitch, vertical, youtube

STANDARD_YOUTUBE_FORMAT = (
    "bv*[height<=1080][vcodec^=avc1]+ba[ext=m4a]"
    "/bv*[height<=1080][ext=mp4][vcodec!^=av01]+ba[ext=m4a]"
    "/b[ext=mp4]/b"
)


class FakeYDL:
    """Stands in for yt_dlp.YoutubeDL: records the options, writes the file."""

    seen: ClassVar[list[dict]] = []
    fail_with: Exception | None = None
    video_id = "abcdefghijk"

    def __init__(self, opts):
        self.opts = opts
        FakeYDL.seen.append(opts)

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False

    def extract_info(self, url, download=False):
        if not download:
            return {"id": self.video_id, "is_live": False}
        if FakeYDL.fail_with:
            raise FakeYDL.fail_with
        template = self.opts["outtmpl"]
        path = template.replace("%(id)s", self.video_id).replace("%(ext)s", "mp4")
        with open(path, "wb") as f:
            f.write(b"video")
        return {"id": self.video_id, "title": "A live", "duration": 600, "channel": "A channel"}


@pytest.fixture
def ydl(monkeypatch):
    FakeYDL.seen = []
    FakeYDL.fail_with = None
    for module in (youtube, twitch, kick):
        monkeypatch.setattr(module.yt_dlp, "YoutubeDL", FakeYDL)
    return FakeYDL


def test_youtube_vertical_asks_for_portrait_at_1080_on_the_short_side(ydl, tmp_path):
    video = youtube.download("https://www.youtube.com/watch?v=abcdefghijk", tmp_path, vertical=True)
    opts = ydl.seen[-1]
    assert opts["format"] == vertical.YOUTUBE_FORMAT and "[aspect_ratio<1]" in opts["format"]
    assert opts["format_sort"] == ["res:1080"]
    assert video.path.name == "abcdefghijk__vertical.mp4" and video.video_id == "abcdefghijk"


def test_the_standard_youtube_download_is_unchanged(ydl, tmp_path):
    video = youtube.download("https://www.youtube.com/watch?v=abcdefghijk", tmp_path)
    opts = ydl.seen[-1]
    assert opts["format"] == STANDARD_YOUTUBE_FORMAT and "format_sort" not in opts
    assert video.path.name == "abcdefghijk.mp4"


def test_a_video_with_no_vertical_version_is_refused_not_swapped(ydl, tmp_path):
    ydl.fail_with = youtube.yt_dlp.utils.DownloadError("ERROR: Requested format is not available")
    with pytest.raises(ValueError, match="no vertical version"):
        youtube.download("https://www.youtube.com/watch?v=abcdefghijk", tmp_path, vertical=True)


@pytest.mark.parametrize(("module", "url", "platform"), [
    (twitch, "https://www.twitch.tv/videos/123456789", "twitch"),
    (kick, "https://kick.com/video/0f0e0d0c-0b0a-0908-0706-050403020100", "kick"),
])
def test_twitch_and_kick_take_the_portrait_rendition(ydl, tmp_path, monkeypatch, module, url, platform):
    if module is kick:
        monkeypatch.setattr(kick, "_impersonation", lambda: {})
    video = module.download(url, tmp_path, vertical=True)
    assert ydl.seen[-1]["format"] == vertical.HLS_FORMAT
    assert video.path.name.endswith("__vertical.mp4")

    ydl.fail_with = module.yt_dlp.utils.DownloadError("ERROR: Requested format is not available")
    with pytest.raises(ValueError, match="vertical version") as e:
        module.download(url, tmp_path, vertical=True)
    if platform == "twitch":
        assert "7 days" in str(e.value)


def test_yt_dlp_picks_the_portrait_rendition_of_a_dual_format_video():
    import yt_dlp

    formats = [
        {"format_id": "wide", "width": 1920, "height": 1080, "vcodec": "avc1", "acodec": "mp4a",
         "ext": "mp4", "url": "x", "protocol": "https", "tbr": 6000},
        {"format_id": "tall", "width": 1080, "height": 1920, "vcodec": "avc1", "acodec": "mp4a",
         "ext": "mp4", "url": "x", "protocol": "https", "tbr": 5000},
    ]
    for f in formats:
        f["aspect_ratio"] = round(f["width"] / f["height"], 2)
    with yt_dlp.YoutubeDL({"quiet": True}) as ydl:
        pick = ydl.build_format_selector(vertical.HLS_FORMAT)
        chosen = list(pick({"formats": formats, "has_merged_format": True, "incomplete_formats": False}))
    assert [f["format_id"] for f in chosen] == ["tall"]


def test_dispatch_passes_the_switch_on_and_files_ignore_it(monkeypatch, tmp_path):
    seen = []
    monkeypatch.setattr(youtube, "download", lambda url, out, vertical=False: seen.append(vertical))
    dispatch.download("https://www.youtube.com/watch?v=abcdefghijk", tmp_path, vertical=True)
    assert seen == [True]


# ---- the vertical copy is its own file -----------------------------------------------


def _ffprobe_h264(cmd, **_kw):
    result = MagicMock()
    result.stdout = "h264\n" if any("codec_name" in str(a) for a in cmd) else "600\n"
    return result


def test_a_cached_horizontal_copy_is_never_used_for_vertical_live(db, tmp_path):
    pytest.importorskip("numpy")
    from core.pipeline import _cached_or_download

    downloads = tmp_path / "downloads"
    downloads.mkdir()
    (downloads / "abcdefghijk.mp4").write_bytes(b"horizontal")
    url = "https://www.youtube.com/watch?v=abcdefghijk"
    with patch("sources.dispatch.download") as download:
        _cached_or_download(url, tmp_path, db, vertical=True)
    download.assert_called_once()
    assert download.call_args.kwargs["vertical"] is True

    (downloads / "abcdefghijk__vertical.mp4").write_bytes(b"vertical")
    db.upsert_video("abcdefghijk", title="A live", channel_name="A channel", duration=600)
    with patch("subprocess.run", side_effect=_ffprobe_h264), patch("sources.dispatch.download") as download:
        video = _cached_or_download(url, tmp_path, db, vertical=True)
    download.assert_not_called()
    assert video.path.name == "abcdefghijk__vertical.mp4"


def test_the_prefetch_fetches_the_vertical_version_too(db, tmp_path, monkeypatch):
    import json

    from core import prefetch, queue

    db.set_flag(queue.PAUSED_KEY, "0")  # the queue starts stopped; nothing prefetches then
    db.add_job("process", json.dumps({"url": "https://www.youtube.com/watch?v=abcdefghijk",
                                      "vertical_live": True}), video_id="abcdefghijk", title="A live")
    started = []

    class Thread:
        def __init__(self, target, args, **_kw):
            started.append(args)

        def start(self):
            pass

        def is_alive(self):
            return False

    monkeypatch.setattr(prefetch.threading, "Thread", Thread)
    prefetch.Prefetcher(tmp_path / "state.db", tmp_path / "downloads").maybe_start(db)
    assert started == [("https://www.youtube.com/watch?v=abcdefghijk", "abcdefghijk", True)]


# ---- where a video came from ------------------------------------------------------------


@pytest.mark.parametrize(("link", "platform"), [
    ("https://www.youtube.com/watch?v=abcdefghijk", "youtube"),
    ("https://youtu.be/abcdefghijk", "youtube"),
    ("https://www.twitch.tv/videos/123", "twitch"),
    ("https://kick.com/video/abc", "kick"),
    ("https://www.instagram.com/reel/abc/", "instagram"),
    ("https://www.tiktok.com/@someone/live", "tiktok"),
    ("https://example.com/my-live", "other"),
    ("not a link", ""),
    ("", ""),
])
def test_the_platform_of_an_original_link(link, platform):
    assert dispatch.platform_of_link(link) == platform


def test_a_videos_source_is_recorded_and_never_blanked(db):
    db.upsert_video("local_abc", title="Downloaded live")
    db.set_video_source("local_abc", "https://www.tiktok.com/@someone/live", "tiktok")
    db.set_video_source("local_abc", "", "")
    row = db.conn.execute("SELECT source_url, source_platform FROM videos WHERE video_id = 'local_abc'").fetchone()
    assert (row["source_url"], row["source_platform"]) == ("https://www.tiktok.com/@someone/live", "tiktok")
