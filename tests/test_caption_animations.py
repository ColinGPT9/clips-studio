"""Animated captions and safe zones in the ASS text (video/captions.py).

- "none" and "off", set or left out, write the very file the app always has.
- Each animation's override tags, timed from the caption's own start: the
  caption's times and words never change.
- Karaoke's \\kf durations follow the words' timings, and words without
  timings are spread over the caption; the word highlight is not drawn
  under karaoke; the other speaker keeps their colour.
- Safe zones move the margins by the documented numbers, which are the
  Gaming layout editor's own.
- A word too wide for a safe zone's safe area shrinks its caption to fit.
- caption_fit works out line breaks the way libass does, or says it cannot.
- A bundled font that is missing burns in Arial, and the job log says so;
  so does a font this copy of the app does not have.

Standard library only: these run in CI's light install.
"""

import json
import math
import re
from pathlib import Path

import pytest

from core.models import ClipCandidate, Segment
from video import captions, fonts
from video.captions import DEFAULT_STYLE, build_captions

ROOT = Path(__file__).resolve().parent.parent
WORDS = [
    {"start": 10.0, "end": 10.3, "word": "this"}, {"start": 10.3, "end": 10.5, "word": "is"},
    {"start": 10.7, "end": 11.2, "word": "huge"}, {"start": 11.2, "end": 11.6, "word": "right"},
    {"start": 11.6, "end": 11.9, "word": "now"}, {"start": 11.9, "end": 12.5, "word": "guys"},
]
SEGMENTS = [Segment(start=10.0, end=12.5, text="this is huge right now guys", words=WORDS)]
WINDOW = ClipCandidate(start=10.0, end=12.5, score=0)


def _ass(tmp_path: Path, style: dict | None = None, name: str = "clip.ass", **kwargs) -> str:
    path = build_captions(SEGMENTS, WINDOW, tmp_path / name, style=style, **kwargs)
    assert path is not None
    return path.read_text(encoding="utf-8")


def _events(text: str) -> list[str]:
    return [line for line in text.splitlines() if line.startswith("Dialogue:")]


def _style(text: str) -> list[str]:
    line = next(line for line in text.splitlines() if line.startswith("Style: Default,"))
    return line[len("Style: "):].split(",")


def _times_and_words(events: list[str]) -> list[tuple[str, str, str]]:
    """Each event's start, end and words, with every override tag taken out."""
    out = []
    for event in events:
        fields = event[len("Dialogue: "):].split(",", 9)
        out.append((fields[1], fields[2], re.sub(r"\{[^}]*\}", "", fields[9])))
    return out


# ---- nothing changes unless it is picked -------------------------------------------------------


@pytest.mark.parametrize("extra", [
    {}, {"animation": "none"}, {"safe_zone": "off"}, {"animation": "none", "safe_zone": "off"},
    {"animation": "wobble"}, {"safe_zone": "snapchat"}, {"animation": ["pop"], "safe_zone": ["tiktok"]},
    {"animation": None, "safe_zone": None},
])
@pytest.mark.parametrize("base", [None, {"highlight": True}, {"position": "top", "font": "Impact"}])
def test_none_and_off_write_the_file_as_it_always_was(tmp_path, base, extra):
    before = _ass(tmp_path, base, "before.ass")
    assert _ass(tmp_path, {**(base or {}), **extra}, "after.ass") == before


def test_the_default_style_has_no_tag_and_todays_header(tmp_path):
    text = _ass(tmp_path)
    assert "Style: Default,Arial,84,&H00FFFFFF,&H00FFFFFF,&H00000000,&H7F000000,-1,0,0,0,100,100,0,0,1,7,2,2,60,60,440,1" in text
    assert all("{" not in e for e in _events(text))
    assert DEFAULT_STYLE["animation"] == "none" and DEFAULT_STYLE["safe_zone"] == "off"


# ---- the animations ------------------------------------------------------------------------------


TAGS = {
    "pop": "{\\fscx80\\fscy80\\t(0,120,\\fscx100\\fscy100)}",
    "bounce": "{\\fscx80\\fscy80\\t(0,120,\\fscx110\\fscy110)\\t(120,200,\\fscx100\\fscy100)}",
    "fade": "{\\fad(120,80)}",
    "slide": "{\\move(540,1520,540,1480,0,150)}",
}


@pytest.mark.parametrize("animation", sorted(TAGS))
def test_each_animation_opens_every_caption_with_its_tags(tmp_path, animation):
    plain = _events(_ass(tmp_path, None, "plain.ass"))
    moving = _events(_ass(tmp_path, {"animation": animation}, "moving.ass"))
    assert _times_and_words(moving) == _times_and_words(plain)       # same times, same words
    for before, after in zip(plain, moving):
        head, text = before.split("Default,,0,0,0,,")
        assert after == f"{head}Default,,0,0,0,,{TAGS[animation]}{text}"


def test_slide_ends_where_the_caption_stands_for_each_position_and_canvas(tmp_path):
    for position, canvas, end in (("bottom", (1080, 1920), (540, 1480)), ("top", (1080, 1920), (540, 140)),
                                  ("middle", (1080, 1920), (540, 960)), ("bottom", (1920, 1080), (960, 832))):
        text = _ass(tmp_path, {"animation": "slide", "position": position}, canvas=canvas)
        rise = round(40 * canvas[1] / 1920)
        assert f"\\move({end[0]},{end[1] + rise},{end[0]},{end[1]},0,150)" in _events(text)[0], (position, canvas)


def test_slide_in_a_safe_zone_stays_centred_and_clear(tmp_path):
    text = _ass(tmp_path, {"animation": "slide", "safe_zone": "tiktok", "position": "top"})
    assert "\\move(540,240,540,200,0,150)" in _events(text)[0]


def test_with_the_word_highlight_a_caption_animates_once(tmp_path):
    """The highlight writes one event per word: the animation is on the
    first of a caption's events, the fade-out on its last, and the events
    after the animation has played have no tag (a slid caption's stand where
    the slide ended, which is where libass puts them)."""
    for animation, first, middle, last in (
        ("pop", TAGS["pop"], "", ""),
        ("fade", "{\\fad(120,0)}", "", "{\\fad(0,80)}"),
        ("slide", TAGS["slide"], "", ""),
    ):
        plain = _events(_ass(tmp_path, {"highlight": True, "words_per_caption": 3}, "plain.ass"))
        moving = _events(_ass(tmp_path, {"highlight": True, "words_per_caption": 3, "animation": animation}))
        assert len(plain) == len(moving) == 6                 # 2 captions x 3 words
        for i, (before, after) in enumerate(zip(plain, moving)):
            tag = (first, middle, last)[i % 3]
            head, text = before.split("Default,,0,0,0,,")
            assert after == f"{head}Default,,0,0,0,,{tag}{text}", (animation, i)


def test_an_animation_never_changes_the_other_speakers_colour(tmp_path):
    lines = [{"start": 0.0, "end": 1.0, "text": "hello there"},
             {"start": 1.0, "end": 2.0, "text": "who me", "speaker": 1}]
    path = build_captions(SEGMENTS, WINDOW, tmp_path / "c.ass", lines=lines,
                          style={"second_speaker": True, "animation": "bounce"})
    events = _events(path.read_text(encoding="utf-8"))
    assert events[1].endswith(TAGS["bounce"] + "{\\1c&HFFE15C&}WHO ME")


SHORT_FIRST = [Segment(start=10.0, end=11.0, text="so close bro", words=[
    {"start": 10.0, "end": 10.06, "word": "so"}, {"start": 10.06, "end": 10.4, "word": "close"},
    {"start": 10.4, "end": 10.8, "word": "bro"}])]


def _short_first(tmp_path: Path, animation: str) -> list[str]:
    path = build_captions(SHORT_FIRST, ClipCandidate(start=10.0, end=11.0, score=0), tmp_path / f"{animation}.ass",
                          style={"animation": animation, "highlight": True}, lines=[
                              {"start": 0.0, "end": 0.8, "text": "so close bro"}])
    return [e.split("Default,,0,0,0,,")[1] for e in _events(path.read_text(encoding="utf-8"))]


def test_a_short_first_word_hands_the_animation_on_where_it_got_to(tmp_path):
    """With the highlight, the second word's event starts 60 ms in: it picks
    the animation up at the state it reached then and plays the rest."""
    pop = _short_first(tmp_path, "pop")
    assert pop[0].startswith(TAGS["pop"])
    assert pop[1].startswith("{\\fscx90\\fscy90\\t(0,60,\\fscx100\\fscy100)}")      # 60 of 120 ms: 90%
    assert not pop[2].startswith("{\\fsc")
    bounce = _short_first(tmp_path, "bounce")
    assert bounce[1].startswith("{\\fscx95\\fscy95\\t(0,60,\\fscx110\\fscy110)\\t(60,140,\\fscx100\\fscy100)}")
    slide = _short_first(tmp_path, "slide")
    assert slide[1].startswith("{\\move(540,1504,540,1480,0,90)}")                 # 24 of the 40 px still to go
    fade = _short_first(tmp_path, "fade")
    assert fade[0].startswith("{\\fade(255,0,255,0,120,720,800)}")
    assert fade[1].startswith("{\\fade(128,0,255,0,60,660,740)}")                  # half faded in
    assert fade[2].startswith("{\\fad(0,80)}")


def test_the_fade_out_spans_a_last_word_shorter_than_it(tmp_path):
    lines = [{"start": 0.0, "end": 1.0, "text": "this is huge"}]
    words = [Segment(start=10.0, end=11.0, text="this is huge", words=[
        {"start": 10.0, "end": 10.5, "word": "this"}, {"start": 10.5, "end": 10.95, "word": "is"},
        {"start": 10.95, "end": 11.0, "word": "huge"}])]
    path = build_captions(words, ClipCandidate(start=10.0, end=11.0, score=0), tmp_path / "f.ass", lines=lines,
                          style={"animation": "fade", "highlight": True})
    bodies = [e.split("Default,,0,0,0,,")[1] for e in _events(path.read_text(encoding="utf-8"))]
    assert bodies[0].startswith("{\\fad(120,0)}")
    assert bodies[1].startswith("{\\fade(0,0,255,0,0,420,500)}")      # the fade-out starts 420 ms into "is"
    assert bodies[2].startswith("{\\fade(96,96,255,0,0,0,50)}")       # 30 of its 80 ms gone: on through "huge"


@pytest.mark.parametrize("animation", ["pop", "bounce", "slide"])
def test_captions_on_screen_together_get_no_moving_animation(tmp_path, animation):
    """Pop, bounce and slide stop libass from stacking captions that are on
    screen together: a caption that overlaps another gets none of them."""
    lines = [{"start": 0.2, "end": 1.4, "text": "first caption"}, {"start": 0.9, "end": 2.0, "text": "second one"},
             {"start": 2.1, "end": 2.4, "text": "third"}]
    plain = _events(build_captions(SEGMENTS, WINDOW, tmp_path / "a.ass", lines=lines).read_text(encoding="utf-8"))
    moving = _events(build_captions(SEGMENTS, WINDOW, tmp_path / "b.ass", lines=lines,
                                    style={"animation": animation}).read_text(encoding="utf-8"))
    assert moving[:2] == plain[:2]
    assert moving[2] != plain[2]                                       # the caption on its own still moves


def test_fade_still_fades_captions_on_screen_together(tmp_path):
    lines = [{"start": 0.2, "end": 1.4, "text": "first caption"}, {"start": 0.9, "end": 2.0, "text": "second one"}]
    events = _events(build_captions(SEGMENTS, WINDOW, tmp_path / "a.ass", lines=lines,
                                    style={"animation": "fade"}).read_text(encoding="utf-8"))
    assert all("{\\fad(120,80)}" in e for e in events)


def _one(tmp_path: Path, style: dict, text: str, name: str = "w.ass") -> tuple[list[str], list[str]]:
    path = build_captions(SEGMENTS, WINDOW, tmp_path / name, style=style,
                          lines=[{"start": 0.0, "end": 1.0, "text": text}])
    out = path.read_text(encoding="utf-8")
    return _style(out), [e.split("Default,,0,0,0,,")[1] for e in _events(out)]


def test_pop_writes_out_the_line_breaks_when_its_size_would_move_them(tmp_path):
    """At 80% this caption fits on one line, at 100% libass breaks it in
    two: pop keeps the two lines from the first frame (\\N, and \\q2 so
    libass does not wrap them again)."""
    from video import caption_fit

    style = {"font": "Titan One", "font_size": 86, "safe_zone": "tiktok", "animation": "pop"}
    fields, bodies = _one(tmp_path, style, "impossible shot")
    wrap = 1080 - int(fields[19]) - int(fields[20])
    assert caption_fit.lines("IMPOSSIBLE SHOT", "Titan One", 86, False, wrap) == ["IMPOSSIBLE", "SHOT"]
    assert caption_fit.lines("IMPOSSIBLE SHOT", "Titan One", 86 * 0.8, False, wrap) == ["IMPOSSIBLE SHOT"]
    assert bodies == ["{\\q2\\fscx80\\fscy80\\t(0,120,\\fscx100\\fscy100)}IMPOSSIBLE\\NSHOT"]
    # The same caption with no animation is left to libass, as always.
    assert _one(tmp_path, {**style, "animation": "none"}, "impossible shot", "n.ass")[1] == ["IMPOSSIBLE SHOT"]


def test_the_written_out_breaks_hold_in_every_event_of_a_highlighted_caption(tmp_path):
    style = {"font": "Titan One", "font_size": 86, "safe_zone": "tiktok", "animation": "bounce", "highlight": True}
    _, bodies = _one(tmp_path, style, "impossible shot")
    assert len(bodies) == 2
    assert all(b.startswith("{\\q2") and b.count("\\N") == 1 for b in bodies), bodies
    assert re.sub(r"\{[^}]*\}", "", bodies[1]) == "IMPOSSIBLE\\NSHOT"


def test_bounce_overshoots_less_when_110_percent_would_reach_the_margins(tmp_path):
    from video import caption_fit

    fields, bodies = _one(tmp_path, {"font": "Titan One", "font_size": 100, "animation": "bounce"},
                          "unbelievable play")
    peak = int(re.search(r"\\t\(0,120,\\fscx(\d+)", bodies[0]).group(1))
    widest = caption_fit.widest(["UNBELIEVABLE PLAY"], "Titan One", 100, False)
    assert 100 <= peak < 110 and widest * peak / 100 < 1080 - 120
    assert "\\q2" not in bodies[0]                     # it fits on one line at every size up to that


def test_a_font_this_machine_cannot_measure_animates_as_before(tmp_path, monkeypatch):
    from video import caption_fit

    monkeypatch.setattr(caption_fit, "font", lambda family: None)
    _, bodies = _one(tmp_path, {"font": "Titan One", "font_size": 86, "safe_zone": "tiktok", "animation": "pop"},
                     "impossible shot")
    assert bodies == [TAGS["pop"] + "IMPOSSIBLE SHOT"]


def test_line_breaks_are_worked_out_the_way_libass_works_them_out():
    """libass evens out lines (wrap_lines_rebalance) measuring each without
    its trailing spaces, and after moving a word goes on from where the
    moved line used to start. These are the breaks the bundled FFmpeg's
    libass makes (test_caption_render_ffmpeg.py burns them); measuring the
    trailing spaces gave THAT IS / THAT / ..."""
    from video import caption_fit

    assert caption_fit.lines("THAT IS THAT CHANGED WAIT EVER AT", "Titan One", 120, False, 682) == [
        "THAT", "IS THAT", "CHANGED", "WAIT", "EVER AT"]


def test_the_qc_caption_breaks_where_libass_breaks_it():
    """QC round 2's case: pop settled on THIS IS / WHERE THE / WHOLE ...
    while libass draws it with no animation as below."""
    from video import caption_fit

    if caption_fit.font("Arial") is None:
        pytest.skip("needs Windows' Arial to measure")
    assert caption_fit.lines("THIS IS WHERE THE WHOLE THING CHANGED", "Arial", 120, True, 682) == [
        "THIS IS", "WHERE", "THE WHOLE", "THING", "CHANGED"]


def test_a_close_call_that_changes_the_lines_is_not_guessed():
    """Moving BELIEVE down evens the lines by a hair's breadth either way:
    libass's own rounding decides it, so lines() says it cannot be sure."""
    from video import caption_fit

    text = "TONIGHT IN GAMEPLAY EVER RIGHT TONIGHT BELIEVE MOMENT FROM"
    assert caption_fit.lines(text, "Bangers", 120, False, 682) is None
    assert caption_fit.lines(text, "Bangers", 120, False, 2000) == [text]


def test_a_character_the_measuring_does_not_follow_is_not_guessed():
    """An emoji is drawn from another font, and so is a letter the font
    lacks: lines() does not guess for a caption with one."""
    from video import caption_fit

    assert caption_fit.lines("WELL KNOWN SHOT \U0001F525", "Titan One", 100, False, 682) is None
    assert caption_fit.lines("WELL KNOWN \u0e2a\u0e38\u0e14", "Titan One", 100, False, 682) is None     # Thai: not in Titan One
    assert caption_fit.lines("WELL KNOWN SHOT", "Titan One", 100, False, 682) is not None


SYMBOLS = ["TWENTY-FIVE", "$500", "50%", "RE-DO", "#1", "@HOME", "1+1", "AND/OR", "5*5", "A=B", "WAIT\u2014WHAT",
           "SO\u2026", "<3", "[OK]", "A_B"]


@pytest.mark.parametrize("word", SYMBOLS)
def test_a_hyphen_a_dollar_or_a_percent_sign_is_measured_like_a_letter(word):
    """QC round 3: libass breaks a line only at a space, never after a
    hyphen or any other sign (burned to check), so a caption with one is
    measured like any other: the word stays whole on its line."""
    from video import caption_fit

    text = f"BELIEVE WHAT {word}"
    assert caption_fit.lines(text, "Titan One", 100, False, 2000) == [text]
    narrow = caption_fit.lines(text, "Titan One", 100, False, 420)
    assert narrow is not None and " ".join(narrow) == text
    assert narrow[-1].split()[-1] == word and len(narrow) > 1


@pytest.mark.parametrize("animation", ["pop", "bounce"])
def test_a_hyphenated_caption_keeps_its_lines_while_it_grows(tmp_path, animation):
    """QC round 3's caption: BELIEVE WHAT TWENTY-FIVE drew one line at 80%
    and jumped to two. Its breaks are written out like any caption's."""
    from video import caption_fit

    style = {"font": "Titan One", "font_size": 84, "animation": animation}
    fields, bodies = _one(tmp_path, style, "believe what twenty-five")
    wrap = 1080 - int(fields[19]) - int(fields[20])
    assert caption_fit.lines("BELIEVE WHAT TWENTY-FIVE", "Titan One", 84, False, wrap) == [
        "BELIEVE WHAT", "TWENTY-FIVE"]
    assert caption_fit.lines("BELIEVE WHAT TWENTY-FIVE", "Titan One", 84 * 0.8, False, wrap) == [
        "BELIEVE WHAT TWENTY-FIVE"]
    (body,) = bodies
    assert body.startswith("{\\q2\\fscx80\\fscy80\\t(0,120,") and body.endswith("}BELIEVE WHAT\\NTWENTY-FIVE"), body


def test_a_glyphs_ink_is_every_point_of_its_outline():
    """libass measures a line by the box around every point of a glyph,
    control points included. Lilita One's "y" has one 6 units right of the
    box in its own header: going by the header broke "wi-fi nobody" a line
    later than libass does (fix round 3's burned sweep)."""
    from video import caption_fit

    face = caption_fit.font("Lilita One")
    assert face.x_bounds(face.cmap()[ord("y")]) == (10, 532)
    assert face.x_bounds(face.cmap()[ord(" ")]) is None
    assert caption_fit.lines("wi-fi nobody where", "Lilita One", 140, False, 682) == ["wi-fi", "nobody", "where"]
    assert caption_fit.lines("wi-fi nobody where", "Lilita One", 140, False, 684) == ["wi-fi nobody", "where"]
    # A glyph built from other glyphs (an accented letter) is measured from its parts.
    anton = caption_fit.font("Anton")
    e, e_acute = (anton.x_bounds(anton.cmap()[ord(c)]) for c in "E\u00c9")
    assert e_acute is not None and e_acute[0] <= e[0] and e_acute[1] >= e[1]


# ---- pop and bounce on a caption that cannot be measured -----------------------------------------


@pytest.mark.parametrize("animation", ["pop", "bounce"])
@pytest.mark.parametrize("text", ["what a shot \U0001F525", "\u0e2a\u0e38\u0e14\u0e22\u0e2d\u0e14 what a shot"])
def test_a_caption_that_cannot_be_measured_fades_in_at_full_size(tmp_path, animation, text):
    """QC round 3: with a character the measuring does not follow, pop and
    bounce used to scale the caption anyway, and libass laid it out again at
    80% and at 110% (another line count for a few frames, an overshoot into
    a safe zone's column). Such a caption is never scaled: it fades in at
    full size over the same 120 ms."""
    for zone in ("off", "tiktok"):
        style = {"font": "Titan One", "animation": animation, "safe_zone": zone}
        _, bodies = _one(tmp_path, style, text, f"{zone}.ass")
        assert bodies == ["{\\fad(120,0)}" + text.upper()], (zone, bodies)
        plain = _one(tmp_path, {**style, "animation": "none"}, text, "n.ass")
        assert plain[1] == [text.upper()]


def test_an_unmeasured_caption_fades_in_once_across_the_word_highlight(tmp_path):
    path = build_captions(SHORT_FIRST, ClipCandidate(start=10.0, end=11.0, score=0), tmp_path / "h.ass",
                          style={"font": "Titan One", "animation": "bounce", "highlight": True},
                          lines=[{"start": 0.0, "end": 0.8, "text": "so close \U0001F525"}])
    bodies = [e.split("Default,,0,0,0,,")[1] for e in _events(path.read_text(encoding="utf-8"))]
    assert len(bodies) == 3
    assert bodies[0].startswith("{\\fade(255,0,255,0,120,") and bodies[1].startswith("{\\fade(128,0,255,0,60,")
    assert bodies[2].startswith("{\\1c")                    # in by then: no tag
    assert not any("\\fsc" in b or "\\t(" in b for b in bodies)


def test_an_unmeasured_caption_in_a_safe_zone_keeps_its_smaller_size(tmp_path):
    """Thai is shaped, so its line breaks are not worked out (lines() says
    None), but its width is (widest_word): the caption shrinks to the safe
    area and fades in at that size."""
    from video import caption_fit

    thai = "\u0e19\u0e35\u0e48\u0e21\u0e31\u0e19\u0e2a\u0e38\u0e14\u0e22\u0e2d\u0e14\u0e21\u0e32\u0e01\u0e08\u0e23\u0e34\u0e07\u0e46\u0e40\u0e25\u0e22\u0e19\u0e30"
    style = {"font": "Kanit ExtraBold", "font_size": 120, "safe_zone": "tiktok", "animation": "pop"}
    path = build_captions(SEGMENTS, WINDOW, tmp_path / "th.ass", style=style, language="th",
                          lines=[{"start": 0.0, "end": 1.0, "text": thai}])
    out = path.read_text(encoding="utf-8")
    fields = _style(out)
    assert fields[0] == "Default" and fields[1] == "Kanit ExtraBold"
    room = 1080 - int(fields[19]) - int(fields[20]) - 1
    widest = caption_fit.widest_word(thai, "Kanit ExtraBold", 120, True)
    fit = math.floor(100 * room / widest)
    assert widest > room and fit < 100
    assert _events(out)[0].endswith(f",,{{\\fscx{fit}\\fscy{fit}\\fad(120,0)}}{thai}")
    assert caption_fit.lines(thai, "Kanit ExtraBold", 120, True, room) is None


# ---- a caption in a language written without spaces ----------------------------------------------


def test_thai_is_measured_with_room_for_its_shaping():
    """libass places Thai marks and splits SARA AM: the width is the plain
    sum of the glyphs with 4% on top, so the shrink never comes up short."""
    from video import caption_fit

    thai = "\u0e2a\u0e38\u0e14\u0e22\u0e2d\u0e14\u0e21\u0e32\u0e01"
    plain = caption_fit.widest([thai], "Kanit ExtraBold", 100, True)
    assert caption_fit.widest_word(thai, "Kanit ExtraBold", 100, True) == pytest.approx(plain * 1.04)
    assert caption_fit.widest_word(thai, "Titan One", 100, False) is None       # not in that font


@pytest.mark.parametrize(("language", "font", "text"), [
    ("zh", "Microsoft YaHei", "\u8fd9\u4e2a\u771f\u7684\u592a\u5389\u5bb3\u4e86\u5427\u670b\u53cb\u4eec"),
    ("ja", "Yu Gothic UI", "\u3053\u308c\u306f\u672c\u5f53\u306b\u3059\u3054\u3044\u3067\u3059\u306d\u7686\u3055\u3093"),
    ("ko", "Malgun Gothic", "\uc774\uac83\uc740\uc815\ub9d0\ub300\ub2e8\ud569\ub2c8\ub2e4\uc5ec\ub7ec\ubd84\ubcf4\uc138\uc694"),
])
def test_a_caption_without_spaces_shrinks_to_the_safe_area(tmp_path, language, font, text):
    """QC round 3: Chinese, Japanese and Korean are written without spaces,
    so the whole caption is one word libass cannot break; it ran under the
    app's column. It is measured glyph by glyph from the script font and
    drawn smaller, like a long English word."""
    from video import caption_fit

    if caption_fit.font(font) is None:
        pytest.skip(f"needs Windows' {font} to measure")
    style = {"font": "Anton", "font_size": 120, "safe_zone": "tiktok"}        # the script font takes Anton's place
    path = build_captions(SEGMENTS, WINDOW, tmp_path / "c.ass", style=style, language=language,
                          lines=[{"start": 0.0, "end": 1.0, "text": text}])
    out = path.read_text(encoding="utf-8")
    fields = _style(out)
    assert fields[1] == font
    room = 1080 - int(fields[19]) - int(fields[20]) - 1
    widest = caption_fit.widest_word(text, font, 120, True)
    fit = math.floor(100 * room / widest)
    assert widest > room and widest * fit / 100 <= room
    assert _events(out)[0].endswith(f",,{{\\fscx{fit}\\fscy{fit}}}{text}")
    # With no safe zone the caption is written as it always was.
    plain = build_captions(SEGMENTS, WINDOW, tmp_path / "p.ass", style={"font": "Anton", "font_size": 120},
                           language=language, lines=[{"start": 0.0, "end": 1.0, "text": text}])
    assert _events(plain.read_text(encoding="utf-8"))[0].endswith(f",,{text}")


# ---- fade on a short caption ---------------------------------------------------------------------


@pytest.mark.parametrize(("length", "tag"), [
    (0.08, "\\fad(29,19)"), (0.10, "\\fad(36,24)"), (0.14, "\\fad(50,34)"), (0.16, "\\fad(58,38)"),
    (0.33, "\\fad(119,79)"), (0.34, "\\fad(120,80)"), (1.0, "\\fad(120,80)"),
])
def test_fade_is_shorter_on_a_short_caption(tmp_path, length, tag):
    """QC round 3: \\fad(120,80) on a caption shorter than 200 ms has the
    fade-out start inside the fade-in, and libass jumps from one to the
    other between two frames (92% to 24%). The two fades take at most 60%
    of a caption, in the same 3:2, so it reaches full colour and holds it."""
    path = build_captions(SEGMENTS, WINDOW, tmp_path / "f.ass", style={"animation": "fade"},
                          lines=[{"start": 0.5, "end": 0.5 + length, "text": "was"}])
    (event,) = _events(path.read_text(encoding="utf-8"))
    assert event.endswith(f",,{{{tag}}}WAS")
    fade_in, fade_out = (int(v) for v in re.search(r"\\fad\((\d+),(\d+)\)", tag).groups())
    assert fade_in + fade_out <= 200
    if length < 0.34:
        assert fade_in + fade_out <= length * 1000 * 0.6 + 0.001       # the rest is held at full colour


def test_a_short_caption_fades_the_same_with_the_word_highlight(tmp_path):
    """A 150 ms caption as two highlight events: in over its first 54 ms,
    out over its last 36, the same times as the one-event caption."""
    words = [Segment(start=10.0, end=10.5, text="no way", words=[
        {"start": 10.3, "end": 10.37, "word": "no"}, {"start": 10.37, "end": 10.45, "word": "way"}])]
    window = ClipCandidate(start=10.0, end=10.5, score=0)
    lines = [{"start": 0.3, "end": 0.45, "text": "no way"}]
    lit = build_captions(words, window, tmp_path / "h.ass", lines=lines, style={"animation": "fade", "highlight": True})
    bodies = [e.split("Default,,0,0,0,,")[1] for e in _events(lit.read_text(encoding="utf-8"))]
    assert [b[:b.index("}") + 1] for b in bodies] == ["{\\fad(54,0)}", "{\\fad(0,36)}"]
    one = build_captions(words, window, tmp_path / "o.ass", lines=lines, style={"animation": "fade"})
    assert _events(one.read_text(encoding="utf-8"))[0].endswith("{\\fad(54,36)}NO WAY")


# ---- a word too wide for the safe area ---------------------------------------------------------


LONG_WORD = "congratulations everyone"


def _fit(fields: list[str], font: str, size: int, bold: bool) -> tuple[int, float, int]:
    """(the room between the style's margins, the widest word's width, the
    size in % that brings it inside)."""
    from video import caption_fit

    room = 1080 - int(fields[19]) - int(fields[20]) - 1
    widest = caption_fit.widest_word(LONG_WORD.upper(), font, size, bold)
    return room, widest, math.floor(100 * room / widest)


def test_a_word_too_wide_for_the_safe_area_shrinks_its_caption(tmp_path):
    """libass never breaks inside a word: CONGRATULATIONS is wider than the
    space between TikTok's margins, so the caption is drawn smaller."""
    fields, bodies = _one(tmp_path, {"font": "Titan One", "safe_zone": "tiktok"}, LONG_WORD)
    room, widest, fit = _fit(fields, "Titan One", 84, False)
    assert widest > room and widest * fit / 100 <= room
    assert bodies == [f"{{\\fscx{fit}\\fscy{fit}}}CONGRATULATIONS EVERYONE"]
    # The style, the margins and the times are what they were.
    assert fields == _one(tmp_path, {"font": "Titan One", "safe_zone": "tiktok"}, "big win", "s.ass")[0]
    # Without a safe zone the caption is written as always.
    assert _one(tmp_path, {"font": "Titan One"}, LONG_WORD, "off.ass")[1] == ["CONGRATULATIONS EVERYONE"]


def test_congratulations_at_the_default_size_fits_inside_tiktoks_safe_area(tmp_path):
    from video import caption_fit

    if caption_fit.font("Arial") is None:
        pytest.skip("needs Windows' Arial to measure")
    fields, bodies = _one(tmp_path, {"safe_zone": "tiktok"}, LONG_WORD)
    room, widest, fit = _fit(fields, "Arial", 84, True)
    assert widest > room and fit < 100
    assert bodies == [f"{{\\fscx{fit}\\fscy{fit}}}CONGRATULATIONS EVERYONE"]


@pytest.mark.parametrize("animation", ["pop", "bounce", "fade", "slide", "karaoke"])
def test_every_animation_keeps_the_smaller_size(tmp_path, animation):
    """Pop and bounce grow to the smaller size, not to 100%; the others draw
    the caption at it from the first frame."""
    fields, bodies = _one(tmp_path, {"font": "Titan One", "safe_zone": "tiktok", "animation": animation}, LONG_WORD)
    _, _, fit = _fit(fields, "Titan One", 84, False)
    size = f"\\fscx{fit}\\fscy{fit}"
    (body,) = bodies
    if animation == "pop":
        start = captions._num(80 * fit / 100)
        assert body.startswith(f"{{\\fscx{start}\\fscy{start}\\t(0,120,{size})}}"), body
    elif animation == "bounce":
        assert body.endswith(f",{size})}}CONGRATULATIONS EVERYONE"), body
        peak = float(re.search(r"\\t\(0,120,\\fscx([\d.]+)", body).group(1))
        assert peak == fit             # the word fills the safe area: no overshoot past it
    else:
        assert body.startswith("{" + size), body
    assert body.count("\\fscx") == body.count("\\fscy")


def test_with_the_word_highlight_every_event_keeps_the_smaller_size(tmp_path):
    for animation in ("none", "pop", "bounce", "fade"):
        style = {"font": "Titan One", "safe_zone": "tiktok", "highlight": True, "animation": animation}
        fields, bodies = _one(tmp_path, style, LONG_WORD, f"{animation}.ass")
        _, _, fit = _fit(fields, "Titan One", 84, False)
        assert len(bodies) == 2
        assert all(f"\\fscx{fit}\\fscy{fit}" in b for b in bodies), (animation, bodies)


def test_a_caption_that_fits_is_not_scaled(tmp_path):
    fields, bodies = _one(tmp_path, {"font": "Titan One", "safe_zone": "tiktok"}, "big win today")
    assert bodies == ["BIG WIN TODAY"]


def test_a_word_that_cannot_be_measured_is_left_as_it_was(tmp_path, monkeypatch):
    from video import caption_fit

    monkeypatch.setattr(caption_fit, "font", lambda family: None)
    assert _one(tmp_path, {"font": "Titan One", "safe_zone": "tiktok"}, LONG_WORD)[1] == ["CONGRATULATIONS EVERYONE"]


# ---- karaoke ---------------------------------------------------------------------------------


def _karaoke_parts(event: str) -> list[tuple[str, int, str]]:
    """(tag, hundredths, text after it) for each \\k or \\kf in an event."""
    body = event.split("Default,,0,0,0,,", 1)[1]
    return [(m.group(1), int(m.group(2)), m.group(3)) for m in re.finditer(r"\{\\(kf|k)(\d+)\}([^{]*)", body)]


def test_karaoke_fills_each_word_while_it_is_said(tmp_path):
    events = _events(_ass(tmp_path, {"animation": "karaoke"}))
    assert len(events) == 2                                 # one event per caption, not one per word
    first = events[0]
    assert first == ("Dialogue: 0,0:00:00.00,0:00:01.20,Default,,0,0,0,,{\\1c&H00E6FF&\\2c&HFFFFFF&}"
                     "{\\kf30}THIS {\\kf20}IS {\\k20}{\\kf50}HUGE")


def test_karaoke_marks_follow_the_word_timings(tmp_path):
    lines = [{"start": 0.0, "end": 2.5, "text": "this is huge right now guys"}]
    path = build_captions(SEGMENTS, WINDOW, tmp_path / "k.ass", lines=lines, style={"animation": "karaoke"})
    parts = _karaoke_parts(_events(path.read_text(encoding="utf-8"))[0])
    at, starts = 0, []
    for tag, hundredths, text in parts:
        if tag == "kf":
            starts.append(at)
        at += hundredths
    assert starts == [round((w["start"] - 10.0) * 100) for w in WORDS]
    assert at == 250                                         # the last word fills until it is said
    assert ("k", 20, "") in parts                            # the pause between "is" and "huge"


def test_karaoke_without_word_timings_spreads_the_words(tmp_path):
    untimed = [Segment(start=10.0, end=12.0, text="one two three four")]
    path = build_captions(untimed, ClipCandidate(start=10.0, end=12.0, score=0), tmp_path / "k.ass",
                          lines=[{"start": 0.0, "end": 2.0, "text": "one two three four"}],
                          style={"animation": "karaoke"})
    parts = _karaoke_parts(_events(path.read_text(encoding="utf-8"))[0])
    assert [p[0] for p in parts] == ["kf"] * 4
    assert all(p[1] > 0 for p in parts) and sum(p[1] for p in parts) == 200


def test_karaoke_takes_the_place_of_the_word_highlight(tmp_path):
    plain = _ass(tmp_path, {"animation": "karaoke"}, "a.ass")
    assert _ass(tmp_path, {"animation": "karaoke", "highlight": True}, "b.ass") == plain


def test_karaoke_keeps_the_other_speakers_colour(tmp_path):
    lines = [{"start": 0.0, "end": 1.0, "text": "hello there"},
             {"start": 1.0, "end": 2.0, "text": "who me", "speaker": 1}]
    path = build_captions(SEGMENTS, WINDOW, tmp_path / "c.ass", lines=lines,
                          style={"second_speaker": True, "animation": "karaoke", "highlight_color": "#FF0000"})
    first, second = _events(path.read_text(encoding="utf-8"))
    assert "{\\1c&H0000FF&\\2c&HFFFFFF&}" in first          # fills red from the caption colour
    assert "{\\1c&H0000FF&\\2c&HFFE15C&}" in second         # from the other speaker's colour


# ---- safe zones ------------------------------------------------------------------------------


@pytest.mark.parametrize(("zone", "bottom", "top", "side"), [
    ("tiktok", 440, 200, 199), ("reels", 440, 265, 214), ("shorts", 440, 270, 199),
])
def test_a_safe_zone_keeps_captions_clear_of_the_apps_own_buttons(tmp_path, zone, bottom, top, side):
    for position, alignment, margin in (("bottom", "2", bottom), ("top", "8", top), ("middle", "5", 0)):
        fields = _style(_ass(tmp_path, {"safe_zone": zone, "position": position}))
        assert fields[18:22] == [alignment, str(side), str(side), str(margin)], (zone, position)


def test_a_safe_zones_side_margins_hold_the_outline_and_shadow_too():
    """The margins bound the letters; the outline (7) and shadow (2) reach
    9 px further, so a safe zone's side margins are the app's column plus 9."""
    assert captions._INK_SPREAD == 7 + 2
    for zone, numbers in captions.SAFE_ZONES.items():
        _, left, right, _ = captions._layout({"safe_zone": zone})
        assert left == right == numbers["right"] + 9, zone


@pytest.mark.parametrize("zone", sorted(captions.SAFE_ZONES))
def test_a_bottom_caption_that_slides_in_never_starts_in_the_apps_bottom_bar(tmp_path, zone):
    """Slide starts a caption 40 px below where it stands: in a safe zone a
    bottom caption stands that much higher, so even its start clears the bar
    by the usual 20 px."""
    text = _ass(tmp_path, {"safe_zone": zone, "animation": "slide"})
    margin = int(_style(text)[21])
    assert margin >= captions.SAFE_ZONES[zone]["bottom"] + 20 + 40
    start_y = int(re.search(r"\\move\(\d+,(\d+),", _events(text)[0]).group(1))
    assert 1920 - start_y >= captions.SAFE_ZONES[zone]["bottom"] + 20
    # Without a slide, or without a safe zone, the margin is what it was.
    assert _style(_ass(tmp_path, {"safe_zone": zone}, "b.ass"))[21] == "440"
    assert _style(_ass(tmp_path, {"animation": "slide"}, "c.ass"))[21] == "440"


def test_a_safe_zone_is_for_vertical_clips_only(tmp_path):
    canvas = (1920, 1080)
    assert _ass(tmp_path, {"safe_zone": "tiktok"}, "a.ass", canvas=canvas) == _ass(tmp_path, None, "b.ass", canvas=canvas)


def test_the_safe_zones_are_the_gaming_layout_editors_numbers():
    layouts = json.loads((ROOT / "gaming" / "layouts.json").read_text(encoding="utf-8"))["safe_zones"]
    for zone, numbers in captions.SAFE_ZONES.items():
        assert {k: layouts[zone][k] for k in numbers} == numbers, zone


# ---- fonts in the header --------------------------------------------------------------------


def test_a_bundled_font_is_written_by_its_family_name(tmp_path):
    assert _style(_ass(tmp_path, {"font": "Poppins ExtraBold"}))[1:2] == ["Poppins ExtraBold"]
    # A one-weight display face is not made bold on top: libass would thicken it.
    assert _style(_ass(tmp_path, {"font": "Bebas Neue"}))[7] == "0"
    assert _style(_ass(tmp_path, {"font": "Poppins ExtraBold"}))[7] == "-1"
    assert _style(_ass(tmp_path, {"font": "Impact"}))[7] == "-1"


def test_an_unknown_font_still_burns_in_arial(tmp_path):
    assert _style(_ass(tmp_path, {"font": "Papyrus"}))[1] == "Arial"


def test_a_font_this_copy_does_not_have_burns_in_arial_and_says_so(tmp_path, capsys):
    text = _ass(tmp_path, {"font": "Papyrus"})
    assert "The caption font Papyrus is not in this copy of the app, so the captions use Arial" \
        in capsys.readouterr().out
    assert text == _ass(tmp_path, {"font": "Arial"}, "arial.ass")      # the log changes, the file does not
    _ass(tmp_path, {"font": "Arial"}, "again.ass")
    assert capsys.readouterr().out == ""                                # nothing to say for a font it has


def test_without_the_apps_fonts_folder_a_bundled_font_says_why(tmp_path, monkeypatch, capsys):
    """An engine built without assets/fonts (or with an unreadable manifest)
    has none of the bundled fonts: a preset's font burns in Arial, and the
    job log says the app's own fonts are missing."""
    monkeypatch.setattr(captions, "FONTS", list(captions.WINDOWS_FONTS))
    monkeypatch.setattr(fonts, "folder", lambda: None)
    monkeypatch.setattr(fonts, "is_bundled", lambda family: False)
    assert _style(_ass(tmp_path, {"font": "Poppins ExtraBold", "animation": "pop"}))[1] == "Arial"
    assert ("The caption font Poppins ExtraBold is not in this copy of the app, so the captions use Arial "
            "(the app's own caption fonts are missing)") in capsys.readouterr().out


def test_a_missing_bundled_font_burns_in_arial_and_says_so(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(fonts, "usable", lambda family: False)
    assert _style(_ass(tmp_path, {"font": "Anton"}))[1] == "Arial"
    assert "The caption font Anton is missing or damaged in this copy of the app, so the captions use Arial" \
        in capsys.readouterr().out


def test_a_language_the_font_cannot_write_takes_the_windows_font(tmp_path):
    assert _style(_ass(tmp_path, {"font": "Bangers"}, language="ru"))[1] == "Arial"
    assert _style(_ass(tmp_path, {"font": "Bangers"}, language="ja"))[1] == "Yu Gothic UI"
    assert _style(_ass(tmp_path, {"font": "Russo One"}, language="ru"))[1] == "Russo One"


# ---- QC fix round 4: what is measured is what is drawn ------------------------------------------

# The same three words with each kind of gap a person can type or paste (or
# a translation can bring: French puts U+00A0 or U+202F before ? ! : ;).
# libass draws every one of them as written and breaks only at U+0020.
ODD_GAPS = {
    "two spaces": "this game  just",
    "three spaces": "this   game just",
    "a tab": "this game\tjust",
    "a no-break space": "this game just",
    "a narrow no-break space": "this game just",
    "an en space": "this game just",
    "an ideographic space": "this game　just",
    "a zero-width space": "this ga​me just",
    "a word joiner and a byte-order mark": "﻿this game⁠ just",
    "spaces at the ends": "  this game just  ",
    "a brace between two spaces": "this game { just",
}
LOOKS = [
    {"animation": "pop"}, {"animation": "bounce"}, {"animation": "fade"}, {"animation": "slide"},
    {"animation": "karaoke"}, {"safe_zone": "tiktok"}, {"safe_zone": "reels", "highlight": True},
    {"animation": "bounce", "safe_zone": "tiktok"}, {"animation": "pop", "highlight": True},
]


@pytest.mark.parametrize("gap", sorted(ODD_GAPS))
@pytest.mark.parametrize("look", LOOKS, ids=lambda look: "+".join(f"{k}={v}" for k, v in look.items()))
def test_an_animated_or_safe_zone_caption_is_written_with_single_plain_spaces(tmp_path, gap, look):
    """The guards measure words with one space between them, so the event
    is written exactly that way: the caption with an odd gap comes out as
    the one typed cleanly, tags and all."""
    style = {"font": "Titan One", "font_size": 86, **look}
    clean = _one(tmp_path, style, "this game just", "clean.ass")
    odd = _one(tmp_path, style, ODD_GAPS[gap], "odd.ass")
    assert odd == clean
    for body in odd[1]:
        words = re.sub(r"\{[^}]*\}", "", body).replace("\\N", " ")
        assert words == "THIS GAME JUST", body


@pytest.mark.parametrize("gap", sorted(ODD_GAPS))
@pytest.mark.parametrize("base", [{}, {"font": "Titan One", "position": "top"}, {"animation": "none", "safe_zone": "off"},
                                  {"safe_zone": "tiktok", "animation": "wobble"}])
def test_a_plain_captions_text_is_left_exactly_as_typed(tmp_path, gap, base):
    """No animation and no safe zone: byte for byte what it always was, odd
    gaps included (only the ends trimmed and the ASS control characters
    taken out, as before this step)."""
    text = ODD_GAPS[gap]
    expected = text.strip().upper().replace("\\", "").replace("{", "").replace("}", "")
    landscape = base.get("safe_zone") == "tiktok"
    path = build_captions(SEGMENTS, WINDOW, tmp_path / "p.ass", style=base,
                          lines=[{"start": 0.0, "end": 1.0, "text": text}],
                          canvas=(1920, 1080) if landscape else (1080, 1920))
    (event,) = _events(path.read_text(encoding="utf-8"))
    assert event == f"Dialogue: 0,0:00:00.00,0:00:01.00,Default,,0,0,0,,{expected}"


@pytest.mark.parametrize("joiner", [" ", " "])
def test_words_joined_by_a_no_break_space_do_not_run_under_the_buttons(tmp_path, joiner):
    """QC round 4: libass never breaks at a no-break space, so ABSOLUTELY
    and UNREAL joined by one were a single run 69 px into TikTok's column.
    The caption is written with an ordinary space: it wraps, and each word
    fits the safe area."""
    from video import caption_fit

    style = {"font": "Titan One", "font_size": 86, "safe_zone": "tiktok"}
    fields, bodies = _one(tmp_path, style, f"absolutely{joiner}unreal")
    assert bodies == ["ABSOLUTELY UNREAL"]
    room = 1080 - int(fields[19]) - int(fields[20]) - 1
    joined = caption_fit.widest(["ABSOLUTELY UNREAL"], "Titan One", 86, False)
    assert joined > room >= caption_fit.widest_word("ABSOLUTELY UNREAL", "Titan One", 86, False)
    # A very long word with French punctuation spacing shrinks like the
    # same line typed with ordinary spaces.
    long_word = _one(tmp_path, style, f"anticonstitutionnellement{joiner}?{joiner}!", "fr.ass")[1]
    assert long_word == _one(tmp_path, style, "anticonstitutionnellement ? !", "fr2.ass")[1]
    assert long_word[0].startswith("{\\fscx") and long_word[0].endswith("}ANTICONSTITUTIONNELLEMENT ? !")


def test_the_widest_word_is_split_only_where_libass_breaks():
    """A no-break space (or any space but U+0020) never makes two short
    words out of one long run: such text is not measured at all."""
    from video import caption_fit

    assert caption_fit.widest_word("ABSOLUTELY UNREAL", "Titan One", 86, False) > 0
    for gap in (" ", " ", "\t", " ", "　", "​"):
        assert caption_fit.widest_word(f"ABSOLUTELY{gap}UNREAL", "Titan One", 86, False) is None, repr(gap)
        assert caption_fit.lines(f"ABSOLUTELY{gap}UNREAL", "Titan One", 86, False, 682) is None, repr(gap)
    # Runs of ordinary spaces are fine: the words are what they were.
    assert caption_fit.widest_word("  ABSOLUTELY   UNREAL ", "Titan One", 86, False) \
        == caption_fit.widest_word("ABSOLUTELY UNREAL", "Titan One", 86, False)


def test_a_doubled_space_near_the_wrap_width_does_not_reflow_at_bounces_peak(tmp_path):
    """QC round 4: THIS GAME  JUST (two spaces) is one space wider than the
    text the guard measured, and crossed the wrap width at bounce's 110%
    for two frames. The event now holds the measured text, whose widest
    line stays inside the margins at the peak."""
    from video import caption_fit

    style = {"font": "Titan One", "font_size": 86, "safe_zone": "tiktok", "animation": "bounce"}
    fields, bodies = _one(tmp_path, style, "this game  just")
    assert bodies == ["{\\fscx80\\fscy80\\t(0,120,\\fscx110\\fscy110)\\t(120,200,\\fscx100\\fscy100)}THIS GAME JUST"]
    wrap = 1080 - int(fields[19]) - int(fields[20])
    drawn = caption_fit.widest(["THIS GAME JUST"], "Titan One", 86, False)
    typed = caption_fit.widest(["THIS GAME  JUST"], "Titan One", 86, False)
    assert drawn * 1.10 < wrap < typed * 1.10          # as typed it would have crossed; as written it does not


def test_text_that_is_not_as_the_event_has_it_is_never_scaled():
    """_scale_lines measures the text exactly as given; anything but single
    ordinary spaces is not what build_captions writes, so it is held (fades
    in at full size) rather than guessed."""
    opts = {**DEFAULT_STYLE, "font": "Titan One", "font_size": 86, "safe_zone": "tiktok"}
    assert captions._scale_lines("THIS GAME JUST", opts, (1080, 1920), 110)[2] is True
    for odd in ("THIS GAME  JUST", "THIS GAME JUST", "THIS\tGAME JUST", " THIS GAME JUST", "THIS GA​ME JUST"):
        assert captions._scale_lines(odd, opts, (1080, 1920), 110) == (None, 110, False), repr(odd)


def test_a_caption_of_nothing_but_invisible_characters_is_not_written_when_animated(tmp_path):
    lines = [{"start": 0.0, "end": 0.5, "text": "​﻿"}, {"start": 0.5, "end": 1.0, "text": "big win"}]
    path = build_captions(SEGMENTS, WINDOW, tmp_path / "z.ass", style={"animation": "fade"}, lines=lines)
    events = _events(path.read_text(encoding="utf-8"))
    assert [e.split("Default,,0,0,0,,")[1] for e in events] == ["{\\fad(120,80)}BIG WIN"]
