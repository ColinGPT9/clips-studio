"""Download failures that a creator can act on, in their own words.

yt-dlp's text is written for people passing command-line flags. What reaches
the window has to be written for someone who pasted a link.
"""

import pytest

yt_dlp = pytest.importorskip("yt_dlp")

from sources import youtube  # noqa: E402

AGE = (
    "ERROR: [youtube] 7XuQx54kooM: Sign in to confirm your age. Use "
    "--cookies-from-browser or --cookies for the authentication. See "
    "https://github.com/yt-dlp/yt-dlp/wiki/FAQ#how-do-i-pass-cookies-to-yt-dlp"
)


def test_an_age_gated_video_says_so_without_mentioning_cookies():
    message = youtube._friendly_message(AGE)
    assert message is not None
    assert "age-restricted" in message
    # The wiki links and the flag names are the part that reads as a crash.
    assert "cookies" not in message.lower()
    assert "github" not in message.lower()
    assert "Twitch" in message  # what to do instead


def test_a_throttled_network_is_not_blamed_on_the_link():
    message = youtube._friendly_message("HTTP Error 403: Forbidden")
    assert message is not None and "link is fine" in message


def test_an_unknown_failure_keeps_its_own_words():
    # Flattening these into an apology would hide the only clue there is.
    assert youtube._friendly_message("some new yt-dlp failure") is None


def test_an_unknown_failure_is_reraised_untouched():
    with pytest.raises(yt_dlp.utils.DownloadError):
        with youtube._friendly_errors():
            raise yt_dlp.utils.DownloadError("something new")


def test_a_known_failure_becomes_advice():
    with pytest.raises(ValueError, match="age-restricted"):
        with youtube._friendly_errors():
            raise yt_dlp.utils.DownloadError(AGE)


def test_the_probe_gets_the_friendly_message_too(monkeypatch, tmp_path):
    """The regression: the probe is the first network call and the likeliest
    to fail, and it was the one call the mapping did not cover."""

    class Failing:
        def __init__(self, *_a, **_k):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_a):
            return False

        def extract_info(self, *_a, **_k):
            raise yt_dlp.utils.DownloadError(AGE)

    monkeypatch.setattr(youtube.yt_dlp, "YoutubeDL", Failing)
    with pytest.raises(ValueError, match="age-restricted"):
        youtube.download("https://youtu.be/7XuQx54kooM", tmp_path)
