"""Step 3, the last round of caption fixes:

- the caption style's size and bottom margin are worked out in the order they
  always were, so no frame height comes out 1 px different;
- karaoke is not written for right-to-left languages (libass would put the
  words in reverse order);
- karaoke on a clip that was cut or sped up spreads each line's fill over the
  line, instead of using word times from the unedited clip;
- a staged font's temporary copy has a short name, so a long clip folder
  keeps its font.
"""

from __future__ import annotations

import re
import shutil
from pathlib import Path

import pytest

from core.models import ClipCandidate, Segment
from video import captions, fonts
from video.captions import DEFAULT_STYLE, build_captions

WORDS = [
    {"start": 10.0, "end": 10.3, "word": "this"}, {"start": 10.3, "end": 10.5, "word": "is"},
    {"start": 10.7, "end": 11.2, "word": "huge"}, {"start": 11.2, "end": 11.6, "word": "right"},
    {"start": 11.6, "end": 11.9, "word": "now"}, {"start": 11.9, "end": 12.5, "word": "guys"},
]
SEGMENTS = [Segment(start=10.0, end=12.5, text="this is huge right now guys", words=WORDS)]
WINDOW = ClipCandidate(start=10.0, end=12.5, score=0)


def _events(path: Path) -> list[str]:
    return [line for line in path.read_text(encoding="utf-8").splitlines() if line.startswith("Dialogue:")]


def test_the_style_size_and_margin_round_as_they_always_did():
    for height in range(100, 4401):
        scale = height / 1920
        canvas = (round(height * 9 / 16), height)
        for size in (40, 60, 85, 90, 95, 110, 140):
            assert captions._style_size({**DEFAULT_STYLE, "font_size": size}, canvas) == max(24, round(size * scale))
        assert captions._layout(dict(DEFAULT_STYLE), canvas)[3] == round(440 * scale)
    # One of the pairs the other order of operations got wrong.
    assert captions._style_size({**DEFAULT_STYLE, "font_size": 110}, (594, 1056)) == 61


@pytest.mark.parametrize("language", ["ar", "he", "fa", "ur", "ar-SA", "AR"])
def test_karaoke_is_burned_plain_for_a_right_to_left_language(tmp_path, language):
    karaoke = build_captions(SEGMENTS, WINDOW, tmp_path / "k.ass", language=language,
                             style={"animation": "karaoke", "highlight": True})
    plain = build_captions(SEGMENTS, WINDOW, tmp_path / "p.ass", language=language,
                           style={"animation": "none", "highlight": False})
    assert "\\k" not in karaoke.read_text(encoding="utf-8")
    assert karaoke.read_text(encoding="utf-8") == plain.read_text(encoding="utf-8")


def test_karaoke_still_fills_words_for_a_left_to_right_language(tmp_path):
    path = build_captions(SEGMENTS, WINDOW, tmp_path / "k.ass", language="fr", style={"animation": "karaoke"})
    assert "\\kf" in path.read_text(encoding="utf-8")


def _fill_starts(event: str) -> tuple[list[int], int, bool]:
    body = event.split("Default,,0,0,0,,", 1)[1]
    at, starts, paused = 0, [], False
    for tag, hundredths in re.findall(r"\{\\(kf|k)(\d+)\}", body):
        if tag == "kf":
            starts.append(at)
        else:
            paused = True
        at += int(hundredths)
    return starts, at, paused


def test_karaoke_on_a_retimed_clip_spreads_the_fill_over_the_line(tmp_path):
    lines = [{"start": 0.0, "end": 2.5, "text": "this is huge right now guys"}]
    timed = build_captions(SEGMENTS, WINDOW, tmp_path / "t.ass", lines=lines, style={"animation": "karaoke"})
    untimed = build_captions(SEGMENTS, WINDOW, tmp_path / "u.ass", lines=lines,
                             style={"animation": "karaoke"}, karaoke_untimed=True)
    starts, total, paused = _fill_starts(_events(untimed)[0])
    assert _fill_starts(_events(timed)[0])[0] == [round((w["start"] - 10.0) * 100) for w in WORDS]
    assert total == 250 and not paused
    # By word length: 4, 2, 4, 5, 3, 4 letters of 22 over 2.5 s.
    assert starts == pytest.approx([0, 45, 68, 114, 170, 205], abs=1)


def test_the_flag_changes_nothing_but_karaoke(tmp_path):
    for i, style in enumerate([None, {"highlight": True}, {"animation": "pop"}, {"animation": "fade", "highlight": True}]):
        a = build_captions(SEGMENTS, WINDOW, tmp_path / f"a{i}.ass", style=style)
        b = build_captions(SEGMENTS, WINDOW, tmp_path / f"b{i}.ass", style=style, karaoke_untimed=True)
        assert a.read_text(encoding="utf-8") == b.read_text(encoding="utf-8")


def test_a_staged_fonts_temporary_copy_has_a_short_name(tmp_path, monkeypatch):
    family = next((f for f in fonts.families() if fonts.usable(f)), None)
    if family is None:
        pytest.skip("no bundled fonts in this checkout")
    seen = []
    real = shutil.copyfile

    def copy(src, dst, *args, **kwargs):
        seen.append((Path(src).name, Path(dst).name))
        return real(src, dst, *args, **kwargs)

    monkeypatch.setattr(fonts.shutil, "copyfile", copy)
    assert fonts.stage([family], tmp_path) == fonts.STAGED
    assert seen
    for final, temporary in seen:
        # Never longer than the font's own file name, so staging needs no
        # longer path than the staged file itself.
        assert temporary.startswith("~") and temporary.endswith(".tmp")
        assert len(temporary) <= len(final)
    left = sorted(p.name for p in (tmp_path / fonts.STAGED).iterdir())
    assert not [n for n in left if n.endswith(".tmp")]
