"""Editor suite step 6: serving a file in byte ranges
(video_editor/media/fileio.py). Standard library only: runs on CI's light
install.

- Every form of Range: a-b, a- (open-ended), -n (suffix), past the end
  (416), and the ones that are ignored (several ranges, another unit,
  nonsense, b before a).
- The strong ETag, If-None-Match and If-Range.
- On Windows a file being read can be renamed, replaced and deleted, and
  the reader still reads the bytes it opened (skipped elsewhere).
"""

import os
import sys

import pytest

from video_editor.media import fileio

SIZE = 1000


@pytest.mark.parametrize(("header", "expected"), [
    ("bytes=0-99", (0, 99)),
    ("bytes=100-199", (100, 199)),
    ("bytes=990-2000", (990, 999)),          # an end past the end is the last byte
    ("bytes=500-", (500, 999)),              # open-ended
    ("bytes=-100", (900, 999)),              # the last 100 bytes
    ("bytes=-5000", (0, 999)),               # a suffix longer than the file is all of it
    ("bytes=999-999", (999, 999)),
    (" Bytes = 0-0 ", (0, 0)),
])
def test_one_range(header, expected):
    assert fileio.parse_range(header, SIZE) == expected


@pytest.mark.parametrize("header", [
    None, "", "bytes=0-1,5-6", "items=0-1", "bytes=", "bytes=-", "bytes=abc", "bytes=5-2", "bytes=1-x",
    "bytes=0x10-20",
])
def test_ranges_that_are_ignored(header):
    assert fileio.parse_range(header, SIZE) is None


@pytest.mark.parametrize(("header", "size"), [("bytes=1000-", SIZE), ("bytes=5000-6000", SIZE),
                                              ("bytes=-0", SIZE), ("bytes=0-", 0), ("bytes=-10", 0)])
def test_ranges_past_the_end(header, size):
    with pytest.raises(fileio.Unsatisfiable):
        fileio.parse_range(header, size)


def test_etag_and_validators():
    tag = fileio.etag(1234, 5_000_000_000)
    assert tag == '"4d2-12a05f200"'
    assert fileio.etag(1234, 5_000_000_001) != tag and fileio.etag(1235, 5_000_000_000) != tag
    modified = fileio.last_modified(5_000_000_000)
    assert modified.endswith("GMT")
    assert fileio.if_none_match(tag, tag) and fileio.if_none_match(f'W/{tag}', tag)
    assert fileio.if_none_match(f'"x", {tag}', tag) and fileio.if_none_match("*", tag)
    assert not fileio.if_none_match('"other"', tag) and not fileio.if_none_match(None, tag)
    assert fileio.if_range(None, tag, modified)
    assert fileio.if_range(tag, tag, modified) and fileio.if_range(modified, tag, modified)
    assert not fileio.if_range('"other"', tag, modified)
    assert not fileio.if_range(f"W/{tag}", tag, modified)       # a weak tag never matches If-Range
    assert not fileio.if_range("Thu, 01 Jan 1970 00:00:00 GMT", tag, modified)


def test_content_types():
    assert fileio.content_type("a.MP4") == "video/mp4"
    assert fileio.content_type("a.webm") == "video/webm"
    assert fileio.content_type("a.m4a") == "audio/mp4"
    assert fileio.content_type("sheet-001.jpg") == "image/jpeg"
    assert fileio.content_type("a.weird") == "application/octet-stream"


def test_reading_at_any_offset_and_closing(tmp_path):
    path = tmp_path / "known.bin"
    data = bytes(range(256)) * 40
    path.write_bytes(data)
    f = fileio.open_shared(path)
    assert str(path) in fileio.open_paths()
    assert (f.size, f.mtime_ns) == (len(data), os.stat(path).st_mtime_ns)
    assert f.read_at(0, 10) == data[:10]
    assert f.read_at(5000, 300) == data[5000:5300]
    assert f.read_at(len(data) - 3, 100) == data[-3:]
    f.close()
    f.close()
    assert str(path) not in fileio.open_paths()


def test_a_folder_or_a_missing_file_is_not_opened(tmp_path):
    with pytest.raises(OSError):
        fileio.open_shared(tmp_path)
    with pytest.raises(FileNotFoundError):
        fileio.open_shared(tmp_path / "nope.mp4")
    assert fileio.open_paths() == []


@pytest.mark.skipif(sys.platform != "win32", reason="share-delete is about Windows file locking")
def test_on_windows_a_file_being_read_can_be_renamed_replaced_and_deleted(tmp_path):
    path = tmp_path / "source.mp4"
    data = os.urandom(3 * fileio.CHUNK)
    path.write_bytes(data)
    f = fileio.open_shared(path)
    try:
        assert f.read_at(0, fileio.CHUNK) == data[: fileio.CHUNK]       # half read
        moved = tmp_path / "moved.mp4"
        os.rename(path, moved)                                            # renamed while open
        os.remove(moved)                                                  # deleted while open
        assert not moved.exists()
        moved.write_bytes(b"a new file")                                  # and replaced by a new file
        assert moved.read_bytes() == b"a new file"
        assert f.read_at(fileio.CHUNK, fileio.CHUNK) == data[fileio.CHUNK: 2 * fileio.CHUNK]   # still the old bytes
    finally:
        f.close()


@pytest.mark.skipif(sys.platform != "win32", reason="share-delete is about Windows file locking")
def test_on_windows_a_file_being_read_is_replaced_by_moving_it_aside_first(tmp_path):
    """os.replace (MoveFileExW) still can't write over a name that has an
    open handle, share-delete or not. Moving the old file aside first (or
    deleting it, above) works while it streams."""
    path = tmp_path / "source.mp4"
    path.write_bytes(b"old" * 1000)
    newer = tmp_path / "newer.mp4"
    newer.write_bytes(b"a new file")
    with fileio.open_shared(path) as f:
        try:
            os.replace(newer, path)     # refused on Windows 10; a later Windows may allow it
        except PermissionError:
            os.rename(path, tmp_path / "aside.mp4")
            os.replace(newer, path)
            os.remove(tmp_path / "aside.mp4")
        assert path.read_bytes() == b"a new file"
        assert f.read_at(0, 3) == b"old"


@pytest.mark.skipif(sys.platform != "win32", reason="shows why share-delete is needed on Windows")
def test_on_windows_pythons_own_open_would_have_locked_it(tmp_path):
    path = tmp_path / "source.mp4"
    path.write_bytes(b"x" * 100)
    with open(path, "rb"), pytest.raises(PermissionError):
        os.rename(path, tmp_path / "moved.mp4")
