"""Where the external binaries live.

The pipeline shells out to FFmpeg constantly. A developer running from the
repo has it on PATH, so the code used to just say "ffmpeg" and hope — which
is fine until someone installs the app. Creators do not have FFmpeg, and
"download FFmpeg, unzip it, add it to your PATH, restart" is exactly the
friction that makes them close the installer and never come back.

So an installed copy ships its own binaries and this module finds them.
Resolution order, first hit wins:

  1. CLIPS_STUDIO_FFMPEG / CLIPS_STUDIO_FFPROBE — explicit override, for
     debugging a specific build or pinning a custom build.
  2. Next to the frozen executable — where the installer puts them.
  3. PATH — the developer case, and any system-wide install.

Falling back to the bare name (rather than raising) keeps the repo workflow
working even if nothing is bundled: the OS resolves it, and if it genuinely
isn't there the caller gets FileNotFoundError naming the binary, which is a
clearer error than anything invented here.
"""

import os
import shutil
import sys
from functools import lru_cache
from pathlib import Path


def _search_roots() -> list[Path]:
    """Directories a packaged build may keep binaries in.

    PyInstaller one-dir puts the executable beside its payload, and the
    installer drops FFmpeg either right there or in an ffmpeg/ subfolder;
    both layouts are accepted so the packaging step can pick whichever is
    convenient without a code change.
    """
    roots: list[Path] = []
    if getattr(sys, "frozen", False):  # running from a PyInstaller build
        exe_dir = Path(sys.executable).parent
        roots += [exe_dir, exe_dir / "ffmpeg", exe_dir / "_internal" / "ffmpeg"]
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            roots += [Path(meipass), Path(meipass) / "ffmpeg"]
    # A repo checkout may vendor binaries for testing a packaged layout.
    repo_vendor = Path(__file__).resolve().parent.parent / "vendor" / "ffmpeg"
    roots.append(repo_vendor)
    return roots


@lru_cache(maxsize=None)
def _resolve(name: str) -> str:
    override = os.environ.get(f"CLIPS_STUDIO_{name.upper()}")
    if override and Path(override).exists():
        return override

    filename = f"{name}.exe" if os.name == "nt" else name
    for root in _search_roots():
        candidate = root / filename
        if candidate.exists():
            return str(candidate)

    return shutil.which(name) or name


def ffmpeg() -> str:
    """Path to the ffmpeg binary this install should use."""
    return _resolve("ffmpeg")


def ffprobe() -> str:
    """Path to the ffprobe binary this install should use."""
    return _resolve("ffprobe")


def missing() -> list[str]:
    """Which required binaries can't be found — for the startup preflight.

    Empty list means everything needed is present.
    """
    absent = []
    for name, path in (("ffmpeg", ffmpeg()), ("ffprobe", ffprobe())):
        # A bare name means nothing resolved it; anything else is a real path.
        if path == name and shutil.which(name) is None:
            absent.append(name)
    return absent
