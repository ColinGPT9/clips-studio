"""Editor suite step 5: an AI clip as a project and back
(video_editor/project/importer.py and projection.py).

to_clip(import_clip(x)) gives x back, exactly as it was saved, for every
clip shape the app makes today: the render options the step-0 snapshot cases
recorded (tests/snapshots/render_argv/), the edits the editor saves
(video_editor/timeline.py), and what a re-render adds (server/jobs.py
_rerender_clip). A changed project is written back with only what changed,
or is said to need the export compiler. Standard library only: runs on the
CI light install.
"""

import copy
import json
from pathlib import Path

import pytest
from project_helpers import ENV, apply

from video_editor import timeline
from video_editor.project import importer, model, ops, projection, store

ROOT = Path(__file__).resolve().parent.parent
SNAPSHOTS = ROOT / "tests" / "snapshots" / "render_argv"
S = 1_000_000

# tests/test_render_argv_snapshots.py PIPELINE_CASES: what core/pipeline.py
# saved on the clip in each (the snapshot's "render_opts").
PIPELINE_CASES = (
    "plain", "captions", "word_highlight", "keep_range_cuts", "speed", "music", "hook",
    "text_watermark", "image_watermark", "filter_adjust", "letterbox", "center_crop",
    "landscape", "vertical_live", "gaming_layout",
)


def _recorded(case: str):
    found = []

    def look(value):
        if isinstance(value, dict):
            for key, inner in value.items():
                if key == "render_opts":
                    found.append(inner)
                else:
                    look(inner)
        elif isinstance(value, list):
            for inner in value:
                look(inner)

    look(json.loads((SNAPSHOTS / f"{case}.json").read_text(encoding="utf-8")))
    assert found, f"{case}.json records no render_opts"
    return found[0]


def _clip(render_opts, start: float = 120.0, end: float = 150.0) -> dict:
    return {"video_id": "vid1", "start": start, "end": end, "render_opts": render_opts, "title": "A clip"}


# What the editor saves under "edit" (timeline.py's data model, and the
# edits tests/test_render_argv_snapshots.py renders).
EDITS = {
    "cuts": {"keep": [[0.0, 3.2], [4.6, 10.0]]},
    "one_range": {"keep": [[5.0, 20.0]]},
    "whole_clip_keep": {"keep": [[0.0, 30.0]]},
    "nothing_kept": {"keep": [[1.0, 1.1]], "volume": 0.5},
    "overlapping_cuts": {"keep": [[10.0, 20.0], [0.0, 4.0], [3.5, 6.0], [50.0, 60.0]]},
    "mutes": {"mutes": [[1.0, 1.4], [6.2, 6.55]],
              "muted_words": [{"start": 1.0, "end": 1.4, "word": "darn"}, {"start": 6.2, "end": 6.55, "word": "heck"}]},
    "speed": {"speed": 2.5},
    "speed_clamped": {"speed": 9, "keep": [[0.0, 10.0], [12.0, 30.0]]},
    "slow_with_fades": {"speed": 0.5, "fade_in": 3.0, "fade_out": 3.0},
    "volume_fades": {"volume": 1.5, "fade_in": 0.5, "fade_out": 1.25},
    "mute_all": {"mute_all": True, "fade_out": 1.0},
    "music_file": {"keep": [[0.0, 4.0], [5.0, 10.0]], "music": {"path": "<TMP>/music.mp3", "volume": 0.3, "duck": True}},
    "music_library": {"music": {"library": "music-doodles", "volume": 0.5, "duck": False}, "fade_out": 1.0},
    "hook": {"hook": {"text": "Wait for the ending", "seconds": 3}},
    "hook_emoji_braces": {"hook": {"text": "  {\\b1}Did that just happen?! 🎉  ", "seconds": 99}},
    "everything": {"keep": [[0.5, 12.25], [14.0, 29.5]], "mutes": [[2.0, 2.5]], "volume": 0.8, "mute_all": False,
                   "fade_in": 0.25, "fade_out": 0.75, "speed": 1.25, "hook": {"text": "Watch this", "seconds": 4.5},
                   "music": {"library": "music-roof-tops", "volume": 0.2, "duck": True},
                   "muted_words": [{"start": 2.0, "end": 2.5, "word": "x", "index": 14}]},
    "junk": {"keep": "all", "mutes": [["a", 1], [3]], "volume": "loud", "hook": {"text": ""}, "music": {"path": ""},
             "muted_words": ["x", {"start": "a"}], "future": {"key": 1}},
    "noop": {"volume": 1.0, "speed": 1.0},
}

# What a re-render and the editor's other tabs add beside "edit".
OPTIONS = {
    "empty": {},
    "captions_off": {"captions": False, "job_captions": True},
    "caption_style": {"captions": True, "caption_style": {"font": "Bangers", "font_size": 96, "highlight": True,
                                                         "animation": "pop", "safe_zone": "tiktok"}},
    "caption_lines": {"caption_lines": [
        {"start": 0.0, "end": 1.5, "text": "one two three", "words": [{"w": "one", "s": 0.0, "e": 0.4}]},
        {"start": 1.5, "end": 3.25, "text": "four {five}", "speaker": 1},
        {"start": 29.0, "end": 31.0, "text": "past the end"}]},
    "speaker_edits": {"speaker_edits": [[0.0, 4.5, 1], [10.0, 12.0, 0], [40.0, 45.0, 1]]},
    "speaker_edits_junk": {"speaker_edits": [[0.0, 4.5, 1], ["a", 2, 0], [5.0, 4.0, 1], [1, 2]], "captions": True},
    "crop_and_look": {"crop": "bias_left", "filter": "vibrant", "adjust": {"contrast": 1.1, "tint": -0.2}},
    "gaming_off": {"gaming": None, "job_captions": False},
    "gaming_by_clip": {"gaming": {"panels": [[0.7, 0.0, 0.3, 1.0]], "cam": [0.0, 0.62, 0.26, 0.38], "by": "clip",
                                  "layout": "split"}},
    "watermark_off": {"watermark": None},
    "watermark_both": {"watermark": {"type": "both", "text": "@YourChannel", "image_asset": "logo.png",
                                     "position": "top_right", "opacity": 0.85}},
    "normalize_off": {"normalize_audio": False},
    "sport": {"sport": "basketball", "sport_moment": 4.2, "stop_by": 31.5, "sport_screen": {"x": 0.1},
              "speaker_turns": [[0.0, 3.0, 0]], "framing": "wide", "whole": True},
    "unknown_keys": {"a_future_key": {"nested": [1, 2.5, None, True]}, "post_style": "old", "edit": {"future": 1}},
    "edit_null": {"edit": None, "crop": "center"},
    "all_at_once": {"edit": EDITS["everything"], "crop": "center", "captions": True,
                    "caption_style": {"highlight": True}, "caption_lines": [{"start": 1.0, "end": 2.0, "text": "hi"}],
                    "speaker_edits": [[1.0, 2.0, 1]], "filter": "warm", "watermark": {"type": "text", "text": "@Me"},
                    "job_captions": True, "normalize_audio": True, "profile_note": "kept"},
}

# The saved value itself in the forms a clip's row can hold.
RAW = {"blank": "", "none": None, "json_text": json.dumps({"edit": {"speed": 1.25}, "crop": "center"}),
       "unreadable": "{not json", "not_an_object": "[1, 2]"}

MEDIA = (None, {"fps": {"num": 30000, "den": 1001}, "duration_us": 3600 * S, "width": 1920, "height": 1080},
         {"fps": 60, "duration_us": 100 * S, "name": "Shorter than the clip says"}, {"fps": "fast"})


def _fixtures() -> dict:
    out = {f"pipeline_{case}": _clip(_recorded(case), 0.0, 10.0) for case in PIPELINE_CASES}
    out.update({f"edit_{name}": _clip({"edit": edit, "job_captions": False}) for name, edit in EDITS.items()})
    out.update({f"opts_{name}": _clip(opts) for name, opts in OPTIONS.items()})
    out.update({f"raw_{name}": _clip(raw) for name, raw in RAW.items()})
    out["window_odd"] = _clip({"edit": {"keep": [[0.1, 7.77], [9.03, 21.119]]}}, 3723.47, 3744.59)
    out["window_at_zero"] = _clip({"edit": {"mutes": [[0.0, 0.5]]}}, 0.0, 61.37)
    return out


FIXTURES = _fixtures()


def _exactly(a, b) -> bool:
    """Equal, with every number of the same kind (1 is not 1.0 is not True)."""
    return a == b and json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)


def test_the_fixtures_cover_every_snapshot_case():
    assert len(PIPELINE_CASES) == 15 and len(FIXTURES) > 50
    kinds = {type(f["render_opts"]).__name__ for f in FIXTURES.values()}
    assert kinds == {"dict", "str", "NoneType"}


@pytest.mark.parametrize("name", sorted(FIXTURES))
def test_a_clip_made_a_project_and_back_is_the_clip_as_it_was_saved(name):
    clip = FIXTURES[name]
    saved = copy.deepcopy(clip)
    for media in MEDIA:
        doc = importer.import_clip(clip, media)
        assert clip == saved, "import_clip changed the clip it was given"
        assert model.validate(doc) == []
        for exact_first in (True, False):
            out = projection.to_clip(doc, exact_first=exact_first)
            assert out["branch"] == "A", (name, media, out)
            assert _exactly(out, {"branch": "A", "start": clip["start"], "end": clip["end"],
                                  "render_opts": clip["render_opts"]}), (name, media, exact_first)
        # The same after being written and read again.
        assert projection.to_clip(json.loads(model.canonical(doc))) == out


@pytest.mark.parametrize("name", sorted(FIXTURES))
def test_an_imported_project_takes_an_edit_and_gives_it_back(name):
    """Every imported shape is a project the operations work on: a change
    and its undo give the clip back as it was saved."""
    clip = FIXTURES[name]
    doc = importer.import_clip(clip, MEDIA[1])
    first = model.track(doc, "V1")["items"][0]
    result = ops.apply(doc, [{"op": "set_item_props", "item_id": first["id"], "framing": {"crop": "letterbox"}},
                             {"op": "set_project_props", "name": "Renamed"}], ENV)
    back = ops.apply(result.document, result.inverse, ENV, internal=True).document
    assert model.to_bytes(back) == model.to_bytes(doc)
    assert _exactly(projection.to_clip(back)["render_opts"], clip["render_opts"])


def test_what_the_project_holds_for_a_clip():
    clip = _clip(OPTIONS["all_at_once"])
    doc = importer.import_clip(clip, MEDIA[1])
    main = model.track(doc, "V1")["items"]
    assert [(i["source_in_us"], i["source_out_us"]) for i in main] == [(120_500_000, 132_250_000),
                                                                       (134 * S, 149_500_000)]
    assert all(i["speed"] == 1.25 and i["framing"] == {"crop": "center"} and i["color"] == {"filter": "warm"}
               for i in main)
    assert main[0]["fades"] == {"in_us": 250_000, "out_us": 0} and main[-1]["fades"] == {"in_us": 0, "out_us": 750_000}
    own = model.track(doc, "A1")
    assert (own["volume"], own["mute"]) == (0.8, False)
    hook = model.track(doc, "T1")["items"][0]
    assert hook["text"] == {"content": "Watch this", "role": "hook"} and hook["duration_us"] == 4_500_000
    music = model.track(doc, "A2")["items"][0]
    assert (music["role"], music["loop"], music["duck"], music["volume"]) == ("music", True, True, 0.2)
    assert doc["media"][music["media_id"]]["source"] == {"type": "library", "id": "music-roof-tops"}
    captions = doc["captions"]
    assert captions["mutes"] == [{"in_us": 122 * S, "out_us": 122_500_000, "mode": "mute"}]       # in source time
    assert captions["muted_words"] == [{"word": "x", "index": 14, "in_us": 122 * S, "out_us": 122_500_000}]
    assert captions["enabled"] is True and captions["style"] == {"highlight": True}
    assert [(line["in_us"], line["out_us"], line["text"]) for line in captions["lines"]] == [(121 * S, 122 * S, "hi")]
    assert captions["speaker_edits"] == [{"in_us": 121 * S, "out_us": 122 * S, "speaker": 1}]
    assert doc["branding"] == {"watermark": {"type": "text", "text": "@Me"}}
    assert doc["context"]["job"] == {"job_captions": True}
    assert doc["extra"]["legacy"] == {"render_opts": {"profile_note": "kept"}, "edit": {}}
    assert doc["context"]["clip"] == {"start_us": 120 * S, "end_us": 150 * S}
    assert doc["canvas"]["fps"] == {"num": 30000, "den": 1001} and doc["name"] == "A clip"
    landscape = importer.import_clip(_clip({"profile": "short_clips"}))
    assert (landscape["canvas"]["width"], landscape["canvas"]["height"]) == (1920, 1080)


def test_a_clip_that_cant_be_a_project_says_so():
    for bad in ({}, {"start": 5, "end": 5}, {"start": 9, "end": 3}, {"start": -1, "end": 3}, {"start": "a", "end": 3}):
        with pytest.raises(importer.ClipImportError):
            importer.import_clip({"video_id": "v", "render_opts": {}, **bad})


def test_times_inside_what_is_carried_verbatim_are_nobodys_business():
    """5-D8: a key ending in "_us" inside a Gaming layout, a caption line or
    a muted word is kept as it is, not read as a time of the document's."""
    clip = _clip({"gaming": {"cam": [0, 0, 1, 1], "dwell_us": 1.5, "by": "clip"},
                  "caption_lines": [{"start": 0, "end": 1, "text": "a", "words": [{"start_us": 0.5}]}],
                  "edit": {"muted_words": [{"start": 1.0, "end": 2.0, "held_us": "x"}], "mutes": [[1.0, 2.0]]},
                  "adjust": {"fade_us": -1}, "watermark": {"type": "text", "delay_us": 2.5}})
    doc = importer.import_clip(clip)
    assert _exactly(projection.to_clip(doc)["render_opts"], clip["render_opts"])
    # The document's own times are still checked.
    doc["tracks"][0]["items"][0]["fades"]["in_us"] = 0.5
    assert model.validate(doc)
    doc = importer.import_clip(clip)
    doc["tracks"][0]["items"][0]["held_us"] = 1.5
    assert any("held_us" in p for p in model.validate(doc))


# ---- a changed project, still within today's render (Branch A) ----------------------------------


def _changed(clip: dict, *op_list: dict, media=MEDIA[1]) -> dict:
    doc = importer.import_clip(clip, media)
    out = projection.to_clip(apply(doc, *op_list))
    assert out["branch"] == "A", out
    return out


def test_only_what_changed_is_written_new():
    clip = _clip(OPTIONS["all_at_once"])
    out = _changed(clip, {"op": "set_track_props", "track_id": "A1", "volume": 0.4})
    wanted = copy.deepcopy(clip["render_opts"])
    wanted["edit"]["volume"] = 0.4
    assert _exactly(out, {"branch": "A", "start": 120.0, "end": 150.0, "render_opts": wanted})

    out = _changed(clip, {"op": "set_item_props", "item_id": "i2", "framing": {"crop": "letterbox"}},
                   {"op": "set_item_props", "item_id": "i3", "framing": {"crop": "letterbox"}},
                   {"op": "set_captions", "enabled": False}, {"op": "set_branding", "watermark": None},
                   {"op": "set_project_props", "normalize_audio": False})
    wanted = copy.deepcopy(clip["render_opts"])
    wanted.update(crop="letterbox", captions=False, watermark=None, normalize_audio=False)
    assert _exactly(out["render_opts"], wanted)

    out = _changed(clip, {"op": "set_text", "item_id": "i4", "content": "New hook"},
                   {"op": "trim_item", "item_id": "i4", "end_us": 2 * S},
                   {"op": "set_item_props", "item_id": "i6", "volume": 0.6, "duck": False},
                   {"op": "set_branding", "clear": True}, {"op": "set_captions", "style": None, "enabled": None})
    wanted = copy.deepcopy(clip["render_opts"])
    wanted["edit"]["hook"] = {"text": "New hook", "seconds": 2.0}
    wanted["edit"]["music"] = {"library": "music-roof-tops", "volume": 0.6, "duck": False}
    for key in ("watermark", "caption_style", "captions"):
        del wanted[key]
    assert _exactly(out["render_opts"], wanted)


def test_removing_the_hook_and_the_music_removes_their_keys():
    clip = _clip({"edit": {"hook": {"text": "Hi", "seconds": 3}, "music": {"path": "song.mp3", "volume": 0.3},
                           "fade_in": 1.0}})
    out = _changed(clip, {"op": "delete_items", "item_ids": ["i3", "i5"]})
    assert out["render_opts"] == {"edit": {"fade_in": 1.0}}
    plain = _clip({"job_captions": False})
    out = _changed(plain, {"op": "add_text", "content": "A hook", "start_us": 0, "duration_us": 3 * S, "role": "hook"},
                   {"op": "add_media", "library": "music-one"},
                   {"op": "add_audio", "media_id": "m4", "track_id": "A2", "start_us": 0, "role": "music",
                    "loop": True, "duration_us": 30 * S, "volume": 0.25, "duck": True})
    assert out["render_opts"] == {"job_captions": False, "edit": {
        "hook": {"text": "A hook", "seconds": 3.0}, "music": {"library": "music-one", "volume": 0.25, "duck": True}}}


def test_a_cut_inside_the_clip_keeps_the_window_and_its_other_times():
    clip = _clip({"edit": {"mutes": [[2.0, 2.5]], "volume": 0.9}, "speaker_edits": [[1.0, 2.0, 1]]})
    out = _changed(clip, {"op": "split_item", "item_id": "i2", "at_us": 10 * S},
                   {"op": "split_item", "item_id": "i3", "at_us": 12 * S},
                   {"op": "delete_items", "item_ids": ["i3"]})
    assert (out["start"], out["end"]) == (120.0, 150.0)
    edit = out["render_opts"]["edit"]
    assert edit["mutes"] == [[2.0, 2.5]] and edit["volume"] == 0.9
    assert len(edit["keep"]) == 2 and edit["keep"][0][0] == 0.0 and edit["keep"][1][1] == 30.0
    assert abs(edit["keep"][0][1] - 10.0) < 0.05 and abs(edit["keep"][1][0] - 12.0) < 0.05
    assert out["render_opts"]["speaker_edits"] == [[1.0, 2.0, 1]]
    # A cut and nothing removed is no cut at all: the clip as it was saved.
    whole = _changed(clip, {"op": "split_item", "item_id": "i2", "at_us": 10 * S})
    assert _exactly(whole["render_opts"], clip["render_opts"])


def test_a_moved_start_or_end_moves_the_window_and_everything_follows():
    clip = _clip({"edit": {"mutes": [[2.0, 2.5], [20.0, 21.0]], "muted_words": [{"start": 20.0, "end": 21.0, "word": "x"}],
                           "keep": [[0.0, 10.0], [15.0, 30.0]], "volume": 0.9},
                  "caption_lines": [{"start": 4.0, "end": 6.0, "text": "early"}, {"start": 16.0, "end": 17.0, "text": "late"}],
                  "speaker_edits": [[1.0, 2.0, 1]], "crop": "center"})
    out = _changed(clip, {"op": "trim_item", "item_id": "i2", "source_in_us": 125 * S})    # the start, 5 s later
    assert (out["start"], out["end"]) == (125.0, 150.0)
    opts = out["render_opts"]
    assert opts["edit"]["keep"] == [[0.0, 5.0], [10.0, 25.0]]
    assert opts["edit"]["mutes"] == [[15.0, 16.0]]                       # the one before the new start is gone
    assert opts["edit"]["muted_words"] == [{"word": "x", "start": 15.0, "end": 16.0}]
    assert opts["edit"]["volume"] == 0.9 and opts["crop"] == "center"
    assert opts["caption_lines"] == [{"text": "early", "start": 0.0, "end": 1.0}, {"text": "late", "start": 11.0, "end": 12.0}]
    assert opts["speaker_edits"] == [[-4.0, -3.0, 1]]                    # never cut to the clip
    # The project made from that answer plays the same stretches of the video.
    again = importer.import_clip({"video_id": "vid1", **{k: out[k] for k in ("start", "end", "render_opts")}}, MEDIA[1])
    assert [(i["source_in_us"], i["source_out_us"]) for i in model.track(again, "V1")["items"]] == [
        (125 * S, 130 * S), (135 * S, 150 * S)]

    wider = _changed(_clip({"job_captions": False}), {"op": "trim_item", "item_id": "i2", "source_out_us": 155 * S})
    assert (wider["start"], wider["end"]) == (120.0, 155.0) and wider["render_opts"] == {"job_captions": False}
    one = _changed(clip, {"op": "delete_items", "item_ids": ["i3"]})     # one stretch left: no cuts
    assert (one["start"], one["end"]) == (120.0, 130.0) and "keep" not in one["render_opts"]["edit"]


@pytest.mark.parametrize("name", sorted(FIXTURES))
def test_a_changed_project_reads_the_same_after_going_round(name):
    """What a changed project is written back as, imported again, says the
    same things: the same stretches, speed, sound, hook, music and look."""
    clip = FIXTURES[name]
    doc = importer.import_clip(clip, MEDIA[1])
    main = model.track(doc, "V1")["items"]
    first, last = main[0], main[-1]
    changes = [{"op": "set_track_props", "track_id": "A1", "volume": 0.65},
               {"op": "set_item_props", "item_id": first["id"], "fades": {"in_us": 200_000}},
               {"op": "set_audio_mutes", "mutes": [{"in_us": first["source_in_us"] + 300_000,
                                                  "out_us": first["source_in_us"] + 900_000}]},
               {"op": "set_speaker", "in_us": first["source_in_us"], "out_us": first["source_in_us"] + S, "speaker": 1},
               {"op": "set_captions", "enabled": True, "media_id": "m1",
                "lines": [{"in_us": first["source_in_us"] + S, "out_us": first["source_in_us"] + 2 * S, "text": "said"}]},
               {"op": "trim_item", "item_id": last["id"], "source_out_us": last["source_out_us"] - 500_000},
               {"op": "set_branding", "watermark": {"type": "text", "text": "@Me"}}]
    for item in main:
        changes.append({"op": "set_item_props", "item_id": item["id"], "speed": 2,
                        "color": {"filter": "mono"}, "framing": {"crop": "center"}})
    changed = apply(doc, *changes)
    out = projection.to_clip(changed)
    assert out["branch"] == "A", out
    again = importer.import_clip({"video_id": "vid1", **{k: out[k] for k in ("start", "end", "render_opts")}}, MEDIA[1])
    assert projection.to_clip(again)["branch"] == "A"
    was, now = projection._read(changed, []), projection._read(again, [])
    for key in ("window", "keep", "speed", "volume", "mute_all", "fade_in", "fade_out", "mutes", "hook", "music",
                "crop", "gaming", "filter", "adjust", "captions", "caption_style", "speaker_edits", "watermark",
                "normalize_audio"):
        assert was[key] == now[key], (name, key)
    assert [(line["in_us"], line["out_us"], line["text"]) for line in now["caption_lines"]] == [
        (line["in_us"], line["out_us"], line["text"]) for line in was["caption_lines"]]


# ---- a project that needs the compiler (Branch B) -----------------------------------------------


def _needs_compiler(doc: dict, *op_list: dict) -> list[str]:
    out = projection.to_clip(apply(doc, *op_list))
    assert out["branch"] == "B" and out["reasons"] and set(out) == {"branch", "reasons"}, out
    assert all(isinstance(r, str) and r.endswith(".") for r in out["reasons"])
    return out["reasons"]


def test_what_todays_render_cant_do_needs_the_compiler():
    clip = _clip({"edit": {"keep": [[0.0, 10.0], [15.0, 30.0]], "hook": {"text": "Hi", "seconds": 3},
                           "music": {"library": "music-doodles", "volume": 0.3}}})
    doc = importer.import_clip(clip, MEDIA[1])
    assert projection.to_clip(doc)["branch"] == "A"
    cases = {
        "different speeds": [{"op": "set_item_props", "item_id": "i2", "speed": 2}],
        "outside 0.5 to 3": [{"op": "set_item_props", "item_id": i, "speed": 4} for i in ("i2", "i3")],
        "framed or coloured differently": [{"op": "set_item_props", "item_id": "i2", "color": {"filter": "mono"}}],
        "doesn't have": [{"op": "set_item_props", "item_id": i, "framing": {"fit": "fill"}} for i in ("i2", "i3")],
        "moved, scaled or turned": [{"op": "set_item_props", "item_id": "i2", "transform": {"scale": 1.2}}],
        "its own volume": [{"op": "set_item_props", "item_id": "i3", "volume": 0.5}],
        "fade sits between": [{"op": "set_item_props", "item_id": "i2", "fades": {"out_us": 100_000}}],
        "longer than 3 seconds": [{"op": "set_item_props", "item_id": "i2", "fades": {"in_us": 4 * S}}],
        "out of the video's order": [{"op": "move_item", "item_id": "i3", "index": 0}],
        "repeat a part": [{"op": "duplicate_items", "item_ids": ["i2"]}],
        "other than the clip's own video": [{"op": "add_media", "video_id": "another"},
                                            {"op": "insert_clip", "media_id": "m7", "source_in_us": 0, "source_out_us": S}],
        "shorter than a quarter": [{"op": "trim_item", "item_id": "i2", "source_out_us": 120_100_000}],
        "holds an overlay": [{"op": "add_track", "kind": "video"},
                             {"op": "insert_clip", "media_id": "m1", "track_id": "V2", "source_in_us": 0,
                              "source_out_us": S, "start_us": 0}],
        "text besides the hook": [{"op": "add_text", "content": "More", "start_us": 5 * S, "duration_us": S}],
        "hook is styled": [{"op": "set_text", "item_id": "i4", "size": 90}],
        "sound besides one music": [{"op": "add_track", "kind": "audio"},
                                    {"op": "add_audio", "media_id": "m5", "track_id": "A3", "start_us": 0,
                                     "role": "sfx", "source_out_us": S}],
        "doesn't play looped": [{"op": "set_track_props", "track_id": "A2", "volume": 0.5}],
        "is soloed": [{"op": "set_track_props", "track_id": "A2", "solo": True}],
        "master volume": [{"op": "set_project_props", "master_volume": 0.5}],
        "bleeped": [{"op": "set_audio_mutes", "mutes": [{"in_us": 121 * S, "out_us": 122 * S, "mode": "bleep"}]}],
        "size or frame rate": [{"op": "set_canvas", "preset": "1:1"}],
        "main track is switched off": [{"op": "set_track_props", "track_id": "V1", "mute": True}],
        "main track is empty": [{"op": "delete_items", "item_ids": ["i2", "i3"]}],
    }
    for said, changes in cases.items():
        reasons = _needs_compiler(doc, *changes)
        assert any(said in r for r in reasons), (said, reasons)
    frame_rate = _needs_compiler(doc, {"op": "set_canvas", "fps": 60})
    assert "size or frame rate" in frame_rate[0]
    # The export preset is applied to the finished clip: it never decides.
    preset = apply(doc, {"op": "set_export_preset", "preset": {"codec": "h264", "height": 720}})
    assert _exactly(projection.to_clip(preset)["render_opts"], clip["render_opts"])


def test_a_project_not_made_from_a_clip_needs_the_compiler():
    assert projection.to_clip(model.new_project()) == {"branch": "B", "reasons": ["This project wasn't made from a clip."]}
    doc = importer.import_clip(_clip({}))
    del doc["context"]["imported"]
    assert projection.to_clip(doc)["branch"] == "B"
    assert projection.to_clip({"schema": 1})["branch"] == "B"
    doc = importer.import_clip(_clip({}))
    doc["tracks"] = []
    assert "isn't valid" in projection.to_clip(doc)["reasons"][0]


def test_to_clip_never_changes_the_project():
    doc = apply(importer.import_clip(_clip(OPTIONS["all_at_once"]), MEDIA[1]),
                {"op": "set_track_props", "track_id": "A1", "volume": 0.4})
    before = model.canonical(doc)
    projection.to_clip(doc)
    assert model.canonical(doc) == before


def test_the_limits_are_todays_renders():
    """projection.py's limits against video_editor/timeline.py's, read from
    what EditList.from_dict does with values past them."""
    def parsed(edit):
        return timeline.EditList.from_dict(edit, duration=100.0)

    assert (parsed({"speed": 0.1}).speed, parsed({"speed": 9}).speed) == projection.SPEED_RANGE
    assert parsed({"fade_in": 99}).fade_in == projection.MAX_FADE_US / S
    hook = parsed({"hook": {"text": "x" * 500, "seconds": 0.1}}).hook
    assert len(hook["text"]) == projection.HOOK_MAX and hook["seconds"] == projection.HOOK_RANGE_US[0] / S
    assert parsed({"hook": {"text": "x", "seconds": 99}}).hook["seconds"] == projection.HOOK_RANGE_US[1] / S
    assert parsed({"music": {"path": "a.mp3", "volume": 5}}).music["volume"] == projection.MUSIC_MAX_VOLUME
    assert timeline.MIN_SEGMENT == projection.MIN_SEGMENT_US / S
    assert parsed({"keep": [[0, 10], [10.005, 20]]}).keep == [(0.0, 20.0)]            # 5 ms apart: joined
    assert len(parsed({"keep": [[0, 10], [10.02, 20]]}).keep) == 2                     # 20 ms apart: two
    assert projection.MERGE_GAP_US == 10_000
    assert set(importer.EDIT_KEYS) == {"keep", "mutes", "muted_words", "volume", "mute_all", "fade_in", "fade_out",
                                       "speed", "hook", "music"}


def test_through_the_store_and_back(db):
    clip = _clip(OPTIONS["all_at_once"])
    made = store.create(db.conn, importer.import_clip(clip, MEDIA[1]), now=0)
    assert _exactly(projection.to_clip(store.get(db.conn, made["id"])["document"])["render_opts"], clip["render_opts"])
    out = store.apply_ops(db.conn, made["id"], 0, [{"op": "set_track_props", "track_id": "A1", "volume": 0.4}], ENV,
                          now=1)
    assert projection.to_clip(out["document"])["render_opts"]["edit"]["volume"] == 0.4
    back = store.undo(db.conn, made["id"], out["revision"], now=2)
    assert _exactly(projection.to_clip(back["document"]), {"branch": "A", "start": 120.0, "end": 150.0,
                                                           "render_opts": clip["render_opts"]})
