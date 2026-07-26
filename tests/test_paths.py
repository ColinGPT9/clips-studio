"""Where an install keeps a creator's videos.

`data_dir` ships as the relative path "data" and used to be resolved against
the CURRENT WORKING DIRECTORY. In a checkout that quietly meant "wherever you
ran python from". In an installed copy it was worse: Electron spawns the
engine without setting a working directory, so downloads, clips and the
database could land inside Program Files, which is not writable without
admin — the first download would simply fail.
"""

import sys

import main


def test_relative_path_ignores_the_working_directory(monkeypatch, tmp_path):
    """The whole point: the answer must not change when the terminal does."""
    resolved = main._resolve_data_dir({"paths": {"data_dir": "data"}})

    monkeypatch.chdir(tmp_path)
    assert main._resolve_data_dir({"paths": {"data_dir": "data"}}) == resolved


def test_checkout_resolves_next_to_the_code():
    got = main._resolve_data_dir({"paths": {"data_dir": "data"}})
    assert got.is_absolute()
    assert got.parent == __import__("pathlib").Path(main.__file__).resolve().parent


def test_installed_build_uses_per_user_storage(monkeypatch):
    """A frozen build must write somewhere the user owns, never beside the
    executable in Program Files."""
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setenv("LOCALAPPDATA", r"C:\Users\someone\AppData\Local")

    got = main._resolve_data_dir({"paths": {"data_dir": "data"}})
    assert "AppData" in str(got)
    assert "Clips Studio" in str(got)
    assert "Program Files" not in str(got)


def test_absolute_path_is_honoured(monkeypatch):
    """Someone pointing data_dir at a big second drive means it — in a
    checkout and in an installed copy alike."""
    for frozen in (False, True):
        monkeypatch.setattr(sys, "frozen", frozen, raising=False)
        got = main._resolve_data_dir({"paths": {"data_dir": "D:/ClipsLibrary"}})
        assert str(got).replace("\\", "/").endswith("D:/ClipsLibrary")


def test_missing_config_still_returns_somewhere_usable():
    got = main._resolve_data_dir({})
    assert got.is_absolute()
