"""The caption controls offer exactly what the renderer burns (#174, step 3).

ui/src/renderer/src/components/CaptionStyleControls.tsx lists the
animations, safe zones and Windows fonts by hand, and lib/captionLook.ts
places the preview's caption with video/captions.py's margins. The UI has no
React test harness, so this reads the TypeScript and compares it with the
Python: a choice the UI offers that the renderer does not know would burn
as "none" or "off" without a word, and a preview margin that differs would
show the caption somewhere it will not be.

Also: every user-visible string in the controls goes through t(), the
renderer's CSP gained only font-src (and the browser dev server's copy the
same entry), and the preset chips keep the person's second-speaker choice.
CI light: reads files and imports video.captions (standard library only).
"""

import importlib.util
import json
import re
from pathlib import Path

from video import captions

ROOT = Path(__file__).resolve().parent.parent
UI = ROOT / "ui" / "src" / "renderer" / "src"
CONTROLS = (UI / "components" / "CaptionStyleControls.tsx").read_text(encoding="utf-8")
LOOK = (UI / "lib" / "captionLook.ts").read_text(encoding="utf-8")
OVERLAY = (UI / "components" / "LiveTextOverlay.tsx").read_text(encoding="utf-8")


def _pairs(name: str) -> list[tuple[str, str]]:
    """The [value, label] pairs of a `const NAME: [...][] = [...]` list."""
    match = re.search(rf"const {name}: \[[^\]]+\]\[\] = \[(.*?)\n\]", CONTROLS, re.DOTALL)
    assert match, f"{name} not found in CaptionStyleControls.tsx"
    return re.findall(r"\['([^']+)', '([^']+)'\]", match.group(1))


def test_animation_choices_are_the_renderers():
    assert tuple(v for v, _ in _pairs("ANIMATIONS")) == captions.ANIMATIONS
    assert _pairs("ANIMATIONS")[0] == ("none", "None")


def test_safe_zone_choices_are_the_renderers():
    assert tuple(v for v, _ in _pairs("SAFE_ZONES")) == captions.SAFE_ZONE_CHOICES
    assert _pairs("SAFE_ZONES")[0] == ("off", "Off")


def test_windows_fonts_are_whitelisted():
    block = re.search(r"export const CAPTION_FONTS = \[(.*?)\]", CONTROLS, re.DOTALL).group(1)
    fonts = re.findall(r"'([^']+)'", block)
    assert fonts == captions.WINDOWS_FONTS[: len(fonts)]
    assert set(fonts) <= set(captions.FONTS)


def test_default_style_is_the_renderers_default():
    block = re.search(r"export const DEFAULT_CAPTION_STYLE[^=]*= \{(.*?)\n\}", CONTROLS, re.DOTALL).group(1)
    ui = {}
    for key, raw in re.findall(r"^\s*(\w+): (.+?),?$", block, re.MULTILINE):
        raw = raw.rstrip(",")
        ui[key] = json.loads(raw.replace("'", '"')) if raw not in ("true", "false") else raw == "true"
    assert ui == captions.DEFAULT_STYLE


def test_preview_margins_are_the_renderers():
    """lib/captionLook.ts captionLayout copies video/captions.py _layout."""
    clearance = int(re.search(r"const SAFE_CLEARANCE = (\d+)", LOOK).group(1))
    assert clearance == captions._SAFE_CLEARANCE
    assert int(re.search(r"const INK_SPREAD = (\d+)", LOOK).group(1)) == captions._INK_SPREAD
    assert int(re.search(r"const SLIDE_PX = (\d+)", LOOK).group(1)) == captions._SLIDE_PX
    assert "zone.bottom + SAFE_CLEARANCE + rise" in LOOK and "style.animation === 'slide' ? SLIDE_PX : 0" in LOOK
    assert "(Math.max(60, zone.right) + INK_SPREAD) * w" in LOOK
    margins = dict(re.findall(r"(\w+): (\d+)", re.search(r"const MARGIN_V = \{([^}]*)\}", LOOK).group(1)))
    assert {k: int(v) for k, v in margins.items()} == {k: v for k, (_, v) in captions._POSITIONS.items()}
    # The zones themselves come from lib/gamingLayouts.ts, the copy of
    # gaming/layouts.json that tests/test_ui_gaming_layout_sync.py checks;
    # video/captions.py SAFE_ZONES equals the same file (test_caption_animations).
    assert "LAYOUTS.safe_zones[zone]" in LOOK
    layouts = json.loads((ROOT / "gaming" / "layouts.json").read_text(encoding="utf-8"))["safe_zones"]
    for name, zone in captions.SAFE_ZONES.items():
        assert {k: layouts[name][k] for k in zone} == zone


def test_preview_weight_follows_the_burn():
    """A bundled family under 600 is drawn regular, as _header writes Bold 0."""
    assert re.search(r"return w < 600 \? 400 : w", LOOK)
    assert "captionFontWeight(st.font, bundled)" in OVERLAY


def test_a_preset_keeps_the_persons_second_speaker_choice():
    assert re.search(r"KEPT_ON_PICK = \['second_speaker', 'second_speaker_color'\]", CONTROLS)
    fn = re.search(r"function presetStyle\(.*?\n\}", CONTROLS, re.DOTALL).group(0)
    assert "second_speaker: current.second_speaker" in fn
    assert "second_speaker_color: current.second_speaker_color" in fn
    # Add Videos builds each change from the render's style, so a preset
    # must arrive whole there, not as one onChange per field.
    add = (UI / "components" / "queue" / "AddVideos.tsx").read_text(encoding="utf-8")
    assert "onPick={(style) => putSlotStyle(slot, style)}" in add


def test_karaoke_shows_the_highlight_as_included():
    assert "disabled={karaoke}" in CONTROLS
    assert "checked={style.highlight || karaoke}" in CONTROLS


def _detector():
    spec = importlib.util.spec_from_file_location("ui_i18n_editor", ROOT / "tests" / "test_ui_i18n_editor.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.untranslated


def test_every_string_in_the_controls_goes_through_t():
    assert _detector()(CONTROLS) == []


CSP_BEFORE = (
    "default-src 'self'; connect-src 'self' http://127.0.0.1:8765 ws://127.0.0.1:8765; "
    "media-src http://127.0.0.1:8765; img-src 'self' data: http://127.0.0.1:8765; "
    "style-src 'self' 'unsafe-inline'"
)


def test_csp_gains_only_font_src():
    html = (ROOT / "ui" / "src" / "renderer" / "index.html").read_text(encoding="utf-8")
    csp = re.search(r'http-equiv="Content-Security-Policy"\s+content="([^"]+)"', html).group(1)
    assert csp == CSP_BEFORE + "; font-src 'self' http://127.0.0.1:8765"
    dev = (ROOT / "ui" / "vite.config.mts").read_text(encoding="utf-8")
    assert "font-src 'self' http://127.0.0.1:8765" in dev
