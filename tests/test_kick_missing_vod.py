"""A deleted Kick VOD is a normal event, not a failure.

Kick removes VODs after a retention window. The link keeps its shape, the
channel stays up, and one day the video is simply gone — so this is something
users hit routinely rather than an outage.

Issue #87 reported it as "Kick is broken", and the raw error supported that
reading: "HTTP Error 404: Unable to download JSON metadata". It was not broken.
Checked at the time: the channel's API answered 200 and listed five current
VODs, the reported id was not among them, and another VOD from that same
channel downloaded at 1080p60 through the same yt-dlp and the same extractor.

So the tooling was fine and the message was the bug — the same shape as the raw
YouTube 403 in #81 and the discarded FFmpeg error in #84.

#161 was the other way round: a public VOD, playing on Kick, reported as
deleted. Kick gives new VODs ids yt-dlp's API doesn't know (version 7 UUIDs),
so the 404 is only "deleted" once the API Kick's own site reads hasn't got the
VOD either.
"""

import io
import json

import pytest

yt_dlp = pytest.importorskip("yt_dlp", reason="yt-dlp is not installed on CI")

from yt_dlp.networking import Response  # noqa: E402
from yt_dlp.networking.exceptions import HTTPError  # noqa: E402

from sources import kick  # noqa: E402

VOD_URL = "https://kick.com/somestreamer/videos/0b5e4f7a-3c21-4d8e-9a6f-2e7d1c4b8a90"
NEW_ID = "01a154ab-1a70-70b6-8a93-2ad91a5dd968"
NEW_URL = f"https://kick.com/somestreamer/videos/{NEW_ID}"
CHANNEL_API = "https://kick.com/api/v2/channels/somestreamer"
VIDEO_API = f"https://web.kick.com/api/v1/channels/4242/videos/{NEW_ID}"
RECORDING = "https://stream.kick.com/example/media/hls/master.m3u8"
NOT_FOUND = "ERROR: [kick:vod] Unable to download JSON metadata: HTTP Error 404: Not Found"


def _install(monkeypatch, *, probe=None, web=None):
    """A YoutubeDL that touches no network: extract_info raises `probe` for a
    Kick page (or answers with an empty info), downloads anything else, and
    urlopen answers from `web`, a URL -> JSON table where anything missing is
    a 404. Returns what was asked for, in order."""
    asked = {"extract": [], "urlopen": []}

    class FakeYDL:
        def __init__(self, opts=None, *a, **k):
            self.opts = opts or {}

        def __enter__(self): return self
        def __exit__(self, *a): return False

        def extract_info(self, url, download=False):
            asked["extract"].append((url, download))
            if probe is not None and url.startswith("https://kick.com/"):
                raise yt_dlp.utils.DownloadError(probe)
            if download:
                out = self.opts["outtmpl"].replace("%(ext)s", "mp4")
                with open(out, "wb") as f:
                    f.write(b"\0")
            return {"title": "master", "duration": 0}

        def urlopen(self, request):
            asked["urlopen"].append(request.url)
            if request.url not in (web or {}):
                raise HTTPError(Response(io.BytesIO(b""), request.url, {}, status=404))
            return Response(io.BytesIO(json.dumps(web[request.url]).encode()), request.url, {})

    monkeypatch.setattr(yt_dlp, "YoutubeDL", FakeYDL)
    return asked


def _fail_with(monkeypatch, message):
    """Make extract_info raise, without touching the network."""
    _install(monkeypatch, probe=message)


def _web_vod(**over):
    data = {
        "id": NEW_ID,
        "title": "Customs with chat",
        "duration": 10981,
        "channel": {"id": 4242, "slug": "somestreamer", "username": "SomeStreamer"},
        "category": {"id": 3, "name": "Fortnite", "slug": "fortnite"},
        "is_live": False,
        "status": "public",
        "recording_url": RECORDING,
        **over,
    }
    return {CHANNEL_API: {"id": 4242, "slug": "somestreamer"}, VIDEO_API: {"data": data}}


def test_a_deleted_vod_is_explained_not_reported_as_a_404(monkeypatch, tmp_path):
    _fail_with(monkeypatch, "ERROR: Unable to download JSON metadata: HTTP Error 404: Not Found")

    with pytest.raises(ValueError) as excinfo:
        kick.download(VOD_URL, tmp_path)

    message = str(excinfo.value)
    assert "no longer exists" in message
    assert "404" not in message, "the raw status code is what made this look like a crash"
    assert "HTTP Error" not in message


def test_the_explanation_says_why_and_what_to_do(monkeypatch, tmp_path):
    """"It's gone" alone invites a bug report. Saying Kick expires VODs, and
    where to find the ones that remain, is what stops the next #87."""
    _fail_with(monkeypatch, "ERROR: Unable to download JSON metadata: HTTP Error 404: Not Found")

    with pytest.raises(ValueError) as excinfo:
        kick.download(VOD_URL, tmp_path)

    message = str(excinfo.value).lower()
    assert "deletes vods" in message, "must say why the link stopped working"
    assert "videos tab" in message, "must say where to find one that still exists"
    assert "kick itself is working" in message, "must not read as a Kick outage"


def test_other_failures_keep_their_own_error(monkeypatch, tmp_path):
    """Only the 404 is translated. A real outage has to report itself
    accurately, or the next genuine breakage gets blamed on a deleted VOD."""
    _fail_with(monkeypatch, "ERROR: Unable to download JSON metadata: HTTP Error 503: Service Unavailable")

    with pytest.raises(yt_dlp.utils.DownloadError) as excinfo:
        kick.download(VOD_URL, tmp_path)

    assert "503" in str(excinfo.value)


# ---- #161: a new VOD id yt-dlp's API doesn't know --------------------------


def test_a_vod_kicks_site_has_is_downloaded_from_its_recording(monkeypatch, tmp_path):
    asked = _install(monkeypatch, probe=NOT_FOUND, web=_web_vod())

    got = kick.download(NEW_URL, tmp_path)

    assert asked["urlopen"] == [CHANNEL_API, VIDEO_API]
    assert asked["extract"][-1] == (RECORDING, True), "the recording is what is downloaded"
    assert got.video_id == f"kick_{NEW_ID}"
    assert got.path == tmp_path / f"kick_{NEW_ID}.mp4" and got.path.exists()
    # The site's metadata, not the playlist's ("master", no length).
    assert (got.title, got.duration, got.channel) == ("Customs with chat", 10981.0, "SomeStreamer")
    assert [g["name"] for g in got.games] == ["Fortnite"]


def test_a_vod_neither_api_has_is_still_explained_as_deleted(monkeypatch, tmp_path):
    # The channel is there, the VOD isn't: Kick deleted it.
    asked = _install(monkeypatch, probe=NOT_FOUND, web={CHANNEL_API: {"id": 4242}})

    with pytest.raises(ValueError) as excinfo:
        kick.download(NEW_URL, tmp_path)

    assert "no longer exists" in str(excinfo.value)
    assert asked["urlopen"] == [CHANNEL_API, VIDEO_API]


def test_a_link_without_its_channel_is_not_looked_up(monkeypatch, tmp_path):
    # The newer API needs the channel's number, which only a link naming the
    # channel can give.
    asked = _install(monkeypatch, probe=NOT_FOUND, web=_web_vod())

    with pytest.raises(ValueError) as excinfo:
        kick.download(f"https://kick.com/video/{NEW_ID}", tmp_path)

    assert "no longer exists" in str(excinfo.value)
    assert asked["urlopen"] == []


def test_an_outage_is_not_looked_up_either(monkeypatch, tmp_path):
    asked = _install(monkeypatch, probe="ERROR: HTTP Error 503: Service Unavailable", web=_web_vod())

    with pytest.raises(yt_dlp.utils.DownloadError):
        kick.download(NEW_URL, tmp_path)

    assert asked["urlopen"] == []


def test_a_vod_still_streaming_says_so(monkeypatch, tmp_path):
    _install(monkeypatch, probe=NOT_FOUND, web=_web_vod(is_live=True))

    with pytest.raises(ValueError) as excinfo:
        kick.download(NEW_URL, tmp_path)

    assert "still being streamed" in str(excinfo.value)


def test_an_answer_without_a_recording_is_deleted(monkeypatch, tmp_path):
    _install(monkeypatch, probe=NOT_FOUND, web=_web_vod(recording_url=None))

    with pytest.raises(ValueError) as excinfo:
        kick.download(NEW_URL, tmp_path)

    assert "no longer exists" in str(excinfo.value)


def test_a_vod_yt_dlp_finds_is_downloaded_as_before(monkeypatch, tmp_path):
    asked = _install(monkeypatch, web=_web_vod())

    got = kick.download(VOD_URL, tmp_path)

    assert asked["urlopen"] == []
    assert asked["extract"][-1] == (VOD_URL, True)
    assert got.title == "master"
