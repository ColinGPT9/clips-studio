"""The export compiler's plans run with real FFmpeg (editor suite step 7,
video_editor/compose.py), on short sources generated here. Skips where FFmpeg
is missing (the CI light install); the Plugin platform job runs it.

- The export lasts exactly the program plus the end card (to a frame).
- Two CPU exports of the same project are the same, frame for frame and
  sample for sample (framemd5, the same FFmpeg in the same test).
- Every stage writes under a temporary name: a failed stage and a cancel
  leave no stage's file and no partial file.
"""

from __future__ import annotations

import subprocess
import threading
from pathlib import Path

import pytest
from project_helpers import FakeEnv

from video_editor import compose
from video_editor.project import model, ops


def _ffmpeg() -> tuple[str, str]:
    from core.binaries import ffmpeg, ffprobe

    try:
        binary, probe = ffmpeg(), ffprobe()
        subprocess.run([binary, "-version"], capture_output=True, check=True, timeout=30)
        subprocess.run([probe, "-version"], capture_output=True, check=True, timeout=30)
    except (OSError, subprocess.SubprocessError, FileNotFoundError):
        pytest.skip("FFmpeg isn't installed")
    return binary, probe


@pytest.fixture(scope="module")
def tools():
    return _ffmpeg()


@pytest.fixture(scope="module")
def source(tools, tmp_path_factory):
    """8 seconds of 640x360 at 30 fps (a moving test picture) with a tone."""
    folder = tmp_path_factory.mktemp("media")
    path = folder / "source.mp4"
    subprocess.run([tools[0], "-hide_banner", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=s=640x360:r=30:d=8",
                    "-f", "lavfi", "-i", "sine=f=440:r=48000:d=8", "-c:v", "libx264", "-preset", "ultrafast",
                    "-g", "30", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(path)], check=True, timeout=120)
    picture = folder / "picture.png"
    subprocess.run([tools[0], "-hide_banner", "-v", "error", "-y", "-f", "lavfi", "-i",
                    "color=c=red@0.5:s=200x100,format=rgba", "-frames:v", "1", str(picture)], check=True, timeout=60)
    return path, picture


class Env(FakeEnv):
    def media(self, ref):
        if "video_id" in ref:
            return {"kind": "video", "source": {"type": "video", "video_id": ref["video_id"]},
                    "duration_us": 8_000_000, "width": 640, "height": 360, "fps": {"num": 30, "den": 1},
                    "has_audio": True, "name": "v"}
        return {"kind": "image", "source": {"type": "asset", "sha256": ref["asset"]}, "duration_us": 0,
                "width": 200, "height": 100, "fps": None, "has_audio": False, "name": "picture"}


ENV = Env()


def project(width=360, height=640) -> dict:
    """Three cuts of the source (the second at double speed, the third at
    half), framed three ways, and a picture over part of it on V2."""
    doc = model.new_project(name="Render", width=width, height=height, fps=30)
    doc = ops.apply(doc, [{"op": "add_media", "video_id": "v"}, {"op": "add_media", "asset": "image-a"},
                          {"op": "add_track", "kind": "video"}], ENV).document
    doc = ops.apply(doc, [
        {"op": "insert_clip", "media_id": "m1", "source_in_us": 1_000_000, "source_out_us": 2_000_000},
        {"op": "insert_clip", "media_id": "m1", "source_in_us": 4_000_000, "source_out_us": 5_000_000, "speed": 2.0},
        {"op": "insert_clip", "media_id": "m1", "source_in_us": 6_000_000, "source_out_us": 6_500_000, "speed": 0.5},
        {"op": "insert_clip", "media_id": "m2", "source_in_us": 0, "source_out_us": 600_000, "track_id": "V2",
         "start_us": 300_000},
    ], ENV).document
    a, b, _c = model.track(doc, "V1")["items"]
    return ops.apply(doc, [{"op": "set_item_props", "item_id": a["id"], "framing": {"fit": "fill"}},
                           {"op": "set_item_props", "item_id": b["id"], "framing": {"fit": "fit", "pad": "#202020"},
                            "transform": {"scale": 0.8, "rotation": 5.0}}], ENV).document


def compiled(doc, tools, source, work: Path, *, card=None, preset=None) -> compose.RenderPlan:
    path, picture = source
    inputs = compose.Inputs(work=work, media={"m1": compose.MediaFile(str(path), "video", 640, 360, True),
                                              "m2": compose.MediaFile(str(picture), "image", 200, 100, False)},
                            ffmpeg=tools[0], version=compose.ffmpeg_version(tools[0]), card_seconds=card)
    return compose.compile(doc, 1, preset, "export", inputs=inputs)


def streams(probe: str, path) -> dict:
    out = subprocess.run([probe, "-v", "error", "-show_entries", "stream=codec_type,duration,nb_frames,width,height",
                          "-of", "json", str(path)], capture_output=True, text=True, check=True).stdout
    import json

    return {s["codec_type"]: s for s in json.loads(out)["streams"]}


def framemd5(binary: str, path) -> str:
    return subprocess.run([binary, "-hide_banner", "-v", "error", "-i", str(path), "-map", "0", "-f", "framemd5", "-"],
                          capture_output=True, text=True, check=True).stdout


def parts_left(folder: Path) -> list[str]:
    return sorted(p.name for p in folder.rglob("*") if ".part" in p.name)


def test_the_export_lasts_the_program_and_nothing_else(tools, source, tmp_path):
    doc = project()
    plan = compiled(doc, tools, source, tmp_path / "work")
    result = compose.run_plan(plan, compose.LocalTask(), config={})
    got = streams(tools[1], result["path"])
    program = model.program_end(doc) / model.US
    frame = 1 / 30
    assert abs(float(got["video"]["duration"]) - program) <= frame
    assert abs(float(got["audio"]["duration"]) - program) <= frame
    assert int(got["video"]["nb_frames"]) == model.frames_at(model.program_end(doc), doc["canvas"]["fps"])
    assert (int(got["video"]["width"]), int(got["video"]["height"])) == (360, 640)
    assert parts_left(tmp_path) == []


def test_the_export_lasts_the_program_plus_the_end_card(tools, source, tmp_path):
    """At 1080x1920 30 fps, the size whose card ships with the app."""
    pytest.importorskip("numpy")
    pytest.importorskip("PIL")
    doc = ops.apply(project(), [{"op": "set_canvas", "preset": "9:16"}], ENV).document
    plan = compiled(doc, tools, source, tmp_path / "work", card=2.9)
    result = compose.run_plan(plan, compose.LocalTask(), config={"paths": {"data_dir": str(tmp_path / "data")}})
    card = streams(tools[1], tmp_path / "work" / "card.mp4")
    got = streams(tools[1], result["path"])
    expected = model.program_end(doc) / model.US + float(card["video"]["duration"])
    assert abs(float(got["video"]["duration"]) - expected) <= 1 / 30
    assert abs(float(got["audio"]["duration"]) - expected) <= 1 / 30


def test_two_cpu_exports_of_one_project_are_the_same(tools, source, tmp_path):
    doc = project()
    one = compose.run_plan(compiled(doc, tools, source, tmp_path / "one"), compose.LocalTask(), config={})
    two = compose.run_plan(compiled(doc, tools, source, tmp_path / "two"), compose.LocalTask(), config={})
    assert framemd5(tools[0], one["path"]) == framemd5(tools[0], two["path"])


def test_an_export_preset_sets_the_size_and_codec(tools, source, tmp_path):
    from video import encoding

    preset = {"kind": "custom", "resolution": "720p", "aspect": "1:1", "fill": "blur", "quality": "smallest",
              "codec": "h264", "encoder": "cpu"}
    doc = project()
    path, picture = source
    inputs = compose.Inputs(work=tmp_path / "work", media={"m1": compose.MediaFile(str(path), "video", 640, 360, True),
                                                           "m2": compose.MediaFile(str(picture), "image", 200, 100,
                                                                                   False)},
                            ffmpeg=tools[0], version=compose.ffmpeg_version(tools[0]),
                            encoder=encoding.Encoder("h264", "cpu", "libx264"))
    result = compose.run_plan(compose.compile(doc, 1, preset, inputs=inputs), compose.LocalTask(), config={})
    got = streams(tools[1], result["path"])
    assert (int(got["video"]["width"]), int(got["video"]["height"])) == (720, 720)


def test_a_failed_stage_leaves_none_of_its_files(tools, source, tmp_path):
    doc = project()
    plan = compiled(doc, tools, source, tmp_path / "work")
    gone = tmp_path / "gone.mp4"
    plan.stages[0].commands[0] = [str(gone) if a == str(source[0]) else a for a in plan.stages[0].commands[0]]
    with pytest.raises(compose.StageFailed):
        compose.run_plan(plan, compose.LocalTask(), config={})
    assert parts_left(tmp_path) == []
    assert not (tmp_path / "work" / "v-001.mp4").exists() and not Path(plan.result).exists()


def test_a_cancel_stops_ffmpeg_and_leaves_no_partial_file(tools, source, tmp_path):
    from video import export_presets

    doc = project()
    plan = compiled(doc, tools, source, tmp_path / "work")
    task = compose.LocalTask()
    original = task.run

    def run(argv, **kwargs):
        if any("final" in a for a in argv):
            threading.Timer(0.05, task.cancel).start()
        return original(argv, **kwargs)

    task.run = run
    with pytest.raises(export_presets.Cancelled):
        compose.run_plan(plan, task, config={})
    assert parts_left(tmp_path) == []
    assert not Path(plan.result).exists()


# ---- part C: captions, sync, loudness, sounds, the still and the draft ----------------------------


class SoundEnv(Env):
    """Env, with sounds: any asset not named "image..." is a 3 s sound."""

    def media(self, ref):
        if "asset" in ref and not ref["asset"].startswith("image"):
            return {"kind": "audio", "source": {"type": "asset", "sha256": ref["asset"]}, "duration_us": 3_000_000,
                    "width": 0, "height": 0, "fps": None, "has_audio": True, "name": ref["asset"]}
        return super().media(ref)

    def caption_style(self, style):
        return dict(style)


SOUND_ENV = SoundEnv()


def _make(binary: str, *args) -> None:
    subprocess.run([binary, "-hide_banner", "-v", "error", "-y", *args], check=True, timeout=120)


@pytest.fixture(scope="module")
def flashes(tools, tmp_path_factory):
    """8 s of black, 320x180 at 30 fps, white for two frames from 3.0 s and
    from 5.0 s (so 2x, which shows every other frame, keeps one), and a
    1 kHz beep starting at the same two times."""
    folder = tmp_path_factory.mktemp("flash")
    path = folder / "flash.mp4"
    _make(tools[0], "-f", "lavfi", "-i",
          "color=c=black:s=320x180:r=30:d=8,drawbox=x=0:y=0:w=iw:h=ih:color=white:t=fill:"
          "enable='between(n,90,91)+between(n,150,151)'",
          "-f", "lavfi", "-i", "aevalsrc='0.5*sin(2*PI*1000*t)*(between(t,3,3.05)+between(t,5,5.05))':s=48000:d=8",
          "-c:v", "libx264", "-preset", "ultrafast", "-g", "30", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k",
          "-shortest", str(path))
    return path


@pytest.fixture(scope="module")
def sounds(tools, tmp_path_factory):
    folder = tmp_path_factory.mktemp("sounds")
    music, click = folder / "music.wav", folder / "click.wav"
    _make(tools[0], "-f", "lavfi", "-i", "sine=f=330:r=48000:d=3", "-ac", "2", str(music))
    _make(tools[0], "-f", "lavfi", "-i", "aevalsrc='0.9*lt(t,0.002)':s=48000:d=0.1", "-ac", "2", str(click))
    return {"music": music, "click": click}


def _gray_frames(binary: str, path, size=(8, 8)) -> list[list[int]]:
    """Each frame of `path` scaled to `size`, as grey levels."""
    raw = subprocess.run([binary, "-hide_banner", "-v", "error", "-i", str(path), "-map", "0:v:0", "-vf",
                          f"scale={size[0]}:{size[1]}:flags=area,format=gray", "-f", "rawvideo", "-"],
                         capture_output=True, check=True, timeout=120).stdout
    n = size[0] * size[1]
    return [list(raw[i:i + n]) for i in range(0, len(raw) - n + 1, n)]


def _mono(binary: str, path) -> list[int]:
    import array

    raw = subprocess.run([binary, "-hide_banner", "-v", "error", "-i", str(path), "-map", "0:a:0", "-ac", "1",
                          "-ar", "48000", "-f", "s16le", "-"], capture_output=True, check=True, timeout=120).stdout
    samples = array.array("h")
    samples.frombytes(raw[:len(raw) // 2 * 2])
    return list(samples)


def _onsets(samples: list[int], threshold: int, quiet: int = 4800) -> list[int]:
    """Where a sound starts after at least `quiet` samples under the threshold."""
    out, last = [], -quiet
    for i, s in enumerate(samples):
        if abs(s) > threshold:
            if i - last >= quiet:
                out.append(i)
            last = i
    return out


def _inputs(work: Path, tools, media: dict, **kw) -> compose.Inputs:
    return compose.Inputs(work=work, media=media, ffmpeg=tools[0], version=compose.ffmpeg_version(tools[0]), **kw)


def test_a_flash_and_its_beep_stay_together_after_cuts_and_speed(tools, flashes, tmp_path):
    """Each white frame and the start of its beep are on the same frame of
    the export, at 1x, 2x and 0.5x, after cuts (within one frame)."""
    doc = model.new_project(name="Sync", width=180, height=320, fps=30)
    doc = ops.apply(doc, [{"op": "add_media", "video_id": "v"}], ENV).document
    doc = ops.apply(doc, [
        {"op": "insert_clip", "media_id": "m1", "source_in_us": 2_500_000, "source_out_us": 3_500_000},
        {"op": "insert_clip", "media_id": "m1", "source_in_us": 4_500_000, "source_out_us": 5_500_000, "speed": 2.0},
        {"op": "insert_clip", "media_id": "m1", "source_in_us": 2_800_000, "source_out_us": 3_300_000, "speed": 0.5},
    ], ENV).document
    plan = compose.compile(doc, 1, None, "export", inputs=_inputs(
        tmp_path / "work", tools, {"m1": compose.MediaFile(str(flashes), "video", 320, 180, True)}))
    result = compose.run_plan(plan, compose.LocalTask(), config={})
    frames = _gray_frames(tools[0], result["path"])
    white = [sum(f) / len(f) > 128 for f in frames]
    flashed = [i / 30 for i, w in enumerate(white) if w and (i == 0 or not white[i - 1])]     # where each starts
    # A beep starts where it reaches 30% of its peak: slowing sound down
    # (atempo's overlapping windows) puts a faint pre-echo a few tens of ms
    # ahead of a transient, which is not where it is heard to start.
    heard = _mono(tools[0], result["path"])
    beeps = [i / 48000 for i in _onsets(heard, int(0.3 * max(abs(s) for s in heard)))]
    assert len(flashed) == 3 and len(beeps) == 3, (flashed, beeps)
    for seen, heard, expected in zip(flashed, beeps, (0.5, 1.25, 1.9)):
        assert abs(seen - heard) <= 1 / 30 + 1e-6, (seen, heard)
        assert abs(seen - expected) <= 1 / 30 + 1e-6


def test_picture_and_sound_stay_together_when_an_in_point_is_between_source_frames(tools, flashes, tmp_path):
    """Fix 7-D9: an item whose in point falls between two source frames
    (here 10 ms, 25 ms and 20 ms before the flash's frame) shows the source
    frame on screen at each moment, so the flash appears where its beep is
    heard and where the item's timing puts it (within one frame), at 0.25x,
    0.5x and 1x. Before the fix the picture ran up to 4 frames early."""
    doc = model.new_project(name="Offgrid", width=180, height=320, fps=30)
    doc = ops.apply(doc, [{"op": "add_media", "video_id": "v"}], ENV).document
    doc = ops.apply(doc, [
        {"op": "insert_clip", "media_id": "m1", "source_in_us": 2_990_000, "source_out_us": 3_400_000, "speed": 0.25},
        {"op": "insert_clip", "media_id": "m1", "source_in_us": 4_975_000, "source_out_us": 5_400_000, "speed": 0.5},
        {"op": "insert_clip", "media_id": "m1", "source_in_us": 2_980_000, "source_out_us": 3_400_000},
    ], ENV).document
    items = model.track(doc, "V1")["items"]
    expected = [i["start_us"] / 1e6 + ((3.0 if k != 1 else 5.0) - i["source_in_us"] / 1e6) / i["speed"]
                for k, i in enumerate(items)]
    plan = compose.compile(doc, 1, None, "export", inputs=_inputs(
        tmp_path / "work", tools, {"m1": compose.MediaFile(str(flashes), "video", 320, 180, True)}))
    result = compose.run_plan(plan, compose.LocalTask(), config={})
    frames = _gray_frames(tools[0], result["path"])
    white = [sum(f) / len(f) > 128 for f in frames]
    flashed = [i / 30 for i, w in enumerate(white) if w and (i == 0 or not white[i - 1])]
    heard = _mono(tools[0], result["path"])
    beeps = [i / 48000 for i in _onsets(heard, int(0.3 * max(abs(s) for s in heard)))]
    assert len(flashed) == 3 and len(beeps) == 3, (flashed, beeps)
    for seen, sound, when in zip(flashed, beeps, expected):
        assert abs(seen - sound) <= 1 / 30 + 1e-6, (seen, sound, when)
        assert abs(seen - when) <= 1 / 30 + 1e-6, (seen, when)
        assert seen > 0, "the flash is shown from the item's first frame"
    # The still picks the same source frame as the export: white on each
    # flash's first frame, dark on the frame before it.
    media = {"m1": compose.MediaFile(str(flashes), "video", 320, 180, True)}
    for seen in flashed:
        n = int(round(seen * 30))
        for frame_no, white_then in ((n - 1, False), (n, True)):
            t_us = model.frame_us(frame_no, doc["canvas"]["fps"])
            plan = compose.compile(doc, 1, None, "still", inputs=_inputs(tmp_path / f"s{frame_no}", tools, media),
                                   at_us=t_us)
            still = compose.run_plan(plan, compose.LocalTask(), config={})
            grey = _gray_frames(tools[0], still["path"])[0]
            assert (sum(grey) / len(grey) > 128) == white_then, (frame_no, white_then)


def test_captions_are_on_screen_at_their_remapped_times(tools, tmp_path):
    """Lines in the source's time, through a cut (the middle line's words
    are cut out) and 2x speed: each caption is on screen where its words
    play, and off where they don't."""
    src = tmp_path / "grey.mp4"
    _make(tools[0], "-f", "lavfi", "-i", "color=c=0x303030:s=320x180:r=30:d=8", "-f", "lavfi", "-i",
          "sine=f=300:r=48000:d=8", "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", "-c:a", "aac",
          "-shortest", str(src))
    doc = model.new_project(name="Words", width=360, height=640, fps=30)
    doc = ops.apply(doc, [{"op": "add_media", "video_id": "v"}], SOUND_ENV).document
    doc = ops.apply(doc, [
        {"op": "insert_clip", "media_id": "m1", "source_in_us": 1_000_000, "source_out_us": 2_000_000},
        {"op": "insert_clip", "media_id": "m1", "source_in_us": 4_000_000, "source_out_us": 6_000_000, "speed": 2.0},
        {"op": "set_captions", "enabled": True, "media_id": "m1",
         "lines": [{"in_us": 1_200_000, "out_us": 1_800_000, "text": "first"},
                   {"in_us": 2_500_000, "out_us": 3_500_000, "text": "gone"},
                   {"in_us": 4_400_000, "out_us": 5_600_000, "text": "second"}]},
    ], SOUND_ENV).document
    plan = compose.compile(doc, 1, None, "export", inputs=_inputs(
        tmp_path / "work", tools, {"m1": compose.MediaFile(str(src), "video", 320, 180, True)}))
    result = compose.run_plan(plan, compose.LocalTask(), config={})
    frames = _gray_frames(tools[0], result["path"], (180, 320))

    def captioned(t: float) -> bool:
        return max(frames[int(round(t * 30))][180 * 224:180 * 256]) > 200    # the band bottom captions sit in

    # "first" at 0.2-0.8 s, "second" at 1.2-1.8 s (4.4-5.6 s at 2x), nothing between.
    assert [captioned(t) for t in (0.1, 0.5, 0.95, 1.1, 1.5, 1.9)] == [False, True, False, False, True, False]


def _ebur128(binary: str, path) -> tuple[float, float]:
    import re

    err = subprocess.run([binary, "-hide_banner", "-nostats", "-i", str(path), "-map", "0:a:0", "-filter:a",
                          "ebur128=peak=true", "-f", "null", "-"], capture_output=True, text=True, timeout=120).stderr
    summary = err[err.rindex("Summary:"):]
    return (float(re.search(r"I:\s+(-?[\d.]+) LUFS", summary).group(1)),
            float(re.search(r"Peak:\s+(-?[\d.]+) dBFS", summary).group(1)))


def test_the_mix_is_at_the_apps_loudness_and_under_its_true_peak(tools, source, sounds, tmp_path):
    doc = model.new_project(name="Loud", width=180, height=320, fps=30)
    doc = ops.apply(doc, [{"op": "add_media", "video_id": "v"}, {"op": "add_media", "asset": "music-a"}],
                    SOUND_ENV).document
    music = list(doc["media"])[-1]
    doc = ops.apply(doc, [
        {"op": "insert_clip", "media_id": "m1", "source_in_us": 500_000, "source_out_us": 7_500_000},
        {"op": "add_audio", "media_id": music, "track_id": "A2", "start_us": 0, "role": "music", "loop": True,
         "duration_us": 7_000_000, "duck": True, "volume": 0.6},
    ], SOUND_ENV).document
    plan = compose.compile(doc, 1, None, "export", inputs=_inputs(
        tmp_path / "work", tools, {"m1": compose.MediaFile(str(source[0]), "video", 640, 360, True),
                                   music: compose.MediaFile(str(sounds["music"]), "audio", 0, 0, True)}))
    result = compose.run_plan(plan, compose.LocalTask(), config={})
    loudness, peak = _ebur128(tools[0], result["path"])
    assert abs(loudness - (-14.0)) <= 1.0 and peak <= -1.0, (loudness, peak)


def test_a_click_lands_within_an_audio_frame(tools, source, sounds, tmp_path):
    """A sound effect placed at 1.234567 s starts there, to well within one
    AAC frame (1,024 samples)."""
    doc = model.new_project(name="Click", width=180, height=320, fps=30)
    doc = ops.apply(doc, [{"op": "add_media", "video_id": "v"}, {"op": "add_media", "asset": "click-a"},
                          {"op": "set_project_props", "normalize_audio": False},
                          {"op": "set_track_props", "track_id": "A1", "mute": True}], SOUND_ENV).document
    click = list(doc["media"])[-1]
    doc = ops.apply(doc, [
        {"op": "insert_clip", "media_id": "m1", "source_in_us": 1_000_000, "source_out_us": 3_000_000},
        {"op": "add_audio", "media_id": click, "track_id": "A2", "start_us": 1_234_567, "role": "sfx",
         "source_in_us": 0, "source_out_us": 100_000},
    ], SOUND_ENV).document
    plan = compose.compile(doc, 1, None, "export", inputs=_inputs(
        tmp_path / "work", tools, {"m1": compose.MediaFile(str(source[0]), "video", 640, 360, True),
                                   click: compose.MediaFile(str(sounds["click"]), "audio", 0, 0, True)}))
    result = compose.run_plan(plan, compose.LocalTask(), config={})
    heard = _onsets(_mono(tools[0], result["path"]), 3000)
    assert len(heard) == 1 and abs(heard[0] - 1.234567 * 48000) < 1024, heard


def _ssim(binary: str, a, b) -> float:
    import re

    err = subprocess.run([binary, "-hide_banner", "-i", str(a), "-i", str(b), "-filter_complex",
                          "[0:v]format=yuv420p[x];[1:v]scale=in_range=pc:out_range=tv,format=yuv420p[y];[x][y]ssim",
                          "-f", "null", "-"], capture_output=True, text=True, timeout=120).stderr
    return float(re.search(r"All:([\d.]+)", err).group(1))


def test_the_still_at_a_time_is_the_exports_frame_then(tools, source, tmp_path):
    """Every layer at once (framing, a transform, an overlay picture, a
    caption, text, a text watermark): the still and the export's frame at
    that time have an SSIM of at least 0.97."""
    doc = ops.apply(project(), [
        {"op": "set_captions", "enabled": True, "media_id": "m1",
         "lines": [{"in_us": 1_100_000, "out_us": 1_900_000, "text": "look here"}]},
        {"op": "add_track", "kind": "text"},
        {"op": "add_text", "content": "Top", "start_us": 0, "duration_us": 2_000_000},
        {"op": "set_branding", "watermark": {"type": "text", "text": "@me"}},
    ], SOUND_ENV).document
    path, picture = source
    media = {"m1": compose.MediaFile(str(path), "video", 640, 360, True),
             "m2": compose.MediaFile(str(picture), "image", 200, 100, False)}
    exported = compose.run_plan(compose.compile(doc, 1, None, "export", inputs=_inputs(tmp_path / "e", tools, media)),
                                compose.LocalTask(), config={})
    for t_us in (500_000, 1_266_667, 1_800_000):
        plan = compose.compile(doc, 1, None, "still", inputs=_inputs(tmp_path / f"s{t_us}", tools, media), at_us=t_us)
        still = compose.run_plan(plan, compose.LocalTask(), config={})
        frame = tmp_path / f"frame-{t_us}.png"
        n = compose.still_frame(doc, t_us)
        _make(tools[0], "-i", exported["path"], "-vf", f"select=eq(n\\,{n})", "-frames:v", "1", str(frame))
        assert _ssim(tools[0], frame, still["path"]) >= 0.97, t_us


@pytest.mark.parametrize("case", ["picture_1s", "silent_track_2s", "volume_0_2s"])
@pytest.mark.parametrize("purpose", ["export", "draft"])
def test_a_short_silent_program_still_exports_with_a_silent_track(tools, source, tmp_path, case, purpose):
    """Fix 7-D10: under 3 s with nothing to hear (a picture, a video whose
    sound is digital silence, an item at volume 0), loudnorm used to put out
    NaN and the AAC encoder refused it. Now the export and the draft finish,
    with a silent sound track as long as the program."""
    path, picture = source
    silent = tmp_path / "silent.mp4"
    _make(tools[0], "-f", "lavfi", "-i", "testsrc2=s=320x180:r=30:d=3", "-f", "lavfi", "-i",
          "anullsrc=r=48000:cl=stereo", "-t", "3", "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
          "-c:a", "aac", "-shortest", str(silent))
    doc = model.new_project(name="Quiet", width=180, height=320, fps=30)
    doc = ops.apply(doc, [{"op": "add_media", "video_id": "v"}, {"op": "add_media", "asset": "image-a"}],
                    ENV).document
    if case == "picture_1s":
        ops_list = [{"op": "insert_clip", "media_id": "m2", "source_in_us": 0, "source_out_us": 1_000_000}]
    else:
        ops_list = [{"op": "insert_clip", "media_id": "m1", "source_in_us": 0, "source_out_us": 2_000_000}]
    doc = ops.apply(doc, ops_list, ENV).document
    if case == "volume_0_2s":
        item = model.track(doc, "V1")["items"][0]
        doc = ops.apply(doc, [{"op": "set_item_props", "item_id": item["id"], "volume": 0.0}], ENV).document
    video = str(silent) if case == "silent_track_2s" else str(path)
    media = {"m1": compose.MediaFile(video, "video", 640, 360, True),
             "m2": compose.MediaFile(str(picture), "image", 200, 100, False)}
    plan = compose.compile(doc, 1, None, purpose, inputs=_inputs(tmp_path / "w", tools, media))
    result = compose.run_plan(plan, compose.LocalTask(), config={})
    got = streams(tools[1], result["path"])
    program = model.program_end(doc) / model.US
    assert abs(float(got["audio"]["duration"]) - program) <= 1 / 30
    heard = _mono(tools[0], result["path"])
    assert heard and max(abs(s) for s in heard) == 0


def test_the_draft_is_small_and_lasts_the_program(tools, source, tmp_path):
    doc = ops.apply(project(), [{"op": "set_canvas", "preset": "9:16"}], ENV).document
    path, picture = source
    media = {"m1": compose.MediaFile(str(path), "video", 640, 360, True),
             "m2": compose.MediaFile(str(picture), "image", 200, 100, False)}
    plan = compose.compile(doc, 1, None, "draft", inputs=_inputs(tmp_path / "d", tools, media, card_seconds=2.9))
    result = compose.run_plan(plan, compose.LocalTask(), config={})
    got = streams(tools[1], result["path"])
    assert (int(got["video"]["width"]), int(got["video"]["height"])) == (540, 960)
    program = model.program_end(doc) / model.US
    assert abs(float(got["video"]["duration"]) - program) <= 1 / 30
    assert abs(float(got["audio"]["duration"]) - program) <= 1 / 30
