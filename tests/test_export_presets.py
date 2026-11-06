"""Export presets (video/export_presets.py): sizes, shapes, frame rates,
the arguments each encoder gets, 2-pass, the size target and its retry, the
estimate, and that nothing a request sends reaches FFmpeg as text.

Standard library only: this runs on CI's light install. Nothing here starts a
process; the encode tests hand encode() a fake runner. Real encodes, checked
with ffprobe, are in tests/test_export_presets_ffmpeg.py.
"""

from pathlib import Path

import pytest

from video import encoding
from video import export_presets as ep

CPU264 = encoding.Encoder("h264", "cpu", "libx264")
CPU265 = encoding.Encoder("hevc", "cpu", "libx265")
NV264 = encoding.Encoder("h264", "nvenc", "h264_nvenc")
NV265 = encoding.Encoder("hevc", "nvenc", "hevc_nvenc")
NVAV1 = encoding.Encoder("av1", "nvenc", "av1_nvenc")
QSV264 = encoding.Encoder("h264", "qsv", "h264_qsv")
AMF264 = encoding.Encoder("h264", "amf", "h264_amf")
VT264 = encoding.Encoder("h264", "videotoolbox", "h264_videotoolbox")

# The app's own clip: 1080x1920 at 60 fps, a minute long, at the baseline's bitrate.
CLIP = ep.Source(width=1080, height=1920, fps=60.0, duration=60.0, has_audio=True, video_kbps=6600.0,
                 size=50_000_000)
HERE = Path(__file__).resolve().parent
SRC = HERE / "no-such-clip.mp4"      # absolute; nothing reads it
DST = HERE / "no-such-export.mp4"


def preset(**changes) -> dict:
    return ep.normalize({"kind": "custom", **changes})


def video_block(plan: ep.Plan, pass_no=None) -> list[str]:
    return ep._video_args(plan, pass_no, ep.PASSLOG)


# ---- Original -------------------------------------------------------------------------


def test_original_is_the_default_and_means_the_copy():
    assert ep.normalize(None)["kind"] == "original"
    assert ep.normalize("original")["kind"] == "original"
    assert ep.is_original(None) and ep.is_original({"kind": "original"})
    assert not ep.is_original({"kind": "custom"})
    # Every key is always there, so a window can show the custom choices too.
    assert set(ep.normalize(None)) == set(ep.CUSTOM_DEFAULTS)
    with pytest.raises(ep.PresetError):
        ep.make_plan(CLIP, ep.normalize(None), CPU264)


# ---- sizes ----------------------------------------------------------------------------

SHORT_SIDE = [
    ("720p", "9:16", (720, 1280)), ("720p", "16:9", (1280, 720)), ("720p", "1:1", (720, 720)),
    ("720p", "4:5", (720, 900)), ("240p", "9:16", (240, 426)), ("360p", "9:16", (360, 640)),
    ("480p", "9:16", (480, 854)), ("540p", "9:16", (540, 960)), ("1080p", "9:16", (1080, 1920)),
    ("1440p", "9:16", (1440, 2560)), ("2160p", "9:16", (2160, 3840)), ("2160p", "16:9", (3840, 2160)),
    ("1080p", "4:5", (1080, 1350)), ("540p", "16:9", (960, 540)),
]


@pytest.mark.parametrize("resolution,aspect,size", SHORT_SIDE)
def test_sizes_count_the_short_side(resolution, aspect, size):
    assert ep.output_size(1080, 1920, preset(resolution=resolution, aspect=aspect)) == size


@pytest.mark.parametrize("src", [(1080, 1920), (1920, 1080), (1081, 1921), (721, 1279), (1000, 1000)])
def test_every_size_is_even(src):
    for resolution in ("same", *ep.RESOLUTIONS):
        for aspect in ("same", *ep.ASPECTS, "custom"):
            w, h = ep.output_size(*src, preset(resolution=resolution, aspect=aspect, aspect_w=7, aspect_h=3))
            assert w % 2 == 0 and h % 2 == 0, (src, resolution, aspect, w, h)
            assert max(w, h) <= ep.MAX_SIDE
    w, h = ep.output_size(*src, preset(resolution="custom", width=1001, height=777))
    assert w % 2 == 0 and h % 2 == 0


def test_same_as_clip_and_custom_shapes():
    assert ep.output_size(1080, 1920, preset()) == (1080, 1920)
    assert ep.output_size(1920, 1080, preset()) == (1920, 1080)
    assert ep.output_size(1080, 1920, preset(aspect="1:1")) == (1080, 1080)
    assert ep.output_size(1080, 1920, preset(resolution="720p", aspect="custom", aspect_w=2, aspect_h=3)) == (720, 1080)
    # The long side stops at 4096, keeping the shape.
    assert ep.output_size(1080, 1920, preset(resolution="2160p", aspect="custom", aspect_w=1, aspect_h=32)) == \
        (128, 4096)
    # A custom size is the size, whatever the shape setting says.
    assert ep.output_size(1080, 1920, preset(resolution="custom", width=1280, height=720, aspect="1:1")) == \
        (1280, 720)


def test_upscale_is_flagged_whenever_the_clip_is_enlarged():
    def up(**p) -> bool:
        return ep.make_plan(CLIP, preset(**p), CPU264).upscale

    assert up(resolution="1440p")
    assert up(resolution="2160p")
    assert not up(resolution="1080p")
    assert not up(resolution="720p")
    assert up(aspect="16:9", fill="crop")       # 1920x1080 cut from a 1080-wide clip: zoomed in
    assert not up(aspect="16:9", fill="bars")   # fitted inside: smaller
    assert not up(aspect="16:9", fill="blur")
    assert not up(aspect="1:1", fill="crop")    # 1080x1080 from 1080x1920: the same scale
    assert up(resolution="custom", width=2160, height=3840)
    assert ep.estimate(60, preset(resolution="1440p"))["upscale"] is True


# ---- frame rate ------------------------------------------------------------------------


def test_the_frame_rate_is_never_raised():
    assert ep.output_fps(30.0, 60) == (30.0, None)
    assert ep.output_fps(30.0, 30) == (30.0, None)
    assert ep.output_fps(60.0, 30) == (30.0, "30")
    assert ep.output_fps(60.0, 25) == (25.0, "25")
    assert ep.output_fps(60.0, "same") == (60.0, None)


def test_ntsc_clips_keep_their_rates():
    fps, expr = ep.output_fps(60000 / 1001, 30)
    assert expr == "30000/1001" and abs(fps - 29.97) < 0.01
    assert ep.output_fps(60000 / 1001, 60) == (60000 / 1001, None)   # already that rate
    assert ep.output_fps(30000 / 1001, 25)[1] == "25"


# ---- the picture -----------------------------------------------------------------------


def test_filters_for_each_fill():
    crop = ep.make_plan(CLIP, preset(aspect="16:9", resolution="720p", fill="crop"), CPU264).vf
    assert crop == ("scale=1280:720:force_original_aspect_ratio=increase:flags=lanczos,crop=1280:720,setsar=1")
    bars = ep.make_plan(CLIP, preset(aspect="16:9", resolution="720p", fill="bars", bar_color="white"), CPU264).vf
    assert bars == ("scale=1280:720:force_original_aspect_ratio=decrease:force_divisible_by=2:flags=lanczos,"
                    "pad=1280:720:(ow-iw)/2:(oh-ih)/2:color=0xFFFFFF,setsar=1")
    blur = ep.make_plan(CLIP, preset(aspect="16:9", resolution="720p", fill="blur", fps=30), CPU264).vf
    assert blur.startswith("fps=30,split=2[bg][fg];[bg]scale=1280:720:force_original_aspect_ratio=increase,")
    assert "gblur=sigma=12" in blur and "scale=160:90" in blur and blur.endswith("overlay=(W-w)/2:(H-h)/2,setsar=1")
    same_shape = ep.make_plan(CLIP, preset(resolution="720p"), CPU264).vf
    assert same_shape == "scale=720:1280:flags=lanczos,setsar=1"
    assert ep.make_plan(CLIP, preset(), CPU264).vf is None            # nothing to change
    assert ep.make_plan(CLIP, preset(fps=30), CPU264).vf == "fps=30"


# ---- checking what a request sends ---------------------------------------------------------

BAD = [
    {"resolution": "999p"}, {"resolution": "720"}, {"aspect": "21:9"}, {"fill": "stretch"},
    {"quality": "ultra"}, {"rate": "vbr"}, {"codec": "vp9"}, {"codec": "H264"}, {"encoder": "nvenc"},
    {"fps": 120}, {"fps": 29.97}, {"fps": "30fps"}, {"bar_color": "red"}, {"bar_color": "#12345G"},
    {"bar_color": "#000000;drawtext=text=x"}, {"crf": "twenty"}, {"crf": "1; rm -rf"}, {"width": True},
    {"width": float("nan")}, {"max_mb": float("inf")}, {"audio_kbps": [128]}, {"kind": "copy"},
    {"unknown": 1}, {"vf": "drawtext=text=hi"},
]


@pytest.mark.parametrize("bad", BAD, ids=lambda b: next(iter(b)))
def test_values_outside_the_allow_lists_are_refused(bad):
    with pytest.raises(ep.PresetError):
        ep.normalize({"kind": "custom", **bad})


@pytest.mark.parametrize("bad", ["720p", "default", 42, ["original"], True])
def test_a_preset_is_an_object_or_original(bad):
    with pytest.raises(ep.PresetError):
        ep.normalize(bad)


def test_numbers_are_clamped_into_range():
    p = preset(crf=99, audio_kbps=10, max_mb=1, bitrate_kbps=1, width=99999, height=101, aspect_w=0, aspect_h=500)
    assert (p["crf"], p["audio_kbps"], p["max_mb"], p["bitrate_kbps"]) == (51, 64, 5.0, 100)
    assert (p["width"], p["height"], p["aspect_w"], p["aspect_h"]) == (4096, 128, 1, 32)
    p = preset(crf=0, audio_kbps=999, max_mb=99999, bitrate_kbps="2500", fps="30")
    assert (p["crf"], p["audio_kbps"], p["max_mb"], p["bitrate_kbps"], p["fps"]) == (1, 320, 4000.0, 2500, 30)
    assert preset(max_mb=None)["max_mb"] is None and preset(max_mb="")["max_mb"] is None
    assert preset(bar_color="black")["bar_color"] == "#000000"
    assert preset(bar_color="#abcdef")["bar_color"] == "#ABCDEF"
    # JSON integers too big for a float are clamped too, never an OverflowError.
    huge = 10**400
    p = preset(width=huge, height=-huge, crf=huge, bitrate_kbps=-huge, audio_kbps=huge, max_mb=huge,
               aspect_w=huge, aspect_h=-huge)
    assert (p["width"], p["height"], p["crf"], p["bitrate_kbps"], p["audio_kbps"]) == (4096, 128, 51, 100, 320)
    assert (p["max_mb"], p["aspect_w"], p["aspect_h"]) == (4000.0, 32, 1)
    with pytest.raises(ep.PresetError):
        preset(crf=float("inf"))


def test_every_offered_choice_is_accepted():
    for key, values in ep.choices().items():
        for value in values:
            assert ep.normalize({"kind": "custom", key: value})


def _argv_tokens(cmds: list[list[str]]) -> set[str]:
    return {token for cmd in cmds for token in cmd[1:]}


def test_no_request_text_reaches_ffmpeg():
    """Every argument is a fixed token, a number the code formatted, one of the
    two absolute paths, or a filter built from those. A request's own strings
    only ever pick from allow-lists."""
    allowed_text = {
        "-y", "-v", "error", "-nostdin", "-progress", "pipe:1", "-nostats", "-i", "-map", "0:v:0", "0:a:0?",
        "-vf", "-pix_fmt", "yuv420p", "-c:v", "-preset", "medium", "fast", "p5", "-crf", "-cq", "-rc", "vbr",
        "-b:v", "-maxrate", "-bufsize", "-global_quality", "-quality", "quality", "-x265-params", "-tag:v", "hvc1",
        "-c:a", "aac", "-b:a", "-ar", "48000", "-an", "-movflags", encoding.FINAL_MOVFLAGS, "-f", "mp4", "null",
        "-", "-pass", "-passlogfile", ep.PASSLOG, *encoding.EXPORT_BASE_ARGS,
    }
    for raw in ({"resolution": "720p", "aspect": "4:5", "fill": "bars", "bar_color": "#102030", "fps": 25},
                {"codec": "hevc", "quality": "custom", "rate": "bitrate", "bitrate_kbps": 3333, "audio_kbps": 96},
                {"aspect": "custom", "aspect_w": 5, "aspect_h": 7, "fill": "blur", "max_mb": 12.5}):
        p = preset(**raw)
        for enc in (CPU264, CPU265, NV264, NV265, QSV264, AMF264, VT264):
            if enc.codec != p["codec"]:
                continue
            plan = ep.make_plan(CLIP, p, enc)
            for token in _argv_tokens(ep.commands(plan, SRC, DST)):
                ok = (token in allowed_text or token in (str(SRC), str(DST)) or token == plan.vf
                      or token.rstrip("k").isdigit() or token.startswith("log-level=error"))
                assert ok, f"{token!r} in an export command ({enc.name}, {raw})"


def test_relative_paths_are_refused():
    plan = ep.make_plan(CLIP, preset(resolution="720p"), CPU264)
    with pytest.raises(ep.ExportError):
        ep.commands(plan, "clip.mp4", DST)
    with pytest.raises(ep.ExportError):
        ep.commands(plan, SRC, "tcp:/evil:80/x.mp4")


# ---- the arguments each encoder gets -------------------------------------------------------


def test_quality_levels_per_encoder():
    def block(enc, quality="balanced"):
        return video_block(ep.make_plan(CLIP, preset(quality=quality, codec=enc.codec), enc))

    assert block(CPU264) == ["-c:v", "libx264", "-preset", "medium", "-crf", "23"]
    assert block(CPU264, "smallest")[-1] == "27" and block(CPU264, "high")[-1] == "18"
    assert block(NV264) == ["-c:v", "h264_nvenc", "-preset", "p5", "-rc", "vbr", "-cq", "25", "-b:v", "0"]
    assert block(QSV264) == ["-c:v", "h264_qsv", "-preset", "medium", "-global_quality", "24"]
    assert block(CPU265) == ["-c:v", "libx265", "-preset", "fast", "-x265-params", "log-level=error", "-crf", "28"]
    assert block(NV265) == ["-c:v", "hevc_nvenc", "-preset", "p5", "-rc", "vbr", "-cq", "28", "-b:v", "0"]
    assert block(NVAV1)[-4:] == ["-cq", "36", "-b:v", "0"]
    # AMD and Apple run at a bitrate worked out from the estimate.
    amf = ep.make_plan(CLIP, preset(), AMF264)
    kbps = amf.video_kbps
    assert amf.rate == "kbps" and kbps > 0
    assert video_block(amf) == ["-c:v", "h264_amf", "-quality", "quality", "-b:v", f"{kbps}k",
                                "-maxrate", f"{round(kbps * 1.5)}k", "-bufsize", f"{kbps * 2}k"]
    vt = ep.make_plan(CLIP, preset(), VT264)
    assert video_block(vt) == ["-c:v", "h264_videotoolbox", "-b:v", f"{vt.video_kbps}k",
                               "-maxrate", f"{round(vt.video_kbps * 1.5)}k"]


def test_a_custom_crf_is_used_as_given_on_each_encoders_scale():
    for enc in (CPU264, NV264, QSV264, CPU265):
        plan = ep.make_plan(CLIP, preset(quality="custom", rate="crf", crf=31, codec=enc.codec), enc)
        assert plan.crf == 31 and plan.passes == 1


def test_the_whole_command_for_a_720p_export():
    plan = ep.make_plan(CLIP, preset(resolution="720p"), CPU264)
    (cmd,) = ep.commands(plan, SRC, DST)
    assert cmd[1:] == [
        "-y", "-v", "error", "-nostdin", "-progress", "pipe:1", "-nostats", "-i", str(SRC),
        "-map", "0:v:0", "-map", "0:a:0?", "-vf", "scale=720:1280:flags=lanczos,setsar=1", "-pix_fmt", "yuv420p",
        "-c:v", "libx264", "-preset", "medium", "-crf", "23",
        "-c:a", "aac", "-b:a", "128k", "-ar", "48000",
        "-movflags", "+faststart+negative_cts_offsets", "-f", "mp4", str(DST),
    ]


def test_hevc_is_tagged_for_phones_and_a_clip_without_sound_gets_none():
    plan = ep.make_plan(CLIP, preset(codec="hevc"), CPU265)
    (cmd,) = ep.commands(plan, SRC, DST)
    assert cmd[cmd.index("-tag:v") + 1] == "hvc1"
    silent = ep.make_plan(ep.Source(1080, 1920, 30.0, 10.0, has_audio=False), preset(), CPU264)
    (cmd,) = ep.commands(silent, SRC, DST)
    assert "-an" in cmd and "-c:a" not in cmd


def test_a_bitrate_is_two_passes_on_the_cpu():
    plan = ep.make_plan(CLIP, preset(quality="custom", rate="bitrate", bitrate_kbps=3000), CPU264)
    assert (plan.mode, plan.passes, plan.video_kbps) == ("bitrate", 2, 3000)
    first, second = ep.commands(plan, SRC, DST)
    assert first[first.index("-c:v"):] == ["-c:v", "libx264", "-preset", "medium", "-pass", "1",
                                           "-passlogfile", ep.PASSLOG, "-b:v", "3000k", "-an", "-f", "null", "-"]
    assert "-pass" in second and second[second.index("-pass") + 1] == "2"
    assert second[-1] == str(DST) and "-c:a" in second
    hevc = ep.make_plan(CLIP, preset(codec="hevc", quality="custom", rate="bitrate", bitrate_kbps=2000), CPU265)
    first, second = ep.commands(hevc, SRC, DST)
    assert f"log-level=error:pass=1:stats={ep.PASSLOG}.x265" in first
    assert f"log-level=error:pass=2:stats={ep.PASSLOG}.x265" in second


def test_a_bitrate_is_one_capped_pass_on_a_graphics_card():
    plan = ep.make_plan(CLIP, preset(quality="custom", rate="bitrate", bitrate_kbps=4000), NV264)
    (cmd,) = ep.commands(plan, SRC, DST)
    assert cmd[cmd.index("-c:v"):cmd.index("-c:v") + 12] == [
        "-c:v", "h264_nvenc", "-preset", "p5", "-rc", "vbr", "-b:v", "4000k", "-maxrate", "6000k",
        "-bufsize", "8000k"]
    qsv = ep.make_plan(CLIP, preset(quality="custom", rate="bitrate", bitrate_kbps=4000), QSV264)
    assert video_block(qsv)[-6:] == ["-b:v", "4000k", "-maxrate", "6000k", "-bufsize", "8000k"]


# ---- the size target ---------------------------------------------------------------------


def test_the_size_target_formula_and_its_floor():
    # 0.95 x 8 x 100 MB / 120 s = 6,333 kbps, less 128 for the sound.
    assert ep.target_video_kbps(100 * ep.MB, 120, 128) == 6205
    assert ep.target_video_kbps(5 * ep.MB, 3600, 128) == ep.MIN_VIDEO_KBPS


def test_a_size_target_on_the_cpu_is_2_pass_and_on_a_card_is_capped_vbr():
    long_clip = ep.Source(1080, 1920, 60.0, 180.0, video_kbps=6600.0)
    p = preset(max_mb=100)
    cpu = ep.make_plan(long_clip, p, CPU264)
    assert (cpu.mode, cpu.passes, cpu.max_bytes) == ("target", 2, 100 * ep.MB)
    assert cpu.video_kbps == ep.target_video_kbps(100 * ep.MB, 180, 128)
    nv = ep.make_plan(long_clip, p, NV264)
    k = nv.video_kbps
    assert nv.passes == 1
    assert video_block(nv)[-6:] == ["-b:v", f"{k}k", "-maxrate", f"{round(k * 1.2)}k", "-bufsize", f"{k}k"]


def test_a_short_clip_is_not_padded_up_to_its_size_target():
    """'Keep under 100 MB' on a 20-second clip gets the quality asked for, not 95 MB."""
    short = ep.Source(1080, 1920, 60.0, 20.0, video_kbps=6600.0)
    plan = ep.make_plan(short, preset(max_mb=100), CPU264)
    assert plan.video_kbps < ep.target_video_kbps(100 * ep.MB, 20, 128) / 3
    assert plan.mode == "target" and plan.target_reachable is True


def test_an_unreachable_target_is_said_so():
    long_clip = ep.Source(1080, 1920, 60.0, 3600.0, video_kbps=6600.0)
    plan = ep.make_plan(long_clip, preset(max_mb=5), CPU264)
    assert plan.target_reachable is False and plan.video_kbps == ep.MIN_VIDEO_KBPS
    assert ep.estimate(3600, preset(max_mb=5), source=long_clip)["target_reachable"] is False


# ---- the estimate --------------------------------------------------------------------------


def test_estimates_are_a_range_for_quality_and_close_for_bitrates():
    est = ep.estimate(60, preset(resolution="720p"))
    assert est["kind"] == "range" and est["low"] < est["bytes"] < est["high"]
    assert (est["width"], est["height"], est["fps"]) == (720, 1280, 60.0)
    assert est["per_minute"]["bytes"] == est["bytes"]
    bitrate = ep.estimate(120, preset(quality="custom", rate="bitrate", bitrate_kbps=2000, audio_kbps=128))
    assert bitrate["kind"] == "close"
    assert bitrate["bytes"] == pytest.approx((2000 + 128) * 125 * 120 * 1.01, rel=0.001)
    assert bitrate["per_minute"]["bytes"] == pytest.approx(bitrate["bytes"] / 2, rel=0.001)
    target = ep.estimate(240, preset(max_mb=100))
    assert target["kind"] == "close" and target["high"] <= 100 * ep.MB and target["target_bytes"] == 100 * ep.MB


def test_original_estimates_the_clip_as_it_is():
    clip = ep.estimate(60, None, source=CLIP)
    assert (clip["kind"], clip["bytes"], clip["codec"]) == ("copy", 50_000_000, "copy")
    # Without a clip: the step-0 baseline, 48-54 MB a minute for 1080x1920 60 fps.
    typical = ep.estimate(60, None)
    assert typical["kind"] == "range" and 48e6 <= typical["bytes"] <= 54e6


def test_a_busier_clip_is_estimated_bigger():
    calm = ep.estimate(60, preset(), source=ep.Source(1080, 1920, 30.0, 60.0, video_kbps=2000.0))
    busy = ep.estimate(60, preset(), source=ep.Source(1080, 1920, 30.0, 60.0, video_kbps=9000.0))
    assert busy["bytes"] > 2 * calm["bytes"]
    unknown = ep.estimate(60, preset())
    assert (unknown["high"] - unknown["low"]) / unknown["bytes"] > (busy["high"] - busy["low"]) / busy["bytes"]


def test_smaller_files_for_lower_quality_sizes_and_frame_rates():
    def size(**p) -> int:
        return ep.estimate(60, preset(**p))["bytes"]

    assert size(quality="smallest") < size(quality="balanced") < size(quality="high")
    assert size(resolution="480p") < size(resolution="720p") < size(resolution="1080p")
    assert size(fps=30) < size()
    assert size(codec="hevc") < size()


# ---- encode(): the size check, the retry and the fallback ---------------------------------------


class FakeRun:
    """Stands in for FFmpeg: records each command and writes the output file
    at a size worked out from the command's bitrate (or a fixed size)."""

    def __init__(self, bytes_for=None, fail=()):
        self.commands: list[list[str]] = []
        self.bytes_for = bytes_for or (lambda kbps: 1000)
        self.fail = set(fail)

    def __call__(self, argv, *, cwd=None, duration=0.0, on_progress=None):
        self.commands.append(list(argv))
        name = argv[argv.index("-c:v") + 1]
        if name in self.fail:
            return 1, "Cannot load nvcuda.dll\nError while opening encoder"
        if on_progress is not None:
            on_progress(0.5)
            on_progress(1.0)
        if argv[-1] != "-":
            kbps = int(argv[argv.index("-b:v") + 1].rstrip("k")) if "-b:v" in argv else 0
            Path(argv[-1]).write_bytes(b"\0" * int(self.bytes_for(kbps)))
        return 0, ""


def test_a_file_over_its_target_is_made_once_more_at_0_9x(tmp_path):
    long_clip = ep.Source(1080, 1920, 60.0, 180.0, video_kbps=6600.0)
    target = 50 * ep.MB
    first = ep.target_video_kbps(target, 180, 128)
    run = FakeRun(bytes_for=lambda kbps: target + 1 if kbps >= first else target - 1)
    seen = []
    got = ep.encode(SRC, tmp_path / "out.mp4", preset(max_mb=50), workdir=tmp_path / "work", run=run,
                    encoder=CPU264, source=long_clip, on_progress=lambda f, step: seen.append(f))
    rates = [c[c.index("-b:v") + 1] for c in run.commands]
    # pass 1, pass 2, then pass 2 again at 0.9x (the first pass is reused)
    assert rates == [f"{first}k", f"{first}k", f"{int(first * 0.9)}k"]
    assert "-pass" in run.commands[2] and run.commands[2][run.commands[2].index("-pass") + 1] == "2"
    assert got["retried"] is True and got["over_target"] is False and got["bytes"] == target - 1
    assert seen == sorted(seen), "progress never goes back"


def test_a_file_still_over_after_the_retry_says_so(tmp_path):
    long_clip = ep.Source(1080, 1920, 60.0, 180.0, video_kbps=6600.0)
    run = FakeRun(bytes_for=lambda kbps: 60 * ep.MB)
    got = ep.encode(SRC, tmp_path / "out.mp4", preset(max_mb=50), workdir=tmp_path, run=run, encoder=NV264,
                    source=long_clip)
    assert len(run.commands) == 2 and got["over_target"] is True and got["retried"] is True


def test_no_retry_without_a_target(tmp_path):
    run = FakeRun(bytes_for=lambda kbps: 999 * ep.MB)
    got = ep.encode(SRC, tmp_path / "out.mp4", preset(), workdir=tmp_path, run=run, encoder=CPU264, source=CLIP)
    assert len(run.commands) == 1 and got["retried"] is False and got["over_target"] is False


def test_a_graphics_card_that_fails_is_replaced_by_the_cpu(tmp_path, monkeypatch):
    monkeypatch.setattr(encoding, "encoder_for", lambda codec, pref="auto": CPU264 if pref == "cpu" else None)
    run = FakeRun(fail={"h264_nvenc"})
    got = ep.encode(SRC, tmp_path / "out.mp4", preset(resolution="720p"), workdir=tmp_path, run=run,
                    encoder=NV264, source=CLIP)
    assert [c[c.index("-c:v") + 1] for c in run.commands] == ["h264_nvenc", "libx264"]
    assert got["fell_back_to_cpu"] is True and got["encoder"] == "libx264" and got["crf"] == 23


def test_a_cpu_failure_is_an_export_error_with_ffmpegs_words(tmp_path):
    run = FakeRun(fail={"libx264"})
    with pytest.raises(ep.ExportError, match="Error while opening encoder"):
        ep.encode(SRC, tmp_path / "out.mp4", preset(), workdir=tmp_path, run=run, encoder=CPU264, source=CLIP)


def test_a_cancel_is_never_retried_on_the_cpu(tmp_path, monkeypatch):
    monkeypatch.setattr(encoding, "encoder_for", lambda *a, **k: pytest.fail("no fallback after a cancel"))

    def cancelled(argv, **_kwargs):
        raise ep.Cancelled()

    with pytest.raises(ep.Cancelled):
        ep.encode(SRC, tmp_path / "out.mp4", preset(), workdir=tmp_path, run=cancelled, encoder=NV264, source=CLIP)


def test_av1_needs_a_graphics_card(monkeypatch):
    monkeypatch.setattr(encoding, "encoder_for", lambda codec, pref="auto": None)
    with pytest.raises(ep.ExportError, match="AV1"):
        ep.pick_encoder(preset(codec="av1"))
    monkeypatch.setattr(encoding, "encoder_for", lambda codec, pref="auto": CPU264 if pref == "cpu" else None)
    assert ep.pick_encoder(preset(encoder="gpu")) == CPU264      # H.264 falls back to the CPU


# ---- the smaller copy (publish/woopsocial.py) ------------------------------------------------


def test_a_smaller_copy_keeps_the_clips_size_when_it_can():
    # 165 s (a 140 MB clip at the baseline's rate) under 95 MB: enough bits for 1080x1920.
    keep = ep.smaller_copy_preset(ep.Source(1080, 1920, 60.0, 165.0), 95 * ep.MB)
    assert keep["resolution"] == "same" and keep["codec"] == "h264" and keep["fps"] == "same"
    # 5 minutes would leave too few: 720p instead.
    small = ep.smaller_copy_preset(ep.Source(1080, 1920, 60.0, 300.0), 95 * ep.MB)
    assert small["resolution"] == "720p"
    assert ep.output_size(1080, 1920, small) == (720, 1280)
    # Already 720p or smaller: never enlarged.
    assert ep.smaller_copy_preset(ep.Source(720, 1280, 60.0, 900.0), 95 * ep.MB)["resolution"] == "same"


def test_a_smaller_copy_aims_under_the_bytes_it_is_given(tmp_path):
    source = ep.Source(1080, 1920, 60.0, 165.0, video_kbps=6600.0)
    run = FakeRun(bytes_for=lambda kbps: kbps * 125 * 165)
    got = ep.encode(SRC, tmp_path / "out.mp4", ep.smaller_copy_preset(source, 3_000_000), workdir=tmp_path,
                    run=run, encoder=CPU264, source=source, max_bytes=3_000_000)
    assert got["target_bytes"] == 3_000_000 and got["bytes"] <= 3_000_000


# ---- progress ---------------------------------------------------------------------------------


@pytest.mark.parametrize("line,seconds", [
    ("out_time_us=1500000", 1.5), ("out_time_ms=1500000", 1.5), ("out_time=00:01:02.500000", 62.5),
    ("out_time_us=N/A", None), ("frame=12", None), ("progress=end", None), ("", None),
])
def test_progress_lines(line, seconds):
    assert ep.progress_seconds(line) == seconds
