"""The waveform: a min/max pyramid, computed once per source.

FFmpeg decodes the first sound track to mono 16-bit PCM at SAMPLE_RATE
(8192 a second) and writes it to a pipe; it is read in chunks and every
SAMPLES_PER_BIN (16) samples become one bin: the lowest and the highest
sample, each kept as a signed byte (the 16-bit value shifted right by 8, so
-128 to 127). That is the finest level, BASE_RATE (512) bins a second. Each
coarser level takes the min of two mins and the max of two maxes, down to 1
bin a second: 512, 256, 128, ..., 2, 1 (10 levels). A level's last bin may
cover less than its share when the length isn't a whole number of bins.

The standard library does it (array, map over strided slices); numpy, when
it is there, does the same sums faster. Both give the same bytes
(tests/test_media_peaks.py).

**The format** (the stored peaks.bin and what GET /media/{id}/peaks sends;
little-endian):

    0   4  magic b"CKPK"
    4   2  version (1)
    6   2  number of levels, L
    8   4  sample rate the peaks were read at (8192)
    12  8  duration in microseconds (the samples read)
    20  12 x L  per level, finest first: bins a second (u32), bin count
              (u32), index of its first bin (u32; not 0 for a part of the
              source)
    then, per level in the same order, count x 2 signed bytes: min, max

The peaks of 2 hours at 64 bins a second and every level below it: 20 +
7 x 12 + 2 x (460800 + 230400 + ... + 7200) bytes, about 1.8 MB.

Standard library only (numpy optional).
"""

from __future__ import annotations

import struct
import subprocess
import sys
from array import array

from video_editor.media import probe

SAMPLE_RATE = 8192
SAMPLES_PER_BIN = 16
BASE_RATE = SAMPLE_RATE // SAMPLES_PER_BIN    # 512 bins a second
LEVELS = 10                                   # 512 .. 1 bins a second
MAGIC = b"CKPK"
VERSION = 1
_HEAD = struct.Struct("<4sHHIQ")
_LEVEL = struct.Struct("<III")
READ = 1 << 20                                # bytes of PCM per read: a multiple of a bin


def command(ffmpeg: str, source) -> list[str]:
    return [ffmpeg, "-hide_banner", "-nostdin", "-v", "error", *probe.LOCAL_ONLY, "-i", str(source), "-map", "0:a:0", "-vn", "-sn",
            "-dn", "-ac", "1", "-ar", str(SAMPLE_RATE), "-f", "s16le", "-acodec", "pcm_s16le", "pipe:1"]


def _numpy():
    try:
        import numpy
    except ImportError:
        return None
    return numpy


class Builder:
    """Bins from PCM, fed in any pieces. `use_numpy`: None for numpy when
    it is there, False for the standard library."""

    def __init__(self, use_numpy: bool | None = None):
        self.np = _numpy() if use_numpy is not False else None
        self.mins = array("b")
        self.maxs = array("b")
        self.samples = 0
        self._rest = b""

    def feed(self, data: bytes) -> None:
        data = self._rest + data
        whole = len(data) // (2 * SAMPLES_PER_BIN) * (2 * SAMPLES_PER_BIN)
        self._rest = data[whole:]
        if whole:
            self._bins(data[:whole])

    def _bins(self, data: bytes) -> None:
        n = len(data) // 2
        self.samples += n
        if self.np is not None:
            np = self.np
            a = np.frombuffer(data, dtype="<i2")
            if n % SAMPLES_PER_BIN:
                lo = [int(a[n - n % SAMPLES_PER_BIN:].min()) >> 8]
                hi = [int(a[n - n % SAMPLES_PER_BIN:].max()) >> 8]
                a = a[: n - n % SAMPLES_PER_BIN]
            else:
                lo = hi = []
            shaped = a.reshape(-1, SAMPLES_PER_BIN)
            self.mins.frombytes((shaped.min(axis=1) >> 8).astype("i1").tobytes() + bytes(array("b", lo)))
            self.maxs.frombytes((shaped.max(axis=1) >> 8).astype("i1").tobytes() + bytes(array("b", hi)))
            return
        a = array("h")
        a.frombytes(data)
        if sys.byteorder == "big":
            a.byteswap()
        whole = n - n % SAMPLES_PER_BIN
        if whole:
            columns = [a[k:whole:SAMPLES_PER_BIN] for k in range(SAMPLES_PER_BIN)]
            self.mins.extend([v >> 8 for v in map(min, *columns)])
            self.maxs.extend([v >> 8 for v in map(max, *columns)])
        if whole < n:
            tail = a[whole:]
            self.mins.append(min(tail) >> 8)
            self.maxs.append(max(tail) >> 8)

    def finish(self) -> Peaks:
        if len(self._rest) >= 2:
            self._bins(self._rest[: len(self._rest) // 2 * 2])
        self._rest = b""
        duration_us = self.samples * 1_000_000 // SAMPLE_RATE
        return Peaks.from_base(self.mins, self.maxs, duration_us, use_numpy=self.np is not None)


def _halve(mins: array, maxs: array, np=None) -> tuple[array, array]:
    n = len(mins)
    if np is not None and n > 1:
        m = np.frombuffer(bytes(mins), dtype="i1")
        x = np.frombuffer(bytes(maxs), dtype="i1")
        even = n - n % 2
        lo = np.minimum(m[0:even:2], m[1:even:2])
        hi = np.maximum(x[0:even:2], x[1:even:2])
        out_lo, out_hi = array("b", lo.tobytes()), array("b", hi.tobytes())
    else:
        out_lo = array("b", map(min, mins[0::2], mins[1::2]))
        out_hi = array("b", map(max, maxs[0::2], maxs[1::2]))
    if n % 2:
        out_lo.append(mins[-1])
        out_hi.append(maxs[-1])
    return out_lo, out_hi


class Peaks:
    """Levels finest first: [(bins a second, first bin, mins, maxs)]."""

    def __init__(self, levels: list[tuple[int, int, array, array]], duration_us: int,
                 sample_rate: int = SAMPLE_RATE):
        self.levels = levels
        self.duration_us = int(duration_us)
        self.sample_rate = sample_rate

    @classmethod
    def from_base(cls, mins: array, maxs: array, duration_us: int, *, use_numpy: bool = False) -> Peaks:
        np = _numpy() if use_numpy else None
        levels = [(BASE_RATE, 0, mins, maxs)]
        rate = BASE_RATE
        for _ in range(LEVELS - 1):
            rate //= 2
            mins, maxs = _halve(mins, maxs, np)
            levels.append((rate, 0, mins, maxs))
        return cls(levels, duration_us)

    def to_bytes(self) -> bytes:
        head = _HEAD.pack(MAGIC, VERSION, len(self.levels), self.sample_rate, self.duration_us)
        table = b"".join(_LEVEL.pack(rate, len(mins), first) for rate, first, mins, _ in self.levels)
        body = []
        for _, _, mins, maxs in self.levels:
            pairs = array("b", bytes(2 * len(mins)))
            pairs[0::2] = mins
            pairs[1::2] = maxs
            body.append(pairs.tobytes())
        return head + table + b"".join(body)

    @classmethod
    def from_bytes(cls, data: bytes) -> Peaks:
        magic, version, count, rate, duration_us = _HEAD.unpack_from(data, 0)
        if magic != MAGIC or version != VERSION:
            raise ValueError("not a peaks file of this version")
        table = [_LEVEL.unpack_from(data, _HEAD.size + i * _LEVEL.size) for i in range(count)]
        offset = _HEAD.size + count * _LEVEL.size
        levels = []
        for per_second, n, first in table:
            pairs = array("b")
            pairs.frombytes(data[offset: offset + 2 * n])
            offset += 2 * n
            levels.append((per_second, first, pairs[0::2], pairs[1::2]))
        return cls(levels, duration_us, rate)

    def select(self, per_second: int, start_us: int = 0, end_us: int | None = None) -> Peaks:
        """The levels at `per_second` bins a second (rounded down to one of
        512, 256, ..., 1) and coarser, cut to start_us..end_us."""
        end_us = self.duration_us if end_us is None else min(int(end_us), self.duration_us)
        start_us = max(0, min(int(start_us), end_us))
        out = []
        for rate, first, mins, maxs in self.levels:
            if rate > per_second:
                continue
            lo = start_us * rate // 1_000_000
            hi = -(-end_us * rate // 1_000_000)          # rounded up: the bin the end falls in
            lo, hi = max(lo, first), min(hi, first + len(mins))
            out.append((rate, lo, mins[lo - first: hi - first], maxs[lo - first: hi - first]))
        return Peaks(out, self.duration_us, self.sample_rate)

    def to_json(self) -> dict:
        return {"version": VERSION, "sample_rate": self.sample_rate, "duration_us": self.duration_us,
                "levels": [{"per_second": rate, "first": first, "min": list(mins), "max": list(maxs)}
                           for rate, first, mins, maxs in self.levels]}


def compute(source, *, popen=None, use_numpy: bool | None = None, on_progress=None, duration_us: int = 0,
            started=None) -> Peaks:
    """Decode `source`'s first sound track and build its peaks. `started`
    gets the FFmpeg process (the editor lane kills it to cancel);
    `on_progress` a fraction. Raises RuntimeError when FFmpeg fails."""
    from core.binaries import ffmpeg
    from video.export_presets import low_priority

    popen = popen or subprocess.Popen
    proc = popen(command(ffmpeg(), source), stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                 stderr=subprocess.PIPE, **low_priority())
    if started is not None:
        started(proc)
    import threading

    err = []
    reader = threading.Thread(target=lambda: err.append(proc.stderr.read()), daemon=True)
    reader.start()
    builder = Builder(use_numpy)
    total = max(1, duration_us * SAMPLE_RATE * 2 // 1_000_000)
    read = 0
    try:
        while True:
            chunk = proc.stdout.read(READ)
            if not chunk:
                break
            builder.feed(chunk)
            read += len(chunk)
            if on_progress is not None and duration_us:
                on_progress(min(0.999, read / total))
    finally:
        code = proc.wait()
        reader.join(timeout=5)
        for stream in (proc.stdout, proc.stderr):
            try:
                stream.close()
            except Exception:
                pass
    if code != 0:
        text = (err[0] if err else b"").decode("utf-8", "replace").strip().splitlines()
        raise RuntimeError("FFmpeg couldn't read the sound: " + (text[-1] if text else f"exit code {code}"))
    return builder.finish()
