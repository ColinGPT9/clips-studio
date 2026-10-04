"""The highlights post style (video/post_style.py): the clip framed as any
other, with the highlight pages' title card and captions on top, and words
written to match."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from core.models import ClipCandidate, Segment
from video import post_style


def test_only_known_styles_count_and_the_default_is_unchanged():
    assert post_style.resolve(None) == "default"
    assert post_style.resolve({}) == "default"
    assert post_style.resolve({"post_style": "Highlights"}) == "highlights"
    assert post_style.resolve({"post_style": "something else"}) == "default"
    assert post_style.card_position({"card_position": "TOP"}) == "top"
    assert post_style.card_position({"card_position": "sideways"}) == "lower"


def test_the_captions_take_the_styles_look_and_keep_the_users_size():
    style = post_style.caption_style_for({"post_style": "highlights", "font": "Georgia",
                                          "color": "#FFFFFF", "position": "bottom",
                                          "font_size": 70, "words_per_caption": 2})
    assert style["font"] == "Impact" and style["color"] == "#F5FA00"
    assert style["uppercase"] is True and style["position"] == "middle"
    assert style["font_size"] == 70 and style["words_per_caption"] == 2


def test_card_lines_are_caps_without_hashtags_and_keep_their_emoji():
    assert post_style.card_text("He did NOT just do that😳 #nba #hoops") == "HE DID NOT JUST DO THAT😳"
    assert post_style.headline_from_title("Bro was NOT happy😭 #shorts") == "BRO WAS NOT HAPPY😭"
    assert post_style.card_text("") == ""


def test_text_and_emoji_are_drawn_as_separate_runs():
    assert post_style._runs("MAXEY STEPBACK!😤") == [("MAXEY STEPBACK!", False), ("😤", True)]
    assert post_style._runs("🔥WOW🔥😳") == [("🔥", True), ("WOW", False), ("🔥😳", True)]


def _boxes(png):
    """The card's boxes, top to bottom: [top, bottom, colour], read off the
    box's own left padding, where no text is drawn."""
    from PIL import Image

    im = Image.open(png).convert("RGBA")
    w, h = im.size
    rows = []
    for y in range(h):
        x0 = next((x for x in range(w) if im.getpixel((x, y))[3] == 255), None)
        if x0 is None or x0 + 6 >= w:
            continue
        kind = "black" if sum(im.getpixel((x0 + 6, y))[:3]) < 60 else "yellow"
        if rows and rows[-1][1] == y - 1 and rows[-1][2] == kind:
            rows[-1][1] = y
        else:
            rows.append([y, y, kind])
    return [r for r in rows if r[1] - r[0] > 10]


def test_the_card_stacks_a_black_headline_over_a_yellow_line(tmp_path):
    pytest.importorskip("PIL", reason="the card is drawn with Pillow, which CI does not install")
    png = post_style.render_card("Maxey stepback!😤", "these 2 are going to be a problem",
                                 (1080, 1920), tmp_path / "c.png")
    assert png is not None
    boxes = _boxes(png)
    assert [b[2] for b in boxes] == ["black", "yellow"]
    # Lower third: its bottom edge sits at 80% of the frame, clear of the
    # platform's own caption and buttons.
    assert abs(boxes[-1][1] - round(1920 * 0.80)) <= 2
    assert boxes[0][1] - boxes[0][0] > boxes[1][1] - boxes[1][0]  # the headline is the bigger box


def test_the_card_can_sit_at_the_top_and_go_without_its_second_line(tmp_path):
    pytest.importorskip("PIL", reason="the card is drawn with Pillow, which CI does not install")
    png = post_style.render_card("Stop the timer prank💀", "", (1080, 1920), tmp_path / "c.png",
                                 position="top")
    boxes = _boxes(png)
    assert [b[2] for b in boxes] == ["black"]
    assert abs(boxes[0][0] - round(1920 * 0.13)) <= 2


def test_a_long_headline_shrinks_then_wraps_inside_the_frame(tmp_path):
    pytest.importorskip("PIL", reason="the card is drawn with Pillow, which CI does not install")
    from PIL import Image

    png = post_style.render_card(
        "He pulled up from the logo with two seconds left in the Finals and the whole arena went silent",
        "", (1080, 1920), tmp_path / "c.png")
    im = Image.open(png)
    box = im.getbbox()
    assert box[0] > 0 and box[2] < 1080  # never past the edges
    assert len(_boxes(png)) == 1 and _boxes(png)[0][1] - _boxes(png)[0][0] > 150  # two lines, one box


def test_nothing_to_say_draws_nothing(tmp_path):
    pytest.importorskip("PIL", reason="the card is drawn with Pillow, which CI does not install")
    assert post_style.render_card("", "  #nba ", (1080, 1920), tmp_path / "c.png") is None
    assert not (tmp_path / "c.png").exists()


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="needs ffmpeg")
def test_the_card_is_laid_over_the_whole_clip(tmp_path):
    pytest.importorskip("PIL", reason="the card is drawn with Pillow, which CI does not install")
    clip = tmp_path / "clip.mp4"
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi", "-i",
                    "color=c=blue:s=1080x1920:d=1", "-f", "lavfi", "-i", "anullsrc", "-t", "1",
                    "-c:v", "libx264", "-c:a", "aac", "-shortest", str(clip)], check=True)
    png = post_style.render_card("Unreal", "", (1080, 1920), tmp_path / "c.png")
    post_style.apply_card(clip, png)
    frame = tmp_path / "f.png"
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-sseof", "-0.2", "-i", str(clip),
                    "-frames:v", "1", str(frame)], check=True)
    from PIL import Image

    im = Image.open(frame).convert("RGB")
    box = _boxes(png)[0]
    assert im.getpixel((20, (box[0] + box[1]) // 2))[2] > 150  # outside the card: still the clip
    assert im.getpixel((540, 200))[2] > 150
    left = next(x for x in range(1080) if sum(im.getpixel((x, box[0] + 6))) < 60)
    assert 0 < left < 540  # the black box is there, centred
    assert not (tmp_path / "clip.card.mp4").exists()


class _Echo:
    def __init__(self, item):
        self.prompts = []
        self.item = item

    def generate(self, prompt, *, json_mode=False):
        self.prompts.append(prompt)
        return json.dumps({"items": [self.item]})


def test_the_highlights_style_writes_titles_and_both_card_lines():
    from analysis.metadata import generate_metadata_batch

    llm = _Echo({"index": 0, "title": "This pass should be ILLEGAL😳", "description": "Too smooth.",
                 "hashtags": ["#basketball"], "headline": "NO LOOK DIME👀",
                 "subline": "\"THE BENCH KNEW IT WAS GOOD\" #nba"})
    cand = [ClipCandidate(start=0, end=5, score=80, hook="pass")]
    seg = [Segment(start=0, end=5, text="what a pass")]
    meta = generate_metadata_batch(cand, seg, "Game 7", llm, style="highlights")
    assert "Never guess who someone is" in llm.prompts[0]
    assert meta[0].title == "This pass should be ILLEGAL😳"
    assert meta[0].headline == "NO LOOK DIME👀"
    assert meta[0].subline == "THE BENCH KNEW IT WAS GOOD"

    plain = generate_metadata_batch(cand, seg, "Game 7", llm)
    assert "title card" not in llm.prompts[1]
    assert plain[0].headline == "" and plain[0].subline == ""


def _fake_overlay(monkeypatch):
    """apply_card with its encode faked: it writes a new clip where FFmpeg
    would. Saves needing FFmpeg to test what happens around it."""
    import core.binaries
    import video.encoding

    monkeypatch.setattr(core.binaries, "ffmpeg", lambda: "ffmpeg")
    monkeypatch.setattr(video.encoding, "video_encoder_args", lambda config=None: list(video.encoding.CPU_ARGS))
    monkeypatch.setattr(video.encoding, "using_hardware_encoder", lambda: False)

    def run(cmd, **kwargs):
        Path(cmd[-1]).write_bytes(b"with the card")
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(post_style.subprocess, "run", run)


def test_the_card_takes_the_clips_place_and_leaves_nothing_behind(tmp_path, monkeypatch):
    pytest.importorskip("PIL", reason="video/outro.py, which places the clip, needs Pillow")
    _fake_overlay(monkeypatch)
    clip = tmp_path / "f7_clip.pre-card.mp4"
    clip.write_bytes(b"as rendered")
    post_style.apply_card(clip, tmp_path / "c.png")
    assert clip.read_bytes() == b"with the card"
    assert list(tmp_path.iterdir()) == [clip]


def test_a_clip_that_cannot_be_written_says_so_and_leaves_nothing_behind(tmp_path, monkeypatch):
    pytest.importorskip("PIL", reason="video/outro.py, which places the clip, needs Pillow")
    import video.outro

    _fake_overlay(monkeypatch)
    monkeypatch.setattr(video.outro, "_replace_with_retry", lambda src, dst: False)
    clip = tmp_path / "f7_clip.pre-card.mp4"
    clip.write_bytes(b"as rendered")
    with pytest.raises(RuntimeError, match="title card"):
        post_style.apply_card(clip, tmp_path / "c.png")
    assert clip.read_bytes() == b"as rendered"
    assert list(tmp_path.iterdir()) == [clip]
