"""Where libass breaks a caption into lines, and how wide its words are,
worked out ahead of the burn.

Pop and bounce change a caption's size with \\fscx/\\fscy inside \\t, and
libass lays the text out again in every frame at the scaled width. A caption
close to the wrap width would change its line count part way through the
animation: one line at pop's 80% start, two at 100%, or two lines for a few
frames at bounce's 110% overshoot. video/captions.py asks lines() for the
breaks libass makes at 100%, writes them as explicit \\N with \\q2 (no
wrapping), and the caption keeps its lines while it grows. A safe zone asks
widest_word() whether one word is wider than the space between the margins
(libass never breaks inside a word), and scales that caption down to fit.

This follows libass's own layout (ass_render.c, wrap_lines_naive and
wrap_lines_rebalance): the font's size is mapped to its usWinAscent +
usWinDescent, a line is measured from the ink of its first glyph to the ink
of its last, a line breaks at the last space before it reaches the space
between the margins, and WrapStyle 0 then moves words down while that makes
neighbouring lines more even, measuring each line without its trailing
spaces. The ASS header has no "Kerning: yes", so libass does not kern, and
neither does this. Checked against the bundled FFmpeg's libass by burning
18,720 captions in 22 fonts: no caption came out with other lines than
lines() said. A decision too close to call (_UNSURE_PX) is worked out both
ways, and lines() says None when the two differ.

Standard library only. A font whose file is not found (a Windows font on a
machine without it), or a caption with a character this model does not
follow (_PLAIN_PUNCTUATION), cannot be measured: lines() and widest_word()
return None, and the caller decides (video/captions.py: a safe zone then
shrinks nothing; pop and bounce fade a caption with such a character in at
full size instead of scaling it).

A glyph's ink is the box around every point of its outline, control points
included (fontfile.x_bounds), as libass takes it: fix round 3's sweep found
Lilita One's "y", whose header box is 6 units tighter, breaking "wi-fi
nobody" a line early.
"""

from __future__ import annotations

import os
from pathlib import Path

from video import fontfile
from video import fonts as bundled

# The file libass picks for each Windows caption font with the style's Bold
# flag on (a weight of 700): the bold face where the family has one.
_WINDOWS_FILES = {
    "Arial": "arialbd.ttf",
    "Arial Black": "ariblk.ttf",
    "Impact": "impact.ttf",
    "Verdana": "verdanab.ttf",
    "Tahoma": "tahomabd.ttf",
    "Trebuchet MS": "trebucbd.ttf",
    "Segoe UI": "segoeuib.ttf",
    "Georgia": "georgiab.ttf",
    "Comic Sans MS": "comicbd.ttf",
    "Courier New": "courbd.ttf",
    "Nirmala UI": "NirmalaB.ttf",
    "Yu Gothic UI": "YuGothB.ttc",
    "Malgun Gothic": "malgunbd.ttf",
    "Microsoft YaHei": "msyhbd.ttc",
    "Leelawadee UI": "LeelaUIb.ttf",
}


def _windows_fonts() -> list[Path]:
    folders = [Path(os.environ.get("WINDIR") or "C:/Windows") / "Fonts"]
    local = os.environ.get("LOCALAPPDATA")
    if local:
        folders.append(Path(local) / "Microsoft" / "Windows" / "Fonts")
    return folders


def font(family: str) -> fontfile.Font | None:
    """The font file libass burns `family` from, read; None when this
    machine does not have it."""
    if bundled.is_bundled(family):
        if not bundled.usable(family):
            return None
        files = bundled.files_for(family)
        return fontfile.load(files[0], family) if files else None
    name = _WINDOWS_FILES.get(family)
    if not name:
        return None
    for folder in _windows_fonts():
        path = folder / name
        if path.is_file():
            return fontfile.load(path, family)
    return None


class Measure:
    """Glyph positions and ink for one font at one size, in script pixels."""

    def __init__(self, face: fontfile.Font, size: float, bold: bool):
        self.face = face
        self.px = size / max(1, face.win_height)      # pixels per font unit, as libass sizes the font
        # libass thickens a face lighter than the bold it was asked for by
        # 1/64 of an em, half on each side of every glyph (its advance stays).
        em = face.units_per_em * self.px
        self.grow = em / 128 if bold and face.weight + 150 < 700 else 0.0
        self.cmap = face.cmap()

    def glyphs(self, text: str) -> list[tuple[str, float, float | None, float | None]]:
        """(character, x where it is drawn, ink left, ink right) for each
        character of a line laid out from x = 0. Ink is None for a space."""
        out = []
        x = 0.0
        for ch in text:
            g = self.cmap.get(ord(ch), 0)
            bounds = None if ch == " " else self.face.x_bounds(g)
            if bounds is None:
                left = right = None
            else:
                left = x + bounds[0] * self.px - self.grow
                right = x + bounds[1] * self.px + self.grow
            out.append((ch, x, left, right))
            x += self.face.advance(g) * self.px
        return out


def _span(glyphs, a: int, b: int) -> float:
    """libass's width of glyphs a..b (inclusive): from the first one's ink
    to the last one's; a space has no ink and counts from where it is drawn."""
    first, last = glyphs[a], glyphs[b]
    left = first[2] if first[2] is not None else first[1]
    right = last[3] if last[3] is not None else last[1]
    return right - left


# Characters libass draws one glyph after another, with no shaping, and
# breaks a line at only when a space follows: Latin, Greek and Cyrillic
# letters and digits, this punctuation and these symbols, and Chinese,
# Japanese and Korean characters (_cjk). The bundled FFmpeg's libass has no
# Unicode line breaking: burning a word of two halves joined by each ASCII
# symbol, an en and em dash, an ellipsis, a soft hyphen and a zero-width
# space, and Chinese, Japanese, Thai and Korean text with no spaces, gave one
# line running past the margins every time (scratch probe_breaks.py, fix
# round 3), never a break. A caption with anything else (an emoji, Thai,
# Hindi, Arabic, a letter the font lacks) may be laid out in a way this
# model does not follow, so lines() does not guess for it.
_PLAIN_PUNCTUATION = set(".,!?'\"’“”:;&()" "-$%#@+/*=<>_|~^[]`" "¿¡«»–—…")
# Thai is shaped (marks are placed, SARA AM is split in two), which moves a
# word's width by a few pixels: too far to tell where a line breaks, close
# enough to tell whether a word is wider than the safe area (widest_word).
_THAI_SLACK = 0.04
_MAX_PASSES = 64
_MAX_TRIES = 64
# How far this model's widths can be from libass's. libass places glyphs in
# 1/64 px from the font's own units, as this does; a burned sweep of 14,400
# captions (10 fonts, 4 sizes, 3 safe zones) found no decision this model
# got wrong outside this distance. Closer than this, a decision is a close
# call: lines() works the caption out both ways and gives up when the two
# differ.
_UNSURE_PX = 0.25


def _cjk(code: int) -> bool:
    """A Chinese, Japanese or Korean character libass draws as one glyph:
    CJK punctuation (but not U+3000, the ideographic space), hiragana and
    katakana, the ideographs, Hangul syllables, and the full-width letters,
    digits and punctuation with the half-width katakana."""
    return (0x3001 <= code < 0x3100 or 0x3400 <= code < 0x4DC0 or 0x4E00 <= code < 0xA000
            or 0xAC00 <= code < 0xD7B0 or 0xFF01 <= code < 0xFFA0)


def _thai(code: int) -> bool:
    return 0x0E01 <= code <= 0x0E5B


def _plain(ch: str) -> bool:
    code = ord(ch)
    if ch in _PLAIN_PUNCTUATION:
        return True
    if ch.isalnum() and (code < 0x0300 or 0x0370 <= code < 0x0530 or 0x1E00 <= code < 0x2000):
        return True
    return _cjk(code)


def _trimmed_end(glyphs, start: int, end: int) -> int:
    """The last glyph of start..end that is not a space (libass leaves the
    spaces at the end of a line out of its width)."""
    while end > start and glyphs[end][0] == " ":
        end -= 1
    return end


def lines(text: str, family: str, size: float, bold: bool, wrap_width: float) -> list[str] | None:
    """The lines libass shows `text` in at 100%, as WrapStyle 0 breaks it
    inside `wrap_width` (the frame's width less the margins). None when the
    font cannot be measured, when the text has a character this model does
    not follow (see _PLAIN_PUNCTUATION), or when a decision too close to
    call (_UNSURE_PX) would change the lines."""
    face = font(family)
    if face is None:
        return None
    m = Measure(face, size, bold)
    for ch in text:
        if ch == " ":
            continue
        if not _plain(ch) or ord(ch) not in m.cmap:
            return None
    glyphs = m.glyphs(text)
    n = len(glyphs)
    if not n:
        return [text]
    # Every close call is tried both ways; the lines are known only when
    # every way gives the same ones.
    found: set[tuple[int, ...]] = set()
    pending: list[tuple[bool, ...]] = [()]
    tries = 0
    while pending:
        tries += 1
        if tries > _MAX_TRIES:
            return None
        choices = pending.pop()
        starts = _wrap(glyphs, wrap_width, choices)
        if starts is None:          # one more close call to try both ways
            pending.extend(((*choices, False), (*choices, True)))
            continue
        if starts == ():            # libass would still be moving words
            return None
        found.add(starts)
        if len(found) > 1:
            return None
    starts = found.pop()
    out = []
    for k, a in enumerate(starts):
        b = starts[k + 1] if k + 1 < len(starts) else n
        out.append(text[a:b].strip(" "))
    return [line for line in out if line]


def _wrap(glyphs, wrap_width: float, choices: tuple[bool, ...]) -> tuple[int, ...] | None:
    """Where each line starts, taking `choices` for the close calls in the
    order they come up; None at a close call `choices` has no answer for,
    and () when libass would not settle."""
    n = len(glyphs)
    calls = iter(choices)

    def call() -> bool | None:
        return next(calls, None)

    # breaks[i]: glyph i starts a line.
    breaks = [False] * n
    # The first pass (wrap_lines_naive): a glyph whose ink reaches the wrap
    # width breaks the line at the last space before it. Whether a word
    # goes down depends on its furthest-reaching glyph, and a word that
    # reaches to within _UNSURE_PX of the wrap width is a close call.
    s1 = 0
    last_space = -1
    unsure = False
    for i in range(n):
        if glyphs[i][0] == " ":
            last_space = i
            unsure = False
            continue
        reach = _span(glyphs, s1, i)
        down = False
        if last_space >= 0:
            if reach >= wrap_width + _UNSURE_PX:
                down = True
            elif reach > wrap_width - _UNSURE_PX:
                unsure = True
        if not down and unsure and (i + 1 == n or glyphs[i + 1][0] == " "):
            down = call()
            if down is None:
                return None
        if down:
            breaks[last_space + 1] = True
            s1 = last_space + 1
            last_space = -1
            unsure = False
    # The second pass (wrap_lines_rebalance, WrapStyle 0), step by step as
    # libass does it: walking the lines in order, move the last word of a
    # line down when that makes the two lines more even, each line measured
    # without its trailing spaces. After a move libass goes on comparing
    # from where the moved line used to start (its s1, s2 and s3 are not
    # updated), and so does this; it repeats until a pass moves nothing.
    for _ in range(_MAX_PASSES):
        moved = False
        s1 = s2 = None
        s3 = 0
        for i in range(n + 1):
            if i < n and not breaks[i]:
                continue
            s1, s2, s3 = s2, s3, i
            if s1 is None or not breaks[s2]:
                continue
            w = s2 - 1
            while w > s1 and glyphs[w][0] == " ":
                w -= 1
            while w > s1 and glyphs[w][0] != " ":
                w -= 1
            e1 = _trimmed_end(glyphs, s1, w)
            if glyphs[w][0] == " ":
                w += 1
            if w == s1:
                continue          # merging two lines never helps
            e1_old = _trimmed_end(glyphs, s1, s2 - 1)
            e2 = _trimmed_end(glyphs, s2, s3 - 1)
            before = abs(_span(glyphs, s1, e1_old) - _span(glyphs, s2, e2))
            after = abs(_span(glyphs, s1, e1) - _span(glyphs, w, e2))
            if abs(after - before) < 2 * _UNSURE_PX:
                move = call()
                if move is None:
                    return None
            else:
                move = after < before
            if move:
                breaks[w] = True
                breaks[s2] = False
                moved = True
        if not moved:
            return tuple([0] + [i for i in range(1, n) if breaks[i]])
    return ()


def widest(text_lines: list[str], family: str, size: float, bold: bool) -> float | None:
    """The width of the widest of `text_lines` at 100%, as libass measures it."""
    face = font(family)
    if face is None:
        return None
    m = Measure(face, size, bold)
    best = 0.0
    for line in text_lines:
        glyphs = m.glyphs(line)
        if glyphs:
            best = max(best, _span(glyphs, 0, len(glyphs) - 1))
    return best


def widest_word(text: str, family: str, size: float, bold: bool) -> float | None:
    """The width of the widest word of `text` (split at ordinary spaces), as libass
    measures a line: libass breaks lines only at spaces, so a word wider
    than the space between the margins runs past them. A caption in a
    language written without spaces (Chinese, Japanese, Thai) is one word.
    None when the font cannot be measured or a character is outside what
    this model follows (the characters lines() follows, and Thai, whose
    width is given with _THAI_SLACK on top for its shaping; all in the font
    itself)."""
    face = font(family)
    if face is None:
        return None
    m = Measure(face, size, bold)
    slack = 1.0
    for ch in text:
        code = ord(ch)
        if ch == " ":
            continue
        if code not in m.cmap:
            return None
        if _thai(code):
            slack = 1.0 + _THAI_SLACK
        elif not _plain(ch):
            return None
    best = 0.0
    # Only at U+0020, the one place libass breaks: any other kind of space
    # (a no-break space joins its two words into one run) has already made
    # this return None above, never a too-small width.
    for word in text.split(" "):
        if not word:
            continue
        glyphs = m.glyphs(word)
        best = max(best, _span(glyphs, 0, len(glyphs) - 1))
    return best * slack
