"""The editor's projects, as Clips Kitty remembers them.

No database table: each project is a folder, data/editor/projects/<id>/,
and its manifest kitty.json is the record (listing = reading the folder).
So there is no migration, and a project needs no row to open, to export or
to protect the clip it changed.

    data/editor/projects/<id>/
        kitty.json      the manifest, written whole (a temp file, then os.replace)
        media/          the project's own media (a clip's base.mp4, music, a logo)
        p/<name>/       the editor's own project folder
        out/            where the editor's Export writes; emptied as exports are taken in
        build.log       what the editor said while the project was built
    data/clips/projects/<id>-<n>.<ext>   a clip project's export once taken in

Standard library only.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path

SCHEMA = 1
MANIFEST = "kitty.json"
KINDS = ("clip", "video", "file")
SHAPES = {"16:9": (1920, 1080), "9:16": (1080, 1920), "1:1": (1080, 1080), "4:5": (1080, 1350)}
# Every container the editor's export sheet writes: mp4 (H.264, HEVC, AV1),
# mov (ProRes), mkv, and webm (VP9 with transparency; concat-media encode.rs).
EXPORT_SUFFIXES = (".mp4", ".mov", ".mkv", ".webm")

_ID = re.compile(r"^[0-9a-f]{32}$")
_EXPORT_NAME = re.compile(r"^(?P<project>[0-9a-f]{32})-\d+(?:-\d+)?$")
# One writer of the manifests at a time in this process (the build thread,
# the routes, a render job's follow_clip_id).
_LOCK = threading.RLock()

# Names Windows keeps for devices: never a folder's or a file's name.
_RESERVED = {"con", "prn", "aux", "nul", *(f"com{i}" for i in range(1, 10)), *(f"lpt{i}" for i in range(1, 10))}


class NotFound(LookupError):
    """No such project."""


class InUse(RuntimeError):
    """The project's folder can't be removed: the editor has it open."""


def is_id(value) -> bool:
    return isinstance(value, str) and _ID.match(value) is not None


def new_id() -> str:
    return uuid.uuid4().hex


def now() -> str:
    """UTC, to the millisecond, as the manifest keeps its times (they also
    say which of two projects is the newer)."""
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def root(data_dir) -> Path:
    return Path(data_dir) / "editor" / "projects"


def exports_folder(data_dir) -> Path:
    """Where a clip project's exports go once taken in. core/state.py's
    PROJECT_EXPORTS_FOLDER and core/housekeeping.py's PROJECT_EXPORTS name
    the same folder; a test keeps the three the same."""
    return Path(data_dir) / "clips" / "projects"


def folder(data_dir, project_id: str) -> Path:
    if not is_id(project_id):
        raise NotFound(project_id)
    return root(data_dir) / project_id


def safe_name(text, fallback: str) -> str:
    """A name that can be a Windows folder or file name: no < > : " / \\ | ? *
    or control characters, a run of dots kept as one ("Wait... what" ->
    "Wait. what"), no dot or space at either end, not a device name, at most
    60 characters. `fallback` when nothing is left."""
    cleaned = "".join(" " if ch in '<>:"/\\|?*' or ord(ch) < 32 or ord(ch) == 127 else ch for ch in str(text or ""))
    cleaned = re.sub(r"\.{2,}", ".", " ".join(cleaned.split()))
    cleaned = cleaned.strip(" .")[:60].rstrip(" .")
    if not cleaned or cleaned.split(".")[0].strip().lower() in _RESERVED:
        cleaned = str(fallback)
    return cleaned


def stamp(path) -> list[int] | None:
    """[size in bytes, mtime_ns] of a file, or None when it isn't one."""
    try:
        st = os.stat(path)
    except (OSError, TypeError, ValueError):
        return None
    return [int(st.st_size), int(st.st_mtime_ns)]


def same_path(a, b) -> bool:
    """Two paths to one file, as the system compares them."""
    if not a or not b:
        return False
    try:
        return os.path.normcase(os.path.abspath(str(a))) == os.path.normcase(os.path.abspath(str(b)))
    except (OSError, ValueError):
        return False


def _damaged(project_id: str, why: str) -> dict:
    return {"schema": SCHEMA, "id": project_id, "name": "", "kind": "file", "state": "failed", "progress": 0.0,
            "message": why, "created_at": "", "last_opened_at": "", "canvas": None, "shape": "",
            "project_dir": "", "export_dir": "", "export_name": "", "clip": None, "video_id": None,
            "source_file": "", "media": [], "notes": [], "exports": [], "damaged": True}


def load(data_dir, project_id: str) -> dict:
    """The project's manifest. NotFound when there is no such folder; a
    manifest that can't be read comes back as a failed project (never hidden,
    never an error), so it can still be listed and deleted."""
    home = folder(data_dir, project_id)
    if not home.is_dir():
        raise NotFound(project_id)
    try:
        manifest = json.loads((home / MANIFEST).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return _damaged(project_id, "This project's record can't be read.")
    if not isinstance(manifest, dict) or manifest.get("id") != project_id or manifest.get("kind") not in KINDS:
        return _damaged(project_id, "This project's record can't be read.")
    return manifest


def save(data_dir, manifest: dict) -> dict:
    """Write the manifest whole: a temp file beside it, then os.replace, so a
    reader never sees half of one. On Windows the replace is refused while
    another thread (a listing, the page asking for the project) has the old
    manifest open to read it, so it is tried again for up to two seconds
    (K-D12)."""
    import time

    home = folder(data_dir, manifest["id"])
    home.mkdir(parents=True, exist_ok=True)
    text = json.dumps(manifest, ensure_ascii=False, indent=1)
    temp = home / f"{MANIFEST}.{os.getpid()}.{threading.get_ident()}.tmp"
    with _LOCK:
        temp.write_text(text, encoding="utf-8")
        until = time.monotonic() + 2.0
        while True:
            try:
                os.replace(temp, home / MANIFEST)
                break
            except PermissionError:
                if time.monotonic() >= until:
                    try:
                        temp.unlink()
                    except OSError:
                        pass
                    raise
                time.sleep(0.01)
    return manifest


def update(data_dir, project_id: str, change) -> dict:
    """Read, change (a function given the manifest) and write, as one step."""
    with _LOCK:
        manifest = load(data_dir, project_id)
        if manifest.get("damaged"):
            return manifest
        change(manifest)
        return save(data_dir, manifest)


def list_all(data_dir) -> list[dict]:
    """Every project, last opened first."""
    base = root(data_dir)
    try:
        ids = [f.name for f in base.iterdir() if f.is_dir() and is_id(f.name)]
    except OSError:
        return []
    found = []
    for project_id in ids:
        try:
            found.append(load(data_dir, project_id))
        except NotFound:
            continue
    found.sort(key=lambda m: (str(m.get("last_opened_at") or ""), str(m.get("created_at") or "")), reverse=True)
    return found


def delete(data_dir, project_id: str) -> None:
    """Remove the project's folder. The editor's own folder (p/) goes first:
    when the editor has it open that fails (InUse) with nothing else gone.
    Exports already taken in are elsewhere and stay."""
    home = folder(data_dir, project_id)
    if not home.is_dir():
        raise NotFound(project_id)
    with _LOCK:
        inner = home / "p"
        if inner.exists():
            try:
                shutil.rmtree(inner)
            except OSError as e:
                raise InUse(project_id) from e
        try:
            shutil.rmtree(home)
        except OSError as e:
            raise InUse(project_id) from e


def find_for_clip(data_dir, clip_id) -> dict | None:
    """The newest project made for this clip, or None."""
    mine = [m for m in list_all(data_dir)
            if m.get("kind") == "clip" and isinstance(m.get("clip"), dict) and m["clip"].get("id") == int(clip_id)]
    mine.sort(key=lambda m: str(m.get("created_at") or ""), reverse=True)
    return mine[0] if mine else None


def find_for_video(data_dir, video_id: str, shape: str) -> dict | None:
    """The newest whole-video project of this video in this shape, or None."""
    mine = [m for m in list_all(data_dir)
            if m.get("kind") == "video" and m.get("video_id") == video_id and m.get("shape") == shape]
    mine.sort(key=lambda m: str(m.get("created_at") or ""), reverse=True)
    return mine[0] if mine else None


def find_for_file(data_dir, source_file, shape: str) -> dict | None:
    """The newest project of this file from the PC in this shape, or None."""
    mine = [m for m in list_all(data_dir)
            if m.get("kind") == "file" and m.get("shape") == shape and same_path(m.get("source_file"), source_file)]
    mine.sort(key=lambda m: str(m.get("created_at") or ""), reverse=True)
    return mine[0] if mine else None


def follow_clip_id(data_dir, old_id, new_id) -> int:
    """A render gave a clip a new id (server/jobs.py): the projects made for
    it follow, so each says "changed since" rather than "deleted". Returns
    how many did. Never raises: a render never fails over this."""
    try:
        old_id, new_id = int(old_id), int(new_id)
        if old_id == new_id or not root(data_dir).is_dir():
            return 0
        moved = 0
        for manifest in list_all(data_dir):
            clip = manifest.get("clip")
            if manifest.get("damaged") or not isinstance(clip, dict) or clip.get("id") != old_id:
                continue

            def change(m, new_id=new_id):
                m["clip"]["id"] = new_id

            update(data_dir, manifest["id"], change)
            moved += 1
        return moved
    except Exception:
        return 0


def project_of_export(path, data_dir=None) -> str:
    """The project an export file in data/clips/projects/ came from (the
    <id>- at the start of its name), when that project is still there; ""
    otherwise. `data_dir` is worked out from the file's place when absent."""
    if not path:
        return ""
    named = _EXPORT_NAME.match(Path(str(path)).stem)
    if named is None:
        return ""
    base = Path(data_dir) if data_dir is not None else Path(str(path)).parent.parent.parent
    try:
        return named["project"] if (root(base) / named["project"] / MANIFEST).is_file() else ""
    except OSError:
        return ""


# A file's length, remembered while the file is as it was: {path: (stamp, seconds)}.
_LENGTHS: dict[str, tuple[list[int], float]] = {}


def probe_seconds(path) -> float:
    """A video's length in seconds from ffprobe; 0.0 when it can't be told
    or the file has no picture (empty, broken, sound only), so 0.0 also
    means "not a playable video"."""
    import subprocess

    from core.binaries import ffprobe

    try:
        done = subprocess.run(
            [ffprobe(), "-v", "error", "-select_streams", "v:0", "-show_entries",
             "stream=codec_type,width,height:format=duration", "-of", "json", str(path)],
            capture_output=True, text=True, timeout=30,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        data = json.loads(done.stdout or "{}")
        streams = [s for s in data.get("streams") or () if isinstance(s, dict)
                   and s.get("codec_type") == "video" and int(s.get("width") or 0) > 0
                   and int(s.get("height") or 0) > 0]
        if done.returncode != 0 or not streams:
            return 0.0
        return max(0.0, round(float((data.get("format") or {}).get("duration") or 0), 3))
    except (OSError, ValueError, TypeError, subprocess.SubprocessError):
        return 0.0


def exports_for(data_dir, paths) -> dict[str, dict]:
    """For each of these clip files that is an editor export: its real
    length and the project it came from ("" when that is gone). The length
    is the one kept at take-in (the manifest's `exports`), and is probed
    when no manifest knows the file."""
    known: dict[str, float] = {}
    for manifest in list_all(data_dir):
        for entry in manifest.get("exports") or ():
            if isinstance(entry, dict) and entry.get("path"):
                known[os.path.normcase(os.path.abspath(str(entry["path"])))] = float(entry.get("seconds") or 0)
    out: dict[str, dict] = {}
    for path in paths or ():
        key = os.path.normcase(os.path.abspath(str(path)))
        seconds = known.get(key, 0.0)
        if seconds <= 0:
            mark = stamp(path)
            cached = _LENGTHS.get(key)
            if mark is not None and cached is not None and cached[0] == mark:
                seconds = cached[1]
            elif mark is not None:
                seconds = probe_seconds(path)
                _LENGTHS[key] = (mark, seconds)
        out[str(path)] = {"seconds": seconds, "project_id": project_of_export(path, data_dir)}
    return out


def unique_target(folder_path: Path, stem: str, suffix: str = ".mp4") -> Path:
    """`stem``suffix` in the folder, or stem-2, stem-3 and so on: the names
    the app gives copies (server/editor_lane.unique_target, which only knows
    .mp4)."""
    target = folder_path / f"{stem}{suffix}"
    i = 2
    while target.exists():
        target = folder_path / f"{stem}-{i}{suffix}"
        i += 1
    return target
