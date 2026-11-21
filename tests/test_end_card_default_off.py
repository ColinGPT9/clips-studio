"""The end card is off by default, existing installs included.

A new default only reaches new installs: an installed build reads its own
settings.yaml, seeded from the copy it first shipped with, which said
`outro: true`. So core.paths switches that one value off once, and a marker
beside the file keeps a later "on" from being undone. Light on purpose
(stdlib and PyYAML), so it runs on CI's light install; the card itself is
tested in test_outro.py.
"""

import sys
from pathlib import Path

import pytest

from core import paths
from core.paths import END_CARD_OFF_MARKER, end_card_off_once, user_config_path

yaml = pytest.importorskip("yaml")

ROOT = Path(__file__).resolve().parent.parent

# The lines an install seeded from 1.2.0-2.1.0 carries, comments and all.
SEEDED = (
    "model: gemma3:12b\r\n"
    "clips:\r\n"
    "  min_score: 61         # discard candidates scored below this (0-100).\r\n"
    "  captions: true        # burn word-synced captions into the clips\r\n"
    "  outro: true           # append the 2.9s Clips Kitty end card to each clip.\r\n"
    "                        # Absent counts as true, so upgrading turns it on.\r\n"
    "\r\n"
    "scoring:\r\n"
    "  weights: {}\r\n"
)


def _installed(monkeypatch, tmp_path, bundled_text: str, user_text: str | None) -> tuple[Path, Path]:
    """An installed build: its bundled settings, and (when given) the user's
    own copy from an earlier version."""
    local_appdata = tmp_path / "AppData" / "Local"
    bundled = tmp_path / "app" / "config" / "settings.yaml"
    bundled.parent.mkdir(parents=True)
    bundled.write_bytes(bundled_text.encode("utf-8"))
    user = local_appdata / "Clips Studio" / "settings.yaml"
    if user_text is not None:
        user.parent.mkdir(parents=True)
        user.write_bytes(user_text.encode("utf-8"))
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setenv("LOCALAPPDATA", str(local_appdata))
    return bundled, user


def test_the_shipped_settings_have_it_off():
    cfg = yaml.safe_load((ROOT / "config" / "settings.yaml").read_text(encoding="utf-8"))
    assert cfg["clips"]["outro"] is False


def test_an_existing_install_is_switched_off_once(monkeypatch, tmp_path):
    shipped = (ROOT / "config" / "settings.yaml").read_text(encoding="utf-8")
    bundled, user = _installed(monkeypatch, tmp_path, shipped, SEEDED)

    assert user_config_path(bundled) == user
    after = user.read_bytes().decode("utf-8")
    assert yaml.safe_load(after)["clips"]["outro"] is False
    # Only the value changed: comments, line endings and other settings kept.
    assert after == SEEDED.replace("outro: true", "outro: false", 1)
    assert (user.parent / END_CARD_OFF_MARKER).is_file()


def test_turning_it_back_on_sticks(monkeypatch, tmp_path):
    shipped = (ROOT / "config" / "settings.yaml").read_text(encoding="utf-8")
    bundled, user = _installed(monkeypatch, tmp_path, shipped, SEEDED)
    user_config_path(bundled)

    # The creator ticks it again in Settings, which rewrites the line.
    user.write_bytes(user.read_bytes().replace(b"outro: false", b"outro: true"))
    for _ in range(2):  # later starts
        user_config_path(bundled)
    assert yaml.safe_load(user.read_text(encoding="utf-8"))["clips"]["outro"] is True


def test_a_new_install_starts_off_and_is_left_alone(monkeypatch, tmp_path):
    shipped = (ROOT / "config" / "settings.yaml").read_text(encoding="utf-8")
    bundled, user = _installed(monkeypatch, tmp_path, shipped, None)

    assert user_config_path(bundled) == user
    assert user.read_text(encoding="utf-8") == shipped
    assert (user.parent / END_CARD_OFF_MARKER).is_file()


@pytest.mark.parametrize("value", ["True", "yes", "on", "TRUE"])
def test_any_yaml_true_is_switched_off(tmp_path, value):
    settings = tmp_path / "settings.yaml"
    settings.write_text(f"clips:\n  outro: {value}\n", encoding="utf-8")
    assert end_card_off_once(settings) is True
    assert yaml.safe_load(settings.read_text(encoding="utf-8"))["clips"]["outro"] is False


@pytest.mark.parametrize("text", [
    "clips:\n  outro: false\n",          # already off
    "clips:\n  min_score: 55\n",         # no line: absent now means off
    "outro: true\nclips:\n  min_score: 55\n",  # not clips.outro
])
def test_nothing_else_is_touched(tmp_path, text):
    settings = tmp_path / "settings.yaml"
    settings.write_text(text, encoding="utf-8")
    assert end_card_off_once(settings) is False
    assert settings.read_text(encoding="utf-8") == text
    assert (tmp_path / END_CARD_OFF_MARKER).is_file()


def test_an_unreadable_file_is_tried_again_next_start(tmp_path, monkeypatch):
    settings = tmp_path / "settings.yaml"
    assert end_card_off_once(settings) is False          # missing
    assert not (tmp_path / END_CARD_OFF_MARKER).exists()

    settings.write_text("clips:\n  outro: true\n", encoding="utf-8")
    real_replace = paths.os.replace

    def locked(*a, **k):
        raise PermissionError("in use")

    monkeypatch.setattr(paths.os, "replace", locked)
    assert end_card_off_once(settings) is False          # couldn't write
    assert not (tmp_path / END_CARD_OFF_MARKER).exists()

    monkeypatch.setattr(paths.os, "replace", real_replace)
    assert end_card_off_once(settings) is True           # the next start
    assert yaml.safe_load(settings.read_text(encoding="utf-8"))["clips"]["outro"] is False
