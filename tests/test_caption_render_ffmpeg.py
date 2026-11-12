"""Captions burned for real by FFmpeg's libass: a bundled font is the font
that burns, and each animation moves the caption the way it says.

Needs FFmpeg with libass (the subtitles filter); skips without it. Nothing is
downloaded: the picture is FFmpeg's own solid colour, the caption a short line
of made-up words, burned exactly the way a render burns it (the ASS file's
folder as the working folder, video/fonts.py's subtitles filter).

- With a bundled font, libass is told to load the staged file and its
  fontselect line names that file's font, not Arial.
- Frames taken every 20 ms across each animation's first 200 ms: pop and
  bounce grow (bounce past full size, then back), fade comes up from nothing,
  slide rises into place, karaoke fills with the highlight colour, and "none"
  never changes. Each ends exactly where the caption stands with no animation.
"""

import math
import subprocess
from pathlib import Path

import pytest

from core import binaries
from core.models import ClipCandidate, Segment
from video.captions import build_captions
from video.fonts import subtitles_filter

W, H, FPS = 540, 960, 50          # a 9:16 frame at half size; a frame every 20 ms
ROWS = (560, 820)                 # the band the bottom caption sits in at this size
WORDS = [{"start": 0.0, "end": 0.15, "word": "big"}, {"start": 0.15, "end": 0.3, "word": "fun"},
         {"start": 0.3, "end": 0.6, "word": "now"}]


def _ffmpeg_with_libass() -> str:
    if binaries.missing():
        pytest.skip("needs FFmpeg")
    exe = binaries.ffmpeg()
    try:
        filters = subprocess.run([exe, "-hide_banner", "-filters"], capture_output=True, text=True,
                                 timeout=60).stdout
    except (OSError, subprocess.SubprocessError):
        pytest.skip("FFmpeg could not be run")
    if " subtitles " not in filters:
        pytest.skip("this FFmpeg has no libass")
    return exe


def _captions(folder: Path, style: dict) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    segments = [Segment(start=0.0, end=0.6, text="big fun now", words=WORDS)]
    ass = build_captions(segments, ClipCandidate(start=0.0, end=0.6, score=0), folder / "clip.ass",
                         style=style, canvas=(W, H))
    assert ass is not None
    return ass


def _burn(exe: str, ass: Path, pix: str = "gray", seconds: float = 0.3, loglevel: str = "error"):
    """Frames of a solid picture with the captions burned, as the render burns them."""
    cmd = [exe, "-hide_banner", "-loglevel", loglevel, "-f", "lavfi",
           "-i", f"color=c=0x203050:s={W}x{H}:r={FPS}:d={seconds}",
           "-vf", subtitles_filter(ass), "-f", "rawvideo", "-pix_fmt", pix, "-"]
    out = subprocess.run(cmd, capture_output=True, cwd=ass.parent, timeout=120)
    assert out.returncode == 0, out.stderr.decode("utf-8", "replace")[-800:]
    size = W * H * (3 if pix == "rgb24" else 1)
    frames = [out.stdout[i:i + size] for i in range(0, len(out.stdout), size)]
    return frames, out.stderr.decode("utf-8", "replace")


def _area(frame: bytes):
    """(left, top, right, bottom, count) of the caption's bright pixels in a
    grey frame, sampled every other pixel; None when nothing is bright."""
    xs, ys = [], []
    for y in range(ROWS[0], ROWS[1], 2):
        row = frame[y * W:(y + 1) * W]
        for x in range(0, W, 2):
            if row[x] > 180:
                xs.append(x)
                ys.append(y)
    return (min(xs), min(ys), max(xs), max(ys), len(xs)) if xs else None


def _width(area) -> int:
    return area[2] - area[0] if area else 0


def _yellow(frame: bytes) -> int:
    """Pixels in the highlight colour (#FFE600) in an RGB frame's caption band."""
    count = 0
    for y in range(ROWS[0], ROWS[1], 2):
        base = y * W * 3
        for x in range(0, W, 2):
            r, g, b = frame[base + 3 * x:base + 3 * x + 3]
            if r > 200 and g > 170 and b < 110:
                count += 1
    return count


@pytest.fixture(scope="module")
def exe():
    return _ffmpeg_with_libass()


@pytest.fixture(scope="module")
def still(exe, tmp_path_factory):
    """Where the caption stands with no animation: every frame the same."""
    frames, _ = _burn(exe, _captions(tmp_path_factory.mktemp("none"), {"font": "Anton"}))
    areas = [_area(f) for f in frames]
    assert areas[0] is not None and all(a == areas[0] for a in areas), areas
    return areas[0]


def test_a_bundled_font_is_the_font_libass_burns(exe, tmp_path):
    ass = _captions(tmp_path, {"font": "Anton"})
    assert subtitles_filter(ass) == "subtitles=clip.ass:fontsdir=fonts"
    _, log = _burn(exe, ass, seconds=0.1, loglevel="verbose")
    picked = [line for line in log.splitlines() if "fontselect:" in line]
    assert any("Loading font file" in line and "Anton-Regular.ttf" in line for line in log.splitlines()), log[-2000:]
    assert picked and all("(Anton," in line and "-> Anton-Regular" in line for line in picked), picked
    assert not any("Arial" in line for line in picked), picked


def test_without_an_animation_nothing_moves(still):
    assert _width(still) > 40


def test_pop_grows_into_place(exe, tmp_path, still):
    areas = [_area(f) for f in _burn(exe, _captions(tmp_path, {"font": "Anton", "animation": "pop"}))[0]]
    widths = [_width(a) for a in areas]
    assert widths[0] < 0.9 * _width(still)                    # starts at about 80%
    assert widths[:7] == sorted(widths[:7])                   # and only grows
    assert all(a == still for a in areas[7:])                 # in place from 140 ms


def test_bounce_overshoots_then_settles(exe, tmp_path, still):
    areas = [_area(f) for f in _burn(exe, _captions(tmp_path, {"font": "Anton", "animation": "bounce"}))[0]]
    widths = [_width(a) for a in areas]
    assert widths[0] < 0.9 * _width(still)
    assert max(widths[3:10]) > _width(still)                  # past full size, around 120 ms
    assert all(a == still for a in areas[11:])                # settled from 220 ms


def test_fade_comes_up_from_nothing(exe, tmp_path, still):
    areas = [_area(f) for f in _burn(exe, _captions(tmp_path, {"font": "Anton", "animation": "fade"}))[0]]
    assert areas[0] is None                                   # nothing bright at the start
    assert all(a == still for a in areas[7:])                 # fully in from 140 ms


def test_slide_rises_into_place(exe, tmp_path, still):
    areas = [_area(f) for f in _burn(exe, _captions(tmp_path, {"font": "Anton", "animation": "slide"}))[0]]
    tops = [a[1] for a in areas]
    assert 14 <= tops[0] - still[1] <= 26                     # 40 px at 1920 tall is 20 px here
    assert tops[:9] == sorted(tops[:9], reverse=True)         # only rises
    assert all(a[:4] == still[:4] for a in areas[9:])         # in place from 180 ms, same spot as no animation


def _rows(frame: bytes, top: int = 0, bottom: int = H) -> int:
    """How many separate bands of bright (text) rows a grey frame has."""
    bands, before = 0, False
    for y in range(top, bottom):
        bright = max(frame[y * W:(y + 1) * W]) > 200
        if bright and not before:
            bands += 1
        before = bright
    return bands


def _lines(folder: Path, style: dict, lines: list[dict], end: float) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    text = " ".join(line["text"] for line in lines)
    segments = [Segment(start=0.0, end=end, text=text, words=[])]
    ass = build_captions(segments, ClipCandidate(start=0.0, end=end, score=0), folder / "clip.ass",
                         style=style, lines=lines, canvas=(W, H))
    assert ass is not None
    return ass


# Captions close to the width between the margins in Titan One at this size:
# libass would break each into a different number of lines at some size
# between pop's 80% and bounce's 110%.
NEAR_THE_EDGE = ["UNBELIEVABLE PLAY", "IMPOSSIBLE SHOT", "ABSOLUTELY WILD", "THAT WAS INSANE"]


@pytest.mark.parametrize("animation", ["pop", "bounce"])
def test_pop_and_bounce_never_change_a_captions_line_count(exe, tmp_path, animation):
    fixed = 0
    for i, text in enumerate(NEAR_THE_EDGE):
        style = {"font": "Titan One", "font_size": 100, "position": "middle"}
        lines = [{"start": 0.0, "end": 0.5, "text": text}]
        still = _burn(exe, _lines(tmp_path / f"none{i}", style, lines, 0.5), seconds=0.1)[0][0]
        ass = _lines(tmp_path / f"{animation}{i}", {**style, "animation": animation}, lines, 0.5)
        fixed += "\\q2" in ass.read_text(encoding="utf-8")
        rows = [_rows(f) for f in _burn(exe, ass, seconds=0.32)[0]]
        assert rows == [_rows(still)] * len(rows), (text, rows)
    assert fixed, "none of the captions needed its lines written out: the test proves nothing"


@pytest.mark.parametrize("animation", ["pop", "bounce", "slide", "fade"])
def test_a_short_first_word_does_not_cut_the_animation_short(exe, tmp_path, animation):
    """With the word highlight a caption is one event per word; a first word
    of 60 ms must not stop the animation: the caption moves frame by frame
    exactly as it does without the highlight."""
    words = [{"start": 0.0, "end": 0.06, "word": "so"}, {"start": 0.06, "end": 0.4, "word": "close"},
             {"start": 0.4, "end": 0.6, "word": "bro"}]
    shapes = {}
    for highlight in (False, True):
        folder = tmp_path / f"{animation}{highlight}"
        folder.mkdir()
        segments = [Segment(start=0.0, end=0.6, text="so close bro", words=words)]
        ass = build_captions(segments, ClipCandidate(start=0.0, end=0.6, score=0), folder / "clip.ass",
                             style={"font": "Anton", "animation": animation, "highlight": highlight,
                                    "highlight_color": "#FFFFFF"}, canvas=(W, H))
        frames, _ = _burn(exe, ass, seconds=0.3)
        shapes[highlight] = [_area(f) for f in frames]
    for plain, lit in zip(shapes[False], shapes[True]):
        if plain is None or lit is None:
            assert plain is lit is None or animation == "fade"
            continue
        assert all(abs(p - q) <= 2 for p, q in zip(plain[:4], lit[:4])), (shapes[False], shapes[True])
    if animation == "bounce":
        widths = [_width(a) for a in shapes[True]]
        assert max(widths[3:10]) > widths[-1]                 # the overshoot shows


@pytest.mark.parametrize("animation", ["none", "pop", "bounce", "slide", "fade"])
def test_captions_on_screen_together_still_stack(exe, tmp_path, animation):
    """Two captions whose times overlap (hand-edited) stand one above the
    other, with or without an animation."""
    lines = [{"start": 0.0, "end": 0.6, "text": "first caption"}, {"start": 0.1, "end": 0.6, "text": "second"}]
    ass = _lines(tmp_path, {"font": "Anton", "animation": animation}, lines, 0.6)
    frames, _ = _burn(exe, ass, seconds=0.3)
    assert _rows(frames[12]) == 2                             # at 240 ms both are on screen, stacked


def test_karaoke_fills_with_the_highlight_colour(exe, tmp_path):
    frames, _ = _burn(exe, _captions(tmp_path, {"font": "Anton", "animation": "karaoke"}), pix="rgb24")
    filled = [_yellow(f) for f in frames]
    assert filled[0] < filled[7] < filled[14]                 # "big", then "fun", then "now"
    assert filled == sorted(filled)


# ---- full-size 9:16 frames: where libass breaks lines, and the safe area ----------------------

FULL_W, FULL_H = 1080, 1920


def _full(folder: Path, style: dict, text: str) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    segments = [Segment(start=0.0, end=1.0, text=text, words=[])]
    ass = build_captions(segments, ClipCandidate(start=0.0, end=1.0, score=0), folder / "clip.ass",
                         style=style, lines=[{"start": 0.0, "end": 1.0, "text": text}], canvas=(FULL_W, FULL_H))
    assert ass is not None
    return ass


def _full_frames(exe: str, ass: Path, seconds: float, fps: int = 50) -> list[bytes]:
    cmd = [exe, "-hide_banner", "-loglevel", "error", "-f", "lavfi",
           "-i", f"color=c=0x203050:s={FULL_W}x{FULL_H}:r={fps}:d={seconds}",
           "-vf", subtitles_filter(ass), "-f", "rawvideo", "-pix_fmt", "gray", "-"]
    out = subprocess.run(cmd, capture_output=True, cwd=ass.parent, timeout=120)
    assert out.returncode == 0, out.stderr.decode("utf-8", "replace")[-800:]
    size = FULL_W * FULL_H
    return [out.stdout[i:i + size] for i in range(0, len(out.stdout), size)]


def _ink_right(frame: bytes) -> int:
    """The right-most pixel of the caption (letters, outline and shadow): any
    pixel that is not the background colour."""
    bg = frame[0]
    marked = frame.translate(bytes(1 if abs(v - bg) > 24 else 0 for v in range(256)))
    right = -1
    for y in range(FULL_H):
        x = marked.rfind(b"\x01", y * FULL_W, (y + 1) * FULL_W)
        if x >= 0:
            right = max(right, x - y * FULL_W)
    return right


# Captions whose 100% lines pop and bounce write out (\N with \q2). The
# first is QC round 2's: pop used to settle on THIS IS / WHERE THE / WHOLE
# while libass draws THIS IS / WHERE / THE WHOLE with no animation.
SETTLED = [("Arial", "THIS IS WHERE THE WHOLE THING CHANGED"), ("Titan One", "THAT IS THAT CHANGED WAIT EVER AT")]


@pytest.mark.parametrize("animation", ["pop", "bounce"])
@pytest.mark.parametrize(("font", "text"), SETTLED)
def test_pop_and_bounce_settle_on_the_picture_of_no_animation(exe, tmp_path, animation, font, text):
    from video import caption_fit

    if caption_fit.font(font) is None:
        pytest.skip(f"needs {font} to measure")
    style = {"font": font, "font_size": 120, "safe_zone": "tiktok", "words_per_caption": 7}
    animated = _full(tmp_path / animation, {**style, "animation": animation}, text)
    assert "\\q2" in animated.read_text(encoding="utf-8"), "no breaks written out: the test proves nothing"
    settled = _full_frames(exe, animated, 0.5, fps=10)[-1]            # at 400 ms, long after it settled
    still = _full_frames(exe, _full(tmp_path / "none", style, text), 0.5, fps=10)[-1]
    assert settled == still


@pytest.mark.parametrize("animation", ["none", "pop", "bounce", "slide"])
@pytest.mark.parametrize("font", ["Arial", "Titan One"])
def test_a_word_too_wide_for_the_safe_area_stays_clear_of_the_buttons(exe, tmp_path, font, animation):
    """CONGRATULATIONS at the default size is wider than the space between
    TikTok's margins; libass cannot break it, so the caption is drawn
    smaller and never reaches the app's column of buttons, not even at
    bounce's overshoot."""
    from video import caption_fit, captions

    if caption_fit.font(font) is None:
        pytest.skip(f"needs {font} to measure")
    ass = _full(tmp_path, {"font": font, "safe_zone": "tiktok", "animation": animation}, "congratulations everyone")
    assert "\\fscx" in ass.read_text(encoding="utf-8")
    column = FULL_W - captions.SAFE_ZONES["tiktok"]["right"]
    rights = [_ink_right(f) for f in _full_frames(exe, ass, 0.3)]
    assert rights and all(0 < r < column for r in rights), (column, rights)


# ---- QC fix round 3 --------------------------------------------------------------------------


def _full_rows(frame: bytes) -> int:
    """How many separate bands of bright (text) rows a full-size grey frame has."""
    bands, before = 0, False
    for y in range(FULL_H):
        bright = max(frame[y * FULL_W:(y + 1) * FULL_W]) > 200
        if bright and not before:
            bands += 1
        before = bright
    return bands


@pytest.mark.parametrize("animation", ["pop", "bounce"])
@pytest.mark.parametrize(("font", "size", "zone", "text"), [
    ("Arial", 84, "off", "BELIEVE WHAT TWENTY-FIVE"),          # QC round 3's: one line at 80%, then two
    ("Titan One", 84, "off", "BELIEVE WHAT TWENTY-FIVE"),
    ("Poppins ExtraBold", 84, "tiktok", "ABOUT $500 50% OFF TODAY"),
    ("Arial", 120, "tiktok", "ONLY TWENTY-FIVE"),
])
def test_a_caption_with_a_hyphen_or_a_sign_keeps_its_lines_and_its_place(exe, tmp_path, animation, font, size,
                                                                         zone, text):
    """A hyphen, a dollar or a percent sign used to leave a caption
    unmeasured: pop and bounce then changed its line count part way, and
    bounce overshot into the safe zone's column."""
    from video import caption_fit, captions

    if caption_fit.font(font) is None:
        pytest.skip(f"needs {font} to measure")
    style = {"font": font, "font_size": size, "safe_zone": zone, "words_per_caption": 6}
    still = _full_frames(exe, _full(tmp_path / "none", style, text), 0.1, fps=10)[0]
    frames = _full_frames(exe, _full(tmp_path / animation, {**style, "animation": animation}, text), 0.32)
    rows = [_full_rows(f) for f in frames]
    assert rows == [_full_rows(still)] * len(rows), rows
    assert frames[-1] == still                                   # settled on the picture of no animation
    if zone != "off":
        column = FULL_W - captions.SAFE_ZONES[zone]["right"]
        rights = [_ink_right(f) for f in frames]
        assert all(0 < r < column for r in rights), (column, rights)


@pytest.mark.parametrize("animation", ["pop", "bounce"])
def test_a_caption_that_cannot_be_measured_never_changes_size(exe, tmp_path, animation):
    """A caption with a character the measuring does not follow (here a
    Thai word in a Latin font, drawn from another font) is not scaled: it
    fades in where it stands, the same width in every frame."""
    from video import captions

    text = "สุดยอด WHAT A SHOT TODAY"
    style = {"font": "Titan One", "font_size": 110, "safe_zone": "tiktok", "words_per_caption": 6}
    ass = _full(tmp_path / animation, {**style, "animation": animation}, text)
    assert "\\fsc" not in ass.read_text(encoding="utf-8") and "\\fad(120,0)" in ass.read_text(encoding="utf-8")
    still = _full_frames(exe, _full(tmp_path / "none", style, text), 0.1, fps=10)[0]
    frames = _full_frames(exe, ass, 0.3)
    column = FULL_W - captions.SAFE_ZONES["tiktok"]["right"]
    rights = [_ink_right(f) for f in frames if _ink_right(f) >= 0]
    assert rights and max(rights) == _ink_right(still) < column, (rights, _ink_right(still))
    assert frames[-1] == still


def _shown(frame: bytes, blank: bytes, full: bytes) -> float:
    """How much of the caption a frame shows, 0 to 1: its brightness over the
    empty picture's, against the caption at full colour."""
    return (sum(frame) - sum(blank)) / max(1, sum(full) - sum(blank))


@pytest.mark.parametrize("length", [0.08, 0.10, 0.14, 0.16])
@pytest.mark.parametrize("highlight", [False, True])
def test_fade_on_a_short_caption_reaches_full_colour_and_goes_smoothly(exe, tmp_path, length, highlight):
    """\\fad(120,80) on a 140 ms caption jumped from 92% to 24% between two
    frames and never reached full colour on a shorter one. The fades are
    shortened to fit: the caption comes up, holds at full colour, goes."""
    words = [{"start": 0.1, "end": 0.1 + length / 2, "word": "no"},
             {"start": 0.1 + length / 2, "end": 0.1 + length, "word": "way"}]
    lines = [{"start": 0.1, "end": 0.1 + length, "text": "no way"}]
    segments = [Segment(start=0.0, end=0.5, text="no way", words=words)]
    shown = {}
    for animation in ("none", "fade"):
        folder = tmp_path / animation
        folder.mkdir()
        ass = build_captions(segments, ClipCandidate(start=0.0, end=0.5, score=0), folder / "clip.ass", lines=lines,
                             style={"font": "Barlow ExtraBold", "animation": animation, "highlight": highlight,
                                    "highlight_color": "#FFFFFF"}, canvas=(W, H))
        cmd = [exe, "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i", f"color=c=0x203050:s={W}x{H}:r=100:d=0.4",
               "-vf", subtitles_filter(ass), "-f", "rawvideo", "-pix_fmt", "gray", "-"]
        out = subprocess.run(cmd, capture_output=True, cwd=folder, timeout=120)
        assert out.returncode == 0, out.stderr.decode("utf-8", "replace")[-800:]
        shown[animation] = [out.stdout[i:i + W * H] for i in range(0, len(out.stdout), W * H)]
    blank = shown["none"][0]
    full = shown["none"][12]                                     # 120 ms: the caption is on screen
    assert full != blank
    levels = [round(_shown(f, blank, full), 2) for f in shown["fade"]]
    on = [v for v, plain in zip(levels, shown["none"]) if plain != blank]
    assert len(on) >= 7 and max(on) >= 0.99, levels              # reaches full colour
    drops = [a - b for a, b in zip(on, on[1:])]
    # No jump: a frame (10 ms) takes off no more than the shortened fade-out does in that time.
    fade_out = math.floor(length * 1000 * 0.6) - round(math.floor(length * 1000 * 0.6) * 0.6)
    assert max(drops) <= 10 / fade_out + 0.2, (levels, fade_out)
    peak = on.index(max(on))
    assert on[:peak + 1] == sorted(on[:peak + 1]), levels        # up, held, then down
    assert on[peak:] == sorted(on[peak:], reverse=True), levels


@pytest.mark.parametrize(("language", "font", "text"), [
    ("zh", "Microsoft YaHei", "这个真的太厉害了吧朋友们"),
    ("ja", "Yu Gothic UI", "これは本当にすごいですね皆さん"),
    ("th", "Leelawadee UI", "นี่มันสุดยอดมากจริงๆเลยนะ"),
])
@pytest.mark.parametrize("animation", ["none", "bounce"])
def test_a_caption_without_spaces_stays_clear_of_the_buttons(exe, tmp_path, language, font, text, animation):
    """Chinese, Japanese and Thai are written without spaces: the caption is
    one word libass cannot break, and it ran under TikTok's column. It is
    drawn smaller, like a long English word."""
    from video import caption_fit, captions

    if caption_fit.font(font) is None:
        pytest.skip(f"needs Windows' {font} to measure")
    segments = [Segment(start=0.0, end=1.0, text=text, words=[])]
    column = FULL_W - captions.SAFE_ZONES["tiktok"]["right"]
    for size in (84, 120):
        folder = tmp_path / str(size)
        folder.mkdir()
        ass = build_captions(segments, ClipCandidate(start=0.0, end=1.0, score=0), folder / "clip.ass",
                             style={"font": "Anton", "font_size": size, "safe_zone": "tiktok", "animation": animation},
                             lines=[{"start": 0.0, "end": 1.0, "text": text}], canvas=(FULL_W, FULL_H),
                             language=language)
        out = ass.read_text(encoding="utf-8")
        assert f"Style: Default,{font}," in out and "\\fscx" in out
        rights = [_ink_right(f) for f in _full_frames(exe, ass, 0.3, fps=25)]
        shown = [r for r in rights if r >= 0]
        assert shown and all(r < column for r in shown), (size, column, rights)


# ---- QC fix round 4 --------------------------------------------------------------------------


def test_a_doubled_space_does_not_flash_to_two_lines_at_bounces_peak(exe, tmp_path):
    """THIS GAME  JUST with a doubled space (a typing slip in the caption
    editor) was one space wider than the text the guard measured, and went
    to two lines for two frames at bounce's 110%."""
    style = {"font": "Titan One", "font_size": 86, "safe_zone": "tiktok", "animation": "bounce"}
    frames = _full_frames(exe, _full(tmp_path, style, "THIS GAME  JUST"), 0.3, fps=100)
    rows = [_full_rows(f) for f in frames]
    assert rows == [1] * len(rows), rows


@pytest.mark.parametrize("animation", ["none", "bounce", "slide"])
@pytest.mark.parametrize("joiner", ["no-break space", "narrow no-break space"])
def test_words_joined_by_a_no_break_space_stay_clear_of_the_buttons(exe, tmp_path, joiner, animation):
    """libass never breaks at a no-break space: ABSOLUTELY and UNREAL
    joined by one were a single line 69 px into TikTok's column."""
    from video import captions

    gap = chr(0xA0) if joiner == "no-break space" else chr(0x202F)
    style = {"font": "Titan One", "font_size": 86, "safe_zone": "tiktok", "animation": animation}
    column = FULL_W - captions.SAFE_ZONES["tiktok"]["right"]
    frames = _full_frames(exe, _full(tmp_path, style, f"ABSOLUTELY{gap}UNREAL"), 0.3)
    rights = [_ink_right(f) for f in frames]
    assert rights and all(0 < r < column for r in rights), (column, rights)
    assert _full_rows(frames[-1]) == 2
