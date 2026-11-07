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
    ("720p", "4:5", (720, 900)), ("1080p", "9:16", (1080, 1920)), ("1080p", "16:9", (1920, 1080)),
    ("1440p", "9:16", (1440, 2560)), ("2160p", "9:16", (2160, 3840)), ("2160p", "16:9", (3840, 2160)),
    ("1080p", "4:5", (1080, 1350)),
]


@pytest.mark.parametrize("resolution,aspect,size", SHORT_SIDE)
def test_sizes_count_the_short_side(resolution, aspect, size):
    assert ep.output_size(1080, 1920, preset(resolution=resolution, aspect=aspect)) == size


def test_sizes_start_at_720p():
    """The owner's call (2026-11-06): 240p-540p look bad, so they are gone.
    Naming one is refused like any size the engine doesn't know, and so is a
    custom width or height under 720."""
    assert list(ep.RESOLUTIONS) == ["720p", "1080p", "1440p", "2160p"]
    assert ep.choices()["resolution"] == ["same", "720p", "1080p", "1440p", "2160p", "custom"]
    assert ep.LIMITS["width"] == ep.LIMITS["height"] == (720, 4096)
    for removed in ("240p", "360p", "480p", "540p"):
        with pytest.raises(ep.PresetError, match="resolution can't be"):
            preset(resolution=removed)
    for key, value in (("width", 640), ("height", 718), ("width", 128), ("height", "500"), ("width", -10**400),
                       ("height", 0)):
        with pytest.raises(ep.PresetError, match=f"{key.capitalize()} can't be .*720 to 4096"):
            preset(resolution="custom", **{key: value})
    # 719 is made even (720), and the long side is clamped as before.
    p = preset(resolution="custom", width=719, height=99999)
    assert (p["width"], p["height"]) == (720, 4096)
    # Every size offered is at least 720 on its short side, whatever the shape.
    for resolution in ep.RESOLUTIONS:
        for aspect in ep.ASPECTS:
            assert min(ep.output_size(1080, 1920, preset(resolution=resolution, aspect=aspect))) >= 720


def test_a_saved_preset_with_a_size_no_longer_offered_loads_at_720p():
    """Settings → Export saved before sizes started at 720p must never break
    the card or an export (server/api.py reads it with from_saved)."""
    for removed in ("240p", "360p", "480p", "540p"):
        p = ep.from_saved({"kind": "custom", "resolution": removed, "quality": "high", "max_mb": 50})
        assert (p["kind"], p["resolution"], p["quality"], p["max_mb"]) == ("custom", "720p", "high", 50.0)
        assert ep.output_size(1080, 1920, p) == (720, 1280)
    # A custom size under 720 grows, keeping its shape, until its short side is 720.
    assert (lambda p: (p["resolution"], p["width"], p["height"]))(
        ep.from_saved({"kind": "custom", "resolution": "custom", "width": 640, "height": 360})) == ("custom", 1280, 720)
    assert (lambda p: (p["width"], p["height"]))(ep.from_saved({"kind": "custom", "width": 128, "height": 128})) == \
        (720, 720)
    # One whose long side would then pass 4096 becomes 720p at the default size.
    p = ep.from_saved({"kind": "custom", "resolution": "custom", "width": 128, "height": 4096})
    assert (p["resolution"], p["width"], p["height"]) == ("720p", 1080, 1920)
    p = ep.from_saved({"kind": "custom", "resolution": "1080p", "width": 128, "height": 4096})
    assert (p["resolution"], p["width"], p["height"]) == ("1080p", 1080, 1920)
    # Original, a preset with nothing removed, and Original kept with old custom choices load as they are.
    assert ep.from_saved(None) == ep.normalize(None)
    assert ep.from_saved({"kind": "custom", "resolution": "1080p"}) == preset(resolution="1080p")
    assert ep.from_saved({"kind": "original", "resolution": "480p"}) == {**ep.ORIGINAL, "resolution": "720p"}
    # Anything else that is wrong is still refused (the server then reads Original).
    with pytest.raises(ep.PresetError):
        ep.from_saved({"kind": "custom", "codec": "vp9"})
    with pytest.raises(ep.PresetError):
        ep.from_saved({"kind": "custom", "width": 0, "height": 1920})
    huge = ep.from_saved({"kind": "custom", "resolution": "custom", "width": 10**400, "height": 5})
    assert (huge["resolution"], huge["width"], huge["height"]) == ("720p", 1080, 1920)


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
    p = preset(crf=99, audio_kbps=10, max_mb=1, bitrate_kbps=1, width=99999, height=719, aspect_w=0, aspect_h=500)
    assert (p["crf"], p["audio_kbps"], p["max_mb"], p["bitrate_kbps"]) == (51, 64, 5.0, 100)
    assert (p["width"], p["height"], p["aspect_w"], p["aspect_h"]) == (4096, 720, 1, 32)
    p = preset(crf=0, audio_kbps=999, max_mb=99999, bitrate_kbps="2500", fps="30")
    assert (p["crf"], p["audio_kbps"], p["max_mb"], p["bitrate_kbps"], p["fps"]) == (1, 320, 4000.0, 2500, 30)
    assert preset(max_mb=None)["max_mb"] is None and preset(max_mb="")["max_mb"] is None
    assert preset(bar_color="black")["bar_color"] == "#000000"
    assert preset(bar_color="#abcdef")["bar_color"] == "#ABCDEF"
    # JSON integers too big for a float are clamped too, never an OverflowError
    # (a width or height that far under 720 is refused, test_sizes_start_at_720p).
    huge = 10**400
    p = preset(width=huge, height=huge, crf=huge, bitrate_kbps=-huge, audio_kbps=huge, max_mb=huge,
               aspect_w=huge, aspect_h=-huge)
    assert (p["width"], p["height"], p["crf"], p["bitrate_kbps"], p["audio_kbps"]) == (4096, 4096, 51, 100, 320)
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


def test_a_size_target_on_the_cpu_is_2_pass_and_on_a_card_is_capped_vbr(tmp_path):
    # Five minutes: even the low end of the estimate is over 100 MB, so the limit binds.
    long_clip = ep.Source(1080, 1920, 60.0, 300.0, video_kbps=6600.0)
    p = preset(max_mb=100)
    cpu = ep.make_plan(long_clip, p, CPU264)
    assert (cpu.mode, cpu.passes, cpu.max_bytes, cpu.binds, cpu.sure) == ("target", 2, 100 * ep.MB, True, True)
    assert cpu.video_kbps == ep.target_video_kbps(100 * ep.MB, 300, 128)
    # A graphics card's sizes were never measured, so the clip is first made
    # as without the limit (CQ); one that came out over is made again at the
    # limit's bitrate, one capped VBR pass.
    nv = ep.make_plan(long_clip, p, NV264)
    assert (nv.binds, nv.sure, nv.rate, nv.crf) == (False, False, "crf", 25)
    assert ep.commands(nv, SRC, DST) == ep.commands(ep.make_plan(long_clip, preset(), NV264), SRC, DST)
    run = FakeRun(bytes_for=lambda kbps: 300 * ep.MB if kbps == 0 else kbps * 125 * 300)
    got = ep.encode(SRC, tmp_path / "nv.mp4", p, workdir=tmp_path / "w", run=run, encoder=NV264, source=long_clip)
    k = cpu.video_kbps
    assert _rates(run) == ["cq 25", f"{k}k"]
    again = run.commands[1]
    assert again[again.index("-c:v"):again.index("-c:a")][-6:] == \
        ["-b:v", f"{k}k", "-maxrate", f"{round(k * 1.2)}k", "-bufsize", f"{k}k"]
    assert (got["retried"], got["over_target"], got["video_kbps"]) == (True, False, k)


def test_a_short_clip_is_not_padded_up_to_its_size_target():
    """'Keep under 100 MB' on a 20-second clip gets the quality asked for, not 95 MB:
    it fits anyway, so it is made just as without the limit."""
    short = ep.Source(1080, 1920, 60.0, 20.0, video_kbps=6600.0)
    plan = ep.make_plan(short, preset(max_mb=100), CPU264)
    assert (plan.rate, plan.crf, plan.video_kbps, plan.passes) == ("crf", 23, None, 1)
    assert plan.mode == "target" and plan.target_reachable is True and plan.fits_anyway is True


def test_an_unreachable_target_is_said_so():
    long_clip = ep.Source(1080, 1920, 60.0, 3600.0, video_kbps=6600.0)
    plan = ep.make_plan(long_clip, preset(max_mb=5), CPU264)
    assert plan.target_reachable is False and plan.too_small is True
    assert plan.video_kbps == plan.floor_kbps == ep.floor_kbps(1080, 1920, 60.0, "h264") == 700
    assert ep.estimate(3600, preset(max_mb=5), source=long_clip)["target_reachable"] is False


# ---- the watchable floor --------------------------------------------------------------------


def test_the_floor_scales_with_size_frame_rate_and_codec():
    """Measured (FLOOR_KBPS): 700 kbps of H.264 at 1080x1920 60 fps, less for
    smaller pictures and lower rates, and less again for HEVC; never the flat
    100 kbps it was."""
    floor = ep.floor_kbps
    assert floor(1080, 1920, 60, "h264") == 700 and floor(1080, 1920, 60, "hevc") == 420
    assert floor(1080, 1920, 60, "av1") == floor(1080, 1920, 60, "hevc")
    # The measured ladder points, within a few percent (docs/editor-suite/api/step1-export.md).
    for (w, h, fps), measured in {(1080, 1920, 30): 457, (720, 1280, 60): 386, (720, 1280, 30): 269,
                                  (480, 854, 30): 159, (360, 640, 30): 108, (240, 426, 30): 63}.items():
        assert 1.0 <= floor(w, h, fps, "h264") / measured <= 1.07, (w, h, fps)
    sizes = [(240, 426), (360, 640), (480, 854), (720, 1280), (1080, 1920), (1440, 2560), (2160, 3840)]
    for codec in ("h264", "hevc", "av1"):
        by_size = [floor(w, h, 30, codec) for w, h in sizes]
        assert by_size == sorted(by_size) and len(set(by_size)) == len(by_size), codec
        by_rate = [floor(720, 1280, fps, codec) for fps in (24, 25, 30, 50, 60)]
        assert by_rate == sorted(by_rate) and len(set(by_rate)) == len(by_rate), codec
        # Smaller pictures need more bits for each pixel to keep the same look.
        assert floor(240, 426, 30, codec) / (240 * 426) > floor(1080, 1920, 30, codec) / (1080 * 1920)
    for w, h in sizes:
        assert floor(w, h, 30, "hevc") < floor(w, h, 30, "h264")
    # Landscape and portrait of the same size are the same picture to an encoder.
    assert floor(1920, 1080, 60, "h264") == floor(1080, 1920, 60, "h264")


@pytest.mark.parametrize("codec,encoder", [("h264", CPU264), ("hevc", CPU265), ("h264", NV264)])
def test_a_limit_too_small_to_look_good_is_made_at_the_floor(codec, encoder, tmp_path):
    """5 MB for a 10-minute clip leaves next to nothing: made at the floor,
    not at a bitrate that leaves no picture, and said to be out of reach.
    On a graphics card it is first made as without the limit (its size was
    never measured), then made again at the floor."""
    long_clip = ep.Source(1080, 1920, 60.0, 600.0, video_kbps=6600.0)
    p = preset(codec=codec, resolution="720p", max_mb=5)
    plan = ep.make_plan(long_clip, p, encoder)
    floor = ep.floor_kbps(720, 1280, 60.0, codec)
    assert ep.target_video_kbps(5 * ep.MB, 600, 128) < floor
    assert (plan.floor_kbps, plan.too_small, plan.target_reachable) == (floor, True, False)
    est = ep.estimate(600, p, source=long_clip, encoder=encoder)
    assert est["target_reachable"] is False and est["floor_kbps"] == floor
    # The size shown is the size at the floor, over the limit, not the limit.
    assert est["bytes"] == pytest.approx((floor + 128) * 125 * 600 * 1.01, abs=2)
    assert est["bytes"] > 5 * ep.MB
    if not encoder.hardware:
        assert (plan.video_kbps, plan.binds, est["kind"]) == (floor, True, "close")
        return
    assert (plan.binds, plan.sure, est["kind"], est["sure"]) == (False, False, "range", False)
    run = FakeRun(bytes_for=lambda kbps: 300 * ep.MB if kbps == 0 else kbps * 125 * 600)
    got = ep.encode(SRC, tmp_path / "out.mp4", p, workdir=tmp_path / "w", run=run, encoder=encoder, source=long_clip)
    assert _rates(run) == ["cq 25", f"{floor}k"]
    assert (got["video_kbps"], got["over_reason"], got["retried"]) == (floor, "too_small", True)


def test_a_custom_bitrate_is_kept_as_typed():
    """The floor is only for a bitrate the app picks for a size limit."""
    for kbps in (100, 150, 400):
        plan = ep.make_plan(CLIP, preset(quality="custom", rate="bitrate", bitrate_kbps=kbps), CPU264)
        assert plan.video_kbps == kbps and plan.floor_kbps is None
    # Even with a limit the bitrate can't keep: never more than typed, though the floor is above it.
    long_clip = ep.Source(1080, 1920, 60.0, 600.0, video_kbps=6600.0)
    plan = ep.make_plan(long_clip, preset(quality="custom", rate="bitrate", bitrate_kbps=150, max_mb=5), CPU264)
    assert plan.video_kbps == 150 < plan.floor_kbps and plan.too_small is True and plan.target_reachable is False


# ---- a limit never makes an export bigger -----------------------------------------------------

FITS = [
    ({}, CPU264), ({"quality": "high"}, CPU264), ({"quality": "smallest", "resolution": "720p"}, CPU264),
    ({"codec": "hevc"}, CPU265), ({"quality": "custom", "rate": "crf", "crf": 31}, CPU264),
    ({"quality": "custom", "rate": "bitrate", "bitrate_kbps": 3000}, CPU264),
    ({"aspect": "16:9", "fill": "blur", "fps": 30}, CPU264),
    ({}, NV264), ({"codec": "hevc"}, NV265), ({}, QSV264), ({}, AMF264), ({}, VT264),
    ({"quality": "custom", "rate": "bitrate", "bitrate_kbps": 4000}, NV264), ({"codec": "av1"}, NVAV1),
]


@pytest.mark.parametrize("raw,encoder", FITS, ids=lambda x: getattr(x, "name", None) or str(x))
@pytest.mark.parametrize("max_mb", [4000, 200, 100])
def test_a_limit_the_clip_fits_under_gives_the_no_limit_argv(raw, encoder, max_mb):
    """A minute of the app's own clip fits under 100 MB at every one of these:
    made with exactly the arguments it has without the limit."""
    free = ep.make_plan(CLIP, preset(**raw), encoder)
    limited = ep.make_plan(CLIP, preset(**raw, max_mb=max_mb), encoder)
    assert limited.fits_anyway is True and limited.binds is False and limited.mode == "target"
    assert ep.commands(limited, SRC, DST) == ep.commands(free, SRC, DST)
    assert limited.max_bytes == max_mb * ep.MB


QUALITY_GRID = [{"quality": q, "resolution": r, "codec": c, "fps": f}
                for q in ("smallest", "balanced", "high") for r in ("same", "720p", "1440p", "2160p")
                for c in ("h264", "hevc") for f in ("same", 30)]


@pytest.mark.parametrize("raw", QUALITY_GRID, ids=lambda r: "-".join(str(v) for v in r.values()))
@pytest.mark.parametrize("source", [None, CLIP, ep.Source(1080, 1920, 30.0, 25.0, video_kbps=2500.0)],
                         ids=["no-clip", "clip", "calm-30fps"])
def test_with_a_limit_that_wont_bind_the_estimate_is_never_above_the_no_limit_one(raw, source):
    seconds = source.duration if source else 60.0
    free = ep.estimate(seconds, preset(**raw), source=source)
    for max_mb in (4000, 1000, 200, free["bytes"] / ep.MB * 1.01, free["high"] / ep.MB):
        if max_mb > 4000:
            continue
        est = ep.estimate(seconds, preset(**raw, max_mb=round(max_mb + 0.05, 1)), source=source)
        assert est["fits_anyway"] is True, (raw, max_mb)
        assert (est["kind"], est["bytes"], est["low"]) == (free["kind"], free["bytes"], free["low"])
        assert est["high"] == min(free["high"], est["target_bytes"])
        assert est["per_minute"]["bytes"] == free["per_minute"]["bytes"]
    # A limit that binds shows the close figure at its bitrate: still no higher.
    held = ep.estimate(seconds, preset(**raw, max_mb=max(5.0, round(free["bytes"] / ep.MB * 0.6, 1))),
                       source=source)
    assert held["bytes"] <= free["bytes"] or held["target_reachable"] is False


def test_the_settings_card_figure_no_longer_goes_up_for_a_limit():
    """The black-box check's numbers: High was about 63 MB a minute with no
    limit and about 102 MB with a 200, 1000 or 4000 MB limit."""
    none = ep.estimate(60, preset(quality="high"))["per_minute"]
    for max_mb in (200, 1000, 4000):
        assert ep.estimate(60, preset(quality="high", max_mb=max_mb))["per_minute"] == none
    smallest = ep.estimate(60, preset(quality="smallest"))["per_minute"]["bytes"]
    assert ep.estimate(60, preset(quality="smallest", max_mb=1000))["per_minute"]["bytes"] == smallest


# synth_20s from the black-box check: 1080x1920 at 30 fps, 20 s, its own video at about 2,669 kbps.
SYNTH_20S = ep.Source(1080, 1920, 30.0, 20.0, has_audio=True, video_kbps=2669.018, size=7_013_227)


def test_a_limit_under_the_estimate_but_over_its_low_end_is_made_as_without_it():
    """The QC check's case. At High, synth_20s's estimate is 8,726,879 bytes;
    the real file is 8,148,958. "Keep under 8.7 MB" used to be held to the
    limit's bitrate (the estimate is over 8.7 MB) and made 8,530,752 bytes,
    bigger than with no limit. Even the low end of the estimate is under it,
    so it is made just as without the limit."""
    high = preset(quality="high")
    free = ep.make_plan(SYNTH_20S, high, CPU264)
    low, typical = ep._expected(free)[:2]
    assert int(typical) == 8_726_879 and low < 8.7 * ep.MB < typical
    for max_mb in (8.7, 8.2, round(low / ep.MB + 0.1, 1)):
        limited = ep.make_plan(SYNTH_20S, {**high, "max_mb": max_mb}, CPU264)
        assert (limited.fits_anyway, limited.binds, limited.target_reachable) == (True, False, True), max_mb
        assert ep.commands(limited, SRC, DST) == ep.commands(free, SRC, DST), max_mb
    none = ep.estimate(20, high, source=SYNTH_20S, encoder=CPU264)
    est = ep.estimate(20, {**high, "max_mb": 8.7}, source=SYNTH_20S, encoder=CPU264)
    assert (est["kind"], est["low"], est["high"], est["fits_anyway"]) == ("range", none["low"], 8_700_000, True)
    # Its central figure is over 8.7 MB, so the typical outcome is a file made again at the limit's bitrate.
    again = (ep.target_video_kbps(8.7 * ep.MB, 20, 128) + 128) * 125 * 20 * 1.01
    assert est["bytes"] == pytest.approx(again, abs=1) and est["bytes"] < none["bytes"]
    # Just under the low end: held to the limit, at less than even that low end.
    held = ep.make_plan(SYNTH_20S, {**high, "max_mb": round(low / ep.MB - 0.1, 1)}, CPU264)
    assert (held.binds, held.fits_anyway) == (True, False) and ep._expected(held)[1] < low


def test_a_clip_that_may_fit_keeps_its_own_file_or_is_made_again_smaller(tmp_path):
    """encode() for that case, with synth_20s's real file without a limit
    (8,148,958 bytes at CRF 18). Under 8.7 MB it is that file, made once.
    Under 7.5 MB it came out over, so it is made again at the limit's
    bitrate, which comes out smaller than the CRF file, not bigger."""
    crf_file = 8_148_958
    run = FakeRun(bytes_for=lambda kbps: crf_file if kbps == 0 else kbps * 125 * 20)
    got = ep.encode(SRC, tmp_path / "a.mp4", preset(quality="high", max_mb=8.7), workdir=tmp_path / "wa", run=run,
                    encoder=CPU264, source=SYNTH_20S)
    assert _rates(run) == ["crf 18"] and got["bytes"] == crf_file
    assert (got["retried"], got["over_target"], got["crf"], got["video_kbps"]) == (False, False, 18, None)
    run = FakeRun(bytes_for=lambda kbps: crf_file if kbps == 0 else kbps * 125 * 20)
    got = ep.encode(SRC, tmp_path / "b.mp4", preset(quality="high", max_mb=7.5), workdir=tmp_path / "wb", run=run,
                    encoder=CPU264, source=SYNTH_20S)
    again = ep.target_video_kbps(7.5 * ep.MB, 20, 128)
    assert _rates(run) == ["crf 18", f"{again}k", f"{again}k"]
    assert got["retried"] is True and got["over_target"] is False and got["bytes"] < crf_file


@pytest.mark.parametrize("raw", QUALITY_GRID, ids=lambda r: "-".join(str(v) for v in r.values()))
@pytest.mark.parametrize("source", [None, CLIP, ep.Source(1080, 1920, 30.0, 25.0, video_kbps=2500.0), SYNTH_20S],
                         ids=["no-clip", "clip", "calm-30fps", "synth-20s"])
def test_no_limit_makes_the_export_or_its_estimate_bigger(raw, source):
    """Every limit from 5 MB to past the top of the estimate. One over the low
    end of the estimate without a limit gets the no-limit arguments; one under
    it is held at a bitrate that comes to less than that low end; and no
    figure goes above the one without a limit."""
    enc = CPU264 if raw["codec"] == "h264" else CPU265
    seconds = source.duration if source else 60.0
    free = ep.make_plan(source or ep.REFERENCE, preset(**raw), enc)
    none = ep.estimate(seconds, preset(**raw), source=source, encoder=enc)
    low = ep._expected(free)[0]
    max_mb = 5.0
    while max_mb <= min(4000.0, none["high"] / ep.MB * 1.1):
        p = preset(**raw, max_mb=max_mb)
        plan = ep.make_plan(source or ep.REFERENCE, p, enc)
        assert plan.fits_anyway is (low < plan.max_bytes), max_mb
        if plan.binds:
            assert ep._expected(plan)[1] < low, max_mb
        else:
            assert ep.commands(plan, SRC, DST) == ep.commands(free, SRC, DST), max_mb
        est = ep.estimate(seconds, p, source=source, encoder=enc)
        for key in ("low", "bytes", "high"):
            assert est[key] <= none[key], (key, max_mb)
        assert est["per_minute"]["bytes"] <= none["per_minute"]["bytes"], max_mb
        max_mb = round(max_mb * 1.1 + 0.1, 1)


def test_a_file_at_the_floor_that_could_be_bigger_is_made_as_without_the_limit(tmp_path):
    """A low custom quality (CRF 38: about 825 kbps here, the floor is 700) on
    a 10-minute clip under 5 MB: too small to look good. A file at the floor
    (62.7 MB) could be bigger than this clip without the limit (its estimate
    starts at 59.1 MB), so it is made as without the limit, and made again at
    the floor only if it came out bigger than a file at the floor."""
    long_clip = ep.Source(1080, 1920, 60.0, 600.0, video_kbps=6600.0)
    p = preset(quality="custom", rate="crf", crf=38, max_mb=5)
    free = ep.make_plan(long_clip, {**p, "max_mb": None}, CPU264)
    plan = ep.make_plan(long_clip, p, CPU264)
    assert (plan.binds, plan.fits_anyway, plan.too_small, plan.target_reachable) == (False, False, True, False)
    assert ep.commands(plan, SRC, DST) == ep.commands(free, SRC, DST)
    floor_file = (700 + 128) * 125 * 600 * 1.01
    assert ep._expected(free)[0] <= floor_file
    none = ep.estimate(600, {**p, "max_mb": None}, source=long_clip, encoder=CPU264)
    est = ep.estimate(600, p, source=long_clip, encoder=CPU264)
    assert (est["kind"], est["low"], est["target_reachable"]) == ("range", none["low"], False)
    assert est["high"] == pytest.approx(floor_file, abs=2) and est["high"] < none["high"]
    # Came out bigger than a file at the floor: made again at the floor, over the limit, and said why.
    run = FakeRun(bytes_for=lambda kbps: 70 * ep.MB if kbps == 0 else kbps * 125 * 600)
    got = ep.encode(SRC, tmp_path / "a.mp4", p, workdir=tmp_path / "wa", run=run, encoder=CPU264, source=long_clip)
    assert _rates(run) == ["crf 38", "700k", "700k"]
    assert (got["over_target"], got["over_reason"], got["retried"]) == (True, "too_small", True)
    # Came out smaller than that: kept as it is.
    run = FakeRun(bytes_for=lambda kbps: 55 * ep.MB)
    got = ep.encode(SRC, tmp_path / "b.mp4", p, workdir=tmp_path / "wb", run=run, encoder=CPU264, source=long_clip)
    assert _rates(run) == ["crf 38"] and got["bytes"] == 55 * ep.MB
    assert (got["over_target"], got["over_reason"], got["retried"]) == (True, "too_small", False)


# QC's calm clip: a still, detailed picture with a small moving square, 1080x1920 at 60 fps,
# 40 s, its own video at 438.51 kbps (busyness 0.07, under BUSY_RANGE's floor).
CALM_40S = ep.Source(1080, 1920, 60.0, 40.0, has_audio=True, video_kbps=438.51)


def test_a_calm_clip_is_made_as_without_the_limit():
    """QC's case. At High its estimate was 10.6-16.2 MB and the real file
    3,539,881 bytes: busyness stops at 0.3, so the estimate can't follow a
    clip this calm. Held to 9.6 MB (1696 kbps) it came to 7.6 MB, 2.1x the
    file without a limit; held to 6.4 MB, x264 overshot to 7.0 MB, over a
    limit the file without one would have met. Now it is made as without
    the limit at any limit, and checked."""
    high = preset(quality="high")
    free = ep.make_plan(CALM_40S, high, CPU264)
    assert free.busy == ep.BUSY_RANGE[0] and ep._expected(free)[0] > 9.6 * ep.MB
    none = ep.estimate(40, high, source=CALM_40S, encoder=CPU264)
    for max_mb in (9.6, 6.4, 5):
        p = {**high, "max_mb": max_mb}
        plan = ep.make_plan(CALM_40S, p, CPU264)
        assert (plan.binds, plan.sure, plan.fits_anyway) == (False, False, False), max_mb
        assert ep.commands(plan, SRC, DST) == ep.commands(free, SRC, DST), max_mb
        est = ep.estimate(40, p, source=CALM_40S, encoder=CPU264)
        assert est["sure"] is False and all(est[k] <= none[k] for k in ("low", "bytes", "high")), max_mb


def test_a_calm_clip_under_a_limit_keeps_the_file_it_has_without_one(tmp_path):
    """Made once, at CRF 18, and it fits: the file without the limit."""
    high = preset(quality="high")
    for max_mb in (9.6, 6.4):
        run = FakeRun(bytes_for=lambda kbps: 3_539_881 if kbps == 0 else kbps * 125 * 40)
        got = ep.encode(SRC, tmp_path / f"{max_mb}.mp4", {**high, "max_mb": max_mb}, workdir=tmp_path / "w",
                        run=run, encoder=CPU264, source=CALM_40S)
        assert _rates(run) == ["crf 18"] and got["bytes"] == 3_539_881
        assert (got["retried"], got["over_target"], got["crf"]) == (False, False, 18)
    # Over a limit and already under the floor's bitrate: kept as it is, said to be too small.
    run = FakeRun(bytes_for=lambda kbps: 5_400_000 if kbps == 0 else kbps * 125 * 40)
    got = ep.encode(SRC, tmp_path / "small.mp4", {**high, "max_mb": 5}, workdir=tmp_path / "w", run=run,
                    encoder=CPU264, source=ep.Source(1080, 1920, 60.0, 400.0, video_kbps=438.51))
    assert _rates(run) == ["crf 18"] and (got["over_reason"], got["retried"]) == ("too_small", False)


def _binding_limit(source, raw, encoder) -> float:
    """A limit under the low end of the export's estimate without one (and
    over what a file at the floor comes to): one the old rule held to."""
    free = ep.make_plan(source, preset(**raw), encoder)
    floor = ep.floor_kbps(free.width, free.height, free.fps, encoder.codec)
    low = ep._expected(free)[0]
    at_floor = (floor + 128) * 125 * source.duration * 1.01
    assert at_floor < low * 0.8
    return round(low * 0.8 / ep.MB, 1)


@pytest.mark.parametrize("raw,encoder,sure", [
    ({}, CPU264, True), ({"codec": "hevc"}, CPU265, True), ({"quality": "high", "resolution": "720p"}, CPU264, True),
    ({"aspect": "1:1", "fill": "crop"}, CPU264, True), ({"aspect": "16:9", "fill": "crop"}, CPU264, True),
    ({"resolution": "2160p"}, CPU264, True), ({"fps": 30}, CPU264, True),
    ({"quality": "custom", "rate": "bitrate", "bitrate_kbps": 9000}, CPU264, True),
    ({"quality": "custom", "rate": "bitrate", "bitrate_kbps": 9000}, NV264, True), ({}, AMF264, True),
    ({}, VT264, True),
    ({"aspect": "16:9", "fill": "bars"}, CPU264, False), ({"aspect": "16:9", "fill": "blur"}, CPU264, False),
    ({"aspect": "4:5", "fill": "blur", "codec": "hevc"}, CPU265, False), ({"aspect": "1:1", "fill": "bars"}, CPU264,
                                                                          False),
    ({}, NV264, False), ({"codec": "hevc"}, NV265, False), ({"codec": "av1"}, NVAV1, False), ({}, QSV264, False),
], ids=lambda x: getattr(x, "name", None) or str(x))
def test_only_a_clip_whose_size_is_known_is_held_to_the_limit_from_the_start(raw, encoder, sure):
    """Held from the start only where the size without the limit is known:
    a bitrate, or a CPU encode of a kind the estimate was measured on
    (BUSY_RANGE's comment). With colour bars a real clip came to 0.47x the
    estimate, with a blurred background 0.82x, and a graphics card's sizes
    were never measured: those are made as without the limit, and checked."""
    long_clip = ep.Source(1080, 1920, 60.0, 600.0, video_kbps=6600.0)
    max_mb = _binding_limit(long_clip, raw, encoder)
    plan = ep.make_plan(long_clip, preset(**raw, max_mb=max_mb), encoder)
    assert (plan.sure, plan.binds, plan.fits_anyway) == (sure, sure, False)
    if not sure:
        free = ep.make_plan(long_clip, preset(**raw), encoder)
        assert ep.commands(plan, SRC, DST) == ep.commands(free, SRC, DST)
    est = ep.estimate(600, preset(**raw, max_mb=max_mb), source=long_clip, encoder=encoder)
    assert est["sure"] is sure and est["kind"] == ("close" if sure else "range")
    # Without a clip (the Settings card), the app's typical clip counts as measured.
    assert ep.make_plan(ep.REFERENCE, preset(**raw, max_mb=5), encoder, typical_clip=True).sure is sure
    # A real clip whose bitrate can't be read is not.
    unknown = ep.Source(1080, 1920, 60.0, 600.0)
    assert ep.make_plan(unknown, preset(**raw, max_mb=5), encoder).sure is (sure and (
        raw.get("rate") == "bitrate" or encoder.family in ("amf", "videotoolbox")))


@pytest.mark.parametrize("own_kbps", [2000.0, 3000.0, 6600.0, 15000.0, 19800.0, 40000.0])
def test_a_held_bitrate_is_under_what_the_clip_itself_needs(own_kbps):
    """A clip held to a limit from the start gets less than the low end of
    what its own bitrate, scaled to the export's size, frame rate and
    quality, says it needs (QC's cap): for a sure clip that is the estimate
    itself, and holding it needs that low end over the limit."""
    for seconds in (30.0, 120.0, 600.0):
        source = ep.Source(1080, 1920, 60.0, seconds, video_kbps=own_kbps)
        own = own_kbps / (ep.REF_KBPS * 1.0)   # its busyness, not held within BUSY_RANGE
        for raw in ({}, {"quality": "high"}, {"quality": "smallest", "resolution": "720p", "fps": 30},
                    {"resolution": "2160p"}, {"aspect": "1:1", "fill": "crop"}, {"codec": "hevc"}):
            enc = CPU265 if raw.get("codec") == "hevc" else CPU264
            for max_mb in (5, 10, 25, 50, 100, 250, 1000):
                plan = ep.make_plan(source, preset(**raw, max_mb=max_mb), enc)
                if not plan.binds:
                    continue
                needs = ep.model_kbps(plan.width, plan.height, plan.fps, ep._level_crf(preset(**raw)), enc.codec,
                                      enc.family, own)
                assert plan.video_kbps < ep.SPREAD_KNOWN[0] * needs, (own_kbps, seconds, raw, max_mb)


def test_the_smallest_file_made_is_kept(tmp_path):
    """An encoder can overshoot its bitrate. Made as without the limit, a clip
    that came out over is made again at the limit's bitrate; if that try and
    its 0.9x retry come out no smaller, the first file is kept, so the export
    is never bigger than it is without the limit."""
    out = (tmp_path / "out.mp4").resolve()
    run = FakeRun(bytes_for=lambda kbps: 120 * ep.MB if kbps == 0 else 125 * ep.MB)
    got = ep.encode(SRC, out, preset(max_mb=100), workdir=tmp_path / "w", run=run, encoder=CPU264, source=CLIP)
    target = ep.target_video_kbps(100 * ep.MB, 60, 128)
    assert _rates(run) == ["crf 23", f"{target}k", f"{target}k", f"{int(target * 0.9)}k"]
    assert out.stat().st_size == got["bytes"] == 120 * ep.MB
    assert (got["crf"], got["video_kbps"], got["retried"]) == (23, None, False)
    assert (got["over_target"], got["over_reason"]) == (True, "missed")
    assert not list((tmp_path / "w").glob("*.mp4")), "nothing is left in the working folder"
    # The remake was smaller but over, and its retry came out bigger: the remake is kept.
    sizes = {0: 120 * ep.MB, target: 110 * ep.MB, int(target * 0.9): 115 * ep.MB}
    run = FakeRun(bytes_for=lambda kbps: sizes[kbps])
    got = ep.encode(SRC, out, preset(max_mb=100), workdir=tmp_path / "w", run=run, encoder=CPU264, source=CLIP)
    assert out.stat().st_size == got["bytes"] == 110 * ep.MB
    assert (got["video_kbps"], got["retried"], got["over_reason"]) == (target, True, "missed")
    # Held from the start: a retry that came out bigger gives the first file back too.
    long_clip = ep.Source(1080, 1920, 60.0, 180.0, video_kbps=6600.0)
    held = ep.target_video_kbps(50 * ep.MB, 180, 128)
    run = FakeRun(bytes_for=lambda kbps: 51 * ep.MB if kbps == held else 52 * ep.MB)
    got = ep.encode(SRC, out, preset(max_mb=50), workdir=tmp_path / "w", run=run, encoder=CPU264, source=long_clip)
    assert (got["bytes"], got["video_kbps"], got["retried"]) == (51 * ep.MB, held, False)
    # WoopSocial's smaller copy keeps its old rules: its retry is kept, whatever its size.
    source = ep.Source(1080, 1920, 60.0, 1800.0)
    run = FakeRun(bytes_for=lambda kbps: 96_000_000 if kbps == 273 else 97_000_000)
    got = ep.encode(SRC, out, ep.smaller_copy_preset(source, 95_000_000), workdir=tmp_path / "w", run=run,
                    encoder=CPU264, source=source, max_bytes=95_000_000)
    assert (got["bytes"], got["retried"]) == (97_000_000, True)


def test_a_cancel_while_a_file_is_made_again_leaves_the_first_in_the_working_folder(tmp_path):
    """The file set aside is in the export's working folder, which the editor
    lane removes after every job, cancelled ones too (server/editor_lane.py)."""
    out = (tmp_path / "out.mp4").resolve()

    def run(argv, *, cwd=None, duration=0.0, on_progress=None):
        if "-crf" not in argv:
            raise ep.Cancelled()
        Path(argv[-1]).write_bytes(b"\0" * 120 * ep.MB)
        return 0, ""

    with pytest.raises(ep.Cancelled):
        ep.encode(SRC, out, preset(max_mb=100), workdir=tmp_path / "w", run=run, encoder=CPU264, source=CLIP)
    assert [p.name for p in (tmp_path / "w").iterdir()] == [f"out{ep.KEPT_TRY}"]
    assert not out.exists()


def test_the_longest_clip_a_limit_keeps_watchable():
    """longest_watchable_s: the length at which the limit leaves exactly the floor."""
    est = ep.estimate(60, preset(max_mb=5))
    longest = 0.95 * 5 * ep.MB / (125 * (700 + 128))
    assert est["longest_watchable_s"] == round(longest, 1) == 45.9
    assert ep.longest_watchable(5 * ep.MB, 700, 128) == pytest.approx(longest)
    assert ep.target_video_kbps(5 * ep.MB, longest, 128) in (699, 700)
    # Either side of it: kept under the limit, or too small to look good.
    assert ep.estimate(longest - 1, preset(max_mb=5))["target_reachable"] is True
    assert ep.estimate(longest + 1, preset(max_mb=5))["target_reachable"] is False
    # It follows the size, frame rate, codec and sound.
    hevc_720 = ep.estimate(60, preset(max_mb=5, resolution="720p", fps=30, codec="hevc", audio_kbps=64))
    assert hevc_720["longest_watchable_s"] == round(0.95 * 5 * ep.MB / (125 * (175 + 64)), 1)
    silent = ep.Source(1080, 1920, 60.0, 60.0, has_audio=False, video_kbps=6600.0)
    assert ep.estimate(60, preset(max_mb=5), source=silent)["longest_watchable_s"] == \
        round(0.95 * 5 * ep.MB / (125 * 700), 1)
    # No limit, no figure.
    assert ep.estimate(60, preset())["longest_watchable_s"] is None
    assert ep.estimate(60, None)["longest_watchable_s"] is None


# ---- the HEVC estimate ------------------------------------------------------------------------

# Measured with x265 fast as exports run it (CODEC_GAIN's comment): the clip
# (width, height, fps, seconds, its own video kbps), the export's size and
# quality, and the file's bytes.
HEVC_MEASURED = [
    ((1080, 1920, 60.0, 30.0, 4176), "same", "smallest", 4175303),
    ((1080, 1920, 60.0, 30.0, 4176), "same", "balanced", 6204745),
    ((1080, 1920, 60.0, 30.0, 4176), "same", "high", 10596309),
    ((1080, 1920, 29.929, 28.31, 3145), "same", "smallest", 2833920),
    ((1080, 1920, 29.929, 28.31, 3145), "same", "balanced", 4186278),
    ((1080, 1920, 29.929, 28.31, 3145), "same", "high", 7175656),
    ((1080, 1920, 59.894, 42.84, 9606), "same", "smallest", 12078261),
    ((1080, 1920, 59.894, 42.84, 9606), "same", "balanced", 18194767),
    ((1080, 1920, 59.894, 42.84, 9606), "same", "high", 31318052),
    ((1080, 1920, 60.0, 150.55, 6433), "same", "smallest", 25768652),
    ((1080, 1920, 60.0, 150.55, 6433), "same", "balanced", 38282837),
    ((1080, 1920, 60.0, 150.55, 6433), "same", "high", 66729331),
    ((1080, 1920, 60.0, 150.55, 6433), "720p", "smallest", 15556444),
    ((1080, 1920, 60.0, 150.55, 6433), "720p", "balanced", 22525967),
    ((1080, 1920, 60.0, 150.55, 6433), "720p", "high", 38593581),
]


@pytest.mark.parametrize("clip,resolution,quality,real", HEVC_MEASURED)
def test_the_hevc_estimate_matches_what_x265_made(clip, resolution, quality, real):
    w, h, fps, seconds, kbps = clip
    source = ep.Source(w, h, fps, seconds, video_kbps=float(kbps))
    est = ep.estimate(seconds, preset(codec="hevc", resolution=resolution, quality=quality), source=source,
                      encoder=CPU265)
    assert 0.85 <= est["bytes"] / real <= 1.15
    assert est["low"] <= real <= est["high"]


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
    # Long enough that even the low end is over the limit: held to it, a close figure.
    target = ep.estimate(400, preset(max_mb=100))
    assert target["kind"] == "close" and target["high"] <= 100 * ep.MB and target["target_bytes"] == 100 * ep.MB
    assert target["fits_anyway"] is False


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
    assert size(resolution="720p") < size(resolution="1080p") < size(resolution="1440p")
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
    run = FakeRun(bytes_for=lambda kbps: 55 * ep.MB + kbps)
    got = ep.encode(SRC, tmp_path / "out.mp4", preset(max_mb=50), workdir=tmp_path, run=run, encoder=CPU264,
                    source=long_clip)
    assert len(run.commands) == 3 and got["over_target"] is True and got["retried"] is True
    # On a graphics card: made as without the limit, again at the limit's bitrate, then at 0.9x that.
    run = FakeRun(bytes_for=lambda kbps: 70 * ep.MB if kbps == 0 else 55 * ep.MB + kbps)
    got = ep.encode(SRC, tmp_path / "nv.mp4", preset(max_mb=50), workdir=tmp_path, run=run, encoder=NV264,
                    source=long_clip)
    assert len(run.commands) == 3 and got["over_target"] is True and got["retried"] is True


def test_no_retry_without_a_target(tmp_path):
    run = FakeRun(bytes_for=lambda kbps: 999 * ep.MB)
    got = ep.encode(SRC, tmp_path / "out.mp4", preset(), workdir=tmp_path, run=run, encoder=CPU264, source=CLIP)
    assert len(run.commands) == 1 and got["retried"] is False and got["over_target"] is False
    assert got["over_reason"] is None and got["floor_kbps"] is None


def _rates(run: FakeRun) -> list[str]:
    """Each command's rate: its -crf or -cq for a constant-quality encode, else its -b:v."""
    return ["crf " + c[c.index("-crf") + 1] if "-crf" in c else "cq " + c[c.index("-cq") + 1] if "-cq" in c
            else c[c.index("-b:v") + 1] for c in run.commands]


def test_a_clip_that_fits_is_made_as_without_the_limit_and_only_checked(tmp_path):
    out = (tmp_path / "out.mp4").resolve()
    run = FakeRun(bytes_for=lambda kbps: 40 * ep.MB)
    got = ep.encode(SRC, out, preset(max_mb=100), workdir=tmp_path / "work", run=run, encoder=CPU264, source=CLIP)
    assert run.commands == ep.commands(ep.make_plan(CLIP, preset(), CPU264), SRC.resolve(), out)
    assert (got["mode"], got["crf"], got["video_kbps"], got["retried"]) == ("target", 23, None, False)
    assert got["over_target"] is False and got["over_reason"] is None and got["bytes"] == 40 * ep.MB


def test_a_clip_that_fits_but_came_out_over_is_made_again_at_the_limits_bitrate(tmp_path):
    """The estimate said a minute fits in 100 MB; this one came out at 120 MB.
    It is made once more, 2-pass at the limit's bitrate, and progress says so."""
    target = ep.target_video_kbps(100 * ep.MB, 60, 128)
    run = FakeRun(bytes_for=lambda kbps: 120 * ep.MB if kbps == 0 else kbps * 125 * 60)
    seen, steps = [], []
    got = ep.encode(SRC, tmp_path / "out.mp4", preset(max_mb=100), workdir=tmp_path / "work", run=run,
                    encoder=CPU264, source=CLIP, on_progress=lambda f, step: (seen.append(f), steps.append(step)))
    assert _rates(run) == ["crf 23", f"{target}k", f"{target}k"]
    assert [c[c.index("-pass") + 1] for c in run.commands[1:]] == ["1", "2"]
    assert got["retried"] is True and got["over_target"] is False and got["bytes"] == target * 125 * 60
    assert (got["video_kbps"], got["crf"]) == (target, None)
    assert seen == sorted(seen), "progress never goes back"
    first, again = [s for s in steps if not s["smaller"]], [s for s in steps if s["smaller"]]
    assert first and again and steps.index(again[0]) > steps.index(first[-1])
    assert again[0]["attempt_fraction"] == 0.0 and again[-1]["attempt_fraction"] == 1.0
    assert [s["pass"] for s in again] == sorted(s["pass"] for s in again)


def test_a_remake_still_over_gets_the_one_0_9x_retry(tmp_path):
    target = ep.target_video_kbps(100 * ep.MB, 60, 128)
    run = FakeRun(bytes_for=lambda kbps: 120 * ep.MB if kbps == 0 or kbps >= target else 90 * ep.MB)
    got = ep.encode(SRC, tmp_path / "out.mp4", preset(max_mb=100), workdir=tmp_path / "work", run=run,
                    encoder=CPU264, source=CLIP)
    assert _rates(run) == ["crf 23", f"{target}k", f"{target}k", f"{int(target * 0.9)}k"]
    assert run.commands[3][run.commands[3].index("-pass") + 1] == "2", "the first pass is reused"
    assert got["retried"] is True and got["over_target"] is False and got["video_kbps"] == int(target * 0.9)


@pytest.mark.parametrize("max_mb,again", [(24, "the limit's bitrate"), (30, "0.9x the typed one")])
def test_a_custom_bitrate_that_came_out_over_reuses_its_first_pass(tmp_path, max_mb, again):
    """3,000 kbps for a minute is 23.7 MB, under 24 and 30 MB. Made again
    smaller, never above the typed bitrate: at the limit's bitrate when that
    is lower (24 MB), else at 0.9x the typed one (30 MB)."""
    p = preset(quality="custom", rate="bitrate", bitrate_kbps=3000, max_mb=max_mb)
    assert ep.make_plan(CLIP, p, CPU264).fits_anyway is True
    target = ep.target_video_kbps(max_mb * ep.MB, 60, 128)
    expected = target if target < 3000 else 2700
    run = FakeRun(bytes_for=lambda kbps: (max_mb + 1) * ep.MB if kbps == 3000 else (max_mb - 1) * ep.MB)
    got = ep.encode(SRC, tmp_path / "out.mp4", p, workdir=tmp_path / "work", run=run, encoder=CPU264, source=CLIP)
    assert _rates(run) == ["3000k", "3000k", f"{expected}k"], again
    assert run.commands[2][run.commands[2].index("-pass") + 1] == "2"
    assert got["over_target"] is False and got["video_kbps"] == expected


def test_neither_a_target_nor_its_retry_goes_below_the_floor(tmp_path):
    # Far too small: made at the floor, and no retry below it.
    long_clip = ep.Source(1080, 1920, 60.0, 600.0, video_kbps=6600.0)
    run = FakeRun(bytes_for=lambda kbps: 30 * ep.MB)
    got = ep.encode(SRC, tmp_path / "a.mp4", preset(max_mb=5), workdir=tmp_path / "wa", run=run, encoder=CPU264,
                    source=long_clip)
    assert _rates(run) == ["700k", "700k"]
    assert got["over_target"] is True and got["over_reason"] == "too_small" and got["retried"] is False
    assert got["video_kbps"] == got["floor_kbps"] == 700
    # Just within reach: the retry stops at the floor instead of going 0.9x below it.
    seconds = ep.longest_watchable(5 * ep.MB, 700, 128) * 0.98
    near = ep.Source(1080, 1920, 60.0, seconds, video_kbps=6600.0)
    first = ep.target_video_kbps(5 * ep.MB, seconds, 128)
    assert 700 < first < 700 / 0.9
    run = FakeRun(bytes_for=lambda kbps: 5 * ep.MB + kbps * 1000)
    got = ep.encode(SRC, tmp_path / "b.mp4", preset(max_mb=5), workdir=tmp_path / "wb", run=run, encoder=CPU264,
                    source=near)
    assert _rates(run) == [f"{first}k", f"{first}k", "700k"]
    assert got["over_reason"] == "too_small" and got["retried"] is True
    # On a graphics card too: made as without the limit (its size was never
    # measured) at 30 MB for ten minutes, 272 kbps of video, already under
    # the floor, so it isn't made again.
    run = FakeRun(bytes_for=lambda kbps: 30 * ep.MB)
    got = ep.encode(SRC, tmp_path / "c.mp4", preset(max_mb=5), workdir=tmp_path / "wc", run=run, encoder=NV264,
                    source=long_clip)
    assert _rates(run) == ["cq 25"] and got["over_reason"] == "too_small" and got["retried"] is False


def test_an_ordinary_overshoot_is_told_apart_from_too_small(tmp_path):
    long_clip = ep.Source(1080, 1920, 60.0, 180.0, video_kbps=6600.0)
    run = FakeRun(bytes_for=lambda kbps: 55 * ep.MB + kbps)
    got = ep.encode(SRC, tmp_path / "out.mp4", preset(max_mb=50), workdir=tmp_path, run=run, encoder=CPU264,
                    source=long_clip)
    assert got["over_target"] is True and got["over_reason"] == "missed" and got["retried"] is True
    assert int(got["video_kbps"]) > got["floor_kbps"]


def test_a_graphics_card_that_fails_is_replaced_by_the_cpu(tmp_path, monkeypatch):
    monkeypatch.setattr(encoding, "encoder_for", lambda codec, pref="auto": CPU264 if pref == "cpu" else None)
    run = FakeRun(fail={"h264_nvenc"})
    got = ep.encode(SRC, tmp_path / "out.mp4", preset(resolution="720p"), workdir=tmp_path, run=run,
                    encoder=NV264, source=CLIP)
    assert [c[c.index("-c:v") + 1] for c in run.commands] == ["h264_nvenc", "libx264"]
    assert got["fell_back_to_cpu"] is True and got["encoder"] == "libx264" and got["crf"] == 23


def _remake_fails_on(card: str, run: FakeRun):
    """`run`, except that `card` fails at anything but a constant-quality
    encode: a first try works, making it again to fit doesn't (a graphics
    card out of encode sessions, say)."""
    def fake(argv, **kw):
        if argv[argv.index("-c:v") + 1] == card and "-cq" not in argv:
            run.commands.append(list(argv))
            return 1, "OpenEncodeSessionEx failed: out of memory (10)\nError while opening encoder"
        return run(argv, **kw)
    return fake


def test_a_graphics_card_that_fails_making_a_file_again_hands_it_to_the_cpu(tmp_path, monkeypatch):
    """Made on NVIDIA as without the limit, 120 MB is over 100 MB. Making it
    again fails on the card, so the CPU makes it: the same bitrate, 2-pass."""
    monkeypatch.setattr(encoding, "encoder_for", lambda codec, pref="auto": CPU264 if pref == "cpu" else None)
    target = ep.target_video_kbps(100 * ep.MB, 60, 128)
    cq = ep.make_plan(CLIP, preset(), NV264).crf
    out = (tmp_path / "out.mp4").resolve()
    run = FakeRun(bytes_for=lambda kbps: 120 * ep.MB if kbps == 0 else 90 * ep.MB)
    steps = []
    got = ep.encode(SRC, out, preset(max_mb=100), workdir=tmp_path / "w", run=_remake_fails_on("h264_nvenc", run),
                    encoder=NV264, source=CLIP, on_progress=lambda f, step: steps.append(step))
    assert [c[c.index("-c:v") + 1] for c in run.commands] == ["h264_nvenc", "h264_nvenc", "libx264", "libx264"]
    assert _rates(run) == [f"cq {cq}", f"{target}k", f"{target}k", f"{target}k"]
    assert [c[c.index("-pass") + 1] for c in run.commands[2:]] == ["1", "2"]
    assert out.stat().st_size == got["bytes"] == 90 * ep.MB
    assert (got["encoder"], got["fell_back_to_cpu"], got["retried"], got["video_kbps"]) == ("libx264", True, True,
                                                                                             target)
    assert got["over_target"] is False and got["over_reason"] is None
    assert steps[-1]["encoder"] == "libx264" and steps[-1]["smaller"] is True


def test_a_cpu_remake_no_smaller_gives_the_cards_file_back(tmp_path, monkeypatch):
    """The CPU's remake and its 0.9x retry both came out at 125 MB, bigger
    than the card's 120 MB: the card's file comes back, as made on the card."""
    monkeypatch.setattr(encoding, "encoder_for", lambda codec, pref="auto": CPU264 if pref == "cpu" else None)
    target = ep.target_video_kbps(100 * ep.MB, 60, 128)
    out = (tmp_path / "out.mp4").resolve()
    run = FakeRun(bytes_for=lambda kbps: 120 * ep.MB if kbps == 0 else 125 * ep.MB)
    got = ep.encode(SRC, out, preset(max_mb=100), workdir=tmp_path / "w", run=_remake_fails_on("h264_nvenc", run),
                    encoder=NV264, source=CLIP)
    assert _rates(run)[1:] == [f"{target}k", f"{target}k", f"{target}k", f"{int(target * 0.9)}k"]
    assert run.commands[-1][run.commands[-1].index("-pass") + 1] == "2", "the CPU's first pass is reused"
    assert out.stat().st_size == got["bytes"] == 120 * ep.MB
    assert (got["encoder"], got["fell_back_to_cpu"], got["retried"]) == ("h264_nvenc", False, False)
    assert (got["over_target"], got["over_reason"]) == (True, "missed")
    assert not list((tmp_path / "w").glob("*.mp4")), "nothing is left in the working folder"


def test_a_failed_remake_with_no_cpu_encoder_is_an_export_error(tmp_path, monkeypatch):
    """AV1 has no CPU encoder: a card that fails at making it again fails the
    export, with FFmpeg's words, as a failed first try does."""
    monkeypatch.setattr(encoding, "encoder_for", lambda codec, pref="auto": None)
    run = FakeRun(bytes_for=lambda kbps: 120 * ep.MB if kbps == 0 else 90 * ep.MB)
    with pytest.raises(ep.ExportError, match="Error while opening encoder"):
        ep.encode(SRC, tmp_path / "out.mp4", preset(codec="av1", max_mb=100), workdir=tmp_path / "w",
                  run=_remake_fails_on("av1_nvenc", run), encoder=NVAV1, source=CLIP)
    assert [c[c.index("-c:v") + 1] for c in run.commands] == ["av1_nvenc", "av1_nvenc"]


def test_a_cancel_while_the_card_makes_it_again_is_not_handed_to_the_cpu(tmp_path, monkeypatch):
    monkeypatch.setattr(encoding, "encoder_for", lambda codec, pref="auto": CPU264 if pref == "cpu" else None)
    run = FakeRun(bytes_for=lambda kbps: 120 * ep.MB)

    def fake(argv, **kw):
        if "-cq" not in argv:
            raise ep.Cancelled()
        return run(argv, **kw)

    with pytest.raises(ep.Cancelled):
        ep.encode(SRC, (tmp_path / "out.mp4").resolve(), preset(max_mb=100), workdir=tmp_path / "w", run=fake,
                  encoder=NV264, source=CLIP)
    assert [c[c.index("-c:v") + 1] for c in run.commands] == ["h264_nvenc"]


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


# What the smaller copy planned at 843bfa0, before the watchable floor and the
# "fits anyway" rule: it keeps exactly these. (clip), then (size, width,
# height, video kbps, target reachable) for 95,000,000 bytes.
SMALLER_COPY_PLANS = [
    ((1080, 1920, 60.0, 165.0, 6600.0), ("same", 1080, 1920, 4247, True)),
    ((1080, 1920, 60.0, 300.0, 6600.0), ("720p", 720, 1280, 2278, True)),
    ((1080, 1920, 60.0, 600.0, None), ("720p", 720, 1280, 1075, True)),
    ((1080, 1920, 60.0, 1800.0, None), ("720p", 720, 1280, 273, True)),
    ((1080, 1920, 60.0, 3600.0, None), ("720p", 720, 1280, 100, False)),
    ((1080, 1920, 30.0, 120.0, 3000.0), ("same", 1080, 1920, 4724, True)),
    ((720, 1280, 60.0, 900.0, None), ("same", 720, 1280, 674, True)),
]


@pytest.mark.parametrize("clip,expected", SMALLER_COPY_PLANS)
def test_the_smaller_copys_plans_are_unchanged(clip, expected):
    w, h, fps, seconds, kbps = clip
    source = ep.Source(w, h, fps, seconds, video_kbps=kbps)
    p = ep.smaller_copy_preset(source, 95_000_000)
    for encoder, passes in ((CPU264, 2), (NV264, 1)):
        plan = ep.make_plan(source, p, encoder, max_bytes=95_000_000)
        assert (p["resolution"], plan.width, plan.height, plan.video_kbps, plan.target_reachable) == expected
        assert (plan.mode, plan.rate, plan.passes, plan.max_bytes) == ("target", "kbps", passes, 95_000_000)
        k = plan.video_kbps
        if encoder is NV264:   # held to its target: the size target's caps
            assert video_block(plan)[-6:] == ["-b:v", f"{k}k", "-maxrate", f"{round(k * 1.2)}k", "-bufsize", f"{k}k"]
        else:
            assert video_block(plan, 2)[-1] == f"{k}k"


def test_the_smaller_copy_still_retries_down_to_100_kbps(tmp_path):
    """Not held to the watchable floor: 273 kbps at 720p 60 fps (half an hour
    in 95 MB) is under it, and its retry is still 0.9x that."""
    source = ep.Source(1080, 1920, 60.0, 1800.0)
    run = FakeRun(bytes_for=lambda kbps: 96_000_000)
    got = ep.encode(SRC, tmp_path / "out.mp4", ep.smaller_copy_preset(source, 95_000_000), workdir=tmp_path,
                    run=run, encoder=CPU264, source=source, max_bytes=95_000_000)
    assert ep.floor_kbps(720, 1280, 60.0, "h264") > 273
    assert _rates(run) == ["273k", "273k", f"{int(273 * 0.9)}k"]
    assert got["over_target"] is True and got["retried"] is True


# ---- progress ---------------------------------------------------------------------------------


@pytest.mark.parametrize("line,seconds", [
    ("out_time_us=1500000", 1.5), ("out_time_ms=1500000", 1.5), ("out_time=00:01:02.500000", 62.5),
    ("out_time_us=N/A", None), ("frame=12", None), ("progress=end", None), ("", None),
])
def test_progress_lines(line, seconds):
    assert ep.progress_seconds(line) == seconds
