"""The export compiler's plans (editor suite step 7, video_editor/compose.py),
compiled from project documents and compared with snapshots in
tests/snapshots/compose_plans/. Compiling runs no FFmpeg and no tracking,
so these run anywhere, the CI light install included.

The snapshots change only with --update-snapshots and a review, as the
step-0 render snapshots do (tests/test_render_argv_snapshots.py, whose
files are not touched by these):

    python -m pytest tests/test_compose_plan.py --update-snapshots

Paths are shown as <WORK> (the plan's folder) and <M:id> (a media file),
with "/" between folders, so a snapshot reads the same on Windows and Linux.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from project_helpers import FakeEnv
from render_harness import ROOT, check_snapshot

from video import encoding
from video_editor import compose
from video_editor.project import importer, model, ops

SNAPSHOTS = ROOT / "tests" / "snapshots" / "compose_plans"
CPU_H264 = encoding.Encoder("h264", "cpu", "libx264")


class Env(FakeEnv):
    """Every video is an hour of 1920x1080 at 30 fps with sound; an asset
    whose name starts "image" is an 800x600 picture."""

    def media(self, ref):
        if "video_id" in ref:
            return {"kind": "video", "source": {"type": "video", "video_id": ref["video_id"]},
                    "duration_us": 3_600_000_000, "width": 1920, "height": 1080, "fps": {"num": 30, "den": 1},
                    "has_audio": True, "name": ref["video_id"]}
        return super().media(ref)

    def caption_style(self, style: dict) -> dict:
        return dict(style)


ENV = Env()
FACTS = {"duration_us": 3_600_000_000, "width": 1920, "height": 1080, "fps": {"num": 30, "den": 1},
         "has_audio": True}


def apply(doc, *op_list):
    return ops.apply(doc, list(op_list), ENV).document


def v1(doc) -> list[dict]:
    return model.track(doc, model.MAIN_VIDEO)["items"]


def manual(*spans, fps=30, width=1080, height=1920) -> dict:
    """A manual project: video m1 (and picture m2), V1 the spans given as
    (source in s, source out s, speed)."""
    doc = model.new_project(name="Plan", width=width, height=height, fps=fps)
    doc = apply(doc, {"op": "add_media", "video_id": "video-a"}, {"op": "add_media", "asset": "image-one"})
    return apply(doc, *({"op": "insert_clip", "media_id": "m1", "source_in_us": int(a * 1e6),
                         "source_out_us": int(b * 1e6), "speed": s} for a, b, s in spans))


def from_clip(render_opts: dict, start: float = 100.0, end: float = 110.0) -> dict:
    """A project made from an AI clip (importer.import_clip)."""
    return importer.import_clip({"video_id": "video-a", "start": start, "end": end, "render_opts": render_opts},
                                FACTS)


def media_files(doc: dict, root: Path) -> dict:
    out = {}
    for mid, entry in doc["media"].items():
        ext = ".png" if entry["kind"] == "image" else ".mp4"
        out[mid] = compose.MediaFile(path=str(root / "media" / f"{mid}{ext}"), kind=entry["kind"],
                                     width=entry["width"], height=entry["height"],
                                     has_audio=entry["has_audio"],
                                     cache=str(root / "cache" / mid) if entry["kind"] == "video" else None)
    return out


PART_C = ("segments", "language", "caption_style", "job_captions", "watermark", "logo", "tracking")


def plan_for(doc: dict, root: Path, preset=None, purpose="export", at_us=None, **kw) -> compose.RenderPlan:
    extra = {k: kw.pop(k) for k in PART_C if k in kw}
    if extra.get("logo"):
        extra["logo"] = str(root / "media" / extra["logo"])
    inputs = compose.Inputs(work=root / "work", media=media_files(doc, root), ffmpeg="ffmpeg",
                            version=kw.pop("version", (8, 1)), encoder=kw.pop("encoder", None),
                            render_args=kw.pop("render_args", None), card_seconds=kw.pop("card", None),
                            project_id="0123456789abcdef", **extra)
    assert not kw
    return compose.compile(doc, 7, preset, purpose, inputs=inputs, at_us=at_us)


def normal(plan: compose.RenderPlan, root: Path) -> dict:
    """The plan as a snapshot shows it."""
    swaps = [(str(root / "work"), "<WORK>"), ((root / "work").as_posix(), "<WORK>"),
             (str(root / "cache"), "<CACHE>"), ((root / "cache").as_posix(), "<CACHE>")]
    for path in sorted((root / "media").glob("*")) if (root / "media").exists() else []:
        swaps.append((str(path), f"<M:{path.stem}>"))
    media = root / "media"
    swaps += [(str(media), "<MEDIA>"), (media.as_posix(), "<MEDIA>")]

    def fix(value):
        if isinstance(value, str):
            for old, new in swaps:
                value = value.replace(old, new)
            return value.replace("\\", "/")
        if isinstance(value, list):
            return [fix(v) for v in value]
        if isinstance(value, dict):
            return {fix(k): fix(v) for k, v in value.items()}
        return value

    return fix(plan.to_dict())


# ---- the cases ------------------------------------------------------------------------------------


def _one_item(_r):
    return manual((10, 14, 1.0)), {}


def _cuts(_r):
    return manual((10, 14, 1.0), (20, 22.5, 1.0), (5, 6, 1.0)), {}


def _speed(_r):
    return manual((10, 12, 0.5), (20, 24, 2.0)), {}


def _manual_fit_and_center(_r):
    doc = manual((10, 14, 1.0), (20, 24, 1.0), (30, 32, 1.0))
    a, b, c = (i["id"] for i in v1(doc))
    return apply(doc, {"op": "set_item_props", "item_id": a, "framing": {"fit": "fit", "align": "center",
                                                                          "pad": "#000000"}},
                 {"op": "set_item_props", "item_id": b, "framing": {"fit": "fill", "align": "center"}},
                 {"op": "set_item_props", "item_id": c, "framing": {"fit": "fit", "align": "top"},
                  "transform": {"scale": 0.5, "rotation": 15.0, "x": 0.1, "y": -0.05, "opacity": 0.8}}), {}


def _ai_crop(_r):
    return from_clip({"crop": "center", "edit": {"keep": [[0.0, 4.0], [6.0, 10.0]]}}), {}


def _ai_gaming(_r):
    return from_clip({"gaming": {"cam": [0.0, 0.0, 0.3, 0.3], "preset": "split"}}), {}


def _overlays(_r):
    doc = apply(manual((10, 18, 1.0)), {"op": "add_track", "kind": "video"})
    return apply(doc, {"op": "insert_clip", "media_id": "m2", "source_in_us": 0, "source_out_us": 2_000_000,
                       "track_id": "V2", "start_us": 1_000_000},
                 {"op": "insert_clip", "media_id": "m1", "source_in_us": 50_000_000, "source_out_us": 54_000_000,
                  "track_id": "V2", "start_us": 4_000_000, "speed": 2.0}), {}


def _overlay_looks(_r):
    doc, _ = _overlays(_r)
    pic, vid = model.track(doc, "V2")["items"]
    return apply(doc, {"op": "set_item_props", "item_id": pic["id"],
                       "transform": {"x": 0.25, "y": -0.3, "scale": 0.5, "opacity": 0.6}},
                 {"op": "set_item_props", "item_id": vid["id"], "framing": {"fit": "fill"},
                  "color": {"filter": "warm"}}), {}


def _colour(_r):
    doc = manual((10, 14, 1.0), (20, 22, 1.0))
    a, b = (i["id"] for i in v1(doc))
    return apply(doc, {"op": "set_item_props", "item_id": a, "color": {"filter": "vibrant"}},
                 {"op": "set_item_props", "item_id": b, "color": {"adjust": {"brightness": 0.1, "contrast": 1.2}}}), {}


def _end_card(_r):
    return manual((10, 14, 1.0)), {"card": 2.9}


def _preset_size(_r):
    return manual((10, 14, 1.0)), {"preset": {"kind": "custom", "resolution": "720p", "aspect": "same",
                                              "quality": "balanced", "codec": "h264", "encoder": "cpu"},
                                   "encoder": CPU_H264, "card": 2.9}


def _preset_aspect_blur(_r):
    return manual((10, 14, 1.0)), {"preset": {"kind": "custom", "resolution": "1080p", "aspect": "1:1",
                                              "fill": "blur", "codec": "h264", "encoder": "cpu"},
                                   "encoder": CPU_H264}


def _preset_aspect_bars(_r):
    return manual((10, 14, 1.0)), {"preset": {"kind": "custom", "resolution": "1080p", "aspect": "16:9",
                                              "fill": "bars", "bar_color": "white", "codec": "h264",
                                              "encoder": "cpu"}, "encoder": CPU_H264}


def _preset_aspect_crop(_r):
    return manual((10, 14, 1.0)), {"preset": {"kind": "custom", "resolution": "same", "aspect": "4:5",
                                              "fill": "crop", "codec": "h264", "encoder": "cpu", "fps": 24},
                                   "encoder": CPU_H264}


def _preset_hevc(_r):
    return manual((10, 14, 1.0)), {"preset": {"kind": "custom", "resolution": "same", "aspect": "same",
                                              "quality": "high", "codec": "hevc", "encoder": "cpu",
                                              "audio_kbps": 192},
                                   "encoder": encoding.Encoder("hevc", "cpu", "libx265")}


def _preset_size_target_cpu(_r):
    return manual((10, 70, 1.0)), {"preset": {"kind": "custom", "resolution": "same", "aspect": "same",
                                              "quality": "high", "codec": "h264", "encoder": "cpu", "max_mb": 10},
                                   "encoder": CPU_H264, "card": 2.9}


def _preset_size_target_gpu(_r):
    return manual((10, 70, 1.0)), {"preset": {"kind": "custom", "resolution": "same", "aspect": "same",
                                              "quality": "balanced", "codec": "h264", "encoder": "gpu",
                                              "max_mb": 10},
                                   "encoder": encoding.Encoder("h264", "nvenc", "h264_nvenc")}


def _original_on_a_graphics_card(_r):
    return manual((10, 14, 1.0)), {"render_args": list(encoding._CANDIDATES["nvenc"])}


def _ffmpeg_6(_r):
    return manual((10, 14, 1.0), (20, 22, 1.0)), {"version": (6, 1)}


def _landscape_canvas_with_ai_items(_r):
    doc = from_clip({"crop": "track"})
    return ops.apply(doc, [{"op": "set_canvas", "preset": "16:9"}], ENV).document, {}


def _sixty_fps_ntsc(_r):
    return manual((10, 12, 1.0), (20, 21, 1.0), fps=29.97), {}


# ---- part C: captions, text, branding, sound, draft, still -----------------------------------------

WORDS = [{"start": 10.2, "end": 10.5, "word": "one"}, {"start": 10.6, "end": 10.9, "word": "two"},
         {"start": 11.0, "end": 11.4, "word": "three"}, {"start": 12.1, "end": 12.6, "word": "four"},
         {"start": 13.7, "end": 13.95, "word": "five"}, {"start": 20.2, "end": 20.5, "word": "six"},
         {"start": 20.6, "end": 21.1, "word": "seven"}, {"start": 23.6, "end": 23.9, "word": "eight"}]
SEGMENTS = [{"start": 10.2, "end": 23.9, "text": " ".join(w["word"] for w in WORDS), "words": WORDS}]


def _text_and_hook(_r):
    doc = apply(manual((10, 14, 1.0)), {"op": "add_track", "kind": "text"})
    doc = apply(doc, {"op": "add_text", "content": "Wait for it", "start_us": 0, "duration_us": 2_000_000},
                {"op": "add_text", "content": "Second line\nand a break", "start_us": 2_500_000,
                 "duration_us": 1_000_000, "font": "Bangers", "size": 120, "color": "#ffcc00",
                 "position": "bottom"})
    first = model.track(doc, "T1")["items"][0]["id"]
    return apply(doc, {"op": "set_text", "item_id": first, "role": "hook"}), {}


def _captions_after_cuts_and_speed(_r):
    """The transcript's captions with the word highlight, through a cut
    (words in it dropped) and items at 0.5x and 2x."""
    doc = manual((10, 12, 1.0), (12.5, 14, 2.0), (20, 21.2, 0.5))
    return apply(doc, {"op": "set_captions", "enabled": True, "media_id": "m1",
                       "style": {"highlight": True}}), {"segments": SEGMENTS}


def _karaoke_saved_lines(_r):
    doc = manual((10, 11.5, 1.0), (20, 22, 2.0))
    return apply(doc, {"op": "set_captions", "enabled": True, "media_id": "m1",
                       "style": {"animation": "karaoke", "font": "Bangers"},
                       "lines": [{"in_us": 10_200_000, "out_us": 11_400_000, "text": "one two three"},
                                 {"in_us": 20_200_000, "out_us": 21_100_000, "text": "six seven"}]}), \
        {"segments": SEGMENTS}


def _music_looped_and_ducked(_r):
    doc = apply(manual((10, 14, 1.0), (20, 24, 1.0)), {"op": "add_media", "library": "music-one"})
    doc = apply(doc, {"op": "add_audio", "media_id": list(doc["media"])[-1], "track_id": "A2", "start_us": 0, "role": "music",
                      "loop": True, "duration_us": 8_000_000, "source_in_us": 5_000_000,
                      "source_out_us": 8_000_000, "duck": True, "volume": 0.25,
                      "fades": {"in_us": 500_000, "out_us": 1_000_000}})
    item = model.track(doc, "A2")["items"][0]
    return apply(doc, {"op": "split_item", "item_id": item["id"], "at_us": 2_000_000}), {}


def _sound_effects(_r):
    doc = apply(manual((10, 14, 1.0)), {"op": "add_media", "asset": "click"},
                {"op": "add_media", "asset": "voice"}, {"op": "add_track", "kind": "audio"})
    click, voice = list(doc["media"])[-2:]
    return apply(doc,
                 {"op": "add_audio", "media_id": click, "track_id": "A2", "start_us": 1_234_567, "role": "sfx",
                  "source_in_us": 0, "source_out_us": 300_000, "volume": 1.5},
                 {"op": "add_audio", "media_id": click, "track_id": "A2", "start_us": 3_000_000, "role": "sfx",
                  "source_in_us": 0, "source_out_us": 300_000},
                 {"op": "add_audio", "media_id": voice, "track_id": "A3", "start_us": 500_000, "role": "voice",
                  "source_in_us": 0, "source_out_us": 2_000_000}), {}


def _mutes_and_a_bleep(_r):
    doc = manual((10, 14, 1.0), (20, 22, 2.0))
    first = v1(doc)[0]["id"]
    return apply(doc, {"op": "set_captions", "media_id": "m1"},
                 {"op": "set_audio_mutes", "mutes": [{"in_us": 11_000_000, "out_us": 11_400_000, "mode": "bleep"},
                                                     {"in_us": 12_000_000, "out_us": 12_500_000, "mode": "mute"},
                                                     {"in_us": 20_500_000, "out_us": 21_000_000, "mode": "bleep"}]},
                 {"op": "set_item_props", "item_id": first, "fades": {"in_us": 250_000, "out_us": 500_000}}), {}


def _watermarks(_r):
    doc = manual((10, 14, 1.0))
    return apply(doc, {"op": "set_branding", "watermark": {"type": "both", "text": "@channel",
                                                           "position": "moving", "image_asset": "logo.png",
                                                           "opacity": 0.7}}), {"logo": "logo.png"}


def _tail_pad(_r):
    """Cuts right after "two" (11.0 s, its padded end 11.3 s, the next word
    at 11.0: no tail) and after "four" (12.7 s: its end 12.6 + 0.4 = 13.0,
    a 0.3 s tail under the next item), and the program's end after
    "seven" (21.2 s: a 0.3 s tail under the end card)."""
    doc = manual((10, 11.0, 1.0), (11.5, 12.7, 1.0), (20, 21.2, 1.0))
    return apply(doc, {"op": "set_captions", "media_id": "m1"}), {"segments": SEGMENTS, "card": 2.9}


def _draft(_r):
    doc, kw = _overlays(_r)
    return doc, {**kw, "purpose": "draft", "card": 2.9}


def _still_manual(_r):
    doc, _ = _overlay_looks(_r)
    doc = apply(doc, {"op": "set_captions", "enabled": True, "media_id": "m1"},
                {"op": "add_text", "content": "Look", "start_us": 0, "duration_us": 8_000_000})
    return doc, {"purpose": "still", "at_us": 4_510_000, "segments": [
        {"start": 50.0, "end": 52.0, "text": "over here", "words": [
            {"start": 50.2, "end": 50.6, "word": "over"}, {"start": 50.7, "end": 51.4, "word": "here"}]}]}


def _still_ai_tracked(_r):
    doc = from_clip({"crop": "track", "filter": "warm"})
    tracking = {"mode": "track", "path": [[0.0, 0.3], [5.0, 0.7]], "face_y": 0.4}
    return doc, {"purpose": "still", "at_us": 2_500_000,
                 "tracking": {"tracking": tracking, "run_start_us": 0}}


CASES = {
    "one_item": _one_item,
    "cuts": _cuts,
    "speed_half_and_double": _speed,
    "manual_fit_fill_transform": _manual_fit_and_center,
    "ai_crop_center": _ai_crop,
    "ai_gaming_layout": _ai_gaming,
    "overlays_picture_and_video": _overlays,
    "overlay_looks": _overlay_looks,
    "colour": _colour,
    "end_card": _end_card,
    "preset_size_720p": _preset_size,
    "preset_aspect_blur": _preset_aspect_blur,
    "preset_aspect_bars": _preset_aspect_bars,
    "preset_aspect_crop_24fps": _preset_aspect_crop,
    "preset_codec_hevc": _preset_hevc,
    "preset_size_target_cpu": _preset_size_target_cpu,
    "preset_size_target_gpu": _preset_size_target_gpu,
    "original_on_a_graphics_card": _original_on_a_graphics_card,
    "ffmpeg_6_filter_script": _ffmpeg_6,
    "landscape_canvas_ai_items": _landscape_canvas_with_ai_items,
    "ntsc_2997": _sixty_fps_ntsc,
    "text_and_hook": _text_and_hook,
    "captions_highlight_after_cuts_and_speed": _captions_after_cuts_and_speed,
    "captions_karaoke_saved_lines": _karaoke_saved_lines,
    "music_looped_and_ducked": _music_looped_and_ducked,
    "sound_effects_and_voice": _sound_effects,
    "mutes_and_a_bleep": _mutes_and_a_bleep,
    "watermark_text_and_image": _watermarks,
    "tail_pad": _tail_pad,
    "draft": _draft,
    "still_manual": _still_manual,
    "still_ai_tracked": _still_ai_tracked,
}


@pytest.mark.parametrize("case", sorted(CASES))
def test_a_plan_matches_its_snapshot(case, tmp_path, update_snapshots):
    doc, kw = CASES[case](tmp_path)
    preset = kw.pop("preset", None)
    plan = plan_for(doc, tmp_path, preset, **kw)
    check_snapshot(SNAPSHOTS / f"{case}.json", normal(plan, tmp_path), update_snapshots)


def test_every_snapshot_is_a_case(update_snapshots):
    if update_snapshots:
        return
    assert sorted(p.stem for p in SNAPSHOTS.glob("*.json")) == sorted(CASES)


# ---- what every plan holds to ------------------------------------------------------------------


def test_compiling_is_pure_and_the_same_twice(tmp_path):
    doc, kw = _overlay_looks(tmp_path)
    before = model.canonical(doc)
    one, two = plan_for(doc, tmp_path, **kw), plan_for(doc, tmp_path, **kw)
    assert json.dumps(one.to_dict()) == json.dumps(two.to_dict())
    assert model.canonical(doc) == before
    assert not (tmp_path / "work").exists()


def test_every_v1_chunk_has_exactly_its_frames_and_samples(tmp_path):
    doc = manual((10, 12.01, 1.0), (20, 21.37, 0.5), (30, 31, 2.0), fps=29.97)
    plan = plan_for(doc, tmp_path)
    script = next(iter(plan.stage("cut-001").scripts.values()))
    fps = doc["canvas"]["fps"]
    for item in v1(doc):
        frames = model.frames_at(item["start_us"] + item["duration_us"], fps) - model.frames_at(item["start_us"], fps)
        samples = compose.samples_at(item["start_us"] + item["duration_us"]) - compose.samples_at(item["start_us"])
        assert f"trim=end_frame={frames}," in script
        assert f"atrim=end_sample={samples}," in script
    total = compose.samples_at(model.program_end(doc))
    assert sum(compose.samples_at(i["start_us"] + i["duration_us"]) - compose.samples_at(i["start_us"])
               for i in v1(doc)) == total


@pytest.mark.parametrize("speed,chain", [(1.0, []), (2.0, ["atempo=2"]), (4.0, ["atempo=2", "atempo=2"]),
                                         (0.25, ["atempo=0.5", "atempo=0.5"]), (3.0, ["atempo=2", "atempo=1.5"]),
                                         (0.3, ["atempo=0.5", "atempo=0.6"])])
def test_speed_is_chained_atempo_each_within_half_and_double(speed, chain):
    assert compose.atempo_chain(speed) == chain


def test_the_filter_script_flag_follows_the_ffmpeg_version():
    assert compose.script_flag((6, 1)) == "-filter_complex_script"
    assert compose.script_flag((7, 0)) == "-filter_complex_script"
    assert compose.script_flag((7, 1)) == "-/filter_complex"
    assert compose.script_flag((8, 1)) == "-/filter_complex"
    assert compose.script_flag(None) == "-filter_complex_script"
    assert compose.parse_version("ffmpeg version 6.1.1-3ubuntu5 Copyright") == (6, 1)
    assert compose.parse_version("ffmpeg version 8.1.2-essentials_build-www.gyan.dev") == (8, 1)
    assert compose.parse_version("ffmpeg version n7.1 Copyright") == (7, 1)
    assert compose.parse_version("ffmpeg version N-124941-g54749da98a-20260610") == (99, 0)
    assert compose.parse_version("") is None


def test_an_ai_run_is_one_stretch_of_one_video_in_its_order(tmp_path):
    """Consecutive AI items of the same video, framing, look and speed, in
    the video's order, are one run (one tracking); a backwards jump starts
    another."""
    doc = from_clip({"crop": "track", "edit": {"keep": [[0.0, 3.0], [5.0, 10.0]]}})
    assert [s.id for s in plan_for(doc, tmp_path).stages] == ["cut-001", "frame-001", "conform-001", "final"]
    doc = ops.apply(doc, [{"op": "move_item", "item_id": v1(doc)[1]["id"], "index": 0}], ENV).document
    ids = [s.id for s in plan_for(doc, tmp_path).stages]
    assert ids == ["cut-001", "frame-001", "conform-001", "cut-002", "frame-002", "conform-002", "join", "final"]


def test_the_tracking_key_follows_what_is_tracked_and_how(tmp_path):
    doc = from_clip({"crop": "track"})
    params = plan_for(doc, tmp_path).stage("frame-001").params
    key = compose.tracking_key(params, {"tracking": {"detector": "yolov8n-pose.pt", "sample_fps": 8.0}})
    assert key == compose.tracking_key(params, {"tracking": {"detector": "yolov8n-pose.pt", "sample_fps": 8.0}})
    assert key != compose.tracking_key(params, {"tracking": {"detector": "yolov8n-pose.pt", "sample_fps": 4.0}})
    other = plan_for(from_clip({"crop": "track"}, end=111.0), tmp_path).stage("frame-001").params
    assert key != compose.tracking_key(other, {"tracking": {"detector": "yolov8n-pose.pt", "sample_fps": 8.0}})
    assert params["tracking_cache"].endswith("m1")


def test_an_unknown_purpose_is_refused():
    with pytest.raises(compose.CompileError, match="purpose"):
        compose.compile(manual((1, 2, 1.0)), 1, None, "poster", inputs=compose.Inputs(work=Path("w"), media={}))


def test_an_empty_project_or_a_missing_media_is_refused(tmp_path):
    with pytest.raises(compose.CompileError, match="nothing on its main video track"):
        plan_for(model.new_project(), tmp_path)
    doc = manual((1, 2, 1.0))
    with pytest.raises(compose.CompileError, match="wasn't found"):
        compose.compile(doc, 1, None, inputs=compose.Inputs(work=tmp_path, media={}))


def test_av1_without_a_graphics_card_is_refused(tmp_path):
    with pytest.raises(compose.CompileError, match="AV1"):
        plan_for(manual((1, 2, 1.0)), tmp_path, {"kind": "custom", "codec": "av1"})


# ---- the 300-item project ------------------------------------------------------------------------


def big_project() -> dict:
    """300 V1 items of a video (every third one sped up, every fifth one
    filled), 50 overlays on two tracks (pictures and video), and 10 sounds
    on A2, which part C mixes."""
    doc = model.new_project(name="Big", fps=30)
    doc = apply(doc, {"op": "add_media", "video_id": "video-a"}, {"op": "add_media", "asset": "image-one"},
                {"op": "add_media", "library": "music-one"}, {"op": "add_track", "kind": "video"},
                {"op": "add_track", "kind": "video"})
    v1_ops = []
    for n in range(300):
        at = n * 3_000_000
        v1_ops.append({"op": "insert_clip", "media_id": "m1", "source_in_us": at, "source_out_us": at + 2_000_000,
                       "speed": 2.0 if n % 3 == 0 else 1.0})
    doc = apply(doc, *v1_ops)
    props = [{"op": "set_item_props", "item_id": i["id"], "framing": {"fit": "fill"}}
             for n, i in enumerate(v1(doc)) if n % 5 == 0]
    doc = apply(doc, *props)
    over = []
    for n in range(50):
        track = "V2" if n % 2 == 0 else "V3"
        start = n * 8_000_000
        if n % 2 == 0:
            over.append({"op": "insert_clip", "media_id": "m2", "source_in_us": 0, "source_out_us": 1_500_000,
                         "track_id": track, "start_us": start})
        else:
            over.append({"op": "insert_clip", "media_id": "m1", "source_in_us": n * 10_000_000,
                         "source_out_us": n * 10_000_000 + 2_000_000, "track_id": track, "start_us": start})
    doc = apply(doc, *over)
    return apply(doc, *({"op": "add_audio", "media_id": "m3", "track_id": "A2", "start_us": n * 20_000_000,
                         "role": "sfx", "source_in_us": 0, "source_out_us": 1_000_000} for n in range(10)))


def test_three_hundred_items_and_fifty_overlays_fit_windows_command_lines(tmp_path):
    """Every command of the plan is under Windows' 32,767 characters, with
    a deep data folder: the plan splits V1 into chunks and draws overlays in
    layer stages when one command can't hold them."""
    deep = tmp_path / ("folder-with-a-long-name-" * 6) / ("and-another-" * 6)
    doc = big_project()
    assert len(v1(doc)) == 300
    plan = plan_for(doc, deep, card=2.9)
    longest = max(compose.cmdline_length(argv) for s in plan.stages for argv in s.commands)
    assert longest <= compose.CMDLINE_LIMIT
    cuts = [s for s in plan.stages if s.id.startswith("cut-")]
    layers = [s for s in plan.stages if s.id.startswith("layer-")]
    assert len(cuts) >= 300 // compose.MAX_ITEM_INPUTS and layers
    covered = []
    for stage in cuts:
        covered += [a for a, b in zip(stage.argv, stage.argv[1:]) if a == "-ss" and b]
    assert len(covered) == 300
    final = plan.stage("final")
    sounds = [s for s in plan.stages if s.id.startswith("sounds-")]
    assert len(sounds) == 1 and sounds[0].argv.count("-i") == 10
    # The final command's inputs: the picture, A1, the card, the sounds' one
    # bus, and the overlays left after the layer stages.
    assert final.argv.count("-i") <= compose.MAX_OVERLAY_INPUTS + 4
    drawn = sum(s.argv.count("-i") - 1 for s in layers) + final.argv.count("-i") - 4
    assert drawn == 50
    listing = plan.stage("join").scripts
    assert sum(text.count("file '") for text in listing.values()) == len(cuts)
    summary = {"stages": [s.id for s in plan.stages], "longest_command": "under the limit",
               "notes": plan.notes}
    assert summary["notes"] == ["The sound of videos on V2 and up is not heard: only V1's own sound and the "
                                "audio tracks are mixed."]


# ---- part C: the rules ---------------------------------------------------------------------------


def test_captions_follow_the_timeline_through_cuts_and_speed():
    """Each word plays where its middle plays: a word in a cut-out stretch is
    dropped, a line split by a cut is a caption on each side, speed scales
    the times, and the words' own times (the highlight's) are mapped too."""
    spans = [compose.CaptionSpan(10_000_000, 11_000_000, 0, 1_000_000, 1.0),
             compose.CaptionSpan(12_000_000, 14_000_000, 1_000_000, 2_000_000, 2.0)]
    lines = [{"start": 10.2, "end": 12.6, "text": "one two three four", "speaker": 1},
             {"start": 11.2, "end": 11.8, "text": "gone"}]
    out, spoken = compose.remap_captions(lines, WORDS, spans)
    assert out == [{"speaker": 1, "start": 0.2, "end": 1.0, "text": "one two"},
                   {"speaker": 1, "start": 1.0, "end": 1.3, "text": "four"}]
    assert spoken == [{"start": 0.2, "end": 0.5, "word": "one"}, {"start": 0.6, "end": 0.9, "word": "two"},
                      {"start": 1.05, "end": 1.3, "word": "four"}]


def test_a_stretch_played_twice_is_captioned_twice_and_a_hand_line_is_shared_by_length():
    spans = [compose.CaptionSpan(10_000_000, 12_000_000, 0, 2_000_000, 1.0),
             compose.CaptionSpan(10_000_000, 12_000_000, 2_000_000, 4_000_000, 1.0)]
    out, _ = compose.remap_captions([{"start": 10.0, "end": 12.0, "text": "aa bb"}], [], spans)
    assert [(line["start"], line["end"], line["text"]) for line in out] == [(0.0, 2.0, "aa bb"), (2.0, 4.0, "aa bb")]


def test_captions_follow_the_settings_the_project_and_the_job():
    doc = manual((10, 12, 1.0))
    inputs = compose.Inputs(work=Path("w"), media={})
    assert not compose.captions_on(doc, inputs)                     # made by hand, no choice: off
    on = apply(doc, {"op": "set_captions", "enabled": True})
    assert compose.captions_on(on, inputs)
    assert not compose.captions_on(on, compose.Inputs(work=Path("w"), media={}, job_captions=False))
    clip = from_clip({})
    assert compose.captions_on(clip, inputs)
    assert not compose.captions_on(from_clip({"job_captions": False}), inputs)


def test_the_tail_keeps_the_last_word_and_never_reaches_the_next():
    doc = manual((10, 11.0, 1.0), (11.5, 12.7, 1.0), (12.7, 13.0, 1.0), (20, 21.2, 1.0))
    doc = apply(doc, {"op": "set_captions", "media_id": "m1"})
    items = v1(doc)
    got = compose.tails(doc, items, WORDS)
    # "two" ends at 10.9: its pad reaches 11.3, but "three" starts at 11.0.
    # Item 2 goes on into item 3 (no cut): no tail there; item 3 ends at
    # 13.0 after "four" (12.6 + 0.4 = 13.0): nothing left to keep. "seven"
    # ends at 21.1: 0.3 s past the end.
    assert got == {items[3]["id"]: 300_000}
    muted = apply(doc, {"op": "set_audio_mutes", "mutes": [{"in_us": 21_100_000, "out_us": 21_300_000}]})
    assert compose.tails(muted, v1(muted), WORDS) == {}
    assert compose.tails(doc, items, []) == {}


def test_sounds_go_to_their_bus_and_loop_from_their_offset():
    doc, _ = _music_looped_and_ducked(None)
    left, right = model.track(doc, "A2")["items"]
    assert compose.bus_of(doc, left) == "duck_free" and right["loop_offset_us"] == 2_000_000
    graph = compose.sound_graph(1, 1, doc, right, 1.0)
    # The 3 s stretch (5 to 8 s of the track) looped, the right half going
    # on 2 s into it, where the left half stopped.
    assert "aloop=loop=-1:size=144000" in graph and "atrim=start_sample=96000:" in graph
    assert f"adelay=delays={compose.samples_at(right['start_us'])}S" in graph


def test_a_bleep_is_a_tone_in_the_muted_stretch():
    doc, _ = _mutes_and_a_bleep(None)
    item = v1(doc)[0]
    mutes = compose.item_mutes(doc, item)
    assert mutes == [(1.0, 1.4, "bleep"), (2.0, 2.5, "mute")]
    text = compose.item_audio(0, 0, item, compose.MediaFile("x.mp4"), 1.0, mutes)
    assert "volume=0:enable='between(t,1,1.4)+between(t,2,2.5)'" in text
    assert f"sine=frequency={compose.BLEEP_HZ}" in text and "not(between(t,1,1.4))" in text
    assert "afade=t=in:ss=0:ns=12000" in text and "afade=t=out:ss=168000:ns=24000" in text


def test_a_draft_is_small_quick_and_has_no_card_or_preset(tmp_path):
    assert compose.draft_size(1080, 1920) == (540, 960)
    assert compose.draft_size(1920, 1080) == (960, 540)
    assert compose.draft_size(1080, 1350) == (540, 676)
    assert compose.draft_size(360, 640) == (360, 640)
    plan = plan_for(manual((10, 14, 1.0)), tmp_path, {"kind": "custom", "resolution": "720p"}, purpose="draft",
                    card=2.9)
    assert (plan.output["width"], plan.output["height"]) == (540, 960) and plan.card_us == 0
    assert plan.preset["kind"] == "original" and plan.result.endswith("draft.mp4")
    final = plan.stage("final").argv
    assert final[final.index("-c:v"):final.index("-c:v") + 6] == compose.DRAFT_ARGS
    assert "ultrafast" in plan.stage("cut-001").argv


def test_a_still_is_the_frame_the_export_shows(tmp_path):
    doc = manual((10, 14, 1.0), (20, 22, 0.5))
    assert compose.still_frame(doc, 0) == 0 and compose.still_frame(doc, 33_332) == 0
    # Frame 1 starts at model.frame_us(1) = 33 333 µs: a time from frame_us is that frame.
    assert compose.still_frame(doc, 33_333) == 1 and compose.still_frame(doc, 10**12) == 239
    assert all(compose.still_frame(doc, model.frame_us(n, doc['canvas']['fps'])) == n for n in range(240))
    plan = plan_for(doc, tmp_path, purpose="still", at_us=5_000_000)
    stage = plan.stage("still")
    # Frame 150 is 1 s into the 0.5x item: source frame 615 of the 30 fps
    # video (20.5 s), sought half a frame before it.
    assert stage.params["t_us"] == 5_000_000 and stage.argv[stage.argv.index("-ss") + 1] == "20.483333"
    assert "-frames:v" in stage.argv and plan.result.endswith("still.jpg")
    with pytest.raises(compose.CompileError, match="t_us"):
        plan_for(doc, tmp_path, purpose="still")


def test_an_ai_still_without_tracking_says_it_is_approximate(tmp_path):
    plan = plan_for(from_clip({"crop": "track"}), tmp_path, purpose="still", at_us=1_000_000)
    assert plan.notes and "approximate" in plan.notes[0]
    graph = next(t for p, t in plan.stage("still").scripts.items() if p.endswith(".graph.txt"))
    assert "crop=trunc(ih*9/32)*2:ih" in graph
    centre = plan_for(from_clip({"crop": "center"}), tmp_path, purpose="still", at_us=1_000_000)
    assert centre.notes == []


def test_the_still_finds_the_same_tracking_the_export_keeps(tmp_path):
    doc = from_clip({"crop": "track", "edit": {"keep": [[0.0, 3.0], [5.0, 10.0]]}})
    inputs = compose.Inputs(work=tmp_path / "w", media=media_files(doc, tmp_path))
    params, start = compose.still_run(doc, inputs, 4_000_000)
    exported = plan_for(doc, tmp_path).stage("frame-001").params
    config = {"tracking": {"detector": "yolov8n-pose.pt", "sample_fps": 8.0}}
    assert start == 0 and compose.tracking_key(params, config) == compose.tracking_key(exported, config)
    assert compose.read_cached_tracking(params, config) is None
    assert compose.still_run(manual((1, 2, 1.0)), compose.Inputs(work=tmp_path, media=media_files(
        manual((1, 2, 1.0)), tmp_path)), 0) is None
