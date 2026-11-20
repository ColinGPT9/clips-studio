"""Making the editor's project for a clip, a whole video or a file.

A clip's project keeps the AI's framing by using a caption-free, framed
render of the clip's WHOLE window as its video (render_base): the cuts,
speed, sound, captions, hook, music and watermark are then put on the
editor's timeline as items of their own, so each can be changed or taken
back there. A whole video or a file from the PC is used in place, whole.

plan_clip is plain arithmetic (what goes where, in seconds); apply_clip_plan
and build_whole make the editor's tool calls (kitty_editor/automation.py).

Standard library only when imported; the app's own modules are imported
inside the functions that need them.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

from kitty_editor import automation

MIN_PIECE = 1 / 30          # a piece shorter than this is not worth a cut
CAPTION_OUTLINE = 7 / 1920  # today's black caption outline, as a share of the frame's height
CAPTION_ROWS = {"bottom": 0.35, "middle": 0.0, "top": -0.35}
HOOK_ROW = -0.35
MUSIC_COPIES = 200          # the most times a short music file is repeated

# What the editor has no equivalent for, said plainly (k2-editor.md).
NOTE_FRAMING = "framing_baked"
NOTE_CAPTIONS = "captions_plain"
NOTE_FONT = "font_default"
NOTE_DUCK = "music_duck"
NOTE_SOURCE = "source_missing"
NOTE_EXPORT = "from_export"
NOTE_WATERMARK = "watermark_still"
NOTE_MUSIC_GONE = "music_missing"

# Fonts every Windows has, for when the app's own list can't be loaded.
_WINDOWS_FONTS = ("Arial", "Arial Black", "Impact", "Verdana", "Tahoma", "Trebuchet MS", "Georgia",
                  "Times New Roman", "Courier New", "Comic Sans MS", "Segoe UI")


def probe(path) -> dict:
    """A media file's picture size, frame rate (as a fraction) and length.
    `video` is False for a file with no picture."""
    from core.binaries import ffprobe

    info = {"video": False, "width": 0, "height": 0, "fps_num": 30, "fps_den": 1, "seconds": 0.0}
    try:
        done = subprocess.run(
            [ffprobe(), "-v", "error", "-select_streams", "v:0",
             "-show_entries", "stream=width,height,r_frame_rate:stream_side_data=rotation:format=duration",
             "-of", "json", str(path)],
            capture_output=True, text=True, timeout=60, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        data = json.loads(done.stdout or "{}")
    except (OSError, ValueError, subprocess.SubprocessError):
        return info
    streams = data.get("streams") or []
    if streams:
        stream = streams[0]
        width, height = int(stream.get("width") or 0), int(stream.get("height") or 0)
        for side in stream.get("side_data_list") or ():
            try:
                if abs(int(side.get("rotation", 0))) % 180 == 90:
                    width, height = height, width       # shown turned on its side
            except (TypeError, ValueError):
                pass
        info.update(video=width > 0 and height > 0, width=width, height=height)
        num, _, den = str(stream.get("r_frame_rate") or "").partition("/")
        try:
            num, den = int(num), int(den or 1)
            if num > 0 and den > 0 and 1 <= num / den <= 240:
                info.update(fps_num=num, fps_den=den)
        except ValueError:
            pass
    try:
        info["seconds"] = max(0.0, float((data.get("format") or {}).get("duration") or 0))
    except (TypeError, ValueError):
        pass
    return info


# The base video is encoded again by the editor's own export, so when it is
# made from the clip's own framing (fastbase) it is encoded for speed on the
# CPU: x264's fastest preset, at a quality (CRF 16) that
# measured as good as the renders' veryfast CRF 20 (SSIM 0.990 against the
# unencoded picture, 0.989 for veryfast CRF 20) at twice the speed and about
# four times the size. A hardware encoder, where Clips Kitty's own FFmpeg
# has one, is used as it is.
BASE_CPU_ARGS = ["-c:v", "libx264", "-preset", "ultrafast", "-crf", "16"]
# Part of a base's key: a base made by an earlier version of this code is
# made again.
BASE_VERSION = 2
# Clip options that change only what is drawn over the base (captions, the
# watermark, an old title card): a change to them alone keeps the base.
_OVERLAY_OPTS = ("caption_style", "caption_lines", "captions", "job_captions", "speaker_edits",
                 "speaker_turns", "watermark", "headline", "subline", "post_style", "card_position")


def base_key(row, config: dict, data_dir: Path) -> str:
    """What a clip's base video is made from, as a short fingerprint: the
    clip's window, its framing options, its file and the original video
    (each by path, size and time), and the settings the framing reads. Two
    bases with the same key are the same video."""
    import hashlib

    from core.paths import cached_source

    opts = json.loads(row["render_opts"]) if row["render_opts"] else {}
    source = cached_source(Path(data_dir) / "downloads", row["video_id"])
    clips = config.get("clips") or {}
    tracking = config.get("tracking") or {}

    def stamp(path) -> list | None:
        try:
            st = Path(path).stat()
        except (OSError, TypeError, ValueError):
            return None
        return [int(st.st_size), int(st.st_mtime_ns)]

    what = {
        "version": BASE_VERSION, "video": row["video_id"],
        "window": [round(float(row["start_s"]), 3), round(float(row["end_s"]), 3)],
        "opts": {k: v for k, v in opts.items() if k not in _OVERLAY_OPTS},
        "file": [str(_field(row, "path") or ""), stamp(_field(row, "path")) if _field(row, "path") else None],
        "source": [str(source or ""), stamp(source) if source else None],
        "clips": {k: clips.get(k) for k in ("vertical", "filter", "whole", "podcast", "gaming", "sport",
                                            "vertical_live")},
        "tracking": {k: tracking.get(k) for k in ("detector", "sample_fps", "shot_aware",
                                                  "shot_aware_no_subject")},
    }
    return hashlib.sha256(json.dumps(what, sort_keys=True, default=str).encode()).hexdigest()[:32]


def render_base(row, config: dict, data_dir: Path, work_dir: Path, progress=None) -> Path | None:
    """The clip's whole window rendered with the AI's framing, layout,
    colour and sound levelling, and nothing else: no captions, no cuts or
    other edits, no watermark, no end card. None when the original video is
    gone. As multilingual/burn.py clean_base renders its base, less the
    edits and the watermark that one keeps.

    For a clip framed by the tracker's moving crop, the crop is read back
    from the clip's own file (kitty_editor/fastbase.py: seconds); any other
    clip is rendered the full way, tracking included, as the pipeline
    renders it (most of a minute for a 25-second 60 fps clip).
    progress(0..1) as it goes: the fast way's frames, the full way's time
    against an estimate."""
    from core.paths import cached_source
    from video.encoding import cpu_encoder_args

    source = cached_source(Path(data_dir) / "downloads", row["video_id"])
    if source is None or not source.exists():
        return None
    opts = json.loads(row["render_opts"]) if row["render_opts"] else {}
    work_dir.mkdir(parents=True, exist_ok=True)
    from kitty_editor import fastbase

    clip_file = Path(_field(row, "path")) if _field(row, "path") else None
    try:
        with cpu_encoder_args(BASE_CPU_ARGS):
            fast = fastbase.make(row, opts, config, source, clip_file, work_dir / "fast", progress=progress)
    except Exception as e:
        print(f"      Editor base: reading the clip's framing failed ({type(e).__name__}: {e}); making it in full")
        fast = None
    if fast is not None:
        return fast
    # The full way encodes as the pipeline does: its cut is what the tracker
    # looks at, and a faster encode of it was measured to change the framing
    # the tracker chose (the face-safe layout became the plain crop).
    return _render_full(row, opts, config, data_dir, source, work_dir, progress)


def _render_full(row, opts: dict, config: dict, data_dir: Path, source: Path, work_dir: Path,
                 progress=None) -> Path:
    """render_base the full way: core.pipeline._render_files on the window,
    with the tracking models loaded while the window is cut."""
    import threading
    import time

    from core.models import ClipCandidate, Segment
    from core.pipeline import _render_files

    opts = {**opts, "captions": False, "watermark": None}
    for key in ("caption_lines", "edit"):
        opts.pop(key, None)
    transcript = Path(data_dir) / "transcripts" / f"{row['video_id']}.json"
    segments = []
    if transcript.exists():
        segments = [Segment(**s) for s in json.loads(transcript.read_text(encoding="utf-8"))["segments"]]
    candidate = ClipCandidate(start=row["start_s"], end=row["end_s"], score=row["score"] or 0,
                              hook=row["hook"] or "")
    # The end card is never part of an editor project (nor of its exports).
    config = {**config, "clips": {**config["clips"], "outro": False}}
    if _tracks(opts, config):
        threading.Thread(target=_load_models, args=(config,), name="kitty-editor-models", daemon=True).start()
    done = threading.Event()
    if progress is not None:
        estimate = full_estimate(float(row["end_s"]) - float(row["start_s"]), probe(source))

        def tick() -> None:
            began = time.monotonic()
            while not done.wait(0.5):
                progress(time_share(time.monotonic() - began, estimate))

        threading.Thread(target=tick, name="kitty-editor-progress", daemon=True).start()
    try:
        rendered, _ = _render_files(source, candidate, segments, work_dir, config, opts)
    finally:
        done.set()
    return rendered


def _field(row, name: str):
    """A clip row's field (a database row or a plain dict), None when it has none."""
    try:
        return row[name] if name in row.keys() else None
    except (AttributeError, TypeError):
        return None


def _tracks(opts: dict, config: dict) -> bool:
    """Whether the full render will run the tracker (a vertical clip that
    isn't centred, Vertical Live or Longform)."""
    from core import modes

    clips = config.get("clips") or {}
    return bool(clips.get("vertical", True) and not opts.get("profile") and opts.get("crop", "track") != "center"
                and not (modes.is_vertical_live(opts) or modes.is_vertical_live(config)))


def _load_models(config: dict) -> None:
    """The tracker's and the speaker detector's models, loaded while the
    window is being cut rather than after it (each is kept for the engine's
    life, as a clipping run keeps them). Anything that goes wrong here is
    left for the render to meet as it always has."""
    try:
        from video import asd, tracker

        tracker._get_model((config.get("tracking") or {}).get("detector") or "yolov8n-pose.pt")
        if asd.available():
            asd._load()
    except Exception:
        pass


def full_estimate(seconds: float, info: dict) -> float:
    """About how long the full render of a window this long takes, in
    seconds: on the PC it was measured on, 59 s for 24.8 s of 60 fps video
    with the models loading (PERF-K2-first-open.md); the tracking part does
    not grow with the frame rate, the cut and the encodes do."""
    rate = float(info.get("fps_num") or 30) / max(1, int(info.get("fps_den") or 1))
    return 8.0 + max(1.0, seconds) * (0.8 + 1.2 * max(24.0, min(120.0, rate)) / 60)


def time_share(elapsed: float, estimate: float) -> float:
    """How far along a bar for work that can't say how far it is: in step
    with the time against the estimate up to 90%, then ever more slowly,
    never 100%."""
    import math

    share = max(0.0, elapsed) / max(1.0, estimate)
    if share < 1.0:
        return 0.9 * share
    return 0.9 + 0.09 * (1 - math.exp(-(share - 1.0)))


def _windows_fonts() -> tuple[str, ...]:
    try:
        from video.captions import WINDOWS_FONTS

        return tuple(WINDOWS_FONTS)
    except Exception:
        return _WINDOWS_FONTS


def _default_style() -> dict:
    try:
        from video.captions import DEFAULT_STYLE

        return dict(DEFAULT_STYLE)
    except Exception:
        return {"font": "Arial", "font_size": 84, "color": "#FFFFFF", "position": "bottom", "uppercase": True,
                "highlight": False, "second_speaker": False, "second_speaker_color": "#5CE1FF",
                "animation": "none", "safe_zone": "off"}


def _safe_zones() -> dict:
    try:
        from video.captions import SAFE_ZONES

        return dict(SAFE_ZONES)
    except Exception:
        return {}


def _hex(value, fallback: str) -> str:
    text = str(value or "").strip()
    if len(text) == 7 and text[0] == "#" and all(c in "0123456789abcdefABCDEF" for c in text[1:]):
        return text.lower()
    return fallback


def caption_look(style: dict | None, canvas: tuple[int, int]) -> tuple[dict, float, list[str]]:
    """A clip's caption style as the editor's text style, the row it sits on
    (a share of the frame's height from the centre, down is positive), and
    the notes for what is not carried."""
    full = {**_default_style(), **(style or {})}
    notes: list[str] = []
    try:
        size = max(12.0, min(400.0, float(full.get("font_size") or 84)))
    except (TypeError, ValueError):
        size = 84.0
    look = {
        "color": _hex(full.get("color"), "#ffffff"),
        "fontSize": round(size / 1920, 5),
        "strokeWidth": round(CAPTION_OUTLINE, 5),
        "strokeColor": "#000000",
        "textCase": "upper" if full.get("uppercase") else "original",
    }
    font = str(full.get("font") or "")
    if font and font in _windows_fonts():
        look["fontFamily"] = font
    elif font:
        notes.append(NOTE_FONT)
    if full.get("highlight") or str(full.get("animation") or "none") != "none":
        notes.append(NOTE_CAPTIONS)

    position = full.get("position") if full.get("position") in CAPTION_ROWS else "bottom"
    row = CAPTION_ROWS[position]
    zone = _safe_zones().get(full.get("safe_zone")) if canvas[1] > canvas[0] else None
    if zone and position != "middle":
        # Clear of the app's own buttons and text, as the burned caption is:
        # the zone's edge, a little air, and half a line of text.
        edge = (zone["bottom"] if position == "bottom" else zone["top"]) + 20 + size
        inside = 0.5 - edge / 1920
        row = min(row, inside) if position == "bottom" else max(row, -inside)
    return look, round(row, 4), notes


def program_lines(lines, edit, length: float) -> list[dict]:
    """Caption lines counted from the clip's start, as they fall in the
    program: through the kept ranges and the speed (a line cut away is
    dropped, a line across a cut is held to what is kept), held to the
    program's length. Each keeps its text and whether the other speaker
    says it."""
    from video_editor.captions import remap_lines

    usable = [line for line in lines or () if isinstance(line, dict) and str(line.get("text") or "").strip()]
    if edit is not None and (edit.keep is not None or abs(edit.speed - 1) >= 0.01):
        usable = remap_lines(usable, edit)
    out = []
    for line in usable:
        try:
            start, end = max(0.0, float(line["start"])), min(float(length), float(line["end"]))
        except (KeyError, TypeError, ValueError):
            continue
        if end - start > 0.05:
            out.append({"start": round(start, 3), "end": round(end, 3), "text": str(line["text"]).strip(),
                        "speaker": bool(line.get("speaker"))})
    out.sort(key=lambda line: line["start"])
    return out


def plan_clip(*, opts: dict, window: float, base_seconds: float, lines, canvas: tuple[int, int],
              default_style: dict | None = None, captions: bool = True, music_file=None,
              watermark: dict | None = None, watermark_image=None) -> dict:
    """What a clip's project holds, worked out from the clip's own settings.

    `opts` are the clip's render options, `window` its length (end - start),
    `base_seconds` the base video's length (the window plus the render's end
    pad), `lines` its caption lines counted from the clip's start.

    Gives {"pieces": [{"in", "out", "muted"}] (seconds of the base, in
    order), "speed", "volume", "fade_in", "fade_out", "length" (the
    program's), "captions": [{"style", "offsetY", "lines"}], "hook",
    "music", "watermark_text", "watermark_image", "notes"}."""
    from video_editor.timeline import EditList

    edit = EditList.from_dict(opts.get("edit"), duration=window) if isinstance(opts.get("edit"), dict) else None
    base_seconds = max(float(base_seconds), MIN_PIECE)
    keep = [(max(0.0, a), min(base_seconds, b)) for a, b in edit.keep] if edit is not None and edit.keep else None
    keep = [r for r in keep if r[1] - r[0] >= MIN_PIECE] if keep else None
    if not keep:
        keep = [(0.0, base_seconds)]            # the whole window, with the render's end pad
    mutes = list(edit.mutes) if edit is not None else []
    mute_all = bool(edit is not None and edit.mute_all)

    pieces: list[dict] = []
    for a, b in keep:
        cuts = sorted({t for m in mutes for t in m if a + MIN_PIECE < t < b - MIN_PIECE})
        edges = [a, *cuts, b]
        for left, right in zip(edges, edges[1:]):
            middle = (left + right) / 2
            muted = mute_all or any(m[0] <= middle <= m[1] for m in mutes)
            pieces.append({"in": round(left, 4), "out": round(right, 4), "muted": muted})
    speed = edit.speed if edit is not None else 1.0
    length = sum(p["out"] - p["in"] for p in pieces) / speed

    notes = [NOTE_FRAMING]
    groups: list[dict] = []
    style = opts.get("caption_style") or default_style
    if captions:
        look, row, look_notes = caption_look(style, canvas)
        placed = program_lines(lines, edit, length)
        if placed:
            notes += look_notes
            second = bool({**_default_style(), **(style or {})}.get("second_speaker"))
            mine = [line for line in placed if not (second and line["speaker"])]
            other = [line for line in placed if second and line["speaker"]]
            for chosen, colour in ((mine, None), (other, (style or {}).get("second_speaker_color")
                                                  or _default_style().get("second_speaker_color"))):
                if chosen:
                    groups.append({
                        "style": {**look, **({"color": _hex(colour, "#5ce1ff")} if colour else {})},
                        "offsetY": row,
                        "lines": [{k: line[k] for k in ("start", "end", "text")} for line in chosen],
                    })

    hook = None
    if edit is not None and edit.hook:
        hook = {"text": edit.hook["text"], "seconds": round(min(float(edit.hook["seconds"]), length), 3)}

    music = None
    if edit is not None and edit.music:
        if music_file:
            music = {"file": str(music_file), "volume": float(edit.music.get("volume", 0.25))}
            if edit.music.get("duck", True):
                notes.append(NOTE_DUCK)
        else:
            notes.append(NOTE_MUSIC_GONE)

    mark_text, mark_image = None, None
    if isinstance(watermark, dict):
        kind = watermark.get("type")
        where = _watermark_place(watermark)
        if watermark.get("position") == "moving":
            notes.append(NOTE_WATERMARK)
        if kind in ("text", "both") and str(watermark.get("text") or "").strip():
            try:
                size = max(12.0, float(watermark.get("font_size") or 42))
                opacity = max(0.0, min(1.0, float(watermark.get("opacity", 0.85))))
            except (TypeError, ValueError):
                size, opacity = 42.0, 0.85
            mark_text = {"text": str(watermark["text"]).strip()[:120], "offsetX": where[0], "offsetY": where[1],
                         "style": {"fontSize": round(size / 1920, 5), "opacity": opacity,
                                   "color": _hex(watermark.get("color"), "#ffffff")}}
        if kind in ("image", "both") and watermark_image:
            try:
                scale = max(0.02, min(1.0, float(watermark.get("scale", 0.18))))
                opacity = max(0.0, min(1.0, float(watermark.get("opacity", 0.85))))
                padding = max(0.0, min(0.4, float(watermark.get("padding", 0.04))))
            except (TypeError, ValueError):
                scale, opacity, padding = 0.18, 0.85, 0.04
            mark_image = {"file": str(watermark_image), "scale": scale, "opacity": opacity, "padding": padding,
                          "corner": where[2]}

    seen: list[str] = []
    for note in notes:
        if note not in seen:
            seen.append(note)
    return {"pieces": pieces, "speed": speed, "volume": edit.volume if edit is not None else 1.0,
            "fade_in": edit.fade_in if edit is not None else 0.0,
            "fade_out": edit.fade_out if edit is not None else 0.0,
            "length": round(length, 4), "captions": groups, "hook": hook, "music": music,
            "watermark_text": mark_text, "watermark_image": mark_image, "notes": seen}


def _watermark_place(cfg: dict) -> tuple[float, float, tuple[float, float]]:
    """Where a watermark sits: its centre for a line of text (shares of the
    frame from the centre), and the corner it leans to, as signs."""
    position = cfg.get("position") or "bottom_right"
    if position == "custom":
        try:
            x = max(0.0, min(1.0, float(cfg.get("x", 0.5)))) - 0.5
            y = max(0.0, min(1.0, float(cfg.get("y", 0.5)))) - 0.5
        except (TypeError, ValueError):
            x, y = 0.0, 0.0
        return round(x, 4), round(y, 4), (0.0, 0.0)
    if position == "center":
        return 0.0, 0.0, (0.0, 0.0)
    if position == "moving":
        return 0.3, 0.0, (1.0, 0.0)             # a still one at the right edge's middle
    sx = -1.0 if "left" in position else 1.0
    sy = -1.0 if "top" in position else 1.0
    return round(0.3 * sx, 4), round(0.44 * sy, 4), (sx, sy)


def _timeline(result: dict) -> dict:
    timeline = result.get("timeline")
    if not isinstance(timeline, dict):
        raise automation.EditorError("The editor did not say what is on the timeline.")
    return timeline


def _main_lane(result: dict) -> str:
    tracks = _timeline(result).get("tracks") or []
    if not tracks:
        raise automation.EditorError("The editor's project has no lane to put the video on.")
    return tracks[-1]["id"]


def _on_lane(result: dict, lane: str) -> list[dict]:
    return sorted((c for c in _timeline(result).get("clips") or () if c.get("track") == lane),
                  key=lambda c: c.get("start", 0.0))


def _create(session, home: Path, name: str, canvas: dict) -> tuple[str, dict]:
    """A new, empty editor project in <home>/p/<name>; its folder and the
    editor's first answer."""
    location = home / "p"
    if location.exists():
        shutil.rmtree(location)                 # what a failed build left
    location.mkdir(parents=True)
    result = session.tool("project_create", location=str(location), name=name,
                          video={"width": int(canvas["width"]), "height": int(canvas["height"]),
                                 "rateNum": int(canvas["fps_num"]), "rateDen": int(canvas["fps_den"])})
    return str(location / name), result


def apply_clip_plan(session, home: Path, name: str, canvas: dict, base: Path, plan: dict) -> str:
    """The editor's project for a clip, made from `plan` (plan_clip) with
    the editor's tools; saved and closed. Returns the project's folder."""
    path, created = _create(session, home, name, canvas)
    lane = _main_lane(created)
    media = session.tool("media_import", path=path, file=str(base))["createdId"]
    result = session.tool("clip_add", path=path, mediaId=media, trackId=lane, start=0.0)
    whole = _on_lane(result, lane)
    total = float(whole[0]["duration"]) if whole else 0.0

    # The pieces: the whole base is cut at every edge, what is not kept is
    # taken out (the lane closes up), so a cut is taken back in the editor
    # by dragging a piece's edge.
    pieces = [p for p in plan["pieces"] if p["in"] < total - MIN_PIECE]
    edges = sorted({round(t, 4) for p in pieces for t in (p["in"], p["out"]) if MIN_PIECE < t < total - MIN_PIECE})
    for t in edges:
        result = session.tool("clips_split", path=path, clipIds=[c["id"] for c in _on_lane(result, lane)], time=t)
    kept, dropped = [], []
    for clip in _on_lane(result, lane):
        middle = float(clip.get("sourceStart", clip["start"])) + float(clip["duration"]) / 2
        piece = next((p for p in pieces if p["in"] <= middle <= p["out"]), None)
        if piece is None:
            dropped.append(clip["id"])
        else:
            kept.append((clip["id"], piece))
    if not kept:
        raise automation.EditorError("Nothing of the clip was left to put on the timeline.")
    if dropped:
        result = session.tool("clips_remove", path=path, clipIds=dropped, ripple=True)

    for clip_id, piece in kept:
        session.tool("clip_frame", path=path, clipId=clip_id, framing="fill")
        patch = {}
        if abs(plan["volume"] - 1.0) >= 0.01:
            patch["volume"] = plan["volume"]
        if piece["muted"]:
            patch["muted"] = True
        if patch:
            session.tool("clip_update", path=path, clipId=clip_id, patch=patch)
    if abs(plan["speed"] - 1.0) >= 0.01:
        for clip_id, _ in kept:
            result = session.tool("clip_speed", path=path, clipId=clip_id, speed=plan["speed"], ripple=True)
    if plan["fade_in"] > 0:
        session.tool("clip_update", path=path, clipId=kept[0][0], patch={"fadeIn": plan["fade_in"]})
    if plan["fade_out"] > 0:
        session.tool("clip_update", path=path, clipId=kept[-1][0], patch={"fadeOut": plan["fade_out"]})
    result = session.tool("timeline_read", path=path)
    length = max((float(c["end"]) for c in _on_lane(result, lane)), default=plan["length"])

    for group in plan["captions"]:
        lines = [{**line, "end": min(line["end"], length)} for line in group["lines"] if line["start"] < length - 0.05]
        if lines:
            session.tool("captions_add", path=path, captions=lines, offsetY=group["offsetY"], style=group["style"])
    if plan["hook"]:
        session.tool("title_add", path=path, start=0.0, duration=plan["hook"]["seconds"], offsetY=HOOK_ROW,
                     above=True, style={"content": plan["hook"]["text"], "fontSize": 0.05,
                                        "strokeWidth": round(CAPTION_OUTLINE, 5), "strokeColor": "#000000"})
    if plan["music"]:
        _add_music(session, path, plan["music"], length)
    if plan["watermark_text"]:
        mark = plan["watermark_text"]
        made = session.tool("title_add", path=path, start=0.0, duration=length, offsetY=mark["offsetY"],
                            above=True, style={"content": mark["text"], **mark["style"]})
        if abs(mark["offsetX"]) > 0.001 and made.get("createdId"):
            session.tool("clip_transform", path=path, clipId=made["createdId"], offsetX=mark["offsetX"])
    if plan["watermark_image"]:
        _add_logo(session, path, plan["watermark_image"], length, canvas)

    session.tool("project_save", path=path)
    session.tool("project_close", path=path, save=True)
    return path


def _add_music(session, path: str, music: dict, length: float) -> None:
    """The music on a lane of its own under every other, from the start to
    the program's end (a shorter file is repeated), at the volume set."""
    media = session.tool("media_import", path=path, file=music["file"])["createdId"]
    lane = session.tool("track_add", path=path, bottom=True)["createdId"]
    at = 0.0
    for _ in range(MUSIC_COPIES):
        result = session.tool("clip_add", path=path, mediaId=media, trackId=lane, start=at)
        clip_id = result["createdId"]
        clip = next(c for c in _timeline(result)["clips"] if c["id"] == clip_id)
        end = float(clip["end"])
        session.tool("clip_update", path=path, clipId=clip_id, patch={"volume": music["volume"]})
        if end >= length - 0.01 or float(clip["duration"]) < 0.1:
            if end > length + 0.01:
                session.tool("clip_trim", path=path, clipId=clip_id, edge="end", delta=-(end - length))
            return
        at = end


def _add_logo(session, path: str, logo: dict, length: float, canvas: dict) -> None:
    """The watermark picture on a lane above everything, for the whole
    length, at its size, corner and opacity."""
    result = session.tool("media_import", path=path, file=logo["file"])
    media = result["createdId"]
    item = next((m for m in result.get("media") or () if m.get("id") == media), {})
    lane = session.tool("track_add", path=path)["createdId"]
    result = session.tool("clip_add", path=path, mediaId=media, trackId=lane, start=0.0)
    clip_id = result["createdId"]
    clip = next(c for c in _timeline(result)["clips"] if c["id"] == clip_id)
    if abs(float(clip["end"]) - length) > 0.01:
        session.tool("clip_trim", path=path, clipId=clip_id, edge="end", delta=length - float(clip["end"]))
    width, height = float(canvas["width"]), float(canvas["height"])
    try:
        shape = float(item["width"]) / float(item["height"])
    except (KeyError, TypeError, ValueError, ZeroDivisionError):
        shape = 1.0
    # At scale 1 the picture fits the frame: its width is then this share of the frame's.
    fitted = min(1.0, shape / (width / height))
    wide = logo["scale"]                         # the logo's width, as a share of the frame's
    tall = wide * width / shape / height         # and its height, as a share of the frame's
    pad_x = logo["padding"] * min(width, height) / width
    pad_y = logo["padding"] * min(width, height) / height
    sx, sy = logo["corner"]
    session.tool("clip_transform", path=path, clipId=clip_id, scale=round(max(0.05, wide / fitted), 4),
                 offsetX=round(sx * (0.5 - pad_x - wide / 2), 4), offsetY=round(sy * (0.5 - pad_y - tall / 2), 4))
    session.tool("clip_update", path=path, clipId=clip_id, patch={"opacity": logo["opacity"]})


def build_whole(session, home: Path, name: str, canvas: dict, source: Path) -> str:
    """The editor's project for a whole video or a file: the source, whole,
    shown whole in the frame (bars where the shapes differ); saved and
    closed. Returns the project's folder."""
    path, created = _create(session, home, name, canvas)
    lane = _main_lane(created)
    media = session.tool("media_import", path=path, file=str(source))["createdId"]
    clip_id = session.tool("clip_add", path=path, mediaId=media, trackId=lane, start=0.0)["createdId"]
    session.tool("clip_frame", path=path, clipId=clip_id, framing="fit")
    session.tool("project_save", path=path)
    session.tool("project_close", path=path, save=True)
    return path


def open_session(exe: str, home: Path, media_folders) -> automation.Session:
    """A headless editor allowed to write in the project's folder and to
    read the folders its media is in."""
    roots = [home, *{str(Path(f)) for f in media_folders}]
    return automation.Session(exe, roots, home / "build.log")
