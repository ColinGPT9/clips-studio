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
    # --- Trending looks (CapCut / TikTok-style LUTs) ---
    # Golden Hour: warm sunset glow — lifted orange highlights, faded blacks.
    "golden": (
        "colorbalance=rm=0.12:bm=-0.12:rh=0.08:bh=-0.10,"
        "colorlevels=romin=0.035:gomin=0.025:bomin=0.015,"
        "eq=saturation=1.10:brightness=0.015"
    ),
    # Teal & Orange: the Hollywood grade — cool teal shadows, warm skin.
    "tealorange": (
        "colorbalance=rs=-0.12:gs=-0.02:bs=0.12:rh=0.10:bh=-0.12,"
        "eq=contrast=1.10:saturation=1.06"
    ),
    # Clean Girl: bright, airy, low-contrast minimal look ("no filter" filter).
    "cleangirl": (
        "eq=brightness=0.05:contrast=0.95:saturation=0.92,"
        "colorbalance=rm=-0.02:bm=0.03,"
        "colorlevels=romin=0.02:gomin=0.02:bomin=0.02"
    ),
    # Pink Glow: soft Barbie-pink tint, dewy and bright.
    "pinkglow": (
        "colorbalance=rm=0.10:gm=-0.04:bm=0.06,"
        "eq=brightness=0.04:saturation=1.05:contrast=0.96"
    ),
    # Peachy: warm pink-red "strawberry girl" flush.
    "peachy": (
        "colorbalance=rm=0.12:gm=0.02:bm=-0.04:rh=0.06,"
        "eq=brightness=0.03:saturation=1.12"
    ),
    # Dreamy: pastel light-leak feel — lifted shadows, soft contrast.
    "dreamy": (
        "colorlevels=rimin=0.03:gimin=0.03:bimin=0.03"
        ":romin=0.05:gomin=0.04:bomin=0.05,"
        "eq=saturation=0.88:contrast=0.90:brightness=0.03,"
        "colorbalance=rm=0.04:bm=0.02"
    ),
    # Gold Coast: blue ocean tone — punchy cool blues.
    "coast": (
        "colorbalance=rm=-0.10:bm=0.14:rh=-0.04:bh=0.08,"
        "eq=saturation=1.10:contrast=1.05:brightness=0.01"
    ),
    # Soft Glow: dewy white-boost (Cybershot-style) — brightens without wash.
    "glow": (
        "colorlevels=rimax=0.94:gimax=0.94:bimax=0.94,"
        "eq=brightness=0.04:contrast=1.02:saturation=1.06"
    ),
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
