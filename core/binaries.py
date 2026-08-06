"""Where the bundled binaries and model weights live.

The pipeline shells out to FFmpeg constantly. A developer running from the
repo has it on PATH, so the code used to just say "ffmpeg" and hope — which
is fine until someone installs the app. Creators do not have FFmpeg, and
"download FFmpeg, unzip it, add it to your PATH, restart" is exactly the
friction that makes them close the installer and never come back.

So an installed copy ships its own and this module finds them. Resolution
order, first hit wins:

  1. CLIPS_STUDIO_FFMPEG / CLIPS_STUDIO_FFPROBE / CLIPS_STUDIO_OLLAMA —
     explicit override, for debugging or pinning a custom build.
  2. Next to the frozen executable — where the installer puts them.
  3. PATH — the developer case, and any system-wide install.

Falling back to the bare name (rather than raising) keeps the repo workflow
working even if nothing is bundled: the OS resolves it, and if it genuinely
isn't there the caller gets FileNotFoundError naming the binary, which is a
clearer error than anything invented here.

The same reasoning covers the Ollama runtime and the Whisper weights. Those
are not binaries on a PATH, but they fail the same way — as an errand handed
to someone who only wanted to clip a video — so they resolve through here too.
"""

import os
import shutil
import sys
from functools import cache
from pathlib import Path


def _search_roots(folder: str) -> list[Path]:
    """Directories a packaged build may keep a bundled tool's binaries in.

    `folder` is the subdirectory the packaging step puts them in, which is not
    always the binary's own name — ffprobe.exe lives in ffmpeg/ beside
    ffmpeg.exe, because they arrive from upstream as one archive.

    PyInstaller one-dir puts the executable beside its payload, and the
    installer drops the tool either right there or in its subfolder; both
    layouts are accepted so the packaging step can pick whichever is
    convenient without a code change.
    """
    roots: list[Path] = []
    if getattr(sys, "frozen", False):  # running from a PyInstaller build
        exe_dir = Path(sys.executable).parent
        roots += [exe_dir, exe_dir / folder, exe_dir / "_internal" / folder]
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            roots += [Path(meipass), Path(meipass) / folder]
    # A repo checkout may vendor binaries for testing a packaged layout.
    repo_vendor = Path(__file__).resolve().parent.parent / "vendor" / folder
    roots.append(repo_vendor)
    return roots


@cache
def _resolve(name: str, folder: str) -> str:
    override = os.environ.get(f"CLIPS_STUDIO_{name.upper()}")
    if override and Path(override).exists():
        return override

    filename = f"{name}.exe" if os.name == "nt" else name
    for root in _search_roots(folder):
        candidate = root / filename
        if candidate.exists():
            return str(candidate)

    return shutil.which(name) or name


def ffmpeg() -> str:
    """Path to the ffmpeg binary this install should use."""
    return _resolve("ffmpeg", "ffmpeg")


def ffprobe() -> str:
    """Path to the ffprobe binary this install should use."""
    return _resolve("ffprobe", "ffmpeg")


def ollama() -> str:
    """Path to the Ollama runtime this install should use.

    Packaged builds ship their own, so a creator never installs it. A repo
    checkout falls back to PATH, which is what a developer with a system-wide
    Ollama already has running.
    """
    return _resolve("ollama", "ollama")


def has_bundled_ollama() -> bool:
    """Whether this install carries its own Ollama, rather than borrowing one.

    The difference matters when reporting a failure: a bundled runtime that
    won't start is a bug to report, while a missing system one is something
    the creator can fix themselves.

    Deliberately ignores PATH. "We found an ollama" and "we shipped an ollama"
    are different facts, and only the second one makes a startup failure our
    problem — a developer with Ollama installed must still be told to go and
    start it.
    """
    filename = "ollama.exe" if os.name == "nt" else "ollama"
    return any((root / filename).exists() for root in _search_roots("ollama"))


# faster-whisper writes several files per model; this is the big one, and its
# presence is what separates a finished download from an abandoned one.
_WHISPER_MARKER = "model.bin"


def whisper_model(size: str) -> str:
    """A local directory holding the CTranslate2 weights for `size`, or `size`.

    Returned unchanged when nothing is vendored, because faster-whisper reads
    a bare size name as "fetch it from Hugging Face". That is the right
    default in a checkout and the wrong one in an installed copy, where it
    means a creator's first video stalls on a silent multi-gigabyte download
    that looks exactly like a hang.
    """
    for root in _search_roots("whisper"):
        candidate = root / size
        if (candidate / _WHISPER_MARKER).exists():
            return str(candidate)
    return size


def bundled_whisper_sizes() -> list[str]:
    """Which Whisper sizes this install actually carries.

    For the preflight: "no transcription weights" is worth saying up front
    rather than twenty minutes into someone's first video.
    """
    found: list[str] = []
    for root in _search_roots("whisper"):
        if not root.is_dir():
            continue
        for child in root.iterdir():
            if (child / _WHISPER_MARKER).exists() and child.name not in found:
                found.append(child.name)
    return found


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
