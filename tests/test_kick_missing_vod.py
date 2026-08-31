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
"""

import pytest
import yt_dlp

from sources import kick

VOD_URL = "https://kick.com/deepak/videos/01a01a47-04a8-7432-bdfb-57393cbe75f5"


def _fail_with(monkeypatch, message):
    """Make extract_info raise, without touching the network."""

    class FakeYDL:
        def __init__(self, *a, **k): pass
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def extract_info(self, *a, **k):
            raise yt_dlp.utils.DownloadError(message)

    monkeypatch.setattr(yt_dlp, "YoutubeDL", FakeYDL)


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
