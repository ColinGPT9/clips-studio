"""The export compiler (editor suite step 7): a project document in, a
finished video out.

    plan = compile(document, revision, preset, "export", inputs=Inputs(...))
    result = run_plan(plan, task, config=config)

Two halves, kept apart on purpose:

- **compile()** is pure. It reads the document and the facts the caller
  resolved (each media's file and size: `Inputs`) and returns a RenderPlan,
  plain data: an ordered list of stages, each with its FFmpeg command line,
  the files it reads and writes, and the filter-script text it needs. It runs
  no FFmpeg, no ffprobe and no tracking, so a plan can be snapshot-tested
  (tests/test_compose_plan.py) and its command lengths checked without a
  video on the PC.
- **run_plan()** executes a plan on the editor lane (a TaskContext of
  server/editor_lane.py, or LocalTask in tests and scripts): each FFmpeg at
  below-normal priority with progress and cancel, every output written under
  a temporary name and renamed when complete.

The plan for an export (Branch B; Branch A, a project that still projects to
today's render options, is today's re-render: server/compose_api.py):

1. **V1, in chunks.** Consecutive V1 items are cut together, each from its
   own input (`-ss` before `-i`, so every item seeks on its own), at their
   speed (setpts, chained atempo) and framing, to exactly the frames and
   audio samples the timeline gives them:
   - a *manual* chunk (fit with a blurred or coloured pad, fill, align,
     transform) comes out at the canvas size, at mezzanine quality
     (`cut-NNN`);
   - an *AI* run (items imported from an AI clip, framing `{crop}` or
     `{gaming}`, kept verbatim) is cut at the source's size (`cut-NNN`), framed by
     core.framing.frame_intermediate exactly as a plain run frames a clip,
     its face tracking kept in the media cache (`frame-NNN`), then brought
     to the canvas (`conform-NNN`).
   Each chunk also writes its A1 sound (the V1 items' own) as a WAV with
   an exact number of samples.
2. **join**: the chunks' pictures joined by a stream copy (the concat
   demuxer with video/encoding.concat's flags; every chunk is encoded with
   the same parameters). The plan's command is the one that runs.
3. **layer-NN** (only when there are too many overlays for one command):
   overlays drawn onto the joined picture ahead of the final encode.
4. **card**: the end card (video/outro.py) for the export's size.
5. **sounds-NN**: the sounds on A2 and up (music looped from its
   loop_offset_us, sound effects and voices placed with adelay, at their
   volume, speed and fades), premixed into one WAV per bus (plain, ducked,
   voice), at most MAX_SOUND_INPUTS sounds to a command.
6. **final**: one FFmpeg: the joined picture, the overlays on V2 and up
   (overlay with enable='between(t,a,b)', transform and opacity), the
   captions, text, hook and text watermark burned from one ASS file
   (build_ass, remapped through the timeline), the image logo
   (watermark.logo_graph), the sound (A1 with the buses, the ducked ones
   under A1 and the voices by sidechaincompress, amix normalize=0, the
   master volume and loudnorm), the export preset of step 1 (size, aspect
   fill, quality, codec, fps, audio bitrate, size target), and the end card
   joined as a concat filter input.

V1's own sound carries each item's fades, the mutes and bleeps in the
captions' media time, and the tail pad (`tails`: the end of a run's last
word, heard under the next item's start or the end card).

purpose "draft" is the same plan at 540 on the short side, ultrafast, no
preset and no card; purpose "still" is one JPEG of the program at a time
(still_plan). docs/editor-suite/api/step7-compiler.md describes the plan's
shape and every stage.

Standard library only at import time; core.framing, video.encoding,
video.export_presets and video.outro are imported inside the functions
that need them. core/pipeline.py and core/framing.py never import this
module (tests/test_editor_import_guard.py).
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import math
import os
import re
import shutil
import subprocess
import threading
from dataclasses import dataclass, field
from pathlib import Path

from video_editor.project import model

PURPOSES = ("export", "draft", "still")
AUDIO_RATE = 48000                 # every sound in a plan: 48 kHz stereo
# Windows' limit for one command line (CreateProcess), in characters, and the
# budget a plan holds each command to, leaving room for the path FFmpeg is at.
CMDLINE_LIMIT = 32767
ARGV_BUDGET = 30000
MAX_ITEM_INPUTS = 16               # V1 items one cut command reads (each is a decoder)
MAX_OVERLAY_INPUTS = 16            # overlays one command draws
MAX_AI_RUN = 64                    # V1 items one AI run tracks as one stretch
READ_MARGIN_US = 200_000           # read this far past an item's end, so trim (not EOF) ends it
# A video item's picture is read from this far before its in point (fix
# 7-D9): the source frame on screen at the in point usually starts before
# it, and -ss drops it. The picture keeps its own times
# (setpts=(PTS-pre/TB)/speed, not PTS-STARTPTS) and fps=...:start_time
# picks, for each canvas frame, the source frame on screen then. Covers
# sources of 4 fps and up; the sound read with it drops the same stretch.
PRE_ROLL_US = 250_000
CROP_MODES = ("track", "center", "letterbox", "bias_left", "bias_right")

# The mezzanine: what V1's chunks are cut to and joined from. x264 at CRF 12
# (visually lossless at 1080x1920; about 30-60 Mbit/s on real footage), no
# B-frames (pts = dts, so the stream-copy join has nothing to reorder), a
# keyframe every two seconds, and the container timescale chosen from the
# canvas's frame rate so every chunk has the same one. The final encode is
# the only generation a viewer can see.
MEZZ_CRF = 12
MEZZ_PRESET = "veryfast"

# The draft (purpose "draft"): a quick look at the whole program, not the
# export. 540 pixels on the canvas's short side, x264 ultrafast, its chunks
# at CRF 18 (still well above what the draft's own CRF 28 can show).
DRAFT_SHORT_SIDE = 540
DRAFT_MEZZ = ("ultrafast", 18)
DRAFT_ARGS = ["-c:v", "libx264", "-preset", "ultrafast", "-crf", "28"]

# The tail pad (plan problem 4): core.pipeline.END_PAD. Whisper's word ends
# run early, so a cut right at a word's end clips the word. Where a run of
# V1 ends (a cut, or the program's end), the sound of the run's last word
# goes on for up to this long past the cut, never into the next word.
END_PAD_US = 400_000
TAIL_MIN_US = 20_000               # a tail shorter than this is not kept

# A bleep (set_audio_mutes mode "bleep"): the stretch's own sound is muted
# and a 1 kHz sine plays in its place, at FFmpeg's sine source's own level
# (1/8 of full scale, -18 dBFS) times A1's volume.
BLEEP_HZ = 1000

# Sounds on A2 and up are mixed in stages of their own ("sounds-NN"), at
# most this many to one command, into one WAV per bus (_bus).
MAX_SOUND_INPUTS = 16
BUSES = ("plain", "duck_own", "duck_free", "voice")
ASS_NAME = "export.ass"            # the plan's one ASS file, burned from its folder
JPEG_QUALITY = 2                   # a still's -q:v (2 is mjpeg's best that is not wasteful)

# How much of the work each kind of stage is, per second of video, for the
# progress bar.
WEIGHTS = {"cut": 1.0, "frame": 4.0, "conform": 0.6, "join": 0.05, "layer": 1.0, "card": 0.05, "final": 2.0,
           "sound": 0.1, "still": 0.05}


class CompileError(ValueError):
    """The document can't be made into a plan; the message says why."""


# ---- what the caller resolves ---------------------------------------------------------------


@dataclass(frozen=True)
class MediaFile:
    """A media entry of the document, resolved to its original file
    (video_editor.media.resolver: never a proxy, decision ED7)."""

    path: str
    kind: str = "video"            # video | audio | image
    width: int = 0
    height: int = 0
    has_audio: bool = True
    cache: str | None = None       # its data/media_cache/<fingerprint>/ folder (tracking), when known


@dataclass
class Inputs:
    """What compile() is given besides the document: no lookups happen
    inside it."""

    work: Path                                   # the plan's own folder (made by the runner)
    media: dict[str, MediaFile]                  # media id -> its file
    ffmpeg: str = "ffmpeg"                       # the FFmpeg the plan's commands start
    version: tuple[int, int] | None = None       # its version (ffmpeg_version()), for the filter-script flag
    encoder: object | None = None                # a preset's video.encoding.Encoder (export_presets.pick_encoder)
    render_args: list[str] | None = None         # Original: the renders' -c:v block (encoding.video_encoder_args)
    card_seconds: float | None = None            # the end card's length (outro.DURATION), None: no card
    project_id: str = ""
    # Part C: what the captions, the branding and the still need.
    segments: list | None = None                 # the captions media's transcript: [{start, end, text, words}], its seconds
    language: str = "en"                         # the transcript's language (the caption font for its letters)
    caption_style: dict | None = None            # the settings' caption style, for a project that has none
    job_captions: bool = True                    # the settings' captions switch (clips.captions)
    watermark: dict | None = None                # the settings' watermark, for a project whose branding has none
    logo: str | None = None                      # the image watermark's file (found by safe name by the caller)
    tracking: dict | None = None                 # a still: {"tracking", "run_start_us"} cached for the AI run at its time
    # Step 9: the export's "Burn captions into the video" (ExportIn.captions
    # "none" is False). Text, the hook and the watermark burn either way.
    burn_captions: bool = True


# ---- the plan --------------------------------------------------------------------------------


@dataclass
class Stage:
    """One step of a plan.

    kind:
    - "ffmpeg": run `commands` in order (two for a 2-pass encode);
    - "join": its one command, the stream-copy join of the listing in
      `scripts` (encoding.concat()'s own first try: -copyts, -c copy,
      final_movflags), run on the task like an "ffmpeg" stage (fix 7-D7:
      the plan shows what runs);
    - "frame": core.framing.frame_intermediate with `params`;
    - "card": video.outro.ensure_outro with `params`, copied to `writes[0]`.

    `writes` are the files a stage leaves; its commands write `temps` (each
    final name's temporary one) and the runner renames them when the stage
    is done. `scripts` are written before it runs (filter graphs, concat
    listings, and in Part C the ASS file)."""

    id: str
    kind: str
    commands: list[list[str]] = field(default_factory=list)
    reads: list[str] = field(default_factory=list)
    writes: list[str] = field(default_factory=list)
    temps: dict[str, str] = field(default_factory=dict)
    scripts: dict[str, str] = field(default_factory=dict)
    seconds: float = 0.0           # seconds of video it makes (progress, and FFmpeg's -progress)
    weight: float = 0.0
    params: dict = field(default_factory=dict)
    fallback: list[list[str]] | None = None   # on the CPU, when a graphics card refuses the encode
    retry: dict | None = None                 # the size target's one retry ({"commands", "video_kbps"})
    cwd: str | None = None

    @property
    def argv(self) -> list[str]:
        return self.commands[-1] if self.commands else []

    def to_dict(self) -> dict:
        out = {"id": self.id, "kind": self.kind, "commands": self.commands, "reads": self.reads,
               "writes": self.writes, "temps": self.temps, "scripts": self.scripts,
               "seconds": round(self.seconds, 6), "weight": round(self.weight, 6), "params": self.params}
        for key in ("fallback", "retry", "cwd"):
            if getattr(self, key) is not None:
                out[key] = getattr(self, key)
        return out


@dataclass
class RenderPlan:
    """A compiled project: stages in the order they run, and what comes out."""

    project_id: str
    revision: int
    purpose: str
    preset: dict                   # export_presets.normalize()'d
    canvas: dict                   # {width, height, fps: {num, den}}
    output: dict                   # {width, height, fps, codec, encoder, audio_kbps, max_bytes}
    program_us: int                # V1's length
    card_us: int                   # the end card's (expected) length, 0 without one
    work: str
    result: str                    # the finished file, inside `work`
    stages: list[Stage]
    video_post: list[str] = field(default_factory=list)   # filters at canvas size before the preset (Part C: subtitles, logo)
    notes: list[str] = field(default_factory=list)        # what this plan leaves out, in plain words

    def to_dict(self) -> dict:
        return {"project_id": self.project_id, "revision": self.revision, "purpose": self.purpose,
                "preset": self.preset, "canvas": self.canvas, "output": self.output,
                "program_us": self.program_us, "card_us": self.card_us, "work": self.work,
                "result": self.result, "video_post": self.video_post, "notes": self.notes,
                "stages": [s.to_dict() for s in self.stages]}

    def stage(self, stage_id: str) -> Stage:
        return next(s for s in self.stages if s.id == stage_id)


# ---- small helpers ---------------------------------------------------------------------------


def cmdline_length(argv: list[str]) -> int:
    """How long `argv` is as one Windows command line (CreateProcess quoting)."""
    return len(subprocess.list2cmdline([str(a) for a in argv]))


_versions: dict[str, tuple[int, int] | None] = {}
_versions_lock = threading.Lock()


def ffmpeg_version(binary: str) -> tuple[int, int] | None:
    """(major, minor) of an FFmpeg, read once from `-version`. A build from
    FFmpeg's own git ("N-12345-g...") counts as the newest. None when it
    can't be read."""
    with _versions_lock:
        if binary in _versions:
            return _versions[binary]
    try:
        first = subprocess.run([binary, "-hide_banner", "-version"], capture_output=True, text=True,
                               timeout=30).stdout.splitlines()[0]
    except (OSError, subprocess.SubprocessError, IndexError):
        first = ""
    found = parse_version(first)
    with _versions_lock:
        _versions[binary] = found
    return found


def parse_version(line: str) -> tuple[int, int] | None:
    m = re.search(r"version\s+n?(\d+)\.(\d+)", line or "")
    if m:
        return int(m.group(1)), int(m.group(2))
    if re.search(r"version\s+N-\d+", line or ""):
        return (99, 0)
    return None


def script_flag(version: tuple[int, int] | None) -> str:
    """The option that reads a filter graph from a file: -filter_complex_script
    before FFmpeg 7.1 (CI has 6.x), the -/filter_complex form from 7.1 on,
    where the old one is deprecated (the app bundles 8.x). An unknown
    version takes the old form, which every version up to 8 still reads."""
    return "-/filter_complex" if version is not None and version >= (7, 1) else "-filter_complex_script"


def _num(x: float) -> str:
    """A number for a filter argument: plain decimal, no exponent, no -0."""
    text = f"{x:.6f}".rstrip("0").rstrip(".")
    return "0" if text in ("-0", "") else text


def _secs(us: int) -> str:
    return _num(us / model.US)


def samples_at(t_us: int) -> int:
    """The audio sample nearest a time, at AUDIO_RATE."""
    return (t_us * AUDIO_RATE + model.US // 2) // model.US


def _fps_text(fps: dict) -> str:
    return f"{fps['num']}/{fps['den']}"


def _fps_float(fps: dict) -> float:
    return fps["num"] / fps["den"]


def _timescale(fps: dict) -> int:
    num = int(fps["num"])
    return num if num >= 1000 else num * 1000


def _even(x: float) -> int:
    return max(2, int(round(x / 2)) * 2)


def atempo_chain(speed: float) -> list[str]:
    """atempo filters whose product is `speed`, each within 0.5 to 2 (what
    every FFmpeg version takes)."""
    out, left = [], float(speed)
    while left > 2.0 + 1e-9:
        out.append("atempo=2")
        left /= 2.0
    while left < 0.5 - 1e-9:
        out.append("atempo=0.5")
        left /= 0.5
    if abs(left - 1.0) > 1e-9:
        out.append(f"atempo={_num(left)}")
    return out


def mezz_args(fps: dict, draft: bool = False) -> list[str]:
    """The mezzanine's -c:v block (see MEZZ_CRF; DRAFT_MEZZ for a draft)."""
    gop = max(1, int(round(2 * _fps_float(fps))))
    preset, crf = DRAFT_MEZZ if draft else (MEZZ_PRESET, MEZZ_CRF)
    return ["-c:v", "libx264", "-preset", preset, "-crf", str(crf), "-bf", "0", "-g", str(gop),
            "-pix_fmt", "yuv420p", "-video_track_timescale", str(_timescale(fps))]


def draft_size(width: int, height: int) -> tuple[int, int]:
    """A draft's picture: DRAFT_SHORT_SIDE on the canvas's short side, the
    canvas's shape kept (even sizes). A canvas already that small is kept."""
    short = min(width, height)
    if short <= DRAFT_SHORT_SIDE:
        return width, height
    f = DRAFT_SHORT_SIDE / short
    w = DRAFT_SHORT_SIDE if width == short else _even(width * f)
    h = DRAFT_SHORT_SIDE if height == short else _even(height * f)
    return w, h


def temp_of(path: Path) -> Path:
    """The temporary name a stage writes `path` under: same folder and
    extension, so FFmpeg picks the same muxer."""
    return path.with_name(f"{path.stem}.part{path.suffix}")


def _head(inputs: Inputs) -> list[str]:
    return [inputs.ffmpeg, "-hide_banner", "-y", "-v", "error", "-nostdin", "-progress", "pipe:1", "-nostats"]


# ---- what the document says ------------------------------------------------------------------


def _main_items(doc: dict) -> list[dict]:
    track = model.track(doc, model.MAIN_VIDEO)
    return list(track["items"]) if track else []


def is_ai_item(doc: dict, item: dict) -> bool:
    """Whether a V1 item is framed the way an AI clip is (the cropper,
    tracking, a Gaming layout): framing {crop} or {gaming}, as an imported
    clip keeps it verbatim, or no framing at all on the clip's own video in
    a project made from a clip (a clip saved without "crop" is tracked, its
    default). Anything else is framed by the document ({fit, align, pad})."""
    framing = item.get("framing") or {}
    if "crop" in framing or "gaming" in framing:
        return True
    if "fit" in framing or "align" in framing or "pad" in framing:
        return False
    context = doc.get("context") or {}
    return context.get("origin") == "clip" and item.get("media_id") == context.get("media_id")


def _nine_sixteen(canvas: dict) -> bool:
    return abs(canvas["width"] * 16 - canvas["height"] * 9) <= max(canvas["width"], canvas["height"]) // 100


def _color_chain(item: dict) -> str:
    """The item's colour as today's render applies it: a filters.is_valid
    look and the manual adjustments (video/filters.py combined_chain)."""
    color = item.get("color") or {}
    name = color.get("filter")
    adjust = color.get("adjust") if isinstance(color.get("adjust"), dict) else None
    if not name and not adjust:
        return ""
    from video.filters import combined_chain, is_valid

    return combined_chain(name if isinstance(name, str) and is_valid(name) else None, adjust)


TRANSFORM_DEFAULTS = {"x": 0.0, "y": 0.0, "scale": 1.0, "rotation": 0.0, "opacity": 1.0}


def _transform(item: dict) -> dict:
    given = item.get("transform") or {}
    return {k: float(given.get(k, v)) for k, v in TRANSFORM_DEFAULTS.items()}


def _plain_transform(t: dict) -> bool:
    return all(abs(t[k] - v) < 1e-9 for k, v in TRANSFORM_DEFAULTS.items())


# ---- picture graphs --------------------------------------------------------------------------


def _align_xy(align: str, outer: tuple[str, str], inner: tuple[str, str]) -> tuple[str, str]:
    """Where a picture of size `inner` goes inside `outer` (expressions)."""
    (ow, oh), (iw, ih) = outer, inner
    x = {"left": "0", "right": f"{ow}-{iw}"}.get(align, f"({ow}-{iw})/2")
    y = {"top": "0", "bottom": f"{oh}-{ih}"}.get(align, f"({oh}-{ih})/2")
    return x, y


def framing_graph(src: str, out: str, tag: str, framing: dict, canvas: tuple[int, int], *,
                  overlay: bool = False) -> list[str]:
    """Filter statements that frame the picture on label `src` into `out`
    on a canvas of `canvas` (width, height).

    - fit (the default): the whole picture, as large as fits. On V1 the rest
      of the canvas is the picture again, blurred and washed out the way an
      export's blur pad and the cropper's fit_blur are (pad "blur", the
      default), or a colour (pad "#RRGGBB"). An overlay keeps the rest clear.
    - fill: covers the canvas, the rest cut off.
    - align (center, top, bottom, left, right): where the picture sits, or
      which part of it a fill keeps."""
    w, h = canvas
    fit = framing.get("fit", "fit")
    align = framing.get("align", "center")
    pad = framing.get("pad", "blur")
    if fit == "fill":
        cx = {"left": "0", "right": "iw-ow"}.get(align, "(iw-ow)/2")
        cy = {"top": "0", "bottom": "ih-oh"}.get(align, "(ih-oh)/2")
        return [f"[{src}]scale={w}:{h}:force_original_aspect_ratio=increase:flags=lanczos,"
                f"crop={w}:{h}:{cx}:{cy},setsar=1[{out}]"]
    fitted = f"scale={w}:{h}:force_original_aspect_ratio=decrease:force_divisible_by=2:flags=lanczos,setsar=1"
    if overlay:
        return [f"[{src}]{fitted}[{out}]"]
    if pad != "blur":
        x, y = _align_xy(align, ("ow", "oh"), ("iw", "ih"))
        return [f"[{src}]{fitted},pad={w}:{h}:{x}:{y}:color=0x{pad.lstrip('#')}[{out}]"]
    x, y = _align_xy(align, ("W", "H"), ("w", "h"))
    wash = (f"scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h},"
            f"scale={_even(w / 8)}:{_even(h / 8)},gblur=sigma=12,scale={w}:{h}:flags=bilinear,setsar=1")
    return [f"[{src}]split=2[{tag}bg][{tag}fg]",
            f"[{tag}bg]{wash}[{tag}wash]",
            f"[{tag}fg]{fitted}[{tag}pic]",
            f"[{tag}wash][{tag}pic]overlay={x}:{y}[{out}]"]


def transform_filters(t: dict) -> list[str]:
    """scale, rotate and opacity of a transform, on an RGBA picture."""
    out = ["format=rgba"]
    if abs(t["scale"] - 1.0) > 1e-9:
        s = _num(t["scale"])
        out.append(f"scale=trunc(iw*{s}/2)*2:trunc(ih*{s}/2)*2")
    if abs(t["rotation"]) > 1e-9:
        a = _num(t["rotation"] * 3.141592653589793 / 180.0)
        out.append(f"rotate={a}:ow=rotw({a}):oh=roth({a}):c=none")
    if t["opacity"] < 1.0 - 1e-9:
        out.append(f"colorchannelmixer=aa={_num(t['opacity'])}")
    return out


def _position(t: dict, canvas: tuple[int, int], align: str = "center") -> tuple[str, str]:
    """overlay's x and y: aligned in the canvas, then moved by the
    transform's x and y (fractions of the canvas's width and height)."""
    w, h = canvas
    x, y = _align_xy(align, ("W", "H"), ("w", "h"))
    dx, dy = t["x"] * w, t["y"] * h
    if abs(dx) > 1e-9:
        x = f"{x}+{_num(dx)}" if dx > 0 else f"{x}-{_num(-dx)}"
    if abs(dy) > 1e-9:
        y = f"{y}+{_num(dy)}" if dy > 0 else f"{y}-{_num(-dy)}"
    return x, y


# ---- V1 ----------------------------------------------------------------------------------------


@dataclass
class _Chunk:
    kind: str                      # manual | ai
    items: list[dict]


def _same_ai_run(doc: dict, a: dict, b: dict) -> bool:
    return (a["media_id"] == b["media_id"]
            and model.canonical(a.get("framing") or {}) == model.canonical(b.get("framing") or {})
            and model.canonical(a.get("color") or {}) == model.canonical(b.get("color") or {})
            and a["speed"] == b["speed"] and b["source_in_us"] >= a["source_out_us"])


def pre_roll(item: dict, media: MediaFile) -> int:
    """How far before its in point a video item is read (PRE_ROLL_US, less
    where the source has less before it); 0 for a picture."""
    if media.kind == "image":
        return 0
    return min(PRE_ROLL_US, int(item["source_in_us"]))


def _input_args(item: dict, media: MediaFile, fps: dict) -> list[str]:
    """One item's input: its own -ss, so each item seeks on its own, from
    pre_roll() before its in point."""
    if media.kind == "image":
        return ["-loop", "1", "-framerate", _fps_text(fps), "-t", _secs(item["duration_us"] + READ_MARGIN_US),
                "-i", media.path]
    span = item["source_out_us"] - item["source_in_us"]
    pre = pre_roll(item, media)
    return ["-ss", _secs(item["source_in_us"] - pre), "-t", _secs(span + pre + READ_MARGIN_US), "-i", media.path]


def _picture_head(j: int, item: dict, media: MediaFile, fps: dict, start_us: int = 0) -> str:
    """Input j's picture on the program's clock: each source frame keeps its
    own offset from the in point (scaled by the speed, plus `start_us`), and
    fps=<canvas>:start_time takes, for each canvas frame from `start_us`,
    the source frame on screen at that moment (the pre-roll supplies the
    one before the in point). Fix 7-D9: PTS-STARTPTS threw the offset away
    and ran the picture early against the sound."""
    shift = f"+{_secs(start_us)}/TB" if start_us else ""
    if media.kind == "image":
        return f"[{j}:v]setpts=PTS-STARTPTS{shift},fps={_fps_text(fps)}"
    pre = pre_roll(item, media)
    base = f"(PTS-{_secs(pre)}/TB)" if pre else "PTS"
    return (f"[{j}:v]setpts={base}/{_num(item['speed'])}{shift},"
            f"fps={_fps_text(fps)}:start_time={_secs(start_us)}")


def _frames(item: dict, fps: dict) -> int:
    start, end = item["start_us"], item["start_us"] + item["duration_us"]
    return model.frames_at(end, fps) - model.frames_at(start, fps)


def _samples(item: dict) -> int:
    start = item["start_us"]
    return samples_at(start + item["duration_us"]) - samples_at(start)


def _v1_picture(k: int, j: int, item: dict, media: MediaFile, fps: dict, canvas: tuple[int, int] | None,
                *, framing: dict | None) -> list[str]:
    """Item k (input j) to exactly its frames on the timeline. `canvas`
    None keeps the source's size (an AI run's intermediate)."""
    head = _picture_head(j, item, media, fps)
    out: list[str] = []
    if canvas is None:
        out.append(f"{head},scale=trunc(iw/2)*2:trunc(ih/2)*2,setsar=1[f{k}]")
    else:
        out.append(f"{head}[s{k}]")
        out += framing_graph(f"s{k}", f"f{k}", f"i{k}", framing or {}, canvas)
    color = _color_chain(item) if canvas is not None else ""
    t = _transform(item)
    chain = [color] if color else []
    if canvas is not None and not _plain_transform(t):
        x, y = _position(t, canvas)
        out.append(f"[f{k}]{','.join([*chain, *transform_filters(t)])}[t{k}]")
        out.append(f"color=c=black:s={canvas[0]}x{canvas[1]}:r={_fps_text(fps)}[k{k}]")
        out.append(f"[k{k}][t{k}]overlay=x={x}:y={y}:shortest=1[g{k}]")
        src, chain = f"g{k}", []
    else:
        src = f"f{k}"
    chain += ["format=yuv420p", "tpad=stop=-1:stop_mode=clone", f"trim=end_frame={_frames(item, fps)}",
              "setpts=PTS-STARTPTS"]
    out.append(f"[{src}]{','.join(chain)}[v{k}]")
    return out


def item_mutes(doc: dict, item: dict) -> list[tuple[float, float, str]]:
    """The mutes and bleeps (captions.mutes, in the source's time) that fall
    in a V1 item, as (from, to, mode) in seconds of the item's own sound
    after its speed. Only an item of the captions' media has them."""
    captions = doc.get("captions") or {}
    if not captions.get("mutes") or item.get("media_id") != captions.get("media_id"):
        return []
    out = []
    lo, hi, speed = item["source_in_us"], item["source_out_us"], float(item["speed"])
    for m in captions["mutes"]:
        a, b = max(int(m["in_us"]), lo), min(int(m["out_us"]), hi)
        if b > a:
            out.append(((a - lo) / speed / model.US, (b - lo) / speed / model.US, m.get("mode", "mute")))
    return out


def _between(spans) -> str:
    return "+".join(f"between(t,{_num(a)},{_num(b)})" for a, b in spans)


def _fade_filters(item: dict, n: int) -> list[str]:
    """The item's fades, sample-exact: in over its first fades.in_us, out
    over its last fades.out_us (of its time on the timeline)."""
    fades = item.get("fades") or {}
    out = []
    fade_in = min(n, samples_at(int(fades.get("in_us") or 0)))
    fade_out = min(n, samples_at(int(fades.get("out_us") or 0)))
    if fade_in > 0:
        out.append(f"afade=t=in:ss=0:ns={fade_in}")
    if fade_out > 0:
        out.append(f"afade=t=out:ss={n - fade_out}:ns={fade_out}")
    return out


ATEMPO_LEAD_S = 0.019     # one atempo stage of factor f starts a sound about this / f seconds early (measured)
LEAD_READ_S = 0.05        # read this much more source before a slowed item, to trim its lead from


def atempo_factors(speed: float) -> list[float]:
    out, left = [], float(speed)
    while left > 2.0 + 1e-9:
        out.append(2.0)
        left /= 2.0
    while left < 0.5 - 1e-9:
        out.append(0.5)
        left /= 0.5
    if abs(left - 1.0) > 1e-9:
        out.append(left)
    return out


def atempo_lead(speed: float) -> float:
    """How early (seconds of output) atempo's chain for `speed` starts a
    sound: each stage's own lead (about ATEMPO_LEAD_S / f, measured with the
    bundled FFmpeg 8.1: 38 ms at 0.5, 23 ms at 0.75, 11 ms at 1.5, 8 ms at
    2), the earlier stages' scaled by the later ones. 0 at speed 1."""
    total = 0.0
    for f in atempo_factors(speed):
        total = total / f + ATEMPO_LEAD_S / f
    return total


def audio_lead(item: dict, media: MediaFile) -> dict | None:
    """For an item slower than 1x: its sound read from LEAD_READ_S (plus the
    lead) earlier than the picture, and as much trimmed off after atempo, so
    the sound starts where the picture does (atempo's lead taken out: 38 ms
    at 0.5x, about 0.1 s at 0.25x). Where the source has nothing before the
    item, the rest is a silence at its start. None at 1x and faster, where
    the lead is under 20 ms (under a frame) and is left."""
    speed = float(item["speed"])
    if speed >= 1.0 or media.kind != "video" or not media.has_audio:
        return None
    lead = atempo_lead(speed)
    read_us = min(int(round((lead * speed + LEAD_READ_S) * model.US)), item["source_in_us"])
    early = read_us / model.US / speed                 # output seconds the earlier read adds
    span = item["source_out_us"] - item["source_in_us"]
    return {"input": ["-ss", _secs(item["source_in_us"] - read_us), "-t", _secs(span + read_us + READ_MARGIN_US),
                      "-i", media.path],
            "trim": samples_at(int(round((early - lead) * model.US))) if early >= lead else 0,
            "delay": 0 if early >= lead else samples_at(int(round((lead - early) * model.US)))}


def item_audio(k: int, j: int, item: dict, media: MediaFile, gain: float,
               mutes: list[tuple[float, float, str]] | None = None, lead: dict | None = None) -> str:
    """Item k's own sound (input j) to exactly its samples on the timeline:
    its speed (chained atempo; for a slowed item, `lead` (audio_lead) takes
    atempo's lead out), its mutes and bleeps (item_mutes: muted, and
    for a bleep BLEEP_HZ in their place), its volume times `gain` (A1's
    volume, or 0 when A1 is muted or another track is soloed) and its fades.
    Silence when its media has no sound. Statements, the last one labelled
    [a<k>]."""
    n = _samples(item)
    tail = f"aformat=sample_fmts=s16:channel_layouts=stereo,apad,atrim=end_sample={n},asetpts=N/SR/TB"
    volume = item.get("volume", 1.0) * gain
    if media.kind == "image" or not media.has_audio or volume <= 0:
        return f"anullsrc=r={AUDIO_RATE}:cl=stereo,{tail}[a{k}]"
    parts = [f"[{j}:a]asetpts=PTS-STARTPTS", f"aresample={AUDIO_RATE}"]
    pre = 0 if lead else pre_roll(item, media)
    if pre:     # the input was read from the pre-roll (_input_args): its sound starts at the in point
        parts += [f"atrim=start_sample={samples_at(pre)}", "asetpts=N/SR/TB"]
    parts += atempo_chain(item["speed"])
    if lead:
        parts.append("asetpts=N/SR/TB")
        if lead["trim"]:
            parts += [f"atrim=start_sample={lead['trim']}", "asetpts=N/SR/TB"]
        if lead["delay"]:
            parts.append(f"adelay=delays={lead['delay']}S|{lead['delay']}S")
    mutes = mutes or []
    if mutes:
        parts += ["asetpts=N/SR/TB", f"volume=0:enable='{_between((a, b) for a, b, _m in mutes)}'"]
    if abs(volume - 1.0) > 1e-9:
        parts.append(f"volume={_num(volume)}")
    fades = _fade_filters(item, n)
    bleeps = [(a, b) for a, b, mode in mutes if mode == "bleep"]
    if not bleeps:
        return ",".join([*parts, tail, *fades]) + f"[a{k}]"
    tone = (f"sine=frequency={BLEEP_HZ}:sample_rate={AUDIO_RATE},"
            f"aformat=sample_fmts=s16:channel_layouts=stereo,atrim=end_sample={n},asetpts=N/SR/TB,"
            + (f"volume={_num(gain)}," if abs(gain - 1.0) > 1e-9 else "")
            + f"volume=0:enable='not({_between(bleeps)})'[b{k}]")
    return ";\n".join([",".join([*parts, tail, *fades]) + f"[p{k}]", tone,
                       f"[p{k}][b{k}]amix=inputs=2:duration=first:normalize=0,"
                       f"aformat=sample_fmts=s16:channel_layouts=stereo[a{k}]"])


def _soloed(doc: dict) -> bool:
    return any(t["kind"] == "audio" and t.get("solo") for t in doc["tracks"])


def _a1_gain(doc: dict) -> float:
    """A1's volume: 0 when it is muted, or when another sound track is
    soloed and A1 isn't."""
    a1 = model.track(doc, model.MAIN_AUDIO) or {}
    if a1.get("mute") or (_soloed(doc) and not a1.get("solo")):
        return 0.0
    return float(a1.get("volume", 1.0))


# ---- the tail pad ------------------------------------------------------------------------------


def run_end(items: list[dict], index: int) -> bool:
    """Whether V1 item `index` ends a run: the next item doesn't go on with
    the same video where this one stops (a cut), or there is no next item."""
    if index + 1 >= len(items):
        return True
    a, b = items[index], items[index + 1]
    return not (a["media_id"] == b["media_id"] and b["source_in_us"] == a["source_out_us"]
                and a["speed"] == b["speed"])


def tails(doc: dict, items: list[dict], words: list[dict]) -> dict[str, int]:
    """{item id: tail in µs of its source} for each V1 item that ends a run
    in the middle of its last word (plan problem 4). The last word started
    before the cut; its transcript end plus END_PAD_US is past the cut, so
    the cut would clip it. The tail runs to that end, but never into the
    next word nor past the video's end, and never over a mute or bleep.
    Only an item of the captions' media has a transcript to go by."""
    captions = doc.get("captions") or {}
    mid = captions.get("media_id")
    if not words or mid is None:
        return {}
    starts = sorted(float(w["start"]) for w in words)
    length = int((doc.get("media") or {}).get(mid, {}).get("duration_us") or 0)
    out: dict[str, int] = {}
    for index, item in enumerate(items):
        if item.get("media_id") != mid or not run_end(items, index):
            continue
        cut = item["source_out_us"] / model.US
        said = [w for w in words if float(w["start"]) < cut]
        if not said:
            continue
        last = max(said, key=lambda w: (float(w["start"]), float(w["end"])))
        end = float(last["end"]) + END_PAD_US / model.US
        later = [s for s in starts if s >= cut and s > float(last["start"])]
        if later:
            end = min(end, later[0])
        if length:
            end = min(end, length / model.US)
        tail = int(round((end - cut) * model.US))
        if tail < TAIL_MIN_US:
            continue
        a, b = item["source_out_us"], item["source_out_us"] + tail
        if any(int(m["in_us"]) < b and int(m["out_us"]) > a for m in captions.get("mutes") or []):
            continue
        out[item["id"]] = tail
    return out


def end_pad_us(doc: dict) -> int:
    """How far past its end an export of a clip's project runs, as Apply
    renders the clip (core.pipeline END_PAD, 0.4 s; step 8's choice, ED34).

    Apply cuts a clip END_PAD past its window's end, because Whisper's word
    ends run early, and drops the pad as soon as the clip has a cut
    (video_editor/timeline.py clamps the kept ranges to the window). So the
    pad is the clip's: a project made from a clip carries it when V1 is
    still one stretch of the clip's own video (no cut, one speed) that ends
    where the clip's window ends. Not for a whole-video clip (no pad, as
    Apply), held to the clip's `stop_by` as Apply holds it, and never past
    the video's end. 0 for everything else, a project made by hand among
    them."""
    ctx = doc.get("context") or {}
    if ctx.get("origin") != "clip":
        return 0
    clip, job = ctx.get("clip") or {}, ctx.get("job") or {}
    mid = ctx.get("media_id")
    if job.get("whole") or not isinstance(clip.get("end_us"), int) or not isinstance(clip.get("start_us"), int):
        return 0
    items = _main_items(doc)
    if not items or any(i.get("media_id") != mid for i in items):
        return 0
    for a, b in zip(items, items[1:]):
        if b["source_in_us"] != a["source_out_us"] or b["speed"] != a["speed"]:
            return 0
    last = items[-1]
    if last["source_out_us"] != clip["end_us"]:
        return 0
    pad = END_PAD_US
    stop = job.get("stop_by")
    length_s = (clip["end_us"] - clip["start_us"]) / model.US
    if isinstance(stop, (int, float)) and not isinstance(stop, bool) and float(stop) >= length_s - 0.01:
        pad = max(0, min(pad, int(round((float(stop) - length_s) * model.US))))
    known = int(((doc.get("media") or {}).get(mid) or {}).get("duration_us") or 0)
    if known:
        pad = min(pad, known - last["source_out_us"])
    return max(0, pad)


def with_end_pad(doc: dict, pad_us: int) -> dict:
    """The document with V1's last item running `pad_us` further in its
    source (its length in whole frames, at its speed), and the pad covering
    the whole program (8-D10): looped music that filled the program fills
    it still, and every item on the other video tracks and the text tracks
    that ends at the old end (an overlay, a picture, a sticker, a logo,
    text, the hook) ends at the new one. An overlay video runs on in its
    source as far as the source has frames, then holds its last frame
    (overlay_graph's hold). Captions are not touched: they follow the
    document's timeline, as Apply's are built from the unpadded window.
    Only the export and the draft compile this; the document itself is
    never changed."""
    if pad_us <= 0:
        return doc
    out = model.clone(doc)
    fps = out["canvas"]["fps"]
    main = model.track(out, model.MAIN_VIDEO)
    old_end = model.program_end(out)
    last = main["items"][-1]
    last["source_out_us"] += pad_us
    first_frame = model.frames_at(last["start_us"], fps)
    frames = max(1, model.frames_at(int(round((last["source_out_us"] - last["source_in_us"]) / last["speed"])), fps))
    last["duration_us"] = model.frame_us(first_frame + frames, fps) - last["start_us"]
    new_end = model.program_end(out)
    grow = new_end - old_end
    old_frame = model.frames_at(old_end, fps)
    media = out.get("media") or {}
    for track in out["tracks"]:
        if track["id"] == model.MAIN_VIDEO:
            continue
        for item in track["items"]:
            ends_at_end = model.frames_at(item["start_us"] + item["duration_us"], fps) == old_frame
            if track["kind"] == "audio":
                if item.get("loop") and item["start_us"] + item["duration_us"] == old_end:
                    item["duration_us"] += grow
                continue
            if not ends_at_end:
                continue
            item["duration_us"] = new_end - item["start_us"]
            info = media.get(item.get("media_id")) or {}
            if track["kind"] == "video" and info.get("kind") == "video" and "source_out_us" in item:
                more = int(round(grow * float(item.get("speed") or 1.0)))
                known = int(info.get("duration_us") or 0)
                if known:
                    more = max(0, min(more, known - int(item["source_out_us"])))
                item["source_out_us"] += more
    return out


def _tail_input(item: dict, tail_us: int, media: MediaFile) -> list[str]:
    return ["-ss", _secs(item["source_out_us"]), "-t", _secs(tail_us + READ_MARGIN_US), "-i", media.path]


def tail_audio(label: str, j: int, item: dict, tail_us: int, gain: float, at_sample: int) -> str:
    """The sound of `item` past its cut (input j, read from its out point),
    at its speed and volume, faded out over the whole tail, starting
    `at_sample` samples into what it is mixed with."""
    n = samples_at(int(round(tail_us / float(item["speed"]))))
    parts = [f"[{j}:a]asetpts=PTS-STARTPTS", f"aresample={AUDIO_RATE}", *atempo_chain(item["speed"])]
    volume = item.get("volume", 1.0) * gain
    if abs(volume - 1.0) > 1e-9:
        parts.append(f"volume={_num(volume)}")
    parts += ["aformat=sample_fmts=s16:channel_layouts=stereo", "apad", f"atrim=end_sample={n}",
              "asetpts=N/SR/TB", f"afade=t=out:ss=0:ns={n}"]
    if at_sample > 0:
        parts.append(f"adelay=delays={at_sample}S|{at_sample}S")
    return ",".join(parts) + f"[{label}]"


def _cut_stage(sid: str, chunk: _Chunk, doc: dict, inputs: Inputs, fps: dict, canvas: tuple[int, int] | None,
               video_out: Path, audio_out: Path, *, tail_of: dict[str, int] | None = None,
               carried: tuple[dict, int] | None = None, draft: bool = False) -> Stage:
    """One chunk of V1 cut to the mezzanine and its A1 sound. `tail_of`
    (tails()) gives the tails of items whose next item is in this chunk;
    `carried` is (item, tail) of the item before the chunk, whose tail
    plays under its start."""
    work = Path(inputs.work)
    args: list[str] = []
    graph: list[str] = []
    reads: list[str] = []
    gain = _a1_gain(doc)
    count = 0                       # inputs so far
    for k, item in enumerate(chunk.items):
        media = inputs.media[item["media_id"]]
        args += _input_args(item, media, fps)
        reads.append(media.path)
        j = count
        count += 1
        framing = None if canvas is None else (item.get("framing") or {})
        graph += _v1_picture(k, j, item, media, fps, canvas, framing=framing)
        lead = audio_lead(item, media) if item.get("volume", 1.0) * gain > 0 else None
        if lead:
            args += lead["input"]
            reads.append(media.path)
            j = count
            count += 1
        graph.append(item_audio(k, j, item, media, gain, item_mutes(doc, item), lead))
    n = len(chunk.items)
    graph.append("".join(f"[v{k}]" for k in range(n)) + f"concat=n={n}:v=1:a=0[vout]")
    # The tails: under the next item's start (an L-cut), the picture and
    # the timeline as they are.
    tails_here: list[str] = []
    if gain > 0:
        first = samples_at(chunk.items[0]["start_us"])
        pending = []
        if carried is not None:
            pending.append((carried[0], carried[1], 0))
        for k, item in enumerate(chunk.items[:-1]):
            if (tail_of or {}).get(item["id"]):
                pending.append((item, tail_of[item["id"]], samples_at(item["start_us"] + item["duration_us"]) - first))
        for item, tail_us, at in pending:
            media = inputs.media[item["media_id"]]
            if media.kind == "image" or not media.has_audio:
                continue
            j = sum(1 for a in args if a == "-i")
            args += _tail_input(item, tail_us, media)
            reads.append(media.path)
            tails_here.append(tail_audio(f"t{len(tails_here)}", j, item, tail_us, gain, at))
    if tails_here:
        graph.append("".join(f"[a{k}]" for k in range(n)) + f"concat=n={n}:v=0:a=1[acat]")
        graph += tails_here
        graph.append("[acat]" + "".join(f"[t{i}]" for i in range(len(tails_here)))
                     + f"amix=inputs={len(tails_here) + 1}:duration=first:normalize=0,"
                       "aformat=sample_fmts=s16:channel_layouts=stereo[aout]")
    else:
        graph.append("".join(f"[a{k}]" for k in range(n)) + f"concat=n={n}:v=0:a=1[aout]")
    script = work / f"{sid}.graph.txt"
    vt, at = temp_of(video_out), temp_of(audio_out)
    argv = [*_head(inputs), *args, script_flag(inputs.version), str(script),
            "-map", "[vout]", *mezz_args(fps, draft), "-an", str(vt),
            "-map", "[aout]", "-c:a", "pcm_s16le", "-ar", str(AUDIO_RATE), "-ac", "2", "-vn", str(at)]
    seconds = sum(i["duration_us"] for i in chunk.items) / model.US
    return Stage(id=sid, kind="ffmpeg", commands=[argv], reads=reads, writes=[str(video_out), str(audio_out)],
                 temps={str(video_out): str(vt), str(audio_out): str(at)},
                 scripts={str(script): ";\n".join(graph) + "\n"}, seconds=seconds,
                 weight=WEIGHTS["cut"] * seconds)


def _chunks(doc: dict, items: list[dict], inputs: Inputs, ai_ok: bool, fps: dict,
            canvas: tuple[int, int], tail_of: dict[str, int] | None = None) -> list[_Chunk]:
    """V1 split into chunks: AI runs (one stretch of one video, one framing,
    look and speed, in the video's order) and manual chunks of at most
    MAX_ITEM_INPUTS items whose command stays inside ARGV_BUDGET."""
    out: list[_Chunk] = []
    for item in items:
        media = inputs.media.get(item["media_id"])
        if media is None:
            raise CompileError(f"The media of item {item['id']} wasn't found.")
        ai = ai_ok and media.kind == "video" and is_ai_item(doc, item)
        kind = "ai" if ai else "manual"
        last = out[-1] if out else None
        if last is not None and last.kind == kind:
            if kind == "ai" and len(last.items) < MAX_AI_RUN and _same_ai_run(doc, last.items[-1], item):
                last.items.append(item)
                continue
            if kind == "manual" and len(last.items) < MAX_ITEM_INPUTS:
                trial = _cut_stage("x", _Chunk("manual", [*last.items, item]), doc, inputs, fps, canvas,
                                   Path(inputs.work) / "v-000.mp4", Path(inputs.work) / "a-000.wav",
                                   tail_of=tail_of)
                if cmdline_length(trial.argv) <= ARGV_BUDGET:
                    last.items.append(item)
                    continue
        out.append(_Chunk(kind, [item]))
    return out


def _ai_pieces(doc: dict, chunk: _Chunk, inputs: Inputs, fps: dict,
               tail_of: dict[str, int] | None = None) -> list[_Chunk]:
    """An AI run's cut, in pieces of at most MAX_ITEM_INPUTS items whose
    command stays inside ARGV_BUDGET, as manual chunks are held (step 8).
    One piece when the whole run fits, which is the plan as before."""
    pieces: list[_Chunk] = []
    work = Path(inputs.work)
    for item in chunk.items:
        last = pieces[-1] if pieces else None
        if last is not None and len(last.items) < MAX_ITEM_INPUTS:
            trial = _cut_stage("x", _Chunk("ai", [*last.items, item]), doc, inputs, fps, None,
                               work / "src-000.mp4", work / "a-000.wav", tail_of=tail_of)
            if cmdline_length(trial.argv) <= ARGV_BUDGET:
                last.items.append(item)
                continue
        pieces.append(_Chunk("ai", [item]))
    return pieces


def _join_stage(sid: str, inputs: Inputs, parts: list[Path], joined: Path, seconds: float) -> Stage:
    """Pieces cut with the same parameters joined by stream copy, with
    video/encoding.py concat()'s flags (as the program's own join)."""
    from video.encoding import concat_entry, final_movflags

    work = Path(inputs.work)
    listing = work / f"{sid}.txt"
    jt = temp_of(joined)
    return Stage(id=sid, kind="join",
                 commands=[[inputs.ffmpeg, "-y", "-v", "error", "-f", "concat", "-safe", "0", "-i", str(listing),
                            "-copyts", "-c", "copy", *final_movflags(), str(jt)]],
                 reads=[str(p) for p in parts], writes=[str(joined)], temps={str(joined): str(jt)},
                 scripts={str(listing): "".join(concat_entry(p.as_posix()) + "\n" for p in parts)},
                 seconds=seconds, weight=WEIGHTS["join"] * seconds)


def _ai_params(doc: dict, chunk: _Chunk, inputs: Inputs, fps: dict, src: Path, framed: Path) -> dict:
    """What the frame stage hands core.framing.frame_intermediate: the
    clip's framing as its render options (crop, gaming) and the job's
    choices kept in the document (context.job: podcast, sport)."""
    first = chunk.items[0]
    framing = first.get("framing") or {}
    job = (doc.get("context") or {}).get("job") or {}
    opts: dict = {}
    if "crop" in framing:
        opts["crop"] = framing["crop"]
    gaming = bool(isinstance(framing.get("gaming"), dict))
    if gaming:
        opts["gaming"] = framing["gaming"]
    sport = job.get("sport")
    sport_name = sport.get("name") if isinstance(sport, dict) else (sport if isinstance(sport, str) else None)
    media = inputs.media[first["media_id"]]
    seconds = sum(i["duration_us"] for i in chunk.items) / model.US
    # The tracking result is kept per stretch: the same items, speed, frame
    # rate and crop track the same way (the tracking settings are added to
    # the key when the stage runs).
    key = {"v": 1, "media": first["media_id"], "fps": fps, "speed": first["speed"],
           "spans": [[i["source_in_us"], i["source_out_us"], i["duration_us"]] for i in chunk.items],
           "crop": framing.get("crop", "track")}
    return {"source": str(src), "out": str(framed), "seconds": seconds, "opts": opts,
            "gaming": gaming, "podcast": bool(job.get("podcast")), "sport_name": sport_name,
            "sport_screen": job.get("sport_screen") if sport_name else None,
            "vf_extra": _color_chain(first), "tracking_cache": media.cache, "tracking_key": key}


def _conform_stage(sid: str, inputs: Inputs, fps: dict, canvas: tuple[int, int], frames: int, seconds: float,
                   framed: Path, video_out: Path, draft: bool = False) -> Stage:
    vt = temp_of(video_out)
    w, h = canvas
    vf = (f"setpts=PTS-STARTPTS,fps={_fps_text(fps)},scale={w}:{h}:flags=lanczos,setsar=1,format=yuv420p,"
          f"tpad=stop=-1:stop_mode=clone,trim=end_frame={frames},setpts=PTS-STARTPTS")
    argv = [*_head(inputs), "-i", str(framed), "-map", "0:v:0", "-vf", vf, *mezz_args(fps, draft), "-an", str(vt)]
    return Stage(id=sid, kind="ffmpeg", commands=[argv], reads=[str(framed)], writes=[str(video_out)],
                 temps={str(video_out): str(vt)}, seconds=seconds, weight=WEIGHTS["conform"] * seconds)


# ---- overlays ----------------------------------------------------------------------------------


def _overlays(doc: dict) -> list[dict]:
    """Every item on the video tracks above V1, bottom track first, in start
    order: the order they are drawn in. A muted video track is hidden."""
    out = []
    for t in doc["tracks"]:
        if t["kind"] != "video" or t["id"] == model.MAIN_VIDEO or t.get("mute"):
            continue
        out.extend(sorted(t["items"], key=lambda i: i["start_us"]))
    return out


def _overlay_input(item: dict, media: MediaFile, fps: dict) -> list[str]:
    if media.kind == "image":
        return ["-loop", "1", "-framerate", _fps_text(fps), "-t", _secs(item["duration_us"]), "-i", media.path]
    span = item["source_out_us"] - item["source_in_us"]
    pre = pre_roll(item, media)
    return ["-ss", _secs(item["source_in_us"] - pre), "-t", _secs(span + pre + READ_MARGIN_US), "-i", media.path]


def overlay_graph(n: int, j: int, item: dict, media: MediaFile, fps: dict, canvas: tuple[int, int],
                  base: str, out: str) -> list[str]:
    """Overlay n (input j) drawn onto label `base` from its start to its end
    on the timeline, framed (fit or fill, no pad), coloured and transformed."""
    start, end = item["start_us"], item["start_us"] + item["duration_us"]
    if media.kind == "image":
        shift = f"+{_secs(start)}/TB" if start else ""
        head = f"[{j}:v]setpts=PTS-STARTPTS{shift}"
    else:
        head = _picture_head(j, item, media, fps, start)
        # A video longer on the timeline than its source span (the export's
        # end pad past the source's last frame, with_end_pad) holds its last
        # frame to its end; a plain item never has this, so its graph is as it was.
        hold = item["duration_us"] - int(round((item["source_out_us"] - item["source_in_us"]) / item["speed"]))
        if hold > 0:
            head += f",tpad=stop_mode=clone:stop_duration={_secs(hold + READ_MARGIN_US)}"
    framing = item.get("framing") or {}
    statements = [f"{head}[os{n}]",
                  *framing_graph(f"os{n}", f"of{n}", f"o{n}", framing, canvas, overlay=True)]
    t = _transform(item)
    color = _color_chain(item)
    chain = [c for c in (color,) if c] + ([] if _plain_transform(t) else transform_filters(t)) + ["format=yuva420p"]
    statements.append(f"[of{n}]{','.join(chain)}[ot{n}]")
    x, y = _position(t, canvas, framing.get("align", "center") if framing.get("fit") != "fill" else "center")
    # between() includes its end: the last microsecond before the item ends.
    statements.append(f"[{base}][ot{n}]overlay=x={x}:y={y}:enable='between(t,{_secs(start)},{_secs(end - 1)})'"
                      f":eof_action=pass[{out}]")
    return statements


# ---- captions, text and branding: one ASS file ------------------------------------------------


def transcript_words(inputs: Inputs) -> list[dict]:
    """The captions media's words, [{start, end, word}] in its seconds, in
    order (a segment without word times is spread evenly, as captions do)."""
    from video import captions as cap

    segments = _segments(inputs)
    return cap._words_in_window(segments, float("-inf"), float("inf")) if segments else []


def _segments(inputs: Inputs) -> list:
    from core.models import Segment

    out = []
    for s in inputs.segments or []:
        if isinstance(s, Segment):
            out.append(s)
            continue
        try:
            out.append(Segment(start=float(s["start"]), end=float(s["end"]), text=str(s.get("text") or ""),
                               words=s.get("words") or None))
        except (KeyError, TypeError, ValueError):
            continue
    return out


@dataclass
class CaptionSpan:
    """A stretch of the captions media on V1: source [src_in, src_out) plays
    at program [start, end), at `speed`. Consecutive V1 items that go on
    with the same video where the last one stopped are one span."""

    src_in: int
    src_out: int
    start: int
    end: int
    speed: float

    def program(self, t: float) -> float:
        """A source time (seconds) in program seconds, held inside the span."""
        us = min(max(t * model.US, self.src_in), self.src_out)
        return min(self.start + (us - self.src_in) / self.speed, self.end) / model.US


def caption_spans(doc: dict) -> list[CaptionSpan]:
    mid = (doc.get("captions") or {}).get("media_id")
    if mid is None or ((doc.get("media") or {}).get(mid) or {}).get("kind") == "image":
        return []
    spans: list[CaptionSpan] = []
    for item in _main_items(doc):
        if item.get("media_id") != mid:
            continue
        span = CaptionSpan(item["source_in_us"], item["source_out_us"], item["start_us"],
                           item["start_us"] + item["duration_us"], float(item["speed"]))
        last = spans[-1] if spans else None
        if last and last.src_out == span.src_in and last.end == span.start and last.speed == span.speed:
            last.src_out, last.end = span.src_out, span.end
        else:
            spans.append(span)
    return spans


def _played_parts(a: float, b: float, spans: list[CaptionSpan]) -> list[tuple[float, float]]:
    """The parts of the source stretch [a, b) (seconds) that the spans play,
    merged and in source order (a stretch played twice counts once)."""
    parts = sorted((max(a, s.src_in / model.US), min(b, s.src_out / model.US)) for s in spans)
    out: list[tuple[float, float]] = []
    for lo, hi in parts:
        if hi <= lo:
            continue
        if out and lo <= out[-1][1]:
            out[-1] = (out[-1][0], max(out[-1][1], hi))
        else:
            out.append((lo, hi))
    return out


def _line_word_times(text: str, a: float, b: float, words: list[dict],
                     spans: list[CaptionSpan]) -> list[tuple[float, float]]:
    """When each word of a caption line [a, b) is said, in the source's
    seconds, never inside a stretch the timeline cuts out (9-D5, 9-D6).

    The transcript's own word times when the line's words line up with them
    (captions._word_times, unchanged); else, when the line reaches over a
    cut (a line merged across one, a line whose end was cut off, a saved
    line made before the cut), the transcript's words of the parts that
    play when they line up with the line's; else the line's words shared out
    by length over the parts that play only. A line wholly inside what
    plays is timed exactly as before, unless it is shorter than 0.05 s
    (its words used to be shared out past its end and dropped)."""
    from video import captions as cap

    times = cap._word_times(text, a, b, words)
    tokens = text.split()
    parts = _played_parts(a, b, spans)
    if not parts or len(times) != len(tokens):
        return times

    def middle(w: dict) -> float:
        return (float(w["start"]) + min(float(w["end"]), float(w["start"]) + cap.LONGEST_WORD_SECONDS)) / 2

    inside = [w for w in words if a - 0.01 <= middle(w) <= b + 0.01]
    matched = len(inside) == len(tokens)
    if matched and not any(not a <= middle(w) < b
                           and not any(s.src_in / model.US <= middle(w) < s.src_out / model.US for s in spans)
                           for w in inside):
        return times                                         # the transcript's own times
    # (_word_times also counts a word just outside the line, by 0.01 s: when
    # that word is cut out, its time would take one of the line's words with
    # it, so the words are timed as below instead. Words inside the line that
    # a cut takes out are dropped by remap_captions, as before.)
    if not matched and parts == [(a, b)] and b - a >= 0.05:
        return times                                         # shared out over the line, as before
    # (A line shorter than 0.05 s is shared out over 0.05 s by _word_times,
    # past its end, so its words would fall outside it and vanish.)
    played = [w for w in inside if any(lo <= middle(w) < hi for lo, hi in parts)]
    if len(played) == len(tokens):
        return [(float(w["start"]), float(w["end"])) for w in played]
    # 9-D14: the line's own transcript words that a cut takes out (said
    # inside the line, in no part that plays). A typed word that lines up
    # with one of them (kept while another word was fixed, or typed again)
    # keeps that word's time, so it stays cut out; only the other words are
    # shared out over the parts that play.
    cut = [w for w in inside if a <= middle(w) < b
           and not any(s.src_in / model.US <= middle(w) < s.src_out / model.US for s in spans)]
    if cut:
        import difflib

        def plain(word: object) -> str:
            return "".join(ch for ch in str(word or "").lower() if ch.isalnum())

        cut_ids = {id(w) for w in cut}
        fixed: dict[int, tuple[float, float]] = {}
        match = difflib.SequenceMatcher(a=[plain(w.get("word")) for w in inside],
                                        b=[plain(t) for t in tokens], autojunk=False)
        for tag, i1, i2, j1, _j2 in match.get_opcodes():
            if tag == "equal":
                for k in range(i2 - i1):
                    if id(inside[i1 + k]) in cut_ids:
                        w = inside[i1 + k]
                        fixed[j1 + k] = (float(w["start"]), float(w["end"]))
        if fixed:
            rest = [t for j, t in enumerate(tokens) if j not in fixed]
            shared = iter(_shared_out(rest, parts))
            return [fixed[j] if j in fixed else next(shared) for j in range(len(tokens))]
    return _shared_out(tokens, parts)


def _shared_out(tokens: list[str], parts: list[tuple[float, float]]) -> list[tuple[float, float]]:
    """Words shared out by length over the parts that play (source seconds),
    each word inside the part that holds its middle."""
    total = sum(hi - lo for lo, hi in parts)
    weights = [max(1, len(t)) for t in tokens]
    out, at = [], 0.0
    for wgt in weights:
        s_off, e_off = at, at + total * wgt / sum(weights)
        at = e_off
        m_off, cum = (s_off + e_off) / 2, 0.0
        for index, (lo, hi) in enumerate(parts):
            if m_off < cum + (hi - lo) or index == len(parts) - 1:
                out.append((lo + max(0.0, s_off - cum), lo + min(e_off - cum, hi - lo)))
                break
            cum += hi - lo
    return out


def remap_captions(lines: list[dict], words: list[dict], spans: list[CaptionSpan]) -> tuple[list[dict], list[dict]]:
    """Caption lines in the source's time ([{start, end, text, ...}],
    seconds) on the program's timeline: (lines, spoken words), both in
    program seconds.

    Every word of a line gets its source time: the transcript's own when the
    line's words line up with it (captions._word_times, as the highlight
    reads them), else the line's time shared out by word length, over the
    parts of the line that play only (_line_word_times). A word is
    kept where its middle plays: words in a cut-out stretch are dropped, and
    a line split by a cut becomes a caption on each side holding its own
    words. Times are mapped through the span, so speed scales them, and the
    words' own times go along for the word highlight and karaoke (plan
    problem 5: they are remapped, never left in the old timeline). A stretch
    played twice is captioned twice."""
    from video import captions as cap

    out: list[dict] = []
    spoken: list[dict] = []
    for line in lines:
        text = str(line.get("text", "")).replace("\\", "").replace("{", "").replace("}", "").strip()
        try:
            a, b = float(line["start"]), float(line["end"])
        except (KeyError, TypeError, ValueError):
            continue
        if not text or b <= a:
            continue
        tokens = text.split()
        times = _line_word_times(text, a, b, words, spans)
        if len(times) != len(tokens):
            continue
        rest = {k: v for k, v in line.items() if k not in ("start", "end", "text", "in_us", "out_us", "id")}
        for span in spans:
            lo, hi = span.src_in / model.US, span.src_out / model.US
            kept = [(tok, ts, te) for tok, (ts, te) in zip(tokens, times)
                    if lo <= (ts + min(te, ts + cap.LONGEST_WORD_SECONDS)) / 2 < hi]
            if not kept:
                continue
            start, end = span.program(max(a, lo)), span.program(min(b, hi))
            if end <= start:
                continue
            out.append({**rest, "start": round(start, 6), "end": round(end, 6), "text": " ".join(t for t, _, _ in kept)})
            spoken += [{"start": round(span.program(ts), 6), "end": round(span.program(te), 6), "word": tok}
                       for tok, ts, te in kept]
    out.sort(key=lambda line: (line["start"], line["end"]))
    spoken.sort(key=lambda w: (w["start"], w["end"]))
    return out, spoken


def captions_on(doc: dict, inputs: Inputs) -> bool:
    """Whether the export burns captions, as core.pipeline.captions_on
    decides for a clip: the settings' switch, then the project's own
    (`captions.enabled`), else the job's (`context.job.job_captions`) for a
    project made from a clip. A project made by hand with no choice: off."""
    if not inputs.job_captions:
        return False
    enabled = (doc.get("captions") or {}).get("enabled")
    if enabled is not None:
        return bool(enabled)
    context = doc.get("context") or {}
    return context.get("origin") == "clip" and bool((context.get("job") or {}).get("job_captions", True))


def _speaker_turns(doc: dict, style: dict) -> list | None:
    """Who talks where, in the source's seconds, for the second speaker's
    colour: the turns the clip was rendered with (context.job.speaker_turns,
    counted from the clip's start) with the project's speaker fixes laid
    over them. None when the style doesn't ask for the colour. The voice
    analysis is not run again."""
    if not style.get("second_speaker"):
        return None
    from video import captions as cap

    context = doc.get("context") or {}
    start = float((context.get("imported") or {}).get("start") or 0.0)
    turns = []
    for turn in (context.get("job") or {}).get("speaker_turns") or []:
        try:
            turns.append([float(turn[0]) + start, float(turn[1]) + start, int(turn[2])])
        except (TypeError, ValueError, IndexError):
            continue
    fixes = [[e["in_us"] / model.US, e["out_us"] / model.US, int(e.get("speaker", 0))]
             for e in (doc.get("captions") or {}).get("speaker_edits") or []]
    return cap.paint_turns(turns, fixes)


def _source_lines(doc: dict, inputs: Inputs, spans: list[CaptionSpan], words: list[dict], wpc: int,
                  turns: list | None) -> list[dict]:
    """The caption lines in the source's seconds: the project's own
    (captions.lines), else made from the transcript over what V1 plays
    (captions.build_caption_lines, the rules a clip's captions follow)."""
    from core.models import ClipCandidate
    from video import captions as cap

    saved = (doc.get("captions") or {}).get("lines")
    if saved is not None:
        lines = [{**{k: v for k, v in line.items() if k not in ("in_us", "out_us", "id")},
                  "start": line["in_us"] / model.US, "end": line["out_us"] / model.US} for line in saved]
        return cap.tag_lines(lines, turns, words) if turns is not None else lines
    segments = _segments(inputs)
    if not segments or not spans:
        return []
    lo, hi = min(s.src_in for s in spans) / model.US, max(s.src_out for s in spans) / model.US
    rel = [[a - lo, b - lo, w] for a, b, w in turns] if turns else None
    made = cap.build_caption_lines(segments, ClipCandidate(start=lo, end=hi, score=0), wpc, rel)
    return [{**line, "start": line["start"] + lo, "end": line["end"] + lo} for line in made]


def caption_style_of(doc: dict, inputs: Inputs) -> dict | None:
    """The caption style the export uses: the project's own, else the
    settings' (inputs.caption_style), else None (DEFAULT_STYLE)."""
    style = (doc.get("captions") or {}).get("style")
    return dict(style) if isinstance(style, dict) else (dict(inputs.caption_style)
                                                        if isinstance(inputs.caption_style, dict) else None)


def caption_sources(doc: dict, inputs: Inputs, caption_doc: dict | None = None
                    ) -> tuple[list[dict], list[dict], list[CaptionSpan]]:
    """(the caption lines in the source's seconds, the transcript's words,
    the spans of the timeline they are mapped through), exactly as the
    export reads them. `caption_doc`: the document whose timeline the
    captions follow (the export's unpadded one, ED34)."""
    from video import captions as cap

    opts = {**cap.DEFAULT_STYLE, **(caption_style_of(doc, inputs) or {})}
    spans = caption_spans(caption_doc or doc)
    words = transcript_words(inputs)
    source = _source_lines(doc, inputs, spans, words, int(opts["words_per_caption"]), _speaker_turns(doc, opts))
    return source, words, spans


def program_caption_lines(doc: dict, inputs: Inputs, caption_doc: dict | None = None
                          ) -> tuple[list[dict], list[dict]]:
    """The caption lines and their spoken words in program seconds: what
    the export burns (build_ass), what its .srt holds (step 9) and what the
    caption lane shows (caption_lane), from one remap."""
    source, words, spans = caption_sources(doc, inputs, caption_doc)
    return remap_captions(source, words, spans)


CUT_LINE_SECONDS = 0.05     # how much of a caption line may fall outside what plays and still count as whole


def caption_line_cut(doc: dict, inputs: Inputs) -> bool:
    """Whether some caption line reaches over a cut or past the ends of what
    V1 plays (step 9, 9-D12): part of it plays and more than
    CUT_LINE_SECONDS of it doesn't. Today's re-render (Branch A) keeps such
    a line whole, with every word and over its whole span
    (video_editor.captions.remap_lines); the compiler, the lane, the live
    viewer and the .srt split it word by word (remap_captions). So an
    export that shows captions takes Branch B when this is true."""
    source, _words, spans = caption_sources(doc, inputs)
    for line in source:
        try:
            a, b = float(line["start"]), float(line["end"])
        except (KeyError, TypeError, ValueError):
            continue
        parts = _played_parts(a, b, spans)
        if parts and (b - a) - sum(hi - lo for lo, hi in parts) > CUT_LINE_SECONDS:
            return True
    return False


def word_timing_retimed(doc: dict, inputs: Inputs, render_opts: dict | None) -> bool:
    """Whether today's re-render would time the words differently from the
    live viewer (step 9, 9-D17): the style lights words up (highlight, or
    the karaoke animation) and the clip is cut or sped up. Today's render
    then shares each line's time out over its words by length
    (karaoke_untimed, core.pipeline's "retimed" path), while the compiler,
    the lane and the live viewer time each word from the transcript. So an
    export that burns such captions takes Branch B."""
    from video import captions as cap

    opts = {**cap.DEFAULT_STYLE, **(caption_style_of(doc, inputs) or {})}
    if not (opts.get("highlight") or opts.get("animation") == "karaoke"):
        return False
    edit = (render_opts or {}).get("edit")
    if not isinstance(edit, dict):
        return False
    try:
        speed = float(edit.get("speed", 1.0) or 1.0)
    except (TypeError, ValueError):
        speed = 1.0
    return bool(edit.get("keep")) or abs(speed - 1) >= 0.01


TRANSCRIPT_LINE_ID = "tx"     # + its index: a caption line made from the transcript, not saved yet


def caption_lane(doc: dict, inputs: Inputs) -> dict:
    """The caption lines for the new editor's caption lane (step 9), in
    program time, from the same remap the export burns.

    One entry per caption line in the source's time, in the source's order:
    `id` (the saved line's; for lines made from the transcript, not saved
    yet, "tx<n>", which set_captions keeps when the lines are saved with
    it), `in_us`/`out_us` and `text` (the source's), `speaker` (1 on the
    other speaker's lines when the style colours them, else 0), `pieces`:
    where it plays, [{start_us, end_us, text}] in program time (a line cut
    by an edit has one per side, a stretch played twice is captioned twice),
    and `hidden`: true when no part of it plays (cut out)."""
    from video import captions as cap

    saved = (doc.get("captions") or {}).get("lines")
    source, words, spans = caption_sources(doc, inputs)
    opts = {**cap.DEFAULT_STYLE, **(caption_style_of(doc, inputs) or {})}
    out = []
    for index, line in enumerate(source):
        own = saved[index] if saved is not None and index < len(saved) else None
        pieces, _ = remap_captions([line], words, spans)
        out.append({
            "id": own["id"] if own is not None else f"{TRANSCRIPT_LINE_ID}{index}",
            "in_us": int(own["in_us"]) if own is not None else int(round(float(line["start"]) * model.US)),
            "out_us": int(own["out_us"]) if own is not None else int(round(float(line["end"]) * model.US)),
            "text": str(own["text"] if own is not None else line.get("text", "")),
            "speaker": 1 if line.get("speaker") else 0,
            "pieces": [{"start_us": int(round(p["start"] * model.US)), "end_us": int(round(p["end"] * model.US)),
                        "text": p["text"]} for p in pieces],
            "hidden": not pieces,
        })
    return {
        "saved": saved is not None,
        "burned": captions_on(doc, inputs),
        "enabled": (doc.get("captions") or {}).get("enabled"),
        "media_id": (doc.get("captions") or {}).get("media_id"),
        "style": opts,
        "program_us": model.program_end(doc),
        "spans": [{"src_in_us": s.src_in, "src_out_us": s.src_out, "start_us": s.start, "end_us": s.end,
                   "speed": s.speed} for s in spans],
        "lines": out,
    }


def caption_source_range(doc: dict, start_us: int, end_us: int) -> tuple[int, int] | None:
    """A caption block's program [start_us, end_us) as a caption line's
    source [in_us, out_us), through the same spans the export maps captions
    with (caption_spans): the span that plays `start_us`, with `end_us` held
    to that span's end (a block never reaches across a cut). Each source
    time is the span's source in point plus the program offset times the
    speed, exactly (as a fraction), then the start rounded up and the end
    down to the microsecond: mapped back (CaptionSpan.program), the start is
    never before the frame the block was put on and the end never after it
    (each within 1 / speed µs), so the burned caption is on the same frames,
    never a frame off. None when no captioned video plays at `start_us` (a
    gap, a picture, another video)."""
    from fractions import Fraction

    spans = caption_spans(doc)
    span = next((s for s in spans if s.start <= start_us < s.end), None)
    if span is None:
        return None
    end_us = min(max(end_us, start_us + 1), span.end)
    speed = Fraction(span.speed)
    a = span.src_in + math.ceil((start_us - span.start) * speed)
    b = min(span.src_in + math.floor((end_us - span.start) * speed), span.src_out)
    return (a, b) if b > a else None


TEXT_POSITIONS = {"top": (8, 190), "middle": (5, 0), "bottom": (2, 440)}   # alignment, margin at 1920 tall


def text_place(look: dict) -> tuple[float, float] | None:
    """A text item's own point ({x, y}: its centre in % of the frame), or
    None when it is placed by name (top, middle, bottom)."""
    from video import captions as cap

    return cap.place_of(look.get("position")) if isinstance(look.get("position"), dict) else None


def _text_style(name: str, look: dict, canvas: tuple[int, int], font: str) -> str:
    """A text item's ASS style: the hook's look (video_editor/overlay.py),
    with the item's own font, size (at a 1920-tall canvas), colour and
    place where it has them."""
    from video import captions as cap

    s = canvas[1] / 1920
    color = cap._ass_color(look.get("color") or "#FFFFFF")
    if text_place(look) is not None:
        align, margin = 5, 0          # centred on its own point: each event's \pos (build_ass)
    else:
        align, margin = TEXT_POSITIONS.get(look.get("position") or "top", TEXT_POSITIONS["top"])
    size = round(int(look.get("size") or 96) * s)
    face = look.get("font") or font
    # Bold as captions are: not a one-weight display face (Anton, Bangers...), which libass would thicken.
    bold = -1 if cap._style_bold(face) else 0
    return (f"Style: {name},{face},{size},{color},{color},&H00000000,&H7F000000,{bold},0,0,0,"
            f"100,100,0,0,1,{max(3, round(9 * s))},3,{align},60,60,{round(margin * s)},1")


def _ass_text(text: str) -> str:
    """Text for an ASS event: no override characters, a line break as \\N."""
    clean = str(text).replace("\\", "").replace("{", "").replace("}", "").strip()
    return "\\N".join(part.strip() for part in clean.splitlines())


def _watermark(doc: dict, inputs: Inputs) -> dict | None:
    """The project's watermark (branding.watermark: null for none), else
    the settings' (inputs.watermark), as a clip's render picks it."""
    branding = doc.get("branding") or {}
    wm = branding["watermark"] if "watermark" in branding else inputs.watermark
    return wm if isinstance(wm, dict) and wm else None


def build_ass(doc: dict, inputs: Inputs, canvas: tuple[int, int], *,
              caption_doc: dict | None = None) -> tuple[str | None, list[str]]:
    """The plan's one ASS file (text, or None when nothing is burned) and
    the bundled font families it uses (staged beside it for the burn).

    - **Captions** (step 3's style, fonts, animations and safe zones, all
      through captions.caption_ass) from the project's lines or the
      transcript, remapped through the timeline (remap_captions);
    - **text items** and the **hook** (T tracks, not muted): the hook's look
      by default, each item's own font, size, colour and place;
    - the **text watermark**, as a clip's render draws it (watermark.py).
    Layers: captions 0, text 1, watermark 2, as today's renders stack them."""
    from core.models import ClipCandidate, Segment
    from video import captions as cap
    from video import fonts as bundled
    from video_editor import overlay
    from video_editor import watermark as wm_mod

    program_s = model.program_end(doc) / model.US
    text: str | None = None
    if inputs.burn_captions and captions_on(doc, inputs):
        style = caption_style_of(doc, inputs)
        # The captions follow the timeline as the document has it, so the
        # export's end pad (end_pad_us) captions nothing new, as Apply's pad
        # doesn't (core/pipeline.py builds a clip's captions unpadded).
        lines, spoken = program_caption_lines(doc, inputs, caption_doc=caption_doc)
        if lines:
            text = cap.caption_ass([Segment(start=0.0, end=program_s, text="", words=spoken)],
                                   ClipCandidate(start=0.0, end=program_s, score=0), style=style, lines=lines,
                                   canvas=canvas, language=inputs.language)
    styles: list[str] = []
    events: list[str] = []
    hook_font = cap.caption_font_for(inputs.language, None) or "Arial Black"
    looks: dict[str, str] = {}
    for track in doc["tracks"]:
        if track["kind"] != "text" or track.get("mute"):
            continue
        for item in sorted(track["items"], key=lambda i: i["start_us"]):
            body = item.get("text") or {}
            content = _ass_text(body.get("content") or "")
            if not content:
                continue
            look = {k: body[k] for k in ("font", "size", "color", "position") if body.get(k) is not None}
            key = json.dumps(look, sort_keys=True)
            if key not in looks:
                if not look:
                    looks[key] = "Hook"
                    styles.append(overlay._hook_style(canvas, hook_font))
                else:
                    looks[key] = f"Text{len(looks) + 1}"
                    styles.append(_text_style(looks[key], look, canvas, hook_font))
            start, end = item["start_us"] / model.US, (item["start_us"] + item["duration_us"]) / model.US
            place = text_place(look)
            if place is not None:
                # Dragged on the viewer (step 9): centred on its point, the
                # point kept on the frame.
                px = min(max(round(place[0] * canvas[0] / 100), 0), canvas[0])
                py = min(max(round(place[1] * canvas[1] / 100), 0), canvas[1])
                content = f"{{\\an5\\pos({px},{py})}}{content}"
            events.append(f"Dialogue: 1,{cap._ass_time(start)},{cap._ass_time(end)},{looks[key]},,0,0,0,,{content}")
    wm = _watermark(doc, inputs)
    if wm and wm_mod.has_text(wm):
        styles.append(wm_mod._text_style(wm, canvas))
        events.append(wm_mod._text_events(_ass_text(wm["text"]), wm, program_s, canvas))
    if styles:
        if text is None:
            text = overlay._MINIMAL_HEADER.format(w=canvas[0], h=canvas[1], style="\n".join(styles))
        else:
            text = text.replace("\n[Events]", "\n" + "\n".join(styles) + "\n\n[Events]", 1)
        text = text.rstrip("\n") + "\n" + "\n".join(events) + "\n"
    if text is None:
        return None, []
    return text, [f for f in bundled.fonts_in_ass(text) if bundled.is_bundled(f)]


def subtitles_filter(bundled_fonts: list[str]) -> str:
    """The burn of the plan's ASS file, from the stage's own folder: with
    `:fontsdir=fonts` only when a bundled font is used (step 3's rule; the
    runner stages the files there first)."""
    return f"subtitles={ASS_NAME}" + (":fontsdir=fonts" if bundled_fonts else "")


# ---- sounds on A2 and up ------------------------------------------------------------------------


def sound_items(doc: dict) -> list[tuple[dict, float]]:
    """(item, its track's volume) for every sound heard on A2 and up: not on
    a muted track, and only soloed tracks' when a track is soloed."""
    soloed = _soloed(doc)
    out = []
    for t in doc["tracks"]:
        if t["kind"] != "audio" or t["id"] == model.MAIN_AUDIO or t.get("mute") or (soloed and not t.get("solo")):
            continue
        out += [(item, float(t.get("volume", 1.0))) for item in sorted(t["items"], key=lambda i: i["start_us"])]
    return out


def bus_of(doc: dict, item: dict) -> str:
    """Where a sound is mixed: "voice" (a voice item: heard, and the music
    ducks under it as under A1), "duck_own" / "duck_free" (ducked under A1
    and the voices, with step 4's setting for the person's own file or for
    one of the app's free tracks), or "plain"."""
    if item.get("role") == "voice":
        return "voice"
    if item.get("duck"):
        source = ((doc.get("media") or {}).get(item["media_id"]) or {}).get("source") or {}
        return "duck_free" if source.get("type") == "library" else "duck_own"
    return "plain"


def _sound_mode(doc: dict, item: dict) -> str:
    """How a sound is read: "whole" (looped music that is the whole file,
    looped by FFmpeg as today's render loops a clip's music), "stretch"
    (looped music of a part of the file: aloop over that part), "once"."""
    if not item.get("loop"):
        return "once"
    length = int(((doc.get("media") or {}).get(item["media_id"]) or {}).get("duration_us") or 0)
    if item["source_in_us"] == 0 and (length == 0 or item["source_out_us"] >= length):
        return "whole"
    return "stretch"


def _sound_input(doc: dict, item: dict, media: MediaFile) -> list[str]:
    mode = _sound_mode(doc, item)
    if mode == "whole":
        return ["-stream_loop", "-1", "-i", media.path]
    span = item["source_out_us"] - item["source_in_us"]
    return ["-ss", _secs(item["source_in_us"]), "-t", _secs(span + READ_MARGIN_US), "-i", media.path]


def sound_graph(n: int, j: int, doc: dict, item: dict, volume: float) -> str:
    """Sound n (input j) placed on the timeline: looped from its
    loop_offset_us when it loops, at its speed, volume (its own times its
    track's), fades, delayed to its start (adelay in samples) and exactly
    its samples long. Labelled [s<n>]."""
    out_n = _samples(item)
    speed = float(item["speed"])
    need = int(out_n * speed) + AUDIO_RATE // 10
    parts = [f"[{j}:a]aresample={AUDIO_RATE}", "aformat=sample_fmts=fltp:channel_layouts=stereo", "asetpts=N/SR/TB"]
    mode = _sound_mode(doc, item)
    offset = int(item.get("loop_offset_us") or 0)
    if mode == "whole":
        length = int(((doc.get("media") or {}).get(item["media_id"]) or {}).get("duration_us") or 0)
        first = samples_at(offset % length if length else offset)
        parts += [f"atrim=start_sample={first}:end_sample={first + need}", "asetpts=N/SR/TB"]
    elif mode == "stretch":
        stretch = samples_at(item["source_out_us"] - item["source_in_us"])
        first = samples_at(offset) % max(1, stretch)
        parts += [f"atrim=end_sample={stretch}", f"aloop=loop=-1:size={stretch}",
                  f"atrim=start_sample={first}:end_sample={first + need}", "asetpts=N/SR/TB"]
    tempo = atempo_chain(speed)
    if tempo:
        parts += [*tempo, "asetpts=N/SR/TB"]
    gain = float(item.get("volume", 1.0)) * volume
    if abs(gain - 1.0) > 1e-9:
        parts.append(f"volume={_num(gain)}")
    parts += ["apad", f"atrim=end_sample={out_n}", *_fade_filters(item, out_n)]
    at = samples_at(item["start_us"])
    if at > 0:
        parts.append(f"adelay=delays={at}S|{at}S")
    return ",".join(parts) + f"[s{n}]"


def _sound_stage(sid: str, group: list[tuple[dict, float]], doc: dict, inputs: Inputs, program_samples: int,
                 ) -> tuple[Stage, dict[str, Path]]:
    """One stage mixing up to MAX_SOUND_INPUTS sounds into a 32-bit float
    WAV per bus, each exactly the program's samples long."""
    work = Path(inputs.work)
    args, graph, reads = [], [], []
    by_bus: dict[str, list[int]] = {}
    for n, (item, volume) in enumerate(group):
        media = inputs.media[item["media_id"]]
        args += _sound_input(doc, item, media)
        reads.append(media.path)
        graph.append(sound_graph(n, n, doc, item, volume))
        by_bus.setdefault(bus_of(doc, item), []).append(n)
    outs: dict[str, Path] = {}
    maps: list[str] = []
    temps: dict[str, str] = {}
    for bus in BUSES:
        if bus not in by_bus:
            continue
        graph.append(f"anullsrc=r={AUDIO_RATE}:cl=stereo,atrim=end_sample={program_samples}[z_{bus}]")
        members = by_bus[bus]
        graph.append(f"[z_{bus}]" + "".join(f"[s{n}]" for n in members)
                     + f"amix=inputs={len(members) + 1}:duration=first:normalize=0[o_{bus}]")
        path = work / f"{sid}-{bus}.wav"
        outs[bus] = path
        temps[str(path)] = str(temp_of(path))
        maps += ["-map", f"[o_{bus}]", "-c:a", "pcm_f32le", "-ar", str(AUDIO_RATE), "-ac", "2", str(temp_of(path))]
    script = work / f"{sid}.graph.txt"
    argv = [*_head(inputs), *args, script_flag(inputs.version), str(script), *maps]
    seconds = program_samples / AUDIO_RATE
    return Stage(id=sid, kind="ffmpeg", commands=[argv], reads=reads, writes=list(temps), temps=temps,
                 scripts={str(script): ";\n".join(graph) + "\n"}, seconds=seconds,
                 weight=WEIGHTS["sound"] * seconds), outs


def _sound_groups(doc: dict, sounds: list[tuple[dict, float]], inputs: Inputs) -> list[list[tuple[dict, float]]]:
    groups: list[list[tuple[dict, float]]] = []
    for entry in sounds:
        if entry[0]["media_id"] not in inputs.media:
            raise CompileError(f"The media of item {entry[0]['id']} wasn't found.")
        if groups and len(groups[-1]) < MAX_SOUND_INPUTS:
            args = [a for i, _v in [*groups[-1], entry] for a in _sound_input(doc, i, inputs.media[i["media_id"]])]
            if cmdline_length(args) <= ARGV_BUDGET // 2:
                groups[-1].append(entry)
                continue
        groups.append([entry])
    return groups


# ---- the final encode --------------------------------------------------------------------------


def _output_plan(preset: dict, canvas: dict, total_s: float, inputs: Inputs):
    """(export_presets.Plan or None for Original, the video block, ...)"""
    from video import export_presets

    if preset["kind"] == "original":
        return None
    from video import encoding

    enc = inputs.encoder
    if enc is None:
        family = encoding.EXPORT_ENCODERS[preset["codec"]]
        if "cpu" not in family:
            raise CompileError("This PC can't make AV1 videos: that needs a graphics card that encodes AV1.")
        enc = encoding.Encoder(preset["codec"], "cpu", family["cpu"])
    source = export_presets.Source(width=canvas["width"], height=canvas["height"], fps=_fps_float(canvas["fps"]),
                                   duration=max(0.1, total_s), has_audio=True)
    return export_presets.make_plan(source, preset, enc)


def a1_audible(doc: dict, items: list[dict], inputs: Inputs) -> bool:
    """Whether any V1 item can be heard: a video with sound, at a volume
    above 0, on an A1 that isn't muted (or out-soloed)."""
    gain = _a1_gain(doc)
    if gain <= 0:
        return False
    for item in items:
        media = inputs.media.get(item["media_id"])
        if media is not None and media.kind == "video" and media.has_audio and item.get("volume", 1.0) > 0:
            return True
    return False


# loudnorm needs 3 s of sound to measure: on a shorter program whose sound
# is digital silence it puts out NaN, which the AAC encoder refuses (fix
# 7-D10). Shorter programs get this guard after it (NaN or infinity -> 0).
LOUDNORM_MIN_S = 3.0
NAN_GUARD = "aeval='if(isnan(val(0))+isinf(val(0)),0,val(0))|if(isnan(val(1))+isinf(val(1)),0,val(1))':c=same"


def _final_audio(doc: dict, a1_input: int, program_samples: int,
                 buses: dict[str, list[int]] | None = None, *, audible: bool = True) -> list[str]:
    """The program's sound.

    - A1: V1's items' own sound (already at their volume, with their fades,
      mutes and bleeps, from the cut stages);
    - the sounds on A2 and up, premixed by bus in the sounds stages
      (`buses`: bus -> its WAVs' input numbers);
    - the ducked buses under A1 and the voices (sidechaincompress, step 4's
      settings: video_editor/export.py _DUCK_OWN for the person's own music,
      _DUCK_FREE for the app's free tracks);
    - everything summed (amix normalize=0, so nothing is turned down);
    - the master volume, then the app's loudness (encoding.LOUDNORM:
      -14 LUFS, true peak -1.5 dBTP) when the project asks for it and
      something can be heard (`audible` False with no buses: the program is
      silence, which loudnorm can't measure); under LOUDNORM_MIN_S, a guard
      after it turns a NaN into silence;
    - held to the program's samples."""
    from video.encoding import LOUDNORM

    audio = doc.get("audio") or {}
    tail = []
    master = float(audio.get("master_volume", 1.0))
    if abs(master - 1.0) > 1e-9:
        tail.append(f"volume={_num(master)}")
    if audio.get("normalize", True) and (audible or buses):
        tail.append(LOUDNORM)
        if program_samples < LOUDNORM_MIN_S * AUDIO_RATE:
            tail.append(NAN_GUARD)
    tail += [f"aresample={AUDIO_RATE}", "aformat=sample_fmts=fltp:channel_layouts=stereo", "apad",
             f"atrim=end_sample={program_samples}", "asetpts=N/SR/TB"]
    if not buses:
        return [",".join([f"[{a1_input}:a]asetpts=PTS-STARTPTS", *tail]) + "[pa]"]
    from video_editor.export import _DUCK_FREE, _DUCK_OWN

    fmt = f"aresample={AUDIO_RATE},aformat=sample_fmts=fltp:channel_layouts=stereo"
    out = [f"[{a1_input}:a]asetpts=PTS-STARTPTS,{fmt}[a1]"]
    for bus in BUSES:
        if bus not in buses:
            continue
        found = buses[bus]
        if len(found) == 1:
            out.append(f"[{found[0]}:a]asetpts=PTS-STARTPTS,{fmt}[x_{bus}]")
        else:
            out.append("".join(f"[{i}:a]" for i in found)
                       + f"amix=inputs={len(found)}:duration=first:normalize=0,{fmt}[x_{bus}]")
    ducked = [b for b in ("duck_own", "duck_free") if b in buses]
    mix = []
    if ducked:
        out.append("[a1]asplit=2[a1m][a1s]")
        mix.append("[a1m]")
        if "voice" in buses:
            out.append("[x_voice]asplit=2[vm][vs]")
            mix.append("[vm]")
            out.append("[a1s][vs]amix=inputs=2:duration=first:normalize=0[sc]")
        else:
            out.append("[a1s]anull[sc]")
        keys = [f"sc{n}" for n in range(len(ducked))]
        out.append("[sc]" + ("asplit=2" if len(ducked) == 2 else "anull") + "".join(f"[{k}]" for k in keys))
        for bus, key in zip(ducked, keys):
            setting = _DUCK_OWN if bus == "duck_own" else _DUCK_FREE
            out.append(f"[x_{bus}][{key}]sidechaincompress={setting}[d_{bus}]")
            mix.append(f"[d_{bus}]")
    else:
        mix.append("[a1]")
        if "voice" in buses:
            mix.append("[x_voice]")
    if "plain" in buses:
        mix.append("[x_plain]")
    out.append("".join(mix) + f"amix=inputs={len(mix)}:duration=first:normalize=0," + ",".join(tail) + "[pa]")
    return out


def _final_commands(*, inputs: Inputs, input_args: list[str], graph_video: list[str], graph_audio: list[str],
                    card_input: int | None, out_size: tuple[int, int], out_fps: str, vf: str | None,
                    video_args_for, passes: int, audio_kbps: int, codec: str, script: Path,
                    result: Path, card_tail: str | None = None) -> tuple[list[list[str]], dict[str, str]]:
    """The final stage's commands (one, or two for a 2-pass encode) and
    their filter scripts. `video_args_for(pass_no)` gives the -c:v block.
    `card_tail`: the program's last word's tail (tail_audio, labelled
    [ft]), heard under the start of the end card."""
    from video.encoding import final_movflags

    def graph(with_audio: bool) -> str:
        statements = list(graph_video)     # ends with the program's picture as [pre]
        last = "pv"
        chain = [vf] if vf else []
        chain.append("format=yuv420p")
        statements.append(f"[pre]{','.join(chain)}[{last}]")
        if with_audio:
            statements += graph_audio
        if card_input is not None:
            w, h = out_size
            statements.append(f"[{card_input}:v]setpts=PTS-STARTPTS,fps={out_fps},"
                              f"scale={w}:{h}:flags=lanczos,setsar=1,format=yuv420p[cv]")
            if with_audio:
                fmt = f"aresample={AUDIO_RATE},aformat=sample_fmts=fltp:channel_layouts=stereo"
                if card_tail:
                    statements.append(f"[{card_input}:a]asetpts=PTS-STARTPTS,{fmt}[ca0]")
                    statements.append(card_tail)
                    statements.append(f"[ca0][ft]amix=inputs=2:duration=first:normalize=0,{fmt}[ca]")
                else:
                    statements.append(f"[{card_input}:a]asetpts=PTS-STARTPTS,{fmt}[ca]")
                statements.append("[pv][pa][cv][ca]concat=n=2:v=1:a=1[vout][aout]")
            else:
                statements.append("[pv][cv]concat=n=2:v=1:a=0[vout]")
        else:
            statements.append("[pv]null[vout]")
            if with_audio:
                statements.append("[pa]anull[aout]")
        return ";\n".join(statements) + "\n"

    tag = ["-tag:v", "hvc1"] if codec == "hevc" else []
    scripts: dict[str, str] = {}
    commands = []
    if passes == 2:
        first = script.with_name(f"{script.stem}.pass1{script.suffix}")
        scripts[str(first)] = graph(False)
        commands.append([*_head(inputs), *input_args, script_flag(inputs.version), str(first),
                         "-map", "[vout]", *video_args_for(1), "-pix_fmt", "yuv420p", "-an", "-f", "null", "-"])
    scripts[str(script)] = graph(True)
    commands.append([*_head(inputs), *input_args, script_flag(inputs.version), str(script),
                     "-map", "[vout]", "-map", "[aout]", *video_args_for(2 if passes == 2 else None),
                     "-pix_fmt", "yuv420p", *tag, "-c:a", "aac", "-b:a", f"{audio_kbps}k", "-ar", str(AUDIO_RATE),
                     *final_movflags(), "-f", "mp4", str(temp_of(result))])
    return commands, scripts


# ---- compile ------------------------------------------------------------------------------------


def compile(doc: dict, revision: int, preset, purpose: str = "export", *, inputs: Inputs,
            at_us: int | None = None) -> RenderPlan:
    """The RenderPlan that makes `doc` (the project at `revision`) into a
    video with `preset` (an export preset of video/export_presets.py, or
    None / "original"). Pure: see the module's docstring. Raises
    CompileError when it can't be made.

    purpose:
    - "export": the finished video, with the preset and the end card;
    - "draft": the same program at DRAFT_SHORT_SIDE on its short side, x264
      ultrafast, no preset, no end card (the preset is ignored);
    - "still": one JPEG of the program at `at_us` (still_plan)."""
    from video import export_presets

    if purpose not in PURPOSES:
        raise CompileError(f"purpose is one of {', '.join(PURPOSES)}.")
    problems = model.validate(doc)
    if problems:
        raise CompileError(f"The project isn't valid: {problems[0]}")
    if purpose == "still":
        return still_plan(doc, revision, inputs, at_us)
    draft = purpose == "draft"
    caption_doc = doc
    if purpose in ("export", "draft"):
        # Apply's 0.4 s past a clip's end, so an unchanged clip's export is
        # as long as Apply's (step 8, ED34). The draft is the export's
        # program, so its AI runs are tracked once for both (the same cache
        # key). The document and the editor's timeline keep the clip's window.
        doc = with_end_pad(doc, end_pad_us(doc))
    try:
        preset = export_presets.normalize(None if draft else preset)
    except export_presets.PresetError as e:
        raise CompileError(str(e)) from e
    items = _main_items(doc)
    if not items:
        raise CompileError("The project has nothing on its main video track to export.")
    work = Path(inputs.work)
    fps = doc["canvas"]["fps"]
    full = (int(doc["canvas"]["width"]), int(doc["canvas"]["height"]))
    canvas = draft_size(*full) if draft else full
    program_us = model.program_end(doc)
    program_frames = model.frames_at(program_us, fps)
    program_samples = samples_at(program_us)
    notes: list[str] = []
    ai_ok = _ai_ok(doc)
    if not ai_ok and any(is_ai_item(doc, i) for i in items):
        notes.append("The AI framing (tracking, Gaming layouts) makes 9:16 pictures, so on this canvas its "
                     "items are fitted with a blurred pad until step 11.")
    gain = _a1_gain(doc)
    tail_of = tails(doc, items, transcript_words(inputs)) if gain > 0 else {}

    stages: list[Stage] = []
    parts: list[Path] = []
    sounds: list[Path] = []
    chunks = _chunks(doc, items, inputs, ai_ok, fps, canvas, tail_of)
    for n, chunk in enumerate(chunks, start=1):
        part, sound = work / f"v-{n:03d}.mp4", work / f"a-{n:03d}.wav"
        frames = sum(_frames(i, fps) for i in chunk.items)
        seconds = sum(i["duration_us"] for i in chunk.items) / model.US
        before = chunks[n - 2].items[-1] if n > 1 else None
        carried = (before, tail_of[before["id"]]) if before is not None and before["id"] in tail_of else None
        if chunk.kind == "manual":
            stages.append(_cut_stage(f"cut-{n:03d}", chunk, doc, inputs, fps, canvas, part, sound,
                                     tail_of=tail_of, carried=carried, draft=draft))
            sounds.append(sound)
        else:
            src, framed = work / f"src-{n:03d}.mp4", work / f"framed-{n:03d}.mp4"
            pieces = _ai_pieces(doc, chunk, inputs, fps, tail_of)
            if len(pieces) == 1:
                stages.append(_cut_stage(f"cut-{n:03d}", chunk, doc, inputs, fps, None, src, sound,
                                         tail_of=tail_of, carried=carried, draft=draft))
                sounds.append(sound)
            else:
                # A long run is cut in pieces as manual chunks are (at most
                # MAX_ITEM_INPUTS decoders, each command inside the budget),
                # then joined by stream copy into the one stretch the frame
                # stage tracks (step 7's fault, fixed in step 8: one command
                # of 64 inputs could pass Windows' limit, and took 2.2 GB).
                srcs = []
                for k, piece in enumerate(pieces, start=1):
                    piece_src, piece_sound = work / f"src-{n:03d}-{k:02d}.mp4", work / f"a-{n:03d}-{k:02d}.wav"
                    stages.append(_cut_stage(f"cut-{n:03d}-{k:02d}", piece, doc, inputs, fps, None, piece_src,
                                             piece_sound, tail_of=tail_of, carried=carried, draft=draft))
                    srcs.append(piece_src)
                    sounds.append(piece_sound)
                    end = piece.items[-1]
                    carried = (end, tail_of[end["id"]]) if end["id"] in tail_of else None
                stages.append(_join_stage(f"join-{n:03d}", inputs, srcs, src, seconds))
            params = _ai_params(doc, chunk, inputs, fps, src, framed)
            stages.append(Stage(id=f"frame-{n:03d}", kind="frame", reads=[str(src)], writes=[str(framed)],
                                temps={str(framed): str(temp_of(framed))}, seconds=seconds,
                                weight=WEIGHTS["frame"] * seconds, params=params))
            stages.append(_conform_stage(f"conform-{n:03d}", inputs, fps, canvas, frames, seconds, framed, part,
                                         draft=draft))
        parts.append(part)

    # The sounds on A2 and up, premixed by bus.
    bus_files: dict[str, list[Path]] = {}
    for g, group in enumerate(_sound_groups(doc, sound_items(doc), inputs), start=1):
        stage, outs = _sound_stage(f"sounds-{g:02d}", group, doc, inputs, program_samples)
        stages.append(stage)
        for bus, path in outs.items():
            bus_files.setdefault(bus, []).append(path)

    from video.encoding import concat_entry, final_movflags

    program_s = program_us / model.US
    if len(parts) == 1:
        joined = parts[0]
    else:
        joined = work / "program.mp4"
        listing = work / "join.txt"
        jt = temp_of(joined)
        stages.append(Stage(
            id="join", kind="join",
            commands=[[inputs.ffmpeg, "-y", "-v", "error", "-f", "concat", "-safe", "0", "-i", str(listing),
                       "-copyts", "-c", "copy", *final_movflags(), str(jt)]],
            reads=[str(p) for p in parts], writes=[str(joined)], temps={str(joined): str(jt)},
            scripts={str(listing): "".join(concat_entry(p.as_posix()) + "\n" for p in parts)},
            seconds=program_s, weight=WEIGHTS["join"] * program_s))
    sound_list = work / "a1.txt"
    sound_listing = "".join(concat_entry(p.as_posix()) + "\n" for p in sounds)

    # Overlays: as many as fit in the final command; the rest drawn onto the
    # joined picture first, in layer stages, in the order they stack.
    overlays = _overlays(doc)
    for item in overlays:
        if item["media_id"] not in inputs.media:
            raise CompileError(f"The media of item {item['id']} wasn't found.")
    if any(inputs.media[i["media_id"]].kind == "video" and inputs.media[i["media_id"]].has_audio for i in overlays):
        notes.append("The sound of videos on V2 and up is not heard: only V1's own sound and the audio tracks "
                     "are mixed.")
    groups: list[list[dict]] = []
    for item in overlays:
        if groups and len(groups[-1]) < MAX_OVERLAY_INPUTS:
            args = [a for i in [*groups[-1], item] for a in _overlay_input(i, inputs.media[i["media_id"]], fps)]
            if cmdline_length(args) <= ARGV_BUDGET // 2:
                groups[-1].append(item)
                continue
        groups.append([item])
    base = joined
    for g, group in enumerate(groups[:-1], start=1):
        layered = work / f"layer-{g:02d}.mp4"
        lt = temp_of(layered)
        args, graph, reads = ["-i", str(base)], ["[0:v]setpts=PTS-STARTPTS[b0]"], [str(base)]
        for n, item in enumerate(group, start=1):
            media = inputs.media[item["media_id"]]
            args += _overlay_input(item, media, fps)
            reads.append(media.path)
            graph += overlay_graph(n, n, item, media, fps, canvas, f"b{n - 1}", f"b{n}")
        graph.append(f"[b{len(group)}]format=yuv420p,trim=end_frame={program_frames}[vout]")
        script = work / f"layer-{g:02d}.graph.txt"
        argv = [*_head(inputs), *args, script_flag(inputs.version), str(script), "-map", "[vout]",
                *mezz_args(fps, draft), "-an", str(lt)]
        stages.append(Stage(id=f"layer-{g:02d}", kind="ffmpeg", commands=[argv], reads=reads,
                            writes=[str(layered)], temps={str(layered): str(lt)},
                            scripts={str(script): ";\n".join(graph) + "\n"}, seconds=program_s,
                            weight=WEIGHTS["layer"] * program_s))
        base = layered
    last_group = groups[-1] if groups else []

    # The export preset, on the program and the card together (a draft has
    # neither: its own encode, no card).
    card_us = 0 if draft else int(round((inputs.card_seconds or 0) * model.US))
    total_s = (program_us + card_us) / model.US
    plan = None if draft else _output_plan(preset, doc["canvas"], total_s, inputs)
    if plan is None:
        out_w, out_h, out_fps_text, vf = canvas[0], canvas[1], _fps_text(fps), None
        out_fps = _fps_float(fps)
        from video.encoding import CPU_ARGS

        render_args = list(DRAFT_ARGS) if draft else list(inputs.render_args or CPU_ARGS)
        passes, audio_kbps, codec = 1, 128, "h264"
        encoder_name = render_args[render_args.index("-c:v") + 1] if "-c:v" in render_args else "libx264"

        def video_args_for(_pass):
            return list(render_args)

        fallback_args = None if draft or render_args == CPU_ARGS else list(CPU_ARGS)
        max_bytes = None
    else:
        out_w, out_h, out_fps, vf = plan.width, plan.height, plan.fps, plan.vf
        out_fps_text = plan.fps_filter or _fps_text(fps)
        passes, audio_kbps, codec = plan.passes, plan.audio_kbps, plan.encoder.codec
        encoder_name = plan.encoder.name
        max_bytes = plan.max_bytes

        def video_args_for(pass_no, p=plan):
            return export_presets._video_args(p, pass_no, export_presets.PASSLOG)

        fallback_args = None
        if plan.upscale:
            notes.append("The export is larger than the project's canvas: the picture is enlarged.")

    # Inputs of the final command: the picture, A1, the overlays left, the
    # card, the logo, the sounds' buses, the last word's tail.
    result = work / ("draft.mp4" if draft else "export.mp4")
    input_args = ["-i", str(base), "-f", "concat", "-safe", "0", "-i", str(sound_list)]
    reads = [str(base), *[str(s) for s in sounds]]
    graph_video = ["[0:v]setpts=PTS-STARTPTS[b0]"]
    for n, item in enumerate(last_group, start=1):
        media = inputs.media[item["media_id"]]
        input_args += _overlay_input(item, media, fps)
        reads.append(media.path)
        graph_video += overlay_graph(n, n + 1, item, media, fps, canvas, f"b{n - 1}", f"b{n}")
    count = 2 + len(last_group)
    card_input = None
    card_path = work / "card.mp4"
    if card_us:
        card_input = count
        count += 1
        input_args += ["-i", str(card_path)]
        reads.append(str(card_path))
        stages.append(Stage(id="card", kind="card", writes=[str(card_path)],
                            temps={str(card_path): str(temp_of(card_path))},
                            params={"w": out_w, "h": out_h, "fps": round(out_fps, 3), "rate": out_fps_text,
                                    "timescale": 0, "pix_fmt": "yuv420p", "sample_rate": AUDIO_RATE,
                                    "channels": 2},
                            seconds=card_us / model.US, weight=WEIGHTS["card"]))
    # Captions, text and the text watermark (one ASS file), then the logo,
    # at the canvas's size, above the overlays and before the preset.
    ass_text, ass_fonts = build_ass(doc, inputs, full, caption_doc=caption_doc)
    post: list[str] = []
    top = f"b{len(last_group)}"
    if ass_text is not None:
        burn = subtitles_filter(ass_fonts)
        graph_video.append(f"[{top}]{burn}[sb]")
        top = "sb"
        post.append(burn)
    wm = _watermark(doc, inputs)
    if wm and wm.get("type") in ("image", "both") and inputs.logo:
        from video_editor.watermark import logo_graph

        input_args += ["-i", str(inputs.logo)]
        reads.append(str(inputs.logo))
        graph_video.append(logo_graph(wm, canvas, base=top, logo=f"{count}:v", tag="wm", out="lg"))
        count += 1
        top = "lg"
        post.append("image watermark: watermark.logo_graph")
    graph_video.append(f"[{top}]trim=end_frame={program_frames}[pre]")
    buses: dict[str, list[int]] = {}
    for bus in BUSES:
        for path in bus_files.get(bus, []):
            input_args += ["-i", str(path)]
            reads.append(str(path))
            buses.setdefault(bus, []).append(count)
            count += 1
    card_tail = None
    last = items[-1]
    if last["id"] in tail_of:
        media = inputs.media[last["media_id"]]
        if card_input is not None and media.has_audio:
            input_args += _tail_input(last, tail_of[last["id"]], media)
            reads.append(media.path)
            card_tail = tail_audio("ft", count, last, tail_of[last["id"]], gain, 0)
            count += 1
        else:
            notes.append("The program ends in the middle of its last word; without the end card there is "
                         "nothing to let it finish under, so it is cut where the timeline ends.")
    graph_audio = _final_audio(doc, 1, program_samples, buses, audible=a1_audible(doc, items, inputs))
    script = work / "final.graph.txt"
    common = {"inputs": inputs, "input_args": input_args, "graph_video": graph_video, "graph_audio": graph_audio,
              "card_input": card_input, "out_size": (out_w, out_h), "out_fps": out_fps_text, "codec": codec,
              "script": script, "result": result, "card_tail": card_tail, "audio_kbps": audio_kbps}
    commands, scripts = _final_commands(vf=vf, video_args_for=video_args_for, passes=passes, **common)
    scripts[str(sound_list)] = sound_listing
    if ass_text is not None:
        scripts[str(work / ASS_NAME)] = ass_text
    final = Stage(id="final", kind="ffmpeg", commands=commands, reads=reads, writes=[str(result)],
                  temps={str(result): str(temp_of(result))}, scripts=scripts, seconds=total_s,
                  weight=WEIGHTS["final"] * total_s * passes, cwd=str(work),
                  params={"fonts": ass_fonts} if ass_fonts else {})
    if fallback_args is not None:
        final.fallback = [[*c[:c.index("-c:v")], *fallback_args, *c[c.index("-c:v") + len(video_args_for(None)):]]
                          for c in commands]
    if plan is not None and plan.encoder.hardware:
        cpu = _output_plan(preset, doc["canvas"], total_s, Inputs(**{**inputs.__dict__, "encoder": None}))
        final.fallback, _ = _final_commands(
            vf=cpu.vf, video_args_for=lambda p, c=cpu: export_presets._video_args(c, p, export_presets.PASSLOG),
            passes=cpu.passes, **common)
    if plan is not None and plan.max_bytes:
        # The size target's one retry (export_presets.encode's rule): a file
        # made as without the limit that came out over is made again at the
        # limit's bitrate; one held to the limit, at 0.9x its bitrate.
        if plan.binds is False:
            again = export_presets._held(plan, max(plan.floor_kbps or 0, plan.limit_kbps or 0))
        else:
            again = dataclasses.replace(plan, video_kbps=int((plan.video_kbps or 0) * export_presets.RETRY_FACTOR))
        if again.video_kbps:
            retry_cmds, retry_scripts = _final_commands(
                vf=vf, video_args_for=lambda p, a=again: export_presets._video_args(a, p, export_presets.PASSLOG),
                passes=again.passes, **common)
            final.scripts.update(retry_scripts)
            final.retry = {"commands": retry_cmds, "video_kbps": int(again.video_kbps),
                           "max_bytes": int(plan.max_bytes)}
    stages.append(final)
    _check_lengths(stages)
    return RenderPlan(
        project_id=inputs.project_id, revision=int(revision), purpose=purpose, preset=preset,
        canvas={"width": full[0], "height": full[1], "fps": dict(fps)},
        output={"width": out_w, "height": out_h, "fps": round(out_fps, 3), "codec": codec, "encoder": encoder_name,
                "audio_kbps": audio_kbps, "max_bytes": max_bytes, "passes": passes},
        program_us=program_us, card_us=card_us, work=str(work), result=str(result), stages=stages,
        video_post=post, notes=notes)


def _ai_ok(doc: dict) -> bool:
    """Whether AI items are framed by the cropper: a 9:16 canvas and not a
    Vertical Live project."""
    return _nine_sixteen(doc["canvas"]) and not ((doc.get("context") or {}).get("job") or {}).get("vertical_live")


def _check_lengths(stages: list[Stage]) -> None:
    for stage in stages:
        for argv in [*stage.commands, *(stage.fallback or []), *((stage.retry or {}).get("commands") or [])]:
            if cmdline_length(argv) > CMDLINE_LIMIT:
                raise CompileError(f"Stage {stage.id}'s command is too long for Windows "
                                   f"({cmdline_length(argv)} characters).")


# ---- the still ---------------------------------------------------------------------------------


def still_frame(doc: dict, at_us: int) -> int:
    """The frame of the program on screen at `at_us` (held inside it): the
    last frame n whose start, model.frame_us(n) (rounded to the
    microsecond), is not after it, so still_frame(frame_us(n)) is n."""
    fps = doc["canvas"]["fps"]
    total = model.frames_at(model.program_end(doc), fps)
    n = (int(at_us) * fps["num"]) // (model.US * fps["den"])
    if model.frame_us(n + 1, fps) <= int(at_us):
        n += 1
    return max(0, min(total - 1, n))


def source_frame_time(entry: dict, media_fps: dict | None, frame: int, fps: dict, *,
                      overlay: bool = False) -> int | None:
    """Where to seek in an item's source so the still shows the source frame
    the export shows on program frame `frame`. The export's picture
    (_picture_head) keeps each source frame's offset from the in point:
    setpts=(PTS-pre/TB)/speed (plus the item's start for an overlay) and
    fps=<canvas>:start_time, whose rule is: each output frame gets the
    latest input frame whose time, rounded to the nearest output frame
    (halves away from 0), is not after it. So at 0.5x every source frame is
    shown twice and the still must take the earlier; at 2x every other one;
    and an in point between two source frames starts on the one before it.
    Returns a time half a source frame before that frame (-ss before -i then
    decodes from exactly it), or None when the source's frame rate isn't
    known."""
    import math

    try:
        src = media_fps["num"] / media_fps["den"]
    except (TypeError, KeyError, ZeroDivisionError):
        return None
    if src <= 0:
        return None
    out = _fps_float(fps)
    speed = float(entry["speed"])
    base = entry["start_us"] / model.US
    first_slot = model.frames_at(entry["start_us"], fps)
    source_in = entry["source_in_us"] / model.US

    def slot(j: int) -> int:        # source frame j (j / src seconds into the file); a tie (to float noise) goes up
        offset = (j / src - source_in) / speed
        if overlay:      # its times are the program's (+start/TB): rounded there
            return math.floor((base + offset) * out + 0.5 + 1e-6)
        return first_slot + math.floor(offset * out + 0.5 + 1e-6)     # a V1 item's own, from its start

    j = max(0, int((source_in + ((frame / out) - base) * speed) * src) + 2)
    while j > 0 and slot(j) > frame:
        j -= 1
    return max(0, int(round((j - 0.5) / src * model.US)))


def _item_at(items: list[dict], t_us: int) -> dict:
    return next((i for i in items if i["start_us"] <= t_us < i["start_us"] + i["duration_us"]), items[-1])


def still_run(doc: dict, inputs: Inputs, at_us: int) -> tuple[dict, int] | None:
    """For a still on an AI item: (the frame stage's params of the AI run it
    is in, exactly as an export plans it, and where that run starts on the
    timeline), so the caller can look its tracking up in the cache
    (read_cached_tracking). None when the item at that time isn't framed by
    the cropper."""
    t = model.frame_us(still_frame(doc, at_us), doc["canvas"]["fps"])
    doc = with_end_pad(doc, end_pad_us(doc))      # the run as the export and the draft track it (ED34)
    items = _main_items(doc)
    if not items or not _ai_ok(doc):
        return None
    fps = doc["canvas"]["fps"]
    canvas = (int(doc["canvas"]["width"]), int(doc["canvas"]["height"]))
    for chunk in _chunks(doc, items, inputs, True, fps, canvas):
        if chunk.kind == "ai" and any(i["start_us"] <= t < i["start_us"] + i["duration_us"] for i in chunk.items):
            return _ai_params(doc, chunk, inputs, fps, Path("src"), Path("framed")), chunk.items[0]["start_us"]
    return None


def _crop_9x16(color: str, canvas: tuple[int, int], crop: str) -> str:
    chain = [crop, "scale=1080:1920:flags=lanczos", "setsar=1", *([color] if color else [])]
    if canvas != (1080, 1920):
        chain.append(f"scale={canvas[0]}:{canvas[1]}:flags=lanczos")
    return ",".join(chain)


def _ai_still(item: dict, media: MediaFile, inputs: Inputs, canvas: tuple[int, int], t_us: int,
              notes: list[str]) -> list[str]:
    """An AI item's picture at one moment, as the cropper frames it, from
    the run's cached tracking (inputs.tracking):
    - a followed subject (a crop path, no zoom, bands or face-safe 4:5):
      the cropper's own 9:16 window at that moment (video/cropper.py
      _render_tracked: full height, centred on the path, held inside);
    - fit with a blurred backdrop (letterbox): the cropper's own graph;
    - anything else (no tracking cached yet, a Gaming layout, a sport's or a
      podcast's framing, zoom and bands): the middle 9:16 of the frame, and
      the plan's notes say the still is approximate.
    Labelled [g0]."""
    framing = item.get("framing") or {}
    color = _color_chain(item)
    held = inputs.tracking or {}
    tracking = held.get("tracking") if isinstance(held.get("tracking"), dict) else None
    sw, sh = int(media.width or 0), int(media.height or 0)
    plain = tracking is not None and not framing.get("gaming") and not any(
        tracking.get(k) for k in ("rows", "zoom", "band_top", "band_bottom"))
    face_safe = tracking is not None and tracking.get("face_y") is not None and float(tracking["face_y"]) < 0.25
    if plain and not face_safe and tracking.get("mode") not in ("split", "fit_blur") \
            and isinstance(tracking.get("path"), list) and tracking["path"] and sw and sh:
        crop_w = int(sh * 9 / 16)
        crop_w -= crop_w % 2
        crop_w = min(crop_w, sw)
        t_run = (t_us - int(held.get("run_start_us", item["start_us"]))) / model.US
        center = _interpolate([(float(p[0]), float(p[1])) for p in tracking["path"]], t_run) * sw
        x0 = max(0, min(sw - crop_w, int(round(center - crop_w / 2))))
        return [f"[0:v]{_crop_9x16(color, canvas, f'crop={crop_w}:{sh}:{x0}:0')}[g0]"]
    if (plain and tracking.get("mode") == "fit_blur") or (tracking is None and framing.get("crop") == "letterbox"):
        region = tracking.get("region") if tracking is not None else None
        x0, y0, x1, y1 = region or (0.0, 0.0, 1.0, 1.0)
        x0 = max(0.0, min(0.85, x0))
        x1 = max(x0 + 0.15, min(1.0, x1))
        y0 = max(0.0, min(0.85, y0))
        y1 = max(y0 + 0.15, min(1.0, y1))
        crop = f"crop=iw*{x1 - x0:.4f}:ih*{y1 - y0:.4f}:iw*{x0:.4f}:ih*{y0:.4f}"
        bg = (f"{crop},scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,scale=135:240,"
              f"gblur=sigma=12,scale=1080:1920:flags=bilinear")
        fg = f"{crop},scale=1080:-2:flags=lanczos,crop=w=1080:h=min(ih\\,1920)" + (f",{color}" if color else "")
        size = f",scale={canvas[0]}:{canvas[1]}:flags=lanczos" if canvas != (1080, 1920) else ""
        return ["[0:v]split=2[ga][gb]", f"[ga]{bg}[gbg]", f"[gb]{fg}[gfg]",
                f"[gbg][gfg]overlay=0:(H-h)/2,setsar=1{size}[g0]"]
    if not (tracking is None and framing.get("crop") == "center"):
        notes.append("This still is approximate: its AI framing isn't known until the item is tracked "
                     "(an export or a draft of the project tracks it), so it shows the middle of the frame.")
    return [f"[0:v]{_crop_9x16(color, canvas, 'crop=trunc(ih*9/32)*2:ih')}[g0]"]


def _interpolate(path: list[tuple[float, float]], t: float) -> float:
    """video/cropper.py _interpolate (that module needs OpenCV)."""
    import bisect

    if t <= path[0][0]:
        return path[0][1]
    if t >= path[-1][0]:
        return path[-1][1]
    i = bisect.bisect_left([p[0] for p in path], t)
    (t0, x0), (t1, x1) = path[i - 1], path[i]
    if t1 == t0:
        return x0
    return x0 + (x1 - x0) * (t - t0) / (t1 - t0)


def still_plan(doc: dict, revision: int, inputs: Inputs, at_us: int | None) -> RenderPlan:
    """One JPEG of the program at `at_us`, at the canvas's size, with every
    layer an export draws: V1's framing, colour and transform, the overlays
    on screen at that moment, the captions, text and watermarks. One FFmpeg,
    each input seeked to its own time (-ss before -i), the program's time
    given to the picture (setpts) so the ASS file and a moving logo are where
    the export has them, and -frames:v 1. The frame is the one the export
    shows at that time (still_frame)."""
    items = _main_items(doc)
    if not items:
        raise CompileError("The project has nothing on its main video track.")
    if at_us is None or not isinstance(at_us, int) or at_us < 0:
        raise CompileError("A still needs its time, t_us: a whole number of microseconds from 0.")
    fps = doc["canvas"]["fps"]
    canvas = (int(doc["canvas"]["width"]), int(doc["canvas"]["height"]))
    frame = still_frame(doc, at_us)
    t = model.frame_us(frame, fps)
    shift = f"+{_secs(t)}/TB"
    work = Path(inputs.work)
    notes: list[str] = []
    item = _item_at(items, t)
    media = inputs.media.get(item["media_id"])
    if media is None:
        raise CompileError(f"The media of item {item['id']} wasn't found.")

    def seek(entry: dict, m: MediaFile, overlay: bool = False) -> list[str]:
        if m.kind == "image":
            return ["-loop", "1", "-framerate", _fps_text(fps), "-t", "1", "-i", m.path]
        own = ((doc.get("media") or {}).get(entry["media_id"]) or {}).get("fps")
        at = source_frame_time(entry, own, frame, fps, overlay=overlay)
        if at is None:
            at = entry["source_in_us"] + int(round((t - entry["start_us"]) * float(entry["speed"])))
        return ["-ss", _secs(at), "-i", m.path]

    args = seek(item, media)
    reads = [media.path]
    graph: list[str] = []
    if _ai_ok(doc) and media.kind == "video" and is_ai_item(doc, item):
        graph += _ai_still(item, media, inputs, canvas, t, notes)
    else:
        graph.append("[0:v]setpts=PTS-STARTPTS[s0]")
        graph += framing_graph("s0", "f0", "i0", item.get("framing") or {}, canvas)
        color = _color_chain(item)
        tr = _transform(item)
        if _plain_transform(tr):
            graph.append(f"[f0]{color or 'null'}[g0]")
        else:
            x, y = _position(tr, canvas)
            graph.append(f"[f0]{','.join([*([color] if color else []), *transform_filters(tr)])}[t0]")
            graph.append(f"color=c=black:s={canvas[0]}x{canvas[1]}:r={_fps_text(fps)}[k0]")
            graph.append(f"[k0][t0]overlay=x={x}:y={y}:shortest=1[g0]")
    graph.append(f"[g0]format=yuv420p,setpts=PTS-STARTPTS{shift}[b0]")
    count, drawn = 1, 0
    for over in _overlays(doc):
        if not over["start_us"] <= t < over["start_us"] + over["duration_us"]:
            continue
        m = inputs.media.get(over["media_id"])
        if m is None:
            raise CompileError(f"The media of item {over['id']} wasn't found.")
        args += seek(over, m, overlay=True)
        reads.append(m.path)
        drawn += 1
        k = drawn
        graph.append(f"[{count}:v]setpts=PTS-STARTPTS{shift}[os{k}]")
        framing = over.get("framing") or {}
        graph += framing_graph(f"os{k}", f"of{k}", f"o{k}", framing, canvas, overlay=True)
        tr = _transform(over)
        chain = [c for c in (_color_chain(over),) if c] + ([] if _plain_transform(tr) else transform_filters(tr))
        graph.append(f"[of{k}]{','.join([*chain, 'format=yuva420p'])}[ot{k}]")
        x, y = _position(tr, canvas, framing.get("align", "center") if framing.get("fit") != "fill" else "center")
        graph.append(f"[b{k - 1}][ot{k}]overlay=x={x}:y={y}[b{k}]")
        count += 1
    top = f"b{drawn}"
    scripts: dict[str, str] = {}
    ass_text, ass_fonts = build_ass(doc, inputs, canvas)
    post: list[str] = []
    if ass_text is not None:
        burn = subtitles_filter(ass_fonts)
        graph.append(f"[{top}]{burn}[sb]")
        top = "sb"
        post.append(burn)
        scripts[str(work / ASS_NAME)] = ass_text
    wm = _watermark(doc, inputs)
    if wm and wm.get("type") in ("image", "both") and inputs.logo:
        from video_editor.watermark import logo_graph

        args += ["-i", str(inputs.logo)]
        reads.append(str(inputs.logo))
        graph.append(logo_graph(wm, canvas, base=top, logo=f"{count}:v", tag="wm", out="lg"))
        top = "lg"
        post.append("image watermark: watermark.logo_graph")
    graph.append(f"[{top}]scale=out_range=full,format=yuvj420p[vout]")
    result = work / "still.jpg"
    script = work / "still.graph.txt"
    scripts[str(script)] = ";\n".join(graph) + "\n"
    argv = [*_head(inputs), *args, script_flag(inputs.version), str(script), "-map", "[vout]", "-frames:v", "1",
            "-q:v", str(JPEG_QUALITY), "-update", "1", str(temp_of(result))]
    stage = Stage(id="still", kind="ffmpeg", commands=[argv], reads=reads, writes=[str(result)],
                  temps={str(result): str(temp_of(result))}, scripts=scripts, seconds=1 / _fps_float(fps),
                  weight=WEIGHTS["still"], cwd=str(work),
                  params={"t_us": t, "frame": frame, **({"fonts": ass_fonts} if ass_fonts else {})})
    _check_lengths([stage])
    return RenderPlan(
        project_id=inputs.project_id, revision=int(revision), purpose="still", preset={"kind": "original"},
        canvas={"width": canvas[0], "height": canvas[1], "fps": dict(fps)},
        output={"width": canvas[0], "height": canvas[1], "fps": round(_fps_float(fps), 3), "codec": "mjpeg",
                "encoder": "mjpeg", "audio_kbps": 0, "max_bytes": None, "passes": 1},
        program_us=model.program_end(doc), card_us=0, work=str(work), result=str(result), stages=[stage],
        video_post=post, notes=notes)


# ---- running a plan ------------------------------------------------------------------------------


class StageFailed(RuntimeError):
    """A stage of the plan failed; the message is FFmpeg's last line."""


class LocalTask:
    """A TaskContext for running a plan outside the editor lane (tests,
    scripts): FFmpeg through export_presets.run_ffmpeg, a cancel flag."""

    def __init__(self, on_progress=None):
        self.cancelled = False
        self.fraction = 0.0
        self.stages: list[str] = []
        self._on_progress = on_progress
        self._proc = None

    def run(self, argv, *, duration: float = 0.0, cwd=None, on_progress=None):
        from video import export_presets

        self.check()

        def started(proc):
            self._proc = proc
            if self.cancelled:
                proc.kill()

        code, err = export_presets.run_ffmpeg(argv, cwd=cwd, duration=duration, on_progress=on_progress,
                                              on_start=started)
        self._proc = None
        self.check()
        return code, err

    def check(self) -> None:
        if self.cancelled:
            from video import export_presets

            raise export_presets.Cancelled()

    def cancel(self) -> None:
        self.cancelled = True
        if self._proc is not None:
            try:
                self._proc.kill()
            except OSError:
                pass

    def progress(self, fraction: float, step: dict | None = None) -> None:
        self.fraction = max(self.fraction, fraction)
        if step and step.get("stage") and (not self.stages or self.stages[-1] != step["stage"]):
            self.stages.append(step["stage"])
        if self._on_progress is not None:
            self._on_progress(fraction, step or {})


def _remove(path: Path) -> None:
    try:
        Path(path).unlink(missing_ok=True)
    except OSError:
        pass


def _ffmpeg_error(err: str, code: int) -> str:
    lines = [ln for ln in (err or "").strip().splitlines() if ln.strip()]
    return lines[-1] if lines else f"FFmpeg stopped (exit code {code})."


def run_plan(plan: RenderPlan, task, *, config: dict | None = None) -> dict:
    """Run every stage of `plan` in order on `task` (a TaskContext: run,
    check, progress). Each stage writes its temporary names, which become
    the stage's files only when it finished; a failure or a cancel leaves
    none of a stage's files. Returns {"path", "bytes", "retried",
    "fell_back_to_cpu"}. The caller removes `plan.work` when it is done
    with the result."""
    work = Path(plan.work)
    work.mkdir(parents=True, exist_ok=True)
    total = sum(s.weight for s in plan.stages) or 1.0
    done = 0.0
    outcome = {"retried": False, "fell_back_to_cpu": False}
    for stage in plan.stages:
        task.check()
        for path, text in stage.scripts.items():
            Path(path).write_text(text, encoding="utf-8")
        if stage.params.get("fonts") and stage.cwd:
            _stage_fonts(stage.params["fonts"], Path(stage.cwd))
        span = (done / total, (done + stage.weight) / total)

        def report(f: float, span=span, stage=stage) -> None:
            task.progress(span[0] + (span[1] - span[0]) * max(0.0, min(1.0, f)), {"stage": stage.id})

        report(0.0)
        try:
            if stage.kind == "ffmpeg":
                _run_ffmpeg_stage(stage, task, report, outcome)
            elif stage.kind == "join":
                _run_ffmpeg_stage(stage, task, report, outcome)
            elif stage.kind == "frame":
                _run_frame(stage, config or {}, task)
            elif stage.kind == "card":
                _run_card(stage, config or {})
            else:
                raise StageFailed(f"Unknown stage kind {stage.kind!r}.")
            task.check()
            for final, temp in stage.temps.items():
                if not Path(temp).is_file() or Path(temp).stat().st_size <= 0:
                    raise StageFailed(f"Stage {stage.id} finished without writing {Path(final).name}.")
                os.replace(temp, final)
        finally:
            for temp in stage.temps.values():
                _remove(Path(temp))
        done += stage.weight
        report(1.0)
    result = Path(plan.result)
    return {"path": str(result), "bytes": result.stat().st_size, **outcome}


def _stage_fonts(families: list[str], folder: Path) -> None:
    """The bundled fonts the ASS file uses, copied into <folder>/fonts for
    the burn's fontsdir (video/fonts.py stage: only files that match their
    sha256). The folder is made even when a copy fails, so the burn still
    runs: libass then draws in another font, as step 3's renders do."""
    from video import fonts

    fonts.stage(families, folder)
    (folder / fonts.STAGED).mkdir(parents=True, exist_ok=True)


def _run_commands(commands: list[list[str]], stage: Stage, task, report) -> tuple[int, str]:
    n = len(commands)
    for i, argv in enumerate(commands):
        code, err = task.run(argv, duration=stage.seconds, cwd=stage.cwd,
                             on_progress=lambda f, i=i: report((i + f) / n))
        if code != 0:
            return code, err
    return 0, ""


def _run_ffmpeg_stage(stage: Stage, task, report, outcome: dict) -> None:
    code, err = _run_commands(stage.commands, stage, task, report)
    if code != 0 and stage.fallback:
        print(f"      Export: the graphics card refused stage {stage.id}; making it on the CPU instead")
        code, err = _run_commands(stage.fallback, stage, task, report)
        outcome["fell_back_to_cpu"] = code == 0
    if code != 0:
        raise StageFailed(_ffmpeg_error(err, code))
    if stage.retry:
        temp = Path(stage.temps[stage.writes[0]])
        size = temp.stat().st_size if temp.is_file() else 0
        if size > stage.retry["max_bytes"]:
            made_kbps = size * 8 / 1000 / max(stage.seconds, 0.1)
            if stage.retry["video_kbps"] < made_kbps:
                kept = temp.with_name(f"{temp.stem}.kept{temp.suffix}")
                os.replace(temp, kept)
                print(f"      Export: {size / 1e6:.1f} MB is over the limit; once more at "
                      f"{stage.retry['video_kbps']} kbps")
                code, err = _run_commands(stage.retry["commands"], stage, task, report)
                if code != 0:
                    _remove(temp)
                    os.replace(kept, temp)
                elif temp.stat().st_size >= size:
                    os.replace(kept, temp)        # made again it was no smaller: keep the first
                else:
                    _remove(kept)
                    outcome["retried"] = True


def _copy(src: Path, dst: Path) -> None:
    try:
        os.link(src, dst)
    except OSError:
        shutil.copyfile(src, dst)


def _run_card(stage: Stage, config: dict) -> None:
    from video import outro

    fmt = dict(stage.params)
    if not fmt.get("timescale"):
        num, _, den = str(fmt["rate"]).partition("/")
        fmt["timescale"] = int(num) if den and int(num) >= 1000 else int(round(float(fmt["fps"]) * 1000))
    card = outro.ensure_outro(fmt, config)
    _copy(Path(card), Path(stage.temps[stage.writes[0]]))


def tracking_key(params: dict, config: dict) -> str:
    """The cache name of a stretch's tracking: what was tracked and how."""
    tracking = (config or {}).get("tracking") or {}
    settings = {k: tracking.get(k) for k in ("detector", "sample_fps", "shot_aware", "shot_aware_no_subject")}
    text = json.dumps({**params["tracking_key"], "settings": settings}, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:32]


def _plain(value):
    """A tracking result as plain JSON: numpy numbers as Python ones,
    tuples as lists."""
    if isinstance(value, dict):
        return {str(k): _plain(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(v) for v in value]
    if isinstance(value, (bool, str)) or value is None:
        return value
    if isinstance(value, int):
        return int(value)
    try:
        number = float(value)
    except (TypeError, ValueError):
        return str(value)
    return int(number) if hasattr(value, "dtype") and str(getattr(value, "dtype", "")).startswith("int")         else number


def _tupled(tracking: dict) -> dict:
    """A tracking result read back from JSON: the path's points as tuples,
    as compute_tracking gives them."""
    out = dict(tracking)
    if isinstance(out.get("path"), list):
        out["path"] = [tuple(p) for p in out["path"]]
    if isinstance(out.get("region"), list):
        out["region"] = tuple(out["region"])
    return out


def tracking_config(config: dict | None) -> dict:
    """The config the frame stage tracks with (_run_frame): the settings'
    tracking, or the app's default detector."""
    cfg = dict(config or {})
    cfg.setdefault("tracking", {"detector": "yolov8n-pose.pt", "sample_fps": 8.0})
    return cfg


def read_cached_tracking(params: dict, config: dict | None) -> dict | None:
    """The tracking an export or a draft kept for a stretch (cached_tracker),
    or None when there is none yet. Reads only; never tracks."""
    folder = params.get("tracking_cache")
    if not folder:
        return None
    from video_editor.media import cache

    known = cache.read_json(Path(folder) / "tracking" / f"{tracking_key(params, tracking_config(config))}.json")
    if isinstance(known, dict) and isinstance(known.get("tracking"), dict):
        return known["tracking"]
    return None


def cached_tracker(params: dict, config: dict, compute=None, check=None):
    """compute_tracking, with its result kept in the media's cache folder
    (data/media_cache/<fingerprint>/tracking/<key>.json, the "tracking"
    product of video_editor/media/cache.py). Without a cache folder it is
    compute_tracking itself. `check` (the task's) runs once the tracking is
    known, so a cancel made while it was worked out stops the frame stage
    before the cropper's render (fix 7-D3)."""
    folder = params.get("tracking_cache")

    def track(clip_path, **kwargs):
        result = _track(clip_path, **kwargs)
        if check is not None:
            check()
        return result

    def _track(clip_path, **kwargs):
        nonlocal compute
        if compute is None:
            from video import tracker

            compute = tracker.compute_tracking
        if not folder:
            return compute(clip_path, **kwargs)
        from video_editor.media import cache

        products = Path(folder) / "tracking"
        target = products / f"{tracking_key(params, config)}.json"
        with cache.hold(Path(folder)):
            known = cache.read_json(target)
            if isinstance(known, dict) and isinstance(known.get("tracking"), dict) and \
                    known.get("force_fit_blur") == bool(kwargs.get("force_fit_blur")):
                cache.touch(products, now=None)
                return _tupled(known["tracking"])
            result = compute(clip_path, **kwargs)
            try:
                products.mkdir(parents=True, exist_ok=True)
                cache.write_json(target, {"force_fit_blur": bool(kwargs.get("force_fit_blur")),
                                          "tracking": _plain(result)})
                cache.write_json(products / "index.json", {"version": 1})
            except (OSError, TypeError, ValueError) as e:
                print(f"      Tracking not kept in the editor cache: {e}")
            return result

    return track


def _run_frame(stage: Stage, config: dict, task) -> None:
    """An AI run framed as a plain run frames a clip: frame_intermediate on
    the run's intermediate (already cut, at its speed), with the cut given as
    a copy of it and tracking from the cache."""
    from core.framing import frame_intermediate
    from core.models import ClipCandidate

    p = stage.params
    src = Path(p["source"])
    out = Path(stage.temps[stage.writes[0]])
    clip_dir = out.parent
    stem = Path(stage.writes[0]).stem

    def cut(_source, _candidate, output_path, **_kwargs):
        _copy(src, Path(output_path))
        return output_path

    cfg = {**config, "clips": {**(config.get("clips") or {}), "vertical": True, "whole": False}}
    cfg.setdefault("tracking", {"detector": "yolov8n-pose.pt", "sample_fps": 8.0})
    scratch: list[Path] = []
    try:
        task.check()
        frame_intermediate(
            src, ClipCandidate(start=0.0, end=float(p["seconds"]), score=0), out, clip_dir, stem,
            config=cfg, opts=dict(p["opts"]), edit=None, ass_path=None, vf_extra=p.get("vf_extra") or "",
            normalize=False, podcast=bool(p.get("podcast")), gaming=bool(p.get("gaming")),
            sport_name=p.get("sport_name"), sport_screen=p.get("sport_screen"), scratch=scratch,
            cut=cut, track=cached_tracker(p, cfg, check=task.check))
    finally:
        for path in scratch:
            _remove(Path(path))
