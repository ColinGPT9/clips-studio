"""Every final write starts its video at 0, whether an app reads the MP4's
edit list or not.

Clips opened on a black moment on Reels, TikTok and Shorts, so each one was
trimmed by hand before posting. Two causes:

- the end card's join, which put the first frame 10-23 ms late behind an
  empty edit (tests/test_outro_join.py, tests/test_outro.py);
- the H.264 encoders' B-frames. The first frame is decoded before the ones
  shown ahead of it, so a plain MP4 says it is shown two or three frames in
  and leans on its edit list to pull it back to 0. An app that ignores edit
  lists opens on black for those frames while the sound starts at 0.

+negative_cts_offsets writes the first frame at 0 in the file itself. So
every write of a file that is posted carries video.encoding.final_movflags():
the vertical renders, the one-encode cut (horizontal, Vertical Live), the
editor's pass when it writes the clip, and the joins. Staging files keep
plain +faststart: only this app reads them, and nothing about them changes.

The command tests need only the standard library and run on CI. The
cropper's and the pipeline's need numpy and cv2, the real encodes FFmpeg;
they skip without them.
"""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from core.models import ClipCandidate
from video import cutter, encoding
from video_editor import export
from video_editor.timeline import EditList

FINAL = ["-movflags", "+faststart+negative_cts_offsets"]
STAGING = ["-movflags", "+faststart"]
CLIP = ClipCandidate(start=1.0, end=3.0, score=80)


class _Ran:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode, self.stdout, self.stderr = returncode, stdout, stderr


def _movflags(cmd) -> list[str]:
    """Every -movflags block in `cmd`."""
    cmd = [str(c) for c in cmd]
    return [cmd[i + 1] for i, c in enumerate(cmd) if c == "-movflags"]


@pytest.fixture
def ran(monkeypatch):
    """subprocess.run as cutter and export see it: records each command."""
    seen = []

    def run(cmd, *a, **k):
        seen.append([str(c) for c in cmd])
        return _Ran()

    monkeypatch.setattr(subprocess, "run", run)
    monkeypatch.setattr(cutter, "video_encoder_args", lambda *a, **k: encoding.CPU_ARGS)
    monkeypatch.setattr(export, "video_encoder_args", lambda *a, **k: encoding.CPU_ARGS)
    monkeypatch.setattr(cutter, "using_hardware_encoder", lambda: False)
    return seen


def test_the_final_flags():
    assert encoding.final_movflags() == FINAL
    assert encoding.FINAL_MOVFLAGS == FINAL[1]


# ---- the one-encode cut -----------------------------------------------------------------


def test_a_final_cut_is_written_with_the_final_flags(ran, tmp_path):
    cutter.cut_clip(tmp_path / "source.mp4", CLIP, tmp_path / "clip.mp4", final=True)
    [cmd] = ran
    assert _movflags(cmd) == [FINAL[1]]
    assert cmd[-1] == str((tmp_path / "clip.mp4").resolve())


def test_a_staging_cut_is_written_as_before(ran, tmp_path):
    """The cut a vertical render crops from, and the plain cut the editor's
    pass reads: only this app reads them."""
    cutter.cut_clip(tmp_path / "source.mp4", CLIP, tmp_path / "staging.mp4")
    cutter.cut_clip(tmp_path / "source.mp4", CLIP, tmp_path / "staging.mp4", final=False)
    assert [_movflags(cmd) for cmd in ran] == [[STAGING[1]], [STAGING[1]]]


def test_final_is_not_the_loudness_switch(ran, tmp_path):
    """A clip can turn loudness normalising off; it is still the clip."""
    cutter.cut_clip(tmp_path / "source.mp4", CLIP, tmp_path / "clip.mp4", normalize=False, final=True)
    cutter.cut_clip(tmp_path / "source.mp4", CLIP, tmp_path / "staging.mp4", normalize=True)
    assert [_movflags(cmd) for cmd in ran] == [[FINAL[1]], [STAGING[1]]]


def test_the_cpu_retry_keeps_the_final_flags(monkeypatch, tmp_path):
    """A GPU encoder that refuses the footage is retried on the CPU with the
    same command, flags included."""
    seen = []
    gpu = ["-c:v", "h264_nvenc", "-preset", "p5"]

    def run(cmd, *a, **k):
        seen.append([str(c) for c in cmd])
        return _Ran(1 if len(seen) == 1 else 0, stderr="refused")

    monkeypatch.setattr(subprocess, "run", run)
    monkeypatch.setattr(cutter, "video_encoder_args", lambda *a, **k: gpu)
    monkeypatch.setattr(cutter, "using_hardware_encoder", lambda: True)
    cutter.cut_clip(tmp_path / "source.mp4", CLIP, tmp_path / "clip.mp4", final=True)
    assert len(seen) == 2 and "libx264" in seen[1]
    assert [_movflags(cmd) for cmd in seen] == [[FINAL[1]], [FINAL[1]]]


# ---- the editor's pass ------------------------------------------------------------------


def test_the_editors_pass_that_writes_the_clip_has_the_final_flags(ran, tmp_path):
    edit = EditList(duration=2.0, keep=[(0.0, 0.8), (1.2, 2.0)], fade_out=0.5)
    export.apply_edits(tmp_path / "plain.mp4", edit, tmp_path / "clip.mp4", final=True)
    export.apply_edits(tmp_path / "staging.mp4", edit, tmp_path / "edited.mp4")
    assert [_movflags(cmd) for cmd in ran] == [[FINAL[1]], [STAGING[1]]]


# ---- gaming layouts, the image watermark, a language's clip ------------------------------
#
# Each writes a clip that is posted as it is: a gaming layout's render, the
# image watermark's pass (it replaces the clip), and a language's subtitled
# and dubbed copies.


@pytest.fixture
def commands(monkeypatch):
    """subprocess.run recording each FFmpeg command and writing its output.
    ffprobe answers a 1080x1920 picture."""
    seen = []

    def run(cmd, *a, **k):
        cmd = [str(c) for c in cmd]
        if "ffprobe" in Path(cmd[0]).name.lower():
            return _Ran(stdout="1080,1920\n")
        seen.append(cmd)
        Path(cmd[-1]).write_bytes(b"clip")
        return _Ran()

    monkeypatch.setattr(subprocess, "run", run)
    monkeypatch.setattr(encoding, "video_encoder_args", lambda *a, **k: encoding.CPU_ARGS)
    return seen


def test_a_gaming_layout_is_written_with_the_final_flags(commands, monkeypatch, tmp_path):
    from gaming import compose
    from gaming.layout import Element, Plan

    monkeypatch.setattr(compose, "video_encoder_args", lambda *a, **k: encoding.CPU_ARGS)
    plan = Plan(preset="fullscreen", elements=(Element("game", (0, 0, 1920, 1080), (0, 0, 1080, 1920)),))
    out = tmp_path / "clip.mp4"
    compose.render(tmp_path / "clip.source.mp4", out, plan)
    [cmd] = commands
    assert _movflags(cmd) == [FINAL[1]]
    assert cmd[-1] == str(out.resolve())


def test_the_image_watermark_rewrites_the_clip_with_the_final_flags(commands, monkeypatch, tmp_path):
    import sys
    import types

    from video_editor import watermark

    monkeypatch.setattr(watermark, "video_encoder_args", lambda *a, **k: encoding.CPU_ARGS)
    # Putting the result in the clip's place is video/outro.py's, which needs
    # numpy and Pillow; here it is a plain rename.
    def replace(src, dst, label=""):
        Path(src).replace(dst)
        return True

    stand_in = types.ModuleType("video.outro")
    stand_in._replace_with_retry = replace
    monkeypatch.setitem(sys.modules, "video.outro", stand_in)
    assets = tmp_path / "assets"
    assets.mkdir()
    (assets / "logo.png").write_bytes(b"png")
    clip = tmp_path / "clip.mp4"
    clip.write_bytes(b"the clip")

    watermark.apply_image(clip, {"image_asset": "logo.png"}, (1080, 1920), assets)
    [cmd] = commands
    assert _movflags(cmd) == [FINAL[1]]
    assert cmd[-1] == str(clip.with_suffix(".wm.mp4").resolve())
    assert clip.read_bytes() == b"clip", "the watermarked pass is the clip now"


def test_a_languages_subtitled_clip_is_written_with_the_final_flags(commands, monkeypatch, tmp_path):
    import video.captions
    from multilingual import burn

    def captions(*_a, **_k):
        ass = tmp_path / "clip.es.ass"
        ass.write_text("[Script Info]\n", encoding="utf-8")
        return ass

    monkeypatch.setattr(video.captions, "build_captions", captions)
    out = tmp_path / "clip.es.mp4"
    assert burn.burn(tmp_path / "base.mp4", [{"start": 0.0, "end": 1.0, "text": "Hola."}], "es", out,
                     None, {}) == out
    [cmd] = commands
    assert _movflags(cmd) == [FINAL[1]]
    assert cmd[-1] == str(out.resolve())


def _speaking(monkeypatch, dub, speak):
    """dub() with Piper stood in for by `speak`."""
    from multilingual import voices

    monkeypatch.setattr(dub, "available", lambda: True)
    monkeypatch.setattr(dub, "supported", lambda _language: True)
    monkeypatch.setattr(dub, "ensure_voice", lambda *a, **k: "voice")
    monkeypatch.setattr(voices, "resolve", lambda _voice, _language: ("voice", None))
    monkeypatch.setattr(dub, "_speak", speak)


def test_a_languages_dubbed_clip_is_written_with_the_final_flags(commands, monkeypatch, tmp_path):
    """A remux: the picture is copied, and copied with plain +faststart its
    first frame would go back behind the edit list."""
    from multilingual import dub

    _speaking(monkeypatch, dub, lambda *a, **k: True)
    monkeypatch.setattr(dub, "_duration", lambda _wav: 1.0)
    out = tmp_path / "clip.es.dubbed.mp4"
    assert dub.dub([{"start": 0.0, "end": 1.0, "text": "Hola."}], "es", tmp_path / "clip.es.mp4", out,
                   tmp_path / "voices", tmp_path / "work") == out
    [cmd] = commands
    assert _movflags(cmd) == [FINAL[1]]
    assert cmd[cmd.index("-c:v") + 1] == "copy"
    assert cmd[-1] == str(out.resolve())


# ---- the vertical renders ---------------------------------------------------------------


@pytest.fixture
def cropper(monkeypatch, tmp_path):
    """video/cropper.py with FFmpeg stood in for: records each command. The
    tracked modes' frames are never produced."""
    np = pytest.importorskip("numpy")
    cv2 = pytest.importorskip("cv2")
    import video.cropper as cropper_mod

    seen = []
    monkeypatch.setattr(cropper_mod, "_run_ffmpeg", lambda cmd, _ass: seen.append(cmd))
    monkeypatch.setattr(cropper_mod, "_run_ffmpeg_piped", lambda cmd, _ass, _produce: seen.append(cmd))
    clip = tmp_path / "staging.mp4"
    out = cv2.VideoWriter(str(clip), cv2.VideoWriter_fourcc(*"mp4v"), 10.0, (640, 360))
    for _ in range(3):
        out.write(np.full((360, 640, 3), 128, np.uint8))
    out.release()
    return cropper_mod, clip, seen


@pytest.mark.parametrize("tracking", [
    {"mode": "track", "path": [(0.0, 0.5)]},
    {"mode": "track", "path": [(0.0, 0.5)], "face_y": 0.1},                 # the face-safe 4:5 layout
    {"mode": "track", "path": [(0.0, 0.5)], "zoom": [(0.0, 1.0)]},          # widened past 9:16
    {"mode": "fit_blur", "region": None},
    {"mode": "split", "webcam_box": (0.0, 0.0, 0.3, 0.3)},
], ids=["track", "face-safe", "zoom", "fit_blur", "split"])
def test_every_vertical_render_is_written_with_the_final_flags(cropper, tmp_path, tracking):
    cropper_mod, clip, seen = cropper
    out = tmp_path / "clip.mp4"
    cropper_mod.render_vertical(clip, tracking, out)
    [cmd] = seen
    assert _movflags(cmd) == [FINAL[1]]
    assert cmd[-1] == str(out.resolve())


# ---- the pipeline: which writes are final -----------------------------------------------


@pytest.fixture
def pipeline(monkeypatch, tmp_path):
    """_render_files with every write stood in for. Returns the module and
    the writes made: (what, output name, final)."""
    pytest.importorskip("numpy")
    pytest.importorskip("cv2")
    from core import modes
    from core import pipeline as pipeline_mod

    writes = []

    def cut(_source, _candidate, output, ass_path=None, vf_extra="", normalize=False, final=False):
        writes.append(("cut", Path(output).name, final))
        Path(output).write_bytes(b"clip")

    def edits(_input, _edit, output, ass_path=None, normalize=False, final=False):
        writes.append(("edits", Path(output).name, final))
        Path(output).write_bytes(b"clip")

    def vertical(_clip, _tracking, output, **_k):
        writes.append(("vertical", Path(output).name, True))    # always the clip
        Path(output).write_bytes(b"clip")

    import video.cropper as cropper_mod
    import video.tracker as tracker

    monkeypatch.setattr(pipeline_mod, "cut_clip", cut)
    monkeypatch.setattr(export, "apply_edits", edits)
    monkeypatch.setattr(cropper_mod, "render_vertical", vertical)
    monkeypatch.setattr(tracker, "compute_tracking", lambda *_a, **_k: {"mode": "track", "path": [(0.0, 0.5)]})
    monkeypatch.setattr(modes, "probe_size", lambda _path: (1080, 1920))
    return pipeline_mod, writes


def _render_files(pipeline_mod, tmp_path, opts=None, **clips):
    config = {
        "clips": {"captions": False, "outro": False, "vertical": True, **clips},
        "paths": {"data_dir": str(tmp_path)},
        "tracking": {"detector": "yolov8n-pose.pt", "sample_fps": 8},
    }
    final, _opts = pipeline_mod._render_files(
        tmp_path / "source.mp4", ClipCandidate(start=10.0, end=20.0, score=80), [],
        tmp_path / "clips", config, opts)
    return final


def test_a_vertical_live_clip_is_one_final_encode(pipeline, tmp_path):
    pipeline_mod, writes = pipeline
    final = _render_files(pipeline_mod, tmp_path, vertical_live=True)
    assert writes == [("cut", final.name, True)]


def test_a_horizontal_clip_with_edits_is_written_final_by_the_editors_pass(pipeline, tmp_path):
    pipeline_mod, writes = pipeline
    edit = {"edit": {"keep": [[0.0, 4.0], [6.0, 10.0]]}}
    final = _render_files(pipeline_mod, tmp_path, opts=edit, vertical=False)
    assert writes == [("cut", final.name.replace(".mp4", ".plain.mp4"), False),
                      ("edits", final.name, True)]


def test_a_tracked_clips_staging_cut_is_not_final(pipeline, tmp_path):
    pipeline_mod, writes = pipeline
    final = _render_files(pipeline_mod, tmp_path)
    assert writes == [("cut", final.name.replace(".mp4", ".source.mp4"), False),
                      ("vertical", final.name, True)]


# ---- a real encode -----------------------------------------------------------------------


def _ffmpeg_or_skip() -> tuple[str, str]:
    from core.binaries import ffmpeg, ffprobe

    for exe in (ffmpeg(), ffprobe()):
        if not (Path(exe).exists() or shutil.which(exe)):
            pytest.skip("FFmpeg isn't installed here")
    return ffmpeg(), ffprobe()


def _first_frame(ffprobe: str, path: Path, edit_list: bool) -> float:
    out = subprocess.run([ffprobe, "-v", "error", *([] if edit_list else ["-ignore_editlist", "1"]),
                          "-select_streams", "v:0", "-read_intervals", "%+#5",
                          "-show_entries", "frame=pts_time", "-of", "csv=p=0", str(path)],
                         capture_output=True, text=True).stdout.split()
    return min(float(x.strip(",")) for x in out)


def _video_edits(ffprobe: str, path: Path) -> list[int]:
    """The media time of each edit on the video track (stream 0 here)."""
    import re

    trace = subprocess.run([ffprobe, "-v", "trace", "-i", str(path)], capture_output=True,
                           text=True, encoding="utf-8", errors="replace").stderr
    return [int(m.group(1)) for m in re.finditer(
        r"Processing st: 0, edit list \d+ - media time: (-?\d+)", trace)]


def test_a_real_final_cut_starts_at_0_with_or_without_the_edit_list(monkeypatch, tmp_path):
    """The CPU encoder (libx264 veryfast) uses B-frames. Written as a staging
    file the first frame is two frames in without the edit list; written as
    the clip it is at 0 either way, and the frames are the same."""
    ffmpeg, ffprobe = _ffmpeg_or_skip()
    monkeypatch.setattr(cutter, "video_encoder_args", lambda *a, **k: encoding.CPU_ARGS)
    monkeypatch.setattr(cutter, "using_hardware_encoder", lambda: False)
    source = tmp_path / "source.mp4"
    subprocess.run([ffmpeg, "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=size=320x568:rate=30:duration=4",
                    "-f", "lavfi", "-i", "sine=sample_rate=48000:duration=4", "-c:v", "libx264",
                    "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(source)],
                   check=True, capture_output=True)

    clip = cutter.cut_clip(source, CLIP, tmp_path / "clip.mp4", final=True)
    staging = cutter.cut_clip(source, CLIP, tmp_path / "staging.mp4")

    assert abs(_first_frame(ffprobe, clip, edit_list=True)) < 0.0005
    assert abs(_first_frame(ffprobe, clip, edit_list=False)) < 0.0005
    assert _video_edits(ffprobe, clip) == [0], "no empty edit, and none to pull the frame back"
    assert abs(_first_frame(ffprobe, staging, edit_list=True)) < 0.0005
    assert abs(_first_frame(ffprobe, staging, edit_list=False) - 2 / 30) < 0.001, "the B-frames' delay"

    def frames(path):
        r = subprocess.run([ffprobe, "-v", "error", "-select_streams", "v:0", "-count_packets",
                            "-show_entries", "stream=nb_read_packets", "-of", "json", str(path)],
                           capture_output=True, text=True)
        return int(json.loads(r.stdout)["streams"][0]["nb_read_packets"])

    assert frames(clip) == frames(staging) == 60


def test_a_real_dub_starts_at_0_with_or_without_the_edit_list(monkeypatch, tmp_path):
    """A language's dubbed clip copies the clip's picture into a new file.
    Copied with plain +faststart, the B-frames' delay came back (the first
    frame a frame or two in without the edit list); with the final flags it
    stays at 0 either way."""
    ffmpeg, ffprobe = _ffmpeg_or_skip()
    from multilingual import dub

    clip = tmp_path / "clip.es.mp4"
    subprocess.run([ffmpeg, "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=size=320x568:rate=30:duration=3",
                    "-f", "lavfi", "-i", "sine=sample_rate=48000:duration=3", "-c:v", "libx264",
                    "-preset", "veryfast", "-pix_fmt", "yuv420p", "-c:a", "aac", *FINAL, str(clip)],
                   check=True, capture_output=True)
    assert abs(_first_frame(ffprobe, clip, edit_list=False)) < 0.0005, "the clip as the app writes it"

    def speak(_text, _voice, _voices_dir, wav, **_k):
        subprocess.run([ffmpeg, "-v", "error", "-y", "-f", "lavfi", "-i",
                        "sine=frequency=330:sample_rate=22050:duration=1", str(wav)],
                       check=True, capture_output=True)
        return True

    _speaking(monkeypatch, dub, speak)
    out = tmp_path / "clip.es.dubbed.mp4"
    assert dub.dub([{"start": 0.5, "end": 1.5, "text": "Hola."}], "es", clip, out,
                   tmp_path / "voices", tmp_path / "work") == out
    assert abs(_first_frame(ffprobe, out, edit_list=True)) < 0.0005
    assert abs(_first_frame(ffprobe, out, edit_list=False)) < 0.0005
    assert _video_edits(ffprobe, out) == [0]
