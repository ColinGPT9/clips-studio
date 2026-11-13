"""The operations that change a project: one checked, versioned set for the
manual editor, the AI and MCP alike.

    result = apply(document, ops, env)

apply() is pure. It never changes `document`; it returns a Result with the
new document, the inverse operations that undo the change exactly (applying
them gives back the earlier document byte for byte), the forward operations
that redo it, and warnings. An operation that can't be applied raises
OpError, in plain words that name the operation and the field, and nothing
has changed. Several operations are all or nothing.

An operation is {"op": name, ...fields}. SPECS lists every operation and its
fields (describe() gives the same as JSON, for a caller that wants to show
them or hand them to a model). OPS_VERSION goes up when an operation or a
field changes meaning.

The inverse of every operation is one `restore` operation holding the
difference between the two documents (patch.py). `restore` is internal: only
the store's undo and redo may apply one, never a caller.

What the operations need to know about the rest of Clips Kitty (which fonts
exist, which filters, which media, what this PC can encode) they ask `env`
(Env below; env.py has the real one). Nothing here opens a file.
"""

from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass, field

from video_editor.project import model, patch
from video_editor.project.model import MAIN_AUDIO, MAIN_VIDEO

OPS_VERSION = 1
MAX_OPS = 500            # in one call, counting what is inside a batch
MAX_BATCH_DEPTH = 4
MAX_ITEMS = 5000         # in one project
MAX_TRACKS = 64
MAX_CAPTION_LINES = 20000
MAX_OBJECT_CHARS = 20000  # a framing, style, watermark or extra object, written as JSON

CROPS = ("track", "center", "letterbox", "bias_left", "bias_right")
FITS = ("fit", "fill")
ALIGNS = ("center", "top", "bottom", "left", "right")
TEXT_POSITIONS = ("top", "middle", "bottom")
TRANSFORM = {"x": (-4.0, 4.0), "y": (-4.0, 4.0), "scale": (0.05, 8.0), "rotation": (-360.0, 360.0),
             "opacity": (0.0, 1.0)}
# video/filters.py ADJUST_RANGES; tests/test_project_env.py keeps the two the same.
ADJUST = {"brightness": (-0.5, 0.5), "saturation": (0.0, 3.0), "contrast": (0.5, 2.0),
          "temperature": (-1.0, 1.0), "tint": (-1.0, 1.0), "sharpen": (0.0, 1.0), "vignette": (0.0, 1.0)}
# What video_editor/watermark.py reads; tests/test_project_env.py keeps the two the same.
WATERMARK_KEYS = ("type", "text", "image_asset", "font", "font_size", "color", "position", "x", "y", "scale",
                  "opacity", "padding", "rotation", "shadow")
_COLOUR = re.compile(r"#[0-9A-Fa-f]{6}")
_ASSET_NAME = re.compile(r"[A-Za-z0-9._-]{1,128}")
_ID = re.compile(r"[A-Za-z0-9_.:-]{1,64}")


class OpError(ValueError):
    """An operation that can't be applied. str() is one plain sentence that
    names the operation and, when one field is at fault, the field."""

    def __init__(self, op: str, field_name: str | None, message: str):
        self.op, self.field, self.message = op, field_name, message
        super().__init__(f"{op}: \"{field_name}\" {message}" if field_name else f"{op}: {message}")


class Env:
    """What an operation asks the rest of Clips Kitty. Each answer is plain
    data; each refusal a ValueError in plain words. env.py's AppEnv is the
    real one; tests bring their own."""

    def media(self, ref: dict) -> dict:
        """The facts of one media the project may use, named by {"video_id"},
        {"asset": sha256} or {"library": id}; never by a path. Returns
        {"kind", "source", "duration_us", "width", "height", "fps",
        "has_audio", "name"}."""
        raise ValueError("no media can be added here")

    def filter_ok(self, name: str) -> bool:
        return False

    def gaming(self, settings: dict) -> dict:
        raise ValueError("Gaming layouts aren't available here")

    def font(self, name: str) -> str | None:
        """The registry's own spelling of a font, or None when it has none."""
        return None

    def caption_style(self, style: dict) -> dict:
        raise ValueError("caption styles aren't available here")

    def export_preset(self, raw) -> dict | None:
        """A checked export preset this PC can encode, or None for Original."""
        raise ValueError("export presets aren't available here")


@dataclass
class Result:
    document: dict
    inverse: list
    forward: list
    warnings: list = field(default_factory=list)
    created: list = field(default_factory=list)   # [{"kind": "item" | "media" | "track", "id": ...}]
    changed: bool = True
    text: str = ""                                # the new document as it is written (model.canonical)


@dataclass
class _Ctx:
    env: Env
    internal: bool
    warnings: list = field(default_factory=list)
    created: list = field(default_factory=list)
    count: int = 0


# ---- the operations and their fields ------------------------------------------------------------
#
# name -> (what it does, {field: (kind, required, what it is)}). Kinds: id,
# time (whole microseconds), int, num, bool, str, dict, list, any.

SPECS: dict[str, tuple[str, dict]] = {
    "set_canvas": ("Change the picture's size or frame rate. A new frame rate moves the main track onto its grid.", {
        "preset": ("str", False, "9:16, 16:9, 1:1 or 4:5 (instead of width and height)"),
        "width": ("int", False, "even, 16 to 4096"),
        "height": ("int", False, "even, 16 to 4096"),
        "fps": ("any", False, "1 to 240: a number (23.976, 29.97 and 59.94 are the broadcast rates) or {num, den}"),
    }),
    "set_project_props": ("Change the project's name or its master sound.", {
        "name": ("str", False, "at most 120 characters"),
        "master_volume": ("num", False, "0 to 2"),
        "normalize_audio": ("bool", False, "even out the loudness of the finished video"),
    }),
    "add_media": ("Add a video, a sound or a picture the project may use. Exactly one of the three names it.", {
        "video_id": ("str", False, "a video in the library"),
        "asset": ("str", False, "the sha256 of an imported file"),
        "library": ("str", False, "the id of a free track or sound effect"),
    }),
    "insert_clip": ("Put a stretch of a video (or a picture) on a video track.", {
        "media_id": ("id", True, "the media to take it from"),
        "source_in_us": ("time", True, "where the stretch starts in the media"),
        "source_out_us": ("time", True, "where it ends"),
        "track_id": ("id", False, "V1 (the default) or an overlay track"),
        "index": ("int", False, "on V1: where among its items (the default is the end)"),
        "start_us": ("time", False, "on an overlay track: when it starts"),
        "speed": ("num", False, "0.25 to 4 (the default is 1)"),
    }),
    "trim_item": ("Move an item's start or end.", {
        "item_id": ("id", True, "the item"),
        "source_in_us": ("time", False, "a clip or sound: the new start in its media"),
        "source_out_us": ("time", False, "a clip or sound: the new end in its media"),
        "start_us": ("time", False, "text, a picture or looped music: the new start in the video"),
        "end_us": ("time", False, "text, a picture or looped music: the new end in the video"),
    }),
    "split_item": ("Cut an item in two at a time in the video.", {
        "item_id": ("id", True, "the item"),
        "at_us": ("time", True, "where to cut, strictly inside the item"),
    }),
    "delete_items": ("Remove items. On V1 the items after them close the gap.", {
        "item_ids": ("list", True, "the items"),
    }),
    "move_item": ("Move an item: to another place on its track, or to another track of the same kind.", {
        "item_id": ("id", True, "the item"),
        "track_id": ("id", False, "the track to move it to (the default is its own)"),
        "index": ("int", False, "on V1: where among its items"),
        "start_us": ("time", False, "on any other track: when it starts"),
    }),
    "duplicate_items": ("Copy items. Each copy comes right after its original.", {
        "item_ids": ("list", True, "the items"),
    }),
    "set_item_props": ("Change how an item plays or looks.", {
        "item_id": ("id", True, "the item"),
        "speed": ("num", False, "0.25 to 4"),
        "volume": ("num", False, "0 to 2"),
        "fades": ("dict", False, "{in_us, out_us}"),
        "framing": ("dict", False, "{crop} (track, center, letterbox, bias_left, bias_right), {gaming} "
                                   "(a Gaming layout, or null for off) or {fit, align}"),
        "transform": ("dict", False, "{x, y, scale, rotation, opacity}"),
        "color": ("dict", False, "{filter, adjust}"),
        "link": ("any", False, "the id of an item this one belongs with, or null"),
        "loop": ("bool", False, "music only"),
        "duck": ("bool", False, "a sound: dip under the video's own sound"),
        "extra": ("dict", False, "anything else, kept as it is"),
    }),
    "add_track": ("Add a track.", {
        "kind": ("str", True, "video, text or audio"),
        "name": ("str", False, "at most 120 characters"),
    }),
    "remove_track": ("Remove a track. Never V1 or A1.", {
        "track_id": ("id", True, "the track"),
        "force": ("bool", False, "remove it with its items (an empty track needs no force)"),
    }),
    "set_track_props": ("Change a track's name, or switch it off. An audio track also has a volume and solo.", {
        "track_id": ("id", True, "the track"),
        "name": ("str", False, "at most 120 characters"),
        "mute": ("bool", False, "switched off"),
        "volume": ("num", False, "audio only: 0 to 2"),
        "solo": ("bool", False, "audio only"),
    }),
    "move_track": ("Put a track at another place among the tracks of its kind. A later video or text track "
                   "is on top. Never V1 or A1, which stay first.", {
        "track_id": ("id", True, "the track"),
        "index": ("int", True, "its new place among the tracks of its kind, counted from 0"),
    }),
    "add_audio": ("Put music, a sound effect or a voice on an audio track (A2 and up).", {
        "media_id": ("id", True, "the sound"),
        "track_id": ("id", True, "an audio track, not A1"),
        "start_us": ("time", True, "when it starts"),
        "role": ("str", True, "music, sfx or voice"),
        "source_in_us": ("time", False, "where it starts in the sound (the default is 0)"),
        "source_out_us": ("time", False, "where it ends (the default is the sound's end)"),
        "duration_us": ("time", False, "looped music: how long it plays"),
        "volume": ("num", False, "0 to 2 (the default is 1)"),
        "loop": ("bool", False, "music only: repeat for duration_us"),
        "duck": ("bool", False, "dip under the video's own sound"),
        "fades": ("dict", False, "{in_us, out_us}"),
    }),
    "set_audio_mutes": ("Say which stretches of the source's sound are silenced or bleeped. Replaces the list.", {
        "mutes": ("list", True, "[{in_us, out_us, mode}], mode mute or bleep, in the source's time"),
        "muted_words": ("list", False, "[{in_us, out_us, word}]: which words those are, for the editor"),
    }),
    "add_text": ("Put text on a text track.", {
        "content": ("str", True, "1 to 500 characters"),
        "start_us": ("time", True, "when it appears"),
        "duration_us": ("time", True, "how long it stays"),
        "track_id": ("id", False, "a text track (the default is the first)"),
        "role": ("str", False, "text or hook"),
        "font": ("str", False, "a caption font"),
        "size": ("int", False, "8 to 400"),
        "color": ("str", False, "#RRGGBB"),
        "position": ("str", False, "top, middle or bottom"),
    }),
    "set_text": ("Change a text item. null puts a look back to its default.", {
        "item_id": ("id", True, "the text item"),
        "content": ("str", False, "1 to 500 characters"),
        "role": ("str", False, "text or hook"),
        "font": ("any", False, "a caption font, or null"),
        "size": ("any", False, "8 to 400, or null"),
        "color": ("any", False, "#RRGGBB, or null"),
        "position": ("any", False, "top, middle or bottom, or null"),
    }),
    "set_captions": ("Switch captions on or off, set their style, or replace their lines.", {
        "enabled": ("any", False, "true, false, or null for the job's own choice"),
        "style": ("any", False, "a caption style, or null for the job's own"),
        "lines": ("any", False, "[{in_us, out_us, text}] in the source's time, or null for the transcript's"),
        "media_id": ("any", False, "the media whose time the captions are in"),
    }),
    "edit_caption_words": ("Fix caption lines: their words or their times. Empty text removes a line.", {
        "edits": ("list", True, "[{line_id, text, in_us, out_us}]"),
    }),
    "set_speaker": ("Say who is talking in a stretch of the source, or forget every such fix.", {
        "in_us": ("time", False, "where the stretch starts"),
        "out_us": ("time", False, "where it ends"),
        "speaker": ("int", False, "0 for the main speaker, 1 for the other"),
        "clear": ("bool", False, "forget every fix"),
    }),
    "set_branding": ("Set the watermark, switch it off (null), or go back to the job's own (clear).", {
        "watermark": ("any", False, "a watermark, or null for none"),
        "clear": ("bool", False, "the job's own branding"),
    }),
    "set_export_preset": ("Choose how the project is exported. The codec must be one this PC can encode.", {
        "preset": ("any", True, "an export preset, or \"original\" / null"),
    }),
    "batch": ("Several operations as one: all of them or none, and one undo step.", {
        "ops": ("list", True, "the operations"),
    }),
}

_KIND_SAID = {"id": "an id", "time": "a whole number of microseconds (0 or more)", "int": "a whole number",
              "num": "a number", "bool": "true or false", "str": "text", "dict": "an object", "list": "a list"}


def describe() -> dict:
    """Every operation and its fields, as JSON."""
    return {"version": OPS_VERSION, "ops": {
        name: {"about": about, "fields": {f: {"kind": k, "required": r, "about": a}
                                          for f, (k, r, a) in fields.items()}}
        for name, (about, fields) in SPECS.items()}}


def _is_num(value) -> bool:
    return (isinstance(value, (int, float)) and not isinstance(value, bool) and value == value
            and abs(value) != float("inf"))


def _of_kind(value, kind: str) -> bool:
    if kind == "any":
        return True
    if kind == "id":
        return isinstance(value, str) and bool(_ID.fullmatch(value))
    if kind == "time":
        return model.is_time(value)
    if kind == "int":
        return type(value) is int
    if kind == "num":
        return _is_num(value)
    return isinstance(value, {"bool": bool, "str": str, "dict": dict, "list": list}[kind])


def _check_fields(name: str, op: dict) -> None:
    fields = SPECS[name][1]
    for key in op:
        if key != "op" and key not in fields:
            raise OpError(name, str(key)[:40], f"is not a field of {name}. Its fields: {', '.join(fields)}.")
    for key, (kind, required, _about) in fields.items():
        if key not in op:
            if required:
                raise OpError(name, key, "is missing.")
        elif not _of_kind(op[key], kind):
            raise OpError(name, key, f"must be {_KIND_SAID[kind]}.")


# ---- helpers ---------------------------------------------------------------------------------


def _plain(value):
    """A copy the document can keep: nothing in it is shared with the caller."""
    return model.clone(value)


def _sized(name: str, key: str, value):
    if len(model.canonical(value)) > MAX_OBJECT_CHARS:
        raise OpError(name, key, "is too large.")
    return _plain(value)


def clean_text(text: str) -> str:
    """Text as the document keeps it: no ASS control characters ({, } and \\,
    which would be read as override tags when it is burned), no other control
    characters, line breaks kept, trimmed."""
    text = text.replace("\r\n", "\n").replace("\r", "\n").replace("\t", " ")
    kept = "".join(ch for ch in text
                   if ch == "\n" or (ch not in "{}\\" and unicodedata.category(ch) not in ("Cc", "Cs")))
    return kept.strip()


def _text(name: str, key: str, value: str, limit: int = model.TEXT_MAX) -> str:
    cleaned = clean_text(value)
    if not cleaned:
        raise OpError(name, key, "has no text in it.")
    if len(cleaned) > limit:
        raise OpError(name, key, f"is {len(cleaned)} characters; the most is {limit}.")
    return cleaned


def _name(name: str, value: str) -> str:
    cleaned = clean_text(value).replace("\n", " ")
    if len(cleaned) > model.NAME_MAX:
        raise OpError(name, "name", f"is {len(cleaned)} characters; the most is {model.NAME_MAX}.")
    return cleaned


def _in_range(name: str, key: str, value, lo: float, hi: float) -> float:
    if not _is_num(value) or not lo <= value <= hi:
        raise OpError(name, key, f"must be a number from {lo:g} to {hi:g}.")
    return float(value) + 0.0       # never -0.0: the document holds one zero


def _track(doc: dict, name: str, key: str, track_id: str) -> dict:
    found = model.track(doc, track_id)
    if found is None:
        raise OpError(name, key, f"{track_id!r} is not a track of this project.")
    return found


def _item(doc: dict, name: str, key: str, item_id) -> tuple:
    found = model.find_item(doc, item_id) if isinstance(item_id, str) else None
    if found is None:
        raise OpError(name, key, f"{str(item_id)[:64]!r} is not an item of this project.")
    return found


def _media(doc: dict, name: str, key: str, media_id: str) -> dict:
    media = doc["media"].get(media_id)
    if media is None:
        raise OpError(name, key, f"{media_id!r} is not in the project's media. Add it with add_media first.")
    return media


def _fps(doc: dict) -> dict:
    return doc["canvas"]["fps"]


def _is_main(t: dict) -> bool:
    return t["id"] == MAIN_VIDEO


def _timed(doc: dict, t: dict, item: dict) -> bool:
    """Text and pictures have no source to play: only a start and a length."""
    return t["kind"] == "text" or doc["media"][item["media_id"]]["kind"] == "image"


def _sync(item: dict) -> None:
    item["source_in_us"], item["source_out_us"] = 0, item["duration_us"]


def _frames(t: dict, fps: dict) -> list[int]:
    """How many frames each of the main track's items is long."""
    out, n = [], 0
    for item in t["items"]:
        last = model.frames_at(item["start_us"] + item["duration_us"], fps)
        out.append(last - n)
        n = last
    return out


def _lay(name: str, doc: dict, t: dict, frames: list[int], fps: dict) -> None:
    """The main track laid out again from each item's length in frames: no
    gaps, and every edge worked out from its frame number.

    Where a frame is not a whole number of microseconds (30 and 29.97 frames
    a second among them) the same number of frames is a microsecond longer
    or shorter at another place on the track. So every item whose length
    moved is kept in step with it here, not only the one being edited: a
    picture's source times are its length, and fades that filled an item
    still fit it."""
    n = 0
    for item, k in zip(t["items"], frames):
        item["start_us"] = model.frame_us(n, fps)
        length = model.frame_us(n + k, fps) - item["start_us"]
        n += k
        if length != item["duration_us"]:
            item["duration_us"] = length
            if _timed(doc, t, item):
                _sync(item)
            _settle_fades(item)
    if model.frame_us(n, fps) > model.MAX_US:
        raise OpError(name, None, "the video would be too long.")


def _settle_fades(item: dict) -> None:
    """Fades that are a microsecond or two too long for an item whose length
    was only rounded: shortened by that much, and nothing said (a person
    can't see it). Anything more is the edit's own doing (_fit_fades, which
    says so)."""
    fades = item["fades"]
    over = fades["in_us"] + fades["out_us"] - item["duration_us"]
    if 0 < over <= 2:
        cut = min(over, fades["out_us"])
        fades["out_us"] -= cut
        fades["in_us"] -= over - cut


def _whole_frames(name: str, key: str | None, duration_us: int, fps: dict) -> int:
    k = model.frames_at(duration_us, fps)
    if k < 1:
        raise OpError(name, key, "would leave an item shorter than one frame.")
    return k


def _loop_from(item: dict, moved_us: int) -> None:
    """Looped music whose start moved by `moved_us` along the video: it
    starts that much further into the stretch it loops (model.py
    loop_offset_us), so what plays at each moment of the video stays the
    same. The key is only there when it isn't 0."""
    stretch = item["source_out_us"] - item["source_in_us"]
    offset = (item.get("loop_offset_us", 0) + moved_us) % stretch
    if offset:
        item["loop_offset_us"] = offset
    else:
        item.pop("loop_offset_us", None)


def _played(item: dict) -> int:
    """How long an item's source stretch plays at its speed."""
    return round((item["source_out_us"] - item["source_in_us"]) / item["speed"])


def _place(name: str, key: str | None, t: dict, item: dict) -> None:
    """An item put on a track that isn't the main one: in start order, and
    refused where it would overlap another."""
    if item["duration_us"] <= 0:
        raise OpError(name, key, "would leave an item with no length.")
    end = item["start_us"] + item["duration_us"]
    if end > model.MAX_US:
        raise OpError(name, key, "is too late.")
    index = len(t["items"])
    for i, other in enumerate(t["items"]):
        if other["start_us"] >= item["start_us"]:
            index = i
            break
    before = t["items"][index - 1] if index else None
    after = t["items"][index] if index < len(t["items"]) else None
    if before is not None and before["start_us"] + before["duration_us"] > item["start_us"]:
        raise OpError(name, key, f"would overlap item {before['id']} on track {t['id']}. "
                                 "Move it, or use another track.")
    if after is not None and end > after["start_us"]:
        raise OpError(name, key, f"would overlap item {after['id']} on track {t['id']}. "
                                 "Move it, or use another track.")
    t["items"].insert(index, item)


def _fit_fades(item: dict, ctx: _Ctx) -> None:
    fades, length = item["fades"], item["duration_us"]
    if fades["in_us"] + fades["out_us"] > length:
        fades["in_us"] = min(fades["in_us"], length)
        fades["out_us"] = min(fades["out_us"], length - fades["in_us"])
        ctx.warnings.append(f"Item {item['id']}'s fades were shortened to fit it.")


def _source_range(name: str, media: dict, source_in: int, source_out: int, key: str = "source_out_us") -> None:
    if source_out <= source_in:
        raise OpError(name, key, "must be after source_in_us.")
    length = media.get("duration_us") or 0
    if length and source_out > length:
        raise OpError(name, key, f"is past the end of the media ({length} microseconds).")


def _count_items(name: str, doc: dict) -> None:
    if sum(len(t["items"]) for t in doc["tracks"]) > MAX_ITEMS:
        raise OpError(name, None, f"a project holds at most {MAX_ITEMS} items.")


def _new(ctx: _Ctx, kind: str, new_id: str) -> None:
    ctx.created.append({"kind": kind, "id": new_id})


def _from_env(name: str, key: str | None, call, *args):
    try:
        return call(*args)
    except OpError:
        raise
    except ValueError as e:
        raise OpError(name, key, str(e) or "was refused.") from e


# ---- project and canvas ----------------------------------------------------------------------


def _set_canvas(doc, op, ctx):
    name, canvas = "set_canvas", doc["canvas"]
    if len(op) == 1:
        raise OpError(name, None, "give a preset, a width, a height or a frame rate.")
    if "preset" in op:
        if op["preset"] not in model.CANVAS_PRESETS:
            raise OpError(name, "preset", f"must be one of {', '.join(model.CANVAS_PRESETS)}.")
        if "width" in op or "height" in op:
            raise OpError(name, "preset", "can't be given together with width or height.")
        canvas["width"], canvas["height"] = model.CANVAS_PRESETS[op["preset"]]
    for side in ("width", "height"):
        if side in op:
            if not model.MIN_SIDE <= op[side] <= model.MAX_SIDE or op[side] % 2:
                raise OpError(name, side, f"must be an even whole number from {model.MIN_SIDE} to {model.MAX_SIDE}.")
            canvas[side] = op[side]
    if "fps" in op:
        try:
            fps = model.fps_of(op["fps"])
        except model.ModelError as e:
            raise OpError(name, "fps", f"isn't a frame rate: {e}.") from e
        if fps != canvas["fps"]:
            main = model.track(doc, MAIN_VIDEO)
            # A clip's length on the new grid comes from its source and its
            # speed, never from its length on the old grid: going to another
            # rate and back gives the lengths it had. Text and pictures have
            # only a length.
            frames = [max(1, model.frames_at(
                item["duration_us"] if _timed(doc, main, item) else _played(item), fps))
                for item in main["items"]]
            canvas["fps"] = fps
            _lay(name, doc, main, frames, fps)
            for item in main["items"]:
                if _timed(doc, main, item):
                    _sync(item)
                _fit_fades(item, ctx)
            if main["items"]:
                ctx.warnings.append("The main track's items were moved onto the new frame rate's grid.")


def _set_project_props(doc, op, ctx):
    name = "set_project_props"
    if len(op) == 1:
        raise OpError(name, None, "give a name, a master_volume or normalize_audio.")
    if "name" in op:
        doc["name"] = _name(name, op["name"])
    if "master_volume" in op:
        doc["audio"]["master_volume"] = _in_range(name, "master_volume", op["master_volume"], *model.VOLUME_RANGE)
    if "normalize_audio" in op:
        doc["audio"]["normalize"] = op["normalize_audio"]


def _add_media(doc, op, ctx):
    name = "add_media"
    given = [k for k in ("video_id", "asset", "library") if k in op]
    if len(given) != 1:
        raise OpError(name, None, "name the media by exactly one of video_id, asset or library.")
    key = given[0]
    if not op[key] or len(op[key]) > 200:
        raise OpError(name, key, "is empty or too long.")
    facts = _from_env(name, key, ctx.env.media, {key: op[key]})
    if not isinstance(facts, dict) or facts.get("kind") not in model.MEDIA_KINDS \
            or not isinstance(facts.get("source"), dict):
        raise OpError(name, key, "could not be read as media.")
    source = _plain(facts["source"])
    for media in doc["media"].values():
        if media["source"] == source:
            ctx.warnings.append(f"That is already in the project, as {media['id']}.")
            _new(ctx, "media", media["id"])
            return
    if len(doc["media"]) >= MAX_ITEMS:
        raise OpError(name, None, "the project holds too much media.")
    media_id = model.next_id(doc, "m")
    doc["media"][media_id] = {
        "id": media_id, "kind": facts["kind"], "source": source,
        "duration_us": int(facts.get("duration_us") or 0),
        "width": int(facts.get("width") or 0), "height": int(facts.get("height") or 0),
        "fps": _plain(facts.get("fps")), "has_audio": bool(facts.get("has_audio", facts["kind"] != "image")),
        "name": clean_text(str(facts.get("name") or ""))[:model.NAME_MAX], "extra": {},
    }
    _new(ctx, "media", media_id)


# ---- items -----------------------------------------------------------------------------------


def _speed(name: str, value) -> float:
    return _in_range(name, "speed", value, *model.SPEED_RANGE)


def _insert_clip(doc, op, ctx):
    name = "insert_clip"
    t = _track(doc, name, "track_id", op.get("track_id", MAIN_VIDEO))
    if t["kind"] != "video":
        raise OpError(name, "track_id", f"{t['id']} is not a video track.")
    media = _media(doc, name, "media_id", op["media_id"])
    if media["kind"] not in ("video", "image"):
        raise OpError(name, "media_id", f"is {media['kind']}: a video track takes video and pictures. "
                                        "Use add_audio for sound.")
    picture = media["kind"] == "image"
    if op["source_out_us"] <= op["source_in_us"]:
        raise OpError(name, "source_out_us", "must be after source_in_us.")
    if not picture:
        _source_range(name, media, op["source_in_us"], op["source_out_us"])
    speed = _speed(name, op.get("speed", 1.0))
    if picture and speed != 1:
        raise OpError(name, "speed", "doesn't apply to a picture.")
    item = model.new_item(model.next_id(doc, "i"), media_id=media["id"], source_in_us=op["source_in_us"],
                          source_out_us=op["source_out_us"], speed=speed)
    item["duration_us"] = _played(item)
    if _is_main(t):
        if "start_us" in op:
            raise OpError(name, "start_us", "doesn't apply to V1, which has no gaps. Give an index instead.")
        index = op.get("index", len(t["items"]))
        if not 0 <= index <= len(t["items"]):
            raise OpError(name, "index", f"must be from 0 to {len(t['items'])}.")
        fps = _fps(doc)
        frames = _frames(t, fps)
        frames.insert(index, _whole_frames(name, "source_out_us", item["duration_us"], fps))
        t["items"].insert(index, item)
        _lay(name, doc, t, frames, fps)
    else:
        if "start_us" not in op:
            raise OpError(name, "start_us", f"is missing: say when it starts on {t['id']}.")
        if "index" in op:
            raise OpError(name, "index", "only applies to V1.")
        item["start_us"] = op["start_us"]
        _place(name, "start_us", t, item)
    if picture:
        _sync(item)
    _count_items(name, doc)
    _new(ctx, "item", item["id"])


def _trim_item(doc, op, ctx):
    name = "trim_item"
    t, index, item = _item(doc, name, "item_id", op["item_id"])
    by_program = _timed(doc, t, item) or item.get("loop")
    wrong = ("source_in_us", "source_out_us") if by_program else ("start_us", "end_us")
    for key in wrong:
        if key in op:
            other = "start_us and end_us" if by_program else "source_in_us and source_out_us"
            raise OpError(name, key, f"doesn't apply to this item. Use {other}.")
    if len(op) == 2:
        raise OpError(name, None, "give the edge to move.")
    old_end = item["start_us"] + item["duration_us"]
    if by_program:
        start, end = op.get("start_us", item["start_us"]), op.get("end_us", old_end)
        if end <= start:
            raise OpError(name, "end_us" if "end_us" in op else "start_us", "would leave an item with no length.")
        length = end - start
    else:
        media = doc["media"][item["media_id"]]
        source_in = op.get("source_in_us", item["source_in_us"])
        source_out = op.get("source_out_us", item["source_out_us"])
        if source_out <= source_in:
            raise OpError(name, "source_out_us" if "source_out_us" in op else "source_in_us",
                          "would leave an item with no length.")
        _source_range(name, media, source_in, source_out)
        item["source_in_us"], item["source_out_us"] = source_in, source_out
        length = _played(item)
    if _is_main(t):
        fps = _fps(doc)
        frames = _frames(t, fps)
        frames[index] = _whole_frames(name, None, length, fps)
        _lay(name, doc, t, frames, fps)
    else:
        if by_program:
            if item.get("loop"):
                _loop_from(item, start - item["start_us"])
            item["start_us"] = start
        elif "source_in_us" in op:
            # Its left edge moved: its right edge stays where it was.
            if old_end - length < 0:
                raise OpError(name, "source_in_us", "would start before the video does.")
            item["start_us"] = old_end - length
        item["duration_us"] = length
        del t["items"][index]
        _place(name, None, t, item)
    if by_program and not item.get("loop"):
        _sync(item)
    _fit_fades(item, ctx)


def _split_item(doc, op, ctx):
    name = "split_item"
    t, index, item = _item(doc, name, "item_id", op["item_id"])
    start, end = item["start_us"], item["start_us"] + item["duration_us"]
    at = op["at_us"]
    fps = _fps(doc)
    main = _is_main(t)
    # Every item's length in frames, counted before this one is shortened:
    # counted after, its right half would be added to the item after it.
    frames = _frames(t, fps) if main else []
    if main:
        first, cut, last = model.frames_at(start, fps), model.frames_at(at, fps), model.frames_at(end, fps)
        if not first < cut < last:
            raise OpError(name, "at_us", "must be inside the item, at least one frame from each end.")
        at = model.frame_us(cut, fps)
    elif not start < at < end:
        raise OpError(name, "at_us", "must be inside the item.")
    right = _plain(item)
    right["id"] = model.next_id(doc, "i")
    item["duration_us"], right["start_us"], right["duration_us"] = at - start, at, end - at
    if _timed(doc, t, item):
        _sync(item)
        _sync(right)
    elif item.get("loop"):
        _loop_from(right, at - start)       # the right half goes on from where the left one stops
    else:
        # Where the cut falls in the source. What the item's length and its
        # source stretch disagree by (rounding, or a length in whole frames)
        # is shared between the two halves, so it never adds up over splits.
        apart = (item["source_out_us"] - item["source_in_us"]) - (end - start) * item["speed"]
        source_cut = item["source_in_us"] + round((at - start) * item["speed"] + apart / 2)
        source_cut = min(max(source_cut, item["source_in_us"] + 1), item["source_out_us"] - 1)
        if not item["source_in_us"] < source_cut < item["source_out_us"]:
            raise OpError(name, "at_us", "would leave an item with no length.")
        item["source_out_us"], right["source_in_us"] = source_cut, source_cut
    item["fades"]["out_us"], right["fades"]["in_us"] = 0, 0
    _fit_fades(item, ctx)
    _fit_fades(right, ctx)
    if main:
        frames[index:index + 1] = [cut - first, last - cut]
        t["items"].insert(index + 1, right)
        _lay(name, doc, t, frames, fps)
    else:
        t["items"].insert(index + 1, right)
    _count_items(name, doc)
    _new(ctx, "item", right["id"])


def _id_list(name: str, key: str, value: list) -> list[str]:
    if not value or len(value) > MAX_ITEMS or not all(isinstance(v, str) for v in value):
        raise OpError(name, key, "must be a list of item ids, at least one.")
    if len(set(value)) != len(value):
        raise OpError(name, key, "names an item twice.")
    return value


def _delete_items(doc, op, ctx):
    name = "delete_items"
    for item_id in _id_list(name, "item_ids", op["item_ids"]):
        t, index, _item_found = _item(doc, name, "item_ids", item_id)
        if _is_main(t):
            fps = _fps(doc)
            frames = _frames(t, fps)
            del frames[index]
            del t["items"][index]
            _lay(name, doc, t, frames, fps)
        else:
            del t["items"][index]
    _unlink(doc)


def _unlink(doc: dict) -> None:
    """An item that belonged with one that is gone now belongs with none."""
    ids = {item["id"] for t in doc["tracks"] for item in t["items"]}
    for t in doc["tracks"]:
        for item in t["items"]:
            if item["link"] is not None and item["link"] not in ids:
                item["link"] = None


def _move_item(doc, op, ctx):
    name = "move_item"
    t, index, item = _item(doc, name, "item_id", op["item_id"])
    target = _track(doc, name, "track_id", op["track_id"]) if "track_id" in op else t
    if target["kind"] != t["kind"]:
        raise OpError(name, "track_id", f"{target['id']} is a {target['kind']} track; the item is on a "
                                        f"{t['kind']} track.")
    if target["id"] == MAIN_AUDIO:
        raise OpError(name, "track_id", "A1 is the video's own sound and holds no items.")
    fps = _fps(doc)
    if _is_main(target):
        if "start_us" in op:
            raise OpError(name, "start_us", "doesn't apply to V1, which has no gaps. Give an index instead.")
    else:
        if "index" in op:
            raise OpError(name, "index", "only applies to V1.")
        if "start_us" not in op and target is t:
            raise OpError(name, "start_us", "is missing: say when it starts.")
    # Lifted off its track...
    if _is_main(t):
        frames = _frames(t, fps)
        length = frames.pop(index)
        del t["items"][index]
        _lay(name, doc, t, frames, fps)
    else:
        length = None
        del t["items"][index]
    # ...and put down.
    if _is_main(target):
        if length is None:
            length = _whole_frames(name, None, item["duration_us"], fps)
        where = op.get("index", len(target["items"]))
        if not 0 <= where <= len(target["items"]):
            raise OpError(name, "index", f"must be from 0 to {len(target['items'])}.")
        frames = _frames(target, fps)
        frames.insert(where, length)
        target["items"].insert(where, item)
        _lay(name, doc, target, frames, fps)
        if _timed(doc, target, item):
            _sync(item)
        _fit_fades(item, ctx)
    else:
        if _is_main(t) and not _timed(doc, t, item):
            # Off the main track a clip is no longer a whole number of
            # frames long: it plays its source stretch, to the microsecond.
            item["duration_us"] = _played(item)
            _fit_fades(item, ctx)
        item["start_us"] = op.get("start_us", item["start_us"])
        _place(name, "start_us", target, item)


def _duplicate_items(doc, op, ctx):
    name = "duplicate_items"
    for item_id in _id_list(name, "item_ids", op["item_ids"]):
        t, index, item = _item(doc, name, "item_ids", item_id)
        copy = _plain(item)
        copy["id"] = model.next_id(doc, "i")
        if _is_main(t):
            fps = _fps(doc)
            frames = _frames(t, fps)
            frames.insert(index + 1, frames[index])
            t["items"].insert(index + 1, copy)
            _lay(name, doc, t, frames, fps)
        else:
            copy["start_us"] = item["start_us"] + item["duration_us"]
            _place(name, "item_ids", t, copy)
        _new(ctx, "item", copy["id"])
    _count_items(name, doc)


def _framing(name: str, value: dict, ctx: _Ctx) -> dict:
    unknown = [k for k in value if k not in ("crop", "gaming", "fit", "align")]
    if unknown:
        raise OpError(name, "framing", f"has no {unknown[0]!r}. It takes crop, gaming, fit and align.")
    out: dict = {}
    if "crop" in value:
        if value["crop"] not in CROPS:
            raise OpError(name, "framing", f"crop must be one of {', '.join(CROPS)}.")
        out["crop"] = value["crop"]
    if "gaming" in value:
        out["gaming"] = None if value["gaming"] is None else _from_env(
            name, "framing", ctx.env.gaming, _sized(name, "framing", value["gaming"]))
    if "fit" in value:
        if value["fit"] not in FITS:
            raise OpError(name, "framing", f"fit must be one of {', '.join(FITS)}.")
        out["fit"] = value["fit"]
    if "align" in value:
        if value["align"] not in ALIGNS:
            raise OpError(name, "framing", f"align must be one of {', '.join(ALIGNS)}.")
        out["align"] = value["align"]
    return _plain(out)


def _numbers(name: str, key: str, value: dict, ranges: dict) -> dict:
    out = {}
    for inner, given in value.items():
        if inner not in ranges:
            raise OpError(name, key, f"has no {str(inner)[:40]!r}. It takes {', '.join(ranges)}.")
        lo, hi = ranges[inner]
        if not _is_num(given) or not lo <= given <= hi:
            raise OpError(name, key, f"{inner} must be a number from {lo:g} to {hi:g}.")
        out[inner] = float(given) + 0.0     # never -0.0
    return out


def _color(name: str, value: dict, ctx: _Ctx) -> dict:
    unknown = [k for k in value if k not in ("filter", "adjust")]
    if unknown:
        raise OpError(name, "color", f"has no {unknown[0]!r}. It takes filter and adjust.")
    out: dict = {}
    if value.get("filter") is not None:
        if not isinstance(value["filter"], str) or not (value["filter"] == "none" or ctx.env.filter_ok(value["filter"])):
            raise OpError(name, "color", f"filter {str(value['filter'])[:40]!r} is not one of the app's filters.")
        out["filter"] = value["filter"]
    if value.get("adjust") is not None:
        if not isinstance(value["adjust"], dict):
            raise OpError(name, "color", "adjust must be an object.")
        out["adjust"] = _numbers(name, "color", value["adjust"], ADJUST)
    return out


def _fades(name: str, value: dict, item: dict) -> dict:
    fades = dict(item["fades"])
    for key, given in value.items():
        if key not in ("in_us", "out_us"):
            raise OpError(name, "fades", "takes in_us and out_us.")
        if not model.is_time(given):
            raise OpError(name, "fades", f"{key} must be a whole number of microseconds.")
        fades[key] = given
    if fades["in_us"] + fades["out_us"] > item["duration_us"]:
        raise OpError(name, "fades", "are longer than the item.")
    return fades


def _set_item_props(doc, op, ctx):
    name = "set_item_props"
    t, index, item = _item(doc, name, "item_id", op["item_id"])
    if len(op) == 2:
        raise OpError(name, None, "give at least one thing to change.")
    is_text, timed = t["kind"] == "text", _timed(doc, t, item)
    if "speed" in op:
        speed = _speed(name, op["speed"])
        if timed or item.get("loop"):
            raise OpError(name, "speed", "applies to video clips and sounds, not to text, pictures or looped music.")
        item["speed"] = speed
        length = _played(item)
        if _is_main(t):
            fps = _fps(doc)
            frames = _frames(t, fps)
            frames[index] = _whole_frames(name, "speed", length, fps)
            _lay(name, doc, t, frames, fps)
        else:
            if length <= 0:
                raise OpError(name, "speed", "would leave an item with no length.")
            item["duration_us"] = length
            del t["items"][index]
            _place(name, "speed", t, item)
        _fit_fades(item, ctx)
    if "volume" in op:
        if is_text:
            raise OpError(name, "volume", "doesn't apply to text.")
        item["volume"] = _in_range(name, "volume", op["volume"], *model.VOLUME_RANGE)
    if "fades" in op:
        item["fades"] = _fades(name, op["fades"], item)
    if "framing" in op:
        if t["kind"] != "video":
            raise OpError(name, "framing", "applies to items on a video track.")
        item["framing"] = _framing(name, op["framing"], ctx)
    if "transform" in op:
        if t["kind"] == "audio":
            raise OpError(name, "transform", "doesn't apply to a sound.")
        item["transform"] = _numbers(name, "transform", op["transform"], TRANSFORM)
    if "color" in op:
        if t["kind"] != "video":
            raise OpError(name, "color", "applies to items on a video track.")
        item["color"] = _color(name, op["color"], ctx)
    if "link" in op:
        if op["link"] is not None and (not isinstance(op["link"], str) or model.find_item(doc, op["link"]) is None
                                       or op["link"] == item["id"]):
            raise OpError(name, "link", "must be the id of another item of this project, or null.")
        item["link"] = op["link"]
    for key in ("loop", "duck"):
        if key in op:
            if t["kind"] != "audio":
                raise OpError(name, key, "applies to items on an audio track.")
            if key == "loop" and op[key] and item.get("role") != "music":
                raise OpError(name, key, "only music can loop.")
            if key == "loop" and item.get("loop") and not op[key]:
                # No longer looped: it plays its stretch once, from its start.
                item.pop("loop_offset_us", None)
                item[key] = False
                if item["duration_us"] != _played(item):
                    item["duration_us"] = _played(item)
                    t["items"].remove(item)
                    _place(name, key, t, item)
                    _fit_fades(item, ctx)
                continue
            item[key] = op[key]
    if "extra" in op:
        item["extra"] = _sized(name, "extra", op["extra"])


# ---- tracks ----------------------------------------------------------------------------------

_PREFIX = {"video": "V", "text": "T", "audio": "A"}


def _add_track(doc, op, ctx):
    name, kind = "add_track", op["kind"]
    if kind not in model.TRACK_KINDS:
        raise OpError(name, "kind", f"must be one of {', '.join(model.TRACK_KINDS)}.")
    if len(doc["tracks"]) >= MAX_TRACKS:
        raise OpError(name, None, f"a project holds at most {MAX_TRACKS} tracks.")
    prefix = _PREFIX[kind]
    taken = [int(t["id"][1:]) for t in doc["tracks"] if t["id"][:1] == prefix and t["id"][1:].isdigit()]
    track_id = f"{prefix}{max(taken, default=0) + 1}"
    new = model.new_track(track_id, kind, _name(name, op["name"]) if "name" in op else None)
    same = [i for i, t in enumerate(doc["tracks"]) if t["kind"] == kind]
    doc["tracks"].insert(same[-1] + 1 if same else len(doc["tracks"]), new)
    _new(ctx, "track", track_id)


def _remove_track(doc, op, ctx):
    name = "remove_track"
    t = _track(doc, name, "track_id", op["track_id"])
    if t["id"] in (MAIN_VIDEO, MAIN_AUDIO):
        raise OpError(name, "track_id", f"{t['id']} can't be removed: every project has it.")
    if t["items"] and not op.get("force", False):
        raise OpError(name, "track_id", f"{t['id']} still holds {len(t['items'])} item(s). "
                                        "Remove them first, or pass force.")
    doc["tracks"].remove(t)
    _unlink(doc)


def _set_track_props(doc, op, ctx):
    name = "set_track_props"
    t = _track(doc, name, "track_id", op["track_id"])
    if len(op) == 2:
        raise OpError(name, None, "give at least one thing to change.")
    if "name" in op:
        t["name"] = _name(name, op["name"])
    if "mute" in op:
        t["mute"] = op["mute"]
    for key in ("volume", "solo"):
        if key in op:
            if t["kind"] != "audio":
                raise OpError(name, key, f"only applies to an audio track; {t['id']} is a {t['kind']} track.")
            t[key] = _in_range(name, key, op[key], *model.VOLUME_RANGE) if key == "volume" else op[key]


def _move_track(doc, op, ctx):
    name = "move_track"
    t = _track(doc, name, "track_id", op["track_id"])
    if t["id"] in (MAIN_VIDEO, MAIN_AUDIO):
        raise OpError(name, "track_id", f"{t['id']} can't be moved: it is always the first {t['kind']} track.")
    # The places the tracks of this kind hold among all the tracks stay the
    # same; only which track is at which of them changes.
    places = [i for i, other in enumerate(doc["tracks"]) if other["kind"] == t["kind"]]
    group = [doc["tracks"][i] for i in places]
    first = 1 if group[0]["id"] in (MAIN_VIDEO, MAIN_AUDIO) else 0
    if not first <= op["index"] < len(group):
        raise OpError(name, "index", f"must be from {first} to {len(group) - 1}.")
    group.remove(t)
    group.insert(op["index"], t)
    for place, moved in zip(places, group):
        doc["tracks"][place] = moved


# ---- audio -----------------------------------------------------------------------------------


def _add_audio(doc, op, ctx):
    name = "add_audio"
    t = _track(doc, name, "track_id", op["track_id"])
    if t["kind"] != "audio" or t["id"] == MAIN_AUDIO:
        raise OpError(name, "track_id", "must be an audio track other than A1, which is the video's own sound.")
    media = _media(doc, name, "media_id", op["media_id"])
    if media["kind"] not in ("audio", "video") or not media.get("has_audio", True):
        raise OpError(name, "media_id", "has no sound.")
    role = op["role"]
    if role not in model.AUDIO_ROLES:
        raise OpError(name, "role", f"must be one of {', '.join(model.AUDIO_ROLES)}.")
    loop = op.get("loop", False)
    if loop and role != "music":
        raise OpError(name, "loop", "only music can loop.")
    length = media.get("duration_us") or 0
    source_in = op.get("source_in_us", 0)
    if "source_out_us" in op:
        source_out = op["source_out_us"]
    elif length:
        source_out = length
    elif loop and "duration_us" in op:
        source_out = source_in + op["duration_us"]
    else:
        raise OpError(name, "source_out_us", "is missing, and the sound's length isn't known.")
    _source_range(name, media, source_in, source_out)
    item = model.new_item(model.next_id(doc, "i"), media_id=media["id"], source_in_us=source_in,
                          source_out_us=source_out, start_us=op["start_us"],
                          volume=_in_range(name, "volume", op.get("volume", 1.0), *model.VOLUME_RANGE))
    item.update(role=role, loop=loop, duck=op.get("duck", False))
    if loop:
        if "duration_us" not in op:
            raise OpError(name, "duration_us", "is missing: say how long the looped music plays.")
        item["duration_us"] = op["duration_us"]
    else:
        if "duration_us" in op:
            raise OpError(name, "duration_us", "only applies to looped music. Use source_in_us and source_out_us.")
        item["duration_us"] = source_out - source_in
    _place(name, "start_us", t, item)
    if "fades" in op:
        item["fades"] = _fades(name, op["fades"], item)
    _count_items(name, doc)
    _new(ctx, "item", item["id"])


def _ranges(name: str, key: str, value: list, extra=None) -> list:
    """[{in_us, out_us, ...}] checked and copied."""
    if len(value) > MAX_CAPTION_LINES:
        raise OpError(name, key, "is too long.")
    out = []
    for index, entry in enumerate(value):
        if (not isinstance(entry, dict) or not model.is_time(entry.get("in_us"))
                or not model.is_time(entry.get("out_us")) or entry["out_us"] <= entry["in_us"]):
            raise OpError(name, key, f"entry {index} must be {{in_us, out_us}} that ends after it starts.")
        entry = _plain(entry)
        if extra is not None:
            extra(index, entry)
        out.append(entry)
    return out


def _set_audio_mutes(doc, op, ctx):
    name = "set_audio_mutes"

    def mode(index: int, entry: dict) -> None:
        entry.setdefault("mode", "mute")
        if entry["mode"] not in model.MUTE_MODES:
            raise OpError(name, "mutes", f"entry {index}: mode must be mute or bleep.")

    def word(index: int, entry: dict) -> None:
        if not isinstance(entry.get("word", ""), str) or len(entry.get("word", "")) > 200:
            raise OpError(name, "muted_words", f"entry {index}: word must be text.")

    mutes = _ranges(name, "mutes", op["mutes"], mode)
    doc["captions"]["mutes"] = sorted(mutes, key=lambda m: (m["in_us"], m["out_us"]))
    if "muted_words" in op:
        doc["captions"]["muted_words"] = _ranges(name, "muted_words", op["muted_words"], word)
    elif not mutes:
        doc["captions"]["muted_words"] = []


# ---- text ------------------------------------------------------------------------------------


def _look(name: str, op: dict, text: dict, ctx: _Ctx) -> None:
    """A text item's font, size, colour and place: each given one checked and
    set; null removes it (the default look)."""
    if "role" in op:
        if op["role"] not in model.TEXT_ROLES:
            raise OpError(name, "role", f"must be one of {', '.join(model.TEXT_ROLES)}.")
        text["role"] = op["role"]
    for key in ("font", "size", "color", "position"):
        if key not in op:
            continue
        value = op[key]
        if value is None:
            text.pop(key, None)
            continue
        if key == "font":
            found = ctx.env.font(value) if isinstance(value, str) else None
            if found is None:
                raise OpError(name, key, f"{str(value)[:60]!r} is not one of the caption fonts.")
            value = found
        elif key == "size" and (type(value) is not int or not 8 <= value <= 400):
            raise OpError(name, key, "must be a whole number from 8 to 400.")
        elif key == "color":
            if not isinstance(value, str) or not _COLOUR.fullmatch(value):
                raise OpError(name, key, "must be a colour as #RRGGBB.")
            value = value.upper()
        elif key == "position" and value not in TEXT_POSITIONS:
            raise OpError(name, key, f"must be one of {', '.join(TEXT_POSITIONS)}.")
        text[key] = value


def _add_text(doc, op, ctx):
    name = "add_text"
    if "track_id" in op:
        t = _track(doc, name, "track_id", op["track_id"])
    else:
        t = next((x for x in doc["tracks"] if x["kind"] == "text"), None)
        if t is None:
            raise OpError(name, "track_id", "is missing, and the project has no text track. Add one with add_track.")
    if t["kind"] != "text":
        raise OpError(name, "track_id", f"{t['id']} is not a text track.")
    if op["duration_us"] <= 0:
        raise OpError(name, "duration_us", "must be more than 0.")
    text = {"content": _text(name, "content", op["content"]), "role": "text"}
    _look(name, op, text, ctx)
    item = model.new_item(model.next_id(doc, "i"), start_us=op["start_us"], duration_us=op["duration_us"], text=text)
    _sync(item)
    _place(name, "start_us", t, item)
    _count_items(name, doc)
    _new(ctx, "item", item["id"])


def _set_text(doc, op, ctx):
    name = "set_text"
    t, _index, item = _item(doc, name, "item_id", op["item_id"])
    if t["kind"] != "text":
        raise OpError(name, "item_id", f"{item['id']} is not a text item.")
    if len(op) == 2:
        raise OpError(name, None, "give at least one thing to change.")
    if "content" in op:
        item["text"]["content"] = _text(name, "content", op["content"])
    _look(name, op, item["text"], ctx)


# ---- captions --------------------------------------------------------------------------------


def _caption_lines(name: str, doc: dict, lines: list) -> list:
    out = _ranges(name, "lines", lines)
    # The ids the caller gave come first, so a new id is never one of them.
    used, own = set(), []
    for entry in out:
        keeps = isinstance(entry.get("id"), str) and bool(_ID.fullmatch(entry["id"])) and entry["id"] not in used
        own.append(keeps)
        if keeps:
            used.add(entry["id"])
    for entry, keeps in zip(out, own):
        if not isinstance(entry.get("text"), str):
            raise OpError(name, "lines", "every line needs its text.")
        entry["text"] = _text(name, "lines", entry["text"])
        if not keeps:
            entry["id"] = model.next_id(doc, "c")
            while entry["id"] in used:
                entry["id"] = model.next_id(doc, "c")
            used.add(entry["id"])
    return out


def _set_captions(doc, op, ctx):
    name, captions = "set_captions", doc["captions"]
    if len(op) == 1:
        raise OpError(name, None, "give enabled, style, lines or media_id.")
    if "enabled" in op:
        if op["enabled"] is not None and not isinstance(op["enabled"], bool):
            raise OpError(name, "enabled", "must be true, false or null.")
        captions["enabled"] = op["enabled"]
    if "style" in op:
        if op["style"] is None:
            captions["style"] = None
        elif not isinstance(op["style"], dict):
            raise OpError(name, "style", "must be an object or null.")
        else:
            captions["style"] = _plain(_from_env(name, "style", ctx.env.caption_style, _sized(name, "style", op["style"])))
    if "media_id" in op:
        if op["media_id"] is not None and op["media_id"] not in doc["media"]:
            raise OpError(name, "media_id", "is not in the project's media.")
        captions["media_id"] = op["media_id"]
    if "lines" in op:
        if op["lines"] is None:
            captions["lines"] = None
        elif not isinstance(op["lines"], list):
            raise OpError(name, "lines", "must be a list or null.")
        else:
            captions["lines"] = _caption_lines(name, doc, op["lines"])


def _edit_caption_words(doc, op, ctx):
    name, captions = "edit_caption_words", doc["captions"]
    if captions["lines"] is None:
        raise OpError(name, "edits", "there are no caption lines to fix yet. Set them with set_captions first.")
    if not op["edits"]:
        raise OpError(name, "edits", "is empty.")
    by_id = {line["id"]: line for line in captions["lines"]}
    for index, edit in enumerate(op["edits"]):
        if not isinstance(edit, dict) or edit.get("line_id") not in by_id:
            raise OpError(name, "edits", f"entry {index}: line_id is not one of the caption lines.")
        unknown = [k for k in edit if k not in ("line_id", "text", "in_us", "out_us")]
        if unknown:
            raise OpError(name, "edits", f"entry {index} has no {unknown[0]!r}. It takes line_id, text, in_us, out_us.")
        line = by_id[edit["line_id"]]
        if "text" in edit:
            if not isinstance(edit["text"], str):
                raise OpError(name, "edits", f"entry {index}: text must be text.")
            if not clean_text(edit["text"]):
                captions["lines"].remove(line)
                del by_id[edit["line_id"]]
                continue
            line["text"] = _text(name, "edits", edit["text"])
        for key in ("in_us", "out_us"):
            if key in edit:
                if not model.is_time(edit[key]):
                    raise OpError(name, "edits", f"entry {index}: {key} must be a whole number of microseconds.")
                line[key] = edit[key]
        if line["out_us"] <= line["in_us"]:
            raise OpError(name, "edits", f"entry {index}: the line must end after it starts.")


def _set_speaker(doc, op, ctx):
    name, captions = "set_speaker", doc["captions"]
    if op.get("clear"):
        if len(op) > 2:
            raise OpError(name, "clear", "can't be given together with a stretch.")
        captions["speaker_edits"] = []
        return
    for key in ("in_us", "out_us", "speaker"):
        if key not in op:
            raise OpError(name, key, "is missing.")
    if op["out_us"] <= op["in_us"]:
        raise OpError(name, "out_us", "must be after in_us.")
    if op["speaker"] not in (0, 1):
        raise OpError(name, "speaker", "must be 0 (the main speaker) or 1 (the other).")
    if len(captions["speaker_edits"]) >= MAX_CAPTION_LINES:
        raise OpError(name, None, "there are too many speaker fixes.")
    captions["speaker_edits"].append({"in_us": op["in_us"], "out_us": op["out_us"], "speaker": op["speaker"]})


def _watermark(name: str, value: dict, ctx: _Ctx) -> dict:
    out = _sized(name, "watermark", value)
    # Only what the render reads (video_editor/watermark.py). A key it
    # doesn't know could hold a path, and a path never comes from a caller.
    unknown = [k for k in out if k not in WATERMARK_KEYS]
    if unknown:
        raise OpError(name, "watermark", f"has no {str(unknown[0])[:40]!r}. It takes {', '.join(WATERMARK_KEYS)}.")
    if out.get("type") not in ("image", "text", "both"):
        raise OpError(name, "watermark", "type must be image, text or both.")
    if "text" in out:
        if not isinstance(out["text"], str):
            raise OpError(name, "watermark", "text must be text.")
        out["text"] = _text(name, "watermark", out["text"], 120) if out["text"].strip() else ""
    if out.get("font") is not None:
        found = ctx.env.font(out["font"]) if isinstance(out["font"], str) else None
        if found is None:
            raise OpError(name, "watermark", f"font {str(out['font'])[:60]!r} is not one of the caption fonts.")
        out["font"] = found
    if out.get("image_asset") is not None and (
            not isinstance(out["image_asset"], str) or not _ASSET_NAME.fullmatch(out["image_asset"])
            or ".." in out["image_asset"]):
        raise OpError(name, "watermark", "image_asset must be the name of a branding image, never a path.")
    if out.get("color") is not None and (not isinstance(out["color"], str) or not _COLOUR.fullmatch(out["color"])):
        raise OpError(name, "watermark", "color must be a colour as #RRGGBB.")
    for key, (lo, hi) in {"opacity": (0, 1), "padding": (0, 0.5), "scale": (0.02, 1), "font_size": (8, 400),
                          "rotation": (-360, 360), "x": (0, 1), "y": (0, 1)}.items():
        if key in out and (not _is_num(out[key]) or not lo <= out[key] <= hi):
            raise OpError(name, "watermark", f"{key} must be a number from {lo:g} to {hi:g}.")
    if "position" in out and (not isinstance(out["position"], str) or not _ID.fullmatch(out["position"])):
        raise OpError(name, "watermark", "position must be the name of a place (bottom_right, custom, moving).")
    if "shadow" in out and not isinstance(out["shadow"], bool):
        raise OpError(name, "watermark", "shadow must be true or false.")
    return out


def _set_branding(doc, op, ctx):
    name = "set_branding"
    if op.get("clear"):
        if "watermark" in op:
            raise OpError(name, "clear", "can't be given together with a watermark.")
        doc["branding"].pop("watermark", None)
        return
    if "watermark" not in op:
        raise OpError(name, "watermark", "is missing (null switches the watermark off).")
    if op["watermark"] is None:
        doc["branding"]["watermark"] = None
    elif not isinstance(op["watermark"], dict):
        raise OpError(name, "watermark", "must be an object or null.")
    else:
        doc["branding"]["watermark"] = _watermark(name, op["watermark"], ctx)


def _set_export_preset(doc, op, ctx):
    name = "set_export_preset"
    preset = _from_env(name, "preset", ctx.env.export_preset, _sized(name, "preset", op["preset"]))
    if preset is not None and not isinstance(preset, dict):
        raise OpError(name, "preset", "could not be read as an export preset.")
    doc["export"]["preset"] = _plain(preset)


_HANDLERS = {
    "set_canvas": _set_canvas, "set_project_props": _set_project_props, "add_media": _add_media,
    "insert_clip": _insert_clip, "trim_item": _trim_item, "split_item": _split_item,
    "delete_items": _delete_items, "move_item": _move_item, "duplicate_items": _duplicate_items,
    "set_item_props": _set_item_props, "add_track": _add_track, "remove_track": _remove_track,
    "set_track_props": _set_track_props, "move_track": _move_track, "add_audio": _add_audio, "set_audio_mutes": _set_audio_mutes,
    "add_text": _add_text, "set_text": _set_text, "set_captions": _set_captions,
    "edit_caption_words": _edit_caption_words, "set_speaker": _set_speaker, "set_branding": _set_branding,
    "set_export_preset": _set_export_preset,
}


# ---- applying --------------------------------------------------------------------------------


def _one(doc: dict, op, ctx: _Ctx, depth: int) -> None:
    if not isinstance(op, dict) or not isinstance(op.get("op"), str):
        raise OpError("ops", "op", "is missing: every operation is an object with an \"op\" name.")
    name = op["op"]
    ctx.count += 1
    if ctx.count > MAX_OPS:
        raise OpError(name, None, f"one call takes at most {MAX_OPS} operations.")
    if name == "restore":
        if not ctx.internal:
            raise OpError(name, None, "is how undo and redo work, and can't be sent. Use the undo and redo routes.")
        try:
            restored = model.clone(patch.apply(doc, op.get("patch")))
        except patch.PatchError as e:
            raise OpError(name, "patch", f"doesn't fit this project: {e}.") from e
        if not isinstance(restored, dict):
            raise OpError(name, "patch", "doesn't give a project.")
        doc.clear()
        doc.update(restored)
        return
    if name not in SPECS:
        raise OpError(name[:40], "op", f"is not an operation. The operations: {', '.join(SPECS)}.")
    _check_fields(name, op)
    if name == "batch":
        if depth >= MAX_BATCH_DEPTH:
            raise OpError(name, "ops", "is nested too deeply.")
        for sub in op["ops"]:
            _one(doc, sub, ctx, depth + 1)
        return
    _HANDLERS[name](doc, op, ctx)


def restore(node) -> dict:
    return {"op": "restore", "patch": node}


def _unsavable(value) -> str | None:
    """Why a value can't be kept in a project (which is JSON, saved as
    UTF-8), in plain words; None when it can."""
    if value is None or isinstance(value, (bool, int)):
        return None
    if isinstance(value, float):
        return None if _is_num(value) else "holds a number that is not a number (NaN or Infinity)."
    if isinstance(value, str):
        try:
            value.encode("utf-8")
        except UnicodeEncodeError:
            return "holds text with a broken character in it (half of an emoji, for example)."
        return None
    if isinstance(value, list):
        return next((found for found in map(_unsavable, value) if found), None)
    if isinstance(value, dict):
        for key, inner in value.items():
            if not isinstance(key, str):
                return "holds a name that is not text."
            found = _unsavable(key) or _unsavable(inner)
            if found:
                return found
        return None
    return "holds something that is not JSON."


def _unsavable_op(op) -> None:
    if not isinstance(op, dict):
        found = _unsavable(op)
        if found:
            raise OpError("ops", "op", found)
        return
    name = op.get("op")
    name = name if isinstance(name, str) and name in SPECS else "ops"
    for key, value in op.items():
        if not isinstance(key, str) or _unsavable(key):
            raise OpError(name, None, "has a field whose name is not plain text.")
        if name == "batch" and key == "ops" and isinstance(value, list):
            for sub in value:
                _unsavable_op(sub)
            continue
        found = _unsavable(value)
        if found:
            raise OpError(name, key, found)


def _savable(ops: list) -> None:
    """Refuse, by its operation and field, what could not be saved: a number
    that is NaN or Infinity, half of a character, anything that is not JSON.
    Checked before anything is applied, since the operations are kept in the
    project's history as they were sent."""
    try:
        json.dumps(ops, ensure_ascii=False, allow_nan=False).encode("utf-8")
        return
    except RecursionError:
        raise OpError("ops", None, "is nested too deeply.") from None
    except (ValueError, TypeError):
        pass
    try:
        for op in ops:
            _unsavable_op(op)
    except RecursionError:
        raise OpError("ops", None, "is nested too deeply.") from None
    raise OpError("ops", None, "holds something that can't be saved.")


def apply(document: dict, ops: list, env: Env, *, internal: bool = False) -> Result:
    """`ops` applied to `document`, in order, all or nothing.

    Returns the new document with the inverse and forward operations and any
    warnings. Raises OpError when an operation can't be applied, or when the
    document is from a newer version of Clips Kitty (it opens read-only).
    `document` is never changed. `internal` lets `restore` through: the
    store's undo and redo pass it, a route never does."""
    if not isinstance(document, dict) or type(document.get("schema")) is not int:
        raise OpError("ops", None, "this is not a project.")
    if document["schema"] > model.SCHEMA:
        raise OpError("ops", None, "this project was saved by a newer version of Clips Kitty, so it opens "
                                   "read-only here. Update Clips Kitty to change it.")
    if not isinstance(ops, list):
        raise OpError("ops", "ops", "must be a list of operations.")
    if not internal:
        _savable(ops)
    before = model.canonical(document)
    work = model.clone(document)
    ctx = _Ctx(env=env, internal=internal)
    for op in ops:
        try:
            _one(work, op, ctx, 0)
        except OpError:
            raise
        except ValueError as e:
            # Never expected (what was sent is checked above): something the
            # rest of the app answered that a project can't hold.
            name = op.get("op") if isinstance(op, dict) and op.get("op") in SPECS else "ops"
            raise OpError(name, None, "could not be applied: it would put something in the project that "
                                      "can't be saved.") from e
    problems = model.validate(work)
    if problems:
        name = ops[-1].get("op", "ops") if ops and isinstance(ops[-1], dict) else "ops"
        raise OpError(str(name)[:40], None, f"would leave the project broken: {problems[0]}.")
    after = model.canonical(work)
    if after == before:
        return Result(work, [], [], ctx.warnings, ctx.created, changed=False, text=after)
    inverse, forward = steps(document, work, before, after)
    return Result(work, inverse, forward, ctx.warnings, ctx.created, text=after)


def steps(earlier: dict, later: dict, earlier_text: str | None = None, later_text: str | None = None) -> tuple:
    """(inverse, forward): the operations that turn `later` back into
    `earlier`, byte for byte, and `earlier` into `later`. What apply() returns
    for one call; the store asks for it when several small tweaks become one
    undo step."""
    before = model.canonical(earlier) if earlier_text is None else earlier_text
    after = model.canonical(later) if later_text is None else later_text
    undo = patch.diff(later, earlier)
    if model.canonical(patch.apply(later, undo)) != before:
        # A number changed kind (1 became 1.0) inside a value that compares
        # equal: looked for properly, so undo gives back the very bytes.
        undo = patch.diff(later, earlier, strict=True)
        redo = patch.diff(earlier, later, strict=True)
    else:
        redo = patch.diff(earlier, later)
        if model.canonical(patch.apply(earlier, redo)) != after:
            redo = patch.diff(earlier, later, strict=True)
    return [restore(undo)], [restore(redo)]


_COALESCE = {"set_item_props": "item_id", "set_text": "item_id", "set_track_props": "track_id",
             "set_project_props": None, "set_canvas": None, "set_captions": None, "set_branding": None}


def coalesce_key(ops: list) -> str:
    """What a call tweaks, when it is one small tweak: the operation, the
    item or track, and the properties it sets. Two calls with the same key,
    made a moment apart, are one undo step (store.py). "" for anything else."""
    if len(ops) != 1 or not isinstance(ops[0], dict) or ops[0].get("op") not in _COALESCE:
        return ""
    op = ops[0]
    target = _COALESCE[op["op"]]
    props = []
    for key in sorted(k for k in op if k not in ("op", target)):
        value = op[key]
        props.append(f"{key}{{{','.join(sorted(map(str, value)))}}}" if isinstance(value, dict) else key)
    if op["op"] == "set_captions" and "lines" in op:
        return ""
    return f"{op['op']}|{op.get(target, '') if target else ''}|{','.join(props)}"
