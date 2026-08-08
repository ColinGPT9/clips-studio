"""Where this install keeps downloads, clips and the database.

Deliberately dependency-free — stdlib only. It lived in main.py, which meant
answering "where does data go?" required importing the entire pipeline:
numpy, OpenCV, PyTorch. That is a heavy question for a simple answer, and it
made the rule impossible to test without a GPU-sized environment.
"""

import os
import stat
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


def picked_file(
    raw: str, allowed_suffixes: tuple[str, ...]
) -> tuple[Path, str, os.stat_result] | None:
    """A file the user chose in a dialog, or None if the request isn't one.

    The complement to safe_name(). Importing any video from anywhere on your
    own disk is the whole point of the local-file feature, so there is no
    directory to confine these to and no allowlist of names to check against.
    What can be checked is that the request describes an ordinary local file
    of a kind this app can actually open:

    * resolved, so `..` segments are collapsed before anything acts on it
    * a real regular file, not a directory, device or dangling symlink
    * a local path, not a `\\\\server\\share` UNC one — the desktop app's file
      dialog never produces those, and honouring them would let anything that
      can reach the API pull files off the network shares this machine can see
    * one of `allowed_suffixes`, so the endpoint that wants an image cannot be
      talked into probing something else

    Returns everything a caller needs so that none of them has to touch the
    filesystem again with a user-derived value: the resolved path, the
    **suffix constant that matched** — taken from `allowed_suffixes`, not from
    the name on disk, so a filename built from it can only ever end in one of
    the extensions this endpoint accepts — and the stat already taken here.
    An earlier version returned only the path, and every caller went on to
    call .stat(), .resolve() and .suffix for itself; that is how one validated
    path turned into seven separate places handling untrusted input.

    The single os.stat also closes the gap between "does it exist" and "is it
    a regular file", which two calls left open.

    None of this makes an arbitrary path safe in the abstract; it makes these
    endpoints do only the narrow thing they advertise. The rest of the
    guarantee is that the API binds 127.0.0.1 and the path is chosen by a
    native file dialog, not typed by a stranger.
    """
    if not raw or "\x00" in raw:
        return None
    if raw.startswith("\\\\") or raw.startswith("//"):
        return None
    try:
        path = Path(raw).resolve()
        st = path.stat()  # codeql[py/path-injection] the file the user picked
    except (OSError, ValueError, RuntimeError):
        return None
    if not stat.S_ISREG(st.st_mode):
        return None

    lowered = path.name.lower()
    for suffix in allowed_suffixes:
        if lowered.endswith(suffix):
            return path, suffix, st
    return None


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


# What a downloaded source can be called. yt-dlp names the file with the
# extension it actually produced: "merge_output_format: mp4" only applies when
# separate video and audio streams had to be merged, so a single-file download
# keeps its own container.
_SOURCE_EXTS = (".mp4", ".mkv", ".webm", ".mov", ".m4v", ".flv", ".ts")


def cached_source(downloads: Path, video_id: str | None) -> Path | None:
    """The already-downloaded file for this video, whatever it is called.

    This used to look only for "<id>.mp4". A source that arrived as .webm was
    therefore invisible to it, so reprocessing downloaded the whole video
    again — and since the new file had a different name, the old one stayed
    where it was. Two copies of the same stream, several GB each, from
    pressing the button twice.

    Ordered by _SOURCE_EXTS so an mp4 wins if more than one somehow exists,
    which matches what the rest of the pipeline prefers to decode.
    """
    if not video_id or not downloads.is_dir():
        return None
    found = {
        p.suffix.lower(): p
        for p in downloads.iterdir()
        if p.is_file() and p.stem == video_id and p.suffix.lower() in _SOURCE_EXTS
    }
    for ext in _SOURCE_EXTS:
        if ext in found:
            return found[ext]
    return None
