"""The highlights post style (video/post_style.py): the whole frame on black,
a headline above it, captions below, and words written to match."""

import json

from core.models import ClipCandidate, Segment
from video import post_style
from video.captions import build_captions


def test_only_known_styles_count_and_the_default_is_unchanged():
    assert post_style.resolve(None) == "default"
    assert post_style.resolve({}) == "default"
    assert post_style.resolve({"post_style": "Highlights"}) == "highlights"
    assert post_style.resolve({"post_style": "something else"}) == "default"


def test_broadcast_footage_fills_the_width_in_the_middle():
    layout = post_style.highlights_layout(1920, 1080)
    assert (layout["w"], layout["h"]) == (1080, 608)
    assert layout["x"] == 0 and layout["y"] == 656
    assert layout["filter"].endswith(f"pad=1080:1920:0:{layout['y']}:black")


def test_a_vertical_source_is_fitted_with_room_for_the_headline():
    layout = post_style.highlights_layout(1080, 1920)
    assert layout["h"] == 1200 and layout["w"] == 674
    assert layout["y"] > 300
    # Unreadable size: treated as broadcast footage, never a crash.
    assert post_style.highlights_layout(0, 0)["h"] == 608


def test_the_headline_drops_emoji_and_hashtags_the_fonts_cannot_draw():
    assert post_style.headline_text("He did NOT just do that 😳🔥 #nba #hoops") == "He did NOT just do that"
    assert post_style.headline_text("") == ""


def test_captions_go_under_the_video_and_the_headline_above(tmp_path):
    layout = post_style.highlights_layout(1920, 1080)
    style = post_style.caption_style_for({"post_style": "highlights", "font_size": 60}, layout)
    seg = [Segment(start=0.0, end=3.0, text="what a shot from downtown")]
    ass = build_captions(seg, ClipCandidate(start=0, end=3, score=80), tmp_path / "c.ass", style=style)
    out = post_style.ensure_headline(ass, tmp_path / "c.ass", "No way he hit that 😳", layout, duration=3)
    text = out.read_text(encoding="utf-8")
    # Captions: top-aligned, starting just below the video.
    assert f",8,60,60,{layout['y'] + layout['h'] + 36},1" in text
    assert "Style: Headline,Arial,64" in text
    assert f"{{\\pos(540,{layout['y'] - 36})}}No way he hit that" in text
    assert "😳" not in text


def test_a_headline_without_captions_gets_its_own_file(tmp_path):
    layout = post_style.highlights_layout(1920, 1080)
    out = post_style.ensure_headline(None, tmp_path / "h.ass", "Unreal", layout, duration=5)
    assert out == tmp_path / "h.ass" and "Headline" in out.read_text(encoding="utf-8")
    # Nothing to say: no file at all.
    assert post_style.ensure_headline(None, tmp_path / "e.ass", "😳", layout, duration=5) is None


def test_a_highlights_clip_is_a_straight_encode_on_any_worker():
    from remote_render import protocol

    cfg = {"clips": {"caption_style": {"post_style": "highlights"}}}
    assert protocol.needs_framing(cfg, None) is False
    assert protocol.needs_framing({"clips": {}}, {"caption_style": {"post_style": "highlights"}}) is False
    assert protocol.needs_framing({"clips": {}}, None) is True


class _Echo:
    def __init__(self):
        self.prompts = []

    def generate(self, prompt, *, json_mode=False):
        self.prompts.append(prompt)
        return json.dumps({"items": [{"index": 0, "title": "This pass should be illegal 😳",
                                      "description": "Too smooth.", "hashtags": ["#basketball"]}]})


def test_the_highlights_style_writes_highlight_page_captions():
    from analysis.metadata import generate_metadata_batch

    llm = _Echo()
    cand = [ClipCandidate(start=0, end=5, score=80, hook="pass")]
    seg = [Segment(start=0, end=5, text="what a pass")]
    meta = generate_metadata_batch(cand, seg, "Game 7", llm, style="highlights")
    assert "highlight pages" in llm.prompts[0] and "never guess who someone is" in llm.prompts[0]
    assert meta[0].title == "This pass should be illegal 😳"
    generate_metadata_batch(cand, seg, "Game 7", llm)
    assert "highlight pages" not in llm.prompts[1]
