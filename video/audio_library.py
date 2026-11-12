"""The free music and sound effects that ship with the app (assets/library/).

Everything in the folder is CC0 1.0 (the authors and sources are in NOTICE
and in the folder), each with its licence file beside it, listed in
assets/library/manifest.json with an id, a kind ("music" or "sfx"), a title,
a mood or category, the length, the sha256 and the source. Standard library
only: the /assets/library routes read it too.

A clip's music can name one of them by id (render_opts.edit.music =
{"library": "<id>", ...}, video_editor/timeline.py). The render asks
resolve() for the file, and so does a render PC, against its own copy: the id
means the same bytes everywhere, because resolve() gives a file only when it
is present and matches the manifest's sha256. Otherwise the clip is made
without music and the job log says so; a render never fails over it. (A
render PC hands such a clip back instead, so the main PC makes it:
remote_render/worker.py.)
"""

from __future__ import annotations

import hashlib
import json
import re
import sys
from functools import lru_cache
from pathlib import Path

KINDS = ("music", "sfx")
_ID = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")

_CHECKED: dict[tuple, bool] = {}     # (path, size, mtime, sha256) -> matches


def _roots() -> list[Path]:
    """Where the library folder can be: in a packaged engine (PyInstaller
    one-dir puts data under _internal/ beside the exe), then the source
    checkout. Found the same way as the caption fonts (video/fonts.py)."""
    roots: list[Path] = []
    if getattr(sys, "frozen", False):
        exe_dir = Path(sys.executable).parent
        roots += [exe_dir / "assets" / "library", exe_dir / "_internal" / "assets" / "library"]
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            roots.append(Path(meipass) / "assets" / "library")
    roots.append(Path(__file__).resolve().parent.parent / "assets" / "library")
    return roots


def _usable_entry(e) -> bool:
    return (isinstance(e, dict) and isinstance(e.get("id"), str) and bool(_ID.fullmatch(e["id"]))
            and e.get("kind") in KINDS and isinstance(e.get("file"), str) and isinstance(e.get("sha256"), str))


@lru_cache(maxsize=1)
def _loaded() -> tuple[Path | None, tuple[dict, ...]]:
    """(the library folder, its manifest's items). No folder, or a manifest
    that cannot be read: (None, ()), and this copy has no library."""
    for root in _roots():
        manifest = root / "manifest.json"
        if not manifest.is_file():
            continue
        try:
            data = json.loads(manifest.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(data, dict):
            continue
        seen: set[str] = set()
        entries = []
        for e in data.get("items") or ():
            if _usable_entry(e) and e["id"] not in seen:
                seen.add(e["id"])
                entries.append(e)
        return root, tuple(entries)
    return None, ()


def reload() -> None:
    """Read the manifest again (tests point _roots at another folder)."""
    _loaded.cache_clear()
    _CHECKED.clear()


def folder() -> Path | None:
    """The bundled library folder, or None when this copy has none."""
    return _loaded()[0]


def items(kind: str | None = None) -> list[dict]:
    """The manifest's items, in its order; only one kind when given."""
    return [dict(e) for e in _loaded()[1] if kind is None or e["kind"] == kind]


def entry(item_id) -> dict | None:
    """The item with this id, or None. An id is matched exactly."""
    wanted = str(item_id or "")
    return next((dict(e) for e in _loaded()[1] if e["id"] == wanted), None)


def is_item(item_id, kind: str | None = None) -> bool:
    """Whether the manifest lists this id (of this kind, when given)."""
    e = entry(item_id)
    return e is not None and (kind is None or e["kind"] == kind)


def _inside(root: Path, rel: str) -> Path | None:
    """root/rel, only when it stays inside the library folder."""
    path = root / rel
    try:
        path.resolve().relative_to(root.resolve())
    except (OSError, ValueError):
        return None
    return path


def _matches(path: Path, sha256: str) -> bool:
    """Whether the file is there and is the one the manifest names."""
    try:
        st = path.stat()
    except OSError:
        return False
    key = (str(path), st.st_size, st.st_mtime_ns, sha256)
    if key not in _CHECKED:
        try:
            _CHECKED[key] = hashlib.sha256(path.read_bytes()).hexdigest() == sha256.lower()
        except OSError:
            return False
    return _CHECKED[key]


def why_not(item_id, kind: str | None = None) -> str:
    """One plain line for the job log: why resolve() has no file for this id."""
    e = entry(item_id)
    if e is None or (kind is not None and e["kind"] != kind):
        return f"The free music \"{str(item_id or '')[:60]}\" isn't in this copy of the app"
    return f"The free music \"{e.get('title') or e['id']}\" is missing or damaged in this copy of the app"


def resolve(item_id, log=None, kind: str | None = None) -> Path | None:
    """The bundled file for an id (of this kind, when given), only when it is
    present and matches its sha256. Anything else (an unknown id, a sound
    effect asked for as music, a missing or changed file): None, and `log`,
    when given, is called with one plain line saying why."""
    root = folder()
    e = entry(item_id)
    if e is not None and kind is not None and e["kind"] != kind:
        e = None
    path = _inside(root, e["file"]) if root is not None and e is not None else None
    if path is not None and _matches(path, e["sha256"]):
        return path
    if log is not None:
        log(why_not(item_id, kind))
    return None


def listing() -> list[dict]:
    """The items for the UI: id, kind, title, mood, seconds and file name
    (the /assets/library routes serve them). "mood" is the manifest's mood
    for music and its category for a sound effect."""
    out = []
    for e in _loaded()[1]:
        seconds = e.get("seconds")
        out.append({
            "id": e["id"],
            "kind": e["kind"],
            "title": str(e.get("title") or e["id"]),
            "mood": str(e.get("mood") or e.get("category") or ""),
            "seconds": float(seconds) if isinstance(seconds, (int, float)) else 0.0,
            "file": Path(e["file"]).name,
            "licence": "CC0-1.0",
        })
    return out


def file_named(name: str) -> tuple[Path, dict] | None:
    """(the bundled file, its item) for the bare file name the manifest
    lists, if it is present and unchanged. Anything else, a path or a
    licence file included: None."""
    root = folder()
    if root is None or not name or name != Path(name).name or "/" in name or "\\" in name:
        return None
    for e in _loaded()[1]:
        if Path(e["file"]).name == name:
            path = _inside(root, e["file"])
            return (path, dict(e)) if path is not None and _matches(path, e["sha256"]) else None
    return None
