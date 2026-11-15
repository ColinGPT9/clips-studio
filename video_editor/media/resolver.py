"""The one place a media id becomes a real file. Never a path from the
client: an id names something the app already knows, and the file is found
from the app's own records.

| Id | What it names | Where the file is |
|---|---|---|
| `video:<video id>` | a video the app downloaded or was given | `data/downloads/<video id>.<ext>` (core/paths.py cached_source) |
| `clip:<clip id>` | a clip's rendered file | the clip's row (`clips.path`), inside the data folder |
| `asset:<sha256>` | a file imported for editing | `data/media/assets/<2>/<sha256><ext>` (media_assets) |
| `library:<id>` | a free track or sound effect that ships with the app | `assets/library/`, only when its sha256 matches (video/audio_library.py) |
| `project:<project id>:<media id>` | a media entry of a project's document | whatever its `source` names, as above; a `{"type": "file"}` source only inside the data folder |

Whatever the id says, the file found must be inside the app's data folder
(or, for a library sound, the library folder) once every link is resolved;
anything else is NotFound, the same answer as a file that isn't there.

Standard library only (video/audio_library.py and core/paths.py are too).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

_VIDEO_ID = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}")
_SHA256 = re.compile(r"[0-9a-f]{64}")
_LIBRARY_ID = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+){0,15}")
_PROJECT_ID = re.compile(r"[0-9a-f]{8,64}")
_MEDIA_KEY = re.compile(r"[A-Za-z0-9_-]{1,64}")
_EXT = re.compile(r"\.[a-z0-9]{1,8}")
KINDS = ("video", "audio", "image")
# What a project's {"type": "file"} source (a clip's own music, step 5) may be.
FILE_SOURCE_EXTS = frozenset({".mp3", ".m4a", ".aac", ".wav", ".ogg", ".opus", ".flac", ".mp4", ".m4v", ".mov",
                              ".mkv", ".webm"})


class NotFound(Exception):
    """No file for this id, in plain words naming what is missing."""


@dataclass(frozen=True)
class Media:
    ref: str            # the canonical id: video:..., clip:..., asset:..., library:..., or file:<path>
    asked: str          # the id as it was asked for
    kind: str           # video, audio or image
    path: Path          # the file, resolved
    name: str           # what a person calls it
    sha256: str | None = None   # an imported file's, which is its identity


def _inside(base: Path | None, path: Path) -> bool:
    if base is None:
        return False
    try:
        path.resolve().relative_to(base.resolve())
        return True
    except (OSError, ValueError, RuntimeError):
        return False


def _checked(path: Path, base: Path | None, what: str) -> Path:
    """`path` resolved, when it is a file inside `base`."""
    try:
        resolved = path.resolve()
    except (OSError, RuntimeError):
        raise NotFound(f"{what} is missing.") from None
    if not _inside(base, resolved):
        raise NotFound(f"{what} is outside Clips Kitty's folders, so it can't be opened here.")
    if not resolved.is_file():
        raise NotFound(f"{what} is missing.")
    return resolved


def is_media_id(media_id: str) -> bool:
    """Whether `media_id` has the shape of an id (not whether it exists)."""
    try:
        _parse(media_id)
        return True
    except NotFound:
        return False


def _parse(media_id: str) -> tuple[str, list[str]]:
    kind, _, rest = str(media_id or "").partition(":")
    parts = rest.split(":") if rest else []
    ok = {
        "video": len(parts) == 1 and bool(_VIDEO_ID.fullmatch(parts[0])) and ".." not in parts[0],
        "clip": len(parts) == 1 and parts[0].isdigit() and len(parts[0]) <= 18 and parts[0].isascii(),
        "asset": len(parts) == 1 and bool(_SHA256.fullmatch(parts[0])),
        "library": len(parts) == 1 and bool(_LIBRARY_ID.fullmatch(parts[0])),
        "project": len(parts) == 2 and bool(_PROJECT_ID.fullmatch(parts[0])) and bool(_MEDIA_KEY.fullmatch(parts[1])),
    }.get(kind, False)
    if not ok:
        raise NotFound(f"There is no media called {str(media_id)[:80]!r}. A media id is video:<id>, clip:<id>, "
                       "asset:<sha256>, library:<id> or project:<project id>:<media id>.")
    return kind, parts


def resolve(conn, data_dir, media_id: str) -> Media:
    """The file `media_id` names. `conn` is the app's database (StateDB.conn,
    rows by name). Raises NotFound."""
    data_dir = Path(data_dir)
    kind, parts = _parse(media_id)
    if kind == "project":
        return _project(conn, data_dir, media_id, parts[0], parts[1])
    return _source(conn, data_dir, media_id, kind, parts[0])


def _source(conn, data_dir: Path, asked: str, kind: str, value: str, media_kind: str | None = None) -> Media:
    if kind == "video":
        return _video(conn, data_dir, asked, value)
    if kind == "clip":
        return _clip(conn, data_dir, asked, int(value))
    if kind == "asset":
        return _asset(conn, data_dir, asked, value)
    if kind == "library":
        return _library(asked, value)
    raise NotFound(f"There is no media called {asked[:80]!r}.")


VERTICAL = "__vertical"   # sources/vertical.py SUFFIX: a Vertical Live video's own download


def _download(downloads: Path, video_id: str) -> Path | None:
    """The download core/paths.py cached_source() finds for `video_id`, by
    trying its few possible names (constant time however many downloads
    there are, which matters for a Range request) and listing the folder
    only when none of them is there: a file whose extension has capitals,
    on a drive that tells them apart."""
    from core.paths import _SOURCE_EXTS, cached_source

    for ext in _SOURCE_EXTS:
        candidate = downloads / f"{video_id}{ext}"
        if candidate.is_file():
            return candidate
    return cached_source(downloads, video_id)


def _video(conn, data_dir: Path, asked: str, video_id: str) -> Media:
    if not _VIDEO_ID.fullmatch(video_id) or ".." in video_id:
        raise NotFound(f"There is no video {video_id[:80]!r} in the library.")
    row = conn.execute("SELECT title FROM videos WHERE video_id = ?", (video_id,)).fetchone()
    downloads = data_dir / "downloads"
    # Its own download, else its Vertical Live one (<id>__vertical.<ext>,
    # as core/remake.py and the pipeline find it). A video has both only
    # when it was clipped both ways; then the plain one is the project's.
    found = _download(downloads, video_id)
    if found is None and not video_id.endswith(VERTICAL):
        found = _download(downloads, video_id + VERTICAL)
    title = str(row["title"] or video_id) if row is not None else video_id
    if found is None:
        if row is None:
            raise NotFound(f"There is no video {video_id!r} in the library.")
        raise NotFound(f"The download of “{title}” is missing (it was deleted, or freed after "
                       "publishing). Download the video again to edit it.")
    path = _checked(found, downloads, f"The download of “{title}”")
    return Media(ref=f"video:{video_id}", asked=asked, kind="video", path=path, name=title)


def _clip(conn, data_dir: Path, asked: str, clip_id: int) -> Media:
    try:
        row = conn.execute("SELECT path, title, hook FROM clips WHERE id = ?", (clip_id,)).fetchone()
    except OverflowError:
        row = None
    if row is None:
        raise NotFound(f"There is no clip {clip_id}.")
    name = str(row["title"] or row["hook"] or f"Clip {clip_id}")
    if not row["path"]:
        raise NotFound(f"The clip “{name}” has no rendered file.")
    path = _checked(Path(row["path"]), data_dir, f"The file of the clip “{name}”")
    return Media(ref=f"clip:{clip_id}", asked=asked, kind="video", path=path, name=name)


def _asset(conn, data_dir: Path, asked: str, sha256: str) -> Media:
    try:
        row = conn.execute("SELECT * FROM media_assets WHERE sha256 = ?", (sha256,)).fetchone()
    except Exception:          # an older database with no media_assets table
        row = None
    if row is None:
        raise NotFound("That file was never imported for editing.")
    ext = str(row["ext"] or "")
    ext = ext if _EXT.fullmatch(ext) else ""
    name = str(row["name"] or sha256[:12])
    base = data_dir / "media" / "assets"
    path = _checked(base / sha256[:2] / f"{sha256}{ext}", base, f"The imported file “{name}”")
    kind = row["kind"] if row["kind"] in KINDS else "video"
    return Media(ref=f"asset:{sha256}", asked=asked, kind=kind, path=path, name=name, sha256=sha256)


def _library(asked: str, item_id: str) -> Media:
    from video import audio_library

    entry = audio_library.entry(item_id)
    if entry is None:
        raise NotFound(f"{item_id!r} is not one of the app's free tracks or sound effects.")
    path = audio_library.resolve(item_id)
    name = str(entry.get("title") or item_id)
    if path is None:
        raise NotFound(f"The free sound “{name}” is missing or damaged in this copy of the app.")
    path = _checked(path, audio_library.folder(), f"The free sound “{name}”")
    return Media(ref=f"library:{item_id}", asked=asked, kind="audio", path=path, name=name)


def _project(conn, data_dir: Path, asked: str, project_id: str, key: str) -> Media:
    try:
        row = conn.execute("SELECT doc FROM projects WHERE id = ?", (project_id,)).fetchone()
    except Exception:
        row = None
    if row is None:
        raise NotFound("There is no such project.")
    try:
        doc = json.loads(row["doc"])
        entry = doc["media"][key]
    except (TypeError, ValueError, KeyError):
        raise NotFound(f"The project has no media {key!r}.") from None
    return resolve_entry(conn, data_dir, asked, key, entry)


def resolve_entry(conn, data_dir, asked: str, key: str, entry: dict) -> Media:
    """The file a project's media entry (`document["media"][key]`) names,
    under the same rules as a `project:` id. The export compiler (step 7)
    resolves the media of the revision it renders this way: that revision's
    document, not necessarily the one saved as the project now."""
    data_dir = Path(data_dir)
    try:
        source = entry["source"]
        source_type = source["type"]
    except (TypeError, KeyError):
        raise NotFound(f"The project has no media {key!r}.") from None
    media_kind = entry.get("kind") if entry.get("kind") in KINDS else None
    if source_type == "video":
        found = _video(conn, data_dir, asked, str(source.get("video_id") or ""))
    elif source_type == "asset":
        sha256 = str(source.get("sha256") or "")
        if not _SHA256.fullmatch(sha256):
            raise NotFound(f"The project's media {key!r} names no imported file.")
        found = _asset(conn, data_dir, asked, sha256)
    elif source_type == "library":
        item_id = str(source.get("id") or "")
        if not _LIBRARY_ID.fullmatch(item_id):
            raise NotFound(f"The project's media {key!r} names no free sound.")
        found = _library(asked, item_id)
    elif source_type == "file":
        # Only from an imported clip's own music file (step 5). Served only
        # when it is inside the data folder: a file elsewhere on the PC is
        # imported for editing first (POST /media/import).
        raw = source.get("path")
        if not isinstance(raw, str) or not raw or "\x00" in raw:
            raise NotFound(f"The project's media {key!r} names no file.")
        name = Path(raw).name
        # A project document is written by the client, so a "file" source
        # could name anything in the data folder (the database, a saved
        # sign-in). Only a sound or a video, by its extension (the name
        # given and the file it leads to), is served.
        refused = f"The file “{name}” is not a sound or a video, so it can't be opened here."
        if Path(raw).suffix.lower() not in FILE_SOURCE_EXTS:
            raise NotFound(refused)
        path = _checked(Path(raw), data_dir, f"The file “{name}”")
        if path.suffix.lower() not in FILE_SOURCE_EXTS:
            raise NotFound(refused)
        found = Media(ref=f"file:{path}", asked=asked, kind=media_kind or "audio", path=path, name=name)
    else:
        raise NotFound(f"The project's media {key!r} is of a kind this version can't open.")
    if media_kind and media_kind != found.kind:
        found = Media(ref=found.ref, asked=asked, kind=media_kind, path=found.path, name=found.name,
                      sha256=found.sha256)
    return found
