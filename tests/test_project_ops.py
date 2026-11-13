"""Editor suite step 5: each project operation (video_editor/project/ops.py):
what it checks, what it does at the edges, and that its inverse gives back
the bytes. Standard library only: runs on the CI light install.
"""

import json
import random

import pytest
from project_helpers import ENV, RATES, apply, main_items, on_grid, project

from video_editor.project import model, ops

S = 1_000_000


def refused(doc: dict, op, field=None, says: str | None = None) -> ops.OpError:
    """The operation is refused, in words that name it (and the field), and
    the document is as it was."""
    before = model.canonical(doc)
    with pytest.raises(ops.OpError) as caught:
        ops.apply(doc, [op], ENV)
    assert model.canonical(doc) == before
    error = caught.value
    if isinstance(op, dict) and isinstance(op.get("op"), str) and op["op"] in ops.SPECS:
        assert str(error).startswith(op["op"])
    if field is not None:
        assert error.field == field, str(error)
        assert f'"{field}"' in str(error)
    if says is not None:
        assert says in str(error), str(error)
    return error


def undone(doc: dict, *op_list: dict) -> dict:
    """The document after the operations, having checked that the inverse
    gives back the earlier bytes and the forward step the later ones."""
    result = ops.apply(doc, list(op_list), ENV)
    assert result.changed
    assert model.validate(result.document) == []
    assert model.to_bytes(ops.apply(result.document, result.inverse, ENV, internal=True).document) == model.to_bytes(doc)
    assert model.to_bytes(ops.apply(doc, result.forward, ENV, internal=True).document) == model.to_bytes(result.document)
    return result.document


def item(doc: dict, item_id: str) -> dict:
    return model.find_item(doc, item_id)[2]


def shape(doc: dict) -> list[tuple]:
    return [(i["id"], i["start_us"], i["duration_us"], i["source_in_us"], i["source_out_us"]) for i in main_items(doc)]


# ---- the set itself -----------------------------------------------------------------------------


def test_the_operations_are_the_plans_and_each_is_described():
    assert set(ops.SPECS) == {
        "set_canvas", "set_project_props", "add_media", "insert_clip", "trim_item", "split_item", "delete_items",
        "move_item", "duplicate_items", "set_item_props", "add_track", "remove_track", "set_track_props", "move_track",
        "add_audio", "set_audio_mutes", "add_text", "set_text", "set_captions", "edit_caption_words",
        "set_speaker", "set_branding", "set_export_preset", "batch"}
    assert set(ops._HANDLERS) == set(ops.SPECS) - {"batch"}
    described = ops.describe()
    assert described["version"] == ops.OPS_VERSION == 1
    for name, spec in described["ops"].items():
        assert spec["about"].strip(), name
        for field, about in spec["fields"].items():
            assert about["kind"] in ("id", "time", "int", "num", "bool", "str", "dict", "list", "any"), (name, field)


def test_the_step_5_doc_lists_every_operation_and_field():
    """docs/editor-suite/api/step5-project-core.md's table of operations: a
    row for each, naming each of its fields, the required ones in bold."""
    from pathlib import Path

    doc = Path(__file__).resolve().parent.parent / "docs" / "editor-suite" / "api" / "step5-project-core.md"
    rows = {line.split("|")[1].strip().strip("`"): line for line in doc.read_text(encoding="utf-8").splitlines()
            if line.startswith("| `") and line.count("|") == 4}
    for name, (_about, fields) in ops.SPECS.items():
        assert name in rows, f"the doc has no row for {name}"
        listed = rows[name].split("|")[2]
        for field, (_kind, required, _what) in fields.items():
            wanted = f"**`{field}`**" if required else f"`{field}`"
            assert wanted in listed, f"{name}: the doc doesn't list {wanted}"
            assert required or f"**`{field}`**" not in listed, f"{name}: {field} isn't required"


def test_a_bad_operation_is_named_in_plain_words():
    doc = project(clips=1)
    refused(doc, "split", says="every operation is an object")
    refused(doc, {"op": "explode"}, "op", "is not an operation")
    refused(doc, {"op": "split_item", "item_id": "i4"}, "at_us", "is missing")
    refused(doc, {"op": "split_item", "item_id": "i4", "at_us": 1.5}, "at_us", "whole number of microseconds")
    refused(doc, {"op": "split_item", "item_id": "i4", "at_us": -1}, "at_us")
    refused(doc, {"op": "split_item", "item_id": "i4", "at_us": True}, "at_us")
    refused(doc, {"op": "split_item", "item_id": "i4", "at_us": 2**53}, "at_us")
    refused(doc, {"op": "split_item", "item_id": "i4", "at_us": S, "path": "C:/x"}, "path", "is not a field")
    refused(doc, {"op": "split_item", "item_id": "nope", "at_us": S}, "item_id", "is not an item")
    refused(doc, {"op": "restore", "patch": ["=", {}]}, says="can't be sent")
    with pytest.raises(ops.OpError, match="must be a list"):
        ops.apply(doc, {"op": "add_track", "kind": "text"}, ENV)
    with pytest.raises(ops.OpError, match="not a project"):
        ops.apply([], [], ENV)
    too_many = [{"op": "set_project_props", "name": "x"}] * (ops.MAX_OPS + 1)
    with pytest.raises(ops.OpError, match="at most 500"):
        ops.apply(doc, too_many, ENV)
    nested = {"op": "set_project_props", "name": "x"}
    for _ in range(ops.MAX_BATCH_DEPTH + 1):
        nested = {"op": "batch", "ops": [nested]}
    refused(doc, nested, "ops", "nested too deeply")


def test_an_operation_that_changes_nothing_says_so():
    doc = project(clips=1)
    result = ops.apply(doc, [{"op": "set_project_props", "name": "Test"}], ENV)
    assert result.changed is False and result.inverse == [] and result.forward == []
    assert ops.apply(doc, [], ENV).changed is False


def test_a_newer_versions_document_is_refused_in_plain_words():
    doc = project()
    doc["schema"] = model.SCHEMA + 1
    with pytest.raises(ops.OpError, match="newer version of Clips Kitty"):
        ops.apply(doc, [{"op": "set_project_props", "name": "x"}], ENV)


# ---- canvas and project -------------------------------------------------------------------------


def test_set_canvas():
    doc = project(clips=2)
    out = undone(doc, {"op": "set_canvas", "preset": "16:9"})
    assert (out["canvas"]["width"], out["canvas"]["height"]) == (1920, 1080)
    out = undone(doc, {"op": "set_canvas", "width": 720, "height": 1280})
    assert (out["canvas"]["width"], out["canvas"]["height"]) == (720, 1280)
    refused(doc, {"op": "set_canvas"}, says="give a preset")
    refused(doc, {"op": "set_canvas", "preset": "3:2"}, "preset")
    refused(doc, {"op": "set_canvas", "preset": "1:1", "width": 100}, "preset")
    for bad in (15, 4098, 1081):
        refused(doc, {"op": "set_canvas", "width": bad}, "width", "even whole number")
    for bad in (0, 241, "fast", {"num": 30}, float("nan")):
        refused(doc, {"op": "set_canvas", "fps": bad}, "fps")
    result = ops.apply(doc, [{"op": "set_canvas", "fps": 29.97}], ENV)
    assert result.document["canvas"]["fps"] == {"num": 30000, "den": 1001}
    assert on_grid(result.document) and "frame rate" in result.warnings[0]
    assert undone(doc, {"op": "set_canvas", "fps": {"num": 24000, "den": 1001}})["canvas"]["fps"]["den"] == 1001


@pytest.mark.parametrize("there", [23.976, 25, 29.97, 50, 59.94, 60])
@pytest.mark.parametrize("home", [30, 59.94, 23.976])
def test_a_frame_rate_and_back_gives_the_same_lengths(home, there):
    """5-D7: lengths on a new grid come from the source and the speed, so
    another rate and back never leaves a short item a frame longer."""
    doc = model.new_project(fps=home)
    doc = apply(doc, {"op": "add_media", "video_id": "v"})
    fps = doc["canvas"]["fps"]
    for frames in (1, 2, 7, 11, 13, 100):
        doc = apply(doc, {"op": "insert_clip", "media_id": "m1", "source_in_us": 0,
                          "source_out_us": model.frame_us(frames, fps)})
    doc = apply(doc, {"op": "set_item_props", "item_id": "i4", "speed": 2})
    before = shape(doc)
    away = apply(doc, {"op": "set_canvas", "fps": there})
    assert on_grid(away)
    back = apply(away, {"op": "set_canvas", "fps": home})
    assert shape(back) == before
    assert shape(apply(apply(back, {"op": "set_canvas", "fps": there}), {"op": "set_canvas", "fps": home})) == before


def test_set_project_props():
    doc = project()
    out = undone(doc, {"op": "set_project_props", "name": "  My {clip}\n ", "master_volume": 0.5,
                       "normalize_audio": False})
    assert out["name"] == "My clip" and out["audio"] == {"master_volume": 0.5, "normalize": False}
    refused(doc, {"op": "set_project_props"}, says="give a name")
    refused(doc, {"op": "set_project_props", "name": "x" * 121}, "name")
    refused(doc, {"op": "set_project_props", "master_volume": 2.01}, "master_volume", "0 to 2")
    refused(doc, {"op": "set_project_props", "normalize_audio": 1}, "normalize_audio")


def test_add_media_is_named_never_given_as_a_path():
    doc = model.new_project()
    out = undone(doc, {"op": "add_media", "video_id": "video-a"})
    assert out["media"]["m1"]["source"] == {"type": "video", "video_id": "video-a"}
    again = ops.apply(out, [{"op": "add_media", "video_id": "video-a"}], ENV)
    assert again.changed is False and again.created == [{"kind": "media", "id": "m1"}]
    assert "already in the project" in again.warnings[0]
    refused(doc, {"op": "add_media"}, says="exactly one")
    refused(doc, {"op": "add_media", "video_id": "a", "library": "b"}, says="exactly one")
    refused(doc, {"op": "add_media", "video_id": "missing"}, "video_id", "is not in the library")
    refused(doc, {"op": "add_media", "video_id": ""}, "video_id")
    for key in ("path", "file", "url"):
        refused(doc, {"op": "add_media", key: "C:/Users/someone/video.mp4"}, key, "is not a field")


# ---- clips on V1 --------------------------------------------------------------------------------


def test_insert_clip_on_the_main_track():
    doc = project(clips=2)
    end = model.program_end(doc)
    out = undone(doc, {"op": "insert_clip", "media_id": "m1", "source_in_us": 60 * S, "source_out_us": 62 * S})
    assert [i["id"] for i in main_items(out)][-1] == "i6" and model.program_end(out) == end + 2 * S
    first = undone(doc, {"op": "insert_clip", "media_id": "m1", "source_in_us": 60 * S, "source_out_us": 62 * S,
                         "index": 0, "speed": 2})
    assert main_items(first)[0]["duration_us"] == S and main_items(first)[1]["start_us"] == S and on_grid(first)
    refused(doc, {"op": "insert_clip", "media_id": "m9", "source_in_us": 0, "source_out_us": S}, "media_id")
    refused(doc, {"op": "insert_clip", "media_id": "m2", "source_in_us": 0, "source_out_us": S}, "media_id", "add_audio")
    refused(doc, {"op": "insert_clip", "media_id": "m1", "source_in_us": S, "source_out_us": S}, "source_out_us")
    refused(doc, {"op": "insert_clip", "media_id": "m1", "source_in_us": 0, "source_out_us": 3601 * S},
            "source_out_us", "past the end")
    refused(doc, {"op": "insert_clip", "media_id": "m1", "source_in_us": 0, "source_out_us": S, "index": 3}, "index")
    refused(doc, {"op": "insert_clip", "media_id": "m1", "source_in_us": 0, "source_out_us": S, "start_us": 0},
            "start_us", "no gaps")
    refused(doc, {"op": "insert_clip", "media_id": "m1", "source_in_us": 0, "source_out_us": 10_000},
            "source_out_us", "shorter than one frame")
    for speed in (0.24, 4.01, "fast"):
        refused(doc, {"op": "insert_clip", "media_id": "m1", "source_in_us": 0, "source_out_us": S, "speed": speed},
                "speed")
    for speed in (0.25, 4):
        assert on_grid(undone(doc, {"op": "insert_clip", "media_id": "m1", "source_in_us": 0, "source_out_us": S,
                                    "speed": speed}))
    refused(doc, {"op": "insert_clip", "media_id": "m1", "source_in_us": 0, "source_out_us": S, "track_id": "A2"},
            "track_id", "not a video track")


def test_an_overlay_and_a_picture():
    doc = apply(project(clips=1), {"op": "add_track", "kind": "video"})
    out = undone(doc, {"op": "insert_clip", "media_id": "m3", "track_id": "V2", "source_in_us": 0,
                       "source_out_us": 3 * S, "start_us": 123_456})
    picture = item(out, "i5")
    assert (picture["start_us"], picture["duration_us"], picture["source_out_us"]) == (123_456, 3 * S, 3 * S)
    refused(out, {"op": "insert_clip", "media_id": "m1", "track_id": "V2", "source_in_us": 0, "source_out_us": S,
                  "start_us": S}, "start_us", "overlap")
    refused(doc, {"op": "insert_clip", "media_id": "m1", "track_id": "V2", "source_in_us": 0, "source_out_us": S},
            "start_us", "is missing")
    refused(doc, {"op": "insert_clip", "media_id": "m1", "track_id": "V2", "source_in_us": 0, "source_out_us": S,
                  "start_us": 0, "index": 0}, "index")
    refused(doc, {"op": "insert_clip", "media_id": "m3", "track_id": "V2", "source_in_us": 0, "source_out_us": S,
                  "start_us": 0, "speed": 2}, "speed", "picture")
    longer = undone(out, {"op": "trim_item", "item_id": "i5", "end_us": 10 * S})
    assert item(longer, "i5")["source_out_us"] == item(longer, "i5")["duration_us"] == 10 * S - 123_456
    refused(out, {"op": "trim_item", "item_id": "i5", "source_out_us": S}, "source_out_us", "start_us and end_us")
    refused(out, {"op": "set_item_props", "item_id": "i5", "speed": 2}, "speed")


def test_trim_item():
    doc = project(clips=3)
    out = undone(doc, {"op": "trim_item", "item_id": "i4", "source_out_us": 2 * S})
    assert shape(out)[0][2] == 2 * S and shape(out)[1][1] == 2 * S and model.program_end(out) == 10 * S
    out = undone(doc, {"op": "trim_item", "item_id": "i6", "source_in_us": 11 * S})      # the last item
    assert shape(out)[2][2] == 3 * S and model.program_end(out) == 11 * S
    out = undone(doc, {"op": "trim_item", "item_id": "i4", "source_in_us": 3_960_000})   # down to one frame
    assert shape(out)[0][2] == 33_333 and on_grid(out) and model.program_end(out) == 8 * S + 33_333
    refused(doc, {"op": "trim_item", "item_id": "i4"}, says="give the edge")
    refused(doc, {"op": "trim_item", "item_id": "i4", "source_in_us": 4 * S}, "source_in_us", "no length")
    refused(doc, {"op": "trim_item", "item_id": "i4", "source_out_us": 0}, "source_out_us", "no length")
    refused(doc, {"op": "trim_item", "item_id": "i4", "source_out_us": 5_000}, says="shorter than one frame")
    refused(doc, {"op": "trim_item", "item_id": "i4", "source_out_us": 3601 * S}, "source_out_us", "past the end")
    refused(doc, {"op": "trim_item", "item_id": "i4", "end_us": S}, "end_us", "source_in_us and source_out_us")


def test_split_item_on_the_main_track_moves_nothing_else():
    """5-D6: a cut in any item but the last used to be refused, or to make
    the next item and the whole video a frame longer."""
    doc = project(clips=3)
    for item_id, at in (("i4", 2 * S), ("i5", 5 * S), ("i6", 10 * S), ("i4", 33_333), ("i4", 3_966_667),
                        ("i5", 4_033_333), ("i5", 7_966_667)):
        out = undone(doc, {"op": "split_item", "item_id": item_id, "at_us": at})
        assert model.program_end(out) == model.program_end(doc) == 12 * S
        halves = [i for i in main_items(out) if i["id"] in (item_id, "i7")]
        whole = item(doc, item_id)
        assert halves[0]["start_us"] == whole["start_us"] and halves[1]["start_us"] == at
        assert halves[0]["duration_us"] + halves[1]["duration_us"] == whole["duration_us"]
        assert halves[0]["source_in_us"] == whole["source_in_us"] and halves[1]["source_out_us"] == whole["source_out_us"]
        assert halves[0]["source_out_us"] == halves[1]["source_in_us"]
        assert [s for s in shape(out) if s[0] not in (item_id, "i7")] == [s for s in shape(doc) if s[0] != item_id]
        assert [i["id"] for i in main_items(out)].index("i7") == [i["id"] for i in main_items(out)].index(item_id) + 1
    # The cut lands on the nearest frame.
    out = apply(doc, {"op": "split_item", "item_id": "i5", "at_us": 5 * S + 20_000})
    assert item(out, "i7")["start_us"] == 5 * S + 33_333
    for at in (0, 10_000, 4 * S, 4 * S - 10_000, 9 * S):
        refused(doc, {"op": "split_item", "item_id": "i4", "at_us": at}, "at_us", "at least one frame from each end")


def test_split_item_keeps_fades_at_the_outer_ends_and_shares_the_speed():
    doc = apply(project(clips=2), {"op": "set_item_props", "item_id": "i4", "speed": 2,
                                   "fades": {"in_us": 300_000, "out_us": 300_000}})
    out = undone(doc, {"op": "split_item", "item_id": "i4", "at_us": S})
    left, right = item(out, "i4"), item(out, "i6")
    assert left["fades"] == {"in_us": 300_000, "out_us": 0} and right["fades"] == {"in_us": 0, "out_us": 300_000}
    assert left["source_out_us"] == right["source_in_us"] == 2 * S and left["speed"] == right["speed"] == 2
    assert model.program_end(out) == model.program_end(doc)


def test_split_text_and_sound():
    doc = apply(project(clips=1),
                {"op": "add_text", "content": "Hello", "start_us": 500_000, "duration_us": 2 * S},
                {"op": "add_audio", "media_id": "m2", "track_id": "A2", "start_us": 0, "role": "sfx",
                 "source_in_us": S, "source_out_us": 5 * S})
    out = undone(doc, {"op": "split_item", "item_id": "i5", "at_us": S + 1})
    assert [(i["start_us"], i["duration_us"], i["source_out_us"]) for i in model.track(out, "T1")["items"]] == [
        (500_000, 500_001, 500_001), (S + 1, 1_499_999, 1_499_999)]
    out = undone(doc, {"op": "split_item", "item_id": "i6", "at_us": 1_500_000})
    sounds = model.track(out, "A2")["items"]
    assert [(i["source_in_us"], i["source_out_us"]) for i in sounds] == [(S, 2_500_000), (2_500_000, 5 * S)]
    for at in (0, 4 * S, 500_000):
        refused(doc, {"op": "split_item", "item_id": "i6" if at != 500_000 else "i5", "at_us": at}, "at_us")


def _plays(item: dict, at_us: int) -> int:
    """Where in its source a looped item is at a moment of the video."""
    stretch = item["source_out_us"] - item["source_in_us"]
    return item["source_in_us"] + (item.get("loop_offset_us", 0) + at_us - item["start_us"]) % stretch


def test_looped_music_stays_in_time_through_a_split_and_a_trimmed_start():
    """The music loops a 90-second stretch for 300 seconds. Whatever is cut
    off it or split out of it, every moment of the video that still has
    music plays the same moment of the music as before."""
    doc = apply(project(clips=1), {"op": "add_audio", "media_id": "m2", "track_id": "A2", "start_us": 10 * S,
                                   "role": "music", "loop": True, "duration_us": 300 * S})
    whole = model.track(doc, "A2")["items"][0]
    assert "loop_offset_us" not in whole
    moments = [10 * S, 50 * S, 100 * S - 1, 100 * S, 150 * S, 190 * S, 250 * S, 310 * S - 1]

    def same_music(out):
        for at in moments:
            now = [i for i in model.track(out, "A2")["items"] if i["start_us"] <= at < i["start_us"] + i["duration_us"]]
            if now:
                assert _plays(now[0], at) == _plays(whole, at), at
        assert not model.validate(out)

    out = undone(doc, {"op": "split_item", "item_id": "i5", "at_us": 50 * S})
    left, right = model.track(out, "A2")["items"]
    assert "loop_offset_us" not in left and right["loop_offset_us"] == 40 * S
    assert (right["source_in_us"], right["source_out_us"], right["loop"]) == (0, 90 * S, True)
    same_music(out)
    again = undone(out, {"op": "split_item", "item_id": right["id"], "at_us": 150 * S})     # past one whole loop
    assert [i.get("loop_offset_us", 0) for i in model.track(again, "A2")["items"]] == [0, 40 * S, 50 * S]
    same_music(again)
    on_the_loop = undone(doc, {"op": "split_item", "item_id": "i5", "at_us": 100 * S})      # exactly one loop in
    assert all("loop_offset_us" not in i for i in model.track(on_the_loop, "A2")["items"])
    same_music(on_the_loop)

    later = undone(doc, {"op": "trim_item", "item_id": "i5", "start_us": 25 * S})
    assert model.track(later, "A2")["items"][0]["loop_offset_us"] == 15 * S
    same_music(later)
    earlier = undone(later, {"op": "trim_item", "item_id": "i5", "start_us": 5 * S})
    assert model.track(earlier, "A2")["items"][0]["loop_offset_us"] == 85 * S
    same_music(earlier)
    back = apply(later, {"op": "trim_item", "item_id": "i5", "start_us": 10 * S})
    assert model.canonical(back) == model.canonical(doc)
    ended = undone(later, {"op": "trim_item", "item_id": "i5", "end_us": 60 * S})           # the end alone: no shift
    assert model.track(ended, "A2")["items"][0]["loop_offset_us"] == 15 * S
    moved = undone(later, {"op": "move_item", "item_id": "i5", "start_us": 0})              # moved: the music moves too
    assert model.track(moved, "A2")["items"][0]["loop_offset_us"] == 15 * S

    # No longer looped: it plays its stretch once, from its start, when there is room.
    once = undone(later, {"op": "set_item_props", "item_id": "i5", "loop": False})
    first = model.track(once, "A2")["items"][0]
    assert (first["loop"], first["start_us"], first["duration_us"], first["source_out_us"]) == (
        False, 25 * S, 90 * S, 90 * S)
    assert "loop_offset_us" not in first
    # The left half of the split is 40 seconds; played once it is 90, into the right half.
    refused(out, {"op": "set_item_props", "item_id": "i5", "loop": False}, "loop", "overlap")

    # The document's own check.
    for bad, says in ((90 * S, "inside the stretch"), (-1, "inside the stretch"), (1.5, "inside the stretch")):
        broken = model.clone(later)
        model.track(broken, "A2")["items"][0]["loop_offset_us"] = bad
        assert any(says in problem for problem in model.validate(broken)), bad
    broken = model.clone(doc)
    model.track(broken, "V1")["items"][0]["loop_offset_us"] = 5
    assert any("only belongs on looped music" in problem for problem in model.validate(broken))


def test_delete_items_ripples_on_the_main_track_only():
    doc = apply(project(clips=3), {"op": "add_text", "content": "Hi", "start_us": 6 * S, "duration_us": S},
                {"op": "set_item_props", "item_id": "i7", "link": "i5"})
    out = undone(doc, {"op": "delete_items", "item_ids": ["i4"]})                       # the first
    assert [s[:3] for s in shape(out)] == [("i5", 0, 4 * S), ("i6", 4 * S, 4 * S)]
    assert item(out, "i7")["start_us"] == 6 * S                                         # text stays where it is
    out = undone(doc, {"op": "delete_items", "item_ids": ["i6", "i5"]})                 # the last two
    assert [s[:3] for s in shape(out)] == [("i4", 0, 4 * S)]
    assert item(out, "i7")["link"] is None                                              # what it belonged with is gone
    assert main_items(undone(doc, {"op": "delete_items", "item_ids": ["i4", "i5", "i6"]})) == []
    refused(doc, {"op": "delete_items", "item_ids": []}, "item_ids")
    refused(doc, {"op": "delete_items", "item_ids": ["i4", "i4"]}, "item_ids", "twice")
    refused(doc, {"op": "delete_items", "item_ids": ["i4", "nope"]}, "item_ids")


def test_move_item():
    doc = apply(project(clips=3), {"op": "add_track", "kind": "video"}, {"op": "add_track", "kind": "audio"},
                {"op": "add_audio", "media_id": "m2", "track_id": "A2", "start_us": 0, "role": "music"})
    out = undone(doc, {"op": "move_item", "item_id": "i6", "index": 0})                # the last to the front
    assert [s[0] for s in shape(out)] == ["i6", "i4", "i5"] and on_grid(out) and model.program_end(out) == 12 * S
    out = undone(doc, {"op": "move_item", "item_id": "i4", "index": 2})                # the first to the end
    assert [s[0] for s in shape(out)] == ["i5", "i6", "i4"]
    lifted = undone(doc, {"op": "move_item", "item_id": "i5", "track_id": "V2", "start_us": 7 * S})
    assert [s[0] for s in shape(lifted)] == ["i4", "i6"] and model.program_end(lifted) == 8 * S
    assert item(lifted, "i5")["start_us"] == 7 * S
    home = undone(lifted, {"op": "move_item", "item_id": "i5", "track_id": "V1", "index": 1})
    assert shape(home) == shape(doc)
    moved = undone(doc, {"op": "move_item", "item_id": "i7", "track_id": "A3", "start_us": 2 * S})
    assert model.track(moved, "A3")["items"][0]["start_us"] == 2 * S
    refused(doc, {"op": "move_item", "item_id": "i4", "index": 3}, "index")
    refused(doc, {"op": "move_item", "item_id": "i4", "start_us": 0}, "start_us", "no gaps")
    refused(doc, {"op": "move_item", "item_id": "i4", "track_id": "A2", "start_us": 0}, "track_id", "audio track")
    refused(doc, {"op": "move_item", "item_id": "i7", "track_id": "A1", "start_us": 0}, "track_id", "A1")
    refused(doc, {"op": "move_item", "item_id": "i7"}, "start_us", "is missing")
    refused(doc, {"op": "move_item", "item_id": "i7", "index": 0}, "index")


def test_duplicate_items():
    doc = apply(project(clips=2), {"op": "add_text", "content": "Hi", "start_us": 0, "duration_us": S})
    out = undone(doc, {"op": "duplicate_items", "item_ids": ["i4", "i6"]})
    assert [s[0] for s in shape(out)] == ["i4", "i7", "i5"] and model.program_end(out) == 12 * S
    assert [(i["id"], i["start_us"]) for i in model.track(out, "T1")["items"]] == [("i6", 0), ("i8", S)]
    crowded = apply(doc, {"op": "add_text", "content": "Next", "start_us": S, "duration_us": S})
    refused(crowded, {"op": "duplicate_items", "item_ids": ["i6"]}, "item_ids", "overlap")
    refused(doc, {"op": "duplicate_items", "item_ids": ["nope"]}, "item_ids")


def test_set_item_props():
    doc = apply(project(clips=2), {"op": "add_audio", "media_id": "m2", "track_id": "A2", "start_us": 0,
                                   "role": "music"},
                {"op": "add_text", "content": "Hi", "start_us": 0, "duration_us": S})
    for speed, length in ((0.25, 16 * S), (0.5, 8 * S), (2, 2 * S), (4, S)):
        out = undone(doc, {"op": "set_item_props", "item_id": "i4", "speed": speed})
        assert shape(out)[0][2] == length and shape(out)[1][1] == length and on_grid(out)
    for speed in (0.2, 4.5, None):
        refused(doc, {"op": "set_item_props", "item_id": "i4", "speed": speed}, "speed")
    refused(doc, {"op": "set_item_props", "item_id": "i7", "speed": 2}, "speed", "not to text")
    for volume in (0, 2):
        assert item(undone(doc, {"op": "set_item_props", "item_id": "i4", "volume": volume}), "i4")["volume"] == volume
    refused(doc, {"op": "set_item_props", "item_id": "i4", "volume": 2.5}, "volume", "0 to 2")
    refused(doc, {"op": "set_item_props", "item_id": "i7", "volume": 1}, "volume", "text")
    out = undone(doc, {"op": "set_item_props", "item_id": "i4", "fades": {"in_us": S}})
    assert item(out, "i4")["fades"] == {"in_us": S, "out_us": 0}
    refused(doc, {"op": "set_item_props", "item_id": "i4", "fades": {"in_us": 3 * S, "out_us": 2 * S}}, "fades")
    refused(doc, {"op": "set_item_props", "item_id": "i4", "fades": {"in_us": 0.5}}, "fades")
    out = undone(doc, {"op": "set_item_props", "item_id": "i4", "framing": {"crop": "letterbox"},
                       "color": {"filter": "vibrant", "adjust": {"contrast": 1.2}},
                       "transform": {"x": 0.1, "opacity": 0.5}, "extra": {"anything": [1, 2]}})
    assert item(out, "i4")["framing"] == {"crop": "letterbox"}
    assert item(out, "i4")["color"] == {"filter": "vibrant", "adjust": {"contrast": 1.2}}
    gaming = undone(doc, {"op": "set_item_props", "item_id": "i4", "framing": {"gaming": {"cam": [0, 0, 1, 1], "x": 1}}})
    assert item(gaming, "i4")["framing"] == {"gaming": {"cam": [0, 0, 1, 1], "by": "clip"}}      # through the env
    assert item(undone(doc, {"op": "set_item_props", "item_id": "i4", "framing": {"gaming": None}}),
                "i4")["framing"] == {"gaming": None}
    refused(doc, {"op": "set_item_props", "item_id": "i4", "framing": {"gaming": {}}}, "framing", "webcam")
    refused(doc, {"op": "set_item_props", "item_id": "i4", "framing": {"crop": "sideways"}}, "framing")
    refused(doc, {"op": "set_item_props", "item_id": "i4", "color": {"filter": "nope"}}, "color", "not one of the app's")
    refused(doc, {"op": "set_item_props", "item_id": "i4", "color": {"adjust": {"contrast": 9}}}, "color")
    refused(doc, {"op": "set_item_props", "item_id": "i4", "transform": {"scale": 100}}, "transform")
    refused(doc, {"op": "set_item_props", "item_id": "i6", "framing": {"crop": "center"}}, "framing")
    refused(doc, {"op": "set_item_props", "item_id": "i4", "loop": True}, "loop")
    refused(doc, {"op": "set_item_props", "item_id": "i4", "link": "i4"}, "link")
    refused(doc, {"op": "set_item_props", "item_id": "i4", "link": "nope"}, "link")
    assert item(undone(doc, {"op": "set_item_props", "item_id": "i7", "link": "i4"}), "i7")["link"] == "i4"
    refused(doc, {"op": "set_item_props", "item_id": "i4"}, says="at least one thing")
    refused(doc, {"op": "set_item_props", "item_id": "i4", "extra": {"big": "x" * 20001}}, "extra", "too large")


# ---- tracks -------------------------------------------------------------------------------------


def test_tracks_any_number_of_audio_tracks_each_with_its_own_mixer_strip():
    """The step-10 request, without a change to the document's shape: any
    number of audio tracks, each with a name, volume, mute and solo, a
    master volume, and items from an imported file or a library id."""
    doc = project(clips=1)
    doc = apply(doc, {"op": "add_media", "asset": "sound-file"})
    assert doc["media"]["m5"]["source"] == {"type": "asset", "sha256": "sound-file"}
    for n in range(4):
        doc = undone(doc, {"op": "add_track", "kind": "audio", "name": f"Sounds {n}"})
    audio = [t for t in doc["tracks"] if t["kind"] == "audio"]
    assert [t["id"] for t in audio] == ["A1", "A2", "A3", "A4", "A5", "A6"]
    doc = undone(doc, {"op": "set_track_props", "track_id": "A5", "name": "Voice", "volume": 0.4, "mute": True,
                       "solo": True},
                 {"op": "set_project_props", "master_volume": 0.8},
                 {"op": "add_audio", "media_id": "m5", "track_id": "A5", "start_us": S, "role": "voice"},
                 {"op": "add_audio", "media_id": "m2", "track_id": "A6", "start_us": 0, "role": "music", "loop": True,
                  "duration_us": 4 * S, "duck": True, "volume": 0.3})
    strip = model.track(doc, "A5")
    assert (strip["name"], strip["volume"], strip["mute"], strip["solo"]) == ("Voice", 0.4, True, True)
    assert doc["media"][strip["items"][0]["media_id"]]["source"]["type"] == "asset"
    assert doc["media"][model.track(doc, "A6")["items"][0]["media_id"]]["source"] == {"type": "library", "id": "music-one"}
    assert doc["audio"]["master_volume"] == 0.8 and doc["schema"] == 1
    refused(doc, {"op": "set_track_props", "track_id": "V1", "volume": 1}, "volume", "audio track")
    refused(doc, {"op": "set_track_props", "track_id": "V1", "solo": True}, "solo")
    refused(doc, {"op": "set_track_props", "track_id": "A2"}, says="at least one thing")
    refused(doc, {"op": "set_track_props", "track_id": "A9", "mute": True}, "track_id")
    refused(doc, {"op": "add_track", "kind": "subtitle"}, "kind")
    for main in ("V1", "A1"):
        refused(doc, {"op": "remove_track", "track_id": main}, "track_id", "every project has it")
    refused(doc, {"op": "remove_track", "track_id": "A5"}, "track_id", "still holds")
    assert model.track(undone(doc, {"op": "remove_track", "track_id": "A5", "force": True}), "A5") is None
    assert model.track(undone(doc, {"op": "remove_track", "track_id": "A3"}), "A3") is None
    full = doc
    for _ in range(ops.MAX_TRACKS - len(doc["tracks"])):
        full = apply(full, {"op": "add_track", "kind": "text"})
    refused(full, {"op": "add_track", "kind": "text"}, says="at most 64")


def test_add_audio():
    doc = project(clips=2)
    out = undone(doc, {"op": "add_audio", "media_id": "m2", "track_id": "A2", "start_us": S, "role": "sfx",
                       "source_in_us": 2 * S, "source_out_us": 3 * S, "volume": 0.5,
                       "fades": {"in_us": 100_000, "out_us": 100_000}})
    sound = model.track(out, "A2")["items"][0]
    assert (sound["start_us"], sound["duration_us"], sound["loop"], sound["duck"]) == (S, S, False, False)
    whole = undone(doc, {"op": "add_audio", "media_id": "m2", "track_id": "A2", "start_us": 0, "role": "voice"})
    assert model.track(whole, "A2")["items"][0]["duration_us"] == 90 * S
    looped = undone(doc, {"op": "add_audio", "media_id": "m2", "track_id": "A2", "start_us": 0, "role": "music",
                          "loop": True, "duration_us": 300 * S, "duck": True})
    music = model.track(looped, "A2")["items"][0]
    assert (music["duration_us"], music["source_out_us"], music["loop"], music["duck"]) == (300 * S, 90 * S, True, True)
    own_sound = undone(doc, {"op": "add_audio", "media_id": "m1", "track_id": "A2", "start_us": 0, "role": "voice",
                             "source_out_us": S})
    assert model.track(own_sound, "A2")["items"][0]["media_id"] == "m1"
    base = {"op": "add_audio", "media_id": "m2", "track_id": "A2", "start_us": 0, "role": "music"}
    for role in ("sfx", "voice"):
        refused(doc, {**base, "role": role, "loop": True, "duration_us": S}, "loop", "only music can loop")
    refused(doc, {**base, "loop": True}, "duration_us", "is missing")
    refused(doc, {**base, "duration_us": S}, "duration_us", "only applies to looped music")
    refused(doc, {**base, "track_id": "A1"}, "track_id", "A1")
    refused(doc, {**base, "track_id": "V1"}, "track_id")
    refused(doc, {**base, "media_id": "m3"}, "media_id", "no sound")
    refused(doc, {**base, "role": "noise"}, "role")
    refused(doc, {**base, "volume": 3}, "volume")
    refused(doc, {**base, "source_out_us": 91 * S}, "source_out_us", "past the end")
    refused(whole, base, "start_us", "overlap")
    refused(looped, {"op": "set_item_props", "item_id": "i6", "speed": 2}, "speed", "looped music")
    stretched = undone(looped, {"op": "trim_item", "item_id": "i6", "end_us": 20 * S})
    assert model.track(stretched, "A2")["items"][0]["duration_us"] == 20 * S


def test_set_audio_mutes():
    doc = project(clips=1)
    out = undone(doc, {"op": "set_audio_mutes",
                       "mutes": [{"in_us": 3 * S, "out_us": 4 * S, "mode": "bleep"}, {"in_us": S, "out_us": 2 * S}],
                       "muted_words": [{"in_us": S, "out_us": 2 * S, "word": "darn"}]})
    assert out["captions"]["mutes"] == [{"in_us": S, "out_us": 2 * S, "mode": "mute"},
                                        {"in_us": 3 * S, "out_us": 4 * S, "mode": "bleep"}]
    cleared = undone(out, {"op": "set_audio_mutes", "mutes": []})
    assert cleared["captions"]["mutes"] == [] and cleared["captions"]["muted_words"] == []
    refused(doc, {"op": "set_audio_mutes", "mutes": [{"in_us": S, "out_us": S}]}, "mutes", "ends after it starts")
    refused(doc, {"op": "set_audio_mutes", "mutes": [{"in_us": 0, "out_us": S, "mode": "beep"}]}, "mutes", "mute or bleep")
    refused(doc, {"op": "set_audio_mutes", "mutes": [{"in_us": 0.5, "out_us": S}]}, "mutes")
    refused(doc, {"op": "set_audio_mutes", "mutes": [], "muted_words": [{"in_us": 0, "out_us": S, "word": 5}]},
            "muted_words")


# ---- text ---------------------------------------------------------------------------------------


def test_add_text_and_set_text():
    doc = project()
    out = undone(doc, {"op": "add_text", "content": " {\\an8}Wait\tfor \\it\r\nnow\x07 ", "start_us": S,
                       "duration_us": 2 * S, "font": "bangers", "size": 72, "color": "#ffcc00", "position": "top",
                       "role": "hook"})
    text = item(out, "i4")["text"]
    assert text == {"content": "an8Wait for it\nnow", "role": "hook", "font": "Bangers", "size": 72,
                    "color": "#FFCC00", "position": "top"}
    assert undone(doc, {"op": "add_text", "content": "x" * 500, "start_us": 0, "duration_us": 1})
    refused(doc, {"op": "add_text", "content": "x" * 501, "start_us": 0, "duration_us": S}, "content", "501 characters")
    refused(doc, {"op": "add_text", "content": " {} \\ ", "start_us": 0, "duration_us": S}, "content", "no text")
    refused(doc, {"op": "add_text", "content": "Hi", "start_us": 0, "duration_us": 0}, "duration_us")
    refused(doc, {"op": "add_text", "content": "Hi", "start_us": 0, "duration_us": S, "font": "Comic Sans"}, "font",
            "not one of the caption fonts")
    for key, bad in (("size", 7), ("size", 401), ("color", "red"), ("position", "left"), ("role", "title")):
        refused(doc, {"op": "add_text", "content": "Hi", "start_us": 0, "duration_us": S, key: bad}, key)
    refused(doc, {"op": "add_text", "content": "Hi", "start_us": 0, "duration_us": S, "track_id": "V1"}, "track_id")
    refused(out, {"op": "add_text", "content": "Hi", "start_us": 2 * S, "duration_us": S}, "start_us", "overlap")
    no_text_track = apply(doc, {"op": "remove_track", "track_id": "T1"})
    refused(no_text_track, {"op": "add_text", "content": "Hi", "start_us": 0, "duration_us": S}, "track_id")

    changed = undone(out, {"op": "set_text", "item_id": "i4", "content": "New {words}", "font": None, "size": 90})
    assert item(changed, "i4")["text"] == {"content": "New words", "role": "hook", "size": 90, "color": "#FFCC00",
                                           "position": "top"}
    refused(out, {"op": "set_text", "item_id": "i4"}, says="at least one thing")
    refused(out, {"op": "set_text", "item_id": "i4", "content": "x" * 501}, "content")
    refused(project(clips=1), {"op": "set_text", "item_id": "i4", "content": "Hi"}, "item_id", "not a text item")
    moved = undone(out, {"op": "trim_item", "item_id": "i4", "start_us": 0, "end_us": 5 * S})
    assert (item(moved, "i4")["start_us"], item(moved, "i4")["duration_us"]) == (0, 5 * S)
    refused(out, {"op": "trim_item", "item_id": "i4", "end_us": S}, "end_us", "no length")


# ---- captions and branding ----------------------------------------------------------------------


def test_set_captions_and_edit_caption_words():
    doc = project()
    out = undone(doc, {"op": "set_captions", "enabled": True, "style": {"font": "Arial", "font_size": 80},
                       "media_id": "m1",
                       "lines": [{"in_us": 0, "out_us": S, "text": "one {two}"},
                                 {"in_us": S, "out_us": 2 * S, "text": "three", "words": [{"w": "three"}]}]})
    captions = out["captions"]
    assert captions["enabled"] is True and captions["style"] == {"font": "Arial", "font_size": 80}
    assert [(line["id"], line["text"]) for line in captions["lines"]] == [("c4", "one two"), ("c5", "three")]
    assert captions["lines"][1]["words"] == [{"w": "three"}]
    back = undone(out, {"op": "set_captions", "enabled": None, "style": None, "lines": None})
    assert (back["captions"]["enabled"], back["captions"]["style"], back["captions"]["lines"]) == (None, None, None)
    refused(doc, {"op": "set_captions"}, says="give enabled")
    refused(doc, {"op": "set_captions", "enabled": "yes"}, "enabled")
    refused(doc, {"op": "set_captions", "style": {"bad": 1}}, "style")
    refused(doc, {"op": "set_captions", "media_id": "m9"}, "media_id")
    refused(doc, {"op": "set_captions", "lines": [{"in_us": S, "out_us": S, "text": "x"}]}, "lines")
    refused(doc, {"op": "set_captions", "lines": [{"in_us": 0, "out_us": S}]}, "lines", "needs its text")
    refused(doc, {"op": "set_captions", "lines": [{"in_us": 0, "out_us": S, "text": "x" * 501}]}, "lines")

    fixed = undone(out, {"op": "edit_caption_words", "edits": [
        {"line_id": "c4", "text": "one, two", "out_us": 900_000}, {"line_id": "c5", "text": "  "}]})
    assert fixed["captions"]["lines"] == [{"id": "c4", "in_us": 0, "out_us": 900_000, "text": "one, two"}]
    refused(doc, {"op": "edit_caption_words", "edits": [{"line_id": "c4", "text": "x"}]}, "edits", "no caption lines")
    refused(out, {"op": "edit_caption_words", "edits": []}, "edits", "is empty")
    refused(out, {"op": "edit_caption_words", "edits": [{"line_id": "c9", "text": "x"}]}, "edits")
    refused(out, {"op": "edit_caption_words", "edits": [{"line_id": "c4", "out_us": 0}]}, "edits", "end after it starts")
    refused(out, {"op": "edit_caption_words", "edits": [{"line_id": "c4", "speaker": 1}]}, "edits")


def test_set_speaker():
    doc = project(clips=1)
    out = undone(doc, {"op": "set_speaker", "in_us": S, "out_us": 3 * S, "speaker": 1})
    assert out["captions"]["speaker_edits"] == [{"in_us": S, "out_us": 3 * S, "speaker": 1}]
    assert undone(out, {"op": "set_speaker", "clear": True})["captions"]["speaker_edits"] == []
    refused(doc, {"op": "set_speaker", "in_us": S, "out_us": 3 * S}, "speaker", "is missing")
    refused(doc, {"op": "set_speaker", "in_us": S, "out_us": S, "speaker": 0}, "out_us")
    refused(doc, {"op": "set_speaker", "in_us": S, "out_us": 2 * S, "speaker": 2}, "speaker")
    refused(doc, {"op": "set_speaker", "clear": True, "speaker": 1}, "clear")


def test_set_branding():
    doc = project(clips=1)
    out = undone(doc, {"op": "set_branding", "watermark": {"type": "text", "text": "@Your{Channel}", "font": "arial",
                                                           "opacity": 0.8, "position": "bottom_right"}})
    assert out["branding"]["watermark"] == {"type": "text", "text": "@YourChannel", "font": "Arial", "opacity": 0.8,
                                            "position": "bottom_right"}
    assert undone(doc, {"op": "set_branding", "watermark": None})["branding"] == {"watermark": None}
    assert undone(out, {"op": "set_branding", "clear": True})["branding"] == {}
    refused(doc, {"op": "set_branding"}, "watermark", "is missing")
    refused(doc, {"op": "set_branding", "watermark": "logo"}, "watermark")
    refused(doc, {"op": "set_branding", "watermark": {"type": "video"}}, "watermark")
    refused(doc, {"op": "set_branding", "watermark": {"type": "text", "font": "Comic Sans"}}, "watermark")
    refused(doc, {"op": "set_branding", "watermark": {"type": "image", "image_asset": "../../secret.png"}},
            "watermark", "never a path")
    refused(doc, {"op": "set_branding", "watermark": {"type": "image", "image_asset": "C:\\logo.png"}}, "watermark")
    refused(doc, {"op": "set_branding", "watermark": {"type": "text", "opacity": 2}}, "watermark")
    refused(doc, {"op": "set_branding", "clear": True, "watermark": None}, "clear")


def test_set_export_preset_goes_through_what_this_pc_can_encode():
    doc = project(clips=1)
    out = undone(doc, {"op": "set_export_preset", "preset": {"codec": "h264", "height": 720}})
    assert out["export"]["preset"] == {"kind": "custom", "codec": "h264", "height": 720}
    assert undone(out, {"op": "set_export_preset", "preset": "original"})["export"]["preset"] is None
    refused(doc, {"op": "set_export_preset", "preset": {"codec": "av1"}}, "preset", "can't encode")
    refused(doc, {"op": "set_export_preset"}, "preset", "is missing")


# ---- the frame grid -----------------------------------------------------------------------------


@pytest.mark.parametrize("rate", RATES)
def test_ten_thousand_edits_never_drift_off_the_frame_grid(rate):
    """10,000 inserts, trims, cuts, moves and deletes on V1, checked against
    a plain list of frame counts kept beside the project: every edge stays
    exactly where its frame number puts it."""
    rng = random.Random(int(rate * 1000))
    doc = project(fps=rate)
    fps = doc["canvas"]["fps"]
    frames: list[int] = []
    ids: list[str] = []
    done = 0
    while done < 10_000:
        batch = []
        for _ in range(100):
            n = len(frames)
            choice = rng.random()
            if n < 3 or (choice < 0.3 and n < 60):
                k, where = rng.randrange(1, 400), rng.randrange(0, n + 1)
                start = rng.randrange(0, 1000) * 1_000_000
                batch.append({"op": "insert_clip", "media_id": "m1", "source_in_us": start,
                              "source_out_us": start + model.frame_us(k, fps), "index": where})
                frames.insert(where, k)
                ids.insert(where, f"i{doc['seq'] + sum(1 for b in batch if b['op'] in ('insert_clip', 'split_item')) - 1}")
            elif choice < 0.5:
                where = rng.randrange(n)
                if frames[where] < 2:
                    continue
                cut = rng.randrange(1, frames[where])
                at = model.frame_us(sum(frames[:where]) + cut, fps)
                batch.append({"op": "split_item", "item_id": ids[where], "at_us": at})
                frames[where:where + 1] = [cut, frames[where] - cut]
                ids.insert(where + 1, f"i{doc['seq'] + sum(1 for b in batch if b['op'] in ('insert_clip', 'split_item')) - 1}")
            elif choice < 0.7:
                where, to = rng.randrange(n), rng.randrange(n)
                batch.append({"op": "move_item", "item_id": ids[where], "index": to})
                frames.insert(to, frames.pop(where))
                ids.insert(to, ids.pop(where))
            else:
                where = rng.randrange(n)
                batch.append({"op": "delete_items", "item_ids": [ids[where]]})
                del frames[where], ids[where]
        doc = ops.apply(doc, batch, ENV).document
        done += len(batch)
        assert [i["id"] for i in main_items(doc)] == ids
        edge = 0
        for n, entry in enumerate(main_items(doc)):
            assert entry["start_us"] == model.frame_us(edge, fps), (rate, done, n)
            edge += frames[n]
            assert entry["start_us"] + entry["duration_us"] == model.frame_us(edge, fps), (rate, done, n)
        assert model.program_end(doc) == model.frame_us(sum(frames), fps)


def test_frame_times_are_exact_whole_numbers_below_2_to_the_53():
    for rate in RATES:
        fps = model.fps_of(rate)
        for n in (0, 1, 2, 1000, 10_000, 107_892, 5_000_000):
            at = model.frame_us(n, fps)
            assert type(at) is int and at < 2**53 and model.frames_at(at, fps) == n
    assert model.fps_of(29.97) == {"num": 30000, "den": 1001}
    assert model.fps_of(23.976) == {"num": 24000, "den": 1001}
    assert model.fps_of(59.94) == {"num": 60000, "den": 1001}
    assert model.frame_us(10_000, model.fps_of(29.97)) == 333_666_667
    assert model.frame_us(3, model.fps_of(29.97)) == 100_100


def test_coalesce_key_names_one_small_tweak():
    key = ops.coalesce_key([{"op": "set_item_props", "item_id": "i4", "volume": 0.5}])
    assert key and key == ops.coalesce_key([{"op": "set_item_props", "item_id": "i4", "volume": 0.9}])
    assert key != ops.coalesce_key([{"op": "set_item_props", "item_id": "i5", "volume": 0.5}])
    assert key != ops.coalesce_key([{"op": "set_item_props", "item_id": "i4", "speed": 2}])
    assert ops.coalesce_key([{"op": "set_item_props", "item_id": "i4", "transform": {"x": 0.1}}]) != ops.coalesce_key(
        [{"op": "set_item_props", "item_id": "i4", "transform": {"y": 0.1}}])
    for other in ([{"op": "split_item", "item_id": "i4", "at_us": 5}], [], ["x"],
                  [{"op": "set_item_props", "item_id": "i4", "volume": 1}] * 2,
                  [{"op": "set_captions", "lines": []}]):
        assert ops.coalesce_key(other) == ""


# ---- found in review (5-D15 onward) -------------------------------------------------------------


def test_the_document_holds_one_zero_and_undo_is_exact_where_it_is_given_another():
    """0.0 and -0.0 are equal and written differently. Where an operation
    checks a number it stores 0.0, so going between the two is no change;
    where it keeps an object as given, the undo still gives back the bytes."""
    doc = apply(project(clips=1), {"op": "add_track", "kind": "audio"})
    makers = (lambda v: {"op": "set_item_props", "item_id": "i4", "transform": {"x": v, "y": v, "rotation": v}},
              lambda v: {"op": "set_item_props", "item_id": "i4", "volume": v},
              lambda v: {"op": "set_item_props", "item_id": "i4", "color": {"adjust": {"brightness": v}}},
              lambda v: {"op": "set_project_props", "master_volume": v},
              lambda v: {"op": "set_track_props", "track_id": "A3", "volume": v})
    for first, second in ((0.0, -0.0), (-0.0, 0.0), (0, -0.0)):
        for make in makers:
            at = ops.apply(doc, [make(first)], ENV)
            assert "-0.0" not in at.text
            again = ops.apply(at.document, [make(second)], ENV)
            assert again.changed is False and again.inverse == [] and again.text == at.text, make(second)
    # Kept as given (an item's extra, a watermark's place): both zeros can
    # be there, and each change between them undoes to the very bytes.
    for plus, minus in (({"n": 0.0}, {"n": -0.0}), ({"deep": [{"n": 0.0}]}, {"deep": [{"n": -0.0}]}),
                        ({"n": [0.0, 1]}, {"n": [-0.0, 1]})):
        has = undone(doc, {"op": "set_item_props", "item_id": "i4", "extra": plus})
        negative = undone(has, {"op": "set_item_props", "item_id": "i4", "extra": minus})
        assert "-0.0" in model.canonical(negative) and "-0.0" not in model.canonical(has)
        back = undone(negative, {"op": "set_item_props", "item_id": "i4", "extra": plus})
        assert model.canonical(back) == model.canonical(has)
    marked = undone(doc, {"op": "set_branding", "watermark": {"type": "text", "text": "@Me", "x": 0.0, "y": 0.5}})
    undone(marked, {"op": "set_branding", "watermark": {"type": "text", "text": "@Me", "x": -0.0, "y": 0.5}})


def test_a_patch_tells_the_two_zeros_and_the_kinds_of_number_apart_when_asked():
    from video_editor.project import patch

    for a, b in ((0.0, -0.0), (-0.0, 0.0), (1, 1.0), (True, 1), ({"k": [0.0]}, {"k": [-0.0]}),
                 ({"k": {"n": 1}}, {"k": {"n": 1.0}})):
        node = patch.diff(a, b, strict=True)
        assert node is not None, (a, b)
        assert json.dumps(patch.apply(a, node)) == json.dumps(b)
    for same in (0.0, -0.0, 1, {"k": [0.0, -0.0, 1, True, None, "x"]}):
        assert patch.diff(same, json.loads(json.dumps(same)), strict=True) is None


def test_what_cant_be_saved_is_refused_by_its_operation_and_field():
    """NaN, Infinity and half of a character can't be written as JSON or
    UTF-8. They are refused before anything is applied, by name; never a
    plain ValueError (which a route would answer as a 500)."""
    doc = apply(project(clips=1), {"op": "add_text", "content": "Hi", "start_us": 0, "duration_us": S})
    nan, inf, half = float("nan"), float("inf"), "\ud83d"
    line = {"in_us": 0, "out_us": S, "text": "one"}
    for bad, field in (
            ({"op": "set_item_props", "item_id": "i4", "extra": {"n": nan}}, "extra"),
            ({"op": "set_item_props", "item_id": "i4", "extra": {"deep": [1, {"n": -inf}]}}, "extra"),
            ({"op": "set_item_props", "item_id": "i4", "volume": nan}, "volume"),
            ({"op": "set_captions", "lines": [{**line, "w": inf}]}, "lines"),
            ({"op": "set_audio_mutes", "mutes": [{"in_us": 0, "out_us": S, "x": nan}]}, "mutes"),
            ({"op": "set_canvas", "fps": inf}, "fps"),
            ({"op": "set_canvas", "fps": nan}, "fps"),
            ({"op": "set_branding", "watermark": {"type": "text", "opacity": nan}}, "watermark"),
            ({"op": "set_export_preset", "preset": {"codec": "h264", "crf": inf}}, "preset"),
            ({"op": "set_item_props", "item_id": "i4", "extra": {"note": "cut " + half}}, "extra"),
            ({"op": "set_item_props", "item_id": "i4", "extra": {half: 1}}, "extra"),
            ({"op": "add_text", "content": "Hi " + half, "start_us": 2 * S, "duration_us": S}, "content"),
            ({"op": "set_text", "item_id": "i5", "content": half}, "content"),
            ({"op": "set_project_props", "name": "Name " + half}, "name"),
            ({"op": "set_captions", "lines": [{**line, "words": [{"w": half}]}]}, "lines"),
            ({"op": "set_branding", "watermark": {"type": "text", "text": half}}, "watermark")):
        error = refused(doc, bad, field)
        assert "ValueError" not in str(error) and half not in str(error)
        with pytest.raises(ops.OpError) as inside:      # inside a batch, the operation at fault is the one named
            ops.apply(doc, [{"op": "batch", "ops": [{"op": "set_project_props", "name": "Fine"}, bad]}], ENV)
        assert (inside.value.op, inside.value.field) == (bad["op"], field)
    for bad in ({"op": "set_project_props", "name": "x", half: 1}, {"op": "explode " + half}, half, nan,
                {"op": "set_canvas", "fps": 10**400}):
        error = refused(doc, bad)
        str(error).encode("utf-8")          # the refusal itself can be sent
    # A whole emoji is text like any other.
    assert undone(doc, {"op": "set_text", "item_id": "i5", "content": "Go \U0001F389"})


@pytest.mark.parametrize("rate", RATES)
def test_edits_before_a_picture_or_fades_that_fill_a_clip_are_never_refused(rate):
    """Laying the main track out again makes a neighbour a microsecond longer
    or shorter where a frame is not a whole number of microseconds (30 and
    29.97 among them). A picture (its source times are its length) and fades
    that fill their clip must follow, or an edit somewhere else is refused."""
    frame = model.frame_us(1, model.fps_of(rate))
    doc = project(fps=rate)
    doc = apply(doc, {"op": "insert_clip", "media_id": "m1", "source_in_us": 0, "source_out_us": 5 * S},
                {"op": "insert_clip", "media_id": "m3", "source_in_us": 0, "source_out_us": 7 * frame},
                {"op": "insert_clip", "media_id": "m1", "source_in_us": 60 * S, "source_out_us": 60 * S + 8 * frame})
    first, picture, last = (i["id"] for i in main_items(doc))
    length = item(doc, last)["duration_us"]
    doc = apply(doc, {"op": "set_item_props", "item_id": last, "fades": {"in_us": length // 2,
                                                                         "out_us": length - length // 2}})
    tried = 0
    for n in range(1, 61):
        for op in ({"op": "insert_clip", "media_id": "m1", "source_in_us": 100 * S, "source_out_us": 100 * S + n * frame,
                    "index": 0},
                   {"op": "trim_item", "item_id": first, "source_out_us": 5 * S - n * frame},
                   {"op": "set_item_props", "item_id": first, "speed": 1 + n / 20},
                   {"op": "split_item", "item_id": first, "at_us": n * frame},
                   {"op": "duplicate_items", "item_ids": [first]},
                   {"op": "batch", "ops": [{"op": "duplicate_items", "item_ids": [picture, last]},
                                           {"op": "move_item", "item_id": last, "index": 0},
                                           {"op": "delete_items", "item_ids": [first]}]}):
            result = ops.apply(doc, [op], ENV)       # raises when refused
            tried += 1
            assert on_grid(result.document) and result.warnings == [], (n, op, result.warnings)
            for entry in main_items(result.document):
                if result.document["media"][entry["media_id"]]["kind"] == "image":
                    assert (entry["source_in_us"], entry["source_out_us"]) == (0, entry["duration_us"])
                assert entry["fades"]["in_us"] + entry["fades"]["out_us"] <= entry["duration_us"]
            fades = item(result.document, last)["fades"]
            assert length - (fades["in_us"] + fades["out_us"]) <= 1      # at most the rounding was taken off
            assert model.to_bytes(ops.apply(result.document, result.inverse, ENV, internal=True).document) \
                == model.to_bytes(doc)
    assert tried == 360
    # An edit that really shortens an item still says its fades were shortened.
    cut = ops.apply(doc, [{"op": "trim_item", "item_id": last, "source_out_us": 60 * S + 4 * frame}], ENV)
    assert cut.warnings and "fades were shortened" in cut.warnings[0]


@pytest.mark.parametrize("rate", (29.97, 30, 23.976, 25))
@pytest.mark.parametrize("speed", (1, 0.25, 4))
def test_a_clip_moves_from_the_main_track_to_an_overlay_and_back(rate, speed):
    """On V1 a clip is a whole number of frames long; anywhere else it plays
    its source stretch to the microsecond."""
    doc = apply(project(fps=rate, clips=1), {"op": "add_track", "kind": "video"},
                {"op": "insert_clip", "media_id": "m1", "source_in_us": 20 * S, "source_out_us": 20 * S + 5_010_000,
                 "speed": speed})
    clip = main_items(doc)[1]
    out = undone(doc, {"op": "move_item", "item_id": clip["id"], "track_id": "V2", "start_us": 0})
    moved = item(out, clip["id"])
    assert moved["duration_us"] == round(5_010_000 / speed) and moved["start_us"] == 0
    assert (moved["source_in_us"], moved["source_out_us"]) == (20 * S, 20 * S + 5_010_000)
    assert len(main_items(out)) == 1 and on_grid(out)
    home = undone(out, {"op": "move_item", "item_id": clip["id"], "track_id": "V1", "index": 1})
    assert item(home, clip["id"])["duration_us"] == clip["duration_us"] and on_grid(home)
    # Fades that filled it on V1 fit it on the overlay too.
    faded = apply(doc, {"op": "set_item_props", "item_id": clip["id"],
                        "fades": {"in_us": clip["duration_us"], "out_us": 0}})
    over = undone(faded, {"op": "move_item", "item_id": clip["id"], "track_id": "V2", "start_us": 0})
    assert item(over, clip["id"])["fades"]["in_us"] <= item(over, clip["id"])["duration_us"]
    # A picture keeps its length.
    doc = apply(doc, {"op": "insert_clip", "media_id": "m3", "source_in_us": 0, "source_out_us": 2 * S})
    picture = main_items(doc)[-1]
    out = undone(doc, {"op": "move_item", "item_id": picture["id"], "track_id": "V2", "start_us": 40 * S})
    assert item(out, picture["id"])["duration_us"] == picture["duration_us"]


def test_a_new_caption_line_never_takes_an_id_the_caller_gave():
    doc = project(clips=1)
    seq = doc["seq"]
    for ids in ([f"c{seq}", None], [None, f"c{seq}"], [f"c{seq}", f"c{seq + 1}", None, None],
                [f"c{seq + 1}", None, f"c{seq}", None], [f"c{seq}", f"c{seq}", None]):
        lines = []
        for n, given in enumerate(ids):
            line = {"in_us": n * S, "out_us": (n + 1) * S, "text": f"line {n}"}
            if given is not None:
                line["id"] = given
            lines.append(line)
        out = undone(doc, {"op": "set_captions", "media_id": "m1", "lines": lines})
        got = [line["id"] for line in out["captions"]["lines"]]
        assert len(set(got)) == len(got), (ids, got)
        for n, given in enumerate(ids):
            if given is not None and given not in ids[:n]:
                assert got[n] == given       # the caller's own id is kept, the first time it appears


def test_a_watermark_takes_only_what_the_render_reads():
    doc = project()
    for key in ("image", "path", "file", "image_path", "src"):
        refused(doc, {"op": "set_branding", "watermark": {"type": "image", key: "D:\\somewhere\\secret.mp4"}},
                "watermark", f"has no '{key}'")
    refused(doc, {"op": "set_branding", "watermark": {"type": "text", "text": "@Me", "x": 2}}, "watermark")
    refused(doc, {"op": "set_branding", "watermark": {"type": "text", "text": "@Me", "position": "C:\\x"}}, "watermark")
    refused(doc, {"op": "set_branding", "watermark": {"type": "text", "text": "@Me", "shadow": "yes"}}, "watermark")
    every = {"type": "both", "text": "@Me", "image_asset": "logo.png", "font": "Arial", "font_size": 42,
             "color": "#FFFFFF", "position": "custom", "x": 0.5, "y": 0.25, "scale": 0.2, "opacity": 0.8,
             "padding": 0.04, "rotation": 15, "shadow": True}
    assert set(every) == set(ops.WATERMARK_KEYS)
    assert undone(doc, {"op": "set_branding", "watermark": every})["branding"]["watermark"] == every


def test_move_track():
    doc = project(clips=1)
    doc = apply(doc, {"op": "add_track", "kind": "video"}, {"op": "add_track", "kind": "video"},
                {"op": "add_track", "kind": "text"}, {"op": "add_track", "kind": "audio"},
                {"op": "add_track", "kind": "audio"})

    def order(d):
        return [t["id"] for t in d["tracks"]]

    assert order(doc) == ["V1", "V2", "V3", "T1", "T2", "A1", "A2", "A3", "A4"]
    assert order(undone(doc, {"op": "move_track", "track_id": "V3", "index": 1})) == \
        ["V1", "V3", "V2", "T1", "T2", "A1", "A2", "A3", "A4"]
    assert order(undone(doc, {"op": "move_track", "track_id": "T2", "index": 0})) == \
        ["V1", "V2", "V3", "T2", "T1", "A1", "A2", "A3", "A4"]
    out = undone(doc, {"op": "move_track", "track_id": "A2", "index": 3})
    assert order(out) == ["V1", "V2", "V3", "T1", "T2", "A1", "A3", "A4", "A2"]
    assert order(undone(out, {"op": "move_track", "track_id": "A4", "index": 1})) == \
        ["V1", "V2", "V3", "T1", "T2", "A1", "A4", "A3", "A2"]
    assert ops.apply(doc, [{"op": "move_track", "track_id": "A3", "index": 2}], ENV).changed is False
    # V1 and A1 stay first of their kind; a track stays among its own kind.
    refused(doc, {"op": "move_track", "track_id": "V1", "index": 1}, "track_id", "can't be moved")
    refused(doc, {"op": "move_track", "track_id": "A1", "index": 2}, "track_id")
    refused(doc, {"op": "move_track", "track_id": "V2", "index": 0}, "index", "from 1 to 2")
    refused(doc, {"op": "move_track", "track_id": "A2", "index": 0}, "index")
    refused(doc, {"op": "move_track", "track_id": "A2", "index": 4}, "index", "from 1 to 3")
    refused(doc, {"op": "move_track", "track_id": "T1", "index": -1}, "index", "from 0 to 1")
    refused(doc, {"op": "move_track", "track_id": "T1", "index": 2}, "index")
    refused(doc, {"op": "move_track", "track_id": "T9", "index": 0}, "track_id")
    refused(doc, {"op": "move_track", "track_id": "T1"}, "index", "is missing")
    refused(project(), {"op": "move_track", "track_id": "A2", "index": 0}, "index")
    assert ops.apply(project(), [{"op": "move_track", "track_id": "A2", "index": 1}], ENV).changed is False
    # The items go with their track, and the track's own settings.
    held = apply(doc, {"op": "add_text", "content": "Hi", "start_us": 0, "duration_us": S, "track_id": "T2"},
                 {"op": "set_track_props", "track_id": "T2", "name": "Titles"})
    out = undone(held, {"op": "move_track", "track_id": "T2", "index": 0})
    assert out["tracks"][3]["name"] == "Titles" and len(out["tracks"][3]["items"]) == 1
