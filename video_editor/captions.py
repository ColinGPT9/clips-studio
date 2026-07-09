"""Caption line remapping across cuts.

Caption lines ({"start","end","text"}, clip-relative) are authored on the
ORIGINAL clip timeline; after sections are removed, every surviving line
shifts to its new position. Lines fully inside a removed section disappear;
lines straddling a cut are clamped to the part that survives.

Word-level mute caption removal is NOT done here: the UI edits the caption
line text directly (the existing caption_lines override), which also lets
the user see and adjust exactly what the captions will say.
"""

from video_editor.timeline import EditList


def remap_lines(lines: list[dict], edit: EditList) -> list[dict]:
    if edit.keep is None:
        return _scale_speed(lines, edit.speed)
    out = []
    for line in lines:
        try:
            start, end = float(line["start"]), float(line["end"])
        except (KeyError, TypeError, ValueError):
            continue
        # The line may span several keep-ranges after a mid-line cut; keep the
        # remapped portion from each and merge contiguous pieces back together.
        pieces = []
        for a, b in edit.keep:
            s, e = max(start, a), min(end, b)
            if e - s <= 0.05:
                continue
            new_s, new_e = edit.remap(s), edit.remap(e)
            if new_s is None or new_e is None:
                continue
            pieces.append((new_s, new_e))
        if not pieces:
            continue
        new_start = min(p[0] for p in pieces)
        new_end = max(p[1] for p in pieces)
        if new_end - new_start > 0.05:
            out.append({**line, "start": round(new_start, 2), "end": round(new_end, 2)})
    return _scale_speed(sorted(out, key=lambda l: l["start"]), edit.speed)


def _scale_speed(lines: list[dict], speed: float) -> list[dict]:
    """Playback speed compresses the timeline — captions compress with it."""
    if abs(speed - 1.0) < 0.01:
        return lines
    return [
        {**l, "start": round(float(l["start"]) / speed, 2), "end": round(float(l["end"]) / speed, 2)}
        for l in lines
    ]
