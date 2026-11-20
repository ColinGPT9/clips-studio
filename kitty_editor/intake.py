"""Taking the editor's exports back in.

The editor's Export writes into the project's own out/ folder. When an
export has finished, the desktop app tells the engine the file's NAME (never
a path), and take_in moves it to where it belongs:

- a clip's project, the clip is there and still on the file the project
  stands on: the export becomes the clip's video (data/clips/projects/), the
  clip's old file is removed only after that worked;
- the clip was changed since (Quick edit rendered it again): the export is
  kept beside it in data/clips/projects/ and the clip is left alone;
- no clip to re-point (a whole video, a file, a deleted clip): the export
  goes to the person's export folder under the name typed in the editor's
  Export sheet, which starts as "<project name> - <date>".

A failure before the clip's row is changed leaves the clip exactly as it
was; nothing is ever deleted on a failure.

Standard library only when imported.
"""

from __future__ import annotations

import os
import re
import shutil
import threading
import time
from datetime import date
from pathlib import Path

from kitty_editor import registry

SETTLE = 3.0        # seconds a file left in out/ must have been still before recovery takes it
# How long POST /editor/projects/{id}/exports waits while the same project's
# exports are being taken in (a reopen's recover, K-D17): it then answers
# with what that run did, never "busy" for an export it took in.
LOCK_WAIT = 120.0
# The editor's own work files (concat-export's lib.rs names them so while it renders).
_WORK_FILE = re.compile(r"^\..*\.concat-(?:video|audio|muxed)\.[^.]+$", re.IGNORECASE)

_BUSY: dict[str, threading.Lock] = {}
_BUSY_GUARD = threading.Lock()


class BadName(ValueError):
    """Not a plain export file name."""


class NoSuchExport(LookupError):
    """No such file in the project's out/ folder."""


class Busy(RuntimeError):
    """This project's export is being taken in right now."""


class BadFolder(ValueError):
    """The export folder can't be used."""


class Failed(RuntimeError):
    """The export could not be taken in; everything is as it was."""


def _project_lock(project_id: str) -> threading.Lock:
    with _BUSY_GUARD:
        return _BUSY.setdefault(project_id, threading.Lock())


def unfinished(name: str) -> bool:
    """One of the editor's own work files while it renders:
    .<name>.concat-video.<ext>, .concat-audio. or .concat-muxed."""
    return _WORK_FILE.match(name) is not None


def checked_name(file) -> str:
    """The export's file name, when it is one the editor's export sheet can
    write (its file_name(): trimmed, not only dots, none of / \\ : * ? " < > |
    or a control character) plus a video ending the editor exports: never a
    folder part or a drive, never one of the editor's unfinished work files,
    at most 255 characters (a file name's limit on Windows). Dots are fine
    anywhere ("Wait... what happened.mp4", ".intro.mp4"): the file is then
    looked for only directly in out/ (_in_out)."""
    name = str(file or "")
    suffix = Path(name).suffix
    stem = name[: -len(suffix)] if suffix else name
    if (not name or len(name) > 255 or name != name.strip() or not stem.strip(". ")
            or any(ch in name for ch in '/\\:*?"<>|') or any(ord(ch) < 32 or ord(ch) == 127 for ch in name)
            or unfinished(name) or not name.lower().endswith(registry.EXPORT_SUFFIXES)):
        raise BadName(name)
    return name


def _in_out(out: Path, name: str) -> Path | None:
    """out/<name> when it is a regular file directly in out/, else None."""
    candidate = out / name
    try:
        if candidate.is_symlink() or not candidate.is_file():
            return None
        if candidate.resolve().parent != out.resolve():
            return None
    except OSError:
        return None
    return candidate


def export_folder(raw) -> Path | None:
    """The person's export folder from the app's settings, made if need be.
    None when none was given; BadFolder for one that can't be used (a
    network share, a relative path, one that can't be made)."""
    if raw is None or str(raw).strip() == "":
        return None
    text = str(raw)
    if text.startswith("\\\\") or text.startswith("//") or "\x00" in text:
        raise BadFolder("Clips Kitty can't save into a network folder.")
    target = Path(text)
    if not target.is_absolute():
        raise BadFolder("Clips Kitty can't save into that folder.")
    try:
        target = target.resolve()
        target.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        raise BadFolder("Clips Kitty can't save into that folder.") from e
    return target


def _move(source: Path, target: Path) -> None:
    """Move a file. Across drives it is copied, the copy's size checked
    against the source's, and only then is the source removed."""
    try:
        os.replace(source, target)
        return
    except OSError:
        pass
    size = source.stat().st_size
    try:
        shutil.copy2(source, target)
        if target.stat().st_size != size:
            raise OSError("the copy is not complete")
    except OSError:
        try:
            target.unlink()
        except OSError:
            pass
        raise
    try:
        source.unlink()
    except OSError:
        pass        # the copy is whole; the leftover in out/ is taken for a repeat and answered as one


def _recorded(manifest: dict, name: str) -> dict | None:
    for entry in reversed(manifest.get("exports") or []):
        if isinstance(entry, dict) and entry.get("file") == name:
            return entry
    return None


def _answer(entry: dict, again: bool, clip_id=None) -> dict:
    path = Path(entry["path"])
    out = {"result": entry["result"], "path": str(path), "folder": str(path.parent), "file": path.name,
           "seconds": float(entry.get("seconds") or 0), "again": again}
    if entry.get("clip_id") is not None:
        out["clip_id"] = entry["clip_id"]
    if entry["result"] == "kept_beside":
        out["reason"] = "clip_changed"
    return out


def take_in(data_dir, project_id: str, file, *, folder=None, open_db, publish=None, clip_lock=None,
            wait: bool | float = False) -> dict:
    """Take the export `file` (a name in the project's out/) in. `folder`
    is the person's export folder (used only when there is no clip to
    re-point); `open_db` gives a StateDB to use and close; `publish` sends
    an event to the app's windows.

    `wait`: False tries the project's lock once, True waits for it, a
    number waits that many seconds; Busy when it is not had. An export
    taken in meanwhile (by recover) is then answered as it was recorded.

    Raises BadName, registry.NotFound, NoSuchExport, Busy, BadFolder or
    Failed; whatever it raises, the clip and its file are as they were."""
    data_dir = Path(data_dir)
    name = checked_name(file)
    manifest = registry.load(data_dir, project_id)
    if manifest.get("damaged"):
        raise NoSuchExport(name)
    lock = _project_lock(project_id)
    if wait is True:
        held = lock.acquire()
    elif wait:
        held = lock.acquire(timeout=max(0.0, float(wait)))
    else:
        held = lock.acquire(blocking=False)
    if not held:
        raise Busy(project_id)
    try:
        return _take_in(data_dir, project_id, name, folder, open_db, publish or (lambda event: None), clip_lock)
    finally:
        lock.release()


def _take_in(data_dir: Path, project_id: str, name: str, folder, open_db, publish, clip_lock) -> dict:
    manifest = registry.load(data_dir, project_id)
    out = registry.folder(data_dir, project_id) / "out"
    source = _in_out(out, name)
    if source is None:
        entry = _recorded(manifest, name)
        if entry is not None:
            return _answer(entry, again=True)       # told twice: harmless
        raise NoSuchExport(name)
    # A playable video or nothing changes: a broken, empty or sound-only file
    # never becomes the clip's video (and the clip's own file is never
    # removed for one). It stays in out/, where the app can find it again.
    seconds = registry.probe_seconds(source)
    if seconds <= 0:
        raise Failed("The export isn't a video Clips Kitty can play.")
    suffix = source.suffix.lower()
    exports = registry.exports_folder(data_dir)
    count = len(manifest.get("exports") or []) + 1
    clip = manifest.get("clip") if manifest.get("kind") == "clip" else None

    if isinstance(clip, dict) and clip.get("id") is not None:
        if clip_lock is None:
            from server.jobs import clip_lock  # the lock a re-render of the clip holds
        with clip_lock(clip["id"]):
            db = open_db()
            try:
                row = db.get_clip(int(clip["id"]))
                if row is not None:
                    unchanged = (registry.same_path(row["path"], clip.get("file"))
                                 and registry.stamp(row["path"]) == clip.get("file_stamp"))
                    try:
                        exports.mkdir(parents=True, exist_ok=True)
                        target = registry.unique_target(exports, f"{project_id}-{count}", suffix)
                        _move(source, target)
                    except OSError as e:
                        raise Failed("The export could not be moved into Clips Kitty's folder.") from e
                    if not unchanged:
                        entry = {"file": name, "at": registry.now(), "result": "kept_beside",
                                 "path": str(target), "seconds": seconds, "clip_id": int(row["id"])}
                        _record(data_dir, project_id, entry)
                        publish({"type": "editor_export", "project_id": project_id, "result": "kept_beside",
                                 "path": str(target)})
                        return _answer(entry, again=False)
                    old = Path(row["path"]) if row["path"] else None
                    try:
                        from core.state import _now

                        db.set_clip(int(row["id"]), path=str(target), edited_at=_now())
                    except Exception as e:
                        # The clip is as it was; the export goes back where the app can find it again.
                        try:
                            _move(target, source)
                        except OSError:
                            pass
                        raise Failed("The export could not be saved as this clip's video.") from e
                    # Only now, with the clip on its new file, does the old one go.
                    _discard_old(db, data_dir, old, target, int(row["id"]))
                    entry = {"file": name, "at": registry.now(), "result": "replaced_clip", "path": str(target),
                             "seconds": seconds, "clip_id": int(row["id"])}

                    def change(m, target=target):
                        m["clip"]["file"] = str(target)
                        m["clip"]["file_stamp"] = registry.stamp(target)
                        m.setdefault("exports", []).append(entry)

                    registry.update(data_dir, project_id, change)
                    publish({"type": "clip_file_replaced", "clip_id": int(row["id"]), "project_id": project_id})
                    publish({"type": "queue"})
                    return _answer(entry, again=False)
            finally:
                db.close()

    # No clip to re-point: a whole video, a file from the PC, or a clip that was deleted.
    where = export_folder(folder)
    if where is None:
        downloads = downloads_folder()
        where = downloads if downloads is not None and downloads.is_dir() else exports
    # The name the person typed in the editor's Export sheet is kept (it
    # starts as "<name> - <date>"); the project's plain name, as an older
    # editor started the sheet with, gets the date added.
    plain = registry.safe_name(f"{manifest.get('export_name') or manifest.get('name') or 'Video'}", "Video")
    typed = registry.safe_name(Path(name).stem, "")
    stem = typed if typed and typed != plain else f"{plain} - {date.today().isoformat()}"
    try:
        where.mkdir(parents=True, exist_ok=True)
        target = registry.unique_target(where, stem, suffix)
        _move(source, target)
    except OSError as e:
        raise Failed("The export could not be moved to your export folder.") from e
    entry = {"file": name, "at": registry.now(), "result": "saved_to_folder", "path": str(target),
             "seconds": seconds}
    _record(data_dir, project_id, entry)
    publish({"type": "editor_export", "project_id": project_id, "result": "saved_to_folder", "path": str(target)})
    return _answer(entry, again=False)


def _record(data_dir: Path, project_id: str, entry: dict) -> None:
    registry.update(data_dir, project_id, lambda m: m.setdefault("exports", []).append(entry))


def downloads_folder() -> Path | None:
    """The Downloads folder as Windows knows it (the Downloads known folder,
    which the person may have moved): the folder the app's own Export
    buttons use when Settings has none (Electron's app.getPath("downloads"),
    ui/src/renderer/src/lib/exportFolder.ts). Elsewhere, or when Windows
    can't say, ~/Downloads. None when there is no home folder either."""
    if os.name == "nt":
        try:
            import ctypes
            import uuid as _uuid
            from ctypes import wintypes

            class _GUID(ctypes.Structure):
                _fields_ = [("Data1", wintypes.DWORD), ("Data2", wintypes.WORD), ("Data3", wintypes.WORD),
                            ("Data4", ctypes.c_ubyte * 8)]

            raw = _uuid.UUID("374DE290-123F-4565-9164-39C4925E467B").bytes_le      # FOLDERID_Downloads
            guid = _GUID.from_buffer_copy(raw)
            found = ctypes.c_wchar_p()
            shell32, ole32 = ctypes.windll.shell32, ctypes.windll.ole32
            if shell32.SHGetKnownFolderPath(ctypes.byref(guid), 0, None, ctypes.byref(found)) == 0:
                try:
                    if found.value:
                        return Path(found.value)
                finally:
                    ole32.CoTaskMemFree(found)
        except (OSError, AttributeError, ValueError):
            pass
    try:
        return Path.home() / "Downloads"
    except (OSError, RuntimeError, KeyError):
        return None


def _discard_old(db, data_dir: Path, old: Path | None, target: Path, clip_id: int) -> None:
    """The clip's old file, when it is inside the data folder, is not the
    new one, and no other clip row still plays it and no run holds it
    (core.remake.file_in_use, as DELETE /clips/{clip_id} asks: two AI
    windows with the same whole-second start and end share one
    clip_<start>-<end>.mp4); and the Quick edit preview of the clip. Called
    with the clip's lock and the database held, after the re-point. Never
    raises."""
    from core.paths import discard, within

    try:
        if old is not None and not registry.same_path(old, target) and old.is_file() and within(data_dir, old):
            from core.remake import file_in_use

            if not file_in_use(db, old):
                discard(old)
        discard(data_dir / "previews" / f"clip_{clip_id}.mp4")
    except Exception:
        pass        # kept: a file left behind is reclaimed later, one deleted can't be had back


def recover(data_dir, project_id: str, *, open_db, publish=None, clip_lock=None, folder=None) -> list[dict]:
    """Exports left in out/ (the app closed between an export's end and its
    report): each taken in, oldest first, into `folder` (the person's
    export folder) when there is no clip to re-point. The editor's
    unfinished work files are never taken, and a file changed in the last
    seconds is left for later. Never raises; what could not be taken in
    stays in out/."""
    try:
        out = registry.folder(data_dir, project_id) / "out"
        files = [f for f in out.iterdir()
                 if f.is_file() and not f.is_symlink() and not unfinished(f.name)
                 and f.name.lower().endswith(registry.EXPORT_SUFFIXES)]
    except (OSError, registry.NotFound):
        return []
    now = time.time()
    waiting = []
    for f in files:
        try:
            changed = f.stat().st_mtime
        except OSError:
            continue
        if now - changed >= SETTLE:
            waiting.append((changed, f.name))
    answers = []
    for _, name in sorted(waiting):
        try:
            answers.append(take_in(data_dir, project_id, name, folder=folder, open_db=open_db,
                                   publish=publish, clip_lock=clip_lock))
        except Exception:
            continue
    return answers
