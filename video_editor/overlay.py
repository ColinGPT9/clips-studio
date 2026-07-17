"""Hook text overlay: a big bold title in the top third for the first few
seconds — the classic Shorts retention hook ("She did WHAT at the gym").

Implemented as an extra ASS style + event merged into the clip's caption
file (or a standalone ASS file when captions are off), so it burns through
the exact same subtitles plumbing as captions: correct at final resolution,
system fonts via libass, no drawtext/fontconfig headaches on Windows.
"""

from pathlib import Path

def _hook_style(canvas: tuple[int, int], font: str = "Arial Black") -> str:
    # Calibrated for the 1920-tall Shorts canvas; scales to other frames.
    s = canvas[1] / 1920
    return (
        f"Style: Hook,{font},{round(96 * s)},&H00FFFFFF,&H00FFFFFF,&H00000000,"
        f"&H7F000000,-1,0,0,0,100,100,0,0,1,{max(3, round(9 * s))},3,8,60,60,{round(190 * s)},1"
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


def _hook_event(text: str, seconds: float) -> str:
    clean = text.replace("\\", "").replace("{", "").replace("}", "").strip()
    return f"Dialogue: 1,{_ass_time(0)},{_ass_time(seconds)},Hook,,0,0,0,,{clean}"


def ensure_hook(
    ass_path: Path | None,
    target: Path,
    hook: dict,
    canvas: tuple[int, int] = (1080, 1920),
    font: str = "Arial Black",
) -> Path:
    """Merge the hook title into the clip's ASS file. If a captions file
    exists, the Hook style + event are added to it; otherwise a minimal ASS
    with only the hook is written to `target`. Returns the file to burn.
    font: override for non-Latin content (see video.captions.SCRIPT_FONTS)."""
    text, seconds = hook["text"], float(hook["seconds"])
    style = _hook_style(canvas, font)
    if ass_path is not None and ass_path.exists():
        content = ass_path.read_text(encoding="utf-8")
        # Style goes right before the [Events] section; event goes at the end.
        content = content.replace("\n[Events]", f"\n{style}\n\n[Events]", 1)
        content = content.rstrip("\n") + "\n" + _hook_event(text, seconds) + "\n"
        ass_path.write_text(content, encoding="utf-8")
        return ass_path
    target.write_text(
        _MINIMAL_HEADER.format(w=canvas[0], h=canvas[1], style=style)
        + _hook_event(text, seconds)
        + "\n",
        encoding="utf-8",
    )
    return target
