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
    "fade_out":    0.0
  }
"""

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
    music: dict | None = None            # {"path": str, "volume": 0-1, "duck": bool}

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
        if isinstance(m, dict) and str(m.get("path", "")).strip():
            music = {
                "path": str(m["path"]).strip(),
                "volume": max(0.0, min(1.0, float(m.get("volume", 0.25) or 0.25))),
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
