"""An AI clip as a project.

A clip today is a window of its source video ({start, end} in seconds) and
its saved render options (clips.render_opts). import_clip() lays that out as
a project, and projection.py turns a project back into {start, end,
render_opts}; for a project nobody changed, exactly the ones it came from.

  render_opts                         the project
  ----------------------------------  ------------------------------------------
  the window, edit.keep, edit.speed   V1 items (source times of the video), their speed
  edit.volume, edit.mute_all          A1's volume and mute
  edit.fade_in, edit.fade_out         the first item's fade in, the last one's fade out
  edit.mutes, edit.muted_words        captions.mutes / muted_words, in source time
  edit.music (a file or a free track) media + one looped item on A2
  edit.hook                           a text item (role "hook") on T1
  crop, gaming                        each V1 item's framing, verbatim
  filter, adjust                      each V1 item's color, verbatim
  captions, caption_style,            captions.enabled, .style, .lines and
    caption_lines, speaker_edits        .speaker_edits (in source time)
  watermark                           branding.watermark, verbatim
  normalize_audio                     audio.normalize
  what the job decides (JOB_KEYS)     context.job, verbatim
  anything else                       extra.legacy, verbatim

The edit is read as the render reads it (video_editor/timeline.py
EditList.from_dict: ranges sorted, merged and clamped, values clamped), so
the project shows what the clip really plays. The clip as it was saved is
kept whole in context.imported: projection.py gives a value back exactly as
it was saved for as long as the project still says the same thing.
"""

from __future__ import annotations

import json

from video_editor.project import model

# What the job that made a clip decides, or the render writes back: carried
# with the project and never edited there.
JOB_KEYS = ("profile", "podcast", "vertical_live", "sport", "speaker_turns", "job_captions", "stop_by",
            "sport_screen", "sport_moment", "framing", "whole", "reel", "of")
# The keys the project holds in a place of its own.
MAPPED_KEYS = ("edit", "crop", "gaming", "captions", "caption_style", "caption_lines", "speaker_edits",
               "filter", "adjust", "watermark", "normalize_audio")
EDIT_KEYS = ("keep", "mutes", "muted_words", "volume", "mute_all", "fade_in", "fade_out", "speed", "hook",
             "music")
SOURCE = "m1"       # the clip's source video, in the project's media


class ClipImportError(ValueError):
    """A clip that can't be laid out as a project."""


def options(raw) -> dict:
    """Saved render options as a dict: from one, or from the JSON text a
    clip's row holds ('' and anything unreadable are none)."""
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str) and raw.strip():
        try:
            parsed = json.loads(raw)
        except ValueError:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def clip_of_row(row) -> dict:
    """A clip's row (core/state.py clips) as import_clip takes it. Render
    options that can't be read count as none, as the render reads them."""
    return {"video_id": row["video_id"], "start": float(row["start_s"]), "end": float(row["end_s"]),
            "render_opts": options(row["render_opts"]), "title": row["title"] or ""}


def _number(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and value == value \
        and abs(value) != float("inf")


def _parsed_edit(edit, duration: float):
    """The edit as the render reads it, or None when it applies none."""
    if not isinstance(edit, dict) or not edit:
        return None
    from video_editor.timeline import EditList

    try:
        return EditList.from_dict(edit, duration=duration)
    except (TypeError, ValueError, KeyError, IndexError):
        return None


def _at(start_us: int, value) -> int | None:
    """A time counted from the clip's start, in seconds, as a time of the
    source video; None when it isn't a number or falls before the video."""
    if not _number(value):
        return None
    at = start_us + model.us(value)
    return at if 0 <= at <= model.MAX_US else None


def _span(start_us: int, a, b) -> tuple[int, int] | None:
    first, last = _at(start_us, a), _at(start_us, b)
    return (first, last) if first is not None and last is not None and last > first else None


def import_clip(clip: dict, media: dict | None = None) -> dict:
    """The project for one clip.

    `clip` is {"video_id", "start", "end", "render_opts"} (and "title"), as
    clip_of_row() gives it. `media` is what is known about the source video
    ({"duration_us", "width", "height", "fps", "has_audio", "name"}), all
    optional: the frame rate sets the canvas's, 30 when it isn't known."""
    try:
        start, end = float(clip["start"]), float(clip["end"])
    except (KeyError, TypeError, ValueError) as e:
        raise ClipImportError("The clip has no start and end.") from e
    if not end > start or start < 0:
        raise ClipImportError("The clip ends before it starts.")
    raw = clip.get("render_opts")
    opts = options(raw)
    media = media or {}
    start_us, end_us = model.us(start), model.us(end)
    landscape = bool(opts.get("profile"))
    try:
        fps = model.fps_of(media["fps"]) if media.get("fps") else dict(model.DEFAULT_FPS)
    except model.ModelError:
        fps = dict(model.DEFAULT_FPS)
    doc = model.new_project(name=str(clip.get("title") or "")[:model.NAME_MAX],
                            width=1920 if landscape else 1080, height=1080 if landscape else 1920, fps=fps)

    edit_raw = opts.get("edit") if isinstance(opts.get("edit"), dict) else None
    edit = _parsed_edit(edit_raw, end - start)
    keep = edit.keep if edit is not None and edit.keep is not None else [(0.0, end - start)]
    speed = float(edit.speed) if edit is not None and abs(edit.speed - 1.0) >= 0.01 else 1.0

    spans = [(start_us + model.us(a), start_us + model.us(b)) for a, b in keep]
    spans = [(a, b) for a, b in spans if b > a]
    if not spans:
        raise ClipImportError("The clip has no length.")
    length = int(media.get("duration_us") or 0)
    if length < max(b for _a, b in spans):
        length = 0     # not known, or shorter than the clip says the video is
    video_id = model.next_id(doc, "m")
    doc["media"][video_id] = {
        "id": video_id, "kind": "video", "source": {"type": "video", "video_id": str(clip.get("video_id") or "")},
        "duration_us": length, "width": int(media.get("width") or 0), "height": int(media.get("height") or 0),
        "fps": fps if media.get("fps") else None, "has_audio": bool(media.get("has_audio", True)),
        "name": str(media.get("name") or "")[:model.NAME_MAX], "extra": {},
    }

    framing = {k: model.clone(opts[k]) for k in ("crop", "gaming") if k in opts}
    color = {k: model.clone(opts[k]) for k in ("filter", "adjust") if k in opts}
    main = model.track(doc, model.MAIN_VIDEO)
    frame = 0
    for source_in, source_out in spans:
        frames = max(1, model.frames_at(round((source_out - source_in) / speed), fps))
        at = model.frame_us(frame, fps)
        main["items"].append(model.new_item(
            model.next_id(doc, "i"), media_id=video_id, source_in_us=source_in, source_out_us=source_out,
            start_us=at, duration_us=model.frame_us(frame + frames, fps) - at, speed=speed,
            framing=model.clone(framing), color=model.clone(color)))
        frame += frames
    total = model.program_end(doc)

    captions = doc["captions"]
    captions["media_id"] = video_id
    if edit is not None:
        first, last = main["items"][0], main["items"][-1]
        first["fades"]["in_us"] = min(model.us(edit.fade_in), first["duration_us"])
        last["fades"]["out_us"] = min(model.us(edit.fade_out), last["duration_us"] - last["fades"]["in_us"])
        own = model.track(doc, model.MAIN_AUDIO)
        own["volume"], own["mute"] = float(edit.volume), bool(edit.mute_all)
        for a, b in edit.mutes:
            span = _span(start_us, a, b)
            if span is not None:
                captions["mutes"].append({"in_us": span[0], "out_us": span[1], "mode": "mute"})
        if edit.hook:
            text = model.track(doc, "T1")
            item = model.new_item(model.next_id(doc, "i"), start_us=0, duration_us=model.us(edit.hook["seconds"]),
                                  text={"content": edit.hook["text"], "role": "hook"})
            item["source_out_us"] = item["duration_us"]
            text["items"].append(item)
        if edit.music:
            music = edit.music
            source = ({"type": "library", "id": music["library"]} if music.get("library")
                      else {"type": "file", "path": music["path"]})
            music_id = model.next_id(doc, "m")
            doc["media"][music_id] = {"id": music_id, "kind": "audio", "source": source, "duration_us": 0,
                                      "width": 0, "height": 0, "fps": None, "has_audio": True, "name": "",
                                      "extra": {}}
            item = model.new_item(model.next_id(doc, "i"), media_id=music_id, source_in_us=0, source_out_us=total,
                                  start_us=0, duration_us=total, volume=float(music["volume"]))
            item.update(role="music", loop=True, duck=bool(music.get("duck", True)))
            model.track(doc, "A2")["items"].append(item)
    if edit_raw is not None and isinstance(edit_raw.get("muted_words"), list):
        for word in edit_raw["muted_words"]:
            span = _span(start_us, word.get("start"), word.get("end")) if isinstance(word, dict) else None
            if span is not None:
                rest = {k: model.clone(v) for k, v in word.items() if k not in ("start", "end")}
                captions["muted_words"].append({**rest, "in_us": span[0], "out_us": span[1]})

    if isinstance(opts.get("captions"), bool):
        captions["enabled"] = opts["captions"]
    if isinstance(opts.get("caption_style"), dict):
        captions["style"] = model.clone(opts["caption_style"])
    if isinstance(opts.get("caption_lines"), list):
        lines = []
        for line in opts["caption_lines"]:
            span = _span(start_us, line.get("start"), line.get("end")) if isinstance(line, dict) else None
            if span is None or not isinstance(line.get("text"), str):
                continue
            rest = {k: model.clone(v) for k, v in line.items() if k not in ("start", "end", "id")}
            lines.append({**rest, "id": model.next_id(doc, "c"), "in_us": span[0], "out_us": span[1]})
        captions["lines"] = lines
    if isinstance(opts.get("speaker_edits"), (list, tuple)):
        for said in opts["speaker_edits"]:
            try:
                a, b, who = float(said[0]), float(said[1]), int(said[2])
            except (TypeError, ValueError, IndexError, KeyError):
                continue
            span = _span(start_us, a, b)
            if span is not None and who >= 0:
                captions["speaker_edits"].append({"in_us": span[0], "out_us": span[1], "speaker": 1 if who else 0})

    if "watermark" in opts:
        doc["branding"]["watermark"] = model.clone(opts["watermark"])
    doc["audio"]["normalize"] = bool(opts.get("normalize_audio", True))

    doc["context"] = {
        "origin": "clip",
        "video_id": str(clip.get("video_id") or ""),
        "media_id": video_id,
        "clip": {"start_us": start_us, "end_us": end_us},
        "job": {k: model.clone(opts[k]) for k in JOB_KEYS if k in opts},
        "imported": {"start": start, "end": end, "render_opts": model.clone(raw)},
    }
    legacy = {k: model.clone(v) for k, v in opts.items() if k not in MAPPED_KEYS and k not in JOB_KEYS}
    legacy_edit = {k: model.clone(v) for k, v in (edit_raw or {}).items() if k not in EDIT_KEYS}
    if legacy or legacy_edit:
        doc["extra"]["legacy"] = {"render_opts": legacy, "edit": legacy_edit}
    problems = model.validate(doc)
    if problems:
        raise ClipImportError(f"The clip can't be opened as a project: {problems[0]}.")
    return doc
