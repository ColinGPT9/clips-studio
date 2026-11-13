"""A project as an AI clip again: importer.py, the other way.

    answer = to_clip(document)

- {"branch": "A", "start", "end", "render_opts"}: the project still says
  nothing that today's render can't do, so today's `render` job can make it
  from these (the export compiler's Branch A). For a project nobody changed
  they are exactly the clip it was made from: to_clip(import_clip(x)) gives
  x's start, end and render_opts back, as they were saved.
- {"branch": "B", "reasons": [...]}: it needs the export compiler (editor
  suite step 7), and why, in plain sentences.

How a changed project is written back: the project is read as "pieces" (the
window it plays, its cuts, its speed, its mutes, its hook and so on, in the
source video's time), and so is the clip it came from, imported again. A
piece that reads the same in both keeps the value the clip was saved with,
untouched; only a piece that differs is written new. So an edit to one thing
never rewrites another, and unknown keys ride along.

What fits Branch A is what importer.py can lay out: one stretch or several
of the clip's own video on V1 at one speed, with one look; fades at the two
ends; A1's volume and mute; one looped music item from the start; one hook;
captions, speaker fixes and mutes; a watermark. Anything else is Branch B:
an overlay, a second sound, text that isn't the hook, a bleep, a different
canvas, a transform, items at different speeds or looks.

The export preset is not part of a clip's render options (an export applies
it to the finished clip), so it never decides the branch.
"""

from __future__ import annotations

from video_editor.project import importer, model
from video_editor.project.model import MAIN_AUDIO, MAIN_VIDEO

# What today's render accepts (video_editor/timeline.py EditList.from_dict);
# tests/test_project_projection.py keeps these in step with it.
SPEED_RANGE = (0.5, 3.0)
MIN_SEGMENT_US = 250_000
MERGE_GAP_US = 10_000
MAX_FADE_US = 3_000_000
HOOK_RANGE_US = (1_000_000, 10_000_000)
HOOK_MAX = 120
MUSIC_MAX_VOLUME = 1.0

_ABSENT = "\x00absent"      # a key the clip doesn't have
_TIMED = ("keep", "mutes", "muted_words", "caption_lines", "speaker_edits")
_EDIT = ("keep", "speed", "volume", "mute_all", "fade_in", "fade_out", "mutes", "muted_words", "hook", "music")
_TOP = ("crop", "gaming", "filter", "adjust", "captions", "caption_style", "caption_lines", "speaker_edits",
        "watermark", "normalize_audio")


def _same(a, b) -> bool:
    return model.canonical(a) == model.canonical(b)


def _facts(doc: dict, media_id) -> dict:
    media = doc["media"].get(media_id) or {}
    return {k: media.get(k) for k in ("duration_us", "width", "height", "fps", "has_audio", "name")}


def _again(doc: dict) -> dict | None:
    """The clip the project was made from, imported again: the project as it
    was before anyone changed it."""
    context = doc["context"]
    imported = context["imported"]
    try:
        return importer.import_clip(
            {"video_id": context.get("video_id", ""), "start": imported["start"], "end": imported["end"],
             "render_opts": imported.get("render_opts"), "title": doc.get("name", "")},
            _facts(doc, context.get("media_id")))
    except (importer.ClipImportError, KeyError, TypeError, ValueError):
        return None


def _essence(doc: dict) -> dict:
    """What of a project decides its render: all of it but its name, its id
    counter, its export preset and media nothing uses."""
    used = {item["media_id"] for t in doc["tracks"] for item in t["items"]}
    used.add(doc["captions"].get("media_id"))
    return {"canvas": doc["canvas"], "tracks": doc["tracks"], "audio": doc["audio"], "captions": doc["captions"],
            "branding": doc["branding"], "context": doc["context"], "extra": doc["extra"],
            "media": {k: v for k, v in doc["media"].items() if k in used}}


def _read(doc: dict, why: list) -> dict:
    """The project's pieces, in the source video's time. Each thing today's
    render can't do adds a sentence to `why`."""
    context = doc["context"]
    source = context.get("media_id")
    pieces: dict = {}
    main, own = model.track(doc, MAIN_VIDEO), model.track(doc, MAIN_AUDIO)
    items = main["items"]
    if not items:
        why.append("The main track is empty.")
        return pieces
    if main["mute"]:
        why.append("The main track is switched off.")
    first, last = items[0], items[-1]
    if any(item["media_id"] != source for item in items):
        why.append("The main track plays something other than the clip's own video.")
    if any(item["speed"] != first["speed"] for item in items):
        why.append("Parts of the main track play at different speeds.")
    elif not SPEED_RANGE[0] <= first["speed"] <= SPEED_RANGE[1]:
        why.append(f"The speed is outside {SPEED_RANGE[0]:g} to {SPEED_RANGE[1]:g}.")
    if any(not _same(item["framing"], first["framing"]) or not _same(item["color"], first["color"]) for item in items):
        why.append("Parts of the main track are framed or coloured differently.")
    if any(k not in ("crop", "gaming") for k in first["framing"]) or any(
            k not in ("filter", "adjust") for k in first["color"]):
        why.append("The framing or colour uses a setting today's render doesn't have.")
    if any(item["transform"] for item in items):
        why.append("A part of the main track is moved, scaled or turned.")
    if any(item["volume"] != 1 for item in items):
        why.append("A part of the main track has its own volume.")
    if any(item["fades"]["in_us"] for item in items[1:]) or any(item["fades"]["out_us"] for item in items[:-1]):
        why.append("A fade sits between two parts of the main track.")
    if first["fades"]["in_us"] > MAX_FADE_US or last["fades"]["out_us"] > MAX_FADE_US:
        why.append("A fade is longer than 3 seconds.")

    spans: list[list[int]] = []
    for item in items:
        a, b = item["source_in_us"], item["source_out_us"]
        if spans and a < spans[-1][1]:
            why.append("The main track's parts are out of the video's order, or repeat a part of it.")
            break
        if spans and a == spans[-1][1]:
            spans[-1][1] = b
        else:
            if spans and a - spans[-1][1] <= MERGE_GAP_US:
                why.append("Two parts of the main track are less than a hundredth of a second apart.")
            spans.append([a, b])
    if len(spans) > 1 and any(b - a < MIN_SEGMENT_US for a, b in spans):
        why.append("A part of the main track is shorter than a quarter of a second.")
    pieces["window"] = [first["source_in_us"], max(b for _a, b in spans)]
    pieces["keep"] = spans
    pieces["speed"] = first["speed"]
    pieces["volume"], pieces["mute_all"] = own["volume"], own["mute"]
    pieces["fade_in"], pieces["fade_out"] = first["fades"]["in_us"], last["fades"]["out_us"]
    for key in ("crop", "gaming"):
        pieces[key] = first["framing"].get(key, _ABSENT)
    for key in ("filter", "adjust"):
        pieces[key] = first["color"].get(key, _ABSENT)

    hook, music = None, None
    total = model.program_end(doc)
    for t in doc["tracks"]:
        if t["kind"] == "audio" and t.get("solo"):
            why.append(f"Track {t['id']} is soloed.")
        if t["id"] in (MAIN_VIDEO, MAIN_AUDIO) or not t["items"]:
            continue
        if t["kind"] == "video":
            why.append(f"Track {t['id']} holds an overlay.")
        elif t["kind"] == "text":
            item = t["items"][0]
            text = item["text"]
            if len(t["items"]) > 1 or hook is not None or text.get("role") != "hook":
                why.append("There is text besides the hook.")
            elif (t["mute"] or item["start_us"] != 0 or item["transform"] or item["fades"]["in_us"]
                  or item["fades"]["out_us"] or any(k not in ("content", "role") for k in text)
                  or len(text["content"]) > HOOK_MAX
                  or not HOOK_RANGE_US[0] <= item["duration_us"] <= HOOK_RANGE_US[1]):
                why.append("The hook is styled, moved or timed in a way today's render doesn't have.")
            else:
                hook = {"text": text["content"], "seconds_us": item["duration_us"]}
        else:
            item = t["items"][0]
            media = doc["media"].get(item["media_id"]) or {}
            kind = (media.get("source") or {}).get("type")
            if len(t["items"]) > 1 or music is not None or item.get("role") != "music":
                why.append("There is sound besides one music track.")
            elif (not item.get("loop") or item.get("loop_offset_us") or item["start_us"] != 0
                  or item["duration_us"] < total
                  or item["source_in_us"] != 0 or item["speed"] != 1 or item["fades"]["in_us"]
                  or item["fades"]["out_us"] or item["volume"] > MUSIC_MAX_VOLUME or t["mute"]
                  or t["volume"] != 1 or kind not in ("library", "file")):
                why.append("The music doesn't play looped under the whole video at one volume.")
            else:
                named = ({"library": media["source"].get("id")} if kind == "library"
                         else {"path": media["source"].get("path")})
                music = {**named, "volume": item["volume"], "duck": bool(item.get("duck", False))}
    pieces["hook"], pieces["music"] = hook, music
    if doc["audio"]["master_volume"] != 1:
        why.append("The master volume is changed.")
    pieces["normalize_audio"] = doc["audio"]["normalize"]

    captions = doc["captions"]
    if captions.get("media_id") != source and (
            captions.get("lines") or captions["speaker_edits"] or captions["mutes"] or captions["muted_words"]):
        why.append("The captions are timed to something other than the clip's own video.")
    if any(m.get("mode") != "mute" for m in captions["mutes"]):
        why.append("A stretch is bleeped.")
    pieces["mutes"] = [[m["in_us"], m["out_us"]] for m in captions["mutes"]]
    pieces["muted_words"] = captions["muted_words"]
    pieces["captions"] = captions.get("enabled")
    pieces["caption_style"] = captions.get("style")
    pieces["caption_lines"] = captions.get("lines")
    pieces["speaker_edits"] = captions["speaker_edits"]
    pieces["watermark"] = doc["branding"].get("watermark", _ABSENT)
    return pieces


def _rel(at_us: int, origin_us: int) -> float:
    return model.seconds(at_us - origin_us)


def _inside(a: int, b: int, origin: int, end: int) -> tuple[float, float] | None:
    """[a, b] cut to the clip, counted from its start; None when under a
    hundredth of a second of it is inside."""
    a, b = max(a, origin), min(b, end)
    return (_rel(a, origin), _rel(b, origin)) if b - a > MERGE_GAP_US else None


def _written(key: str, pieces: dict, origin: int, end: int, whole: tuple[int, int]):
    """One piece as render_opts holds it; _ABSENT for "no such key"."""
    value = pieces[key]
    if key == "keep":
        if len(value) == 1 and abs(value[0][0] - whole[0]) <= 1 and abs(value[0][1] - whole[1]) <= 1:
            return _ABSENT
        return [[_rel(a, origin), _rel(b, origin)] for a, b in value]
    if key in ("fade_in", "fade_out"):
        return model.seconds(value)
    if key == "mutes":
        return [list(r) for r in (_inside(a, b, origin, end) for a, b in value) if r is not None]
    if key == "muted_words":
        return [{**{k: v for k, v in word.items() if k not in ("in_us", "out_us")},
                 "start": _rel(word["in_us"], origin), "end": _rel(word["out_us"], origin)}
                for word in value if _inside(word["in_us"], word["out_us"], origin, end) is not None]
    if key == "hook":
        return _ABSENT if value is None else {"text": value["text"], "seconds": model.seconds(value["seconds_us"])}
    if key == "music":
        return _ABSENT if value is None else value
    if key in ("captions", "caption_style"):
        return _ABSENT if value is None else value
    if key == "caption_lines":
        if value is None:
            return _ABSENT
        out = []
        for line in value:
            cut = _inside(line["in_us"], line["out_us"], origin, end)
            if cut is not None:
                out.append({**{k: v for k, v in line.items() if k not in ("id", "in_us", "out_us")},
                            "start": cut[0], "end": cut[1]})
        return out
    if key == "speaker_edits":
        # Never cut to the clip: a fix past its end is back when it grows.
        return [[_rel(e["in_us"], origin), _rel(e["out_us"], origin), e["speaker"]] for e in value] or _ABSENT
    return value


def to_clip(doc: dict, exact_first: bool = True) -> dict:
    """The project as {"branch": "A", "start", "end", "render_opts"}, or
    {"branch": "B", "reasons"} when it needs the export compiler. Never
    changes `doc`. `exact_first` (always on outside tests) answers for a
    project nobody changed without reading it piece by piece."""
    context = doc.get("context") if isinstance(doc, dict) else None
    imported = context.get("imported") if isinstance(context, dict) else None
    if (not isinstance(context, dict) or context.get("origin") != "clip" or not isinstance(imported, dict)
            or not isinstance(context.get("clip"), dict)):
        return {"branch": "B", "reasons": ["This project wasn't made from a clip."]}
    problems = model.validate(doc)
    if problems:
        return {"branch": "B", "reasons": [f"The project isn't valid: {problems[0]}."]}
    base = _again(doc)
    if base is None:
        return {"branch": "B", "reasons": ["The clip this project was made from can't be read any more."]}
    exact = {"branch": "A", "start": imported["start"], "end": imported["end"],
             "render_opts": model.clone(imported.get("render_opts"))}
    if exact_first and _same(_essence(doc), _essence(base)):
        return exact

    why: list[str] = []
    if not _same(doc["canvas"], base["canvas"]):
        why.append("The picture's size or frame rate is changed.")
    now = _read(doc, why)
    if why:
        return {"branch": "B", "reasons": why}
    was = _read(base, [])
    moved = now["window"] != was["window"]
    # With the window moved, every time counted from its start is written
    # again, changed or not (there is nothing to write for an empty one).
    changed = [k for k in (*_EDIT, *_TOP)
               if not _same(now[k], was.get(k)) or (moved and k in _TIMED and now[k])]
    if not moved and not changed:
        return exact

    clip = context["clip"]
    if moved:
        origin, end = now["window"]
        start_s, end_s = model.seconds(origin), model.seconds(end)
    else:
        origin, end = clip["start_us"], clip["end_us"]
        start_s, end_s = imported["start"], imported["end"]
    whole = (origin, max(end, now["window"][1]) if moved else end)
    opts = model.clone(importer.options(imported.get("render_opts")))
    edit = opts["edit"] if isinstance(opts.get("edit"), dict) else {}
    for key in changed:
        value = _written(key, now, origin, whole[1], whole)
        target = edit if key in _EDIT else opts
        if isinstance(value, str) and value == _ABSENT:
            target.pop(key, None)
        else:
            target[key] = model.clone(value)
    if edit:
        opts["edit"] = edit
    return {"branch": "A", "start": start_s, "end": end_s, "render_opts": opts}
