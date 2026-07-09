"""Word-synced caption generation.

Builds an ASS subtitle file for one clip — short word groups, bold with a
heavy outline — which FFmpeg burns in during the clip's final encode.

Caption styling is parameterized (size, colour, position, words per group,
casing) so per-clip render options — including the AI edit assistant — can
restyle captions without touching code.

Falls back to spreading a segment's words evenly across its duration when
word-level timestamps are missing (transcripts cached before they were
enabled).
"""

from pathlib import Path

from core.models import ClipCandidate, Segment

DEFAULT_STYLE = {
    "font": "Arial",          # must be in FONTS (installed on stock Windows)
    "font_size": 84,          # at 1080x1920 playback resolution
    "color": "#FFFFFF",       # text colour (hex RGB)
    "position": "bottom",     # bottom | middle | top
    "words_per_caption": 3,
    "uppercase": True,
}

# Fonts shipped with every stock Windows install, so a burned clip renders
# identically on any user's machine. Whitelisted: the name goes into the ASS
# header, and unknown names would silently fall back to a default anyway.
FONTS = [
    "Arial",
    "Arial Black",
    "Impact",
    "Verdana",
    "Tahoma",
    "Trebuchet MS",
    "Segoe UI",
    "Georgia",
    "Comic Sans MS",
    "Courier New",
]

# position -> (ASS numpad alignment, vertical margin)
_POSITIONS = {"bottom": (2, 440), "middle": (5, 0), "top": (8, 140)}


def build_caption_lines(
    segments: list[Segment],
    candidate: ClipCandidate,
    words_per_caption: int = 3,
) -> list[dict]:
    """The caption lines for one clip as editable data:
    [{"start", "end", "text"}] with times relative to the clip start.
    This is what the caption editor in the UI shows and what users correct."""
    words = _words_in_window(segments, candidate.start, candidate.end)
    lines = []
    for group in _grouped(words, max(1, int(words_per_caption))):
        start = max(0.0, group[0]["start"] - candidate.start)
        end = min(candidate.duration, group[-1]["end"] - candidate.start)
        if end <= start:
            continue
        lines.append(
            {"start": round(start, 2), "end": round(end, 2), "text": " ".join(w["word"] for w in group)}
        )
    return lines


def build_captions(
    segments: list[Segment],
    candidate: ClipCandidate,
    output_path: Path,
    style: dict | None = None,
    lines: list[dict] | None = None,
    canvas: tuple[int, int] = (1080, 1920),
) -> Path | None:
    """Write an ASS file with times relative to the clip start.
    `lines` (user-corrected caption text) overrides the generated ones.
    `canvas` is the output frame (default portrait Shorts; longform passes
    1920x1080 and the style scales to it). Returns the path, or None if
    there is nothing to caption."""
    opts = {**DEFAULT_STYLE, **(style or {})}
    if lines is None:
        lines = build_caption_lines(segments, candidate, opts["words_per_caption"])

    dialogue = []
    for line in lines:
        text = str(line.get("text", "")).strip()
        if not text:
            continue  # a blanked-out line deletes that caption
        if opts["uppercase"]:
            text = text.upper()
        text = text.replace("\\", "").replace("{", "").replace("}", "")  # ASS control chars
        start, end = float(line["start"]), float(line["end"])
        if end <= start:
            continue
        dialogue.append(
            f"Dialogue: 0,{_ass_time(start)},{_ass_time(end)},Default,,0,0,0,,{text}"
        )

    if not dialogue:
        return None
    output_path.write_text(_header(opts, canvas) + "\n".join(dialogue) + "\n", encoding="utf-8")
    return output_path


def _header(opts: dict, canvas: tuple[int, int] = (1080, 1920)) -> str:
    alignment, margin_v = _POSITIONS.get(opts["position"], _POSITIONS["bottom"])
    color = _ass_color(opts["color"])
    size = max(40, min(140, int(opts["font_size"])))
    font = opts.get("font") if opts.get("font") in FONTS else "Arial"
    # Style values are calibrated for the 1920-tall Shorts canvas; scale
    # them to whatever frame this clip renders at (e.g. landscape 1080).
    scale = canvas[1] / 1920
    size = max(24, round(size * scale))
    margin_v = round(margin_v * scale)
    return f"""[Script Info]
ScriptType: v4.00+
PlayResX: {canvas[0]}
PlayResY: {canvas[1]}
WrapStyle: 0

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,{font},{size},{color},{color},&H00000000,&H7F000000,-1,0,0,0,100,100,0,0,1,7,2,{alignment},60,60,{margin_v},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""


def _ass_color(hex_rgb: str) -> str:
    """#RRGGBB -> ASS &H00BBGGRR (ASS stores colours little-endian)."""
    h = hex_rgb.lstrip("#")
    if len(h) != 6:
        return "&H00FFFFFF"
    r, g, b = h[0:2], h[2:4], h[4:6]
    return f"&H00{b}{g}{r}".upper()


def _words_in_window(segments: list[Segment], start: float, end: float) -> list[dict]:
    words: list[dict] = []
    for seg in segments:
        if seg.end <= start or seg.start >= end:
            continue
        if seg.words:
            words.extend(w for w in seg.words if w["end"] > start and w["start"] < end)
        else:
            words.extend(_spread_evenly(seg, start, end))
    return sorted(words, key=lambda w: w["start"])


def _spread_evenly(seg: Segment, start: float, end: float) -> list[dict]:
    tokens = seg.text.split()
    if not tokens:
        return []
    step = (seg.end - seg.start) / len(tokens)
    return [
        {"start": seg.start + i * step, "end": seg.start + (i + 1) * step, "word": tok}
        for i, tok in enumerate(tokens)
        if seg.start + (i + 1) * step > start and seg.start + i * step < end
    ]


def _grouped(words: list[dict], size: int) -> list[list[dict]]:
    return [words[i : i + size] for i in range(0, len(words), size)]


def _ass_time(seconds: float) -> str:
    h = int(seconds // 3600)
    m = int(seconds % 3600 // 60)
    s = seconds % 60
    return f"{h}:{m:02d}:{s:05.2f}"
