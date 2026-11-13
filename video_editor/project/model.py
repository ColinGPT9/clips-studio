"""The project document: what an editor project is, as plain JSON.

  {
    "schema": 1,
    "name": "My clip",
    "seq": 7,                       # the next id number (ids are i7, m7, c7)
    "canvas": {"width": 1080, "height": 1920, "fps": {"num": 30, "den": 1}},
    "media": {"m1": {"id": "m1", "kind": "video", "source": {"type": "video", "video_id": "..."},
                     "duration_us": 600000000, "width": 1920, "height": 1080,
                     "fps": {"num": 30, "den": 1}, "has_audio": true, "name": "", "extra": {}}},
    "tracks": [
      {"id": "V1", "kind": "video", "name": "V1", "mute": false, "items": [...], "extra": {}},
      {"id": "T1", "kind": "text",  "name": "Text", "mute": false, "items": [...], "extra": {}},
      {"id": "A1", "kind": "audio", "name": "A1", "mute": false, "volume": 1.0, "solo": false,
       "items": [], "extra": {}},
      {"id": "A2", "kind": "audio", "name": "Music", ...}
    ],
    "audio": {"master_volume": 1.0, "normalize": true},
    "captions": {"media_id": "m1", "enabled": null, "style": null, "lines": null,
                 "speaker_edits": [], "mutes": [], "muted_words": []},
    "branding": {},
    "export": {"preset": null},
    "context": {},
    "extra": {}
  }

Tracks, in the order they are stacked (a later video or text track is on top):
- V1 is the main video track. It is gapless: its first item starts at 0 and
  each item starts where the one before ends. Every edge is on the canvas's
  frame grid.
- V2 and up are overlays; T tracks hold text. Their items sit anywhere, in
  start order, and never overlap inside one track.
- A1 is the sound of V1's own items. It holds no items: each V1 item carries
  its own volume and fades, and A1 its name, volume, mute and solo.
- A2 and up hold music, sound effects and voice.

An item:
  {"id": "i3", "media_id": "m1", "source_in_us": 10000000, "source_out_us": 20000000,
   "start_us": 0, "duration_us": 10000000, "speed": 1.0, "volume": 1.0,
   "fades": {"in_us": 0, "out_us": 0}, "framing": {}, "transform": {}, "color": {},
   "text": null, "link": null, "extra": {}}
plus, on an audio track, "role" (music | sfx | voice), "loop" and "duck".
Looped music plays source_in_us to source_out_us again and again for
duration_us; "loop_offset_us" (only there when it isn't 0) is how far into
that stretch it starts, which is what keeps a split or a trimmed start in
time with the music.

Time is a whole number of microseconds everywhere ("..._us"): exact in JSON
and in JavaScript (always below 2**53), and independent of the frame rate. A
frame rate is a fraction, {"num": 30000, "den": 1001} for 29.97. Captions,
speaker fixes and mutes are in the SOURCE's time (captions.media_id), so they
stay on the same words when items are trimmed or moved.

Keys this version doesn't know are kept as they are, wherever they appear.
validate() reports what is wrong with a document, as plain sentences;
canonical() writes it the one way it is ever written.
"""

from __future__ import annotations

import json
from fractions import Fraction

SCHEMA = 1
US = 1_000_000
MAX_US = 2**53 - 1          # the largest whole number JavaScript holds exactly

CANVAS_PRESETS = {"9:16": (1080, 1920), "16:9": (1920, 1080), "1:1": (1080, 1080), "4:5": (1080, 1350)}
MIN_SIDE, MAX_SIDE = 16, 4096
MIN_FPS, MAX_FPS = 1, 240
DEFAULT_FPS = {"num": 30, "den": 1}

TRACK_KINDS = ("video", "text", "audio")
MEDIA_KINDS = ("video", "audio", "image")
AUDIO_ROLES = ("music", "sfx", "voice")
TEXT_ROLES = ("text", "hook")
MUTE_MODES = ("mute", "bleep")
MAIN_VIDEO, MAIN_AUDIO = "V1", "A1"

SPEED_RANGE = (0.25, 4.0)
VOLUME_RANGE = (0.0, 2.0)
TEXT_MAX = 500
NAME_MAX = 120

ITEM_KEYS = ("id", "media_id", "source_in_us", "source_out_us", "start_us", "duration_us", "speed",
             "volume", "fades", "framing", "transform", "color", "text", "link", "extra")


class ModelError(ValueError):
    """A document that isn't a valid project."""


# ---- time and the frame grid -----------------------------------------------------------------


def is_time(value) -> bool:
    """A time as the document stores it: a whole number of microseconds."""
    return type(value) is int and 0 <= value <= MAX_US


def us(seconds) -> int:
    """Seconds, as today's clips keep them, in microseconds."""
    return int(round(float(seconds) * US))


def seconds(microseconds: int) -> float:
    """Microseconds as seconds, to the microsecond."""
    return round(microseconds / US, 6)


def fps_of(value) -> dict:
    """A frame rate as {"num", "den"}: from that, or from a number. 23.976,
    29.97 and 59.94 are the broadcast fractions (x1000/1001)."""
    if isinstance(value, dict):
        num, den = value.get("num"), value.get("den")
        if type(num) is not int or type(den) is not int or num <= 0 or den <= 0:
            raise ModelError("a frame rate is {\"num\": whole number, \"den\": whole number}")
        frac = Fraction(num, den)
    elif isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ModelError("a frame rate is a number, or {\"num\", \"den\"}")
    elif isinstance(value, float) and (value != value or abs(value) == float("inf")):
        raise ModelError("a frame rate is a number")
    elif not -MAX_FPS * 2 < value < MAX_FPS * 2:      # also a number too large to be read as a fraction
        raise ModelError(f"a frame rate is between {MIN_FPS} and {MAX_FPS} frames a second")
    else:
        frac = None
        for whole in (24, 30, 60, 48, 120):
            if abs(float(value) - whole * 1000 / 1001) < 0.006:
                frac = Fraction(whole * 1000, 1001)
        if frac is None:
            frac = Fraction(str(value)).limit_denominator(1001)
    if not MIN_FPS <= frac <= MAX_FPS:
        raise ModelError(f"a frame rate is between {MIN_FPS} and {MAX_FPS} frames a second")
    return {"num": frac.numerator, "den": frac.denominator}


def frame_us(n: int, fps: dict) -> int:
    """Where frame n starts, to the nearest microsecond. Always worked out
    from the frame number, never by adding frame lengths, so it cannot drift."""
    num, den = fps["num"], fps["den"]
    return (2 * n * US * den + num) // (2 * num)


def frames_at(time_us: int, fps: dict) -> int:
    """The frame nearest to a time."""
    num, den = fps["num"], fps["den"]
    return (2 * time_us * num + US * den) // (2 * US * den)


def program_end(doc: dict) -> int:
    """Where the main track ends: the length of the video."""
    items = track(doc, MAIN_VIDEO)["items"]
    return items[-1]["start_us"] + items[-1]["duration_us"] if items else 0


# ---- building --------------------------------------------------------------------------------


def new_track(track_id: str, kind: str, name: str | None = None) -> dict:
    out = {"id": track_id, "kind": kind, "name": track_id if name is None else name, "mute": False,
           "items": [], "extra": {}}
    if kind == "audio":
        out["volume"] = 1.0
        out["solo"] = False
    return out


def new_item(item_id: str, **fields) -> dict:
    """An item with every key present. `fields` replace the defaults."""
    out = {"id": item_id, "media_id": None, "source_in_us": 0, "source_out_us": 0, "start_us": 0,
           "duration_us": 0, "speed": 1.0, "volume": 1.0, "fades": {"in_us": 0, "out_us": 0},
           "framing": {}, "transform": {}, "color": {}, "text": None, "link": None, "extra": {}}
    out.update(fields)
    return out


def new_project(name: str = "", width: int = 1080, height: int = 1920, fps=None) -> dict:
    """An empty project: V1, a text track, A1 and one more audio track."""
    doc = {
        "schema": SCHEMA,
        "name": name,
        "seq": 1,
        "canvas": {"width": width, "height": height, "fps": fps_of(fps if fps is not None else DEFAULT_FPS)},
        "media": {},
        "tracks": [new_track("V1", "video"), new_track("T1", "text", "Text"),
                   new_track("A1", "audio"), new_track("A2", "audio", "Music")],
        "audio": {"master_volume": 1.0, "normalize": True},
        "captions": {"media_id": None, "enabled": None, "style": None, "lines": None,
                     "speaker_edits": [], "mutes": [], "muted_words": []},
        "branding": {},
        "export": {"preset": None},
        "context": {},
        "extra": {},
    }
    check(doc)
    return doc


def next_id(doc: dict, prefix: str) -> str:
    """A new id (i for an item, m for media, c for a caption line). The
    document counts them, so the same operations always give the same ids."""
    n = doc["seq"]
    doc["seq"] = n + 1
    return f"{prefix}{n}"


def track(doc: dict, track_id: str) -> dict | None:
    return next((t for t in doc["tracks"] if t["id"] == track_id), None)


def find_item(doc: dict, item_id: str):
    """(track, index, item) for an item id, or None."""
    for t in doc["tracks"]:
        for index, item in enumerate(t["items"]):
            if item["id"] == item_id:
                return t, index, item
    return None


# ---- writing ---------------------------------------------------------------------------------


def canonical(doc) -> str:
    """The document as it is always written: keys in order, no spaces, no
    NaN. The same document gives the same text, on every system."""
    return json.dumps(doc, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


def to_bytes(doc) -> bytes:
    return canonical(doc).encode("utf-8")


def clone(doc):
    """A deep copy, by way of JSON: also proof the document is JSON."""
    return json.loads(json.dumps(doc, allow_nan=False))


# ---- checking --------------------------------------------------------------------------------


def _number(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and value == value \
        and abs(value) != float("inf")


def _fps_ok(value) -> bool:
    return (isinstance(value, dict) and type(value.get("num")) is int and type(value.get("den")) is int
            and value["num"] > 0 and value["den"] > 0
            and MIN_FPS * value["den"] <= value["num"] <= MAX_FPS * value["den"])


# What the document carries as it was given and never reads the times of: a
# Gaming layout, a colour adjustment, a caption style, an export preset,
# where a media comes from, and anything under "extra". A key ending in
# "_us" inside one of them is the business of whoever wrote it.
VERBATIM = ("framing", "transform", "color", "text", "extra", "style", "preset", "source")


def _bad_times(value, where: str, out: list) -> None:
    """Every "..._us" key of the document's own must hold a whole number of
    microseconds. What is carried verbatim (VERBATIM) is not looked into."""
    if isinstance(value, dict):
        for key, inner in value.items():
            if isinstance(inner, (dict, list)):
                if key not in VERBATIM:
                    _bad_times(inner, f"{where}.{key}", out)
            elif key.endswith("_us") and not is_time(inner):
                out.append(f"{where}.{key} must be a whole number of microseconds, not {inner!r}")
    elif isinstance(value, list):
        for index, inner in enumerate(value):
            if isinstance(inner, (dict, list)):
                _bad_times(inner, f"{where}[{index}]", out)


def _item_problems(item, where: str, kind: str, main: bool, doc: dict, fps: dict, out: list) -> bool:
    """One item's problems; True when its times can be used to check the track."""
    if not isinstance(item, dict):
        out.append(f"{where} must be an object")
        return False
    missing = [k for k in ITEM_KEYS if k not in item]
    if missing:
        out.append(f"{where} has no {missing[0]}")
        return False
    if not isinstance(item["id"], str) or not item["id"]:
        out.append(f"{where}.id must be text")
    times = ("source_in_us", "source_out_us", "start_us", "duration_us")
    if not all(is_time(item[k]) for k in times):
        out.append(f"{where}: its times must be whole numbers of microseconds")
        return False
    if item["duration_us"] <= 0:
        out.append(f"{where} has no length")
        return False
    if item["start_us"] + item["duration_us"] > MAX_US:
        out.append(f"{where} ends too late")
        return False
    speed, volume, fades = item["speed"], item["volume"], item["fades"]
    if not _number(speed) or not SPEED_RANGE[0] <= speed <= SPEED_RANGE[1]:
        out.append(f"{where}.speed must be between {SPEED_RANGE[0]} and {SPEED_RANGE[1]}")
        return False
    if not _number(volume) or not VOLUME_RANGE[0] <= volume <= VOLUME_RANGE[1]:
        out.append(f"{where}.volume must be between 0 and 2")
    if (not isinstance(fades, dict) or not is_time(fades.get("in_us")) or not is_time(fades.get("out_us"))
            or fades["in_us"] + fades["out_us"] > item["duration_us"]):
        out.append(f"{where}.fades must be {{in_us, out_us}} and fit inside the item")
    for key in ("framing", "transform", "color", "extra"):
        if not isinstance(item[key], dict):
            out.append(f"{where}.{key} must be an object")
    if item["link"] is not None and not isinstance(item["link"], str):
        out.append(f"{where}.link must be an item id or null")
    text = item["text"]
    if kind == "text":
        if item["media_id"] is not None:
            out.append(f"{where} is text and can't have media")
        if (not isinstance(text, dict) or not isinstance(text.get("content"), str) or not text["content"]
                or len(text["content"]) > TEXT_MAX):
            out.append(f"{where}.text.content must be 1 to {TEXT_MAX} characters")
        elif text.get("role", "text") not in TEXT_ROLES:
            out.append(f"{where}.text.role must be one of {', '.join(TEXT_ROLES)}")
        if item["source_in_us"] != 0 or item["source_out_us"] != item["duration_us"] or speed != 1:
            out.append(f"{where} is text: its source times are 0 and its length, at speed 1")
        return True
    if text is not None:
        out.append(f"{where}.text must be null on a {kind} track")
    media = doc["media"].get(item["media_id"]) if isinstance(item["media_id"], str) else None
    if media is None:
        out.append(f"{where}.media_id {item['media_id']!r} is not in the project's media")
        return True
    wanted = ("video", "image") if kind == "video" else ("audio", "video")
    if media.get("kind") not in wanted:
        out.append(f"{where}: {media.get('kind')} media can't go on a {kind} track")
        return True
    if kind == "audio":
        if item.get("role") not in AUDIO_ROLES:
            out.append(f"{where}.role must be one of {', '.join(AUDIO_ROLES)}")
        if not isinstance(item.get("loop", False), bool) or not isinstance(item.get("duck", False), bool):
            out.append(f"{where}.loop and .duck must be true or false")
        elif item.get("loop") and item.get("role") != "music":
            out.append(f"{where}: only music can loop")
    if item["source_out_us"] <= item["source_in_us"]:
        out.append(f"{where}.source_out_us must be after source_in_us")
        return True
    if "loop_offset_us" in item:
        offset = item["loop_offset_us"]
        if kind != "audio" or item.get("loop") is not True:
            out.append(f"{where}.loop_offset_us only belongs on looped music")
        elif not is_time(offset) or offset >= item["source_out_us"] - item["source_in_us"]:
            out.append(f"{where}.loop_offset_us must be inside the stretch that loops")
    length = media.get("duration_us") or 0
    if media.get("kind") == "image":
        if item["source_in_us"] != 0 or item["source_out_us"] != item["duration_us"] or speed != 1:
            out.append(f"{where} is a picture: its source times are 0 and its length, at speed 1")
        return True
    if length and item["source_out_us"] > length:
        out.append(f"{where}.source_out_us is past the end of its media")
    if not item.get("loop"):
        # The source it plays and how long it plays agree, to within a frame
        # (the main track's lengths are whole frames) and the rounding.
        slack = int(frame_us(1, fps) * speed) + 2 if main else int(speed) + 2
        if abs((item["source_out_us"] - item["source_in_us"]) - round(item["duration_us"] * speed)) > slack:
            out.append(f"{where}: its length doesn't match its source times at speed {speed}")
    return True


def _track_problems(t, where: str, doc: dict, fps: dict, seen: set, out: list) -> None:
    if not isinstance(t, dict) or not isinstance(t.get("items"), list):
        out.append(f"{where} must be an object with a list of items")
        return
    kind = t.get("kind")
    if kind not in TRACK_KINDS:
        out.append(f"{where}.kind must be one of {', '.join(TRACK_KINDS)}")
        return
    if not isinstance(t.get("name"), str) or len(t["name"]) > NAME_MAX:
        out.append(f"{where}.name must be text of at most {NAME_MAX} characters")
    if not isinstance(t.get("mute"), bool):
        out.append(f"{where}.mute must be true or false")
    if not isinstance(t.get("extra"), dict):
        out.append(f"{where}.extra must be an object")
    if kind == "audio":
        if not _number(t.get("volume")) or not VOLUME_RANGE[0] <= t["volume"] <= VOLUME_RANGE[1]:
            out.append(f"{where}.volume must be between 0 and 2")
        if not isinstance(t.get("solo"), bool):
            out.append(f"{where}.solo must be true or false")
    main = t["id"] == MAIN_VIDEO
    if t["id"] == MAIN_AUDIO and t["items"]:
        out.append(f"{where}: A1 is the sound of V1's items and holds none of its own")
    edge, frame = 0, 0
    for index, item in enumerate(t["items"]):
        at = f"{where}.items[{index}]"
        if not _item_problems(item, at, kind, main, doc, fps, out):
            return
        if item["id"] in seen:
            out.append(f"{at}.id {item['id']!r} is used twice")
        seen.add(item["id"])
        end = item["start_us"] + item["duration_us"]
        if main:
            last = frames_at(end, fps)
            if item["start_us"] != edge:
                out.append(f"{at}: the main track has no gaps, so it must start at {edge}")
                return
            if last <= frame or end != frame_us(last, fps):
                out.append(f"{at}: its end is not on the frame grid")
                return
            frame = last
        elif item["start_us"] < edge:
            out.append(f"{at} overlaps the item before it")
            return
        edge = end


def _caption_problems(captions, doc: dict, out: list) -> None:
    if not isinstance(captions, dict):
        out.append("captions must be an object")
        return
    if captions.get("media_id") is not None and captions.get("media_id") not in doc["media"]:
        out.append("captions.media_id is not in the project's media")
    if captions.get("enabled") is not None and not isinstance(captions.get("enabled"), bool):
        out.append("captions.enabled must be true, false or null")
    if captions.get("style") is not None and not isinstance(captions.get("style"), dict):
        out.append("captions.style must be an object or null")
    lines = captions.get("lines")
    if lines is not None and not isinstance(lines, list):
        out.append("captions.lines must be a list or null")
        lines = None
    ids = set()
    for index, line in enumerate(lines or []):
        at = f"captions.lines[{index}]"
        if (not isinstance(line, dict) or not isinstance(line.get("id"), str) or line["id"] in ids
                or not isinstance(line.get("text"), str)):
            out.append(f"{at} must have its own id and its text")
            continue
        ids.add(line["id"])
        if not is_time(line.get("in_us")) or not is_time(line.get("out_us")) or line["out_us"] <= line["in_us"]:
            out.append(f"{at} must end after it starts")
    for key in ("speaker_edits", "mutes", "muted_words"):
        entries = captions.get(key)
        if not isinstance(entries, list):
            out.append(f"captions.{key} must be a list")
            continue
        for index, entry in enumerate(entries):
            at = f"captions.{key}[{index}]"
            if (not isinstance(entry, dict) or not is_time(entry.get("in_us")) or not is_time(entry.get("out_us"))
                    or entry["out_us"] <= entry["in_us"]):
                out.append(f"{at} must be an object that ends after it starts")
            elif key == "mutes" and entry.get("mode") not in MUTE_MODES:
                out.append(f"{at}.mode must be mute or bleep")
            elif key == "speaker_edits" and entry.get("speaker") not in (0, 1):
                out.append(f"{at}.speaker must be 0 (the main speaker) or 1")


def validate(doc) -> list[str]:
    """What is wrong with a document, one plain sentence each; [] when it is
    a valid project. Never changes it."""
    if not isinstance(doc, dict):
        return ["a project is an object"]
    out: list[str] = []
    if type(doc.get("schema")) is not int or doc["schema"] < 1:
        return ["schema must be a whole number, 1 or more"]
    for key, kind in (("name", str), ("canvas", dict), ("media", dict), ("tracks", list), ("audio", dict),
                      ("captions", dict), ("branding", dict), ("export", dict), ("context", dict),
                      ("extra", dict)):
        if not isinstance(doc.get(key), kind):
            out.append(f"{key} is missing or of the wrong kind")
    if type(doc.get("seq")) is not int or doc["seq"] < 1:
        out.append("seq must be a whole number, 1 or more")
    if out:
        return out
    if len(doc["name"]) > NAME_MAX:
        out.append(f"name must be at most {NAME_MAX} characters")
    canvas = doc["canvas"]
    for side in ("width", "height"):
        value = canvas.get(side)
        if type(value) is not int or not MIN_SIDE <= value <= MAX_SIDE or value % 2:
            out.append(f"canvas.{side} must be an even whole number from {MIN_SIDE} to {MAX_SIDE}")
    if not _fps_ok(canvas.get("fps")):
        return [*out, "canvas.fps must be {num, den}, between 1 and 240 frames a second"]
    fps = canvas["fps"]
    for media_id, media in doc["media"].items():
        at = f"media.{media_id}"
        if not isinstance(media, dict) or media.get("id") != media_id:
            out.append(f"{at} must be an object with that id")
            continue
        if media.get("kind") not in MEDIA_KINDS:
            out.append(f"{at}.kind must be one of {', '.join(MEDIA_KINDS)}")
        if not isinstance(media.get("source"), dict) or not isinstance(media["source"].get("type"), str):
            out.append(f"{at}.source must say where it comes from")
        if not is_time(media.get("duration_us", 0)):
            out.append(f"{at}.duration_us must be a whole number of microseconds")
        if media.get("fps") is not None and not _fps_ok(media["fps"]):
            out.append(f"{at}.fps must be {{num, den}} or null")
    ids = [t.get("id") if isinstance(t, dict) else None for t in doc["tracks"]]
    if len(set(ids)) != len(ids) or not all(isinstance(i, str) and i for i in ids):
        return [*out, "every track needs its own id"]
    for wanted, kind in ((MAIN_VIDEO, "video"), (MAIN_AUDIO, "audio")):
        found = track(doc, wanted)
        if found is None or found.get("kind") != kind:
            out.append(f"the project needs its {wanted} track")
    seen: set = set()
    for index, t in enumerate(doc["tracks"]):
        _track_problems(t, f"tracks[{index}]", doc, fps, seen, out)
    audio = doc["audio"]
    if not _number(audio.get("master_volume")) or not VOLUME_RANGE[0] <= audio["master_volume"] <= VOLUME_RANGE[1]:
        out.append("audio.master_volume must be between 0 and 2")
    if not isinstance(audio.get("normalize"), bool):
        out.append("audio.normalize must be true or false")
    _caption_problems(doc["captions"], doc, out)
    if "preset" not in doc["export"] or not isinstance(doc["export"]["preset"], (dict, type(None))):
        out.append("export.preset must be an export preset or null")
    # Caption lines, mutes and speaker fixes had their own times checked
    # above; the rest of each entry (a line's words, a muted word's own keys)
    # came from the clip as it was and is kept as it was.
    for key in ("canvas", "media", "tracks", "audio", "export"):
        _bad_times(doc[key], key, out)
    return out


def check(doc) -> None:
    """Raise ModelError with the first problem, if the document has one."""
    problems = validate(doc)
    if problems:
        raise ModelError(problems[0])
