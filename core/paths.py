"""Where this install keeps downloads, clips and the database.

Deliberately dependency-free — stdlib only. It lived in main.py, which meant
answering "where does data go?" required importing the entire pipeline:
numpy, OpenCV, PyTorch. That is a heavy question for a simple answer, and it
made the rule impossible to test without a GPU-sized environment.
"""

import os
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent


def resolve_data_dir(config: dict) -> Path:
    """The absolute directory for this install's data.

    `data_dir` ships as the relative path "data", and it used to be resolved
    against the CURRENT WORKING DIRECTORY. In a checkout that quietly means
    "wherever you happened to run python from". In an installed copy it is
    worse: Electron spawns the engine without setting a working directory, so
    a creator's videos could land inside Program Files, which is not writable
    without admin — the first download simply fails.

    An absolute path in settings.yaml is always honoured: someone pointing
    data_dir at a big second drive means it. A relative one resolves against
    a stable base instead — per-user app data for an installed build, the
    repo folder for a checkout.
    """
    raw = Path(str((config.get("paths") or {}).get("data_dir") or "data"))
    if raw.is_absolute():
        return raw

    if getattr(sys, "frozen", False):
        # Installed: per-user, writable, and survives reinstalling the app.
        base = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local")
        return base / "Clips Studio" / raw

    # Checkout: next to the code, not next to the terminal.
    return _REPO_ROOT / raw


def safe_name(name: str) -> str | None:
    """`name` if it is a plain filename, otherwise None.

    For values that are STORED and later used to build a path — an asset
    filename inside a saved branding profile, a voice id from settings. Those
    are meant to name one file in one folder, and nothing checked that they
    did. `asset_dir / "../../../../Windows/System32/x"` is a perfectly valid
    Path expression, and the result was handed to FFmpeg as an input.

    Rejects, in order of how they bite:

    * separators and NUL — the traversal itself
    * "." and ".." — the same thing, spelled shorter
    * absolute paths, including "C:" drive-relative ones on Windows
    * a leading "-" — not traversal at all, but these names are also passed
      to piper and ffmpeg as arguments, where "-foo" becomes a FLAG rather
      than a filename. Same class of bug, different consequence.

    NOT for paths a user picks in a file dialog: importing any video and
    exporting to any folder are the point of the app, and those are already
    the user's own files. This is for values that arrive as data.
    """
    if not name or name in (".", ".."):
        return None
    if "/" in name or "\\" in name or "\x00" in name:
        return None
    if name.startswith("-"):
        return None
    if os.path.isabs(name) or (len(name) > 1 and name[1] == ":"):
        return None
    return name


def within(base: Path, candidate: Path) -> bool:
    """True when `candidate` really sits inside `base`, symlinks resolved.

    The belt to safe_name()'s braces: it answers the question about the final
    path rather than about the string it was built from, so it still holds if
    a name gets through by some route this file has not thought of.
    """
    try:
        candidate.resolve().relative_to(base.resolve())
        return True
    except (ValueError, OSError):
        return False
