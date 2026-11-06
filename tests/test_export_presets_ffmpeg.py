"""Export presets made for real by FFmpeg, checked with ffprobe.

Platform tier: these need FFmpeg (with libx264), which CI's Plugin platform
job installs; elsewhere they skip. The sources are generated here (FFmpeg's
test pattern, 9:16 at 60 fps, with 96 kHz sound as the app's clips carry), so
nothing is downloaded.

- each preset comes out at its size, codec and frame rate, with 48 kHz AAC
  and the index at the front (+faststart);
- a size target is kept: the file is no bigger than the target;
- WoopSocial's smaller copy of a clip over the limit is a real H.264 file
  under its target, and is gone afterwards;
- the editor lane makes an export end to end, and a cancel kills FFmpeg and
  leaves nothing behind.
"""

import json
import subprocess
import time
from pathlib import Path

import pytest

from core import binaries
from video import encoding
from video import export_presets as ep

pytestmark = pytest.mark.skipif(bool(binaries.missing()), reason="needs FFmpeg and ffprobe")


@pytest.fixture(scope="module")
def ffmpeg_ok():
    if not encoding.export_encoder_works("libx264"):
        pytest.skip("this FFmpeg has no libx264")


def _make(path: Path, *, size="1080x1920", rate=60, seconds=4, noise=False) -> Path:
    # Light noise: busy enough that High comes out far over 5 MB, and still
    # possible to fit in 5 MB. Strong noise can't be: at 1080x1920 it needs
    # more bits than even H.264's coarsest setting gives, so a target that
    # small is missed (and encode() says so with over_target).
    video = f"testsrc2=s={size}:r={rate}:d={seconds}" + (",noise=alls=8:allf=t+u" if noise else "")
    subprocess.run([binaries.ffmpeg(), "-y", "-v", "error", "-f", "lavfi", "-i", video,
                    "-f", "lavfi", "-i", f"sine=f=440:d={seconds}:sample_rate=96000",
                    "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p",
                    "-c:a", "aac", "-b:a", "128k", "-shortest", str(path)], check=True, capture_output=True)
    return path


@pytest.fixture(scope="module")
def clip(tmp_path_factory, ffmpeg_ok) -> Path:
    return _make(tmp_path_factory.mktemp("source") / "clip.mp4")


@pytest.fixture(scope="module")
def busy_clip(tmp_path_factory, ffmpeg_ok) -> Path:
    """Ten seconds of noise: hard to compress, so a size target has to bite."""
    return _make(tmp_path_factory.mktemp("busy") / "busy.mp4", rate=30, seconds=10, noise=True)


def probe(path: Path) -> dict:
    out = subprocess.run([binaries.ffprobe(), "-v", "error", "-show_entries",
                          "stream=codec_type,codec_name,codec_tag_string,width,height,avg_frame_rate,sample_rate"
                          ":format=size,duration", "-of", "json", str(path)],
                         capture_output=True, text=True, check=True).stdout
    data = json.loads(out)
    video = next(s for s in data["streams"] if s["codec_type"] == "video")
    audio = next((s for s in data["streams"] if s["codec_type"] == "audio"), None)
    num, _, den = video["avg_frame_rate"].partition("/")
    return {"codec": video["codec_name"], "tag": video.get("codec_tag_string"), "w": video["width"],
            "h": video["height"], "fps": round(float(num) / float(den or 1), 2),
            "audio": audio and (audio["codec_name"], int(audio["sample_rate"])),
            "size": int(data["format"]["size"]), "duration": float(data["format"]["duration"])}


def index_first(path: Path) -> bool:
    head = path.read_bytes()[:256 * 1024]
    return 0 <= head.find(b"moov") < head.find(b"mdat")


CASES = {
    "720p": ({"resolution": "720p"}, ("h264", 720, 1280, 60.0)),
    "square_blur_30fps": ({"aspect": "1:1", "fill": "blur", "fps": 30}, ("h264", 1080, 1080, 30.0)),
    "landscape_bars_540p": ({"aspect": "16:9", "fill": "bars", "resolution": "540p", "bar_color": "white"},
                            ("h264", 960, 540, 60.0)),
    "4_5_crop_720p_24fps": ({"aspect": "4:5", "resolution": "720p", "fps": 24}, ("h264", 720, 900, 24.0)),
    "smallest_360p": ({"quality": "smallest", "resolution": "360p"}, ("h264", 360, 640, 60.0)),
    "hevc": ({"codec": "hevc", "resolution": "720p"}, ("hevc", 720, 1280, 60.0)),
}


@pytest.mark.parametrize("case", list(CASES))
def test_each_preset_comes_out_as_asked(case, clip, tmp_path):
    raw, (codec, w, h, fps) = CASES[case]
    preset = ep.normalize({"kind": "custom", "encoder": "cpu", **raw})
    if codec == "hevc" and not encoding.export_encoder_works("libx265"):
        pytest.skip("this FFmpeg has no libx265")
    out = tmp_path / f"{case}.mp4"
    got = ep.encode(clip, out, preset, workdir=tmp_path / "work")
    seen = probe(out)
    assert (seen["codec"], seen["w"], seen["h"], seen["fps"]) == (codec, w, h, fps)
    assert seen["audio"] == ("aac", 48000)
    assert seen["size"] == got["bytes"] == out.stat().st_size
    assert abs(seen["duration"] - 4.0) < 0.25
    assert index_first(out), "the index is at the front (+faststart)"
    if codec == "hevc":
        assert seen["tag"] == "hvc1"


def test_a_bitrate_comes_out_close_to_its_estimate(clip, tmp_path):
    preset = ep.normalize({"kind": "custom", "encoder": "cpu", "quality": "custom", "rate": "bitrate",
                           "bitrate_kbps": 1500})
    out = tmp_path / "bitrate.mp4"
    got = ep.encode(clip, out, preset, workdir=tmp_path / "work")
    est = ep.estimate(4.0, preset, source=ep.probe(clip), encoder=encoding.Encoder("h264", "cpu", "libx264"))
    assert got["mode"] == "bitrate" and est["kind"] == "close"
    assert 0.75 * est["bytes"] <= out.stat().st_size <= 1.2 * est["bytes"]


def test_a_size_target_is_kept(busy_clip, tmp_path):
    source = ep.probe(busy_clip)
    preset = ep.normalize({"kind": "custom", "encoder": "cpu", "quality": "high", "max_mb": 5})
    plan = ep.make_plan(source, preset, encoding.Encoder("h264", "cpu", "libx264"))
    assert plan.mode == "target" and plan.passes == 2
    assert plan.video_kbps == ep.target_video_kbps(5 * ep.MB, source.duration, 128), "the target binds here"
    high = tmp_path / "high.mp4"
    ep.encode(busy_clip, high, {**preset, "max_mb": None}, workdir=tmp_path / "work", source=source)
    assert high.stat().st_size > 5 * ep.MB, "without the target this clip is over 5 MB"
    out = tmp_path / "under5.mp4"
    got = ep.encode(busy_clip, out, preset, workdir=tmp_path / "work", source=source)
    size = out.stat().st_size
    assert size <= 5 * ep.MB, f"{size} bytes is over 5 MB"
    assert size >= 3 * ep.MB, "the target was used, not something far smaller"
    assert got["over_target"] is False
    assert probe(out)["codec"] == "h264"


def test_a_smaller_copy_for_woopsocial_is_real_and_then_gone(busy_clip, tmp_path, monkeypatch):
    from publish import woopsocial as ws

    monkeypatch.setattr(ws, "SINGLE_UPLOAD_MAX_BYTES", busy_clip.stat().st_size - 1)
    monkeypatch.setattr(ws, "SMALLER_COPY_BYTES", 2_000_000)
    monkeypatch.setattr(encoding, "_mode", "cpu")    # this test's encoder, whatever the PC has
    sent = []

    class Client:
        def social_accounts(self, project_id=""):
            return [{"platform": "YOUTUBE", "id": "a1"}]

        def upload_media(self, video, project_id):
            sent.append((Path(video), probe(Path(video))))
            return "media-1"

        def create_post(self, body):
            return {"id": "p", "socialAccountPosts": [{"platform": "YOUTUBE", "status": "SCHEDULED"}]}

    ws.WoopSocialPublisher(Client(), "proj", smaller_copy=True).start(
        busy_clip, platforms=["youtube"], title="T", text="t")
    ((copy, seen),) = sent
    assert copy != busy_clip and not copy.exists()
    assert seen["codec"] == "h264" and seen["size"] <= 2_000_000
    assert (seen["w"], seen["h"]) == (720, 1280), "too few bits left for 1080x1920, so 720p"


def test_the_lane_makes_an_export_and_a_cancel_leaves_nothing(clip, busy_clip, tmp_path):
    from server.editor_lane import EditorLane

    events = []
    lane = EditorLane(tmp_path / "data", publish=events.append)
    out = tmp_path / "exports"
    lane.submit_export(source=clip, folder=out, name="clip", preset={"kind": "custom", "resolution": "720p",
                                                                         "encoder": "cpu"})
    assert lane.run_pending() == 1
    done = events[-1]["export"]
    assert done["status"] == "done", done
    assert (probe(Path(done["result"]["path"]))["w"], done["result"]["width"]) == (720, 720)

    lane.start()
    try:
        slow = {"kind": "custom", "encoder": "cpu", "quality": "custom", "rate": "bitrate", "bitrate_kbps": 20000,
                "resolution": "1440p"}
        job = lane.submit_export(source=busy_clip, folder=out, name="busy", preset=slow)
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline and lane.get(job["export_id"])["fraction"] < 0.05:
            time.sleep(0.05)
        assert lane.get(job["export_id"])["status"] == "running"
        got = lane.cancel(job["export_id"], wait=15)
    finally:
        lane.stop()
        lane.join(15)
    assert got["status"] == "cancelled", got
    assert sorted(p.name for p in out.iterdir()) == ["clip.mp4"], "no partial file and no export"
    assert lane._read_partials() == []
