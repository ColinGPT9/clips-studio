"""The GPU frame reader must pick exactly the frames the CPU loop picked.

Decoding is what pinned the CPU: cv2.VideoCapture has no CUDA in any pip
wheel and its decoder ignores every thread-limiting knob, so a 23s 1080p60
clip cost 10.16 CPU-seconds over 6.2 cores. FFmpeg with -hwaccel does the
same work for 0.94 CPU-seconds on 0.3 cores.

That saving is worthless if the frames move. Everything downstream — YOLO,
the mouth patches, the TalkNet crop grid — is indexed off "every Nth source
frame", so the reader has to agree with the loop it replaces, frame for
frame. These check that, and that a caller who stops early does not leave an
FFmpeg process writing into a dead pipe.
"""

import pytest

np = pytest.importorskip("numpy")
cv2 = pytest.importorskip("cv2")

from pathlib import Path  # noqa: E402

from video.encoding import sampled_frames  # noqa: E402

CLIP = Path("data/_bench/toxic_exact.mp4")
needs_clip = pytest.mark.skipif(
    not CLIP.exists(), reason="needs a real clip in data/_bench"
)


def _cv2_indices(path, step):
    """What the loop being replaced selects."""
    cap = cv2.VideoCapture(str(path))
    got, i = [], 0
    try:
        while True:
            if not cap.grab():
                break
            if i % step == 0:
                ok, _ = cap.retrieve()
                if ok:
                    got.append(i)
            i += 1
    finally:
        cap.release()
    return got


@needs_clip
@pytest.mark.parametrize("step", [8, 3])
def test_it_selects_the_same_source_frames_as_the_cv2_loop(step):
    cap = cv2.VideoCapture(str(CLIP))
    w, h = int(cap.get(3)), int(cap.get(4))
    cap.release()

    expected = _cv2_indices(CLIP, step)
    got = [i for i, _ in sampled_frames(CLIP, step, w, h)]
    assert got == expected, f"frame grid moved: {len(got)} vs {len(expected)}"


@needs_clip
def test_frames_come_back_the_right_shape_and_type():
    cap = cv2.VideoCapture(str(CLIP))
    w, h = int(cap.get(3)), int(cap.get(4))
    cap.release()

    idx, frame = next(iter(sampled_frames(CLIP, 8, w, h)))
    assert idx == 0
    assert frame.shape == (h, w, 3) and frame.dtype == np.uint8


@needs_clip
def test_software_fallback_agrees_with_hardware():
    """Not every machine has NVDEC, and the fallback must not shift the grid."""
    cap = cv2.VideoCapture(str(CLIP))
    w, h = int(cap.get(3)), int(cap.get(4))
    cap.release()

    hw = [i for i, _ in sampled_frames(CLIP, 8, w, h, hwaccel=True)]
    sw = [i for i, _ in sampled_frames(CLIP, 8, w, h, hwaccel=False)]
    assert hw == sw


@needs_clip
def test_stopping_early_does_not_leave_ffmpeg_running():
    import psutil

    before = len([p for p in psutil.process_iter(["name"])
                  if "ffmpeg" in (p.info["name"] or "").lower()])
    cap = cv2.VideoCapture(str(CLIP))
    w, h = int(cap.get(3)), int(cap.get(4))
    cap.release()

    gen = sampled_frames(CLIP, 8, w, h)
    next(iter(gen))
    gen.close()          # the generator's finally must kill the subprocess

    after = len([p for p in psutil.process_iter(["name"])
                 if "ffmpeg" in (p.info["name"] or "").lower()])
    assert after <= before
