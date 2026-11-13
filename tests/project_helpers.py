"""What the project tests share (editor suite step 5): an Env that needs no
other part of the app, documents to work on, and random operations drawn
from a seeded generator. Standard library only, so every test that uses it
runs on the CI light install.
"""

from __future__ import annotations

import random

from video_editor.project import model, ops

RATES = (23.976, 25, 29.97, 30, 50, 59.94, 60)
FONTS = ("Arial", "Bangers", "Montserrat")
FILTERS = ("vibrant", "mono", "warm")
VIDEO_US = 3_600_000_000      # every test video is an hour long
SOUND_US = 90_000_000


class FakeEnv(ops.Env):
    """Any video id is a one-hour 1920x1080 video, any library id a
    90-second sound; "missing" is neither. Fonts and filters are short
    lists; an export preset is accepted unless its codec is "av1"."""

    def media(self, ref: dict) -> dict:
        if "missing" in ref.values():
            raise ValueError("is not in the library")
        if "video_id" in ref:
            return {"kind": "video", "source": {"type": "video", "video_id": ref["video_id"]},
                    "duration_us": VIDEO_US, "width": 1920, "height": 1080, "fps": {"num": 30, "den": 1},
                    "has_audio": True, "name": ref["video_id"]}
        if "library" in ref:
            return {"kind": "audio", "source": {"type": "library", "id": ref["library"]},
                    "duration_us": SOUND_US, "width": 0, "height": 0, "fps": None, "has_audio": True,
                    "name": ref["library"]}
        if ref["asset"].startswith("image"):
            return {"kind": "image", "source": {"type": "asset", "sha256": ref["asset"]}, "duration_us": 0,
                    "width": 800, "height": 600, "fps": None, "has_audio": False, "name": "picture"}
        return {"kind": "audio", "source": {"type": "asset", "sha256": ref["asset"]}, "duration_us": SOUND_US,
                "width": 0, "height": 0, "fps": None, "has_audio": True, "name": "sound"}

    def filter_ok(self, name: str) -> bool:
        return name in FILTERS

    def gaming(self, settings: dict) -> dict:
        if "cam" not in settings:
            raise ValueError("a Gaming layout needs its webcam box")
        return {"cam": settings["cam"], "by": "clip"}

    def font(self, name: str):
        return next((f for f in FONTS if f.casefold() == str(name).casefold()), None)

    def caption_style(self, style: dict) -> dict:
        if any(k not in ("font", "font_size", "highlight") for k in style):
            raise ValueError("has a key the captions don't read")
        return dict(style)

    def export_preset(self, raw):
        if raw in (None, "original"):
            return None
        if not isinstance(raw, dict) or raw.get("codec") == "av1":
            raise ValueError("this PC can't encode that")
        return {"kind": "custom", **raw}


ENV = FakeEnv()


def apply(doc: dict, *op_list: dict) -> dict:
    """The document after the operations (the test's shorthand)."""
    return ops.apply(doc, list(op_list), ENV).document


def project(fps=30, clips: int = 0, clip_us: int = 4_000_000) -> dict:
    """A 9:16 project with a video (m1), a sound (m2) and a picture (m3),
    and `clips` stretches of the video on V1, each `clip_us` long and a
    second apart in the source."""
    doc = model.new_project(name="Test", fps=fps)
    doc = apply(doc, {"op": "add_media", "video_id": "video-a"}, {"op": "add_media", "library": "music-one"},
                {"op": "add_media", "asset": "image-one"})
    for n in range(clips):
        at = n * (clip_us + 1_000_000)
        doc = apply(doc, {"op": "insert_clip", "media_id": "m1", "source_in_us": at, "source_out_us": at + clip_us})
    return doc


def main_items(doc: dict) -> list[dict]:
    return model.track(doc, model.MAIN_VIDEO)["items"]


def on_grid(doc: dict) -> bool:
    """V1 starts at 0, has no gaps, and every edge is on the frame grid."""
    fps, edge = doc["canvas"]["fps"], 0
    for item in main_items(doc):
        end = item["start_us"] + item["duration_us"]
        if item["start_us"] != edge or end != model.frame_us(model.frames_at(end, fps), fps):
            return False
        edge = end
    return True


def _items(doc: dict, kind: str | None = None, main: bool | None = None) -> list[tuple[dict, dict]]:
    out = []
    for t in doc["tracks"]:
        if kind is not None and t["kind"] != kind:
            continue
        if main is not None and (t["id"] == model.MAIN_VIDEO) != main:
            continue
        out.extend((t, item) for item in t["items"])
    return out


def random_op(rng: random.Random, doc: dict) -> dict:
    """One operation, drawn at random: usually one that can be applied,
    sometimes one that is refused. Every kind of operation can come up."""
    end = max(model.program_end(doc), 1_000_000)
    everything = _items(doc)
    videos = _items(doc, "video")
    texts = _items(doc, "text")
    tracks = doc["tracks"]

    def any_item(pool):
        return rng.choice(pool)[1]["id"] if pool else "i999"

    def when():
        return rng.randrange(0, end + 2_000_000)

    kind = rng.choice((
        "set_canvas", "set_project_props", "add_media", "insert_clip", "insert_clip", "insert_clip",
        "insert_overlay", "trim_item", "trim_item", "split_item", "split_item", "delete_items", "move_item",
        "move_item", "duplicate_items", "set_item_props", "set_item_props", "add_track", "remove_track",
        "set_track_props", "move_track", "insert_picture", "add_audio", "set_audio_mutes", "add_text", "set_text", "set_captions",
        "edit_caption_words", "set_speaker", "set_branding", "set_export_preset", "batch", "bad"))
    if kind == "set_canvas":
        return rng.choice(({"op": "set_canvas", "fps": rng.choice(RATES)},
                           {"op": "set_canvas", "preset": rng.choice(list(model.CANVAS_PRESETS))},
                           {"op": "set_canvas", "width": rng.choice((640, 1280, 1081)), "height": 720}))
    if kind == "set_project_props":
        return {"op": "set_project_props", "name": f"Name {rng.randrange(5)}",
                "master_volume": rng.choice((0.5, 1, 1.0, 2, 2.5, 0, 0.0, -0.0))}
    if kind == "add_media":
        return rng.choice(({"op": "add_media", "video_id": f"video-{rng.choice('abc')}"},
                           {"op": "add_media", "library": f"sound-{rng.choice('ab')}"},
                           {"op": "add_media", "asset": "image-two"}, {"op": "add_media", "video_id": "missing"}))
    if kind == "insert_clip":
        a = rng.randrange(0, VIDEO_US - 20_000_000)
        return {"op": "insert_clip", "media_id": "m1", "source_in_us": a,
                "source_out_us": a + rng.randrange(1, 12_000_000), "index": rng.randrange(0, len(main_items(doc)) + 2),
                "speed": rng.choice((1, 1.0, 0.25, 0.5, 1.5, 2, 4, 5))}
    if kind == "insert_overlay":
        picture = rng.random() < 0.5
        op = {"op": "insert_clip", "media_id": "m3" if picture else "m1", "track_id": rng.choice(("V2", "V1", "T1")),
              "source_in_us": 0, "source_out_us": rng.randrange(200_000, 5_000_000), "start_us": when()}
        return op
    if kind == "insert_picture":
        # A picture on the main track, often only a few frames long.
        return {"op": "insert_clip", "media_id": "m3", "source_in_us": 0,
                "source_out_us": rng.choice((rng.randrange(40_000, 400_000), rng.randrange(400_000, 5_000_000))),
                "index": rng.randrange(0, len(main_items(doc)) + 1)}
    if kind == "trim_item":
        t, item = rng.choice(everything) if everything else (None, {"id": "i999"})
        if t is None:
            return {"op": "trim_item", "item_id": "i999", "source_in_us": 0}
        if t["kind"] == "text" or item.get("loop") or doc["media"][item["media_id"]]["kind"] == "image":
            return {"op": "trim_item", "item_id": item["id"], "end_us": item["start_us"] + rng.randrange(1, 6_000_000)}
        op = {"op": "trim_item", "item_id": item["id"]}
        if rng.random() < 0.5:
            op["source_in_us"] = max(0, item["source_in_us"] + rng.randrange(-2_000_000, 2_000_000))
        else:
            op["source_out_us"] = max(0, item["source_out_us"] + rng.randrange(-2_000_000, 2_000_000))
        return op
    if kind == "split_item":
        if not everything:
            return {"op": "split_item", "item_id": "i999", "at_us": 5}
        _t, item = rng.choice(everything)
        return {"op": "split_item", "item_id": item["id"],
                "at_us": item["start_us"] + rng.randrange(0, item["duration_us"] + 1)}
    if kind == "delete_items":
        ids = [item["id"] for _t, item in rng.sample(everything, min(len(everything), rng.randrange(1, 3)))]
        return {"op": "delete_items", "item_ids": ids or ["i999"]}
    if kind == "move_item":
        if not everything:
            return {"op": "move_item", "item_id": "i999", "index": 0}
        t, item = rng.choice(everything)
        if t["id"] == model.MAIN_VIDEO and rng.random() < 0.7:
            return {"op": "move_item", "item_id": item["id"], "index": rng.randrange(0, len(t["items"]) + 1)}
        target = rng.choice([x["id"] for x in tracks])
        op = {"op": "move_item", "item_id": item["id"], "track_id": target}
        if target != model.MAIN_VIDEO:
            op["start_us"] = when()
        return op
    if kind == "duplicate_items":
        return {"op": "duplicate_items", "item_ids": [any_item(everything)]}
    if kind == "set_item_props":
        op = {"op": "set_item_props", "item_id": any_item(everything)}
        which = rng.choice(("speed", "volume", "fades", "framing", "transform", "color", "link", "duck", "extra"))
        op[which] = {
            "speed": rng.choice((0.25, 0.5, 1, 1.25, 2, 4, 0.1)),
            "volume": rng.choice((0, 0.0, -0.0, 0.5, 1, 2, 3)),
            "fades": {"in_us": rng.randrange(0, 600_000), "out_us": rng.randrange(0, 600_000)},
            "framing": rng.choice(({"crop": "center"}, {"gaming": {"cam": [0, 0, 0.5, 0.5]}}, {"gaming": None},
                                   {"fit": "fill", "align": "top"}, {"crop": "sideways"})),
            "transform": rng.choice(({"x": 0.25, "scale": 1.5}, {"opacity": 0.5}, {"scale": 100}, {"x": 0.0},
                                     {"x": -0.0}, {"x": 0, "rotation": -0.0})),
            "color": rng.choice(({"filter": "vibrant"}, {"adjust": {"contrast": 1.2}}, {"filter": "nope"})),
            "link": rng.choice((None, any_item(everything))),
            "duck": rng.random() < 0.5,
            "extra": {"note": rng.choice((0, 1, 2, 0.0, -0.0, [0.0], [-0.0]))},     # kept as given, either zero
        }[which]
        return op
    if kind == "add_track":
        return {"op": "add_track", "kind": rng.choice(("video", "text", "audio")), "name": f"Track {rng.randrange(9)}"}
    if kind == "remove_track":
        return {"op": "remove_track", "track_id": rng.choice(tracks)["id"], "force": rng.random() < 0.5}
    if kind == "set_track_props":
        t = rng.choice(tracks)
        op = {"op": "set_track_props", "track_id": t["id"], "mute": rng.random() < 0.5}
        if rng.random() < 0.6:
            op["volume"] = rng.choice((0, 0.0, -0.0, 0.5, 1, 1.0, 2))
        if rng.random() < 0.3:
            op["solo"] = rng.random() < 0.5
        return op
    if kind == "move_track":
        return {"op": "move_track", "track_id": rng.choice(tracks)["id"], "index": rng.randrange(0, 4)}
    if kind == "add_audio":
        loop = rng.random() < 0.3
        op = {"op": "add_audio", "media_id": rng.choice(("m2", "m1", "m3")),
              "track_id": rng.choice([t["id"] for t in tracks if t["kind"] == "audio"] + ["V1"]),
              "start_us": when(), "role": rng.choice(("music", "sfx", "voice")),
              "source_in_us": 0, "source_out_us": rng.randrange(500_000, 8_000_000), "duck": rng.random() < 0.5}
        if loop:
            op.update(loop=True, duration_us=rng.randrange(1_000_000, 20_000_000))
        return op
    if kind == "set_audio_mutes":
        a = rng.randrange(0, 50_000_000)
        return {"op": "set_audio_mutes", "mutes": [{"in_us": a, "out_us": a + 400_000,
                                                   "mode": rng.choice(("mute", "bleep"))}],
                "muted_words": [{"in_us": a, "out_us": a + 400_000, "word": "darn"}]}
    if kind == "add_text":
        return {"op": "add_text", "content": rng.choice(("Hello", "Wait {for} it\\N", "  ", "x" * 501, "Привет 🎉")),
                "start_us": when(), "duration_us": rng.randrange(0, 4_000_000),
                "font": rng.choice((*FONTS, "Comic Sans")), "size": rng.choice((48, 72, 7)),
                "color": rng.choice(("#ffcc00", "#FFFFFF", "red")), "role": rng.choice(("text", "hook"))}
    if kind == "set_text":
        return {"op": "set_text", "item_id": any_item(texts or videos), "content": f"Text {rng.randrange(9)}",
                "size": rng.choice((None, 64))}
    if kind == "set_captions":
        a = rng.randrange(0, 50_000_000)
        return rng.choice((
            {"op": "set_captions", "enabled": rng.choice((True, False, None))},
            {"op": "set_captions", "style": rng.choice(({"font": "Arial", "font_size": 80}, None, {"bad": 1}))},
            {"op": "set_captions", "media_id": "m1",
             "lines": [{"in_us": a, "out_us": a + 900_000, "text": "one two"},
                       {"in_us": a + 900_000, "out_us": a + 1_800_000, "text": "three"}]}))
    if kind == "edit_caption_words":
        lines = doc["captions"]["lines"] or []
        line = rng.choice(lines)["id"] if lines else "c999"
        return {"op": "edit_caption_words", "edits": [{"line_id": line, "text": rng.choice(("fixed", "", "new words"))}]}
    if kind == "set_speaker":
        a = rng.randrange(0, 50_000_000)
        return rng.choice(({"op": "set_speaker", "in_us": a, "out_us": a + 2_000_000, "speaker": rng.choice((0, 1, 2))},
                           {"op": "set_speaker", "clear": True}))
    if kind == "set_branding":
        return rng.choice(({"op": "set_branding", "watermark": {"type": "text", "text": "@Channel", "opacity": 0.8}},
                           {"op": "set_branding", "watermark": {"type": "text", "text": "@Channel", "x": 0.0, "y": 0.5}},
                           {"op": "set_branding", "watermark": {"type": "text", "text": "@Channel", "x": -0.0, "y": 0.5}},
                           {"op": "set_branding", "watermark": {"type": "image", "path": "logo.png"}},
                           {"op": "set_branding", "watermark": None}, {"op": "set_branding", "clear": True},
                           {"op": "set_branding", "watermark": {"type": "image", "image_asset": "../x.png"}}))
    if kind == "set_export_preset":
        return {"op": "set_export_preset", "preset": rng.choice(("original", None, {"codec": "h264", "height": 720},
                                                                 {"codec": "av1"}))}
    if kind == "batch":
        return {"op": "batch", "ops": [random_op(rng, doc) for _ in range(rng.randrange(1, 4))]}
    return rng.choice(({"op": "explode"}, {"op": "split_item", "item_id": 5, "at_us": 1}, "not an op",
                       {"op": "restore", "patch": ["=", {}]}, {"op": "set_canvas", "fps": 0},
                       {"op": "delete_items", "item_ids": []}, {"op": "trim_item", "item_id": "i1", "bogus": 1}))


OP_NAMES = tuple(ops.SPECS)


def names_in(op) -> set[str]:
    """The operation names in one operation, a batch's included."""
    if not isinstance(op, dict):
        return set()
    out = {op.get("op")}
    for sub in op.get("ops", []) if op.get("op") == "batch" else []:
        out |= names_in(sub)
    return out
