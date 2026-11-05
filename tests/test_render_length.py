"""A clip is never saved shorter than it says it is (#213).

A clip's card shows its window, so a render that lost its frames was saved
saying "64s" and played for 2. Two fixes, tested here:

  * video/cropper.py finishes on the CPU when the GPU decoder stops partway.
    sampled_frames reads FFmpeg exiting early as the end of the clip, and only
    a decoder that gave nothing at all used to fall back.
  * core/pipeline.py measures each clip before saving it, and reports one that
    came out much shorter than its window as a failed render.

Both modules import numpy (the cropper cv2 too), which CI does not install.
"""

import pytest

pytest.importorskip("numpy", reason="core.pipeline imports numpy, which CI does not install")

from core import pipeline

# ---- the length check -----------------------------------------------------------------------


def test_only_a_clip_well_short_of_its_window_counts_as_short():
    assert pipeline._short_render(64, 2.9)          # the report: "64s", plays for 2
    assert not pipeline._short_render(64, 66.9)     # the window and its end card
    assert not pipeline._short_render(64, 60)       # a little missing at the end passes
    assert not pipeline._short_render(0, 1)         # nothing to compare it with


@pytest.fixture
def clip(tmp_path):
    path = tmp_path / "clip_00010-00074.mp4"
    path.write_bytes(b"not really a video")
    return path


def test_a_short_clip_is_refused_and_its_file_removed(db, clip, monkeypatch):
    monkeypatch.setattr(pipeline, "_whole_duration", lambda path: 2.9)
    with pytest.raises(RuntimeError, match=r"came out 2\.9s long instead of 64s"):
        pipeline._check_length(db, clip, 64.0)
    assert not clip.exists()


def test_a_short_file_an_older_clip_points_at_stays(db, clip, monkeypatch):
    db.upsert_video("local_0393912b0a31", title="t")
    db.add_clip("local_0393912b0a31", 10, 74, 68, "hook", path=str(clip))
    monkeypatch.setattr(pipeline, "_whole_duration", lambda path: 2.9)
    with pytest.raises(RuntimeError):
        pipeline._check_length(db, clip, 64.0)
    assert clip.exists()


def test_a_full_length_clip_passes(db, clip, monkeypatch):
    monkeypatch.setattr(pipeline, "_whole_duration", lambda path: 66.9)
    pipeline._check_length(db, clip, 64.0)
    assert clip.exists()


def test_a_clip_the_probe_cannot_measure_is_kept(db, clip, monkeypatch):
    def unmeasurable(path):
        raise ValueError("The whole clip's duration could not be measured.")

    monkeypatch.setattr(pipeline, "_whole_duration", unmeasurable)
    pipeline._check_length(db, clip, 64.0)
    assert clip.exists()


# ---- the GPU decoder stopping partway -------------------------------------------------------


def _numbered_clip(tmp_path, np, cv2, n=30, fps=10.0):
    """A clip whose frame i is grey level 8 * i, so every frame the renderer
    hands on can be told apart and put in order."""
    path = tmp_path / "numbered.mp4"
    out = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"), fps, (320, 180))
    for i in range(n):
        out.write(np.full((180, 320, 3), 8 * i, np.uint8))
    out.release()
    return path


def _render(monkeypatch, tmp_path, gpu_frames):
    """Render the numbered clip with a GPU decoder that gives up after
    `gpu_frames` frames; returns each frame's level index and what it printed."""
    np = pytest.importorskip("numpy")
    cv2 = pytest.importorskip("cv2")
    import video.cropper as cropper
    import video.encoding as encoding

    clip = _numbered_clip(tmp_path, np, cv2)

    def decoder(clip_path, every, width, height, hwaccel=True):
        cap = cv2.VideoCapture(str(clip_path))
        try:
            for i in range(gpu_frames):
                ok, frame = cap.read()
                if not ok:
                    return
                yield i, frame
        finally:
            cap.release()

    frames: list = []
    monkeypatch.setattr(encoding, "sampled_frames", decoder)
    monkeypatch.setattr(cropper, "_run_ffmpeg_piped", lambda cmd, ass_path, produce: produce(frames.append))
    cropper.render_vertical(clip, {"mode": "track", "path": [(0.0, 0.5)]}, tmp_path / "out.mp4")
    return [round(float(np.frombuffer(f, np.uint8).mean()) / 8) for f in frames]


def test_a_decoder_that_stops_partway_is_finished_on_the_cpu(monkeypatch, tmp_path, capsys):
    assert _render(monkeypatch, tmp_path, gpu_frames=8) == list(range(30))
    assert "GPU decode stopped at frame 8 of 30; finishing on the CPU" in capsys.readouterr().out


def test_a_decoder_that_finishes_is_left_alone(monkeypatch, tmp_path, capsys):
    assert _render(monkeypatch, tmp_path, gpu_frames=30) == list(range(30))
    assert "finishing on the CPU" not in capsys.readouterr().out
