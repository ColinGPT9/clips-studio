"""Post styles: the overall look of a finished Short, beyond its framing.

"default" is the look every clip has always had: captions in the style the
user picked, nothing else drawn on the picture.

"highlights" is the sports-page look (the style House of Highlights made
familiar on Reels, Shorts and TikTok). The clip is framed exactly as any
other Short (face tracking, or the sport's own framing for a match), and on
top of it go:

- a stacked title card for the whole clip: a bold condensed ALL CAPS
  headline in yellow on a black box, and under it an optional second line
  in black on a yellow box, each box hugging its own line, an emoji at the
  end of the line in colour;
- spoken captions in the same voice: yellow, ALL CAPS, black outline, in
  the middle of the frame, clear of the card.

Only the style is borrowed: no logo, name or watermark of theirs is drawn.
The user's own handle or logo goes on through Branding (watermark) as for
any clip.

The card is drawn with Pillow, not libass, because libass has no colour
emoji and the emoji are half the look. It is laid over the finished clip in
one extra encode, the same way an image watermark is.

The style is chosen with the caption style ({"post_style": "highlights"}),
so it rides the same plumbing as every other caption setting: the Generate
bar, per-clip re-renders and the remote render workers all carry it.
"""

import functools
import os
import re
import shutil
import subprocess
import unicodedata
from pathlib import Path

DEFAULT = "default"
HIGHLIGHTS = "highlights"
STYLES = (DEFAULT, HIGHLIGHTS)
CARD_POSITIONS = ("lower", "top")

# Colours sampled from their posts.
YELLOW = (245, 250, 0, 255)
BLACK = (0, 0, 0, 255)

# The captions under the highlights style. Size and words per caption stay
# the user's; the rest IS the style. Impact is the closest condensed heavy
# face every stock Windows install has (video/captions.py FONTS).
CAPTION_LOOK = {
    "font": "Impact",
    "color": "#F5FA00",
    "uppercase": True,
    "position": "middle",
    "highlight": False,
    "second_speaker": False,
}

# Card geometry, as fractions of the frame WIDTH unless noted, measured off
# their posts at 1080 wide: a short headline is ~100px type, a long one
# shrinks to fit, and a line that still does not fit wraps into a second
# box of the same kind.
_MAX_TEXT_W = 0.86       # widest a line of text may be
_HEAD_TEXT_W = 0.74      # how wide they let a headline get before its type shrinks
_HEAD_SIZE = 0.093       # headline type size, before fitting
_HEAD_MIN = 0.056        # smallest it shrinks to before wrapping
_SUB_RATIO = 0.80        # second line's size against the headline's
_PAD_X = 0.36            # box padding, in ems of that line's size
_PAD_Y_HEAD = 0.44       # the headline box is roomier than the second line's
_PAD_Y_SUB = 0.34
_RADIUS = 0.18
# Where the card sits, as fractions of the frame HEIGHT: "lower" keeps its
# bottom edge above the platform's caption and buttons; "top" sits under the
# platform's own top bar.
_LOWER_BOTTOM = 0.80
_TOP_TOP = 0.13

# A line wraps into at most this many boxes. Past that the type shrinks
# further, and only at its smallest is the line cut short.
_MAX_LINES = 4

# Emoji, drawn from the colour emoji font. A character here is an emoji
# whatever follows it: the pictographs, the dingbats and miscellaneous
# symbols (bar the plain stars), the few symbols elsewhere whose usual look
# IS the emoji, and ‼ ⁉, which no condensed face has.
_EMOJI_ALWAYS = (
    "\U0001F000-\U0001FAFF\u2600-\u2604\u2607-\u27BF\u203C\u2049"
    "\u231A\u231B\u23E9-\u23EC\u23F0\u23F3\u25FD\u25FE\u2B1B\u2B1C\u2B50\u2B55"
)
# These are text unless a U+FE0F after one asks for the emoji: arrows,
# technical signs, shapes, stars, ™ and the like. "LEBRON → LAKERS ★" is
# typography; "▪" or "▶" with U+FE0F after it is an emoji.
_EMOJI_ON_REQUEST = (
    "\u00A9\u00AE\u2122\u2139\u2190-\u21FF\u2300-\u23FF\u24C2\u25A0-\u25FF\u2605\u2606"
    "\u2934\u2935\u2B00-\u2BFF\u3030\u303D\u3297\u3299"
)
# What rides on an emoji: the emoji selector, a skin tone, a flag's tag letters.
_EMOJI_MODS = "(?:\uFE0F|[\U0001F3FB-\U0001F3FF]|[\U000E0020-\U000E007F])*"
# One emoji as a phone counts it: a keycap (a digit, U+FE0F, U+20E3), a flag
# (two regional letters), or a pictograph with what rides on it, joined to
# more by ZWJ.
_EMOJI = (
    "[#*0-9]\uFE0F?\u20E3"
    "|[\U0001F1E6-\U0001F1FF]{2}"
    f"|(?:[{_EMOJI_ALWAYS}]|[{_EMOJI_ON_REQUEST}]\uFE0F){_EMOJI_MODS}"
    f"(?:\u200D[{_EMOJI_ALWAYS}{_EMOJI_ON_REQUEST}]{_EMOJI_MODS})*"
)
_EMOJI_CLUSTER = re.compile(_EMOJI)
_EMOJI_RUN = re.compile(f"(?:{_EMOJI})+")
# A hashtag has a letter in it: "#1 PICK" and "#23 WENT OFF" are a draft
# rank and a jersey number, and stay; #nba, #2k25 and #ゴール go.
_HASHTAG = re.compile(r"(^|\s)#(?=\w*[^\W\d_])\w+")

# Languages written right to left. Whisper can detect more than the app
# publishes in, so this is wider than multilingual/languages.py.
_RTL_LANGUAGES = {"ar", "fa", "ur", "he", "iw", "yi", "ps", "sd", "ug", "dv", "ckb", "syr"}
# Kinsoku, the short form: a Japanese or Chinese line never starts with
# closing punctuation, a small kana or a long-vowel mark.
_NO_LINE_START = "ーぁぃぅぇぉっゃゅょゎァィゥェォッャュョヮヵヶ々"


def resolve(caption_style: dict | None) -> str:
    """The post style a clip renders in; anything unknown is the default."""
    name = str((caption_style or {}).get("post_style") or DEFAULT).strip().lower()
    return name if name in STYLES else DEFAULT


def card_position(caption_style: dict | None) -> str:
    pos = str((caption_style or {}).get("card_position") or "lower").strip().lower()
    return pos if pos in CARD_POSITIONS else "lower"


def caption_style_for(caption_style: dict | None) -> dict:
    """The caption style a highlights clip burns with: the style's look, the
    user's size and words per caption."""
    return {**(caption_style or {}), **CAPTION_LOOK}


def card_text(text: str) -> str:
    """A line for the card: ALL CAPS, no hashtags, single spaces. Emoji stay."""
    text = _HASHTAG.sub(" ", str(text or ""))
    text = re.sub(r"\s+", " ", text).strip(" -|·")
    return text.upper()


def headline_from_title(title: str) -> str:
    """The card's headline when the model wrote none: the post's title."""
    return card_text(title)


# ---- fonts ----------------------------------------------------------------


def _windows_fonts() -> Path:
    return Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts"


# (file, variation instances to try or None, horizontal squeeze). A face that is not
# condensed is squeezed so it still reads as their narrow heavy type.
# Windows first; the rest are what a Linux render worker or a dev box has.
_LATIN_FACES = [
    ("bahnschrift.ttf", ("Bold Condensed", "SemiBold Condensed"), 1.0),
    ("impact.ttf", None, 1.0),
    ("arialbd.ttf", None, 0.82),
    ("/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf", None, 0.82),
    ("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", None, 0.78),
]
# Languages written in other scripts: a bold face that has the glyphs, so a
# card in Hindi or Japanese is not a row of empty boxes. Same keys as
# video/captions.py SCRIPT_FONTS.
_SCRIPT_FACES = {
    "nirmala": [("NirmalaB.ttf", None, 1.0), ("Nirmala.ttc", None, 1.0)],
    "ja": [("YuGothB.ttc", None, 1.0), ("meiryob.ttc", None, 1.0)],
    "ko": [("malgunbd.ttf", None, 1.0)],
    "zh": [("msyhbd.ttc", None, 1.0), ("msyh.ttc", None, 1.0)],
    "th": [("LeelUIb.ttf", None, 1.0), ("LeelawUI.ttf", None, 1.0)],
    "segoe": [("segoeuib.ttf", None, 1.0)],
}
# The end of every language's list: broad bold faces, so text in a script
# its list does not name (Hebrew, which has no entry in SCRIPT_FONTS, or a
# Greek name on a Hindi card) still finds a face that has the glyphs.
_FALLBACK_FACES = [
    ("segoeuib.ttf", None, 1.0),
    ("arialbd.ttf", None, 1.0),
    ("/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf", None, 1.0),
    ("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", None, 1.0),
]
_EMOJI_FACES = [
    "seguiemj.ttf",
    "/usr/share/fonts/truetype/noto/NotoColorEmoji.ttf",
    "/System/Library/Fonts/Apple Color Emoji.ttc",
]


def _faces_for(language: str) -> list[tuple[str, tuple | None, float]]:
    from video.captions import SCRIPT_FONTS

    script = SCRIPT_FONTS.get((language or "en").split("-")[0].lower())
    if script is None:
        faces = _LATIN_FACES
    else:
        key = {"Nirmala UI": "nirmala", "Yu Gothic UI": "ja", "Malgun Gothic": "ko",
               "Microsoft YaHei": "zh", "Leelawadee UI": "th"}.get(script, "segoe")
        faces = _SCRIPT_FACES[key]
    named = {name for name, _, _ in faces}
    return faces + [f for f in _FALLBACK_FACES if f[0] not in named]


def _resolve_file(name: str) -> Path | None:
    p = Path(name)
    if not p.is_absolute():
        p = _windows_fonts() / name
    return p if p.exists() else None


@functools.cache
def _raqm() -> bool:
    """Whether Pillow can shape text (libraqm, with FriBiDi). Without it text
    is laid out a character at a time: Arabic is not joined or put right to
    left, and emoji sequences are not combined. Pillow's Windows wheels load
    FriBiDi only when one is installed, which a stock install has not."""
    from PIL import features

    return bool(features.check_feature("raqm"))


def _layout():
    from PIL import ImageFont

    return ImageFont.Layout.RAQM if _raqm() else ImageFont.Layout.BASIC


def _invisible(ch: str) -> bool:
    """A mark that only means something to the shaper or an emoji font: a
    variation selector, the zero-width joiner, a flag's tag letters."""
    return "\uFE00" <= ch <= "\uFE0F" or ch == "\u200D" or "\U000E0000" <= ch <= "\U000E007F"


_glyphs: dict = {}


def _glyph_print(path: Path, text: str, size: int, colour: bool):
    """`text` drawn without shaping, as raw pixels, with its advance."""
    from PIL import Image, ImageDraw, ImageFont

    key = ("font", str(path), size)
    if key not in _glyphs:
        _glyphs[key] = ImageFont.truetype(str(path), size, layout_engine=ImageFont.Layout.BASIC)
    font = _glyphs[key]
    img = Image.new("RGBA" if colour else "L", (size * 4, size * 2))
    ink = {"embedded_color": True} if colour else {"fill": 255}
    ImageDraw.Draw(img).text((size, size // 2), text, font=font, **ink)
    return img.tobytes(), font.getlength(text)


def _has_glyph(path: Path, ch: str, size: int = 32, colour: bool = False) -> bool:
    """Whether the font file has a glyph of its own for `ch`. Pillow has no
    glyph fallback: a character the font lacks is drawn as its .notdef, an
    empty box (or nothing, in a bitmap emoji font). Laid out a character at
    a time, a missing character IS the .notdef, so the test is to draw both
    and compare, the .notdef drawn for the last private-use code point, which
    no font maps."""
    key = (str(path), ch, size)
    if key not in _glyphs:
        notdef = ("notdef", str(path), size)
        try:
            if notdef not in _glyphs:
                _glyphs[notdef] = _glyph_print(path, "\U0010FFFD", size, colour)
            _glyphs[key] = _glyph_print(path, ch, size, colour) != _glyphs[notdef]
        except Exception:
            _glyphs[key] = False  # the font cannot draw it at all
    return _glyphs[key]


class _Face:
    """A loaded text face at one size, with its squeeze."""

    def __init__(self, path: Path, variations: tuple | None, squeeze: float, size: int):
        from PIL import ImageFont

        self.font = ImageFont.truetype(str(path), size, layout_engine=_layout())
        if variations:
            # Bahnschrift is one variable font; its condensed bold is a named
            # instance. Raises when none is there, and the next face is tried.
            names = {n.decode() if isinstance(n, bytes) else n for n in self.font.get_variation_names()}
            name = next((v for v in variations if v in names), None)
            if name is None:
                raise ValueError("no condensed bold instance")
            self.font.set_variation_by_name(name)
        self.path, self.variations = path, variations
        self.squeeze = squeeze
        self.size = size
        top = self.font.getbbox("H", anchor="ls")[1]
        self.cap = max(1, -top)

    def at(self, size: int) -> "_Face":
        return _Face(self.path, self.variations, self.squeeze, size)


def _needed(text: str) -> set[str]:
    """What a face must have to draw `text`: every character outside its
    emoji, bar spaces and invisible format marks."""
    return {ch for run, is_emoji in _runs(text) if not is_emoji for ch in run
            if unicodedata.category(ch) not in ("Zs", "Zl", "Zp", "Cc", "Cf")}


def _face(language: str, size: int, text: str = "") -> tuple[_Face | None, str]:
    """The first face of the language's list that has every character of
    `text`, at `size`, and the text it draws. Failing that, the first with
    every letter, digit and mark, the symbols it lacks left out: a ★ dropped
    beats a box drawn. (None, text) when no face has the letters."""
    needed = _needed(text)
    lacking = None
    for name, variations, squeeze in _faces_for(language):
        path = _resolve_file(name)
        if path is None:
            continue
        missing = {ch for ch in needed if not _has_glyph(path, ch)}
        if lacking is not None and missing:
            continue
        if any(unicodedata.category(ch)[0] in "LMN" for ch in missing):
            continue
        try:
            face = _Face(path, variations, squeeze, size)
        except Exception:
            continue  # a missing instance or an unreadable file: next face
        if not missing:
            return face, text
        lacking = (face, missing)
    if lacking is None:
        return None, text
    face, missing = lacking
    kept = "".join(run if is_emoji else "".join(ch for ch in run if ch not in missing)
                   for run, is_emoji in _runs(text))
    return face, re.sub(r"\s+", " ", kept).strip()


class _Emoji:
    """The colour emoji font, measured once.

    Every emoji is drawn at one scale, the one that makes a smiley's ink the
    height asked for, in its own advance cell: a flat ➖ or a wide 👀 keeps
    its designed size and spacing next to a 😤, as on a phone."""

    def __init__(self, path: Path, size: int):
        from PIL import ImageFont

        self.path, self.native = path, size
        self.font = ImageFont.truetype(str(path), size, layout_engine=_layout())
        self.asc, self.desc = self.font.getmetrics()
        self._cells: dict = {}
        self._images: dict = {}
        ref = self.cell("\U0001F600")
        box = ref.getbbox() if ref is not None else None
        if box:
            self.ref_h, self.ref_mid = box[3] - box[1], (box[1] + box[3]) / 2
        else:
            self.ref_h, self.ref_mid = (self.asc + self.desc) * 0.85, (self.asc + self.desc) / 2

    def _drawable(self, cluster: str) -> str | None:
        """What to draw for an emoji, or None when the font lacks any of it."""
        if not _raqm():
            # Laid out a character at a time, nothing is combined: a keycap
            # would be a digit beside an empty frame, so it is drawn as text,
            # and the invisible marks would be drawn as boxes or blank cells.
            if "\u20E3" in cluster:
                return None
            cluster = "".join(ch for ch in cluster if not _invisible(ch))
        if cluster and all(_invisible(ch) or _has_glyph(self.path, ch, self.native, colour=True)
                           for ch in cluster):
            return cluster
        return None

    def cell(self, cluster: str):
        """The emoji at the font's own size, cropped to its advance and the
        font's line height, not to its ink. None when the font lacks it."""
        if cluster not in self._cells:
            from PIL import Image, ImageDraw

            text, cell = self._drawable(cluster), None
            if text is not None:
                adv = max(1, round(self.font.getlength(text)))
                canvas = Image.new("RGBA", (adv + 2 * self.native, self.asc + self.desc), (0, 0, 0, 0))
                try:
                    ImageDraw.Draw(canvas).text((self.native, self.asc), text, font=self.font,
                                                anchor="ls", embedded_color=True)
                    cell = canvas.crop((self.native, 0, self.native + adv, canvas.height))
                except Exception:
                    cell = None
            self._cells[cluster] = cell
        return self._cells[cluster]

    def scale(self, height: int) -> float:
        return height / self.ref_h

    def width(self, cluster: str, height: int) -> int | None:
        cell = self.cell(cluster)
        return None if cell is None else max(1, round(cell.width * self.scale(height)))

    def image(self, cluster: str, height: int):
        key = (cluster, height)
        if key not in self._images:
            cell, s = self.cell(cluster), self.scale(height)
            self._images[key] = cell.resize((max(1, round(cell.width * s)),
                                             max(1, round(cell.height * s))))
        return self._images[key]

    def top(self, height: int) -> float:
        """Where a cell's top sits above the smiley's centre, scaled."""
        return self.ref_mid * self.scale(height)

    def cell_height(self, height: int) -> int:
        return max(1, round((self.asc + self.desc) * self.scale(height)))


_emoji_cache: dict = {}


def _emoji_font() -> _Emoji | None:
    """The colour emoji font, or None. Bitmap emoji fonts (Noto) only load at
    their one strike size, so the size is kept and the glyph scaled afterwards."""
    if "font" in _emoji_cache:
        return _emoji_cache["font"]
    found = None
    for name in _EMOJI_FACES:
        path = _resolve_file(name)
        if path is None:
            continue
        for size in (128, 109, 160, 96, 64):
            try:
                found = _Emoji(path, size)
                break
            except OSError:
                continue
        if found:
            break
    _emoji_cache["font"] = found
    return found


# ---- drawing --------------------------------------------------------------


def _runs(text: str) -> list[tuple[str, bool]]:
    """The line split into (text, is_emoji) runs. A selector left in the
    text (a U+FE0F after a letter) asks for nothing and is dropped."""
    out: list[tuple[str, bool]] = []
    pos = 0
    for m in _EMOJI_RUN.finditer(text):
        if m.start() > pos:
            out.append((text[pos:m.start()], False))
        out.append((m.group(), True))
        pos = m.end()
    if pos < len(text):
        out.append((text[pos:], False))
    out = [(t if e else "".join(ch for ch in t if not "\uFE00" <= ch <= "\uFE0F"), e) for t, e in out]
    return [(t, e) for t, e in out if t]


def _clusters(text: str) -> list[str]:
    """The text in the pieces a line may never break inside: an emoji with
    what rides on it, or a character with its marks and joiners."""
    out, i, n = [], 0, len(text)
    while i < n:
        m = _EMOJI_CLUSTER.match(text, i)
        j = m.end() if m else i + 1
        while j < n:
            if text[j] == "\u200D":
                j += 2  # a joiner holds the next character to this one
            elif text[j] == "\u200C" or unicodedata.category(text[j]) in ("Mn", "Mc", "Me"):
                j += 1
            else:
                break
        out.append(text[i:j])
        i = j
    return out


def _has_rtl(text: str) -> bool:
    return any(unicodedata.bidirectional(ch) in ("R", "AL") for ch in text)


def _is_rtl(text: str, language: str) -> bool:
    """Whether a line reads right to left: it has right-to-left letters, and
    the content's language is written that way or the line starts in one."""
    if not _has_rtl(text):
        return False
    if (language or "").split("-")[0].lower() in _RTL_LANGUAGES:
        return True
    first = next((d for d in map(unicodedata.bidirectional, text) if d in ("L", "R", "AL")), "L")
    return first != "L"


def _text_image(face: _Face, text: str, color, rtl: bool = False):
    """One run of text the full height of the face, squeezed. Returns
    (image, advance): the image overhangs its advance a little, so a glyph
    that pokes past its own width is not cut off."""
    from PIL import Image, ImageDraw

    asc, desc = face.font.getmetrics()
    advance = face.font.getlength(text)
    img = Image.new("RGBA", (max(1, round(advance) + 8), asc + desc), (0, 0, 0, 0))
    direction = {"direction": "rtl" if rtl else "ltr"} if _raqm() else {}
    ImageDraw.Draw(img).text((0, asc), text, font=face.font, fill=color, anchor="ls", **direction)
    if abs(face.squeeze - 1.0) > 0.01:
        img = img.resize((max(1, round(img.width * face.squeeze)), img.height))
    return img, round(advance * face.squeeze)


def _emoji_height(face: _Face) -> int:
    """How tall a smiley's ink is drawn beside this face's capitals."""
    return round(face.cap * 1.3)


def _parts(face: _Face, text: str) -> list[tuple[str, str, int]]:
    """The line as parts in reading order, each with its width:
    ("text", run) drawn in the face, ("emoji", cluster) from the emoji font.
    An emoji the emoji font lacks is drawn as text when the face has it, and
    otherwise left out, never drawn as an empty box."""
    emoji, height = _emoji_font(), _emoji_height(face)
    parts = []
    for run, is_emoji in _runs(text):
        if not is_emoji:
            if not _raqm():
                # Drawn a character at a time, a format mark is a box.
                run = "".join(ch for ch in run if unicodedata.category(ch) != "Cf")
            if run:
                parts.append(("text", run, round(face.font.getlength(run) * face.squeeze)))
            continue
        for cluster in _EMOJI_CLUSTER.findall(run):
            w = emoji.width(cluster, height) if emoji else None
            if w is not None:
                parts.append(("emoji", cluster, w))
                continue
            plain = "".join(ch for ch in cluster if not _invisible(ch) and ch != "\u20E3"
                            and not "\U0001F3FB" <= ch <= "\U0001F3FF")
            if plain and all(_has_glyph(face.path, ch) for ch in plain):
                parts.append(("text", plain, round(face.font.getlength(plain) * face.squeeze)))
    return parts


def _line_width(face: _Face, text: str) -> int:
    return sum(w for _, _, w in _parts(face, text))


def _draw_line(face: _Face, text: str, color, rtl: bool = False):
    """The line as one RGBA strip: (image, cap_top_y, baseline_y).

    A right-to-left line is laid out from the right: its parts are placed
    in reverse and each run of text is shaped right to left, so an emoji at
    the end of the sentence lands at its left end, and the words either side
    of an emoji keep their reading order."""
    from PIL import Image

    parts = _parts(face, text)
    if rtl:
        parts.reverse()
    asc, desc = face.font.getmetrics()
    emoji, height = _emoji_font(), _emoji_height(face)
    top, bottom, cell_top = 0, asc + desc, 0
    if emoji and any(kind == "emoji" for kind, _, _ in parts):
        # Every emoji cell sits at one height: the smiley centred on the capitals.
        cell_top = round(asc - face.cap / 2 - emoji.top(height))
        top, bottom = min(0, cell_top), max(bottom, cell_top + emoji.cell_height(height))
    width = sum(w for _, _, w in parts)
    strip = Image.new("RGBA", (max(1, width + face.size), bottom - top), (0, 0, 0, 0))
    x = 0
    for kind, value, w in parts:
        if kind == "emoji":
            strip.alpha_composite(emoji.image(value, height), (x, cell_top - top))
        else:
            strip.alpha_composite(_text_image(face, value, color, rtl)[0], (x, -top))
        x += w
    return strip.crop((0, 0, max(1, x), bottom - top)), asc - face.cap - top, asc - top


def _fit(language: str, text: str, size: int, minimum: int, target_w: int,
         max_w: int) -> tuple[_Face | None, list[str]]:
    """The face and the line(s) for one tier: shrink until the line is no
    wider than target_w, then wrap what still passes max_w into as few lines
    as fit, each its own box. A single word too wide for the frame shrinks
    the type further rather than run off the edge; at the smallest size it
    breaks between characters, and what still does not fit ends on an
    ellipsis. No line is ever wider than max_w."""
    face, text = _face(language, size, text)
    if face is None or not text:
        return None, []
    while _line_width(face, text) > target_w and face.size > minimum:
        face = face.at(max(minimum, round(face.size * 0.94)))
    if _line_width(face, text) <= max_w:
        return face, [text]
    floor = max(8, minimum // 2)
    while True:
        smallest = face.size <= floor
        lines = _wrap(face, text, max_w, split_words=smallest)
        if smallest or (len(lines) <= _MAX_LINES and all(_line_width(face, ln) <= max_w for ln in lines)):
            break
        face = face.at(max(floor, round(face.size * 0.9)))
    if len(lines) > _MAX_LINES:
        lines = [*lines[:_MAX_LINES - 1], _clip(face, lines[_MAX_LINES - 1], max_w, cut=True)]
    lines = [_clip(face, ln, max_w) for ln in lines]
    return face, [ln for ln in lines if ln]


def _may_break(before: str, after: str) -> bool:
    """Whether a line may break between two clusters of one word: next to a
    Chinese or Japanese character, as those scripts break, but not before an
    emoji (it belongs to what it follows), and not where kinsoku forbids.
    Korean is left out: it puts spaces between words, and breaks there."""
    def cjk(cluster):
        c = cluster[0]
        return (unicodedata.east_asian_width(c) in ("W", "F") and not _EMOJI_CLUSTER.match(cluster)
                and not ("\uAC00" <= c <= "\uD7AF" or "\u1100" <= c <= "\u11FF" or "\u3130" <= c <= "\u318F"))

    if _EMOJI_CLUSTER.match(after) or not (cjk(before) or cjk(after)):
        return False
    if unicodedata.category(after[0]) in ("Pe", "Pf", "Po") or after[0] in _NO_LINE_START:
        return False
    return unicodedata.category(before[0]) not in ("Ps", "Pi")


def _chunks(face: _Face, text: str, max_w: int, split_words: bool) -> list[tuple[str, str]]:
    """The text as (glue, piece) pairs, cut wherever a line may break: at a
    space (glue " "), between CJK characters, and, with `split_words`,
    between the characters of a word too wide for a line (glue "")."""
    out: list[tuple[str, str]] = []
    for word in text.split(" "):
        if not word:
            continue
        clusters = _clusters(word)
        pieces = [clusters[0]]
        for before, after in zip(clusters, clusters[1:]):
            if _may_break(before, after):
                pieces.append(after)
            else:
                pieces[-1] += after
        if split_words:
            pieces = [c for p in pieces for c in (_clusters(p) if _line_width(face, p) > max_w else [p])]
        out += [(" " if out and k == 0 else "", p) for k, p in enumerate(pieces)]
    return out


def _wrap(face: _Face, text: str, max_w: int, split_words: bool = False) -> list[str]:
    """Greedy wrap at the places a line may break; two lines are balanced
    rather than one long and one stub, the way a person would break them."""
    chunks = _chunks(face, text, max_w, split_words)

    def join(part):
        return "".join((glue if k else "") + piece for k, (glue, piece) in enumerate(part))

    lines: list[str] = []
    for glue, piece in chunks:
        if lines and _line_width(face, f"{lines[-1]}{glue}{piece}") <= max_w:
            lines[-1] = f"{lines[-1]}{glue}{piece}"
        else:
            lines.append(piece)
    if len(lines) == 2:
        splits = [(join(chunks[:i]), join(chunks[i:])) for i in range(1, len(chunks))]
        fitting = [p for p in splits if max(_line_width(face, a) for a in p) <= max_w]
        if fitting:
            lines = list(min(fitting, key=lambda p: abs(_line_width(face, p[0]) - _line_width(face, p[1]))))
    return lines


def _clip(face: _Face, line: str, max_w: int, cut: bool = False) -> str:
    """The line as it fits in max_w: whole when it does, otherwise cut short
    on an ellipsis. `cut` ends it on one anyway: text after it was left out."""
    if not cut and _line_width(face, line) <= max_w:
        return line
    dots = "\u2026" if _has_glyph(face.path, "\u2026") else "..."
    clusters = _clusters(line)
    while clusters and _line_width(face, "".join(clusters).rstrip() + dots) > max_w:
        clusters.pop()
    return "".join(clusters).rstrip() + dots if clusters else ""


def render_card(headline: str, subline: str, size: tuple[int, int], out_path: Path,
                position: str = "lower", language: str = "en") -> Path | None:
    """Draw the title card as a transparent PNG the size of the frame.

    None when there is nothing to draw or no usable font, so the caller
    keeps the clip as it is."""
    from PIL import Image, ImageDraw

    head, sub = card_text(headline), card_text(subline)
    if not head and not sub:
        return None
    # Right-to-left text needs Pillow's text layout (raqm) to be joined and
    # put in order; without it Arabic comes out as separate letters, left
    # to right. No card is better than that one.
    if _has_rtl(head + sub) and not _raqm():
        return None
    w, h = size
    # Sizes are fractions of a portrait frame's width; on any other shape,
    # of the widest portrait frame that fits, so the card keeps its scale.
    base = min(w, round(h * 9 / 16))
    max_w = round(base * _MAX_TEXT_W)
    tiers = []  # (face, line, text colour, box colour, vertical padding, right to left)
    head_face = None
    if head:
        head_face, lines = _fit(language, head, round(base * _HEAD_SIZE), round(base * _HEAD_MIN),
                                round(base * _HEAD_TEXT_W), max_w)
        if head_face is None:
            return None
        rtl = _is_rtl(head, language)
        tiers += [(head_face, line, YELLOW, BLACK, _PAD_Y_HEAD, rtl) for line in lines]
    if sub:
        sub_size = round((head_face.size if head_face else base * _HEAD_SIZE) * _SUB_RATIO)
        sub_face, lines = _fit(language, sub, sub_size, round(sub_size * 0.75), max_w, max_w)
        if sub_face is not None:
            rtl = _is_rtl(sub, language)
            tiers += [(sub_face, line, BLACK, YELLOW, _PAD_Y_SUB, rtl) for line in lines]
    if not tiers:
        return None

    boxes = []
    for face, line, fg, bg, pad, rtl in tiers:
        strip, cap_top, baseline = _draw_line(face, line, fg, rtl)
        pad_x, pad_y = round(face.size * _PAD_X), round(face.size * pad)
        box_h = (baseline - cap_top) + 2 * pad_y
        boxes.append((strip, cap_top, pad_x, pad_y, box_h, round(face.size * _RADIUS), bg))
    total_h = sum(b[4] for b in boxes)
    y = round(h * _TOP_TOP) if position == "top" else round(h * _LOWER_BOTTOM) - total_h

    card = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(card)
    for strip, cap_top, pad_x, pad_y, box_h, radius, bg in boxes:
        box_w = strip.width + 2 * pad_x
        x = (w - box_w) // 2
        draw.rounded_rectangle((x, y, x + box_w, y + box_h), radius=radius, fill=bg)
        card.alpha_composite(strip, (x + pad_x, max(0, y + pad_y - cap_top)))
        y += box_h
    card.save(out_path)
    return out_path


def apply_card(video_path: Path, card_png: Path) -> None:
    """Lay the card over the whole clip, in place. One extra encode, like an
    image watermark (video_editor/watermark.py).

    Raises when the new clip could not take the old one's place
    (_into_place), so the caller says the card was skipped. The full-size
    copy is never left behind in the clip folder."""
    from core.binaries import ffmpeg
    from core.paths import discard
    from video.encoding import CPU_ARGS, using_hardware_encoder, video_encoder_args

    tmp = video_path.with_suffix(".card.mp4")
    cmd = [
        ffmpeg(), "-y",
        "-i", str(video_path.resolve()),
        "-i", str(card_png.resolve()),
        "-filter_complex", "[0:v][1:v]overlay=0:0:eof_action=repeat,format=yuv420p",
        "-map", "0:a?",
        *video_encoder_args(),
        "-c:a", "copy",
        "-movflags", "+faststart",
        str(tmp.resolve()),
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0 and using_hardware_encoder():
            enc = video_encoder_args()
            i = cmd.index(enc[0])
            result = subprocess.run(cmd[:i] + CPU_ARGS + cmd[i + len(enc):], capture_output=True, text=True)
        if result.returncode != 0:
            raise RuntimeError(f"title card overlay failed:\n{result.stderr[-1500:]}")
        if not _into_place(tmp, video_path):
            raise RuntimeError(f"{video_path.name} is held open elsewhere and could not be "
                               f"replaced or written, so it has no title card")
    finally:
        # discard(), not unlink(): it never raises, so it cannot replace the
        # error above (core/paths.py has the story).
        discard(tmp)


def _into_place(new: Path, clip: Path) -> bool:
    """Put `new` in `clip`'s place, against whatever is holding the clip.

    A rename first: it is atomic, and nothing holds a freshly rendered clip as
    a rule. When it is refused the likeliest holder is the app's own preview,
    since with the end card off the card goes on the finished clip, which the
    user may be watching while they re-render it. On Windows that handle
    forbids a rename but allows a write, and holds for as long as the preview
    is open, so write into the file at once rather than wait on the rename
    (video/outro.py measured both). Only when the write is refused too is it
    a scanner, which blocks both and clears in seconds: then wait on the
    rename as the end card does, which writes in place once more at the end.
    """
    try:
        new.replace(clip)
        return True
    except PermissionError:
        pass
    if _write_into(new, clip):
        return True
    from video.outro import _replace_with_retry

    return _replace_with_retry(new, clip)


def _write_into(new: Path, clip: Path) -> bool:
    """Copy `new`'s bytes into `clip`, keeping the file itself. Quiet on
    failure, which only means the next try in _into_place is the one that
    counts. `new` stays on disk, so a write cut short is put right by the
    tries that follow."""
    try:
        with open(new, "rb") as src, open(clip, "r+b") as dst:
            shutil.copyfileobj(src, dst)
            dst.truncate()
        return clip.stat().st_size == new.stat().st_size
    except OSError:
        return False
