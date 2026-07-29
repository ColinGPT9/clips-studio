"""Where an install keeps a creator's videos.

`data_dir` ships as the relative path "data" and used to be resolved against
the CURRENT WORKING DIRECTORY. In a checkout that quietly meant "wherever you
ran python from". In an installed copy it was worse: Electron spawns the
engine without setting a working directory, so downloads, clips and the
database could land inside Program Files, which is not writable without
admin — the first download would simply fail.
"""

import sys
from pathlib import Path

from core.paths import resolve_data_dir


def test_relative_path_ignores_the_working_directory(monkeypatch, tmp_path):
    """The whole point: the answer must not change when the terminal does."""
    resolved = resolve_data_dir({"paths": {"data_dir": "data"}})

    monkeypatch.chdir(tmp_path)
    assert resolve_data_dir({"paths": {"data_dir": "data"}}) == resolved


def test_checkout_resolves_next_to_the_code():
    got = resolve_data_dir({"paths": {"data_dir": "data"}})
    assert got.is_absolute()
    assert got == Path(__file__).resolve().parent.parent / "data"


def test_installed_build_uses_per_user_storage(monkeypatch, tmp_path):
    """A frozen build must write somewhere the user owns, never beside the
    executable in Program Files."""
    local_appdata = tmp_path / "AppData" / "Local"
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setenv("LOCALAPPDATA", str(local_appdata))

    got = resolve_data_dir({"paths": {"data_dir": "data"}})
    assert got == local_appdata / "Clips Studio" / "data"
    # Never beside the executable, wherever that happens to be installed.
    assert Path(sys.executable).parent not in got.parents


def test_absolute_path_is_honoured(monkeypatch, tmp_path):
    """Someone pointing data_dir at a big second drive means it — in a
    checkout and in an installed copy alike.

    tmp_path rather than a literal "D:/ClipsLibrary": a Windows drive path is
    NOT absolute on Linux, so that version passed here and failed on CI.
    """
    library = tmp_path / "ClipsLibrary"
    for frozen in (False, True):
        monkeypatch.setattr(sys, "frozen", frozen, raising=False)
        assert resolve_data_dir({"paths": {"data_dir": str(library)}}) == library


def test_missing_config_still_returns_somewhere_usable():
    got = resolve_data_dir({})
    assert got.is_absolute()


# ---- finding an already-downloaded source -----------------------------------
#
# The lookup used to be `downloads / f"{video_id}.mp4"`. yt-dlp names the file
# with the extension it actually produced — "merge_output_format: mp4" only
# applies when separate streams had to be merged — so a single-file .webm was
# invisible to it. Reprocessing then downloaded the whole video again under a
# different name and left the first copy in place: two files, several GB each,
# from pressing the button twice.


def test_cached_source_finds_any_container(tmp_path):
    from core.paths import cached_source

    (tmp_path / "abc123.webm").write_bytes(b"x")
    assert cached_source(tmp_path, "abc123").name == "abc123.webm"


def test_cached_source_prefers_mp4_when_both_exist(tmp_path):
    """After the codec swap both can briefly exist; decode the fast one."""
    from core.paths import cached_source

    (tmp_path / "abc123.webm").write_bytes(b"x")
    (tmp_path / "abc123.mp4").write_bytes(b"x")
    assert cached_source(tmp_path, "abc123").name == "abc123.mp4"


def test_cached_source_ignores_partials_and_sidecars(tmp_path):
    """A half-finished download is not a source. Treating "x.mp4.part" as one
    would hand the pipeline a truncated file instead of re-fetching it."""
    from core.paths import cached_source

    (tmp_path / "xyz.mp4.part").write_bytes(b"x")
    (tmp_path / "xyz.info.json").write_bytes(b"x")
    assert cached_source(tmp_path, "xyz") is None


def test_cached_source_does_not_match_an_id_prefix(tmp_path):
    """Video ids are opaque platform strings and one can prefix another."""
    from core.paths import cached_source

    (tmp_path / "abc123.mp4").write_bytes(b"x")
    assert cached_source(tmp_path, "abc") is None


def test_cached_source_handles_nothing_to_find(tmp_path):
    from core.paths import cached_source

    assert cached_source(tmp_path, "missing") is None
    assert cached_source(tmp_path / "no-such-folder", "abc") is None
    assert cached_source(tmp_path, None) is None
