"""Editor suite step 6: the editor cache (video_editor/media/cache.py) and
its place in Storage's clean-up (core/housekeeping.py).

- The fingerprint changes when the file changes; an imported file's is its
  sha256.
- A product is written under a temporary name and appears only whole; a
  write that fails leaves nothing, and leftovers are swept.
- The cap removes the least recently used product first, never one in use.
- Orphan folders (source gone, no project names it) are reclaimable, and
  the clean-up removes them; one a project names stays.
- The rest of the clean-up never touches the cache, and the cache never
  holds a file of downloads/ or clips/.

Standard library only: runs on CI's light install.
"""

import os
import time

import pytest
from media_helpers import add_project, add_video

from core import housekeeping
from core.state import StateDB, project_media
from video_editor.media import cache, resolver


@pytest.fixture
def lib(tmp_path):
    data = tmp_path / "data"
    db = StateDB(data / "state.db")
    (data / "downloads").mkdir()
    yield db, data
    db.conn.close()


def _media(path, ref="video:v1", sha256=None):
    return resolver.Media(ref=ref, asked=ref, kind="video", path=path.resolve(), name=path.name, sha256=sha256)


def _product(folder, name, size, used):
    path = folder / name
    if name == "thumbs":
        path.mkdir(exist_ok=True)
        (path / "index.json").write_bytes(b"{}")
        (path / "sheet-001.jpg").write_bytes(b"j" * size)
        marker = path / "index.json"
    else:
        path.write_bytes(b"p" * size)
        marker = path
    os.utime(marker, (used, used))
    return path


def test_the_fingerprint_follows_the_file(tmp_path):
    source = tmp_path / "v.mp4"
    source.write_bytes(os.urandom(3 * cache.EDGE))
    first = cache.fingerprint(source)
    assert len(first) == 64 and cache.fingerprint(source) == first
    data = bytearray(source.read_bytes())
    data[-10] ^= 0xFF                                   # same size: the last megabyte changed
    source.write_bytes(bytes(data))
    st = source.stat()
    os.utime(source, ns=(st.st_atime_ns, st.st_mtime_ns + 1_000_000_000))
    second = cache.fingerprint(source)
    assert second != first
    os.utime(source, ns=(st.st_atime_ns, st.st_mtime_ns + 2_000_000_000))  # touched only
    assert cache.fingerprint(source) not in (first, second)
    assert cache.fingerprint(source, sha256="ab" * 32) == "ab" * 32


def test_a_replaced_file_gets_a_new_folder(lib):
    _db, data = lib
    source = data / "downloads" / "v1.mp4"
    source.write_bytes(b"first file")
    first = cache.folder_for(data, _media(source))
    assert cache.read_json(first / cache.SOURCE)["ref"] == "video:v1"
    source.unlink()
    source.write_bytes(b"another file, longer")
    second = cache.folder_for(data, _media(source))
    assert second != first and second.parent == first.parent == data / "media_cache"


def test_a_product_appears_only_whole(tmp_path):
    target = tmp_path / "peaks.bin"
    with cache.writing(target) as tmp:
        tmp.write_bytes(b"half")
        assert not target.exists() and cache.PART in tmp.name
    assert target.read_bytes() == b"half"
    with pytest.raises(RuntimeError), cache.writing(tmp_path / "proxy.mp4") as tmp:
        tmp.write_bytes(b"half a proxy")
        raise RuntimeError("FFmpeg stopped")
    assert sorted(p.name for p in tmp_path.iterdir()) == ["peaks.bin"]
    folder = tmp_path / "thumbs"
    for n in (1, 2):
        with cache.writing(folder) as tmp:
            tmp.mkdir()
            (tmp / "index.json").write_text(str(n))
    assert (folder / "index.json").read_text() == "2" and sorted(p.name for p in tmp_path.iterdir()) == [
        "peaks.bin", "thumbs"]


def test_leftovers_of_a_stopped_write_are_swept(lib):
    _db, data = lib
    folder = cache.root(data) / ("a" * 64)
    folder.mkdir(parents=True)
    (folder / "proxy.mp4.part-1234abcd").write_bytes(b"half")
    (folder / "thumbs.part-1234abcd").mkdir()
    busy = cache.root(data) / ("b" * 64)
    busy.mkdir()
    (busy / "peaks.bin.part-00000000").write_bytes(b"being written")
    with cache.hold(busy):
        assert cache.remove_partials(data) == 2
    assert list(folder.iterdir()) == [] and (busy / "peaks.bin.part-00000000").exists()


def test_the_cap_removes_the_least_recently_used_first(lib):
    _db, data = lib
    base = cache.root(data)
    now = time.time()
    a, b, c = (base / (ch * 64) for ch in "abc")
    for f in (a, b, c):
        f.mkdir(parents=True)
    oldest = _product(a, "proxy.mp4", 1000, now - 300)
    middle = _product(b, "thumbs", 1000, now - 200)
    newest = _product(c, "peaks.bin", 1000, now - 100)
    in_use = _product(c, "proxy.mp4", 1000, now - 400)            # the oldest of all, but in use
    with cache.hold(c):
        out = cache.enforce_cap(data, 2100)
    assert not oldest.exists() and not middle.exists() and newest.exists() and in_use.exists()
    assert out["bytes_freed"] == 2002 and out["files_removed"] == 3
    assert cache.enforce_cap(data, 10 ** 9) == {"files_removed": 0, "bytes_freed": 0}


def test_using_a_product_makes_it_recent(lib):
    _db, data = lib
    folder = cache.root(data) / ("a" * 64)
    folder.mkdir(parents=True)
    first = _product(folder, "proxy.mp4", 1000, time.time() - 500)
    second = _product(folder, "peaks.bin", 1000, time.time() - 400)
    cache.touch(first, now=time.time())
    cache.enforce_cap(data, 1500)
    assert first.exists() and not second.exists()


def test_the_cap_setting():
    assert cache.cap_bytes(None) == cache.DEFAULT_CAP_BYTES == 5 * 1024 ** 3
    assert cache.cap_bytes({"editor": {"media_cache_gb": 2}}) == 2 * 1024 ** 3
    assert cache.cap_bytes({"editor": {"media_cache_gb": 0.5}}) == 512 * 1024 ** 2
    for bad in (0, -1, "3", True, None, 1e9):
        assert cache.cap_bytes({"editor": {"media_cache_gb": bad}}) == cache.DEFAULT_CAP_BYTES


def _cached(data, source, ref):
    folder = cache.folder_for(data, _media(source, ref))
    (folder / "peaks.bin").write_bytes(b"p" * 100)
    (folder / "thumbs").mkdir()
    (folder / "thumbs" / "sheet-001.jpg").write_bytes(b"j" * 50)
    (folder / "thumbs" / "index.json").write_bytes(b"{}")
    return folder


def test_orphans_are_folders_whose_source_is_gone_and_no_project_names(lib):
    db, data = lib
    sources = {name: data / "downloads" / f"{name}.mp4" for name in ("kept", "gone", "named")}
    for path in sources.values():
        path.write_bytes(path.name.encode())
    folders = {name: _cached(data, path, f"video:{name}") for name, path in sources.items()}
    sources["gone"].unlink()
    sources["named"].unlink()
    add_project(db.conn, {"m1": ("video", {"type": "video", "video_id": "named"})})
    assert cache.orphans(data, project_media(db.conn)) == [folders["gone"]]
    with cache.hold(folders["gone"]):
        assert cache.orphans(data, project_media(db.conn)) == []


def test_storage_shows_the_cache_and_cleans_only_its_orphans(lib):
    db, data = lib
    add_video(db.conn, "v1")
    source = data / "downloads" / "v1.mp4"
    source.write_bytes(b"the video")
    gone = data / "downloads" / "v2.mp4"
    gone.write_bytes(b"another")
    live = _cached(data, source, "video:v1")
    orphan = _cached(data, gone, "video:v2")
    gone.unlink()
    found = housekeeping.survey(db, data)
    assert found["reclaimable"]["editor_cache"] == {
        "files": 4, "bytes": 152 + (orphan / cache.SOURCE).stat().st_size}
    assert found["editor_cache"]["folders"] == 2 and found["editor_cache"]["files"] == 8
    for group in ("orphan_downloads", "orphan_clips", "render_leftovers", "previews", "partial_downloads"):
        assert not any("media_cache" in str(p) for p in found["_groups"][group])
    out = housekeeping.clean(db, data)
    assert not orphan.exists() and (live / "peaks.bin").exists() and (live / "thumbs" / "sheet-001.jpg").exists()
    assert source.exists() and out["files_removed"] >= 3


def test_the_clean_up_holds_the_cache_to_its_cap(lib):
    db, data = lib
    add_video(db.conn, "v1")
    source = data / "downloads" / "v1.mp4"
    source.write_bytes(b"the video")
    folder = _cached(data, source, "video:v1")
    os.utime(folder / "peaks.bin", (time.time() - 1000, time.time() - 1000))
    housekeeping.clean(db, data, cache_cap=cache.survey(data)["bytes"] - 60)
    assert not (folder / "peaks.bin").exists() and (folder / "thumbs" / "sheet-001.jpg").exists()


def test_clear_keeps_what_is_in_use(lib):
    _db, data = lib
    a = cache.root(data) / ("a" * 64)
    b = cache.root(data) / ("b" * 64)
    for f in (a, b):
        f.mkdir(parents=True)
        (f / "peaks.bin").write_bytes(b"x" * 10)
    with cache.hold(b):
        out = cache.clear(data)
    assert out == {"files_removed": 1, "bytes_freed": 10, "kept_in_use": 1}
    assert not a.exists() and (b / "peaks.bin").exists()
    assert cache.clear(data)["kept_in_use"] == 0 and cache.folders(data) == []


def test_the_cache_holds_nothing_of_downloads_or_clips(lib):
    _db, data = lib
    assert "downloads" not in cache.root(data).parts and "clips" not in cache.root(data).parts
    assert cache.root(data).parent == data


def test_free_up_never_takes_a_running_jobs_empty_work_folder(lib):
    """A thumbnails job's work folder (thumbs.part-xxxx) is empty until
    FFmpeg writes its first sheet. Storage's clean-up leaves it, and the
    folder it is in, while the job holds that folder."""
    db, data = lib
    add_video(db.conn, "v1")
    source = data / "downloads" / "v1.mp4"
    source.write_bytes(b"the video")
    folder = cache.folder_for(data, _media(source))
    work = cache.part_name(folder / "thumbs")
    work.mkdir()
    old = time.time() - 3600
    os.utime(work, (old, old))
    with cache.hold(folder):
        housekeeping.clean(db, data)
        assert work.is_dir()
    os.utime(work, (old, old))
    housekeeping.clean(db, data)
    assert work.is_dir()                    # a temporary name is never taken for an empty folder


def test_empty_folders_go_only_when_free_and_not_new(lib):
    _db, data = lib
    old = time.time() - 3600
    empty = cache.root(data) / ("e" * 64)
    inner = empty / "thumbs"
    inner.mkdir(parents=True)
    fresh = cache.root(data) / ("f" * 64)
    fresh.mkdir()
    held = cache.root(data) / ("a" * 64)
    held.mkdir()
    for d in (inner, empty, held):
        os.utime(d, (old, old))
    with cache.hold(held):
        assert cache.remove_empty(data) == 1        # inner; empty itself changed just now
    assert not inner.exists() and empty.is_dir() and fresh.is_dir() and held.is_dir()
    assert cache.remove_empty(data, now=time.time() + 2 * cache.EMPTY_GRACE) == 3
    assert cache.folders(data) == []


def test_a_new_folder_is_held_while_it_is_made(lib, monkeypatch):
    _db, data = lib
    source = data / "downloads" / "v1.mp4"
    source.write_bytes(b"the video")
    seen = []
    real = cache.write_json

    def watching(target, value, **kw):
        seen.append(cache.held(target.parent))
        real(target, value, **kw)

    monkeypatch.setattr(cache, "write_json", watching)
    cache.folder_for(data, _media(source))
    assert seen == [True]


def test_a_shared_file_another_request_holds_is_taken_as_written(tmp_path, monkeypatch):
    """Windows refuses to replace a file another request has open (WinError
    5). source.json and info.json say the same whoever writes them, so the
    one that is there does; any other write tries again, then fails."""
    target = tmp_path / "info.json"
    target.write_text('{"a":1}')
    calls = []

    def refused(src, dst):
        calls.append(dst)
        raise PermissionError(13, "Access is denied")

    monkeypatch.setattr(cache.os, "replace", refused)
    cache.write_json(target, {"a": 1}, once=True)
    assert len(calls) == 1 and target.read_text() == '{"a":1}'
    with pytest.raises(PermissionError):
        cache.write_json(target, {"a": 2})
    assert len(calls) == 6
    assert sorted(p.name for p in tmp_path.iterdir()) == ["info.json"]     # no temporary file left
