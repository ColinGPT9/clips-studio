"""Read what the captions need from a TrueType font file: which characters
it has, and how wide each one is drawn.

Standard library only (struct). Two uses:

- video/fonts.py asks which characters a bundled font has (its cmap), so a
  language whose letters the font lacks burns in a font that has them, and
  tests/test_caption_fonts.py checks each family's "scripts" in the manifest
  against the file itself.
- video/caption_fit.py measures a caption the way libass lays it out, so an
  animation that changes the caption's size never changes its line breaks.

Reads .ttf and .ttc (a collection: the font whose family name matches).
Fonts with glyph outlines in a CFF table have no glyf bounding boxes; for
those the advance widths stand in. Nothing here raises on a bad file: load()
returns None.
"""

from __future__ import annotations

import struct
import threading
from functools import lru_cache
from pathlib import Path

_LOCK = threading.Lock()


class Font:
    """One font of a file: its names, weight, metrics and character map."""

    def __init__(self, data: bytes, base: int):
        self._data = data
        tables = {}
        num = struct.unpack_from(">H", data, base + 4)[0]
        for i in range(num):
            tag, _check, offset, length = struct.unpack_from(">4sIII", data, base + 12 + 16 * i)
            tables[tag.decode("latin-1")] = (offset, length)
        self._tables = tables
        head = tables["head"][0]
        self.units_per_em = struct.unpack_from(">H", data, head + 18)[0]
        self._long_loca = struct.unpack_from(">h", data, head + 50)[0] == 1
        self.glyph_count = struct.unpack_from(">H", data, tables["maxp"][0] + 4)[0]
        self._metric_count = struct.unpack_from(">H", data, tables["hhea"][0] + 34)[0]
        self.weight = 400
        self.bold_flag = False
        self.win_height = 0
        if "OS/2" in tables:
            os2 = tables["OS/2"][0]
            self.weight = struct.unpack_from(">H", data, os2 + 4)[0]
            self.bold_flag = bool(struct.unpack_from(">H", data, os2 + 62)[0] & 0x20)
            win_ascent, win_descent = struct.unpack_from(">hh", data, os2 + 74)
            self.win_height = win_ascent + win_descent
        if self.win_height <= 0:
            asc, desc = struct.unpack_from(">hh", data, tables["hhea"][0] + 4)
            self.win_height = asc - desc
        self.names = self._names()
        self._cmap: dict[int, int] | None = None
        self._bounds: dict[int, tuple[float, float] | None] = {}

    # ---- names -------------------------------------------------------------------------

    def _names(self) -> dict[int, list[str]]:
        """name ID -> the strings the name table gives it (Windows and Mac)."""
        out: dict[int, list[str]] = {}
        if "name" not in self._tables:
            return out
        base = self._tables["name"][0]
        data = self._data
        _fmt, count, strings = struct.unpack_from(">HHH", data, base)
        for i in range(count):
            platform, _enc, _lang, name_id, length, offset = struct.unpack_from(">6H", data, base + 6 + 12 * i)
            raw = data[base + strings + offset:base + strings + offset + length]
            try:
                text = raw.decode("utf-16-be") if platform in (0, 3) else raw.decode("latin-1")
            except UnicodeDecodeError:
                continue
            if text and text not in out.setdefault(name_id, []):
                out[name_id].append(text)
        return out

    def family_names(self) -> list[str]:
        """The family names libass matches a font by (name IDs 1 and 16)."""
        return self.names.get(1, []) + self.names.get(16, [])

    # ---- characters ----------------------------------------------------------------------

    def cmap(self) -> dict[int, int]:
        """Character -> glyph, from the best Unicode subtable."""
        if self._cmap is None:
            self._cmap = self._read_cmap()
        return self._cmap

    def codepoints(self) -> frozenset[int]:
        return frozenset(c for c, g in self.cmap().items() if g)

    def _read_cmap(self) -> dict[int, int]:
        if "cmap" not in self._tables:
            return {}
        data = self._data
        base = self._tables["cmap"][0]
        count = struct.unpack_from(">H", data, base + 2)[0]
        found = {}
        for i in range(count):
            platform, encoding, offset = struct.unpack_from(">HHI", data, base + 4 + 8 * i)
            fmt = struct.unpack_from(">H", data, base + offset)[0]
            found[(platform, encoding, fmt)] = base + offset
        for key in ((3, 10, 12), (0, 6, 12), (0, 4, 12), (3, 1, 4), (0, 3, 4), (0, 4, 4), (0, 1, 4), (0, 0, 4),
                    (3, 0, 4)):
            if key in found:
                return self._format12(found[key]) if key[2] == 12 else self._format4(found[key])
        return {}

    def _format4(self, at: int) -> dict[int, int]:
        data = self._data
        segs = struct.unpack_from(">H", data, at + 6)[0] // 2
        ends = struct.unpack_from(f">{segs}H", data, at + 14)
        starts = struct.unpack_from(f">{segs}H", data, at + 16 + 2 * segs)
        deltas = struct.unpack_from(f">{segs}h", data, at + 16 + 4 * segs)
        range_at = at + 16 + 6 * segs
        ranges = struct.unpack_from(f">{segs}H", data, range_at)
        out = {}
        for s in range(segs):
            start, end, delta, rng = starts[s], ends[s], deltas[s], ranges[s]
            if start == 0xFFFF:
                continue
            for c in range(start, end + 1):
                if rng == 0:
                    g = (c + delta) & 0xFFFF
                else:
                    where = range_at + 2 * s + rng + 2 * (c - start)
                    g = struct.unpack_from(">H", data, where)[0]
                    if g:
                        g = (g + delta) & 0xFFFF
                if g:
                    out[c] = g
        return out

    def _format12(self, at: int) -> dict[int, int]:
        data = self._data
        groups = struct.unpack_from(">I", data, at + 12)[0]
        out = {}
        for i in range(groups):
            start, end, glyph = struct.unpack_from(">III", data, at + 16 + 12 * i)
            for c in range(start, min(end, 0x10FFFF) + 1):
                if glyph + c - start:
                    out[c] = glyph + c - start
        return out

    # ---- widths --------------------------------------------------------------------------

    def advance(self, glyph: int) -> int:
        """The glyph's advance width, in font units."""
        index = min(glyph, self._metric_count - 1)
        return struct.unpack_from(">H", self._data, self._tables["hmtx"][0] + 4 * index)[0]

    def x_bounds(self, glyph: int) -> tuple[float, float] | None:
        """(xMin, xMax) of the glyph's outline, in font units; None for an
        empty glyph (a space).

        These are the bounds of every point of the outline, the curves'
        control points included, because that is what libass measures a
        line by. The box in the glyph's own header can be tighter (the
        curve itself): Lilita One's "y" has a control point 6 units right
        of its header's xMax, and libass broke "wi-fi nobody" one line
        earlier than the header's box said. The header's box stands in only
        for a glyph whose points cannot be read."""
        if glyph in self._bounds:
            return self._bounds[glyph]
        bounds = self._x_bounds(glyph)
        self._bounds[glyph] = bounds
        return bounds

    def _x_bounds(self, glyph: int) -> tuple[float, float] | None:
        if "glyf" not in self._tables or "loca" not in self._tables:
            adv = self.advance(glyph)
            return (0, adv) if adv else None
        where = self._glyph_at(glyph)
        if where is None:
            return None
        try:
            points = self._points(glyph, 0)
        except (struct.error, IndexError, ValueError):
            points = None
        if points:
            xs = [p[0] for p in points]
            return min(xs), max(xs)
        x_min, _y_min, x_max, _y_max = struct.unpack_from(">hhhh", self._data, where + 2)
        return x_min, x_max

    def _glyph_at(self, glyph: int) -> int | None:
        """Where the glyph's data starts in the file; None for an empty one."""
        loca = self._tables["loca"][0]
        if self._long_loca:
            start, end = struct.unpack_from(">II", self._data, loca + 4 * glyph)
        else:
            start, end = (2 * v for v in struct.unpack_from(">HH", self._data, loca + 2 * glyph))
        if end <= start:
            return None
        return self._tables["glyf"][0] + start

    def _points(self, glyph: int, depth: int) -> list[tuple[float, float]] | None:
        """Every point of the glyph's outline (on the curve and off it), in
        font units; [] for an empty glyph, None when they cannot be worked
        out here (a part placed by matching points, parts nested too deep)."""
        if depth > 8 or not 0 <= glyph < self.glyph_count:
            return None
        at = self._glyph_at(glyph)
        if at is None:
            return []
        data = self._data
        contours = struct.unpack_from(">h", data, at)[0]
        at += 10
        if contours >= 0:
            if contours == 0:
                return []
            count = struct.unpack_from(">H", data, at + 2 * (contours - 1))[0] + 1
            at += 2 * contours
            at += 2 + struct.unpack_from(">H", data, at)[0]        # the hinting program
            flags = []
            while len(flags) < count:
                flag = data[at]
                at += 1
                flags.append(flag)
                if flag & 0x08:
                    repeat = data[at]
                    at += 1
                    flags.extend([flag] * repeat)
            flags = flags[:count]
            axes = []
            for short, same in ((0x02, 0x10), (0x04, 0x20)):
                values, v = [], 0
                for flag in flags:
                    if flag & short:
                        v += data[at] if flag & same else -data[at]
                        at += 1
                    elif not flag & same:
                        v += struct.unpack_from(">h", data, at)[0]
                        at += 2
                    values.append(v)
                axes.append(values)
            return list(zip(axes[0], axes[1]))
        out: list[tuple[float, float]] = []
        while True:
            flags, part = struct.unpack_from(">HH", data, at)
            at += 4
            if flags & 0x0001:
                dx, dy = struct.unpack_from(">hh", data, at)
                at += 4
            else:
                dx, dy = struct.unpack_from(">bb", data, at)
                at += 2
            a, b, c, d = 1.0, 0.0, 0.0, 1.0
            if flags & 0x0008:
                a = d = struct.unpack_from(">h", data, at)[0] / 16384
                at += 2
            elif flags & 0x0040:
                a, d = (v / 16384 for v in struct.unpack_from(">hh", data, at))
                at += 4
            elif flags & 0x0080:
                a, b, c, d = (v / 16384 for v in struct.unpack_from(">hhhh", data, at))
                at += 8
            if not flags & 0x0002:
                return None                     # placed by matching two points
            inner = self._points(part, depth + 1)
            if inner is None:
                return None
            if flags & 0x0800:                  # the offset is scaled with the part
                dx, dy = a * dx + c * dy, b * dx + d * dy
            out.extend((a * x + c * y + dx, b * x + d * y + dy) for x, y in inner)
            if not flags & 0x0020:
                return out


def _fonts_in(data: bytes) -> list[int]:
    """Where each font of a file starts: one for a .ttf, several in a .ttc."""
    if data[:4] == b"ttcf":
        count = struct.unpack_from(">I", data, 8)[0]
        return list(struct.unpack_from(f">{count}I", data, 12))
    return [0]


@lru_cache(maxsize=32)
def _load(path: str, size: int, mtime: int, family: str) -> Font | None:
    try:
        data = Path(path).read_bytes()
        candidates = [Font(data, base) for base in _fonts_in(data)]
    except (OSError, struct.error, KeyError, ValueError, IndexError):
        return None
    if not candidates:
        return None
    if family:
        wanted = family.casefold()
        named = [f for f in candidates if wanted in (n.casefold() for n in f.family_names())]
        if named:
            return named[0]
        return None if len(candidates) > 1 else candidates[0]
    return candidates[0]


def load(path: Path | str, family: str = "") -> Font | None:
    """The font in `path` (in a collection, the one named `family`), or None
    when the file is missing or cannot be read. Cached by path and mtime."""
    try:
        st = Path(path).stat()
    except OSError:
        return None
    with _LOCK:
        return _load(str(path), st.st_size, st.st_mtime_ns, family)
