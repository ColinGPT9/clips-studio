"""The editor cache: data/media_cache/<fingerprint>/, one folder per source.

    data/media_cache/
      <fingerprint>/              64 hex characters
        source.json               what it was made from: {ref, path, size, mtime_ns, made_at}
        info.json                 what the source is (probe.describe), read once
        proxy.mp4 | proxy.m4a     the proxy, when one was asked for or needed
        peaks.bin                 the waveform pyramid (peaks.py)
        thumbs/                   the thumbnail sheets and their index (thumbs.py)

- **The fingerprint** comes from the source's identity: its size,
  modification time and the sha256 of its first and last megabyte; for a
  file imported for editing, its own sha256. A file replaced under the same
  name gets a new folder, so nothing made from the old one is ever served
  for the new one.
- **Never half written.** Every product is written under a temporary name
  (`<name>.part-<8 hex>`, a folder for the thumbnails) and renamed when it
  is complete. Only the final names are ever served. A temporary one the app
  was closed in the middle of is removed the next time the cache is used.
- **Housekeeping** (core/housekeeping.py): the cache is its own group.
  Orphan folders (their source is gone and no project names it) are removed
  by Storage's clean-up; a size cap (DEFAULT_CAP_BYTES, `editor.media_cache_gb`
  in the settings) removes the least recently used products first, never
  one a running job or an open stream uses (hold()).
- Nothing here is in downloads/ or clips/, which the other clean-ups sweep.

Standard library only.
"""

from __future__ import annotations

import hashlib
import json
import os
import threading
import time
import uuid
from contextlib import contextmanager
from pathlib import Path

DIR = "media_cache"
SOURCE, INFO = "source.json", "info.json"
PRODUCTS = ("proxy.mp4", "proxy.m4a", "peaks.bin", "thumbs")
DEFAULT_CAP_BYTES = 5 * 1024 ** 3
PART = ".part-"
EDGE = 1024 * 1024              # the first and last megabyte go into a fingerprint
TOUCH_EVERY = 60.0              # seconds between two "used" marks of one product


def root(data_dir) -> Path:
    return Path(data_dir) / DIR


def cap_bytes(config: dict | None) -> int:
    """The cap: `editor.media_cache_gb` in the settings when it is a number
    above 0 (installed builds never get new settings.yaml keys, so the
    default lives here), else 5 GB."""
    editor = (config or {}).get("editor") if isinstance(config, dict) else None
    raw = editor.get("media_cache_gb") if isinstance(editor, dict) else None
    if isinstance(raw, (int, float)) and not isinstance(raw, bool) and 0 < raw < 10_000:
        return int(raw * 1024 ** 3)
    return DEFAULT_CAP_BYTES


# ---- fingerprints -------------------------------------------------------------------------------

_fingerprints: dict[tuple, str] = {}
_fp_lock = threading.Lock()


def fingerprint(path, *, sha256: str | None = None) -> str:
    """The cache folder's name for a source. An imported file's own sha256;
    otherwise from its size, modification time and first and last megabyte
    (read without locking the file: fileio.open_shared)."""
    if sha256:
        return str(sha256)
    from video_editor.media.fileio import open_shared

    with open_shared(path) as f:
        key = (str(path), f.size, f.mtime_ns)
        with _fp_lock:
            known = _fingerprints.get(key)
        if known:
            return known
        digest = hashlib.sha256(f"clips-kitty-media-1\0{f.size}\0{f.mtime_ns}\0".encode())
        digest.update(f.read_at(0, EDGE))
        if f.size > EDGE:
            digest.update(f.read_at(max(EDGE, f.size - EDGE), EDGE))
    value = digest.hexdigest()
    with _fp_lock:
        if len(_fingerprints) > 4096:
            _fingerprints.clear()
        _fingerprints[key] = value
    return value


def folder_for(data_dir, media) -> Path:
    """The cache folder for a resolver.Media, made with its source.json the
    first time."""
    folder = root(data_dir) / fingerprint(media.path, sha256=media.sha256)
    if not (folder / SOURCE).is_file():
        # Held while it is made, so the empty-folder sweep (remove_empty)
        # never takes it between the mkdir and its source.json.
        with hold(folder):
            folder.mkdir(parents=True, exist_ok=True)
            st = media.path.stat()
            write_json(folder / SOURCE, {"ref": media.ref, "path": str(media.path), "size": st.st_size,
                                         "mtime_ns": st.st_mtime_ns, "made_at": time.time()}, once=True)
    sweep_partials_once(data_dir)
    return folder


# ---- writing ------------------------------------------------------------------------------------


def part_name(target: Path) -> Path:
    return target.with_name(f"{target.name}{PART}{uuid.uuid4().hex[:8]}")


@contextmanager
def writing(target: Path):
    """A temporary name beside `target`, renamed to it when the block ends
    without an error; removed otherwise. A folder works the same way: the
    caller makes it."""
    tmp = part_name(Path(target))
    try:
        yield tmp
        if Path(target).is_dir():
            _remove(Path(target))
        os.replace(tmp, target)
    finally:
        if tmp.exists():
            _remove(tmp)


def write_json(target: Path, value, *, once: bool = False) -> None:
    """Write `value` to `target` through a temporary name. `once`: the file
    says the same whoever writes it (source.json, info.json), so when
    several requests for a new media race to write it and Windows refuses
    the rename because another one has the file open or is replacing it
    (WinError 5), the file that is there will do. Otherwise the rename is
    tried a few times before the error is raised."""
    target = Path(target)
    tmp = part_name(target)
    try:
        tmp.write_text(json.dumps(value, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
        for attempt in range(5):
            try:
                os.replace(tmp, target)
                return
            except PermissionError:
                if once and target.is_file():
                    return
                if attempt == 4:
                    raise
                time.sleep(0.02 * (attempt + 1))
    finally:
        if tmp.exists():
            _remove(tmp)


def read_json(path: Path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _remove(path: Path) -> int:
    """Remove a file or folder; the bytes that went."""
    freed = 0
    if path.is_dir() and not path.is_symlink():
        for p in sorted(path.rglob("*"), key=lambda p: -len(p.parts)):
            freed += _remove(p)
        try:
            path.rmdir()
        except OSError:
            pass
        return freed
    try:
        size = path.stat().st_size
        path.unlink()
        return size
    except OSError:
        return 0


# ---- in use -------------------------------------------------------------------------------------

_holds: dict[str, int] = {}
_holds_lock = threading.Lock()


def _key(folder) -> str:
    return os.path.normcase(str(Path(folder).resolve()))


@contextmanager
def hold(folder):
    """Keep a cache folder from the cap, the orphan sweep and Clear while a
    job writes into it or a stream reads from it."""
    key = _key(folder)
    with _holds_lock:
        _holds[key] = _holds.get(key, 0) + 1
    try:
        yield
    finally:
        with _holds_lock:
            _holds[key] -= 1
            if not _holds[key]:
                del _holds[key]


def held(folder) -> bool:
    with _holds_lock:
        return _key(folder) in _holds


_touched: dict[str, float] = {}


def touch(product: Path, *, now: float | None = None) -> None:
    """Mark a product used (its access time, set explicitly so it works on
    drives that don't keep access times), at most once a minute."""
    product = Path(product)
    marker = product / "index.json" if product.is_dir() else product
    key = str(marker)
    clock = time.monotonic()
    if now is None and clock - _touched.get(key, -TOUCH_EVERY) < TOUCH_EVERY:
        return
    _touched[key] = clock
    try:
        st = marker.stat()
        os.utime(marker, ns=(int((now if now is not None else time.time()) * 1e9), st.st_mtime_ns))
    except OSError:
        pass


def used_at(product: Path) -> float:
    marker = product / "index.json" if product.is_dir() else product
    try:
        st = marker.stat()
    except OSError:
        return 0.0
    return max(st.st_atime, st.st_mtime)


# ---- what is there ------------------------------------------------------------------------------


def _size(path: Path) -> int:
    if path.is_dir():
        return sum(_size(p) for p in path.iterdir())
    try:
        return path.stat().st_size
    except OSError:
        return 0


def _files(path: Path) -> int:
    if path.is_dir():
        return sum(_files(p) for p in path.iterdir())
    return 1 if path.exists() else 0


def folders(data_dir) -> list[Path]:
    base = root(data_dir)
    if not base.is_dir():
        return []
    return sorted(p for p in base.iterdir() if p.is_dir() and PART not in p.name)


def survey(data_dir) -> dict:
    """{"folders", "files", "bytes"} for the whole cache."""
    found = folders(data_dir)
    return {"folders": len(found), "files": sum(_files(f) for f in found),
            "bytes": sum(_size(f) for f in found)}


_swept: set[str] = set()


def sweep_partials_once(data_dir) -> None:
    key = str(Path(data_dir).resolve())
    if key in _swept:
        return
    _swept.add(key)
    remove_partials(data_dir)


def remove_partials(data_dir) -> int:
    """Remove the temporary names a stopped write left (never in a held
    folder). Returns how many."""
    base = root(data_dir)
    removed = 0
    if not base.is_dir():
        return 0
    for folder in base.iterdir():
        if PART in folder.name:
            _remove(folder)
            removed += 1
            continue
        if not folder.is_dir() or held(folder):
            continue
        for p in folder.iterdir():
            if PART in p.name:
                _remove(p)
                removed += 1
    return removed


EMPTY_GRACE = 60.0              # seconds an empty folder is left alone after it last changed


def remove_empty(data_dir, *, now: float | None = None) -> int:
    """Remove the cache folders, and the folders inside them, that are
    empty (Storage's clean-up, after it has emptied the orphans). Never one
    that is held (a job writing into it, a stream reading from it, a folder
    being made), never a temporary name (a job's work folder, which stays
    empty until FFmpeg writes its first file), and never one that changed
    in the last EMPTY_GRACE seconds. Returns how many went."""
    base = root(data_dir)
    if not base.is_dir():
        return 0
    clock = time.time() if now is None else now
    removed = 0

    def fresh(d: Path) -> bool:
        try:
            return clock - d.stat().st_mtime < EMPTY_GRACE
        except OSError:
            return True

    for folder in base.iterdir():
        if not folder.is_dir() or folder.is_symlink() or PART in folder.name or held(folder):
            continue
        inner = sorted((p for p in folder.rglob("*") if p.is_dir() and not p.is_symlink()),
                       key=lambda p: -len(p.parts))
        for d in [*inner, folder]:
            if PART in d.name or held(folder) or fresh(d):
                continue
            try:
                if not any(d.iterdir()):
                    d.rmdir()
                    removed += 1
            except OSError:
                pass  # in use, or not empty after all: next time
    return removed


def _source_gone(meta) -> bool:
    if not isinstance(meta, dict) or not isinstance(meta.get("path"), str):
        return True
    try:
        st = Path(meta["path"]).stat()
    except OSError:
        return True
    return st.st_size != meta.get("size") or st.st_mtime_ns != meta.get("mtime_ns")


def _named(meta, named: dict[str, set[str]]) -> bool:
    """Whether a project names the source a folder was made from."""
    ref = str(meta.get("ref") or "") if isinstance(meta, dict) else ""
    kind, _, value = ref.partition(":")
    if kind == "file":
        return value in named.get("file", set())
    return value in named.get(kind, set())


def orphans(data_dir, named: dict[str, set[str]]) -> list[Path]:
    """Folders whose source is gone (deleted, or replaced by another file)
    and that no project names. `named` is core/state.py project_media()."""
    return [f for f in folders(data_dir)
            if not held(f) and _source_gone(read_json(f / SOURCE)) and not _named(read_json(f / SOURCE), named)]


def remove_folders(paths) -> dict:
    files = freed = 0
    for folder in paths:
        if held(folder):
            continue
        files += _files(folder)
        freed += _remove(folder)
    return {"files_removed": files, "bytes_freed": freed}


def products(data_dir) -> list[Path]:
    out = []
    for folder in folders(data_dir):
        out += [folder / name for name in PRODUCTS if (folder / name).exists()]
    return out


def enforce_cap(data_dir, cap: int) -> dict:
    """Remove the least recently used products until the cache is no bigger
    than `cap` bytes. A product in a held folder stays."""
    total = survey(data_dir)["bytes"]
    removed = freed = 0
    if total <= cap:
        return {"files_removed": 0, "bytes_freed": 0}
    for product in sorted(products(data_dir), key=used_at):
        if total <= cap:
            break
        if held(product.parent):
            continue
        files = _files(product)
        gone = _remove(product)
        if gone or not product.exists():
            removed += files
            freed += gone
            total -= gone
    return {"files_removed": removed, "bytes_freed": freed}


def clear(data_dir) -> dict:
    """The "Clear editor cache" button: every folder but those in use."""
    found = folders(data_dir)
    kept = [f for f in found if held(f)]
    out = remove_folders(f for f in found if f not in kept)
    out["kept_in_use"] = len(kept)
    return out
