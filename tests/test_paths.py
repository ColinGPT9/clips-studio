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

from core import paths
from core.paths import resolve_data_dir, user_config_path


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
    # Not "Clips Kitty": the data folder keeps its pre-rename name on
    # purpose, so an upgrade does not orphan an existing library.
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


# ---- where settings.yaml is read and written --------------------------------
#
# The app rewrites this file: switching the model rewrites the `model:` line,
# and saving settings rewrites the whole thing. It used to write the copy that
# ships inside the installation directory, which only worked because the
# installer happens to install per-user. Per-machine puts it under Program
# Files and MSIX makes it read-only, and in both cases the write fails —
# looking to a creator like the setting simply not sticking.


def test_checkout_edits_the_file_in_the_repo(monkeypatch, tmp_path):
    """Nothing clever in a checkout: the repo's own file is the one to edit."""
    monkeypatch.setattr(sys, "frozen", False, raising=False)
    bundled = tmp_path / "settings.yaml"
    bundled.write_text("model: gemma3:4b\n", encoding="utf-8")

    assert user_config_path(bundled) == bundled


def test_installed_build_writes_settings_where_the_user_can(monkeypatch, tmp_path):
    local_appdata = tmp_path / "AppData" / "Local"
    bundled = tmp_path / "app" / "config" / "settings.yaml"
    bundled.parent.mkdir(parents=True)
    bundled.write_text("model: gemma3:4b\n", encoding="utf-8")

    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setenv("LOCALAPPDATA", str(local_appdata))

    got = user_config_path(bundled)
    assert got == local_appdata / "Clips Studio" / "settings.yaml"
    # Beside the data directory, so everything a creator owns is in one place.
    assert got.parent == resolve_data_dir({"paths": {"data_dir": "data"}}).parent


def test_an_existing_installs_settings_are_carried_over(monkeypatch, tmp_path):
    """The upgrade case, and the reason this seeds rather than starting fresh.

    Someone already running Clips Kitty has been editing the bundled copy,
    because that is where the app has been writing. Ignoring it on upgrade
    would silently reset their model choice and every other setting.
    """
    local_appdata = tmp_path / "AppData" / "Local"
    bundled = tmp_path / "app" / "config" / "settings.yaml"
    bundled.parent.mkdir(parents=True)
    bundled.write_text("model: gemma3:27b\nclips:\n  min_score: 8\n", encoding="utf-8")

    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setenv("LOCALAPPDATA", str(local_appdata))

    got = user_config_path(bundled)
    assert got.read_text(encoding="utf-8") == bundled.read_text(encoding="utf-8")

    # And once seeded it is theirs: a later app update ships new defaults, and
    # those must not overwrite what the creator has since chosen.
    got.write_text("model: gemma3:12b\n", encoding="utf-8")
    bundled.write_text("model: gemma4:e2b\n", encoding="utf-8")
    assert user_config_path(bundled).read_text(encoding="utf-8") == "model: gemma3:12b\n"


def test_unwritable_appdata_falls_back_to_the_bundled_copy(monkeypatch, tmp_path):
    """No writable location is not a reason to crash on startup. Reads keep
    working, and a write fails with the error it would have failed with
    anyway rather than one invented here."""
    bundled = tmp_path / "settings.yaml"
    bundled.write_text("model: gemma3:4b\n", encoding="utf-8")

    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "nope"))
    monkeypatch.setattr(
        paths.shutil, "copyfile", lambda *a, **k: (_ for _ in ()).throw(OSError("read-only"))
    )

    assert user_config_path(bundled) == bundled


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
