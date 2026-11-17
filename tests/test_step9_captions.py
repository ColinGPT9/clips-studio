"""Editor suite step 9, part A: captions and text as a layer you can drag.
The engine side, standard library only (CI light install):

- E1: a caption style's optional x and y (the caption's centre in % of the
  frame) put every caption there with \\an5\\pos, kept inside the frame and
  the chosen app's safe area; slide rises into that point; the highlight,
  karaoke and the second speaker's colour still work. A style without x and
  y is placed by `position` exactly as before (the step-0 and step-7
  snapshots hold the bytes; tests/test_compose_plan.py and
  tests/test_render_argv_snapshots.py).
- E2: a text item's position {x, y}.
- The checks on both (env.AppEnv.caption_style, ops add_text / set_text).
- E3: the caption lane's lines in program time (compose.caption_lane) are
  the export's own lines, after cuts, speed and reordering; program to
  source times (compose.caption_source_range) never land a frame off.
- E4: an export with the captions not burned keeps text; the .srt's lines
  are the burn's, within a frame.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from test_compose_plan import ENV, SEGMENTS, WORDS, apply, manual, plan_for

from core.models import ClipCandidate, Segment
from video import captions as cap
from video_editor import compose
from video_editor.project import env as project_env
from video_editor.project import model, ops

POS = re.compile(r"\\pos\((-?\d+),(-?\d+)\)")
MOVE = re.compile(r"\\move\((-?\d+),(-?\d+),(-?\d+),(-?\d+),")
FONT = "Anton"          # a bundled font: measured the same on every platform

SPOKEN = [{"start": 0.1 + i * 0.3, "end": 0.35 + i * 0.3, "word": w}
          for i, w in enumerate("one two three four five six".split())]


def ass(style: dict, canvas=(1080, 1920), lines=None, language="en") -> str:
    segs = [Segment(start=0.0, end=3.0, text=" ".join(w["word"] for w in SPOKEN), words=SPOKEN)]
    return cap.caption_ass(segs, ClipCandidate(start=0.0, end=3.0, score=1), style=style, lines=lines,
                           canvas=canvas, language=language)


def events(text: str) -> list[str]:
    return [line for line in text.splitlines() if line.startswith("Dialogue:")]


def style_line(text: str) -> list[str]:
    line = next(line for line in text.splitlines() if line.startswith("Style: Default,"))
    return line.split(",")


def points(text: str) -> set[tuple[int, int]]:
    return {(int(m.group(1)), int(m.group(2))) for m in POS.finditer(text)}


# ---- E1: the caption style's x and y -------------------------------------------------------------


@pytest.mark.parametrize("given", [None, {}, {"x": 50}, {"y": 50}, {"x": True, "y": 50}, {"x": "50", "y": 50},
                                   {"x": float("nan"), "y": 50}, {"x": None, "y": None}])
def test_without_both_numbers_a_caption_is_placed_by_position(given):
    assert cap.place_of(given) is None
    style = {"font": FONT, "position": "top", **(given or {})}
    text = ass(style)
    assert "\\pos(" not in text and "\\an5" not in text
    assert style_line(text)[18] == "8"        # top, as always


def test_x_and_y_are_not_default_style_keys():
    assert "x" not in cap.DEFAULT_STYLE and "y" not in cap.DEFAULT_STYLE
    assert cap.PLACE_KEYS == ("x", "y")


@pytest.mark.parametrize("canvas,x,y,want", [((1080, 1920), 50, 70, (540, 1344)),
                                             ((1080, 1920), 50, 50, (540, 960)),
                                             ((1920, 1080), 50, 50, (960, 540)),
                                             ((1080, 1080), 40, 30, (432, 324))])
def test_x_and_y_put_every_caption_centre_there(canvas, x, y, want):
    text = ass({"font": FONT, "x": x, "y": y, "position": "top"}, canvas)
    assert style_line(text)[18] == "5" and style_line(text)[21] == "0"     # alignment 5, no vertical margin
    found = events(text)
    assert found and all(f"{{\\an5\\pos({want[0]},{want[1]})}}" in e for e in found)


def test_a_caption_is_kept_inside_the_frame():
    """Dragged into a corner, each caption moves in only as far as its own
    size needs: a short one sits nearer the edge than a long one."""
    for x, y in ((0, 0), (100, 100), (0, 100), (100, 0)):
        style = {"font": FONT, "x": x, "y": y}
        text = ass(style)
        left, top, right, bottom = cap._place_bounds({**cap.DEFAULT_STYLE, **style}, (1080, 1920))
        for event in events(text):
            px, py = map(int, POS.search(event).groups())
            words = event.split("}", 1)[1]
            w, h = cap._caption_box(words, {**cap.DEFAULT_STYLE, **style}, (1080, 1920), None)
            assert left <= px - w / 2 + 1 and px + w / 2 - 1 <= right, (x, y, event)
            assert top <= py - h / 2 + 1 and py + h / 2 - 1 <= bottom, (x, y, event)
            assert 0 < px < 1080 and 0 < py < 1920
    # Moved in by its size, not to the middle: far from the centre still.
    near = points(ass({"font": FONT, "x": 0, "y": 0}))
    assert all(px < 400 and py < 200 for px, py in near)


def test_a_safe_zone_keeps_a_dragged_caption_off_the_apps_bars_and_buttons():
    style = {"font": FONT, "x": 100, "y": 100, "safe_zone": "tiktok"}
    text = ass(style)
    zone = cap.SAFE_ZONES["tiktok"]
    for event in events(text):
        px, py = map(int, POS.search(event).groups())
        words = event.split("}", 1)[1]
        w, h = cap._caption_box(words, {**cap.DEFAULT_STYLE, **style}, (1080, 1920), None)
        assert py + h / 2 <= 1920 - zone["bottom"] - cap._SAFE_CLEARANCE + 1
        assert px + w / 2 <= 1080 - zone["right"] + 1
    top = points(ass({"font": FONT, "x": 50, "y": 0, "safe_zone": "reels"}))
    assert all(py >= cap.SAFE_ZONES["reels"]["top"] + cap._SAFE_CLEARANCE for _, py in top)
    # Landscape: no app bars, so no safe zone (as for any caption).
    wide = points(ass({"font": FONT, "x": 50, "y": 50, "safe_zone": "tiktok"}, (1920, 1080)))
    assert wide == {(960, 540)}


def test_slide_rises_into_the_dragged_point():
    text = ass({"font": FONT, "x": 30, "y": 40, "animation": "slide", "highlight": True})
    moves = [tuple(map(int, m.groups())) for m in MOVE.finditer(text)]
    assert moves
    at = points(text)
    for x1, y1, x2, y2 in moves:
        assert (x2, y2) in at and x1 == x2 and y1 == y2 + cap._SLIDE_PX
    for event in events(text):
        assert "\\an5" in event
        assert ("\\move(" in event) != ("\\pos(" in event)       # never both on one event


def test_highlight_karaoke_and_the_second_speaker_still_work():
    lines = [{"start": 0.1, "end": 0.9, "text": "one two three"},
             {"start": 1.0, "end": 1.9, "text": "four five six", "speaker": 1}]
    hot = cap._inline_color(cap.DEFAULT_STYLE["highlight_color"])
    other = cap._inline_color(cap.DEFAULT_STYLE["second_speaker_color"])
    lit = ass({"font": FONT, "x": 50, "y": 75, "highlight": True, "second_speaker": True}, lines=lines)
    plain = ass({"font": FONT, "highlight": True, "second_speaker": True}, lines=lines)
    assert len(events(lit)) == len(events(plain)) == 6 and hot in lit and other in lit
    # Every event is the plain one with the point added in front, nothing else changed.
    for a, b in zip(events(lit), events(plain)):
        assert POS.sub("", a.replace("\\an5", "")).replace("{}", "") == b
    sung = ass({"font": FONT, "x": 50, "y": 75, "animation": "karaoke"}, lines=lines)
    assert "\\kf" in sung and all("\\an5\\pos(" in e for e in events(sung))


@pytest.mark.parametrize("anim", cap.ANIMATIONS)
def test_every_animation_with_x_and_y_writes_one_point_per_caption(anim):
    lines = [{"start": 0.1, "end": 0.9, "text": "one two three"}, {"start": 1.0, "end": 1.9, "text": "four"}]
    text = ass({"font": FONT, "x": 50, "y": 60, "animation": anim}, lines=lines)
    assert all("\\an5" in e for e in events(text))
    ends = points(text) | {(int(m.group(3)), int(m.group(4))) for m in MOVE.finditer(text)}
    assert ends and all(px == 540 for px, _ in ends)


# ---- E1 and E2: what the project accepts ------------------------------------------------------------


@pytest.mark.parametrize("style", [{"x": 0, "y": 100}, {"x": 33.5, "y": 70.25}, {"x": 50, "y": 50, "font_size": 90}])
def test_a_caption_style_takes_x_and_y(style):
    assert project_env.AppEnv().caption_style(style) == style


def test_the_old_highlights_post_style_keys_are_dropped_as_the_render_drops_them():
    # 9-D16: a clip saved with the removed post style's keys can still be
    # dragged and restyled; the project keeps the person's own caption look.
    from video_editor.project import importer

    old = {"font": "Arial", "font_size": 84, "post_style": "card", "card_position": "top"}
    assert project_env.AppEnv().caption_style({**old, "x": 50, "y": 70}) == \
        {"font": "Arial", "font_size": 84, "x": 50, "y": 70}
    doc = importer.import_clip({"video_id": "v", "start": 0, "end": 9, "render_opts": {"caption_style": old}})
    assert doc["captions"]["style"] == {"font": "Arial", "font_size": 84}
    # The same names today's render drops.
    pipeline = (Path(__file__).resolve().parent.parent / "core" / "pipeline.py").read_text(encoding="utf-8")
    said = re.search(r"^_OLD_STYLE_KEYS = \((.*)\)$", pipeline, re.MULTILINE)
    assert said and tuple(re.findall(r'"([^"]+)"', said.group(1))) == importer.OLD_STYLE_KEYS


def test_null_x_and_y_leave_the_place_out():
    assert project_env.AppEnv().caption_style({"x": None, "y": None, "font_size": 90}) == {"font_size": 90}


@pytest.mark.parametrize("style,said", [({"x": 50}, "go together"), ({"y": 50, "x": None}, "go together"),
                                         ({"x": 101, "y": 5}, "0 to 100"), ({"x": -1, "y": 5}, "0 to 100"),
                                         ({"x": True, "y": 5}, "number"), ({"x": "50", "y": 5}, "number"),
                                         ({"x": float("inf"), "y": 5}, "number"), ({"z": 1}, "x, y")])
def test_a_caption_style_refuses_a_bad_place(style, said):
    with pytest.raises(ValueError, match=said):
        project_env.AppEnv().caption_style(style)


def text_doc() -> dict:
    return apply(manual((10, 14, 1.0)), {"op": "add_track", "kind": "text"},
                 {"op": "add_text", "content": "Hello", "start_us": 0, "duration_us": 2_000_000,
                  "position": {"x": 25, "y": 80}})


def text_item(doc: dict) -> dict:
    return model.track(doc, "T1")["items"][0]


def test_a_text_takes_a_place_and_goes_back_to_a_named_one():
    doc = text_doc()
    item = text_item(doc)
    assert item["text"]["position"] == {"x": 25, "y": 80}
    doc = apply(doc, {"op": "set_text", "item_id": item["id"], "position": {"y": 10.5, "x": 90}})
    assert text_item(doc)["text"]["position"] == {"x": 90, "y": 10.5}
    doc = apply(doc, {"op": "set_text", "item_id": item["id"], "position": "middle"})
    assert text_item(doc)["text"]["position"] == "middle"
    doc = apply(doc, {"op": "set_text", "item_id": item["id"], "position": None})
    assert "position" not in text_item(doc)["text"]
    assert model.validate(doc) == []


@pytest.mark.parametrize("bad", [{"x": 50}, {"x": 50, "y": 50, "z": 1}, {"x": 101, "y": 5}, {"x": True, "y": 5},
                                 {"x": "5", "y": 5}, [50, 50], "left", 3])
def test_a_text_refuses_a_bad_place(bad):
    doc = text_doc()
    with pytest.raises(ops.OpError) as e:
        ops.apply(doc, [{"op": "set_text", "item_id": text_item(doc)["id"], "position": bad}], ENV)
    assert e.value.field == "position"


def test_a_text_with_a_place_is_drawn_centred_there():
    doc = text_doc()
    text, _fonts = compose.build_ass(doc, compose.Inputs(work=Path("unused"), media={}), (1080, 1920))
    event = next(line for line in text.splitlines() if line.startswith("Dialogue: 1,"))
    assert event.endswith(",{\\an5\\pos(270,1536)}Hello")
    style = next(line for line in text.splitlines() if line.startswith("Style: Text"))
    assert style.split(",")[18] == "5"
    # On a 16:9 canvas the same % is the same place on that frame.
    wide, _ = compose.build_ass(doc, compose.Inputs(work=Path("unused"), media={}), (1920, 1080))
    assert "{\\an5\\pos(480,864)}Hello" in wide


def test_a_text_without_a_place_is_drawn_as_before():
    doc = apply(manual((10, 14, 1.0)), {"op": "add_track", "kind": "text"},
                {"op": "add_text", "content": "Hello", "start_us": 0, "duration_us": 2_000_000, "position": "bottom"})
    text, _ = compose.build_ass(doc, compose.Inputs(work=Path("unused"), media={}), (1080, 1920))
    assert "\\pos" not in text
    assert ",2,60,60,440,1" in text      # bottom, as TEXT_POSITIONS has it


# ---- E3: the caption lane in program time ----------------------------------------------------------


def captioned(*spans, **style) -> dict:
    return apply(manual(*spans), {"op": "set_captions", "enabled": True, "media_id": "m1",
                                  **({"style": style} if style else {})})


def inputs(**kw) -> compose.Inputs:
    return compose.Inputs(work=Path("unused"), media={}, segments=SEGMENTS, **kw)


def lane_pieces(lane: dict) -> list[dict]:
    pieces = [p for line in lane["lines"] for p in line["pieces"]]
    return sorted(pieces, key=lambda p: (p["start_us"], p["end_us"]))


@pytest.mark.parametrize("spans", [((10, 12, 1.0), (12.5, 14, 2.0), (20, 21.2, 0.5)),
                                   ((20, 22, 1.0), (10, 11.5, 1.0)),                    # reordered
                                   ((10, 11.5, 1.0), (10, 11.5, 1.0)),                  # played twice
                                   ((10, 24, 1.0),)])
def test_the_lane_shows_exactly_the_lines_the_export_burns(spans):
    doc = captioned(*spans)
    lane = compose.caption_lane(doc, inputs())
    burned, _spoken = compose.program_caption_lines(doc, inputs())
    assert [(round(b["start"] * 1e6), round(b["end"] * 1e6), b["text"]) for b in burned] == \
        [(p["start_us"], p["end_us"], p["text"]) for p in lane_pieces(lane)]
    assert lane["saved"] is False and lane["burned"] is True
    assert all(line["id"] == f"tx{i}" for i, line in enumerate(lane["lines"]))
    assert [(s["start_us"], s["end_us"]) for s in lane["spans"]] == \
        [(s.start, s.end) for s in compose.caption_spans(doc)]


def test_a_line_cut_out_is_hidden_and_one_cut_through_has_a_piece_each_side():
    lines = [{"in_us": 10_200_000, "out_us": 11_400_000, "text": "one two three"},
             {"in_us": 12_100_000, "out_us": 12_600_000, "text": "four"},
             {"in_us": 20_200_000, "out_us": 21_100_000, "text": "six seven"}]
    doc = apply(manual((10, 10.8, 1.0), (11.0, 12.0, 1.0), (20, 22, 1.0)),
                {"op": "set_captions", "enabled": True, "media_id": "m1", "lines": lines})
    lane = compose.caption_lane(doc, inputs())
    by_text = {line["text"]: line for line in lane["lines"]}
    assert lane["saved"] is True
    assert by_text["four"]["hidden"] is True and by_text["four"]["pieces"] == []
    split = by_text["one two three"]["pieces"]
    assert [p["text"] for p in split] == ["one two", "three"]
    assert by_text["six seven"]["hidden"] is False
    assert [line["id"] for line in lane["lines"]] == [line["id"] for line in doc["captions"]["lines"]]
    assert by_text["six seven"]["in_us"] == 20_200_000


def test_saving_the_transcripts_lines_with_their_lane_ids_keeps_them():
    doc = captioned((10, 24, 1.0))
    lane = compose.caption_lane(doc, inputs())
    saved = apply(doc, {"op": "set_captions", "lines": [
        {"id": line["id"], "in_us": line["in_us"], "out_us": line["out_us"], "text": line["text"]}
        for line in lane["lines"]]})
    again = compose.caption_lane(saved, inputs())
    assert again["saved"] is True
    assert [line["id"] for line in again["lines"]] == [line["id"] for line in lane["lines"]]
    assert lane_pieces(again) == lane_pieces(lane)


def _saved(spans, lines) -> dict:
    return apply(manual(*spans), {"op": "set_captions", "enabled": True, "media_id": "m1", "lines": lines})


def _shown(doc) -> list[tuple[int, int, str]]:
    burned, _spoken = compose.program_caption_lines(doc, inputs())
    return [(round(b["start"] * 1e6), round(b["end"] * 1e6), b["text"]) for b in burned]


def test_a_line_over_a_cut_keeps_every_word_that_plays():
    # 9-D5: "two three four" (10.6-12.6 s) cut out; a line merged across the
    # cut, "one five" (10.2-13.95 s), has words the transcript says around it
    # only in the parts that play, so it keeps both, one each side.
    doc = _saved([(10, 10.55, 1.0), (13.5, 14, 1.0)], [{"in_us": 10_200_000, "out_us": 13_950_000, "text": "one five"}])
    assert [t for _, _, t in _shown(doc)] == ["one", "five"]
    # Words the transcript doesn't line up with are shared out over the parts
    # that play only: none falls in the cut and vanishes.
    doc = _saved([(10, 10.55, 1.0), (13.5, 14, 1.0)], [{"in_us": 10_200_000, "out_us": 13_950_000,
                                                          "text": "we did it whole"}])
    assert " ".join(t for _, _, t in _shown(doc)) == "we did it whole"


def test_a_line_an_edit_cut_off_shows_what_the_lane_saves():
    # 9-D6: a line saved as the part that plays (as the lane now sends a
    # retime, a move, a split or a fix of a line an edit cut off) burns its
    # words there, also when it is shorter than 0.05 s or a cut-out word sits
    # just outside it.
    doc = _saved([(10, 11.2, 1.0), (20, 22, 1.0)], [{"in_us": 11_000_000, "out_us": 11_200_000, "text": "three"}])
    assert _shown(doc) == [(1_000_000, 1_200_000, "three")]
    doc = _saved([(10, 11.2, 1.0), (20, 22, 1.0)], [{"in_us": 11_166_667, "out_us": 11_200_000, "text": "three"}])
    assert _shown(doc) == [(1_166_667, 1_200_000, "three")]
    # "two" (10.6-10.9 s) is cut out and its middle is 0.005 s before the line:
    # it no longer takes "three"'s place.
    doc = _saved([(10, 10.5, 1.0), (10.755, 12, 1.0)], [{"in_us": 10_755_000, "out_us": 11_400_000, "text": "three"}])
    assert [t for _, _, t in _shown(doc)] == ["three"]


def test_a_cut_through_a_transcript_line_still_drops_the_words_cut_out():
    # The old rule holds: a line whose words line up with the transcript loses the cut-out ones.
    doc = _saved([(10, 10.55, 1.0), (10.95, 14, 1.0)], [{"in_us": 10_200_000, "out_us": 11_400_000,
                                                          "text": "one two three"}])
    assert [t for _, _, t in _shown(doc)] == ["one", "three"]


@pytest.mark.parametrize("typed", ["one more two three", "one two three now", "One, two! three", "one three",
                                   "one tree"])
def test_a_word_fix_never_brings_back_a_word_cut_out(typed):
    # 9-D14: "two" is cut out of "one two three". Whatever the person types for
    # the line (more or fewer words, the cut word kept or not), it stays out.
    doc = _saved([(10, 10.55, 1.0), (10.95, 14, 1.0)], [{"in_us": 10_200_000, "out_us": 11_400_000,
                                                          "text": typed}])
    shown = " ".join(t for _, _, t in _shown(doc)).lower().split()
    assert "two" not in [w.strip(",!") for w in shown]
    kept = [w for w in typed.split() if w.strip(",!").lower() != "two"]
    assert " ".join(t for _, _, t in _shown(doc)).split() == kept


@pytest.mark.parametrize("speed", [0.25, 0.5, 1.0, 1.5, 2.0, 4.0])
@pytest.mark.parametrize("fps", [30, 29.97, 23.976, 60])
def test_a_block_put_on_the_lane_never_lands_a_frame_off(speed, fps):
    doc = manual((10, 13, speed), (20, 22, 1.0), fps=fps)
    doc = apply(doc, {"op": "set_captions", "enabled": True, "media_id": "m1"})
    rate = doc["canvas"]["fps"]
    span = compose.caption_spans(doc)[0]
    for n in range(0, model.frames_at(span.end - span.start, rate) - 3, 7):
        a, b = model.frame_us(n, rate), model.frame_us(n + 3, rate)
        found = compose.caption_source_range(doc, a, b)
        assert found is not None
        back = (span.program(found[0] / model.US) * model.US, span.program(found[1] / model.US) * model.US)
        # Its start never before the frame it was put on, its end never
        # after: the same frames, at any speed (less than one source
        # microsecond apart, 1 / speed in the program).
        step = 1 / speed + 1e-6
        assert a - 1e-6 <= back[0] < a + step and b - step < back[1] <= b + 1e-6, (n, found, back)


def test_a_block_never_reaches_across_a_cut():
    doc = captioned((10, 12, 1.0), (20, 22, 1.0))
    # From 1.5 s to 3 s in the program: the end is held at the first stretch's end (2 s).
    assert compose.caption_source_range(doc, 1_500_000, 3_000_000) == (11_500_000, 12_000_000)
    assert compose.caption_source_range(doc, 2_500_000, 3_000_000) == (20_500_000, 21_000_000)
    assert compose.caption_source_range(doc, 4_000_000, 4_500_000) is None       # past the end


def test_no_captions_media_means_no_lane_and_no_mapping():
    doc = apply(manual((10, 12, 1.0)), {"op": "set_captions", "enabled": True, "media_id": "m2"})   # the picture
    assert compose.caption_lane(doc, inputs())["lines"] == []
    assert compose.caption_source_range(doc, 0, 500_000) is None


# ---- E4: captions not burned, and the .srt -----------------------------------------------------------


def _script(plan) -> str:
    return next(v for stage in plan.stages for k, v in stage.scripts.items() if k.endswith(compose.ASS_NAME))


def test_captions_none_burns_the_text_but_not_the_captions(tmp_path):
    doc = captioned((10, 12, 1.0), (20, 21.2, 1.0), highlight=True)
    doc = apply(doc, {"op": "add_track", "kind": "text"},
                {"op": "add_text", "content": "Watch this", "start_us": 0, "duration_us": 1_000_000})
    burned = plan_for(doc, tmp_path, segments=SEGMENTS)
    assert any(line.startswith("Dialogue: 0,") for line in _script(burned).splitlines())
    inputs_off = compose.Inputs(work=tmp_path / "work", media={}, segments=SEGMENTS, burn_captions=False)
    text, _ = compose.build_ass(doc, inputs_off, (1080, 1920))
    assert "Watch this" in text and not any(line.startswith("Dialogue: 0,") for line in text.splitlines())
    # Only the captions go: without text, nothing is burned at all.
    plain = captioned((10, 12, 1.0))
    assert compose.build_ass(plain, compose.Inputs(work=tmp_path, media={}, segments=SEGMENTS,
                                                   burn_captions=False), (1080, 1920)) == (None, [])


def _ass_seconds(stamp: str) -> float:
    h, m, s = stamp.split(":")
    return int(h) * 3600 + int(m) * 60 + float(s)


def _srt_seconds(stamp: str) -> float:
    h, m, rest = stamp.split(":")
    s, ms = rest.split(",")
    return int(h) * 3600 + int(m) * 60 + int(s) + int(ms) / 1000


def test_the_srt_has_the_burns_lines_within_a_frame(tmp_path):
    from multilingual import subtitles

    doc = captioned((10, 12, 1.0), (12.5, 14, 2.0), (20, 21.2, 0.5))
    lines, _ = compose.program_caption_lines(doc, inputs())
    path = subtitles.write_srt([{"start": round(line["start"], 3), "end": round(line["end"], 3),
                                 "text": line["text"]} for line in lines], tmp_path / "x.srt")
    blocks = [b.splitlines() for b in path.read_text(encoding="utf-8").strip().split("\n\n")]
    text, _ = compose.build_ass(doc, inputs(), (1080, 1920))
    burned = [line.split(",", 9) for line in text.splitlines() if line.startswith("Dialogue: 0,")]
    assert len(blocks) == len(burned) == len(lines) > 2
    frame = 1 / 30
    for block, event in zip(blocks, burned):
        start, end = (_srt_seconds(t) for t in block[1].split(" --> "))
        assert abs(start - _ass_seconds(event[1])) < frame and abs(end - _ass_seconds(event[2])) < frame
        assert block[2].upper() == event[9].upper()
    assert WORDS  # the transcript the plan snapshots use
