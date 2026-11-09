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
"""

import re
from pathlib import Path

from core import in_use

# Staging files written next to a clip during rendering. The pipeline
# deletes them on success; these are the ones a failure left behind.
_SCRATCH_SUFFIXES = (".source.mp4", ".edited.mp4", ".plain.mp4", ".cropped.mp4")

# A Make clips again run (core/remake.py) gives each old clip's file a second
# name while it works, clip_00010-00070.clip-17.mp4, and its rows point
# there; the video's own copy gets one too, <video id>.remaking.mp4. If the
# app is closed in the middle, a row can say one name while its file has the
# other, until the next start puts it right.
_ASIDE = re.compile(r"^(?P<first>.+)\.clip-\d+$")
_KEPT = ".remaking"


def _size(paths) -> int:
    total = 0
    for p in paths:
        try:
            total += p.stat().st_size
        except OSError:
            pass  # vanished mid-scan; it contributes nothing to the total
    return total


def survey(db, data_dir: Path) -> dict:
    """What is on disk, split into what is safe to delete and what is not."""
    known_videos = {r["video_id"] for r in db.conn.execute("SELECT video_id FROM videos")}
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
    }

    downloads = data_dir / "downloads"
    if downloads.is_dir():
        for f in downloads.iterdir():
            if not f.is_file():
                continue
            # yt-dlp leftovers from an interrupted download.
            if f.suffix == ".part" or ".part-Frag" in f.name:
                groups["partial_downloads"].append(f)
            elif f.stem not in known_videos and not (
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
    if clips.is_dir():
        for f in clips.rglob("*.mp4"):
            key = Path(str(f.resolve()).lower())
            aside = _ASIDE.match(key.stem)
            if f.name.endswith(_SCRATCH_SUFFIXES):
                groups["render_leftovers"].append(f)
            elif str(key) in known_clips or str(key) in other_names:
                continue
            elif aside and str(key.with_name(aside["first"] + key.suffix)) in known_clips:
                continue
            else:
                groups["orphan_clips"].append(f)

    previews = data_dir / "previews"
    if previews.is_dir():
        groups["previews"] = [f for f in previews.iterdir() if f.is_file()]

    # Held: in use, though no row points at it yet (core/in_use.py). Nothing
    # is held outside a Make clips again run, and then nothing is left out.
    held = in_use.files_held()
    if held:
        groups = {k: [f for f in v if in_use.file_key(f) not in held] for k, v in groups.items()}

    reclaimable = {k: {"files": len(v), "bytes": _size(v)} for k, v in groups.items()}

    # Source downloads still in use — reported, never auto-deleted.
    kept = [
        f for f in (downloads.iterdir() if downloads.is_dir() else [])
        if f.is_file() and f.stem in known_videos
    ]
    return {
        "reclaimable": reclaimable,
        "reclaimable_bytes": sum(g["bytes"] for g in reclaimable.values()),
        "sources": {"files": len(kept), "bytes": _size(kept)},
        "_groups": groups,
    }


def clean(db, data_dir: Path) -> dict:
    """Delete everything survey() classed as reclaimable. Returns the totals
    actually freed — never touches clips or sources the database knows."""
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
