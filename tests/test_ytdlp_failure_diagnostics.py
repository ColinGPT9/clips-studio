"""A download failure that says which yt-dlp produced it.

Twitch, Kick and YouTube change things regularly and yt-dlp fixes them within
days, but the copy inside Clips Kitty is frozen at build time
(clips-studio.spec). So the one useful fact about a download that stopped
working — the version — is not in the failure at all, and "Kick stopped
working" arrives with nothing to triage it. See issue #39 and KNOWN-ISSUES.md.

The version lookup and the wording live in `sources.ytdlp_common`, which
imports without yt_dlp, so everything below runs on CI. The wiring (dispatch
adding the note to whatever the platform raised) is checked at the end, where
yt_dlp exists.
"""

import sys
import types

import pytest

from sources import ytdlp_common

# The exact shape of an extractor break: yt-dlp names the extractor, says it
# could not read the page, and appends its own "confirm you are on the latest
# version" hint. That last line is upstream saying the copy may be stale.
EXTRACTOR = (
    "ERROR: [youtube] aB3dEfGhIjK: Unable to extract player response; please "
    "report this issue on https://github.com/yt-dlp/yt-dlp/issues?q= , filling "
    "out the appropriate issue template. Confirm you are on the latest version "
    "using  yt-dlp -U"
)

# Taken from the same wording the download paths already see.
AGE = (
    "ERROR: [youtube] 7XuQx54kooM: Sign in to confirm your age. Use "
    "--cookies-from-browser or --cookies for the authentication."
)
BOT = "ERROR: [youtube] aB3dEfGhIjK: Sign in to confirm you're not a bot."
TIMEOUT = "ERROR: Unable to download webpage: The read operation timed out"

# The wording that makes a note a *suggestion* to update rather than just a
# version given as context. Asserting on this is what separates "said so" from
# "did not send them looking for a version".
SENT_UPSTREAM = "may already have the fix"


@pytest.fixture(autouse=True)
def _fresh_version_cache(monkeypatch):
    """`installed_version` caches on purpose; each test starts uncached."""
    monkeypatch.setattr(ytdlp_common, "_version", None)


# ---- the version itself -------------------------------------------------------------


def test_the_version_is_read_from_the_installed_yt_dlp():
    version = ytdlp_common.installed_version()
    assert isinstance(version, str) and version
    # CI installs no yt_dlp, so there it is the fallback; either way this is the
    # version the app would report, never one written into the source.
    assert version == "unknown" or version[0].isdigit()


def test_no_yt_dlp_is_a_version_and_not_a_second_exception(monkeypatch):
    """A diagnostic must not become the failure. This runs in a checkout
    without yt_dlp (which is what CI is), and in an install where the bundled
    copy is somehow unreadable."""
    monkeypatch.setitem(sys.modules, "yt_dlp.version", None)
    monkeypatch.setitem(sys.modules, "yt_dlp", None)

    assert ytdlp_common.installed_version() == "unknown"
    # And the failure it was called for still gets its note.
    assert ytdlp_common.failure_note(RuntimeError(EXTRACTOR)) is not None


def test_the_version_is_read_once(monkeypatch):
    """Cached deliberately: a failure can be reported while the interpreter is
    shutting down, and a diagnostic that re-imported on every error could be
    the thing that fails."""
    monkeypatch.setitem(sys.modules, "yt_dlp.version", None)
    assert ytdlp_common.installed_version() == "unknown"

    # Even if yt_dlp becomes importable afterwards, the first answer stands.
    arrived = types.ModuleType("yt_dlp.version")
    arrived.__version__ = "9999.1.1"
    monkeypatch.setitem(sys.modules, "yt_dlp.version", arrived)
    assert ytdlp_common.installed_version() == "unknown"


# ---- what the note says -------------------------------------------------------------


def test_a_site_change_names_the_version_and_points_upstream():
    note = ytdlp_common.failure_note(RuntimeError(EXTRACTOR))

    assert ytdlp_common.installed_version() in note
    # The suggestion is the whole point for this failure: the fix exists and
    # the app is simply carrying the old copy.
    assert SENT_UPSTREAM in note and "yt-dlp/releases" in note


def test_a_timeout_is_given_the_version_but_not_the_version_excuse():
    note = ytdlp_common.failure_note(RuntimeError(TIMEOUT))

    assert ytdlp_common.installed_version() in note
    assert SENT_UPSTREAM not in note and "yt-dlp/releases" not in note


def test_a_video_that_needs_a_sign_in_is_not_told_to_update():
    for message in (AGE, BOT):
        note = ytdlp_common.failure_note(RuntimeError(message))

        assert ytdlp_common.installed_version() in note
        assert SENT_UPSTREAM not in note and "yt-dlp/releases" not in note


def test_an_unrecognised_failure_gets_the_version_and_no_claim():
    note = ytdlp_common.failure_note(RuntimeError("ERROR: something new"))

    assert note == f"Clips Kitty's bundled yt-dlp is {ytdlp_common.installed_version()}."


def test_the_versions_own_hint_outranks_a_403_in_the_same_message():
    """yt-dlp prints "confirm you are on the latest version" only alongside an
    extractor error, so when it appears it is the more specific evidence — even
    if a status code sits in the same block."""
    note = ytdlp_common.failure_note(RuntimeError(f"{EXTRACTOR} (HTTP Error 403: Forbidden)"))

    assert SENT_UPSTREAM in note


def test_a_deleted_kick_vod_keeps_the_apps_own_words(_ytdlp):
    """The sources translate the failures a creator can act on into ValueError.
    Those already say what happened, so nothing is appended to them."""
    from sources import kick

    def deleted():
        raise _ytdlp.utils.DownloadError("ERROR: HTTP Error 404: Unable to download JSON metadata")

    with pytest.raises(ValueError) as caught:
        with kick._gone_or_raise():
            deleted()

    assert "no longer exists" in str(caught.value)
    assert ytdlp_common.installed_version() not in str(caught.value)


def test_a_failure_the_app_explained_gets_nothing_appended():
    assert ytdlp_common.failure_note(ValueError("That Kick VOD no longer exists.")) is None


def test_a_cancelled_download_is_not_a_failure_to_explain():
    from core.cancel import CancelledError

    assert ytdlp_common.failure_note(CancelledError("aB3dEfGhIjK")) is None


# ---- what the note must never carry -------------------------------------------------


def test_the_note_never_echoes_the_failure():
    """Only the version is interpolated, so a private URL, a cookie header or a
    token that the error text carries cannot be put back into a message the job
    layer scrubs on the way out (server/jobs.py)."""
    secret = "Authorization: Bearer sk-live-DO-NOT-PRINT"
    private = "https://www.youtube.com/watch?v=SECRET1D&list=PRIVATE"
    note = ytdlp_common.failure_note(RuntimeError(f"ERROR: Unable to extract data from {private}\n{secret}"))

    assert "sk-live" not in note
    assert "SECRET1D" not in note and "PRIVATE" not in note
    assert secret not in note


def test_the_note_is_the_version_and_nothing_from_the_original():
    """Two unrelated failures of the same kind produce the same note: proof the
    text is not merely formatted around the original error."""
    first = ytdlp_common.failure_note(RuntimeError("ERROR: The read operation timed out"))
    second = ytdlp_common.failure_note(RuntimeError("ERROR: getaddrinfo ENOTFOUND www.example.test"))

    assert first == second


# ---- the wiring: dispatch adds it to whatever the platform raised --------------------
# Skipped on CI, which has no yt_dlp and so cannot import the source modules.
# The logic above is what carries the behaviour; this is the check that it is
# actually called (see tests/test_input_safety.py for the same split).


@pytest.fixture
def _ytdlp():
    return pytest.importorskip("yt_dlp", reason="sources.dispatch imports the platform modules; not installed on CI")


def test_a_failing_download_carries_its_version_out(monkeypatch, tmp_path, _ytdlp):
    from sources import dispatch, youtube

    def fails(*_a, **_k):
        raise _ytdlp.utils.DownloadError(EXTRACTOR)

    monkeypatch.setattr(youtube, "download", fails)

    with pytest.raises(_ytdlp.utils.DownloadError) as told:
        dispatch.download("https://youtu.be/aB3dEfGhIjK", tmp_path)

    message = str(told.value)
    assert EXTRACTOR in message                                  # the reason survives intact
    assert ytdlp_common.installed_version() in message
    assert SENT_UPSTREAM in message and "yt-dlp/releases" in message


def test_the_apps_own_message_is_not_touched_by_dispatch(monkeypatch, tmp_path, _ytdlp):
    from sources import dispatch, youtube

    told_off = "YouTube will not serve this video to anyone who is not signed in, because it is age-restricted."

    def fails(*_a, **_k):
        raise ValueError(told_off)

    monkeypatch.setattr(youtube, "download", fails)

    with pytest.raises(ValueError) as caught:
        dispatch.download("https://youtu.be/aB3dEfGhIjK", tmp_path)

    assert str(caught.value) == told_off


def test_a_download_that_works_is_returned_untouched(monkeypatch, tmp_path, _ytdlp):
    from core.models import DownloadedVideo
    from sources import dispatch, youtube

    video = DownloadedVideo(
        video_id="aB3dEfGhIjK", title="A video", path=tmp_path / "a.mp4",
        duration=12.0, channel="Some Channel",
    )
    monkeypatch.setattr(youtube, "download", lambda *_a, **_k: video)

    assert dispatch.download("https://youtu.be/aB3dEfGhIjK", tmp_path) is video
