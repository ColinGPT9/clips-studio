"""Audio filter chains for the editor.

Section mutes run BEFORE cutting (original-timeline coordinates); volume,
mute-all and fades run AFTER cutting (they describe the final clip).
"""

from video_editor.timeline import EditList


def mute_filter(mutes: list[tuple[float, float]]) -> str:
    """volume=0 over the muted sections, e.g. word mutes. Original timeline."""
    if not mutes:
        return ""
    expr = "+".join(f"between(t,{a:.3f},{b:.3f})" for a, b in mutes)
    return f"volume=0:enable='{expr}'"


def master_filter(edit: EditList) -> str:
    """Whole-clip audio treatment on the FINAL timeline: volume/mute + fades."""
    parts = []
    if edit.mute_all:
        parts.append("volume=0")
    elif abs(edit.volume - 1.0) >= 0.01:
        parts.append(f"volume={edit.volume:.2f}")
    if edit.fade_in > 0:
        parts.append(f"afade=t=in:st=0:d={edit.fade_in:.2f}")
    if edit.fade_out > 0:
        final = edit.final_duration()
        start = max(0.0, final - edit.fade_out)
        parts.append(f"afade=t=out:st={start:.3f}:d={edit.fade_out:.2f}")
    parts.append("aresample=async=1")  # keep A/V locked through the edit
    return ",".join(parts)
