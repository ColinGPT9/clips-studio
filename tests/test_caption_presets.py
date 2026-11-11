"""Caption presets (config/caption_presets.json) and the caption style
validators that take the new choices (analysis/clip_edit.py for the AI edit
assistant, server/mcp.py for MCP clients).

- Classic is exactly today's default style; every preset is a complete,
  valid caption_style that both validators accept unchanged.
- The validators accept the bundled fonts, every animation and safe zone,
  and refuse (MCP, with the list) or drop (the AI edit) anything else, as
  they treat an unknown font.

Standard library only: these run in CI's light install.
"""

import json
from pathlib import Path

import pytest

from analysis.clip_edit import _clean_caption_style
from server import mcp
from video.captions import ANIMATIONS, DEFAULT_STYLE, FONTS, SAFE_ZONE_CHOICES, WINDOWS_FONTS
from video.fonts import families

ROOT = Path(__file__).resolve().parent.parent
PRESETS = json.loads((ROOT / "config" / "caption_presets.json").read_text(encoding="utf-8"))["presets"]


def test_classic_comes_first_and_is_todays_default_exactly():
    assert PRESETS[0]["id"] == "classic" and PRESETS[0]["name"] == "Classic"
    assert PRESETS[0]["style"] == DEFAULT_STYLE


def test_the_presets_cover_every_animation_and_have_unique_names():
    assert [p["name"] for p in PRESETS] == ["Classic", "Bold pop", "Clean fade", "Karaoke", "Bounce", "Slide up"]
    assert len({p["id"] for p in PRESETS}) == len(PRESETS)
    assert {p["style"]["animation"] for p in PRESETS} == set(ANIMATIONS)


@pytest.mark.parametrize("preset", PRESETS, ids=[p["id"] for p in PRESETS])
def test_every_preset_is_a_complete_valid_caption_style(preset):
    style = preset["style"]
    assert set(style) == set(DEFAULT_STYLE)
    assert style["font"] in FONTS
    assert style["animation"] in ANIMATIONS and style["safe_zone"] in SAFE_ZONE_CHOICES
    assert style["position"] in ("bottom", "middle", "top")
    assert 40 <= style["font_size"] <= 140 and 1 <= style["words_per_caption"] <= 6
    # The AI edit's validator keeps every field it knows as it is...
    clean = _clean_caption_style(style)
    assert clean == {k: style[k] for k in clean}
    assert {"font", "font_size", "color", "position", "words_per_caption", "uppercase", "animation",
            "safe_zone"} <= set(clean)
    # ...and MCP's accepts it whole.
    mcp_style = mcp._caption_style(style)
    assert {k: mcp_style[k] for k in ("font", "animation", "safe_zone", "position")} == {
        k: style[k] for k in ("font", "animation", "safe_zone", "position")}


# ---- the validators -----------------------------------------------------------------------------


def test_the_ai_edit_accepts_bundled_fonts_animations_and_safe_zones():
    assert _clean_caption_style({"font": "bebas neue"}) == {"font": "Bebas Neue"}
    for animation in ANIMATIONS:
        assert _clean_caption_style({"animation": animation.upper()}) == {"animation": animation}
    for zone in SAFE_ZONE_CHOICES:
        assert _clean_caption_style({"safe_zone": zone}) == {"safe_zone": zone}


def test_the_ai_edit_drops_what_the_renderer_does_not_know():
    assert _clean_caption_style({"font": "Papyrus", "animation": "wobble", "safe_zone": "snapchat"}) == {}
    assert _clean_caption_style({"animation": 3, "safe_zone": None}) == {}


def test_the_ai_edit_prompt_lists_every_font_and_the_new_choices():
    prompt = (ROOT / "config" / "prompts" / "edit_clip.txt").read_text(encoding="utf-8")
    font_line = next(line for line in prompt.splitlines() if line.strip().startswith('"font":'))
    assert [f for f in FONTS[:10] + families() if f'"{f}"' not in font_line] == []
    for value in (*ANIMATIONS, *SAFE_ZONE_CHOICES):
        assert f'"{value}"' in prompt


def test_mcp_lists_the_windows_and_bundled_fonts():
    assert mcp.CAPTION_FONTS == WINDOWS_FONTS[:10] + families()
    assert mcp._caption_style({"font": "poppins"})["font"] == "Poppins ExtraBold"
    assert tuple(mcp.CAPTION_ANIMATIONS) == ANIMATIONS
    assert tuple(mcp.CAPTION_SAFE_ZONES) == SAFE_ZONE_CHOICES


def test_mcp_accepts_animations_and_safe_zones():
    assert mcp._caption_style({"animation": "Karaoke", "safe_zone": "TikTok"}) == {
        "animation": "karaoke", "safe_zone": "tiktok"}
    assert mcp._caption_style({"safe_zone": "youtube"}) == {"safe_zone": "shorts"}
    assert mcp._caption_style({"safe_zone": "instagram"}) == {"safe_zone": "reels"}


def test_mcp_refuses_an_unknown_animation_or_safe_zone_with_the_choices():
    with pytest.raises(ValueError, match="Caption animation 'wobble' is not one of: none, pop, fade, bounce"):
        mcp._caption_style({"animation": "wobble"})
    with pytest.raises(ValueError, match="Caption safe zone 'snapchat' is not one of: off, tiktok, reels, shorts"):
        mcp._caption_style({"safe_zone": "snapchat"})
    with pytest.raises(ValueError, match="No font called 'Papyrus'"):
        mcp._caption_style({"font": "Papyrus"})


def test_mcp_tells_the_model_about_the_new_choices():
    schema = next(t for t in mcp.TOOLS if t["name"] == "queue_video")["inputSchema"]["properties"]
    style = schema["caption_style"]["properties"]
    assert style["animation"]["enum"] == list(ANIMATIONS)
    assert style["safe_zone"]["enum"] == list(SAFE_ZONE_CHOICES)
    assert style["font"]["enum"] == mcp.CAPTION_FONTS
