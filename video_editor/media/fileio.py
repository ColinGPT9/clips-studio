"""A media file served in byte ranges, the way a <video> element asks for it.

- open_shared(path): the file opened for reading so that, on Windows, it can
  still be renamed, replaced or deleted while it streams (CreateFileW with
  FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE). Python's own
  open() leaves out FILE_SHARE_DELETE, so a file it holds can't be renamed
  or deleted until it is closed. Elsewhere a plain open.
- parse_range(): the Range header (RFC 9110 section 14): one range
  `bytes=a-b`, open-ended `bytes=a-` or a suffix `bytes=-n`.
- etag(), if_none_match(), if_range(): a strong validator from the file's
  size and modification time.

Standard library only.
"""

from __future__ import annotations

import os
import re
import stat
import threading
from email.utils import formatdate
from pathlib import Path

CHUNK = 256 * 1024

CONTENT_TYPES = {
    ".mp4": "video/mp4", ".m4v": "video/mp4", ".mov": "video/quicktime", ".mkv": "video/x-matroska",
    ".webm": "video/webm", ".ts": "video/mp2t", ".flv": "video/x-flv", ".avi": "video/x-msvideo",
    ".mp3": "audio/mpeg", ".m4a": "audio/mp4", ".aac": "audio/aac", ".wav": "audio/wav", ".ogg": "audio/ogg",
    ".opus": "audio/ogg", ".flac": "audio/flac",
    ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp", ".gif": "image/gif",
    ".bmp": "image/bmp",
    ".json": "application/json", ".bin": "application/octet-stream",
}


def content_type(path) -> str:
    return CONTENT_TYPES.get(Path(path).suffix.lower(), "application/octet-stream")


# ---- opening ------------------------------------------------------------------------------------

_lock = threading.Lock()
_open: dict[int, str] = {}          # id(SharedFile) -> path, while it is open (tests read it)


def open_paths() -> list[str]:
    """The paths open_shared() holds open right now."""
    with _lock:
        return sorted(_open.values())


class SharedFile:
    """A file open for reading at any offset. `size` and `mtime_ns` come from
    the open handle, so they describe the bytes this handle reads even when
    the name is replaced meanwhile."""

    def __init__(self, fd: int, path: str):
        self.fd = fd
        self.path = path
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode):
            os.close(fd)
            raise IsADirectoryError(f"{Path(path).name} is not a file")
        self.size = int(st.st_size)
        self.mtime_ns = int(st.st_mtime_ns)
        self._closed = False
        with _lock:
            _open[id(self)] = path

    def read_at(self, offset: int, n: int) -> bytes:
        if hasattr(os, "pread"):
            return os.pread(self.fd, n, offset)
        os.lseek(self.fd, offset, os.SEEK_SET)
        return os.read(self.fd, n)

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        with _lock:
            _open.pop(id(self), None)
        try:
            os.close(self.fd)
        except OSError:
            pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


def _open_windows(path: str) -> int:
    import ctypes
    import msvcrt
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    create = kernel32.CreateFileW
    create.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID, wintypes.DWORD,
                       wintypes.DWORD, wintypes.HANDLE]
    create.restype = wintypes.HANDLE
    generic_read, open_existing, normal = 0x80000000, 3, 0x80
    share_all = 0x1 | 0x2 | 0x4      # FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE
    handle = create(path, generic_read, share_all, None, open_existing, normal, None)
    if handle is None or handle == wintypes.HANDLE(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        return msvcrt.open_osfhandle(handle, os.O_RDONLY | getattr(os, "O_BINARY", 0))
    except OSError:
        kernel32.CloseHandle(wintypes.HANDLE(handle))
        raise


def open_shared(path) -> SharedFile:
    """Open `path` for reading. On Windows it can be renamed, replaced or
    deleted while open; the bytes read stay those of the file that was
    opened. Raises OSError (FileNotFoundError when it isn't there)."""
    path = str(path)
    if os.name == "nt":
        fd = _open_windows(path)
    else:
        # O_NONBLOCK: opening a FIFO never waits. A regular file reads as usual.
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_NONBLOCK", 0))
    return SharedFile(fd, path)


# ---- ranges and validators ----------------------------------------------------------------------


_DIGITS = re.compile(r"[0-9]+")


class Unsatisfiable(Exception):
    """The Range asks only for bytes past the end of the file (416)."""


def parse_range(header: str | None, size: int) -> tuple[int, int] | None:
    """(first, last) byte, both included, for one range of a file of `size`
    bytes; None to answer with the whole file (no Range, a unit that isn't
    bytes, several ranges, or a header that doesn't parse, which RFC 9110
    says to ignore). Raises Unsatisfiable for a range wholly past the end, or
    a suffix of 0 bytes."""
    if not header:
        return None
    unit, _, spec = header.strip().partition("=")
    if unit.strip().lower() != "bytes" or not spec or "," in spec:
        return None
    first, dash, last = spec.strip().partition("-")
    first, last = first.strip(), last.strip()
    # ASCII digits only: str.isdigit() also takes the superscripts the
    # Latin-1 header decoding can give (byte B2 becomes a superscript two),
    # which int() refuses.
    if not dash or (first and not _DIGITS.fullmatch(first)) or (last and not _DIGITS.fullmatch(last)) or not (
            first or last):
        return None
    if not first:                                   # bytes=-n: the last n bytes
        n = int(last)
        if n == 0 or size == 0:
            raise Unsatisfiable()
        return max(0, size - n), size - 1
    start = int(first)
    if last and int(last) < start:
        return None
    if start >= size:
        raise Unsatisfiable()
    end = min(int(last), size - 1) if last else size - 1
    return start, end


def etag(size: int, mtime_ns: int) -> str:
    """A strong ETag from the file's size and modification time."""
    return f'"{int(size):x}-{int(mtime_ns):x}"'


def last_modified(mtime_ns: int) -> str:
    return formatdate(int(mtime_ns) / 1e9, usegmt=True)


def _tags(header: str) -> list[str]:
    return [t.strip() for t in header.split(",") if t.strip()]


def if_none_match(header: str | None, tag: str) -> bool:
    """Whether If-None-Match matches (weak comparison): answer 304."""
    if not header:
        return False
    for t in _tags(header):
        if t == "*" or t.removeprefix("W/") == tag:
            return True
    return False


def if_range(header: str | None, tag: str, modified: str) -> bool:
    """Whether a Range may be honoured under If-Range: no If-Range, an
    entity tag that matches strongly, or the exact Last-Modified date."""
    if header is None:
        return True
    header = header.strip()
    if header.startswith('"') or header.startswith("W/"):
        return header == tag                        # strong comparison: a weak tag never matches
    return header == modified
