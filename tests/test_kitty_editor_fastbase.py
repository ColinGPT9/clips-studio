"""Editor suite K2, first open: a clip's base video made from the clip's own
framing (kitty_editor/fastbase.py), and what render_base does around it.

The framing tests make small videos with FFmpeg: a "source" full of detail,
and a "clip" cut from it the way video/cropper._render_tracked lays a clip
out (a moving 9:16 crop filling the screen, or the 4:5 crop under the apps'
top bar over a blurred copy), with a bar standing in for a caption and an
end card after it. The crop is then read back from the clip's file. They
skip without numpy, OpenCV or FFmpeg (the Plugin platform job has all three).
"""

from __future__ import annotations

import json
import shutil
import subprocess
import threading
from pathlib import Path

import pytest

np = pytest.importorskip("numpy")
cv2 = pytest.importorskip("cv2")

from kitty_editor import build, fastbase  # noqa: E402
from video import encoding  # noqa: E402

W, H, RATE = 640, 360, 30


def _ffmpeg() -> str:
    try:
        from core.binaries import ffmpeg

        exe = ffmpeg()
    except Exception:
        exe = None
    if not exe or not (Path(exe).is_file() or shutil.which(exe)):
        pytest.skip("FFmpeg is not on this machine")
    return exe


def _run(*args) -> None:
    done = subprocess.run([_ffmpeg(), "-v", "error", "-y", *args], capture_output=True, text=True)
    assert done.returncode == 0, done.stderr[-2000:]


@pytest.fixture(scope="module")
def source(tmp_path_factory) -> Path:
    """4 s of a zooming fractal (detail everywhere, nothing repeats), with a tone."""
    out = tmp_path_factory.mktemp("fastbase") / "source.mp4"
    _run("-f", "lavfi", "-i", f"mandelbrot=s={W}x{H}:rate={RATE}", "-f", "lavfi", "-i", "sine=f=440:r=48000",
         "-t", "4", "-c:v", "libx264", "-preset", "ultrafast", "-crf", "12", "-pix_fmt", "yuv420p",
         "-c:a", "aac", "-shortest", str(out))
    return out


def _clip(source: Path, out: Path, x_expr: str, *, seconds: float = 3.4, layout: str = "full") -> Path:
    """A clip as the standard render lays it out, cropped at x_expr (FFmpeg's
    crop expression, in t), with a white bar where a caption sits and half a
    second of an end card."""
    full, face = fastbase.layouts(W, H)
    if layout == "full":
        picture = f"crop={full.crop_w}:{H}:x='{x_expr}':y=0,scale=1080:1920:flags=lanczos,setsar=1"
    else:
        picture = (f"crop={face.crop_w}:{H}:x='{x_expr}':y=0,split=2[a][b];"
                   "[a]scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,scale=135:240,"
                   "gblur=sigma=12,scale=1080:1920:flags=bilinear[bg];[b]scale=1080:-2:flags=lanczos[fg];"
                   f"[bg][fg]overlay=0:{face.top},setsar=1")
    graph = (f"[0:v]trim=duration={seconds},setpts=PTS-STARTPTS,{picture},"
             "drawbox=x=120:y=1250:w=840:h=160:color=white:t=fill,"
             "tpad=stop_mode=add:stop_duration=0.5:color=red[v]")
    _run("-i", str(source), "-filter_complex", graph, "-map", "[v]", "-c:v", "libx264", "-preset", "ultrafast",
         "-crf", "18", "-pix_fmt", "yuv420p", str(out))
    return out


def _letterboxed(source: Path, out: Path) -> Path:
    """The whole picture at full width over a blurred copy: the letterbox layout."""
    graph = ("[0:v]trim=duration=3.4,setpts=PTS-STARTPTS,split=2[a][b];"
             "[a]scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,scale=135:240,"
             "gblur=sigma=12,scale=1080:1920[bg];[b]scale=1080:-2[fg];[bg][fg]overlay=0:(H-h)/2[v]")
    _run("-i", str(source), "-filter_complex", graph, "-map", "[v]", "-c:v", "libx264", "-preset", "ultrafast",
         "-crf", "18", "-pix_fmt", "yuv420p", str(out))
    return out


def _read(source, clip, **kw):
    return fastbase.read_framing(source, clip, 0.0, 3.4, src_w=W, src_h=H, fps=f"{RATE}/1", step=4, **kw)


def test_the_layouts_are_the_standard_renders():
    full, face = fastbase.layouts(1920, 1080)
    assert (full.name, full.crop_w, full.crop_h, full.top, full.height) == ("full", 606, 1080, 0, 1920)
    assert (face.name, face.crop_w, face.crop_h, face.top, face.height) == ("face", 864, 1080, 220, 1350)


def test_a_moving_crop_is_read_back_from_the_clip(source, tmp_path):
    clip = _clip(source, tmp_path / "clip.mp4", "20+90*t")
    record: list = []
    layout, keys, frames = _read(source, clip, record=record)
    assert layout.name == "full" and frames in (101, 102, 103)
    # Every sample, to within a pixel and a half of where the crop was.
    for frame, x in keys:
        assert abs(x - (20 + 90 * frame / RATE)) <= 1.5, (frame, x)
    assert {r[0] for r in record} >= {"full"} and min(r[3] for r in record if r[0] == "full") > 0.5


def test_a_jump_is_placed_on_its_own_frame(source, tmp_path):
    clip = _clip(source, tmp_path / "jump.mp4", "if(lt(t,1.5),40,380)")
    _layout, keys, _frames = _read(source, clip)
    at = dict(keys)
    assert abs(at[44] - 40) <= 1.5 and abs(at[45] - 380) <= 1.5       # 1.5 s at 30 fps is frame 45
    first, script = fastbase.crop_lines(keys, 102, src_w=W, crop_w=202, fps="30/1")
    assert abs(first - 40) <= 1
    jumps = [line for line in script.splitlines() if line]
    assert len(jumps) == 1 and jumps[0].startswith("1.483333 crop@base x ")


def test_the_face_safe_layout_is_told_and_read(source, tmp_path):
    clip = _clip(source, tmp_path / "face.mp4", "30+60*t", layout="face")
    layout, keys, _frames = _read(source, clip)
    assert layout.name == "face"
    for frame, x in keys:
        assert abs(x - (30 + 60 * frame / RATE)) <= 1.5, (frame, x)


def test_a_clip_that_is_not_a_moving_crop_is_left_to_the_full_render(source, tmp_path):
    with pytest.raises(fastbase.NotPlain):
        _read(source, _letterboxed(source, tmp_path / "letterbox.mp4"))
    other = tmp_path / "other.mp4"         # a clip of something else entirely
    _run("-f", "lavfi", "-i", "testsrc2=s=1080x1920:rate=30", "-t", "4", "-c:v", "libx264", "-preset",
         "ultrafast", "-pix_fmt", "yuv420p", str(other))
    with pytest.raises(fastbase.NotPlain):
        _read(source, other)


def test_the_base_is_rendered_from_the_read_framing(source, tmp_path):
    clip = _clip(source, tmp_path / "clip.mp4", "20+90*t")
    row = {"start_s": 0.0, "end_s": 3.0}
    config = {"clips": {}, "tracking": {"sample_fps": 7.5}}
    seen = []
    with encoding.cpu_encoder_args(build.BASE_CPU_ARGS):
        out = fastbase.make(row, {}, config, source, clip, tmp_path / "work", progress=seen.append)
    assert out is not None and out.is_file()
    info = build.probe(out)
    assert (info["width"], info["height"]) == (1080, 1920) and abs(info["seconds"] - 3.4) < 0.1
    assert seen == sorted(seen) and 0 < seen[-1] <= 1.0
    framing = json.loads((tmp_path / "work" / "framing.json").read_text(encoding="utf-8"))
    assert framing["layout"] == "full" and framing["frames"] >= 101
    # Untagged, as the standard render's piped frames are written.
    from core.binaries import ffprobe

    tags = subprocess.run([ffprobe(), "-v", "error", "-select_streams", "v",
                           "-show_entries", "stream=color_space", "-of", "csv=p=0", str(out)],
                          capture_output=True, text=True).stdout.strip()
    assert tags in ("", "unknown")


def test_only_a_clip_framed_the_standard_way_is_read_back():
    plain = {"clips": {"vertical": True}}
    assert fastbase.plain_layout({}, plain) and fastbase.plain_layout({"crop": "bias_left"}, plain)
    for opts in ({"crop": "letterbox"}, {"podcast": True}, {"gaming": {"layout": "split"}}, {"sport": "soccer"},
                 {"vertical_live": True}, {"profile": "16:9"}, {"edit": {"keep": [[0, 3]]}}):
        assert not fastbase.plain_layout(opts, plain), opts
    assert not fastbase.plain_layout({}, {"clips": {"vertical": False}})
    assert not fastbase.plain_layout({}, {"clips": {"gaming": True}})
    assert fastbase.plain_layout({"gaming": None}, {"clips": {"gaming": True}})     # turned off for this clip


def test_the_window_has_the_renders_end_pad():
    pytest.importorskip("yaml")
    from core.pipeline import END_PAD

    row = {"start_s": 10.0, "end_s": 30.0}
    assert fastbase.window(row, {}, {"clips": {}}) == (10.0, pytest.approx(20.0 + END_PAD))
    assert fastbase.window(row, {"whole": True}, {"clips": {}}) == (10.0, pytest.approx(20.0))
    assert fastbase.window(row, {"stop_by": 20.1}, {"clips": {}}) == (10.0, pytest.approx(20.1))


def test_the_base_encoder_settings_reach_only_the_renders_inside(monkeypatch):
    monkeypatch.setattr(encoding, "_selected", ("cpu", encoding.CPU_ARGS))
    monkeypatch.setattr(encoding, "_mode", "auto")
    assert encoding.video_encoder_args() == encoding.CPU_ARGS
    other: list = []
    with encoding.cpu_encoder_args(build.BASE_CPU_ARGS):
        assert encoding.video_encoder_args() == build.BASE_CPU_ARGS
        thread = threading.Thread(target=lambda: other.append(encoding.video_encoder_args()))
        thread.start()
        thread.join()
    assert other == [encoding.CPU_ARGS]                       # another thread's render: as configured
    assert encoding.video_encoder_args() == encoding.CPU_ARGS
    nvenc = encoding._CANDIDATES["nvenc"]
    monkeypatch.setattr(encoding, "_selected", ("nvenc", nvenc))
    with encoding.cpu_encoder_args(build.BASE_CPU_ARGS):
        assert encoding.video_encoder_args() == nvenc          # a hardware encoder is kept


def _row(tmp_path, opts=None, start=10.0, end=30.0):
    data = tmp_path / "data"
    (data / "downloads").mkdir(parents=True, exist_ok=True)
    (data / "downloads" / "vid1.mp4").write_bytes(b"the video")
    clip = tmp_path / "clip.mp4"
    if not clip.exists():
        clip.write_bytes(b"the clip")
    return data, {"id": 1, "video_id": "vid1", "start_s": start, "end_s": end, "score": 80, "hook": "",
                  "path": str(clip), "render_opts": json.dumps(opts or {})}


def test_render_base_takes_the_fast_way_and_falls_back_to_the_full_render(tmp_path, monkeypatch):
    pytest.importorskip("yaml")
    data, row = _row(tmp_path)
    config = {"clips": {"outro": True}, "tracking": {}}
    asked = []

    def fast(row, opts, config, source, clip_file, work_dir, progress=None):
        asked.append(("fast", encoding.video_encoder_args()))
        work_dir.mkdir(parents=True, exist_ok=True)
        (work_dir / "base.mp4").write_bytes(b"read back")
        return work_dir / "base.mp4"

    def full(row, opts, config, data_dir, source, work_dir, progress=None):
        asked.append(("full", encoding.video_encoder_args()))
        return work_dir / "full.mp4"

    monkeypatch.setattr(encoding, "_selected", ("cpu", encoding.CPU_ARGS))
    monkeypatch.setattr(fastbase, "make", fast)
    monkeypatch.setattr(build, "_render_full", full)
    assert build.render_base(row, config, data, tmp_path / "work").name == "base.mp4"
    monkeypatch.setattr(fastbase, "make", lambda *a, **k: None)
    assert build.render_base(row, config, data, tmp_path / "work").name == "full.mp4"

    def broken(*a, **k):
        raise ValueError("unreadable")

    monkeypatch.setattr(fastbase, "make", broken)
    assert build.render_base(row, config, data, tmp_path / "work").name == "full.mp4"
    # The full way encodes as the pipeline does: its cut is what the tracker looks at.
    assert asked == [("fast", build.BASE_CPU_ARGS), ("full", encoding.CPU_ARGS), ("full", encoding.CPU_ARGS)]
    assert encoding.video_encoder_args() == encoding.CPU_ARGS          # and as before outside


def test_the_full_renders_bar_moves_with_time_and_never_reaches_the_end():
    shares = [build.time_share(t, 40.0) for t in (0, 5, 20, 39, 40, 41, 80, 400, 4000)]
    assert shares == sorted(shares) and shares[0] == 0.0 and max(shares) < 1.0
    assert build.time_share(20, 40.0) == pytest.approx(0.45)
    assert build.full_estimate(25.0, {"fps_num": 60, "fps_den": 1}) > build.full_estimate(25.0, {"fps_num": 30,
                                                                                                 "fps_den": 1})


def test_the_bases_key_changes_with_what_it_is_made_from(tmp_path):
    pytest.importorskip("yaml")
    data, row = _row(tmp_path, {"crop": "track", "caption_style": {"color": "#FFFFFF"}})
    config = {"clips": {}, "tracking": {"sample_fps": 8}}
    key = build.base_key(row, config, data)
    assert build.base_key(dict(row), config, data) == key
    same = {**row, "render_opts": json.dumps({"crop": "track", "caption_style": {"color": "#FFE600"},
                                              "caption_lines": [{"start": 0, "end": 1, "text": "x"}]})}
    assert build.base_key(same, config, data) == key                  # captions only: the same base
    for changed in ({**row, "render_opts": json.dumps({"crop": "center"})}, {**row, "end_s": 31.0},
                    {**row, "start_s": 9.0}):
        assert build.base_key(changed, config, data) != key, changed
    assert build.base_key(row, {**config, "tracking": {"sample_fps": 4}}, data) != key
    Path(row["path"]).write_bytes(b"the clip, rendered again")
    assert build.base_key(row, config, data) != key                   # the clip's file changed


def test_the_colours_are_turned_as_the_standard_renders_bgr_frames_turn_them(tmp_path):
    """BT.709 video: one matrix change to BT.601, untagged (the standard
    render's frames go through BGR and come back with FFmpeg's default
    matrix, untagged); untagged video: nothing but the tags; anything else
    by way of BGR, exactly as there."""

    def made(name, *tags) -> Path:
        out = tmp_path / name
        _run("-f", "lavfi", "-i", "testsrc2=s=320x180:rate=30", "-t", "0.2", *tags, "-c:v", "libx264",
             "-preset", "ultrafast", str(out))
        return out

    hd = made("hd.mp4", "-pix_fmt", "yuv420p", "-colorspace", "bt709", "-color_primaries", "bt709",
              "-color_trc", "bt709")
    plain = made("plain.mp4", "-pix_fmt", "yuv420p")
    full = made("full.mp4", "-pix_fmt", "yuvj420p")
    deep = made("deep.mp4", "-pix_fmt", "yuv420p10le")
    assert fastbase.colour_steps(hd) == f"colormatrix=bt709:bt601,{fastbase.UNTAGGED}"
    assert fastbase.colour_steps(plain) == fastbase.UNTAGGED
    assert fastbase.colour_steps(full) == f"format=bgr24,{fastbase.UNTAGGED}"
    assert fastbase.colour_steps(deep) == f"format=bgr24,{fastbase.UNTAGGED}"
    graph = fastbase.filter_graph(fastbase.layouts(1920, 1080)[0], "60/1", 10, "crop.cmd", "eq=saturation=1.3",
                                  fastbase.colour_steps(hd))
    assert graph.index("crop@base") < graph.index("colormatrix") < graph.index("scale=1080:1920")
    assert graph.endswith("setsar=1,eq=saturation=1.3[v]")
