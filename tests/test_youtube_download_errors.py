"""Download failures that a creator can act on, in their own words.

yt-dlp's text is written for people passing command-line flags. What reaches
the window has to be written for someone who pasted a link.
"""

from pathlib import Path

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


# ---- taken for a bot ----------------------------------------------------------------
# YouTube goes by the address a request comes from. A PC on both IPv4 and IPv6
# has two, and one can be refused while the other is served a second later.

BOT = (
    "ERROR: [youtube] aB3dEfGhIjK: Sign in to confirm you’re not a bot. Use "
    "--cookies-from-browser or --cookies for the authentication. See "
    "https://github.com/yt-dlp/yt-dlp/wiki/FAQ#how-do-i-pass-cookies-to-yt-dlp"
)
LINK = "https://www.youtube.com/watch?v=aB3dEfGhIjK"


@pytest.fixture
def asked(monkeypatch, tmp_path):
    """yt-dlp stood in for. `asked.refuses(options)` says whether YouTube
    takes a request made with those options for a bot; `asked` lists the
    address each request left from (None: whichever the PC picks)."""
    from sources import ytdlp_common

    class Asked(list):
        refuses = staticmethod(lambda options: False)
        failure = BOT                   # or a function of the options, for a failure that depends on the way

    made = Asked()
    made.how = []                       # the keyword arguments of each request

    class Standin:
        def __init__(self, options=None, *_a, **_k):
            self.options = dict(options or {})

        def __enter__(self):
            return self

        def __exit__(self, *_a):
            return False

        def extract_info(self, url, download=False, **how):
            made.append(self.options.get("source_address"))
            made.how.append({"download": download, **how})
            if made.refuses(self.options):
                failure = made.failure
                raise yt_dlp.utils.DownloadError(failure(self.options) if callable(failure) else failure)
            if download:
                (tmp_path / "aB3dEfGhIjK.mp4").write_bytes(b"video")
            return {"id": "aB3dEfGhIjK", "title": "A video", "duration": 12.0, "channel": "Some Channel"}

    monkeypatch.setattr(youtube.yt_dlp, "YoutubeDL", Standin)
    monkeypatch.setattr(ytdlp_common, "_session", {"ipv4_only": False})
    monkeypatch.setattr(ytdlp_common, "_cookies", "")
    monkeypatch.setattr(ytdlp_common, "_cookie_file", None)
    made.options = []                   # every request's options, for the cookies
    real = Standin.extract_info

    def recorded(self, url, download=False, **how):
        made.options.append(self.options)
        return real(self, url, download=download, **how)

    monkeypatch.setattr(Standin, "extract_info", recorded)
    return made


def test_a_download_that_works_is_asked_for_once_as_before(asked, tmp_path):
    video = youtube.download(LINK, tmp_path)

    assert video.path == tmp_path / "aB3dEfGhIjK.mp4" and video.title == "A video"
    assert asked == [None, None]            # the live-stream probe, then the download


def test_refused_as_a_bot_it_is_asked_again_over_ipv4(asked, tmp_path):
    """The probe is refused and served over IPv4; the download that follows
    starts there, rather than being refused first as well."""
    asked.refuses = lambda options: "source_address" not in options

    video = youtube.download(LINK, tmp_path)

    assert video.path.exists()
    assert asked == [None, "0.0.0.0", "0.0.0.0"]


def test_refused_both_ways_it_says_so_in_plain_words(asked, tmp_path):
    asked.refuses = lambda options: True

    with pytest.raises(ValueError) as told:
        youtube.download(LINK, tmp_path)

    message = str(told.value)
    assert "treating this network as a bot" in message and "link is fine" in message
    # Not yt-dlp's flags and wiki: the setting that lets the app ask as you.
    assert "--cookies" not in message and "github" not in message.lower()
    assert "Settings" in message and "YouTube cookies" in message
    assert asked == [None, "0.0.0.0"]       # asked twice, not over and over


def test_ipv4_is_not_kept_once_it_is_the_one_refused(asked):
    """Later in the session the IPv4 address is the one taken for a bot: the
    ordinary request is tried, and is where the next one starts again."""
    from sources import ytdlp_common

    asked.refuses = lambda options: "source_address" not in options
    ytdlp_common.extract_info(LINK, {"quiet": True})
    assert asked == [None, "0.0.0.0"] and ytdlp_common._session["ipv4_only"]

    asked.refuses = lambda options: "source_address" in options
    ytdlp_common.extract_info(LINK, {"quiet": True})
    assert asked[2:] == ["0.0.0.0", None] and not ytdlp_common._session["ipv4_only"]

    asked.refuses = lambda options: False
    ytdlp_common.extract_info(LINK, {"quiet": True})
    assert asked[4:] == [None]


def test_another_failure_is_not_asked_for_twice(asked, tmp_path):
    asked.refuses = lambda options: True
    asked.failure = "something new"

    with pytest.raises(yt_dlp.utils.DownloadError, match="something new"):
        youtube.download(LINK, tmp_path)

    assert asked == [None]


def test_an_address_the_caller_chose_is_left_alone(asked):
    from sources import ytdlp_common

    asked.refuses = lambda options: True

    with pytest.raises(yt_dlp.utils.DownloadError):
        ytdlp_common.extract_info(LINK, {"source_address": "192.0.2.1"})

    assert asked == ["192.0.2.1"]


def test_a_twitch_link_is_asked_for_the_way_it_always_was(asked):
    """Also once YouTube has needed IPv4, and whatever the answer says."""
    from sources import ytdlp_common

    vod = "https://www.twitch.tv/videos/123456789"
    asked.refuses = lambda options: "source_address" not in options
    ytdlp_common.extract_info(LINK, {})
    assert ytdlp_common._session["ipv4_only"]

    asked.refuses = lambda options: False
    ytdlp_common.extract_info(vod, {})
    assert asked[2:] == [None]

    asked.refuses = lambda options: True
    with pytest.raises(yt_dlp.utils.DownloadError):
        ytdlp_common.extract_info(vod, {})
    assert asked[3:] == [None]              # and not asked a second way


def test_any_other_failure_over_ipv4_puts_the_session_back(asked):
    """Starting on IPv4 is a habit, not a need: a timeout there must not keep
    every later request on it until the app is restarted."""
    from sources import ytdlp_common

    asked.refuses = lambda options: "source_address" not in options
    ytdlp_common.extract_info(LINK, {})
    assert ytdlp_common._session["ipv4_only"]

    asked.refuses = lambda options: True
    asked.failure = "ERROR: Unable to download API page: timed out"
    with pytest.raises(yt_dlp.utils.DownloadError, match="timed out"):
        ytdlp_common.extract_info(LINK, {})
    assert asked[2:] == ["0.0.0.0"] and not ytdlp_common._session["ipv4_only"]

    asked.refuses = lambda options: False
    ytdlp_common.extract_info(LINK, {})
    assert asked[3:] == [None]


def test_the_refusal_is_what_is_said_when_ipv4_fails_another_way(asked, tmp_path):
    """Refused as a bot, and then no way out over IPv4 at all: the refusal is
    the cause, so that is the message, not the connection error behind it."""
    asked.refuses = lambda options: True
    asked.failure = lambda options: (
        "ERROR: Unable to download API page: unreachable network" if "source_address" in options else BOT)

    with pytest.raises(ValueError, match="treating this network as a bot"):
        youtube.download(LINK, tmp_path)

    assert asked == [None, "0.0.0.0"]


def test_every_lookup_gets_the_second_way_not_only_the_download(asked):
    """The details of a video already on disk are asked for through the same
    function, as are the watched channel's videos, which are asked for
    without resolving their formats."""
    from sources import dispatch, vod_finder

    asked.refuses = lambda options: "source_address" not in options
    assert dispatch.metadata(LINK) == ("A video", "Some Channel")
    assert asked == [None, "0.0.0.0"]

    vod_finder._extract(LINK, flat=False)
    assert asked[2:] == ["0.0.0.0"]
    assert asked.how[0] == {"download": False} and asked.how[-1] == {"download": False, "process": False}


# ---- YouTube cookies (Settings → Advanced settings, #136) ------------------------

def _cookie_opts(options: dict) -> dict:
    return {k: v for k, v in options.items() if k in ("cookiefile", "cookiesfrombrowser")}


def test_cookies_set_are_not_used_while_youtube_serves_without_them(asked, tmp_path, monkeypatch):
    from sources import ytdlp_common

    jar = tmp_path / ytdlp_common.COOKIES_FILE
    jar.write_text("# Netscape HTTP Cookie File\n", encoding="utf-8")
    ytdlp_common.set_cookies("file", tmp_path)
    asked.refuses = lambda options: "source_address" not in options     # IPv4 gets through

    youtube.download(LINK, tmp_path)

    assert all(not _cookie_opts(o) for o in asked.options)


def test_refused_both_ways_it_asks_once_more_with_the_cookies_file(asked, tmp_path, monkeypatch):
    from sources import ytdlp_common

    jar = tmp_path / ytdlp_common.COOKIES_FILE
    jar.write_text("# Netscape HTTP Cookie File\n", encoding="utf-8")
    ytdlp_common.set_cookies("file", tmp_path)
    asked.refuses = lambda options: "cookiefile" not in options

    seen = []
    real_refuses = asked.refuses

    def refuses(options):
        if "cookiefile" in options:   # what the file holds while yt-dlp has it
            seen.append(Path(options["cookiefile"]).read_text(encoding="utf-8"))
        return real_refuses(options)

    asked.refuses = refuses
    video = youtube.download(LINK, tmp_path)

    assert video.path.exists()
    assert [_cookie_opts(o) for o in asked.options[:2]] == [{}, {}]
    signed = asked.options[2]
    # A copy, gone afterwards: yt-dlp writes its cookie jar back to the file it is given.
    copy = Path(signed["cookiefile"])
    assert copy != jar and copy.name.startswith("clips-cookies-") and not copy.exists()
    assert seen[0] == "# Netscape HTTP Cookie File\n" and jar.read_text(encoding="utf-8") == seen[0]
    # Over IPv4, both ways having been refused; and with QuickJS to run YouTube's player.
    assert signed["source_address"] == "0.0.0.0" and "quickjs" in signed["js_runtimes"]


def test_firefox_is_asked_for_its_own_cookies(asked, monkeypatch):
    from sources import ytdlp_common

    monkeypatch.setattr(ytdlp_common, "_cookies", "firefox")
    asked.refuses = lambda options: "cookiesfrombrowser" not in options

    ytdlp_common.extract_info(LINK, {"quiet": True})

    assert _cookie_opts(asked.options[-1]) == {"cookiesfrombrowser": ("firefox",)}
    assert len(asked.options) == 3


def test_refused_even_with_cookies_it_says_so(asked, tmp_path, monkeypatch):
    from sources import ytdlp_common

    monkeypatch.setattr(ytdlp_common, "_cookies", "firefox")
    asked.refuses = lambda options: True

    with pytest.raises(ValueError) as told:
        youtube.download(LINK, tmp_path)

    assert "even with the YouTube cookies" in str(told.value)
    assert len(asked.options) == 3


def test_a_cookies_file_gone_since_says_to_choose_it_again(asked, tmp_path, monkeypatch):
    from sources import ytdlp_common

    ytdlp_common.set_cookies("file", tmp_path)          # nothing saved there
    asked.refuses = lambda options: True

    with pytest.raises(yt_dlp.utils.DownloadError, match="not there any more"):
        ytdlp_common.extract_info(LINK, {"quiet": True})


def test_the_cookies_are_tried_when_the_other_way_fails_for_its_own_reason(asked, monkeypatch):
    # The ordinary request is refused as a bot, and IPv4 then times out (or
    # the reverse, on a PC whose IPv6 to YouTube stalls): the refusal is what
    # stands in the way, so the cookies are tried the way that reached YouTube.
    from sources import ytdlp_common

    monkeypatch.setattr(ytdlp_common, "_cookies", "firefox")
    asked.refuses = lambda options: "cookiesfrombrowser" not in options
    asked.failure = lambda options: "Read timed out" if options.get("source_address") else BOT

    ytdlp_common.extract_info(LINK, {"quiet": True})

    assert len(asked.options) == 3 and "source_address" not in asked.options[2]
    assert _cookie_opts(asked.options[2]) == {"cookiesfrombrowser": ("firefox",)}


def test_the_command_line_reads_the_cookies_setting_too():
    text = (Path(__file__).resolve().parent.parent / "main.py").read_text(encoding="utf-8")
    assert 'ytdlp_common.set_cookies(config.get("youtube_cookies"), config["paths"]["data_dir"])' in text


@pytest.mark.parametrize("value, data_dir, kind, file", [
    ("file", "D:/data", "file", Path("D:/data") / "youtube_cookies.txt"),
    ("Firefox", "D:/data", "firefox", Path("D:/data") / "youtube_cookies.txt"),
    # A path is never taken as the file: only the one in the data folder is.
    ("C:/Users/me/cookies.txt", "D:/data", "", Path("D:/data") / "youtube_cookies.txt"),
    (None, None, "", None),
])
def test_the_setting_names_a_kind_never_a_path(value, data_dir, kind, file, monkeypatch):
    from sources import ytdlp_common

    monkeypatch.setattr(ytdlp_common, "_cookies", "")
    monkeypatch.setattr(ytdlp_common, "_cookie_file", None)
    ytdlp_common.set_cookies(value, data_dir)
    assert ytdlp_common.cookies() == kind and ytdlp_common._cookie_file == file


@pytest.mark.parametrize("text, ok", [
    ("# Netscape HTTP Cookie File\n", True),
    ("# HTTP Cookie File\r\n.youtube.com\tTRUE\t/\tTRUE\t0\tSID\tx\r\n", True),
    # yt-dlp reads the first line for the header: without it there, it refuses the file.
    (".youtube.com\tTRUE\t/\tTRUE\t0\tSID\tx\n", False),
    ("\n# Netscape HTTP Cookie File\n", False),
    ("﻿# Netscape HTTP Cookie File\n", True),
    ("SID=x; HSID=y\n", False),
    ("", False),
    ("# Netscape HTTP Cookie File\n" + "x" * (1024 * 1024), False),
], ids=["header", "header-crlf", "no-header", "blank-first", "bom", "cookie-header", "empty", "too-big"])
def test_only_a_cookies_txt_is_taken(text, ok):
    from sources import ytdlp_common

    assert ytdlp_common.cookies_text_ok(text) is ok
