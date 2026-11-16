"""Editor suite step 6: what a media file is, and whether the editor's
<video> (Chromium in Electron) plays it as it is or needs a proxy
(video_editor/media/probe.py).

The rule is checked on ffprobe's JSON as data, on CI's light install; the
real ffprobe runs on generated files where FFmpeg is installed.
"""

import pytest
from media_helpers import make_picture, make_tone_and_silence, make_video

from video_editor.media import probe

MP4 = "mov,mp4,m4a,3gp,3g2,mj2"


def _data(container=MP4, video="h264", audio="aac", width=1920, height=1080, fps="30/1", pix="yuv420p",
          rotation=None, tag=None, duration="60.0"):
    streams = []
    if video:
        stream = {"codec_type": "video", "codec_name": video, "width": width, "height": height,
                  "avg_frame_rate": fps, "r_frame_rate": fps, "pix_fmt": pix, "disposition": {"attached_pic": 0}}
        if rotation is not None:
            stream["side_data_list"] = [{"side_data_type": "Display Matrix", "rotation": rotation}]
        if tag is not None:
            stream["tags"] = {"rotate": str(tag)}
        streams.append(stream)
    if audio:
        streams.append({"codec_type": "audio", "codec_name": audio, "channels": 2, "sample_rate": "48000"})
    return {"format": {"format_name": container, "duration": duration, "size": "1000", "start_time": "0.000"},
            "streams": streams}


def _reasons(**kw):
    return probe.proxy_reasons(probe.describe(_data(**kw)))


def test_what_chromium_plays_needs_no_proxy():
    assert _reasons() == []                                                     # H.264 + AAC in MP4
    assert _reasons(audio="mp3") == [] and _reasons(audio="opus") == []
    assert _reasons(video="vp9") == [] and _reasons(video="av1") == []
    assert _reasons(container="matroska,webm", video="vp9", audio="opus") == []
    assert _reasons(container="matroska,webm", video="vp8", audio="vorbis") == []
    assert _reasons(video=None) == []                                           # sound only
    assert _reasons(container="mp3", video=None, audio="mp3") == []
    assert _reasons(container="wav", video=None, audio="pcm_s16le") == []
    assert _reasons(width=1080, height=1920, fps="60/1") == []                  # 1080p60, upright
    assert _reasons(fps="60000/1001") == []


def test_what_needs_a_proxy_and_why():
    assert _reasons(video="hevc") == ["Chromium doesn't play hevc video in MP4"]
    assert _reasons(video="prores") == ["Chromium doesn't play prores video in MP4"]
    assert _reasons(audio="ac3") == ["Chromium doesn't play ac3 sound in MP4"]
    assert _reasons(pix="yuv420p10le") == ["Chromium doesn't play yuv420p10le H.264 (only 8-bit 4:2:0)"]
    # Step 8 played H.264 + AAC in Matroska in Electron 31 (Chromium 126): no proxy.
    assert _reasons(container="matroska,webm", video="h264", audio="aac") == []
    assert _reasons(container="matroska,webm", video="h264", audio="aac", pix="yuv420p10le") == [
        "Chromium doesn't play yuv420p10le H.264 (only 8-bit 4:2:0)"]
    assert _reasons(container="matroska,webm", video="hevc", audio="ac3") == [
        "Chromium doesn't play hevc video in MATROSKA", "Chromium doesn't play ac3 sound in MATROSKA"]
    assert _reasons(container="avi", video="mpeg4", audio="mp3") == ["Chromium doesn't play the avi format"]
    assert _reasons(width=2560, height=1440) == ["bigger than 1080p (2560x1440)"]
    assert _reasons(width=1440, height=2560) == ["bigger than 1080p (1440x2560)"]
    assert _reasons(fps="120/1") == ["more than 60 frames a second (120)"]


def test_rotation_and_the_size_as_shown():
    turned = probe.describe(_data(width=1920, height=1080, rotation=-90))
    assert turned["rotation"] == 90 and (turned["display_width"], turned["display_height"]) == (1080, 1920)
    tagged = probe.describe(_data(width=1920, height=1080, tag=270))
    assert tagged["rotation"] == 270 and tagged["display_width"] == 1080
    assert probe.describe(_data(rotation=180))["rotation"] == 180
    assert probe.describe(_data())["rotation"] == 0


def test_the_facts():
    info = probe.describe(_data(fps="30000/1001", duration="12.5"))
    assert info["kind"] == "video" and info["duration_us"] == 12_500_000
    assert info["fps"] == {"num": 30000, "den": 1001} and info["fps_value"] == 29.97
    assert info["video_codec"] == "h264" and info["audio_codec"] == "aac" and info["has_audio"]
    sound = probe.describe(_data(video=None))
    assert sound["kind"] == "audio" and not sound["has_video"] and sound["fps"] is None
    with pytest.raises(probe.ProbeError):
        probe.describe({"format": {"format_name": "data"}, "streams": [{"codec_type": "data"}]})


def test_cover_art_is_not_a_picture():
    data = _data(container="mp3", video="mjpeg", audio="mp3")
    data["streams"][0]["disposition"] = {"attached_pic": 1}
    info = probe.describe(data)
    assert info["kind"] == "audio" and not info["has_video"]


def test_a_real_video_sound_and_picture(tmp_path):
    video = probe.info(make_video(tmp_path / "v.mp4", seconds=2))
    assert video["kind"] == "video" and video["video_codec"] == "h264" and video["has_audio"]
    assert (video["width"], video["height"]) == (640, 360) and abs(video["duration_us"] - 2_000_000) < 100_000
    assert probe.proxy_reasons(video) == []
    sound = probe.info(make_tone_and_silence(tmp_path / "s.wav"))
    assert sound["kind"] == "audio" and sound["audio_codec"] == "pcm_s16le" and probe.proxy_reasons(sound) == []
    picture = probe.info(make_picture(tmp_path / "p.png"))
    assert picture["kind"] == "image" and picture["video_codec"] == "png" and picture["duration_us"] == 0
    (tmp_path / "notes.txt").write_text("not media at all")
    with pytest.raises(probe.ProbeError):
        probe.info(tmp_path / "notes.txt")


@pytest.mark.parametrize("container", ["hls", "applehttp", "concat", "dash", "sdp", "tee", "ffmetadata"])
def test_a_list_of_other_files_is_not_media(container):
    """A playlist or a concat list would make FFmpeg read whatever it names
    (files outside the data folders, addresses) and stops working when they
    go: refused, whatever it holds."""
    with pytest.raises(probe.ProbeError, match="list of other files"):
        probe.describe(_data(container=container))


def test_ffprobe_and_ffmpeg_read_local_files_only():
    from video_editor.media import peaks, proxy, thumbs

    assert probe.LOCAL_ONLY == ("-protocol_whitelist", "file")
    info = {"has_video": True, "has_audio": True, "display_width": 640, "display_height": 360, "fps_value": 30.0}
    for argv in (peaks.command("ffmpeg", "in.mp4"), proxy.command("ffmpeg", "in.mp4", "out", info),
                 thumbs.command("ffmpeg", "in.mp4", "o", interval=1, tile=(160, 90), keyframes_only=True)):
        at = argv.index("-protocol_whitelist")
        assert argv[at + 1] == "file" and at < argv.index("-i")


def test_a_playlist_is_refused_by_the_real_ffprobe(tmp_path):
    clip = make_video(tmp_path / "elsewhere.mp4", seconds=1)
    playlist = tmp_path / "disguised.m3u8"
    playlist.write_text(f"#EXTM3U\n#EXT-X-TARGETDURATION:2\n#EXTINF:1.0,\n{clip.name}\n#EXT-X-ENDLIST\n")
    with pytest.raises(probe.ProbeError):
        probe.info(playlist)
