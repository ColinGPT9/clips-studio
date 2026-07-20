"""Find and remove files the app no longer needs.

Processing the same video repeatedly leaves things behind: a render that
failed or was cancelled kept its multi-hundred-MB staging file, yt-dlp
leaves `.part` fragments when a download is interrupted, and every editor
preview writes a full clip that is never read again. None of it is
referenced by the database, so it is invisible — the disk just shrinks.

Nothing here touches a file the database still points at. Source downloads
for videos you still have are NOT scratch and are reported separately: the
editor, re-rendering and translated burns all read them, so removing one
costs you those features until the video is downloaded again.
"""

import re
from pathlib import Path

# Staging files written next to a clip during rendering. The pipeline
# deletes them on success; these are the ones a failure left behind.
_SCRATCH_SUFFIXES = (".source.mp4", ".edited.mp4", ".plain.mp4", ".cropped.mp4")


def _size(paths) -> int:
    total = 0
    for p in paths:
        try:
            total += p.stat().st_size
        except OSError:
            pass
    return total


def survey(db, data_dir: Path) -> dict:
    """What is on disk, split into what is safe to delete and what is not."""
    known_videos = {r["video_id"] for r in db.conn.execute("SELECT video_id FROM videos")}
    known_clips = {
        str(Path(r["path"]).resolve()).lower()
        for r in db.conn.execute("SELECT path FROM clips WHERE path IS NOT NULL AND path <> ''")
    }

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
            elif f.stem not in known_videos:
                groups["orphan_downloads"].append(f)

    transcripts = data_dir / "transcripts"
    if transcripts.is_dir():
        groups["orphan_transcripts"] = [
            f for f in transcripts.glob("*.json") if f.stem not in known_videos
        ]

    clips = data_dir / "clips"
    if clips.is_dir():
        for f in clips.rglob("*.mp4"):
            if f.name.endswith(_SCRATCH_SUFFIXES):
                groups["render_leftovers"].append(f)
            elif str(f.resolve()).lower() not in known_clips:
                groups["orphan_clips"].append(f)

    previews = data_dir / "previews"
    if previews.is_dir():
        groups["previews"] = [f for f in previews.iterdir() if f.is_file()]

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
                    pass
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
