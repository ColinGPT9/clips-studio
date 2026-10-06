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


def test_a_sports_title_rules_go_into_the_highlights_prompt_too():
    """Basketball's rules (which player to name) and its rewrite of wrong
    titles go through the same call, so a Highlights clip keeps its card
    lines when its title is written again."""
    from analysis.metadata import generate_metadata_batch

    llm = _Echo({"index": 0, "title": "He did NOT miss😤", "description": "", "hashtags": [],
                 "headline": "FROM THE LOGO😤", "subline": ""})
    cand = [ClipCandidate(start=0, end=5, score=80, hook="three")]
    seg = [Segment(start=0, end=5, text="bang")]
    meta = generate_metadata_batch(cand, seg, "Game 7", llm, rules="- Name the scorer.", style="highlights")
    _, _, clips = llm.prompts[0].partition("CLIPS:\n")
    assert clips.startswith("RULES FOR THESE CLIPS:\n- Name the scorer.")
    assert "Never guess who someone is" in llm.prompts[0]
    assert meta[0].headline == "FROM THE LOGO😤"


# ---- what the review found --------------------------------------------------

_FONTS = Path("/usr/share/fonts")
_LIBERATION = _FONTS / "truetype/liberation/LiberationSans-Bold.ttf"
_DEJAVU_BOLD = _FONTS / "truetype/dejavu/DejaVuSans-Bold.ttf"
_DEJAVU = _FONTS / "truetype/dejavu/DejaVuSans.ttf"
_NOTO_EMOJI = _FONTS / "truetype/noto/NotoColorEmoji.ttf"
_CJK = _FONTS / "truetype/wqy/wqy-zenhei.ttc"
_LATIN_ONLY = _FONTS / "opentype/tlwg/Loma-Bold.otf"  # Latin and Thai, no Hebrew


def _need(*fonts):
    pytest.importorskip("PIL", reason="the card is drawn with Pillow, which CI does not install")
    for font in fonts:
        if not font.exists():
            pytest.skip(f"needs {font.name}")


@pytest.fixture
def windows_fonts(tmp_path, monkeypatch):
    """A Windows fonts folder whose files are fonts this machine has, so the
    face lists' Windows names can be tried here."""
    fonts = tmp_path / "Windows" / "Fonts"
    fonts.mkdir(parents=True)
    monkeypatch.setenv("WINDIR", str(fonts.parent))

    def add(name, real):
        _need(real)
        try:
            (fonts / name).symlink_to(real)
        except OSError:
            shutil.copyfile(real, fonts / name)

    return add


def test_rank_and_jersey_numbers_are_not_hashtags():
    from analysis.metadata import _clean_card_line

    assert post_style.card_text("#1 pick cooked him😭") == "#1 PICK COOKED HIM😭"
    assert post_style.card_text("#23 went off #nba #2k25 #ゴール") == "#23 WENT OFF"
    assert _clean_card_line("#1 PICK COOKED HIM😭 #nba", 40) == "#1 PICK COOKED HIM😭"
    assert _clean_card_line("GOAT #2k25 #ゴール", 40) == "GOAT"


def test_symbols_are_emoji_only_where_a_phone_draws_them_as_emoji():
    runs = post_style._runs
    assert runs("WHAT WAS THAT⁉\ufe0f") == [("WHAT WAS THAT", False), ("⁉\ufe0f", True)]
    assert runs("WHAT‼") == [("WHAT", False), ("‼", True)]
    assert runs("BIG MOVE▪\ufe0f") == [("BIG MOVE", False), ("▪\ufe0f", True)]
    assert runs("TOP 1\ufe0f\u20e3 PLAY") == [("TOP ", False), ("1\ufe0f\u20e3", True), (" PLAY", False)]
    # Arrows, stars and the wavy dash are typography unless U+FE0F asks.
    assert runs("LEBRON → LAKERS ★") == [("LEBRON → LAKERS ★", False)]
    assert runs("NEXT ➡\ufe0f") == [("NEXT ", False), ("➡\ufe0f", True)]
    assert runs("すごい〰") == [("すごい〰", False)]
    # A sequence is one emoji: a family, a skin tone, a flag.
    family, thumb, flag = "\U0001F468\u200d\U0001F469\u200d\U0001F467", "\U0001F44D\U0001F3FD", "\U0001F1FA\U0001F1F8"
    assert post_style._EMOJI_CLUSTER.findall(family + thumb + flag) == [family, thumb, flag]


def test_a_line_breaks_only_between_whole_characters():
    assert post_style._clusters("E\u0301A1\ufe0f\u20e3\U0001F44D\U0001F3FD") == [
        "E\u0301", "A", "1\ufe0f\u20e3", "\U0001F44D\U0001F3FD"]
    may = post_style._may_break
    assert may("試", "合") and may("A", "試")
    assert not may("A", "B")                       # a Latin word stays whole
    assert not may("合", "。") and not may("「", "試") and not may("合", "ー")  # kinsoku
    assert not may("合", "\U0001F525")              # an emoji stays with what it follows
    assert not may("경", "기")                      # Korean breaks at its spaces


def test_right_to_left_is_decided_by_the_language_and_the_letters():
    assert post_style._is_rtl("هدف رائع", "ar")
    assert post_style._is_rtl("שער מטורף", "en")     # starts in Hebrew, whatever the language
    assert not post_style._is_rtl("GOAL\U0001F525", "ar")  # nothing right to left in it
    assert not post_style._is_rtl("GOAL BY محمد", "en")


def test_every_language_ends_on_broad_fallback_faces():
    fallbacks = [name for name, _, _ in post_style._FALLBACK_FACES]
    for language in ("en", "he", "ja", "hi", "ar", "th", "ko"):
        names = [name for name, _, _ in post_style._faces_for(language)]
        assert set(fallbacks) <= set(names) and len(names) == len(set(names))
    japanese = [name for name, _, _ in post_style._faces_for("ja")]
    assert japanese[0] == "YuGothB.ttc" and japanese[-len(fallbacks):] == fallbacks


def test_a_cjk_headline_wraps_between_characters_inside_the_frame(tmp_path, windows_fonts):
    windows_fonts("YuGothB.ttc", _CJK)
    from PIL import Image

    headline = "今日の試合で彼が見せたプレーは本当に信じられないほど素晴らしかったし観客も総立ち"
    png = post_style.render_card(headline, "", (1080, 1920), tmp_path / "c.png", language="ja")
    box = Image.open(png).getbbox()
    assert box[0] > 0 and box[2] < 1080
    assert _boxes(png)[0][1] - _boxes(png)[0][0] > 150  # more than one line
    # 95 characters, the most a title can be: still inside the frame, in a
    # few lines, smaller.
    face, lines = post_style._fit("ja", "あ" * 95, 100, 60, 799, 929)
    assert 1 < len(lines) <= post_style._MAX_LINES
    assert all(post_style._line_width(face, line) <= 929 for line in lines)
    assert "".join(lines) == "あ" * 95


def test_no_line_is_ever_wider_than_the_frame():
    _need()
    if post_style._face("en", 60)[0] is None:
        pytest.skip("needs a Latin face")
    face, lines = post_style._fit("en", "A" * 400, 100, 60, 799, 929)
    assert 1 < len(lines) <= post_style._MAX_LINES
    assert all(post_style._line_width(face, line) <= 929 for line in lines)
    assert lines[-1].endswith("…")


def _emoji_columns(strip):
    """The x of every column with an emoji's ink: opaque, and not the yellow text."""
    w, h = strip.size
    px = strip.load()
    return [x for x in range(w) if any(
        px[x, y][3] > 200 and abs(px[x, y][0] - 245) + abs(px[x, y][1] - 250) + px[x, y][2] > 90
        for y in range(h))]


def test_a_right_to_left_line_is_laid_out_from_the_right():
    _need(_DEJAVU_BOLD, _NOTO_EMOJI)
    if not post_style._raqm():
        pytest.skip("needs Pillow with raqm")
    face, _ = post_style._face("ar", 100, "هدف رائع")
    assert face is not None
    strip, _, _ = post_style._draw_line(face, "هدف رائع\U0001F525", post_style.YELLOW, rtl=True)
    assert max(_emoji_columns(strip)) < strip.width * 0.3  # the end of the sentence is its left

    # Split by an emoji: the first word is the right one.
    strip, _, _ = post_style._draw_line(face, "هدف\U0001F525رائع", post_style.YELLOW, rtl=True)
    first, second = (round(face.font.getlength(word) * face.squeeze) for word in ("هدف", "رائع"))
    emoji_left = min(_emoji_columns(strip))
    assert abs(emoji_left - second) < abs(emoji_left - first)


def test_without_raqm_a_right_to_left_card_is_not_drawn(tmp_path, monkeypatch):
    _need()
    monkeypatch.setattr(post_style, "_raqm", lambda: False)
    monkeypatch.setattr(post_style, "_emoji_cache", {})
    assert post_style.render_card("هدف رائع\U0001F525", "", (1080, 1920), tmp_path / "c.png",
                                  language="ar") is None
    assert post_style.render_card("שער מטורף", "", (1080, 1920), tmp_path / "c.png", language="he") is None
    if post_style._face("en", 60)[0] is not None:
        assert post_style.render_card("GOAL\U0001F525", "", (1080, 1920), tmp_path / "c.png") is not None


def test_an_emoji_the_emoji_font_lacks_is_never_a_box(tmp_path, monkeypatch):
    # DejaVu Sans as the emoji font: an outline font whose .notdef is a drawn
    # box, as Segoe UI Emoji's is. It has no 🫡.
    _need(_DEJAVU, _NOTO_EMOJI)
    monkeypatch.setattr(post_style, "_emoji_cache", {"font": post_style._Emoji(_DEJAVU, 64)})
    assert post_style._emoji_font().cell("\U0001FAE1") is None
    png = post_style.render_card("SALUTE\U0001FAE1", "", (1080, 1920), tmp_path / "c.png")
    from PIL import Image, ImageChops

    im = Image.open(png).convert("RGBA")
    blue, alpha = (im.getchannel(c).point(lambda v: 255 if v > 128 else 0) for c in "BA")
    assert ImageChops.multiply(blue, alpha).getbbox() is None  # nothing white: yellow on black only

    # With the real emoji font: a symbol it lacks is drawn as text when the face has it.
    monkeypatch.setattr(post_style, "_emoji_cache", {})
    face = post_style._Face(_DEJAVU_BOLD, None, 1.0, 60)
    assert [kind for kind, _, _ in post_style._parts(face, "★\ufe0f")] == ["text"]


def test_emoji_keep_their_designed_size():
    _need(_LIBERATION, _NOTO_EMOJI)
    face = post_style._Face(_LIBERATION, None, 0.82, 100)
    widths = {value: w for kind, value, w in post_style._parts(face, "3➖0\U0001F440\U0001F62D")
              if kind == "emoji"}
    # A flat minus is one emoji wide, not a slab three and a half wide.
    assert widths["➖"] == widths["\U0001F62D"]
    emoji, height = post_style._emoji_font(), post_style._emoji_height(face)
    smiley = emoji.image("\U0001F600", height).getbbox()
    assert abs((smiley[3] - smiley[1]) - height) <= 2  # a smiley's ink is the height asked for
    assert emoji.image("➖", height).height == emoji.image("\U0001F525", height).height
    # A keycap is drawn as one emoji, not a plain digit.
    if post_style._raqm():
        assert [kind for kind, _, _ in post_style._parts(face, "1\ufe0f\u20e3")] == ["emoji"]


def test_a_face_without_the_scripts_letters_is_passed_over(windows_fonts):
    windows_fonts("impact.ttf", _LATIN_ONLY)
    _need(_LIBERATION)
    face, text = post_style._face("he", 60, "שער מטורף")
    assert face is not None and face.path.name != "impact.ttf"
    assert all(post_style._has_glyph(face.path, ch) for ch in "שערמטורף")
    face, _ = post_style._face("en", 60, "GOAL")
    assert face.path.name == "impact.ttf"  # a Latin card still gets the first face


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


def _held(monkeypatch, clip, refusals=None):
    """Hold `clip` open the way Windows does: its rename is refused, every
    time or the first `refusals` times. Returns the renames tried on it."""
    real = Path.replace
    tries = []

    def replace(self, target):
        if Path(target) == clip:
            tries.append(self)
            if refusals is None or len(tries) <= refusals:
                raise PermissionError(13, "Access is denied", str(target))
        return real(self, target)

    monkeypatch.setattr(Path, "replace", replace)
    return tries


def test_a_clip_open_in_the_preview_is_written_into_at_once(tmp_path, monkeypatch):
    """The preview's handle refuses the rename for as long as it is open, so
    waiting on the rename first only stalls every such clip."""
    pytest.importorskip("PIL", reason="video/outro.py, which places the clip, needs Pillow")
    import video.outro

    _fake_overlay(monkeypatch)
    clip = tmp_path / "f7_clip.mp4"
    clip.write_bytes(b"as rendered, which was longer")
    _held(monkeypatch, clip)
    monkeypatch.setattr(video.outro, "_replace_with_retry",
                        lambda src, dst: pytest.fail("waited on the rename"))
    post_style.apply_card(clip, tmp_path / "c.png")
    assert clip.read_bytes() == b"with the card"
    assert list(tmp_path.iterdir()) == [clip]


def test_a_clip_a_scanner_holds_is_waited_for(tmp_path, monkeypatch):
    pytest.importorskip("PIL", reason="video/outro.py, which places the clip, needs Pillow")
    import video.outro

    _fake_overlay(monkeypatch)
    clip = tmp_path / "f7_clip.mp4"
    clip.write_bytes(b"as rendered")
    tries = _held(monkeypatch, clip, refusals=3)
    monkeypatch.setattr(post_style, "_write_into", lambda new, clip: False)  # a scanner blocks writes too
    monkeypatch.setattr(video.outro.time, "sleep", lambda seconds: None)
    post_style.apply_card(clip, tmp_path / "c.png")
    assert clip.read_bytes() == b"with the card"
    assert len(tries) == 4
    assert list(tmp_path.iterdir()) == [clip]


def test_a_clip_that_cannot_be_written_says_so_and_leaves_nothing_behind(tmp_path, monkeypatch):
    pytest.importorskip("PIL", reason="video/outro.py, which places the clip, needs Pillow")
    import video.outro

    _fake_overlay(monkeypatch)
    clip = tmp_path / "f7_clip.pre-card.mp4"
    clip.write_bytes(b"as rendered")
    _held(monkeypatch, clip)
    monkeypatch.setattr(post_style, "_write_into", lambda new, clip: False)
    monkeypatch.setattr(video.outro, "_replace_with_retry", lambda src, dst: False)
    with pytest.raises(RuntimeError, match="title card"):
        post_style.apply_card(clip, tmp_path / "c.png")
    assert clip.read_bytes() == b"as rendered"
    assert list(tmp_path.iterdir()) == [clip]
