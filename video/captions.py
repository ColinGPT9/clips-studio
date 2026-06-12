"""Word-synced caption generation.

Builds an ASS subtitle file for one clip — short uppercase word groups,
bold white with a heavy black outline, positioned in the lower third of
the 9:16 frame — which FFmpeg burns in during the clip's final encode.

Falls back to spreading a segment's words evenly across its duration when
word-level timestamps are missing (transcripts cached before they were
enabled).
"""

from pathlib import Path

from core.models import ClipCandidate, Segment

ASS_HEADER = """[Script Info]
ScriptType: v4.00+
PlayResX: 1080
PlayResY: 1920
WrapStyle: 0

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,Arial,84,&H00FFFFFF,&H00FFFFFF,&H00000000,&H7F000000,-1,0,0,0,100,100,0,0,1,7,2,2,60,60,440,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""


def build_captions(
    segments: list[Segment],
    candidate: ClipCandidate,
    output_path: Path,
    words_per_caption: int = 3,
) -> Path | None:
    """Write an ASS file with times relative to the clip start.
    Returns the path, or None if the clip window contains no speech."""
    words = _words_in_window(segments, candidate.start, candidate.end)
    if not words:
        return None

    lines = []
    for group in _grouped(words, words_per_caption):
        start = max(0.0, group[0]["start"] - candidate.start)
        end = min(candidate.duration, group[-1]["end"] - candidate.start)
        if end <= start:
            continue
        text = " ".join(w["word"] for w in group).upper()
        text = text.replace("\\", "").replace("{", "").replace("}", "")  # ASS control chars
        lines.append(
            f"Dialogue: 0,{_ass_time(start)},{_ass_time(end)},Default,,0,0,0,,{text}"
        )

    if not lines:
        return None
    output_path.write_text(ASS_HEADER + "\n".join(lines) + "\n", encoding="utf-8")
    return output_path


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
