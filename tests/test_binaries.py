"""Finding FFmpeg.

The pipeline shells out to FFmpeg constantly, and it used to do so by bare
name. That works in a checkout and cannot work in an installed copy: creators
do not have FFmpeg, and "download it, unzip it, add it to your PATH" is where
someone closes the installer for good.
"""

import os

from core import binaries


def test_env_override_wins(monkeypatch, tmp_path):
    """The escape hatch for debugging a specific build."""
    fake = tmp_path / ("ffmpeg.exe" if os.name == "nt" else "ffmpeg")
    fake.write_text("")
    monkeypatch.setenv("CLIPS_STUDIO_FFMPEG", str(fake))
    binaries._resolve.cache_clear()

    assert binaries.ffmpeg() == str(fake)
    binaries._resolve.cache_clear()


def test_override_pointing_at_nothing_is_ignored(monkeypatch):
    """A stale override must not brick the app — fall through to the normal
    search instead."""
    monkeypatch.setenv("CLIPS_STUDIO_FFMPEG", "/nowhere/at/all/ffmpeg")
    binaries._resolve.cache_clear()

    assert binaries.ffmpeg() != "/nowhere/at/all/ffmpeg"
    binaries._resolve.cache_clear()


def test_frozen_build_looks_beside_its_executable(monkeypatch, tmp_path):
    """The installed layout: FFmpeg ships inside the frozen engine."""
    exe_dir = tmp_path / "backend"
    (exe_dir / "_internal" / "ffmpeg").mkdir(parents=True)
    name = "ffmpeg.exe" if os.name == "nt" else "ffmpeg"
    bundled = exe_dir / "_internal" / "ffmpeg" / name
    bundled.write_text("")

    monkeypatch.setattr(binaries.sys, "frozen", True, raising=False)
    monkeypatch.setattr(binaries.sys, "executable", str(exe_dir / "api.exe"))
    monkeypatch.delenv("CLIPS_STUDIO_FFMPEG", raising=False)
    binaries._resolve.cache_clear()

    assert binaries.ffmpeg() == str(bundled)
    binaries._resolve.cache_clear()


def test_falls_back_to_a_bare_name_rather_than_raising():
    """If nothing is found, the caller should get FileNotFoundError naming
    the binary — a clearer error than anything invented here."""
    binaries._resolve.cache_clear()
    assert binaries.ffmpeg() in ("ffmpeg",) or os.path.exists(binaries.ffmpeg())
    binaries._resolve.cache_clear()


def test_missing_reports_both_binaries():
    """ffprobe is as essential as ffmpeg; the preflight needs to know about
    each separately."""
    absent = binaries.missing()
    assert isinstance(absent, list)
    assert all(name in ("ffmpeg", "ffprobe") for name in absent)


def test_no_module_calls_ffmpeg_by_bare_name():
    """A regression guard for the whole point of this module. Adding a new
    subprocess call with a literal "ffmpeg" would break every installed
    copy while working perfectly on the developer's machine."""
    import pathlib
    import re

    root = pathlib.Path(binaries.__file__).resolve().parent.parent
    offenders = []
    skip = {"vendor", "build", "dist", "release", "data", "site", "ui", "tests", ".git"}

    for path in root.rglob("*.py"):
        if set(path.relative_to(root).parts) & skip or path.name == "binaries.py":
            continue
        for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            # argv[0] position: right after a "[" or first on its own line.
            if re.search(r'(\[\s*|^\s*)"(ffmpeg|ffprobe)"\s*,', line):
                offenders.append(f"{path.relative_to(root)}:{n}")

    assert not offenders, (
        "these call FFmpeg by bare name and will fail in an installed copy; "
        f"use core.binaries.ffmpeg() instead: {offenders}"
    )
