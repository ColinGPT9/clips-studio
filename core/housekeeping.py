"""Find and remove files the app no longer needs.

Processing the same video repeatedly leaves things behind: a render that
failed or was cancelled kept its multi-hundred-MB staging file, yt-dlp
leaves `.part` fragments when a download is interrupted, and every editor
preview writes a full clip that is never read again. None of it is
referenced by the database, so it is invisible — the disk just shrinks.

Nothing here touches a file the database still points at, or one held in
core/in_use.py (a Make clips again run holds the renders no row points at
until it has finished, and the files of the clips from before), or a file
whose other Make clips again name a clip row or a video has (_ASIDE, _KEPT):
an app closed in the middle of a run can leave a row on one name and its
file on the other until the next start. Source downloads for videos you
still have are NOT scratch and are reported separately: the editor,
re-rendering and translated burns all read them, so removing one costs you
those features until the video is downloaded again.

A video one of the new editor's projects uses (core/state.py
pinned_video_ids) is never an orphan download, even when it has left the
library: the project still plays it. A Vertical Live video's own download
(<video id>__vertical.<ext>) is that video's, for both rules: it used to be
taken for an orphan and freed even while its video was in the library.

The new editor's cache (data/media_cache/, video_editor/media/cache.py) is
a group of its own. Its orphan folders (the source is gone and no project
names it) are reclaimable; clean() removes them and holds the cache to its
size cap, least recently used first. Nothing else here looks inside it,
and nothing in it is ever taken for a clip or a download.

A project's exports (data/clips/projects/, server/compose_api.py) are the
person's finished videos: a manual project's export has no clip row, and
a deleted project's export no row at all, so this folder is never swept
for orphan clips (fix 7-D11). Its files are whole exports only: an export
is made in the editor lane's work folder and moved here when complete.
The one thing listed from it: an export's captions file ("<export>.srt",
editor suite step 9) whose video is gone (9-D1, orphan_export_captions).
"""

import re
from pathlib import Path

from core import in_use
from core.state import pinned_video_ids, project_media

# Staging files written next to a clip during rendering. The pipeline
# deletes them on success; these are the ones a failure left behind.
_SCRATCH_SUFFIXES = (".source.mp4", ".edited.mp4", ".plain.mp4", ".cropped.mp4")
# Where the export compiler keeps project exports, under the data folder
# (server/compose_api.py PROJECTS_FOLDER): never an orphan clip.
PROJECT_EXPORTS = ("clips", "projects")

# A Make clips again run (core/remake.py) gives each old clip's file a second
# name while it works, clip_00010-00070.clip-17.mp4, and its rows point
# there; the video's own copy gets one too, <video id>.remaking.mp4. If the
# app is closed in the middle, a row can say one name while its file has the
# other, until the next start puts it right.
_ASIDE = re.compile(r"^(?P<first>.+)\.clip-\d+$")
_KEPT = ".remaking"


# sources/vertical.py SUFFIX: a Vertical Live video's own download is
# <video id>__vertical.<ext>, and it is that video's as much as <video id>.<ext>.
_VERTICAL = "__vertical"


def _video_of(stem: str) -> str:
    """The video a download's name (without its extension) belongs to."""
    return stem[: -len(_VERTICAL)] if stem.endswith(_VERTICAL) and len(stem) > len(_VERTICAL) else stem


def _size(paths) -> int:
    total = 0
    for p in paths:
        try:
            total += p.stat().st_size
        except OSError:
            pass  # vanished mid-scan; it contributes nothing to the total
    return total


# A project export's own name (server/compose_api.py: "<project id>-r<rev>",
# "-<n>" when taken), the only kind of .srt the app writes beside a video
# under its own name ("<video>.srt", editor suite step 9). Translations are
# "<clip>.<language>.srt" and are never matched.
_EXPORT_STEM = re.compile(r"^.+-r\d+(?:-\d+)?$")


def orphan_export_captions(db, data_dir: Path) -> list[Path]:
    """A new-editor export's captions file ("<video>.srt", step 9) whose
    video is gone (9-D1): beside a file a project_exports row names, or in
    the projects' exports folder under an export's name. Only inside the
    data folder; a .srt in the person's own export folder is theirs."""
    root = Path(str(data_dir.resolve()).lower())
    found: dict[str, Path] = {}

    def consider(srt: Path) -> None:
        try:
            key = Path(str(srt.resolve()).lower())
            if root in key.parents and srt.is_file() and not srt.with_suffix(".mp4").exists():
                found.setdefault(str(key), srt)
        except OSError:
            pass   # vanished or unreadable: nothing to list

    try:
        rows = db.conn.execute("SELECT path FROM project_exports WHERE path IS NOT NULL AND path <> ''").fetchall()
    except Exception:
        rows = []                 # a database from before the new editor
    for r in rows:
        path = Path(str(r["path"]))
        if path.suffix.lower() == ".mp4":
            consider(path.with_suffix(".srt"))
    exports = data_dir.joinpath(*PROJECT_EXPORTS)
    if exports.is_dir():
        for srt in exports.glob("*.srt"):
            if _EXPORT_STEM.match(srt.stem):
                consider(srt)
    return sorted(found.values())


def survey(db, data_dir: Path) -> dict:
    """What is on disk, split into what is safe to delete and what is not."""
    known_videos = {r["video_id"] for r in db.conn.execute("SELECT video_id FROM videos")}
    pinned = pinned_video_ids(db.conn)
    known_clips = {
        str(Path(r["path"]).resolve()).lower()
        for r in db.conn.execute("SELECT path FROM clips WHERE path IS NOT NULL AND path <> ''")
    }
    # A clip's file under its other name (_ASIDE) is still that clip's: the
    # first name of a file a row points at under its .clip-<id> name, and
    # (below) a .clip-<id> file whose first name a row points at.
    other_names = set()
    for known in known_clips:
        path = Path(known)
        aside = _ASIDE.match(path.stem)
        if aside:
            other_names.add(str(path.with_name(aside["first"] + path.suffix)))

    groups: dict[str, list[Path]] = {
        "partial_downloads": [],
        "orphan_downloads": [],
        "orphan_transcripts": [],
        "render_leftovers": [],
        "orphan_clips": [],
        "previews": [],
        "editor_cache": [],
    }

    downloads = data_dir / "downloads"
    if downloads.is_dir():
        for f in downloads.iterdir():
            if not f.is_file():
                continue
            # yt-dlp leftovers from an interrupted download.
            if f.suffix == ".part" or ".part-Frag" in f.name:
                groups["partial_downloads"].append(f)
            elif _video_of(f.stem) not in known_videos and _video_of(f.stem) not in pinned and not (
                    f.stem.endswith(_KEPT) and f.stem[: -len(_KEPT)] in known_videos):
                groups["orphan_downloads"].append(f)

    transcripts = data_dir / "transcripts"
    if transcripts.is_dir():
        groups["orphan_transcripts"] = [
            f for f in transcripts.glob("*.json") if f.stem not in known_videos
        ]

    # A video's voices (analysis/voice_turns.py), a few KB kept beside its
    # transcript and counted with it.
    voice_profiles = data_dir / "voice_profiles"
    if voice_profiles.is_dir():
        groups["orphan_transcripts"] += [
            f for f in voice_profiles.glob("*.json") if f.stem not in known_videos
        ]

    clips = data_dir / "clips"
    exports = Path(str(data_dir.joinpath(*PROJECT_EXPORTS).resolve()).lower())
    if clips.is_dir():
        for f in clips.rglob("*.mp4"):
            key = Path(str(f.resolve()).lower())
            if exports in key.parents:
                continue
            aside = _ASIDE.match(key.stem)
            if f.name.endswith(_SCRATCH_SUFFIXES):
                groups["render_leftovers"].append(f)
            elif str(key) in known_clips or str(key) in other_names:
                continue
            elif aside and str(key.with_name(aside["first"] + key.suffix)) in known_clips:
                continue
            else:
                groups["orphan_clips"].append(f)
    groups["orphan_clips"] += orphan_export_captions(db, data_dir)

    previews = data_dir / "previews"
    if previews.is_dir():
        groups["previews"] = [f for f in previews.iterdir() if f.is_file()]

    # The editor cache's orphan folders, every file in them.
    from video_editor.media import cache as media_cache

    for folder in media_cache.orphans(data_dir, project_media(db.conn)):
        groups["editor_cache"] += media_cache.walk_files(folder)   # never through a link or junction

    # Held: in use, though no row points at it yet (core/in_use.py). Nothing
    # is held outside a Make clips again run, and then nothing is left out.
    held = in_use.files_held()
    if held:
        groups = {k: [f for f in v if in_use.file_key(f) not in held] for k, v in groups.items()}

    reclaimable = {k: {"files": len(v), "bytes": _size(v)} for k, v in groups.items()}

    # Source downloads still in use — reported, never auto-deleted.
    kept = [
        f for f in (downloads.iterdir() if downloads.is_dir() else [])
        if f.is_file() and (_video_of(f.stem) in known_videos or _video_of(f.stem) in pinned)
    ]
    return {
        "reclaimable": reclaimable,
        "reclaimable_bytes": sum(g["bytes"] for g in reclaimable.values()),
        "sources": {"files": len(kept), "bytes": _size(kept)},
        # The whole editor cache, kept to its cap (its orphans are above).
        "editor_cache": media_cache.survey(data_dir),
        "_groups": groups,
    }


def clean(db, data_dir: Path, *, cache_cap: int | None = None) -> dict:
    """Delete everything survey() classed as reclaimable, and hold the
    editor cache to `cache_cap` bytes (its default cap when None). Returns
    the totals actually freed — never touches clips or sources the database
    knows, or a download a project uses."""
    from video_editor.media import cache as media_cache

    found = survey(db, data_dir)
    freed, removed = 0, 0
    for paths in found["_groups"].values():
        for p in paths:
            try:
                n = p.stat().st_size
                p.unlink()
                freed += n
                removed += 1
            except OSError:
                pass  # locked or already gone: skip, never fail the sweep

    # The editor cache: the orphan folders emptied above go (never one a
    # job or a stream has taken since, cache.hold), then empty folders left
    # by earlier sweeps (never a job's work folder or one just made:
    # cache.remove_empty), then the cap.
    base = media_cache.root(data_dir)
    orphan_folders = set()
    for p in found["_groups"]["editor_cache"]:
        try:
            orphan_folders.add(base / p.relative_to(base).parts[0])
        except (ValueError, IndexError):
            pass  # not under the cache folder: never taken for one of its folders
    media_cache.remove_folders(sorted(orphan_folders))
    media_cache.remove_empty(data_dir)
    capped = media_cache.enforce_cap(
        data_dir, media_cache.DEFAULT_CAP_BYTES if cache_cap is None else cache_cap)
    removed += capped["files_removed"]
    freed += capped["bytes_freed"]

    # Clip folders left completely empty once their leftovers are gone.
    clips = data_dir / "clips"
    if clips.is_dir():
        for d in sorted(clips.glob("*/*"), key=lambda p: -len(p.parts)):
            if d.is_dir() and not any(d.iterdir()):
                try:
                    d.rmdir()
                except OSError:
                    pass  # not empty after all, or in use; leaving it is harmless
    return {"files_removed": removed, "bytes_freed": freed}


def orphan_clip_dirs(db, data_dir: Path) -> list[Path]:
    """Clip folders whose video is no longer in the library at all."""
    known = {r["video_id"] for r in db.conn.execute("SELECT video_id FROM videos")}
    out = []
    for d in (data_dir / "clips").glob("*/*"):
        if not d.is_dir():
            continue
        m = re.search(r"\[([^\]]+)\]$", d.name)
        if m and m.group(1) not in known:
            out.append(d)
    return out
