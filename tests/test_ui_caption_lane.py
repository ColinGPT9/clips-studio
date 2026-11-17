"""The caption lane, the transcript list, the caption and text panels and
"+ Text" of the new editor (editor suite step 9, part C;
docs/editor-suite/api/step9-captions.md):

- lib/captionLane.ts, run under Node (skipped without it): the lane's blocks
  are the lines that play, an edit of lines made from the transcript saves
  them first with their ids, a dragged block stays between its neighbours and
  inside its stretch of video, split / merge / add / delete give the lines
  step 5's operations take, the list shows the lane's lines, and the first
  caption of a project made by hand maps to source times exactly as the
  engine's own compose.caption_source_range does;
- the sources: every lane action is one change through the project's
  `apply` (one undo step), a dropped block's times go through the engine,
  S and Delete act on a selected caption line, the inspector's caption panel
  is step 3's controls sending the whole style, the text panel replaces the
  "later version" line, "+ Text" adds a centred 3 s text, and the lane's Lock
  reaches the viewer."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "ui" / "src" / "renderer" / "src"
LIB = SRC / "lib"
EDITOR = SRC / "components" / "editor"


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _code(source: str) -> str:
    source = re.sub(r"/\*.*?\*/", "", source, flags=re.DOTALL)
    return re.sub(r"(?m)^\s*//.*$", "", source)


def _between(source: str, start: str, end: str) -> str:
    return source[source.index(start):source.index(end, source.index(start))]


def _node(tmp_path, body: str):
    """Run `body` (JavaScript, with `m` the module and `out` what it prints)
    against lib/captionLane.ts."""
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node isn't available")
    (tmp_path / "captionLane.ts").write_text(read(LIB / "captionLane.ts"), encoding="utf-8")
    script = (f"const m = await import({json.dumps((tmp_path / 'captionLane.ts').as_uri())});"
              f"let out; {body}; console.log(JSON.stringify(out));")
    r = subprocess.run([node, "--experimental-strip-types", "--no-warnings", "--input-type=module", "-e", script],
                       capture_output=True, text=True, timeout=60)
    if r.returncode != 0 and "strip-types" in r.stderr:
        pytest.skip("this Node can't run TypeScript directly")
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


def line(id_: str, in_s: float, out_s: float, text: str, pieces: list[tuple[float, float]] | None = None) -> dict:
    """A lane line (GET /projects/{id}/captions), times in seconds; by default
    it plays where it is in the source (one piece)."""
    pieces = [(in_s, out_s)] if pieces is None else pieces
    return {"id": id_, "in_us": round(in_s * 1e6), "out_us": round(out_s * 1e6), "text": text, "speaker": 0,
            "pieces": [{"start_us": round(a * 1e6), "end_us": round(b * 1e6), "text": text} for a, b in pieces],
            "hidden": not pieces}


LINES = [line("a", 0.0, 1.0, "one two"), line("b", 1.0, 2.0, "three four five"), line("c", 3.0, 4.0, "six")]
SPAN = [{"src_in_us": 0, "src_out_us": 10_000_000, "start_us": 0, "end_us": 10_000_000, "speed": 1.0}]


# ---- lib/captionLane.ts ---------------------------------------------------------------------------


def test_the_lane_shows_one_block_per_piece_that_plays(tmp_path):
    lines = [line("late", 5.0, 6.0, "later"), line("cut", 2.0, 3.0, "all cut", pieces=[]),
             line("two", 1.0, 4.0, "in two parts", pieces=[(3.5, 4.0), (1.0, 1.5)])]
    got = _node(tmp_path, f"out = m.blocksOf({json.dumps(lines)})")
    # In program order; a line cut out has none; a line cut through has a block per part.
    assert [(b["id"], b["piece"], b["pieces"], b["start"]) for b in got] == [
        ("two", 0, 2, 1_000_000), ("two", 1, 2, 3_500_000), ("late", 0, 1, 5_000_000)]


def test_an_edit_of_lines_made_from_the_transcript_saves_them_first_with_their_ids(tmp_path):
    edit = {"op": "edit_caption_words", "edits": [{"line_id": "b", "text": "x"}]}
    got = _node(tmp_path, f"out = [m.withSave(false, {json.dumps(LINES)}, [{json.dumps(edit)}]),"
                          f" m.withSave(true, {json.dumps(LINES)}, [{json.dumps(edit)}])]")
    unsaved, saved = got
    assert unsaved[0] == {"op": "set_captions", "lines": [
        {"id": "a", "in_us": 0, "out_us": 1_000_000, "text": "one two"},
        {"id": "b", "in_us": 1_000_000, "out_us": 2_000_000, "text": "three four five"},
        {"id": "c", "in_us": 3_000_000, "out_us": 4_000_000, "text": "six"}]}
    assert unsaved[1:] == [edit] and saved == [edit]


def test_a_dragged_block_stays_between_its_neighbours_and_inside_its_stretch(tmp_path):
    spans = [{"src_in_us": 0, "src_out_us": 3_500_000, "start_us": 0, "end_us": 3_500_000, "speed": 1.0},
             {"src_in_us": 9_000_000, "src_out_us": 12_000_000, "start_us": 3_500_000, "end_us": 6_500_000, "speed": 1.0}]
    got = _node(tmp_path, f"""
        const blocks = m.blocksOf({json.dumps(LINES)});
        const b = blocks[1], c = blocks[2];
        const none = () => null;
        out = {{
          room: m.room(blocks, {json.dumps(spans)}, b),
          roomC: m.room(blocks, {json.dumps(spans)}, c),
          moveLeft: m.dragBlock(b, 'move', -500000, m.room(blocks, {json.dumps(spans)}, b), 33333, none),
          moveRight: m.dragBlock(b, 'move', 600000, m.room(blocks, {json.dumps(spans)}, b), 33333, none),
          rightPastCut: m.dragBlock(c, 'right', 2000000, m.room(blocks, {json.dumps(spans)}, c), 33333, none),
          leftTooFar: m.dragBlock(c, 'left', 1500000, m.room(blocks, {json.dumps(spans)}, c), 33333, none),
          snapped: m.dragBlock(b, 'move', 950000, m.room(blocks, {json.dumps(spans)}, b), 33333,
                               (t) => Math.abs(t - 3000000) < 100000 ? 3000000 : null),
        }}""")
    # b (1-2 s) sits between a's end (1 s) and c's start (3 s), in the first stretch (0-3.5 s).
    assert got["room"] == {"lo": 1_000_000, "hi": 3_000_000}
    assert got["roomC"] == {"lo": 2_000_000, "hi": 3_500_000}      # held at the cut, 3.5 s
    assert got["moveLeft"] == {"start": 1_000_000, "end": 2_000_000}   # never over a: held, its length kept
    assert got["moveRight"] == {"start": 1_600_000, "end": 2_600_000}
    assert got["rightPastCut"] == {"start": 3_000_000, "end": 3_500_000}
    assert got["leftTooFar"] == {"start": 3_966_667, "end": 4_000_000}  # at least a frame left
    assert got["snapped"] == {"start": 2_000_000, "end": 3_000_000}     # its end snapped to c's start


def test_a_retime_changes_only_the_edge_dragged(tmp_path):
    got = _node(tmp_path, "const g = {in_us: 5, out_us: 9};"
                          "out = ['left', 'right', 'move'].map((k) => m.retimeEdit('b', k, g))")
    assert got == [{"line_id": "b", "in_us": 5}, {"line_id": "b", "out_us": 9},
                   {"line_id": "b", "in_us": 5, "out_us": 9}]


def test_split_merge_and_add_give_the_whole_list_with_ids_kept(tmp_path):
    got = _node(tmp_path, f"""
        const L = {json.dumps(LINES)};
        out = {{
          split: m.splitLine(L, 'b', 1400000),
          oneWord: m.splitLine(L, 'c', 3500000),
          outside: m.splitLine(L, 'b', 2500000),
          merge: m.mergeLines(L, 'a', {json.dumps(SPAN)}),
          last: m.mergeLines(L, 'c', {json.dumps(SPAN)}),
          add: m.addLine(L, {{in_us: 2200000, out_us: 2800000, text: 'new'}}),
          free: m.newBlockAt(m.blocksOf(L), {json.dumps(SPAN)}, 2200000, 2000000, 33333),
          taken: m.newBlockAt(m.blocksOf(L), {json.dumps(SPAN)}, 1500000, 2000000, 33333),
          past: m.newBlockAt(m.blocksOf(L), {json.dumps(SPAN)}, 10500000, 2000000, 33333),
        }}""")
    # Split at 40% of "three four five": its first part keeps the id; the second is new (no id).
    assert got["split"][1] == {"id": "b", "in_us": 1_000_000, "out_us": 1_400_000, "text": "three"}
    assert got["split"][2] == {"in_us": 1_400_000, "out_us": 2_000_000, "text": "four five"}
    assert [x.get("id") for x in got["split"]] == ["a", "b", None, "c"]
    assert got["oneWord"] is None and got["outside"] is None
    assert got["merge"] == [{"id": "a", "in_us": 0, "out_us": 2_000_000, "text": "one two three four five"},
                            {"id": "c", "in_us": 3_000_000, "out_us": 4_000_000, "text": "six"}]
    assert got["last"] is None
    assert [x["text"] for x in got["add"]] == ["one two", "three four five", "new", "six"]
    # A new caption goes up to the next one; none where one already is, or where no video plays.
    assert got["free"] == {"start": 2_200_000, "end": 3_000_000}
    assert got["taken"] is None and got["past"] is None


def test_lines_put_before_by_a_reorder_are_never_merged(tmp_path):
    # In the program, "x" plays after "y", but "x" comes first in the source.
    lines = [line("x", 0.0, 1.0, "first said", pieces=[(5.0, 6.0)]), line("y", 9.0, 10.0, "later said", pieces=[(1.0, 2.0)])]
    got = _node(tmp_path, f"out = [m.nextLine({json.dumps(lines)}, 'y', {json.dumps(SPAN)}),"
                          f" m.mergeLines({json.dumps(lines)}, 'y', {json.dumps(SPAN)})]")
    assert got == [None, None]


# Two stretches of one video: source 0-3.5 s plays at 0-3.5 s, then source
# 5-9 s at 3.5-7.5 s (source 3.5-5 s was cut out).
CUT = [{"src_in_us": 0, "src_out_us": 3_500_000, "start_us": 0, "end_us": 3_500_000, "speed": 1.0},
       {"src_in_us": 5_000_000, "src_out_us": 9_000_000, "start_us": 3_500_000, "end_us": 7_500_000, "speed": 1.0}]


def _cut_line(id_: str, in_s: float, out_s: float, text: str, start: float, end: float, shown: str) -> dict:
    out = line(id_, in_s, out_s, text, pieces=[(start, end)])
    out["pieces"][0]["text"] = shown
    return out


def test_no_merge_across_a_cut(tmp_path):
    # 9-D5: "we did" (2-3 s) plays up to the cut, "whole" (6-7 s) right after it.
    lines = [line("a", 2.0, 3.0, "we did"), _cut_line("b", 6.0, 7.0, "whole", 4.5, 5.5, "whole")]
    got = _node(tmp_path, f"const L = {json.dumps(lines)}, S = {json.dumps(CUT)};"
                          "out = [m.nextLine(L, 'a', S), m.mergeLines(L, 'a', S), m.cutBeforeNext(L, 'a', S),"
                          f" m.cutBeforeNext(L, 'a', {json.dumps(SPAN)})]")
    assert got[0] is None and got[1] is None
    assert got[2] is True and got[3] is False


def test_a_line_an_edit_cut_off_is_edited_as_the_part_that_plays(tmp_path):
    # 9-D6: "again and the" (source 3-4.4 s) plays only up to the cut at 3.5 s,
    # showing "again"; "w0 w1 w2" (4.6-5.6 s) plays only from 5 s, showing "w2".
    end_cut = _cut_line("e", 3.0, 4.4, "again and the", 3.0, 3.5, "again")
    start_cut = _cut_line("s", 4.6, 5.6, "w0 w1 w2", 3.5, 4.1, "w1 w2")
    whole = line("w", 1.0, 2.0, "one two")
    got = _node(tmp_path, f"""
        const S = {json.dumps(CUT)}, E = {json.dumps(end_cut)}, B = {json.dumps(start_cut)}, W = {json.dumps(whole)};
        const g = {{in_us: 3100000, out_us: 3400000}};
        out = {{
          trimE: m.trimmedLine(E, S), trimB: m.trimmedLine(B, S), trimW: m.trimmedLine(W, S),
          retime: ['left', 'right', 'move'].map((k) => m.retimeEdit('e', k, g, m.trimmedLine(E, S))),
          words: [m.wordsToEdit(E, S), m.wordsToEdit(W, S)],
          wordsEdit: [m.wordsEdit(E, S, 'a gain'), m.wordsEdit(W, S, 'one too')],
          split: m.splitLine([W, E], 'e', 3200000, S),
          splitOneWord: m.splitLine([W, E], 'e', 3800000, S),
          splitB: m.splitLine([B], 's', 5300000, S),
        }}""")
    assert got["trimE"] == {"id": "e", "in_us": 3_000_000, "out_us": 3_500_000, "text": "again"}
    assert got["trimB"] == {"id": "s", "in_us": 5_000_000, "out_us": 5_600_000, "text": "w1 w2"}
    assert got["trimW"] is None
    # Both ends sent, held to the stretch, with the words that play.
    assert got["retime"] == [
        {"line_id": "e", "in_us": 3_100_000, "out_us": 3_500_000, "text": "again"},
        {"line_id": "e", "in_us": 3_000_000, "out_us": 3_400_000, "text": "again"},
        {"line_id": "e", "in_us": 3_100_000, "out_us": 3_400_000, "text": "again"}]
    assert got["words"] == ["again", "one two"]
    assert got["wordsEdit"] == [{"line_id": "e", "in_us": 3_000_000, "out_us": 3_500_000, "text": "a gain"},
                                {"line_id": "w", "text": "one too"}]
    # "again" is one word: not split; a split point in the cut-out part is refused.
    assert got["split"] is None and got["splitOneWord"] is None
    assert got["splitB"] == [{"id": "s", "in_us": 5_000_000, "out_us": 5_300_000, "text": "w1"},
                             {"in_us": 5_300_000, "out_us": 5_600_000, "text": "w2"}]


def test_a_line_an_edit_cut_through_offers_only_the_words_that_play(tmp_path):
    # 9-D14: "we did it" (source 3-5.5 s) plays as "we" (3-3.5 s) and "it"
    # (5-5.5 s, program 3.5-4 s); "did" is cut out and is not offered.
    through = line("t", 3.0, 5.5, "we did it", pieces=[(3.0, 3.5), (3.5, 4.0)])
    through["pieces"][0]["text"], through["pieces"][1]["text"] = "we", "it"
    # The same two stretches swapped on the timeline: still offered in the source's order.
    swapped = [{"src_in_us": 5_000_000, "src_out_us": 9_000_000, "start_us": 0, "end_us": 4_000_000, "speed": 1.0},
               {"src_in_us": 0, "src_out_us": 3_500_000, "start_us": 4_000_000, "end_us": 7_500_000, "speed": 1.0}]
    rev = line("r", 3.0, 5.5, "we did it", pieces=[(0.0, 0.5), (7.0, 7.5)])
    rev["pieces"][0]["text"], rev["pieces"][1]["text"] = "it", "we"
    got = _node(tmp_path, f"""
        out = [m.wordsToEdit({json.dumps(through)}, {json.dumps(CUT)}),
               m.wordsToEdit({json.dumps(rev)}, {json.dumps(swapped)})]""")
    assert got == ["we it", "we it"]


def test_every_place_that_edits_a_line_uses_the_part_that_plays():
    # 9-D5 / 9-D6: the lane, the viewer's typing and the list send what plays.
    lane = _code(read(EDITOR / "CaptionLane.tsx"))
    assert "retimeEdit(block.id, mode, got, line ? trimmedLine(line, lane.spans) : null)" in lane
    assert "splitLine(lane.lines, block.id, r.in_us, lane.spans)" in lane
    assert "mergeLines(lane.lines, selectedLine, lane.spans)" in lane
    assert "nextLine(lane.lines, id, lane.spans)" in lane
    for name in ("CaptionLane.tsx", "CaptionTextLayer.tsx", "TranscriptPanel.tsx"):
        code = _code(read(EDITOR / name))
        assert "wordsEdit(" in code and "wordsToEdit(" in code, name
        assert "edits: [{ line_id: id, text: words }]" not in code, name
    assert "title={mergeTitle(lane, selectedLine)}" in _code(read(EDITOR / "TimelinePanel.tsx"))


def test_the_list_shows_the_lanes_lines_in_program_order_and_filters(tmp_path):
    lines = [line("a", 0.0, 1.0, "Café time", pieces=[(4.0, 5.0)]), line("b", 1.0, 2.0, "other words"),
             line("gone", 2.0, 3.0, "cut out", pieces=[])]
    got = _node(tmp_path, f"const L = {json.dumps(lines)};"
                          "out = [m.listLines(L, '').map((r) => [r.line.id, r.start]),"
                          " m.listLines(L, 'cafe').map((r) => r.line.id), m.listLines(L, 'zzz').length]")
    assert got == [[["b", 1_000_000], ["a", 4_000_000]], ["a"], 0]


@pytest.mark.parametrize("speed", [0.25, 0.5, 1.0, 1.5, 2.0, 4.0])
@pytest.mark.parametrize("fps", [30, 29.97, 23.976, 60])
def test_the_first_caption_of_a_project_made_by_hand_maps_as_the_engine_does(tmp_path, speed, fps):
    """A project made by hand names no captions' video; "+ Caption" works its
    first line's source times out itself (lib/captionLane.ts firstLineAt), as
    compose.caption_source_range does once the video is named."""
    from test_compose_plan import apply, manual

    from video_editor import compose
    from video_editor.project import model

    doc = manual((10, 13, speed), (20, 22, 1.0), fps=fps)
    rate = doc["canvas"]["fps"]
    items = [{"media_id": i["media_id"], "video": True, "start_us": i["start_us"], "duration_us": i["duration_us"],
              "source_in_us": i["source_in_us"], "source_out_us": i["source_out_us"], "speed": i["speed"]}
             for i in model.track(doc, model.MAIN_VIDEO)["items"]]
    named = apply(doc, {"op": "set_captions", "enabled": True, "media_id": "m1"})
    frames = [model.frame_us(n, rate) for n in range(0, model.frames_at(items[0]["duration_us"], rate) - 1, 5)]
    got = _node(tmp_path, f"const items = {json.dumps(items)};"
                          f"out = {json.dumps(frames)}.map((t) => m.firstLineAt(items, t, 2000000, 1))")
    for t, mine in zip(frames, got):
        engine = compose.caption_source_range(named, t, t + 2_000_000)
        assert mine is not None and engine is not None
        assert mine["media_id"] == "m1" and (mine["in_us"], mine["out_us"]) == engine, (t, mine, engine)


def test_the_first_caption_needs_a_video_under_the_playhead(tmp_path):
    items = [{"media_id": "m2", "video": False, "start_us": 0, "duration_us": 3_000_000, "source_in_us": 0,
              "source_out_us": 3_000_000, "speed": 1}]
    got = _node(tmp_path, f"out = [m.firstLineAt({json.dumps(items)}, 1000000, 2000000, 1),"
                          f" m.firstLineAt({json.dumps(items)}, 5000000, 2000000, 1)]")
    assert got == [None, None]


# ---- the sources ----------------------------------------------------------------------------------


def test_every_lane_action_is_one_change_through_the_projects_apply():
    lane = _code(read(EDITOR / "CaptionLane.tsx"))
    assert "fetch(" not in lane and "editorApi.ops" not in lane
    # One call per action, labelled (one undo step each).
    labels = re.findall(r"send\((.*?), t\('([^']+)'\)\)", lane, flags=re.DOTALL)
    names = {name for _, name in labels}
    for pair in re.findall(r"\? t\('([^']+)'\) : t\('([^']+)'\)", lane):
        names |= set(pair)
    assert {"Move caption", "Retime caption", "Edit caption", "Split caption", "Delete caption", "Merge captions",
            "Add caption", "Hide captions", "Show captions"} <= names
    assert "apply(ops, label, lane ? laneRevisionFor(ops, lane.revision) : null)" in lane
    # A dropped block's program times become source times through the engine, then one edit
    # (the keys and the inspector's times go the same way: `place`).
    drop = _between(lane, "const place = async", "const cancelDrag")
    assert "editorApi.captionSourceTimes(projectId, lane.revision" in drop
    assert "withSave(lane.saved, lane.lines, [{ op: 'edit_caption_words'" in drop
    # Nothing is sent while dragging: the ghost only.
    assert "send(" not in _between(lane, "const onMove = ", "const onUp = ")
    # A delete is an empty text (step 5's rule); a split, merge or add replaces the list.
    assert "edits: [{ line_id: id, text: '' }]" in lane
    assert lane.count("op: 'set_captions', lines: next") == 2
    # Blocks are buttons only near the view, named for screen readers.
    assert "blocksIn(blocks, win.from, win.to)" in lane and "aria-label={say}" in lane


def test_a_whole_caption_list_goes_at_the_lanes_revision(tmp_path):
    """9-D13: a change that sends the whole caption list was built from the
    lane at its revision; sent at a newer one it would put back the words of
    a fix made just before (fix a line, then Merge). It is pinned to the
    lane's revision and not made when the project moved on; a change to one
    line is not pinned."""
    got = _node(tmp_path, "out = [m.laneRevisionFor([{op: 'set_captions', lines: []}], 4),"
                          " m.laneRevisionFor(m.withSave(false, [], [{op: 'edit_caption_words', edits: []}]), 5),"
                          " m.laneRevisionFor(m.withSave(true, [], [{op: 'edit_caption_words', edits: []}]), 5),"
                          " m.laneRevisionFor([{op: 'set_captions', enabled: false}], 6)]")
    assert got == [4, 5, None, None]
    hook = _code(read(EDITOR / "useProject.ts"))
    turn = _between(hook, "const apply = useCallback(", "const step = ")
    assert "at !== undefined && at !== null && at !== p.revision" in turn
    assert turn.index("at !== p.revision") < turn.index("editorApi.ops(")
    lane = _code(read(EDITOR / "CaptionLane.tsx"))
    assert "apply(ops, label, lane ? laneRevisionFor(ops, lane.revision) : null)" in lane
    assert re.search(r"(?<![.\w])apply\(", lane.replace("apply(ops, label, lane ?", "")) is None
    panel = _code(read(EDITOR / "TranscriptPanel.tsx"))
    assert "apply(ops, t('Edit caption'), laneRevisionFor(ops, lane.revision))" in panel
    layer = _code(read(EDITOR / "CaptionTextLayer.tsx"))
    assert "laneRevisionFor(ops, lane.revision))" in _between(layer, "const finishEdit = ", "if (!parsed) return null")
    assert "await apply(ops, label, at)" in layer


def test_s_and_delete_act_on_a_selected_caption_line_first():
    timeline = _code(read(EDITOR / "TimelinePanel.tsx"))
    assert "if (captionLane.current?.split()) return" in _between(timeline, "const split = ", "const remove = ")
    assert "if (captionLane.current?.remove()) return" in _between(timeline, "const remove = ", "const zoomBy = ")
    lane = _code(read(EDITOR / "CaptionLane.tsx"))
    split = _between(lane, "const split = ", "const remove = ")
    assert "if (!selectedLine || !lane) return false" in split      # no line selected: the items are split
    # Clicking an empty part of a track lets go of the caption line too.
    assert "onSelectLine(null)" in _between(timeline, "const onLaneDown = ", "const onDragOver = ")


def test_the_lane_and_the_list_show_the_same_answer():
    screen = _code(read(EDITOR / "EditorScreen.tsx"))
    assert screen.count("lane={layer.lane}") == 3            # the timeline, the list and the inspector
    assert "<TranscriptPanel" in screen and 'role="tablist"' in screen
    listing = _code(read(EDITOR / "TranscriptPanel.tsx"))
    assert "listLines(lane?.lines ?? [], query)" in listing
    assert "withSave(lane.saved, lane.lines, [{ op: 'edit_caption_words'" in listing
    assert "onSeek(start)" in listing


def test_the_caption_panel_is_step_3s_controls_sending_the_whole_style():
    inspector = _code(read(EDITOR / "Inspector.tsx"))
    panel = _between(inspector, "function CaptionSettings(", "function TextSettings(")
    assert "<CaptionStyleControls" in panel
    assert "op: 'set_captions', style: clean" in panel
    # The base is the project's own style, else the one drawn (the lane's), as the viewer's drag uses.
    assert "own && typeof own === 'object' ? (own as Record<string, unknown>) : (lane?.style ?? {})" in panel
    # A preset keeps a dragged place; "Reset position" and a Position by name take it off.
    assert "x: current.x, y: current.y" in panel
    assert "x: undefined, y: undefined" in panel and "key === 'position'" in panel
    # Words per caption only while it does something (lines not saved yet).
    assert "hideWordsPerCaption={lane?.saved === true}" in panel
    # Shown for the caption layer (or a caption line) with no item selected.
    assert "captionLayer && selected.length === 0 ? (" in inspector


def test_the_text_panel_edits_text_and_the_later_version_line_is_gone():
    inspector = _code(read(EDITOR / "Inspector.tsx"))
    assert "Text is edited in a later version" not in inspector
    panel = _between(inspector, "function TextSettings(", "export default function Inspector(")
    for field in ("{ content: next }", "{ role: e.target.value }", "{ font: e.target.value || null }",
                  "{ size: Math.round(v) }", "{ color: c }", "{ position: p }", "{ position: null }"):
        assert field in panel, field
    assert "op: 'set_text', item_id: item.id" in panel
    assert "min={8}" in panel and "max={400}" in panel           # the engine's size range


def test_plus_text_adds_a_centred_three_second_text_at_the_playhead():
    timeline = _code(read(EDITOR / "TimelinePanel.tsx"))
    add = _between(timeline, "const addText = ", "const toggleTrack = ")
    assert "NEW_TEXT_US" in add and "export const NEW_TEXT_US = 3_000_000" in timeline
    assert "position: { x: 50, y: 50 }" in add and "font" not in add.replace("Math.floor", "")
    assert "onTextAdded(made.id)" in add


def test_the_lanes_lock_reaches_the_viewer():
    viewer = _code(read(EDITOR / "Viewer.tsx"))
    assert "captionsLocked={captionsLocked}" in viewer
    layer = _code(read(EDITOR / "CaptionTextLayer.tsx"))
    assert layer.count("target.kind === 'captions' && captionsLocked") >= 2
    assert "selection.kind === 'captions' && captionsLocked" in layer


def test_a_selected_caption_moves_and_retimes_from_the_keyboard():
    # Alt+arrows move the selected caption line (a frame; Shift: a second)
    # before any item, through the engine's source times as a drag does.
    timeline = _code(read(EDITOR / "TimelinePanel.tsx"))
    assert "if (captionLane.current?.nudge(dir, big)) return" in _between(timeline, "const nudge = ", "useImperativeHandle(")
    assert "'Move the selected caption a frame earlier or later (Shift: a second)'" in timeline
    lane = _code(read(EDITOR / "CaptionLane.tsx"))
    nudge = _between(lane, "const nudge = ", "const retime = ")
    assert "if (!selectedLine || !lane) return false" in nudge     # no line selected: the items move
    assert "room(blocks, spans, block)" in nudge and "void place(block, 'move', start, end)" in nudge
    place = _between(lane, "const place = async ", "const cancelDrag = ")
    assert "editorApi.captionSourceTimes(" in place and "op: 'edit_caption_words'" in place
    assert "const drop = (g: Drag): Promise<void> => place(g.block, g.mode, g.start, g.end)" in lane
    # The inspector's Starts at / Ends at for the selected line go through the lane's retime.
    inspector = _code(read(EDITOR / "Inspector.tsx"))
    panel = _between(inspector, "function CaptionSettings(", "function TextSettings(")
    assert "onCommit={(us) => onRetime?.(us, block.end)}" in panel
    assert "onCommit={(us) => onRetime?.(block.start, us)}" in panel
    screen = _code(read(EDITOR / "EditorScreen.tsx"))
    assert "onRetimeCaption={(start, end) => timeline.current?.retimeCaption(start, end)}" in screen


def test_the_words_box_takes_focus_once_after_plus_text():
    # Selecting the text again on the viewer keeps the focus there (WCAG 3.2.1).
    screen = _code(read(EDITOR / "EditorScreen.tsx"))
    assert "setFocusText(null)" in _between(screen, "const selectLayer = ", "const selectLine = ")
    assert "onTextFocused={() => setFocusText(null)}" in screen
    inspector = _code(read(EDITOR / "Inspector.tsx"))
    effect = _between(inspector, "area.current?.focus()", "}, [focus])")
    assert "onFocused?.()" in effect
