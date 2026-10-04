"""Post styles: the overall look of a finished Short, beyond its captions.

"default" is the look every clip has always had: the subject tracked into a
full 9:16 frame with captions on top of the picture.

"highlights" is the sports-page repost look (the style House of Highlights
made familiar): the WHOLE source frame sits in the middle of a black 9:16
canvas, a bold white headline sits in the black band above it, and spoken
captions, when on, go in the band below. Nothing is cropped away, which is
the point for sports: the play, the scoreboard and the crowd all stay in
shot. Only the style is borrowed: no logo, name or watermark of theirs is
drawn. The user's own handle or logo goes on through Branding (watermark)
as for any clip.

The style is chosen with the caption style ({"post_style": "highlights"}),
so it rides the same plumbing as every other caption setting: the Generate
bar, per-clip re-renders and the remote render workers all carry it.
"""

import re
from pathlib import Path

DEFAULT = "default"
HIGHLIGHTS = "highlights"
STYLES = (DEFAULT, HIGHLIGHTS)

# The canvas every Short renders at.
_W, _H = 1080, 1920
# The tallest the video may be in the highlights layout (vertical or square
# sources), so the headline above it always has room.
_MAX_VIDEO_H = 1200
# Gap between the video's edge and the headline above / captions below.
_GAP = 36

HEADLINE_FONT = "Arial"
HEADLINE_SIZE = 64


def resolve(caption_style: dict | None) -> str:
    """The post style a clip renders in; anything unknown is the default."""
    name = str((caption_style or {}).get("post_style") or DEFAULT).strip().lower()
    return name if name in STYLES else DEFAULT


def highlights_layout(src_w: int, src_h: int) -> dict:
    """Where the video sits on the black 9:16 canvas.

    Returns {"w", "h", "x", "y", "filter"}: the scaled video's size and
    top-left corner, and the FFmpeg filter that builds the frame. A wide
    source fills the width; a narrow one is fitted to _MAX_VIDEO_H tall."""
    if src_w <= 0 or src_h <= 0:
        src_w, src_h = 16, 9  # unreadable size: assume broadcast footage
    w = _W
    h = round(_W * src_h / src_w)
    if h > _MAX_VIDEO_H:
        h = _MAX_VIDEO_H
        w = round(_MAX_VIDEO_H * src_w / src_h)
    w, h = max(2, _even(w)), max(2, _even(h))
    x = _even((_W - w) // 2)
    y = _even((_H - h) // 2)
    vf = (f"scale={w}:{h}:flags=lanczos,setsar=1,"
          f"pad={_W}:{_H}:{x}:{y}:black")
    return {"w": w, "h": h, "x": x, "y": y, "filter": vf}


def caption_style_for(caption_style: dict | None, layout: dict) -> dict:
    """The caption style with captions moved into the band under the video.
    Everything else the user chose (font, colour, size, casing) is kept."""
    return {**(caption_style or {}), "position": "top",
            "margin_v": layout["y"] + layout["h"] + _GAP}


# Emoji and pictographs: the stock Windows fonts libass burns with have no
# colour emoji, so they would come out as empty boxes. They stay in the post
# caption, where the platform draws them.
_EMOJI = re.compile(
    "[\U0001F000-\U0001FAFF\U00002600-\U000027BF\U0000FE0F\U0000200D\U00002B00-\U00002BFF]"
)


def headline_text(title: str) -> str:
    """A clip title as the on-video headline: no emoji, no hashtags."""
    text = _EMOJI.sub("", title or "")
    text = re.sub(r"(^|\s)#\w+", " ", text)
    return re.sub(r"\s+", " ", text).strip(" -|·")


def _even(n: int) -> int:
    return max(0, int(n) // 2 * 2)


def _headline_style(font: str) -> str:
    # White bold, a thin dark outline so it holds up if a colour filter
    # lifts the black bars. Alignment 2 (bottom centre): the text grows
    # upwards from just above the video however many lines it wraps to.
    return (
        f"Style: Headline,{font},{HEADLINE_SIZE},&H00FFFFFF,&H00FFFFFF,&H00000000,"
        f"&H00000000,-1,0,0,0,100,100,0,0,1,2,0,2,70,70,0,1"
    )


_MINIMAL_HEADER = """[Script Info]
ScriptType: v4.00+
PlayResX: {w}
PlayResY: {h}
WrapStyle: 0

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
{style}

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""


def _ass_time(seconds: float) -> str:
    h = int(seconds // 3600)
    m = int(seconds % 3600 // 60)
    s = seconds % 60
    return f"{h}:{m:02d}:{s:05.2f}"


def ensure_headline(
    ass_path: Path | None,
    target: Path,
    headline: str,
    layout: dict,
    duration: float,
    font: str = HEADLINE_FONT,
) -> Path | None:
    """Merge the headline into the clip's ASS file for the whole clip, just
    above the video. Writes a headline-only file to `target` when there are
    no captions. None (and nothing written) when the headline is empty."""
    text = headline_text(headline).replace("\\", "").replace("{", "").replace("}", "")
    if not text:
        return ass_path
    style = _headline_style(font)
    pos = f"{{\\pos({_W // 2},{layout['y'] - _GAP})}}"
    event = (f"Dialogue: 2,{_ass_time(0)},{_ass_time(max(0.1, duration) + 1)},"
             f"Headline,,0,0,0,,{pos}{text}")
    if ass_path is not None and ass_path.exists():
        content = ass_path.read_text(encoding="utf-8")
        content = content.replace("\n[Events]", f"\n{style}\n\n[Events]", 1)
        content = content.rstrip("\n") + "\n" + event + "\n"
        ass_path.write_text(content, encoding="utf-8")
        return ass_path
    target.write_text(_MINIMAL_HEADER.format(w=_W, h=_H, style=style) + event + "\n",
                      encoding="utf-8")
    return target
