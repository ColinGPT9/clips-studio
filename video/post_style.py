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

import os
import re
import shutil
import subprocess
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
}

# Card geometry, as fractions of the frame WIDTH unless noted, measured off
# their posts at 1080 wide: a short headline is ~100px type, a long one
# shrinks to fit, and a line that still does not fit wraps into a second
# box of the same kind.
_MAX_TEXT_W = 0.86       # widest a line of text may be
_HEAD_SIZE = 0.093       # headline type size, before fitting
_HEAD_MIN = 0.056        # smallest it shrinks to before wrapping
_SUB_RATIO = 0.72        # second line's size against the headline's
_PAD_X = 0.36            # box padding, in ems of that line's size
_PAD_Y_HEAD = 0.44       # the headline box is roomier than the second line's
_PAD_Y_SUB = 0.34
_RADIUS = 0.18
# Where the card sits, as fractions of the frame HEIGHT: "lower" keeps its
# bottom edge above the platform's caption and buttons; "top" sits under the
# platform's own top bar.
_LOWER_BOTTOM = 0.80
_TOP_TOP = 0.13

# Emoji and pictographs, plus what joins and modifies them, so a sequence
# stays one run. Mirrors what the platforms draw in colour.
_EMOJI_CHARS = (
    "\U0001F000-\U0001FAFF\U00002600-\U000027BF\U00002B00-\U00002BFF"
    "\U0000FE0F\U0000200D\U000020E3\U0001F3FB-\U0001F3FF\U000E0020-\U000E007F"
    "\U00002190-\U000021FF\U00002300-\U000023FF\U00003030\U0000303D\U00003297\U00003299"
)
_EMOJI_RUN = re.compile(f"[{_EMOJI_CHARS}]+")
_HASHTAG = re.compile(r"(^|\s)#\w+")


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
_EMOJI_FACES = [
    "seguiemj.ttf",
    "/usr/share/fonts/truetype/noto/NotoColorEmoji.ttf",
    "/System/Library/Fonts/Apple Color Emoji.ttc",
]


def _faces_for(language: str) -> list[tuple[str, tuple | None, float]]:
    from video.captions import SCRIPT_FONTS

    script = SCRIPT_FONTS.get((language or "en").split("-")[0].lower())
    if script is None:
        return _LATIN_FACES
    key = {"Nirmala UI": "nirmala", "Yu Gothic UI": "ja", "Malgun Gothic": "ko",
           "Microsoft YaHei": "zh", "Leelawadee UI": "th"}.get(script, "segoe")
    return _SCRIPT_FACES[key]


def _resolve_file(name: str) -> Path | None:
    p = Path(name)
    if not p.is_absolute():
        p = _windows_fonts() / name
    return p if p.exists() else None


class _Face:
    """A loaded text face at one size, with its squeeze."""

    def __init__(self, path: Path, variations: tuple | None, squeeze: float, size: int):
        from PIL import ImageFont

        self.font = ImageFont.truetype(str(path), size)
        if variations:
            # Bahnschrift is one variable font; its condensed bold is a named
            # instance. Raises when none is there, and the next face is tried.
            names = {n.decode() if isinstance(n, bytes) else n for n in self.font.get_variation_names()}
            name = next((v for v in variations if v in names), None)
            if name is None:
                raise ValueError("no condensed bold instance")
            self.font.set_variation_by_name(name)
        self.squeeze = squeeze
        self.size = size
        top = self.font.getbbox("H", anchor="ls")[1]
        self.cap = max(1, -top)


def _face(language: str, size: int) -> _Face | None:
    for name, variations, squeeze in _faces_for(language):
        path = _resolve_file(name)
        if path is None:
            continue
        try:
            return _Face(path, variations, squeeze, size)
        except Exception:
            continue  # a missing instance or an unreadable file: next face
    return None


_emoji_cache: dict = {}


def _emoji_font():
    """(font, native size) for colour emoji, or None. Bitmap emoji fonts
    (Noto) only load at their one strike size, so the size is kept and the
    glyph scaled afterwards."""
    if "font" in _emoji_cache:
        return _emoji_cache["font"]
    from PIL import ImageFont

    found = None
    for name in _EMOJI_FACES:
        path = _resolve_file(name)
        if path is None:
            continue
        for size in (128, 109, 160, 96, 64):
            try:
                found = (ImageFont.truetype(str(path), size), size)
                break
            except OSError:
                continue
        if found:
            break
    _emoji_cache["font"] = found
    return found


# ---- drawing --------------------------------------------------------------


def _runs(text: str) -> list[tuple[str, bool]]:
    """The line split into (text, is_emoji) runs."""
    out: list[tuple[str, bool]] = []
    pos = 0
    for m in _EMOJI_RUN.finditer(text):
        if m.start() > pos:
            out.append((text[pos:m.start()], False))
        out.append((m.group(), True))
        pos = m.end()
    if pos < len(text):
        out.append((text[pos:], False))
    return [(t, e) for t, e in out if t.strip("\uFE0F\u200D")]


def _text_image(face: _Face, text: str, color):
    """One run of text the full height of the face, squeezed. Returns
    (image, advance): the image overhangs its advance a little, so a glyph
    that pokes past its own width is not cut off."""
    from PIL import Image, ImageDraw

    asc, desc = face.font.getmetrics()
    advance = face.font.getlength(text)
    img = Image.new("RGBA", (max(1, round(advance) + 8), asc + desc), (0, 0, 0, 0))
    ImageDraw.Draw(img).text((0, asc), text, font=face.font, fill=color, anchor="ls")
    if abs(face.squeeze - 1.0) > 0.01:
        img = img.resize((max(1, round(img.width * face.squeeze)), img.height))
    return img, round(advance * face.squeeze)


def _emoji_images(text: str, height: int) -> list:
    """Each emoji of a run as its own colour image `height` tall. Empty when
    no colour emoji font is installed, or the font has no such glyph."""
    from PIL import Image, ImageDraw

    found = _emoji_font()
    if not found:
        return []
    font, native = found
    # One cluster per emoji: a base character plus its joiners and modifiers.
    clusters = re.findall(
        r"(?:[\U0001F1E6-\U0001F1FF]{2}|[^\uFE0F\u200D\U0001F3FB-\U0001F3FF\U000E0020-\U000E007F\u20E3]"
        r"(?:[\uFE0F\U0001F3FB-\U0001F3FF\u20E3]|\u200D.|[\U000E0020-\U000E007F])*)",
        text,
    )
    out = []
    for cluster in clusters:
        canvas = Image.new("RGBA", (native * 3, native * 2), (0, 0, 0, 0))
        try:
            ImageDraw.Draw(canvas).text((native // 2, native // 2), cluster, font=font,
                                        embedded_color=True)
        except Exception:
            continue
        box = canvas.getbbox()
        if not box:
            continue  # the font has no glyph for it
        glyph = canvas.crop(box)
        w = max(1, round(glyph.width * height / glyph.height))
        out.append(glyph.resize((w, height)))
    return out


def _line_width(face: _Face, text: str) -> int:
    width = 0
    emoji_h = round(face.cap * 1.3)
    for run, is_emoji in _runs(text):
        if is_emoji:
            width += sum(im.width for im in _emoji_images(run, emoji_h)) + round(face.size * 0.04)
        else:
            width += round(face.font.getlength(run) * face.squeeze)
    return width


def _draw_line(face: _Face, text: str, color):
    """The line as one RGBA strip: (image, cap_top_y, baseline_y)."""
    from PIL import Image

    parts = []
    emoji_h = round(face.cap * 1.3)
    for run, is_emoji in _runs(text):
        if is_emoji:
            for im in _emoji_images(run, emoji_h):
                parts.append(("emoji", im))
        else:
            parts.append(("text", _text_image(face, run, color)))
    asc, desc = face.font.getmetrics()
    height = max(asc + desc, emoji_h + asc - face.cap)
    width = sum((p[1].width if p[0] == "emoji" else p[1][0].width) for p in parts) + face.size
    strip = Image.new("RGBA", (max(1, width), height), (0, 0, 0, 0))
    x = 0
    cap_mid = asc - face.cap / 2
    for kind, part in parts:
        if kind == "emoji":
            x += round(face.size * 0.02)
            strip.alpha_composite(part, (x, max(0, round(cap_mid - part.height / 2))))
            x += part.width
        else:
            img, advance = part
            strip.alpha_composite(img, (x, 0))
            x += advance
    return strip.crop((0, 0, max(1, x), height)), asc - face.cap, asc


def _fit(language: str, text: str, size: int, minimum: int, max_w: int) -> tuple[_Face | None, list[str]]:
    """The face and the line(s) for one tier: shrink to fit, then wrap into
    as few lines as fit, each its own box. A single word too wide for the
    frame shrinks the type further rather than run off the edge."""
    face = _face(language, size)
    if face is None:
        return None, []
    while _line_width(face, text) > max_w and face.size > minimum:
        face = _face(language, max(minimum, round(face.size * 0.94)))
    if _line_width(face, text) <= max_w:
        return face, [text]
    while True:
        lines = _wrap(face, text, max_w)
        if all(_line_width(face, line) <= max_w for line in lines) or face.size <= minimum // 2:
            return face, lines
        face = _face(language, max(minimum // 2, round(face.size * 0.9)))


def _wrap(face: _Face, text: str, max_w: int) -> list[str]:
    """Greedy word wrap; two lines are balanced rather than one long and
    one stub, the way a person would break them."""
    lines: list[str] = []
    for word in text.split(" "):
        if lines and _line_width(face, f"{lines[-1]} {word}") <= max_w:
            lines[-1] = f"{lines[-1]} {word}"
        else:
            lines.append(word)
    if len(lines) == 2:
        words = text.split(" ")
        splits = [(" ".join(words[:i]), " ".join(words[i:])) for i in range(1, len(words))]
        fitting = [p for p in splits if max(_line_width(face, a) for a in p) <= max_w]
        if fitting:
            lines = list(min(fitting, key=lambda p: abs(_line_width(face, p[0]) - _line_width(face, p[1]))))
    return lines


def render_card(headline: str, subline: str, size: tuple[int, int], out_path: Path,
                position: str = "lower", language: str = "en") -> Path | None:
    """Draw the title card as a transparent PNG the size of the frame.

    None when there is nothing to draw or no usable font, so the caller
    keeps the clip as it is."""
    from PIL import Image, ImageDraw

    head, sub = card_text(headline), card_text(subline)
    if not head and not sub:
        return None
    w, h = size
    # Sizes are fractions of a portrait frame's width; on any other shape,
    # of the widest portrait frame that fits, so the card keeps its scale.
    base = min(w, round(h * 9 / 16))
    max_w = round(base * _MAX_TEXT_W)
    tiers = []  # (face, line, text colour, box colour, vertical padding)
    head_face = None
    if head:
        head_face, lines = _fit(language, head, round(base * _HEAD_SIZE), round(base * _HEAD_MIN), max_w)
        if head_face is None:
            return None
        tiers += [(head_face, line, YELLOW, BLACK, _PAD_Y_HEAD) for line in lines]
    if sub:
        sub_size = round((head_face.size if head_face else base * _HEAD_SIZE) * _SUB_RATIO)
        sub_face, lines = _fit(language, sub, sub_size, round(sub_size * 0.75), max_w)
        if sub_face is not None:
            tiers += [(sub_face, line, BLACK, YELLOW, _PAD_Y_SUB) for line in lines]
    if not tiers:
        return None

    boxes = []
    for face, line, fg, bg, pad in tiers:
        strip, cap_top, base = _draw_line(face, line, fg)
        pad_x, pad_y = round(face.size * _PAD_X), round(face.size * pad)
        box_h = (base - cap_top) + 2 * pad_y
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
    image watermark (video_editor/watermark.py). Written INTO the file rather
    than replacing it: a clip open in the app's preview can be written but
    not replaced on Windows (see video/outro.py)."""
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
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0 and using_hardware_encoder():
        enc = video_encoder_args()
        i = cmd.index(enc[0])
        result = subprocess.run(cmd[:i] + CPU_ARGS + cmd[i + len(enc):], capture_output=True, text=True)
    if result.returncode != 0:
        discard(tmp)
        raise RuntimeError(f"title card overlay failed:\n{result.stderr[-1500:]}")
    shutil.copyfile(tmp, video_path)
    discard(tmp)
