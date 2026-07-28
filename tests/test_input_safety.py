"""Values that arrive as data must not be able to escape their folder.

CodeQL raised 50 alerts against this repo; these cover the ones that were
genuinely reachable rather than the ones that describe the app working as
intended (a creator picking any video to import and any folder to export to
is the product, not a vulnerability).

Two shapes of real bug, both from stored configuration rather than a file
dialog:

  * `image_asset` inside a saved branding profile was joined straight onto
    the assets folder and handed to FFmpeg as an input path.
  * `voice_id` from the API became both `voices_dir / f"{name}.onnx"` and
    the `-m` argument of a piper subprocess. A leading "-" there is not
    traversal — it turns a filename into a flag.
"""

from pathlib import Path

from core.paths import safe_name, within

TRAVERSALS = [
    "../../../etc/passwd",
    "..\\..\\Windows\\System32\\config\\SAM",
    "sub/dir/file.png",
    "sub\\dir\\file.png",
    "..",
    ".",
    "",
]


def test_safe_name_accepts_a_generated_asset_name():
    """What the upload endpoint actually produces: sha256[:16] + extension."""
    assert safe_name("a1b2c3d4e5f6a7b8.png") == "a1b2c3d4e5f6a7b8.png"
    assert safe_name("logo with spaces.webp") == "logo with spaces.webp"


def test_safe_name_rejects_traversal():
    for name in TRAVERSALS:
        assert safe_name(name) is None, f"accepted {name!r}"


def test_safe_name_rejects_absolute_paths():
    assert safe_name("/etc/passwd") is None
    assert safe_name("C:\\Windows\\x.png") is None


def test_safe_name_rejects_leading_dash():
    """Not a path problem. These names are also passed to piper and ffmpeg as
    arguments, where a leading dash makes the value a flag instead of a
    filename."""
    assert safe_name("-rf") is None
    assert safe_name("--data-dir=/tmp") is None


def test_within_catches_what_the_name_check_missed():
    base = Path("data/branding/assets")
    assert within(base, base / "x.png")
    assert not within(base, base / ".." / ".." / "x.png")


def test_voice_id_falls_back_instead_of_reaching_the_command_line():
    """A hostile voice id must not reach `piper -m <value>`.

    Falling back to the language default rather than raising is deliberate:
    a bad id should dub in the standard voice, not abort a batch export
    halfway through.
    """
    from multilingual.voices import DEFAULTS, resolve

    for hostile in ("--data-dir=/tmp", "-m", "../../../../etc/passwd", "..", "a/b"):
        name, speaker = resolve(hostile, "en")
        assert name == DEFAULTS["en"], f"{hostile!r} survived as {name!r}"
        assert speaker is None


def test_real_voice_ids_still_work():
    from multilingual.voices import resolve

    assert resolve("fr_FR-upmc-medium", "fr") == ("fr_FR-upmc-medium", None)
    assert resolve("fr_FR-upmc-medium#1", "fr") == ("fr_FR-upmc-medium", 1)
    assert resolve(None, "de")[0] == "de_DE-thorsten-medium"


def test_watermark_refuses_an_asset_outside_its_folder(tmp_path):
    from video_editor import watermark

    cfg = {"image_asset": "../../../../Windows/System32/drivers/etc/hosts"}
    try:
        watermark.apply_image(tmp_path / "v.mp4", cfg, (1080, 1920), tmp_path / "assets")
    except ValueError as e:
        assert "invalid branding asset name" in str(e)
    else:  # pragma: no cover
        raise AssertionError("traversing asset name was accepted")


def test_has_image_does_not_probe_outside_the_assets_folder(tmp_path):
    from video_editor import watermark

    assert not watermark.has_image(
        {"type": "image", "image_asset": "../../secret.png"}, tmp_path
    )


def test_url_routing_matches_the_host_not_a_substring():
    """`youtube.com/watch?v=x&ref=kick.com` used to be routed to Kick.

    Tests `host_matches` rather than `sources.kick.is_kick_url`, because
    importing that module pulls in yt_dlp. CI installs pyyaml/ruff/pytest/
    requests and nothing else on purpose — requirements.txt would drag ~4 GB
    of PyTorch onto a runner to check some string handling. The logic under
    test lives here anyway; the source modules are one-line wrappers.
    """
    from sources.urlmatch import host_matches

    assert host_matches("https://kick.com/video/abc", "kick.com")
    assert host_matches("https://www.kick.com/someone/videos/abc", "kick.com")
    assert host_matches("kick.com/video/abc", "kick.com")  # people paste bare links
    assert not host_matches("https://www.youtube.com/watch?v=1&ref=kick.com", "kick.com")
    assert not host_matches("https://kick.com.evil.net/video/abc", "kick.com")

    assert host_matches("https://www.twitch.tv/videos/123", "twitch.tv")
    assert not host_matches("https://evil.example/twitch.tv/videos/123", "twitch.tv")

    assert not host_matches("", "kick.com")
    assert not host_matches("http://[oops", "kick.com")  # malformed, must not raise


def test_the_source_modules_actually_use_host_matches():
    """The wrapper wiring, checked where the dependencies exist.

    Skipped on CI, which has no yt_dlp — see the note above. Without this the
    logic could be correct and simply not called.
    """
    import pytest

    pytest.importorskip("yt_dlp", reason="source modules import yt_dlp; not installed on CI")

    from sources.kick import is_kick_url
    from sources.twitch import is_twitch_url

    assert is_kick_url("https://kick.com/video/abc")
    assert not is_kick_url("https://www.youtube.com/watch?v=1&ref=kick.com")
    assert is_twitch_url("https://www.twitch.tv/videos/123")
    assert not is_twitch_url("https://evil.example/twitch.tv/videos/123")


def test_redaction_is_not_quadratic_on_hostile_input():
    """The email pattern was unbounded, so a long run of '+' with no '@' made
    the scan retry from every start position. Diagnostics are attached to bug
    reports, so the input is whatever was in the log."""
    import time

    from server.feedback import redact

    started = time.perf_counter()
    redact("+" * 40_000)
    assert time.perf_counter() - started < 1.0


def test_installed_voice_name_is_unchanged_for_a_real_voice(tmp_path):
    """The hardening must not change what piper is asked for.

    `_installed_name` re-derives the name from the directory listing instead
    of trusting the caller's string. The point is that the RESULT is byte
    identical, so the subprocess command line is exactly what it was — only
    the provenance of the string changes.
    """
    from multilingual.dub import _installed_name

    (tmp_path / "en_US-lessac-medium.onnx").write_bytes(b"model")

    assert _installed_name(tmp_path, "en_US-lessac-medium") == "en_US-lessac-medium"
    assert _installed_name(tmp_path, "fr_FR-upmc-medium") is None  # not installed
    assert _installed_name(tmp_path, "../../etc/passwd") is None


def test_asset_lookup_finds_real_files_and_nothing_else(tmp_path):
    from video_editor.watermark import _asset_in

    (tmp_path / "a1b2c3d4e5f6a7b8.png").write_bytes(b"png")
    (tmp_path / "sub").mkdir()

    assert _asset_in(tmp_path, "a1b2c3d4e5f6a7b8.png") == tmp_path / "a1b2c3d4e5f6a7b8.png"
    assert _asset_in(tmp_path, "missing.png") is None
    assert _asset_in(tmp_path, "../../secret.png") is None
    assert _asset_in(tmp_path, "sub") is None  # a directory is not an asset
    assert _asset_in(tmp_path / "nonexistent", "x.png") is None


def test_real_video_id_shapes_are_still_accepted():
    """delete_video now rejects ids that are not plain names. Every platform
    id the app actually creates has to survive that, or deleting a video from
    the library stops working."""
    for vid in ("tw_2814378156", "grMkMHCx9Bo", "local_a7266e1b1a02",
                "317EVqR5mOw", "FnWfRNYI_4g", "kick_2b0f1e4c-1111-2222-3333-444455556666"):
        assert safe_name(vid) == vid

    # And the shape that made it worth checking: "/" cannot appear in a path
    # parameter, but "\" can, and it traverses on Windows.
    assert safe_name("..\\..\\Windows\\x") is None


def test_redaction_still_removes_what_it_should():
    from server.feedback import redact

    assert "@" not in redact("mail me at someone@example.com")
    assert "ghp_" not in redact("token=ghp_abcdefghijklmnopqrstuvwxyz012345")
    assert "<user>" in redact("C:\\Users\\colin\\Videos\\clip.mp4")
