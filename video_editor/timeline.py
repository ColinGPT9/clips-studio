"""The edit list: validation and time remapping.

Data model (render_opts["edit"], all seconds relative to the clip start,
in the clip's ORIGINAL timeline):

  {
    "keep":        [[0.0, 12.4], [15.2, 34.0]],   # absent/None = whole clip
    "mutes":       [[3.1, 3.4]],                  # audio silenced, video kept
    "muted_words": [{"start": 3.1, "end": 3.4, "word": "..."}],  # UI bookkeeping
    "volume":      1.0,          # 0..2, applied to the whole (final) clip
    "mute_all":    false,
    "fade_in":     0.0,          # seconds
    "fade_out":    0.0,
    "music":       {"path": "C:/.../song.mp3", "volume": 0.25, "duck": true}
                   # the person's own file, or one of the app's free tracks:
                   # {"library": "<id>", "volume": 0.25, "duck": true}
  }
"""

import copy
from dataclasses import dataclass, field

MIN_SEGMENT = 0.25  # keep-ranges shorter than this are dropped (unwatchable)


def _clean_ranges(raw, duration: float) -> list[tuple[float, float]]:
    """Sort, clamp to [0, duration], merge overlaps, drop empty/invalid."""
    ranges = []
    for r in raw or []:
        try:
            a, b = float(r[0]), float(r[1])
        except (TypeError, ValueError, IndexError):
            continue
        a, b = max(0.0, a), min(duration, b)
        if b - a > 0.01:
            ranges.append((a, b))
    ranges.sort()
    merged: list[tuple[float, float]] = []
    for a, b in ranges:
        if merged and a <= merged[-1][1] + 0.01:
            merged[-1] = (merged[-1][0], max(merged[-1][1], b))
        else:
            merged.append((a, b))
    return merged


@dataclass
class EditList:
    duration: float                      # the clip's original duration
    keep: list[tuple[float, float]] | None = None
    mutes: list[tuple[float, float]] = field(default_factory=list)
    volume: float = 1.0
    mute_all: bool = False
    fade_in: float = 0.0
    fade_out: float = 0.0
    speed: float = 1.0                   # whole-clip playback speed (0.5-3x)
    hook: dict | None = None             # {"text": str, "seconds": float} top title
    music: dict | None = None            # {"path": str, "volume": 0-1, "duck": bool}, or "library": id for "path"

    @classmethod
    def from_dict(cls, d: dict | None, duration: float) -> "EditList | None":
        """Parse + validate a stored edit; None when there is nothing to do."""
        if not isinstance(d, dict):
            return None
        keep = None
        if d.get("keep"):
            keep = [r for r in _clean_ranges(d["keep"], duration) if r[1] - r[0] >= MIN_SEGMENT]
            if not keep:
                return None  # everything removed: refuse rather than render nothing
            # Keeping the entire clip is not a cut.
            if len(keep) == 1 and keep[0][0] < 0.05 and keep[0][1] > duration - 0.05:
                keep = None
        try:
            volume = max(0.0, min(2.0, float(d.get("volume", 1.0))))
            fade_in = max(0.0, min(3.0, float(d.get("fade_in", 0.0))))
            fade_out = max(0.0, min(3.0, float(d.get("fade_out", 0.0))))
            speed = max(0.5, min(3.0, float(d.get("speed", 1.0))))
        except (TypeError, ValueError):
            volume, fade_in, fade_out, speed = 1.0, 0.0, 0.0, 1.0

        hook = None
        h = d.get("hook")
        if isinstance(h, dict) and str(h.get("text", "")).strip():
            hook = {
                "text": str(h["text"]).strip()[:120],
                "seconds": max(1.0, min(10.0, float(h.get("seconds", 3.0) or 3.0))),
            }

        music = None
        m = d.get("music")
        if isinstance(m, dict) and isinstance(m.get("path"), str) and m["path"].strip():
            music = {
                "path": str(m["path"]).strip(),
                "volume": max(0.0, min(1.0, float(m.get("volume", 0.25) or 0.25))),
                "duck": bool(m.get("duck", True)),
            }
        elif isinstance(m, dict) and isinstance(m.get("library"), str) and m["library"].strip():
            # One of the app's own free tracks, named by its id
            # (video/audio_library.py); the render finds the file.
            try:
                level = float(m.get("volume", 0.25) or 0.25)
            except (TypeError, ValueError):
                level = 0.25
            music = {
                "library": m["library"].strip(),
                "volume": max(0.0, min(1.0, level)),
                "duck": bool(m.get("duck", True)),
            }

        edit = cls(
            duration=duration,
            keep=keep,
            mutes=_clean_ranges(d.get("mutes"), duration),
            volume=volume,
            mute_all=bool(d.get("mute_all", False)),
            fade_in=fade_in,
            fade_out=fade_out,
            speed=speed,
            hook=hook,
            music=music,
        )
        return None if edit.is_noop() else edit

    def is_noop(self) -> bool:
        return (
            self.keep is None
            and not self.mutes
            and not self.mute_all
            and abs(self.volume - 1.0) < 0.01
            and self.fade_in <= 0
            and self.fade_out <= 0
            and abs(self.speed - 1.0) < 0.01
            and self.hook is None
            and self.music is None
        )

    def final_duration(self) -> float:
        base = self.duration if self.keep is None else sum(b - a for a, b in self.keep)
        return base / self.speed

    def remap(self, t: float) -> float | None:
        """Original-timeline time -> final-timeline time; None if removed."""
        if self.keep is None:
            return t
        offset = 0.0
        for a, b in self.keep:
            if a <= t <= b:
                return offset + (t - a)
            if t < a:
                return None
            offset += b - a
        return None


def _moved(raw, by: float) -> tuple[float, float] | None:
    """One saved [start, end] pair moved by `by` seconds, or None when it
    isn't a pair of numbers."""
    try:
        return float(raw[0]) + by, float(raw[1]) + by
    except (TypeError, ValueError, IndexError, KeyError):
        return None


def _within(a: float, b: float, duration: float) -> list[float] | None:
    """[a, b] cut to the clip's [0, duration], or None when less than a
    hundredth of it is left (what _clean_ranges drops)."""
    a, b = max(0.0, a), min(duration, b)
    return [round(a, 3), round(b, 3)] if b - a > 0.01 else None


def shift_edit(edit, old_start: float, old_end: float, new_start: float, new_end: float) -> dict | None:
    """A saved edit (render_opts["edit"]) carried to the clip after its start
    or end moved, so every cut and mute stays on the same moment of the video.

    The edit's times count from the clip's start, so a clip that now starts
    `old_start - new_start` seconds earlier moves each of them that much
    later: new = old + (old_start - new_start).

    - keep: what the clip gains plays, since whoever widened it wants to see
      what they widened it to. A start moved earlier adds the new head
      [0, old_start - new_start]; an end moved later adds the stretch from the
      old end to the new one. What the clip loses is cut away. Ranges that
      touch are merged, and pieces shorter than the render plays
      (MIN_SEGMENT) go. With no piece left, keep goes and the whole new clip
      plays (the caller says so in the job log). No keep is the whole clip,
      before and after.
    - mutes: moved, cut to the new clip, and dropped when nothing is left.
    - muted_words (which words the editor shows muted): moved, and dropped
      when the word is no longer in the clip. A word half in it keeps its own
      times, so the editor still finds it.
    - Everything else (volume, mute_all, fades, speed, hook, music, and keys
      this doesn't know) is left as it is, and so is a keep, mutes or
      muted_words that isn't a list: the render reads those as it always did.

    The input is never changed. The same window gives back an equal copy;
    anything but a dict gives None."""
    if not isinstance(edit, dict):
        return None
    out = copy.deepcopy(edit)
    old_start, old_end = float(old_start), float(old_end)
    new_start, new_end = float(new_start), float(new_end)
    if abs(old_start - new_start) < 1e-9 and abs(old_end - new_end) < 1e-9:
        return out
    by = old_start - new_start
    duration = new_end - new_start

    if edit.get("keep") and isinstance(edit["keep"], (list, tuple)):
        ranges = [r for r in (_moved(raw, by) for raw in edit["keep"]) if r is not None]
        if new_start < old_start:
            ranges.append((0.0, by))                                   # the head it gained
        if new_end > old_end:
            ranges.append((old_end - new_start, duration))             # the tail it gained
        keep = [[round(a, 3), round(b, 3)] for a, b in _clean_ranges(ranges, duration)
                if b - a >= MIN_SEGMENT]
        if keep:
            out["keep"] = keep
        else:
            del out["keep"]                                            # the whole new clip plays

    if "mutes" in edit and isinstance(edit["mutes"], (list, tuple)):
        mutes = []
        for raw in edit["mutes"]:
            moved = _moved(raw, by)
            inside = _within(*moved, duration) if moved is not None else None
            if inside is not None:
                mutes.append(inside)
        out["mutes"] = mutes

    if "muted_words" in edit and isinstance(edit["muted_words"], (list, tuple)):
        words = []
        for word in edit["muted_words"]:
            if not isinstance(word, dict):
                continue
            moved = _moved((word.get("start"), word.get("end")), by)
            if moved is None or _within(*moved, duration) is None:
                continue
            words.append({**word, "start": round(moved[0], 3), "end": round(moved[1], 3)})
        out["muted_words"] = words
    return out


def made_seconds(edit, window: float) -> float:
    """How long a clip `window` seconds long plays once rendered with this
    saved edit (render_opts["edit"]), before any end card: what the edit
    keeps of it at its speed, or the window itself when the render applies
    none of it (EditList.from_dict)."""
    parsed = EditList.from_dict(edit, duration=window)
    return parsed.final_duration() if parsed is not None else window
