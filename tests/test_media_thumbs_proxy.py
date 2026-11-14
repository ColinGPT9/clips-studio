"""Editor suite step 6: thumbnail sheets (video_editor/media/thumbs.py) and
proxies (video_editor/media/proxy.py).

- The spacing, tile size and FFmpeg commands, as data (CI's light install).
- Through real FFmpeg (skipped without it): a source with a keyframe every
  second gives one tile a second from keyframes only, at the keyframes'
  times; a source with sparse keyframes is done again decoding every frame;
  a picture is one tile.
- A proxy from a short generated 1080p60 source, probed: 540p, H.264
  yuv420p, a keyframe every half second, AAC stereo, no longer than 60 fps.
"""

import json
import subprocess

import pytest
from media_helpers import make_picture, make_video, need_ffmpeg

from core import binaries
from video_editor.media import probe, proxy, thumbs


def test_spacing_and_tile_size():
    assert thumbs.spacing(30) == 1 and thumbs.spacing(600) == 1 and thumbs.spacing(601) == 2
    assert thumbs.spacing(7200) == 15                 # 2 hours: 481 tiles
    assert thumbs.spacing(10 ** 9) == thumbs.SPACINGS[-1]
    assert thumbs.tile_size(1920, 1080) == (160, 90)
    assert thumbs.tile_size(1080, 1920) == (50, 90)
    assert thumbs.tile_size(0, 0) == (160, 90)
    assert thumbs.tile_size(10000, 100) == (320, 90)


def test_the_thumbnail_command():
    argv = thumbs.command("ffmpeg", "in.mp4", "out/sheet-%03d.jpg", interval=15, tile=(160, 90), keyframes_only=True)
    assert argv[argv.index("-skip_frame") + 1] == "nokey" and argv.index("-skip_frame") < argv.index("-i")
    chain = argv[argv.index("-vf") + 1]
    assert "gt(floor(t/15)\\,floor(prev_selected_t/15))" in chain and "showinfo" in chain and chain.endswith("tile=10x10")
    assert "-skip_frame" not in thumbs.command("ffmpeg", "in.mp4", "o", interval=1, tile=(160, 90),
                                               keyframes_only=False)


def test_showinfo_lines():
    lines = ["[Parsed_showinfo_4 @ 000001] n:   0 pts:      0 pts_time:0       duration:512",
             "[Parsed_showinfo_4 @ 000001] n:   1 pts: 512000 pts_time:1.000   duration:512",
             "frame=    1 fps=0.0 q=-0.0 size=N/A time=00:00:01.00",
             "[Parsed_showinfo_4 @ 000001] config in time_base: 1/15360"]
    assert thumbs.parse_times(lines) == [0.0, 1.0]


def test_the_proxy_command():
    info = {"has_video": True, "has_audio": True, "display_width": 1920, "display_height": 1080, "fps_value": 59.94}
    argv = proxy.command("ffmpeg", "in.mkv", "out.part", info)
    chain = argv[argv.index("-vf") + 1]
    assert chain.startswith("scale=960:540") and "format=yuv420p" in chain and "fps=" not in chain
    assert argv[argv.index("-g") + 1] == "30" and argv[argv.index("-c:v") + 1] == "libx264"
    assert "expr:gte(t,n_forced*0.5)" in argv and argv[argv.index("-c:a") + 1] == "aac"
    assert argv[-3:] == ["-f", "mp4", "out.part"]
    fast = proxy.command("ffmpeg", "in", "out", {**info, "fps_value": 120.0})
    assert fast[fast.index("-vf") + 1].startswith("fps=60,") and fast[fast.index("-g") + 1] == "30"
    upright = proxy.size_for({"display_width": 1080, "display_height": 1920})
    assert upright == (540, 960)
    assert proxy.size_for({"display_width": 640, "display_height": 360}) == (640, 360)     # never larger
    sound = proxy.command("ffmpeg", "in.wav", "out", {"has_video": False, "has_audio": True})
    assert "-vn" in sound and "-vf" not in sound and proxy.name_for({"has_video": False}) == "proxy.m4a"


# ---- through FFmpeg ---------------------------------------------------------------------


def test_thumbnails_from_keyframes(tmp_path):
    source = make_video(tmp_path / "v.mp4", seconds=12, rate=25, keyint=25)
    info = probe.info(source)
    index = thumbs.make(source, tmp_path / "thumbs", info=info)
    assert index["keyframes_only"] is True and index["interval_us"] == 1_000_000
    assert index["count"] == 12 and len(index["times_us"]) == 12
    assert index["times_us"] == [i * 1_000_000 for i in range(12)]
    assert index["tile"] == {"width": 160, "height": 90} and index["columns"] == 10
    assert [s["count"] for s in index["sheets"]] == [12]
    sheet = tmp_path / "thumbs" / index["sheets"][0]["file"]
    assert sheet.is_file() and probe.info(sheet)["width"] == 1600 and probe.info(sheet)["height"] == 900


def test_more_than_one_sheet(tmp_path):
    source = make_video(tmp_path / "v.mp4", seconds=130, size="160x90", rate=5, keyint=5, audio=False)
    index = thumbs.make(source, tmp_path / "thumbs", info=probe.info(source))
    assert index["count"] == 130 and [s["count"] for s in index["sheets"]] == [100, 30]
    assert [s["first"] for s in index["sheets"]] == [0, 100]
    assert sorted(p.name for p in (tmp_path / "thumbs").iterdir()) == ["sheet-001.jpg", "sheet-002.jpg"]


def test_sparse_keyframes_fall_back_to_every_frame(tmp_path):
    source = make_video(tmp_path / "v.mp4", seconds=12, rate=25, keyint=250)    # a keyframe every 10 s
    index = thumbs.make(source, tmp_path / "thumbs", info=probe.info(source))
    assert index["keyframes_only"] is False
    assert index["count"] == 12 and index["times_us"] == [i * 1_000_000 for i in range(12)]


def test_a_picture_is_one_tile(tmp_path):
    picture = make_picture(tmp_path / "p.png")
    index = thumbs.make(picture, tmp_path / "thumbs", info=probe.info(picture))
    assert index["count"] == 1 and index["times_us"] == [0]


def _keyframe_times(path) -> list[float]:
    out = subprocess.run([binaries.ffprobe(), "-v", "error", "-select_streams", "v:0", "-show_entries",
                          "packet=pts_time,flags", "-of", "json", str(path)], capture_output=True, text=True,
                         check=True)
    return [float(p["pts_time"]) for p in json.loads(out.stdout)["packets"] if "K" in p.get("flags", "")]


def test_a_proxy_is_540p_h264_with_a_keyframe_every_half_second(tmp_path):
    need_ffmpeg()
    source = make_video(tmp_path / "big.mp4", seconds=3, size="1920x1080", rate=60)
    info = probe.info(source)
    target = tmp_path / "proxy.mp4"
    subprocess.run(proxy.command(binaries.ffmpeg(), source, target, info), check=True, capture_output=True)
    made = probe.info(target)
    assert (made["width"], made["height"]) == (960, 540)
    assert made["video_codec"] == "h264" and made["pix_fmt"] == "yuv420p" and made["fps_value"] == 60.0
    assert made["audio_codec"] == "aac" and made["audio_channels"] == 2
    assert abs(made["duration_us"] - info["duration_us"]) < 100_000
    keys = _keyframe_times(target)
    assert keys[0] == 0.0 and len(keys) >= 6
    assert max(b - a for a, b in zip(keys, keys[1:])) <= 0.5 + 1e-6
    assert probe.proxy_reasons(made) == []


def test_a_proxy_of_a_fast_turned_phone_video(tmp_path):
    need_ffmpeg()
    plain = make_video(tmp_path / "plain.mp4", seconds=2, size="1280x720", rate=120, audio=False)
    source = tmp_path / "phone.mp4"
    # A phone's turn: the display matrix (FFmpeg 7: -display_rotation), or
    # the older rotate tag.
    for how in (["-display_rotation:v:0", "90", "-i", str(plain)],
                ["-i", str(plain), "-metadata:s:v:0", "rotate=90"]):
        done = subprocess.run([binaries.ffmpeg(), "-y", "-v", "error", *how, "-c", "copy", str(source)],
                              capture_output=True)
        if done.returncode == 0 and probe.info(source)["rotation"] in (90, 270):
            break
    else:
        pytest.skip("this FFmpeg can't mark a video as turned")
    info = probe.info(source)
    assert (info["display_width"], info["display_height"]) == (720, 1280)
    assert "more than 60 frames a second (120)" in probe.proxy_reasons(info)
    target = tmp_path / "proxy.mp4"
    subprocess.run(proxy.command(binaries.ffmpeg(), source, target, info), check=True, capture_output=True)
    made = probe.info(target)
    assert (made["width"], made["height"], made["rotation"]) == (540, 960, 0)
    assert made["fps_value"] == 60.0 and not made["has_audio"]
