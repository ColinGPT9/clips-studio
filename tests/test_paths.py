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
