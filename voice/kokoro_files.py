"""The built-in voice's two files: which ones, and how to know they are right.

Kept apart from the engine so the fetch script, the packaging test and the
status check can read the pins without importing numpy or onnxruntime.

The files are the kokoro-onnx project's own release of the Kokoro-82M model
(Apache-2.0) exported to ONNX, plus its voice pack. Both are pinned by
SHA-256: a file that does not match is never loaded, whoever put it there.
They are fetched at build time by scripts/fetch_kokoro.py into models/kokoro/
and bundled by clips-studio.spec. Nothing here downloads anything.
"""

from __future__ import annotations

import hashlib
import sys
from pathlib import Path

RELEASE = "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.1/"

# (file name, what it is, URL, SHA-256, size in bytes)
FILES: tuple[tuple[str, str, str, str, int], ...] = (
    (
        "kokoro-v1.0.onnx",
        "Kokoro voice model",
        RELEASE + "kokoro-v1.0.onnx",
        "beb0d1848dee9a49da392cc3df26958d46cfa35d321edf434f52949153f0df3a",
        325_505_369,
    ),
    (
        "voices-v1.0.bin",
        "Kokoro voice pack",
        RELEASE + "voices-v1.0.bin",
        "bca610b8308e8d99f32e6fe4197e7ec01679264efed0cac9140fe9c29f1fbf7d",
        28_214_398,
    ),
)

MODEL_FILE = FILES[0][0]
VOICES_FILE = FILES[1][0]
MODEL_SHA256 = FILES[0][3]
VOICES_SHA256 = FILES[1][3]

FETCH_COMMAND = "python scripts/fetch_kokoro.py"

# One hash per (path, size, mtime): hashing 350 MB takes about a second, and
# the status route is asked often.
_checked: dict[tuple[str, int, int], str] = {}


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with Path(path).open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def _digest_cached(path: Path) -> str:
    st = path.stat()
    key = (str(path), st.st_size, st.st_mtime_ns)
    got = _checked.get(key)
    if got is None:
        got = sha256(path)
        _checked[key] = got
    return got


def find() -> Path | None:
    """The folder that holds both files (unchecked), or None. core/binaries
    knows where a checkout and an installed copy keep them."""
    from core.binaries import kokoro_dir

    return kokoro_dir()


def what_to_do() -> str:
    """How to get the files back: the fetch command in a checkout. An
    installed copy has no command line to send anyone to."""
    if getattr(sys, "frozen", False):
        return "Install Clips Kitty again to put them back."
    return f"Run: {FETCH_COMMAND}"


def known_wrong(folder: Path | None = None) -> str | None:
    """The name of a file the SHA-256 check has already found to be the wrong
    one, without reading anything again (None when nothing has been checked
    yet, or all that was checked is right). For the start-up check, which
    must stay instant and must not call a voice ready that will not load."""
    folder = folder or find()
    if folder is None:
        return None
    for name, _label, _url, want, _size in FILES:
        path = Path(folder) / name
        try:
            st = path.stat()
        except OSError:
            continue
        got = _checked.get((str(path), st.st_size, st.st_mtime_ns))
        if got is not None and got != want:
            return name
    return None


def check(folder: Path | None = None) -> tuple[Path | None, str]:
    """(folder, "") when both files are there and match their SHA-256,
    else (None, why) in plain words."""
    folder = folder or find()
    if folder is None:
        return None, f"Voice files missing. {what_to_do()}"
    for name, _label, _url, want, _size in FILES:
        path = Path(folder) / name
        try:
            if not path.is_file():
                return None, f"Voice files missing. {what_to_do()}"
            got = _digest_cached(path)
        except OSError as e:
            return None, f"The voice files could not be read ({e.__class__.__name__}). {what_to_do()}"
        if got != want:
            return None, (
                f"The voice file {name} is not the one this version expects, "
                f"so it was not loaded. {what_to_do()}"
            )
    return Path(folder), ""
