"""Vertical Live: a livestream already composed as 9:16 keeps its layout.

What the toggle promises, and what it must not touch:
- a 9:16 source renders whole at 1080x1920 in one encode, with no rescale when
  it already is 1080x1920;
- nothing that decides framing runs (face tracking, TalkNet, podcast framing,
  the render-thread setup);
- a source that isn't 9:16 is refused before any work;
- without the toggle, the standard path is exactly as it was.
"""

import json
import subprocess
from pathlib import Path

import pytest

from core import modes

# ---- what counts as vertical --------------------------------------------------------


@pytest.mark.parametrize(("size", "expected"), [
    ((1080, 1920), "vertical"),
    ((720, 1280), "vertical"),
    ((1440, 2560), "vertical"),
    ((2160, 3840), "vertical"),
    ((1920, 1080), "horizontal"),
    ((1280, 720), "horizontal"),
    ((1080, 1350), "other"),   # 4:5
    ((1080, 1080), "other"),   # square
    ((0, 0), "other"),         # unreadable
])
def test_orientation(size, expected):
    assert modes.orientation(*size) == expected


def test_an_exact_1080x1920_source_is_not_rescaled():
    assert modes.fit_filter(1080, 1920) == ""
    fit = modes.fit_filter(720, 1280)
    assert fit.startswith("scale=1080:1920:force_original_aspect_ratio=decrease")
    assert "pad=1080:1920" in fit and "crop" not in fit


def test_the_toggle_is_read_from_a_job_config_or_a_clips_own_options():
    assert modes.is_vertical_live({"clips": {"vertical_live": True}})
    assert modes.is_vertical_live({"vertical_live": True})      # a clip's render_opts
    assert not modes.is_vertical_live({"clips": {"podcast": True}})
    assert not modes.is_vertical_live(None)
    assert modes.needs_framing({"clips": {}}) is True
    assert modes.needs_framing({"clips": {"vertical_live": True}}) is False


# ---- the job option -------------------------------------------------------------------


@pytest.fixture
def api():
    pytest.importorskip("fastapi")
    from server import api as api_mod

    return api_mod


def test_the_toggle_reaches_the_job(api):
    payload = api._process_options(api.JobIn(url="https://x/v", vertical_live=True))
    assert payload == {"vertical_live": True}
    assert "vertical_live" not in api._process_options(api.JobIn(url="https://x/v", podcast=True))


def test_it_cant_be_combined_with_podcast_or_longform(api):
    from fastapi import HTTPException

    for extra in ({"podcast": True}, {"longform": {"mode": "highlights"}}):
        with pytest.raises(HTTPException) as e:
            api._process_options(api.JobIn(url="https://x/v", vertical_live=True, **extra))
        assert e.value.status_code == 400 and "Vertical Live" in e.value.detail


def test_a_queued_job_can_turn_it_off(api):
    patched = api._process_options(api.JobPatch(clear=["vertical_live"]), into={"vertical_live": True})
    assert "vertical_live" not in patched


def test_an_uploaded_file_can_carry_its_original_link(api):
    body = api.LocalVideoIn(path="C:/v.mp4", vertical_live=True, source_url="https://www.youtube.com/watch?v=x")
    assert body.source_url.startswith("https://") and api._process_options(body)["vertical_live"] is True


# ---- rendering --------------------------------------------------------------------------


@pytest.fixture
def pipeline(monkeypatch):
    pytest.importorskip("numpy")
    pytest.importorskip("cv2")
    from core import pipeline as pipeline_mod

    def framing(*_a, **_k):
        raise AssertionError("framing ran")

    import video.asd as asd
    import video.cropper as cropper
    import video.podcast as podcast
    import video.tracker as tracker

    for module, name in ((tracker, "compute_tracking"), (podcast, "analyze"), (cropper, "render_vertical"),
                         (asd, "_load"), (pipeline_mod, "_share_the_cpu")):
        monkeypatch.setattr(module, name, framing)
    return pipeline_mod


def _config(tmp_path, **clips):
    return {
        "clips": {"captions": False, "outro": False, "vertical": True, **clips},
        "paths": {"data_dir": str(tmp_path)},
        "tracking": {"detector": "yolov8n-pose.pt", "sample_fps": 8},
    }


def _render(pipeline, monkeypatch, tmp_path, size, config, opts=None):
    from core.models import ClipCandidate

    cuts = []

    def fake_cut(source, candidate, output, ass_path=None, vf_extra="", normalize=True):
        cuts.append(vf_extra)
        Path(output).write_bytes(b"clip")

    monkeypatch.setattr(pipeline, "cut_clip", fake_cut)
    monkeypatch.setattr(modes, "probe_size", lambda _path: size)
    final, opts_json = pipeline._render_files(
        tmp_path / "source.mp4", ClipCandidate(start=10.0, end=40.0, score=80), [],
        tmp_path / "clips", config, opts,
    )
    return final, opts_json, cuts


def test_a_1080x1920_live_is_one_encode_of_the_whole_frame(pipeline, monkeypatch, tmp_path):
    final, opts_json, cuts = _render(pipeline, monkeypatch, tmp_path, (1080, 1920),
                                     _config(tmp_path, vertical_live=True))
    assert final.exists() and cuts == [""]          # one encode, no scale, no crop
    assert json.loads(opts_json)["vertical_live"] is True


def test_a_720x1280_live_is_scaled_to_1080x1920_without_cropping(pipeline, monkeypatch, tmp_path):
    _final, _opts, cuts = _render(pipeline, monkeypatch, tmp_path, (720, 1280),
                                  _config(tmp_path, vertical_live=True))
    assert len(cuts) == 1 and cuts[0].startswith("scale=1080:1920") and "crop" not in cuts[0]


def test_an_editor_rerender_keeps_it_from_the_clips_own_options(pipeline, monkeypatch, tmp_path):
    _final, opts_json, cuts = _render(pipeline, monkeypatch, tmp_path, (1080, 1920),
                                      _config(tmp_path), opts={"vertical_live": True})
    assert cuts == [""] and json.loads(opts_json)["vertical_live"] is True


def test_without_the_toggle_the_standard_tracked_path_runs_as_before(pipeline, monkeypatch, tmp_path):
    with pytest.raises(AssertionError, match="framing ran"):
        _render(pipeline, monkeypatch, tmp_path, (1080, 1920), _config(tmp_path))


def test_a_source_that_isnt_9x16_is_refused_before_any_work(pipeline, monkeypatch, tmp_path, db):
    from core.models import DownloadedVideo

    source = tmp_path / "wide.mp4"
    source.write_bytes(b"not really a video")
    monkeypatch.setattr(pipeline, "_cached_or_download", lambda *_a: DownloadedVideo(
        video_id="local_wide", title="Wide", path=source, duration=600.0))
    monkeypatch.setattr("video.encoding.source_codec", lambda _p: "h264")
    monkeypatch.setattr(modes, "probe_size", lambda _p: (1920, 1080))

    def no_work(*_a, **_k):
        raise AssertionError("analysis started")

    monkeypatch.setattr(pipeline, "transcribe", no_work)
    with pytest.raises(modes.NotVerticalError) as e:
        pipeline.process_video("local:wide", _config(tmp_path, vertical_live=True), db)
    assert str(e.value).startswith(modes.MISMATCH) and "1920×1080" in str(e.value)


# ---- a real render, when FFmpeg is here ----------------------------------------------


def _ffmpeg_or_skip() -> str:
    from core.binaries import ffmpeg

    binary = ffmpeg()
    try:
        subprocess.run([binary, "-version"], capture_output=True, check=True)
    except (OSError, subprocess.CalledProcessError):
        pytest.skip("FFmpeg isn't available")
    return binary


@pytest.mark.parametrize("size", [(1080, 1920), (720, 1280)])
def test_a_real_vertical_live_render_comes_out_1080x1920(pipeline, tmp_path, size):
    from core.models import ClipCandidate

    binary = _ffmpeg_or_skip()
    source = tmp_path / "live.mp4"
    subprocess.run([binary, "-v", "error", "-f", "lavfi", "-i", f"testsrc2=size={size[0]}x{size[1]}:rate=30",
                    "-f", "lavfi", "-i", "sine=frequency=440", "-t", "4", "-c:v", "libx264",
                    "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(source)], check=True)
    assert modes.probe_size(source) == size

    final, _opts = pipeline._render_files(
        source, ClipCandidate(start=0.5, end=3.0, score=80), [], tmp_path / "clips",
        _config(tmp_path, vertical_live=True),
    )
    assert modes.probe_size(final) == (1080, 1920)
