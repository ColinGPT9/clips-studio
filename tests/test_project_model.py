"""Editor suite step 5: the project document (video_editor/project/model.py)
and the patches between two documents (patch.py): what a valid project is,
that it is always written the same way, and that time is whole microseconds.
Standard library only: runs on the CI light install.
"""

import json
import random

import pytest
from project_helpers import ENV, apply, project, random_op

from video_editor.project import model, ops, patch

S = 1_000_000


def test_a_new_project_has_its_tracks_and_is_valid():
    doc = model.new_project(name="Mine", width=1920, height=1080, fps=29.97)
    assert model.validate(doc) == []
    assert [(t["id"], t["kind"]) for t in doc["tracks"]] == [("V1", "video"), ("T1", "text"), ("A1", "audio"),
                                                             ("A2", "audio")]
    assert doc["schema"] == model.SCHEMA == 1 and doc["canvas"]["fps"] == {"num": 30000, "den": 1001}
    assert set(doc) == {"schema", "name", "seq", "canvas", "media", "tracks", "audio", "captions", "branding",
                        "export", "context", "extra"}
    assert doc["audio"] == {"master_volume": 1.0, "normalize": True}
    for t in doc["tracks"]:
        assert ("volume" in t and "solo" in t) == (t["kind"] == "audio")
    assert set(model.new_item("i1")) == set(model.ITEM_KEYS)
    for bad in ({"width": 1081}, {"height": 8}, {"fps": 0}, {"fps": 500}, {"fps": "x"}, {"name": "x" * 121}):
        with pytest.raises(model.ModelError):
            model.new_project(**bad)


def test_the_same_document_is_always_the_same_bytes():
    doc = project(fps=59.94, clips=5)
    text = model.canonical(doc)
    shuffled = json.loads(text)
    rng = random.Random(1)

    def stir(value):
        if isinstance(value, dict):
            keys = list(value)
            rng.shuffle(keys)
            return {k: stir(value[k]) for k in keys}
        if isinstance(value, list):
            return [stir(v) for v in value]
        return value

    for _ in range(5):
        shuffled = stir(shuffled)
        assert model.canonical(shuffled) == text
    assert model.to_bytes(doc) == text.encode("utf-8")
    assert model.to_bytes(model.clone(doc)) == model.to_bytes(doc)
    assert " " not in text.replace("Test", "") and "\n" not in text
    named = apply(doc, {"op": "set_project_props", "name": "Привет 🎉"})
    assert "Привет 🎉" in model.canonical(named)                 # written as itself, not escaped
    with pytest.raises(ValueError):
        model.canonical({"x": float("nan")})


def test_every_time_in_a_document_is_a_whole_number_below_2_to_the_53():
    rng = random.Random(5)
    doc = project(fps=23.976, clips=3)
    for _ in range(300):
        try:
            doc = ops.apply(doc, [random_op(rng, doc)], ENV).document
        except ops.OpError:
            pass

    def walk(value, key=""):
        if isinstance(value, dict):
            for k, inner in value.items():
                walk(inner, k)
        elif isinstance(value, list):
            for inner in value:
                walk(inner, key)
        elif key.endswith("_us"):
            assert type(value) is int and 0 <= value < 2**53, (key, value)

    walk({k: doc[k] for k in ("canvas", "media", "tracks", "audio", "captions")})
    assert model.is_time(0) and model.is_time(2**53 - 1)
    for bad in (-1, 2**53, 1.0, True, "1", None):
        assert not model.is_time(bad)
    assert model.us(1.5) == 1_500_000 and model.us(0.1 + 0.2) == 300_000 and model.seconds(1_234_567) == 1.234567


def _broken(change) -> list[str]:
    doc = apply(project(clips=3), {"op": "add_text", "content": "Hi", "start_us": 0, "duration_us": S},
                {"op": "add_audio", "media_id": "m2", "track_id": "A2", "start_us": 0, "role": "music"})
    assert model.validate(doc) == []
    before = model.canonical(doc)
    change(doc)
    after = model.canonical(doc)
    problems = model.validate(doc)
    assert model.canonical(doc) == after and after != before        # validate never changes what it reads
    return problems


def test_validate_says_what_is_wrong_in_plain_words():
    v1 = lambda d: d["tracks"][0]["items"]   # noqa: E731
    cases = {
        "schema must be": lambda d: d.update(schema="1"),
        "tracks is missing": lambda d: d.pop("tracks"),
        "seq must be": lambda d: d.update(seq=0),
        "canvas.width": lambda d: d["canvas"].update(width=1081),
        "canvas.fps": lambda d: d["canvas"].update(fps=30),
        "needs its V1": lambda d: d["tracks"].pop(0),
        "needs its A1": lambda d: d["tracks"][2].update(kind="text"),
        "its own id": lambda d: d["tracks"][3].update(id="A1"),
        "no gaps": lambda d: v1(d)[1].update(start_us=v1(d)[1]["start_us"] + 33_333),
        "not on the frame grid": lambda d: v1(d)[2].update(duration_us=v1(d)[2]["duration_us"] + 5),
        "whole numbers of microseconds": lambda d: v1(d)[0].update(start_us=0.0),
        "has no length": lambda d: v1(d)[0].update(duration_us=0),
        "speed must be": lambda d: v1(d)[0].update(speed=5),
        "volume must be": lambda d: v1(d)[0].update(volume=-1),
        "fades must be": lambda d: v1(d)[0]["fades"].update(in_us=5 * S),
        "doesn't match its source": lambda d: v1(d)[0].update(source_out_us=v1(d)[0]["source_out_us"] + S),
        "past the end of its media": lambda d: v1(d)[2].update(source_in_us=3599 * S, source_out_us=3603 * S),
        "is not in the project's media": lambda d: v1(d)[0].update(media_id="m9"),
        "used twice": lambda d: v1(d)[1].update(id=v1(d)[0]["id"]),
        "has no extra": lambda d: v1(d)[0].pop("extra"),
        "link must be": lambda d: v1(d)[0].update(link=5),
        "text must be null": lambda d: v1(d)[0].update(text={"content": "x"}),
        "can't go on a video track": lambda d: v1(d)[0].update(media_id="m2"),
        "holds none of its own": lambda d: d["tracks"][2]["items"].append(dict(d["tracks"][3]["items"][0], id="i99")),
        "text.content must be": lambda d: d["tracks"][1]["items"][0]["text"].update(content=""),
        "can't have media": lambda d: d["tracks"][1]["items"][0].update(media_id="m1"),
        "role must be one of": lambda d: d["tracks"][3]["items"][0].update(role="noise"),
        "only music can loop": lambda d: d["tracks"][3]["items"][0].update(role="sfx", loop=True),
        "overlaps the item before": lambda d: d["tracks"][3]["items"].append(
            dict(d["tracks"][3]["items"][0], id="i98", start_us=S)),
        "master_volume": lambda d: d["audio"].update(master_volume=3),
        "audio.normalize": lambda d: d["audio"].update(normalize="yes"),
        "tracks[3].volume": lambda d: d["tracks"][3].update(volume="loud"),
        "solo must be": lambda d: d["tracks"][3].pop("solo"),
        "captions.media_id": lambda d: d["captions"].update(media_id="m9"),
        "captions.mutes[0]": lambda d: d["captions"]["mutes"].append({"in_us": 5, "out_us": 5, "mode": "mute"}),
        "mode must be mute or bleep": lambda d: d["captions"]["mutes"].append({"in_us": 1, "out_us": 5, "mode": "x"}),
        "speaker must be": lambda d: d["captions"]["speaker_edits"].append({"in_us": 1, "out_us": 5, "speaker": 3}),
        "captions.lines[0]": lambda d: d["captions"].update(lines=[{"id": "c1", "in_us": 9, "out_us": 5, "text": "x"}]),
        "export.preset": lambda d: d["export"].update(preset="720p"),
        "media.m1.kind": lambda d: d["media"]["m1"].update(kind="document"),
        "media.m2.source": lambda d: d["media"]["m2"].update(source="C:/x.mp3"),
        "tracks[0].items[0].late_us must be a whole number": lambda d: v1(d)[0].update(late_us=0.5),
    }
    for said, change in cases.items():
        problems = _broken(change)
        assert any(said in p for p in problems), (said, problems)
    assert model.validate("project") == ["a project is an object"]
    with pytest.raises(model.ModelError):
        model.check({"schema": 1})


def test_a_patch_turns_one_document_into_the_other_exactly():
    rng = random.Random(9)
    doc = project(clips=4)
    for _ in range(200):
        try:
            new = ops.apply(doc, [random_op(rng, doc)], ENV).document
        except ops.OpError:
            continue
        for a, b in ((doc, new), (new, doc)):
            node = patch.diff(a, b, strict=True)
            assert model.canonical(patch.apply(a, node)) == model.canonical(b)
            assert json.loads(json.dumps(node)) == node                  # a patch is plain JSON
        doc = new
    assert patch.diff(doc, model.clone(doc)) is None and patch.apply(doc, None) is doc
    # 1, 1.0 and True are equal to Python and are not the same bytes.
    assert patch.diff({"a": [1, {"b": 1.0}]}, {"a": [1.0, {"b": True}]}) is None
    exact = patch.diff({"a": [1, {"b": 1.0}]}, {"a": [1.0, {"b": True}]}, strict=True)
    assert model.canonical(patch.apply({"a": [1, {"b": 1.0}]}, exact)) == '{"a":[1.0,{"b":true}]}'
    small = patch.diff(doc, apply(doc, {"op": "set_track_props", "track_id": "A1", "name": "The clip's own sound"}))
    assert len(json.dumps(small)) < 200                                   # only what changed is in it
    for bad, value in ((["?"], {}), (["d", {"x": ["-"]}], {}), (["d", {"x": ["d", {}]}], {}), ("=", {}),
                       (["l", None, {}], {"not": "a list"}), (["l", ["i1"], {}], [])):
        with pytest.raises(patch.PatchError):
            patch.apply(value, bad)


def test_a_number_that_changes_kind_still_undoes_to_the_same_bytes():
    doc = apply(project(clips=1), {"op": "set_item_props", "item_id": "i4", "extra": {"n": 1, "flag": True}})
    result = ops.apply(doc, [{"op": "set_item_props", "item_id": "i4", "extra": {"n": 1.0, "flag": 1}}], ENV)
    assert result.changed and model.canonical(result.document) != model.canonical(doc)
    assert model.canonical(ops.apply(result.document, result.inverse, ENV, internal=True).document) == model.canonical(doc)
    assert model.canonical(ops.apply(doc, result.forward, ENV, internal=True).document) == result.text
