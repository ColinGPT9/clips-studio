"""Import for editing: a video, a sound or a picture a person picked on this
PC, copied into the project media (data/media/assets/, by sha256, through
video_editor/project/store.py add_asset). No AI run is started.

This is the one media route that takes a path from the client, so the path
is checked before anything reads it:

- a whole path (with its drive on Windows), to a regular file that exists,
  is not empty, and is only ever opened for reading;
- not a folder, a device (`CON`, `\\\\.\\...`, `/dev/...`) or a pipe;
- not a network path (`\\\\server\\share\\...`): like the app's other routes
  that take a picked file (core/paths.py picked_file), so the engine never
  reaches out to another machine for a path it was handed. A network drive
  with a letter is fine when it answers; when it doesn't answer within
  STAT_TIMEOUT seconds, the import is refused;
- FFprobe must read it as a video, a sound or a picture.

The file is hashed and copied in one pass of 1 MB blocks, never held in
memory whole.
"""

from __future__ import annotations

import os
import stat
import threading
from pathlib import Path

from video_editor.media import probe

STAT_TIMEOUT = 5.0
MAX_PATH = 4096


class Refused(Exception):
    """The file can't be imported, in plain words. `status` is the HTTP
    answer for it."""

    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


def _stat(path: str, timeout: float):
    """os.stat, given up on after `timeout` seconds (a drive that doesn't
    answer)."""
    box: dict = {}

    def run() -> None:
        try:
            box["st"] = os.stat(path)
        except BaseException as e:   # handed to the caller
            box["error"] = e

    t = threading.Thread(target=run, daemon=True, name="import-stat")
    t.start()
    t.join(timeout)
    if t.is_alive():
        raise Refused("The drive that file is on didn't answer. If it is a network drive, check that it is "
                      "connected, or copy the file to this PC first.")
    if "error" in box:
        raise box["error"]
    return box["st"]


def check_path(raw, *, timeout: float = STAT_TIMEOUT) -> tuple[Path, os.stat_result]:
    """The picked file, checked: (its resolved path, its stat). Raises Refused."""
    if not isinstance(raw, str) or not raw.strip():
        raise Refused("Give the path of the file to import.")
    raw = raw.strip()
    if "\x00" in raw or len(raw) > MAX_PATH:
        raise Refused("That is not a path to a file.")
    if raw.startswith(("\\\\", "//")):
        raise Refused("That file is on a network path. Copy it to this PC first, then import it.")
    path = Path(raw)
    if not path.is_absolute() or (os.name == "nt" and not path.drive):
        raise Refused("Give the whole path of the file, starting with its drive.")
    if os.name == "nt" and path.is_reserved():
        raise Refused("That is a device, not a file.")
    try:
        st = _stat(raw, timeout)
    except FileNotFoundError:
        raise Refused("There is no file at that path. It may have been moved or deleted.", 404) from None
    except PermissionError:
        raise Refused("Clips Kitty isn't allowed to read that file.") from None
    except OSError as e:
        raise Refused(f"That file can't be read ({e.strerror or e}).") from None
    if stat.S_ISDIR(st.st_mode):
        raise Refused("That is a folder. Pick a file inside it.")
    if not stat.S_ISREG(st.st_mode):
        raise Refused("That is not an ordinary file (a device or a pipe), so it can't be imported.")
    if st.st_size == 0:
        raise Refused("That file is empty.")
    try:
        resolved = path.resolve(strict=True)
    except (OSError, RuntimeError):
        raise Refused("That file can't be read.") from None
    return resolved, st


def facts_of(path: Path) -> dict:
    """What FFprobe says the file is, for the media_assets row. Raises Refused."""
    try:
        info = probe.info(path)
    except probe.ProbeError as e:
        raise Refused(f"{e} Pick a video, a sound or a picture.") from None
    if info["kind"] == "image" and info.get("video_codec") not in probe.IMAGE_CODECS:
        raise Refused(f"Clips Kitty can't show {str(info.get('video_codec') or 'that kind of').upper()} pictures. "
                      "Save it as PNG or JPEG, then import it.")
    if info["kind"] != "image" and info["duration_us"] <= 0:
        raise Refused("That file has no length Clips Kitty can read.")
    return info


def public(row: dict) -> dict:
    """A media_assets row as the routes answer it."""
    fps = {"num": row["fps_num"], "den": row["fps_den"]} if row.get("fps_num") and row.get("fps_den") else None
    return {"id": f"asset:{row['sha256']}", "sha256": row["sha256"], "kind": row["kind"], "name": row["name"],
            "ext": row["ext"], "size": row["size"], "duration_us": row["duration_us"], "width": row["width"],
            "height": row["height"], "fps": fps, "has_audio": bool(row["has_audio"])}


def _facts(info: dict) -> dict:
    return {"duration_us": info["duration_us"], "width": info["display_width"], "height": info["display_height"],
            "fps": info.get("fps"), "has_audio": info["has_audio"]}


def checked_copy(copy: Path) -> dict:
    """The copy read again before it is kept (store.add_asset's `check`):
    what is recorded is what was copied, not what the file was when the
    import was asked for. Raises Refused, as facts_of does."""
    info = facts_of(Path(copy))
    return {"kind": info["kind"], "facts": _facts(info)}


def import_file(conn, data_dir, path: Path, info: dict, *, name: str = "", progress=None) -> dict:
    """Copy a checked file (check_path, facts_of) into the project media.
    The same file imported twice is one asset. The copy is read again
    before it is kept (checked_copy), so a file replaced or still being
    written between the check and the copy is recorded as it was copied,
    or refused."""
    from video_editor.project import store

    try:
        row = store.add_asset(conn, data_dir, path, kind=info["kind"], name=(name or path.name).strip()[:120],
                              facts=_facts(info), progress=progress, check=checked_copy)
    except OSError as e:
        raise Refused(f"The file couldn't be copied ({e.strerror or e}).") from None
    return public(row)
