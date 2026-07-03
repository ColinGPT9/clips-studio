"""Color filter presets (LUT-style looks) applied at render time.

Each preset is an FFmpeg video-filter chain — real color processing on the
rendered file. The UI shows instant CSS approximations of the same looks for
preview; this module is the source of truth for what actually gets burned
into the clip.

Preset names are validated against this registry everywhere (API, AI edit
chat), so an unknown name can never inject filter syntax into FFmpeg.
"""

PRESETS: dict[str, str] = {
    "none": "",
    "vibrant": "eq=saturation=1.35:contrast=1.08",
    "warm": "colorbalance=rm=0.10:bm=-0.10,eq=saturation=1.10:brightness=0.01",
    "cool": "colorbalance=rm=-0.08:bm=0.10,eq=saturation=1.05",
    "cinematic": "colorbalance=rs=-0.05:bs=0.08:rh=0.06:bh=-0.06,eq=contrast=1.12:saturation=0.92",
    "vintage": "curves=preset=vintage,eq=saturation=0.85",
    "bw": "hue=s=0,eq=contrast=1.15",
    "fade": "colorlevels=rimin=0.04:gimin=0.04:bimin=0.04,eq=saturation=0.82:contrast=0.94",
}


def filter_chain(name: str | None) -> str:
    """The FFmpeg vf fragment for a preset ('' for none/unknown)."""
    if not name:
        return ""
    return PRESETS.get(name, "")


def is_valid(name: str) -> bool:
    return name in PRESETS


# Manual picture adjustments (independent of presets, applied after them).
# Ranges are clamped: FFmpeg's eq filter takes brightness -1..1 (additive,
# 0 = unchanged), saturation 0..3, contrast -1000..1000 (1 = unchanged) —
# we allow sane creative ranges only.
ADJUST_RANGES = {
    "brightness": (-0.5, 0.5, 0.0),
    "saturation": (0.0, 3.0, 1.0),
    "contrast": (0.5, 2.0, 1.0),
}


def adjust_chain(adjust: dict | None) -> str:
    """FFmpeg eq fragment for manual brightness/saturation/contrast, or ''
    when everything is at its neutral default. Values are clamped, and only
    numbers survive — nothing user-provided is interpolated as syntax."""
    if not adjust:
        return ""
    parts = []
    for key, (lo, hi, default) in ADJUST_RANGES.items():
        try:
            value = float(adjust.get(key, default))
        except (TypeError, ValueError):
            continue
        value = max(lo, min(hi, value))
        if abs(value - default) > 0.005:
            parts.append(f"{key}={value:.3f}")
    return "eq=" + ":".join(parts) if parts else ""


def combined_chain(preset: str | None, adjust: dict | None) -> str:
    """Preset look + manual adjustments as one vf fragment."""
    return ",".join(p for p in (filter_chain(preset), adjust_chain(adjust)) if p)
