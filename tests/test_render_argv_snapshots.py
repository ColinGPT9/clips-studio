"""The FFmpeg work a render does, pinned down command by command.

The new editor (the editor suite) is built beside the render code that plain
auto-clipping runs through, and must not change what that code does by a
single argument. So each render here runs with FFmpeg, ffprobe, OpenCV's
decoder and the tracker faked (tests/render_harness.py), and what it would
have run is compared with a snapshot in tests/snapshots/render_argv/:

- every command line, with the .ass captions and concat listings it reads;
- the render options JSON the pipeline saves with the clip;
- the files left behind.

Each snapshot holds two runs: with the CPU encoder and with NVENC.

Two tiers:

- Light (nothing beyond the engine itself): video/cutter.py,
  video_editor/export.py, video_editor/watermark.py and video/encoding.py.
  These run in CI's Python job. The end card's join (video/outro.py) is in
  this tier too, but video/outro.py imports numpy and Pillow, so it runs
  where those are installed.
- Platform (numpy, OpenCV and Pillow): video/cropper.py's render_vertical in
  each of its modes, and core/pipeline.py's _render_files over the cases in
  tests/fixtures/render_opts/. These skip without them, and run in CI's Plugin
  platform job.

A snapshot changes only on purpose:

    python -m pytest tests/test_render_argv_snapshots.py --update-snapshots

and then the diff is reviewed. A missing snapshot fails; CI never records one.
"""

import copy
import json
import os
import subprocess
import sys
import types
from dataclasses import replace
from pathlib import Path

import pytest
from render_harness import ENCODERS, RenderHarness, check_snapshot

from core.models import ClipCandidate, Segment
from video import cutter, encoding
from video.captions import build_captions
from video_editor import export, watermark
from video_editor.overlay import ensure_hook
from video_editor.timeline import EditList

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = ROOT / "tests" / "fixtures" / "render_opts"
SNAPSHOTS = ROOT / "tests" / "snapshots" / "render_argv"

SHARED = json.loads((FIXTURES / "_shared.json").read_text(encoding="utf-8"))
SEGMENTS = [Segment(**s) for s in SHARED["segments"]]
WINDOW = ClipCandidate(**SHARED["candidate"])
# What the pipeline cuts for that window: 0.4 s past its end (core/pipeline.py END_PAD).
PADDED = replace(WINDOW, end=WINDOW.end + 0.4)
LOOK = "eq=saturation=1.35:contrast=1.08"          # the Vibrant preset (video/filters.py)


def _record(case: str, about: str, monkeypatch, tmp_path: Path, update: bool, run,
            canvas: tuple[int, int] = (1080, 1920)) -> None:
    """Run `run(harness, monkeypatch)` once per encoder, each in a folder and
    a monkeypatch context of its own, and compare what the two runs did with
    the case's snapshot. `run` returns anything else worth keeping."""
    snapshot: dict = {"about": about}
    for encoder in ENCODERS:
        with monkeypatch.context() as mp:
            harness = RenderHarness(mp, tmp_path / encoder, encoder, canvas)
            snapshot[encoder] = harness.result(**(run(harness, mp) or {}))
    check_snapshot(SNAPSHOTS / f"{case}.json", snapshot, update)


# ---- light tier: video/cutter.py ---------------------------------------------------------


def _cut_clip_staging(h, _mp):
    cutter.cut_clip(h.source(), PADDED, h.out("clip.source.mp4"))


def _cut_clip_final(h, _mp):
    ass = build_captions(SEGMENTS, WINDOW, h.root / "clip.ass", canvas=(1920, 1080))
    cutter.cut_clip(h.source(), PADDED, h.out("clip.mp4"), ass_path=ass, vf_extra=LOOK,
                    normalize=True, final=True)


def _cut_clip_gpu_retry(h, _mp):
    h.fail_next("ffmpeg")
    try:
        cutter.cut_clip(h.source(), PADDED, h.out("clip.mp4"), normalize=True, final=True)
    except RuntimeError as e:
        return {"raised": str(e).splitlines()[0]}
    return None


# ---- light tier: video_editor/export.py ----------------------------------------------------


def _edit(h, edit: dict, out: str = "clip.edited.mp4", **kwargs) -> None:
    """The editor's pass over a staging cut of the padded window."""
    clip = h.media("clip.source.mp4", duration=PADDED.duration)
    export.apply_edits(clip, EditList.from_dict(edit, duration=WINDOW.duration), h.root / out, **kwargs)


def _apply_edits_cuts(h, _mp):
    _edit(h, {"keep": [[0.0, 3.2], [4.6, 10.0]]})


def _apply_edits_mutes(h, _mp):
    _edit(h, {"mutes": [[1.0, 1.4], [6.2, 6.55]]})


def _apply_edits_speed(h, _mp):
    _edit(h, {"speed": 2.5})


def _apply_edits_volume_fades(h, _mp):
    h.step("louder, faded in and out, written as the clip")
    _edit(h, {"volume": 1.5, "fade_in": 0.5, "fade_out": 1.25}, "clip.mp4", normalize=True, final=True)
    h.step("muted throughout: no loudness pass")
    _edit(h, {"mute_all": True, "fade_out": 1.0}, "muted.mp4", normalize=True, final=True)


def _apply_edits_hook(h, _mp):
    hook = {"text": "Wait for the ending", "seconds": 3.0}
    ass = ensure_hook(None, h.root / "clip.ass", hook, canvas=(1920, 1080), font="Arial Black")
    plain = h.media("clip.plain.mp4", duration=PADDED.duration)
    export.apply_edits(plain, EditList.from_dict({"hook": hook}, duration=WINDOW.duration),
                       h.out("clip.mp4"), ass_path=ass, normalize=True, final=True)


def _apply_edits_music(h, _mp):
    music = h.media("music.mp3", duration=95.0)
    h.step("ducked under the voice, with cuts")
    _edit(h, {"keep": [[0.0, 4.0], [5.0, 10.0]], "music": {"path": str(music), "volume": 0.3, "duck": True}})
    h.step("not ducked, fading out, written as the clip")
    _edit(h, {"music": {"path": str(music), "volume": 0.5, "duck": False}, "fade_out": 1.0},
          "clip.mp4", normalize=True, final=True)


def _apply_edits_library_music(h, mp):
    """One of the app's free tracks (editor suite step 4): the bundled file is
    the music input. An id the app doesn't have, or a file that no longer
    matches its sha256, is a clip without music and a line in the job log."""
    import contextlib
    import io

    from video import audio_library

    said = io.StringIO()
    with contextlib.redirect_stdout(said):
        h.step("a free track, ducked under the voice, with cuts")
        _edit(h, {"keep": [[0.0, 4.0], [5.0, 10.0]],
                  "music": {"library": "music-roof-tops", "volume": 0.3, "duck": True}})
        h.step("a free track, not ducked, written as the clip")
        _edit(h, {"music": {"library": "music-doodles", "volume": 0.5, "duck": False}, "fade_out": 1.0},
              "clip.mp4", normalize=True, final=True)
        h.step("an id this copy of the app doesn't have: no music")
        _edit(h, {"music": {"library": "no-such-track", "volume": 0.3, "duck": True}}, "unknown.mp4")
        h.step("a damaged file: no music")
        mp.setattr(audio_library, "_matches", lambda path, sha256: False)
        _edit(h, {"music": {"library": "music-roof-tops", "volume": 0.3, "duck": True}}, "damaged.mp4")
    return {"job_log": said.getvalue().splitlines()}


# ---- light tier: video_editor/watermark.py -------------------------------------------------

TEXT_MARK = {"type": "text", "text": "@YourChannel", "position": "bottom_right"}
LOGO = {"type": "image", "image_asset": "logo.png"}


def _watermark_text(h, _mp):
    """Text branding is an ASS event burned with the captions; here it is
    burned by the one-encode cut, which is where a horizontal clip burns it."""
    source = h.source()
    h.step("alone, in a corner")
    ass = watermark.ensure_text(None, h.root / "alone.ass", TEXT_MARK, (1080, 1920), duration=WINDOW.duration)
    cutter.cut_clip(source, PADDED, h.root / "alone.mp4", ass_path=ass, normalize=True, final=True)
    h.step("moving, over captions")
    captions = build_captions(SEGMENTS, WINDOW, h.root / "captions.ass")
    moving = {**TEXT_MARK, "position": "moving", "font_size": 48, "opacity": 0.6, "shadow": False}
    ass = watermark.ensure_text(captions, h.root / "unused.ass", moving, (1080, 1920), duration=WINDOW.duration)
    cutter.cut_clip(source, PADDED, h.root / "moving.mp4", ass_path=ass, normalize=True, final=True)
    h.step("dragged and turned, on a landscape clip")
    dragged = {**TEXT_MARK, "position": "custom", "x": 0.3, "y": 0.8, "rotation": 12, "color": "#FFE600"}
    ass = watermark.ensure_text(None, h.root / "dragged.ass", dragged, (1920, 1080), duration=WINDOW.duration)
    cutter.cut_clip(source, PADDED, h.root / "dragged.mp4", ass_path=ass, normalize=True, final=True)


def _outro_stand_in(mp) -> None:
    """watermark.apply_image() puts its pass in the clip's place with
    video/outro.py's _replace_with_retry, and video/outro.py needs numpy and
    Pillow to import. Without them a plain rename stands in for it: it starts
    no process, so the commands are the same either way."""
    try:
        import video.outro  # noqa: F401
    except ImportError:
        def replace_file(src, dst, budget=0.0, label=""):
            Path(src).replace(dst)
            return True

        stand_in = types.ModuleType("video.outro")
        stand_in._replace_with_retry = replace_file
        mp.setitem(sys.modules, "video.outro", stand_in)


def _watermark_logo(h, mp):
    _outro_stand_in(mp)
    assets = h.root / "assets"
    h.write(assets / "logo.png", "a logo")
    clip = h.media("clip.mp4", w=1080, h=1920, duration=PADDED.duration)
    turned = {**LOGO, "position": "custom", "x": 0.25, "y": 0.7, "rotation": 15, "opacity": 0.6, "scale": 0.25}
    for label, cfg in (("in a corner", LOGO), ("moving", {**LOGO, "position": "moving"}),
                       ("dragged, turned and faded", turned)):
        h.step(label)
        watermark.apply_image(clip, cfg, (1080, 1920), assets)


# ---- light tier: video/outro.py and video/encoding.py -----------------------------------------


def _outro_finish(h, mp):
    pytest.importorskip("numpy")
    pytest.importorskip("PIL")
    from video import outro

    mp.setattr(outro, "_tally", {"added": 0, "skipped": 0})
    clips = h.root / "clips"
    src = h.media(clips / "clip_00010-00020.pre-card.mp4", w=1080, h=1920, duration=PADDED.duration)
    config = {"clips": {"outro": True}, "paths": {"data_dir": str(h.root / "data")}}
    return {"returned": outro.finish(src, clips / "clip_00010-00020.mp4", config)}


def _join(h, folder: Path, name: str, first_frame: float) -> None:
    """Join a clip and its card through a listing, as outro.append() writes one."""
    clip = h.media(folder / f"{name}.mp4", w=1080, h=1920, duration=PADDED.duration, first_frame=first_frame)
    card = h.media(folder / f"{name}.card.mp4", w=1080, h=1920, duration=2.9, first_frame=first_frame)
    listing = h.write(folder / f".{name}.outro.txt",
                      f"{encoding.concat_entry(clip.as_posix())}\n{encoding.concat_entry(card.as_posix())}\n")
    encoding.concat(listing, folder / f".{name}.joined.mp4")


def _encoding_concat(h, _mp):
    folder = h.root / "I'll be fine [abc123]"
    h.step("a clip and its card, from a folder whose name has a quote")
    _join(h, folder, "clip", first_frame=0.0)
    h.step("a part whose video starts late: the join as before is tried beside it")
    _join(h, folder, "late", first_frame=0.033)


def _encoding_args(h, mp):
    values = {
        "video_encoder_args()": encoding.video_encoder_args(),
        "using_hardware_encoder()": encoding.using_hardware_encoder(),
        "audio_filter_args(True)": encoding.audio_filter_args(True),
        "audio_filter_args(False)": encoding.audio_filter_args(False),
        "hwaccel_input_args()": encoding.hwaccel_input_args(),
        "final_movflags()": encoding.final_movflags(),
    }
    h.step("auto: each hardware encoder's start-up probe, until one works")
    mp.setattr(encoding, "_selected", None)
    values["auto"] = encoding.video_encoder_args()
    values["forced cpu"] = encoding.video_encoder_args({"video": {"encoder": "cpu"}})
    return {"values": values}


# ---- light tier: caption fonts, animations and safe zones (editor suite step 3) ---------------


def _cut_clip_bundled_font(h, _mp):
    """A bundled font: its file is staged beside the captions, and the burn
    is given fontsdir. Burned by the one-encode cut and by the editor's pass."""
    source = h.source()
    h.step("the one-encode cut, landscape")
    ass = build_captions(SEGMENTS, WINDOW, h.root / "clip.ass", style={"font": "Anton"}, canvas=(1920, 1080))
    cutter.cut_clip(source, PADDED, h.out("clip.mp4"), ass_path=ass, normalize=True, final=True)
    h.step("the editor's pass, with cuts")
    ass = build_captions(SEGMENTS, WINDOW, h.root / "edited.ass",
                         style={"font": "Poppins ExtraBold", "animation": "pop"}, canvas=(1920, 1080))
    clip = h.media("edited.source.mp4", duration=PADDED.duration)
    export.apply_edits(clip, EditList.from_dict({"keep": [[0.0, 3.2], [4.6, 10.0]]}, duration=WINDOW.duration),
                       h.root / "edited.mp4", ass_path=ass, normalize=True, final=True)


def _caption_animations(h, _mp):
    """Each animation's tags in the burned captions (a Windows font: no fontsdir)."""
    source = h.source()
    for animation in ("pop", "fade", "bounce", "karaoke", "slide"):
        h.step(animation)
        ass = build_captions(SEGMENTS, WINDOW, h.root / f"{animation}.ass", style={"animation": animation})
        cutter.cut_clip(source, PADDED, h.root / f"{animation}.mp4", ass_path=ass, normalize=True, final=True)
    h.step("pop over the word highlight: the first of each caption's events")
    ass = build_captions(SEGMENTS, WINDOW, h.root / "pop-highlight.ass", style={"animation": "pop", "highlight": True})
    cutter.cut_clip(source, PADDED, h.root / "pop-highlight.mp4", ass_path=ass, normalize=True, final=True)


def _caption_animation_handoffs(h, _mp):
    """Animations across events and lines: a short first word hands the
    animation on, pop and bounce write out the line breaks libass makes at
    100%, overlapping captions get no moving tag, and a bottom caption that
    slides in a safe zone stands clear of the bar from its first frame."""
    source = h.source()
    short = [{"start": 0.0, "end": 0.9, "text": "so impossible shot"}, {"start": 1.0, "end": 2.0, "text": "a buzzer"},
             {"start": 2.1, "end": 3.0, "text": "is overlapping"}, {"start": 2.6, "end": 3.4, "text": "these two"}]
    words = [Segment(start=10.0, end=13.4, text="so impossible shot a buzzer is overlapping these two", words=[
        {"start": 10.0, "end": 10.07, "word": "so"}, {"start": 10.07, "end": 10.5, "word": "impossible"},
        {"start": 10.5, "end": 10.9, "word": "shot"}, {"start": 11.0, "end": 11.05, "word": "a"},
        {"start": 11.05, "end": 12.0, "word": "buzzer"}])]
    window = ClipCandidate(start=10.0, end=13.4, score=0)
    padded = ClipCandidate(start=10.0, end=13.4, score=0)
    for animation in ("pop", "bounce", "slide", "fade"):
        h.step(f"{animation}, Titan One, word highlight, TikTok safe zone")
        ass = build_captions(words, window, h.root / f"{animation}.ass", lines=short,
                             style={"font": "Titan One", "font_size": 86, "animation": animation, "highlight": True,
                                    "safe_zone": "tiktok"})
        cutter.cut_clip(source, padded, h.root / f"{animation}.mp4", ass_path=ass, normalize=True, final=True)


def _caption_safe_zones(h, _mp):
    """Each app's safe zone: the captions' margins, at the bottom and at the top."""
    source = h.source()
    for zone in ("tiktok", "reels", "shorts"):
        for position in ("bottom", "top"):
            h.step(f"{zone}, {position}")
            name = f"{zone}-{position}"
            ass = build_captions(SEGMENTS, WINDOW, h.root / f"{name}.ass",
                                 style={"safe_zone": zone, "position": position})
            cutter.cut_clip(source, PADDED, h.root / f"{name}.mp4", ass_path=ass, normalize=True, final=True)


LIGHT = {
    "cut_clip_bundled_font": (
        "Editor suite step 3: captions in a bundled font, staged into fonts/ beside them and burned with "
        "fontsdir=fonts, by the one-encode cut and by the editor's pass.",
        _cut_clip_bundled_font),
    "caption_animations": (
        "Editor suite step 3: pop, fade, bounce, karaoke and slide as ASS override tags, and pop over the word "
        "highlight. Arial: the burn is as before.",
        _caption_animations),
    "caption_animation_handoffs": (
        "Editor suite step 3, QC fix round 1: animations across the word highlight's events (a short first word), "
        "line breaks written out for pop and bounce, no moving tag on overlapping captions, and slide in a safe zone. "
        "Titan One, a bundled font: fontsdir=fonts.",
        _caption_animation_handoffs),
    "caption_safe_zones": (
        "Editor suite step 3: TikTok, Reels and Shorts safe zones, bottom and top: the captions' margins.",
        _caption_safe_zones),
    "cut_clip_staging": (
        "video/cutter.py: the staging cut a vertical render crops from. No loudness pass, plain +faststart.",
        _cut_clip_staging),
    "cut_clip_final": (
        "video/cutter.py: a one-encode clip (horizontal, Vertical Live): captions burned from their folder, "
        "a colour look, loudness normalised, the final MP4 flags.",
        _cut_clip_final),
    "cut_clip_gpu_retry": (
        "video/cutter.py: the encode is refused. With a GPU encoder it is retried once on the CPU with the "
        "same command; with the CPU encoder the cut fails.",
        _cut_clip_gpu_retry),
    "apply_edits_cuts": ("video_editor/export.py: two kept sections, trimmed and joined.", _apply_edits_cuts),
    "apply_edits_mutes": ("video_editor/export.py: two muted sections (word mutes).", _apply_edits_mutes),
    "apply_edits_speed": ("video_editor/export.py: 2.5x, which takes two atempo filters.", _apply_edits_speed),
    "apply_edits_volume_fades": (
        "video_editor/export.py: volume and fades on the final timeline, loudness first.",
        _apply_edits_volume_fades),
    "apply_edits_hook": (
        "video_editor/overlay.py's hook title burned by the editor's pass on a horizontal clip.",
        _apply_edits_hook),
    "apply_edits_music": ("video_editor/export.py: background music, ducked and not.", _apply_edits_music),
    "apply_edits_library_music": (
        "Editor suite step 4: music from the app's free library (edit.music.library): the bundled file as the "
        "music input, ducked and not; an unknown id and a damaged file give a clip without music and a job log line.",
        _apply_edits_library_music),
    "watermark_text": (
        "video_editor/watermark.py: text branding as ASS events, alone, merged into captions, and dragged.",
        _watermark_text),
    "watermark_logo": (
        "video_editor/watermark.py: the logo's overlay pass, in a corner, moving, and dragged and turned.",
        _watermark_logo),
    "outro_finish": (
        "video/outro.py finish(): the end card joined onto a clip, the shipped card copied into the cache.",
        _outro_finish),
    "encoding_concat": ("video/encoding.py concat(): stream-copy joins through a listing.", _encoding_concat),
    "encoding_args": ("video/encoding.py: the argument blocks every render is built from.", _encoding_args),
}


@pytest.mark.parametrize("case", sorted(LIGHT))
def test_a_light_render_matches_its_snapshot(case, monkeypatch, tmp_path, update_snapshots):
    about, run = LIGHT[case]
    _record(case, about, monkeypatch, tmp_path, update_snapshots, run)


# ---- platform tier: video/cropper.py --------------------------------------------------------

PATH = [[0.0, 0.42], [3.0, 0.46], [6.5, 0.55], [10.4, 0.52]]
WEBCAM = [0.0, 0.62, 0.26, 0.38]

CROPPER = {
    "cropper_track": (
        "Following the subject: the 9:16 crop piped frame by frame into the encode, scaled to 1080x1920 there.",
        {"tracking": {"mode": "track", "path": PATH, "face_y": 0.34}}),
    "cropper_track_captions_look": (
        "The same, with captions and a colour look burned, and loudness switched off for this clip.",
        {"tracking": {"mode": "track", "path": PATH, "face_y": 0.34}, "ass": True, "vf_extra": LOOK,
         "normalize": False}),
    "cropper_face_safe": (
        "A face in the top quarter: the wider 4:5 crop under the platforms' top bar, on a blurred wash.",
        {"tracking": {"mode": "track", "path": PATH, "face_y": 0.12}, "ass": True}),
    "cropper_zoom": (
        "A crop that widens past 9:16 (a sport's zoom): each 1080x1920 frame composed in OpenCV.",
        {"tracking": {"mode": "track", "path": PATH, "zoom": [[0.0, 0.3164], [3.0, 0.6], [6.0, 0.3164]]}}),
    "cropper_bands": (
        "Rows kept and blurred bands above and below the picture (a sport's picture share).",
        {"tracking": {"mode": "track", "path": PATH, "rows": [0.0, 0.86], "band_top": 0.1, "band_bottom": 0.18}}),
    "cropper_fit_blur": (
        "Subjects too spread out to crop: their region at full width on a blurred backdrop, captions and a look.",
        {"tracking": {"mode": "fit_blur", "region": [0.18, 0.06, 0.82, 0.94]}, "ass": True, "vf_extra": LOOK}),
    "cropper_fit_blur_whole": (
        "Letterbox of the whole frame on a blurred backdrop.",
        {"tracking": {"mode": "fit_blur", "region": None}}),
    "cropper_split": (
        "The tracker's facecam layout: the webcam band over the game, captions burned.",
        {"tracking": {"mode": "split", "webcam_box": WEBCAM}, "ass": True}),
    "cropper_split_cam_bottom": (
        "The facecam layout with the webcam band at the bottom.",
        {"tracking": {"mode": "split", "webcam_box": WEBCAM}, "cam_position": "bottom"}),
    "cropper_track_bundled_font": (
        "Editor suite step 3: following the subject, captions in a bundled font that bounce, in the TikTok "
        "safe zone: the font staged beside them and fontsdir=fonts in the burn.",
        {"tracking": {"mode": "track", "path": PATH, "face_y": 0.34}, "ass": True,
         "style": {"font": "Bangers", "animation": "bounce", "safe_zone": "tiktok"}}),
}


def _tracking(spec: dict) -> dict:
    """A tracking result from JSON, with the tuples the tracker returns."""
    out = dict(spec)
    for key in ("path", "zoom"):
        if key in out:
            out[key] = [tuple(point) for point in out[key]]
    for key in ("region", "webcam_box", "rows"):
        if out.get(key) is not None:
            out[key] = tuple(out[key])
    return out


@pytest.mark.parametrize("case", sorted(CROPPER))
def test_a_vertical_render_matches_its_snapshot(case, monkeypatch, tmp_path, update_snapshots):
    pytest.importorskip("numpy")
    pytest.importorskip("cv2")
    from video.cropper import render_vertical

    about, spec = CROPPER[case]

    def run(h, _mp):
        clip = h.media("clip.source.mp4", duration=PADDED.duration)
        ass = build_captions(SEGMENTS, WINDOW, h.root / "clip.ass", style=spec.get("style")) if spec.get("ass") else None
        options = {k: spec[k] for k in ("vf_extra", "cam_position", "normalize") if k in spec}
        render_vertical(clip, _tracking(spec["tracking"]), h.out("clip.mp4"), ass_path=ass, **options)

    _record(case, about, monkeypatch, tmp_path, update_snapshots, run)


# ---- platform tier: core/pipeline.py _render_files ------------------------------------------

PIPELINE_CASES = (
    "plain", "captions", "word_highlight", "keep_range_cuts", "speed", "music", "hook",
    "text_watermark", "image_watermark", "filter_adjust", "letterbox", "center_crop",
    "landscape", "vertical_live", "gaming_layout",
)


def _case(name: str) -> dict:
    """A case's fixture laid over _shared.json: its config key by key, any
    other key replaced."""
    spec = copy.deepcopy(SHARED)
    for key, value in json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8")).items():
        spec[key] = _merged(spec["config"], value) if key == "config" else value
    return spec


def _merged(base: dict, over: dict) -> dict:
    out = dict(base)
    for key, value in over.items():
        out[key] = _merged(base[key], value) if isinstance(value, dict) and isinstance(base.get(key), dict) else value
    return out


def _here(value, root: Path):
    """A fixture's "<TMP>/..." strings as paths in this run's own folder."""
    if isinstance(value, str) and value.startswith("<TMP>/"):
        return str(root / value[len("<TMP>/"):])
    if isinstance(value, dict):
        return {k: _here(v, root) for k, v in value.items()}
    if isinstance(value, list):
        return [_here(v, root) for v in value]
    return value


def _fake_detection(h, detect, panels, spec: dict | None) -> None:
    """Person detection for the gaming layout. With `spec`, sample_tracks
    sees its people on every sampled frame and head_in_box finds its head.
    TalkNet, the clip's own webcam search and the chat-panel search are
    refused: no case should reach them, and none may load a model."""
    if spec:
        times = [i / spec["sample_fps"] for i in range(spec["samples"])]
        tracks = {}
        for tid, person in enumerate(spec["people"]):
            track = detect.Track()
            for t in times:
                track.times.append(t)
                track.person.append(tuple(person["box"]))
                track.seen.append((t, tuple(person["head"])))
                track.head_cx.append(person["head_cx"])
            tracks[tid] = track
        width, height = spec["frame"]
        h.fake(detect, "sample_tracks",
               (tracks, width, height, spec["video_fps"], spec["duration"], spec["samples"]))
        h.fake(detect, "head_in_box", tuple(spec["head_in_box"]))
    else:
        for name in ("sample_tracks", "head_in_box"):
            h.refuse(detect, name, "a detector would run")
    for name in ("speaking_scores", "clip_cam", "find_cam", "stills"):
        h.refuse(detect, name, "TalkNet or a detector would run")
    h.refuse(panels, "panels_in_video", "the chat-panel search would read the video")


@pytest.mark.parametrize("case", PIPELINE_CASES)
def test_a_pipeline_render_matches_its_snapshot(case, monkeypatch, tmp_path, update_snapshots):
    pytest.importorskip("numpy")
    pytest.importorskip("cv2")
    pytest.importorskip("PIL")      # the end card, video/outro.py
    from core import pipeline
    from gaming import detect, panels
    from gaming import run as gaming_run  # noqa: F401  imported before any fake goes in
    from video import outro, tracker

    spec = _case(case)

    def run(h, mp):
        mp.setattr(outro, "_tally", {"added": 0, "skipped": 0})
        source = h.source(**spec["source"])
        for rel, info in (spec.get("media") or {}).items():
            h.media(rel, **info)
        for rel, text in (spec.get("files") or {}).items():
            h.write(rel, text)
        config = _here(spec["config"], h.root)
        config["paths"] = {"data_dir": str(h.root / "data")}
        h.fake(tracker, "compute_tracking", _tracking(spec["tracking"]))
        _fake_detection(h, detect, panels, spec.get("gaming"))
        final, opts = pipeline._render_files(
            source, ClipCandidate(**spec["candidate"]), [Segment(**s) for s in spec["segments"]],
            h.root / "clips", config, _here(spec["render_opts"], h.root), spec["language"])
        return {"returned": final, "render_opts": opts}

    _record(case, spec["about"], monkeypatch, tmp_path, update_snapshots, run, tuple(spec["canvas"]))


# ---- the snapshots themselves ------------------------------------------------------------------


def test_every_fixture_is_a_pipeline_case():
    found = sorted(p.stem for p in FIXTURES.glob("*.json") if not p.name.startswith("_"))
    assert found == sorted(PIPELINE_CASES)


def test_every_case_has_a_snapshot_and_every_snapshot_a_case(update_snapshots):
    """Checked here too, in the tier that always runs: a platform case's
    snapshot must exist even where that tier skips."""
    if update_snapshots:
        pytest.skip("recording")
    cases = {*LIGHT, *CROPPER, *PIPELINE_CASES}
    have = {p.stem for p in SNAPSHOTS.glob("*.json")}
    missing, stray = sorted(cases - have), sorted(have - cases)
    assert not missing, f"cases without a snapshot (record them with --update-snapshots): {missing}"
    assert not stray, f"snapshots of no case (one renamed or removed?): {stray}"


def test_a_missing_snapshot_fails_and_is_not_created(tmp_path):
    path = tmp_path / "new_case.json"
    with pytest.raises(pytest.fail.Exception, match="No snapshot"):
        check_snapshot(path, {"cpu": {"commands": []}}, update=False)
    assert not path.exists()


def test_a_changed_command_fails_with_a_short_diff(tmp_path):
    path = tmp_path / "case.json"
    check_snapshot(path, {"cpu": {"commands": [{"argv": ["ffmpeg", "-crf", "20"]}]}}, update=True)
    with pytest.raises(pytest.fail.Exception) as failed:
        check_snapshot(path, {"cpu": {"commands": [{"argv": ["ffmpeg", "-crf", "21"]}]}}, update=False)
    message = str(failed.value)
    assert '-          "20"' in message and '+          "21"' in message
    assert "--update-snapshots" in message and len(message.splitlines()) < 30


# ---- the harness keeps its promises ------------------------------------------------------------

NOT_A_PROGRAM = "clips-kitty-harness-no-such-program"   # if a fake ever leaks, nothing real starts


def test_the_harness_refuses_every_other_program(monkeypatch, tmp_path):
    with monkeypatch.context() as mp:
        RenderHarness(mp, tmp_path)
        for start in (lambda: subprocess.run([NOT_A_PROGRAM, "--version"], capture_output=True),
                      lambda: subprocess.Popen([NOT_A_PROGRAM]),
                      lambda: subprocess.check_output([NOT_A_PROGRAM]),
                      lambda: os.system(NOT_A_PROGRAM)):
            with pytest.raises(pytest.fail.Exception):
                start()


def test_the_harness_puts_everything_back(monkeypatch, tmp_path):
    real = (subprocess.run, subprocess.Popen, os.system, encoding._selected)
    with monkeypatch.context() as mp:
        h = RenderHarness(mp, tmp_path, "nvenc")
        h.fake(encoding, "hwaccel_input_args", ["-hwaccel", "none"])
        kept = encoding.hwaccel_input_args       # as a module that imported it now would
        assert encoding.video_encoder_args()[1] == "h264_nvenc"
    assert (subprocess.run, subprocess.Popen, os.system, encoding._selected) == real
    assert kept() == ["-hwaccel", "auto"], "a stand-in kept past its harness calls the real thing"


def test_the_harness_shows_paths_the_same_on_every_system(monkeypatch, tmp_path):
    with monkeypatch.context() as mp:
        h = RenderHarness(mp, tmp_path)
        source = h.source()
        folder = tmp_path / "I'll be fine [abc]"
        assert h.norm(str(source)) == "<SRC>"
        assert h.norm(str(folder / "clip.mp4")) == "<TMP>/I'll be fine [abc]/clip.mp4"
        line = encoding.concat_entry((folder / "clip.mp4").as_posix())
        assert h.norm(line) == "file '<TMP>/I'\\''ll be fine [abc]/clip.mp4'"
        assert h.norm("crop=w=1080:h=min(ih\\,1920)") == "crop=w=1080:h=min(ih\\,1920)"
