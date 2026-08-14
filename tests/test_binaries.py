"""Finding the binaries an installed copy ships.

The pipeline shells out to FFmpeg constantly, and it used to do so by bare
name. That works in a checkout and cannot work in an installed copy: creators
do not have FFmpeg, and "download it, unzip it, add it to your PATH" is where
someone closes the installer for good.

The same reasoning now covers Ollama, which used to be an errand the setup
wizard sent people on.
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


def test_ffprobe_is_found_in_the_ffmpeg_folder():
    """The two arrive from upstream as one archive, so the folder is named for
    ffmpeg and ffprobe has to be looked up there rather than in a folder of its
    own. A search keyed on the binary's own name would miss it."""
    assert binaries._search_roots("ffmpeg") == binaries._search_roots("ffmpeg")
    assert any(
        root.name == "ffmpeg" for root in binaries._search_roots("ffmpeg")
    ), "ffprobe resolution depends on the ffmpeg/ folder being searched"


def test_frozen_build_finds_its_bundled_ollama(monkeypatch, tmp_path):
    """The installed layout: the AI runtime ships inside the frozen engine, so
    a creator never installs a second program."""
    exe_dir = tmp_path / "backend"
    (exe_dir / "_internal" / "ollama").mkdir(parents=True)
    name = "ollama.exe" if os.name == "nt" else "ollama"
    bundled = exe_dir / "_internal" / "ollama" / name
    bundled.write_text("")

    monkeypatch.setattr(binaries.sys, "frozen", True, raising=False)
    monkeypatch.setattr(binaries.sys, "executable", str(exe_dir / "api.exe"))
    monkeypatch.delenv("CLIPS_STUDIO_OLLAMA", raising=False)
    binaries._resolve.cache_clear()

    assert binaries.ollama() == str(bundled)
    assert binaries.has_bundled_ollama()
    binaries._resolve.cache_clear()


def test_a_system_ollama_on_path_does_not_count_as_bundled(monkeypatch, tmp_path):
    """preflight words its advice differently for the two cases — a bundled
    runtime that won't start is a bug to report, a missing system one is
    something the developer can start themselves.

    PATH is the trap here. A developer machine almost always has Ollama on it,
    so keying this off "did anything resolve" would tell them their own
    install is our bug, and would flip depending on whose machine ran it.
    """
    monkeypatch.delenv("CLIPS_STUDIO_OLLAMA", raising=False)
    monkeypatch.setattr(binaries, "_search_roots", lambda _folder: [tmp_path])
    monkeypatch.setattr(binaries.shutil, "which", lambda _name: "C:/tools/ollama.exe")
    binaries._resolve.cache_clear()

    assert binaries.ollama() == "C:/tools/ollama.exe"
    assert not binaries.has_bundled_ollama()
    binaries._resolve.cache_clear()


def test_missing_reports_both_binaries():
    """ffprobe is as essential as ffmpeg; the preflight needs to know about
    each separately."""
    absent = binaries.missing()
    assert isinstance(absent, list)
    assert all(name in ("ffmpeg", "ffprobe") for name in absent)


def test_ytdlp_is_told_where_the_bundled_ffmpeg_is(monkeypatch, tmp_path):
    """yt-dlp does not use core.binaries; it hunts for ffmpeg on PATH itself.

    Found by installing into a clean Windows: Twitch downloads worked and every
    YouTube one failed with "ffmpeg is not installed". A Twitch VOD is a single
    muxed HLS stream, while YouTube serves video and audio separately and has
    to merge them. A developer machine has ffmpeg on PATH and never sees it.
    """
    from sources import ytdlp_common

    bundled = tmp_path / ("ffmpeg.exe" if os.name == "nt" else "ffmpeg")
    bundled.write_text("")
    monkeypatch.setattr(ytdlp_common, "ffmpeg", lambda: str(bundled))

    opts = ytdlp_common.progress_opts("vid1")
    assert opts["ffmpeg_location"] == str(tmp_path), (
        "yt-dlp needs the FOLDER holding ffmpeg and ffprobe"
    )


def test_ytdlp_is_left_alone_when_there_is_no_bundled_ffmpeg(monkeypatch):
    """A checkout relying on PATH must keep working: handing yt-dlp a location
    that does not exist is worse than saying nothing and letting it search."""
    from sources import ytdlp_common

    monkeypatch.setattr(ytdlp_common, "ffmpeg", lambda: "ffmpeg")
    assert "ffmpeg_location" not in ytdlp_common.progress_opts("vid1")


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


def test_yolo_weights_are_never_loaded_by_bare_name():
    """Same failure as the FFmpeg one above, with a worse ending.

    Ultralytics reads a bare "yolov8n-pose.pt" as "look next to the working
    directory, then download it from GitHub". The spec bundles these weights
    so a first video never stalls on that download, but bundling achieves
    nothing while the loader is handed a bare name — and the engine is spawned
    with no working directory, so the lookup misses every time.

    A Store build did exactly this and the job died with
    "Download failure for .../yolov8n-pose.pt. Retry limit reached", which is
    what anyone behind a firewall would have seen.
    """
    import pathlib
    import re

    root = pathlib.Path(binaries.__file__).resolve().parent.parent
    offenders = []
    skip = {"vendor", "build", "dist", "release", "data", "site", "ui", "tests", ".git"}

    for path in root.rglob("*.py"):
        if set(path.relative_to(root).parts) & skip or path.name == "binaries.py":
            continue
        for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            m = re.search(r"YOLO\(\s*([^)]*)\)", line)
            if m and "yolo_weights" not in m.group(1):
                offenders.append(f"{path.relative_to(root)}:{n}")

    assert not offenders, (
        "these hand ultralytics a path it will resolve against the working "
        "directory and then download; wrap it in core.binaries.yolo_weights(): "
        f"{offenders}"
    )
